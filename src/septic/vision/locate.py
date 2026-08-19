"""Locating septic tanks and wells on a sheet, in pixels, against a style guide.

This is the style guided, pixel coordinate counterpart to detect.py. Three things
make it different, and each one exists because of something on the real sheets in
assets/well_style.

The model is shown the symbols first. A well on these plans is a circled W and a
septic tank is a rectangle with two lid circles, and those are local draughting
conventions rather than anything a model should be expected to know. The crops in
assets/septic_style and assets/well_style are passed as labelled exemplars ahead of
the sheet. See styleguide.py.

Coordinates are pixels, and the model is told the pixel dimensions. Asking for
fractions of a page and asking for pixels are different questions, and a caller
who wants to crop a region, draw an overlay, or hand a point to a georeferencing
step wants pixels. Normalised values are derived afterwards from the true image
size read off the file, never from the size the model echoes back, because the
echo is just another thing it can get wrong.

A symbol in the legend is not a well on the site. Sheet dnrec_29 carries a circled
W in its LEGEND panel and its actual wells are off the parcel, described only as
"100'+ TO WELLS". Returning the legend key as a well location would put a setback
into a report that the drawing does not support, so every symbol is classified as
legend, sited, or detail, and only sited ones are locations. This is the same
principle as the label discrimination in ingest/extract.py: the risk is not
failing to find a value, it is confidently reporting the wrong one.

Nothing here measures a distance. Two pixel centres plus a scale bar would give
feet, and that conversion is deliberately absent until the scale bar is read and
verified separately.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..ingest.layout import Box
from . import styleguide
from .detect import SUPPORTED_FORMATS, VisionUnavailable, image_hash

# Bumped when the prompt or parsing contract changes. Part of the cache key.
# v2: refinement became an iterative loop with a window that shrinks each pass,
# which changes the coordinates materially, so v1 entries must not be served.
LOCATE_PROMPT_VERSION = "v2"

# How a symbol occurrence is being used on the sheet. Only "sited" is a location.
CONTEXTS = ("sited", "legend", "detail")


@dataclass
class PixelBox:
    """A box in image pixels, with its centre.

    Pixels rather than fractions because that is what the caller asked the model
    for and what a crop or an overlay needs. `to_box` converts to the normalised
    layout.Box space so a symbol can be compared with a Textract word on the same
    sheet.
    """

    left: int
    top: int
    width: int
    height: int
    image_width: int
    image_height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def to_box(self, page: int) -> Box:
        """Normalised 0 to 1 box, divided by the true image size."""
        return Box(
            left=self.left / self.image_width,
            top=self.top / self.image_height,
            width=self.width / self.image_width,
            height=self.height / self.image_height,
            page=page,
        )

    def to_json(self) -> dict:
        cx, cy = self.center
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "center_x": cx,
            "center_y": cy,
        }


@dataclass
class Symbol:
    """One located symbol: what it is, where it is in pixels, how it is being used."""

    label: str
    pixel: PixelBox
    context: str = "sited"
    confidence: float | None = None
    note: str | None = None
    page: int = 1
    # True when a second pass tightened this box. Recorded because a refined box
    # and a first pass box are not the same quality of measurement, and a reviewer
    # comparing two symbols should be able to see which is which.
    refined: bool = False
    # Unique handle such as well_1, well_2. Assigned by LocateResult.assign_ids so
    # that a row in a distance table, an entry in the JSON and a box on the overlay
    # can all be talked about as the same object. Two wells on one sheet are
    # otherwise indistinguishable in any output that names them by label.
    symbol_id: str = ""
    # How many refinement passes actually moved this box. Carried because a
    # position agreed by four successive crops and one produced by a single pass
    # are not the same quality of answer, and a consumer should be able to tell.
    refine_passes: int = 0

    @property
    def is_location(self) -> bool:
        """True only for a symbol actually placed on the site.

        A legend key and a construction detail are both real occurrences of the
        symbol and neither is a place where the object exists.
        """
        return self.context == "sited"

    @property
    def box(self) -> Box:
        return self.pixel.to_box(self.page)

    def to_json(self) -> dict:
        return {
            "id": self.symbol_id,
            "label": self.label,
            "context": self.context,
            "is_location": self.is_location,
            "refined": self.refined,
            "refine_passes": self.refine_passes,
            "pixel": self.pixel.to_json(),
            "normalized": self.box.to_json(),
            "confidence": (
                round(self.confidence, 3) if self.confidence is not None else None
            ),
            "note": self.note,
        }

    def describe(self) -> str:
        cx, cy = self.pixel.center
        conf = (
            f" conf {self.confidence:.2f}" if self.confidence is not None else ""
        )
        where = "" if self.is_location else f" [{self.context}, not a location]"
        mark = "" if self.refined else "  (unrefined)"
        name = self.symbol_id or self.label
        return (
            f"{name:18s} at ({cx}, {cy}) px  "
            f"box {self.pixel.width}x{self.pixel.height}{conf}{where}{mark}"
        )


@dataclass
class LocateResult:
    """Everything one style guided pass produced over one sheet."""

    source: str
    page: int
    model_id: str
    image_width: int
    image_height: int
    symbols: list[Symbol] = field(default_factory=list)
    style_fingerprint: str = "none"
    style_summary: str = ""
    from_cache: bool = False
    message: str | None = None
    raw: str | None = None

    # Same caveat as detect.DetectionResult. A vision model estimates coordinates,
    # it does not regress them. Good enough to crop or point at, not to measure.
    boxes_are_estimates: bool = True

    def assign_ids(self) -> None:
        """Give every symbol a unique handle: well_1, well_2, septic_tank_1.

        Numbered per label in reading order, top to bottom then left to right,
        rather than in the order the model happened to return them. Model order
        carries no meaning and is not guaranteed stable between runs, so numbering
        by it would make well_1 refer to a different well on a rerun of the same
        sheet, and the overlay and the JSON would then disagree about which well is
        which.

        These handles are stable within one result and positional across runs. They
        are for cross referencing a distance row against a box on the overlay, not
        for storing as a durable key: if a later run locates a symbol slightly
        differently, the numbering can change.
        """
        counters: dict[str, int] = {}
        for symbol in sorted(self.symbols, key=lambda s: (s.pixel.center[1],
                                                          s.pixel.center[0])):
            counters[symbol.label] = counters.get(symbol.label, 0) + 1
            symbol.symbol_id = f"{symbol.label}_{counters[symbol.label]}"

    def by_id(self, symbol_id: str) -> Symbol | None:
        for s in self.symbols:
            if s.symbol_id == symbol_id:
                return s
        return None

    def locations(self, label: str | None = None) -> list[Symbol]:
        """Only the sited symbols, which are the only ones that are places."""
        found = [s for s in self.symbols if s.is_location]
        return [s for s in found if s.label == label] if label else found

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.locations():
            out[s.label] = out.get(s.label, 0) + 1
        return out

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "page": self.page,
            "model_id": self.model_id,
            "prompt_version": LOCATE_PROMPT_VERSION,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "boxes_are_estimates": self.boxes_are_estimates,
            "style_fingerprint": self.style_fingerprint,
            "style_summary": self.style_summary,
            "counts": self.counts(),
            "message": self.message,
            "symbols": [s.to_json() for s in self.symbols],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "LocateResult":
        iw = payload.get("image_width", 0)
        ih = payload.get("image_height", 0)
        page = payload.get("page", 1)
        symbols = []
        for entry in payload.get("symbols", []):
            px = entry.get("pixel") or {}
            symbols.append(
                Symbol(
                    label=entry["label"],
                    pixel=PixelBox(
                        left=px.get("left", 0), top=px.get("top", 0),
                        width=px.get("width", 0), height=px.get("height", 0),
                        image_width=iw or 1, image_height=ih or 1,
                    ),
                    context=entry.get("context", "sited"),
                    confidence=entry.get("confidence"),
                    note=entry.get("note"),
                    page=page,
                    refined=entry.get("refined", False),
                    refine_passes=entry.get("refine_passes", 0),
                    symbol_id=entry.get("id", ""),
                )
            )
        result = cls(
            source=payload.get("source", ""),
            page=page,
            model_id=payload.get("model_id", ""),
            image_width=iw,
            image_height=ih,
            symbols=symbols,
            style_fingerprint=payload.get("style_fingerprint", "none"),
            style_summary=payload.get("style_summary", ""),
            from_cache=True,
            message=payload.get("message"),
            boxes_are_estimates=payload.get("boxes_are_estimates", True),
        )
        # Older cache entries predate ids. Reassigning is safe because the rule is
        # deterministic from the positions, so a replayed result gets the same
        # handles the live run produced.
        if any(not s.symbol_id for s in result.symbols):
            result.assign_ids()
        return result


def _prompt(width: int, height: int, labels: list[str],
            descriptions: dict[str, str]) -> str:
    """The instruction. States the pixel geometry and demands the legend distinction."""
    catalogue = "\n".join(
        f"  {label}: {descriptions.get(label, label)}" for label in labels
    )
    return f"""You are reading one sheet from a Delaware septic permit application.

