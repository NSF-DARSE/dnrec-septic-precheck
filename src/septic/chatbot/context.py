"""Build grounded context for the chatbot from a review payload.

The context is a compact JSON summary of the review results. It is designed to
give the chatbot enough information to answer reviewer questions without sending
the entire OCR document or any personally identifiable information.

PII filtering:
    - Owner names, applicant names, contact information are stripped
    - Document hashes are stripped
    - Phone numbers, emails, addresses are stripped
    - Only regulation-relevant technical facts are kept
"""
from __future__ import annotations

import re
from typing import Any


# Fields in facts that may contain PII and should never be sent to Gemini.
PII_FACT_KEYS = frozenset({
    "owner_name",
    "owner_names",
    "applicant_name",
    "applicant",
    "contact_name",
    "contact",
    "phone",
    "phone_number",
    "email",
    "email_address",
    "address",
    "mailing_address",
    "street_address",
    "owner_address",
    "parcel_owner",
    "property_owner",
    "document_hash",
})

# Regex patterns for PII detection in string values.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)

# Subject keys that are safe to forward. Everything else is stripped.
_SAFE_SUBJECT_KEYS = frozenset({
    "document",
    "pages",
    "permit_number",
})


def _strip_pii_from_value(value: Any) -> Any:
    """Remove obvious PII patterns from a string value."""
    if not isinstance(value, str):
        return value
    # Replace email addresses
    value = _EMAIL_RE.sub("[email redacted]", value)
    # Replace phone numbers
    value = _PHONE_RE.sub("[phone redacted]", value)
    return value


# Pattern for Exhibit C note references: ", note b" or ", notes a, c, d, e, h, i"
_NOTE_REF_RE = re.compile(
    r",?\s*notes?\s+[a-z](?:\s*,\s*[a-z])*",
    re.IGNORECASE,
)


def _strip_note_references(text: str) -> str:
    """Remove Exhibit C note letter references from regulation quotes.

    These references (e.g., "100, note b" or "100, notes a, c, d, e, h, i")
    point to exception/reduction provisions. Leaving them in the context
    leads the model to discuss those exceptions.
    """
    return _NOTE_REF_RE.sub("", text).strip()


def _filter_facts(facts: list[dict]) -> list[dict]:
    """Filter facts_read list, removing PII-containing entries."""
    filtered = []
    for fact in facts:
        key = fact.get("parameter", fact.get("key", ""))
        if key.lower() in PII_FACT_KEYS:
            continue
        # Also check the value for embedded PII
        cleaned = dict(fact)
        if "value" in cleaned:
            cleaned["value"] = _strip_pii_from_value(cleaned["value"])
        # The 'raw' field often contains the original OCR text which may
        # include owner names, addresses, and contact information. Remove it
        # entirely. The chatbot only needs the extracted parameter and value.
        cleaned.pop("raw", None)
        # Strip PII from 'where' field description if present
        if "where" in cleaned:
            cleaned["where"] = _strip_pii_from_value(cleaned["where"])
        filtered.append(cleaned)
    return filtered


def _filter_facts_dict(facts: dict) -> dict:
    """Filter a facts dictionary, removing PII keys and values."""
    filtered = {}
    for key, value in facts.items():
        if key.lower() in PII_FACT_KEYS:
            continue
        filtered[key] = _strip_pii_from_value(value)
    return filtered


def _compact_finding(finding: dict) -> dict:
    """Extract the fields a chatbot needs from a finding, omitting bulk text.

    The caveats field is deliberately excluded. It contains detailed regulatory
    cross-references and interpretation notes (e.g., "Section 5.3.5.2 allows
    water-conservation reductions", "percolation averaging per 5.2.4.2.5.1")
    that, when included, lead the model to present them as independent regulatory
    findings.

    The remedy field is also excluded. It contains relocation advice ("Move the
    disposal area..."), Department-approval references, Exhibit C note reductions,
    and exception paths that the model presents as actionable recommendations.
    The chatbot must not advise on remedies, relocations, or exceptions.
    """
    # Strip Exhibit C note references from quotes (e.g., ", note b",
    # ", notes a, c, d, e, h, i") to prevent the model from discussing
    # what exceptions those notes provide.
    quote = finding.get("quote") or ""
    quote = _strip_note_references(quote) if quote else None

    return {
        "rule_id": finding.get("rule_id"),
        "outcome": finding.get("outcome"),
        "requirement": finding.get("requirement"),
        "reason": finding.get("reason"),
        "observed": finding.get("observed"),
        "threshold": finding.get("threshold"),
        "units": finding.get("units"),
        "severity": finding.get("severity"),
        "citation": finding.get("citation"),
        "section": finding.get("section"),
        "page": finding.get("page"),
        "quote": quote or None,
        "verified": finding.get("verified"),
        "applicability": finding.get("applicability"),
        "excluded_by": finding.get("excluded_by"),
        # Keep cross-references compact
        "cross_references": [
            {"label": xr.get("label"), "title": xr.get("title"), "page": xr.get("page")}
            for xr in (finding.get("cross_references") or [])
        ],
        "definitions": finding.get("definitions") or [],
        "exceptions": [
            {"section": ex.get("section"), "text": (ex.get("text") or "")[:200]}
            for ex in (finding.get("exceptions") or [])
        ],
    }


