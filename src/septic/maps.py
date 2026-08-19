"""Publication quality figures, drawn from local vector layers only.

Designed for projection rather than for paper: high contrast, large type, few
colours, readable from the back of a room. The palette is colourblind safe, taken
from Paul Tol's bright qualitative set, which stays distinguishable under
deuteranopia, protanopia and tritanopia, and also survives a projector with poor
colour balance.

No contextily and no web requests at render time. Everything is drawn from the
GeoJSON layers under data/gis, and the aerial imagery is read from tiles already
cached under data/gis/imagery by scripts/fetch_imagery.py. A tile that is not
cached is simply not drawn. A figure that needs a network call to render is wrong
here, because the venue wifi is expected to fail.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

# Agg before pyplot, so nothing tries to open a window on a headless machine.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, Patch, Polygon as MplPolygon  # noqa: E402

from . import config, geo  # noqa: E402

# Paul Tol bright. Distinguishable under the common colour vision deficiencies.
INK = "#111111"
ROAD = "#B0B7C0"      # basemap only, drawn behind everything
WATER = "#33638D"
WATER_FILL = "#BBD3E8"
WATER_LABEL = "#1B3D5C"
PERMIT = "#CC3311"
RING = "#117733"
# Paul Tol's #CCBB44 is a pale yellow. It survives a colour vision deficiency but
# not a white background: at roughly 1.8:1 against white the ring label was the
# hardest thing on the figure to read, and this is projected. Swapped for the
# dark amber from the same family, which keeps the hue separation and clears
# 4.5:1.
RING_ALT = "#8A6D00"
ESCARPMENT = "#882255"
GRID = "#DDDDDD"

# Buffer rings come from the promoted rules rather than being chosen for the
# picture, so the figure cannot drift from the rule set.
RING_RULES = (
    ("ISO-001-disposal-area-to-well", "well"),
    ("ISO-002-disposal-area-to-watercourse", "watercourse"),
    ("ISO-004-disposal-area-to-escarpment", "escarpment"),
)

FEET_PER_METRE = 1.0 / geo.METRES_TO_FEET


@dataclass
class MapResult:
    png: Path
    svg: Path
    nearest_feet: float | None
    nearest_label: str | None
    rings: list[tuple[float, list[str], str]]

    def to_json(self) -> dict:
        return {
            "png": str(self.png),
            "svg": str(self.svg),
            "nearest_feet": self.nearest_feet,
            "nearest_label": self.nearest_label,
            "rings": [
                {"feet": feet, "rules": rules, "label": label}
                for feet, rules, label in self.rings
            ],
        }


def ring_specs() -> list[tuple[float, list[str], str]]:
    """Buffer distances read out of the rule set, grouped by radius.

    Read from rules_7101.yaml rather than hardcoded, so a figure cannot show a
    threshold the rules do not hold.

    Grouped because several Exhibit C distances coincide: the well setback and the
    watercourse setback for the disposal area are both 100 feet. Drawn separately
    they land exactly on top of each other, and the legend then shows two colours
    for one visible circle, which reads as a drawing error.
    """
    from .rules.engine import load_rules

    by_id = {r.id: r for r in load_rules()}
    grouped: dict[float, dict[str, Any]] = {}
    for rule_id, what in RING_RULES:
        rule = by_id.get(rule_id)
        if rule is None or rule.threshold is None:
            continue
        try:
            feet = float(rule.threshold)
        except (TypeError, ValueError):
            continue
        entry = grouped.setdefault(
            feet, {"targets": [], "citation": rule.citation, "rules": []}
        )
        entry["targets"].append(what)
        entry["rules"].append(rule.id)

    specs: list[tuple[float, list[str], str]] = []
    for feet in sorted(grouped):
        entry = grouped[feet]
        targets = entry["targets"]
        if len(targets) == 1:
            joined = targets[0]
        else:
            joined = ", ".join(targets[:-1]) + " and " + targets[-1]
        citation = entry["citation"]
        label = (
            f"{feet:.0f} ft to {joined}, {citation.section} p.{citation.page}"
        )
        specs.append((feet, entry["rules"], label))
    return specs


def _compass(x0: float, y0: float, x1: float, y1: float) -> str:
    """Sixteen point compass bearing from one projected point to another.

    Grid north in UTM, not true north. Over a window this size the difference is
    well under one compass point, and a reviewer reading "471 ft NNW" wants the
    direction to look at on the plan, not a survey bearing.
    """
    angle = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
    points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return points[int((angle + 11.25) % 360 // 22.5)]


def _draw_geometry(ax, geometry, colour, fill, linewidth, zorder):
    """Draw one shapely geometry in UTM metres."""
    kind = geometry.geom_type
    if kind in ("LineString", "LinearRing"):
        xs, ys = geometry.xy
        ax.plot(xs, ys, color=colour, linewidth=linewidth, zorder=zorder,
                solid_capstyle="round")
    elif kind == "MultiLineString":
        for part in geometry.geoms:
            _draw_geometry(ax, part, colour, fill, linewidth, zorder)
    elif kind == "Polygon":
        ax.add_patch(MplPolygon(
            list(geometry.exterior.coords), closed=True,
            facecolor=fill, edgecolor=colour, linewidth=linewidth, zorder=zorder,
        ))
    elif kind in ("MultiPolygon", "GeometryCollection"):
        for part in geometry.geoms:
            _draw_geometry(ax, part, colour, fill, linewidth, zorder)
    elif kind == "Point":
        ax.plot(geometry.x, geometry.y, marker="o", color=colour,
                markersize=6, zorder=zorder)


def _scale_bar(ax, xmin, ymin, span_metres):
    """A scale bar in feet, because the regulation is written in feet."""
    candidates = [50, 100, 200, 300, 500, 800, 1000, 1500, 2000]
    target_feet = span_metres * geo.METRES_TO_FEET * 0.25
    bar_feet = min(candidates, key=lambda c: abs(c - target_feet))
    bar_metres = bar_feet * FEET_PER_METRE

    x0 = xmin + span_metres * 0.06
    y0 = ymin + span_metres * 0.07
    height = span_metres * 0.012

    ax.add_patch(plt.Rectangle((x0, y0), bar_metres, height,
                               facecolor=INK, edgecolor=INK, zorder=8))
    ax.add_patch(plt.Rectangle((x0 + bar_metres / 2, y0), bar_metres / 2, height,
                               facecolor="white", edgecolor=INK, zorder=9))
    ax.text(x0 + bar_metres / 2, y0 + height * 2.1, f"{bar_feet:.0f} ft",
            ha="center", va="bottom", fontsize=14, fontweight="bold", color=INK,
            zorder=9)


def _north_arrow(ax, xmax, ymax, span_metres):
    x = xmax - span_metres * 0.07
    y = ymax - span_metres * 0.16
    ax.annotate(
        "N", xy=(x, y + span_metres * 0.075), xytext=(x, y),
        ha="center", va="center", fontsize=17, fontweight="bold", color=INK,
        arrowprops=dict(facecolor=INK, edgecolor=INK, width=3.5, headwidth=13,
                        headlength=11),
        zorder=9,
    )


IMAGERY_DIR = config.ROOT / "data" / "gis" / "imagery"
IMAGERY_ALPHA = 0.45


def _imagery_path(lat: float, lon: float) -> Path | None:
    """Find a cached imagery tile for the given coordinates, or None."""
    lat_r = round(lat, 4)
    lon_r = round(lon, 4)
    key = f"usgs_{lat_r}_{lon_r}.png"
    path = IMAGERY_DIR / key
    return path if path.is_file() else None


def _draw_cached_imagery(ax, lat, lon, easting, northing, radius_m):
    """Draw cached USGS aerial imagery under the map at low opacity.

    A cache miss does nothing: no error, no blank panel.
    The imagery is decoration and orientation only. It never enters a measurement.
    """
    path = _imagery_path(lat, lon)
    if path is None:
        return
    try:
        img = plt.imread(str(path))
    except Exception:  # noqa: BLE001
        return
    # The tile covers the bounding box of the point +/- BUFFER_DEG in WGS84,
    # reprojected to Web Mercator by the USGS service. We place it in the UTM
    # plot space at the window extent.
    xmin = easting - radius_m
    xmax = easting + radius_m
    ymin = northing - radius_m
    ymax = northing + radius_m
    ax.imshow(img, extent=[xmin, xmax, ymin, ymax], aspect="auto",
              alpha=IMAGERY_ALPHA, zorder=0, interpolation="bilinear")


def permit_map(
    permit: str,
    lat: float,
    lon: float,
    out_dir: Path | None = None,
    radius_feet: float = 900.0,
    details: dict | None = None,
) -> MapResult | None:
    """Draw the location map for one permit.

    Returns None when no layers are present, rather than producing an empty map
    that implies there is no water nearby.
    """
    from shapely.geometry import Point, box

    layers = geo.available_layers()
    if not layers:
        return None

    out_dir = Path(out_dir or (config.OUT_DIR / "figures"))
    out_dir.mkdir(parents=True, exist_ok=True)

    easting, northing = geo.to_utm(lon, lat)
    radius_m = radius_feet * FEET_PER_METRE
    window = box(easting - radius_m, northing - radius_m,
                 easting + radius_m, northing + radius_m)
    origin = Point(easting, northing)

    fig, ax = plt.subplots(figsize=(10, 10), dpi=170)
    ax.set_facecolor("white")

    # Aerial imagery as background decoration, if cached.
    # A cache miss draws the existing roads basemap with no error.
    _draw_cached_imagery(ax, lat, lon, easting, northing, radius_m)

    # Water features inside the window, with the nearest tracked for annotation.
    nearest_distance = math.inf
    nearest_label = None
    nearest_point = None
    nearest_layer = None
    drawn = 0
    labelled: set[str] = set()

    # Roads first, so everything that matters draws on top of them. These are
    # orientation only. They are never compared against, never labelled on the
    # figure, and never enter the nearest feature search below, which is why
    # they come from available_basemap_layers rather than available_layers.
    roads_drawn = 0
    for name in geo.available_basemap_layers():
        tree, geometries, _labels = geo.layer_index(name)
        for position in tree.query(window):
            geometry = geometries[position]
            if not geometry.intersects(window):
                continue
            _draw_geometry(ax, geometry, ROAD, ROAD, 1.9, zorder=1)
            roads_drawn += 1

    for name in layers:
        tree, geometries, labels = geo.layer_index(name)
        is_polygon = "lakes" in name or "ponds" in name
        for position in tree.query(window):
            geometry, label = geometries[position], labels[position]
            if not geometry.intersects(window):
                continue
            _draw_geometry(
                ax, geometry, WATER, WATER_FILL,
                2.6 if not is_polygon else 1.8, zorder=3,
            )
            drawn += 1
            distance = origin.distance(geometry)
            if math.isfinite(distance) and distance < nearest_distance:
                nearest_distance = distance
                nearest_label = label
                nearest_layer = name
                from shapely.ops import nearest_points
                nearest_point = nearest_points(origin, geometry)[1]
            # Label named features once each, inside the window.
            if (label and label not in labelled
                    and not label.startswith("unnamed")
                    and not label.isdigit()):
                centre = geometry.centroid
                if window.contains(centre):
                    ax.annotate(
                        label, xy=(centre.x, centre.y), fontsize=13,
                        color=WATER_LABEL, fontweight="semibold", zorder=6,
                        ha="center",
                        bbox=dict(boxstyle="round,pad=0.26", facecolor="white",
                                  edgecolor=WATER, linewidth=0.8, alpha=0.97),
                    )
                    labelled.add(label)

    # Buffer rings at the actual rule thresholds. Labels are placed on different
    # bearings per ring so a small ring's label does not land on top of a larger
    # one, or on the permit marker.
    rings = ring_specs()
    bearings = [90, 45, 135, 0, 180]
    for index, (feet, _rule_ids, label) in enumerate(rings):
        colour = [RING, RING_ALT, ESCARPMENT][index % 3]
        radius_ring = feet * FEET_PER_METRE
        ax.add_patch(Circle(
            (easting, northing), radius_ring,
            fill=False, edgecolor=colour, linewidth=2.8,
            linestyle=(0, (7, 4)), zorder=4,
        ))
        angle = math.radians(bearings[index % len(bearings)])
        label_x = easting + radius_ring * math.cos(angle)
        label_y = northing + radius_ring * math.sin(angle)
        # A ring much smaller than the window cannot carry a legible inline label:
        # at a 900 ft window the 15 ft escarpment ring is under two percent of the
        # width, so its label lands on the permit marker. Those rings are still
        # drawn and still named in the legend, just not labelled in place.
        if radius_ring >= radius_m * 0.08:
            ax.annotate(
                f"{feet:.0f} ft", xy=(label_x, label_y),
                fontsize=13, fontweight="bold", color=colour, ha="center",
                va="center", zorder=7,
                bbox=dict(boxstyle="round,pad=0.24", facecolor="white",
                          edgecolor=colour, linewidth=1.5),
            )

    # The permit point.
    ax.plot(easting, northing, marker="*", markersize=30, color=PERMIT,
            markeredgecolor=INK, markeredgewidth=1.7, zorder=10)

    # An arrow from the permit point to the nearest feature, so the measurement
    # reads as a direction and a target rather than a line between two dots.
    nearest_feet = None
    nearest_bearing = None
    if nearest_point is not None and math.isfinite(nearest_distance):
        nearest_feet = nearest_distance * geo.METRES_TO_FEET
        nearest_bearing = _compass(easting, northing,
                                   nearest_point.x, nearest_point.y)
        ax.annotate(
            "", xy=(nearest_point.x, nearest_point.y), xytext=(easting, northing),
            zorder=9,
            arrowprops=dict(
                arrowstyle="-|>,head_width=0.42,head_length=0.85",
                color=INK, linewidth=2.6, shrinkA=17, shrinkB=2,
                connectionstyle="arc3,rad=0",
            ),
        )
        mid_x = (easting + nearest_point.x) / 2
        mid_y = (northing + nearest_point.y) / 2
        target = nearest_label if nearest_label and not str(nearest_label).isdigit() \
            else "nearest mapped water"
        ax.annotate(
            f"{nearest_feet:.0f} ft {nearest_bearing}\nto {target}",
            xy=(mid_x, mid_y), fontsize=14.5, fontweight="bold", color=INK,
            ha="center", va="center", zorder=11,
            bbox=dict(boxstyle="round,pad=0.38", facecolor="white",
                      edgecolor=INK, linewidth=1.8),
        )

    xmin, ymin = easting - radius_m, northing - radius_m
    xmax, ymax = easting + radius_m, northing + radius_m
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")

    # Clean frame: no axis labels or tick labels, since coordinates are stated
    # in the data panel rendered by the console and report. Keep the frame itself
    # for visual boundary.
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_edgecolor(INK)
        spine.set_linewidth(1.6)

    _scale_bar(ax, xmin, ymin, radius_m * 2)
    _north_arrow(ax, xmax, ymax, radius_m * 2)

    # Legend built from what was actually drawn, not from a fixed list.
    handles = [
        Line2D([], [], marker="*", color="none", markerfacecolor=PERMIT,
               markeredgecolor=INK, markersize=18, label="Permit location"),
    ]
    if drawn:
        handles.append(
            Line2D([], [], color=WATER, linewidth=3, label="Mapped surface water")
        )
    if roads_drawn:
        handles.append(
            Line2D([], [], color=ROAD, linewidth=2.4, label="Road")
        )
    if nearest_point is not None:
        handles.append(
            Line2D([], [], color=INK, linewidth=2.4,
                   marker=">", markersize=8, markevery=[-1],
                   label="Measured distance")
        )
    for index, (feet, _rule_ids, label) in enumerate(rings):
        colour = [RING, RING_ALT, ESCARPMENT][index % 3]
        radius_ring = feet * FEET_PER_METRE
        # Only add to legend if the ring is large enough to be visible
        if radius_ring >= radius_m * 0.03:
            handles.append(Line2D(
                [], [], color=colour, linewidth=2.8,
                linestyle=(0, (7, 4)),
                label=f"{feet:.0f} ft setback",
            ))
    ax.legend(handles=handles, loc="upper left", fontsize=11.5,
              framealpha=0.95, edgecolor=INK, borderpad=0.6,
              handlelength=2.2)

    ax.set_title(
        f"Permit {permit}: location against mapped surface water",
        fontsize=20, fontweight="bold", color=INK, pad=16,
    )

    # The data panel (permit details, nearest feature, coordinates) is rendered
    # by the console and report as a selectable definition list beside the figure,
    # not baked into the PNG. Removed from the image so it carries only the map,
    # scale bar, north arrow, distance annotation, and caption.

    # Wrapped by hand rather than relying on wrap=True, which measures against
    # the figure edge and was overflowing the right margin.
    caption_lines = [
        "Dashed rings are isolation distances read from the rule set, not chosen for this figure.",
        "Surface water from Delaware FirstMap (NHD), generalised on download. Aerial imagery from USGS National Map.",
        "Projection UTM zone 18N. Distance is measured from the geocoded address point, not from the disposal area,",
        "so this is a screening prompt for the reviewer and not a compliance determination.",
    ]
    fig.text(0.5, 0.012, "\n".join(caption_lines), ha="center", va="bottom",
             fontsize=10.5, color="#333333")

    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.10)

    png = out_dir / f"permit_{permit}_map.png"
    svg = out_dir / f"permit_{permit}_map.svg"
    fig.savefig(png, facecolor="white")
    fig.savefig(svg, facecolor="white")
    plt.close(fig)

    return MapResult(
        png=png, svg=svg,
        nearest_feet=(
            nearest_distance * geo.METRES_TO_FEET
            if math.isfinite(nearest_distance) else None
        ),
        nearest_label=nearest_label,
        rings=rings,
    )


# ---------------------------------------------------------------------------
# The comparison figure
# ---------------------------------------------------------------------------

def distance_distributions(
    year_min: int | None = None, limit_per_group: int = 0
) -> dict[str, Any]:
    """Distance to nearest mapped surface water, denied against approved.

    Returns the raw distances so the caller can plot and also report sample sizes.
    Screening every permit is slow, so the denied group is taken whole and the
    approved group is sampled evenly across the selection rather than taking the
    first N, which would bias toward one county.

    select_permits maps a fixed set of columns through PERMIT_FIELDS and the
    coordinate columns are not among them, so the selection is used only to decide
    which permits belong to each group and the coordinates are read from the raw
    frame by detail_id. Passing the mapped records straight to the screener
    silently produced zero distances for every permit, since none of them carried a
    Latitude key at all.
    """
    from .harvest import csv_index

    frame = csv_index.load_csv()
    year_min = config.YEAR_MIN if year_min is None else year_min
    out: dict[str, Any] = {"year_min": year_min, "groups": {}}

    raw_by_detail = {
        str(row["detail_id"]): row
        for row in frame.to_dict("records")
        if row.get("detail_id")
    }

    groups = {
        "denied and returned": ["Denied", "Application Returned"],
        "approved": ["Approved"],
    }
    for label, statuses in groups.items():
        selection = csv_index.select_permits(
            frame, statuses=statuses, year_min=year_min
        )
        rows = selection.rows
        if limit_per_group and len(rows) > limit_per_group:
            step = max(1, len(rows) // limit_per_group)
            rows = rows[::step][:limit_per_group]

        distances: list[float] = []
        no_coordinates = 0
        rejected = 0
        for record in rows:
            raw_row = raw_by_detail.get(str(record.get("detail_id")))
            if raw_row is None:
                no_coordinates += 1
                continue
            try:
                point = geo.permit_point(raw_row)
            except geo.CoordinateError:
                rejected += 1
                continue
            if point is None:
                no_coordinates += 1
                continue
            screening = geo.screen_point(point.lat, point.lon)
            if screening.nearest_water is not None:
                distances.append(screening.nearest_water.distance_feet)

        out["groups"][label] = {
            "selected": len(selection.rows),
            "screened": len(rows),
            "with_distance": len(distances),
            "no_coordinates": no_coordinates,
            "rejected_coordinates": rejected,
            "distances": distances,
        }
    return out


def comparison_figure(
    data: dict[str, Any], out_dir: Path | None = None
) -> dict[str, Any]:
    """Distribution of distance to nearest surface water, by outcome.

    Bin edges are fixed in advance and the full range is shown. No cut is selected
    after looking at the data: if the distributions overlap, the figure says so on
    its face, because a real negative is a finding and a flattering cut is not.
    """
    import numpy as np

    out_dir = Path(out_dir or (config.OUT_DIR / "figures"))
    out_dir.mkdir(parents=True, exist_ok=True)

    denied = data["groups"].get("denied and returned", {})
    approved = data["groups"].get("approved", {})
    denied_distances = np.array(denied.get("distances") or [], dtype=float)
    approved_distances = np.array(approved.get("distances") or [], dtype=float)

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(13, 11), dpi=170,
        gridspec_kw={"height_ratios": [2.1, 1]},
    )

    bins = list(range(0, 2100, 100))
    for values, colour, label in (
        (denied_distances, PERMIT, "Denied and returned"),
        (approved_distances, WATER, "Approved"),
    ):
        if values.size:
            ax.hist(
                np.clip(values, 0, 2000), bins=bins, density=True,
                histtype="stepfilled", alpha=0.42, color=colour,
                edgecolor=colour, linewidth=2.4,
                label=f"{label}, n={values.size}",
            )

    ax.set_xlabel("Distance to nearest mapped surface water, feet",
                  fontsize=15, fontweight="semibold")
    ax.set_ylabel("Share of permits", fontsize=15, fontweight="semibold")
    ax.tick_params(labelsize=13)
    ax.grid(True, color=GRID, linewidth=0.9)
    ax.set_axisbelow(True)
    ax.legend(fontsize=13.5, edgecolor=INK, framealpha=0.95)
    ax.set_title(
        "Distance to nearest mapped surface water, by permit outcome",
        fontsize=19, fontweight="bold", pad=14,
    )

    # Medians, stated rather than implied.
    lines = []
    if denied_distances.size:
        median_denied = float(np.median(denied_distances))
        ax.axvline(min(median_denied, 2000), color=PERMIT, linewidth=2.4,
                   linestyle="--")
        lines.append(f"denied median {median_denied:.0f} ft")
    if approved_distances.size:
        median_approved = float(np.median(approved_distances))
        ax.axvline(min(median_approved, 2000), color=WATER, linewidth=2.4,
                   linestyle="--")
        lines.append(f"approved median {median_approved:.0f} ft")

    # Cumulative view, which is where separation would be visible if present.
    for values, colour, label in (
        (denied_distances, PERMIT, "Denied and returned"),
        (approved_distances, WATER, "Approved"),
    ):
        if values.size:
            ordered = np.sort(values)
            share = np.arange(1, ordered.size + 1) / ordered.size
            ax2.plot(ordered, share, color=colour, linewidth=3, label=label)
    ax2.set_xlim(0, 2000)
    ax2.set_xlabel("Distance to nearest mapped surface water, feet",
                   fontsize=15, fontweight="semibold")
    ax2.set_ylabel("Cumulative share", fontsize=15, fontweight="semibold")
    ax2.tick_params(labelsize=13)
    ax2.grid(True, color=GRID, linewidth=0.9)
    ax2.set_axisbelow(True)
    ax2.legend(fontsize=13, edgecolor=INK, framealpha=0.95)

    summary: dict[str, Any] = {
        "denied": {
            "n": int(denied_distances.size),
            "median_feet": (
                round(float(np.median(denied_distances)), 1)
                if denied_distances.size else None
            ),
            "within_200ft": int((denied_distances <= 200).sum())
            if denied_distances.size else 0,
            "selected": denied.get("selected"),
            "no_coordinates": denied.get("no_coordinates"),
        },
        "approved": {
            "n": int(approved_distances.size),
            "median_feet": (
                round(float(np.median(approved_distances)), 1)
                if approved_distances.size else None
            ),
            "within_200ft": int((approved_distances <= 200).sum())
            if approved_distances.size else 0,
            "selected": approved.get("selected"),
            "no_coordinates": approved.get("no_coordinates"),
        },
    }

    # State the finding on the figure. The task this serves is deciding whether to
    # fund the work, so a figure that leaves the reader to infer a null result is
    # doing the wrong job. Rates are computed, not eyeballed.
    denied_rate = (
        100.0 * summary["denied"]["within_200ft"] / summary["denied"]["n"]
        if summary["denied"]["n"] else None
    )
    approved_rate = (
        100.0 * summary["approved"]["within_200ft"] / summary["approved"]["n"]
        if summary["approved"]["n"] else None
    )
    summary["denied"]["within_200ft_pct"] = (
        round(denied_rate, 1) if denied_rate is not None else None
    )
    summary["approved"]["within_200ft_pct"] = (
        round(approved_rate, 1) if approved_rate is not None else None
    )

    if denied_rate is not None and approved_rate is not None:
        separates = abs(denied_rate - approved_rate) > 10.0
        summary["separates"] = separates
        if separates:
            finding = (
                f"FINDING: the groups differ. {denied_rate:.0f} percent of denied "
                f"and returned permits sit within\n200 ft of mapped water against "
                f"{approved_rate:.0f} percent of approved."
            )
        else:
            finding = (
                f"FINDING: the distributions do not separate. "
                f"{denied_rate:.0f} percent of denied and returned\npermits sit "
                f"within 200 ft of mapped water against {approved_rate:.0f} "
                f"percent of approved, and the\ncumulative curves track each "
                f"other. Distance to water alone does not predict the\noutcome in "
                f"this corpus."
            )
        ax.text(
            0.5, -0.215, finding, transform=ax.transAxes, ha="center", va="top",
            fontsize=12.5, fontweight="semibold", color="#111827",
            bbox=dict(boxstyle="round,pad=0.55", facecolor="#F3F4F6",
                      edgecolor=INK, linewidth=1.5),
        )

    caption_lines = [
        f"Samples: denied and returned n={summary['denied']['n']}, approved "
        f"n={summary['approved']['n']}, {data['year_min']} onward. "
        + ("  ".join(lines) + "." if lines else ""),
        "Only 35 of the 104 denied and returned permits from 2014 onward carry "
        "usable coordinates, so the negative sample is small and a few records "
        "move it several points.",
        "Bin edges were fixed at 100 ft before plotting and the full range is "
        "shown, so no cut was selected after seeing the data.",
        "Distance is from the geocoded address point to generalised NHD geometry, "
        "not from the disposal area to the legal feature, so this compares",
        "populations and cannot judge an individual permit.",
    ]
    fig.text(0.5, 0.012, "\n".join(caption_lines), ha="center", va="bottom",
             fontsize=11, color="#333333")

    fig.subplots_adjust(left=0.09, right=0.97, top=0.945, bottom=0.20, hspace=0.55)

    png = out_dir / "distance_to_water_by_outcome.png"
    svg = out_dir / "distance_to_water_by_outcome.svg"
    fig.savefig(png, facecolor="white")
    fig.savefig(svg, facecolor="white")
    plt.close(fig)

    summary["png"] = str(png)
    summary["svg"] = str(svg)
    return summary
