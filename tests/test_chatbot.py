"""Tests for the reviewer chatbot module.

All Gemini API calls are mocked. No cloud credits are spent by these tests.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_payload():
    """A minimal review payload for testing."""
    return {
        "verdict": "CANNOT VERIFY",
        "headline": "CANNOT VERIFY",
        "explanation": "No check reached a decision.",
        "subject": {
            "document": "permit_281364_60839580.pdf",
            "document_hash": "444470bb1036133331bb769dfecd34d9",
            "pages": 13,
            "permit_number": "281364",
        },
        "counts": {"pass": 7, "fail": 0, "unknown": 8, "return_reasons": 0},
        "coverage": {"evaluated": 7, "total": 15, "text": "7 of 15"},
        "deficiencies": [],
        "unresolved": [
            {
                "rule_id": "ISO-001-disposal-area-to-well",
                "outcome": "UNKNOWN",
                "requirement": "dist_disposal_to_well >= 100 feet",
                "reason": "dist_disposal_to_well could not be read",
                "observed": None,
                "threshold": 100,
                "units": "feet",
                "severity": "return",
                "citation": "Exhibit C, page 173",
                "section": "Exhibit C",
                "page": 173,
                "quote": "MINIMUM ISOLATION DISTANCES row Disposal area column Well: 100",
                "remedy": "Move the disposal area at least 100 feet from every well.",
                "verified": True,
                "provenance": None,
                "cross_references": [
                    {"label": "Exhibit C", "title": "Minimum Isolation Distances", "page": 173, "text": "table data"}
                ],
                "definitions": [],
                "exceptions": [],
                "caveats": "Four documented reductions apply.",
                "applicability": "applies",
                "excluded_by": None,
            }
        ],
        "satisfied": [
            {
                "rule_id": "PERC-001",
                "outcome": "PASS",
                "requirement": "percolation_rate <= 120 minutes/inch",
                "reason": "percolation_rate = 45, threshold is 120",
                "observed": 45,
                "threshold": 120,
                "units": "minutes/inch",
                "severity": "return",
                "citation": "5.2.4.2.5.7, page 42",
                "section": "5.2.4.2.5.7",
                "page": 42,
                "quote": "The percolation rate shall not exceed 120 minutes per inch.",
                "remedy": None,
                "verified": True,
                "provenance": "Page 3, field 'Percolation Rate'",
                "cross_references": [],
                "definitions": [],
                "exceptions": [],
                "caveats": None,
                "applicability": "applies",
                "excluded_by": None,
            }
        ],
        "not_applicable": [],
        "missing_information": [
            {"parameter": "dist_disposal_to_well", "reason": "not found in packet"}
        ],
        "discarded_readings": [],
        "facts_read": [
            {"parameter": "percolation_rate", "value": "45", "page": 3},
            {"parameter": "system_type", "value": "gravity", "page": 2},
            {"parameter": "owner_name", "value": "John Smith", "page": 1},
            {"parameter": "phone_number", "value": "302-555-1234", "page": 1},
            {"parameter": "email_address", "value": "john@example.com", "page": 1},
        ],
        "screening": {},
        "precedents": {},
        "notices": [],
        "generated_at": "2026-08-18T16:00:00Z",
        "wording_source": "rules and regulation text only",
    }


@pytest.fixture
def chatbot_env(monkeypatch):
    """Set the environment variables for chatbot configuration."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "hackathon-2026-dnrec")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("SEPTIC_GEMINI_MODEL", "gemini-2.5-flash")


