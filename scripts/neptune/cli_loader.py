"""Load openCypher statements into Neptune Analytics via AWS CLI.

This loader uses `aws neptune-graph execute-query` instead of boto3, because
the project's pinned boto3 (1.34.162) does not support the `login_session`
credential mechanism used by Workshop Studio / Kiro CLI.

Usage:
    python scripts/neptune/cli_loader.py [--graph-id ID] [--export-dir PATH] [--start N]

The --start flag allows resuming from a specific statement index after an
interruption. MERGE-based statements make re-runs idempotent regardless.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


GRAPH_ID_DEFAULT = os.environ.get("NEPTUNE_GRAPH_ID", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE", "hackathon")


def execute_cypher(graph_id: str, statement: str) -> tuple[bool, str]:
    """Execute one openCypher statement via AWS CLI. Returns (success, output)."""
    cmd = [
        "aws", "--profile", PROFILE, "--region", REGION,
        "neptune-graph", "execute-query",
        "--graph-identifier", graph_id,
        "--query-string", statement,
        "--language", "OPEN_CYPHER",
        "/dev/stdout",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def load_file(graph_id: str, cypher_file: Path, label: str,
              start: int = 0) -> dict:
    """Load a .cypher file statement by statement. Returns stats."""
    statements = [
        line.strip().rstrip(";")
        for line in cypher_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("//")
    ]

    total = len(statements)
    loaded = 0
    errors = []
    start_time = time.time()

    print(f"  {label}: {total} statements, starting from index {start}")

    for i in range(start, total):
        stmt = statements[i]
        success, output = execute_cypher(graph_id, stmt)
        if success:
            loaded += 1
        else:
            errors.append({"index": i, "statement": stmt[:100], "error": output[:200]})
            if len(errors) >= 5:
                print(f"  STOPPING at index {i}: too many errors")
                break

        if (loaded) % 100 == 0 and loaded > 0:
            elapsed = time.time() - start_time
            rate = loaded / elapsed if elapsed > 0 else 0
            print(f"  {label}: {i + 1}/{total} loaded={loaded} ({rate:.1f} stmt/s)")

    elapsed = time.time() - start_time
    return {
        "file": str(cypher_file),
        "total_statements": total,
        "start_index": start,
        "attempted": min(total, len(statements)) - start,
        "loaded": loaded,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
        "rate": round(loaded / elapsed, 2) if elapsed > 0 else 0,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Load openCypher via AWS CLI")
    parser.add_argument("--graph-id", default=GRAPH_ID_DEFAULT)
    parser.add_argument("--export-dir", default="out/neptune_export")
    parser.add_argument("--start-nodes", type=int, default=0,
                        help="Resume nodes from this index")
    parser.add_argument("--start-edges", type=int, default=0,
                        help="Resume edges from this index")
    parser.add_argument("--edges-only", action="store_true",
                        help="Skip nodes, load only edges")
    parser.add_argument("--nodes-only", action="store_true",
                        help="Load only nodes, skip edges")
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    nodes_file = export_dir / "nodes.cypher"
    edges_file = export_dir / "edges.cypher"

    # Verify connectivity
    print("Verifying Neptune access...")
    success, output = execute_cypher(args.graph_id, "MATCH (n) RETURN count(n) AS cnt")
    if not success:
        print(f"ERROR: Cannot connect to Neptune: {output}")
        sys.exit(1)
    current_nodes = json.loads(output).get("results", [{}])[0].get("cnt", 0)
    print(f"  Current nodes in graph: {current_nodes}")

    results = {}

    if not args.edges_only:
        print(f"\nLoading nodes from {nodes_file}...")
        results["nodes"] = load_file(args.graph_id, nodes_file, "nodes", args.start_nodes)

    if not args.nodes_only:
        print(f"\nLoading edges from {edges_file}...")
        results["edges"] = load_file(args.graph_id, edges_file, "edges", args.start_edges)

    # Final counts
    print("\nVerifying final counts...")
    success, output = execute_cypher(args.graph_id, "MATCH (n) RETURN count(n) AS cnt")
    if success:
        final_nodes = json.loads(output).get("results", [{}])[0].get("cnt", 0)
        print(f"  Final nodes: {final_nodes}")
    success, output = execute_cypher(args.graph_id, "MATCH ()-[r]->() RETURN count(r) AS cnt")
    if success:
        final_edges = json.loads(output).get("results", [{}])[0].get("cnt", 0)
        print(f"  Final edges: {final_edges}")

    # Save results
    results_file = Path(args.export_dir) / "cli_load_results.json"
    results_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults written to {results_file}")


if __name__ == "__main__":
    main()
