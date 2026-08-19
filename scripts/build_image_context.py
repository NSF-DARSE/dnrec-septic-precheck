"""Assemble the small subset of out/ that the container image needs.

The Textract cache is 1.1 GB across 243 documents and out/ as a whole is gitignored
run output. The console only ever reads the demo packets and the cache entries
keyed by their SHA256, which is 10.2 MB, so the image copies from a staged
directory rather than from out/ directly.

    python scripts/build_image_context.py
    docker build -t septic-precheck .

Run it again whenever a demo packet changes, because the cache key is the hash of
the file's bytes and a changed packet has a different key.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
CONTEXT = ROOT / "docker-context"


def main() -> int:
    examples = OUT / "examples"
    if not examples.exists():
        print(f"no {examples}, nothing to stage", file=sys.stderr)
        return 1

    if CONTEXT.exists():
        shutil.rmtree(CONTEXT)
    (CONTEXT / "examples").mkdir(parents=True)
    (CONTEXT / "cache").mkdir(parents=True)

    cache_dir = OUT / "cache" / "textract"
    staged = 0
    missing = []
    total = 0

    for pdf in sorted(examples.glob("*.pdf")):
        shutil.copy2(pdf, CONTEXT / "examples" / pdf.name)
        total += pdf.stat().st_size

        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        # The cache writes an md5-shaped suffix of the sha256 digest.
        hits = list(cache_dir.glob(f"sha256-{digest[:32]}*.json"))
        if not hits:
            missing.append(pdf.name)
            continue
        for hit in hits:
            shutil.copy2(hit, CONTEXT / "cache" / hit.name)
            total += hit.stat().st_size
            staged += 1

    graph = OUT / "reg_graph.json"
    if graph.exists():
        shutil.copy2(graph, CONTEXT / "reg_graph.json")
        total += graph.stat().st_size
    else:
        print("warning: out/reg_graph.json is missing, run: python -m septic graph build")

    examples_json = examples / "examples.json"
    if examples_json.exists():
        shutil.copy2(examples_json, CONTEXT / "examples" / "examples.json")

    print(f"staged {staged} cache entries for "
          f"{len(list((CONTEXT / 'examples').glob('*.pdf')))} packets, "
          f"{total / 1048576:.1f} MB into {CONTEXT.name}/")

    if missing:
        # A packet with no cached analysis will ask for credentials at review
        # time, which is exactly what the container cannot do.
        print("\nno cached analysis for these, so they will not review offline:")
        for name in missing:
            print(f"  {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
