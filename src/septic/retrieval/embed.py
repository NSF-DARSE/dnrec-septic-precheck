"""Embedding permit records for similarity search.

Titan embeddings are reachable from the current account (1024 dimensions,
confirmed by preflight), unlike text generation. Chunking strategy and what a
permit record should contain are still open questions, so nothing is built yet.
"""
from __future__ import annotations

PENDING = "retrieval design not yet approved"


def embed_texts(texts: list[str]) -> list[list[float]]:
    raise NotImplementedError(PENDING)
