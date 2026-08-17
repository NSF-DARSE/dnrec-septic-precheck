"""Capability checks against the live AWS account.

The account used for the prototype is a temporary workshop account, so every
service the pipeline depends on has to be proven before work is built on top of
it. Each check returns a row for a PASS/FAIL table and never raises.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from . import config


@dataclass
class Check:
    name: str
    status: str = "SKIP"
    detail: str = ""
    fatal: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def ok(self, detail: str = "", **data: Any) -> "Check":
        self.status = "PASS"
        self.detail = detail
        self.data.update(data)
        return self

    def fail(self, detail: str, fatal: bool = False, **data: Any) -> "Check":
        self.status = "FAIL"
        self.detail = detail
        self.fatal = fatal
        self.data.update(data)
        return self


def _err(exc: Exception) -> str:
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
    text = f"{code}: {exc}" if code else str(exc)
    return " ".join(text.split())[:300]


def check_identity(sess) -> Check:
    c = Check("STS identity")
    try:
        ident = sess.client("sts").get_caller_identity()
        return c.ok(ident["Arn"], account=ident["Account"], arn=ident["Arn"])
    except Exception as exc:
        return c.fail(_err(exc), fatal=True)


def check_s3(sess) -> tuple[Check, Check, Check]:
    read = Check("S3 read")
    write = Check("S3 write")
    pick = Check("S3 sample PDF")

    s3 = sess.client("s3")
    bucket = config.S3_BUCKET

    keys: list[dict] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=config.PDF_PREFIX):
            keys.extend(page.get("Contents", []))
        read.ok(f"{len(keys)} objects under {config.PDF_PREFIX}/", count=len(keys))
    except Exception as exc:
        read.fail(_err(exc), fatal=True)
        return read, write, pick

    probe_key = f"{config.PREFLIGHT_PREFIX}/roundtrip.txt"
    try:
        body = f"preflight {time.time()}".encode()
        s3.put_object(Bucket=bucket, Key=probe_key, Body=body)
        got = s3.get_object(Bucket=bucket, Key=probe_key)["Body"].read()
        if got != body:
            write.fail("round-trip body mismatch")
        else:
            s3.delete_object(Bucket=bucket, Key=probe_key)
            write.ok("put, get, delete all succeeded")
    except Exception as exc:
        write.fail(_err(exc), fatal=True)

    pdfs = [k for k in keys if k["Key"].lower().endswith(".pdf") and k["Size"] > 0]
    if not pdfs:
        pick.fail("no PDF objects found to test OCR with")
    else:
        # Smallest real PDF keeps the Textract poll short.
        smallest = min(pdfs, key=lambda k: k["Size"])
        pick.ok(
            f"{smallest['Key']} ({smallest['Size'] / 1_048_576:.2f} MB)",
            key=smallest["Key"],
            size=smallest["Size"],
        )
    return read, write, pick


def check_textract(sess, key: str | None, timeout: int = 300) -> Check:
    c = Check("Textract StartDocumentAnalysis")
    if not key:
        return c.fail("no sample PDF available", fatal=True)

    try:
        tx = sess.client("textract")
        job = tx.start_document_analysis(
            DocumentLocation={
                "S3Object": {"Bucket": config.S3_BUCKET, "Name": key}
            },
            FeatureTypes=["FORMS", "TABLES"],
        )
        job_id = job["JobId"]
    except Exception as exc:
        return c.fail(_err(exc), fatal=True)

    deadline = time.time() + timeout
    status = "IN_PROGRESS"
    resp: dict = {}
    while time.time() < deadline:
        time.sleep(5)
        try:
            resp = tx.get_document_analysis(JobId=job_id, MaxResults=10)
        except Exception as exc:
            return c.fail(_err(exc), fatal=True, job_id=job_id)
        status = resp.get("JobStatus", "")
        if status in ("SUCCEEDED", "FAILED", "PARTIAL_SUCCESS"):
            break

    if status == "IN_PROGRESS":
        return c.fail(
            f"still running after {timeout}s (permission is fine, job is slow)",
            job_id=job_id,
        )
    if status not in ("SUCCEEDED", "PARTIAL_SUCCESS"):
        return c.fail(
            f"job {status}: {resp.get('StatusMessage', '')}", fatal=True, job_id=job_id
        )

    blocks = resp.get("Blocks", [])
    pages = resp.get("DocumentMetadata", {}).get("Pages", 0)
    words = [b for b in blocks if b.get("BlockType") == "WORD"]
    sample = " ".join(w.get("Text", "") for w in words[:12])
    return c.ok(
        f"{status}, {pages} page(s), {len(blocks)} blocks in first result page",
        job_id=job_id,
        pages=pages,
        blocks=len(blocks),
        sample_text=sample,
    )


def check_bedrock_list(sess) -> Check:
    c = Check("Bedrock ListFoundationModels")
    try:
        br = sess.client("bedrock")
        models = br.list_foundation_models().get("modelSummaries", [])
        anthropic = [m["modelId"] for m in models if m["modelId"].startswith("anthropic.")]
        embed = [
            m["modelId"]
            for m in models
            if "embed" in m["modelId"].lower()
        ]
        return c.ok(
            f"{len(models)} models visible, {len(anthropic)} Anthropic, {len(embed)} embedding",
            total=len(models),
            anthropic=anthropic,
            embedding=embed,
        )
    except Exception as exc:
        return c.fail(_err(exc))


def _invoke_candidates(preferred: str, available: list[str], contains: str) -> list[str]:
    """Ordered model ids to try: the configured one first, then anything similar.

    Newer models require cross-region inference profile IDs (us.anthropic.*)
    rather than foundation model ARNs. The profile ID must NOT have a trailing
    :0 suffix.
    """
    out = [preferred]
    # Add the us. prefix variant if not already present
    if not preferred.startswith("us.") and "anthropic." in preferred:
        out.append("us." + preferred.split(":")[0])
    for m in available:
        base = m.split(":")[0]
        us_variant = "us." + base
        if contains in m.lower():
            if m not in out:
                out.append(m)
            if us_variant not in out:
                out.append(us_variant)
    return out


def check_bedrock_text(sess, available: list[str]) -> Check:
    c = Check("Bedrock invoke Claude")
    rt = sess.client("bedrock-runtime")
    tried: list[str] = []
    last = ""
    for model_id in _invoke_candidates(config.BEDROCK_TEXT_MODEL, available, "claude"):
        tried.append(model_id)
        try:
            resp = rt.invoke_model(
                modelId=model_id,
                body=json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 16,
                        "messages": [{"role": "user", "content": "Reply with the word ready."}],
                    }
                ),
            )
            payload = json.loads(resp["body"].read())
            text = "".join(
                blk.get("text", "") for blk in payload.get("content", [])
            ).strip()
            return c.ok(f"{model_id} -> {text!r}", model_id=model_id, reply=text)
        except Exception as exc:
            last = _err(exc)
    return c.fail(f"all candidates denied ({len(tried)} tried). last error: {last}",
                  fatal=True, tried=tried)


def check_bedrock_embed(sess, available: list[str]) -> Check:
    c = Check("Bedrock invoke embeddings")
    rt = sess.client("bedrock-runtime")
    tried: list[str] = []
    last = ""
    candidates = _invoke_candidates(config.BEDROCK_EMBED_MODEL, available, "embed")
    for model_id in candidates:
        tried.append(model_id)
        if "cohere" in model_id:
            body = {"texts": ["percolation rate"], "input_type": "search_document"}
        else:
            body = {"inputText": "percolation rate"}
        try:
            resp = rt.invoke_model(modelId=model_id, body=json.dumps(body))
            payload = json.loads(resp["body"].read())
            vec = payload.get("embedding") or (payload.get("embeddings") or [[]])[0]
            if not vec:
                last = f"{model_id}: empty embedding"
                continue
            return c.ok(
                f"{model_id} -> {len(vec)} dimensions", model_id=model_id, dims=len(vec)
            )
        except Exception as exc:
            last = _err(exc)
    return c.fail(f"all candidates denied ({len(tried)} tried). last error: {last}",
                  fatal=True, tried=tried)


def run(textract_timeout: int = 300) -> tuple[list[Check], bool]:
    """Run every check. Returns the rows and whether a fatal check failed."""
    sess = config.session()
    checks: list[Check] = []

    checks.append(check_identity(sess))
    read, write, pick = check_s3(sess)
    checks.extend([read, write, pick])
    checks.append(check_textract(sess, pick.data.get("key"), timeout=textract_timeout))

    listing = check_bedrock_list(sess)
    checks.append(listing)
    available = listing.data.get("anthropic", []) + listing.data.get("embedding", [])
    checks.append(check_bedrock_text(sess, available))
    checks.append(check_bedrock_embed(sess, available))

    blocked = any(c.status == "FAIL" and c.fatal for c in checks)
    return checks, blocked


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks) + 2
    lines = ["PREFLIGHT", "=" * 72, f"{'CHECK'.ljust(width)}{'STATUS':<8}DETAIL", "-" * 72]
    for c in checks:
        lines.append(f"{c.name.ljust(width)}{c.status:<8}{c.detail}")
    lines.append("-" * 72)

    failed = [c for c in checks if c.status == "FAIL"]
    lines.append(f"{len(checks) - len(failed)}/{len(checks)} passed")

    bedrock_denied = [
        c for c in failed if c.name.startswith("Bedrock invoke")
    ]
    if bedrock_denied:
        lines += [
            "",
            "!" * 72,
            "BEDROCK INVOKE IS DENIED. STOP AND SWITCH TO THE FALLBACK.",
            "The workshop role cannot call InvokeModel, so retrieval embeddings and",
            "report composition cannot run on Bedrock in this account. Use a direct",
            "Anthropic API key, or request model access in a DNREC-owned account.",
        ]
        for c in bedrock_denied:
            lines.append(f"  {c.name}: {c.detail}")
        lines.append("!" * 72)
    return "\n".join(lines)
