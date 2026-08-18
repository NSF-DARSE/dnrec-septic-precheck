"""Capture AWS evidence to docs/evidence/ while credentials are valid.

The credentials on this machine are temporary workshop credentials that live in
environment variables and expire in hours. Everything the demo needs from AWS has
to be on disk before that happens. This script writes the evidence files and, more
importantly, seeds the on-disk Textract cache that the review command reads, so a
review runs with no credentials at all.

Writes:
    docs/evidence/s3_inventory.txt      object count, bytes, sample keys
    docs/evidence/textract_sample.txt   real key/value pairs and table cells
    docs/evidence/bedrock_sample.txt    one request and response, verbatim

Usage:
    python scripts/capture_evidence.py
    python scripts/capture_evidence.py --examples 5
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.ingest import layout
from septic.ingest.textract import TextractClient, document_hash

EVIDENCE = config.ROOT / "docs" / "evidence"


def capture_s3_inventory() -> str:
    """Object count, total bytes, and a representative sample of keys."""
    session = config.session()
    s3 = session.client("s3")
    lines: list[str] = []
    add = lines.append

    add("S3 BUCKET INVENTORY")
    add("=" * 72)
    add(f"bucket   {config.S3_BUCKET}")
    add(f"region   {config.AWS_REGION}")
    add("")

    total_objects = 0
    total_bytes = 0
    by_prefix: Counter = Counter()
    bytes_by_prefix: Counter = Counter()
    samples: list[tuple[str, int]] = []

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.S3_BUCKET):
        for obj in page.get("Contents", []) or []:
            total_objects += 1
            size = obj.get("Size", 0)
            total_bytes += size
            key = obj["Key"]
            top = key.split("/")[0]
            by_prefix[top] += 1
            bytes_by_prefix[top] += size
            # Take an even sample across the listing rather than the first N,
            # which would all come from one prefix.
            if total_objects % 40 == 1 and len(samples) < 25:
                samples.append((key, size))

    add(f"total objects  {total_objects}")
    add(f"total bytes    {total_bytes} ({total_bytes / 1e9:.2f} GB)")
    add("")
    add("by top level prefix")
    add(f"  {'prefix':<24}{'objects':>10}{'bytes':>16}")
    for prefix, count in by_prefix.most_common():
        add(f"  {prefix:<24}{count:>10}{bytes_by_prefix[prefix]:>16}")
    add("")
    add(f"representative sample of {len(samples)} keys")
    for key, size in samples:
        add(f"  {size:>10}  {key}")
    add("")

    text = "\n".join(lines)
    (EVIDENCE / "s3_inventory.txt").write_text(text, encoding="utf-8")
    return f"{total_objects} objects, {total_bytes / 1e9:.2f} GB"


def capture_textract_sample(count: int) -> str:
    """Ensure examples are cached by document hash, and excerpt what was read.

    The cache is the part that matters. The excerpt is evidence for a reader.
    """
    client = TextractClient()
    examples_dir = config.OUT_DIR / "examples"
    pdfs = sorted(examples_dir.glob("*.pdf"))
    if not pdfs:
        return "no example PDFs found, run scripts/prepare_examples.py first"

    lines: list[str] = []
    add = lines.append
    add("TEXTRACT SAMPLE OUTPUT")
    add("=" * 72)
    add("StartDocumentAnalysis, asynchronous, FeatureTypes FORMS and TABLES.")
    add("")
    add("Every analysis below is cached on disk under the SHA256 of the document,")
    add("which is what allows the review command to run with no credentials. The")
    add("excerpts are real output, not illustrations.")
    add("")

    cached_count = 0
    for pdf in pdfs[:count]:
        doc_hash = document_hash(pdf.read_bytes())
        analysis = client.cached_by_hash(doc_hash)
        source = "cache"
        if analysis is None:
            print(f"  analysing {pdf.name}, this is the slow part")
            analysis = client.analyze_file(pdf)
            source = "fresh Textract run"
        if analysis is None or not analysis.ok:
            add(f"--- {pdf.name}: analysis unavailable ---")
            add("")
            continue
        cached_count += 1

        document = layout.parse_blocks(analysis.blocks)
        add("-" * 72)
        add(f"document        {pdf.name}")
        add(f"document sha256 {doc_hash}")
        add(f"job id          {analysis.job_id or 'from cache'}")
        add(f"status          {analysis.status} ({source})")
        add(f"pages           {analysis.pages}")
        add(f"blocks          {len(analysis.blocks)}")
        add(f"lines           {len(document.lines)}")
        add(f"form fields     {len(document.fields)}")
        add(f"tables          {len(document.tables)}")
        add("")

        add("  FORM KEY/VALUE PAIRS, first 18 with a non empty value")
        shown = 0
        for form_field in document.fields:
            if not form_field.value.strip():
                continue
            key = form_field.key.strip().rstrip(":")
            add(f"    p{form_field.page}  {key[:40]:<42}= {form_field.value[:34]!r}"
                f"  conf {form_field.confidence:.0f}%")
            shown += 1
            if shown >= 18:
                break
        if not shown:
            add("    none with a value")
        add("")

        table_shown = 0
        for table in document.tables:
            non_empty = [r for r in table.rows if any(c.strip() for c in r)]
            if len(non_empty) < 2:
                continue
            add(f"  TABLE on page {table.page}, {len(table.rows)} rows x "
                f"{len(table.rows[0]) if table.rows else 0} columns")
            for row in non_empty[:6]:
                cells = " | ".join(c.strip()[:18] for c in row)
                add(f"    {cells[:110]}")
            add("")
            table_shown += 1
            if table_shown >= 2:
                break
        if not table_shown:
            add("  no multi row tables detected on this document")
            add("")

    text = "\n".join(lines)
    (EVIDENCE / "textract_sample.txt").write_text(text, encoding="utf-8")
    return f"{cached_count} documents cached by hash and excerpted"


def capture_bedrock_sample() -> str:
    """One invoke of each Bedrock model this project uses, request and response."""
    session = config.session()
    client = session.client("bedrock-runtime")

    lines: list[str] = []
    add = lines.append
    add("BEDROCK INVOKE SAMPLE")
    add("=" * 72)
    add("Both models this project calls, with the exact request and response.")
    add("")
    add("Neither is ever asked whether an application complies. The text model")
    add("rephrases remedy wording only, and it is handed the verdict as an input.")
    add("The embedding model produces vectors for the precedent list, which is")
    add("context for the reviewer and never a verdict.")
    add("")

    # Text model, used for optional remedy rephrasing.
    prompt = (
        "Rewrite each numbered remedy below in plainer language for a homeowner. "
        "Keep every number, unit, and section reference exactly as written. Do not "
        "add, remove, combine, or reorder items. Do not comment on whether the "
        "application should be approved. Return the same count of numbered lines "
        "and nothing else.\n\n"
        "1. Move the disposal area so it is at least 100 feet from every well "
        "shown on the site plan, or document the Department approval that permits "
        "a lesser distance under Exhibit C note a, e, h or i."
    )
    request = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    add("-" * 72)
    add(f"MODEL  {config.BEDROCK_TEXT_MODEL}")
    add("PURPOSE  optional plain language pass on remedy wording")
    add("")
    add("REQUEST")
    add(json.dumps(request, indent=2))
    add("")
    try:
        response = client.invoke_model(
            modelId=config.BEDROCK_TEXT_MODEL,
            body=json.dumps(request),
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        add("RESPONSE")
        add(json.dumps(payload, indent=2)[:3000])
        text_out = "".join(b.get("text", "") for b in payload.get("content", []))
        add("")
        add("EXTRACTED TEXT")
        add(text_out.strip())
        text_ok = True
    except Exception as exc:  # noqa: BLE001
        add(f"RESPONSE  failed: {exc}")
        text_ok = False
    add("")

    # Embedding model, used for the precedent index.
    embed_request = {"inputText": (
        "permitStatus Approved; septicSystemType Gravity; constructionType "
        "New Construction; propUse 4-bedroom; county Sussex; perkRate 40; "
        "flowRate 480; year 2025"
    )}
    add("-" * 72)
    add(f"MODEL  {config.BEDROCK_EMBED_MODEL}")
    add("PURPOSE  embeddings for the local permit index")
    add("")
    add("REQUEST")
    add(json.dumps(embed_request, indent=2))
    add("")
    try:
        response = client.invoke_model(
            modelId=config.BEDROCK_EMBED_MODEL,
            body=json.dumps(embed_request),
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        vector = payload.get("embedding") or []
        add("RESPONSE")
        add(f"  embedding dimensions {len(vector)}")
        add(f"  first 12 values      {[round(v, 6) for v in vector[:12]]}")
        add(f"  inputTextTokenCount  {payload.get('inputTextTokenCount')}")
        embed_ok = True
    except Exception as exc:  # noqa: BLE001
        add(f"RESPONSE  failed: {exc}")
        embed_ok = False
    add("")

    text = "\n".join(lines)
    (EVIDENCE / "bedrock_sample.txt").write_text(text, encoding="utf-8")
    return f"text model {'ok' if text_ok else 'failed'}, embeddings {'ok' if embed_ok else 'failed'}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="capture_evidence")
    ap.add_argument("--examples", type=int, default=5,
                    help="how many example PDFs to cache and excerpt")
    ap.add_argument("--skip-s3", action="store_true")
    ap.add_argument("--skip-textract", action="store_true")
    ap.add_argument("--skip-bedrock", action="store_true")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    if not args.skip_s3:
        print("s3 inventory ...")
        print(f"  {capture_s3_inventory()}")
    if not args.skip_textract:
        print("textract sample ...")
        print(f"  {capture_textract_sample(args.examples)}")
    if not args.skip_bedrock:
        print("bedrock sample ...")
        print(f"  {capture_bedrock_sample()}")

    print(f"\nevidence written to {EVIDENCE}")
    for path in sorted(EVIDENCE.glob("*.txt")):
        print(f"  {path.name:<26}{path.stat().st_size:>8} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
