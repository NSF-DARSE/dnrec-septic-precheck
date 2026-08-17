"""Tests for the rule engine.

Every rule here is synthetic and cites nothing real. Thresholds are invented for
the purpose of exercising comparison logic, which is why no test asserts anything
about the actual regulation. Real thresholds only enter rules_7101.yaml after a
person verifies them against the PDF.
"""
import pytest

from septic.rules.engine import (
    coerce_number,
    decide,
    evaluate,
    evaluate_rule,
    load_rules,
)
from septic.rules.schema import (
    Citation,
    Operator,
    Outcome,
    Rule,
    Severity,
    Verdict,
)

CITATION = Citation(section="TEST-0.0", page=1, quote="synthetic, not a real requirement")


def rule(**kwargs) -> Rule:
    defaults = dict(
        id="T001",
        description="synthetic test rule",
        citation=CITATION,
        parameter="widgets",
        operator=Operator.GE,
        threshold=10,
        units="widgets",
        severity=Severity.RETURN,
        verified=True,
    )
    defaults.update(kwargs)
    return Rule(**defaults)


class TestVerifiedGate:
    def test_unverified_rule_is_unknown_even_when_it_would_pass(self):
        r = rule(verified=False, threshold=1)
        ev = evaluate_rule(r, {"widgets": 999})
        assert ev.outcome is Outcome.UNKNOWN
        assert "not been verified" in ev.reason

    def test_unverified_rule_is_unknown_even_when_it_would_fail(self):
        r = rule(verified=False, threshold=1000)
        ev = evaluate_rule(r, {"widgets": 1})
        assert ev.outcome is Outcome.UNKNOWN

    def test_unverified_rule_is_never_a_return_reason(self):
        r = rule(verified=False, threshold=1000)
        assert not evaluate_rule(r, {"widgets": 1}).is_return_reason


class TestNumericComparisons:
    @pytest.mark.parametrize(
        "operator,threshold,value,expected",
        [
            (Operator.GE, 10, 10, Outcome.PASS),
            (Operator.GE, 10, 9, Outcome.FAIL),
            (Operator.LE, 10, 10, Outcome.PASS),
            (Operator.LE, 10, 11, Outcome.FAIL),
            (Operator.GT, 10, 11, Outcome.PASS),
            (Operator.GT, 10, 10, Outcome.FAIL),
            (Operator.LT, 10, 9, Outcome.PASS),
            (Operator.LT, 10, 10, Outcome.FAIL),
        ],
    )
    def test_boundaries(self, operator, threshold, value, expected):
        ev = evaluate_rule(rule(operator=operator, threshold=threshold),
                           {"widgets": value})
        assert ev.outcome is expected

    def test_units_appear_in_the_reason(self):
        ev = evaluate_rule(rule(threshold=10, units="feet"), {"widgets": 4})
        assert "feet" in ev.reason

    def test_unreadable_value_is_unknown_not_fail(self):
        ev = evaluate_rule(rule(), {"widgets": "illegible"})
        assert ev.outcome is Outcome.UNKNOWN

    def test_missing_value_is_unknown_not_fail(self):
        ev = evaluate_rule(rule(), {})
        assert ev.outcome is Outcome.UNKNOWN
        assert "could not be read" in ev.reason


class TestPresenceOperators:
    def test_present_passes_when_supplied(self):
        r = rule(operator=Operator.PRESENT, threshold=None)
        assert evaluate_rule(r, {"widgets": "yes"}).outcome is Outcome.PASS

    def test_present_fails_when_missing(self):
        r = rule(operator=Operator.PRESENT, threshold=None)
        assert evaluate_rule(r, {}).outcome is Outcome.FAIL

    def test_present_fails_on_empty_string(self):
        r = rule(operator=Operator.PRESENT, threshold=None)
        assert evaluate_rule(r, {"widgets": ""}).outcome is Outcome.FAIL

    def test_absent_inverts(self):
        r = rule(operator=Operator.ABSENT, threshold=None)
        assert evaluate_rule(r, {}).outcome is Outcome.PASS
        assert evaluate_rule(r, {"widgets": "x"}).outcome is Outcome.FAIL


class TestMembershipAndEquality:
    def test_in_list(self):
        r = rule(operator=Operator.IN, threshold=["gravity", "mound"])
        assert evaluate_rule(r, {"widgets": "Gravity"}).outcome is Outcome.PASS
        assert evaluate_rule(r, {"widgets": "drip"}).outcome is Outcome.FAIL

    def test_not_in_list(self):
        r = rule(operator=Operator.NOT_IN, threshold=["banned"])
        assert evaluate_rule(r, {"widgets": "allowed"}).outcome is Outcome.PASS

    def test_in_with_non_list_threshold_is_unknown(self):
        r = rule(operator=Operator.IN, threshold="gravity")
        assert evaluate_rule(r, {"widgets": "gravity"}).outcome is Outcome.UNKNOWN

    def test_equality_is_case_insensitive(self):
        r = rule(operator=Operator.EQ, threshold="Gravity")
        assert evaluate_rule(r, {"widgets": "gravity"}).outcome is Outcome.PASS


