"""Embedding permit records for similarity search.

Titan v2 on Bedrock, 1024 dimensions, confirmed reachable by preflight.

Three things about this module are deliberate.

Embeddings are optional. Retrieval only ever supplies context to a report, never
a verdict, so if Bedrock is unreachable the pipeline must keep working. A local
hashing embedder stands in when the network is absent. It is not semantically
meaningful and it says so: its only job is to keep the surrounding code
exercisable offline and to keep a demo from dying on a bad connection. Anything
built on it is labelled as such all the way to the report.

Titan v2 accepts one text per call, so a corpus of 1460 permits is 1460 round
trips. Embedding is done through a small thread pool, and throttling is retried
with backoff rather than being allowed to kill an unattended run.

Measured on this account: 0.93 requests per second with one worker, 1.11 with
eight, and 1.12 with sixteen at a connection pool of 32. The rate is capped at
about 1.1 per second no matter how many workers ask, so the ceiling is this
workshop account's Titan throughput and not the client. Concurrency is therefore
worth a real but modest 1.2x, and a full 1460 record run takes roughly 22 minutes.
What makes such a run survivable is the checkpointing in scripts/build_index.py,
not the parallelism.

Nothing here decides anything. A permit that came back approved is not evidence
that a new application complies, so the similarity score never reaches a verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .. import config

DIMENSIONS = 1024
DEFAULT_WORKERS = 8

# Botocore error codes that mean "slow down", as opposed to "this will never work".
THROTTLE_CODES = {
    "ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded",
    "ProvisionedThroughputExceededException", "ServiceUnavailable",
    "ServiceQuotaExceededException", "ModelTimeoutException",
    "InternalServerException",
}


class EmbeddingUnavailable(RuntimeError):
    """Bedrock could not be reached. Callers fall back rather than fail."""


def _bedrock_client(client=None):
    if client is not None:
        return client
    # A connection pool at least as large as the worker count, so the pool is
    # never the thing that serializes requests. Measurement above says the account
    # quota binds first, but a pool smaller than the workers would hide that.
    try:
        from botocore.config import Config

        return config.session().client(
            "bedrock-runtime",
            config=Config(max_pool_connections=max(10, DEFAULT_WORKERS * 2)),
        )
    except ImportError:  # pragma: no cover
        return config.session().client("bedrock-runtime")


def _is_throttle(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    if code in THROTTLE_CODES:
        return True
    text = str(exc).lower()
    return "throttl" in text or "too many requests" in text or "rate exceeded" in text


def embed_one(text: str, client, attempts: int = 6) -> list[float]:
    """Embed a single text, retrying throttling with exponential backoff.

    Raises EmbeddingUnavailable on a failure that retrying will not fix, such as
    missing credentials or an unknown model, so the caller can fall back at once
    rather than sleeping through six pointless retries.
    """
    body = json.dumps({"inputText": (text or "")[:8000]})
    delay = 1.0
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.invoke_model(
                modelId=config.BEDROCK_EMBED_MODEL,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
            payload = json.loads(response["body"].read())
            vector = payload.get("embedding")
            if not vector:
                raise EmbeddingUnavailable("Titan returned no embedding field")
            return [float(v) for v in vector]
        except EmbeddingUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_throttle(exc) or attempt == attempts - 1:
                raise EmbeddingUnavailable(str(exc)) from exc
            # Jitter, so a pool of eight workers does not retry in lockstep.
            time.sleep(delay + random.uniform(0, delay * 0.4))
            delay = min(delay * 2, 30.0)
    raise EmbeddingUnavailable(str(last))


def embed_texts_bedrock(
    texts: list[str],
    client=None,
    workers: int = DEFAULT_WORKERS,
    on_result: Callable[[int, list[float]], None] | None = None,
) -> list[list[float]]:
    """Embed with Titan through a thread pool.

    on_result is called with the index and vector as each one completes, which is
    how the caller checkpoints without waiting for the whole run.

    Results are returned in input order regardless of completion order, because
    the caller pairs them with records positionally and a reordering here would
    silently attach every vector to the wrong permit.
    """
    if not texts:
        return []
    client = _bedrock_client(client)
    results: list[list[float] | None] = [None] * len(texts)

    def work(index: int) -> tuple[int, list[float]]:
        return index, embed_one(texts[index], client)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for index, vector in pool.map(work, range(len(texts))):
            results[index] = vector
            if on_result is not None:
                on_result(index, vector)

    missing = [i for i, v in enumerate(results) if v is None]
    if missing:
        raise EmbeddingUnavailable(f"{len(missing)} texts produced no vector")
    return [v for v in results if v is not None]


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


def embed_texts(
    texts: list[str],
    client=None,
    allow_local: bool = True,
    workers: int = DEFAULT_WORKERS,
    on_result: Callable[[int, list[float]], None] | None = None,
) -> tuple[list[list[float]], str]:
    """Embed texts, returning the vectors and which backend produced them.

    The backend name is returned rather than logged because it has to travel with
    the results into the report. A precedent list built on the local stand-in must
    not be presented as though Titan produced it.
    """
    if not texts:
        return [], "none"
    try:
        vectors = embed_texts_bedrock(
            texts, client=client, workers=workers, on_result=on_result
        )
        return vectors, "bedrock-titan-v2"
    except EmbeddingUnavailable:
        if not allow_local:
            raise
        vectors = embed_texts_local(texts)
        if on_result is not None:
            for index, vector in enumerate(vectors):
                on_result(index, vector)
        return vectors, "local-hashing-fallback"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
