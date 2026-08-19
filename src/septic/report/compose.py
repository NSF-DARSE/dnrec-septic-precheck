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
    Verdict.NO_DEFICIENCIES: "NO DEFICIENCIES FOUND",
    Verdict.DEFICIENCIES_FOUND: "DEFICIENCIES FOUND",
    Verdict.CANNOT_VERIFY: "CANNOT VERIFY",
}

VERDICT_EXPLANATION = {
    Verdict.NO_DEFICIENCIES: (
        "Nothing was flagged among the checks that ran. This is not an approval, "
        "and it is not a statement about the checks that did not run: read it "
        "together with the coverage figure beside it, because a packet where "
        "little could be checked can reach this headline with most of the "
        "regulation still unexamined."
    ),
    Verdict.DEFICIENCIES_FOUND: (
        "At least one requirement is not met. Each item below cites the section of "
        "the regulation it comes from so it can be checked against the source."
    ),
    Verdict.CANNOT_VERIFY: (
        "No check reached a decision, so this tool has no answer at all. Either "
        "the values the rules need could not be read from the packet, or the rules "
        "needed have not been confirmed against the regulation by a person. "
        "Missing information is itself a common reason an application is "
        "returned, so the unresolved items below are worth attention rather than "
        "being treated as noise."
    ),
}


def coverage_sentence(coverage: dict) -> str:
    """The coverage figure as a sentence, for the explanation paragraph.

    Every surface shows coverage["text"] verbatim as the headline number. This is
    the longer form that says what the number means, so a reviewer who has never
    seen the tool before does not have to infer it.

    The two reasons a check did not run are named separately, because they mean
    opposite things to a reviewer. A rule that does not govern this kind of system
    is nothing to chase. A rule whose value could not be read is the reviewer's
    own next task.
    """
    evaluated = coverage.get("evaluated", 0)
    total = coverage.get("total", 0)
    not_applicable = coverage.get("not_applicable", 0)
    unreadable = coverage.get("unreadable", 0)
    if not total:
        return "No rules were applied to this packet."
    if not not_applicable and not unreadable:
        return (
            f"All {total} checks in the rule set ran against this packet, so the "
            f"verdict covers everything this tool checks."
        )

    sentences = [
        f"{evaluated} of the {total} checks in the rule set compared a value off "
        f"this packet against the regulation."
    ]
    if not_applicable:
        sentences.append(
            f"{not_applicable} do not govern this kind of system and were not "
            f"applied to it. They are listed separately below, with the value "
            f"that took each one out of scope, and they are not requirements this "
            f"packet met."
        )
    if unreadable:
        sentences.append(
            f"{unreadable} could not be read and are itemised below, each one "
            f"naming the value to read, where the packet normally carries it, and "
            f"the section to compare it against. A check that did not run is not "
            f"a check that passed."
        )
    return " ".join(sentences)


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
    # The fact this rule compares. Carried so the surfaces can name the value a
    # reviewer has to go and read, rather than recovering it out of requirement
    # text that also holds an operator and a threshold.
    parameter: str = ""
    provenance: str | None = None
    cross_references: list[dict] = field(default_factory=list)
    definitions: list[dict] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    caveats: str | None = None
    applicability: str = "applies"
    # The fact that took this rule out of scope: its name, the value read, and
    # where that value came from. Carried through from the Evaluation rather than
    # recovered from the reason text.
    excluded_by: dict | None = None
    # The bounding box of the fact that supplied this finding's observed value,
    # carried through so the viewer can highlight it on the rendered page.
    fact_box: dict | None = None
    fact_page: int | None = None
    # The parameter that blocked this check from running. For an undetermined
    # applicability check, this is the gate (excluded_by.parameter). For a check
    # whose value could not be read, this is the parameter itself. Both surfaces
    # group unresolved findings on this key without parsing the reason string.
    blocked_by: str | None = None

    @property
    def citation(self) -> str:
        return f"{self.section}, page {self.page}" if self.page else self.section

    def to_json(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "outcome": self.outcome,
            "applicability": self.applicability,
            "excluded_by": self.excluded_by,
            "requirement": self.requirement,
            "parameter": self.parameter,
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
            "fact_box": self.fact_box,
            "fact_page": self.fact_page,
            "cross_references": self.cross_references,
            "definitions": self.definitions,
            "exceptions": self.exceptions,
            "caveats": self.caveats,
            "blocked_by": self.blocked_by,
        }