class TestApplicability:
    def test_rule_not_applicable_passes(self):
        r = rule(applies_to={"system_type": "mound"})
        ev = evaluate_rule(r, {"system_type": "gravity", "widgets": 0})
        assert ev.outcome is Outcome.PASS
        assert "does not apply" in ev.reason

    def test_applicable_rule_still_evaluates(self):
        r = rule(applies_to={"system_type": "gravity"})
        assert evaluate_rule(r, {"system_type": "gravity", "widgets": 0}).outcome is Outcome.FAIL

    def test_unknown_gating_fact_is_unknown_not_pass(self):
        r = rule(applies_to={"system_type": "gravity"})
        ev = evaluate_rule(r, {"widgets": 0})
        assert ev.outcome is Outcome.UNKNOWN
        assert "cannot tell whether this rule applies" in ev.reason

    def test_applies_to_accepts_a_list(self):
        r = rule(applies_to={"system_type": ["gravity", "mound"]})
        assert evaluate_rule(r, {"system_type": "mound", "widgets": 99}).outcome is Outcome.PASS


class TestCoerceNumber:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (10, 10.0),
            ("10", 10.0),
            ("1,250 gallons", 1250.0),
            ("min. 5 ft", 5.0),
            ("0.5", 0.5),
            ("36 inches", 36.0),
        ],
    )
    def test_reads_numbers(self, raw, expected):
        assert coerce_number(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["", None, "illegible", "n/a", "none", "13-014.00-039", "not provided"]
    )
    def test_rejects_non_numbers(self, raw):
        assert coerce_number(raw) is None


class TestVerdict:
    def test_failed_return_rule_gives_likely_return(self):
        evs = [evaluate_rule(rule(threshold=100), {"widgets": 1})]
        assert decide(evs) is Verdict.LIKELY_RETURN

    def test_unknown_gives_cannot_verify(self):
        evs = [evaluate_rule(rule(verified=False), {"widgets": 1})]
        assert decide(evs) is Verdict.CANNOT_VERIFY

    def test_all_pass_gives_ready_to_submit(self):
        evs = [evaluate_rule(rule(threshold=1), {"widgets": 5})]
        assert decide(evs) is Verdict.READY_TO_SUBMIT

    def test_return_outranks_unknown(self):
        evs = [
            evaluate_rule(rule(id="A", threshold=100), {"widgets": 1}),
            evaluate_rule(rule(id="B", verified=False), {"widgets": 1}),
        ]
        assert decide(evs) is Verdict.LIKELY_RETURN

    def test_advisory_failure_does_not_force_return(self):
        evs = [
            evaluate_rule(
                rule(threshold=100, severity=Severity.ADVISORY), {"widgets": 1}
            )
        ]
        assert decide(evs) is Verdict.CANNOT_VERIFY or decide(evs) is Verdict.READY_TO_SUBMIT
        assert not evs[0].is_return_reason

    def test_no_rules_gives_cannot_verify(self):
        assert decide([]) is Verdict.CANNOT_VERIFY

    def test_evaluation_is_deterministic(self):
        facts = {"widgets": 4, "system_type": "gravity"}
        rules = [rule(id="A", threshold=10), rule(id="B", threshold=1)]
        first = evaluate(facts, rules)
        second = evaluate(facts, rules)
        assert first.verdict is second.verdict
        assert first.counts() == second.counts()


class TestSchemaValidation:
    def test_numeric_rule_without_threshold_is_rejected(self):
        with pytest.raises(ValueError):
            rule(operator=Operator.GE, threshold=None)

    def test_missing_citation_section_is_rejected(self):
        with pytest.raises(ValueError):
            rule(citation=Citation(section=""))

    def test_unknown_key_is_rejected(self):
        with pytest.raises(ValueError):
            Rule.from_dict(
                {
                    "id": "X",
                    "description": "d",
                    "citation": {"section": "1.0"},
                    "parameter": "p",
                    "operator": "present",
                    "typo_field": 1,
                }
            )


class TestShippedRuleSet:
    def test_every_shipped_rule_is_unverified(self):
        rules = load_rules()
        assert rules, "expected seed rules to load"
        assert all(not r.verified for r in rules)

    def test_shipped_rules_carry_no_numeric_threshold(self):
        # Guards the constraint that no unverified regulatory number is shipped.
        for r in load_rules():
            assert r.threshold is None, f"{r.id} ships a threshold value"

    def test_shipped_rule_set_yields_cannot_verify(self):
        report = evaluate({"site_plan": "yes", "perc_rate": 30, "lot_area": 20000})
        assert report.verdict is Verdict.CANNOT_VERIFY
        assert len(report.unknowns) == len(report.evaluations)
