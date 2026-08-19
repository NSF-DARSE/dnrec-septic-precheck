"""Run Bedrock object detection over local images and print what came back.

This is the local proving step before any of it is deployed. It answers three
questions in one run: whether the account can invoke a vision model at all,
whether the model returns JSON this code can parse, and whether the boxes land
anywhere sensible on the page.

Usage:

    # every image in assets/
    python scripts/vision_detect.py --dir assets

    # specific files
    python scripts/vision_detect.py --image assets/dnrec-logo.png

    # a page of a real packet, rendered to PNG first
    python scripts/vision_detect.py --pdf out/uploads/packet.pdf --page 7

    # replay from cache with the network off
    python scripts/vision_detect.py --dir assets --offline

Nothing here is part of the review pipeline. It is a harness, and it writes only
to out/vision/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from septic import config  # noqa: E402
from septic.vision import (  # noqa: E402
    OBJECT_CLASSES,
    VisionClient,
    VisionUnavailable,
)
from septic.vision.detect import SUPPORTED_FORMATS, image_hash  # noqa: E402

OUT = config.OUT_DIR / "vision"


def render_pdf_page(pdf: Path, page: int, scale: float = 2.0) -> Path:
    """Rasterise one PDF page to PNG so a vision model can be shown it.

    Scale 2.0 is roughly 144 dpi. A scale bar and a hand lettered dimension are
    small on a plan sheet, and at 72 dpi they are the first things to disappear.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    if not 1 <= page <= len(doc):
        raise ValueError(f"{pdf.name} has {len(doc)} pages, so page {page} does not exist")
    bitmap = doc[page - 1].render(scale=scale)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{pdf.stem}-p{page}.png"
    bitmap.to_pil().save(target)
    return target


def report(result, verbose: bool = False) -> None:
    origin = "cache" if result.from_cache else "bedrock"
    print(f"\n  {result.source}  [{origin}]  {result.model_id}")

    if result.scale_text:
        print(f"    scale as written: {result.scale_text}")

    if not result.detections:
        # An empty result is a real answer, not an error. Said plainly so nobody
        # reads it as a failure that needs retrying.
        print("    no objects from the vocabulary were located on this image")
    else:
        for d in sorted(result.detections, key=lambda x: -(x.confidence or 0)):
            line = f"    {d.describe()}"
            if d.note:
                line += f"  note: {d.note!r}"
            print(line)

    if result.message:
        print(f"    discarded: {result.message}")
    if verbose and result.raw:
        print(f"    raw response:\n{result.raw}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, action="append", default=[],
                    help="an image file; repeatable")
    ap.add_argument("--dir", type=Path,
                    help="a directory of images")
    ap.add_argument("--pdf", type=Path,
                    help="a PDF to rasterise a page from")
    ap.add_argument("--page", type=int, default=1,
                    help="which PDF page, 1 based")
    ap.add_argument("--scale", type=float, default=2.0,
                    help="render scale for --pdf, 2.0 is about 144 dpi")
    ap.add_argument("--model", help="override the vision model id")
    ap.add_argument("--offline", action="store_true",
                    help="serve only from cache, never call Bedrock")
    ap.add_argument("--no-cache", action="store_true",
                    help="always call Bedrock, ignoring any cached answer")
    ap.add_argument("--classes", help="comma separated subset of the vocabulary")
    ap.add_argument("--verbose", action="store_true",
                    help="also print the raw model response")
    args = ap.parse_args(argv)

    targets: list[Path] = list(args.image)
    if args.dir:
        targets += sorted(
            p for p in args.dir.iterdir()
            if p.suffix.lower() in SUPPORTED_FORMATS
        )
    if args.pdf:
        targets.append(render_pdf_page(args.pdf, args.page, args.scale))

    if not targets:
        ap.error("nothing to do: pass --image, --dir or --pdf")

    classes = OBJECT_CLASSES
    if args.classes:
        wanted = [c.strip() for c in args.classes.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in OBJECT_CLASSES]
        if unknown:
            ap.error(f"unknown classes {unknown}, choose from {sorted(OBJECT_CLASSES)}")
        classes = {c: OBJECT_CLASSES[c] for c in wanted}

    client = VisionClient(model_id=args.model)
    print(f"model      {client.model_id}")
    print(f"vocabulary {', '.join(sorted(classes))}")
    print(f"cache      {client.cache_dir}")
    print(f"{len(targets)} image(s)")

    results = []
    failures = 0
    for path in targets:
        if args.offline:
            hit = client.cached_for_image(path)
            if hit is None:
                print(f"\n  {path.name}  [no cached result, skipped in offline mode]")
                continue
            report(hit, args.verbose)
            results.append(hit)
            continue
        try:
            result = client.detect_image(
                path, page=args.page if args.pdf else 1,
                classes=classes, use_cache=not args.no_cache,
            )
        except (VisionUnavailable, ValueError, FileNotFoundError) as exc:
            print(f"\n  {path.name}  FAILED  {exc}")
            failures += 1
            continue
        report(result, args.verbose)
        results.append(result)

    if results:
        OUT.mkdir(parents=True, exist_ok=True)
        summary = OUT / "detections.json"
        summary.write_text(
            json.dumps([r.to_json() for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        located = sum(len(r.detections) for r in results)
        print(f"\n{located} detection(s) across {len(results)} image(s) -> {summary}")

    if failures:
        print(f"{failures} image(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
