"""Read a permit PDF with either OCR provider and write the result as JSON.

Usage:

    python scripts/ocr_extract.py --pdf permit.pdf --provider bedrock
    python scripts/ocr_extract.py --pdf permit.pdf --provider textract
    python scripts/ocr_extract.py --pdf permit.pdf --provider bedrock --offline
    python scripts/ocr_extract.py --pdf permit.pdf --compare

Writes out/ocr/<stem>-<provider>-ocr.json.

--compare runs both providers over the same document and reports where they
disagree on form field values. That comparison is the evidence the OCR provider
decision in docs/DECISIONS.md is currently missing: it is recorded there as
decided but not measured, and a field level agreement rate is what would let it be
rewritten as measured.

Nothing here decides anything about an application. It reads a document and writes
down what was read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from septic import config  # noqa: E402
from septic.ingest import ocr  # noqa: E402

OUT = config.OUT_DIR / "ocr"


def write(result: ocr.OcrResult, stem: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{stem}-{result.provider}-ocr.json"
    target.write_text(
        json.dumps(result.to_json(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return target


def show(result: ocr.OcrResult, preview: int) -> None:
    print(f"\n{result.summary()}")
    if result.model_id:
        print(f"  model      {result.model_id}")
    if result.job_id:
        print(f"  job        {result.job_id}")
    if not result.geometry_available:
        print("  note       no geometry from this provider, so no value can be "
              "pointed at on the page")
    if not result.confidence_is_calibrated:
        print("  note       confidence is self-reported by the model, not a "
              "recogniser score, so it is not comparable to Textract's")
    for w in result.warnings:
        print(f"  warning    {w}")

    fields = result.document.field_map()
    if fields:
        print(f"\n  first {min(preview, len(fields))} of {len(fields)} form field(s):")
        for key, value in list(fields.items())[:preview]:
            shown = value if len(value) <= 58 else value[:55] + "..."
            print(f"    {key[:42]:42s} = {shown!r}")

    text = result.document.text()
    if text:
        lines = text.splitlines()
        print(f"\n  {len(lines)} line(s) of text, first 5:")
        for line in lines[:5]:
            print(f"    {line[:88]}")


def compare(pdf: Path, offline: bool, preview: int) -> int:
    """Run both providers and report field level disagreement."""
    results: dict[str, ocr.OcrResult] = {}
    for provider in ocr.PROVIDERS:
        try:
            results[provider] = ocr.read(pdf, provider=provider, offline=offline)
        except Exception as exc:  # noqa: BLE001
            print(f"\n{provider}: unavailable, {type(exc).__name__}: {exc}")
    if len(results) < 2:
        print("\nboth providers are needed for a comparison")
        return 1

    a, b = results["textract"], results["bedrock"]
    fa, fb = a.document.field_map(), b.document.field_map()
    shared = sorted(set(fa) & set(fb))
    agree = [k for k in shared if fa[k].strip() == fb[k].strip()]

    print(f"\ncomparison on {pdf.name}")
    print(f"  textract  {len(fa)} field(s), bedrock {len(fb)} field(s), "
          f"{len(shared)} key(s) in common")
    if shared:
        print(f"  agreement {len(agree)}/{len(shared)} "
              f"({100 * len(agree) / len(shared):.0f}%) on shared keys")
    only_a = sorted(set(fa) - set(fb))
    only_b = sorted(set(fb) - set(fa))
    if only_a:
        print(f"  textract only, {len(only_a)}: {', '.join(only_a[:6])}"
              + (" ..." if len(only_a) > 6 else ""))
    if only_b:
        print(f"  bedrock only, {len(only_b)}: {', '.join(only_b[:6])}"
              + (" ..." if len(only_b) > 6 else ""))

    disagree = [k for k in shared if k not in agree]
    if disagree:
        print(f"\n  disagreements, {len(disagree)}, first {min(preview, len(disagree))}:")
        for key in disagree[:preview]:
            print(f"    {key}")
            print(f"      textract {fa[key]!r}")
            print(f"      bedrock  {fb[key]!r}")

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{pdf.stem}-comparison.json"
    target.write_text(json.dumps({
        "document": pdf.name,
        "textract_field_count": len(fa),
        "bedrock_field_count": len(fb),
        "shared_keys": len(shared),
        "agreed": len(agree),
        "agreement_pct": round(100 * len(agree) / len(shared), 1) if shared else None,
        "textract_only": only_a,
        "bedrock_only": only_b,
        "disagreements": [
            {"key": k, "textract": fa[k], "bedrock": fb[k]} for k in disagree
        ],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n-> {target}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", type=Path, required=True, help="the document to read")
    ap.add_argument("--provider", choices=ocr.PROVIDERS,
                    help=f"which OCR provider; defaults to config, currently "
                         f"{config.OCR_PROVIDER}")
    ap.add_argument("--offline", action="store_true",
                    help="serve only from cache, never construct an AWS client")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached reads")
    ap.add_argument("--compare", action="store_true",
                    help="run both providers and report field level disagreement")
    ap.add_argument("--preview", type=int, default=12,
                    help="how many fields or disagreements to print")
    args = ap.parse_args(argv)

    if not args.pdf.exists():
        print(f"no such file: {args.pdf}")
        return 1

    print(f"document  {args.pdf}  ({args.pdf.stat().st_size / 1e6:.2f} MB)")

    if args.compare:
        return compare(args.pdf, args.offline, args.preview)

    provider = args.provider or config.OCR_PROVIDER
    print(f"provider  {provider}")
    try:
        result = ocr.read(args.pdf, provider=provider,
                          use_cache=not args.no_cache, offline=args.offline)
    except Exception as exc:  # noqa: BLE001
        print(f"\nread failed: {type(exc).__name__}: {exc}")
        return 1

    show(result, args.preview)
    target = write(result, args.pdf.stem)
    print(f"\n-> {target}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
