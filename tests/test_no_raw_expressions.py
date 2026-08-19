"""Test that no rendered surface shows raw machine expressions to a reviewer.

A reviewer should never see dist_disposal_to_well >= 100 feet on screen.
They should see Isolation distance from the disposal area to the nearest well
must be at least 100 feet. This test catches the defect pattern that has now
appeared twice: a surface falling back to the raw requirement field instead
of calling the shared requirement_sentence function.
"""
import re
from pathlib import Path

import pytest

from septic import config
from septic.ingest import layout
from septic.ingest.extract import extract_facts
from septic.ingest.textract import TextractClient, document_hash
from septic.report import compose as compose_mod
from septic.report import render as render_mod
from septic.report.wording import requirement_sentence
from septic.rules import engine
from septic.rules.schema import Citation, Operator, Rule, Severity

ROOT = Path(__file__).resolve().parent.parent

# The pattern that should never appear on a reviewer-facing surface:
# a parameter name (multiple lowercase words joined by underscores) followed by
# a comparison operator. This is the specific shape of the defect.
RAW_EXPRESSION_RE = re.compile(r"[a-z]+_[a-z_]+\s*(?:>=|<=|>|<|==|!=)\s*\d")


def _make_rule(rule_id, parameter, **overrides):
    defaults = dict(
        id=rule_id, description="d",
        citation=Citation(section="TEST", page=1, quote="q"),
        parameter=parameter, operator=Operator.GE, threshold=100,
        units="feet", severity=Severity.RETURN, verified=True,
        remedy="r",
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestNoRawExpressionsOnScreen:
    """No rendered surface may show a bare parameter name with an operator."""

    @pytest.fixture
    def payload_with_all_groups(self):
        """A payload exercising deficiencies, unresolved, satisfied, not_applicable."""
        rules = [
            _make_rule("FAIL-1", "dist_disposal_to_well"),
            _make_rule("PASS-1", "perc_rate", operator=Operator.LE,
                       threshold=120, units="minutes per inch"),
            _make_rule("UNREAD-1", "dist_tank_to_well", threshold=50),
            _make_rule("UNREAD-2", "dist_disposal_to_watercourse",
                       applies_to={"system_scale": "small"}),
            _make_rule("NA-1", "disposal_slope", operator=Operator.LE,
                       threshold=2, units="percent",
                       applies_to={"system_type": "sand mound"}),
        ]
        facts = {
            "dist_disposal_to_well": 10,
            "perc_rate": 80,
            "system_type": "gravity",
        }
        report = engine.evaluate(facts, rules)
        return compose_mod.compose(report).to_json()

    def test_html_report_has_no_raw_expressions(self, payload_with_all_groups):
        html = render_mod.render_html(payload_with_all_groups)
        matches = RAW_EXPRESSION_RE.findall(html)
        assert not matches, (
            f"HTML report contains raw machine expressions: {matches}"
        )

    def test_text_report_has_no_raw_expressions(self, payload_with_all_groups):
        text = render_mod.render_text(payload_with_all_groups)
        matches = RAW_EXPRESSION_RE.findall(text)
        assert not matches, (
            f"text report contains raw machine expressions: {matches}"
        )

    def test_requirement_sentence_never_returns_raw_expression(self):
        """The shared function must always produce a readable sentence."""
        cases = [
            {"parameter": "dist_disposal_to_well", "threshold": 100,
             "units": "feet", "requirement": "dist_disposal_to_well >= 100 feet"},
            {"parameter": "perc_rate", "threshold": 120,
             "units": "minutes per inch", "requirement": "perc_rate <= 120 minutes per inch"},
            {"parameter": "site_evaluation_report", "threshold": None,
             "units": None, "requirement": "site_evaluation_report must be provided"},
        ]
        for case in cases:
            sentence = requirement_sentence(case)
            assert not RAW_EXPRESSION_RE.search(sentence), (
                f"requirement_sentence returned raw expression: {sentence}"
            )
            assert "_" not in sentence.split()[0], (
                f"sentence starts with an underscore name: {sentence}"
            )

    def test_real_packet_has_no_raw_expressions(self):
        """The shipped rules on a real cached packet produce no raw expressions."""
        pdf = config.OUT_DIR / "examples" / "permit_282133_60843649.pdf"
        if not pdf.exists():
            pytest.skip("no cached example")
        client = TextractClient()
        analysis = client.cached_by_hash(document_hash(pdf.read_bytes()))
        if analysis is None:
            pytest.skip("no cache for 282133")
        document = layout.parse_blocks(analysis.blocks)
        extraction = extract_facts(document)
        report = engine.evaluate(extraction.facts)
        composed = compose_mod.compose(report, extraction=extraction)
        payload = composed.to_json()
        html = render_mod.render_html(payload)
        text = render_mod.render_text(payload)
        for name, surface in [("HTML", html), ("text", text)]:
            matches = RAW_EXPRESSION_RE.findall(surface)
            assert not matches, (
                f"{name} report for permit 282133 contains raw expressions: "
                f"{matches}"
            )
