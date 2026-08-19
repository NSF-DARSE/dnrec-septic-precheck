"""Reading what OCR cannot: the drawing itself.

Textract and Azure both read text. A site plan sheet carries information that is
not text: a scale bar, a north arrow, the outline of a house, a circle marked as
a well, a hatched rectangle that is the disposal field. A setback is the distance
between two of those objects, and no amount of form field reading recovers it.

Two entry points, for two different questions.

detect.VisionClient answers "what is on this sheet", over a broad vocabulary, in
the normalised 0 to 1 coordinate space layout.Box already uses.

locate.SymbolLocator answers "where exactly are the septic tanks and the wells",
in pixels, after being shown reference crops of how those symbols are actually
drawn on Delaware permit plans. It also classifies each occurrence as sited,
legend or detail, because a symbol in a legend panel is not a place where the
object is.

Neither produces a verdict. A detection is evidence a reviewer can look at, and
the rules decide.
"""
from .detect import (
    Detection,
    DetectionResult,
    VisionClient,
    VisionUnavailable,
    OBJECT_CLASSES,
)
from . import overlay
from .locate import (
    CONTEXTS,
    LocateResult,
    PixelBox,
    Symbol,
    SymbolLocator,
)
from .scale import Scale, ScaleBar, ScaleReader, ScaleUnavailable
from .styleguide import Exemplar, StyleGuide

__all__ = [
    # broad detection, normalised coordinates
    "Detection",
    "DetectionResult",
    "VisionClient",
    "OBJECT_CLASSES",
    # symbol location, pixel coordinates, style guided
    "SymbolLocator",
    "LocateResult",
    "Symbol",
    "PixelBox",
    "CONTEXTS",
    # drawing scale, feet per pixel
    "ScaleReader",
    "Scale",
    "ScaleBar",
    "ScaleUnavailable",
    # style references
    "StyleGuide",
    "Exemplar",
    # verification overlay
    "overlay",
    # shared
    "VisionUnavailable",
]