This image is exactly {width} pixels wide and {height} pixels tall. Pixel (0, 0) is
the top left corner and pixel ({width - 1}, {height - 1}) is the bottom right.
Report every coordinate in pixels in that space.

Find every occurrence of these symbols:

{catalogue}

For each occurrence, decide how it is being used on the sheet:
  sited   - drawn at its actual location on the property or site plan
  legend  - a key or legend entry explaining what the symbol means
  detail  - part of a construction detail, cross section, or typical drawing

Return JSON only, no prose and no code fence:

{{
  "image_width": {width},
  "image_height": {height},
  "symbols": [
    {{
      "label": "one of: {', '.join(labels)}",
      "context": "sited or legend or detail",
      "left": 0, "top": 0, "width": 0, "height": 0,
      "confidence": 0.0,
      "note": "any text labelling this symbol, or null"
    }}
  ]
}}

Rules you must follow:
- left, top, width and height are integer pixels, and the box must be a tight
  bounding box around the symbol itself, not the region it sits in.
- Keep every box inside the image: left + width must not exceed {width} and
  top + height must not exceed {height}.
- confidence is your own certainty between 0 and 1.
- Classify context honestly. A symbol inside a LEGEND panel is context legend even
  though it is a real occurrence of the symbol. Getting this wrong is worse than
  missing the symbol, because a legend entry is not a place where the object is.
