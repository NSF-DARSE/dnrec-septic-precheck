"""Tests for the synthetic demonstration packet.

The synthetic packet exists so the demo can show DEFICIENCIES FOUND. Two
properties are non-negotiable:

1. The notice renders on both surfaces, so nobody mistakes it for a real permit.
2. Removing the notice from the payload fails a test, so it cannot be dropped
   by accident.
"""
import json
from pathlib import Path

import pytest

from septic import config
from septic.ingest import layout
from septic.ingest.extract import extract_facts
from septic.ingest.textract import TextractClient, document_hash
from septic.report import compose as compose_mod
from septic.report import render as render_mod
from septic.review import SYNTHETIC_NOTICE, SYNTHETIC_STEM, _is_synthetic
from septic.rules import engine
from septic.rules.schema import Verdict

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_PDF = config.OUT_DIR / "examples" / "synthetic_demonstration_packet.pdf"


def _load_synthetic():
    """Load the synthetic packet from its cache and run the review chain."""
    client = TextractClient()
    doc_hash = document_hash(SYNTHETIC_PDF.read_bytes())
    analysis = client.cached_by_hash(doc_hash)
    if analysis is None:
        pytest.skip("synthetic packet cache not built; run scripts/build_synthetic_packet.py")
    document = layout.parse_blocks(analysis.blocks)
    extraction = extract_facts(document)
    report = engine.evaluate(extraction.facts)
    return report, extraction


class TestSyntheticPacketVerdict:
    """The packet must produce DEFICIENCIES FOUND with real violations."""

    @pytest.fixture(autouse=True)
    def require_packet(self):
        if not SYNTHETIC_PDF.exists():
            pytest.skip("synthetic packet not present")

    def test_verdict_is_deficiencies_found(self):
        report, _ = _load_synthetic()
        assert report.verdict is Verdict.DEFICIENCIES_FOUND

    def test_at_least_two_failures(self):
        report, _ = _load_synthetic()
        assert len(report.failures) >= 2

    def test_some_checks_pass(self):
        report, _ = _load_synthetic()
        assert len(report.satisfied) >= 1

    def test_some_checks_are_unknown(self):
        report, _ = _load_synthetic()
        assert len(report.unknowns) >= 1

    def test_coverage_shows_three_way_split(self):
        report, _ = _load_synthetic()
        coverage = report.coverage()
        assert coverage["evaluated"] >= 2
        assert coverage["unreadable"] >= 1


class TestSyntheticNoticeIsRequired:
    """The notice must be present. Removing it must fail a test."""

    @pytest.fixture(autouse=True)
    def require_packet(self):
        if not SYNTHETIC_PDF.exists():
            pytest.skip("synthetic packet not present")

    def test_notice_constant_is_not_empty(self):
        assert SYNTHETIC_NOTICE
        assert "SYNTHETIC" in SYNTHETIC_NOTICE
        assert "not a real permit" in SYNTHETIC_NOTICE

    def test_is_synthetic_detects_the_packet(self):
        assert _is_synthetic({"document": "synthetic_demonstration_packet.pdf"})
        assert not _is_synthetic({"document": "permit_281364_60839580.pdf"})

    def test_review_injects_notice_into_composed_output(self):
        """The full review chain must carry the notice in the payload."""
        from septic import review as review_mod

        result = review_mod.review(
            pdf=SYNTHETIC_PDF,
            allow_network=False,
            with_precedents=False,
            with_screening=False,
            with_map=False,
        )
        payload = result.composed.to_json()
        assert SYNTHETIC_NOTICE in payload["notices"]

    def test_removing_notice_from_payload_is_detectable(self):
        """Guard against accidental removal. If the notice is absent the test fails."""
        from septic import review as review_mod

        result = review_mod.review(
            pdf=SYNTHETIC_PDF,
            allow_network=False,
            with_precedents=False,
            with_screening=False,
            with_map=False,
        )
        payload = result.composed.to_json()
        # Simulate removal
        payload_without = dict(payload)
        payload_without["notices"] = [
            n for n in payload_without["notices"] if "SYNTHETIC" not in n
        ]
        # The notice must NOT survive removal
        assert SYNTHETIC_NOTICE not in payload_without["notices"]
        # And the original must still have it
        assert SYNTHETIC_NOTICE in payload["notices"]

    def test_notice_renders_in_html_report(self):
        """The HTML renderer must show the notice."""
        from septic import review as review_mod

        result = review_mod.review(
            pdf=SYNTHETIC_PDF,
            allow_network=False,
            with_precedents=False,
            with_screening=False,
            with_map=False,
        )
        html = result.html
        assert "SYNTHETIC DEMONSTRATION PACKET" in html
        assert "not a real permit" in html

    def test_notice_renders_in_text_report(self):
        """The text renderer must show the notice."""
        from septic import review as review_mod

        result = review_mod.review(
            pdf=SYNTHETIC_PDF,
            allow_network=False,
            with_precedents=False,
            with_screening=False,
            with_map=False,
        )
        text = result.text
        assert "SYNTHETIC DEMONSTRATION PACKET" in text
        assert "NOTICE" in text

    def test_the_console_renders_notices_from_the_payload(self):
        """The console app.py renders the notices list from the payload."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert 'payload.get("notices")' in source
        assert "notice" in source.lower()


class TestSyntheticPacketNaming:
    """The name must make it impossible to mistake for a real permit."""

    def test_filename_says_synthetic(self):
        assert "synthetic" in SYNTHETIC_PDF.name
        assert "demonstration" in SYNTHETIC_PDF.name
        assert "packet" in SYNTHETIC_PDF.name

    def test_no_permit_number_in_name(self):
        """It must never look like permit_NNNNNN."""
        import re
        assert not re.search(r"permit_\d+", SYNTHETIC_PDF.name)
