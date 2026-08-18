"""The reviewer console, driven through Streamlit's own test harness.

Runs app.py for real: the dashboard, the packet loader, and the chat turn. The
Bedrock call is replaced in every test, so the suite never needs credentials and
never reaches the network.

Skipped when streamlit is absent, since it is a demo only dependency in
requirements-dev.txt and the pipeline does not import it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="reviewer console is a dev extra")

from streamlit.testing.v1 import AppTest  # noqa: E402

from septic.chat import bedrock_client  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app.py"
EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "examples" / "sample_facts.json"

LOAD_EXAMPLE = "Load example packet"
CLEAR_PACKET = "Clear packet"


def run_app() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=120)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


def click(app: AppTest, label: str) -> AppTest:
    matches = [b for b in app.button if b.label == label]
    assert matches, f"no button labelled {label!r}"
    result = matches[0].click().run()
    assert not result.exception, [str(e.value) for e in result.exception]
    return result


def texts(elements) -> str:
    return "\n".join(e.value for e in elements)


@pytest.fixture
def fake_reply(monkeypatch):
    """Replace the Bedrock stream. Returns the recorded call arguments."""
    recorded: dict = {}

    def install(chunks=("Because ", "the rule is unverified."), raises=None):
        def fake_stream(messages, system_prompt=None, **kwargs):
            recorded["messages"] = messages
            recorded["system_prompt"] = system_prompt
            if raises is not None:
                raise raises
            yield from chunks

        monkeypatch.setattr(bedrock_client, "chat_stream", fake_stream)
        return recorded

    return install


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


class TestColdStart:
    def test_app_runs_with_no_packet(self):
        app = run_app()
        headers = [h.value for h in app.header]
        assert "Pre-submission review" in headers
        assert "Ask about a finding" in headers

    def test_shows_the_rule_set_when_nothing_is_loaded(self):
        app = run_app()
        assert len(app.dataframe) >= 1
        assert "No packet loaded" in texts(app.info)

    def test_offers_starter_questions(self):
        app = run_app()
        labels = [b.label for b in app.button]
        assert any(label.endswith("?") for label in labels)

    def test_chat_works_without_a_packet(self, fake_reply):
        """General regulation questions must not require an application."""
        recorded = fake_reply()
        app = run_app()
        app.chat_input[0].set_value("What are the isolation distances?").run()

        assert "No packet loaded" in recorded["system_prompt"]
        assert recorded["messages"][-1]["content"] == (
            "What are the isolation distances?"
        )


# ---------------------------------------------------------------------------
# Loading a packet
# ---------------------------------------------------------------------------


class TestPacket:
    def test_example_file_is_tracked_and_readable(self):
        """It lives outside out/, which is gitignored, so the button survives a clone."""
        assert EXAMPLE.exists()
        assert isinstance(json.loads(EXAMPLE.read_text(encoding="utf-8")), dict)

    def test_loading_the_example_produces_a_verdict_and_counts(self):
        app = click(run_app(), LOAD_EXAMPLE)
        labels = {m.label: m.value for m in app.metric}
        assert set(labels) == {"Pass", "Fail", "Cannot verify", "Return reasons"}
        assert sum(int(v) for v in labels.values()) > 0

    def test_unverified_rule_set_yields_cannot_verify(self):
        """The interlock: nothing certified means no verdict, by design."""
        app = click(run_app(), LOAD_EXAMPLE)
        assert "CANNOT VERIFY" in texts(app.warning)

    def test_findings_and_facts_are_both_shown(self):
        app = click(run_app(), LOAD_EXAMPLE)
        assert len(app.dataframe) >= 2
        assert len(app.expander) >= 1

    def test_metadata_keys_are_not_treated_as_facts(self):
        app = click(run_app(), LOAD_EXAMPLE)
        facts = [df for df in app.dataframe if "Field" in list(df.value.columns)]
        assert facts, "expected a facts table"
        assert not any(
            str(name).startswith("_") for name in facts[0].value["Field"]
        )

    def test_clearing_returns_to_the_rule_set_view(self):
        app = click(click(run_app(), LOAD_EXAMPLE), CLEAR_PACKET)
        assert "No packet loaded" in texts(app.info)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class TestChat:
    def test_reply_is_rendered_and_kept_in_history(self, fake_reply):
        fake_reply(chunks=("The rule is ", "unverified."))
        app = run_app()
        app = app.chat_input[0].set_value("Why is EX001 unknown?").run()

        assert not app.exception
        assert len(app.chat_message) >= 2

        # A second turn must carry the first one back to the model.
        recorded = fake_reply(chunks=("Second answer.",))
        app = app.chat_input[0].set_value("And the lot area?").run()
        roles = [m["role"] for m in recorded["messages"]]
        assert roles == ["user", "assistant", "user"]

    def test_findings_reach_the_model_as_context(self, fake_reply):
        recorded = fake_reply()
        app = click(run_app(), LOAD_EXAMPLE)
        app.chat_input[0].set_value("Why is the perc rate unknown?").run()

        prompt = recorded["system_prompt"]
        assert "Findings for the packet on screen" in prompt
        assert "EX002-percolation-rate-present" in prompt

    def test_the_verdict_shown_is_the_verdict_sent(self, fake_reply):
        """The panel and the prompt must not disagree about the outcome."""
        recorded = fake_reply()
        app = click(run_app(), LOAD_EXAMPLE)
        app.chat_input[0].set_value("What is the verdict?").run()
        assert "CANNOT VERIFY" in recorded["system_prompt"]

    def test_starter_question_is_answered_not_just_recorded(self, fake_reply):
        recorded = fake_reply()
        app = run_app()
        starters = [b for b in app.button if b.label.endswith("?")]
        app = starters[0].click().run()

        assert not app.exception
        assert recorded.get("messages"), "the starter question was never sent"
        assert len(app.chat_message) >= 2

    def test_refusal_is_reported_without_crashing(self, fake_reply):
        fake_reply(raises=bedrock_client.BedrockChatError("no model access"))
        app = run_app()
        app = app.chat_input[0].set_value("Why?").run()

        assert not app.exception
        assert "no model access" in texts(app.error)

    def test_refusal_says_the_findings_still_stand(self, fake_reply):
        """A model outage must not read as the review being invalid."""
        fake_reply(raises=bedrock_client.BedrockChatError("throttled"))
        app = click(run_app(), LOAD_EXAMPLE)
        app = app.chat_input[0].set_value("Why?").run()
        assert "findings on the left still stand" in texts(app.caption)

    def test_failed_turn_is_not_kept_in_history(self, fake_reply):
        """Otherwise the next turn replays a question that got no answer."""
        fake_reply(raises=bedrock_client.BedrockChatError("down"))
        app = run_app()
        app = app.chat_input[0].set_value("first question").run()

        recorded = fake_reply(chunks=("now it works",))
        app = app.chat_input[0].set_value("second question").run()

        contents = [m["content"] for m in recorded["messages"]]
        assert contents == ["second question"]
