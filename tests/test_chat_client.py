"""The Bedrock wrapper: request shape, reply parsing, and failure translation.

No network. The boto client is replaced, because what matters here is that the
request Bedrock receives is well formed and that every way the call can fail
produces something a reviewer can act on.
"""
from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from septic import config
from septic.chat import bedrock_client as bc


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeBody:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class FakeClient:
    """Records the request and returns a canned reply."""

    def __init__(self, payload: dict | None = None, raises: Exception | None = None,
                 events: list | None = None):
        self.payload = payload or {"content": [{"type": "text", "text": "ok"}]}
        self.raises = raises
        self.events = events
        self.calls: list[dict] = []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return {"body": FakeBody(self.payload)}

    def invoke_model_with_response_stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return {"body": self.events or []}


def delta(text: str) -> dict:
    return {
        "chunk": {
            "bytes": json.dumps(
                {"type": "content_block_delta", "delta": {"text": text}}
            ).encode("utf-8")
        }
    }


def client_error(code: str, message: str = "nope") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}}, "InvokeModel"
    )


@pytest.fixture
def patched(monkeypatch):
    """Install a FakeClient and hand it back for inspection."""

    def install(**kwargs) -> FakeClient:
        fake = FakeClient(**kwargs)
        monkeypatch.setattr(bc, "_client", lambda model: fake)
        return fake

    return install


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class TestRequest:
    def test_sends_messages_and_system_prompt(self, patched):
        fake = patched()
        bc.chat(
            [{"role": "user", "content": "why did EX001 fail?"}],
            system_prompt="you explain findings",
        )

        body = json.loads(fake.calls[0]["body"])
        assert body["messages"] == [
            {"role": "user", "content": "why did EX001 fail?"}
        ]
        assert body["system"] == "you explain findings"
        assert body["anthropic_version"] == bc.ANTHROPIC_VERSION

    def test_omits_system_key_when_no_prompt(self, patched):
        fake = patched()
        bc.chat([{"role": "user", "content": "hello"}])
        assert "system" not in json.loads(fake.calls[0]["body"])

    def test_multi_turn_history_is_preserved_in_order(self, patched):
        fake = patched()
        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        bc.chat(history)

        sent = json.loads(fake.calls[0]["body"])["messages"]
        assert [m["role"] for m in sent] == ["user", "assistant", "user"]
        assert [m["content"] for m in sent] == ["first", "reply", "second"]

    def test_extra_message_keys_are_dropped(self, patched):
        """Streamlit history can carry UI keys Bedrock rejects."""
        fake = patched()
        bc.chat([{"role": "user", "content": "hi", "rendered_at": 123}])

        sent = json.loads(fake.calls[0]["body"])["messages"]
        assert sent == [{"role": "user", "content": "hi"}]

    def test_uses_configured_model_by_default(self, patched):
        fake = patched()
        bc.chat([{"role": "user", "content": "hi"}])
        assert fake.calls[0]["modelId"] == config.BEDROCK_TEXT_MODEL

    def test_model_override(self, patched):
        fake = patched()
        bc.chat([{"role": "user", "content": "hi"}], model_id="custom.model")
        assert fake.calls[0]["modelId"] == "custom.model"

    def test_temperature_is_low_by_default(self, patched):
        """Wording may vary, cited numbers may not."""
        fake = patched()
        bc.chat([{"role": "user", "content": "hi"}])
        assert json.loads(fake.calls[0]["body"])["temperature"] <= 0.3

    def test_empty_conversation_is_refused_before_any_call(self, patched):
        fake = patched()
        with pytest.raises(bc.BedrockChatError):
            bc.chat([])
        assert fake.calls == []


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------


