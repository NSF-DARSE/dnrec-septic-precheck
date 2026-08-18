"""Tests for the regulation graph.

Covers:
1. CONTAINS hierarchy is acyclic and every child is a proper prefix extension
   of its parent.
2. A known cross-reference resolves to a real node.
3. unresolved() correctly reports an unread dependency.
4. The graph round-trips through JSON without loss.
"""
import json
import tempfile
from pathlib import Path

import networkx as nx
import pytest

from septic.rules.graph import (
    build_graph,
    context,
    graph_summary,
    load_graph,
    orphans,
    save_graph,
    unresolved,
)


@pytest.fixture(scope="module")
def regulation_graph():
    """Build or load the regulation graph once for all tests."""
    graph_path = Path("out/reg_graph.json")
    if graph_path.exists():
        return load_graph(graph_path)
    G, _ = build_graph()
    save_graph(G, graph_path)
    return G


class TestContainsHierarchy:
    """The CONTAINS tree must be acyclic and structurally valid."""

    def test_acyclic(self, regulation_graph):
        """CONTAINS subgraph has no cycles."""
        G = regulation_graph
        contains_edges = [
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("type") == "CONTAINS"
        ]
        subgraph = nx.DiGraph(contains_edges)
        assert nx.is_directed_acyclic_graph(subgraph), "CONTAINS has a cycle"

    def test_child_extends_parent(self, regulation_graph):
        """Every CONTAINS child number is a proper prefix extension of parent."""
        G = regulation_graph
        violations = []
        for parent_id, child_id, d in G.edges(data=True):
            if d.get("type") != "CONTAINS":
                continue
            parent_data = G.nodes[parent_id]
            child_data = G.nodes[child_id]
            parent_num = parent_data.get("number", "")
            child_num = child_data.get("number", "")
            if not parent_num or not child_num:
                continue
            # Child must start with parent number followed by a dot
            if not child_num.startswith(parent_num + "."):
                violations.append((parent_num, child_num))

        assert not violations, (
            f"Found {len(violations)} children that are not prefix extensions: "
            f"{violations[:5]}"
        )

    def test_contains_has_expected_count(self, regulation_graph):
        """CONTAINS edges should be in reasonable range."""
        G = regulation_graph
        count = sum(
            1 for _, _, d in G.edges(data=True) if d.get("type") == "CONTAINS"
        )
        # Should have at least 1000 parent-child relationships
        assert count > 1000, f"Only {count} CONTAINS edges, expected > 1000"


class TestCrossReferences:
    """Cross-references should resolve to real nodes."""

    def test_known_exhibit_exists(self, regulation_graph):
        """Exhibit C (Isolation Distances table) should exist as a node."""
        G = regulation_graph
        assert "exhibit:C" in G, "Exhibit C not found in graph"

    def test_known_section_reference_resolves(self, regulation_graph):
        """Section 5.3.4.1 references Exhibit C and that edge exists."""
        G = regulation_graph
        node_id = "section:5.3.4.1"
        if node_id not in G:
            pytest.skip("Section 5.3.4.1 not in graph")

        # Check outgoing REFERENCES edges
        ref_targets = [
            target for _, target, d in G.out_edges(node_id, data=True)
            if d.get("type") == "REFERENCES"
        ]
        assert "exhibit:C" in ref_targets, (
            f"5.3.4.1 should reference Exhibit C. "
            f"Found references to: {ref_targets}"
        )

    def test_references_point_to_existing_nodes(self, regulation_graph):
        """Every REFERENCES edge target exists in the graph."""
        G = regulation_graph
        for src, tgt, d in G.edges(data=True):
            if d.get("type") == "REFERENCES":
                assert tgt in G, f"REFERENCES target {tgt} not in graph"


