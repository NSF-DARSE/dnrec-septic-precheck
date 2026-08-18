"""Nearest neighbour lookup over the permit index.

WHY OUTCOME BASED MATCHING IS WEAK HERE, AND WHAT THAT MEANS FOR THE REPORT.

The tempting design is to retrieve permits with similar characteristics, look at
how they were decided, and let that inform the answer. Three measured facts about
this corpus rule that out.

  The negative class is tiny. 253 permits out of 112,613 were ever denied or
  returned for correction, and only 101 of those are from 2014 onward and so fall
  under the current regulation. Any similarity neighbourhood large enough to be
  stable contains almost no negatives, so "most similar permits were approved" is
  true of nearly every query and carries no information.

  Approvals are barely documented. Only 218 of the 1226 approved permits harvested
  from 2014 onward carry any document at all. The rest expose nothing but their
  CSV row, so an index built on document text would silently be an index of the
  minority of permits that happen to have a scan.

  The outcome is not the reason. DNREC rarely denies outright. It returns an
  application, the applicant fixes it, and the resubmission is approved, so the
  same permit can end up recorded as approved having been returned twice. The
  return letters that say why are not published. The final status therefore does
  not encode the deficiency, which is the thing a reviewer needs.

So retrieval here does one narrow job: show a reviewer comparable prior permits as
context they can weigh themselves. It never contributes to a verdict, it is
labelled as precedent rather than evidence in the report, and the fact that a
neighbour was approved is never presented as a reason this application should be.
The rules decide. This informs.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .embed import embed_texts
from .index import PermitIndex, load_index, summarize

# A caveat that travels with every precedent list into the report. If this text
# and the report ever disagree, the report is wrong.
PRECEDENT_CAVEAT = (
    "Precedent only. These are prior permits with similar recorded "
    "characteristics, shown so the reviewer can weigh them. A prior permit being "
    "approved is not evidence that this application complies, and none of this "
    "affected the verdict above: the verdict comes only from the rules."
)

OUTCOME_LIMITS = (
    "Outcome matching is weak in this corpus and is not used as a signal. Only "
    "253 of 112,613 permits were ever denied or returned, only 218 of the 1226 "
    "approved permits harvested carry any document, and the return letters that "
    "explain a decision are not published."
)


@dataclass
class Precedent:
    """One retrieved prior permit, with everything needed to caveat it."""

    detail_id: str
    permit_number: str | None
    score: float
    summary: str
    metadata: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.metadata.get("permitStatus") or "unknown")

    def to_json(self) -> dict:
        return {
            "detail_id": self.detail_id,
            "permit_number": self.permit_number,
            "score": round(self.score, 4),
            "summary": self.summary,
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass
class SearchResult:
    """Precedents plus the provenance the report needs to caveat them."""

    precedents: list[Precedent]
    backend: str
    degraded: bool
    index_size: int
    caveat: str = PRECEDENT_CAVEAT
    limits: str = OUTCOME_LIMITS

    def to_json(self) -> dict:
        return {
            "precedents": [p.to_json() for p in self.precedents],
            "backend": self.backend,
            "degraded": self.degraded,
            "index_size": self.index_size,
            "caveat": self.caveat,
            "limits": self.limits,
        }


def query_from_facts(facts: dict[str, Any]) -> str:
    """Build an index query from extracted facts.

    Deliberately uses the same vocabulary as index.summarize so a query and an
    indexed record are described the same way. Mapping the fact names onto the CSV
    column names happens here and only here.
    """
    mapped = {
        "septicSystemType": facts.get("system_type"),
        "propUse": facts.get("use_type"),
        "perkRate": facts.get("perc_rate"),
        "flowRate": facts.get("design_flow"),
        "county": facts.get("county"),
    }
    return summarize({k: v for k, v in mapped.items() if v not in (None, "")})


def search(
    query: str,
    k: int = 5,
    index: PermitIndex | None = None,
    path: Path | None = None,
    client=None,
) -> SearchResult:
    """Find the k most similar prior permits.

    Returns an empty result rather than raising when there is no index, because a
    missing precedent list must not stop a report from rendering. The verdict does
    not depend on this.
    """
    if index is None:
        try:
            index = load_index(path)
        except FileNotFoundError:
            return SearchResult(
                precedents=[], backend="none", degraded=False, index_size=0
            )

    if not query.strip() or len(index) == 0:
        return SearchResult(
            precedents=[], backend=index.backend, degraded=index.degraded,
            index_size=len(index),
        )

    vectors, backend = embed_texts([query], client=client)
    if not vectors:
        return SearchResult(
            precedents=[], backend=backend, degraded=True, index_size=len(index)
        )

    hits = index.nearest(vectors[0], k=k)
    precedents = [
        Precedent(
            detail_id=entry.detail_id,
            permit_number=entry.permit_number,
            score=score,
            summary=entry.summary,
            metadata=entry.metadata,
        )
        for entry, score in hits
        if score > 0.0
    ]

    # The query backend and the index backend must agree, otherwise the vectors
    # are not comparable and the scores are meaningless.
    mismatch = backend != index.backend
    return SearchResult(
        precedents=[] if mismatch else precedents,
        backend=f"{backend} vs index {index.backend}" if mismatch else backend,
        degraded=index.degraded or backend == "local-hashing-fallback" or mismatch,
        index_size=len(index),
    )


def search_for_facts(facts: dict[str, Any], k: int = 5, **kwargs) -> SearchResult:
    return search(query_from_facts(facts), k=k, **kwargs)