@dataclass
class Composed:
    """Everything the renderers need. No rendering decisions live here."""

    verdict: str
    headline: str
    explanation: str
    subject: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    deficiencies: list[Finding] = field(default_factory=list)
    unresolved: list[Finding] = field(default_factory=list)
    satisfied: list[Finding] = field(default_factory=list)
    # Rules this packet is out of scope for. Separate from satisfied on purpose:
    # "does not apply because system_type is 'pressure dosed'" is not a
    # requirement that was met, and a reviewer must never read it as one.
    not_applicable: list[Finding] = field(default_factory=list)
    # Unresolved findings grouped by the parameter that blocked them. Each entry
    # carries the blocked_by key, a human description of why, and the findings.
    # Both the console and the printable report render from this rather than
    # computing groups themselves.
    unresolved_groups: list[dict] = field(default_factory=list)
    missing_information: list[dict] = field(default_factory=list)
    discarded_readings: list[dict] = field(default_factory=list)
    facts_read: list[dict] = field(default_factory=list)
    screening: dict[str, Any] = field(default_factory=dict)
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
            "coverage": self.coverage,
            "deficiencies": [f.to_json() for f in self.deficiencies],
            "unresolved": [f.to_json() for f in self.unresolved],
            "unresolved_groups": self.unresolved_groups,
            "satisfied": [f.to_json() for f in self.satisfied],
            "not_applicable": [f.to_json() for f in self.not_applicable],
            "missing_information": self.missing_information,
            "discarded_readings": self.discarded_readings,
            "facts_read": self.facts_read,
            "screening": self.screening,
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


def _finding_from(evaluation, graph, provenance: dict,
                  facts: dict | None = None) -> Finding:
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

    # A rule taken out of scope was excluded by a fact that is usually not the
    # one it would have compared, so its own provenance is the wrong thing to
    # show. SLOPE-001 compares disposal_slope and is excluded by system_type. The
    # gating fact comes off the Evaluation, so this never parses the reason text.
    excluded_by = None
    gating = getattr(evaluation, "applicability_parameter", None)
    if gating:
        gating_fact = provenance.get(gating)
        excluded_by = {
            "parameter": gating,
            "value": (facts or {}).get(gating),
            "where": gating_fact.describe() if gating_fact else None,
        }

    applicability_value = getattr(
        getattr(evaluation, "applicability", None), "value", "applies"
    )
    if applicability_value == "undetermined" and gating:
        blocked_by = gating
    elif evaluation.outcome.value == "UNKNOWN":
        blocked_by = rule.parameter
    else:
        blocked_by = None

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
        parameter=rule.parameter,
        provenance=fact.describe() if fact else None,
        fact_box=fact.box.to_json() if fact and fact.box else None,
        fact_page=fact.page if fact else None,
        cross_references=ctx["cross_references"],
        definitions=ctx["definitions"],
        exceptions=ctx["exceptions"],
        caveats=caveats,
        applicability=applicability_value,
        excluded_by=excluded_by,
        blocked_by=blocked_by,
    )


