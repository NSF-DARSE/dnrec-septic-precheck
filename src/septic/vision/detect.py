"""Locating objects on a plan sheet with a Bedrock vision model.

Why this exists. The rule engine needs distances between things, and the packets
state some of them in form fields and leave the rest on the drawing. A sheet shows
a well as a small circle, the disposal field as a hatched rectangle, the dwelling
as a footprint outline, and a scale bar somewhere in the margin. Those are objects,
not text, so OCR cannot return them.

What this returns, and what it does not. A Detection is a label, a normalised box,
and the model's own stated confidence. It is evidence, positioned on a page, that a
reviewer can look at. It is not a measurement and it is not a finding. Converting
two boxes into a distance in feet needs the scale bar, and that is deliberately a
separate step, because a distance derived from a misread scale is worse than no
distance at all.

Boxes are approximate and the code says so. A vision model estimates coordinates,
it does not regress them the way a detector trained for the task would. A box here
is reliable enough to point a reviewer at a region of a sheet and not reliable
enough to measure against, so DetectionResult carries `boxes_are_estimates=True`
and nothing downstream is allowed to treat a box as survey data.

Caching. Keyed by the SHA256 of the image bytes together with the model id and the
prompt version, so a rerun on the same sheet is free and needs no network, and so
changing the prompt or the model invalidates the entry rather than silently
serving an answer produced by different instructions. This mirrors the Textract
cache in ingest/textract.py for the same reason: the demo has to run with the wifi
off.

Failure is not a verdict. Every failure path raises VisionUnavailable or returns an
empty result, and callers treat an absent detection as unknown. A sheet the model
could not read is reported as unread, never as a sheet with nothing on it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..ingest.layout import Box

# Bumped whenever the prompt or the parsing contract changes. Part of the cache
# key, so an old answer produced by different instructions is never reused.
PROMPT_VERSION = "v1"

# Bedrock Converse accepts these. Anything else has to be converted first.
SUPPORTED_FORMATS = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
}

# The vocabulary. A label outside this set is discarded rather than passed on,
# for the same reason extract.py constrains a category to `allowed`: an
# open vocabulary lets the model invent a class, and a class nothing consumes is
# noise a reviewer has to read past.
#
# Each entry is what the object looks like on a sheet, phrased for the model.
OBJECT_CLASSES: dict[str, str] = {
    "scale_bar": (
        "a graphic scale bar or a written scale such as 1 inch = 30 feet"
    ),
    "north_arrow": "a north arrow or compass rose",
    "well": "a well, usually a small circle labelled well or W",
    "septic_tank": "a septic tank, usually a small rectangle labelled tank",
    "disposal_field": (
        "the absorption or disposal area, often a hatched or dashed rectangle "
        "containing trenches or a bed"
    ),
    "dwelling": "the house or building footprint",
    "property_line": "the parcel or lot boundary lines",
    "watercourse": "a stream, ditch, pond, or other surface water",
    "test_hole": "a percolation test hole or soil boring, often marked with a cross or P",
    "title_block": "the title block or drawing legend, usually a corner panel",
}


class VisionUnavailable(RuntimeError):
    """Bedrock could not be reached, or refused the call.

    Named to match EmbeddingUnavailable in retrieval/embed.py: callers fall back
    rather than fail, because vision enriches a review and does not enable one.
    """


def image_hash(data: bytes) -> str:
    """Stable content key for an image. Same bytes, same key, no network."""
    return hashlib.sha256(data).hexdigest()[:32]


@dataclass
class Detection:
    """One located object, with where it is and how sure the model said it was."""

    label: str
    box: Box
    confidence: float | None = None
    note: str | None = None

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "box": self.box.to_json(),
            "confidence": (
                round(self.confidence, 3) if self.confidence is not None else None
            ),
            "note": self.note,
        }

    def describe(self) -> str:
        """One line a reviewer can read."""
        left, top = self.box.center
        conf = (
            f", model confidence {self.confidence:.2f}"
            if self.confidence is not None else ""
        )
        return (
            f"{self.label} near {left:.0%} across and {top:.0%} down "
            f"page {self.box.page}{conf}"
        )


@dataclass
class DetectionResult:
    """Everything one vision pass produced over one image."""

    source: str
    page: int
    model_id: str
    detections: list[Detection] = field(default_factory=list)
    scale_text: str | None = None
    from_cache: bool = False
    message: str | None = None
    raw: str | None = None

    # A vision model estimates coordinates rather than regressing them. Carried
    # explicitly so no caller can quietly treat a box as a survey measurement.
    boxes_are_estimates: bool = True

    @property
    def ok(self) -> bool:
        return bool(self.detections)

    def labels(self) -> list[str]:
        return sorted({d.label for d in self.detections})

    def by_label(self, label: str) -> list[Detection]:
        return [d for d in self.detections if d.label == label]

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "page": self.page,
            "model_id": self.model_id,
            "prompt_version": PROMPT_VERSION,
            "boxes_are_estimates": self.boxes_are_estimates,
            "scale_text": self.scale_text,
            "message": self.message,
            "detections": [d.to_json() for d in self.detections],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "DetectionResult":
        detections = []
        for entry in payload.get("detections", []):
            raw_box = entry.get("box") or {}
            detections.append(
                Detection(
                    label=entry["label"],
                    box=Box(
                        left=raw_box.get("left", 0.0),
                        top=raw_box.get("top", 0.0),
                        width=raw_box.get("width", 0.0),
                        height=raw_box.get("height", 0.0),
                        page=raw_box.get("page", payload.get("page", 1)),
                    ),
                    confidence=entry.get("confidence"),
                    note=entry.get("note"),
                )
            )
        return cls(
            source=payload.get("source", ""),
            page=payload.get("page", 1),
            model_id=payload.get("model_id", ""),
            detections=detections,
            scale_text=payload.get("scale_text"),
            from_cache=True,
            message=payload.get("message"),
            boxes_are_estimates=payload.get("boxes_are_estimates", True),
        )


def _prompt(classes: dict[str, str]) -> str:
    """The instruction. Explicit about absence, because absence is the useful answer.

    Two things are stated firmly. Omit what is not there, rather than guessing at
    it: a fabricated well on a sheet that has none would put a setback into a
    report that no drawing supports. And return coordinates as fractions of the
    page, because that is the space layout.Box uses and a pixel answer would have
    to be rescaled by whoever consumed it.
    """
    catalogue = "\n".join(f"  {name}: {desc}" for name, desc in classes.items())
    return f"""You are reading one sheet from a septic system permit application.

