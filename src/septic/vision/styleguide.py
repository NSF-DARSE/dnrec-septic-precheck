"""Reference crops that teach the model what a symbol looks like on these sheets.

Why this exists. "A well" is not a visual description. On a DNREC site plan a well
is a circle with a W in it, sometimes a bare dot at the end of a leader line
labelled EXISTING WELL, and a septic tank is a rectangle with two small circles in
it for the access lids. Those conventions are not universal draughting practice,
they are what these engineering firms draw, and describing them in prose gets a
model most of the way and no further. Showing it the actual symbol is what closes
the gap.

So the crops under assets/septic_style and assets/well_style are passed to the
model as labelled exemplars ahead of the sheet being read.

Only crops are exemplars. assets/well_style also contains seven full plan sheets,
which are test inputs and not style references, and sending a whole sheet as an
example of what a well looks like would teach the model nothing and cost a great
deal of context. They are excluded on size, and the excluded list is reported
rather than dropped silently, because a reference folder that has quietly stopped
contributing exemplars is very hard to notice from the outputs.

The exemplar set is part of the detection cache key. Two runs with different
reference images are two different questions, and serving the first answer to the
second would be a stale result of the worst kind: invisible.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

# Above this, on the longest edge, the file is a sheet rather than a symbol crop.
# The real crops in these folders top out at 200 px and the real sheets start at
# 986 px, so anything in between is ambiguous and the threshold sits in the gap
# with room on both sides.
MAX_EXEMPLAR_EDGE = 400

# Bedrock accepts a limited number of images per request, and every exemplar is
# also tokens and latency on every call. Ten per class is more than enough to
# establish a drawing convention.
MAX_PER_CLASS = 10

ASSETS_DIR = config.ROOT / "assets"

# Where each class keeps its reference crops, and how to describe the convention
# in words alongside the pictures. The prose and the images do different jobs: the
# prose says what to look for, the crops say what it looks like here.
STYLE_SOURCES: dict[str, dict] = {
    "septic_tank": {
        "dir": ASSETS_DIR / "septic_style",
        "description": (
            "a septic tank in plan view: a rectangle or box, with pipes entering "
            "one end and leaving the other. Two small circles inside it are the "
            "access lids and confirm it is the tank, but many are drawn as a plain "
            "box with no circles at all, so the absence of circles does not mean "
            "it is not a tank. Where two boxes are drawn together connected by a "
            "pipe, the septic tank is the LARGER box; the smaller box downstream "
            "of it is a distribution box, not the tank"
        ),
    },
    # Given a label of its own on purpose. The small box beside a tank is a real
    # object that has to go somewhere, and with septic_tank as the only box label
    # available the model is pushed into calling both boxes tanks. A correct home
    # for the small box is what keeps the tank count right, which is the same
    # reason the categories in ingest/extract.py are constrained to `allowed`
    # rather than left open.
    "distribution_box": {
        "dir": None,
        "description": (
            "a distribution box or d-box: the SMALLER box drawn downstream of a "
            "septic tank, splitting flow out to the disposal laterals. It is never "
            "the larger of a connected pair of boxes"
        ),
    },
    "well": {
        "dir": ASSETS_DIR / "well_style",
        "description": (
            "a well: most often a circle containing the letter W, sometimes a "
            "small solid dot at the end of a leader line labelled WELL or "
            "EXISTING WELL"
        ),
    },
}


@dataclass
class Exemplar:
    """One reference crop for one class."""

    label: str
    path: Path
    width: int
    height: int

    @property
    def image_format(self) -> str:
        return "png" if self.path.suffix.lower() == ".png" else "jpeg"

    def read(self) -> bytes:
        return self.path.read_bytes()


@dataclass
class StyleGuide:
    """The exemplars for one or more classes, plus what was left out and why."""

    exemplars: list[Exemplar] = field(default_factory=list)
    descriptions: dict[str, str] = field(default_factory=dict)
    excluded: list[dict] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        """Every class in play, including any that is description only.

        Taken from descriptions rather than from the exemplars, because a class
        with no crops still has to be offered to the model. Deriving this from the
        exemplar list would have quietly dropped distribution_box, and dropping it
        is exactly what makes the model label the small box a septic tank.
        """
        return sorted(self.descriptions)

    @property
    def exemplar_labels(self) -> list[str]:
        """Only the classes that actually contributed reference crops."""
        return sorted({e.label for e in self.exemplars})

    def for_label(self, label: str) -> list[Exemplar]:
        return [e for e in self.exemplars if e.label == label]

    def fingerprint(self) -> str:
        """Content key for the exemplar set, for the detection cache key.

        Hashes the bytes, not the paths. Re-cropping a reference in place must
        invalidate cached detections, and a path would not notice that.
        """
        digest = hashlib.sha256()
        for exemplar in sorted(self.exemplars, key=lambda e: (e.label, e.path.name)):
            digest.update(exemplar.label.encode("utf-8"))
            digest.update(hashlib.sha256(exemplar.read()).digest())
        return digest.hexdigest()[:16] if self.exemplars else "none"

    def summary(self) -> str:
        if not self.exemplars:
            return "no style exemplars"
        counts = ", ".join(
            f"{label} x{len(self.for_label(label))}" for label in self.exemplar_labels
        )
        prose_only = [l for l in self.labels if l not in self.exemplar_labels]
        tail = f", {len(self.excluded)} excluded" if self.excluded else ""
        if prose_only:
            tail += f", description only: {', '.join(prose_only)}"
        return f"{len(self.exemplars)} exemplars ({counts}){tail}"


def load(labels: list[str] | None = None,
         max_per_class: int = MAX_PER_CLASS) -> StyleGuide:
    """Collect reference crops for the requested classes.

    A class whose folder is missing contributes nothing and is not an error: the
    detector still runs on the prose description alone, just less well.
    """
    wanted = labels or list(STYLE_SOURCES)
    guide = StyleGuide()

    for label in wanted:
        source = STYLE_SOURCES.get(label)
        if source is None:
            continue
        guide.descriptions[label] = source["description"]

        # A class may be description only. distribution_box has no crop folder: it
        # exists so the smaller box beside a tank has a correct label to land in,
        # and it is defined by its relationship to the tank rather than by a look
        # of its own, which prose carries better than a crop would.
        if source.get("dir") is None:
            continue

        folder = Path(source["dir"])
        if not folder.is_dir():
            guide.excluded.append({
                "label": label,
                "path": str(folder),
                "reason": "reference folder does not exist",
            })
            continue

        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Pillow is required to size the style exemplars: pip install Pillow"
            ) from exc

        kept = 0
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            try:
                with Image.open(path) as im:
                    width, height = im.size
            except Exception as exc:  # noqa: BLE001
                guide.excluded.append({
                    "label": label,
                    "path": path.name,
                    "reason": f"could not be opened: {exc}",
                })
                continue

            if max(width, height) > MAX_EXEMPLAR_EDGE:
                # A full sheet, not a symbol. Reported so the reason is visible.
                guide.excluded.append({
                    "label": label,
                    "path": path.name,
                    "reason": (
                        f"{width}x{height} is larger than {MAX_EXEMPLAR_EDGE} px on "
                        f"the long edge, so it is a plan sheet rather than a symbol "
                        f"crop and is a test input, not a style reference"
                    ),
                })
                continue

            if kept >= max_per_class:
                guide.excluded.append({
                    "label": label,
                    "path": path.name,
                    "reason": f"beyond the {max_per_class} exemplar limit for this class",
                })
                continue

            guide.exemplars.append(
                Exemplar(label=label, path=path, width=width, height=height)
            )
            kept += 1

    return guide


def content_blocks(guide: StyleGuide) -> list[dict]:
    """Exemplars as Bedrock Converse content, grouped and labelled by class.

    Each group is introduced by text naming the class, then its crops. Without the
    naming text the model receives a pile of unlabelled thumbnails and has to
    infer which is which, which is a guess nobody needs it to make.
    """
    blocks: list[dict] = []
    for label in guide.exemplar_labels:
        crops = guide.for_label(label)
        if not crops:
            continue
        blocks.append({
            "text": (
                f"Reference symbols for {label}. {guide.descriptions.get(label, '')} "
                f"The next {len(crops)} image(s) are cropped examples of "
                f"{label} taken from real Delaware septic permit site plans. "
                f"They show the drawing convention only. Do not look for these "
                f"crops themselves in the sheet."
            )
        })
        for crop in crops:
            blocks.append({
                "image": {
                    "format": crop.image_format,
                    "source": {"bytes": crop.read()},
                }
            })
    return blocks


def find_test_sheets() -> list[Path]:
    """The full sheets sitting in the style folders, which are test inputs.

    Kept here rather than in the harness because the same size rule decides both
    questions: too big to be an exemplar is exactly what makes it a sheet to run
    detection on.
    """
    sheets: list[Path] = []
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return sheets
    for source in STYLE_SOURCES.values():
        if source.get("dir") is None:
            continue
        folder = Path(source["dir"])
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            try:
                with Image.open(path) as im:
                    if max(im.size) > MAX_EXEMPLAR_EDGE:
                        sheets.append(path)
            except Exception:  # noqa: BLE001
                continue
    return sheets
