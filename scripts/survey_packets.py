"""Survey the harvested packets: OCR what is not cached, then apply the rules.

Only 218 of the 1226 approved permits harvested carry a document, and only three
of those had been through Textract. This runs the rest and catalogues what the
rule engine says about each one, so the answer to "does any real DNREC packet
actually fail a requirement" comes from the corpus rather than from a guess.

    python scripts/survey_packets.py --list
    python scripts/survey_packets.py --cached-only
    python scripts/survey_packets.py --permits-from 250308 --permits-to 283066 \
        --page-budget 500 --tag q1
    python scripts/survey_packets.py --merge

Every result is written into the same on-disk cache the review command reads,
keyed by the document SHA256, so a packet analysed here is instantly reviewable
offline afterwards and is never sent to Textract twice.

A packet is all of its documents, not the first one. That matters: on the
multi document packets the first file is usually a one page issued permit
certificate and the application itself is a later, much larger file. Permit
282133 reads two of fifteen checks from its first document and carries a 12.7 MB
document after it. Every document is analysed and the facts are merged, first
statement winning, with any disagreement between documents recorded rather than
silently resolved. The verdict still comes from engine.evaluate on the merged
facts and from nowhere else.

Cost control. Textract bills per page for FORMS and TABLES, and page count is
only known once a job finishes, so the budget is enforced before each submission
against the pages already committed. The running total is logged after every
document. Reaching the budget stops the run and is reported as a stop, not as a
completed survey: how far it got is part of the result.

Splitting the work. A shard is a permit number range, which partitions the
corpus without any shared state, so several can run at once and --merge combines
the shard files afterwards. Ordering inside a shard is by permit number and
nothing else. No packet is chosen, skipped or edited for what it might show.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from septic import config
from septic.ingest import layout
from septic.ingest.extract import extract_facts
from septic.ingest.textract import Analysis, TextractClient, document_hash
from septic.rules import engine

DEFAULT_FEATURES = ("FORMS", "TABLES")
POLL_SECONDS = 5.0
SUBMIT_RETRY_SECONDS = (5, 15, 45)
# Used only to hold the page budget while jobs are in flight, since Textract
# reports a page count when a job finishes and not before. Replaced by the
# running average as soon as anything completes.
ASSUMED_PAGES = 15


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

def documents(manifest: Path) -> list[dict]:
    """Every harvested document, in permit number then document order.

    document_hash is the cache key and is the first 32 hex characters of the
    document SHA256, which is what septic.ingest.textract.document_hash returns
    for the same bytes. 49 of the 239 manifest rows, permits 283203 to 283372,
    were written without a sha256, and taking that empty string as a cache key
    silently pointed 47 permits at one file: every analysis overwrote the last,
    and every one of those permits was then evaluated against whichever document
    finished last. It produced 37 identical readings of a limiting zone depth
    that belonged to one packet. So an empty hash is never a key here. It is left
    as None and resolved from the object bytes only when a run is allowed to
    touch S3, and a document with no hash is reported as not analysed rather than
    guessed at.
    """
    found: list[dict] = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        docs = [
            d for d in (record.get("documents") or [])
            if d.get("s3_key") or d.get("key")
        ]
        for index, document in enumerate(docs):
            key = document.get("s3_key") or document.get("key")
            sha = (document.get("sha256") or "").strip()
            found.append({
                "permit_number": str(record.get("permitNumber") or ""),
                "detail_id": str(record.get("detail_id") or ""),
                "doc_index": index,
                "doc_count": len(docs),
                "doc_name": key.rsplit("/", 1)[-1],
                "doctype": document.get("doctype"),
                "s3_key": key,
                "document_hash": sha[:32] if sha else None,
                "bytes": document.get("bytes") or 0,
                "county": record.get("county"),
                "system_type": record.get("septicSystemType"),
                "construction_type": record.get("constructionType"),
                "csv_flow_rate": record.get("flowRate"),
                "csv_perk_rate": record.get("perkRate"),
                "csv_prop_use": record.get("propUse"),
            })

    def sort_key(d: dict) -> tuple[int, str, int]:
        number = d["permit_number"]
        return (
            int(number) if number.isdigit() else 10**9, number, d["doc_index"]
        )

    found.sort(key=sort_key)
    return found


def cache_index(client: TextractClient) -> dict[str, str]:
    """Map S3 key to document hash for everything already in the cache.

    The manifest is missing a sha256 for 49 documents, so those rows cannot
    address the cache by content. The cache can address itself: the file name is
    the hash and the first bytes of each file carry the s3_key, so the index is
    built by reading a few hundred bytes per file rather than the several
    megabytes of blocks behind them.

    This is how a document analysed before a hash was known stays usable without
    downloading it again, and it is read only, so it cannot mis-key anything.
    """
    index: dict[str, str] = {}
    for path in sorted(client.cache_dir.glob("sha256-*.json")):
        doc_hash = path.stem.split("sha256-", 1)[-1]
        if not doc_hash:
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                head = handle.read(400)
        except OSError:
            continue
        match = re.search(r'"s3_key"\s*:\s*"([^"]+)"', head)
        if match:
            index.setdefault(match.group(1), doc_hash)
    return index


def resolve_hash(client: TextractClient, row: dict) -> str | None:
    """The document hash for a manifest row that has none, from the object bytes.

    Needs S3 but no Textract. Returns None and says so rather than inventing a
    key, because a wrong cache key is worse than a missing one: it reads one
    packet's document as another packet's evidence.
    """
    if row.get("document_hash"):
        return row["document_hash"]
    try:
        body = client.s3.get_object(
            Bucket=client.bucket, Key=row["s3_key"]
        )["Body"].read()
    except Exception as exc:  # noqa: BLE001
        print(f"  cannot hash {row['s3_key']}: {exc}")
        return None
    row["document_hash"] = document_hash(body)
    row["bytes"] = row["bytes"] or len(body)
    return row["document_hash"]


def in_range(row: dict, low: int | None, high: int | None) -> bool:
    number = row["permit_number"]
    if not number.isdigit():
        return low is None and high is None
    value = int(number)
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


# ---------------------------------------------------------------------------
# Textract, submitted in parallel and polled
# ---------------------------------------------------------------------------

def submit(client: TextractClient, s3_key: str) -> str | None:
    """Start one analysis, retrying a throttle. Returns a job id or None."""
    for attempt, wait in enumerate((0,) + SUBMIT_RETRY_SECONDS):
        if wait:
            time.sleep(wait)
        try:
            job = client.client.start_document_analysis(
                DocumentLocation={
                    "S3Object": {"Bucket": client.bucket, "Name": s3_key}
                },
                FeatureTypes=list(DEFAULT_FEATURES),
            )
            return job["JobId"]
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            throttled = any(
                word in name
                for word in ("Throttling", "ProvisionedThroughput", "LimitExceeded")
            )
            if not throttled or attempt == len(SUBMIT_RETRY_SECONDS):
                print(f"    submit failed: {name}: {exc}")
                return None
            print(f"    throttled, retrying in {SUBMIT_RETRY_SECONDS[attempt]}s")
    return None


def analyse_batch(client: TextractClient, batch: list[dict], budget: int,
                  concurrency: int, timeout: int) -> tuple[
                      dict[str, Analysis], int, bool]:
    """Run Textract over a batch of documents, respecting the page budget.

    Returns the analyses keyed by document hash, the pages spent, and whether the
    budget stopped the run before the batch was finished.
    """
    pending = list(batch)
    in_flight: dict[str, dict] = {}
    started_at: dict[str, float] = {}
    done: dict[str, Analysis] = {}
    average_pages = float(ASSUMED_PAGES)
    completed_pages: list[int] = []
    pages_used = 0
    stopped = False

    while pending or in_flight:
        while pending and len(in_flight) < concurrency and not stopped:
            projected = pages_used + int(average_pages * len(in_flight))
            if projected >= budget:
                stopped = True
                print(f"  page budget reached at {pages_used} pages spent and "
                      f"{len(in_flight)} jobs in flight, submitting no more")
                break
            row = pending.pop(0)
            if not row.get("document_hash"):
                # Belt and braces. Writing an analysis under a shared key made 47
                # permits read one document, so this is a hard stop, not a warning.
                row["error"] = "no document hash, refusing to submit"
                print(f"  skipping permit {row['permit_number']} {row['doc_name']}: "
                      f"{row['error']}")
                continue
            job_id = submit(client, row["s3_key"])
            if job_id is None:
                row["error"] = "submit failed"
                continue
            in_flight[job_id] = row
            started_at[job_id] = time.time()
            print(f"  submitted permit {row['permit_number']} "
                  f"{row['doc_name']}  job {job_id[:12]}")

        if not in_flight:
            break

        time.sleep(POLL_SECONDS)
        for job_id in list(in_flight):
            row = in_flight[job_id]
            try:
                response = client.client.get_document_analysis(
                    JobId=job_id, MaxResults=1
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  poll failed for permit {row['permit_number']}: {exc}")
                continue
            status = response.get("JobStatus", "")
            if status == "IN_PROGRESS":
                if time.time() - started_at[job_id] > timeout:
                    in_flight.pop(job_id)
                    row["error"] = f"still running after {timeout}s, abandoned"
                    print(f"  permit {row['permit_number']}: {row['error']}")
                continue
            in_flight.pop(job_id)
            pages = response.get("DocumentMetadata", {}).get("Pages", 0)
            message = response.get("StatusMessage")
            if status not in ("SUCCEEDED", "PARTIAL_SUCCESS"):
                row["error"] = f"{status} {message or ''}".strip()
                print(f"  permit {row['permit_number']} failed: {row['error']}")
                continue
            blocks = list(client._collect(job_id))  # noqa: SLF001
            analysis = Analysis(
                s3_key=row["s3_key"], job_id=job_id, status=status, pages=pages,
                blocks=blocks, message=message,
            )
            client.save_to_hash_cache(analysis, row["document_hash"])
            done[row["document_hash"]] = analysis
            pages_used += pages
            completed_pages.append(pages)
            average_pages = sum(completed_pages) / len(completed_pages)
            print(f"  permit {row['permit_number']} {row['doc_name']}  "
                  f"{pages} pages  {len(analysis.blocks)} blocks  "
                  f"running total {pages_used} pages")

    if pending:
        stopped = True
    return done, pages_used, stopped


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def read_document(row: dict, analysis: Analysis) -> dict:
    """Extract one document's facts and record what it gave up."""
    document = layout.parse_blocks(analysis.blocks)
    extraction = extract_facts(document)
    return {
        "doc_index": row["doc_index"],
        "doc_name": row["doc_name"],
        "doctype": row["doctype"],
        "s3_key": row["s3_key"],
        "document_hash": row["document_hash"],
        "pages": analysis.pages or document.pages,
        "form_fields": len(document.fields),
        "facts": extraction.facts,
        # The full provenance of every fact, so a failure can be checked against
        # the page it came from without rerunning anything. A finding a reviewer
        # cannot spot check is a finding they have to take on trust, and the first
        # survey produced 63 of those.
        "provenance": {
            name: {
                "source": fact.source,
                "field_label": fact.label,
                "page": fact.page,
                "confidence": (
                    round(fact.confidence, 1) if fact.confidence is not None
                    else None
                ),
                "raw": (fact.raw or "")[:200],
                "where": fact.describe(),
            }
            for name, fact in extraction.provenance.items()
        },
        "rejected_readings": extraction.rejected,
    }


