"""Writing harvested documents and manifests to S3.

Layout:
    pdfs/status=<Status-Slug>/<detail_id>_<permitNumber>/<NN>_<DocType>.pdf
    manifest/manifest_<tag>.jsonl

Keys are deterministic so a rerun skips what is already uploaded instead of
duplicating it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from botocore.exceptions import ClientError

from .. import config

SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
MISSING_CODES = ("404", "NoSuchKey", "NotFound")


def slug(value: str | None, maxlen: int = 60) -> str:
    s = SAFE_RE.sub("-", (value or "").strip()).strip("-")
    return (s or "unknown")[:maxlen]


def pdf_key(detail_id: str, permit_number: str | None, status: str | None,
            index: int, doctype: str | None) -> str:
    return (
        f"{config.PDF_PREFIX}/status={slug(status, 40)}/"
        f"{detail_id}_{slug(permit_number, 30)}/"
        f"{index:02d}_{slug(doctype, 30)}.pdf"
    )


def manifest_key(tag: str) -> str:
    return f"{config.MANIFEST_PREFIX}/manifest_{slug(tag, 60)}.jsonl"


@dataclass
class PutResult:
    key: str
    status: str
    bytes: int = 0
    md5: str | None = None
    sha256: str | None = None
    error: str | None = None


class S3Sink:
    """Idempotent writes to the permit bucket."""

    def __init__(self, client=None, bucket: str = config.S3_BUCKET,
                 dry_run: bool = False):
        self.bucket = bucket
        self.dry_run = dry_run
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = config.session().client("s3")
        return self._client

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in MISSING_CODES:
                return False
            raise

    def put_pdf(self, key: str, body: bytes, metadata: dict[str, str]) -> PutResult:
        """Upload one PDF, refusing anything without a PDF signature."""
        if not body[:4] == b"%PDF":
            return PutResult(key=key, status="not-a-pdf", bytes=len(body))

        result = PutResult(
            key=key,
            status="uploaded",
            bytes=len(body),
            md5=hashlib.md5(body).hexdigest(),
            sha256=hashlib.sha256(body).hexdigest(),
        )
        if self.dry_run:
            result.status = "dry-run"
            return result

        clean = {k: str(v)[:1024] for k, v in metadata.items() if v is not None}
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/pdf",
                Metadata=clean,
            )
        except Exception as exc:
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
        return result

    def put_manifest(self, tag: str, body: bytes) -> str:
        key = manifest_key(tag)
        if self.dry_run:
            return f"s3://{self.bucket}/{key} (dry-run)"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/x-ndjson",
        )
        return f"s3://{self.bucket}/{key}"

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def inventory(self, prefix: str = "") -> list[dict]:
        """Every object under a prefix. Used by the audit to reconcile counts."""
        objects: list[dict] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects.extend(page.get("Contents", []))
        return objects

    def ensure_bucket(self) -> list[str]:
        """Create the bucket if absent and apply the baseline hardening.

        Public access blocked and default encryption on, because the corpus is
        FOIA releasable but is not ours to publish.
        """
        notes: list[str] = []
        try:
            self.client.head_bucket(Bucket=self.bucket)
            notes.append(f"bucket exists: {self.bucket}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in MISSING_CODES:
                raise
            self.client.create_bucket(Bucket=self.bucket)
            notes.append(f"created bucket: {self.bucket}")

        try:
            self.client.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            notes.append("public access blocked")
        except ClientError as exc:
            notes.append(f"public access block failed: {exc.response['Error']['Code']}")

        try:
            self.client.put_bucket_encryption(
                Bucket=self.bucket,
                ServerSideEncryptionConfiguration={
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "AES256"
                            },
                            "BucketKeyEnabled": True,
                        }
                    ]
                },
            )
            notes.append("default encryption AES256")
        except ClientError as exc:
            notes.append(f"encryption failed: {exc.response['Error']['Code']}")

        return notes