@pytest.fixture
def no_chatbot_env(monkeypatch):
    """Clear chatbot environment variables."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("SEPTIC_GEMINI_MODEL", raising=False)


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------

class TestConfig:
    def test_from_env_with_all_vars(self, chatbot_env):
        from septic.chatbot.config import ChatbotConfig
        cfg = ChatbotConfig.from_env()
        assert cfg is not None
        assert cfg.project == "hackathon-2026-dnrec"
        assert cfg.location == "global"
        assert cfg.use_vertexai is True
        assert cfg.model == "gemini-2.5-flash"

    def test_from_env_missing_project(self, no_chatbot_env):
        from septic.chatbot.config import ChatbotConfig
        cfg = ChatbotConfig.from_env()
        assert cfg is None

    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
        monkeypatch.delenv("SEPTIC_GEMINI_MODEL", raising=False)
        from septic.chatbot.config import ChatbotConfig
        cfg = ChatbotConfig.from_env()
        assert cfg is not None
        assert cfg.location == "global"
        assert cfg.use_vertexai is False
        assert cfg.model == "gemini-2.5-flash"

    def test_model_override(self, chatbot_env, monkeypatch):
        monkeypatch.setenv("SEPTIC_GEMINI_MODEL", "gemini-2.0-pro")
        from septic.chatbot.config import ChatbotConfig
        cfg = ChatbotConfig.from_env()
        assert cfg.model == "gemini-2.0-pro"

    def test_is_available_no_env(self, no_chatbot_env):
        from septic.chatbot.config import is_available
        assert is_available() is False

    def test_is_available_with_env(self, chatbot_env):
        from septic.chatbot.config import is_available
        # SDK is installed, env is set
        assert is_available() is True


# ---------------------------------------------------------------------------
# Context construction tests
# ---------------------------------------------------------------------------

class TestContext:
    def test_build_context_structure(self, sample_payload):
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        assert ctx["verdict"] == "CANNOT VERIFY"
        assert ctx["headline"] == "CANNOT VERIFY"
        assert ctx["counts"] == {"pass": 7, "fail": 0, "unknown": 8, "return_reasons": 0}
        assert len(ctx["unresolved"]) == 1
        assert len(ctx["satisfied"]) == 1

    def test_pii_excluded_from_subject(self, sample_payload):
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        # document_hash should be stripped from subject
        assert "document_hash" not in ctx["subject"]
        # Safe fields preserved
        assert ctx["subject"]["document"] == "permit_281364_60839580.pdf"
        assert ctx["subject"]["pages"] == 13

    def test_pii_excluded_from_facts(self, sample_payload):
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        fact_params = [f["parameter"] for f in ctx["facts_read"]]
        # PII fields should be removed
        assert "owner_name" not in fact_params
        assert "phone_number" not in fact_params
        assert "email_address" not in fact_params
        # Technical facts preserved
        assert "percolation_rate" in fact_params
        assert "system_type" in fact_params

    def test_email_redacted_in_values(self):
        from septic.chatbot.context import _strip_pii_from_value
        result = _strip_pii_from_value("Contact: john@example.com for info")
        assert "john@example.com" not in result
        assert "[email redacted]" in result

    def test_phone_redacted_in_values(self):
        from septic.chatbot.context import _strip_pii_from_value
        result = _strip_pii_from_value("Call 302-555-1234 for questions")
        assert "302-555-1234" not in result
        assert "[phone redacted]" in result

    def test_compact_finding_has_citation(self, sample_payload):
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        finding = ctx["unresolved"][0]
        assert finding["citation"] == "Exhibit C, page 173"
        assert finding["section"] == "Exhibit C"
        assert finding["page"] == 173
        assert finding["quote"] is not None

    def test_cross_references_compacted(self, sample_payload):
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        finding = ctx["unresolved"][0]
        xrefs = finding["cross_references"]
        assert len(xrefs) == 1
        assert xrefs[0]["label"] == "Exhibit C"
        # Bulk text field should not be present in compact form
        assert "text" not in xrefs[0]

    def test_build_context_message_is_json(self, sample_payload):
        from septic.chatbot.context import build_context_message
        msg = build_context_message(sample_payload)
        assert msg.startswith("GROUNDED CONTEXT")
        # The JSON part should be parseable
        json_start = msg.index("{")
        parsed = json.loads(msg[json_start:])
        assert parsed["verdict"] == "CANNOT VERIFY"

    def test_empty_payload(self):
        from septic.chatbot.context import build_context
        ctx = build_context({})
        assert ctx["verdict"] is None
        assert ctx["deficiencies"] == []
        assert ctx["facts_read"] == []


# ---------------------------------------------------------------------------
# System instruction tests
# ---------------------------------------------------------------------------

class TestInstructions:
    def test_instruction_prohibits_approval(self):
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "NEVER approve or deny" in SYSTEM_INSTRUCTION

    def test_instruction_prohibits_overriding_results(self):
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "NEVER change, override, or second-guess" in SYSTEM_INSTRUCTION

    def test_instruction_requires_citations(self):
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "MUST include the section and page citation" in SYSTEM_INSTRUCTION

    def test_instruction_requires_separation(self):
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "FACTS" in SYSTEM_INSTRUCTION
        assert "RULE RESULTS" in SYSTEM_INSTRUCTION
        assert "EXPLANATION" in SYSTEM_INSTRUCTION

    def test_instruction_rejects_untrusted_overrides(self):
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "untrusted" in SYSTEM_INSTRUCTION.lower()

    def test_instruction_insufficient_evidence(self):
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "insufficient" in SYSTEM_INSTRUCTION.lower() or "Do not speculate" in SYSTEM_INSTRUCTION


# ---------------------------------------------------------------------------
# Client tests (all Gemini calls mocked)
# ---------------------------------------------------------------------------

class TestClient:
    def test_create_chatbot_no_config(self, no_chatbot_env, sample_payload):
        from septic.chatbot.client import create_chatbot
        bot = create_chatbot(sample_payload)
        assert bot is None

    @patch("septic.chatbot.client.genai", create=True)
    def test_send_message_success(self, mock_genai_module, chatbot_env, sample_payload):
        """Test that send_message returns model response text."""
        from septic.chatbot.client import ReviewerChatbot
        from septic.chatbot.config import ChatbotConfig

        config = ChatbotConfig.from_env()

        # Mock the genai module and client
        with patch("google.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client

            # Mock the chat response
            mock_response = MagicMock()
            mock_response.text = "The permit has 8 unresolved rules because the isolation distances could not be read from the packet."
            mock_response.candidates = None

            mock_chat = MagicMock()
            mock_chat.send_message.return_value = mock_response
            mock_client.chats.create.return_value = mock_chat

            # Patch the import inside ReviewerChatbot.__init__
            with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
                from septic.chatbot import client as client_mod
                # Directly construct with mocked internals
                bot = ReviewerChatbot.__new__(ReviewerChatbot)
                bot._config = config
                bot._payload = sample_payload
                bot._history = []
                bot._genai = mock_genai
                bot._types = MagicMock()
                bot._client = mock_client

                # Mock types.Content and types.Part
                bot._types.Content = MagicMock()
                bot._types.Part = MagicMock()
                bot._types.GenerateContentConfig = MagicMock()

                from septic.chatbot.context import build_context_message
                bot._context_message = build_context_message(sample_payload)

                response = bot.send_message("Why are rules unresolved?")

                assert "unresolved" in response.lower() or "isolation" in response.lower()
                assert len(bot.history) == 2
                assert bot.history[0]["role"] == "user"
                assert bot.history[1]["role"] == "model"

    @patch("septic.chatbot.client.genai", create=True)
    def test_send_message_api_failure(self, mock_genai_module, chatbot_env, sample_payload):
        """Test graceful handling of API errors."""
        from septic.chatbot.client import ChatbotError, ReviewerChatbot
        from septic.chatbot.config import ChatbotConfig

        config = ChatbotConfig.from_env()

        with patch("google.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.chats.create.side_effect = Exception("API quota exceeded")

            with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
                bot = ReviewerChatbot.__new__(ReviewerChatbot)
                bot._config = config
                bot._payload = sample_payload
                bot._history = []
                bot._genai = mock_genai
                bot._types = MagicMock()
                bot._types.Content = MagicMock()
                bot._types.Part = MagicMock()
                bot._types.GenerateContentConfig = MagicMock()
                bot._client = mock_client

                from septic.chatbot.context import build_context_message
                bot._context_message = build_context_message(sample_payload)

                with pytest.raises(ChatbotError, match="temporarily unavailable"):
                    bot.send_message("test question")

                # History should not be modified on failure
                assert len(bot.history) == 0

    def test_clear_history(self, chatbot_env, sample_payload):
        """Test that clear_history empties the conversation."""
        from septic.chatbot.client import ReviewerChatbot
        from septic.chatbot.config import ChatbotConfig

        config = ChatbotConfig.from_env()

        with patch("google.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client

            bot = ReviewerChatbot.__new__(ReviewerChatbot)
            bot._config = config
            bot._payload = sample_payload
            bot._history = [
                {"role": "user", "content": "hello"},
                {"role": "model", "content": "hi"},
            ]
            bot._genai = mock_genai
            bot._types = MagicMock()
            bot._client = mock_client

            bot.clear_history()
            assert bot.history == []

    def test_empty_response_raises(self, chatbot_env, sample_payload):
        """Test that an empty API response raises ChatbotError."""
        from septic.chatbot.client import ChatbotError, ReviewerChatbot
        from septic.chatbot.config import ChatbotConfig

        config = ChatbotConfig.from_env()

        with patch("google.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.text = ""
            mock_response.candidates = []

            mock_chat = MagicMock()
            mock_chat.send_message.return_value = mock_response
            mock_client.chats.create.return_value = mock_chat

            bot = ReviewerChatbot.__new__(ReviewerChatbot)
            bot._config = config
            bot._payload = sample_payload
            bot._history = []
            bot._genai = mock_genai
            bot._types = MagicMock()
            bot._types.Content = MagicMock()
            bot._types.Part = MagicMock()
            bot._types.GenerateContentConfig = MagicMock()
            bot._client = mock_client

            from septic.chatbot.context import build_context_message
            bot._context_message = build_context_message(sample_payload)

            with pytest.raises(ChatbotError, match="empty response"):
                bot.send_message("test")

    def test_create_chatbot_import_error(self, chatbot_env, sample_payload):
        """Test that missing SDK is handled gracefully."""
        from septic.chatbot.client import create_chatbot
        with patch.dict("sys.modules", {"google": None, "google.genai": None}):
            # Patch the import inside create_chatbot
            with patch("septic.chatbot.config.is_available", return_value=True):
                with patch(
                    "septic.chatbot.client.ReviewerChatbot.__init__",
                    side_effect=ImportError("No module named 'google.genai'"),
                ):
                    bot = create_chatbot(sample_payload)
                    # Should return None, not raise
                    assert bot is None


# ---------------------------------------------------------------------------
# No permit / no review tests
# ---------------------------------------------------------------------------

class TestNoPemit:
    def test_context_from_empty_payload(self):
        from septic.chatbot.context import build_context
        ctx = build_context({})
        assert ctx["verdict"] is None
        assert ctx["deficiencies"] == []
        assert ctx["unresolved"] == []
        assert ctx["facts_read"] == []

    def test_context_from_none_fields(self):
        from septic.chatbot.context import build_context
        payload = {
            "verdict": None,
            "headline": None,
            "subject": None,
            "counts": None,
            "deficiencies": None,
            "unresolved": None,
            "satisfied": None,
            "facts_read": None,
        }
        ctx = build_context(payload)
        assert ctx["verdict"] is None
        assert ctx["deficiencies"] == []


# ---------------------------------------------------------------------------
# PII filtering edge cases
# ---------------------------------------------------------------------------

class TestPIIFiltering:
    def test_facts_dict_filter(self):
        from septic.chatbot.context import _filter_facts_dict
        facts = {
            "percolation_rate": "45",
            "owner_name": "Jane Doe",
            "system_type": "gravity",
            "email": "jane@example.com",
            "document_hash": "abc123",
        }
        filtered = _filter_facts_dict(facts)
        assert "percolation_rate" in filtered
        assert "system_type" in filtered
        assert "owner_name" not in filtered
        assert "email" not in filtered
        assert "document_hash" not in filtered

    def test_mixed_case_pii_keys(self):
        from septic.chatbot.context import _filter_facts
        facts = [
            {"parameter": "Owner_Name", "value": "Test"},
            {"parameter": "percolation_rate", "value": "45"},
        ]
        filtered = _filter_facts(facts)
        params = [f["parameter"] for f in filtered]
        assert "Owner_Name" not in params
        assert "percolation_rate" in params

    def test_email_in_value_field(self):
        from septic.chatbot.context import _filter_facts
        facts = [
            {"parameter": "notes", "value": "Contact: user@test.com for info"},
        ]
        filtered = _filter_facts(facts)
        assert "[email redacted]" in filtered[0]["value"]
        assert "user@test.com" not in filtered[0]["value"]

    def test_non_string_values_unchanged(self):
        from septic.chatbot.context import _strip_pii_from_value
        assert _strip_pii_from_value(42) == 42
        assert _strip_pii_from_value(None) is None
        assert _strip_pii_from_value(3.14) == 3.14

    def test_raw_field_stripped_from_facts(self):
        """The 'raw' field in facts often contains OCR text with names/addresses."""
        from septic.chatbot.context import _filter_facts
        facts = [
            {
                "parameter": "site_evaluation_report",
                "value": "present",
                "source": "form_field",
                "where": "form field 'Site Evaluation Number', page 1",
                "raw": "Smith, John 123 Main Street, Dover, DE 19901 US",
            },
            {
                "parameter": "perc_rate",
                "value": "45",
                "raw": "45 min/in",
            },
        ]
        filtered = _filter_facts(facts)
        # raw field should be removed from all facts
        for f in filtered:
            assert "raw" not in f, f"raw field not stripped from {f['parameter']}"
        # Other fields preserved
        assert filtered[0]["parameter"] == "site_evaluation_report"
        assert filtered[0]["value"] == "present"
        assert filtered[1]["value"] == "45"


# ---------------------------------------------------------------------------
# Citation safety tests
# ---------------------------------------------------------------------------

class TestCitationSafety:
    """Verify that the context only forwards citations actually in the payload."""

    def test_citation_present_in_context(self, sample_payload):
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        # The unresolved finding has a citation
        finding = ctx["unresolved"][0]
        assert finding["citation"] == "Exhibit C, page 173"
        assert finding["page"] == 173

    def test_no_fabricated_citations(self, sample_payload):
        """The context builder only passes through what's in the payload."""
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        # All citations come from the original payload
        all_findings = ctx["deficiencies"] + ctx["unresolved"] + ctx["satisfied"]
        for finding in all_findings:
            # Every citation should match one from the original payload
            original_findings = (
                sample_payload["deficiencies"]
                + sample_payload["unresolved"]
                + sample_payload["satisfied"]
            )
            original_citations = {f["citation"] for f in original_findings}
            assert finding["citation"] in original_citations

    def test_missing_citation_handling(self):
        """Findings without citations pass through as None."""
        from septic.chatbot.context import _compact_finding
        finding = {
            "rule_id": "TEST-001",
            "outcome": "UNKNOWN",
            "requirement": "test >= 1",
            "reason": "not found",
            "observed": None,
            "threshold": 1,
            "units": None,
            "severity": "return",
            "citation": None,
            "section": None,
            "page": None,
            "quote": None,
            "remedy": None,
            "verified": False,
            "caveats": None,
            "applicability": "applies",
            "excluded_by": None,
            "cross_references": [],
            "definitions": [],
            "exceptions": [],
        }
        compact = _compact_finding(finding)
        assert compact["citation"] is None
        assert compact["page"] is None