def survey_packet(permit_number: str, rows: list[dict],
                  analyses: dict[str, Analysis]) -> dict:
    """Evaluate one packet from the merged facts of all its documents.

    Facts merge with the first document that states a value winning, and any
    disagreement between documents is recorded rather than resolved quietly, so a
    reviewer can see that two files in one packet said different things. The
    verdict comes from engine.evaluate and from nothing else here.
    """
    reference = rows[0]
    read: list[dict] = []
    missing: list[dict] = []
    for row in rows:
        analysis = analyses.get(row["document_hash"])
        if analysis is None:
            missing.append({
                "doc_index": row["doc_index"],
                "doc_name": row["doc_name"],
                "s3_key": row["s3_key"],
                "error": row.get("error", "no cached analysis"),
            })
            continue
        read.append(read_document(row, analysis))

    facts: dict[str, Any] = {}
    fact_source: dict[str, str] = {}
    conflicts: list[dict] = []
    for document in read:
        for name, value in document["facts"].items():
            if name not in facts:
                facts[name] = value
                fact_source[name] = document["doc_name"]
                continue
            if facts[name] != value:
                conflicts.append({
                    "parameter": name,
                    "kept": facts[name],
                    "kept_from": fact_source[name],
                    "discarded": value,
                    "discarded_from": document["doc_name"],
                })

    report = engine.evaluate(facts)
    provenance: dict[str, dict] = {}
    for document in read:
        for name, detail in document["provenance"].items():
            if name not in provenance:
                provenance[name] = dict(detail, doc_name=document["doc_name"])

    def spot_check(parameter: str) -> dict:
        """What a reviewer needs in order to disagree with a finding."""
        detail = provenance.get(parameter) or {}
        return {
            "source": detail.get("source"),
            "field_label": detail.get("field_label"),
            "document": detail.get("doc_name"),
            "page": detail.get("page"),
            "confidence": detail.get("confidence"),
            "raw": detail.get("raw"),
        }

    return {
        "permit_number": permit_number,
        "detail_id": reference["detail_id"],
        "doc_count": reference["doc_count"],
        "documents_read": len(read),
        "documents_missing": missing,
        "county": reference["county"],
        "system_type": reference["system_type"],
        "construction_type": reference["construction_type"],
        "csv_flow_rate": reference["csv_flow_rate"],
        "csv_perk_rate": reference["csv_perk_rate"],
        "csv_prop_use": reference["csv_prop_use"],
        "pages": sum(d["pages"] for d in read),
        "form_fields": sum(d["form_fields"] for d in read),
        "status": "surveyed" if read else "not analysed",
        "verdict": report.verdict.value,
        "coverage": report.coverage(),
        "counts": report.counts(),
        "failed_rules": [e.rule.id for e in report.failures],
        "failures": [
            dict(
                {
                    "rule_id": e.rule.id,
                    "reason": e.reason,
                    "observed": e.observed,
                    "threshold": e.rule.threshold,
                    "units": e.rule.units,
                    "severity": e.rule.severity.value,
                    "citation": e.rule.citation.short(),
                    "parameter": e.rule.parameter,
                    "stated_in": fact_source.get(e.rule.parameter),
                },
                **spot_check(e.rule.parameter),
            )
            for e in report.failures
        ],
        "passed_rules": [e.rule.id for e in report.satisfied],
        "not_applicable_rules": [e.rule.id for e in report.not_applicable],
        "unknown_rules": [e.rule.id for e in report.unknowns],
        "unknown_parameters": sorted({
            e.rule.parameter for e in report.unknowns
            if e.rule.parameter not in facts
        }),
        "facts": facts,
        "fact_source": fact_source,
        "fact_provenance": provenance,
        "conflicts": conflicts,
        "documents": [
            {
                "doc_index": d["doc_index"],
                "doc_name": d["doc_name"],
                "doctype": d["doctype"],
                "document_hash": d["document_hash"],
                "pages": d["pages"],
                "form_fields": d["form_fields"],
                "facts_read": sorted(d["facts"]),
                "rejected_readings": d["rejected_readings"],
            }
            for d in read
        ],
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def shard_path(tag: str) -> Path:
    return config.OUT_DIR / f"packet_survey.{tag}.json"


def merge(tags: list[str] | None = None) -> dict:
    """Combine every shard file into one survey payload."""
    if tags:
        files = [shard_path(t) for t in tags]
    else:
        files = sorted(config.OUT_DIR.glob("packet_survey.*.json"))
    rows: dict[str, dict] = {}
    pages = 0
    shards = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"skipping {path.name}: {exc}")
            continue
        if "packets" not in payload:
            continue
        for row in payload["packets"]:
            existing = rows.get(row["permit_number"])
            # A packet surveyed in one shard beats a placeholder from another.
            if existing is None or (
                existing.get("status") != "surveyed"
                and row.get("status") == "surveyed"
            ):
                rows[row["permit_number"]] = row
        pages += payload.get("pages_analysed", 0)
        shards.append({
            "tag": payload.get("tag"),
            "range": payload.get("range"),
            "packets": len(payload["packets"]),
            "pages_analysed": payload.get("pages_analysed", 0),
            "stopped_on_budget": payload.get("stopped_on_budget", False),
        })

    ordered = sorted(
        rows.values(),
        key=lambda r: (int(r["permit_number"]) if r["permit_number"].isdigit()
                       else 10**9),
    )
    return {
        "shards": shards,
        "pages_analysed": pages,
        "packets": ordered,
        "summary": summarise(ordered),
    }


