"""Ground truth for symbol positions, and scoring detections against it.

Why this exists before any further tuning. Every accuracy claim made about this
pipeline so far rests on four positions read by eye off one zoomed sheet. That was
enough to catch a 100 px error, and nowhere near enough to tune against: a change
that helps dnrec_53 and hurts everything else would look like progress. Worse, the
temptation is to calibrate against the one sample, which is fitting the sheet
rather than fixing the method.

So this records true positions per sheet and scores detections against them, and
nothing here calls a model or costs anything.

Three subcommands, meant to be used in this order:

    --grid    render the sheet with a labelled pixel grid, so a true centre can be
              read off by eye. There is no GUI here, and a grid is what makes
              coordinates legible without one.

    --init    write a truth file pre-filled with what the detector currently
              reports, so the job is correcting numbers rather than typing them
              from nothing. Every entry is marked unverified until you edit it.

    --eval    score current detections against the truth file, per symbol and in
              aggregate, in pixels and in feet.

Truth lives in testdata/truth/<sheet>.json, which is gitignored along with the
rest of testdata, because these files describe real properties.

Matching is by nearest neighbour within a label class, not by id. Ids are
positional and renumber when positions move, so scoring by id would report a
change in numbering as a change in accuracy.

Because matching is within a class, --eval also cross checks the assignment
between classes and reports any pair that looks interchanged. Within class
matching cannot see a swap by construction: a tank found on a dosing chamber and
a box found on the tank each match their own class and score as two ordinary
errors, which is how a sheet whose components carried each other's names read as
a 14.1 px mean on dnrec_53.

Usage:

    python scripts/vision_truth.py --image testdata/dnrec_53.png --grid
    python scripts/vision_truth.py --image testdata/dnrec_53.png --init
    python scripts/vision_truth.py --image testdata/dnrec_53.png --eval
    python scripts/vision_truth.py --eval-all
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from septic import config  # noqa: E402
from septic.vision import SymbolLocator, styleguide  # noqa: E402
from septic.vision.scale import ScaleReader  # noqa: E402

TRUTH_DIR = config.ROOT / "testdata" / "truth"
OUT = config.OUT_DIR / "vision"

GRID_COLOR = (0, 170, 200)
MAJOR_COLOR = (220, 30, 120)


def truth_path(image: Path) -> Path:
    return TRUTH_DIR / f"{Path(image).stem}.json"


# ---------------------------------------------------------------------------
# --grid
# ---------------------------------------------------------------------------

def draw_grid(image: Path, minor: int = 25, major: int = 100,
              upscale: float = 2.0) -> Path:
    """Render the sheet with a labelled coordinate grid.

    Labels go on the major lines only. Labelling every minor line turns the sheet
    into unreadable text, and the minor lines are there to count against the
    nearest labelled one.
    """
    from PIL import Image, ImageDraw

    from septic.vision.overlay import _font

    im = Image.open(image).convert("RGB")
    src_w, src_h = im.size
    if upscale != 1.0:
        im = im.resize((int(src_w * upscale), int(src_h * upscale)), Image.LANCZOS)
    draw = ImageDraw.Draw(im)
    font = _font(max(11, int(12 * upscale ** 0.5)))
    k = upscale

    for x in range(0, src_w + 1, minor):
        is_major = x % major == 0
        draw.line([x * k, 0, x * k, im.height],
                  fill=MAJOR_COLOR if is_major else GRID_COLOR,
                  width=2 if is_major else 1)
    for y in range(0, src_h + 1, minor):
        is_major = y % major == 0
        draw.line([0, y * k, im.width, y * k],
                  fill=MAJOR_COLOR if is_major else GRID_COLOR,
                  width=2 if is_major else 1)

    # Labels last, so they sit on top of the lines rather than under them.
    for x in range(0, src_w + 1, major):
        for y in range(0, src_h + 1, major):
            text = f"{x},{y}"
            draw.rectangle([x * k + 2, y * k + 2, x * k + 8 + 7 * len(text),
                            y * k + 18], fill=(255, 255, 255))
            draw.text((x * k + 4, y * k + 3), text, fill=MAJOR_COLOR, font=font)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{Path(image).stem}-grid.png"
    im.save(target)
    return target


# ---------------------------------------------------------------------------
# --init
# ---------------------------------------------------------------------------

def init_truth(image: Path, located, overwrite: bool = False) -> Path:
    """Pre-fill a truth file from current detections.

    Marked unverified so an untouched file cannot be mistaken for ground truth. A
    truth file that is silently just a copy of the detector's output would score a
    perfect result and mean nothing at all, which is the one failure mode this
    whole harness exists to avoid.
    """
    target = truth_path(image)
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} already exists; pass --overwrite to replace it"
        )
    payload = {
        "sheet": Path(image).name,
        "image_width": located.image_width,
        "image_height": located.image_height,
        "verified": False,
        "_instructions": (
            "Correct center_x and center_y to the true centre of each symbol, in "
            "pixels of the original image. Delete any entry that is not really "
            "there and add any the detector missed. Then set verified to true."
        ),
        "symbols": [
            {
                "label": s.label,
                "center_x": s.pixel.center[0],
                "center_y": s.pixel.center[1],
                "note": s.note,
                "context": s.context,
                "from_detector": True,
            }
            for s in located.symbols
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# --eval
# ---------------------------------------------------------------------------

def match(truth_symbols: list[dict], detected) -> tuple[list[dict], list[dict], list]:
    """Greedy nearest neighbour match within each label class.

    Returns (pairs, missed, spurious). Matching within a class only: a well found
    where a septic tank should be is two errors, a miss and a false positive, not
    one mislocated symbol, and scoring it as the latter would hide it.
    """
    pairs: list[dict] = []
    missed: list[dict] = []
    remaining = list(detected)

    for t in truth_symbols:
        candidates = [d for d in remaining if d.label == t["label"]]
        if not candidates:
            missed.append(t)
            continue
        best = min(candidates, key=lambda d: math.dist(
            (d.pixel.center[0], d.pixel.center[1]),
            (t["center_x"], t["center_y"])))
        remaining.remove(best)
        pairs.append({
            "label": t["label"],
            "truth": (t["center_x"], t["center_y"]),
            "found": best.pixel.center,
            "error_px": math.dist(best.pixel.center,
                                  (t["center_x"], t["center_y"])),
            "note": t.get("note"),
            "id": best.symbol_id,
            "refined": best.refined,
            "passes": getattr(best, "refine_passes", 0),
        })
    return pairs, missed, remaining


# Smallest improvement worth calling a swap rather than noise. Both detections
# already have to be individually nearer the other's truth before this is even
# consulted, which is a strict condition on its own, so this only suppresses the
# case where the two totals are effectively tied.
SWAP_MARGIN_PX = 2.0


def find_swaps(pairs: list[dict]) -> list[dict]:
    """Find pairs of different classes whose positions appear interchanged.

    Why this is separate from `match`, and why scoring without it was misleading.
    Matching is nearest neighbour within a label class, so a septic tank found on
    a dosing chamber and a distribution box found on the septic tank each still
    match their own class and each score as a moderate error. On dnrec_53 that
    reported 17.0 px and 10.0 px, a 14.1 px mean, and read as ordinary imprecision
    while the sheet was in fact being described wrongly: the component at each
    location had the other one's name. Cross checking the assignment shows it
    plainly, because each detection is nearer the other's truth than its own,
    27.0 px as labelled against 10.3 px swapped.

    That distinction matters beyond tidiness. A tank and a dosing chamber are not
    interchangeable to a setback rule, so a swap is a wrong answer about what is
    where, not a slightly imprecise one, and a mean error that keeps improving
    while the labels stay crossed is measuring the wrong thing.

    Only cross class assignments are considered. Two wells that traded places are
    matched correctly by nearest neighbour already, and ids are positional, so
    reporting that as a swap would be reporting a renumbering.
    """
    swaps: list[dict] = []
    for i, a in enumerate(pairs):
        for b in pairs[i + 1:]:
            if a["label"] == b["label"]:
                continue
            as_labelled = a["error_px"] + b["error_px"]
            crossed = (math.dist(a["found"], b["truth"])
                       + math.dist(b["found"], a["truth"]))
            nearer_other = (
                math.dist(a["found"], b["truth"]) < a["error_px"]
                and math.dist(b["found"], a["truth"]) < b["error_px"]
            )
            if nearer_other and crossed < as_labelled - SWAP_MARGIN_PX:
                swaps.append({
                    "a_id": a["id"], "b_id": b["id"],
                    "a_label": a["label"], "b_label": b["label"],
                    "as_labelled_px": round(as_labelled, 1),
                    "swapped_px": round(crossed, 1),
                })
    return swaps


def evaluate(image: Path, located, scale=None, quiet: bool = False) -> dict | None:
    target = truth_path(image)
    if not target.exists():
        if not quiet:
            print(f"  no truth file at {target}; run --init first")
        return None
    truth = json.loads(target.read_text(encoding="utf-8"))
    if not truth.get("verified"):
        print(f"  WARNING {target.name} is not marked verified, so it may still be "
              f"a copy of the detector's own output. Scores against it mean nothing "
              f"until it has been corrected by hand.")

    pairs, missed, spurious = match(truth.get("symbols", []), located.symbols)

    print(f"\n  {Path(image).name}  {located.image_width}x{located.image_height} px")
    if pairs:
        for p in sorted(pairs, key=lambda q: -q["error_px"]):
            feet = (f"  ~{p['error_px'] * scale.feet_per_pixel:5.1f} ft"
                    if scale else "")
            passes = f"  passes={p['passes']}" if p["passes"] else ""
            print(f"    {p['label']:17s} truth {p['truth']}  found {p['found']}  "
                  f"err {p['error_px']:5.1f} px{feet}{passes}")
    for m in missed:
        print(f"    {m['label']:17s} truth ({m['center_x']},{m['center_y']})  "
              f"NOT FOUND")
    for s in spurious:
        print(f"    {s.label:17s} found {s.pixel.center}  "
              f"NO MATCHING TRUTH (false positive)")

    swaps = find_swaps(pairs)
    for s in swaps:
        print(f"    SWAPPED  {s['a_id']} and {s['b_id']} look interchanged: "
              f"{s['as_labelled_px']} px as labelled vs {s['swapped_px']} px "
              f"swapped; each is nearer the other's truth")

    errors = [p["error_px"] for p in pairs]
    summary = {
        "sheet": Path(image).name,
        "verified": bool(truth.get("verified")),
        "matched": len(pairs),
        "missed": len(missed),
        "spurious": len(spurious),
        "swapped": len(swaps),
        "mean_error_px": round(sum(errors) / len(errors), 1) if errors else None,
        "max_error_px": round(max(errors), 1) if errors else None,
        "mean_error_feet": (
            round(sum(errors) / len(errors) * scale.feet_per_pixel, 1)
            if errors and scale else None
        ),
        "diagonal_pct": (
            round(sum(errors) / len(errors)
                  / math.hypot(located.image_width, located.image_height) * 100, 2)
            if errors else None
        ),
    }
    if errors:
        print(f"    mean {summary['mean_error_px']} px, max "
              f"{summary['max_error_px']} px, "
              f"{summary['diagonal_pct']}% of the diagonal"
              + (f", mean {summary['mean_error_feet']} ft" if scale else ""))
        if swaps:
            print(f"    the mean above understates this sheet: "
                  f"{len(swaps)} pair(s) are interchanged, so those positions are "
                  f"close to a symbol but carry the wrong label")
    return summary


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, help="the sheet to work on")
    ap.add_argument("--grid", action="store_true",
                    help="render a labelled coordinate grid over the sheet")
    ap.add_argument("--minor", type=int, default=25, help="minor grid spacing px")
    ap.add_argument("--major", type=int, default=100, help="labelled grid spacing px")
    ap.add_argument("--grid-scale", type=float, default=2.0,
                    help="enlarge the grid image so labels are readable")
    ap.add_argument("--init", action="store_true",
                    help="seed a truth file from current detections")
    ap.add_argument("--overwrite", action="store_true",
                    help="allow --init to replace an existing truth file")
    ap.add_argument("--eval", action="store_true",
                    help="score current detections against the truth file")
    ap.add_argument("--eval-all", action="store_true",
                    help="score every sheet that has a truth file")
    ap.add_argument("--with-scale", action="store_true",
                    help="also read the drawing scale so errors are shown in feet")
    ap.add_argument("--no-refine", action="store_true",
                    help="score the coarse first pass instead of refined positions")
    args = ap.parse_args(argv)

    if args.grid:
        if not args.image:
            ap.error("--grid needs --image")
        path = draw_grid(args.image, args.minor, args.major, args.grid_scale)
        print(f"-> {path}")
        print(f"   pink lines every {args.major} px are labelled with their x,y; "
              f"cyan every {args.minor} px")
        print("   read the true centre of each symbol off the grid, then put those "
              "numbers in the truth file")
        return 0

    guide = styleguide.load()
    locator = SymbolLocator(guide=guide)
    reader = ScaleReader()

    def load_for(image: Path):
        """Cached detections only. This harness must never spend money to score."""
        return locator.cached_for_image(image, refine=not args.no_refine)

    if args.eval_all:
        if not TRUTH_DIR.is_dir():
            print(f"no truth directory at {TRUTH_DIR}")
            return 1
        summaries = []
        for tf in sorted(TRUTH_DIR.glob("*.json")):
            truth = json.loads(tf.read_text(encoding="utf-8"))
            sheet = config.ROOT / "testdata" / truth.get("sheet", f"{tf.stem}.png")
            if not sheet.exists():
                print(f"\n  {tf.stem}: sheet {sheet} is missing")
                continue
            located = load_for(sheet)
            if located is None:
                print(f"\n  {tf.stem}: no cached detections; run vision_measure first")
                continue
            scale = reader.cached_for_image(sheet) if args.with_scale else None
            s = evaluate(sheet, located, scale)
            if s:
                summaries.append(s)
        if summaries:
            scored = [s for s in summaries if s["mean_error_px"] is not None]
            verified = [s for s in scored if s["verified"]]
            print(f"\n{len(summaries)} sheet(s), {len(verified)} verified")
            if verified:
                overall = sum(s["mean_error_px"] for s in verified) / len(verified)
                print(f"mean centre error across verified sheets: {overall:.1f} px")
                print(f"missed {sum(s['missed'] for s in verified)}, "
                      f"false positives {sum(s['spurious'] for s in verified)}, "
                      f"swapped pairs {sum(s.get('swapped', 0) for s in verified)}")
            else:
                print("no verified truth files yet, so no meaningful aggregate")
        return 0

    if not args.image:
        ap.error("pass --image, or --eval-all")

    located = load_for(args.image)
    if located is None:
        print(f"no cached detections for {args.image}.\n"
              f"run this first:\n"
              f"  python scripts/vision_measure.py --image {args.image}")
        return 1

    if args.init:
        try:
            path = init_truth(args.image, located, args.overwrite)
        except FileExistsError as exc:
            print(exc)
            return 1
        print(f"-> {path}")
        print("   every position in it is the detector's own guess and is wrong by "
              "roughly the amount we are trying to measure")
        print("   correct center_x / center_y against the --grid image, then set "
              "verified to true")
        return 0

    if args.eval:
        scale = reader.cached_for_image(args.image) if args.with_scale else None
        evaluate(args.image, located, scale)
        return 0

    ap.error("pass one of --grid, --init, --eval, --eval-all")


if __name__ == "__main__":
    raise SystemExit(main())
