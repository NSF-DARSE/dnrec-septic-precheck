"""Validate coordinate parsing across the whole CSV, then screen the examples.

Two jobs. First, prove the comma decimal parsing is right by running it over every
geocoded row and asserting the result lands inside Delaware, cross checked against
the Geocoded Location column which uses dots. Second, compute and print the actual
distances for the example applications.

Usage:
    python scripts/validate_geo.py
    python scripts/validate_geo.py --permits 281364 282133 282863
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config, geo


def validate_parsing(limit: int = 0) -> dict:
    import pandas as pd

    raw = pd.read_csv(config.PERMIT_CSV, dtype=str, low_memory=False)
    if limit:
        raw = raw.head(limit)

    stats = {
        "rows": len(raw),
        "no_coordinates": 0,
        "parsed": 0,
        "cross_checked": 0,
        "outside_delaware": 0,
        "cross_check_disagreements": 0,
        "naive_float_would_fail": 0,
        "examples": [],
    }
    outside: list[str] = []
    disagreements: list[str] = []

    for row in raw.to_dict("records"):
        raw_lat = row.get("Latitude")
        # How many values plain float() cannot read, which is the reason this
        # parser exists at all.
        if raw_lat is not None and str(raw_lat).strip() not in ("", "nan"):
            try:
                float(str(raw_lat))
            except ValueError:
                stats["naive_float_would_fail"] += 1
        try:
            point = geo.permit_point(row)
        except geo.CoordinateError as exc:
            message = str(exc)
            if "outside Delaware" in message:
                stats["outside_delaware"] += 1
                if len(outside) < 5:
                    outside.append(f"{row.get('permitNumber')}: {message}")
            else:
                stats["cross_check_disagreements"] += 1
                if len(disagreements) < 5:
                    disagreements.append(f"{row.get('permitNumber')}: {message}")
            continue
        if point is None:
            stats["no_coordinates"] += 1
            continue
        stats["parsed"] += 1
        if point.cross_checked:
            stats["cross_checked"] += 1
        if len(stats["examples"]) < 3:
            stats["examples"].append({
                "permit": row.get("permitNumber"),
                "raw_latitude": str(row.get("Latitude")),
                "raw_longitude": str(row.get("Longitude")),
                "parsed": [point.lat, point.lon],
                "cross_checked": point.cross_checked,
            })

    stats["outside_samples"] = outside
    stats["disagreement_samples"] = disagreements
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="validate_geo")
    ap.add_argument("--permits", nargs="*",
                    default=["281364", "282133", "282863"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    print("=" * 74)
    print("COORDINATE PARSE VALIDATION")
    print("=" * 74)
    stats = validate_parsing(args.limit)
    print(f"  rows examined                       {stats['rows']}")
    print(f"  rows with no coordinates            {stats['no_coordinates']}")
    print(f"  parsed and inside Delaware          {stats['parsed']}")
    print(f"  cross checked against Geocoded      {stats['cross_checked']}")
    print(f"  rejected, outside Delaware          {stats['outside_delaware']}")
    print(f"  rejected, cross check disagreement  "
          f"{stats['cross_check_disagreements']}")
    print(f"  values plain float() cannot read    "
          f"{stats['naive_float_would_fail']}")
    for sample in stats["outside_samples"]:
        print(f"    outside: {sample}")
    for sample in stats["disagreement_samples"]:
        print(f"    disagreement: {sample}")
    print()
    print("  worked examples of the comma decimal parse:")
    for example in stats["examples"]:
        print(f"    permit {example['permit']}: "
              f"{example['raw_latitude']!r}, {example['raw_longitude']!r}"
              f"  ->  {example['parsed'][0]}, {example['parsed'][1]}"
              f"  cross checked {example['cross_checked']}")
    print()

    print("=" * 74)
    print("LAYERS")
    print("=" * 74)
    names = geo.available_layers()
    if not names:
        print("  none present under data/gis")
        return 1
    for name in names:
        layer = geo.load_layer(name)
        print(f"  {name:<34}{len(layer):>8} features, projected to UTM 18N")
    print()

    print("=" * 74)
    print("SCREENING THE EXAMPLE APPLICATIONS")
    print("=" * 74)
    import pandas as pd

    raw = pd.read_csv(config.PERMIT_CSV, dtype=str, low_memory=False)
    results = {}
    for permit in args.permits:
        subset = raw[raw["permitNumber"] == permit]
        if subset.empty:
            print(f"  permit {permit}: not in the CSV")
            continue
        row = subset.iloc[0].to_dict()
        screening = geo.screen_permit(row)
        results[permit] = screening.to_json()

        print(f"  permit {permit}  ({row.get('County')})")
        if screening.point:
            print(f"    location      {screening.point.lat}, "
                  f"{screening.point.lon}  from {screening.point.source}"
                  f"  cross checked {screening.point.cross_checked}")
            print(f"    UTM 18N       {screening.utm[0]:.1f} E, "
                  f"{screening.utm[1]:.1f} N metres")
        for layer_name, nearest in sorted(
            screening.per_layer.items(), key=lambda kv: kv[1].distance_feet
        ):
            print(f"    {layer_name:<32}{nearest.distance_feet:>9.1f} ft"
                  f"  {nearest.label}")
        print("    screening flags:")
        for flag in screening.flags():
            print(f"      {flag}")
        print(f"    facts offered to the engine: {screening.facts()}")
        print()

    out = config.OUT_DIR / "geo_screening.json"
    out.write_text(json.dumps({"validation": stats, "screenings": results},
                              indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
