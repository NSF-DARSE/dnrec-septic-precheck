"""Chatbot configuration from environment variables.

All GCP/Gemini settings are read from the environment. The chatbot is optional:
the review app works fully without it. When configuration is incomplete or the
SDK is unavailable, ``is_available()`` returns False and the UI gracefully hides
the chatbot section.

Environment variables:
    GOOGLE_CLOUD_PROJECT       GCP project id (required for Vertex AI)
    GOOGLE_CLOUD_LOCATION      GCP location, e.g. "global" or "us-central1"
    GOOGLE_GENAI_USE_VERTEXAI  Set to "true" to route through Vertex AI
    SEPTIC_GEMINI_MODEL        Model name override (default: gemini-2.5-flash)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_MODEL = "gemini-2.5-flash"


@dataclass(frozen=True)
class ChatbotConfig:
    """Validated chatbot configuration."""

    project: str
    location: str
    use_vertexai: bool
    model: str

    @classmethod
    def from_env(cls) -> "ChatbotConfig | None":
        """Load configuration from environment variables.

        Returns None if required variables are not set.
        """
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global").strip()
        use_vertexai_raw = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip()
        model = os.environ.get("SEPTIC_GEMINI_MODEL", DEFAULT_MODEL).strip()

        if not project:
            return None

        use_vertexai = use_vertexai_raw.lower() in ("true", "1", "yes")

        if not model:
            model = DEFAULT_MODEL

        return cls(
            project=project,
            location=location,
            use_vertexai=use_vertexai,
            model=model,
        )


def is_available() -> bool:
    """Check whether the chatbot can be used.

    Returns True only when the SDK is importable and configuration is present.
    """
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return ChatbotConfig.from_env() is not None
