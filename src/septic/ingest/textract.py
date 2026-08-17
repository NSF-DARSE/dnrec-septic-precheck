"""Textract job submission, polling, and caching.

Site plans and permit applications in this corpus are scanned raster PDFs, so
there is no embedded text or vector geometry to read. Textract is the only route
to the field values and to where they sit on the page.

Analyses are cached on disk keyed by the S3 object ETag. OCR is the slowest and
most expensive step in the pipeline and the same document gets read many times
during development, so a rerun must not resubmit.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

TERMINAL = ("SUCCEEDED", "FAILED", "PARTIAL_SUCCESS")
DEFAULT_FEATURES = ("FORMS", "TABLES")


class TextractError(RuntimeError):
    pass


@dataclass
class Analysis:
    """A completed Textract analysis for one document."""

    s3_key: str
    job_id: str | None
    status: str
    pages: int
    blocks: list[dict] = field(default_factory=list)
    from_cache: bool = False
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("SUCCEEDED", "PARTIAL_SUCCESS") and bool(self.blocks)

    def to_json(self) -> dict:
        return {
            "s3_key": self.s3_key,
            "job_id": self.job_id,
            "status": self.status,
            "pages": self.pages,
            "message": self.message,
            "blocks": self.blocks,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "Analysis":
        return cls(
            s3_key=payload["s3_key"],
            job_id=payload.get("job_id"),
            status=payload.get("status", "SUCCEEDED"),
            pages=payload.get("pages", 0),
            blocks=payload.get("blocks", []),
            from_cache=True,
            message=payload.get("message"),
        )


class TextractClient:
    """Submit, poll, and cache document analyses."""

    def __init__(self, client=None, s3_client=None, bucket: str = config.S3_BUCKET,
                 cache_dir: Path | None = None):
        self.bucket = bucket
        self.cache_dir = Path(cache_dir or config.CACHE_DIR) / "textract"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._s3 = s3_client
        self._session = None

    def _get_session(self):
        if self._session is None:
            self._session = config.session()
        return self._session

    @property
    def client(self):
        if self._client is None:
            self._client = self._get_session().client("textract")
        return self._client

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = self._get_session().client("s3")
        return self._s3

    def _version(self, s3_key: str) -> str:
        """Cache discriminator. ETag changes when the object changes."""
        try:
            head = self.s3.head_object(Bucket=self.bucket, Key=s3_key)
            return head["ETag"].strip('"')
        except Exception:
            return "noetag"

    def _cache_path(self, s3_key: str, version: str) -> Path:
        safe = s3_key.replace("/", "__").replace("=", "-")
        return self.cache_dir / f"{safe}.{version}.json"

    def cached(self, s3_key: str) -> Analysis | None:
        path = self._cache_path(s3_key, self._version(s3_key))
        if not path.exists():
            return None
        try:
            return Analysis.from_json(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def analyze(
        self,
        s3_key: str,
        features: tuple[str, ...] = DEFAULT_FEATURES,
        timeout: int = 600,
        poll_interval: float = 5.0,
        use_cache: bool = True,
    ) -> Analysis:
        """Run document analysis on an S3 object, reusing a cached result."""
        version = self._version(s3_key)
        cache_path = self._cache_path(s3_key, version)
        if use_cache and cache_path.exists():
            cached = self.cached(s3_key)
            if cached is not None:
                return cached

        job = self.client.start_document_analysis(
            DocumentLocation={"S3Object": {"Bucket": self.bucket, "Name": s3_key}},
            FeatureTypes=list(features),
        )
        job_id = job["JobId"]

        status, pages, message = self._wait(job_id, timeout, poll_interval)
        if status not in ("SUCCEEDED", "PARTIAL_SUCCESS"):
            return Analysis(s3_key=s3_key, job_id=job_id, status=status, pages=pages,
                            message=message)

        blocks = list(self._collect(job_id))
        analysis = Analysis(s3_key=s3_key, job_id=job_id, status=status, pages=pages,
                            blocks=blocks, message=message)
        cache_path.write_text(
            json.dumps(analysis.to_json(), ensure_ascii=False), encoding="utf-8"
        )
        return analysis

    def _wait(self, job_id: str, timeout: int, poll_interval: float):
        deadline = time.time() + timeout
        status = "IN_PROGRESS"
        pages = 0
        message = None
        while time.time() < deadline:
            time.sleep(poll_interval)
            resp = self.client.get_document_analysis(JobId=job_id, MaxResults=1)
            status = resp.get("JobStatus", "")
            pages = resp.get("DocumentMetadata", {}).get("Pages", 0)
            message = resp.get("StatusMessage")
            if status in TERMINAL:
                return status, pages, message
        return "TIMEOUT", pages, f"still running after {timeout}s"

    def _collect(self, job_id: str):
        """All blocks across every result page."""
        token = None
        while True:
            kwargs = {"JobId": job_id, "MaxResults": 1000}
            if token:
                kwargs["NextToken"] = token
            resp = self.client.get_document_analysis(**kwargs)
            yield from resp.get("Blocks", [])
            token = resp.get("NextToken")
            if not token:
                return
