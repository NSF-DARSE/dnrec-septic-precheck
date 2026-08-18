"""Generate the figures: a location map per example, and the comparison figure.

Everything is drawn from the local layers under data/gis. No network, no basemap
tiles.

Usage:
    python scripts/make_figures.py
    python scripts/make_figures.py --maps-only
    python scripts/make_figures.py --comparison-only --sample 400
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config, geo, maps


def example_permits() -> list[str]:
    examples = config.OUT_DIR / "examples"
    if not examples.exists():
        return []
    found = []
    for pdf in sorted(examples.glob("*.pdf")):
        parts = pdf.stem.split("_")
        if len(parts) > 1:
            found.append(parts[1])
    return found


def permit_rows(permits: list[str]) -> dict[str, dict]:
    import pandas as pd

    if not config.PERMIT_CSV.exists():
        return {}
    frame = pd.read_csv(config.PERMIT_CSV, dtype=str, low_memory=False)
    out = {}
    for permit in permits:
        subset = frame[frame["permitNumber"].astype(str) == str(permit)]
        if not subset.empty:
            out[permit] = subset.iloc[0].to_dict()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_figures")
    ap.add_argument("--maps-only", action="store_true")
    ap.add_argument("--comparison-only", action="store_true")
    ap.add_argument("--sample", type=int, default=350,
                    help="approved permits to screen for the comparison figure")
    ap.add_argument("--radius", type=float, default=900.0,
                    help="map window radius in feet")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    figures = config.OUT_DIR / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    if not geo.available_layers():
        print("no GIS layers under data/gis. Run scripts/fetch_gis.py once.")
        return 1

    report: dict = {}

    if not args.comparison_only:
        print("=" * 74)
        print("LOCATION MAPS")
        print("=" * 74)
        permits = example_permits()
        rows = permit_rows(permits)
        report["maps"] = {}
        for permit in permits:
            row = rows.get(permit)
            if row is None:
                print(f"  permit {permit}: not in the CSV, skipped")
                continue
            try:
                point = geo.permit_point(row)
            except geo.CoordinateError as exc:
                print(f"  permit {permit}: {exc}")
                continue
            if point is None:
                print(f"  permit {permit}: no coordinates, no map drawn")
                continue
            result = maps.permit_map(
                permit, point.lat, point.lon, out_dir=figures,
                radius_feet=args.radius,
            )
            if result is None:
                print(f"  permit {permit}: no layers, no map drawn")
                continue
            report["maps"][permit] = result.to_json()
            print(f"  permit {permit}")
            print(f"    {result.png.name}  "
                  f"{result.png.stat().st_size / 1e3:.0f} KB")
            print(f"    {result.svg.name}  "
                  f"{result.svg.stat().st_size / 1e3:.0f} KB")
            if result.nearest_feet is not None:
                print(f"    nearest mapped water {result.nearest_feet:.0f} ft "
                      f"({result.nearest_label})")
            print(f"    rings drawn: "
                  f"{', '.join(f'{f:.0f} ft' for f, _rs, _l in result.rings)}")
        print()

    if not args.maps_only:
        print("=" * 74)
        print("COMPARISON FIGURE")
        print("=" * 74)
        print(f"  screening the denied group whole and up to {args.sample} "
              f"approved permits, this takes a few minutes")
        data = maps.distance_distributions(limit_per_group=args.sample)
        for label, group in data["groups"].items():
            print(f"  {label}: {group['selected']} selected, "
                  f"{group['screened']} screened, "
                  f"{group['with_distance']} with a distance, "
                  f"{group['no_coordinates']} without coordinates, "
                  f"{group['rejected_coordinates']} coordinates rejected")
        summary = maps.comparison_figure(data, out_dir=figures)
        report["comparison"] = summary
        print()
        print(f"  denied and returned  n={summary['denied']['n']}  "
              f"median {summary['denied']['median_feet']} ft  "
              f"{summary['denied']['within_200ft']} within 200 ft")
        print(f"  approved             n={summary['approved']['n']}  "
              f"median {summary['approved']['median_feet']} ft  "
              f"{summary['approved']['within_200ft']} within 200 ft")
        print(f"  wrote {Path(summary['png']).name} and "
              f"{Path(summary['svg']).name}")

    (config.OUT_DIR / "figures_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {config.OUT_DIR / 'figures_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
