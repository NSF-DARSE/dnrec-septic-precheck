"""Rendering a composed report to text or HTML.

Every finding must show its citation, because a reviewer has to be able to check
the requirement against the regulation.

Not implemented yet, pending sign off.
"""
from __future__ import annotations

from .compose import PENDING


def render_text(composed: dict) -> str:
    raise NotImplementedError(PENDING)


def render_html(composed: dict) -> str:
    raise NotImplementedError(PENDING)