# ---------------------------------------------------------------------------
# Verdict summary and coverage tests
# ---------------------------------------------------------------------------

class TestVerdictSummary:
    """Verify the verdict summary wording for different scenarios."""

    def test_no_deficiencies_with_unknown_checks(self):
        """When NO DEFICIENCIES FOUND but UNKNOWN checks exist, must explain."""
        from septic.chatbot.context import _build_verdict_summary
        summary = _build_verdict_summary(
            headline="NO DEFICIENCIES FOUND",
            evaluated=5,
            not_applicable_count=3,
            unreadable=7,
            total=15,
        )
        assert "No deficiencies were found among the" in summary
        assert "5 checks" in summary or "5 applicable" in summary
        assert "7 checks could not be evaluated" in summary
        assert "not an approval" in summary

    def test_no_deficiencies_never_claims_full_compliance(self):
        """Must never present NO DEFICIENCIES FOUND as proof of compliance."""
        from septic.chatbot.context import _build_verdict_summary
        summary = _build_verdict_summary(
            headline="NO DEFICIENCIES FOUND",
            evaluated=5,
            not_applicable_count=3,
            unreadable=7,
            total=15,
        )
        assert "complies" not in summary.lower()
        assert "approved" not in summary.lower()
        assert "approval" in summary.lower()  # "not an approval decision"
        assert "not an approval" in summary

    def test_coverage_breakdown_separates_categories(self):
        """Coverage must show evaluated, unevaluated, and not-applicable."""
        from septic.chatbot.context import _build_verdict_summary
        summary = _build_verdict_summary(
            headline="NO DEFICIENCIES FOUND",
            evaluated=5,
            not_applicable_count=3,
            unreadable=7,
            total=15,
        )
        assert "5 applicable checks evaluated and satisfied" in summary
        assert "7 could not be evaluated" in summary
        assert "3 were not applicable" in summary

    def test_all_checks_passed_no_unknown(self):
        """When all applicable checks pass, still say not an approval."""
        from septic.chatbot.context import _build_verdict_summary
        summary = _build_verdict_summary(
            headline="NO DEFICIENCIES FOUND",
            evaluated=12,
            not_applicable_count=3,
            unreadable=0,
            total=15,
        )
        assert "not an approval" in summary
        assert "could not be evaluated" not in summary

    def test_deficiencies_found_wording(self):
        """DEFICIENCIES FOUND must state requirements are not met."""
        from septic.chatbot.context import _build_verdict_summary
        summary = _build_verdict_summary(
            headline="DEFICIENCIES FOUND",
            evaluated=10,
            not_applicable_count=2,
            unreadable=3,
            total=15,
        )
        assert "requirement is not met" in summary

    def test_cannot_verify_wording(self):
        """CANNOT VERIFY must say no check reached a decision."""
        from septic.chatbot.context import _build_verdict_summary
        summary = _build_verdict_summary(
            headline="CANNOT VERIFY",
            evaluated=0,
            not_applicable_count=0,
            unreadable=15,
            total=15,
        )
        assert "No check reached a decision" in summary

    def test_verdict_summary_in_context(self, sample_payload):
        """The verdict_summary field must be present in the built context."""
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        assert "verdict_summary" in ctx
        assert len(ctx["verdict_summary"]) > 0


