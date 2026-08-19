"""Gemini client wrapper for the reviewer chatbot.

Uses the google-genai SDK's chat session pattern (client.chats.create +
chat.send_message) to maintain conversation context without AFC warnings.

All Gemini calls go through this module so they can be mocked in tests.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import ChatbotConfig
from .context import build_context_message
from .instructions import SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)


class ChatbotError(Exception):
    """Raised when the chatbot cannot produce a response."""

    pass


# ---------------------------------------------------------------------------
# Deterministic guardrail: blocked topics
# ---------------------------------------------------------------------------

BLOCKED_TOPIC_RESPONSE = (
    "The verified review context does not contain enough evidence to determine "
    "approval, exceptions, reductions, variances, relocation or redesign "
    "options. Consult the cited regulation and a DNREC reviewer."
)

# Keywords that indicate a question about forbidden topics. When any of these
# appear in the user's message, the response is returned locally without
# calling Gemini. This is a deterministic guardrail, and it cannot be bypassed
# by prompt injection.
_BLOCKED_KEYWORDS = (
    "approv",       # approval, approved, approve
    "deny",         # deny, denial
    "denied",
    "exception",
    "reduction",
    "reduce",
    "variance",
    "waiver",
    "relocat",      # relocate, relocation
    "redesign",
    "move the",     # "move the disposal area"
    "moved",
    "lesser distance",
    "department approval",
    "note a",
    "note b",
    "note e",
    "note h",
    "note i",
    "ephemeral",
)


def _is_blocked_question(message: str) -> bool:
    """Return True if the question asks about a forbidden topic.

    This is intentionally broad, because it is better to refuse a borderline
    question than to let Gemini invent regulatory advice.
    """
    lower = message.lower()
    return any(keyword in lower for keyword in _BLOCKED_KEYWORDS)


class ReviewerChatbot:
    """A grounded conversational assistant for permit reviewers.

    Uses the google-genai SDK chat session to maintain multi-turn context.
    The system instruction and grounded context are set at creation time
    and cannot be overridden by user messages.
    """

    def __init__(self, config: ChatbotConfig, payload: dict):
        """Initialize the chatbot for a specific review payload.

        Args:
            config: Validated chatbot configuration.
            payload: The composed review payload dict (Composed.to_json()).

        Raises:
            ChatbotError: If the SDK cannot be imported or the client cannot
                be created.
        """
        self._config = config
        self._payload = payload
        self._history: list[dict[str, str]] = []

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ChatbotError(
                "The google-genai package is not installed. "
                "Install it with: pip install google-genai"
            ) from exc

        self._genai = genai
        self._types = types

        try:
            self._client = genai.Client(
                vertexai=config.use_vertexai,
                project=config.project,
                location=config.location,
            )
        except Exception as exc:
            raise ChatbotError(
                f"Could not create Gemini client: {exc}"
            ) from exc

        # Build the grounded context once at init
        self._context_message = build_context_message(payload)

    def send_message(self, user_message: str) -> str:
        """Send a reviewer question and return the chatbot response.

        Before calling Gemini, checks if the question asks about topics that
        require a deterministic refusal (approval, exceptions, reductions,
        relocations, etc.). For those, returns a fixed response without any
        API call.

        Prior assistant responses are NOT sent back to Gemini as context.
        Only the grounded review data and the current user question are sent.
        This prevents the model from treating its own prior outputs as
        authoritative evidence.

        Args:
            user_message: The reviewer's question (treated as untrusted).

        Returns:
            The chatbot's text response.

        Raises:
            ChatbotError: If the API call fails.
        """
        # Deterministic guardrail: intercept questions about forbidden topics
        # before they reach Gemini.
        blocked = _is_blocked_question(user_message)
        if blocked:
            response_text = BLOCKED_TOPIC_RESPONSE
            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "model", "content": response_text})
            return response_text

        types = self._types

        # Build chat context: grounded review data + current question only.
        # Prior assistant responses are NOT included, because this prevents the model
        # from treating its own prior outputs as authoritative evidence.
        history_contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=self._context_message)],
            ),
            types.Content(
                role="model",
                parts=[types.Part(text=(
                    "I have the review context. I will answer questions about "
                    "these findings using only the data provided, citing "
                    "regulation sections when available. How can I help?"
                ))],
            ),
        ]

        # Only include prior USER messages for conversational continuity,
        # not model responses (which could contain hallucinated content).
        for turn in self._history:
            if turn["role"] == "user":
                history_contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(text=turn["content"])],
                    )
                )
                # Add a minimal acknowledgment so the chat structure is valid
                history_contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part(text="Noted.")],
                    )
                )

        try:
            chat = self._client.chats.create(
                model=self._config.model,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0,
                ),
                history=history_contents,
            )
            response = chat.send_message(user_message)
        except Exception as exc:
            logger.warning("Gemini API call failed: %s", exc)
            raise ChatbotError(
                "The AI assistant is temporarily unavailable. "
                "The review results above are unaffected."
            ) from exc

        # Extract response text
        response_text = ""
        if response and response.text:
            response_text = response.text
        elif response and response.candidates:
            # Fallback: extract from candidates
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            response_text += part.text
        
        if not response_text:
            raise ChatbotError(
                "The AI assistant returned an empty response. "
                "Please try rephrasing your question."
            )

        # Record in history
        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "model", "content": response_text})

        return response_text

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self._history = []

    @property
    def history(self) -> list[dict[str, str]]:
        """Return a copy of the conversation history."""
        return list(self._history)


def create_chatbot(payload: dict) -> "ReviewerChatbot | None":
    """Create a chatbot instance, or None if unavailable.

    This is the entry point for the Streamlit UI. It handles all failure
    modes gracefully and returns None rather than raising.
    """
    config = ChatbotConfig.from_env()
    if config is None:
        logger.info("Chatbot configuration not set. Chatbot disabled.")
        return None

    try:
        return ReviewerChatbot(config=config, payload=payload)
    except ChatbotError as exc:
        logger.warning("Chatbot unavailable: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unexpected error creating chatbot: %s", exc)
        return None
