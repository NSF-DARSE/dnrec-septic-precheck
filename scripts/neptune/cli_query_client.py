"""Neptune Analytics query client using AWS CLI.

Uses `aws neptune-graph execute-query` instead of boto3, because the project's
pinned boto3 (1.34.162) does not support the `login_session` credential mechanism.

Provides the same interface as query_client.py but shells out to the AWS CLI.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any


REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE", "hackathon")


class NeptuneClient:
    """Query client for Neptune Analytics regulation graph (CLI-based)."""

    def __init__(self, graph_id: str, region: str = REGION, profile: str = PROFILE):
        self.graph_id = graph_id
        self.region = region
        self.profile = profile

    def _query(self, cypher: str) -> dict:
        """Execute an openCypher query via AWS CLI and return parsed results."""
        cmd = [
            "aws", "--profile", self.profile, "--region", self.region,
            "neptune-graph", "execute-query",
            "--graph-identifier", self.graph_id,
            "--query-string", cypher,
            "--language", "OPEN_CYPHER",
            "/dev/stdout",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"Neptune query failed: {result.stderr or result.stdout}")
        return json.loads(result.stdout)

    def graph_summary(self) -> dict[str, Any]:
        """Node and edge counts by type."""
        node_result = self._query(
            "MATCH (n) RETURN labels(n)[0] AS type, count(n) AS cnt"
        )
        edge_result = self._query(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS cnt"
        )

        nodes_by_type = {}
        total_nodes = 0
        for row in node_result.get("results", []):
            t = row.get("type", "unknown")
            c = row.get("cnt", 0)
            nodes_by_type[t] = c
            total_nodes += c

        edges_by_type = {}
        total_edges = 0
        for row in edge_result.get("results", []):
            t = row.get("type", "unknown")
            c = row.get("cnt", 0)
            edges_by_type[t] = c
            total_edges += c

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_type": nodes_by_type,
            "edges_by_type": edges_by_type,
        }

    def context(self, section_number: str) -> dict[str, Any]:
        """Regulatory context for a section."""
        node_id = f"section:{section_number}"

        # Get the section itself
        section_result = self._query(
            f"MATCH (s {{node_id: '{node_id}'}}) "
            f"RETURN s.node_id AS node_id, s.title AS title, s.page AS page, "
            f"s.text AS text, s.number AS number"
        )
        rows = section_result.get("results", [])
        if not rows:
            return {"error": f"section {section_number} not found in graph"}
        section = rows[0]

        # Ancestors via CONTAINS (walk up)
        ancestors_result = self._query(
            f"MATCH path = (ancestor:Section)-[:CONTAINS*]->(s {{node_id: '{node_id}'}}) "
            f"UNWIND nodes(path) AS n "
            f"WITH n WHERE n.node_id <> '{node_id}' "
            f"RETURN DISTINCT n.number AS number, n.title AS title "
            f"ORDER BY n.number"
        )
        ancestors = [
            {"number": r.get("number", ""), "title": r.get("title", "")}
            for r in ancestors_result.get("results", [])
        ]

        # References
        references_result = self._query(
            f"MATCH (s {{node_id: '{node_id}'}})-[:REFERENCES]->(ref) "
            f"RETURN ref.node_id AS id, labels(ref)[0] AS type, "
            f"coalesce(ref.number, ref.letter, '') AS number, "
            f"ref.title AS title, "
            f"substring(coalesce(ref.text, ''), 0, 500) AS text"
        )
        references = [
            {
                "id": r.get("id", ""),
                "type": r.get("type", ""),
                "number": r.get("number", ""),
                "title": r.get("title", ""),
                "text": r.get("text", "")[:500],
            }
            for r in references_result.get("results", [])
        ]

        # Definitions
        definitions_result = self._query(
            f"MATCH (s {{node_id: '{node_id}'}})-[:USES_TERM]->(d:Definition) "
            f"RETURN d.term AS term, d.defined_in AS defined_in"
        )
        definitions = [
            {"term": r.get("term", ""), "defined_in": r.get("defined_in", "")}
            for r in definitions_result.get("results", [])
        ]

        # Exceptions
        exceptions_result = self._query(
            f"MATCH (exc:Section)-[:EXCEPTION]->(s {{node_id: '{node_id}'}}) "
            f"RETURN exc.number AS number, exc.title AS title, "
            f"substring(coalesce(exc.text, ''), 0, 300) AS text"
        )
        exceptions = [
            {
                "number": r.get("number", ""),
                "title": r.get("title", ""),
                "text": r.get("text", "")[:300],
            }
            for r in exceptions_result.get("results", [])
        ]

        return {
            "section": section_number,
            "title": section.get("title", ""),
            "page": section.get("page"),
            "text": section.get("text", ""),
            "ancestors": ancestors,
            "references": references,
            "definitions": definitions,
            "exceptions": exceptions,
        }

    def unresolved(self, rule_id: str) -> dict[str, Any]:
        """Unresolved dependencies for a rule."""
        rule_node_id = f"rule:{rule_id}"

        # Check rule exists
        check_result = self._query(
            f"MATCH (r {{node_id: '{rule_node_id}'}}) RETURN r.node_id AS node_id"
        )
        if not check_result.get("results"):
            return {"error": f"rule {rule_id} not found in graph"}

        # Traverse CITES and REFERENCES, find unreadable deps
        traverse_result = self._query(
            f"MATCH (r {{node_id: '{rule_node_id}'}})-[:CITES|REFERENCES*1..5]->(dep) "
            f"WHERE (dep.text IS NULL OR dep.text = '' OR trim(dep.text) = '') "
            f"  AND NOT (labels(dep)[0] = 'Section' AND size(split(coalesce(dep.title,''), ' ')) >= 4) "
            f"RETURN DISTINCT dep.node_id AS id, labels(dep)[0] AS type, "
            f"coalesce(dep.number, dep.letter, '') AS number, dep.title AS title"
        )
        unresolved_nodes = [
            {
                "id": r.get("id", ""),
                "type": r.get("type", ""),
                "number": r.get("number", ""),
                "title": r.get("title", ""),
            }
            for r in traverse_result.get("results", [])
        ]

        return {
            "rule_id": rule_id,
            "unresolved_count": len(unresolved_nodes),
            "unresolved": unresolved_nodes,
        }

    def orphans(self) -> list[dict[str, Any]]:
        """Sections with obligation language not cited by any rule."""
        orphans_result = self._query(
            "MATCH (s:Section) "
            "WHERE NOT exists { MATCH (:Rule)-[:CITES]->(s) } "
            "  AND (s.text =~ '(?i).*(shall|must|may\\\\s+not|minimum|maximum).*' "
            "       OR s.title =~ '(?i).*(shall|must|may\\\\s+not|minimum|maximum).*') "
            "RETURN s.number AS section, s.title AS title, s.page AS page, "
            "substring(coalesce(s.text, s.title, ''), 0, 150) AS text_preview "
            "ORDER BY s.number"
        )
        return [
            {
                "section": r.get("section", ""),
                "title": r.get("title", ""),
                "page": r.get("page"),
                "text_preview": r.get("text_preview", ""),
            }
            for r in orphans_result.get("results", [])
        ]

    def count_nodes(self) -> int:
        result = self._query("MATCH (n) RETURN count(n) AS cnt")
        rows = result.get("results", [])
        return rows[0]["cnt"] if rows else 0

    def count_edges(self) -> int:
        result = self._query("MATCH ()-[r]->() RETURN count(r) AS cnt")
        rows = result.get("results", [])
        return rows[0]["cnt"] if rows else 0

    def check_duplicates(self) -> dict[str, Any]:
        """Check for duplicate node_ids."""
        result = self._query(
            "MATCH (n) "
            "WITH n.node_id AS nid, count(n) AS cnt "
            "WHERE cnt > 1 "
            "RETURN nid, cnt ORDER BY cnt DESC LIMIT 20"
        )
        dups = result.get("results", [])
        return {
            "has_duplicates": len(dups) > 0,
            "duplicate_count": len(dups),
            "duplicates": dups,
        }


def timed_call(func, *args, **kwargs) -> tuple[Any, float]:
    """Call a function and return (result, elapsed_seconds)."""
    start = time.time()
    result = func(*args, **kwargs)
    elapsed = time.time() - start
    return result, elapsed
