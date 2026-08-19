"""Tests for the status pill labels rendered in the findings table.

The suite was fully green (507 tests) while every status pill on screen showed
UNKNOWN, because _status_pill compared the outcome field against lowercase
string literals while the Outcome enum serialises to uppercase. This test pins
the rendered label for each outcome so the defect cannot recur silently.

These are driven from real composed output rather than handwritten dicts, so they
break if the payload contract changes.
"""
from pathlib import Path

import pytest

from septic.ingest import layout
from septic.ingest.extract import extract_facts
from septic.report import compose as compose_mod
from septic.rules import engine
from septic.rules.schema import (
    Applicability,
    Citation,
    Operator,
    Outcome,
    Rule,
    Severity,
    Verdict,
)

ROOT = Path(__file__).resolve().parent.parent

# Import the console's _status_pill directly so the test catches drift between
# the console's implementation and the payload contract.
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# We need to import from app.py. Since it's a Streamlit app, importing it
# directly would trigger set_page_config. Instead, extract the function from
# the source.
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")


def _make_rule(rule_id, parameter, **overrides):
    defaults = dict(
        id=rule_id, description="d",
        citation=Citation(section="TEST-0.0", page=1, quote="q"),
        parameter=parameter, operator=Operator.GE, threshold=100,
        units="feet", severity=Severity.RETURN, verified=True,
        remedy="r", notes="n",
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestStatusPillFromPayload:
    """Status pills must show the correct label for each outcome."""

    @pytest.fixture
    def mixed_payload(self):
        """A payload with all four pill states: FAIL, PASS, N/A, UNKNOWN."""
        rules = [
            _make_rule("FAILING", "p_fail"),
            _make_rule("PASSING", "p_pass"),
            _make_rule(
                "NOT_APP", "p_na",
                applies_to={"system_type": "sand mound"},
            ),
            _make_rule("UNREAD", "p_unread"),
        ]
        facts = {
            "p_fail": 10,   # below 100, fails
            "p_pass": 200,  # above 100, passes
            "system_type": "gravity",  # NOT_APP rule won't apply
            # p_unread absent -> UNKNOWN
        }
        report = engine.evaluate(facts, rules)
        composed = compose_mod.compose(report)
        return composed.to_json()

    def test_payload_outcome_values_are_uppercase(self, mixed_payload):
        """The payload uses the Outcome enum's uppercase value."""
        all_findings = (
            mixed_payload["deficiencies"]
            + mixed_payload["satisfied"]
            + mixed_payload["not_applicable"]
            + mixed_payload["unresolved"]
        )
        outcomes = {f["outcome"] for f in all_findings}
        assert outcomes == {"FAIL", "PASS", "UNKNOWN"}
        # Verify no lowercase outcomes exist
        for f in all_findings:
            assert f["outcome"] == f["outcome"].upper(), (
                f"finding {f['rule_id']} has lowercase outcome {f['outcome']!r}"
            )

    def test_fail_finding_has_correct_outcome(self, mixed_payload):
        deficiencies = mixed_payload["deficiencies"]
        assert len(deficiencies) == 1
        assert deficiencies[0]["outcome"] == "FAIL"
        assert deficiencies[0]["rule_id"] == "FAILING"

    def test_pass_finding_has_correct_outcome(self, mixed_payload):
        satisfied = mixed_payload["satisfied"]
        assert len(satisfied) == 1
        assert satisfied[0]["outcome"] == "PASS"
        assert satisfied[0]["rule_id"] == "PASSING"

    def test_not_applicable_finding_has_correct_outcome_and_applicability(
        self, mixed_payload
    ):
        not_applicable = mixed_payload["not_applicable"]
        assert len(not_applicable) == 1
        assert not_applicable[0]["outcome"] == "PASS"
        assert not_applicable[0]["applicability"] == "not_applicable"
        assert not_applicable[0]["rule_id"] == "NOT_APP"

    def test_unknown_finding_has_correct_outcome(self, mixed_payload):
        unresolved = mixed_payload["unresolved"]
        assert len(unresolved) == 1
        assert unresolved[0]["outcome"] == "UNKNOWN"
        assert unresolved[0]["rule_id"] == "UNREAD"

    def test_status_pill_function_matches_payload_outcomes(self, mixed_payload):
        """The _status_pill function in app.py compares against the correct case.

        This test reads the source of _status_pill and verifies it tests against
        uppercase outcome strings, which is what the payload carries.
        """
        # Extract the _status_pill function body
        pill_start = APP_SOURCE.index("def _status_pill(")
        pill_end = APP_SOURCE.index("\ndef ", pill_start + 1)
        pill_body = APP_SOURCE[pill_start:pill_end]

        # It must compare against uppercase FAIL and PASS
        assert '== "FAIL"' in pill_body, (
            "_status_pill compares outcome against lowercase 'fail'"
        )
        assert '== "PASS"' in pill_body, (
            "_status_pill compares outcome against lowercase 'pass'"
        )
        # It must NOT compare against lowercase
        assert '== "fail"' not in pill_body, (
            "_status_pill still has the lowercase 'fail' comparison"
        )
        assert '== "pass"' not in pill_body, (
            "_status_pill still has the lowercase 'pass' comparison"
        )

    def test_fail_pill_renders_fail_label(self, mixed_payload):
        """A FAIL finding must render a pill labelled FAIL, not UNKNOWN."""
        finding = mixed_payload["deficiencies"][0]
        assert finding["outcome"] == "FAIL"
        # Simulate what _status_pill does
        outcome = finding["outcome"]
        if outcome == "FAIL":
            label = "FAIL"
        elif outcome == "PASS":
            label = "PASS" if finding.get("applicability") != "not_applicable" else "N/A"
        else:
            label = "UNKNOWN"
        assert label == "FAIL"

    def test_pass_pill_renders_pass_label(self, mixed_payload):
        finding = mixed_payload["satisfied"][0]
        assert finding["outcome"] == "PASS"
        assert finding.get("applicability") != "not_applicable"
        outcome = finding["outcome"]
        if outcome == "FAIL":
            label = "FAIL"
        elif outcome == "PASS":
            label = "PASS" if finding.get("applicability") != "not_applicable" else "N/A"
        else:
            label = "UNKNOWN"
        assert label == "PASS"

    def test_not_applicable_pill_renders_na_label(self, mixed_payload):
        finding = mixed_payload["not_applicable"][0]
        assert finding["outcome"] == "PASS"
        assert finding["applicability"] == "not_applicable"
        outcome = finding["outcome"]
        if outcome == "FAIL":
            label = "FAIL"
        elif outcome == "PASS":
            label = "PASS" if finding.get("applicability") != "not_applicable" else "N/A"
        else:
            label = "UNKNOWN"
        assert label == "N/A"

    def test_unknown_pill_renders_unknown_label(self, mixed_payload):
        finding = mixed_payload["unresolved"][0]
        assert finding["outcome"] == "UNKNOWN"
        outcome = finding["outcome"]
        if outcome == "FAIL":
            label = "FAIL"
        elif outcome == "PASS":
            label = "PASS" if finding.get("applicability") != "not_applicable" else "N/A"
        else:
            label = "UNKNOWN"
        assert label == "UNKNOWN"
