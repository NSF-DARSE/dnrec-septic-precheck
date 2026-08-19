"""Reading the drawing scale, and turning it into feet per pixel.

This is what makes a pixel position mean something. A well at (107, 767) is a fact
about an image; a well 43 feet from a disposal field is a fact about a property,
and the only thing standing between the two is the scale.

Two scales appear on these sheets and they are not equally trustworthy.

A graphic scale bar is drawn on the sheet, so it scales with the sheet. Photocopy
it, scan it at any resolution, render it at any DPI, crop it: the bar and the
drawing change together and the ratio between them holds. This is the primary
source and it is measured, not read.

A written ratio such as 1" = 100' is only meaningful with a physical inch, which a
PNG does not have. It is usable when we rasterised the PDF ourselves, because then
the DPI is known exactly and an inch is a known number of pixels. For an image of
unknown provenance, a scan or a screenshot, the written ratio is unusable on its
own: the page may have been printed or scanned at any size and the sentence would
still say 1" = 100'. It is kept as a cross check, never as the sole answer.

The division of labour matters. The model reads the bar, meaning it finds it and
says what its labels are and what total distance it spans, which is a semantic
question. Pixel geometry is then measured with plain image analysis, because the
model's coordinate error was measured at roughly 6 percent per axis, and a 6
percent error in a scale bar length is a 6 percent error in every distance derived
from it, compounding into a setback that could pass a rule that should fail. A
threshold cannot be decided by an estimate.

Nothing here decides anything. It produces feet per pixel and the evidence behind
it, and it refuses to produce a number when the evidence is not there.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from .detect import SUPPORTED_FORMATS, VisionUnavailable, image_hash
from .locate import MAX_UPSCALE, PixelBox

SCALE_PROMPT_VERSION = "v1"

# Padding around the model's bar box before measuring, as a fraction of the box's
# long edge. The box is approximate, so the true bar ends may sit outside it.
BAR_PAD = 0.25

# Enlargement target for the bar crop sent to the model, matching locate.py. A
# scale bar and its tick labels are small print.
TARGET_BAR_EDGE = 900

# A bar must be meaningfully wider than it is tall. A scale bar is a long thin
# horizontal object; anything squarer is something else the model has pointed at.
MIN_BAR_ASPECT = 2.5

# Sanity bounds on the answer. A residential septic site plan is drawn somewhere
# between about 1 inch to 10 feet and 1 inch to 400 feet, and at plausible raster
# resolutions that lands well inside this range. Outside it, something has been
# misread and returning nothing is better than returning a scale that will
# silently multiply every distance on the sheet.
MIN_FEET_PER_PIXEL = 0.005
MAX_FEET_PER_PIXEL = 20.0

# How closely the measured bar and the written ratio must agree, as a percentage,
# when a known dpi makes both available. Below this neither is used.
#
# The two are independent instruments and both can be wrong in ways the other
# catches: the bar is wrong when the model points at something that is not the bar,
# and the ratio is wrong when the sheet was reduced onto a smaller page, since a
# physical inch then no longer means what the printed ratio says. Agreement is the
# only evidence available that neither happened.
MIN_CROSS_CHECK_AGREEMENT = 85.0

FIND_BAR_PROMPT = """This image is exactly {w} pixels wide and {h} pixels tall.

It is a sheet from a Delaware septic permit application. Find the drawing scale.

There are two things to look for and they are different:

1. A graphic scale bar: a long thin horizontal bar, often divided into alternating
   black and white segments, with distance labels printed along or beneath it such
   as 0, 50', 100'. Note that the zero is often NOT at the left end; many of these
   bars extend to the left of zero.

2. A written ratio scale, a sentence such as: Scale: 1" = 100'

Return JSON only, no prose and no code fence:

{{
  "bar_found": true,
  "bar": {{"left": 0, "top": 0, "width": 0, "height": 0}},
  "labels_left_to_right": ["100'", "0", "50'", "100'"],
  "left_end_value": 100,
  "right_end_value": 100,
  "zero_position": "left" or "middle" or "none",
  "total_span_value": 200,
  "units": "feet",
  "written_scale": "1\\" = 100'",
  "written_scale_inches": 1,
  "written_scale_distance": 100
}}

Rules you must follow:
- "bar" is a tight integer pixel box around the graphic bar itself, the drawn
  bar only, excluding its printed labels and any caption such as GRAPHIC SCALE.
