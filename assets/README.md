# assets

Images the reviewer console and the HTML report load from disk. Nothing here is
fetched at render time. The console has to look identical on venue wifi that has
failed, so there is no CDN, no web font, and no remote image anywhere in this
project.

## The attribution rule, read this before placing any of these

This tool is not a DNREC product and DNREC has not endorsed it. It was built at
HENnovate 2026 at the University of Delaware.

Putting the DNREC logo or the state seal in the page header, beside the product
title, says visually that this is official state software. It is not, and that
misrepresentation in front of the agency itself is worse than having no logos at
all.

So:

- These marks appear only in a clearly labelled sponsor and attribution strip,
  visually separated from the product identity, under a heading that says what the
  relationship actually is.
- Every logo carries an `alt` attribute naming the organisation.
- No sponsor logo goes in the report body. A reviewer prints or forwards that
  page, and a detached document carrying the state seal reads as an official
  finding. `tests/test_assets.py` pins this.

## Files

| file | what it is | source | how it got here |
| --- | --- | --- | --- |
| `dnrec-logo.png` | Delaware Department of Natural Resources and Environmental Control seal, 667x667, palette PNG with a tRNS table | supplied for the event | committed as it arrived |
| `fsaii-logo.png` | First State AI Institute wordmark, 523x268, palette PNG with a tRNS table | supplied for the event | committed as it arrived |
| `udel-logo.png` | University of Delaware monogram, 300x300, palette PNG with a tRNS table | supplied for the event | committed as it arrived |
| `delaware-seal.svg` | Great Seal of the State of Delaware, 332 KB of Inkscape output, 258 paths | supplied for the event | committed as it arrived, kept as the source of record |
| `delaware-seal.png` | the same seal rendered to 320x320 RGBA, 80 KB | derived from `delaware-seal.svg` | `python scripts/build_assets.py` |
| `favicon.png` | the DNREC mark cropped to its inner scene, 128x128 RGBA | derived from `dnrec-logo.png` | `python scripts/build_assets.py` |

## Two things that were checked rather than assumed

**No white matte.** All three logos arrived as palette PNGs, and a palette PNG can
carry transparency two ways: one fully transparent index, or a tRNS table with an
alpha per entry. These carry the table, so the edges are already anti aliased and
the background is already keyed out. Every corner measures alpha 0 on all three.
A bleed pass over the colour sitting under the transparent pixels was tried and
changed no visible pixel and no measurable halo, so the sponsors' files are
committed byte for byte as they arrived rather than re-encoded for nothing.
`tests/test_assets.py` asserts the corners stay transparent, which is what
distinguishes a keyed background from a white rectangle.

**The FSAII wordmark is white.** It reads only on a dark background. On the white
sidebar all that survives is the small blue Delaware silhouette and the dotted
rule, and the words vanish. Recolouring a sponsor's mark to suit our layout is not
an option, so the sponsor strip has a dark band behind it. That is why
`TOKENS["colour"]["band"]` exists and why the strip is dark rather than matching
the page. The dark band also does the separation work the attribution rule asks
for.

## Why the seal is rasterised

The SVG is 332 KB, larger than the rest of the page put together, and it is
inlined as base64, which adds a third again. At the size it is displayed, about 56
pixels in the strip, none of that detail is visible. `delaware-seal.png` is 80 KB
at 320x320, four times its largest display size, which covers a hi-dpi projector.
The SVG stays in the repository as the source of record and is what the PNG is
rebuilt from.

## Why the favicon is cropped

A favicon renders at 32 pixels. The full DNREC seal carries its department name in
a ring of type that becomes noise at that size, and the whole mark turns to mush.
The inner scene survives, and it has a colour signature nothing else on a tab strip
has: a yellow sun over green and blue. So the ring is cropped away deliberately,
the crop is masked back to a circle so a square file still reads as a round mark,
and it is stored at 128x128 for hi-dpi tabs and bookmark bars.

## Rebuilding

```bash
pip install svglib reportlab      # build time only, not in requirements.txt
python scripts/build_assets.py
```

`svglib` and `reportlab` are deliberately absent from `requirements.txt`. Nothing
at runtime rasterises anything, and the console has to install from a file that
carries no image toolchain. The script prints the alpha measurement for each logo
and rebuilds only the two derived files. It renders through a one page PDF and
`pypdfium2`, which the project already depends on for reading the regulation,
because reportlab's own raster backend wants a native cairo build.
