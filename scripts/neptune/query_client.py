"""Neptune Analytics query client mirroring the NetworkX graph query functions.

Provides the same interface as graph.py's context(), unresolved(), orphans(),
and graph_summary() but queries Neptune Analytics via openCypher.

Usage:
    from scripts.neptune.query_client import NeptuneClient
    client = NeptuneClient(os.environ["NEPTUNE_GRAPH_ID"])
    result = client.context("5.3.12.1.3")
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from botocore.config import Config


REGION = os.environ.get("AWS_REGION", "us-west-2")


class NeptuneClient:
    """Query client for Neptune Analytics regulation graph."""

    def __init__(self, graph_id: str, region: str = REGION):
        self.graph_id = graph_id
        self.region = region
        config = Config(
            region_name=region,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        session = boto3.Session(region_name=region)
        self._client = session.client("neptune-graph", config=config)

    def _query(self, cypher: str, parameters: dict | None = None) -> dict:
        """Execute an openCypher query and return parsed results."""
        kwargs = {
            "graphIdentifier": self.graph_id,
            "language": "OPEN_CYPHER",
            "queryString": cypher,
        }
        if parameters:
            kwargs["parameters"] = parameters
        response = self._client.execute_query(**kwargs)
        # Neptune Analytics returns results in payload
        payload = response.get("payload")
        if payload:
            if hasattr(payload, "read"):
                raw = payload.read()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                return json.loads(raw)
            return json.loads(payload) if isinstance(payload, str) else payload
        return {"results": []}

    def graph_summary(self) -> dict[str, Any]:
        """Node and edge counts by type, matching graph.graph_summary()."""
        node_query = """
        MATCH (n)
        RETURN labels(n)[0] AS type, count(n) AS cnt
        """
        edge_query = """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(r) AS cnt
        """
        node_result = self._query(node_query)
        edge_result = self._query(edge_query)

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
        """Regulatory context for a section, matching graph.context()."""
        node_id = f"section:{section_number}"

        # Get the section itself
        section_query = """
        MATCH (s {node_id: $node_id})
        RETURN s.node_id AS node_id, s.title AS title, s.page AS page,
               s.text AS text, s.number AS number
        """
        section_result = self._query(section_query, {"node_id": node_id})
        rows = section_result.get("results", [])
        if not rows:
            return {"error": f"section {section_number} not found in graph"}
        section = rows[0]

        # Ancestors via CONTAINS (walk up)
        ancestors_query = """
        MATCH path = (ancestor:Section)-[:CONTAINS*]->(s {node_id: $node_id})
        UNWIND nodes(path) AS n
        WITH n WHERE n.node_id <> $node_id
        RETURN DISTINCT n.number AS number, n.title AS title
        ORDER BY n.number
        """
        ancestors_result = self._query(ancestors_query, {"node_id": node_id})
        ancestors = [
            {"number": r.get("number", ""), "title": r.get("title", "")}
            for r in ancestors_result.get("results", [])
        ]

        # References (outgoing REFERENCES edges)
        references_query = """
        MATCH (s {node_id: $node_id})-[:REFERENCES]->(ref)
        RETURN ref.node_id AS id, labels(ref)[0] AS type,
               coalesce(ref.number, ref.letter, '') AS number,
               ref.title AS title,
               substring(coalesce(ref.text, ''), 0, 500) AS text
        """
        references_result = self._query(references_query, {"node_id": node_id})
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

        # Definitions used (outgoing USES_TERM edges)
        definitions_query = """
        MATCH (s {node_id: $node_id})-[:USES_TERM]->(d:Definition)
        RETURN d.term AS term, d.defined_in AS defined_in
        """
        definitions_result = self._query(definitions_query, {"node_id": node_id})
        definitions = [
            {"term": r.get("term", ""), "defined_in": r.get("defined_in", "")}
            for r in definitions_result.get("results", [])
        ]

        # Exceptions (incoming EXCEPTION edges)
        exceptions_query = """
        MATCH (exc:Section)-[:EXCEPTION]->(s {node_id: $node_id})
        RETURN exc.number AS number, exc.title AS title,
               substring(coalesce(exc.text, ''), 0, 300) AS text
        """
        exceptions_result = self._query(exceptions_query, {"node_id": node_id})
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
        """Unresolved dependencies for a rule, matching graph.unresolved()."""
        rule_node_id = f"rule:{rule_id}"

        # Check rule exists
        check_query = """
        MATCH (r {node_id: $node_id})
        RETURN r.node_id AS node_id
        """
        check_result = self._query(check_query, {"node_id": rule_node_id})
        if not check_result.get("results"):
            return {"error": f"rule {rule_id} not found in graph"}

        # Traverse CITES and REFERENCES edges transitively
        # Find nodes with empty or no text
        traverse_query = """
        MATCH (r {node_id: $node_id})-[:CITES|REFERENCES*1..5]->(dep)
        WHERE (dep.text IS NULL OR dep.text = '' OR trim(dep.text) = '')
          AND NOT (labels(dep)[0] = 'Section' AND size(split(coalesce(dep.title,''), ' ')) >= 4)
        RETURN DISTINCT dep.node_id AS id, labels(dep)[0] AS type,
               coalesce(dep.number, dep.letter, '') AS number,
               dep.title AS title
        """
        traverse_result = self._query(traverse_query, {"node_id": rule_node_id})
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
        # Find sections not cited by any rule that contain obligation words
        orphans_query = """
        MATCH (s:Section)
        WHERE NOT exists { MATCH (:Rule)-[:CITES]->(s) }
          AND (s.text =~ '(?i).*(shall|must|may\\\\s+not|minimum|maximum|no\\\\s+less\\\\s+than|no\\\\s+more\\\\s+than|at\\\\s+least|not\\\\s+exceed|prohibited).*'
               OR s.title =~ '(?i).*(shall|must|may\\\\s+not|minimum|maximum|no\\\\s+less\\\\s+than|no\\\\s+more\\\\s+than|at\\\\s+least|not\\\\s+exceed|prohibited).*')
        RETURN s.number AS section, s.title AS title, s.page AS page,
               substring(coalesce(s.text, s.title, ''), 0, 150) AS text_preview
        ORDER BY s.number
        """
        orphans_result = self._query(orphans_query)
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
        """Total node count."""
        result = self._query("MATCH (n) RETURN count(n) AS cnt")
        rows = result.get("results", [])
        return rows[0]["cnt"] if rows else 0

    def count_edges(self) -> int:
        """Total edge count."""
        result = self._query("MATCH ()-[r]->() RETURN count(r) AS cnt")
        rows = result.get("results", [])
        return rows[0]["cnt"] if rows else 0

    def check_duplicates(self) -> dict[str, Any]:
        """Check for duplicate node_ids."""
        dup_query = """
        MATCH (n)
        WITH n.node_id AS nid, count(n) AS cnt
        WHERE cnt > 1
        RETURN nid, cnt
        ORDER BY cnt DESC
        LIMIT 20
        """
        result = self._query(dup_query)
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
