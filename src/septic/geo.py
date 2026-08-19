"""Geospatial screening for permit locations.

The regulation is full of isolation distances and Textract cannot measure a
scanned raster site plan. Coordinates route around that: 105,801 of the 117,802
CSV rows are geocoded, so a distance to the nearest mapped surface water feature
can be computed for most permits without reading a drawing at all.

WHAT THIS OUTPUTS, AND WHAT IT DOES NOT

It outputs a screening flag for a reviewer:

    This parcel is within 140 ft of mapped surface water. Verify the Exhibit C
    isolation distance on the site plan.

It does not output a compliance determination, and four separate reasons stop it
from ever doing so.

The regulation measures from the wrong thing. Exhibit C measures isolation
distance from the disposal area and from the septic tank. This measures from a
geocoded address point, which is somewhere on the parcel and usually the
structure. On a large parcel the disposal area can be over a hundred feet from
that point in any direction, which is the same order as the setback itself.

The water geometry is generalised. Flowline centrelines were simplified by about
33 metres to keep the layer committable, and 33 metres is a large fraction of the
100 foot setback. See data/gis/SOURCE.md.

The layer is not the legal source. Exhibit C note b turns on whether a watercourse
is designated for public water supply or shellfish, and states there is no setback
at all from an ephemeral watercourse, with that determination assigned to the Class
D soil scientist. A mapped NHD flowline does not carry that judgment.

There is no well layer. The well setback is the most commonly binding distance in
Exhibit C at 100 feet, and no public well location layer exists on FirstMap. Well
distance is reported as unavailable rather than estimated.

So this is wired into the engine as a measured fact that rules may consume, and the
rules that consume it are staged unverified like every other rule. A permit with no
coordinates, which is about ten percent of them, produces no fact at all and
therefore reads as CANNOT VERIFY. It never reads as a pass.
"""
from __future__ import annotations

import gzip
import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config

GIS_DIR = config.ROOT / "data" / "gis"

# Delaware's bounding box in WGS84. Every parsed coordinate is asserted against
# this, because the failure mode being guarded is a parse that silently produces a
# plausible looking number in the wrong hemisphere or with the decimal moved.
DE_BBOX = (-75.80, 38.44, -74.98, 39.85)

# UTM zone 18N covers Delaware. Metres, so distances are measurable.
# Distances are never computed in degrees: at Delaware's latitude a degree of
# longitude is about 87 km and a degree of latitude about 111 km, so treating
# degrees as distance is wrong by roughly a quarter, in a direction that depends
# on the bearing.
WGS84 = "EPSG:4326"
UTM_18N = "EPSG:32618"
METRES_TO_FEET = 3.280839895

# Surface water layers, in the order a label should prefer them. A named river is
# a more useful label than an unnamed ditch.
WATER_LAYERS = (
    "surface_water_major_rivers",
    "surface_water_lakes_ponds",
    "surface_water_flowlines",
    "public_ponds",
    "tax_ditches",
)

# Layers drawn on the screening figure for orientation only. These are
# deliberately not in WATER_LAYERS, because available_layers() drives the
# distance search and nothing here is a feature the regulation measures to. A
# road is context for a reviewer looking at where a parcel sits, never a
# setback. Keeping the two tuples separate is what stops a basemap layer from
# ever reaching screen_point().
BASEMAP_LAYERS = (
    "roads_centerline",
)


def available_basemap_layers() -> list[str]:
    """Basemap layers present on disk. Never used for any measurement."""
    if not GIS_DIR.exists():
        return []
    return [
        name for name in BASEMAP_LAYERS
        if (GIS_DIR / f"{name}.geojson.gz").exists()
        or (GIS_DIR / f"{name}.geojson").exists()
    ]

# The screening threshold. Set from the largest surface water isolation distance
# in the Exhibit C small systems table, 100 feet for the disposal area, with a
# margin because the measurement origin is an address point rather than the
# disposal area. Anything inside this gets flagged for the reviewer to check.
SCREEN_FEET = 200.0


class CoordinateError(ValueError):
    """A coordinate could not be parsed, or fell outside Delaware."""


# ---------------------------------------------------------------------------
# Coordinate parsing. The CSV uses comma decimal separators.
# ---------------------------------------------------------------------------

# "38,658307" and "-75,574018". A comma with more than three following digits
# cannot be a thousands separator in a latitude, so it is the decimal point.
COMMA_DECIMAL_RE = re.compile(r"^(-?\d{1,3}),(\d{2,})$")
DOT_DECIMAL_RE = re.compile(r"^-?\d{1,3}\.\d+$")
# "(38.658307, -75.574018)" from the Geocoded Location column, which uses dots.
GEOCODED_RE = re.compile(r"^\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\)$")


