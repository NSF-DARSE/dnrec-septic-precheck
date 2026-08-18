"""Tests for manifest repair.

The dedup preference is the part worth protecting. If it picks the wrong record
for a duplicated detail_id it silently discards documents that were successfully
harvested, and nothing downstream would report a problem: the manifest would look
internally consistent and just be missing files.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import repair_manifest  # noqa: E402


def _record(detail_id, docs=0, harvested_at="2026-08-17T01:00:00Z", **extra):
    return {
        "detail_id": detail_id,
        "documents": [{"key": f"doc{i}.pdf"} for i in range(docs)],
        "harvested_at": harvested_at,
        **extra,
    }


def _write_manifest(path, records_or_lines):
    with path.open("w", encoding="utf-8") as fh:
        for item in records_or_lines:
            if isinstance(item, str):
                fh.write(item + "\n")
            else:
                fh.write(json.dumps(item) + "\n")


class TestDocumentPreference:
    """A collapsed duplicate must keep the record with the most documents."""

    def test_prefers_more_documents(self):
        fewer = _record("111", docs=0)
        more = _record("111", docs=2)
        assert repair_manifest._is_better(more, fewer)
        assert not repair_manifest._is_better(fewer, more)

    def test_document_count_handles_missing_key(self):
        assert repair_manifest._document_count({}) == 0
        assert repair_manifest._document_count({"documents": None}) == 0
        assert repair_manifest._document_count({"documents": []}) == 0
        assert repair_manifest._document_count({"documents": [1, 2, 3]}) == 3

    def test_tie_breaks_on_later_timestamp(self):
        earlier = _record("111", docs=1, harvested_at="2026-08-17T01:00:00Z")
        later = _record("111", docs=1, harvested_at="2026-08-17T02:00:00Z")
        assert repair_manifest._is_better(later, earlier)
        assert not repair_manifest._is_better(earlier, later)

    def test_document_count_outranks_timestamp(self):
        """A later write with fewer documents must not win.

        This is the concurrent write case: the second process truncated the file
        and re-wrote a record mid-fetch, so it is newer but less complete.
        """
        later_but_empty = _record("111", docs=0, harvested_at="2026-08-17T09:00:00Z")
        earlier_complete = _record("111", docs=2, harvested_at="2026-08-17T01:00:00Z")
        assert not repair_manifest._is_better(later_but_empty, earlier_complete)


class TestRepair:
    """End to end repair against a synthetic manifest."""

    @pytest.fixture
    def fake_selection(self, monkeypatch):
        """Stub select_permits so the test does not need the 45 MB CSV."""
        class Selection:
            rows = [{"detail_id": "111"}, {"detail_id": "222"}, {"detail_id": "333"}]

        monkeypatch.setattr(
            repair_manifest.csv_index,
            "select_permits",
            lambda **kwargs: Selection(),
        )
        return Selection

    def test_drops_unparsable_line(self, tmp_path, fake_selection):
        path = tmp_path / "m.jsonl"
        _write_manifest(path, [
            _record("111", docs=1),
            '{"detail_id": "222", "documen',  # torn
            _record("333", docs=1),
        ])
        result, survivors = repair_manifest.repair(path)

        assert len(result.torn) == 1
        assert result.torn[0].line_number == 2
        assert result.parsed == 2
        assert len(survivors) == 2

    def test_collapses_duplicates_keeping_documents(self, tmp_path, fake_selection):
        path = tmp_path / "m.jsonl"
        _write_manifest(path, [
            _record("111", docs=0),
            _record("111", docs=2),
            _record("222", docs=1),
            _record("333", docs=0),
        ])
        result, survivors = repair_manifest.repair(path)

        assert result.duplicates_collapsed == 1
        assert result.unique_ids == 3
        kept = {r["detail_id"]: len(r["documents"]) for r in survivors}
        assert kept["111"] == 2, "collapsing a duplicate lost documents"
        assert result.documents_recovered == 2

    def test_reports_missing_permits_as_refetch_worklist(self, tmp_path, fake_selection):
        path = tmp_path / "m.jsonl"
        _write_manifest(path, [_record("111", docs=1)])
        result, survivors = repair_manifest.repair(path)

        assert result.selected_count == 3
        assert result.missing_ids == ["222", "333"]

    def test_clean_manifest_reports_nothing_to_repair(self, tmp_path, fake_selection):
        path = tmp_path / "m.jsonl"
        _write_manifest(path, [
            _record("111", docs=1),
            _record("222", docs=0),
            _record("333", docs=2),
        ])
        result, survivors = repair_manifest.repair(path)

        assert not result.torn
        assert result.duplicates_collapsed == 0
        assert result.missing_ids == []
        assert "clean" in result.render()

    def test_output_order_is_stable(self, tmp_path, fake_selection):
        path = tmp_path / "m.jsonl"
        _write_manifest(path, [
            _record("333", docs=1),
            _record("111", docs=1),
            _record("222", docs=1),
        ])
        _, first = repair_manifest.repair(path)
        _, second = repair_manifest.repair(path)
        assert [r["detail_id"] for r in first] == ["111", "222", "333"]
        assert first == second

    def test_survivor_document_coverage_counts(self, tmp_path, fake_selection):
        path = tmp_path / "m.jsonl"
        _write_manifest(path, [
            _record("111", docs=1),
            _record("222", docs=0),
            _record("333", docs=0),
        ])
        result, _ = repair_manifest.repair(path)
        assert result.survivors_with_docs == 1
        assert result.survivors_without_docs == 2

    def test_missing_file_raises(self, tmp_path, fake_selection):
        with pytest.raises(FileNotFoundError):
            repair_manifest.repair(tmp_path / "nope.jsonl")