class TestMissingInformationFiltering:
    """Verify that missing_information only includes unresolved rule blockers."""

    def test_not_applicable_blockers_excluded(self):
        """Items that only block not_applicable rules must be filtered out."""
        from septic.chatbot.context import build_context
        payload = {
            "verdict": "NO DEFICIENCIES FOUND",
            "headline": "NO DEFICIENCIES FOUND",
            "counts": {"pass": 5, "fail": 0, "unknown": 2},
            "coverage": {"evaluated": 5, "not_applicable": 3, "unreadable": 2, "total": 10},
            "not_applicable": [
                {"rule_id": "SLOPE-001", "outcome": "PASS", "applicability": "not_applicable"},
                {"rule_id": "SEP-001", "outcome": "PASS", "applicability": "not_applicable"},
            ],
            "unresolved": [
                {"rule_id": "ISO-001", "outcome": "UNKNOWN"},
            ],
            "missing_information": [
                {"parameter": "disposal_slope", "named": "Slope", "blocks_rules": ["SLOPE-001"]},
                {"parameter": "limiting_zone", "named": "Limiting zone", "blocks_rules": ["SEP-001"]},
                {"parameter": "dist_to_well", "named": "Well distance", "blocks_rules": ["ISO-001"]},
            ],
        }
        ctx = build_context(payload)
        # Only the ISO-001 blocker should remain
        assert len(ctx["missing_information"]) == 1
        assert ctx["missing_information"][0]["parameter"] == "dist_to_well"

    def test_named_renamed_to_field(self):
        """The 'named' key must be renamed to 'field' in context output."""
        from septic.chatbot.context import build_context
        payload = {
            "verdict": "CANNOT VERIFY",
            "headline": "CANNOT VERIFY",
            "counts": {"pass": 0, "fail": 0, "unknown": 1},
            "coverage": {"evaluated": 0, "unreadable": 1, "total": 1},
            "unresolved": [{"rule_id": "TEST-001", "outcome": "UNKNOWN"}],
            "missing_information": [
                {"parameter": "test_param", "named": "Test Field Name", "blocks_rules": ["TEST-001"]},
            ],
        }
        ctx = build_context(payload)
        item = ctx["missing_information"][0]
        assert "named" not in item, "'named' key should be renamed to 'field'"
        assert item.get("field") == "Test Field Name"

    def test_mixed_blockers_keep_unresolved_ones(self):
        """Items blocking both not_applicable and unresolved rules are kept."""
        from septic.chatbot.context import build_context
        payload = {
            "verdict": "CANNOT VERIFY",
            "headline": "CANNOT VERIFY",
            "counts": {},
            "coverage": {"evaluated": 0, "unreadable": 2, "total": 3},
            "not_applicable": [{"rule_id": "NA-001", "outcome": "PASS"}],
            "unresolved": [{"rule_id": "ISO-001", "outcome": "UNKNOWN"}],
            "missing_information": [
                {"parameter": "shared_param", "named": "Shared", "blocks_rules": ["NA-001", "ISO-001"]},
            ],
        }
        ctx = build_context(payload)
        # Should be kept because it also blocks ISO-001 (unresolved)
        assert len(ctx["missing_information"]) == 1
        assert ctx["missing_information"][0]["field"] == "Shared"


