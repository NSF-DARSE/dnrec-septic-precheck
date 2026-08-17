"""Turning Textract blocks into text, form fields, and page coordinates.

Textract returns a flat block list with parent and child relationships. This
module resolves those into three views the extractor needs: reading order text
per page, key/value pairs from FORMS, and tables. Bounding boxes are kept on
every item because a reviewer has to be able to point at where on the page a
value came from, and because a site plan measurement is only meaningful with its
position.

Coordinates stay in Textract's normalised 0 to 1 space relative to the page.
Converting to inches or feet needs a scale bar, which is a separate problem.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Box:
    """Normalised bounding box, 0 to 1 relative to the page."""

    left: float
    top: float
    width: float
    height: float
    page: int

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.width / 2, self.top + self.height / 2)

    def to_json(self) -> dict:
        return {
            "left": round(self.left, 5),
            "top": round(self.top, 5),
            "width": round(self.width, 5),
            "height": round(self.height, 5),
            "page": self.page,
        }


@dataclass
class TextItem:
    text: str
    box: Box
    confidence: float
    block_type: str

    def to_json(self) -> dict:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 2),
            "block_type": self.block_type,
            "box": self.box.to_json(),
        }


@dataclass
class FormField:
    """One key/value pair from the FORMS feature."""

    key: str
    value: str
    key_box: Box | None
    value_box: Box | None
    confidence: float
    page: int

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "confidence": round(self.confidence, 2),
            "page": self.page,
            "key_box": self.key_box.to_json() if self.key_box else None,
            "value_box": self.value_box.to_json() if self.value_box else None,
        }


@dataclass
class Table:
    page: int
    rows: list[list[str]] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"page": self.page, "rows": self.rows}


@dataclass
class Document:
    """Everything read off one document."""

    pages: int = 0
    lines: list[TextItem] = field(default_factory=list)
    words: list[TextItem] = field(default_factory=list)
    fields: list[FormField] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    def text(self, page: int | None = None) -> str:
        """Reading order text, optionally for a single page."""
        items = [l for l in self.lines if page is None or l.box.page == page]
        items.sort(key=lambda l: (l.box.page, round(l.box.top, 3), l.box.left))
        return "\n".join(i.text for i in items)

    def field_map(self) -> dict[str, str]:
        """Form fields as a lowercase keyed dict.

        First occurrence wins, since repeated labels on a form are usually a
        continuation block rather than a new value.
        """
        out: dict[str, str] = {}
        for f in self.fields:
            key = f.key.strip().rstrip(":").lower()
            if key and key not in out:
                out[key] = f.value.strip()
        return out

    def to_json(self) -> dict:
        return {
            "pages": self.pages,
            "fields": [f.to_json() for f in self.fields],
            "tables": [t.to_json() for t in self.tables],
            "lines": [l.to_json() for l in self.lines],
        }


def _box(block: dict) -> Box | None:
    geometry = block.get("Geometry", {}).get("BoundingBox")
    if not geometry:
        return None
    return Box(
        left=geometry.get("Left", 0.0),
        top=geometry.get("Top", 0.0),
        width=geometry.get("Width", 0.0),
        height=geometry.get("Height", 0.0),
        page=block.get("Page", 1),
    )


def _children(block: dict, index: dict[str, dict], kinds=("CHILD",)) -> list[dict]:
    out = []
    for rel in block.get("Relationships", []) or []:
        if rel.get("Type") in kinds:
            for cid in rel.get("Ids", []):
                child = index.get(cid)
                if child is not None:
                    out.append(child)
    return out


def _words_of(block: dict, index: dict[str, dict]) -> str:
    parts: list[str] = []
    for child in _children(block, index):
        btype = child.get("BlockType")
        if btype == "WORD":
            parts.append(child.get("Text", ""))
        elif btype == "SELECTION_ELEMENT":
            # A ticked box carries meaning even though it has no text.
            if child.get("SelectionStatus") == "SELECTED":
                parts.append("[X]")
    return " ".join(p for p in parts if p).strip()


def parse_blocks(blocks: Iterable[dict]) -> Document:
    """Resolve a Textract block list into a Document."""
    blocks = list(blocks)
    index = {b["Id"]: b for b in blocks if "Id" in b}
    doc = Document()

    pages = {b.get("Page", 1) for b in blocks}
    doc.pages = max(pages) if pages else 0

    key_blocks: list[dict] = []
    for block in blocks:
        btype = block.get("BlockType")
        box = _box(block)

        if btype in ("LINE", "WORD") and box is not None:
            item = TextItem(
                text=block.get("Text", ""),
                box=box,
                confidence=block.get("Confidence", 0.0),
                block_type=btype,
            )
            (doc.lines if btype == "LINE" else doc.words).append(item)

        elif btype == "KEY_VALUE_SET":
            if "KEY" in (block.get("EntityTypes") or []):
                key_blocks.append(block)

        elif btype == "TABLE":
            doc.tables.append(_parse_table(block, index))

    for key_block in key_blocks:
        value_block = None
        for rel in key_block.get("Relationships", []) or []:
            if rel.get("Type") == "VALUE":
                for vid in rel.get("Ids", []):
                    value_block = index.get(vid)
                    if value_block is not None:
                        break
        key_text = _words_of(key_block, index)
        value_text = _words_of(value_block, index) if value_block else ""
        if not key_text:
            continue
        doc.fields.append(
            FormField(
                key=key_text,
                value=value_text,
                key_box=_box(key_block),
                value_box=_box(value_block) if value_block else None,
                confidence=min(
                    key_block.get("Confidence", 0.0),
                    (value_block or {}).get("Confidence", key_block.get("Confidence", 0.0)),
                ),
                page=key_block.get("Page", 1),
            )
        )

    return doc


def _parse_table(table_block: dict, index: dict[str, dict]) -> Table:
    cells = [
        c for c in _children(table_block, index) if c.get("BlockType") == "CELL"
    ]
    if not cells:
        return Table(page=table_block.get("Page", 1))

    height = max(c.get("RowIndex", 1) for c in cells)
    width = max(c.get("ColumnIndex", 1) for c in cells)
    grid = [["" for _ in range(width)] for _ in range(height)]
    for cell in cells:
        r = cell.get("RowIndex", 1) - 1
        c = cell.get("ColumnIndex", 1) - 1
        if 0 <= r < height and 0 <= c < width:
            grid[r][c] = _words_of(cell, index)
    return Table(page=table_block.get("Page", 1), rows=grid)
