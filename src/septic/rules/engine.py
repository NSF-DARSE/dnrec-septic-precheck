"""Rule evaluation.

The verdict is computed here and only here. Retrieval supplies context for the
written report, and a language model supplies wording, but neither decides the
outcome. Given the same facts and the same rule set this function returns the
same verdict every time.

Three outcomes per rule rather than two. UNKNOWN covers an unverified threshold,
a fact the extractor could not read, and a value that will not parse as a number.
All three are real situations in scanned documents, and collapsing them into a
PASS would hide a problem while collapsing them into a FAIL would invent one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import re

from .schema import Evaluation, Outcome, Rule, Severity, Verdict

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

RULES_FILE = Path(__file__).resolve().parent / "rules_7101.yaml"

NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+")
# Parcel ids and similar codes contain digits but have no single numeric value.
PARCEL_LIKE_RE = re.compile(r"\d+[-.]\d+[-.]\d+")

NUMERIC_FALSE = {
    "", "-", "--", "no", "n", "false", "absent", "none", "not provided",
    "n/a", "na", "unknown",
}


def load_rules(path: Path | None = None) -> list[Rule]:
    """Read rules from YAML.

    Raises on a malformed file rather than skipping entries, because a rule that
    silently fails to load is a check that silently stops running.
    """
    path = Path(path or RULES_FILE)
    if yaml is None:
        raise ImportError("PyYAML is required to load rules")
    if not path.exists():
        return []

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = payload.get("rules", [])
    rules = [Rule.from_dict(entry) for entry in entries]

    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise ValueError(f"duplicate rule id: {rule.id}")
        seen.add(rule.id)
    return rules


def coerce_number(value: Any) -> float | None:
    """Best effort numeric read of an OCR value, or None.

    Takes the first signed decimal in the string, so "1,250 gallons" reads as
    1250 and "min. 5 ft" reads as 5. Returns None rather than guessing when
    nothing numeric is present, which becomes an UNKNOWN outcome upstream. A
    parcel id like "13-014.00-039" has no single numeric reading and is rejected.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if text in NUMERIC_FALSE:
        return None

    compact = text.replace(",", "")
    if PARCEL_LIKE_RE.search(compact):
        return None

    match = NUMBER_RE.search(compact)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


class Applicability(str, Enum):
    APPLIES = "applies"
    NOT_APPLICABLE = "not_applicable"
    UNDETERMINED = "undetermined"


def _applies(rule: Rule, facts: dict[str, Any]) -> tuple[Applicability, str]:
    """Whether a rule's applies_to conditions are satisfied by the facts.

    Undetermined is separate from not applicable: if the fact that gates the rule
    is unreadable, we cannot claim the rule passed.
    """
    for key, expected in rule.applies_to.items():
        if key not in facts or facts[key] in (None, ""):
            return (
                Applicability.UNDETERMINED,
                f"cannot tell whether this rule applies because {key} is unknown",
            )
        actual = facts[key]
        if isinstance(expected, (list, tuple, set)):
            match = str(actual).strip().lower() in {
                str(e).strip().lower() for e in expected
            }
        else:
            match = str(actual).strip().lower() == str(expected).strip().lower()
        if not match:
            return (
                Applicability.NOT_APPLICABLE,
                f"does not apply because {key} is {actual!r}",
            )
    return Applicability.APPLIES, ""


def evaluate_rule(rule: Rule, facts: dict[str, Any]) -> Evaluation:
    """Apply one rule to a fact mapping."""
    if not rule.verified:
        return Evaluation(
            rule=rule,
            outcome=Outcome.UNKNOWN,
            reason=(
                "threshold has not been verified against the regulation, so this "
                "check is not evaluated"
            ),
        )

    applicability, why = _applies(rule, facts)
    if applicability is Applicability.UNDETERMINED:
        return Evaluation(rule=rule, outcome=Outcome.UNKNOWN, reason=why)
    if applicability is Applicability.NOT_APPLICABLE:
        return Evaluation(rule=rule, outcome=Outcome.PASS, reason=why)

    op = rule.operator
    present = rule.parameter in facts and facts[rule.parameter] not in (None, "")
    observed = facts.get(rule.parameter)

    if op.value == "present":
        if present:
            return Evaluation(
                rule, Outcome.PASS, f"{rule.parameter} is provided", observed
            )
        # Absence of evidence is not evidence of absence. If the extractor never
        # produced this parameter at all, nobody has established whether the
        # packet contains it, and on a scanned site plan that is the usual case:
        # the item may well be drawn and simply unreadable. Reporting FAIL there
        # tells a reviewer the item is missing when the truth is that it could
        # not be read, which is the one mistake this tool cannot afford.
        #
        # FAIL is reserved for a positive finding of absence: the extractor
        # produced the field and it came back empty.
        if rule.parameter not in facts:
            return Evaluation(
                rule,
                Outcome.UNKNOWN,
                f"{rule.parameter} could not be read from the application, so "
                f"whether it is present was not established",
            )
        return Evaluation(rule, Outcome.FAIL, f"{rule.parameter} is missing", None)
    if op.value == "absent":
        return (
            Evaluation(rule, Outcome.FAIL, f"{rule.parameter} is present", observed)
            if present
            else Evaluation(rule, Outcome.PASS, f"{rule.parameter} is absent", None)
        )

    if not present:
        return Evaluation(
            rule=rule,
            outcome=Outcome.UNKNOWN,
            reason=f"{rule.parameter} could not be read from the application",
        )

    if op.is_numeric:
        left = coerce_number(observed)
        right = coerce_number(rule.threshold)
        if left is None:
            return Evaluation(
                rule, Outcome.UNKNOWN,
                f"{rule.parameter} value {observed!r} is not a number", observed,
            )
        if right is None:
            return Evaluation(
                rule, Outcome.UNKNOWN,
                f"threshold {rule.threshold!r} is not a number", observed,
            )
        comparisons = {
            ">=": left >= right,
            "<=": left <= right,
            ">": left > right,
            "<": left < right,
        }
        ok = comparisons[op.value]
        units = f" {rule.units}" if rule.units else ""
        detail = f"{left:g}{units} {op.value} {right:g}{units}"
        return Evaluation(
            rule,
            Outcome.PASS if ok else Outcome.FAIL,
            detail if ok else f"requires {op.value} {right:g}{units}, found {left:g}{units}",
            observed,
        )

    if op.value in ("==", "!="):
        equal = str(observed).strip().lower() == str(rule.threshold).strip().lower()
        ok = equal if op.value == "==" else not equal
        return Evaluation(
            rule,
            Outcome.PASS if ok else Outcome.FAIL,
            f"{rule.parameter} is {observed!r}, expected {op.value} {rule.threshold!r}",
            observed,
        )

    if op.value in ("in", "not_in"):
        allowed = rule.threshold
        if not isinstance(allowed, (list, tuple, set)):
            return Evaluation(
                rule, Outcome.UNKNOWN,
                f"threshold for {op.value} must be a list, got {type(allowed).__name__}",
                observed,
            )
        member = str(observed).strip().lower() in {
            str(a).strip().lower() for a in allowed
        }
        ok = member if op.value == "in" else not member
        return Evaluation(
            rule,
            Outcome.PASS if ok else Outcome.FAIL,
            f"{rule.parameter} is {observed!r}, expected {op.value} {list(allowed)!r}",
            observed,
        )

    return Evaluation(rule, Outcome.UNKNOWN, f"operator {op.value} is not implemented")


