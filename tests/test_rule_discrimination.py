"""Tests for the CSV to rule parameter mapping.

This mapping is the seam where a silent mistake poisons every number the
discrimination harness prints. A wrong mapping looks exactly like a working one:
the rules still run, the counts still add up, and the answer is meaningless. So
the hazards in the real export are pinned here.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rule_discrimination as rd  # noqa: E402


class TestFlowParsing:
    """flowRate uses a period as a thousands separator in some records.

    16 records in the export are written like "2.475", meaning 2,475 gallons per
    day. Read literally that is 2.475 gallons per day, which would fail the 240
    gallon residential minimum that the system actually clears by a factor of ten,
    and would also classify a 2475 gallon system as small when it sits right at
    the 2500 gallon boundary in Section 5.0.
    """

    @pytest.mark.parametrize("raw,expected", [
        ("480", 480.0),
        ("1.080", 1080.0),
        ("2.475", 2475.0),
        ("1,080", 1080.0),
        ("240", 240.0),
        ("1.5", 1.5),
    ])
    def test_parses_flow(self, raw, expected):
        assert rd.parse_flow(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "nan", "NaN", "not a number"])
    def test_unparsable_flow_is_none(self, raw):
        assert rd.parse_flow(raw) is None

    def test_thousands_separator_only_with_three_digits(self):
        """1.5 is one and a half, 1.500 is fifteen hundred."""
        assert rd.parse_flow("1.5") == 1.5
        assert rd.parse_flow("1.500") == 1500.0


class TestBedroomAndUse:
    """propUse encodes the bedroom count rather than the use."""

    def test_bedroom_count_and_residential_use(self):
        facts, _ = rd.facts_from_csv_row({"propUse": "3-bedroom", "flowRate": "360"})
        assert facts["bedrooms"] == 3
        assert facts["use_type"] == "residential"

    def test_flow_per_bedroom_is_derived(self):
        facts, _ = rd.facts_from_csv_row({"propUse": "4-bedroom", "flowRate": "480"})
        assert facts["design_flow_per_bedroom"] == 120.0

    def test_other_use_is_left_absent_not_guessed(self):
        """Mapping Other to residential would fire residential flow rules on it."""
        facts, context = rd.facts_from_csv_row({"propUse": "Other", "flowRate": "480"})
        assert "use_type" not in facts
        assert "bedrooms" not in facts
        assert context.get("use_type_unmapped") == "Other"


class TestSystemTypeMapping:
    @pytest.mark.parametrize("raw,expected", [
        ("Gravity", "gravity"),
        ("Low Pressure Pipe", "low pressure pipe"),
        ("Elevated Mound", "sand mound"),
        ("Alternative Elevated Sand Mound", "sand mound"),
        ("Pressure Dose", "pressure dosed"),
    ])
    def test_maps_display_names(self, raw, expected):
        facts, _ = rd.facts_from_csv_row({"septicSystemType": raw})
        assert facts["system_type"] == expected

    def test_unmapped_type_is_recorded_not_guessed(self):
        facts, context = rd.facts_from_csv_row({"septicSystemType": "Spaceship"})
        assert "system_type" not in facts
        assert context.get("system_type_unmapped") == "spaceship"


class TestScaleDerivation:
    """Section 5.0 puts the small system boundary at 2500 gallons per day."""

    @pytest.mark.parametrize("flow,expected", [
        ("480", "small"),
        ("2.475", "small"),
        ("2500", "large"),
        ("3000", "large"),
    ])
    def test_scale_follows_flow(self, flow, expected):
        facts, _ = rd.facts_from_csv_row({"flowRate": flow})
        assert facts["system_scale"] == expected

    def test_no_flow_means_no_scale(self):
        """Guessing small would switch on every isolation rule."""
        facts, _ = rd.facts_from_csv_row({"perkRate": "45"})
        assert "system_scale" not in facts


class TestAbsentIsNotDefaulted:
    def test_empty_row_produces_no_facts(self):
        facts, _ = rd.facts_from_csv_row({})
        assert facts == {}

    def test_nan_values_are_absent(self):
        facts, _ = rd.facts_from_csv_row({
            "perkRate": "nan", "flowRate": "nan", "propUse": "nan",
            "septicSystemType": "nan",
        })
        assert facts == {}

    def test_parameters_not_in_csv_are_declared(self):
        """The harness must know which rules it cannot test.

        Anything listed here is a rule the CSV can never evaluate, and the report
        separates those from rules that failed for a real reason.
        """
        facts, _ = rd.facts_from_csv_row({
            "perkRate": "45", "flowRate": "480", "propUse": "4-bedroom",
            "septicSystemType": "Gravity", "constructionType": "New Construction",
            "county": "Kent", "taxParcel": "1-2-3",
        })
        for parameter in rd.NOT_IN_CSV:
            assert parameter not in facts, (
                f"{parameter} is declared as absent from the CSV but was mapped"
            )


class TestReplacementDetection:
    """Section 5.2.4.2.4.2 exempts replacements from the 20 inch rule."""

    @pytest.mark.parametrize("construction,expected", [
        ("New Construction", False),
        ("Replacement", True),
        ("Component Replacement", True),
        ("Upgrade", True),
        ("Repair to Existing System", True),
        ("Authorization to Connect", False),
    ])
    def test_flags_replacements(self, construction, expected):
        _, context = rd.facts_from_csv_row({"constructionType": construction})
        assert context["is_replacement"] is expected


class TestEvaluableRules:
    def test_splits_testable_from_untestable(self):
        from septic.rules.engine import load_rules

        testable, untestable = rd.evaluable_rules(load_rules())
        assert testable, "expected some rules the CSV can test"
        assert untestable, "expected some rules the CSV cannot test"
        for rule in testable:
            assert rule.parameter not in rd.NOT_IN_CSV
        for rule in untestable:
            assert rule.parameter in rd.NOT_IN_CSV
