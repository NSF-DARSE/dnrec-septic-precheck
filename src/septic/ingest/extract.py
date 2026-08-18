"""Turning a read document into the facts the rule engine expects.

The rule engine asks for parameters by name: dist_disposal_to_well, perc_rate,
limiting_zone_depth and so on. This module produces those names from what
Textract could read off the packet, and nothing else. The parameter vocabulary is
defined once, here, in FACTS, and it is the contract with rules_7101.yaml. If a
rule names a parameter this module does not produce, the engine returns UNKNOWN
for that rule, which is the correct outcome and not a failure.

Two rules govern everything here.

Absent is not zero. A field the extractor could not read is left out of the fact
mapping entirely rather than defaulted, because a missing setback distance and a
setback distance of zero mean opposite things to a reviewer. The engine turns an
absent parameter into UNKNOWN, and a missing field on a plan is itself a reason
DNREC returns an application, so reporting it honestly is the whole point.

Every fact carries its provenance. A reviewer has to be able to see where a
number came from, so each fact records whether it came from a form field or from a
text pattern, which page it was on, the raw text it was read from, and Textract's
confidence. A value with no provenance is not usable in a report a regulator
will read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .layout import Document

# ---------------------------------------------------------------------------
# The parameter vocabulary. This is the contract with rules_7101.yaml.
# ---------------------------------------------------------------------------

# For each fact: the form field labels that carry it, and the text patterns that
# can recover it when the form field is missing. Labels are matched on whole
# tokens, patterns strictly, because loose matching over OCR noise invents
# numbers rather than finding them.
#
# range is a plausibility bound, not a regulatory limit. Its only job is to catch
# OCR misreads: a stray mark becomes a digit, a gridline becomes a decimal point,
# and a 19 page scanned packet produces a few of those every time. A value outside
# the bound is discarded and reported as unreadable, which is the safe direction,
# since a reviewer told a value could not be read will go and look at the plan.
# Bounds are deliberately wide enough that no realistic site is excluded.
#
# allowed constrains a category to a known vocabulary, so a mis-paired form field
# cannot inject "1 inch = 50 feet" as a system scale.
FACTS: dict[str, dict[str, Any]] = {
    "system_scale": {
        "kind": "category",
        "labels": ["system scale", "design flow category"],
        "allowed": {"small", "large"},
        "patterns": [],
        "help": "small for under 2500 gallons per day, large at or above",
    },
    "system_type": {
        "kind": "category",
        "labels": ["system type", "type of system", "proposed system",
                   "septic system type"],
        "allowed": {
            "gravity", "sand mound", "low pressure pipe", "wisconsin at-grade",
            "pressure dosed", "sand-lined", "drip", "conventional",
        },
        "patterns": [
            (r"\b(full\s+depth\s+gravity|capping\s+fill\s+gravity|gravity)\b",
             "gravity"),
            (r"\b(elevated\s+sand\s+mound|sand\s+mound)\b", "sand mound"),
            (r"\b(low\s+pressure\s+pipe|\bLPP\b)\b", "low pressure pipe"),
            (r"\b(wisconsin\s+at[\s-]?grade)\b", "wisconsin at-grade"),
            (r"\b(pressure\s+dosed)\b", "pressure dosed"),
            (r"\b(sand[\s-]?lined)\b", "sand-lined"),
            (r"\b(micro[\s-]?drip|drip\s+irrigation)\b", "drip"),
        ],
        "help": "gravity, sand mound, low pressure pipe and so on",
    },
    "use_type": {
        "kind": "category",
        "labels": ["property use", "use type", "prop use", "type of structure",
                   "occupancy"],
        "allowed": {"residential", "commercial"},
        "patterns": [
            (r"\b(single\s+family\s+dwelling|single\s+family)\b", "residential"),
            (r"\b(multi[\s-]?family)\b", "residential"),
            (r"\b(residential)\b", "residential"),
            (r"\b(commercial)\b", "commercial"),
        ],
        "help": "residential or commercial",
    },
    "absorption_type": {
        "kind": "category",
        "labels": ["absorption facility", "absorption type", "disposal type"],
        "allowed": {"trench", "bed"},
        "patterns": [
            (r"\bseepage\s+bed\b", "bed"),
            (r"\bseepage\s+trench(?:es)?\b", "trench"),
            (r"\btrench(?:es)?\b", "trench"),
            (r"\bbed\s+system\b", "bed"),
        ],
        "help": "trench or bed",
    },
    "dist_disposal_to_well": {
        "kind": "number",
        "units": "feet",
        "range": (0, 1000),
        "labels": ["disposal area to well", "distance to well",
                   "absorption to well", "well isolation", "well distance",
                   "isolation to well"],
        "patterns": [
            (r"(?:disposal|absorption)[^.\n]{0,40}?to\s+well[^0-9\n]{0,20}"
             r"(\d{1,4}(?:\.\d+)?)\s*(?:feet|foot|ft)", None),
        ],
        "help": "feet from the disposal area to the nearest well",
    },
    "dist_disposal_to_watercourse": {
        "kind": "number",
        "units": "feet",
        "range": (0, 5000),
        "labels": ["disposal area to watercourse", "distance to watercourse",
                   "watercourse isolation", "distance to surface water",
                   "distance to water body"],
        "patterns": [
            (r"(?:disposal|absorption)[^.\n]{0,40}?to\s+(?:watercourse|surface\s+water)"
             r"[^0-9\n]{0,20}(\d{1,4}(?:\.\d+)?)\s*(?:feet|foot|ft)", None),
        ],
        "help": "feet from the disposal area to the nearest watercourse",
    },
    "dist_disposal_to_property_line": {
        "kind": "number",
        "units": "feet",
        "range": (0, 1000),
        "labels": ["disposal area to property line", "distance to property line",
                   "property line isolation", "distance to lot line"],
        "patterns": [
            (r"(?:disposal|absorption)[^.\n]{0,40}?to\s+(?:property|lot)\s+line"
             r"[^0-9\n]{0,20}(\d{1,4}(?:\.\d+)?)\s*(?:feet|foot|ft)", None),
        ],
        "help": "feet from the disposal area to the nearest property line",
    },
    "dist_disposal_to_escarpment": {
        "kind": "number",
        "units": "feet",
        "range": (0, 1000),
        "labels": ["distance to escarpment", "distance to top of bank"],
        "patterns": [
            (r"(?:escarpment|top\s+of\s+bank)[^0-9\n]{0,20}"
             r"(\d{1,4}(?:\.\d+)?)\s*(?:feet|foot|ft)", None),
        ],
        "help": "feet from the disposal area to the top of bank or escarpment",
    },
    "dist_tank_to_well": {
        "kind": "number",
        "units": "feet",
        "range": (0, 1000),
        "labels": ["septic tank to well", "tank to well", "tank well distance"],
        "patterns": [
            (r"(?:septic\s+)?tank[^.\n]{0,30}?to\s+well[^0-9\n]{0,20}"
             r"(\d{1,4}(?:\.\d+)?)\s*(?:feet|foot|ft)", None),
        ],
        "help": "feet from the septic tank to the nearest well",
    },
    "dist_tank_to_watercourse": {
        "kind": "number",
        "units": "feet",
        "range": (0, 5000),
        "labels": ["septic tank to watercourse", "tank to watercourse"],
        "patterns": [
            (r"(?:septic\s+)?tank[^.\n]{0,30}?to\s+watercourse[^0-9\n]{0,20}"
             r"(\d{1,4}(?:\.\d+)?)\s*(?:feet|foot|ft)", None),
        ],
        "help": "feet from the septic tank to the nearest watercourse",
    },
    "perc_rate": {
        "kind": "number",
        "units": "minutes per inch",
        "range": (0.1, 600),
        "labels": ["perc rate", "percolation rate", "perk rate",
                   "percolation test rate", "average percolation rate",
                   "avg percolation rate"],
        "patterns": [
            (r"(?:perc(?:olation)?|perk)\s*(?:rate)?[^0-9\n]{0,15}"
             r"(\d{1,3}(?:\.\d+)?)\s*(?:mpi|min(?:ute)?s?\s*/?\s*(?:per\s*)?inch)",
             None),
            (r"(\d{1,3}(?:\.\d+)?)\s*(?:mpi\b|min(?:ute)?s?\s*per\s*inch)", None),
        ],
        "help": "site average percolation rate in minutes per inch",
    },
    "perc_test_holes": {
        "kind": "number",
        "units": "holes",
        "range": (1, 40),
        "labels": ["number of test holes", "test holes", "perc holes",
                   "number of percolation holes"],
        "patterns": [
            (r"(\d{1,2})\s*(?:perc(?:olation)?\s*)?(?:test\s*)?holes\b", None),
        ],
        "help": "count of percolation test holes recorded",
    },
    "limiting_zone_depth": {
        "kind": "number",
        "units": "inches",
        "range": (0, 240),
        "labels": ["limiting zone", "depth to limiting zone",
                   "limiting zone depth", "depth to water table",
                   "seasonal high water table"],
        "patterns": [
            (r"(?:limiting\s+zone|water\s+table)[^0-9\n]{0,25}"
             r"(\d{1,3}(?:\.\d+)?)\s*(?:inches|inch|in\b)", None),
        ],
        "help": "inches from the soil surface to the limiting zone",
    },
    "limiting_zone_below_trench_bottom": {
        "kind": "number",
        "units": "inches",
        "range": (0, 240),
        "labels": ["separation below trench", "separation distance",
                   "limiting zone below trench bottom",
                   "vertical separation"],
        "patterns": [
            (r"(?:separation|below\s+trench(?:\s+bottom)?)[^0-9\n]{0,25}"
             r"(\d{1,3}(?:\.\d+)?)\s*(?:inches|inch|in\b)", None),
        ],
        "help": "inches between the trench bottom and the limiting zone",
    },
    "design_flow": {
        "kind": "number",
        "units": "gallons per day",
        "range": (20, 100000),
        "labels": ["design flow", "flow rate", "daily flow", "design daily flow",
                   "projected flow", "gallons per day", "gallons per day flow"],
        "patterns": [
            (r"(?:design\s+)?flow[^0-9\n]{0,20}(\d{1,3}(?:,\d{3})?)\s*"
             r"(?:gpd|gallons?\s+per\s+day)", None),
            (r"(\d{2,5})\s*(?:gpd\b|gallons?\s+per\s+day)", None),
        ],
        "help": "design flow in gallons per day",
    },
    "bedrooms": {
        "kind": "number",
        "units": "bedrooms",
        "range": (1, 20),
        "labels": ["bedrooms", "number of bedrooms", "no of bedrooms"],
        "patterns": [
            (r"(\d{1,2})\s*bedrooms?\b", None),
            (r"bedrooms?[^0-9\n]{0,10}(\d{1,2})\b", None),
        ],
        "help": "bedroom count, used to derive flow per bedroom",
    },
    "disposal_slope": {
        "kind": "number",
        "units": "percent",
        "range": (0, 60),
        "labels": ["disposal area slope", "percent slope", "ground slope",
                   "site slope"],
        "patterns": [
            (r"slope[^0-9\n]{0,15}(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)", None),
            (r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*slope", None),
        ],
        "help": "slope of the disposal area in percent",
    },
    "site_evaluation_report": {
        "kind": "presence",
        "labels": ["site evaluation", "site evaluation report",
                   "site evaluation number", "class d soil scientist"],
        "patterns": [
            (r"site\s+evaluation\s+(?:report|no|number|#)", "present"),
            (r"class\s+D\s+soil\s+scientist", "present"),
        ],
        "help": "whether a site evaluation report accompanies the packet",
    },
    "wells_within_150_feet_shown": {
        "kind": "presence",
        "labels": ["wells within 150 feet", "adjacent wells", "existing wells"],
        "patterns": [
            (r"wells?\s+within\s+150\s*(?:feet|ft)", "present"),
            (r"(?:adjacent|existing|on-?site)\s+wells?\s+(?:shown|located|noted)",
             "present"),
        ],
        "help": "whether the drawing marks wells within 150 feet",
    },
}

# Derived facts are computed from other facts rather than read off the page.
# Kept separate so provenance can say so plainly.
DERIVED = ("design_flow_per_bedroom",)

NUMBER_RE = re.compile(r"\d{1,4}(?:,\d{3})?(?:\.\d+)?")

# Values that mean "the form asked and nobody answered". Treated as absent.
BLANK_VALUES = {
    "", "-", "--", "n/a", "na", "none", "not provided", "tbd", "unknown",
    "not applicable", "see plan", "x",
}


@dataclass
class Fact:
    """One extracted value and where it came from."""

    name: str
    value: Any
    source: str            # form_field | text_pattern | derived
    raw: str = ""
    page: int | None = None
    confidence: float | None = None
    label: str | None = None
    note: str | None = None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "source": self.source,
            "raw": self.raw,
            "page": self.page,
            "confidence": (
                round(self.confidence, 1) if self.confidence is not None else None
            ),
            "label": self.label,
            "note": self.note,
        }

    def describe(self) -> str:
        """One line a reviewer can read."""
        if self.source == "form_field":
            where = f"form field {self.label!r}"
        elif self.source == "text_pattern":
            where = "text on the page"
        else:
            where = self.note or "derived"
        page = f", page {self.page}" if self.page else ""
        conf = (
            f", OCR confidence {self.confidence:.0f}%"
            if self.confidence is not None else ""
        )
        return f"{where}{page}{conf}"


@dataclass
class Extraction:
    """The fact mapping for the engine, plus provenance for the report."""

    facts: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Fact] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "facts": self.facts,
            "provenance": {k: v.to_json() for k, v in self.provenance.items()},
            "missing": self.missing,
            "rejected": self.rejected,
        }


def _is_blank(text: str) -> bool:
    return text.strip().lower() in BLANK_VALUES


def _first_number(text: str) -> float | None:
    match = NUMBER_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return [t for t in cleaned.split() if t]


# Tokens too generic to carry a match on their own.
WEAK_TOKENS = {
    "to", "of", "the", "a", "an", "in", "on", "no", "number", "distance",
    "type", "site", "rate", "area", "system", "n", "s", "e", "w", "x", "y",
}


def _label_score(label: str, candidates: list[str]) -> int:
    """How well a form field label matches a fact's expected labels.

    Returns 0 for no match, higher for a better one.

    This used to be a raw substring test in both directions, which was badly
    wrong: the OCR label "N" scored against "distance to well" because the letter
    n appears inside the word distance. One single character artifact on page 18
    of a real packet matched nine different facts and gave them all the same
    value. On a verified rule set that would have put nine fabricated numbers in
    front of a reviewer, so matching is now on whole tokens only.

    A match requires either every significant token of a candidate to appear in
    the label, or the label to be a multi token subset of a candidate. A single
    weak token such as "scale" or "site" never matches on its own, because a
    drawing scale and a system scale are different things and guessing between
    them is how a wrong value reaches a report.
    """
    label_tokens = _tokens(label)
    if not label_tokens:
        return 0
    label_set = set(label_tokens)
    strong_label = {t for t in label_set if t not in WEAK_TOKENS and len(t) > 1}
    if not strong_label:
        return 0

    best = 0
    for candidate in candidates:
        candidate_tokens = _tokens(candidate)
        if not candidate_tokens:
            continue
        candidate_set = set(candidate_tokens)
        strong_candidate = {
            t for t in candidate_set if t not in WEAK_TOKENS and len(t) > 1
        }
        if not strong_candidate:
            continue

        # A one word label may only claim a one word fact label. Otherwise a
        # drawing field marked "SCALE:" claims "system scale", and a field marked
        # "SITE" claims "site slope", both of which happened on real packets.
        # Counted on all tokens, not just strong ones, so a genuinely specific
        # label like "Site Evaluation Number" is still allowed to match.
        if len(label_tokens) == 1 and len(candidate_tokens) > 1:
            continue

        # The label must contain the whole candidate. Matching the other way,
        # where a short label is a subset of a longer candidate, was tried and
        # removed: a field marked "LIMITING ZONE =" matched the candidate
        # "limiting zone below trench bottom" and so filled in a separation
        # distance from a depth measurement. Those are different quantities and
        # the packet gave only one of them, so a rule comparing the wrong one
        # would produce a confident and wrong finding. Abbreviated labels are
        # handled by listing the short form in labels instead of by inference.
        if strong_candidate <= label_set:
            best = max(best, 10 + len(strong_candidate))
    return best


def _check_range(name: str, value: float, spec: dict) -> str | None:
    """Reject a number outside the plausible range for its fact.

    OCR reads a stray mark as a digit and a table gridline as a decimal point, so
    a reading of 3500 feet to an escarpment or a 94 percent slope is noise, not
    data. Rejecting it makes the value absent, which the engine reports as UNKNOWN
    and the report lists as missing information. That is the safe direction: a
    reviewer told a value could not be read will go and look, while a reviewer
    shown 3500 feet may not.

    Returns a reason string when the value should be rejected, or None to keep it.
    """
    bounds = spec.get("range")
    if not bounds:
        return None
    low, high = bounds
    if value < low or value > high:
        units = spec.get("units", "")
        return (
            f"{value:g} {units} is outside the plausible range "
            f"{low:g} to {high:g} for {name}, so it was discarded as a "
            f"misread rather than passed to the rules"
        )
    return None


def _from_form_fields(
    document: Document, name: str, spec: dict, rejected: list[dict]
) -> Fact | None:
    labels = spec.get("labels") or []
    if not labels:
        return None

    # Score every field and take the best, rather than the first that matches.
    scored: list[tuple[int, Any]] = []
    for form_field in document.fields:
        score = _label_score(form_field.key, labels)
        if score:
            scored.append((score, form_field))
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], pair[1].page))

    kind = spec["kind"]
    for _, form_field in scored:
        value_text = form_field.value.strip()
        if _is_blank(value_text):
            # The label exists and the answer is blank. Still absent, but worth
            # recording: a blank field on a form is a finding in its own right.
            rejected.append({
                "parameter": name,
                "reason": f"form field {form_field.key.strip()!r} is present but blank",
                "page": form_field.page,
            })
            continue
        if kind == "number":
            number = _first_number(value_text)
            if number is None:
                continue
            problem = _check_range(name, number, spec)
            if problem:
                rejected.append({
                    "parameter": name,
                    "reason": problem,
                    "page": form_field.page,
                    "raw": value_text,
                })
                continue
            value: Any = number
        elif kind == "presence":
            value = "present"
        else:
            allowed = spec.get("allowed")
            normalized = value_text.strip().lower()
            if allowed and normalized not in allowed:
                rejected.append({
                    "parameter": name,
                    "reason": (
                        f"form field {form_field.key.strip()!r} read as "
                        f"{value_text!r}, which is not one of {sorted(allowed)}"
                    ),
                    "page": form_field.page,
                    "raw": value_text,
                })
                continue
            value = normalized if allowed else value_text
        return Fact(
            name=name,
            value=value,
            source="form_field",
            raw=value_text,
            page=form_field.page,
            confidence=form_field.confidence,
            label=form_field.key.strip().rstrip(":"),
        )
    return None


def _from_text(
    document: Document, name: str, spec: dict, rejected: list[dict]
) -> Fact | None:
    patterns = spec.get("patterns") or []
    if not patterns:
        return None
    kind = spec["kind"]
    # Search page by page so the provenance can name a page.
    for page in range(1, max(document.pages, 1) + 1):
        page_text = document.text(page)
        if not page_text:
            continue
        flat = " ".join(page_text.split())
        for pattern, fixed_value in patterns:
            match = re.search(pattern, flat, re.IGNORECASE)
            if not match:
                continue
            if kind == "number":
                if not match.groups():
                    continue
                number = _first_number(match.group(1))
                if number is None:
                    continue
                problem = _check_range(name, number, spec)
                if problem:
                    rejected.append({
                        "parameter": name,
                        "reason": problem,
                        "page": page,
                        "raw": match.group(0)[:80],
                    })
                    continue
                value: Any = number
            elif kind == "presence":
                value = fixed_value or "present"
            else:
                value = fixed_value or match.group(0).strip().lower()
            start = max(0, match.start() - 30)
            return Fact(
                name=name,
                value=value,
                source="text_pattern",
                raw=flat[start:match.end() + 20].strip(),
                page=page,
            )
    return None


def _infer_system_scale(extraction: Extraction) -> Fact | None:
    """Small versus large follows from design flow, per Section 5.0.

    Section 5.0 scopes small systems to under 2500 gallons per day, so the scale
    can be derived rather than read. Derived only when the flow was actually read:
    guessing small because the flow is missing would silently enable every
    isolation rule on a packet nobody could measure.
    """
    flow = extraction.facts.get("design_flow")
    if flow is None:
        return None
    try:
        flow_value = float(flow)
    except (TypeError, ValueError):
        return None
    scale = "small" if flow_value < 2500 else "large"
    return Fact(
        name="system_scale",
        value=scale,
        source="derived",
        raw=f"design_flow={flow_value:g}",
        note=(
            "derived from design flow against the 2500 gallons per day boundary "
            "in Section 5.0"
        ),
    )


def _derive_flow_per_bedroom(extraction: Extraction) -> Fact | None:
    """Flow per bedroom, needed by FLOW-002.

    The regulation states 120 gallons per day per bedroom, and the packet states a
    total. Neither number alone answers the rule.
    """
    flow = extraction.facts.get("design_flow")
    bedrooms = extraction.facts.get("bedrooms")
    if flow is None or bedrooms in (None, 0):
        return None
    try:
        per_bedroom = float(flow) / float(bedrooms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return Fact(
        name="design_flow_per_bedroom",
        value=round(per_bedroom, 1),
        source="derived",
        raw=f"design_flow={flow} / bedrooms={bedrooms}",
        note="design flow divided by bedroom count",
    )


def extract_facts(document: Document) -> Extraction:
    """Read every known parameter off a document.

    Order matters: form fields are preferred over text patterns, because a form
    field pairs a label with a value while a text pattern only sees proximity.
    Derived facts run last, once their inputs are known.
    """
    extraction = Extraction()

    for name, spec in FACTS.items():
        fact = _from_form_fields(document, name, spec, extraction.rejected)
        if fact is None:
            fact = _from_text(document, name, spec, extraction.rejected)
        if fact is None:
            extraction.missing.append(name)
            continue
        extraction.facts[name] = fact.value
        extraction.provenance[name] = fact

    # Derived facts. Each one is skipped rather than defaulted when its inputs
    # are missing, so the engine reports UNKNOWN instead of comparing a guess.
    for deriver in (_infer_system_scale, _derive_flow_per_bedroom):
        fact = deriver(extraction)
        if fact is None:
            continue
        if fact.name in extraction.facts:
            continue  # something read off the page wins over a derivation
        extraction.facts[fact.name] = fact.value
        extraction.provenance[fact.name] = fact
        if fact.name in extraction.missing:
            extraction.missing.remove(fact.name)

    for name in DERIVED:
        if name not in extraction.facts and name not in extraction.missing:
            extraction.missing.append(name)

    return extraction


def parameter_help(name: str) -> str:
    """What a parameter means, for the report's missing information section."""
    spec = FACTS.get(name)
    if spec:
        return spec.get("help", name)
    if name == "design_flow_per_bedroom":
        return "design flow divided by bedroom count"
    return name
