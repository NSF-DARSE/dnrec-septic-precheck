"""Reading scanned permit documents into structured text and coordinates.

Two providers can do the reading, Textract and Bedrock, and both produce the same
`Document`. Prefer `ocr.read`, which picks one from config, over calling either
directly. `bedrock_ocr` is imported lazily inside `ocr` so that nothing here needs
a Bedrock client just to parse a cached Textract analysis.
"""

from . import ocr
from .layout import Box, Document, FormField, Table, TextItem, parse_blocks
from .ocr import OcrResult
from .textract import Analysis, TextractClient

__all__ = [
    "Analysis",
    "Box",
    "Document",
    "FormField",
    "OcrResult",
    "Table",
    "TextItem",
    "TextractClient",
    "ocr",
    "parse_blocks",
]
