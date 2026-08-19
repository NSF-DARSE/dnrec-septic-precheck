"""Locate septic tanks and wells on a sheet in pixels, guided by reference crops.

Prints the pixel position of every symbol found, and optionally draws them onto a
copy of the sheet so the boxes can be checked by eye rather than trusted.

Usage:

    # the real sheets sitting in the style folders
    python scripts/vision_locate.py --test-sheets --overlay

    # one sheet
    python scripts/vision_locate.py --image assets/well_style/dnrec_29.png --overlay

    # a page of a packet, rasterised first
    python scripts/vision_locate.py --pdf out/uploads/packet.pdf --page 8 --overlay

    # what the style guide picked up, without calling Bedrock
    python scripts/vision_locate.py --show-style

    # replay from cache with the network off
    python scripts/vision_locate.py --test-sheets --offline

Writes only to out/vision/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from septic import config  # noqa: E402
from septic.vision import SymbolLocator, VisionUnavailable, overlay, styleguide  # noqa: E402
from septic.vision.detect import SUPPORTED_FORMATS  # noqa: E402

OUT = config.OUT_DIR / "vision"


def render_pdf_page(pdf: Path, page: int, scale: float = 2.0) -> Path:
    """Rasterise one PDF page to PNG. Scale 2.0 is roughly 144 dpi."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    if not 1 <= page <= len(doc):
        raise ValueError(
            f"{pdf.name} has {len(doc)} pages, so page {page} does not exist"
        )
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{pdf.stem}-p{page}.png"
    doc[page - 1].render(scale=scale).to_pil().save(target)
    return target


def draw_overlay(image_path: Path, result, upscale: float = 2.0) -> Path:
    """Delegate to the shared renderer in septic.vision.overlay.

    Not reimplemented here. This script and vision_measure.py draw the same thing,
    and the two would drift apart if each owned its own copy, which is the failure
    the report renderers in this project already warn about.
    """
    return overlay.annotate(
        image_path, result, scale=None,
        out_path=OUT / f"{image_path.stem}-located.png",
        upscale=upscale,
    )


def report(result, verbose: bool = False) -> None:
    origin = "cache" if result.from_cache else "bedrock"
    print(f"\n  {result.source}  [{origin}]  "
          f"{result.image_width}x{result.image_height} px")

    if not result.symbols:
        print("    nothing located on this sheet")
    else:
        for s in sorted(result.symbols,
                        key=lambda x: (not x.is_location, x.label)):
            line = f"    {s.describe()}"
            if s.note:
                line += f"  {s.note!r}"
            print(line)

    counts = result.counts()
    if counts:
        print("    sited: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    skipped = [s for s in result.symbols if not s.is_location]
    if skipped:
        print(f"    {len(skipped)} occurrence(s) were legend or detail, "
              f"not counted as locations")
    if result.message:
        print(f"    notes: {result.message}")
    if verbose and result.raw:
        print(f"    raw:\n{result.raw}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, action="append", default=[],
                    help="a sheet image; repeatable")
    ap.add_argument("--test-sheets", action="store_true",
                    help="use the full sheets found in the style folders")
    ap.add_argument("--pdf", type=Path, help="a PDF to rasterise a page from")
    ap.add_argument("--page", type=int, default=1, help="which PDF page, 1 based")
    ap.add_argument("--scale", type=float, default=2.0,
                    help="render scale for --pdf, 2.0 is about 144 dpi")
    ap.add_argument("--labels", help="comma separated subset of classes")
    ap.add_argument("--model", help="override the vision model id")
    ap.add_argument("--overlay", action="store_true",
                    help="draw the boxes onto a copy of each sheet")
    ap.add_argument("--offline", action="store_true",
                    help="serve only from cache, never call Bedrock")
    ap.add_argument("--no-cache", action="store_true",
                    help="always call Bedrock, ignoring any cached answer")
    ap.add_argument("--no-refine", action="store_true",
                    help="skip the second pass; faster and cheaper, and the boxes "
                         "sit roughly 6 percent up and left of the symbol")
    ap.add_argument("--show-style", action="store_true",
                    help="print what the style guide loaded, then exit")
    ap.add_argument("--verbose", action="store_true",
                    help="also print the raw model response")
    args = ap.parse_args(argv)

    guide = styleguide.load()

    if args.show_style:
        print(f"style guide: {guide.summary()}\n")
        for label in guide.labels:
            crops = guide.for_label(label)
            kind = f"{len(crops)} crop(s)" if crops else "description only"
            print(f"  {label}  [{kind}]")
            print(f"    {guide.descriptions.get(label, '')}")
            for c in crops:
                print(f"      {c.path.name:16s} {c.width}x{c.height}")
        if guide.excluded:
            print(f"\n  excluded {len(guide.excluded)}:")
            for e in guide.excluded:
                print(f"    {e['label']}/{e['path']}: {e['reason']}")
        print(f"\n  fingerprint {guide.fingerprint()}")
        return 0

    targets: list[Path] = list(args.image)
    if args.test_sheets:
        targets += styleguide.find_test_sheets()
    if args.pdf:
        targets.append(render_pdf_page(args.pdf, args.page, args.scale))
    if not targets:
        ap.error("nothing to do: pass --image, --test-sheets, --pdf or --show-style")

    labels = None
    if args.labels:
        labels = [l.strip() for l in args.labels.split(",") if l.strip()]
        unknown = [l for l in labels if l not in guide.labels]
        if unknown:
            ap.error(f"unknown labels {unknown}, choose from {guide.labels}")

    locator = SymbolLocator(model_id=args.model, guide=guide)
    print(f"model      {locator.model_id}")
    print(f"style      {guide.summary()}")
    print(f"classes    {', '.join(labels or guide.labels)}")
    print(f"cache      {locator.cache_dir}")
    print(f"{len(targets)} sheet(s)")

    results = []
    failures = 0
    for path in targets:
        if path.suffix.lower() not in SUPPORTED_FORMATS:
            print(f"\n  {path.name}  SKIPPED  unsupported format {path.suffix!r}")
            continue
        if args.offline:
            hit = locator.cached_for_image(path, refine=not args.no_refine)
            if hit is None:
                print(f"\n  {path.name}  [no cached result, skipped offline]")
                continue
            report(hit, args.verbose)
            if args.overlay:
                print(f"    overlay -> {draw_overlay(path, hit)}")
            results.append(hit)
            continue
        try:
            result = locator.locate(
                path, page=args.page if args.pdf else 1,
                labels=labels, use_cache=not args.no_cache,
                refine=not args.no_refine,
            )
        except (VisionUnavailable, ValueError, FileNotFoundError) as exc:
            print(f"\n  {path.name}  FAILED  {exc}")
            failures += 1
            continue
        report(result, args.verbose)
        if args.overlay:
            print(f"    overlay -> {draw_overlay(path, result)}")
        results.append(result)

    if results:
        OUT.mkdir(parents=True, exist_ok=True)
        summary = OUT / "located.json"
        summary.write_text(
            json.dumps([r.to_json() for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8")
        total = sum(len(r.locations()) for r in results)
        print(f"\n{total} sited symbol(s) across {len(results)} sheet(s) -> {summary}")

    if failures:
        print(f"{failures} sheet(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
