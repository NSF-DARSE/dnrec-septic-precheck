"""Prepare cached example applications so the demo runs offline.

Textract is the only slow, costly, network dependent step in the pipeline. This
script runs it once for a handful of real harvested permits and writes the result
into the on-disk cache keyed by document hash. Every later run of
"python -m septic review" replays from that cache, which means the demo works with
the wifi unplugged and costs nothing to repeat.

It picks permits that actually carry a document, since only 218 of the 1226
approved permits harvested do, and prefers ones with more pages because a longer
packet has more fields to read.

Usage:
    python scripts/prepare_examples.py --list
    python scripts/prepare_examples.py --count 3
    python scripts/prepare_examples.py --count 3 --dry-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.ingest.textract import TextractClient, document_hash


def candidates(manifest: Path) -> list[dict]:
    """Manifest records that carry at least one document, best first."""
    records = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        documents = record.get("documents") or []
        if not documents:
            continue
        record["_doc_count"] = len(documents)
        record["_bytes"] = sum(d.get("bytes") or 0 for d in documents)
        records.append(record)

    # Prefer several documents and a decent size: more pages, more fields.
    records.sort(key=lambda r: (-r["_doc_count"], -r["_bytes"]))
    return records


def first_key(record: dict) -> str | None:
    for document in record.get("documents") or []:
        key = document.get("s3_key") or document.get("key")
        if key:
            return key
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prepare_examples")
    ap.add_argument("--manifest", type=Path,
                    default=config.OUT_DIR / "manifest_control.jsonl")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--list", action="store_true",
                    help="show the candidates and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be analysed without calling Textract")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(argv)

    config.ensure_dirs()
    if not args.manifest.exists():
        print(f"manifest not found: {args.manifest}")
        return 1

    found = candidates(args.manifest)
    print(f"{len(found)} permits in the manifest carry a document")
    if args.list:
        for record in found[:25]:
            print(f"  {record['detail_id']}  permit {record.get('permitNumber')}  "
                  f"{record['_doc_count']} docs  "
                  f"{record['_bytes'] / 1e6:.1f} MB  "
                  f"{record.get('septicSystemType')}")
        return 0

    examples_dir = config.OUT_DIR / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)

    client = TextractClient()
    prepared = []
    for record in found:
        if len(prepared) >= args.count:
            break
        key = first_key(record)
        if key is None:
            continue
        detail_id = record["detail_id"]
        permit_number = record.get("permitNumber") or detail_id
        local_pdf = examples_dir / f"permit_{permit_number}_{detail_id}.pdf"

        print(f"\n{detail_id} (permit {permit_number})")
        print(f"  s3 key: {key}")
        if args.dry_run:
            prepared.append({"detail_id": detail_id, "key": key, "dry_run": True})
            continue

        # Keep a local copy so a reviewer can open the packet the report describes.
        if not local_pdf.exists():
            try:
                client.s3.download_file(client.bucket, key, str(local_pdf))
                print(f"  downloaded to {local_pdf.name} "
                      f"({local_pdf.stat().st_size / 1e6:.1f} MB)")
            except Exception as exc:  # noqa: BLE001
                print(f"  download failed: {exc}")
                continue
        else:
            print(f"  already have {local_pdf.name}")

        doc_hash = document_hash(local_pdf.read_bytes())
        if client.cached_by_hash(doc_hash) is not None:
            print(f"  already cached under hash {doc_hash[:12]}")
            prepared.append({
                "detail_id": detail_id, "permit_number": permit_number,
                "pdf": local_pdf.name, "hash": doc_hash, "cached": True,
            })
            continue

        print("  running Textract, this is the slow part")
        analysis = client.analyze(key, timeout=args.timeout, use_cache=True)
        if not analysis.ok:
            print(f"  analysis failed: {analysis.status} {analysis.message or ''}")
            continue
        client.save_to_hash_cache(analysis, doc_hash)
        print(f"  cached {analysis.pages} pages, {len(analysis.blocks)} blocks "
              f"under hash {doc_hash[:12]}")
        prepared.append({
            "detail_id": detail_id, "permit_number": permit_number,
            "pdf": local_pdf.name, "hash": doc_hash,
            "pages": analysis.pages, "blocks": len(analysis.blocks),
        })

    index_path = examples_dir / "examples.json"
    index_path.write_text(json.dumps(prepared, indent=2), encoding="utf-8")
    print(f"\nprepared {len(prepared)} examples, index at {index_path}")
    for item in prepared:
        if item.get("pdf"):
            print(f"  python -m septic review --pdf out/examples/{item['pdf']} --offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