def compose(
    report,
    extraction=None,
    graph=None,
    precedents=None,
    screening=None,
    subject: dict[str, Any] | None = None,
) -> Composed:
    """Turn a rule Report into report content.

    report is the output of rules.engine.evaluate and is the sole source of the
    verdict. This function sorts, groups and annotates. It never recomputes an
    outcome and never suppresses one.

    screening is geospatial context. It is presented as a screening flag telling
    the reviewer what to check on the site plan, and it is not a finding. The
    regulation measures isolation distance from the disposal area, while a geocoded
    point is somewhere on the parcel, so the two are not the same measurement and
    this must never read as compliance. See src/septic/geo.py.
    """
    provenance = getattr(extraction, "provenance", {}) or {}
    missing = list(getattr(extraction, "missing", []) or [])

    verdict = report.verdict
    coverage = report.coverage()
    findings = [
        _finding_from(e, graph, provenance, report.facts) for e in report.evaluations
    ]
    by_rule = {f.rule_id: f for f in findings}

    deficiencies = [f for f in findings if f.outcome == Outcome.FAIL.value]
    unresolved = [f for f in findings if f.outcome == Outcome.UNKNOWN.value]
    # The engine decides which passes are rules that never applied. Grouping reads
    # that decision off the evaluations rather than re-deriving it here, so the
    # report and the verdict cannot disagree about what ran.
    not_applicable = [by_rule[e.rule.id] for e in report.not_applicable]
    excluded_ids = {f.rule_id for f in not_applicable}
    satisfied = [
        f for f in findings
        if f.outcome == Outcome.PASS.value and f.rule_id not in excluded_ids
    ]

    # Return severity first, so the items that would actually get the application
    # returned are read first.
    deficiencies.sort(key=lambda f: (f.severity != Severity.RETURN.value, f.rule_id))

    notices: list[str] = []
    unverified = [f for f in findings if not f.verified]
    if unverified:
        notices.append(
            f"{len(unverified)} of {len(findings)} rules have not been confirmed "
            "against the regulation by a person, so they were not evaluated and "
            "are counted against coverage rather than as passes. See "
            "docs/rules_review.md."
        )

    # Which parameters the rules actually wanted but the packet did not supply.
    # Reported explicitly, never folded into a pass. Each one carries where the
    # packet normally holds it, from the same table the unread checks are worded
    # from, so the two lists cannot describe the same value differently.
    wanted = {e.rule.parameter for e in report.evaluations}
    missing_information = []
    try:
        from ..ingest.extract import parameter_help
    except Exception:  # noqa: BLE001
        def parameter_help(name):  # type: ignore
            return name
    from .wording import parameter_location, parameter_name
    for parameter in sorted(wanted):
        if parameter in report.facts:
            continue
        rules_needing = sorted(
            e.rule.id for e in report.evaluations if e.rule.parameter == parameter
        )
        missing_information.append({
            "parameter": parameter,
            "means": parameter_help(parameter),
            "named": parameter_name(parameter),
            "normally_found": parameter_location(parameter),
            "blocks_rules": rules_needing,
        })

    facts_read = [
        {
            "parameter": name,
            "value": fact.value,
            "source": fact.source,
            "where": fact.describe(),
            "raw": fact.raw,
            "page": fact.page,
            "box": fact.box.to_json() if fact.box is not None else None,
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

    screening_payload = {}
    if screening is not None:
        screening_payload = (
            screening.to_json() if hasattr(screening, "to_json") else screening
        )

    # Group unresolved findings by blocked_by so both surfaces render them
    # grouped by cause rather than as 14 separate rows repeating the same text.
    from .wording import parameter_name, parameter_location
    grouped_order: list[str] = []
    grouped_map: dict[str, list[Finding]] = {}
    for f in unresolved:
        key = f.blocked_by or f.parameter or "unknown"
        if key not in grouped_map:
            grouped_order.append(key)
            grouped_map[key] = []
        grouped_map[key].append(f)
    unresolved_groups = []
    for key in grouped_order:
        members = grouped_map[key]
        name = parameter_name(key)
        location = parameter_location(key)
        unresolved_groups.append({
            "blocked_by": key,
            "description": name,
            "location": location,
            "count": len(members),
            "findings": [f.to_json() for f in members],
        })

    return Composed(
        verdict=verdict.value,
        headline=VERDICT_HEADLINE[verdict],
        explanation=(
            f"{VERDICT_EXPLANATION[verdict]} {coverage_sentence(coverage)}"
        ),
        subject=subject or {},
        counts=report.counts(),
        coverage=coverage,
        deficiencies=deficiencies,
        unresolved=unresolved,
        unresolved_groups=unresolved_groups,
        satisfied=satisfied,
        not_applicable=not_applicable,
        missing_information=missing_information,
        discarded_readings=list(getattr(extraction, "rejected", []) or []),
        facts_read=facts_read,
        screening=screening_payload,
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
