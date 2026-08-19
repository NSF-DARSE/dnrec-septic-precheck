"""Drawing what was found onto the sheet, for checking it by eye.

This is a verification surface, not a product one. Its whole job is to let somebody
look at a sheet and tell in one glance whether the boxes landed on the right
objects, whether the larger box of a pair got called the tank, whether a legend key
was mistaken for a sited well, and whether the scale bar was measured end to end.
Numbers in a JSON file cannot answer any of those questions, and every bug found in
this pipeline so far was found by drawing it.

It lives in the package rather than in a script because both harnesses draw the
same thing, and the report renderers in this project already carry a warning about
what happens when two surfaces render the same data separately: they drift, and the
one nobody is looking at drifts first.

Conventions, all of them chosen so a mistake is visible rather than plausible:

  solid box       a sited symbol, an actual location on the property
  dashed box      a legend key or a construction detail, not a location
  crosshair       the centre point, which is what distances are measured between
  id label        well_1, well_2 and so on, matching the JSON and the distance table
  green bar       the measured extent of the scale bar, end to end
"""
from __future__ import annotations

from pathlib import Path

# One colour per class. Distinct hues rather than shades, because the tank and the
# distribution box sit next to each other and telling them apart is the point.
COLORS: dict[str, tuple[int, int, int]] = {
    "septic_tank": (214, 24, 24),
    "distribution_box": (240, 138, 0),
    "well": (24, 92, 214),
    "scale_bar": (16, 148, 68),
}
DEFAULT_COLOR = (110, 110, 110)

# Candidate faces for the labels. PIL's built in bitmap font is fixed at a size
# that is unreadable on a 900 px sheet once it has been scaled for viewing, so a
# real face is loaded when one is available and the built in is the fallback.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _dashed_rect(draw, box, colour, width: int, dash: int = 9, gap: int = 6) -> None:
    """A dashed rectangle. Marks an occurrence that is not a location."""
    left, top, right, bottom = box
    x = left
    while x < right:
        end = min(x + dash, right)
        draw.line([x, top, end, top], fill=colour, width=width)
        draw.line([x, bottom, end, bottom], fill=colour, width=width)
        x = end + gap
    y = top
    while y < bottom:
        end = min(y + dash, bottom)
        draw.line([left, y, left, end], fill=colour, width=width)
        draw.line([right, y, right, end], fill=colour, width=width)
        y = end + gap


def _tag(draw, xy, text: str, colour, font) -> None:
    """A label with an opaque plate behind it.

    Plain text over a line drawing is frequently unreadable, and an unreadable id
    defeats the purpose of having ids.
    """
    x, y = xy
    try:
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
    except Exception:  # noqa: BLE001
        right, bottom = x + 8 * len(text), y + 12
        left, top = x, y
    pad = 3
    draw.rectangle([left - pad, top - pad, right + pad, bottom + pad],
                   fill=(255, 255, 255), outline=colour, width=1)
    draw.text((x, y), text, fill=colour, font=font)


def annotate(
    image_path: Path,
    located,
    scale=None,
    out_path: Path | None = None,
    upscale: float = 1.0,
    show_legend: bool = True,
) -> Path:
    """Draw located symbols, and the measured scale bar, onto a copy of the sheet.

    upscale enlarges the output so small symbols and their labels are legible. On a
    885 px sheet a well is around 15 px across, and at 1:1 its box and id are too
    small to judge, which is the situation the overlay exists to fix.
    """
    from PIL import Image, ImageDraw

    image_path = Path(image_path)
    im = Image.open(image_path).convert("RGB")

    if upscale and upscale != 1.0:
        im = im.resize(
            (int(round(im.width * upscale)), int(round(im.height * upscale))),
            Image.LANCZOS,
        )
    k = upscale if upscale else 1.0

    draw = ImageDraw.Draw(im)
    base = max(11, int(round(13 * max(1.0, k) ** 0.6)))
    font = _font(base)
    small = _font(max(9, base - 3))

    stroke = max(2, int(round(2 * k)))
    arm = max(6, int(round(8 * k)))

    # -- the measured scale bar -------------------------------------------
    if scale is not None and getattr(scale, "bar", None) is not None:
        b = scale.bar.pixel
        colour = COLORS["scale_bar"]
        box = [b.left * k, b.top * k, (b.left + b.width) * k, (b.top + b.height) * k]
        draw.rectangle(box, outline=colour, width=stroke)
        # End ticks, so the measured extent is unambiguous. This is the number
        # every distance depends on, and a 57 percent error in it was invisible
        # until it was drawn.
        for x in (b.left * k, (b.left + b.width) * k):
            draw.line([x, box[1] - arm, x, box[3] + arm], fill=colour, width=stroke)
        _tag(draw, (box[0], box[3] + arm + 2),
             f"scale bar {scale.bar.measured_length_px} px "
             f"= {scale.bar.total_span_value:g} ft",
             colour, small)

    # -- symbols ----------------------------------------------------------
    for s in located.symbols:
        colour = COLORS.get(s.label, DEFAULT_COLOR)
        p = s.pixel
        box = [p.left * k, p.top * k, p.right * k, p.bottom * k]

        if s.is_location:
            draw.rectangle(box, outline=colour, width=stroke)
        else:
            faded = tuple(min(255, c + 80) for c in colour)
            _dashed_rect(draw, box, faded, stroke)

        cx, cy = p.center
        cx, cy = cx * k, cy * k
        draw.line([cx - arm, cy, cx + arm, cy], fill=colour, width=stroke)
        draw.line([cx, cy - arm, cx, cy + arm], fill=colour, width=stroke)

        name = s.symbol_id or s.label
        if not s.is_location:
            name += f" ({s.context})"
        if not s.refined:
            name += " *"
        _tag(draw, (box[0], max(0, box[1] - base - 8)), name, colour, font)

    # -- legend -----------------------------------------------------------
    if show_legend:
        lines = []
        if scale is not None:
            lines.append(f"{scale.feet_per_pixel:.4f} ft/px  "
                         f"({1 / scale.feet_per_pixel:.2f} px/ft)  via {scale.source}")
        else:
            lines.append("scale not established")
        counts = located.counts()
        lines.append("sited: " + (", ".join(f"{k_}={v}" for k_, v in sorted(counts.items()))
                                 or "none"))
        skipped = [s for s in located.symbols if not s.is_location]
        if skipped:
            lines.append(f"dashed = legend/detail, not a location ({len(skipped)})")
        if any(not s.symbol_id or not s.refined for s in located.symbols):
            lines.append("* = position not refined, coarser")
        lines.append("boxes and centres are estimates, not survey positions")

        pad = 8
        heights = []
        widths = []
        for line in lines:
            try:
                l_, t_, r_, b_ = draw.textbbox((0, 0), line, font=small)
                widths.append(r_ - l_)
                heights.append(b_ - t_ + 4)
            except Exception:  # noqa: BLE001
                widths.append(9 * len(line))
                heights.append(14)
        panel_w = max(widths) + 2 * pad
        panel_h = sum(heights) + 2 * pad
        draw.rectangle([0, 0, panel_w, panel_h], fill=(255, 255, 255),
                       outline=(60, 60, 60), width=2)
        y = pad
        for line, h in zip(lines, heights):
            draw.text((pad, y), line, fill=(20, 20, 20), font=small)
            y += h

    target = Path(out_path) if out_path else (
        image_path.parent / f"{image_path.stem}-annotated.png"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    im.save(target)
    return target
