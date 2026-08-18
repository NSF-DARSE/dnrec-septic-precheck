"""Rule definitions.

A rule is a single checkable requirement traced to one passage of the
regulation. Every rule carries a citation because a reviewer has to be able to
verify the threshold against the source document, and because telling an
applicant a number without saying where it comes from is not useful.

The verified flag is the safety interlock. A threshold that no human has checked
against the regulation PDF cannot produce a PASS or a FAIL. The engine returns
UNKNOWN for it. Presenting a wrong regulatory number to permitting staff is a
worse failure than reporting nothing, so the default is verified: false and it
has to be turned on deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Operator(str, Enum):
    """Comparison a rule applies to an extracted value."""

    GE = ">="
    LE = "<="
    GT = ">"
    LT = "<"
    EQ = "=="
    NE = "!="
    IN = "in"
    NOT_IN = "not_in"
    PRESENT = "present"
    ABSENT = "absent"

    @property
    def needs_threshold(self) -> bool:
        return self not in (Operator.PRESENT, Operator.ABSENT)

    @property
    def is_numeric(self) -> bool:
        return self in (Operator.GE, Operator.LE, Operator.GT, Operator.LT)


class Severity(str, Enum):
    """What a failure means for the application."""

    RETURN = "return"      # would get the application returned for correction
    ADVISORY = "advisory"  # worth fixing, not by itself a return reason


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class Applicability(str, Enum):
    """Whether a rule's applies_to conditions were met by the facts.

    This is deliberately not a fourth Outcome. The three valued outcome
    vocabulary is the product claim and every surface is built on it. What this
    records is a different question, asked before the comparison happens: was
    this rule ever applied to this packet at all.

    It has to be carried on the Evaluation rather than recovered afterwards. A
    rule that does not apply still reports PASS, so without this field the only
    way to tell "the trench slope is under 25 percent" from "there is no trench"
    is to match on the reason text, and a reporting layer that reads prose to
    decide what a number means is the kind of fragility this project has already
    been bitten by.

        APPLIES        the rule was applied and a value was compared
        NOT_APPLICABLE the packet is not the kind of system this rule governs
        UNDETERMINED   the fact that gates the rule could not be read, so
                       whether the rule applies was never established
    """

    APPLIES = "applies"
    NOT_APPLICABLE = "not_applicable"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class Citation:
    """Where in the regulation a rule comes from."""

    section: str
    page: int | None = None
    quote: str | None = None
    document: str = "Delaware Regulations Governing On-Site Wastewater Treatment and Disposal Systems (January 11, 2014)"

    def short(self) -> str:
        return f"{self.section}" + (f", p.{self.page}" if self.page else "")

    def to_json(self) -> dict:
        return {
            "section": self.section,
            "page": self.page,
            "quote": self.quote,
            "document": self.document,
        }


@dataclass
class Rule:
    """One checkable requirement.

    parameter names a fact the extractor produces. threshold is the value from
    the regulation. applies_to narrows the rule to a subset of applications, as
    equality tests against facts; an empty mapping means the rule always applies.
    """

    id: str
    description: str
    citation: Citation
    parameter: str
    operator: Operator
    threshold: Any = None
    units: str | None = None
    severity: Severity = Severity.RETURN
    applies_to: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    remedy: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.operator, str):
            self.operator = Operator(self.operator)
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity)
        if not self.id:
            raise ValueError("rule id is required")
        if not self.citation.section:
            raise ValueError(f"rule {self.id} has no citation section")
        if self.operator.needs_threshold and self.threshold is None:
            raise ValueError(
                f"rule {self.id} uses {self.operator.value} but has no threshold"
            )

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "citation": self.citation.to_json(),
            "parameter": self.parameter,
            "operator": self.operator.value,
            "threshold": self.threshold,
            "units": self.units,
            "severity": self.severity.value,
            "applies_to": self.applies_to,
            "verified": self.verified,
            "remedy": self.remedy,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Rule":
        data = dict(payload)
        citation_raw = data.pop("citation", None)
        if citation_raw is None:
            raise ValueError(f"rule {data.get('id')} has no citation")
        if isinstance(citation_raw, str):
            citation = Citation(section=citation_raw)
        else:
            citation = Citation(
                section=str(citation_raw.get("section", "")),
                page=citation_raw.get("page"),
                quote=citation_raw.get("quote"),
                document=citation_raw.get(
                    "document", Citation.__dataclass_fields__["document"].default
                ),
            )
        allowed = {
            "id", "description", "parameter", "operator", "threshold", "units",
            "severity", "applies_to", "verified", "remedy", "notes",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"rule {data.get('id')} has unrecognised keys: {sorted(unknown)}"
            )
        return cls(citation=citation, **data)


@dataclass
class Evaluation:
    """Result of applying one rule to one application.

    applicability records whether the rule was applied at all, and
    applicability_parameter names the fact that settled it, so a report can say
    which value took the rule out of scope and where that value was read from.
    Both default to "applied normally", which is what every comparison path
    produces.
    """

    rule: Rule
    outcome: Outcome
    reason: str
    observed: Any = None
    applicability: Applicability = Applicability.APPLIES
    applicability_parameter: str | None = None

    @property
    def is_return_reason(self) -> bool:
        return self.outcome is Outcome.FAIL and self.rule.severity is Severity.RETURN

    @property
    def is_not_applicable(self) -> bool:
        """A PASS that compared nothing, because the rule does not govern this system."""
        return self.applicability is Applicability.NOT_APPLICABLE

    @property
    def compared_a_value(self) -> bool:
        """The rule ran: a value off the packet was compared against a threshold.

        This is the number a reviewer reads as coverage. It is not the same as
        "did not come back UNKNOWN", which is what coverage used to count and why
        37 percent of the passes in the corpus were checks that never ran.
        """
        return (
            self.outcome in (Outcome.PASS, Outcome.FAIL)
            and not self.is_not_applicable
        )

    def to_json(self) -> dict:
        return {
            "rule_id": self.rule.id,
            "outcome": self.outcome.value,
            "applicability": self.applicability.value,
            "applicability_parameter": self.applicability_parameter,
            "reason": self.reason,
            "observed": self.observed,
            "threshold": self.rule.threshold,
            "units": self.rule.units,
            "severity": self.rule.severity.value,
            "citation": self.rule.citation.short(),
            "quote": self.rule.citation.quote,
            "description": self.rule.description,
            "remedy": self.rule.remedy,
            "verified": self.rule.verified,
        }


class Verdict(str, Enum):
    """Overall result, answering only "is anything wrong with this packet".

    How much of the packet could be checked is a second and separate question,
    carried alongside the verdict as coverage rather than folded into it. Six of
    the isolation distances live on a scanned drawing that cannot be measured, so
    a verdict that degraded whenever any check could not run would read CANNOT
    VERIFY on every real packet forever and tell a reviewer nothing.

    The earlier names were READY TO SUBMIT and LIKELY RETURN. Both were wrong for
    this audience. The reviewer is not submitting anything, and predicting that
    DNREC will return an application is a claim this tool does not make.
    """

    NO_DEFICIENCIES = "NO DEFICIENCIES FOUND"
    DEFICIENCIES_FOUND = "DEFICIENCIES FOUND"
    CANNOT_VERIFY = "CANNOT VERIFY"