Locate any of these objects that are actually present on the sheet:

{catalogue}

Return JSON only, no prose and no code fence, in exactly this shape:

{{
  "scale_text": "the scale exactly as written on the sheet, or null",
  "detections": [
    {{
      "label": "one of the names listed above",
      "left": 0.0, "top": 0.0, "width": 0.0, "height": 0.0,
      "confidence": 0.0,
      "note": "any text written on or beside the object, or null"
    }}
  ]
}}

Rules you must follow:
- left, top, width and height are fractions of the page between 0 and 1, where
  left 0 is the left edge and top 0 is the top edge.
- confidence is your own certainty between 0 and 1.
- Omit any object you do not actually see. An empty detections list is a correct
  answer for a sheet that contains none of them. Do not guess.
- Use only the labels listed above. Do not invent new ones.
- If the same object appears more than once, return one entry for each.
"""


def _parse(text: str, page: int, classes: dict[str, str]) -> tuple[list[Detection], str | None, list[str]]:
    """Read the model's JSON into Detections, discarding anything unusable.

    Returns (detections, scale_text, complaints). Every rejection is reported
    rather than swallowed, on the same principle as Extraction.rejected in
    extract.py: a value that was dropped is something a reviewer may need to know
    was dropped.
    """
    complaints: list[str] = []

    # Models wrap JSON in a fence often enough to be worth handling, and refusing
    # a well formed answer over three backticks would be a pointless failure.
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
        return [], None, [f"model did not return parseable JSON: {exc}"]

    scale_text = payload.get("scale_text")
    if isinstance(scale_text, str) and not scale_text.strip():
        scale_text = None

    detections: list[Detection] = []
    for entry in payload.get("detections") or []:
        if not isinstance(entry, dict):
            complaints.append(f"discarded a detection that was not an object: {entry!r}")
            continue
        label = str(entry.get("label", "")).strip().lower().replace(" ", "_")
        if label not in classes:
            complaints.append(
                f"discarded label {label!r}, which is not one of "
                f"{sorted(classes)}"
            )
            continue

        try:
            left = float(entry.get("left"))
            top = float(entry.get("top"))
            width = float(entry.get("width"))
            height = float(entry.get("height"))
        except (TypeError, ValueError):
            complaints.append(
                f"discarded {label}: coordinates were not numbers"
            )
            continue

        # A box outside the page, or with no area, is not a location. Clamping
        # instead would move the box somewhere the model did not put it, which is
        # worse than dropping it: a reviewer sent to the wrong corner of a sheet
        # loses more time than one told the object was not located.
        if not (0.0 <= left <= 1.0 and 0.0 <= top <= 1.0):
            complaints.append(
                f"discarded {label}: origin ({left:.3f}, {top:.3f}) is off the page"
            )
            continue
        if width <= 0 or height <= 0 or left + width > 1.001 or top + height > 1.001:
            complaints.append(
                f"discarded {label}: box {width:.3f} by {height:.3f} at "
                f"({left:.3f}, {top:.3f}) does not lie on the page"
            )
            continue

        confidence = entry.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            confidence = None

        note = entry.get("note")
        if isinstance(note, str) and not note.strip():
            note = None

        detections.append(
            Detection(
                label=label,
                box=Box(
                    left=left,
                    top=top,
                    width=min(width, 1.0 - left),
                    height=min(height, 1.0 - top),
                    page=page,
                ),
                confidence=confidence,
                note=note if isinstance(note, str) else None,
            )
        )

    return detections, scale_text, complaints


class VisionClient:
    """Detect objects on plan sheets, caching by image content."""

    def __init__(self, client=None, model_id: str | None = None,
                 cache_dir: Path | None = None):
        self.model_id = model_id or config.BEDROCK_VISION_MODEL
        self.cache_dir = Path(cache_dir or config.CACHE_DIR) / "vision"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._session = None

    @property
    def client(self):
        """Constructed lazily, so a cache hit needs no credentials at all."""
        if self._client is None:
            if self._session is None:
                self._session = config.session()
            self._client = self._session.client("bedrock-runtime")
        return self._client

    # -- cache ------------------------------------------------------------

    def _cache_path(self, img_hash: str) -> Path:
        """Keyed by image, model and prompt version together.

        The model id and prompt version belong in the key. Without them, editing
        the prompt would keep serving answers produced by the old one, which is
        the kind of stale result that is very hard to notice.
        """
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "-", self.model_id)
        return self.cache_dir / f"sha256-{img_hash}.{safe_model}.{PROMPT_VERSION}.json"

    def cached_by_hash(self, img_hash: str) -> DetectionResult | None:
        path = self._cache_path(img_hash)
        if not path.exists():
            return None
        try:
            return DetectionResult.from_json(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except Exception:  # noqa: BLE001
            return None

    def cached_for_image(self, path: Path) -> DetectionResult | None:
        """Cached result for a local image, or None. Never touches the network."""
        return self.cached_by_hash(image_hash(Path(path).read_bytes()))

    def save_to_cache(self, result: DetectionResult, img_hash: str) -> Path:
        target = self._cache_path(img_hash)
        payload = result.to_json()
        payload["image_hash"] = img_hash
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target

    # -- detection --------------------------------------------------------

    def detect_bytes(
        self,
        data: bytes,
        image_format: str,
        page: int = 1,
        source: str = "",
        classes: dict[str, str] | None = None,
        use_cache: bool = True,
        max_tokens: int = 2000,
    ) -> DetectionResult:
        """Detect objects in raw image bytes.

        A cache hit returns before any client is constructed, which is what lets
        this run with no credentials and no network.
        """
        classes = classes or OBJECT_CLASSES
        img_hash = image_hash(data)

        if use_cache:
            hit = self.cached_by_hash(img_hash)
            if hit is not None:
                return hit

        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=[{
                    "role": "user",
                    "content": [
                        {"image": {"format": image_format,
                                   "source": {"bytes": data}}},
                        {"text": _prompt(classes)},
                    ],
                }],
                # Temperature 0: the same sheet must give the same answer twice,
                # or a cached result and a fresh one would disagree and there
                # would be no way to tell which a report was built from.
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
        except Exception as exc:  # noqa: BLE001
            raise VisionUnavailable(
                f"{self.model_id} could not be invoked: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            text = response["output"]["message"]["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise VisionUnavailable(
                f"{self.model_id} returned an unexpected response shape: {exc}"
            ) from exc

        detections, scale_text, complaints = _parse(text, page, classes)
        result = DetectionResult(
            source=source,
            page=page,
            model_id=self.model_id,
            detections=detections,
            scale_text=scale_text,
            message="; ".join(complaints) if complaints else None,
            raw=text,
        )

        # A parse failure is cached too. It is a real and repeatable outcome for
        # this image under this prompt, and re-invoking to be told the same thing
        # costs money and changes nothing.
        self.save_to_cache(result, img_hash)
        return result

    def detect_image(
        self,
        path: Path,
        page: int = 1,
        classes: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> DetectionResult:
        """Detect objects in a local image file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"image not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_FORMATS:
            raise ValueError(
                f"{path.name} has format {suffix!r}, and Bedrock accepts only "
                f"{sorted(SUPPORTED_FORMATS)}"
            )
        return self.detect_bytes(
            path.read_bytes(),
            image_format=SUPPORTED_FORMATS[suffix],
            page=page,
            source=path.name,
            classes=classes,
            use_cache=use_cache,
        )
