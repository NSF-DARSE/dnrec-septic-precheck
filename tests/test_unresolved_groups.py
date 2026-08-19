"""Tests for grouped unresolved findings.

The console and the printable report must group unresolved findings identically,
by the blocked_by key from the composed payload. Neither surface computes or
infers grouping; both read the same unresolved_groups list.
"""
from pathlib import Path

import pytest

from septic.ingest import layout
from septic.ingest.extract import extract_facts
from septic.report import compose as compose_mod
from septic.report import render as render_mod
from septic.rules import engine
from septic.rules.schema import Citation, Operator, Rule, Severity

ROOT = Path(__file__).resolve().parent.parent


def _make_rule(rule_id, parameter, applies_to=None, **overrides):
    defaults = dict(
        id=rule_id, description="d",
        citation=Citation(section="TEST-0.0", page=1, quote="q"),
        parameter=parameter, operator=Operator.GE, threshold=100,
        units="feet", severity=Severity.RETURN, verified=True,
        remedy="r", notes="n",
    )
    if applies_to is not None:
        defaults["applies_to"] = applies_to
    defaults.update(overrides)
    return Rule(**defaults)


class TestUnresolvedGrouping:
    """Unresolved findings group by blocked_by rather than repeating per rule."""

    @pytest.fixture
    def grouped_payload(self):
        """A payload with multiple rules blocked by the same missing field."""
        rules = [
            _make_rule("GATE-001", "p_a", applies_to={"gate": "small"}),
            _make_rule("GATE-002", "p_b", applies_to={"gate": "small"}),
            _make_rule("GATE-003", "p_c", applies_to={"gate": "small"}),
            _make_rule("SOLO-001", "p_d"),
            _make_rule("SOLO-002", "p_e"),
        ]
        # gate is unknown so GATE-001..003 are undetermined. p_d and p_e are absent.
        facts = {}
        report = engine.evaluate(facts, rules)
        composed = compose_mod.compose(report)
        return composed.to_json()

    def test_unresolved_groups_are_present_in_payload(self, grouped_payload):
        groups = grouped_payload["unresolved_groups"]
        assert len(groups) > 0

    def test_groups_have_correct_structure(self, grouped_payload):
        groups = grouped_payload["unresolved_groups"]
        for group in groups:
            assert "blocked_by" in group
            assert "description" in group
            assert "count" in group
            assert "findings" in group
            assert group["count"] == len(group["findings"])

    def test_gate_blocked_rules_are_grouped_together(self, grouped_payload):
        groups = grouped_payload["unresolved_groups"]
        gate_group = next(g for g in groups if g["blocked_by"] == "gate")
        assert gate_group["count"] == 3
        rule_ids = [f["rule_id"] for f in gate_group["findings"]]
        assert set(rule_ids) == {"GATE-001", "GATE-002", "GATE-003"}

    def test_individually_blocked_rules_are_separate_groups(self, grouped_payload):
        groups = grouped_payload["unresolved_groups"]
        singles = [g for g in groups if g["count"] == 1]
        single_keys = {g["blocked_by"] for g in singles}
        assert "p_d" in single_keys
        assert "p_e" in single_keys

    def test_total_findings_across_groups_equals_unresolved_count(self, grouped_payload):
        groups = grouped_payload["unresolved_groups"]
        total = sum(g["count"] for g in groups)
        assert total == len(grouped_payload["unresolved"])

    def test_every_finding_has_blocked_by_in_payload(self, grouped_payload):
        for f in grouped_payload["unresolved"]:
            assert f["blocked_by"] is not None

    def test_console_and_report_use_same_groups(self, grouped_payload):
        """Both surfaces read unresolved_groups, so both produce the same order."""
        groups = grouped_payload["unresolved_groups"]
        # The HTML report contains each group's findings in order within the
        # unresolved section.
        html = render_mod.render_html(grouped_payload)
        # Find the unresolved section
        unresolved_start = html.find("Could not be evaluated")
        assert unresolved_start >= 0
        unresolved_html = html[unresolved_start:]
        # Verify the group keys appear in order
        positions = []
        for group in groups:
            for f in group["findings"]:
                pos = unresolved_html.find(f["rule_id"])
                assert pos >= 0, f"{f['rule_id']} not found in unresolved HTML"
                positions.append(pos)
        # Positions must be strictly increasing (same order as the payload)
        assert positions == sorted(positions), (
            "HTML renders groups in a different order than the payload"
        )

    def test_text_report_groups_by_cause(self, grouped_payload):
        """The text renderer also groups by cause."""
        text = render_mod.render_text(grouped_payload)
        # The group header for gate (3 rules) should appear once
        assert "3 checks could not run" in text
        # Individual rules should still be listed
        assert "GATE-001" in text
        assert "GATE-002" in text
        assert "GATE-003" in text

    def test_each_finding_citation_is_preserved_in_groups(self, grouped_payload):
        """Grouping does not lose the per-finding citation."""
        groups = grouped_payload["unresolved_groups"]
        for group in groups:
            for f in group["findings"]:
                assert f.get("citation"), (
                    f"finding {f['rule_id']} lost its citation in the group"
                )
                assert f.get("section")

    def test_app_source_reads_unresolved_groups(self):
        """The console reads unresolved_groups from the payload, not computing groups."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert 'payload.get("unresolved_groups")' in source
