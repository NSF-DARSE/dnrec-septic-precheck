"""Export out/reg_graph.json to openCypher CREATE statements for Neptune Analytics.

Usage:
    python scripts/neptune/export.py [--graph-json PATH] [--out-dir PATH]

Produces two files:
    nodes.cypher   — one MERGE per node with all properties
    edges.cypher   — one MERGE per relationship

The MERGE key is the stable `node_id` property, making re-runs idempotent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def escape_cypher(value) -> str:
    """Escape a value for openCypher string literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")
    return f"'{s}'"


def props_string(props: dict) -> str:
    """Build a Cypher properties map string."""
    parts = []
    for k, v in props.items():
        if v is None:
            continue
        parts.append(f"{k}: {escape_cypher(v)}")
    return "{" + ", ".join(parts) + "}"


def export_graph(graph_json: Path, out_dir: Path) -> tuple[int, int]:
    """Export graph JSON to .cypher files. Returns (node_count, edge_count)."""
    data = json.loads(graph_json.read_text(encoding="utf-8"))

    out_dir.mkdir(parents=True, exist_ok=True)
    nodes_file = out_dir / "nodes.cypher"
    edges_file = out_dir / "edges.cypher"

    node_count = 0
    with open(nodes_file, "w", encoding="utf-8") as f:
        for node in data["nodes"]:
            node_id = node["id"]
            node_type = node.get("type", "Node")
            props = {"node_id": node_id}
            for k, v in node.items():
                if k == "id":
                    continue
                props[k] = v
            stmt = f"MERGE (n:{node_type} {{node_id: {escape_cypher(node_id)}}}) SET n += {props_string(props)};\n"
            f.write(stmt)
            node_count += 1

    edge_count = 0
    with open(edges_file, "w", encoding="utf-8") as f:
        for edge in data["edges"]:
            source = edge["source"]
            target = edge["target"]
            edge_type = edge.get("type", "RELATES_TO")
            props = {k: v for k, v in edge.items() if k not in ("source", "target", "type")}
            if props:
                props_part = f" {props_string(props)}"
            else:
                props_part = ""
            stmt = (
                f"MATCH (a {{node_id: {escape_cypher(source)}}}), "
                f"(b {{node_id: {escape_cypher(target)}}}) "
                f"MERGE (a)-[r:{edge_type}]->(b)"
            )
            if props:
                stmt += f" SET r += {props_string(props)}"
            stmt += ";\n"
            f.write(stmt)
            edge_count += 1

    return node_count, edge_count


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Export reg_graph.json to openCypher")
    parser.add_argument("--graph-json", default="out/reg_graph.json")
    parser.add_argument("--out-dir", default="out/neptune_export")
    args = parser.parse_args()

    graph_json = Path(args.graph_json)
    if not graph_json.exists():
        print(f"ERROR: {graph_json} not found", file=sys.stderr)
        sys.exit(1)

    nodes, edges = export_graph(graph_json, Path(args.out_dir))
    print(f"Exported {nodes} nodes to {args.out_dir}/nodes.cypher")
    print(f"Exported {edges} edges to {args.out_dir}/edges.cypher")


if __name__ == "__main__":
    main()