def parse_coordinate(raw: Any) -> float | None:
    """Parse one coordinate that may use a comma as the decimal separator.

    The Latitude and Longitude columns in this export are written "38,658307".
    float() raises on that, and a naive replace of comma with nothing would give
    38658307. Neither failure is loud: the first drops the row, the second
    produces a number that is obviously wrong to a person and not obviously wrong
    to a bounding box check on the wrong axis.

    Returns None rather than raising, so a caller can distinguish "not present"
    from "present and outside Delaware", which are different problems.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in ("nan", "none", "null", ""):
        return None

    comma = COMMA_DECIMAL_RE.match(text)
    if comma:
        return float(f"{comma.group(1)}.{comma.group(2)}")
    if DOT_DECIMAL_RE.match(text):
        return float(text)
    try:
        return float(text)
    except ValueError:
        return None


def parse_geocoded_location(raw: Any) -> tuple[float, float] | None:
    """Parse "(38.658307, -75.574018)" into (lat, lon).

    This column uses dots while Latitude and Longitude use commas, which makes it
    an independent cross check on the comma parsing rather than a second copy of
    the same risk.
    """
    if raw is None:
        return None
    match = GEOCODED_RE.match(str(raw).strip())
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def in_delaware(lon: float, lat: float) -> bool:
    xmin, ymin, xmax, ymax = DE_BBOX
    return xmin <= lon <= xmax and ymin <= lat <= ymax


@dataclass
class PermitPoint:
    """A permit location, parsed and validated."""

    lat: float
    lon: float
    source: str
    cross_checked: bool = False

    def to_json(self) -> dict:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "source": self.source,
            "cross_checked": self.cross_checked,
        }


def permit_point(row: dict) -> PermitPoint | None:
    """Read a permit location out of a CSV row, or None.

    Prefers the Latitude and Longitude columns and cross checks them against the
    Geocoded Location column when both are present. A disagreement beyond a metre
    means the comma parsing is wrong, which is exactly the silent corruption this
    is guarding, so it raises rather than picking a winner.
    """
    lat = parse_coordinate(row.get("Latitude"))
    lon = parse_coordinate(row.get("Longitude"))
    geocoded = parse_geocoded_location(row.get("Geocoded Location"))

    if lat is None or lon is None:
        if geocoded is None:
            return None
        lat, lon = geocoded
        source = "Geocoded Location"
        cross_checked = False
    else:
        source = "Latitude and Longitude"
        cross_checked = False
        if geocoded is not None:
            glat, glon = geocoded
            if abs(glat - lat) > 1e-5 or abs(glon - lon) > 1e-5:
                raise CoordinateError(
                    f"Latitude and Longitude parsed as ({lat}, {lon}) but "
                    f"Geocoded Location says ({glat}, {glon}). The comma decimal "
                    f"parsing is wrong."
                )
            cross_checked = True

    if not in_delaware(lon, lat):
        raise CoordinateError(
            f"({lat}, {lon}) is outside Delaware's bounding box {DE_BBOX}"
        )
    return PermitPoint(lat=lat, lon=lon, source=source, cross_checked=cross_checked)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _transformer():
    from pyproj import Transformer

    return Transformer.from_crs(WGS84, UTM_18N, always_xy=True)


@lru_cache(maxsize=1)
def _inverse_transformer():
    from pyproj import Transformer

    return Transformer.from_crs(UTM_18N, WGS84, always_xy=True)


def to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Projected metres back to (lon, lat).

    Needed to label a map drawn in UTM with the degrees a reviewer can type into
    any other mapping tool.
    """
    lon, lat = _inverse_transformer().transform(easting, northing)
    return lon, lat


def to_utm(lon: float, lat: float) -> tuple[float, float]:
    """Project one WGS84 point to UTM 18N metres."""
    return _transformer().transform(lon, lat)


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

@dataclass
class Layer:
    name: str
    geometries: list = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.geometries)


def _label_for(properties: dict) -> str:
    for key in ("GNIS_NAME", "NAME", "POND_NAME", "TAXDITCH"):
        value = properties.get(key)
        if value and str(value).strip() and str(value).strip().lower() != "null":
            return str(value).strip()
    ftype = properties.get("FTYPE")
    return str(ftype).strip() if ftype else "unnamed water feature"


def _binary_cache_path(name: str) -> Path:
    return config.OUT_DIR / "cache" / "gis" / f"{name}.wkb.pickle"