class TestResponse:
    def test_joins_text_blocks(self, patched):
        patched(payload={
            "content": [
                {"type": "text", "text": "Section 5.3.4 "},
                {"type": "text", "text": "requires the isolation distance."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 900, "output_tokens": 40},
        })
        result = bc.chat([{"role": "user", "content": "q"}])

        assert result.text == "Section 5.3.4 requires the isolation distance."
        assert result.input_tokens == 900
        assert result.output_tokens == 40
        assert result.stop_reason == "end_turn"
        assert result.truncated is False

    def test_ignores_non_text_blocks(self, patched):
        patched(payload={
            "content": [
                {"type": "thinking", "thinking": "internal"},
                {"type": "text", "text": "visible"},
            ]
        })
        assert bc.chat([{"role": "user", "content": "q"}]).text == "visible"

    def test_truncated_reply_is_flagged(self, patched):
        patched(payload={
            "content": [{"type": "text", "text": "cut off"}],
            "stop_reason": "max_tokens",
        })
        assert bc.chat([{"role": "user", "content": "q"}]).truncated is True

    def test_empty_content_gives_empty_text_not_an_error(self, patched):
        patched(payload={"content": []})
        assert bc.chat([{"role": "user", "content": "q"}]).text == ""


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class TestStreaming:
    def test_yields_deltas_in_order(self, patched):
        patched(events=[delta("Section "), delta("5.3.4 "), delta("applies.")])
        chunks = list(bc.chat_stream([{"role": "user", "content": "q"}]))
        assert chunks == ["Section ", "5.3.4 ", "applies."]

    def test_skips_non_delta_events(self, patched):
        patched(events=[
            {"chunk": {"bytes": json.dumps({"type": "message_start"}).encode()}},
            delta("text"),
            {"chunk": {"bytes": json.dumps({"type": "message_stop"}).encode()}},
        ])
        assert list(bc.chat_stream([{"role": "user", "content": "q"}])) == ["text"]

    def test_refusal_raises_before_any_text_is_yielded(self, patched):
        """A caller must be able to show an error instead of a partial answer."""
        patched(raises=client_error("AccessDeniedException"))
        stream = bc.chat_stream([{"role": "user", "content": "q"}])
        with pytest.raises(bc.BedrockChatError):
            next(stream)


# ---------------------------------------------------------------------------
# Failure translation
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_credentials_names_the_profile(self, patched):
        patched(raises=NoCredentialsError())
        with pytest.raises(bc.BedrockChatError) as caught:
            bc.chat([{"role": "user", "content": "q"}])

        message = str(caught.value)
        assert config.AWS_PROFILE in message
        assert caught.value.retriable is False

    def test_access_denied_is_not_retriable(self, patched):
        patched(raises=client_error("AccessDeniedException"))
        with pytest.raises(bc.BedrockChatError) as caught:
            bc.chat([{"role": "user", "content": "q"}])
        assert caught.value.retriable is False

    def test_throttling_is_retriable(self, patched):
        patched(raises=client_error("ThrottlingException"))
        with pytest.raises(bc.BedrockChatError) as caught:
            bc.chat([{"role": "user", "content": "q"}])
        assert caught.value.retriable is True

    def test_validation_error_mentions_the_inference_profile(self, patched):
        """The trap the preflight notes warn about, surfaced where it is hit."""
        patched(raises=client_error("ValidationException"))
        with pytest.raises(bc.BedrockChatError) as caught:
            bc.chat([{"role": "user", "content": "q"}])
        assert "inference profile" in str(caught.value)

    def test_unknown_code_still_reports_the_message(self, patched):
        patched(raises=client_error("SomethingNew", "unexpected condition"))
        with pytest.raises(bc.BedrockChatError) as caught:
            bc.chat([{"role": "user", "content": "q"}])
        assert "unexpected condition" in str(caught.value)

    def test_client_construction_failure_is_wrapped(self, monkeypatch):
        """Regression: the failure path must not raise NameError itself."""
        def boom():
            raise NoCredentialsError()

        monkeypatch.setattr(config, "session", boom)
        with pytest.raises(bc.BedrockChatError):
            bc.chat([{"role": "user", "content": "q"}])