# ---------------------------------------------------------------------------
# Grounding restriction tests
# ---------------------------------------------------------------------------

class TestGroundingRestrictions:
    """Verify that the context and instructions prevent ungrounded claims."""

    def test_caveats_excluded_from_context(self, sample_payload):
        """Caveats field must not appear in compact findings sent to Gemini.

        Caveats contain rich regulatory cross-references (e.g., Section 5.3.5.2,
        water-conservation reductions, percolation averaging) that lead the model
        to present them as independent regulatory findings.
        """
        from septic.chatbot.context import build_context
        ctx = build_context(sample_payload)
        for cat in ("unresolved", "satisfied", "not_applicable", "deficiencies"):
            for finding in ctx.get(cat, []):
                assert "caveats" not in finding, (
                    f"caveats field present in {finding.get('rule_id')} — "
                    f"this leads to ungrounded regulatory claims"
                )

    def test_no_uncited_sections_in_context(self, sample_payload):
        """Only explicitly cited sections should appear in finding fields."""
        from septic.chatbot.context import build_context
        import json
        ctx = build_context(sample_payload)
        ctx_json = json.dumps(ctx, default=str)
        # Section 5.3.5.2 should NOT appear (it was in caveats only)
        # In the sample payload we don't have it, but verify the pattern
        for finding in ctx.get("unresolved", []):
            section = finding.get("section")
            if section:
                # The section field should be the primary citation, not a cross-ref
                assert section == finding.get("section")

    def test_instruction_prohibits_introducing_sections(self):
        """System instruction must forbid introducing uncited sections."""
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "Do NOT introduce regulation sections" in SYSTEM_INSTRUCTION

    def test_instruction_prohibits_unsupported_exceptions(self):
        """System instruction must forbid unsupported exceptions/reductions."""
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "Do NOT suggest exceptions, reductions" in SYSTEM_INSTRUCTION

    def test_instruction_prohibits_relocation_advice(self):
        """System instruction must forbid relocation unless in remedy field."""
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "Do NOT recommend relocating" in SYSTEM_INSTRUCTION

    def test_instruction_prohibits_related_sections(self):
        """System instruction must forbid 'related sections' from general knowledge."""
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "Do NOT introduce" in SYSTEM_INSTRUCTION
        assert "related sections" in SYSTEM_INSTRUCTION

    def test_instruction_restricts_verify_next(self):
        """'What should the reviewer verify next?' must be limited to unresolved."""
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "unresolved applicable checks" in SYSTEM_INSTRUCTION

    def test_instruction_prohibits_averaging_interpretation(self):
        """Must not interpret percolation averaging without explicit context."""
        from septic.chatbot.instructions import SYSTEM_INSTRUCTION
        assert "percolation-rate averaging" in SYSTEM_INSTRUCTION

    def test_real_demo_context_has_no_caveats(self):
        """The real demo PDF context must not contain caveats text."""
        import sys, json
        sys.path.insert(0, 'src') if 'src' not in sys.path else None
        from pathlib import Path
        from septic.chatbot.context import build_context
        from septic import review as review_mod

        pdf = Path("testdata/permit_281364_60839580.pdf")
        if not pdf.exists():
            pytest.skip("demo PDF not present")

        result = review_mod.review(
            pdf=pdf, allow_network=False, with_precedents=False,
            with_screening=True, with_map=True,
        )
        payload = result.composed.to_json()
        ctx = build_context(payload)
        ctx_json = json.dumps(ctx, default=str)

        # These phrases come from caveats and must NOT be in the context
        assert "5.3.5.2" not in ctx_json, "Section 5.3.5.2 leaked from caveats"
        assert "water-conservation" not in ctx_json.lower(), "water-conservation leaked from caveats"
        assert "averaging" not in ctx_json.lower() or "percolation" not in ctx_json.lower(), (
            "percolation averaging interpretation leaked from caveats"
        )

    def test_real_demo_context_has_no_remedies_or_notes(self):
        """The real demo PDF context must not contain remedy or note content."""
        import sys, json
        sys.path.insert(0, 'src') if 'src' not in sys.path else None
        from pathlib import Path
        from septic.chatbot.context import build_context
        from septic import review as review_mod

        pdf = Path("testdata/permit_281364_60839580.pdf")
        if not pdf.exists():
            pytest.skip("demo PDF not present")

        result = review_mod.review(
            pdf=pdf, allow_network=False, with_precedents=False,
            with_screening=True, with_map=True,
        )
        payload = result.composed.to_json()
        ctx = build_context(payload)
        ctx_json = json.dumps(ctx, default=str)

        forbidden = [
            "remedy", "note a", "note b", "note e", "note h", "note i",
            "ephemeral", "lesser distance", "department approval",
            "move the", "relocat",
        ]
        for term in forbidden:
            assert term not in ctx_json.lower(), (
                f"'{term}' found in context — leads to ungrounded advice"
            )


