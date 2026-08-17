"""Local vector index over harvested permits.

A local index rather than a managed one, because the corpus is small enough and
the account is temporary.
"""
from __future__ import annotations

from .embed import PENDING


def build_index(records: list[dict]):
    raise NotImplementedError(PENDING)


def load_index(path):
    raise NotImplementedError(PENDING)