- Omit anything you do not actually see. An empty symbols list is a correct answer
  for a sheet that has none of them. Do not guess and do not infer a symbol from
  text alone: wording such as "100' TO WELLS" refers to a well that is not drawn
  on this sheet, so it is not an occurrence.
- Use only the labels listed above.
- Where two boxes are drawn next to each other and joined by a pipe, they are a
  septic tank and a distribution box, not two tanks. The larger box is the
  septic_tank and the smaller one is the distribution_box. Return both, each with
  its own tight box. A septic tank does not need to have lid circles drawn in it
  to be a septic tank.
"""


def _parse(text: str, page: int, labels: list[str],
           width: int, height: int) -> tuple[list[Symbol], list[str]]:
    """Read the model's JSON into Symbols, discarding anything unusable."""
    complaints: list[str] = []

    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.DOTALL)
    if fence:
        body = fence.group(1).strip()
    if not body.startswith("{"):
        brace = body.find("{")
        if brace >= 0:
            body = body[brace:]

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return [], [f"model did not return parseable JSON: {exc}"]

    # The model echoes the dimensions back. Checked, not trusted: a mismatch means
    # it was reasoning about a differently sized image than the one on disk, which
    # makes every coordinate it returned suspect.
    echoed_w = payload.get("image_width")
    echoed_h = payload.get("image_height")
    if echoed_w and echoed_h and (echoed_w != width or echoed_h != height):
        complaints.append(
            f"model reported the image as {echoed_w}x{echoed_h} but it is "
            f"{width}x{height}; coordinates may be scaled wrongly"
        )

    symbols: list[Symbol] = []
    for entry in payload.get("symbols") or []:
        if not isinstance(entry, dict):
            complaints.append(f"discarded a symbol that was not an object: {entry!r}")
            continue

        label = str(entry.get("label", "")).strip().lower().replace(" ", "_")
        if label not in labels:
            complaints.append(
                f"discarded label {label!r}, which is not one of {labels}"
            )
            continue

        try:
            left = int(round(float(entry.get("left"))))
            top = int(round(float(entry.get("top"))))
            box_w = int(round(float(entry.get("width"))))
            box_h = int(round(float(entry.get("height"))))
        except (TypeError, ValueError):
            complaints.append(f"discarded {label}: coordinates were not numbers")
            continue

        if box_w <= 0 or box_h <= 0:
            complaints.append(
                f"discarded {label}: box {box_w}x{box_h} px has no area"
            )
            continue
        if not (0 <= left < width and 0 <= top < height):
            complaints.append(
                f"discarded {label}: origin ({left}, {top}) px is outside the "
                f"{width}x{height} image"
            )
            continue
        # Overhang is clipped rather than discarded. A tight box whose edge runs a
        # few pixels past the margin is still the right symbol in the right place,
        # unlike a box whose origin is off the page, which is a coordinate the
        # model did not ground in the image at all.
        if left + box_w > width or top + box_h > height:
            clipped_w = min(box_w, width - left)
            clipped_h = min(box_h, height - top)
            complaints.append(
                f"clipped {label}: box ran {left + box_w - width}x"
                f"{top + box_h - height} px past the edge"
            )
            box_w, box_h = clipped_w, clipped_h

        context = str(entry.get("context", "sited")).strip().lower()
        if context not in CONTEXTS:
            complaints.append(
                f"{label}: context {context!r} is not one of {list(CONTEXTS)}, "
                f"treated as detail so it is not reported as a location"
            )
            # Defaulting to sited would promote an unclassified symbol to a
            # location, which is the one direction that puts a wrong number in a
            # report. Defaulting to detail keeps it visible but inert.
            context = "detail"

        confidence = entry.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            confidence = None

        note = entry.get("note")
        if not isinstance(note, str) or not note.strip():
            note = None

        symbols.append(
            Symbol(
                label=label,
                pixel=PixelBox(
                    left=left, top=top, width=box_w, height=box_h,
                    image_width=width, image_height=height,
                ),
                context=context,
                confidence=confidence,
                note=note,
                page=page,
            )
        )

    return symbols, complaints