- "total_span_value" is the total distance the bar represents from its extreme
  LEFT end to its extreme RIGHT end. Work it out from the labels. If the labels
  read 100', 0, 50', 100' then the bar runs 100 feet to the left of zero and 100
  feet to the right of it, so the total span is 200.
- "written_scale_inches" and "written_scale_distance" decompose the written ratio:
  for 1" = 100' they are 1 and 100. Use null if there is no written ratio.
- If there is no graphic scale bar, set "bar_found" to false and still report any
  written scale you find.
- If the sheet says NOT TO SCALE and has no bar, set "bar_found" to false and
  "written_scale" to "NOT TO SCALE".
"""

MEASURE_BAR_PROMPT = """This image is exactly {w} pixels wide and {h} pixels tall.

It is a close crop of a graphic scale bar from an engineering drawing, enlarged.

Return JSON only:

{{"left": 0, "top": 0, "width": 0, "height": 0, "found": true}}

giving a tight integer pixel bounding box around the drawn bar itself: the
rectangular bar with its alternating segments, from its extreme left end to its
extreme right end.

Rules:
- Bound only the bar. Exclude the printed distance labels above or below it, and
  exclude any caption such as GRAPHIC SCALE.
- The box must span the bar's full length, end to end.
- If there is no bar in this crop, return {{"found": false}}.
"""


class ScaleUnavailable(RuntimeError):
    """No trustworthy scale could be established for this sheet.

    Raised rather than returning a guess. Every distance on the sheet depends on
    this number, so a wrong one is worse than none: a reviewer told the scale could
    not be read will measure the drawing, and a reviewer given a plausible wrong
    scale will not.
    """


@dataclass
class ScaleBar:
    """A graphic scale bar, located by the model and measured by image analysis."""

    pixel: PixelBox
    total_span_value: float
    units: str
    labels: list[str] = field(default_factory=list)
    measured_length_px: int = 0
    measured_by: str = "ink_projection"
    # What the model estimated before measurement, kept so the two can be compared
    # in the output rather than only inside the rejection check.
    model_reported_width_px: int = 0
    # True when the measured run hit the edge of its search window, which makes
    # the length a lower bound. See measure_bar_extent.
    clipped: bool = False

    def to_json(self) -> dict:
        return {
            "pixel": self.pixel.to_json(),
            "labels_left_to_right": self.labels,
            "total_span_value": self.total_span_value,
            "units": self.units,
            "measured_length_px": self.measured_length_px,
            "model_reported_width_px": self.model_reported_width_px,
            "clipped": self.clipped,
            "measured_by": self.measured_by,
        }


@dataclass
class Scale:
    """Feet per pixel, and the evidence for it."""

    feet_per_pixel: float
    source: str                      # graphic_bar | ratio_and_dpi
    bar: ScaleBar | None = None
    written_scale: str | None = None
    dpi: float | None = None
    ratio_feet_per_pixel: float | None = None
    agreement_pct: float | None = None
    warnings: list[str] = field(default_factory=list)

    def pixels_to_feet(self, pixels: float) -> float:
        return pixels * self.feet_per_pixel

    def feet_to_pixels(self, feet: float) -> float:
        return feet / self.feet_per_pixel

    def to_json(self) -> dict:
        return {
            "feet_per_pixel": round(self.feet_per_pixel, 6),
            "pixels_per_foot": round(1.0 / self.feet_per_pixel, 4),
            "source": self.source,
            "written_scale": self.written_scale,
            "dpi": self.dpi,
            "ratio_feet_per_pixel": (
                round(self.ratio_feet_per_pixel, 6)
                if self.ratio_feet_per_pixel is not None else None
            ),
            "cross_check_agreement_pct": (
                round(self.agreement_pct, 1) if self.agreement_pct is not None else None
            ),
            "bar": self.bar.to_json() if self.bar else None,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Geometry: measuring the bar without asking the model for coordinates
# ---------------------------------------------------------------------------

def measure_bar_extent(image_path: Path, approx: PixelBox,
                       pad_fraction: float = BAR_PAD
                       ) -> tuple[int, int, int, int, int, str, bool]:
    """Measure a scale bar's pixel length by projecting its ink.

    Returns (left_x, right_x, length, top_y, band_height, method, clipped) in full
    page pixels. `clipped` says the run reached the edge of the search window, so
    the length is a lower bound rather than a measurement.

    The vertical extent is returned alongside the horizontal one because the
    band this function grows to take its measurement *is* the bar, so it is the
    only honest description of where the bar sits. Reporting the measured length
    against the model's estimated top produced a hybrid box that was correct in x
    and wrong in y: on a real sheet the model put the bar at y 393 to 405 while
    the ink was at 354 to 380, so the box drawn for a human to check the scale
    against was framing the tick labels and the words GRAPHIC SCALE instead of
    the bar. The length was right and the picture said otherwise, which is the
    worst way for a measurement to be wrong.

    How it works, and why this rather than a model estimate. A drawn scale bar has
    a continuous horizontal rule along its top and bottom edge, so of all the rows
    in a crop around it, the bar's own edge rows contain by far the most dark
    pixels. Finding the darkest row locates the bar; the extent of dark pixels
    along that row and its neighbours is the bar's length. It is a few lines of
    arithmetic, it is deterministic, and it is exact to the pixel, which is what a
    number that multiplies every distance on the sheet needs to be.

    The printed labels sit above or below the bar and are excluded by construction:
    a row of text has far fewer dark pixels than a solid rule, so the search never
    selects one.
    """
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        page = np.array(im.convert("L"))
    page_h, page_w = page.shape

    pad = int(pad_fraction * max(approx.width, approx.height))
    x0 = max(0, approx.left - pad)
    y0 = max(0, approx.top - pad)
    x1 = min(page_w, approx.right + pad)
    y1 = min(page_h, approx.bottom + pad)
    crop = page[y0:y1, x0:x1]
    if crop.size == 0:
        raise ScaleUnavailable("the scale bar crop region was empty")

    dark = crop < 160
    row_counts = dark.sum(axis=1)
    if row_counts.max() == 0:
        raise ScaleUnavailable("no ink found where the scale bar was reported")

    # Find the bar's full vertical extent by seeding at its densest row and growing
    # outwards while rows still carry substantial ink.
    #
    # Selecting the densest rows directly does not work, and the failure is
    # specific to how these bars are drawn. A graphic scale bar is a two row
    # checkerboard, and the two rows are not equally inked: on a real sheet the
    # lower row profiled at 158 to 217 dark pixels and the upper at 54 to 81. A
    # threshold at 70 percent of the maximum therefore kept only the lower row, and
    # within a single row of a checkerboard the filled segments alternate with
    # empty ones, so the longest contiguous run was one segment, 104 px, against a
    # true bar length of 207 px. Taking the union of ink over the bar's whole
    # height instead makes every column inked, because a column empty in one row of
    # a checkerboard is filled in the other, and the bar becomes the single
    # unbroken run it visually is.
    seed = int(np.argmax(row_counts))
    floor = max(1, int(0.15 * row_counts.max()))
    top = seed
    while top - 1 >= 0 and row_counts[top - 1] >= floor:
        top -= 1
    bottom = seed
    while bottom + 1 < row_counts.size and row_counts[bottom + 1] >= floor:
        bottom += 1

    band = dark[top:bottom + 1, :]
    col_any = band.any(axis=0)
    if not col_any.any():
        raise ScaleUnavailable("the scale bar band contained no ink")

    # Longest CONTIGUOUS run of inked columns, not the outermost inked columns.
    #
    # This distinction is the whole correctness of the scale. Taking the leftmost
    # and rightmost ink in the band measured a bar on a real sheet as 328 px when
    # it is about 209 px, because the band also crossed the engineer's stamp and a
    # north arrow leader to the left and the sheet border rule to the right. That
    # is a 57 percent scale error, and since the scale multiplies every distance,
    # it would have reported every separation on the sheet about 36 percent short,
    # quietly, with a plausible looking number.
    #
    # A drawn scale bar has continuous rules along its top and bottom edge, so it
    # is one unbroken run. Unrelated ink at the same height is separated from it by
    # white space. Small gaps are bridged first, because a scan can break a thin
    # rule into a dashed one.
    gap_tolerance = max(2, int(0.01 * col_any.size))
    filled = col_any.copy()
    inked = np.where(col_any)[0]
    for a, b in zip(inked[:-1], inked[1:]):
        if b - a <= gap_tolerance:
            filled[a:b] = True

    runs: list[tuple[int, int]] = []
    start = None
    for idx, on in enumerate(filled):
        if on and start is None:
            start = idx
        elif not on and start is not None:
            runs.append((start, idx - 1))
            start = None
    if start is not None:
        runs.append((start, filled.size - 1))
    if not runs:
        raise ScaleUnavailable("no contiguous scale bar run was found")

    # Prefer the longest run that actually overlaps where the model said the bar
    # was. Longest alone would happily select a sheet border rule.
    approx_lo = approx.left - x0
    approx_hi = approx.right - x0

    def overlaps(run: tuple[int, int]) -> bool:
        return run[1] >= approx_lo and run[0] <= approx_hi

    candidates = [r for r in runs if overlaps(r)] or runs
    run = max(candidates, key=lambda r: r[1] - r[0])

    # Whether the run was cut off by the crop rather than by the end of the bar. A
    # clipped run is a lower bound, not a length, and it is how a mislocated box
    # yields a plausible wrong number: the crop lands beside the bar and whatever
    # ink fits measures cleanly. Reported rather than corrected.
    #
    # Widening the crop was tried here and removed. On a 216 dpi render it returned
    # 1846 px, the whole page width, because at that crop size the gap bridging
    # tolerance is 18 px and unrelated ink merges into one run. A wrong box cannot be
    # repaired by looking at more of the page around it.
    clipped = bool(run[0] <= 1 or run[1] >= filled.size - 2)

    left_x = int(x0 + run[0])
    right_x = int(x0 + run[1])
    length = right_x - left_x
    if length < 8:
        raise ScaleUnavailable(
            f"the measured scale bar is only {length} px long, which is too short "
            f"to derive a scale from"
        )

    # The model's box is a weak prior, but a measurement that disagrees with it
    # wildly means the run selected is not the bar. Reported rather than used.
    if approx.width > 0:
        ratio = length / approx.width
        if not (0.5 <= ratio <= 2.0):
            raise ScaleUnavailable(
                f"the measured bar is {length} px but the model located a bar "
                f"{approx.width} px wide, a factor of {ratio:.2f}; the measurement "
                f"is not of the bar and was rejected rather than used as a scale"
            )
    top_y = int(y0 + top)
    band_height = int(bottom - top + 1)
    return (left_x, right_x, length, top_y, band_height,
            "ink_projection_longest_run", clipped)


# ---------------------------------------------------------------------------
# Reading the scale
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict:
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.DOTALL)
    if fence:
        body = fence.group(1).strip()
    match = re.search(r"\{.*\}", body, re.DOTALL)
    if not match:
        raise ScaleUnavailable("the model returned no JSON when asked for the scale")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ScaleUnavailable(f"the scale JSON did not parse: {exc}") from exc


def _to_feet(value: float, units: str) -> float:
    """Normalise a stated distance to feet."""
    u = (units or "feet").strip().lower()
    if u in ("ft", "feet", "foot", "'"):
        return value
    if u in ("m", "meter", "meters", "metre", "metres"):
        return value * 3.280839895
    if u in ("in", "inch", "inches", '"'):
        return value / 12.0
    if u in ("yd", "yard", "yards"):
        return value * 3.0
    raise ScaleUnavailable(f"unrecognised scale units {units!r}")


class ScaleReader:
    """Find and measure the drawing scale on a sheet."""

    def __init__(self, client=None, model_id: str | None = None,
                 cache_dir: Path | None = None):
        self.model_id = model_id or config.BEDROCK_VISION_MODEL
        self.cache_dir = Path(cache_dir or config.CACHE_DIR) / "vision-scale"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._session = None

    @property
    def client(self):
        if self._client is None:
            if self._session is None:
                self._session = config.session()
            self._client = self._session.client("bedrock-runtime")
        return self._client

    def _cache_path(self, img_hash: str) -> Path:
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "-", self.model_id)
        return self.cache_dir / f"sha256-{img_hash}.{safe_model}.{SCALE_PROMPT_VERSION}.json"

    def cached_for_image(self, path: Path) -> Scale | None:
        p = self._cache_path(image_hash(Path(path).read_bytes()))
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        bar = None
        if payload.get("bar"):
            b = payload["bar"]
            px = b["pixel"]
            bar = ScaleBar(
                pixel=PixelBox(
                    left=px["left"], top=px["top"], width=px["width"],
                    height=px["height"],
                    image_width=payload.get("image_width", 1),
                    image_height=payload.get("image_height", 1),
                ),
                total_span_value=b["total_span_value"],
                units=b["units"],
                labels=b.get("labels_left_to_right", []),
                measured_length_px=b.get("measured_length_px", 0),
                measured_by=b.get("measured_by", "ink_projection"),
                model_reported_width_px=b.get("model_reported_width_px", 0),
                clipped=b.get("clipped", False),
            )
        return Scale(
            feet_per_pixel=payload["feet_per_pixel"],
            source=payload["source"],
            bar=bar,
            written_scale=payload.get("written_scale"),
            dpi=payload.get("dpi"),
            ratio_feet_per_pixel=payload.get("ratio_feet_per_pixel"),
            agreement_pct=payload.get("cross_check_agreement_pct"),
            warnings=payload.get("warnings", []),
        )

    def _save(self, scale: Scale, img_hash: str, width: int, height: int) -> Path:
        target = self._cache_path(img_hash)
        payload = scale.to_json()
        payload["image_hash"] = img_hash
        payload["image_width"] = width
        payload["image_height"] = height
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target

    def _ask(self, prompt: str, payload: bytes, image_format: str,
             max_tokens: int = 1200) -> dict:
        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=[{"role": "user", "content": [
                    {"image": {"format": image_format, "source": {"bytes": payload}}},
                    {"text": prompt},
                ]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
            return _parse_json(response["output"]["message"]["content"][0]["text"])
        except ScaleUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VisionUnavailable(
                f"{self.model_id} could not be invoked: {type(exc).__name__}: {exc}"
            ) from exc

    def read(self, path: Path, dpi: float | None = None,
             use_cache: bool = True) -> Scale:
        """Establish feet per pixel for one sheet.

        dpi is the resolution the image was rasterised at, when that is known
        because we rendered it ourselves. Supplying it enables the written ratio
        scale as an independent cross check. For a scan or a screenshot leave it
        None: the written ratio cannot be converted without it, and assuming a
        value would manufacture a second opinion that is really a guess.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"image not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_FORMATS:
            raise ValueError(f"{path.name} has unsupported format {suffix!r}")

        data = path.read_bytes()
        img_hash = image_hash(data)
        if use_cache:
            hit = self.cached_for_image(path)
            if hit is not None:
                return hit

        from PIL import Image
        with Image.open(path) as im:
            width, height = im.size

        found = self._ask(
            FIND_BAR_PROMPT.format(w=width, h=height), data, SUPPORTED_FORMATS[suffix]
        )

        warnings: list[str] = []
        written = found.get("written_scale")

        # The written ratio, usable only with a known DPI.
        ratio_fpp: float | None = None
        w_inches = found.get("written_scale_inches")
        w_distance = found.get("written_scale_distance")
        if dpi and w_inches and w_distance:
            try:
                feet = _to_feet(float(w_distance), found.get("units", "feet"))
                ratio_fpp = feet / (float(w_inches) * dpi)
            except (TypeError, ValueError, ScaleUnavailable):
                ratio_fpp = None
        elif w_inches and w_distance and not dpi:
            warnings.append(
                f"the sheet states {written!r}, which cannot be converted to feet "
                f"per pixel without knowing the resolution the image was produced "
                f"at, so it was not used"
            )

        bar_fpp: float | None = None
        bar: ScaleBar | None = None

        if found.get("bar_found") and isinstance(found.get("bar"), dict):
            raw = found["bar"]
            try:
                approx = PixelBox(
                    left=int(raw["left"]), top=int(raw["top"]),
                    width=int(raw["width"]), height=int(raw["height"]),
                    image_width=width, image_height=height,
                )
            except (KeyError, TypeError, ValueError):
                approx = None
                warnings.append("the reported scale bar box was not usable")

            if approx is not None:
                if approx.width < approx.height * MIN_BAR_ASPECT:
                    warnings.append(
                        f"the reported scale bar is {approx.width}x{approx.height} px, "
                        f"which is not the long thin shape a scale bar has, so it was "
                        f"not measured"
                    )
                else:
                    span = found.get("total_span_value")
                    try:
                        span_feet = _to_feet(float(span), found.get("units", "feet"))
                    except (TypeError, ValueError, ScaleUnavailable) as exc:
                        span_feet = None
                        warnings.append(f"the bar's total span was unusable: {exc}")

                    if span_feet and span_feet > 0:
                        try:
                            (left_x, right_x, length, bar_top, bar_height,
                             method, was_clipped) = measure_bar_extent(path, approx)
                            bar_fpp = span_feet / length
                            bar = ScaleBar(
                                pixel=PixelBox(
                                    left=left_x, top=bar_top,
                                    width=length, height=bar_height,
                                    image_width=width, image_height=height,
                                ),
                                total_span_value=span_feet,
                                units="feet",
                                labels=found.get("labels_left_to_right") or [],
                                measured_length_px=length,
                                measured_by=method,
                                model_reported_width_px=approx.width,
                                clipped=was_clipped,
                            )
                        except ScaleUnavailable as exc:
                            warnings.append(f"the scale bar could not be measured: {exc}")
        else:
            if (written or "").strip().upper() == "NOT TO SCALE":
                raise ScaleUnavailable(
                    "the sheet is marked NOT TO SCALE, so no distance can be "
                    "derived from it"
                )
            warnings.append("no graphic scale bar was found on this sheet")

        # Choose. The measured bar wins whenever it exists, because it is the only
        # source that survives the sheet being rescaled.
        if bar_fpp is not None:
            chosen, source = bar_fpp, "graphic_bar"
        elif ratio_fpp is not None:
            chosen, source = ratio_fpp, "ratio_and_dpi"
            warnings.append(
                "derived from the written ratio and the known render resolution, "
                "with no graphic bar to confirm it"
            )
        else:
            raise ScaleUnavailable(
                "no usable scale on this sheet: "
                + ("; ".join(warnings) if warnings else "nothing was found")
            )

        if not (MIN_FEET_PER_PIXEL <= chosen <= MAX_FEET_PER_PIXEL):
            raise ScaleUnavailable(
                f"the derived scale of {chosen:.4g} feet per pixel is outside the "
                f"plausible range {MIN_FEET_PER_PIXEL} to {MAX_FEET_PER_PIXEL}, so "
                f"it was rejected as a misreading rather than used"
            )

        agreement = None
        if bar_fpp is not None and ratio_fpp is not None:
            agreement = 100.0 * (1.0 - abs(bar_fpp - ratio_fpp) / max(bar_fpp, ratio_fpp))
            if agreement < MIN_CROSS_CHECK_AGREEMENT:
                # A large disagreement is usually not a fault, which is why this
                # reports rather than refuses.
                #
                # The ordinary cause is reproduction. A 24 by 36 sheet copied onto
                # letter carries a written ratio that is now wrong by the reduction
                # factor, while the bar was reduced along with the drawing and is
                # still exact. That is the whole reason the bar wins, and a sheet
                # reproduced at 70 percent of plot size will always disagree by 30
                # percent. Refusing it would throw away a correct measurement on a
                # normal sheet.
                #
                # So the factor is stated, because it is the useful number: it says
                # how the sheet was reproduced. The bar is still used.
                factor = ratio_fpp / bar_fpp if bar_fpp else 0.0
                warnings.append(
                    f"the measured bar gives {bar_fpp:.4g} feet per pixel and the "
                    f"written ratio gives {ratio_fpp:.4g}, agreeing to "
                    f"{agreement:.0f} percent. That is consistent with the sheet "
                    f"having been reproduced at about {factor * 100:.0f} percent of "
                    f"the size it was plotted at, which leaves the written ratio "
                    f"stale and the bar correct. The bar was used"
                )
                if bar is not None and bar.clipped:
                    # Both wrong at once, and this combination is not benign. A
                    # clipped run means the measurement was cut off by its own crop,
                    # so the bar length is a lower bound rather than a measurement,
                    # and a lower bound that also disagrees with the ratio is not
                    # evidence of reproduction. This is the case that produced
                    # 0.5988 ft/px on a 216 dpi render when the true answer was
                    # 0.4630: the model put the bar 450 px from where it sits, the
                    # crop missed it, and 334 px of unrelated ink measured cleanly.
                    raise ScaleUnavailable(
                        f"the bar measurement was cut off by the edge of its own "
                        f"search window, so {bar.measured_length_px} px is a lower "
                        f"bound rather than a length, and it disagrees with the "
                        f"written ratio at {agreement:.0f} percent. A clipped run "
                        f"that also fails the cross check means the bar was not "
                        f"found, so no scale was established"
                    )

        scale = Scale(
            feet_per_pixel=chosen,
            source=source,
            bar=bar,
            written_scale=written,
            dpi=dpi,
            ratio_feet_per_pixel=ratio_fpp,
            agreement_pct=agreement,
            warnings=warnings,
        )
        self._save(scale, img_hash, width, height)
        return scale
