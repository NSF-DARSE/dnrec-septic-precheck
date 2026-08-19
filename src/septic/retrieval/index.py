"""Local vector index over harvested permits.

A file rather than a managed vector store. The corpus is 1226 approved permits
plus 253 denied and returned ones, the account is temporary, and the whole index
fits in memory several times over. FAISS is a dependency worth adding only if this
grows by two orders of magnitude; until then a JSON file and a dot product are
both faster to reason about and easier to hand to DNREC.

What a record contains is a summary line built from the CSV columns that are
already structured: system type, use, county, percolation rate, flow. That is
deliberate. The alternative, embedding OCR text from the packets, would restrict
the index to the 218 approved permits that actually have a document, and would
make similarity depend on scan quality.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from .embed import cosine, embed_texts

DEFAULT_INDEX_PATH = config.OUT_DIR / "permit_index.json"

# CSV derived fields that describe a permit well enough to compare it to another.
SUMMARY_FIELDS = (
    "permitStatus", "septicSystemType", "constructionType", "propUse",
    "county", "perkRate", "flowRate", "year",
)


def summarize(record: dict) -> str:
    """A comparable one line description of a permit.

    Field names are spelled out rather than concatenated bare so that two records
    differing in system type end up further apart than two differing only in
    county.
    """
    parts = []
    for key in SUMMARY_FIELDS:
        value = record.get(key)
        if value in (None, "", "nan"):
            continue
        label = key.replace("_", " ")
        parts.append(f"{label} {value}")
    return "; ".join(parts)


@dataclass
class IndexEntry:
    detail_id: str
    permit_number: str | None
    summary: str
    vector: list[float]
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "detail_id": self.detail_id,
            "permit_number": self.permit_number,
            "summary": self.summary,
            "vector": [round(v, 6) for v in self.vector],
            "metadata": self.metadata,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "IndexEntry":
        return cls(
            detail_id=payload["detail_id"],
            permit_number=payload.get("permit_number"),
            summary=payload.get("summary", ""),
            vector=[float(v) for v in payload.get("vector", [])],
            metadata=payload.get("metadata", {}),
        )


@dataclass
class PermitIndex:
    entries: list[IndexEntry] = field(default_factory=list)
    backend: str = "none"
    dimensions: int = 0

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def degraded(self) -> bool:
        """True when the vectors came from the offline stand-in.

        Callers must surface this. A precedent list built on hashed tokens is not
        a semantic match and must never be presented as one.
        """
        return self.backend == "local-hashing-fallback"

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "backend": self.backend,
            "dimensions": self.dimensions,
            "count": len(self.entries),
            "entries": [e.to_json() for e in self.entries],
        }

    def nearest(self, vector: list[float], k: int = 5) -> list[tuple[IndexEntry, float]]:
        scored = [(entry, cosine(vector, entry.vector)) for entry in self.entries]
        scored.sort(key=lambda pair: -pair[1])
        return scored[:k]


def build_index(records: list[dict], client=None, allow_local: bool = True
                ) -> PermitIndex:
    """Embed a list of manifest records into an index."""
    usable = [r for r in records if r.get("detail_id")]
    summaries = [summarize(r) for r in usable]
    vectors, backend = embed_texts(summaries, client=client, allow_local=allow_local)

    entries = []
    for record, summary, vector in zip(usable, summaries, vectors):
        entries.append(
            IndexEntry(
                detail_id=str(record["detail_id"]),
                permit_number=record.get("permitNumber"),
                summary=summary,
                vector=vector,
                metadata={
                    key: record.get(key)
                    for key in SUMMARY_FIELDS
                    if record.get(key) not in (None, "")
                },
            )
        )
    return PermitIndex(
        entries=entries,
        backend=backend,
        dimensions=len(vectors[0]) if vectors else 0,
    )


def save_index(index: PermitIndex, path: Path | None = None) -> Path:
    path = Path(path or DEFAULT_INDEX_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index.to_json()), encoding="utf-8")
    return path


# The index is a 16 MB JSON file holding every permit vector, and parsing it took
# 5.7 seconds of every review because it was read from disk again on each call.
# The console reviews one packet after another in one process, so an audience was
# watching that cost repeat. Keyed by path and modification time, so rebuilding
# the index still takes effect without a restart.
_INDEX_CACHE: dict[tuple[str, float], "PermitIndex"] = {}


def load_index(path: Path | None = None) -> PermitIndex:
    path = Path(path or DEFAULT_INDEX_PATH)
    if not path.exists():
        raise FileNotFoundError(f"permit index not found: {path}")

    key = (str(path.resolve()), path.stat().st_mtime)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    payload = json.loads(path.read_text(encoding="utf-8"))
    index = PermitIndex(
        entries=[IndexEntry.from_json(e) for e in payload.get("entries", [])],
        backend=payload.get("backend", "unknown"),
        dimensions=payload.get("dimensions", 0),
    )
    _INDEX_CACHE.clear()
    _INDEX_CACHE[key] = index
    return index


def load_manifest(path: Path) -> list[dict]:
    """Read a harvest manifest, skipping unparsable lines."""
    records = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