# ---------------------------------------------------------------------------
# Second pass refinement
# ---------------------------------------------------------------------------

# How much context to keep around a first pass box when cropping for refinement.
# Fraction of the box's longest edge.
REFINE_PAD = 0.35

# Floor on the crop window, in source pixels, regardless of how small the symbol
# is. A purely proportional pad fails badly at the small end and it failed in
# exactly that way on a real sheet: a well came back as an 18 px box, 0.35 of 18 is
# a 6 px pad, and the resulting 30 by 30 crop was too small for the model to
# identify anything in, so every refinement on that sheet returned "not found"
# and silently kept its unrefined first pass position. A symbol needs surrounding
# drawing to be recognisable, and how much it needs does not shrink just because
# the symbol did.
MIN_CROP_EDGE = 180

# The crop window must be large enough to still contain the symbol after the first
# pass has misplaced it, so it is sized by the expected error rather than by the
# symbol. This fraction is of the page's long edge.
#
# Measured, not guessed. On a real 885 by 1259 site plan the first pass centres
# were off by a mean of 102 px with a consistent leftward bias near 100 px, about
# 11 percent of the width. The window in use was 180 px, giving a half width of
# 90 px, so a symbol 102 px away fell outside its own crop and the refinement was
# asked to find something that was not in the picture. It correctly answered "not
# found" for three of four symbols, and the coarse positions stood.
#
# 0.15 of the long edge gives a half window of 189 px on that sheet, comfortably
# past the observed error, and the enlargement below keeps the symbol legible
# despite the wider view.
ERROR_ALLOWANCE_FRACTION = 0.15

