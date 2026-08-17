"""Harvest orchestration.

Per permit: fetch the detail page, parse the Documents grid, download each PDF,
verify the signature, upload to S3, and emit one manifest record joining the CSV
permit fields to the documents. The manifest is what downstream retrieval reads,
so it is written incrementally and flushed per record to survive an interrupted
run.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import csv_index
from .detail import Fetcher, RateLimiter, detail_url, load_detail
from .s3sink import S3Sink, pdf_key


class Log:
    """Line log to stdout and a file. Console output is unreliable to parse."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = path.open("w", encoding="utf-8")
        self.path = path

    def __call__(self, message: str) -> None:
        line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
        with self._lock:
            print(line, flush=True)
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class Harvester:
    def __init__(self, sink: S3Sink, fetcher: Fetcher, log: Log):
        self.sink = sink
        self.fetcher = fetcher
        self.log = log
        self.stats: Counter = Counter()
        self._lock = threading.Lock()

    def bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats[key] += n

    def handle_permit(self, row: dict) -> dict:
        detail_id = row["detail_id"]
        permit = row.get("permitNumber") or "unknown"
        status = row.get("permitStatus") or "unknown"

        record = dict(row)
        record["detail_url"] = detail_url(detail_id)
        record["harvested_at"] = datetime.now(timezone.utc).isoformat()
        record["documents"] = []

        page = load_detail(self.fetcher, detail_id)
        record["fetch_outcome"] = page.outcome
        record["page_bytes"] = page.fetch.length

        if not page.fetch.ok:
            record["error"] = page.fetch.error
            self.bump("permit_fetch_failed")
            self.log(f"[{permit}] FETCH_FAILED {page.fetch.error}")
            return record

        self.bump("permits_ok")
        if not page.documents:
            # Kept distinct because a missing grid is not evidence of no documents.
            self.bump("permits_no_docs")
            self.log(f"[{permit}] status={status} docs=0 outcome={page.outcome}")
            return record

        self.log(
            f"[{permit}] status={status} docs={len(page.documents)} "
            f"types={[d['doctype'] for d in page.documents]}"
        )

        for index, doc in enumerate(page.documents):
            key = pdf_key(detail_id, permit, status, index, doc["doctype"])
            entry = dict(doc)
            entry["s3_key"] = key
            entry["s3_uri"] = self.sink.uri(key)

            if self.sink.dry_run:
                entry["status"] = "dry-run"
                record["documents"].append(entry)
                continue

            try:
                if self.sink.exists(key):
                    entry["status"] = "already-present"
                    self.bump("docs_skipped")
                    record["documents"].append(entry)
                    continue
            except Exception as exc:
                self.log(f"    head_object error {exc}")

            fetched = self.fetcher.get(doc["url"], want_bytes=True)
            if not fetched.ok:
                entry["status"] = "error"
                entry["error"] = fetched.error
                self.bump("docs_failed")
                self.log(f"    FAILED {doc['doctype']}: {fetched.error}")
                record["documents"].append(entry)
                continue

            result = self.sink.put_pdf(
                key,
                fetched.content,
                {
                    "permit-number": permit,
                    "permit-status": status,
                    "detail-id": detail_id,
                    "doctype": doc["doctype"],
                    "parcel-id": doc.get("parcel_id"),
                    "source-url": doc["url"],
                },
            )
            entry.update(
                {
                    "status": result.status,
                    "bytes": result.bytes,
                    "md5": result.md5,
                    "sha256": result.sha256,
                }
            )
            if result.error:
                entry["error"] = result.error

            if result.status == "uploaded":
                self.bump("docs_uploaded")
                self.bump("bytes_uploaded", result.bytes)
                self.log(f"    -> {entry['s3_uri']} ({result.bytes / 1_048_576:.2f} MB)")
            elif result.status == "not-a-pdf":
                self.bump("docs_not_pdf")
                self.log(f"    not a PDF: {doc['doctype']}")
            elif result.status == "error":
                self.bump("docs_failed")
                self.log(f"    upload FAILED {doc['doctype']}: {result.error}")

            record["documents"].append(entry)

        return record


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Harvest permit documents into S3")
    ap.add_argument("--status", nargs="*", default=["Denied", "Application Returned"],
                    help="permitStatus values to harvest, or ALL")
    ap.add_argument("--year-min", type=int, default=config.YEAR_MIN,
                    help="earliest permit year to include, 0 disables the cutoff")
    ap.add_argument("--keep-undated", action="store_true",
                    help="include permits with no parseable date")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--min-interval", type=float, default=config.MIN_REQUEST_INTERVAL)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.ensure_dirs()

    tag = args.tag or ("all" if args.status == ["ALL"] else "-".join(args.status))
    from .s3sink import slug

    tag = slug(tag, 40)
    log = Log(config.OUT_DIR / f"harvest_{tag}.log")
    manifest_path = config.OUT_DIR / f"manifest_{tag}.jsonl"

    log(f"reading {config.PERMIT_CSV.name}")
    selection = csv_index.select_permits(
        statuses=args.status,
        year_min=args.year_min or None,
        keep_undated=args.keep_undated,
        limit=args.limit,
    )
    log(selection.summary())
    log(f"statuses={selection.statuses}")
    if selection.no_year and not args.keep_undated:
        log(f"NOTE {selection.no_year} permits excluded for having no parseable "
            f"date. Pass --keep-undated to include them.")

    if not selection.rows:
        log("nothing to do")
        log.close()
        return 1

    session = config.session()
    identity = session.client("sts").get_caller_identity()
    log(f"identity {identity['Arn']}")

    sink = S3Sink(client=session.client("s3"), dry_run=args.dry_run)
    fetcher = Fetcher(RateLimiter(args.min_interval))
    harvester = Harvester(sink, fetcher, log)

    started = time.time()
    written = 0
    with manifest_path.open("w", encoding="utf-8") as mf:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(harvester.handle_permit, row): row
                for row in selection.rows
            }
            for n, future in enumerate(as_completed(futures), 1):
                try:
                    record = future.result()
                except Exception as exc:
                    log(f"worker crashed: {exc}")
                    continue
                mf.write(json.dumps(record, ensure_ascii=False) + "\n")
                mf.flush()
                written += 1
                if n % 25 == 0:
                    log(f"progress {n}/{len(selection.rows)} "
                        f"({time.time() - started:.0f}s) "
                        f"docs_uploaded={harvester.stats['docs_uploaded']}")

    log("")
    log("=== summary ===")
    log(f"elapsed            {time.time() - started:.0f}s")
    log(f"permits processed  {written}")
    for key in ("permits_ok", "permits_no_docs", "permit_fetch_failed",
                "docs_uploaded", "docs_skipped", "docs_failed", "docs_not_pdf"):
        log(f"{key:<19}{harvester.stats[key]}")
    log(f"{'uploaded':<19}{harvester.stats['bytes_uploaded'] / 1_048_576:,.1f} MB")
    log(f"manifest           {manifest_path}")

    if not args.dry_run:
        try:
            log(f"manifest -> {sink.put_manifest(tag, manifest_path.read_bytes())}")
        except Exception as exc:
            log(f"manifest upload failed: {exc}")

    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
