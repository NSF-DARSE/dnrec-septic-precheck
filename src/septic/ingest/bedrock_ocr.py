"""Reading a permit PDF with Bedrock, and turning the answer into a Document.

This is the second OCR provider. It sends the PDF itself as a Converse document
block and asks for the extracted text back as JSON, rather than reassembling a
block graph the way ingest/layout.py does for Textract.

What it gives, and what it does not
-----------------------------------

It gives the text, the form key/value pairs, and the tables, which is what the
fact extractor actually reads.

It does not give geometry. A model asked for a bounding box per line will supply
numbers, and those numbers will look like coordinates, but nothing measured
produced them. Every TextItem from here therefore carries box=None. That matters
because ingest/layout.py keeps boxes so a reviewer can point at where on a page a
value came from, and a fabricated box would be indistinguishable downstream from
a measured one.

It does not give a calibrated confidence either. Textract reports a per-block
confidence from its own recogniser: in docs/evidence/textract_sample.txt the Site
Evaluation Number came back at 54 percent while the fields around it were at 94
and 95, and that gap is how a bad read announces itself. A model can be asked how
sure it is, and the number it returns is a self-report, not an instrument reading.
It is carried through as `self_reported_confidence` and deliberately not written
into TextItem.confidence, which stays 0.0 so that nothing prints it as if it were
the Textract figure.

Caching
-------

Keyed on the SHA256 of the file bytes, the model id, and the prompt version,
matching ingest/textract.py and the vision cache. Same three reasons: OCR is the
expensive step, the same documents are read many times during development, and
the demo has to run with no credentials and no network.

Size limits
-----------

Converse allows up to five documents per request at 4.5 MB each, and for Claude 4
and later that size cap does not apply to PDFs. This corpus averages 8.92 MB per
document with the largest seen at 31.2 MB, so the exemption is what makes a whole
permit a single request. On a model without it, split the PDF first.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import layout
from .layout import Document, FormField, Table, TextItem

# Bumped when the prompt changes, so a cached answer is never served against
# different instructions.
OCR_PROMPT_VERSION = "v1"

# Converse names the format, and it has to match the bytes.
SUPPORTED_FORMATS = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".doc": "doc",
    ".docx": "docx",
    ".xls": "xls",
    ".xlsx": "xlsx",
    ".html": "html",
    ".txt": "txt",
    ".md": "md",
}

MAX_TOKENS = 16000


class BedrockOcrUnavailable(RuntimeError):
    """Raised when the read could not be performed at all."""


OCR_PROMPT = """You are performing OCR on a scanned Delaware septic permit
document. Transcribe what is printed, and do not interpret, summarise, correct or
complete it.

Return JSON only, matching this shape exactly:

{{
  "pages": <integer, how many pages you were given>,
  "lines": [
    {{"page": <integer, 1 based>, "text": "<one line exactly as printed>"}}
  ],
  "fields": [
    {{"page": <integer>, "key": "<the printed label>", "value": "<the filled value>",
      "self_reported_confidence": <0-100, how legible this value was to you>}}
  ],
  "tables": [
    {{"page": <integer>, "rows": [["<cell>", "<cell>"]]}}
  ]
}}

Rules:
- Transcribe verbatim, including permit numbers, dates, parcel ids and units.
  Preserve the printed spelling even where it looks wrong.
- Emit lines in reading order, top to bottom, for each page in turn.
- A ticked checkbox is the value "[X]". An empty one is "".
- "fields" is for labelled values on a form, where a printed label has a filled
  in answer beside or below it. If a label has no answer, give value "".
- Do not invent a value that is not printed. If something is illegible, use ""
  and set self_reported_confidence low.
- Do not return bounding boxes or coordinates. They are not wanted from you.
- Return the JSON and nothing else, with no commentary and no code fence.
"""


@dataclass
class OcrRead:
    """One Bedrock OCR read of one document."""

    document: Document
    document_hash: str
    model_id: str
    prompt_version: str = OCR_PROMPT_VERSION
    from_cache: bool = False
    # Per field, keyed by "<page>:<key>". Kept apart from FormField.confidence
    # because it is not the same kind of number. See the module docstring.
    self_reported_confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.document.lines or self.document.fields)

    def to_json(self) -> dict:
        return {
            "provider": "bedrock",
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "document_hash": self.document_hash,
            "geometry_available": False,
            "confidence_is_calibrated": False,
            "self_reported_confidence": {
                k: round(v, 1) for k, v in self.self_reported_confidence.items()
            },
            "warnings": self.warnings,
            "document": self.document.to_json(),
        }


def _parse_json(text: str) -> dict:
    """Take the JSON out of a model reply, fenced or not."""
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.DOTALL)
    if fence:
        body = fence.group(1).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # A reply that is JSON with prose either side of it is common enough to be
        # worth one recovery attempt before giving up.
        start, end = body.find("{"), body.rfind("}")
        if start != -1 and end > start:
            return json.loads(body[start:end + 1])
        raise


def _to_document(payload: dict) -> tuple[Document, dict[str, float], list[str]]:
    """Build a provider neutral Document out of the model's JSON."""
    warnings: list[str] = []
    doc = Document()

    lines = payload.get("lines") or []
    for entry in lines:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text", ""))
        if not text.strip():
            continue
        doc.lines.append(TextItem(
            text=text,
            box=None,
            confidence=0.0,
            block_type="LINE",
            page=int(entry.get("page", 1) or 1),
        ))

    self_reported: dict[str, float] = {}
    for entry in payload.get("fields") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "")).strip()
        if not key:
            continue
        page = int(entry.get("page", 1) or 1)
        doc.fields.append(FormField(
            key=key,
            value=str(entry.get("value", "")),
            key_box=None,
            value_box=None,
            # Not the model's number. Textract's confidence and a model's
            # self-report are different instruments and must not share a field.
            confidence=0.0,
            page=page,
        ))
        raw_conf = entry.get("self_reported_confidence")
        if isinstance(raw_conf, (int, float)):
            self_reported[f"{page}:{key}"] = float(raw_conf)

    for entry in payload.get("tables") or []:
        if not isinstance(entry, dict):
            continue
        rows = entry.get("rows") or []
        clean = [[str(c) for c in row] for row in rows if isinstance(row, list)]
        doc.tables.append(Table(page=int(entry.get("page", 1) or 1), rows=clean))

    stated = payload.get("pages")
    seen = max((l.page for l in doc.lines), default=0)
    doc.pages = int(stated) if isinstance(stated, int) and stated > 0 else seen
    if seen and doc.pages and seen != doc.pages:
        warnings.append(
            f"the model said the document has {doc.pages} pages but emitted lines "
            f"for {seen}, so a page may have been skipped"
        )

    if not doc.lines:
        warnings.append("no lines were returned, so the read produced no text")

    return doc, self_reported, warnings