# The crop is upscaled to roughly this on its long edge before being sent. A 16 px
# well symbol is unresolvable no matter how tightly it is cropped; enlarging it is
# what makes it legible. Lanczos, because nearest neighbour on a line drawing
# turns thin strokes into staircases and the symbol stops looking like itself.
#
# Raised alongside the wider error allowance window: a 405 px crop enlarged only to
# 640 leaves a 27 px tank at 43 px, which is the resolution the first pass already
# struggled with.
TARGET_CROP_EDGE = 900

# Ceiling on the enlargement. Past this the image is mostly interpolation and the
# apparent detail is invented rather than recovered.
MAX_UPSCALE = 8.0

# Below this a box has no usable centre to crop around.
MIN_REFINE_EDGE = 4

# How many times to re-crop and re-locate. Each pass views a smaller region, and
# since the error is a roughly fixed fraction of whatever is being viewed, each
# pass shrinks the error by about the same factor.
#
# The fraction was measured three times before this loop was written: 6 percent of
# the diagonal on a 1224 px regulation page, 11 percent on an 885 px site plan, and
# 6.9 percent on that sheet upscaled to 1770 px. Upscaling made the absolute error
# worse rather than better, which is the observation that rules out simply feeding
# bigger images and points at shrinking the view instead.
#
# Predicted against measured, on the 885 x 1259 sheet:
#     pass 1   view 1259 px   predicted ~88 px    measured 116 px
#     pass 2   view  405 px   predicted ~28 px    measured  26 px
#     pass 3   view  150 px   predicted ~10 px
#     pass 4   view   60 px   predicted  ~4 px
# Four passes is where the window reaches the floor below, so more would re-ask
# the same question of the same crop.
MAX_REFINE_PASSES = 4

# Stop early when a pass moves the centre less than this. Two passes agreeing to
# within a couple of pixels have converged, and another call would spend money to
# be told the same thing.
CONVERGENCE_PX = 3

# Absolute floor on the crop window. A window this small around a 15 px symbol is
# already mostly symbol, and cropping tighter removes the surrounding drawing the
# model needs to recognise what it is looking at.
MIN_WINDOW_EDGE = 56

REFINE_PROMPT = """This image is exactly {w} pixels wide and {h} pixels tall.

It is an enlarged crop of a Delaware septic permit site plan. Somewhere in it is
{article} {label}: {description}
{hint}
Return JSON only, no prose and no code fence:

{{"left": 0, "top": 0, "width": 0, "height": 0, "found": true}}

giving a tight integer pixel bounding box around the {label} itself, in this
cropped image's coordinate space.

Rules:
- Bound only the drawn symbol. Exclude its text label, its leader line, dimension
  lines, and any hatching around it. The symbol is what the leader line points AT,
  not the words at the other end of it.
- If more than one {label} appears in this crop, choose the one NEAREST THE CENTRE
  of the image. The crop was centred on the one being asked about.
- Keep the box inside the image: left + width must not exceed {w} and top +
  height must not exceed {h}.
- If there is genuinely no {label} in this crop, return {{"found": false}}.
"""


def _window_for(span: int, prior_view: int, page_edge: int) -> int:
    """Crop window for a symbol whose position came from viewing `prior_view` px.

    Sized to contain the truth given the error the previous pass is expected to
    have made, which is a fraction of what that pass could see. Capped at the page
    so the window cannot fail to shrink, and floored so the crop keeps enough
    surrounding drawing to be recognisable.
    """
    allowance = int(ERROR_ALLOWANCE_FRACTION * prior_view)
    want = max(
        MIN_WINDOW_EDGE,
        int(span * (1 + 2 * REFINE_PAD)),
        2 * allowance + span,
    )
    return min(want, page_edge)


