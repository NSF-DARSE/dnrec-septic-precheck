"""Parity checker: compare Neptune Analytics results against NetworkX.

Usage:
    python scripts/neptune/parity_check.py [--graph-id ID]

Runs graph_summary, context, unresolved, and orphans against both backends
and reports differences.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.neptune.query_client import NeptuneClient, timed_call
from src.septic.rules.graph import load_graph, context, unresolved, orphans, graph_summary


GRAPH_ID_DEFAULT = os.environ.get("NEPTUNE_GRAPH_ID", "")

# Test sections chosen to exercise different graph patterns
TEST_SECTIONS = [
    "5.3.12.1.3",   # deep nesting, references, definitions
    "5.3.4.1",      # references Exhibit C, creates isolation obligation
    "2.0",          # definitions section
    "5.2.4.2.4.2",  # exception language, the 20-inch limiting zone
]

# Test rules for unresolved check
TEST_RULES = [
    "ISO-001-disposal-area-to-well",
    "SEP-002-limiting-zone-new-construction",
]


def compare_summary(nx_summary: dict, neptune_summary: dict) -> list[str]:
    """Compare graph_summary results."""
    diffs = []
    if nx_summary["total_nodes"] != neptune_summary["total_nodes"]:
        diffs.append(
            f"total_nodes: NetworkX={nx_summary['total_nodes']}, "
            f"Neptune={neptune_summary['total_nodes']}"
        )
    if nx_summary["total_edges"] != neptune_summary["total_edges"]:
        diffs.append(
            f"total_edges: NetworkX={nx_summary['total_edges']}, "
            f"Neptune={neptune_summary['total_edges']}"
        )
    for node_type in set(nx_summary["nodes_by_type"]) | set(neptune_summary["nodes_by_type"]):
        nx_count = nx_summary["nodes_by_type"].get(node_type, 0)
        np_count = neptune_summary["nodes_by_type"].get(node_type, 0)
        if nx_count != np_count:
            diffs.append(f"nodes[{node_type}]: NetworkX={nx_count}, Neptune={np_count}")
    for edge_type in set(nx_summary["edges_by_type"]) | set(neptune_summary["edges_by_type"]):
        nx_count = nx_summary["edges_by_type"].get(edge_type, 0)
        np_count = neptune_summary["edges_by_type"].get(edge_type, 0)
        if nx_count != np_count:
            diffs.append(f"edges[{edge_type}]: NetworkX={nx_count}, Neptune={np_count}")
    return diffs


def compare_context(nx_ctx: dict, neptune_ctx: dict, section: str) -> list[str]:
    """Compare context() results."""
    diffs = []
    if "error" in nx_ctx or "error" in neptune_ctx:
        if nx_ctx.get("error") != neptune_ctx.get("error"):
            diffs.append(f"context({section}): error mismatch: NX={nx_ctx.get('error')}, Neptune={neptune_ctx.get('error')}")
        return diffs

    # Compare basic fields
    for field in ("title", "page", "text"):
        nx_val = nx_ctx.get(field)
        np_val = neptune_ctx.get(field)
        if str(nx_val or "").strip() != str(np_val or "").strip():
            diffs.append(f"context({section}).{field}: differs")

    # Compare ancestors
    nx_anc = set(a["number"] for a in nx_ctx.get("ancestors", []))
    np_anc = set(a["number"] for a in neptune_ctx.get("ancestors", []))
    if nx_anc != np_anc:
        missing = nx_anc - np_anc
        extra = np_anc - nx_anc
        if missing:
            diffs.append(f"context({section}).ancestors: Neptune missing {missing}")
        if extra:
            diffs.append(f"context({section}).ancestors: Neptune extra {extra}")

    # Compare references
    nx_refs = set(r.get("id", r.get("number", "")) for r in nx_ctx.get("references", []))
    np_refs = set(r.get("id", r.get("number", "")) for r in neptune_ctx.get("references", []))
    if nx_refs != np_refs:
        missing = nx_refs - np_refs
        extra = np_refs - nx_refs
        if missing:
            diffs.append(f"context({section}).references: Neptune missing {missing}")
        if extra:
            diffs.append(f"context({section}).references: Neptune extra {extra}")

    # Compare definitions
    nx_defs = set(d["term"] for d in nx_ctx.get("definitions", []))
    np_defs = set(d["term"] for d in neptune_ctx.get("definitions", []))
    if nx_defs != np_defs:
        missing = nx_defs - np_defs
        extra = np_defs - nx_defs
        if missing:
            diffs.append(f"context({section}).definitions: Neptune missing {missing}")
        if extra:
            diffs.append(f"context({section}).definitions: Neptune extra {extra}")

    # Compare exceptions
    nx_exc = set(e["number"] for e in nx_ctx.get("exceptions", []))
    np_exc = set(e["number"] for e in neptune_ctx.get("exceptions", []))
    if nx_exc != np_exc:
        missing = nx_exc - np_exc
        extra = np_exc - nx_exc
        if missing:
            diffs.append(f"context({section}).exceptions: Neptune missing {missing}")
        if extra:
            diffs.append(f"context({section}).exceptions: Neptune extra {extra}")

    return diffs


def compare_unresolved(nx_unres: dict, neptune_unres: dict, rule_id: str) -> list[str]:
    """Compare unresolved() results."""
    diffs = []
    if "error" in nx_unres or "error" in neptune_unres:
        if nx_unres.get("error") != neptune_unres.get("error"):
            diffs.append(f"unresolved({rule_id}): error mismatch")
        return diffs

    nx_ids = set(u["id"] for u in nx_unres.get("unresolved", []))
    np_ids = set(u["id"] for u in neptune_unres.get("unresolved", []))
    if nx_ids != np_ids:
        missing = nx_ids - np_ids
        extra = np_ids - nx_ids
        if missing:
            diffs.append(f"unresolved({rule_id}): Neptune missing {missing}")
        if extra:
            diffs.append(f"unresolved({rule_id}): Neptune extra {extra}")

    return diffs


def compare_orphans(nx_orphans: list, neptune_orphans: list) -> list[str]:
    """Compare orphans() results."""
    nx_sections = set(o["section"] for o in nx_orphans)
    np_sections = set(o["section"] for o in neptune_orphans)
    diffs = []
    if len(nx_sections) != len(np_sections):
        diffs.append(
            f"orphans count: NetworkX={len(nx_sections)}, Neptune={len(np_sections)}"
        )
    missing = nx_sections - np_sections
    extra = np_sections - nx_sections
    if missing and len(missing) <= 20:
        diffs.append(f"orphans: Neptune missing {len(missing)} sections")
    elif missing:
        diffs.append(f"orphans: Neptune missing {len(missing)} sections (too many to list)")
    if extra and len(extra) <= 20:
        diffs.append(f"orphans: Neptune extra {len(extra)} sections")
    elif extra:
        diffs.append(f"orphans: Neptune extra {len(extra)} sections (too many to list)")
    return diffs


def run_parity_check(graph_id: str = GRAPH_ID_DEFAULT) -> dict[str, Any]:
    """Full parity check. Returns structured results."""
    print("Loading NetworkX graph...")
    G = load_graph()
    neptune = NeptuneClient(graph_id)

    results: dict[str, Any] = {
        "graph_id": graph_id,
        "diffs": [],
        "timings": {},
    }

    # 1. Graph summary
    print("Comparing graph_summary...")
    nx_summary, nx_time = timed_call(graph_summary, G)
    np_summary, np_time = timed_call(neptune.graph_summary)
    results["timings"]["graph_summary"] = {"networkx_ms": round(nx_time * 1000, 1), "neptune_ms": round(np_time * 1000, 1)}
    results["nx_summary"] = nx_summary
    results["neptune_summary"] = np_summary
    results["diffs"].extend(compare_summary(nx_summary, np_summary))

    # 2. Context queries
    for section in TEST_SECTIONS:
        print(f"Comparing context({section})...")
        nx_ctx, nx_time = timed_call(context, G, section)
        np_ctx, np_time = timed_call(neptune.context, section)
        results["timings"][f"context_{section}"] = {"networkx_ms": round(nx_time * 1000, 1), "neptune_ms": round(np_time * 1000, 1)}
        results["diffs"].extend(compare_context(nx_ctx, np_ctx, section))

    # 3. Unresolved queries
    for rule_id in TEST_RULES:
        print(f"Comparing unresolved({rule_id})...")
        nx_unres, nx_time = timed_call(unresolved, G, rule_id)
        np_unres, np_time = timed_call(neptune.unresolved, rule_id)
        results["timings"][f"unresolved_{rule_id}"] = {"networkx_ms": round(nx_time * 1000, 1), "neptune_ms": round(np_time * 1000, 1)}
        results["diffs"].extend(compare_unresolved(nx_unres, np_unres, rule_id))

    # 4. Orphans
    print("Comparing orphans (may take a moment for regex matching)...")
    nx_orphans, nx_time = timed_call(orphans, G)
    np_orphans, np_time = timed_call(neptune.orphans)
    results["timings"]["orphans"] = {"networkx_ms": round(nx_time * 1000, 1), "neptune_ms": round(np_time * 1000, 1)}
    results["diffs"].extend(compare_orphans(nx_orphans, np_orphans))
    results["orphan_counts"] = {"networkx": len(nx_orphans), "neptune": len(np_orphans)}

    # 5. Duplicates check
    print("Checking for duplicates...")
    dup_check = neptune.check_duplicates()
    results["duplicates"] = dup_check

    results["total_diffs"] = len(results["diffs"])
    results["parity"] = "PASS" if not results["diffs"] else "DIFFERENCES FOUND"
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Neptune vs NetworkX parity check")
    parser.add_argument("--graph-id", default=GRAPH_ID_DEFAULT)
    args = parser.parse_args()

    results = run_parity_check(args.graph_id)

    print(f"\n{'='*60}")
    print(f"PARITY CHECK: {results['parity']}")
    print(f"{'='*60}")
    print(f"Differences: {results['total_diffs']}")
    for d in results["diffs"]:
        print(f"  - {d}")
    print(f"\nDuplicates: {'NONE' if not results['duplicates']['has_duplicates'] else results['duplicates']}")
    print(f"\nTimings:")
    for name, t in results["timings"].items():
        print(f"  {name}: NX={t['networkx_ms']:.1f}ms, Neptune={t['neptune_ms']:.1f}ms")

    # Save results
    out = Path("out/neptune_export/parity_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nFull results: {out}")


if __name__ == "__main__":
    main()
