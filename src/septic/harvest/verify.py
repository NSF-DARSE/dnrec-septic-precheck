"""Rechecking permits, and confirming a document count of zero.

The Documents grid is served non-deterministically. Refetching a permit that has
documents can return a page without the grid, which parses as zero documents.
Measured on 16 permits, 5 of 8 known to have documents returned a different count
on refetch, including zero, and the HTML length changed with it.

So a single observation of zero is not evidence. confirm_zero refetches until it
sees the same count twice in a row, and reports the outcome it reached rather
than collapsing everything into a number. A failed fetch is never counted as
zero documents.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .detail import Fetcher, RateLimiter, load_detail

# Outcomes reported per permit. Kept explicit so a fetch failure can never be
# mistaken for an empty grid.
PARSED_DOCS = "PARSED_DOCS"
PARSED_ZERO_DOCS = "PARSED_ZERO_DOCS"
GRID_ABSENT = "GRID_ABSENT"
FETCH_FAILED = "FETCH_FAILED"
PARSE_ERROR = "PARSE_ERROR"
UNSTABLE = "UNSTABLE"


@dataclass
class Observation:
    attempt: int
    outcome: str
    doc_count: int
    status_code: int | None
    page_bytes: int
    error: str | None = None


@dataclass
class PermitCheck:
    detail_id: str
    permit_number: str | None
    recorded_docs: int
    observations: list[Observation] = field(default_factory=list)
    outcome: str = ""
    doc_count: int | None = None

    @property
    def counts_seen(self) -> list[int]:
        return [o.doc_count for o in self.observations]

    def to_json(self) -> dict:
        return {
            "detail_id": self.detail_id,
            "permit_number": self.permit_number,
            "recorded_docs": self.recorded_docs,
            "outcome": self.outcome,
            "doc_count": self.doc_count,
            "counts_seen": self.counts_seen,
            "observations": [o.__dict__ for o in self.observations],
        }


def confirm_zero(fetcher: Fetcher, detail_id: str, permit_number: str | None = None,
                 recorded_docs: int = 0, attempts: int = 3) -> PermitCheck:
    """Refetch a permit until two consecutive responses agree.

    Returns UNSTABLE when the count never repeats, which is a real state for this
    site and more honest than picking one of the answers.
    """
    check = PermitCheck(
        detail_id=str(detail_id),
        permit_number=permit_number,
        recorded_docs=recorded_docs,
    )

    previous: int | None = None
    for attempt in range(1, attempts + 1):
        try:
            page = load_detail(fetcher, detail_id)
        except Exception as exc:
            check.observations.append(
                Observation(attempt, PARSE_ERROR, -1, None, 0, f"{type(exc).__name__}: {exc}")
            )
            continue

        count = len(page.documents)
        check.observations.append(
            Observation(
                attempt=attempt,
                outcome=page.outcome,
                doc_count=count if page.fetch.ok else -1,
                status_code=page.fetch.status_code,
                page_bytes=page.fetch.length,
                error=page.fetch.error,
            )
        )

        if not page.fetch.ok:
            previous = None
            continue

        if previous is not None and previous == count:
            check.doc_count = count
            check.outcome = PARSED_DOCS if count else PARSED_ZERO_DOCS
            return check
        previous = count

    successful = [o for o in check.observations if o.doc_count >= 0]
    if not successful:
        check.outcome = (
            PARSE_ERROR
            if any(o.outcome == PARSE_ERROR for o in check.observations)
            else FETCH_FAILED
        )
        return check

    best = max(o.doc_count for o in successful)
    check.doc_count = best
    if len({o.doc_count for o in successful}) > 1:
        check.outcome = UNSTABLE
    elif best:
        check.outcome = PARSED_DOCS
    else:
        check.outcome = (
            PARSED_ZERO_DOCS
            if any(o.outcome == PARSED_ZERO_DOCS for o in successful)
            else GRID_ABSENT
        )
    return check


def recheck_manifest(manifest_path: Path, only_zero: bool = True, limit: int = 0,
                     attempts: int = 3, min_interval: float = 1.0) -> list[PermitCheck]:
    """Recheck permits from a manifest, by default only those recorded with zero."""
    records = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    targets = [
        r for r in records
        if not only_zero or not r.get("documents")
    ]
    if limit:
        targets = targets[:limit]

    fetcher = Fetcher(RateLimiter(min_interval))
    return [
        confirm_zero(
            fetcher,
            r["detail_id"],
            r.get("permitNumber"),
            len(r.get("documents") or []),
            attempts=attempts,
        )
        for r in targets
    ]


def render(checks: list[PermitCheck]) -> str:
    outcomes = Counter(c.outcome for c in checks)
    lines = [
        f"rechecked {len(checks)} permits",
        "",
        "outcomes:",
    ]
    for outcome, count in outcomes.most_common():
        lines.append(f"  {outcome:<18}{count}")

    recovered = [c for c in checks if c.recorded_docs == 0 and (c.doc_count or 0) > 0]
    lines += [
        "",
        f"recorded zero but found documents: {len(recovered)}",
    ]
    for c in recovered:
        lines.append(
            f"  permit={c.permit_number} detail_id={c.detail_id} "
            f"counts_seen={c.counts_seen}"
        )

    unstable = [c for c in checks if c.outcome == UNSTABLE]
    if unstable:
        lines += ["", f"unstable (count never repeated): {len(unstable)}"]
        for c in unstable:
            lines.append(f"  permit={c.permit_number} counts_seen={c.counts_seen}")

    failed = [c for c in checks if c.outcome in (FETCH_FAILED, PARSE_ERROR)]
    if failed:
        lines += ["", f"could not be checked: {len(failed)}"]
        for c in failed:
            lines.append(f"  permit={c.permit_number} outcome={c.outcome}")

    if recovered:
        lines += [
            "",
            "A permit recorded with zero documents that returns documents on "
            "refetch confirms the single pass under-collects.",
        ]
    return "\n".join(lines)
