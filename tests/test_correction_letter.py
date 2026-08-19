"""Tests for the draft correction letter.

The letter is a rendering of data that exists in the composed payload. It must
contain every deficiency, never name a rule that passed, never use approval or
compliance language, and carry the synthetic notice when reviewing the synthetic
packet.
"""
import re
from pathlib import Path

import pytest

from septic import config
from septic.ingest import layout
from septic.ingest.extract import extract_facts
from septic.ingest.textract import TextractClient, document_hash
from septic.report import compose as compose_mod
from septic.report.letter import render_letter
from septic.report.wording import requirement_sentence
from septic.rules import engine
from septic.rules.schema import Citation, Operator, Rule, Severity, Verdict

ROOT = Path(__file__).resolve().parent.parent

SYNTHETIC_PDF = config.OUT_DIR / "examples" / "synthetic_demonstration_packet.pdf"
PERMIT_281364 = config.OUT_DIR / "examples" / "permit_281364_60839580.pdf"

FORBIDDEN_WORDS = [
    "approved", "compliant", "compliance", "satisfactory", "acceptable",
    "meets requirements", "no issues", "passes all",
]


def _make_rule(rule_id, parameter, **overrides):
    defaults = dict(
        id=rule_id, description="d",
        citation=Citation(section="TEST-1.2.3", page=99, quote="quoted text"),
        parameter=parameter, operator=Operator.GE, threshold=100,
        units="feet", severity=Severity.RETURN, verified=True,
        remedy="Fix it by doing this.",
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestLetterContent:
    """The letter must contain every deficiency with citation and value."""

    @pytest.fixture
    def deficient_payload(self):
        rules = [
            _make_rule("DEF-001", "dist_disposal_to_well"),
            _make_rule("DEF-002", "perc_rate", operator=Operator.LE,
                       threshold=120, units="mpi",
                       citation=Citation(section="5.2.4.2.5.7", page=52,
                                         quote="percolation rates slower than 120")),
            _make_rule("PASS-001", "design_flow", threshold=240,
                       units="gpd"),
            _make_rule("UNREAD-001", "dist_tank_to_well", threshold=50),
        ]
        facts = {
            "dist_disposal_to_well": 60,
            "perc_rate": 140,
            "design_flow": 480,
        }
        report = engine.evaluate(facts, rules)
        return compose_mod.compose(report).to_json()

    def test_letter_contains_every_deficiency(self, deficient_payload):
        letter = render_letter(deficient_payload)
        assert letter, "no letter produced for a deficient payload"
        # Both deficiencies must appear
        assert "TEST-1.2.3, page 99" in letter
        assert "5.2.4.2.5.7, page 52" in letter
        # Values must appear
        assert "60" in letter
        assert "140" in letter

    def test_letter_contains_readable_requirement_sentences(self, deficient_payload):
        letter = render_letter(deficient_payload)
        for f in deficient_payload["deficiencies"]:
            sentence = requirement_sentence(f)
            assert sentence in letter, (
                f"requirement sentence not in letter: {sentence}"
            )

    def test_letter_contains_remedy(self, deficient_payload):
        letter = render_letter(deficient_payload)
        assert "Fix it by doing this." in letter

    def test_letter_never_names_a_passing_rule(self, deficient_payload):
        letter = render_letter(deficient_payload)
        assert "PASS-001" not in letter
        assert "design_flow" not in letter

    def test_letter_never_uses_compliance_language(self, deficient_payload):
        letter = render_letter(deficient_payload).lower()
        for word in FORBIDDEN_WORDS:
            assert word not in letter, (
                f"letter contains forbidden word: {word!r}"
            )

    def test_letter_states_it_is_a_draft(self, deficient_payload):
        letter = render_letter(deficient_payload)
        assert "DRAFT" in letter
        assert "reviewer" in letter.lower()
        assert "edit" in letter.lower()

    def test_letter_includes_unresolved_grouped(self, deficient_payload):
        letter = render_letter(deficient_payload)
        # The unresolved check should be mentioned
        assert "ADDITIONAL INFORMATION NEEDED" in letter
        # The requirement sentence for the unread check should be present
        unresolved_f = deficient_payload["unresolved"][0]
        sentence = requirement_sentence(unresolved_f)
        assert sentence in letter

    def test_no_letter_when_no_deficiencies(self):
        """A packet with no deficiencies produces no letter."""
        rules = [_make_rule("PASS-001", "design_flow", threshold=240)]
        facts = {"design_flow": 480}
        report = engine.evaluate(facts, rules)
        payload = compose_mod.compose(report).to_json()
        assert payload["headline"] == "NO DEFICIENCIES FOUND"
        letter = render_letter(payload)
        assert letter == ""

    def test_no_raw_expressions_in_letter(self, deficient_payload):
        """No machine expressions should appear in the letter."""
        letter = render_letter(deficient_payload)
        raw_re = re.compile(r"[a-z]+_[a-z_]+\s*(?:>=|<=|>|<|==|!=)\s*\d")
        matches = raw_re.findall(letter)
        assert not matches, f"letter contains raw expressions: {matches}"


class TestSyntheticPacketLetter:
    """The synthetic packet's letter must carry the synthetic notice."""

    @pytest.fixture(autouse=True)
    def require_packet(self):
        if not SYNTHETIC_PDF.exists():
            pytest.skip("synthetic packet not present")

    def test_synthetic_letter_carries_notice(self):
        from septic import review as review_mod
        result = review_mod.review(
            pdf=SYNTHETIC_PDF, allow_network=False,
            with_precedents=False, with_screening=False, with_map=False,
        )
        payload = result.composed.to_json()
        letter = render_letter(payload)
        assert "SYNTHETIC DEMONSTRATION PACKET" in letter
        assert "not a real permit application" in letter

    def test_synthetic_letter_contains_both_deficiencies(self):
        from septic import review as review_mod
        result = review_mod.review(
            pdf=SYNTHETIC_PDF, allow_network=False,
            with_precedents=False, with_screening=False, with_map=False,
        )
        payload = result.composed.to_json()
        letter = render_letter(payload)
        assert "Exhibit C, page 173" in letter
        assert "5.2.4.2.5.7, page 52" in letter
        assert "60" in letter
        assert "140" in letter


class TestLetterNotShownForPassingPacket:
    """The console must not offer a letter for NO DEFICIENCIES FOUND."""

    def test_console_only_shows_letter_for_deficiencies(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        # The letter download is gated on DEFICIENCIES FOUND
        assert '"DEFICIENCIES FOUND"' in source
        assert "render_letter" in source
        # It must be inside a conditional, not unconditional
        letter_section = source[source.index("render_letter"):]
        # Should appear after the DEFICIENCIES FOUND check
        gate_pos = source.index('"DEFICIENCIES FOUND"')
        letter_pos = source.index("render_letter")
        assert gate_pos < letter_pos
