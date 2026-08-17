"""Reconciling the manifest against what is actually in S3.

The manifest is the input to retrieval, so a document recorded as uploaded but
missing from the bucket would become a silent gap. This module compares the two
and reports both directions of mismatch.

Sizes are computed in Python rather than in the shell because shell number
formatting is locale dependent and produced a wrong total during earlier work.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .s3sink import S3Sink

MB = 1_048_576


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def year_of(value) -> str:
    if value is None or isinstance(value, float):
        return "unknown"
    text = str(value)
    return text[-4:] if len(text) >= 4 else "unknown"


@dataclass
class Audit:
    bucket: str
    objects_total: int = 0
    pdf_objects: int = 0
    pdf_bytes: int = 0
    size_min: int = 0
    size_median: int = 0
    size_max: int = 0
    by_prefix: dict[str, int] = field(default_factory=dict)
    permit_records: int = 0
    document_entries: int = 0
    permits_with_docs: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    upload_status: dict[str, int] = field(default_factory=dict)
    doctypes: dict[str, int] = field(default_factory=dict)
    counties: dict[str, int] = field(default_factory=dict)
    years: dict[str, int] = field(default_factory=dict)
    parcel_fill: int = 0
    in_s3_not_manifest: list[str] = field(default_factory=list)
    in_manifest_not_s3: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "=== S3 inventory ===",
            f"bucket             s3://{self.bucket}",
            f"objects_total      {self.objects_total}",
            f"pdf_objects        {self.pdf_objects}",
            f"pdf_bytes          {self.pdf_bytes:,} "
            f"({self.pdf_bytes / MB:,.1f} MB / {self.pdf_bytes / MB / 1024:.2f} GB)",
        ]
        if self.pdf_objects:
            mean = self.pdf_bytes / self.pdf_objects
            lines += [
                f"size min/med/mean/max  {self.size_min / MB:.2f} / "
                f"{self.size_median / MB:.2f} / {mean / MB:.2f} / "
                f"{self.size_max / MB:.2f} MB",
            ]
        lines.append("")
        lines.append("by prefix:")
        for key, count in sorted(self.by_prefix.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {key:<45}{count}")

        lines += [
            "",
            "=== manifest ===",
            f"permit_records     {self.permit_records}",
            f"document_entries   {self.document_entries}",
            f"permits_with_docs  {self.permits_with_docs} / {self.permit_records}",
            f"parcel_id_present  {self.parcel_fill} / {self.document_entries}",
            f"statuses           {self.statuses}",
            f"upload_status      {self.upload_status}",
            f"doctypes           {self.doctypes}",
            f"counties           {self.counties}",
            f"years (top)        {dict(list(self.years.items())[:10])}",
            "",
            "=== reconciliation ===",
            f"in_s3_not_manifest {len(self.in_s3_not_manifest)} "
            f"{self.in_s3_not_manifest[:5]}",
            f"in_manifest_not_s3 {len(self.in_manifest_not_s3)} "
            f"{self.in_manifest_not_s3[:5]}",
        ]
        if not self.in_manifest_not_s3:
            lines.append("every document recorded as uploaded is present in S3")
        return "\n".join(lines)


def run(manifest_path: Path, sink: S3Sink | None = None) -> Audit:
    sink = sink or S3Sink()
    records = load_manifest(manifest_path)
    objects = sink.inventory()

    pdfs = [o for o in objects if o["Key"].endswith(".pdf")]
    sizes = sorted(o["Size"] for o in pdfs)

    audit = Audit(
        bucket=sink.bucket,
        objects_total=len(objects),
        pdf_objects=len(pdfs),
        pdf_bytes=sum(sizes),
        size_min=sizes[0] if sizes else 0,
        size_median=sizes[len(sizes) // 2] if sizes else 0,
        size_max=sizes[-1] if sizes else 0,
        by_prefix=dict(Counter("/".join(o["Key"].split("/")[:2]) for o in pdfs)),
        permit_records=len(records),
    )

    documents = [d for r in records for d in r.get("documents", [])]
    audit.document_entries = len(documents)
    audit.permits_with_docs = sum(1 for r in records if r.get("documents"))
    audit.statuses = dict(Counter(r.get("permitStatus") or "unknown" for r in records))
    audit.upload_status = dict(Counter(d.get("status") for d in documents))
    audit.doctypes = dict(Counter(d.get("doctype") for d in documents))
    audit.counties = dict(Counter(r.get("county") or "unknown" for r in records))
    audit.years = dict(
        sorted(
            Counter(year_of(r.get("appReceivedDate")) for r in records).items(),
            key=lambda kv: -kv[1],
        )
    )
    audit.parcel_fill = sum(1 for d in documents if d.get("parcel_id"))

    manifest_keys = {
        d["s3_key"] for d in documents
        if d.get("status") in ("uploaded", "already-present") and d.get("s3_key")
    }
    s3_keys = {o["Key"] for o in pdfs}
    audit.in_s3_not_manifest = sorted(s3_keys - manifest_keys)
    audit.in_manifest_not_s3 = sorted(manifest_keys - s3_keys)
    return audit