def _refine_box(client, model_id: str, image_path: Path, symbol: Symbol,
                description: str, window: int | None = None,
                max_tokens: int = 400) -> tuple[PixelBox | None, str | None]:
    """Re-locate one symbol inside a padded crop around its first pass box.

    Why this exists, with numbers. Measured against ink bounding box ground truth
    on a rendered regulation exhibit, a first pass box for a septic tank sat 75 px
    left and 98 px high of the truth, a centre error of 153 px on a 1224 by 1584
    sheet, about 6 percent of each axis. Refining inside a 798 by 492 crop brought
    the same centre to 27 px, an 82 percent reduction. The reason is mechanical
    rather than clever: the model's coordinate error scales with the size of the
    image it is looking at, so making the image smaller makes the error smaller.

    A well symbol is around 30 px across, so a 153 px error does not merely
    mislocate it, it misses it entirely. That is what makes this pass necessary
    rather than an optimisation.

    Returns (refined box, complaint). A None box means keep the first pass answer.
    """
    import io
    import json as _json

    from PIL import Image

    with Image.open(image_path) as im:
        im = im.convert("RGB")
        page_w, page_h = im.size
        box = symbol.pixel
        if max(box.width, box.height) < MIN_REFINE_EDGE:
            return None, (
                f"{symbol.label}: box {box.width}x{box.height} px is too small to "
                f"refine, kept the first pass position"
            )
        # Window centred on the box so a misplaced guess still brackets the truth.
        # The caller decides how wide, shrinking it each pass.
        span = max(box.width, box.height)
        want = window or _window_for(span, max(page_w, page_h), max(page_w, page_h))
        cx, cy = box.center
        x0 = max(0, cx - want // 2)
        y0 = max(0, cy - want // 2)
        x1 = min(page_w, x0 + want)
        y1 = min(page_h, y0 + want)
        # Clamping at the far edge would otherwise shrink the window; pull the
        # origin back so a symbol near a margin still gets a full sized crop.
        x0 = max(0, x1 - want)
        y0 = max(0, y1 - want)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None, f"{symbol.label}: crop region was empty"

        crop = im.crop((x0, y0, x1, y1))
        src_w, src_h = crop.size

        # Enlarge so the symbol is actually legible to the model.
        factor = min(MAX_UPSCALE, max(1.0, TARGET_CROP_EDGE / max(src_w, src_h)))
        if factor > 1.0:
            crop = crop.resize(
                (int(round(src_w * factor)), int(round(src_h * factor))),
                Image.LANCZOS,
            )
        crop_w, crop_h = crop.size

        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        payload = buf.getvalue()

    article = "an" if symbol.label[0] in "aeiou" else "a"
    # The text the first pass read beside the symbol is the strongest available
    # disambiguator when a wider crop happens to contain two of the same class.
    hint = (
        f"\nIt is annotated on the drawing as {symbol.note!r}, so the leader line "
        f"carrying those words points to the symbol you want.\n"
        if symbol.note else "\n"
    )
    prompt = REFINE_PROMPT.format(
        w=crop_w, h=crop_h, label=symbol.label,
        description=description, article=article, hint=hint,
    )

    try:
        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [
                {"image": {"format": "png", "source": {"bytes": payload}}},
                {"text": prompt},
            ]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        )
        text = response["output"]["message"]["content"][0]["text"]
    except Exception as exc:  # noqa: BLE001
        # A refinement failure is not a detection failure. The first pass box
        # stands, flagged unrefined, because a coarse position is still useful and
        # losing the symbol entirely would be worse.
        return None, f"{symbol.label}: refinement call failed ({type(exc).__name__})"

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None, f"{symbol.label}: refinement returned no JSON"
    try:
        data = _json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, f"{symbol.label}: refinement JSON did not parse"

    if data.get("found") is False:
        return None, (
            f"{symbol.label}: not found again inside its own crop, so the first "
            f"pass position is doubtful and was kept unrefined"
        )

    try:
        rl = int(round(float(data["left"])))
        rt = int(round(float(data["top"])))
        rw = int(round(float(data["width"])))
        rh = int(round(float(data["height"])))
    except (KeyError, TypeError, ValueError):
        return None, f"{symbol.label}: refinement box was not numeric"

    if rw <= 0 or rh <= 0:
        return None, f"{symbol.label}: refinement box had no area"

    # Back through the enlargement, then into full page pixels, then clamped.
    # Dividing by the factor before adding the crop origin is the whole of the
    # coordinate transform, and getting the order wrong here would put every
    # refined symbol somewhere plausible and wrong.
    rl = rl / factor
    rt = rt / factor
    rw = rw / factor
    rh = rh / factor

    abs_left = max(0, min(page_w - 1, int(round(rl + x0))))
    abs_top = max(0, min(page_h - 1, int(round(rt + y0))))
    abs_w = max(1, min(int(round(rw)), page_w - abs_left))
    abs_h = max(1, min(int(round(rh)), page_h - abs_top))

    return PixelBox(
        left=abs_left, top=abs_top, width=abs_w, height=abs_h,
        image_width=page_w, image_height=page_h,
    ), None


def _refine_iteratively(client, model_id: str, image_path: Path, symbol: Symbol,
                        description: str, page_edge: int,
                        max_passes: int = MAX_REFINE_PASSES,
                        ) -> tuple[int, list[str]]:
    """Re-crop and re-locate a symbol until its position stops moving.

    Mutates the symbol in place and returns (passes that improved it, complaints).

    The loop exists because one refinement pass is not enough and the reason is
    arithmetic. The error is a roughly constant fraction of the view, so a single
    pass converts "a fraction of the page" into "a fraction of the first crop",
    which on a 1259 px sheet is 116 px into 26 px. Repeating with the window sized
    to the new, smaller error keeps dividing it.

    Stops on any of: convergence within CONVERGENCE_PX, the window reaching its
    floor, a pass reporting the symbol absent, or MAX_REFINE_PASSES. Each stop is
    reported rather than silent, because "refined four times" and "refined once
    then lost" are different confidences in the same coordinate.
    """
    complaints: list[str] = []
    view = page_edge
    passes = 0

    for _ in range(max_passes):
        span = max(symbol.pixel.width, symbol.pixel.height)
        window = _window_for(span, view, page_edge)

        # Once the window stops shrinking there is nothing further to ask: the next
        # pass would send an identical crop and, at temperature 0, get an identical
        # answer back.
        if passes and window >= view:
            break

        before = symbol.pixel.center
        refined, complaint = _refine_box(
            client, model_id, image_path, symbol, description, window=window,
        )
        if complaint:
            complaints.append(f"pass {passes + 1}: {complaint}")
        if refined is None:
            break

        symbol.pixel = refined
        symbol.refined = True
        passes += 1
        moved = math.dist(before, symbol.pixel.center)
        view = window

        if moved <= CONVERGENCE_PX:
            break
        if window <= MIN_WINDOW_EDGE:
            break

    return passes, complaints


class SymbolLocator:
    """Locate septic tanks and wells on a sheet, guided by reference crops."""

    def __init__(self, client=None, model_id: str | None = None,
                 cache_dir: Path | None = None, guide: styleguide.StyleGuide | None = None):
        self.model_id = model_id or config.BEDROCK_VISION_MODEL
        self.cache_dir = Path(cache_dir or config.CACHE_DIR) / "vision-locate"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._session = None
        self.guide = guide if guide is not None else styleguide.load()

    @property
    def client(self):
        """Lazy, so a cache hit needs no credentials."""
        if self._client is None:
            if self._session is None:
                self._session = config.session()
            self._client = self._session.client("bedrock-runtime")
        return self._client

    def _cache_path(self, img_hash: str, refine: bool = True,
                    max_passes: int = MAX_REFINE_PASSES) -> Path:
        """Keyed by image, model, prompt version, exemplar set and refinement depth.

        Refinement depth is in the key because a run allowed four passes and one
        allowed a single pass give materially different coordinates for the same
        sheet. They are two different answers and must not share an entry.
        """
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "-", self.model_id)
        tag = f"refined-p{max_passes}" if refine else "coarse"
        return self.cache_dir / (
            f"sha256-{img_hash}.{safe_model}.{LOCATE_PROMPT_VERSION}."
            f"style-{self.guide.fingerprint()}.{tag}.json"
        )

    def cached_by_hash(self, img_hash: str, refine: bool = True,
                       max_passes: int = MAX_REFINE_PASSES) -> LocateResult | None:
        path = self._cache_path(img_hash, refine, max_passes)
        if not path.exists():
            return None
        try:
            return LocateResult.from_json(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None

    def cached_for_image(self, path: Path, refine: bool = True,
                         max_passes: int = MAX_REFINE_PASSES) -> LocateResult | None:
        return self.cached_by_hash(
            image_hash(Path(path).read_bytes()), refine, max_passes
        )

    def save_to_cache(self, result: LocateResult, img_hash: str,
                      refine: bool = True,
                      max_passes: int = MAX_REFINE_PASSES) -> Path:
        target = self._cache_path(img_hash, refine, max_passes)
        payload = result.to_json()
        payload["image_hash"] = img_hash
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target

    def locate(
        self,
        path: Path,
        page: int = 1,
        labels: list[str] | None = None,
        use_cache: bool = True,
        max_tokens: int = 4000,
        refine: bool = True,
        max_passes: int = MAX_REFINE_PASSES,
    ) -> LocateResult:
        """Locate symbols on one sheet and return pixel positions.

        With refine=True, every symbol found by the first pass is re-located inside
        a padded crop of itself. That costs one extra model call per symbol and is
        the difference between a coordinate that points at the symbol and one that
        points near it. See _refine_box for the measured effect.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"image not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_FORMATS:
            raise ValueError(
                f"{path.name} has format {suffix!r}, and Bedrock accepts only "
                f"{sorted(SUPPORTED_FORMATS)}"
            )

        data = path.read_bytes()
        img_hash = image_hash(data)
        if use_cache:
            hit = self.cached_by_hash(img_hash, refine, max_passes)
            if hit is not None:
                return hit

        # True dimensions, read off the file. Everything normalised later divides
        # by these, never by anything the model reported.
        from PIL import Image
        with Image.open(path) as im:
            width, height = im.size

        wanted = labels or self.guide.labels or list(styleguide.STYLE_SOURCES)
        descriptions = {
            label: styleguide.STYLE_SOURCES.get(label, {}).get("description", label)
            for label in wanted
        }

        content = styleguide.content_blocks(self.guide)
        content.append({
            "text": (
                f"That is the end of the reference symbols. The next image is the "
                f"permit sheet to read, {width} by {height} pixels."
            )
        })
        content.append({
            "image": {
                "format": SUPPORTED_FORMATS[suffix],
                "source": {"bytes": data},
            }
        })
        content.append({"text": _prompt(width, height, wanted, descriptions)})

        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=[{"role": "user", "content": content}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
        except Exception as exc:  # noqa: BLE001
            raise VisionUnavailable(
                f"{self.model_id} could not be invoked: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            text = response["output"]["message"]["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise VisionUnavailable(
                f"{self.model_id} returned an unexpected response shape: {exc}"
            ) from exc

        symbols, complaints = _parse(text, page, wanted, width, height)

        if refine and symbols:
            for symbol in symbols:
                passes, notes = _refine_iteratively(
                    self.client, self.model_id, path, symbol,
                    descriptions.get(symbol.label, symbol.label),
                    page_edge=max(width, height),
                    max_passes=max_passes,
                )
                symbol.refine_passes = passes
                complaints.extend(notes)

        result = LocateResult(
            source=path.name,
            page=page,
            model_id=self.model_id,
            image_width=width,
            image_height=height,
            symbols=symbols,
            style_fingerprint=self.guide.fingerprint(),
            style_summary=self.guide.summary(),
            message="; ".join(complaints) if complaints else None,
            raw=text,
        )
        # After refinement, so the numbering follows the final positions rather
        # than the coarse ones it would have used before the boxes moved.
        result.assign_ids()
        self.save_to_cache(result, img_hash, refine, max_passes)
        return result
