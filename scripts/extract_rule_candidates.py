"""Scan the regulation PDF and write the rule candidate report.

Extraction only. Nothing here writes a threshold into rules_7101.yaml.
"""
import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.rules import candidates as cand

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src" / "septic" / "rules" / "candidates.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, default=config.REGULATION_PDF)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--include-unitless", action="store_true",
                    help="also report numbers with no unit nearby")
    args = ap.parse_args()

    config.ensure_dirs()
    found = cand.extract(args.pdf, require_units=not args.include_unitless)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(cand.render_markdown(found, args.pdf), encoding="utf-8")
    (config.OUT_DIR / "rule_candidates.json").write_text(
        json.dumps([c.to_json() for c in found], indent=2), encoding="utf-8"
    )

    by_section = cand.counts_by_section(found)
    lines = [
        f"candidates: {len(found)}",
        f"sections: {len(by_section)}",
        f"report: {args.out}",
        "",
        "by unit family:",
        *[f"  {unit:<14}{count}" for unit, count in cand.counts_by_unit(found)],
        "",
        "top sections by candidate count:",
        *[f"  {s:<16}{n}" for s, n in sorted(by_section, key=lambda kv: -kv[1])[:25]],
    ]
    text = "\n".join(lines)
    print(text)
    (config.OUT_DIR / "rule_candidates_summary.txt").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
