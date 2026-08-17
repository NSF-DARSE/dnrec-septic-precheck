"""Re-derive document fields from the stored URLs in an existing manifest.

Offline. No HTTP, no S3, no re-download. Writes a v2 manifest and leaves the
original untouched.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.harvest.doc_parse import parse_doc_url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path,
                    default=config.OUT_DIR / "manifest_denied-returned.jsonl")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if not args.manifest.exists():
        print(f"manifest not found: {args.manifest}")
        return 1

    out_path = args.out or args.manifest.with_suffix(".v2.jsonl")
    records = [
        json.loads(line)
        for line in args.manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    before: Counter = Counter()
    after: Counter = Counter()
    total = 0
    parcels_before = 0
    parcels_after = 0

    for record in records:
        for doc in record.get("documents", []):
            total += 1
            before[doc.get("doctype", "unknown")] += 1
            if doc.get("parcel_id"):
                parcels_before += 1

            parsed = parse_doc_url(doc.get("url", ""))
            doc["doctype"] = parsed["doc_type"] or "Other"
            doc["program"] = parsed["program"]
            doc["permit_number"] = parsed["permit_number"]
            doc["description"] = parsed["description"]
            doc["parcel_id"] = parsed["parcel_id"]
            doc["foia"] = parsed["foia"]
            doc["title"] = parsed["title_raw"] or doc.get("title", "")
            if "doc_name" in doc:
                doc["doc_name_raw"] = doc.pop("doc_name")

            after[doc["doctype"]] += 1
            if parsed["parcel_id"]:
                parcels_after += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def table(counter: Counter) -> list[str]:
        return [
            f"  {name:<30}{count:>4}  ({100 * count / total:5.1f}%)"
            for name, count in counter.most_common()
        ] if total else ["  none"]

    other = after.get("Other", 0)
    lines = [
        f"documents: {total} across {len(records)} permits",
        "",
        "before:",
        *table(before),
        "",
        "after:",
        *table(after),
        "",
        f"Other: {other}/{total} ({100 * other / total:.1f}%)" if total else "Other: n/a",
        f"parcel_id before: {parcels_before}/{total}",
        f"parcel_id after:  {parcels_after}/{total}",
        f"output: {out_path}",
    ]
    text = "\n".join(lines)
    print(text)
    (config.OUT_DIR / "backfill_report.txt").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
