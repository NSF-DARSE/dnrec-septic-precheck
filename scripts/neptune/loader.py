"""Load openCypher statements into Neptune Analytics.

Usage:
    python scripts/neptune/loader.py [--graph-id ID] [--export-dir PATH]

Loads nodes first, then edges. Uses batched ExecuteOpenCypherQuery calls.
MERGE-based statements make the load idempotent: re-running creates no duplicates.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config


import os

GRAPH_ID_DEFAULT = os.environ.get("NEPTUNE_GRAPH_ID", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")
BATCH_SIZE = 50  # statements per request (Neptune Analytics limit is ~64KB per request)


def get_client(region: str = REGION):
    """Create a Neptune Graph client."""
    config = Config(
        region_name=region,
        retries={"max_attempts": 3, "mode": "adaptive"},
    )
    session = boto3.Session(region_name=region)
    return session.client("neptune-graph", config=config)


def execute_query(client, graph_id: str, query: str, parameters: dict | None = None):
    """Execute an openCypher query against Neptune Analytics."""
    kwargs = {
        "graphIdentifier": graph_id,
        "language": "OPEN_CYPHER",
        "queryString": query,
    }
    if parameters:
        kwargs["parameters"] = parameters
    return client.execute_query(**kwargs)


def load_file(client, graph_id: str, cypher_file: Path, label: str) -> dict:
    """Load a .cypher file statement by statement. Returns stats."""
    statements = [
        line.strip().rstrip(";")
        for line in cypher_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("//")
    ]

    total = len(statements)
    loaded = 0
    errors = []
    start = time.time()

    for i, stmt in enumerate(statements):
        try:
            execute_query(client, graph_id, stmt)
            loaded += 1
        except Exception as e:
            errors.append({"index": i, "statement": stmt[:200], "error": str(e)})
            if len(errors) >= 10:
                break

        if (i + 1) % 100 == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"  {label}: {i + 1}/{total} ({rate:.0f} stmt/s)")

    elapsed = time.time() - start
    return {
        "file": str(cypher_file),
        "total_statements": total,
        "loaded": loaded,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
    }


def load_graph(graph_id: str, export_dir: Path) -> dict:
    """Load nodes then edges into Neptune Analytics."""
    client = get_client()

    nodes_file = export_dir / "nodes.cypher"
    edges_file = export_dir / "edges.cypher"

    if not nodes_file.exists():
        raise FileNotFoundError(f"Nodes file not found: {nodes_file}")
    if not edges_file.exists():
        raise FileNotFoundError(f"Edges file not found: {edges_file}")

    print(f"Loading nodes from {nodes_file}...")
    nodes_result = load_file(client, graph_id, nodes_file, "nodes")

    print(f"\nLoading edges from {edges_file}...")
    edges_result = load_file(client, graph_id, edges_file, "edges")

    return {
        "graph_id": graph_id,
        "nodes": nodes_result,
        "edges": edges_result,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Load openCypher into Neptune Analytics")
    parser.add_argument("--graph-id", default=GRAPH_ID_DEFAULT)
    parser.add_argument("--export-dir", default="out/neptune_export")
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    result = load_graph(args.graph_id, export_dir)

    print(f"\n=== Load Complete ===")
    print(f"Nodes: {result['nodes']['loaded']}/{result['nodes']['total_statements']} "
          f"in {result['nodes']['elapsed_seconds']}s")
    print(f"Edges: {result['edges']['loaded']}/{result['edges']['total_statements']} "
          f"in {result['edges']['elapsed_seconds']}s")

    if result["nodes"]["errors"]:
        print(f"\nNode errors ({len(result['nodes']['errors'])}):")
        for e in result["nodes"]["errors"][:5]:
            print(f"  [{e['index']}] {e['error'][:120]}")

    if result["edges"]["errors"]:
        print(f"\nEdge errors ({len(result['edges']['errors'])}):")
        for e in result["edges"]["errors"][:5]:
            print(f"  [{e['index']}] {e['error'][:120]}")

    # Write results to JSON for later verification
    results_file = Path(args.export_dir) / "load_results.json"
    results_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nResults written to {results_file}")


if __name__ == "__main__":
    main()