def _build_verdict_summary(
    headline: str,
    evaluated: int,
    not_applicable_count: int,
    unreadable: int,
    total: int,
) -> str:
    """Build a clear verdict summary that separates coverage from outcome.

    When NO DEFICIENCIES FOUND but UNKNOWN checks exist, this must make clear
    that the verdict covers only what was evaluated and is not an approval.
    """
    parts = []

    if headline == "NO DEFICIENCIES FOUND":
        if unreadable > 0:
            parts.append(
                f"No deficiencies were found among the {evaluated} checks "
                f"that could be evaluated. {unreadable} checks could not be "
                f"evaluated because the required information was not readable "
                f"from the packet. This is not an approval decision."
            )
        else:
            parts.append(
                "No deficiencies were found. All applicable checks ran. "
                "This is not an approval decision."
            )
    elif headline == "DEFICIENCIES FOUND":
        parts.append(
            "At least one requirement is not met. Each deficiency is cited "
            "with the regulation section it comes from."
        )
    elif headline == "CANNOT VERIFY":
        parts.append(
            "No check reached a decision. Either the values the rules need "
            "could not be read, or the rules have not been confirmed."
        )

    # Always add the breakdown
    breakdown = []
    if evaluated > 0:
        breakdown.append(f"{evaluated} applicable checks evaluated and satisfied")
    if unreadable > 0:
        breakdown.append(f"{unreadable} could not be evaluated")
    if not_applicable_count > 0:
        breakdown.append(f"{not_applicable_count} were not applicable to this system")
    if breakdown:
        parts.append("Coverage breakdown: " + "; ".join(breakdown) + f" (of {total} total).")

    return " ".join(parts)


def build_context(payload: dict) -> dict:
    """Build a compact grounded context from a review payload.

    This is the structured data the chatbot uses to answer questions. It
    contains only regulation-relevant information and no PII.

    Args:
        payload: The composed review payload dict (from Composed.to_json()).

    Returns:
        A dict suitable for serializing to JSON and including in the prompt.
    """
    # Subject: keep only safe fields
    subject = payload.get("subject") or {}
    safe_subject = {k: v for k, v in subject.items() if k in _SAFE_SUBJECT_KEYS}

    # Findings by category
    deficiencies = [_compact_finding(f) for f in (payload.get("deficiencies") or [])]
    unresolved = [_compact_finding(f) for f in (payload.get("unresolved") or [])]
    satisfied = [_compact_finding(f) for f in (payload.get("satisfied") or [])]
    not_applicable = [_compact_finding(f) for f in (payload.get("not_applicable") or [])]

    # Collect rule IDs that are not_applicable, whose blocked info is not
    # "missing" in the sense a reviewer needs to chase it.
    not_applicable_rule_ids = {
        f.get("rule_id") for f in (payload.get("not_applicable") or [])
    }

    # Missing information: only include items that block actually-unresolved
    # rules. Items that only block not_applicable rules are irrelevant to the
    # reviewer because those rules don't govern this system.
    raw_missing = payload.get("missing_information") or []
    missing = []
    for item in raw_missing:
        blocks = set(item.get("blocks_rules") or [])
        # Keep if it blocks at least one rule that is NOT in the not_applicable set
        if blocks - not_applicable_rule_ids:
            # Rename 'named' → 'field' for clarity
            cleaned = dict(item)
            if "named" in cleaned:
                cleaned["field"] = cleaned.pop("named")
            missing.append(cleaned)

    # Facts read, filtered for PII
    facts_read = _filter_facts(payload.get("facts_read") or [])

    # Build a clear verdict summary that separates coverage from outcome
    coverage = payload.get("coverage") or {}
    counts = payload.get("counts") or {}
    headline = payload.get("headline") or ""
    verdict_summary = _build_verdict_summary(
        headline=headline,
        evaluated=coverage.get("evaluated", 0),
        not_applicable_count=coverage.get("not_applicable", 0),
        unreadable=coverage.get("unreadable", 0),
        total=coverage.get("total", 0),
    )

    context = {
        "verdict": payload.get("verdict"),
        "headline": headline,
        "verdict_summary": verdict_summary,
        "explanation": payload.get("explanation"),
        "subject": safe_subject,
        "counts": counts,
        "coverage": coverage,
        "deficiencies": deficiencies,
        "unresolved": unresolved,
        "satisfied": satisfied,
        "not_applicable": not_applicable,
        "missing_information": missing,
        "facts_read": facts_read,
        "notices": payload.get("notices") or [],
    }
    return context


def build_context_message(payload: dict) -> str:
    """Build the grounded context as a formatted string for the prompt.

    This is included as a user message at the start of the chat to ground
    all subsequent answers.
    """
    import json

    context = build_context(payload)
    return (
        "GROUNDED CONTEXT. Use ONLY this data to answer questions. "
        "Do not invent citations or facts not present here.\n\n"
        + json.dumps(context, indent=2, default=str)
    )
