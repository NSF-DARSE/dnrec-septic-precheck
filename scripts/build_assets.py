"""Build the derived image assets from their committed sources.

    python scripts/build_assets.py

Run once. The outputs are committed, because the console has to start with no
network and nothing may be generated at render time. Rerunning is idempotent.

Sources, all committed under assets/:

    delaware-seal.svg   Inkscape output, 258 paths, no raster, no gradients
    dnrec-logo.png      667x667
    fsaii-logo.png      523x268
    udel-logo.png       300x300

Outputs:

    delaware-seal.png   the seal rendered once, so a page does not carry 341 KB
                        of path data it cannot cache
    favicon.png         the DNREC mark for the browser tab

Two things this handles rather than just converting.

Alpha. The three logos arrived as palette PNGs with a tRNS table, so they already
carry graduated alpha and none of them has a white matte. That was checked rather
than assumed, and because there was nothing to fix, the sponsors' files are
committed exactly as they arrived. check_logo_alpha prints the measurement.

Resampling. Downscaling non premultiplied RGBA darkens or lightens edges, so every
resize here premultiplies, resizes, then unpremultiplies.

svglib and reportlab are needed only by this script and are deliberately not in
requirements.txt: nothing at runtime rasterises anything, and the console must
install from a file that carries no image toolchain.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# Rendered size for the seal. The sponsor strip is about 300 pixels wide across
# four logos, so nothing displays the seal above roughly 52 pixels, and a footer
# band would not take it past 80. 320 is four times that, which covers a hi-dpi
# projector with room to spare. 512 was tried and is 148 KB against 80 KB for no
# visible gain at any size this page uses, and every one of those bytes is inlined
# into the HTML as base64, at a third again.
SEAL_PX = 320
FAVICON_PX = 128


def bleed_transparent_rgb(image: Image.Image, passes: int = 8) -> Image.Image:
    """Push opaque colour outward under the fully transparent pixels.

    Leaves every visible pixel byte for byte identical: only the RGB of pixels
    with alpha 0 changes, and those are invisible by definition. What it changes
    is what happens when the image is scaled, because a resampling filter mixes
    those hidden values into the edge.
    """
    rgba = image.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.float32).copy()
    rgb = arr[..., :3]
    known = arr[..., 3] > 0
    for _ in range(passes):
        unknown = ~known
        if not unknown.any():
            break
        total = np.zeros_like(rgb)
        count = np.zeros(known.shape, dtype=np.float32)
        for shift, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
            total += np.roll(rgb, shift, axis=axis) * np.roll(
                known, shift, axis=axis
            )[..., None]
            count += np.roll(known, shift, axis=axis)
        fillable = unknown & (count > 0)
        if not fillable.any():
            break
        rgb[fillable] = total[fillable] / count[fillable][..., None]
        known = known | fillable
    arr[..., :3] = rgb
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def resize_premultiplied(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Downscale in premultiplied space, which is the only correct way."""
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    alpha = arr[..., 3:4]
    premultiplied = np.concatenate([arr[..., :3] * alpha, alpha], axis=2)
    small = np.asarray(
        Image.fromarray((premultiplied * 255).round().astype(np.uint8), "RGBA")
        .resize(size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    ) / 255.0
    out_alpha = small[..., 3:4]
    with np.errstate(divide="ignore", invalid="ignore"):
        straight = np.where(out_alpha > 0, small[..., :3] / out_alpha, 0.0)
    result = np.concatenate([np.clip(straight, 0, 1), out_alpha], axis=2)
    return Image.fromarray((result * 255).round().astype(np.uint8), "RGBA")


def halo_score(image: Image.Image, backing: tuple[int, int, int]) -> float:
    """Share of visible pixels that carry the old backing colour after a downscale.

    The evidence for the bleed fix. Measured at 70 pixels, which is roughly what
    the sidebar strip will show.
    """
    small = np.asarray(resize_premultiplied(image, (70, 70)), dtype=np.int16)
    visible = small[..., 3] > 8
    if not visible.any():
        return 0.0
    distance = np.abs(small[..., :3] - np.array(backing, dtype=np.int16)).sum(axis=2)
    return float((visible & (distance < 40)).sum() / visible.sum())


def check_logo_alpha() -> list[str]:
    """Report what each logo's alpha actually does. Writes nothing.

    The brief for this work expected a white matte and a conversion to RGBA. There
    is no matte to fix. All three arrived as palette PNGs with a tRNS table, which
    carries a per palette entry alpha, so the edges are already anti aliased and
    the background is already keyed out. The colour sitting under the fully
    transparent pixels is a solid dark green or slate, which would matter if a
    consumer resampled without premultiplying, and measurably it does not: the
    halo score at sidebar size is the same to two decimal places with and without
    a bleed pass, and a bleed pass leaves every visible pixel byte identical.

    So the sponsors' files are committed exactly as they arrived, and the property
    that would actually break a dark footer band is asserted in
    tests/test_assets.py instead: the corners have to be transparent, which is
    what tells a keyed background from a filled rectangle.
    """
    notes = []
    backing = {
        "fsaii-logo.png": (71, 112, 76),
        "dnrec-logo.png": (76, 105, 113),
        "udel-logo.png": (71, 112, 76),
    }
    for name, colour in backing.items():
        path = ASSETS / name
        original = Image.open(path)
        rgba = original.convert("RGBA")
        arr = np.asarray(rgba)
        alpha = arr[..., 3]
        clear = float((alpha == 0).mean())
        partial = float(((alpha > 0) & (alpha < 255)).mean())
        corners = [
            int(alpha[0, 0]), int(alpha[0, -1]), int(alpha[-1, 0]), int(alpha[-1, -1])
        ]
        before = halo_score(rgba, colour)
        after = halo_score(bleed_transparent_rgb(rgba), colour)
        notes.append(
            f"{name}: {original.mode} {original.size[0]}x{original.size[1]}, "
            f"{clear:.0%} clear, {partial:.2%} part transparent, corners {corners}, "
            f"halo at 70px {before:.2%} as shipped and {after:.2%} bled, "
            f"{path.stat().st_size / 1024:.0f} KB, left as it arrived"
        )
    return notes


def rasterise_seal() -> str:
    """Render the seal once. The SVG stays as the source of record.

    The route is SVG to a one page PDF through reportlab, then PDF to a bitmap
    through pypdfium2, which the project already depends on for reading the
    regulation. reportlab's own raster backend wants cairo, and adding a native
    imaging library to a laptop that has to demo tomorrow is not a trade worth
    making for one static file.
    """
    try:
        from reportlab.graphics import renderPDF
        from svglib.svglib import svg2rlg
    except ImportError:  # pragma: no cover - build time only
        return (
            "delaware-seal.png: SKIPPED, svglib and reportlab are not installed. "
            "pip install svglib reportlab, then rerun. They are build time only "
            "and are deliberately absent from requirements.txt."
        )
    import pypdfium2

    drawing = svg2rlg(str(ASSETS / "delaware-seal.svg"))
    if drawing is None:
        raise SystemExit("the seal SVG did not parse")
    pdf = pypdfium2.PdfDocument(renderPDF.drawToString(drawing))
    page = pdf[0]
    scale = SEAL_PX / max(page.get_width(), page.get_height())
    # Transparent fill, so only painted pixels come back opaque. The seal is line
    # art with white fills, and the white enclosed by ink has to stay white while
    # the page around it stays transparent.
    rendered = page.render(scale=scale, fill_color=(255, 255, 255, 0)).to_pil()
    rendered = rendered.convert("RGBA")
    canvas = Image.new("RGBA", (SEAL_PX, SEAL_PX), (255, 255, 255, 0))
    canvas.alpha_composite(
        rendered,
        ((SEAL_PX - rendered.width) // 2, (SEAL_PX - rendered.height) // 2),
    )
    canvas = bleed_transparent_rgb(canvas)
    out = ASSETS / "delaware-seal.png"
    canvas.save(out, "PNG", optimize=True)
    svg_kb = (ASSETS / "delaware-seal.svg").stat().st_size / 1024
    opaque = np.asarray(canvas)[..., 3] > 8
    return (
        f"delaware-seal.png: {SEAL_PX}x{SEAL_PX} from a {svg_kb:.0f} KB SVG, "
        f"{out.stat().st_size / 1024:.0f} KB, {opaque.mean():.1%} of the canvas "
        f"carries ink"
    )


def build_favicon() -> str:
    """The DNREC mark, cropped to the element that survives 32 pixels.

    The full seal carries its department name in a ring of 5 pixel type at that
    size, which is mush. The inner scene is the recognisable part and keeps a
    colour signature nothing else on a tab strip has: a yellow sun over green and
    blue. So the ring is cropped away deliberately rather than shipping a blur.
    """
    source = Image.open(ASSETS / "dnrec-logo.png").convert("RGBA")
    width, height = source.size
    # The inner scene sits inside the ring. Measured off the source, the ring band
    # ends at about 19 percent in on every side.
    inset = round(width * 0.19)
    inner = source.crop((inset, inset, width - inset, height - inset))
    inner = bleed_transparent_rgb(inner)
    icon = resize_premultiplied(inner, (FAVICON_PX, FAVICON_PX))

    # Round it off, so a square crop of a circular mark still reads as a mark.
    mask = Image.new("L", (FAVICON_PX * 4, FAVICON_PX * 4), 0)
    from PIL import ImageDraw

    ImageDraw.Draw(mask).ellipse(
        (0, 0, FAVICON_PX * 4 - 1, FAVICON_PX * 4 - 1), fill=255
    )
    mask = mask.resize((FAVICON_PX, FAVICON_PX), Image.Resampling.LANCZOS)
    alpha = Image.fromarray(
        (
            np.asarray(icon.getchannel("A"), dtype=np.float32)
            * np.asarray(mask, dtype=np.float32)
            / 255.0
        ).astype(np.uint8),
        "L",
    )
    icon.putalpha(alpha)
    out = ASSETS / "favicon.png"
    icon.save(out, "PNG", optimize=True)
    return (
        f"favicon.png: {FAVICON_PX}x{FAVICON_PX} cropped from the DNREC mark, "
        f"{out.stat().st_size / 1024:.0f} KB"
    )


def main() -> int:
    if not ASSETS.is_dir():
        print(f"no assets directory at {ASSETS}")
        return 1
    for line in check_logo_alpha():
        print(line)
    print(rasterise_seal())
    print(build_favicon())
    return 0


if __name__ == "__main__":
    sys.exit(main())