def summarise(rows: list[dict]) -> dict:
    surveyed = [r for r in rows if r.get("status") == "surveyed"]
    by_verdict: dict[str, int] = {}
    blocked: dict[str, int] = {}
    out_of_scope: dict[str, int] = {}
    failed: dict[str, int] = {}
    parameters: dict[str, int] = {}
    for row in surveyed:
        by_verdict[row["verdict"]] = by_verdict.get(row["verdict"], 0) + 1
        for rule_id in row.get("unknown_rules", []):
            blocked[rule_id] = blocked.get(rule_id, 0) + 1
        for rule_id in row.get("not_applicable_rules", []):
            out_of_scope[rule_id] = out_of_scope.get(rule_id, 0) + 1
        for rule_id in row.get("failed_rules", []):
            failed[rule_id] = failed.get(rule_id, 0) + 1
        for parameter in row.get("unknown_parameters", []):
            parameters[parameter] = parameters.get(parameter, 0) + 1
    coverages = [r["coverage"]["evaluated"] for r in surveyed]
    not_applicable = [r["coverage"].get("not_applicable", 0) for r in surveyed]
    unreadable = [r["coverage"].get("unreadable", 0) for r in surveyed]

    def mean(values: list[int]) -> float:
        return round(sum(values) / len(values), 2) if values else 0

    return {
        "packets_total": len(rows),
        "packets_surveyed": len(surveyed),
        "documents_read": sum(r.get("documents_read", 0) for r in surveyed),
        "by_verdict": dict(sorted(by_verdict.items())),
        "failures_by_rule": dict(sorted(failed.items(), key=lambda kv: -kv[1])),
        "unknowns_by_rule": dict(sorted(blocked.items(), key=lambda kv: -kv[1])),
        "not_applicable_by_rule": dict(
            sorted(out_of_scope.items(), key=lambda kv: -kv[1])
        ),
        "unknown_parameters": dict(
            sorted(parameters.items(), key=lambda kv: -kv[1])
        ),
        "coverage_best": max(coverages) if coverages else 0,
        "coverage_worst": min(coverages) if coverages else 0,
        "coverage_mean": mean(coverages),
        "not_applicable_mean": mean(not_applicable),
        "unreadable_mean": mean(unreadable),
        "packets_with_conflicts": sum(1 for r in surveyed if r.get("conflicts")),
        "pages": sum(r.get("pages") or 0 for r in surveyed),
    }


