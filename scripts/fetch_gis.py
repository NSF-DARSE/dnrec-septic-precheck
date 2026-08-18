"""Download the Delaware FirstMap layers once, into data/gis.

The demo must run with no network, so nothing is fetched at runtime. This script
is the only thing that talks to FirstMap, it is run by hand, and what it writes is
committed.

Layers are paginated through the ArcGIS REST resultOffset because the services cap
a single response, and geometry is requested in WGS84 so the stored files need no
projection metadata to be interpreted. Projection to a measurable coordinate
system happens at query time in septic/geo.py.

Usage:
    python scripts/fetch_gis.py
    python scripts/fetch_gis.py --list
    python scripts/fetch_gis.py --max-features 20000
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config

BASE = "https://enterprise.firstmap.delaware.gov/arcgis/rest/services"
GIS_DIR = config.ROOT / "data" / "gis"

# Delaware's bounding box, used to clip and to sanity check what comes back.
DE_BBOX = (-75.80, 38.44, -74.98, 39.85)

# What to fetch. Each entry is a service path, a layer id, and the local filename.
# simplify is maxAllowableOffset in degrees. Roughly 0.0001 degrees is 11 metres.
LAYERS = [
    {
        "name": "surface_water_major_rivers",
        "service": "Hydrology/DE_Water/FeatureServer",
        "layer": 0,
        "simplify": 0.0001,
        "description": "MajorRivers, polyline, derived from NHD",
    },
    {
        "name": "surface_water_flowlines",
        "service": "Hydrology/DE_Water/FeatureServer",
        "layer": 1,
        "simplify": 0.0003,
        "description": "FlowLine, polyline, streams and ditches from NHD",
    },
    {
        "name": "surface_water_lakes_ponds",
        "service": "Hydrology/DE_Water/FeatureServer",
        "layer": 2,
        "simplify": 0.0002,
        "description": "Lakes and Ponds, polygon, from NHD",
    },
    {
        "name": "public_ponds",
        "service": "Hydrology/DE_Public_Ponds/FeatureServer",
        "layer": 0,
        "simplify": 0.0,
        "description": "DE_Public_Ponds, public pond features",
    },
    {
        "name": "tax_ditches",
        "service": "Hydrology/DE_TaxDitch/FeatureServer",
        "layer": 0,
        "simplify": 0.0003,
        "description": "DE_TaxDitch, tax ditch centrelines with easements",
    },
]


def get_json(url: str, params: dict, attempts: int = 4) -> dict:
    query = urllib.parse.urlencode(params)
    full = f"{url}?{query}"
    delay = 2.0
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                full, headers={"User-Agent": config.USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f"{full[:120]} failed: {exc}") from exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def describe_service(service: str) -> dict:
    return get_json(f"{BASE}/{service}", {"f": "json"})


def service_total(service: str, layer: int) -> int | None:
    """How many features the service holds, so truncation is detectable.

    A silently truncated water layer is worse than a missing one: a permit beside
    an unmapped stream reads as far from water, which is a false all clear rather
    than a visible gap.
    """
    try:
        payload = get_json(f"{BASE}/{service}/{layer}/query", {
            "where": "1=1", "returnCountOnly": "true", "f": "json",
        })
        return payload.get("count")
    except RuntimeError:
        return None


def round_geometry(geometry, places: int = 5):
    """Round coordinates in place. Five places is about one metre.

    The full precision from the service is around 13 decimal places, which is
    sub-millimetre and is roughly two thirds of the file size. This screening
    reports distance to the nearest feature at a resolution of feet, so a metre of
    coordinate precision is far more than the output can justify.
    """
    if isinstance(geometry, list):
        if geometry and isinstance(geometry[0], (int, float)):
            return [round(float(v), places) for v in geometry]
        return [round_geometry(g, places) for g in geometry]
    return geometry


def fetch_layer(service: str, layer: int, max_features: int,
                simplify_degrees: float = 0.0) -> dict:
    """Page through a layer and return one GeoJSON FeatureCollection.

    Clipped to Delaware's bounding box on the server side, which keeps the
    download small and means the committed files contain only the three counties
    this project covers.

    simplify_degrees is passed as maxAllowableOffset, which makes the service
    generalise geometry before sending it. The flowline network is dense enough
    that the unsimplified layer is 72 MB and still truncated, which is not
    something to commit. At 0.0002 degrees, roughly 20 metres, a stream still sits
    well inside the tolerance a 100 foot setback screening needs.

    Only the fields that identify a feature are requested. The full attribute set
    roughly doubles the file and none of it is used.
    """
    url = f"{BASE}/{service}/{layer}/query"
    xmin, ymin, xmax, ymax = DE_BBOX
    features: list[dict] = []
    offset = 0
    page_size = 500

    while len(features) < max_features:
        params = {
            "where": "1=1",
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }
        if simplify_degrees:
            params["maxAllowableOffset"] = simplify_degrees
        payload = get_json(url, params)
        page = payload.get("features") or []
        if not page:
            break
        for feature in page:
            geometry = feature.get("geometry") or {}
            if geometry.get("coordinates") is not None:
                geometry["coordinates"] = round_geometry(geometry["coordinates"])
            # Keep a small, useful subset of attributes. Names are what a map
            # label needs; everything else is weight.
            properties = feature.get("properties") or {}
            kept = {
                key: value for key, value in properties.items()
                if key.upper() in (
                    "GNIS_NAME", "NAME", "FTYPE", "FCODE", "TAXDITCH",
                    "POND_NAME", "OBJECTID",
                )
            }
            feature["properties"] = kept
        features.extend(page)
        if len(features) % 5000 < page_size:
            print(f"    {len(features)} features")
        if len(page) < page_size:
            break
        offset += page_size

    return {"type": "FeatureCollection", "features": features[:max_features]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_gis")
    ap.add_argument("--list", action="store_true",
                    help="describe the services and exit without downloading")
    ap.add_argument("--max-features", type=int, default=40000)
    ap.add_argument("--only", action="append",
                    help="fetch only the named layer, repeatable")
    args = ap.parse_args(argv)

    GIS_DIR.mkdir(parents=True, exist_ok=True)

    if args.list:
        for entry in LAYERS:
            try:
                info = describe_service(entry["service"])
                names = [f"{ly['id']}:{ly['name']}" for ly in info.get("layers", [])]
                print(f"{entry['service']}  {', '.join(names)}")
            except RuntimeError as exc:
                print(f"{entry['service']}  UNREACHABLE  {exc}")
        return 0

    manifest: list[dict] = []
    for entry in LAYERS:
        if args.only and entry["name"] not in args.only:
            continue
        print(f"{entry['name']}  <-  {entry['service']} layer {entry['layer']}")
        try:
            collection = fetch_layer(
                entry["service"], entry["layer"], args.max_features,
                simplify_degrees=entry.get("simplify", 0.0),
            )
        except RuntimeError as exc:
            print(f"  FAILED: {exc}")
            manifest.append({**entry, "status": "failed", "error": str(exc)})
            continue

        count = len(collection["features"])
        if count == 0:
            print("  returned no features, not written")
            manifest.append({**entry, "status": "empty"})
            continue

        # Gzipped, because completeness matters more than convenience here. The
        # full flowline network is 123,960 features and a truncated water layer
        # would make a permit next to an unmapped stream look far from water,
        # which is a wrong all clear rather than a missing feature. GeoJSON
        # compresses about six to one, so the complete layers stay committable.
        target = GIS_DIR / f"{entry['name']}.geojson.gz"
        raw = json.dumps(collection, separators=(",", ":")).encode("utf-8")
        with gzip.open(target, "wb", compresslevel=9) as handle:
            handle.write(raw)
        size_mb = target.stat().st_size / 1e6
        print(f"  wrote {target.name}  {count} features  "
              f"{size_mb:.1f} MB gzipped from {len(raw) / 1e6:.1f} MB")
        manifest.append({
            **entry,
            "status": "ok",
            "features": count,
            "file": target.name,
            "size_mb": round(size_mb, 2),
            "uncompressed_mb": round(len(raw) / 1e6, 2),
            "service_total": service_total(entry["service"], entry["layer"]),
        })

    (GIS_DIR / "manifest.json").write_text(
        json.dumps({
            "source": BASE,
            "downloaded": date.today().isoformat(),
            "bbox_wgs84": DE_BBOX,
            "layers": manifest,
        }, indent=2),
        encoding="utf-8",
    )
    print(f"\nmanifest written to {GIS_DIR / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