def _read_binary_cache(name: str, source_mtime: float) -> "Layer | None":
    """Return the parsed layer from the binary cache, or None if unusable.

    Any failure returns None and the caller reparses the GeoJSON. A stale or
    corrupt cache must never be able to change what the map shows.
    """
    path = _binary_cache_path(name)
    if not path.is_file():
        return None
    try:
        import pickle

        import shapely

        with path.open("rb") as handle:
            payload = pickle.load(handle)
        if payload.get("source_mtime") != source_mtime:
            return None
        geometries = list(shapely.from_wkb(payload["wkb"]))
        return Layer(name=name, geometries=geometries, labels=list(payload["labels"]))
    except Exception:  # noqa: BLE001
        return None


def _write_binary_cache(name: str, layer: "Layer", source_mtime: float) -> None:
    """Best effort. A cache that cannot be written is not an error."""
    try:
        import pickle

        import shapely

        path = _binary_cache_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_mtime": source_mtime,
            "wkb": [shapely.to_wkb(g) for g in layer.geometries],
            "labels": list(layer.labels),
        }
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as handle:
            pickle.dump(payload, handle, protocol=5)
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        pass


@lru_cache(maxsize=8)
def load_layer(name: str) -> Layer:
    """Load one GeoJSON layer, projected to UTM 18N.

    Projecting once at load time rather than per query is the difference between a
    console that answers in milliseconds and one that stalls. Cached, so the app
    pays this cost once.

    The five layers hold about 100,000 geometries between them, and parsing that
    much GeoJSON and projecting it takes roughly eighteen seconds, every process
    start. So the projected result is also cached to disk as WKB, keyed by the
    source file's modification time, which brings the same load down to about a
    tenth of a second. The GeoJSON stays the source of truth: the binary cache is
    only ever a faster route to the identical geometries, and any doubt about it
    falls back to reparsing.
    """
    from shapely.geometry import shape
    from shapely.ops import transform as shapely_transform

    path = GIS_DIR / f"{name}.geojson.gz"
    source = path if path.exists() else GIS_DIR / f"{name}.geojson"
    if source.exists():
        cached = _read_binary_cache(name, source.stat().st_mtime)
        if cached is not None:
            return cached
    if not path.exists():
        plain = GIS_DIR / f"{name}.geojson"
        if not plain.exists():
            return Layer(name=name)
        payload = json.loads(plain.read_text(encoding="utf-8"))
    else:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)

    transformer = _transformer()

    def project(x, y, z=None):
        return transformer.transform(x, y)

    geometries = []
    labels = []
    for feature in payload.get("features") or []:
        geometry = feature.get("geometry")
        if not geometry or not geometry.get("coordinates"):
            continue
        try:
            shaped = shape(geometry)
            if shaped.is_empty or not shaped.is_valid:
                # An invalid geometry makes shapely's distance return NaN, and a
                # NaN compares false against every threshold, so it would quietly
                # drop out of a nearest search instead of raising. Simplification
                # on the server produces a few self touching rings, so this is not
                # hypothetical.
                shaped = shaped.buffer(0) if not shaped.is_empty else shaped
                if shaped.is_empty or not shaped.is_valid:
                    continue
            projected = shapely_transform(project, shaped)
            if projected.is_empty or not projected.is_valid:
                continue
            geometries.append(projected)
            labels.append(_label_for(feature.get("properties") or {}))
        except Exception:  # noqa: BLE001 - a bad geometry is skipped, not fatal
            continue
    layer = Layer(name=name, geometries=geometries, labels=labels)
    if source.exists() and geometries:
        _write_binary_cache(name, layer, source.stat().st_mtime)
    return layer


def available_layers() -> list[str]:
    if not GIS_DIR.exists():
        return []
    found = []
    for name in WATER_LAYERS:
        if (GIS_DIR / f"{name}.geojson.gz").exists() or (
            GIS_DIR / f"{name}.geojson"
        ).exists():
            found.append(name)
    return found


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------

@dataclass
class NearestFeature:
    layer: str
    label: str
    distance_feet: float
    geometry: Any = None

    def to_json(self) -> dict:
        return {
            "layer": self.layer,
            "label": self.label,
            "distance_feet": round(self.distance_feet, 1),
        }


