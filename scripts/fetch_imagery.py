"""Fetch USGS National Map basemap tiles and cache them under data/gis/imagery/.

Usage:
    python scripts/fetch_imagery.py --lat 38.9126 --lon -75.4279
    python scripts/fetch_imagery.py --all-demo
    python scripts/fetch_imagery.py --all-demo --layer imagery

Downloads a single 900x700 PNG tile from the USGS National Map for the given
coordinates and caches it keyed by the layer and the rounded bounding box.
Nothing is fetched at render time: maps.py reads the cache only, and a cache
miss draws the existing roads basemap with no error.

Two layers are available. topo is the default: a topographic map, which shows
contours, the road network and named water, and reads as a map a reviewer is
being asked to check something against. imagery is the aerial photograph, which
is striking on a projector but says nothing about elevation, and elevation is
what a septic system cares about. Both are US federal, public domain, and free
to cache and commit.
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

IMAGERY_DIR = ROOT / "data" / "gis" / "imagery"

USGS_URL = (
    "https://basemap.nationalmap.gov/arcgis/rest/services/"
    "{service}/MapServer/export"
)

# The service name behind each layer we ask for.
LAYERS = {
    "topo": "USGSTopo",
    "imagery": "USGSImageryOnly",
}
DEFAULT_LAYER = "topo"

# Buffer around the point in degrees (roughly 900 ft at Delaware latitudes)
BUFFER_DEG = 0.004


def tile_key(lat: float, lon: float, layer: str = DEFAULT_LAYER) -> str:
    """A stable filename for the tile, keyed by the layer and bounding box."""
    # Round to 4 decimal places (about 11 metres) for stable keys
    lat_r = round(lat, 4)
    lon_r = round(lon, 4)
    return f"usgs_{layer}_{lat_r}_{lon_r}.png"


def tile_bbox(lat: float, lon: float) -> tuple[float, float, float, float]:
    """Bounding box for the tile: xmin, ymin, xmax, ymax in WGS84."""
    return (
        lon - BUFFER_DEG,
        lat - BUFFER_DEG,
        lon + BUFFER_DEG,
        lat + BUFFER_DEG,
    )


def fetch_tile(
    lat: float, lon: float, force: bool = False, layer: str = DEFAULT_LAYER,
) -> Path:
    """Download one USGS basemap tile for a point and cache it."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}, expected {sorted(LAYERS)}")
    IMAGERY_DIR.mkdir(parents=True, exist_ok=True)
    key = tile_key(lat, lon, layer)
    path = IMAGERY_DIR / key

    if path.exists() and not force:
        print(f"  Already cached: {path}")
        return path

    import urllib.request
    import urllib.parse

    xmin, ymin, xmax, ymax = tile_bbox(lat, lon)
    params = urllib.parse.urlencode({
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": "4326",
        "imageSR": "3857",
        "size": "900,700",
        "format": "png",
        "transparent": "false",
        "f": "image",
    })
    url = f"{USGS_URL.format(service=LAYERS[layer])}?{params}"
    print(f"  Fetching: {url[:120]}...")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dnrec-septic-precheck/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return path

    if len(data) < 1000:
        print(f"  WARNING: response too small ({len(data)} bytes), may be an error")
        return path

    path.write_bytes(data)
    print(f"  Saved: {path} ({len(data)} bytes)")
    return path


# Demo packet coordinates
DEMO_POINTS = [
    ("packet_a", 38.9126, -75.4279),    # Milford, Kent County
    ("packet_b", 38.6904, -75.3857),    # Georgetown, Sussex County
    ("packet_c", 39.1582, -75.5244),    # Dover
    ("permit_281364", 38.7745, -75.3014),  # Real permit 281364
]


def main():
    parser = argparse.ArgumentParser(description="Fetch USGS imagery tiles")
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--all-demo", action="store_true",
                        help="Fetch tiles for all demonstration packets")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even if cached")
    parser.add_argument("--layer", choices=sorted(LAYERS), default=DEFAULT_LAYER,
                        help="Which National Map layer to fetch")
    args = parser.parse_args()

    if args.all_demo:
        print("Fetching " + args.layer + " for all demo points:")
        for name, lat, lon in DEMO_POINTS:
            print(f"\n  {name} ({lat}, {lon}):")
            fetch_tile(lat, lon, force=args.force, layer=args.layer)
    elif args.lat is not None and args.lon is not None:
        fetch_tile(args.lat, args.lon, force=args.force, layer=args.layer)
    else:
        parser.print_help()
        return 1

    # Update SOURCE.md
    source_md = ROOT / "data" / "gis" / "SOURCE.md"
    if source_md.exists():
        content = source_md.read_text(encoding="utf-8")
        if "USGS National Map" not in content:
            content += (
                "\n\n## Aerial Imagery\n\n"
                "USGS National Map Imagery Only service. US federal, public domain.\n"
                "Endpoint: basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export\n"
                "Fetched 2026-08-19 by scripts/fetch_imagery.py.\n"
                "Used as decoration and orientation only. Never enters a measurement.\n"
            )
            source_md.write_text(content, encoding="utf-8")
            print(f"\nUpdated {source_md}")
    else:
        source_md.write_text(
            "# GIS Data Sources\n\n"
            "## Hydrography\n\n"
            "Delaware FirstMap NHD (National Hydrography Dataset), generalised.\n"
            "Downloaded once by scripts/fetch_gis.py.\n\n"
            "## Aerial Imagery\n\n"
            "USGS National Map Imagery Only service. US federal, public domain.\n"
            "Endpoint: basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export\n"
            "Fetched 2026-08-19 by scripts/fetch_imagery.py.\n"
            "Used as decoration and orientation only. Never enters a measurement.\n",
            encoding="utf-8",
        )
        print(f"\nCreated {source_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
