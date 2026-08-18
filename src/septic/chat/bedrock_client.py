"""Claude on Bedrock, for the chat panel.

One thin wrapper over the Anthropic Messages API as Bedrock exposes it. Model id
and credentials come from septic.config, so this follows the same profile and
region as the rest of the pipeline and there is no second place to configure.

Nothing here decides anything. The caller passes the finished rule findings in
as context and the model writes prose about them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    NoRegionError,
    ProfileNotFound,
)

from .. import config

ANTHROPIC_VERSION = "bedrock-2023-05-31"

# Temperature is low but not zero. This is explanatory prose about a decision
# that has already been made, so wording can vary; the numbers it cites cannot,
# and those come from the prompt rather than from sampling.
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2048

PREFLIGHT_HINT = "Check access with `python -m septic preflight`."


class BedrockChatError(Exception):
    """A chat turn could not be completed.

    retriable marks the failures worth trying again (throttling, timeouts) as
    opposed to the ones that need a person to change something (no credentials,
    no model access).
    """

    def __init__(self, message: str, retriable: bool = False):
        super().__init__(message)
        self.retriable = retriable


@dataclass
class ChatResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    model_id: str = ""

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"


# Bedrock reports every refusal as a ClientError. The code is what distinguishes
# "you cannot use this model" from "slow down".
_RETRIABLE_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
    "ModelNotReadyException",
}

_CODE_MESSAGES = {
    "AccessDeniedException": (
        "Bedrock refused the request for model {model}. The role can reach "
        "Bedrock but is not allowed to invoke this model. " + PREFLIGHT_HINT
    ),
    "ValidationException": (
        "Bedrock rejected the request for model {model} as invalid. Newer "
        "Claude models need a cross region inference profile id such as "
        "us.anthropic.claude-..., with no trailing :0. Override it with "
        "SEPTIC_BEDROCK_TEXT_MODEL."
    ),
    "ResourceNotFoundException": (
        "Bedrock does not have a model called {model} in "
        f"{config.AWS_REGION}. Override it with SEPTIC_BEDROCK_TEXT_MODEL."
    ),
    "ThrottlingException": (
        "Bedrock throttled the request. Wait a moment and ask again."
    ),
    "TooManyRequestsException": (
        "Bedrock throttled the request. Wait a moment and ask again."
    ),
    "ServiceUnavailableException": (
        "Bedrock is temporarily unavailable. Ask again in a moment."
    ),
    "ModelTimeoutException": (
        "The model did not respond in time. Ask again, or ask something narrower."
    ),
}


def _wrap(exc: Exception, model: str) -> BedrockChatError:
    """Turn a boto exception into something a reviewer can act on."""
    if isinstance(exc, NoCredentialsError):
        return BedrockChatError(
            "No AWS credentials found, so the assistant cannot be reached. Set "
            f"up the {config.AWS_PROFILE} profile in ~/.aws/credentials, or "
            "export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY. "
            + PREFLIGHT_HINT
        )
    if isinstance(exc, ProfileNotFound):
        return BedrockChatError(
            f"AWS profile {config.AWS_PROFILE} is not configured and no ambient "
            "credentials were found. " + PREFLIGHT_HINT
        )
    if isinstance(exc, NoRegionError):
        return BedrockChatError(
            "No AWS region configured. Set AWS_REGION, currently expected to be "
            f"{config.AWS_REGION}."
        )
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        template = _CODE_MESSAGES.get(code)
        message = (
            template.format(model=model)
            if template
            else f"Bedrock returned {code or 'an error'}: "
            f"{exc.response.get('Error', {}).get('Message', exc)}"
        )
        return BedrockChatError(message, retriable=code in _RETRIABLE_CODES)
    if isinstance(exc, BotoCoreError):
        return BedrockChatError(f"Could not reach Bedrock: {exc}", retriable=True)
    return BedrockChatError(f"Unexpected failure calling Bedrock: {exc}")


def _client(model: str):
    try:
        return config.session().client("bedrock-runtime")
    except Exception as exc:
        raise _wrap(exc, model) from exc


def _body(
    messages: list[dict],
    system_prompt: str | None,
    max_tokens: int,
    temperature: float,
) -> str:
    if not messages:
        raise BedrockChatError("Cannot send an empty conversation to the model.")

    payload: dict = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": m["role"], "content": m["content"]} for m in messages
        ],
    }
    if system_prompt:
        payload["system"] = system_prompt
    return json.dumps(payload)


def chat(
    messages: list[dict],
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    model_id: str | None = None,
) -> ChatResponse:
    """Send a conversation and return the whole reply.

    messages is the history in Anthropic order, alternating user and assistant,
    ending with the user turn to be answered.
    """
    model = model_id or config.BEDROCK_TEXT_MODEL
    client = _client(model)
    body = _body(messages, system_prompt, max_tokens, temperature)

    try:
        response = client.invoke_model(modelId=model, body=body)
        payload = json.loads(response["body"].read())
    except BedrockChatError:
        raise
    except Exception as exc:
        raise _wrap(exc, model) from exc

    usage = payload.get("usage", {})
    return ChatResponse(
        text="".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type", "text") == "text"
        ).strip(),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        stop_reason=payload.get("stop_reason", ""),
        model_id=model,
    )


def chat_stream(
    messages: list[dict],
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    model_id: str | None = None,
) -> Iterator[str]:
    """Same call, yielding text as it arrives.

    Streaming is worth the extra path here: the prompt carries the full rule
    findings plus retrieved regulation text, so a reply can take several seconds
    and a reviewer watching a blank panel assumes it has hung.

    Raises before yielding anything if the call is refused, so a caller can show
    the error instead of a half written answer.
    """
    model = model_id or config.BEDROCK_TEXT_MODEL
    client = _client(model)
    body = _body(messages, system_prompt, max_tokens, temperature)

    try:
        response = client.invoke_model_with_response_stream(
            modelId=model, body=body
        )
    except BedrockChatError:
        raise
    except Exception as exc:
        raise _wrap(exc, model) from exc

    stream = response.get("body")
    if stream is None:
        raise BedrockChatError("Bedrock returned no response stream.")

    try:
        for event in stream:
            chunk = event.get("chunk")
            if not chunk:
                continue
            payload = json.loads(chunk["bytes"])
            if payload.get("type") != "content_block_delta":
                continue
            text = payload.get("delta", {}).get("text", "")
            if text:
                yield text
    except Exception as exc:
        raise _wrap(exc, model) from exc