@dataclass
class Screening:
    """The result of screening one permit location."""

    point: PermitPoint | None = None
    utm: tuple[float, float] | None = None
    nearest_water: NearestFeature | None = None
    nearest_well: NearestFeature | None = None
    per_layer: dict[str, NearestFeature] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    figure_png: str | None = None

    @property
    def has_location(self) -> bool:
        return self.point is not None

    def flags(self) -> list[str]:
        """Screening sentences for a reviewer. Never a determination."""
        out: list[str] = []
        if self.point is None:
            out.append(
                "This permit has no usable coordinates, so no geospatial screening "
                "was possible. Isolation distances must be checked on the site plan."
            )
            return out
        if self.nearest_water is not None:
            distance = self.nearest_water.distance_feet
            label = self.nearest_water.label
            if distance <= SCREEN_FEET:
                out.append(
                    f"This parcel is within {distance:.0f} ft of mapped surface "
                    f"water ({label}). Verify the Exhibit C isolation distance on "
                    f"the site plan."
                )
            else:
                out.append(
                    f"Nearest mapped surface water is {distance:.0f} ft away "
                    f"({label}), beyond the {SCREEN_FEET:.0f} ft screening radius. "
                    f"This is not a clearance: the distance was measured from the "
                    f"geocoded address point, not from the disposal area."
                )
        for item in self.unavailable:
            out.append(item)
        return out

    def to_json(self) -> dict:
        return {
            "point": self.point.to_json() if self.point else None,
            "utm": list(self.utm) if self.utm else None,
            "nearest_water": (
                self.nearest_water.to_json() if self.nearest_water else None
            ),
            "nearest_well": (
                self.nearest_well.to_json() if self.nearest_well else None
            ),
            "per_layer": {k: v.to_json() for k, v in self.per_layer.items()},
            "unavailable": self.unavailable,
            "flags": self.flags(),
            "figure_png": self.figure_png,
            "screen_radius_feet": SCREEN_FEET,
        }

    def facts(self) -> dict[str, Any]:
        """Measured facts the rule engine may consume.

        Only the surface water distance is offered, and only when a location was
        parsed. A permit with no coordinates contributes nothing, so any rule
        needing this returns UNKNOWN, which is the required behaviour.
        """
        out: dict[str, Any] = {}
        if self.nearest_water is not None:
            out["dist_point_to_mapped_water"] = round(
                self.nearest_water.distance_feet, 1
            )
        return out


def screen_point(lat: float, lon: float, layers: list[str] | None = None,
                 keep_geometry: bool = False) -> Screening:
    """Distance from a location to the nearest feature in each water layer."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    point = PermitPoint(lat=lat, lon=lon, source="given")
    if not in_delaware(lon, lat):
        raise CoordinateError(f"({lat}, {lon}) is outside Delaware")

    easting, northing = to_utm(lon, lat)
    origin = Point(easting, northing)
    result = Screening(point=point, utm=(easting, northing))

    names = layers if layers is not None else available_layers()
    if not names:
        result.unavailable.append(
            "No surface water layers are present under data/gis, so no distance "
            "was computed. Run scripts/fetch_gis.py once with network access."
        )
        return result

    for name in names:
        layer = load_layer(name)
        if not len(layer):
            continue
        best_distance = math.inf
        best_index = -1
        for index, geometry in enumerate(layer.geometries):
            distance = origin.distance(geometry)
            # A NaN compares false against everything, so without this check a
            # broken geometry silently never wins and never reports itself.
            if math.isnan(distance):
                continue
            if distance < best_distance:
                best_distance = distance
                best_index = index
        if best_index < 0 or math.isinf(best_distance):
            continue
        nearest = NearestFeature(
            layer=name,
            label=layer.labels[best_index],
            distance_feet=best_distance * METRES_TO_FEET,
            geometry=layer.geometries[best_index] if keep_geometry else None,
        )
        result.per_layer[name] = nearest

    if result.per_layer:
        result.nearest_water = min(
            result.per_layer.values(), key=lambda f: f.distance_feet
        )

    # No public well layer exists. Reported as unavailable, never estimated.
    result.unavailable.append(
        "No public well location layer exists on Delaware FirstMap, so the "
        "distance to the nearest well was not computed. The Exhibit C well "
        "setback of 100 ft must be checked on the site plan, which Section "
        "5.2.1.5 requires to show every well within 150 ft."
    )
    return result


def screen_permit(row: dict, **kwargs) -> Screening:
    """Screen one CSV row. A row with no coordinates yields an empty screening."""
    try:
        point = permit_point(row)
    except CoordinateError as exc:
        result = Screening()
        result.unavailable.append(f"Coordinates rejected: {exc}")
        return result
    if point is None:
        return Screening()
    screening = screen_point(point.lat, point.lon, **kwargs)
    screening.point = point
    return screening
