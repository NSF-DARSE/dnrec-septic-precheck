"""Unit tests for Neptune export and query utilities.

These tests do NOT require AWS access — they test the export logic locally.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.neptune.export import escape_cypher, props_string, export_graph


class TestEscapeCypher:
    def test_none(self):
        assert escape_cypher(None) == "null"

    def test_bool_true(self):
        assert escape_cypher(True) == "true"

    def test_bool_false(self):
        assert escape_cypher(False) == "false"

    def test_integer(self):
        assert escape_cypher(42) == "42"

    def test_float(self):
        assert escape_cypher(3.14) == "3.14"

    def test_simple_string(self):
        assert escape_cypher("hello") == "'hello'"

    def test_string_with_quotes(self):
        assert escape_cypher("it's") == "'it\\'s'"

    def test_string_with_newlines(self):
        assert escape_cypher("line1\nline2") == "'line1\\nline2'"

    def test_string_with_backslash(self):
        assert escape_cypher("a\\b") == "'a\\\\b'"


class TestPropsString:
    def test_empty(self):
        assert props_string({}) == "{}"

    def test_simple(self):
        result = props_string({"name": "test", "count": 5})
        assert "name: 'test'" in result
        assert "count: 5" in result

    def test_none_skipped(self):
        result = props_string({"a": "yes", "b": None})
        assert "a: 'yes'" in result
        assert "b" not in result


class TestExportGraph:
    def test_export_small_graph(self, tmp_path):
        graph_data = {
            "schema_version": 1,
            "node_count": 3,
            "edge_count": 2,
            "nodes": [
                {"id": "section:1.0", "type": "Section", "number": "1.0",
                 "title": "Authority", "page": 2, "text": "Some text"},
                {"id": "section:1.1", "type": "Section", "number": "1.1",
                 "title": "Sub", "page": 3, "text": ""},
                {"id": "rule:R001", "type": "Rule", "rule_id": "R001",
                 "description": "Test rule", "parameter": "x",
                 "operator": ">=", "severity": "return",
                 "verified": True, "citation_section": "1.0",
                 "citation_page": 2},
            ],
            "edges": [
                {"source": "section:1.0", "target": "section:1.1", "type": "CONTAINS"},
                {"source": "rule:R001", "target": "section:1.0", "type": "CITES"},
            ],
        }

        graph_json = tmp_path / "test_graph.json"
        graph_json.write_text(json.dumps(graph_data))

        out_dir = tmp_path / "export"
        nodes, edges = export_graph(graph_json, out_dir)

        assert nodes == 3
        assert edges == 2
        assert (out_dir / "nodes.cypher").exists()
        assert (out_dir / "edges.cypher").exists()

        # Verify node statements use MERGE
        nodes_content = (out_dir / "nodes.cypher").read_text()
        assert "MERGE" in nodes_content
        assert "section:1.0" in nodes_content
        assert "Section" in nodes_content

        # Verify edge statements
        edges_content = (out_dir / "edges.cypher").read_text()
        assert "CONTAINS" in edges_content
        assert "CITES" in edges_content

    def test_export_actual_graph(self):
        """Test export with the real reg_graph.json if available."""
        graph_path = Path("out/reg_graph.json")
        if not graph_path.exists():
            pytest.skip("out/reg_graph.json not found")

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            nodes, edges = export_graph(graph_path, out_dir)
            assert nodes == 2176
            assert edges == 2927

    def test_idempotent_output(self, tmp_path):
        """Same input produces identical output."""
        graph_data = {
            "schema_version": 1,
            "node_count": 1,
            "edge_count": 0,
            "nodes": [
                {"id": "section:1.0", "type": "Section", "number": "1.0",
                 "title": "Test", "page": 1, "text": ""},
            ],
            "edges": [],
        }
        graph_json = tmp_path / "test.json"
        graph_json.write_text(json.dumps(graph_data))

        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        export_graph(graph_json, out1)
        export_graph(graph_json, out2)

        assert (out1 / "nodes.cypher").read_text() == (out2 / "nodes.cypher").read_text()
        assert (out1 / "edges.cypher").read_text() == (out2 / "edges.cypher").read_text()
