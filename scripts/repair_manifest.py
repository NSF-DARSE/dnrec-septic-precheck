"""Repair a manifest damaged by concurrent writes.

Two harvest processes briefly ran against the same manifest path. cli.py opens
that file with mode "w", so the second process truncated and re-wrote while the
first was still appending. The result is a file with a torn line where two
partial writes met, and duplicate records for permits both processes fetched.

This script does four things and reports each count:

  1. Drops lines that do not parse as JSON. A torn line is not recoverable: the
     bytes of two different records are interleaved, so any attempt to salvage it
     would be inventing data.
  2. Keeps one record per detail_id, preferring the one with the most documents.
     A record written mid-fetch can show zero documents for a permit that
     actually has two, and the higher count is the one that reflects a completed
     fetch. Ties break on the later harvested_at.
  3. Verifies the survivors against the permits select_permits returns, so the
     manifest is checked against the selection that produced it rather than
     against itself.
  4. Lists any selected detail_id with no surviving record, which is the
     re-fetch worklist.

The repaired manifest is written beside the original with a .repaired.jsonl
suffix. The original is never modified, because a repair script that overwrites
its only input has no second attempt.

Usage:
    python scripts/repair_manifest.py
    python scripts/repair_manifest.py --manifest out/manifest_control.jsonl
    python scripts/repair_manifest.py --status Approved --year-min 2014
    python scripts/repair_manifest.py --in-place
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.harvest import csv_index


@dataclass
class TornLine:
    """A line that did not parse, kept for the report rather than discarded silently."""

    line_number: int
    length: int
    error: str
    preview: str


@dataclass
class RepairResult:
    """Everything the repair learned, so the report can be printed from one object."""

    manifest: Path
    total_lines: int = 0
    blank_lines: int = 0
    parsed: int = 0
    torn: list[TornLine] = field(default_factory=list)
    records_without_id: int = 0
    unique_ids: int = 0
    duplicates_collapsed: int = 0
    duplicate_detail: dict[str, list[int]] = field(default_factory=dict)
    documents_recovered: int = 0
    selected_count: int = 0
    missing_ids: list[str] = field(default_factory=list)
    unexpected_ids: list[str] = field(default_factory=list)
    survivors_with_docs: int = 0
    survivors_without_docs: int = 0

    @property
    def survivors(self) -> int:
        return self.unique_ids

    def render(self) -> str:
        lines = [
            "manifest repair",
            "=" * 60,
            f"file                    {self.manifest}",
            "",
            "input",
            f"  lines read            {self.total_lines}",
            f"  blank lines           {self.blank_lines}",
            f"  parsed as JSON        {self.parsed}",
            f"  unparsable dropped    {len(self.torn)}",
        ]
        for t in self.torn:
            lines.append(
                f"    line {t.line_number} ({t.length} bytes): {t.error}"
            )
            lines.append(f"      {t.preview}")
        if self.records_without_id:
            lines.append(f"  records with no detail_id dropped {self.records_without_id}")

        lines += [
            "",
            "deduplication",
            f"  unique detail_id      {self.unique_ids}",
            f"  duplicates collapsed  {self.duplicates_collapsed}",
        ]
        if self.duplicate_detail:
            for detail_id, doc_counts in sorted(self.duplicate_detail.items()):
                kept = max(doc_counts)
                lines.append(
                    f"    {detail_id}: document counts {doc_counts}, kept {kept}"
                )
        if self.documents_recovered:
            lines.append(
                f"  documents recovered   {self.documents_recovered} "
                f"(kept the higher count on a collapsed duplicate)"
            )

        lines += [
            "",
            "verification against select_permits",
            f"  permits selected      {self.selected_count}",
            f"  permits with a record {self.selected_count - len(self.missing_ids)}",
            f"  permits missing       {len(self.missing_ids)}",
        ]
        if self.missing_ids:
            lines.append("  re-fetch worklist:")
            for detail_id in self.missing_ids:
                lines.append(f"    {detail_id}")
        if self.unexpected_ids:
            lines.append(
                f"  records not in selection {len(self.unexpected_ids)}: "
                f"{', '.join(self.unexpected_ids[:10])}"
            )

        lines += [
            "",
            "survivor document coverage",
            f"  with at least one doc {self.survivors_with_docs}",
            f"  with no documents     {self.survivors_without_docs}",
            "",
        ]

        if not self.torn and not self.duplicates_collapsed and not self.missing_ids:
            lines.append("RESULT clean, nothing to repair")
        else:
            lines.append(
                f"RESULT repaired, {self.survivors} records, "
                f"{len(self.torn)} lines dropped, "
                f"{self.duplicates_collapsed} duplicates collapsed, "
                f"{len(self.missing_ids)} permits to re-fetch"
            )
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "manifest": str(self.manifest),
            "total_lines": self.total_lines,
            "blank_lines": self.blank_lines,
            "parsed": self.parsed,
            "lines_dropped": len(self.torn),
            "torn_lines": [
                {
                    "line_number": t.line_number,
                    "length": t.length,
                    "error": t.error,
                }
                for t in self.torn
            ],
            "records_without_id": self.records_without_id,
            "unique_ids": self.unique_ids,
            "duplicates_collapsed": self.duplicates_collapsed,
            "duplicate_detail": self.duplicate_detail,
            "documents_recovered": self.documents_recovered,
            "selected_count": self.selected_count,
            "missing_ids": self.missing_ids,
            "unexpected_ids": self.unexpected_ids,
            "survivors_with_docs": self.survivors_with_docs,
            "survivors_without_docs": self.survivors_without_docs,
        }


def _document_count(record: dict) -> int:
    documents = record.get("documents")
    return len(documents) if isinstance(documents, list) else 0


def _is_better(candidate: dict, incumbent: dict) -> bool:
    """Whether candidate should replace incumbent for the same detail_id.

    Most documents wins, because a record written while a fetch was still in
    flight can show zero documents for a permit that has two. A later
    harvested_at breaks a tie, on the assumption that the later write saw more.
    """
    candidate_docs = _document_count(candidate)
    incumbent_docs = _document_count(incumbent)
    if candidate_docs != incumbent_docs:
        return candidate_docs > incumbent_docs
    return str(candidate.get("harvested_at") or "") > str(
        incumbent.get("harvested_at") or ""
    )


def repair(
    manifest: Path,
    statuses: list[str] | None = None,
    year_min: int | None = config.YEAR_MIN,
) -> tuple[RepairResult, list[dict]]:
    """Repair the manifest and return the result plus the surviving records."""
    result = RepairResult(manifest=manifest)

    if not manifest.exists():
        raise FileNotFoundError(f"manifest not found: {manifest}")

    raw_lines = manifest.read_text(encoding="utf-8", errors="replace").splitlines()
    result.total_lines = len(raw_lines)

    kept: dict[str, dict] = {}
    duplicate_docs: dict[str, list[int]] = {}

    for line_number, line in enumerate(raw_lines, start=1):
        if not line.strip():
            result.blank_lines += 1
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            result.torn.append(
                TornLine(
                    line_number=line_number,
                    length=len(line),
                    error=str(exc),
                    preview=line[:100].replace("\n", " "),
                )
            )
            continue

        result.parsed += 1
        detail_id = record.get("detail_id")
        if not detail_id:
            result.records_without_id += 1
            continue
        detail_id = str(detail_id)

        if detail_id in kept:
            result.duplicates_collapsed += 1
            duplicate_docs.setdefault(detail_id, [_document_count(kept[detail_id])])
            duplicate_docs[detail_id].append(_document_count(record))
            if _is_better(record, kept[detail_id]):
                gained = _document_count(record) - _document_count(kept[detail_id])
                if gained > 0:
                    result.documents_recovered += gained
                kept[detail_id] = record
        else:
            kept[detail_id] = record

    result.unique_ids = len(kept)
    result.duplicate_detail = duplicate_docs

    # Verify against the selection that produced this manifest, not against the
    # manifest itself. This is the check that catches a permit the harvest never
    # reached at all, which no amount of internal consistency would reveal.
    selection = csv_index.select_permits(statuses=statuses, year_min=year_min)
    result.selected_count = len(selection.rows)
    selected_ids = {str(row["detail_id"]) for row in selection.rows}

    result.missing_ids = sorted(selected_ids - set(kept))
    result.unexpected_ids = sorted(set(kept) - selected_ids)

    for record in kept.values():
        if _document_count(record):
            result.survivors_with_docs += 1
        else:
            result.survivors_without_docs += 1

    # Stable output order so a re-run produces an identical file.
    survivors = [kept[k] for k in sorted(kept)]
    return result, survivors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="repair_manifest")
    ap.add_argument(
        "--manifest",
        type=Path,
        default=config.OUT_DIR / "manifest_control.jsonl",
        help="manifest to repair",
    )
    ap.add_argument(
        "--status",
        nargs="*",
        default=["Approved"],
        help="permitStatus values the manifest was harvested for",
    )
    ap.add_argument("--year-min", type=int, default=config.YEAR_MIN)
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="overwrite the manifest instead of writing a .repaired.jsonl copy",
    )
    args = ap.parse_args(argv)

    config.ensure_dirs()
    result, survivors = repair(
        args.manifest, statuses=args.status, year_min=args.year_min
    )

    if args.in_place:
        target = args.manifest
    else:
        target = args.manifest.with_suffix(".repaired.jsonl")

    with target.open("w", encoding="utf-8") as fh:
        for record in survivors:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    text = result.render()
    print(text)
    print(f"\nwrote {len(survivors)} records to {target}")

    report_txt = config.OUT_DIR / "manifest_repair.txt"
    report_json = config.OUT_DIR / "manifest_repair.json"
    report_txt.write_text(text + f"\n\nwrote {target}\n", encoding="utf-8")
    report_json.write_text(
        json.dumps(result.to_json(), indent=2), encoding="utf-8"
    )

    return 1 if result.missing_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