class TestUnresolved:
    """unresolved() must report dependencies with empty text."""

    def test_unresolved_with_empty_exhibit(self, regulation_graph):
        """Exhibits have no text extracted, so any rule citing a section
        that references an exhibit should report it as unresolved."""
        G = regulation_graph

        # Find a section that references an exhibit
        for node_id, attrs in G.nodes(data=True):
            if attrs.get("type") != "Section":
                continue
            for _, target, d in G.out_edges(node_id, data=True):
                if d.get("type") == "REFERENCES" and target.startswith("exhibit:"):
                    # Create a temporary rule node that cites this section
                    test_rule_id = "TEST-unresolved-check"
                    G.add_node(
                        f"rule:{test_rule_id}",
                        type="Rule",
                        rule_id=test_rule_id,
                    )
                    G.add_edge(
                        f"rule:{test_rule_id}", node_id, type="CITES"
                    )

                    result = unresolved(G, test_rule_id)

                    # Clean up
                    G.remove_node(f"rule:{test_rule_id}")

                    assert result["unresolved_count"] > 0, (
                        f"Expected unresolved exhibits for rule citing {node_id}"
                    )
                    exhibit_ids = [
                        u["id"] for u in result["unresolved"]
                    ]
                    assert any(
                        eid.startswith("exhibit:") for eid in exhibit_ids
                    ), f"Expected an exhibit in unresolved: {exhibit_ids}"
                    return

        pytest.skip("No section referencing an exhibit found")

    def test_unresolved_existing_rules_have_section_tbd(self, regulation_graph):
        """Existing placeholder rules cite 'TBD' so unresolved returns 0."""
        G = regulation_graph
        result = unresolved(G, "EX001-site-plan-present")
        # TBD section does not exist in graph, so CITES edge was never created
        assert result["unresolved_count"] == 0


class TestJsonRoundTrip:
    """The graph must survive serialization without loss."""

    def test_roundtrip_preserves_counts(self, regulation_graph):
        """Node and edge counts are identical after JSON round-trip."""
        G = regulation_graph
        original_nodes = G.number_of_nodes()
        original_edges = G.number_of_edges()

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as f:
            tmp_path = Path(f.name)

        try:
            save_graph(G, tmp_path)
            G2 = load_graph(tmp_path)
            assert G2.number_of_nodes() == original_nodes
            assert G2.number_of_edges() == original_edges
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_roundtrip_preserves_attributes(self, regulation_graph):
        """Node attributes survive round-trip."""
        G = regulation_graph

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as f:
            tmp_path = Path(f.name)

        try:
            save_graph(G, tmp_path)
            G2 = load_graph(tmp_path)

            # Check a known section
            for node_id in G.nodes():
                if G.nodes[node_id].get("type") == "Section":
                    original = G.nodes[node_id]
                    loaded = G2.nodes[node_id]
                    assert original["number"] == loaded["number"]
                    assert original["title"] == loaded["title"]
                    assert original["page"] == loaded["page"]
                    break
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_roundtrip_preserves_edge_types(self, regulation_graph):
        """Edge types survive round-trip."""
        G = regulation_graph

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as f:
            tmp_path = Path(f.name)

        try:
            save_graph(G, tmp_path)
            G2 = load_graph(tmp_path)

            original_types = set(
                d.get("type") for _, _, d in G.edges(data=True)
            )
            loaded_types = set(
                d.get("type") for _, _, d in G2.edges(data=True)
            )
            assert original_types == loaded_types
        finally:
            tmp_path.unlink(missing_ok=True)


class TestOrphans:
    """orphans() must return sections with obligation language."""

    def test_orphans_returns_list(self, regulation_graph):
        """orphans() returns a non-empty list."""
        G = regulation_graph
        result = orphans(G)
        assert isinstance(result, list)
        assert len(result) > 0, "Expected at least some orphan sections"

    def test_orphans_have_obligation_language(self, regulation_graph):
        """Each orphan section must contain obligation language."""
        import re
        G = regulation_graph
        OBLIGATION_RE = re.compile(
            r"\b(shall|must|may\s+not|shall\s+not|is\s+required|"
            r"are\s+required|minimum|maximum|no\s+less\s+than|"
            r"no\s+more\s+than|at\s+least|not\s+exceed|prohibited)\b",
            re.IGNORECASE,
        )
        result = orphans(G)
        for item in result[:20]:  # spot check first 20
            section_id = f"section:{item['section']}"
            if section_id in G:
                text = G.nodes[section_id].get("text", "")
                assert OBLIGATION_RE.search(text), (
                    f"Orphan {item['section']} has no obligation language in text"
                )


class TestContext:
    """context() must return complete information."""

    def test_context_returns_all_fields(self, regulation_graph):
        """context() response has all expected keys."""
        G = regulation_graph
        # Find a section that exists
        for node_id, attrs in G.nodes(data=True):
            if attrs.get("type") == "Section" and attrs.get("text"):
                number = attrs["number"]
                result = context(G, number)
                assert "section" in result
                assert "title" in result
                assert "page" in result
                assert "text" in result
                assert "ancestors" in result
                assert "references" in result
                assert "definitions" in result
                assert "exceptions" in result
                return
        pytest.skip("No section with text found")

    def test_context_nonexistent_section(self, regulation_graph):
        """context() with invalid section returns error."""
        G = regulation_graph
        result = context(G, "999.999.999")
        assert "error" in result