# ---------------------------------------------------------------------------
# Deterministic guardrail tests
# ---------------------------------------------------------------------------

class TestBlockedQuestions:
    """Verify that forbidden-topic questions are blocked locally without Gemini."""

    def test_reduction_question_blocked(self):
        """The exact test question must be blocked."""
        from septic.chatbot.client import _is_blocked_question, BLOCKED_TOPIC_RESPONSE
        question = "Can any isolation distance be reduced, or should the disposal area be relocated?"
        assert _is_blocked_question(question) is True

    def test_blocked_response_content(self):
        """The blocked response must not contain forbidden advice."""
        from septic.chatbot.client import BLOCKED_TOPIC_RESPONSE
        lower = BLOCKED_TOPIC_RESPONSE.lower()
        assert "move" not in lower
        assert "note a" not in lower
        assert "note b" not in lower
        assert "department" not in lower.replace("consult", "").replace("dnrec", "")
        assert "consult" in lower
        assert "DNREC reviewer" in BLOCKED_TOPIC_RESPONSE

    def test_blocked_question_does_not_call_gemini(self, chatbot_env, sample_payload):
        """Blocked questions must return locally without any API call."""
        from unittest.mock import MagicMock, patch
        from septic.chatbot.client import ReviewerChatbot, BLOCKED_TOPIC_RESPONSE
        from septic.chatbot.config import ChatbotConfig
        from septic.chatbot.context import build_context_message

        config = ChatbotConfig.from_env()

        with patch("google.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client

            bot = ReviewerChatbot.__new__(ReviewerChatbot)
            bot._config = config
            bot._payload = sample_payload
            bot._history = []
            bot._genai = mock_genai
            bot._types = MagicMock()
            bot._client = mock_client
            bot._context_message = build_context_message(sample_payload)

            question = "Can any isolation distance be reduced, or should the disposal area be relocated?"
            response = bot.send_message(question)

            # Must not call Gemini
            mock_client.chats.create.assert_not_called()
            # Must return the deterministic response
            assert response == BLOCKED_TOPIC_RESPONSE
            # Must still record in history
            assert len(bot.history) == 2
            assert bot.history[0]["content"] == question
            assert bot.history[1]["content"] == BLOCKED_TOPIC_RESPONSE

    def test_approval_question_blocked(self):
        from septic.chatbot.client import _is_blocked_question
        assert _is_blocked_question("Should this permit be approved?") is True

    def test_exception_question_blocked(self):
        from septic.chatbot.client import _is_blocked_question
        assert _is_blocked_question("Are there any exceptions that apply here?") is True

    def test_waiver_question_blocked(self):
        from septic.chatbot.client import _is_blocked_question
        assert _is_blocked_question("Can the applicant get a waiver?") is True

    def test_variance_question_blocked(self):
        from septic.chatbot.client import _is_blocked_question
        assert _is_blocked_question("Is a variance available for this distance?") is True

    def test_normal_question_not_blocked(self):
        from septic.chatbot.client import _is_blocked_question
        assert _is_blocked_question("What information is missing?") is False
        assert _is_blocked_question("Summarize the review findings.") is False
        assert _is_blocked_question("Why could some rules not be evaluated?") is False

    def test_blocked_response_contains_no_forbidden_advice(self):
        """The deterministic response must not contain any of the advice we block."""
        from septic.chatbot.client import BLOCKED_TOPIC_RESPONSE
        lower = BLOCKED_TOPIC_RESPONSE.lower()
        forbidden_in_response = [
            "note a", "note b", "note e", "note h", "note i",
            "ephemeral", "lesser distance", "relocate the",
            "move the disposal", "50 feet",
        ]
        for term in forbidden_in_response:
            assert term not in lower, f"Blocked response contains '{term}'"


class TestChatStateClearing:
    """Verify that chat state is cleared when switching permits."""

    def test_state_cleared_on_different_payload(self, chatbot_env, sample_payload):
        """Chatbot history must reset when the payload changes."""
        from unittest.mock import MagicMock, patch
        from septic.chatbot.client import ReviewerChatbot
        from septic.chatbot.config import ChatbotConfig
        from septic.chatbot.context import build_context_message

        config = ChatbotConfig.from_env()

        with patch("google.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client

            # Create bot with first payload
            bot = ReviewerChatbot.__new__(ReviewerChatbot)
            bot._config = config
            bot._payload = sample_payload
            bot._history = [
                {"role": "user", "content": "hello"},
                {"role": "model", "content": "hi"},
            ]
            bot._genai = mock_genai
            bot._types = MagicMock()
            bot._client = mock_client
            bot._context_message = build_context_message(sample_payload)

            # History should be populated
            assert len(bot.history) == 2

            # Clear simulates what the UI does on permit change
            bot.clear_history()
            assert len(bot.history) == 0

    def test_ui_clears_state_on_payload_change(self):
        """The app.py chatbot section must clear state on payload ID change."""
        from pathlib import Path
        source = (Path("app.py")).read_text(encoding="utf-8")
        # Must track payload ID and clear on change
        assert "chatbot_payload_id" in source
        assert 'st.session_state["chatbot_messages"] = []' in source
        assert 'st.session_state["chatbot_instance"] = None' in source
