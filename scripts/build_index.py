"""Build the local permit similarity index.

Embeds one summary line per harvested permit so the review report can show a
reviewer comparable prior permits. Read the module docstring in
src/septic/retrieval/search.py before using this for anything else: outcome based
matching is weak in this corpus and this index is deliberately not wired into any
verdict.

Uses Titan on Bedrock when it is reachable and a local hashing stand-in when it is
not. The backend is recorded in the index file and travels into the report, so a
precedent list built on the stand-in is never presented as a semantic match.

Usage:
    python scripts/build_index.py
    python scripts/build_index.py --manifest out/manifest_denied-returned.jsonl
    python scripts/build_index.py --limit 200 --local
"""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.retrieval.index import build_index, load_manifest, save_index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_index")
    ap.add_argument("--manifest", type=Path, action="append",
                    help="manifest to index, repeatable")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="index only the first N records, for a quick check")
    ap.add_argument("--local", action="store_true",
                    help="skip Bedrock and use the offline stand-in embedder")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    manifests = args.manifest or [
        config.OUT_DIR / "manifest_control.jsonl",
        config.OUT_DIR / "manifest_denied-returned.jsonl",
    ]

    records: list[dict] = []
    for manifest in manifests:
        if not Path(manifest).exists():
            print(f"skipping missing manifest {manifest}")
            continue
        loaded = load_manifest(Path(manifest))
        print(f"{len(loaded):>5} records from {Path(manifest).name}")
        records.extend(loaded)

    if not records:
        print("no records to index")
        return 1

    if args.limit:
        records = records[:args.limit]

    if args.local:
        from septic.retrieval import embed as embed_mod

        def _force_local(texts, client=None):
            raise embed_mod.EmbeddingUnavailable("forced local by --local")

        embed_mod.embed_texts_bedrock = _force_local  # type: ignore

    print(f"embedding {len(records)} permits")
    index = build_index(records)
    path = save_index(index, args.out)

    print(f"backend    {index.backend}")
    print(f"dimensions {index.dimensions}")
    print(f"entries    {len(index)}")
    print(f"written to {path} ({path.stat().st_size / 1e6:.1f} MB)")
    if index.degraded:
        print("NOTE this index uses the offline stand-in embedder, so similarity "
              "is lexical overlap and not semantic. Reports built on it say so.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
