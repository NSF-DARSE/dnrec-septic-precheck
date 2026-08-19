"""One way to read a document, whichever provider does the reading.

Everything downstream of OCR reads an ingest.layout.Document. Textract and Bedrock
both produce one, so the extractor, the rule engine and the site plan symbol
mapping do not need to know which ran. That is the point of this module: it is the
only place the choice is made.

The site plan mapping in septic/vision is unaffected by the choice for a concrete
reason rather than by intention. It imports exactly one thing from this package,
`layout.Box`, and it works in the same normalised 0 to 1 page space that Box uses.
It never reads a Textract block. So symbol positions can be compared against words
read by either provider, and a provider swap does not reach it.

What differs between the two is not the shape of the answer but what can be
claimed about it:

                        geometry   confidence
    textract            yes        calibrated, per block, from the recogniser
    bedrock             no         self-reported by the model

`OcrResult` carries those two facts explicitly so a caller can decide what to
print rather than having to know which provider it asked for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import layout
from .layout import Document

PROVIDERS = ("textract", "bedrock")


class UnknownProvider(ValueError):
    pass


@dataclass
class OcrResult:
    """A read document, plus what may honestly be claimed about it."""

    document: Document
    provider: str
    document_hash: str
    from_cache: bool = False
    geometry_available: bool = False
    confidence_is_calibrated: bool = False
    # Present only for providers that self-report, keyed "<page>:<key>".
    self_reported_confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    model_id: str | None = None
    job_id: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.document.lines or self.document.fields)

    def summary(self) -> str:
        geo = "with geometry" if self.geometry_available else "no geometry"
        conf = "calibrated confidence" if self.confidence_is_calibrated \
            else "self-reported confidence"
        cached = ", from cache" if self.from_cache else ""
        return (
            f"{self.provider}: {self.document.pages} page(s), "
            f"{len(self.document.lines)} line(s), {len(self.document.fields)} field(s), "
            f"{len(self.document.tables)} table(s), {geo}, {conf}{cached}"
        )

    def to_json(self) -> dict:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "job_id": self.job_id,
            "document_hash": self.document_hash,
            "from_cache": self.from_cache,
            "geometry_available": self.geometry_available,
            "confidence_is_calibrated": self.confidence_is_calibrated,
            "self_reported_confidence": {
                k: round(v, 1) for k, v in self.self_reported_confidence.items()
            },
            "warnings": self.warnings,
            "document": self.document.to_json(),
        }


def read(path: Path, provider: str | None = None, use_cache: bool = True,
         offline: bool = False) -> OcrResult:
    """Read a document with the configured provider.

    `offline` serves only from cache and never constructs an AWS client, which is
    what lets a demo run with no credentials. It raises rather than falling back
    to a live call, because a silent live call is the thing that breaks a demo on
    a bad network.
    """
    provider = (provider or config.OCR_PROVIDER).lower()
    if provider not in PROVIDERS:
        raise UnknownProvider(
            f"{provider!r} is not an OCR provider; choose from {list(PROVIDERS)}"
        )
    path = Path(path)
    if provider == "bedrock":
        return _read_bedrock(path, use_cache=use_cache, offline=offline)
    return _read_textract(path, use_cache=use_cache, offline=offline)


def _read_bedrock(path: Path, use_cache: bool, offline: bool) -> OcrResult:
    from .bedrock_ocr import BedrockOcr, BedrockOcrUnavailable

    reader = BedrockOcr()
    if offline:
        hit = reader.cached_for_file(path)
        if hit is None:
            raise BedrockOcrUnavailable(
                f"no cached bedrock read for {path.name}, and offline was requested"
            )
        got = hit
    else:
        got = reader.read(path, use_cache=use_cache)
    return OcrResult(
        document=got.document,
        provider="bedrock",
        document_hash=got.document_hash,
        from_cache=got.from_cache,
        geometry_available=False,
        confidence_is_calibrated=False,
        self_reported_confidence=got.self_reported_confidence,
        warnings=list(got.warnings),
        model_id=got.model_id,
    )


def _read_textract(path: Path, use_cache: bool, offline: bool) -> OcrResult:
    from .textract import TextractClient, TextractError, hash_file

    client = TextractClient()
    if offline:
        analysis = client.cached_for_file(path)
        if analysis is None:
            raise TextractError(
                f"no cached textract analysis for {path.name}, and offline was "
                f"requested"
            )
    else:
        analysis = client.analyze_file(path, use_cache=use_cache)
    if not analysis.ok:
        raise TextractError(
            f"textract returned {analysis.status} for {path.name}"
            + (f": {analysis.message}" if analysis.message else "")
        )
    return OcrResult(
        document=layout.parse_blocks(analysis.blocks),
        provider="textract",
        document_hash=hash_file(path),
        from_cache=analysis.from_cache,
        geometry_available=True,
        confidence_is_calibrated=True,
        warnings=[],
        job_id=analysis.job_id,
    )


__all__ = ["read", "OcrResult", "PROVIDERS", "UnknownProvider"]
