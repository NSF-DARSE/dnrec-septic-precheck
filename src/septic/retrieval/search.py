"""Nearest neighbour lookup over the permit index.

What counts as a useful precedent for an application is the open question here.
Matching on outcome alone would surface the 253 denied and returned permits and
little else, which is too small and too skewed to be representative.
"""
from __future__ import annotations

from .embed import PENDING


def search(query: str, k: int = 5) -> list[dict]:
    raise NotImplementedError(PENDING)
