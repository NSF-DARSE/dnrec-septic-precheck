"""Locate the symbols, read the scale, emit one JSON with feet per pixel.

This is the whole vision path end to end: find the septic tanks and wells, measure
the drawing scale, and write a single document that carries every symbol's pixel
position together with the feet per pixel needed to turn those positions into
distances.

Usage:

    python scripts/vision_measure.py --image dnrec_53.png
    python scripts/vision_measure.py --image sheet.png --distances
    python scripts/vision_measure.py --pdf packet.pdf --page 7 --scale 3.0
    python scripts/vision_measure.py --image sheet.png --offline

Writes out/vision/<name>-measured.json.

--distances additionally prints the pairwise separations between sited symbols.
Those are reported as approximate on purpose: they inherit the model's coordinate
error, which was measured at several percent of the sheet, so they are a triage aid
and not a survey. The rule engine is not fed from here.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from septic import config  # noqa: E402
from septic.vision import SymbolLocator, VisionUnavailable, overlay, styleguide  # noqa: E402
from septic.vision.scale import ScaleReader, ScaleUnavailable  # noqa: E402

OUT = config.OUT_DIR / "vision"


def render_pdf_page(pdf: Path, page: int, scale: float) -> tuple[Path, float]:
    """Rasterise one page and return the file and its exact DPI.

    The DPI is returned because we know it here and nowhere else can. pypdfium2
    renders at scale x 72 dpi, and knowing that lets the written ratio scale on the
    sheet act as an independent check on the measured graphic bar.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    if not 1 <= page <= len(doc):
        raise ValueError(f"{pdf.name} has {len(doc)} pages, so page {page} does not exist")
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{pdf.stem}-p{page}.png"
    doc[page - 1].render(scale=scale).to_pil().save(target)
    return target, scale * 72.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, help="a sheet image")
    ap.add_argument("--pdf", type=Path, help="a PDF to rasterise a page from")
    ap.add_argument("--page", type=int, default=1, help="which PDF page, 1 based")
    ap.add_argument("--scale", type=float, default=3.0,
                    help="render scale for --pdf; 3.0 is 216 dpi")
    ap.add_argument("--dpi", type=float,
                    help="the resolution --image was produced at, if known; "
                         "enables the written ratio scale as a cross check")
    ap.add_argument("--labels", help="comma separated subset of classes")
    ap.add_argument("--model", help="override the vision model id")
    ap.add_argument("--distances", action="store_true",
                    help="also print approximate pairwise distances in feet")
    ap.add_argument("--overlay", action="store_true",
                    help="write an annotated PNG with every symbol boxed, "
                         "labelled with its id, and the measured scale bar marked")
    ap.add_argument("--overlay-scale", type=float, default=2.0,
                    help="enlarge the annotated PNG by this factor so small "
                         "symbols and their labels are legible; default 2.0")
    ap.add_argument("--offline", action="store_true", help="serve only from cache")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached answers")
    ap.add_argument("--no-refine", action="store_true",
                    help="skip the symbol refinement pass")
    args = ap.parse_args(argv)

    dpi = args.dpi
    if args.pdf:
        target, dpi = render_pdf_page(args.pdf, args.page, args.scale)
        page_no = args.page
    elif args.image:
        target, page_no = args.image, 1
    else:
        ap.error("pass --image or --pdf")

    labels = [l.strip() for l in args.labels.split(",")] if args.labels else None

    guide = styleguide.load()
    locator = SymbolLocator(model_id=args.model, guide=guide)
    reader = ScaleReader(model_id=args.model)

    print(f"sheet   {target}")
    print(f"model   {locator.model_id}")
    print(f"style   {guide.summary()}")
    if dpi:
        print(f"dpi     {dpi:.0f} (known, so the written ratio can cross check)")
    else:
        print("dpi     unknown, so only a graphic scale bar can give a scale")

    # -- symbols ---------------------------------------------------------
    if args.offline:
        located = locator.cached_for_image(target, refine=not args.no_refine)
        if located is None:
            print("\nno cached symbol result for this sheet")
            return 1
    else:
        try:
            located = locator.locate(
                target, page=page_no, labels=labels,
                use_cache=not args.no_cache, refine=not args.no_refine,
            )
        except (VisionUnavailable, ValueError, FileNotFoundError) as exc:
            print(f"\nsymbol location failed: {exc}")
            return 1

    print(f"\n{located.image_width}x{located.image_height} px, "
          f"{len(located.symbols)} symbol(s) found")
    for s in located.symbols:
        print(f"  {s.describe()}" + (f"  {s.note!r}" if s.note else ""))

    # -- scale -----------------------------------------------------------
    scale = None
    scale_error = None
    if args.offline:
        scale = reader.cached_for_image(target)
        if scale is None:
            scale_error = "no cached scale result for this sheet"
    else:
        try:
            scale = reader.read(target, dpi=dpi, use_cache=not args.no_cache)
        except (ScaleUnavailable, VisionUnavailable, ValueError) as exc:
            scale_error = str(exc)

    if scale is None:
        print(f"\nscale: NOT ESTABLISHED\n  {scale_error}")
    else:
        print(f"\nscale: {scale.feet_per_pixel:.4f} feet per pixel "
              f"({1/scale.feet_per_pixel:.2f} px per foot)")
        print(f"  source        {scale.source}")
        if scale.written_scale:
            print(f"  written       {scale.written_scale}")
        if scale.bar:
            print(f"  bar measured  {scale.bar.measured_length_px} px "
                  f"= {scale.bar.total_span_value:g} ft "
                  f"(labels {scale.bar.labels})")
        if scale.ratio_feet_per_pixel is not None:
            print(f"  ratio says    {scale.ratio_feet_per_pixel:.4f} ft/px")
        if scale.agreement_pct is not None:
            print(f"  agreement     {scale.agreement_pct:.0f}%")
        for w in scale.warnings:
            print(f"  warning       {w}")

    # -- combined document ----------------------------------------------
    payload = {
        "sheet": {
            "source": str(target),
            "page": page_no,
            "image_width": located.image_width,
            "image_height": located.image_height,
            "dpi": dpi,
        },
        "model": {
            "id": located.model_id,
            "style": located.style_summary,
            "style_fingerprint": located.style_fingerprint,
        },
        "scale": scale.to_json() if scale else None,
        "scale_error": scale_error,
        "coordinates_are_estimates": True,
        "counts": located.counts(),
        "items": [],
    }

    for s in located.symbols:
        item = s.to_json()
        if scale is not None:
            cx, cy = s.pixel.center
            item["real_world"] = {
                "center_x_feet_from_left": round(scale.pixels_to_feet(cx), 2),
                "center_y_feet_from_top": round(scale.pixels_to_feet(cy), 2),
                "width_feet": round(scale.pixels_to_feet(s.pixel.width), 2),
                "height_feet": round(scale.pixels_to_feet(s.pixel.height), 2),
            }
        payload["items"].append(item)

    if args.distances and scale is not None:
        pairs = []
        for a, b in itertools.combinations(located.locations(), 2):
            ax, ay = a.pixel.center
            bx, by = b.pixel.center
            px = math.dist((ax, ay), (bx, by))
            pairs.append({
                # Ids rather than labels. With two wells on the sheet, a row
                # reading "septic_tank -> well" appeared twice with no way to tell
                # which well it meant, which made the table unusable.
                "from_id": a.symbol_id,
                "to_id": b.symbol_id,
                "from_label": a.label,
                "to_label": b.label,
                "pixels": round(px, 1),
                "feet_approx": round(scale.pixels_to_feet(px), 1),
                "from_note": a.note,
                "to_note": b.note,
                "both_refined": bool(a.refined and b.refined),
            })
        pairs.sort(key=lambda p: p["feet_approx"])
        payload["approximate_distances"] = pairs
        if pairs:
            print("\napproximate distances between sited symbols:")
            for p in pairs:
                flag = "" if p["both_refined"] else "  *"
                print(f"  {p['from_id']:18s} -> {p['to_id']:18s} "
                      f"{p['pixels']:7.1f} px  ~{p['feet_approx']:7.1f} ft{flag}")
            if any(not p["both_refined"] for p in pairs):
                print("  * involves a symbol whose position was not refined")
            print("  these inherit the coordinate error and are triage only, "
                  "not measurements")

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / f"{target.stem}-measured.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\n-> {out_path}")

    if args.overlay:
        png = overlay.annotate(
            target, located, scale=scale,
            out_path=OUT / f"{target.stem}-annotated.png",
            upscale=args.overlay_scale,
        )
        payload["annotated_png"] = str(png)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"-> {png}")

    return 0 if scale is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