@dataclass
class Report:
    """Rule results for one application, plus the derived verdict."""

    verdict: Verdict
    evaluations: list[Evaluation] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> list[Evaluation]:
        return [e for e in self.evaluations if e.outcome is Outcome.FAIL]

    @property
    def return_reasons(self) -> list[Evaluation]:
        return [e for e in self.evaluations if e.is_return_reason]

    @property
    def unknowns(self) -> list[Evaluation]:
        return [e for e in self.evaluations if e.outcome is Outcome.UNKNOWN]

    @property
    def passes(self) -> list[Evaluation]:
        return [e for e in self.evaluations if e.outcome is Outcome.PASS]

    def counts(self) -> dict[str, int]:
        return {
            "pass": len(self.passes),
            "fail": len(self.failures),
            "unknown": len(self.unknowns),
            "return_reasons": len(self.return_reasons),
        }

    def coverage(self) -> dict[str, Any]:
        """How much of the rule set actually reached a decision.

        The verdict alone is not readable without this. NO DEFICIENCIES FOUND on
        7 of 15 checks and NO DEFICIENCIES FOUND on 15 of 15 are very different
        statements, and the difference is invisible unless this number is carried
        next to the headline. text is the phrasing every surface uses, so the
        console banner, the HTML report and the text report cannot word it
        differently.
        """
        evaluated = len(self.passes) + len(self.failures)
        total = len(self.evaluations)
        return {
            "evaluated": evaluated,
            "total": total,
            "not_evaluated": total - evaluated,
            "text": f"{evaluated} of {total} checks ran",
        }

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "counts": self.counts(),
            "coverage": self.coverage(),
            "evaluations": [e.to_json() for e in self.evaluations],
            "facts": self.facts,
        }


def decide(evaluations: list[Evaluation]) -> Verdict:
    """Map rule results to the three valued verdict.

    The verdict answers one question: is anything wrong with this packet. It does
    not answer how much of the packet could be checked, which is reported
    separately as coverage and has to be read next to the verdict for either to
    mean anything.

        DEFICIENCIES FOUND     at least one rule failed
        NO DEFICIENCIES FOUND  nothing failed and at least one rule was evaluated
        CANNOT VERIFY          no rule reached a decision at all

    An unevaluated check is still never counted as a pass. It is counted nowhere:
    it lowers coverage, and coverage travels with the verdict everywhere the
    verdict is shown.

    This used to degrade to CANNOT VERIFY on any single UNKNOWN. Six of the
    isolation distances are measurements on a scanned drawing that Textract
    cannot take, so in practice every real packet returned CANNOT VERIFY whether
    or not anything was wrong with it, and the one thing a reviewer most needs to
    see, an actual failed requirement, was reported with the same headline as a
    packet nobody could read. Any FAIL now surfaces, including an advisory one,
    because a report that itemises a deficiency under the headline NO
    DEFICIENCIES FOUND contradicts itself. Severity still orders the findings and
    is still reported per item.
    """
    if any(e.outcome is Outcome.FAIL for e in evaluations):
        return Verdict.DEFICIENCIES_FOUND
    if any(e.outcome is Outcome.PASS for e in evaluations):
        return Verdict.NO_DEFICIENCIES
    return Verdict.CANNOT_VERIFY


def evaluate(facts: dict[str, Any], rules: list[Rule] | None = None,
             path: Path | None = None) -> Report:
    """Evaluate every rule against the facts and derive the verdict."""
    rules = rules if rules is not None else load_rules(path)
    evaluations = [evaluate_rule(rule, facts) for rule in rules]
    return Report(
        verdict=decide(evaluations), evaluations=evaluations, facts=dict(facts)
    )
