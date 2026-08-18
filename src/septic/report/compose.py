"""Assembling the report content from a rule Report.

Composition is deliberately separate from rendering so the verdict and the
itemised findings can be tested without a template.

Text generation on Bedrock is available to this account through the
us.anthropic.* cross-region inference profile, confirmed by preflight. An earlier
version of this note said generation was denied; that is no longer true.

What Bedrock is allowed to do here is narrow and worth stating plainly. It may
rephrase a remedy sentence into plainer language. It is handed the verdict and the
findings as inputs and is never asked to produce or review them. Every field a
reviewer acts on is filled in before any model is called, so composing a report
with the network unplugged produces the same verdict, the same findings, the same
citations, and the same counts, just less fluent prose. The wording pass is
therefore optional by construction rather than by a flag someone remembered to
set.

The reason is not architectural taste. This tool tells a state regulator that an
application is deficient and cites the regulation for it. If a model sat anywhere
on that path it would be impossible to say, later, whether the citation came from
the regulation or from the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..rules.schema import Outcome, Severity, Verdict

# Reviewer facing headline for each verdict. The enum values are internal.
VERDICT_HEADLINE = {
    Verdict.READY_TO_SUBMIT: "NO DEFICIENCIES FOUND",
    Verdict.LIKELY_RETURN: "DEFICIENCIES FOUND",
    Verdict.CANNOT_VERIFY: "CANNOT VERIFY",
}

VERDICT_EXPLANATION = {
    Verdict.READY_TO_SUBMIT: (
        "Every rule that could be evaluated passed. This is not an approval: it "
        "means this tool found nothing to flag among the checks it is able to run."
    ),
    Verdict.LIKELY_RETURN: (
        "At least one requirement is not met. Each item below cites the section of "
        "the regulation it comes from so it can be checked against the source."
    ),
    Verdict.CANNOT_VERIFY: (
        "This tool cannot give an answer. Either a value could not be read from "
        "the packet, or the rule needed has not been confirmed against the "
        "regulation by a person. Missing information is itself a common reason an "
        "application is returned, so the unresolved items below are worth "
        "attention rather than being treated as noise."
    ),
}


@dataclass
class Finding:
    """One rule result, as a reviewer reads it."""

    rule_id: str
    outcome: str
    requirement: str
    reason: str
    observed: Any
    threshold: Any
    units: str | None
    severity: str
    section: str
    page: int | None
    quote: str | None
    remedy: str | None
    verified: bool
    provenance: str | None = None
    cross_references: list[dict] = field(default_factory=list)
    definitions: list[dict] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    caveats: str | None = None

    @property
    def citation(self) -> str:
        return f"{self.section}, page {self.page}" if self.page else self.section

    def to_json(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "outcome": self.outcome,
            "requirement": self.requirement,
            "reason": self.reason,
            "observed": self.observed,
            "threshold": self.threshold,
            "units": self.units,
            "severity": self.severity,
            "citation": self.citation,
            "section": self.section,
            "page": self.page,
            "quote": self.quote,
            "remedy": self.remedy,
            "verified": self.verified,
            "provenance": self.provenance,
            "cross_references": self.cross_references,
            "definitions": self.definitions,
            "exceptions": self.exceptions,
            "caveats": self.caveats,
        }


@dataclass
class Composed:
    """Everything the renderers need. No rendering decisions live here."""

    verdict: str
    headline: str
    explanation: str
    subject: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    deficiencies: list[Finding] = field(default_factory=list)
    unresolved: list[Finding] = field(default_factory=list)
    satisfied: list[Finding] = field(default_factory=list)
    missing_information: list[dict] = field(default_factory=list)
    discarded_readings: list[dict] = field(default_factory=list)
    facts_read: list[dict] = field(default_factory=list)
    precedents: dict[str, Any] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    generated_at: str = ""
    wording_source: str = "rules and regulation text only"

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict,
            "headline": self.headline,
            "explanation": self.explanation,
            "subject": self.subject,
            "counts": self.counts,
            "deficiencies": [f.to_json() for f in self.deficiencies],
            "unresolved": [f.to_json() for f in self.unresolved],
            "satisfied": [f.to_json() for f in self.satisfied],
            "missing_information": self.missing_information,
            "discarded_readings": self.discarded_readings,
            "facts_read": self.facts_read,
            "precedents": self.precedents,
            "notices": self.notices,
            "generated_at": self.generated_at,
            "wording_source": self.wording_source,
        }


def _graph_context(graph, section: str) -> dict:
    """Cross references, definitions and exceptions for a cited section.

    Returns empty structures when the graph is absent or the section is not in it.
    A report must render without the graph; the graph enriches a finding, it does
    not enable one.
    """
    empty = {"cross_references": [], "definitions": [], "exceptions": []}
    if graph is None or not section:
        return empty
    try:
        from ..rules.graph import context as graph_context
    except Exception:  # noqa: BLE001
        return empty

    if section.lower().startswith("exhibit"):
        letter = section.split()[-1].upper()
        node_id = f"exhibit:{letter}"
        if node_id not in graph:
            return empty
        node = graph.nodes[node_id]
        return {
            "cross_references": [{
                "label": f"Exhibit {letter}",
                "title": node.get("title", ""),
                "page": node.get("page"),
                "text": (node.get("text") or "")[:600],
            }],
            "definitions": [],
            "exceptions": [],
        }

    ctx = graph_context(graph, section)
    if "error" in ctx:
        return empty
    return {
        "cross_references": [
            {
                "label": f"{r.get('type', '')} {r.get('number', '')}".strip(),
                "title": r.get("title", ""),
                "page": None,
                "text": (r.get("text") or "")[:400],
            }
            for r in (ctx.get("references") or [])[:4]
        ],
        "definitions": [
            {"term": d.get("term", ""), "defined_in": d.get("defined_in", "")}
            for d in (ctx.get("definitions") or [])[:6]
        ],
        "exceptions": [
            {
                "section": e.get("number", ""),
                "text": (e.get("text") or "")[:300],
            }
            for e in (ctx.get("exceptions") or [])[:3]
        ],
    }


def _finding_from(evaluation, graph, provenance: dict) -> Finding:
    rule = evaluation.rule
    units = f" {rule.units}" if rule.units else ""
    if rule.threshold is None:
        requirement = f"{rule.parameter} must be provided"
    else:
        requirement = f"{rule.parameter} {rule.operator.value} {rule.threshold}{units}"

    fact = provenance.get(rule.parameter)
    ctx = _graph_context(graph, rule.citation.section)

    caveats = None
    if rule.notes:
        # The notes field records documented reductions and scope limits that a
        # reviewer has to weigh before treating a failure as settled. Passing it
        # through matters most on the isolation distances, where several
        # reductions are available by Department approval.
        caveats = " ".join(rule.notes.split())

    return Finding(
        rule_id=rule.id,
        outcome=evaluation.outcome.value,
        requirement=requirement,
        reason=evaluation.reason,
        observed=evaluation.observed,
        threshold=rule.threshold,
        units=rule.units,
        severity=rule.severity.value,
        section=rule.citation.section,
        page=rule.citation.page,
        quote=rule.citation.quote,
        remedy=rule.remedy,
        verified=rule.verified,
        provenance=fact.describe() if fact else None,
        cross_references=ctx["cross_references"],
        definitions=ctx["definitions"],
        exceptions=ctx["exceptions"],
        caveats=caveats,
    )


def compose(
    report,
    extraction=None,
    graph=None,
    precedents=None,
    subject: dict[str, Any] | None = None,
) -> Composed:
    """Turn a rule Report into report content.

    report is the output of rules.engine.evaluate and is the sole source of the
    verdict. This function sorts, groups and annotates. It never recomputes an
    outcome and never suppresses one.
    """
    provenance = getattr(extraction, "provenance", {}) or {}
    missing = list(getattr(extraction, "missing", []) or [])

    verdict = report.verdict
    findings = [_finding_from(e, graph, provenance) for e in report.evaluations]

    deficiencies = [f for f in findings if f.outcome == Outcome.FAIL.value]
    unresolved = [f for f in findings if f.outcome == Outcome.UNKNOWN.value]
    satisfied = [f for f in findings if f.outcome == Outcome.PASS.value]

    # Return severity first, so the items that would actually get the application
    # returned are read first.
    deficiencies.sort(key=lambda f: (f.severity != Severity.RETURN.value, f.rule_id))

    notices: list[str] = []
    unverified = [f for f in findings if not f.verified]
    if unverified:
        notices.append(
            f"{len(unverified)} of {len(findings)} rules have not been confirmed "
            "against the regulation by a person, so they were not evaluated. Until "
            "a reviewer certifies them this tool cannot clear an application. See "
            "docs/rules_review.md."
        )

    # Which parameters the rules actually wanted but the packet did not supply.
    # Reported explicitly, never folded into a pass.
    wanted = {e.rule.parameter for e in report.evaluations}
    missing_information = []
    try:
        from ..ingest.extract import parameter_help
    except Exception:  # noqa: BLE001
        def parameter_help(name):  # type: ignore
            return name
    for parameter in sorted(wanted):
        if parameter in report.facts:
            continue
        rules_needing = sorted(
            e.rule.id for e in report.evaluations if e.rule.parameter == parameter
        )
        missing_information.append({
            "parameter": parameter,
            "means": parameter_help(parameter),
            "blocks_rules": rules_needing,
        })

    facts_read = [
        {
            "parameter": name,
            "value": fact.value,
            "source": fact.source,
            "where": fact.describe(),
            "raw": fact.raw,
        }
        for name, fact in sorted(provenance.items())
    ]

    precedent_payload = {}
    if precedents is not None:
        precedent_payload = (
            precedents.to_json() if hasattr(precedents, "to_json") else precedents
        )
        if precedent_payload.get("degraded"):
            notices.append(
                "Similar permits were matched with the offline stand-in embedder, "
                "not Titan, so the precedent list is not a semantic match and is "
                "shown for completeness only."
            )

    return Composed(
        verdict=verdict.value,
        headline=VERDICT_HEADLINE[verdict],
        explanation=VERDICT_EXPLANATION[verdict],
        subject=subject or {},
        counts=report.counts(),
        deficiencies=deficiencies,
        unresolved=unresolved,
        satisfied=satisfied,
        missing_information=missing_information,
        discarded_readings=list(getattr(extraction, "rejected", []) or []),
        facts_read=facts_read,
        precedents=precedent_payload,
        notices=notices,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
    )


# ---------------------------------------------------------------------------
# Optional wording pass.
# ---------------------------------------------------------------------------

REPHRASE_INSTRUCTION = (
    "Rewrite each numbered remedy below in plainer language for a homeowner. "
    "Keep every number, unit, and section reference exactly as written. Do not "
    "add, remove, combine, or reorder items. Do not comment on whether the "
    "application should be approved. Return the same count of numbered lines and "
    "nothing else."
)


def rephrase_remedies(composed: Composed, client=None, model: str | None = None
                      ) -> Composed:
    """Optionally soften remedy wording through Bedrock.

    Returns composed unchanged on any failure, and only accepts a response that
    returns exactly as many lines as it was given. The verdict, the findings, the
    thresholds and the citations are not passed for rewriting and cannot be
    altered by this call. If the model returns something unexpected the original
    wording stands, because a report that renders plainly is better than one that
    renders wrongly.
    """
    from .. import config

    targets = [f for f in composed.deficiencies if f.remedy]
    if not targets:
        return composed

    numbered = "\n".join(f"{i}. {f.remedy}" for i, f in enumerate(targets, 1))
    try:
        import json

        client = client or config.session().client("bedrock-runtime")
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1200,
            "temperature": 0,
            "messages": [
                {"role": "user",
                 "content": f"{REPHRASE_INSTRUCTION}\n\n{numbered}"}
            ],
        })
        response = client.invoke_model(
            modelId=model or config.BEDROCK_TEXT_MODEL,
            body=body,
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        text = "".join(
            block.get("text", "") for block in payload.get("content", [])
        )
    except Exception:  # noqa: BLE001 - any failure keeps the original wording
        composed.notices.append(
            "Plain language pass was unavailable, so remedies are shown in their "
            "original wording. Nothing else about this report is affected."
        )
        return composed

    lines = [
        line.strip() for line in text.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]
    if len(lines) != len(targets):
        composed.notices.append(
            "Plain language pass returned an unexpected number of items and was "
            "discarded. Remedies are shown in their original wording."
        )
        return composed

    import re as _re
    for finding, line in zip(targets, lines):
        finding.remedy = _re.sub(r"^\d+[.)]\s*", "", line)
    composed.wording_source = (
        "remedy wording rephrased by Bedrock; verdict, findings, thresholds and "
        "citations produced by the rules only"
    )
    return composed
