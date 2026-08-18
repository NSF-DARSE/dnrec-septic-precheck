"""Tests for resumable, concurrent index building.

Two properties matter here. Vectors must stay paired with the right permit, since
a reordering would attach every vector to the wrong record and produce scores that
look plausible and mean nothing. And a checkpoint must never be continued by a
different backend, because Titan vectors and stand-in vectors are not comparable.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_index as bi  # noqa: E402
from septic.retrieval import embed as em  # noqa: E402


class FakeClient:
    """A Bedrock stand-in that can be told to throttle or fail."""

    def __init__(self, throttle_times=0, fail_with=None, dims=8):
        self.calls = 0
        self.throttle_times = throttle_times
        self.fail_with = fail_with
        self.dims = dims

    def invoke_model(self, **kwargs):
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        if self.calls <= self.throttle_times:
            error = Exception("ThrottlingException: rate exceeded")
            error.response = {"Error": {"Code": "ThrottlingException"}}
            raise error
        text = json.loads(kwargs["body"])["inputText"]
        # A deterministic vector derived from the text, so ordering is checkable.
        seed = float(len(text))
        body = json.dumps({"embedding": [seed] * self.dims})

        class Body:
            def read(self_inner):
                return body

        return {"body": Body()}


class TestRetryAndBackoff:
    def test_retries_throttling_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(em.time, "sleep", lambda s: None)
        client = FakeClient(throttle_times=3)
        vector = em.embed_one("hello", client, attempts=6)
        assert len(vector) == 8
        assert client.calls == 4

    def test_gives_up_after_attempts(self, monkeypatch):
        monkeypatch.setattr(em.time, "sleep", lambda s: None)
        client = FakeClient(throttle_times=99)
        with pytest.raises(em.EmbeddingUnavailable):
            em.embed_one("hello", client, attempts=3)

    def test_non_throttle_error_fails_immediately(self, monkeypatch):
        """No credentials will not be fixed by waiting."""
        monkeypatch.setattr(em.time, "sleep", lambda s: None)
        error = Exception("NoCredentialsError: unable to locate credentials")
        client = FakeClient(fail_with=error)
        with pytest.raises(em.EmbeddingUnavailable):
            em.embed_one("hello", client, attempts=6)
        assert client.calls == 1, "a permanent failure must not be retried"

    def test_throttle_detection(self):
        error = Exception("boom")
        error.response = {"Error": {"Code": "ThrottlingException"}}
        assert em._is_throttle(error)
        assert em._is_throttle(Exception("Too Many Requests"))
        assert not em._is_throttle(Exception("AccessDeniedException"))


class TestOrdering:
    def test_concurrent_results_stay_in_input_order(self):
        """The caller pairs vectors with records positionally."""
        client = FakeClient()
        texts = ["a", "bb", "ccc", "dddd", "eeeee", "ffffff"]
        vectors = em.embed_texts_bedrock(texts, client=client, workers=4)
        assert len(vectors) == len(texts)
        for text, vector in zip(texts, vectors):
            assert vector[0] == float(len(text)), (
                "vector does not match its input text, ordering is broken"
            )

    def test_on_result_called_for_every_record(self):
        client = FakeClient()
        seen = {}
        texts = ["a", "bb", "ccc"]
        em.embed_texts_bedrock(
            texts, client=client, workers=2,
            on_result=lambda i, v: seen.__setitem__(i, v),
        )
        assert set(seen) == {0, 1, 2}

    def test_fallback_still_reports_every_result(self, monkeypatch):
        """The offline path must checkpoint too, or a local run cannot resume."""
        def boom(*a, **k):
            raise em.EmbeddingUnavailable("no network")
        monkeypatch.setattr(em, "embed_texts_bedrock", boom)
        seen = []
        vectors, backend = em.embed_texts(
            ["a", "b", "c"], on_result=lambda i, v: seen.append(i)
        )
        assert backend == "local-hashing-fallback"
        assert sorted(seen) == [0, 1, 2]


class TestCheckpoint:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "index.partial.json"
        vectors = {"1": [0.1, 0.2], "2": [0.3, 0.4]}
        bi.save_checkpoint(path, "bedrock-titan-v2", vectors)
        loaded = bi.load_checkpoint(path, "bedrock-titan-v2")
        assert set(loaded) == {"1", "2"}
        assert loaded["1"] == pytest.approx([0.1, 0.2])

    def test_backend_mismatch_is_refused(self, tmp_path):
        """Titan vectors and stand-in vectors are not comparable."""
        path = tmp_path / "index.partial.json"
        bi.save_checkpoint(path, "bedrock-titan-v2", {"1": [0.1]})
        loaded = bi.load_checkpoint(path, "local-hashing-fallback")
        assert loaded == {}, "a checkpoint from another backend must not be reused"

    def test_missing_checkpoint_is_empty(self, tmp_path):
        assert bi.load_checkpoint(tmp_path / "nope.json", "any") == {}

    def test_corrupt_checkpoint_is_discarded_not_raised(self, tmp_path):
        """An interrupted write must not stop the next run."""
        path = tmp_path / "index.partial.json"
        path.write_text("{not json", encoding="utf-8")
        assert bi.load_checkpoint(path, "bedrock-titan-v2") == {}

    def test_write_is_atomic(self, tmp_path):
        """Written via a temp file, so a crash cannot leave a truncated file."""
        path = tmp_path / "index.partial.json"
        bi.save_checkpoint(path, "b", {"1": [0.5]})
        assert path.exists()
        assert not path.with_suffix(".tmp").exists()
        assert json.loads(path.read_text())["count"] == 1


class TestProgressFormatting:
    @pytest.mark.parametrize("seconds,expected", [
        (30, "30s"), (90, "1.5m"), (7200, "2.0h"),
    ])
    def test_humanize(self, seconds, expected):
        assert bi.humanize(seconds) == expected
