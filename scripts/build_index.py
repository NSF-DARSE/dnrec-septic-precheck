"""Build the local permit similarity index, resumably.

Titan v2 accepts one text per call, so 1460 permits is 1460 round trips. The
earlier version did those serially, printed nothing, and wrote the index only
after the last one, so a failure at record 1400 lost the whole run. That is not
acceptable for work nobody is watching.

This version embeds through a thread pool, checkpoints to a partial file every 50
records, and resumes from that file on restart. A rerun after a failure picks up
where it stopped instead of starting over. Throttling is retried with exponential
backoff inside embed.embed_one rather than being allowed to end the run.

Read the module docstring in src/septic/retrieval/search.py before using this for
anything else: outcome based matching is weak in this corpus and this index is
deliberately not wired into any verdict.

The backend is recorded in both the checkpoint and the final index, and a
checkpoint written by one backend is never continued by another, because vectors
from Titan and from the offline stand-in are not comparable and mixing them would
produce scores that look fine and mean nothing.

Usage:
    python scripts/build_index.py
    python scripts/build_index.py --workers 8 --checkpoint-every 50
    python scripts/build_index.py --limit 200 --local
    python scripts/build_index.py --restart
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.retrieval import embed as embed_mod
from septic.retrieval.index import (
    SUMMARY_FIELDS,
    IndexEntry,
    PermitIndex,
    load_manifest,
    save_index,
    summarize,
)


def checkpoint_path(out: Path) -> Path:
    return out.with_suffix(".partial.json")


def load_checkpoint(path: Path, backend: str) -> dict[str, list[float]]:
    """Vectors already embedded, keyed by detail_id.

    Returns nothing when the checkpoint was written by a different backend. Titan
    vectors and stand-in vectors cannot be compared, and a half and half index
    would score confidently against nothing.
    """
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"  checkpoint at {path.name} is unreadable, starting over")
        return {}
    saved_backend = payload.get("backend")
    if saved_backend and saved_backend != backend:
        print(f"  checkpoint was built with {saved_backend}, this run wants "
              f"{backend}, so it cannot be continued. Starting over.")
        return {}
    vectors = payload.get("vectors") or {}
    print(f"  resuming from {path.name}: {len(vectors)} records already embedded")
    return {k: [float(x) for x in v] for k, v in vectors.items()}


def save_checkpoint(path: Path, backend: str, vectors: dict[str, list[float]]) -> None:
    """Write the partial file. Written to a temp path then moved, so an
    interrupted write cannot leave a truncated checkpoint that looks valid."""
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({
            "backend": backend,
            "count": len(vectors),
            "vectors": {k: [round(x, 6) for x in v] for k, v in vectors.items()},
        }),
        encoding="utf-8",
    )
    temp.replace(path)


def humanize(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_index")
    ap.add_argument("--manifest", type=Path, action="append",
                    help="manifest to index, repeatable")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="index only the first N records, for a quick check")
    ap.add_argument("--workers", type=int, default=embed_mod.DEFAULT_WORKERS)
    ap.add_argument("--checkpoint-every", type=int, default=50)
    ap.add_argument("--local", action="store_true",
                    help="skip Bedrock and use the offline stand-in embedder")
    ap.add_argument("--restart", action="store_true",
                    help="ignore any existing checkpoint and embed everything again")
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

    # Deduplicate on detail_id, since the checkpoint is keyed by it and a repeated
    # id would otherwise make the resume count disagree with the record count.
    seen: set[str] = set()
    unique: list[dict] = []
    for record in records:
        detail_id = str(record.get("detail_id") or "")
        if not detail_id or detail_id in seen:
            continue
        seen.add(detail_id)
        unique.append(record)
    if len(unique) != len(records):
        print(f"  {len(records) - len(unique)} duplicate or unusable records dropped")
    records = unique[:args.limit] if args.limit else unique

    if args.local:
        def _force_local(texts, client=None, workers=8, on_result=None):
            raise embed_mod.EmbeddingUnavailable("forced local by --local")
        embed_mod.embed_texts_bedrock = _force_local  # type: ignore
        backend = "local-hashing-fallback"
    else:
        backend = "bedrock-titan-v2"

    out_path = Path(args.out or (config.OUT_DIR / "permit_index.json"))
    ckpt = checkpoint_path(out_path)
    if args.restart and ckpt.exists():
        ckpt.unlink()
        print("  checkpoint discarded by --restart")

    done = {} if args.restart else load_checkpoint(ckpt, backend)

    summaries = {str(r["detail_id"]): summarize(r) for r in records}
    todo = [r for r in records if str(r["detail_id"]) not in done]
    print(f"{len(records)} records total, {len(done)} already done, "
          f"{len(todo)} to embed, {args.workers} workers")

    if todo:
        started = time.monotonic()
        completed = 0
        pending: dict[str, list[float]] = {}
        todo_ids = [str(r["detail_id"]) for r in todo]
        texts = [summaries[i] for i in todo_ids]

        def on_result(index: int, vector: list[float]) -> None:
            nonlocal completed
            pending[todo_ids[index]] = vector
            completed += 1
            if completed % args.checkpoint_every == 0:
                done.update(pending)
                pending.clear()
                save_checkpoint(ckpt, backend, done)
                elapsed = time.monotonic() - started
                rate = completed / elapsed if elapsed else 0.0
                remaining = (len(todo) - completed) / rate if rate else 0.0
                print(f"  {completed}/{len(todo)}  {rate:.1f}/s  "
                      f"eta {humanize(remaining)}  checkpointed {len(done)}")

        vectors, actual_backend = embed_mod.embed_texts(
            texts, workers=args.workers, on_result=on_result
        )
        done.update(pending)

        if actual_backend != backend:
            # Bedrock was unreachable and the stand-in answered. A checkpoint from
            # the other backend must not be mixed in, so rebuild the whole mapping
            # from this run's vectors only.
            print(f"  backend fell back to {actual_backend}, discarding any "
                  f"{backend} checkpoint to keep vectors comparable")
            backend = actual_backend
            done = {i: v for i, v in zip(todo_ids, vectors)}
        save_checkpoint(ckpt, backend, done)
        elapsed = time.monotonic() - started
        print(f"  embedded {len(todo)} in {humanize(elapsed)} "
              f"({len(todo) / elapsed:.1f}/s)" if elapsed else "")

    entries = []
    for record in records:
        detail_id = str(record["detail_id"])
        vector = done.get(detail_id)
        if vector is None:
            continue
        entries.append(IndexEntry(
            detail_id=detail_id,
            permit_number=record.get("permitNumber"),
            summary=summaries[detail_id],
            vector=vector,
            metadata={
                key: record.get(key) for key in SUMMARY_FIELDS
                if record.get(key) not in (None, "")
            },
        ))

    index = PermitIndex(
        entries=entries,
        backend=backend,
        dimensions=len(entries[0].vector) if entries else 0,
    )
    path = save_index(index, out_path)

    print(f"backend    {index.backend}")
    print(f"dimensions {index.dimensions}")
    print(f"entries    {len(index)}")
    print(f"written to {path} ({path.stat().st_size / 1e6:.1f} MB)")
    if index.degraded:
        print("NOTE this index uses the offline stand-in embedder, so similarity "
              "is lexical overlap and not semantic. Reports built on it say so.")
    if len(entries) == len(records) and ckpt.exists():
        ckpt.unlink()
        print("checkpoint removed, the index is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