def render(payload: dict) -> str:
    """The readable survey. One line per packet, then what it adds up to."""
    rows = payload.get("packets", [])
    summary = payload.get("summary") or summarise(rows)
    surveyed = [r for r in rows if r.get("status") == "surveyed"]
    total_rules = surveyed[0]["coverage"]["total"] if surveyed else 15

    lines: list[str] = []
    add = lines.append
    bar = "=" * 100

    add(bar)
    add("PACKET SURVEY")
    add(bar)
    add("")
    add(f"packets carrying a document   {summary['packets_total']}")
    add(f"packets surveyed              {summary['packets_surveyed']}")
    add(f"documents read                {summary['documents_read']}")
    add(f"pages through Textract        {payload.get('pages_analysed', 0)} "
        f"in this run")
    add(f"pages in surveyed packets     {summary['pages']}")
    add("")
    for verdict, count in summary["by_verdict"].items():
        add(f"  {verdict:<24}{count}")
    add("")
    add(f"checks that ran      best {summary['coverage_best']} of {total_rules}, "
        f"worst {summary['coverage_worst']} of {total_rules}, "
        f"mean {summary['coverage_mean']} of {total_rules}")
    add(f"not applicable      mean {summary.get('not_applicable_mean', 0)} "
        f"of {total_rules}")
    add(f"could not be read   mean {summary.get('unreadable_mean', 0)} "
        f"of {total_rules}")
    add("")
    add("A check that ran compared a value off the packet against a threshold. A")
    add("rule that does not govern the system on the packet was never applied to")
    add("it, and is counted on its own line rather than as a check that ran.")
    add("")

    add(bar)
    add("PER PACKET")
    add(bar)
    add(f"{'permit':<9}{'docs':>5}{'pages':>6}  {'verdict':<22}"
        f"{'ran':>5}{'fail':>5}{'n/a':>5}{'unrd':>6}  failures")
    add("-" * 100)
    for row in surveyed:
        coverage = row.get("coverage") or {}
        add(f"{row['permit_number']:<9}{row.get('documents_read', 0):>5}"
            f"{row.get('pages', 0):>6}  {row['verdict']:<22}"
            f"{coverage.get('evaluated', 0):>5}{row['counts']['fail']:>5}"
            f"{coverage.get('not_applicable', 0):>5}"
            f"{coverage.get('unreadable', 0):>6}  "
            f"{', '.join(row['failed_rules'])}")
    add("")

    not_surveyed = [r for r in rows if r.get("status") != "surveyed"]
    if not_surveyed:
        add(bar)
        add(f"NOT ANALYSED ({len(not_surveyed)})")
        add(bar)
        for row in not_surveyed[:30]:
            reason = ""
            if row.get("documents_missing"):
                reason = row["documents_missing"][0].get("error", "")
            add(f"  {row['permit_number']:<9}{row.get('status', ''):<16}{reason}")
        if len(not_surveyed) > 30:
            add(f"  and {len(not_surveyed) - 30} more")
        add("")

    add(bar)
    add("FAILURES BY RULE")
    add(bar)
    if summary["failures_by_rule"]:
        for rule_id, count in summary["failures_by_rule"].items():
            add(f"  {count:>4} packets  {rule_id}")
        add("")
        add(bar)
        add("EVERY FAILURE, WITH WHAT A REVIEWER NEEDS TO CHECK IT")
        add(bar)
        add("")
        add("Rule, the value read, the exact field label it came from, the")
        add("document, the page, and Textract's confidence in that field. These")
        add("are approved permits, so a finding a reviewer would not agree with on")
        add("the cited page is a reading error and has to be treated as one.")
        for row in surveyed:
            for failure in row.get("failures", []):
                add("")
                add(f"  permit {row['permit_number']}  {failure['rule_id']}  "
                    f"severity {failure['severity']}")
                add(f"    {failure['reason']}")
                add(f"    read {failure['parameter']} as {failure['observed']!r}, "
                    f"threshold {failure['threshold']} {failure['units'] or ''}")
                add(f"    source      {failure.get('source')}")
                add(f"    field label {failure.get('field_label')!r}")
                add(f"    document    {failure.get('document')}  "
                    f"page {failure.get('page')}  "
                    f"confidence {failure.get('confidence')}")
                add(f"    field value {(failure.get('raw') or '')[:90]!r}")
                add(f"    citation    {failure['citation']}")
        add("")
    else:
        add("  none. No rule failed on any packet surveyed. That is a finding")
        add("  about the corpus, not a gap in the search: these are approved")
        add("  permits, so the packet on file is the corrected one.")
        add("")

    add(bar)
    add("UNKNOWNS BY RULE, MOST PACKETS BLOCKED FIRST")
    add(bar)
    for rule_id, count in summary["unknowns_by_rule"].items():
        share = count / len(surveyed) * 100 if surveyed else 0
        add(f"  {count:>4} packets  {share:>5.1f}%  {rule_id}")
    add("")

    add(bar)
    add("NOT APPLICABLE BY RULE, RULES THAT NEVER RAN ON MOST PACKETS")
    add(bar)
    add("")
    add("These are not gaps. The rule governs a kind of system the packet is not,")
    add("so it was never applied. They are listed because they used to be counted")
    add("as checks that ran and as requirements met, which overstated coverage.")
    add("")
    for rule_id, count in (summary.get("not_applicable_by_rule") or {}).items():
        share = count / len(surveyed) * 100 if surveyed else 0
        add(f"  {count:>4} packets  {share:>5.1f}%  {rule_id}")
    add("")

    add(bar)
    add("PARAMETERS THE RULES WANTED AND NO DOCUMENT SUPPLIED")
    add(bar)
    for parameter, count in summary["unknown_parameters"].items():
        share = count / len(surveyed) * 100 if surveyed else 0
        add(f"  {count:>4} packets  {share:>5.1f}%  {parameter}")
    add("")

    conflicted = [r for r in surveyed if r.get("conflicts")]
    if conflicted:
        add(bar)
        add(f"DOCUMENTS IN ONE PACKET THAT DISAGREED ({len(conflicted)} packets)")
        add(bar)
        for row in conflicted[:20]:
            for conflict in row["conflicts"]:
                add(f"  permit {row['permit_number']}  {conflict['parameter']}: "
                    f"kept {conflict['kept']!r} from {conflict['kept_from']}, "
                    f"discarded {conflict['discarded']!r} from "
                    f"{conflict['discarded_from']}")
        add("")

    add(bar)
    add("SPOT CHECK, THE HIGHEST COVERAGE PACKETS")
    add(bar)
    add("")
    add("Every value behind a rule that reached a decision, with the field label")
    add("and page it was read from. With no failures left, this is what there is")
    add("to disagree with: open the page and see whether the field says this.")
    for row in sorted(surveyed, key=lambda r: -r["coverage"]["evaluated"])[:3]:
        add("")
        add(f"  permit {row['permit_number']}  {row['coverage']['text']}  "
            f"{row['verdict']}  ({row.get('documents_read')} of "
            f"{row.get('doc_count')} documents read, {row.get('pages')} pages)")
        for name, value in sorted(row.get("facts", {}).items()):
            detail = (row.get("fact_provenance") or {}).get(name) or {}
            if detail.get("source") == "form_field":
                where = (f"field {detail.get('field_label')!r} "
                         f"page {detail.get('page')} "
                         f"confidence {detail.get('confidence')}")
            elif detail.get("source") == "text_pattern":
                where = f"text on page {detail.get('page')}"
            else:
                where = detail.get("source") or "derived"
            add(f"    {name:<34}{str(value):<12}{where}")
    add("")

    add(bar)
    add("DEMO CANDIDATES")
    add(bar)
    add("")
    with_failures = [r for r in surveyed if r["failed_rules"]]
    if with_failures:
        add("A real deficiency, highest coverage first:")
        for row in sorted(with_failures,
                          key=lambda r: -r["coverage"]["evaluated"])[:5]:
            add(f"  permit {row['permit_number']}  {row['coverage']['text']}  "
                f"failed {', '.join(row['failed_rules'])}")
    else:
        add("No packet surveyed failed any rule.")
    add("")
    if surveyed:
        add("Highest coverage, for NO DEFICIENCIES FOUND:")
        for row in sorted(surveyed,
                          key=lambda r: -r["coverage"]["evaluated"])[:5]:
            add(f"  permit {row['permit_number']}  {row['coverage']['text']}  "
                f"{row['verdict']}")
        add("")
        add("Lowest coverage:")
        for row in sorted(surveyed, key=lambda r: r["coverage"]["evaluated"])[:5]:
            add(f"  permit {row['permit_number']}  {row['coverage']['text']}  "
                f"{row['verdict']}")
        add("")

    add(bar)
    add("This survey reports what the rules said about packets in permit number")
    add("order. No packet was chosen, edited or excluded for what it would show.")
    add(bar)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="survey_packets")
    ap.add_argument("--manifest", type=Path,
                    default=config.OUT_DIR / "manifest_control.jsonl")
    ap.add_argument("--permits-from", type=int, default=None,
                    help="lowest permit number in this shard, inclusive")
    ap.add_argument("--permits-to", type=int, default=None,
                    help="highest permit number in this shard, inclusive")
    ap.add_argument("--tag", default="all",
                    help="shard name, used for out/packet_survey.<tag>.json")
    ap.add_argument("--page-budget", type=int, default=2000,
                    help="stop submitting once this many pages are spent")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="Textract jobs in flight at once")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit", type=int, default=None,
                    help="analyse at most this many uncached documents")
    ap.add_argument("--cached-only", action="store_true",
                    help="survey what is already cached and call no AWS service")
    ap.add_argument("--list", action="store_true",
                    help="show the shard and what is cached, then stop")
    ap.add_argument("--merge", action="store_true",
                    help="combine the shard files into the final survey")
    args = ap.parse_args(argv)

    config.ensure_dirs()

    if args.merge:
        payload = merge()
        json_path = config.OUT_DIR / "packet_survey.json"
        text_path = config.OUT_DIR / "packet_survey.txt"
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        text = render(payload)
        text_path.write_text(text, encoding="utf-8")
        print(text)
        print(f"\nwrote {json_path}")
        print(f"wrote {text_path}")
        return 0

    if not args.manifest.exists():
        print(f"manifest not found: {args.manifest}")
        return 1

    everything = documents(args.manifest)
    shard = [d for d in everything if in_range(d, args.permits_from, args.permits_to)]
    client = TextractClient()

    analyses: dict[str, Analysis] = {}
    uncached: list[dict] = []
    unhashed: list[dict] = []
    by_s3_key = cache_index(client)
    recovered = 0
    for row in shard:
        if not row["document_hash"] and row["s3_key"] in by_s3_key:
            # Analysed before the hash was known. The cache knows its own keys.
            row["document_hash"] = by_s3_key[row["s3_key"]]
            recovered += 1
        if not row["document_hash"] and not args.cached_only:
            # Needs S3 to get a key at all. Free, and never Textract.
            resolve_hash(client, row)
        if not row["document_hash"]:
            row["error"] = (
                "the manifest recorded no sha256 for this document, so it has no "
                "cache key and was not read"
            )
            unhashed.append(row)
            continue
        hit = client.cached_by_hash(row["document_hash"])
        if hit is not None and hit.ok:
            analyses[row["document_hash"]] = hit
        else:
            uncached.append(row)

    permits: dict[str, list[dict]] = defaultdict(list)
    for row in shard:
        permits[row["permit_number"]].append(row)

    label = f"{args.permits_from or 'first'} to {args.permits_to or 'last'}"
    print(f"shard {args.tag}: permits {label}")
    print(f"  {len(everything)} documents across the whole manifest")
    print(f"  {len(shard)} documents in this shard over {len(permits)} permits")
    print(f"  {len(analyses)} already cached, {len(uncached)} to analyse")
    if recovered:
        print(f"  {recovered} keyed from the cache index rather than the manifest")
    if unhashed:
        print(f"  {len(unhashed)} with no sha256 in the manifest, not readable "
              f"without S3")
    print(f"  page budget {args.page_budget}")

    if args.list:
        for row in shard:
            state = "cached" if row["document_hash"] in analyses else "to analyse"
            print(f"  {row['permit_number']:<9}{row['doc_index']}  {state:<12}"
                  f"{row['bytes'] / 1e6:>7.1f} MB  {row['doc_name']}")
        return 0

    pages_used = 0
    stopped = False
    if uncached and not args.cached_only:
        batch = uncached[:args.limit] if args.limit else uncached
        fresh, pages_used, stopped = analyse_batch(
            client, batch, args.page_budget, args.concurrency, args.timeout
        )
        analyses.update(fresh)
        print(f"  analysed {len(fresh)} documents, {pages_used} pages")
        if stopped:
            print("  run stopped early: the page budget was reached")

    rows: list[dict] = []
    for permit_number in sorted(
        permits, key=lambda n: int(n) if n.isdigit() else 10**9
    ):
        try:
            rows.append(
                survey_packet(permit_number, permits[permit_number], analyses)
            )
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "permit_number": permit_number,
                "status": "survey failed",
                "error": f"{type(exc).__name__}: {exc}",
            })

    payload = {
        "tag": args.tag,
        "range": {"from": args.permits_from, "to": args.permits_to},
        "page_budget": args.page_budget,
        "pages_analysed": pages_used,
        "stopped_on_budget": stopped,
        "packets": rows,
        "summary": summarise(rows),
    }
    path = shard_path(args.tag)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")

    summary = payload["summary"]
    print(f"\nshard {args.tag}: {summary['packets_surveyed']} packets surveyed, "
          f"{summary['documents_read']} documents read, "
          f"{pages_used} pages through Textract")
    for verdict, count in summary["by_verdict"].items():
        print(f"  {verdict:<24}{count}")
    if summary["failures_by_rule"]:
        print("  failures by rule:")
        for rule_id, count in summary["failures_by_rule"].items():
            print(f"    {count:>4}  {rule_id}")
    else:
        print("  no rule failed on any packet in this shard")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
