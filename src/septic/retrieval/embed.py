"""Embedding permit records for similarity search.

Titan v2 on Bedrock, 1024 dimensions, confirmed reachable by preflight.

Two things about this module are deliberate.

Embeddings are optional. Retrieval only ever supplies context to a report, never
a verdict, so if Bedrock is unreachable the pipeline must keep working. A local
hashing embedder stands in when the network is absent. It is not semantically
meaningful and it says so: its only job is to keep the surrounding code
exercisable offline and to keep a demo from dying on a bad connection. Anything
built on it is labelled as such all the way to the report.

Nothing here decides anything. A permit that came back approved is not evidence
that a new application complies, so the similarity score never reaches a verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct

from .. import config

DIMENSIONS = 1024


class EmbeddingUnavailable(RuntimeError):
    """Bedrock could not be reached. Callers fall back rather than fail."""


def _bedrock_client(client=None):
    if client is not None:
        return client
    return config.session().client("bedrock-runtime")


def embed_texts_bedrock(texts: list[str], client=None) -> list[list[float]]:
    """Embed with Titan. Raises EmbeddingUnavailable on any transport failure."""
    client = _bedrock_client(client)
    vectors: list[list[float]] = []
    for text in texts:
        body = json.dumps({"inputText": text[:8000]})
        try:
            response = client.invoke_model(
                modelId=config.BEDROCK_EMBED_MODEL,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
            payload = json.loads(response["body"].read())
        except Exception as exc:  # noqa: BLE001 - transport, auth, throttling
            raise EmbeddingUnavailable(str(exc)) from exc
        vector = payload.get("embedding")
        if not vector:
            raise EmbeddingUnavailable("Titan returned no embedding field")
        vectors.append([float(v) for v in vector])
    return vectors


def embed_texts_local(texts: list[str], dimensions: int = DIMENSIONS
                      ) -> list[list[float]]:
    """Deterministic offline stand-in. Not semantically meaningful.

    Hashes token trigrams into a fixed width vector. Two documents sharing tokens
    land near each other, which is enough to exercise indexing and search without
    a network, and nowhere near enough to call it semantic similarity. Every code
    path that uses it marks its output as degraded.
    """
    vectors: list[list[float]] = []
    for text in texts:
        vector = [0.0] * dimensions
        tokens = (text or "").lower().split()
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            slot = struct.unpack("<I", digest[:4])[0] % dimensions
            vector[slot] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        vectors.append([v / norm for v in vector])
    return vectors


def embed_texts(texts: list[str], client=None, allow_local: bool = True
                ) -> tuple[list[list[float]], str]:
    """Embed texts, returning the vectors and which backend produced them.

    The backend name is returned rather than logged because it has to travel with
    the results into the report. A precedent list built on the local stand-in must
    not be presented as though Titan produced it.
    """
    if not texts:
        return [], "none"
    try:
        return embed_texts_bedrock(texts, client=client), "bedrock-titan-v2"
    except EmbeddingUnavailable:
        if not allow_local:
            raise
        return embed_texts_local(texts), "local-hashing-fallback"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