class BedrockOcr:
    """Read a document with Bedrock, cached under the document hash."""

    def __init__(self, model_id: str | None = None, client=None,
                 cache_dir: Path | None = None):
        self.model_id = model_id or config.BEDROCK_OCR_MODEL
        self.cache_dir = Path(cache_dir or config.CACHE_DIR) / "bedrock-ocr"
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

    def _cache_path(self, doc_hash: str) -> Path:
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "-", self.model_id)
        return self.cache_dir / f"sha256-{doc_hash}.{safe_model}.{OCR_PROMPT_VERSION}.json"

    def cached_for_file(self, path: Path) -> OcrRead | None:
        """A cached read, or None. Never constructs a client, never uses network."""
        from .textract import hash_file

        return self.cached_by_hash(hash_file(Path(path)))

    def cached_by_hash(self, doc_hash: str) -> OcrRead | None:
        p = self._cache_path(doc_hash)
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        raw = payload.get("raw") or {}
        doc, self_reported, warnings = _to_document(raw)
        return OcrRead(
            document=doc,
            document_hash=doc_hash,
            model_id=payload.get("model_id", self.model_id),
            prompt_version=payload.get("prompt_version", OCR_PROMPT_VERSION),
            from_cache=True,
            self_reported_confidence=self_reported,
            warnings=warnings,
            raw=raw,
        )

    def read(self, path: Path, use_cache: bool = True) -> OcrRead:
        """Read one document. Prefers the cache, which needs no credentials."""
        from .textract import hash_file

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"document not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_FORMATS:
            raise BedrockOcrUnavailable(
                f"{path.name} is a {suffix or 'no extension'} file; Converse accepts "
                f"{sorted(SUPPORTED_FORMATS)}"
            )

        doc_hash = hash_file(path)
        if use_cache:
            hit = self.cached_by_hash(doc_hash)
            if hit is not None:
                return hit

        data = path.read_bytes()
        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=[{
                    "role": "user",
                    "content": [
                        {"document": {
                            # Converse rejects a name with punctuation it does not
                            # like, so it is reduced to a safe token rather than
                            # passed through as the filename.
                            "name": re.sub(r"[^A-Za-z0-9 -]", "", path.stem)[:60] or "document",
                            "format": SUPPORTED_FORMATS[suffix],
                            "source": {"bytes": data},
                        }},
                        {"text": OCR_PROMPT},
                    ],
                }],
                inferenceConfig={"temperature": 0, "maxTokens": MAX_TOKENS},
            )
        except Exception as exc:  # noqa: BLE001
            raise BedrockOcrUnavailable(
                f"Bedrock could not read {path.name}: {type(exc).__name__}: {exc}"
            ) from exc

        parts = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(p.get("text", "") for p in parts)
        if not text.strip():
            raise BedrockOcrUnavailable("the model returned an empty response")
        try:
            payload = _parse_json(text)
        except json.JSONDecodeError as exc:
            raise BedrockOcrUnavailable(
                f"the model's reply was not JSON: {exc}"
            ) from exc

        doc, self_reported, warnings = _to_document(payload)
        stop = response.get("stopReason")
        if stop == "max_tokens":
            warnings.append(
                f"the reply hit the {MAX_TOKENS} token ceiling, so the tail of this "
                f"document is missing; split it and read the parts"
            )

        read = OcrRead(
            document=doc,
            document_hash=doc_hash,
            model_id=self.model_id,
            self_reported_confidence=self_reported,
            warnings=warnings,
            raw=payload,
        )
        if read.ok:
            self._save(read)
        return read

    def _save(self, read: OcrRead) -> Path:
        target = self._cache_path(read.document_hash)
        payload = read.to_json()
        payload["raw"] = read.raw
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target


__all__ = ["BedrockOcr", "OcrRead", "BedrockOcrUnavailable", "OCR_PROMPT_VERSION"]
