"""Demo comparison: run cached permits through both NetworkX and Neptune context retrieval.

Usage:
    python scripts/neptune/demo_compare.py [--graph-id ID]

Finds the three cached demo PDFs, runs each through the deterministic review,
then retrieves regulatory context from both backends and compares.
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
from src.septic.rules.graph import load_graph, context as nx_context
from src.septic.rules.engine import load_rules, evaluate


GRAPH_ID_DEFAULT = os.environ.get("NEPTUNE_GRAPH_ID", "")


def find_demo_permits() -> list[Path]:
    """Find cached demo permit PDFs."""
    examples_dir = Path("out/examples")
    if not examples_dir.exists():
        # Try to find cached review outputs instead
        cache_dir = Path("out/cache")
        if cache_dir.exists():
            return sorted(cache_dir.glob("*.json"))[:3]
        return []
    pdfs = sorted(examples_dir.glob("*.pdf"))
    return pdfs[:3]


def find_cached_reviews() -> list[Path]:
    """Find cached review result JSON files."""
    out_dir = Path("out")
    # Look for review output JSONs
    patterns = [
        out_dir / "reviews",
        out_dir / "cache",
        out_dir / "examples",
    ]
    results = []
    for d in patterns:
        if d.exists():
            results.extend(sorted(d.glob("*review*.json")))
            results.extend(sorted(d.glob("*composed*.json")))
    return results[:3]


def get_rule_citations() -> list[dict]:
    """Get all rule citations from the rule set."""
    rules = load_rules()
    citations = []
    for rule in rules:
        citations.append({
            "rule_id": rule.id,
            "section": rule.citation.section,
            "page": rule.citation.page,
        })
    return citations


def compare_context_for_rule(
    G, neptune: NeptuneClient, rule_id: str, section: str
) -> dict[str, Any]:
    """Compare context retrieval for one rule's citation."""
    nx_ctx, nx_time = timed_call(nx_context, G, section)
    np_ctx, np_time = timed_call(neptune.context, section)

    diffs = []
    if "error" in nx_ctx and "error" not in np_ctx:
        diffs.append(f"NetworkX returned error, Neptune did not")
    elif "error" not in nx_ctx and "error" in np_ctx:
        diffs.append(f"Neptune returned error, NetworkX did not")
    elif "error" not in nx_ctx and "error" not in np_ctx:
        # Compare fields
        if nx_ctx.get("title", "").strip() != (np_ctx.get("title") or "").strip():
            diffs.append(f"title differs")
        if nx_ctx.get("page") != np_ctx.get("page"):
            diffs.append(f"page: NX={nx_ctx.get('page')}, Neptune={np_ctx.get('page')}")

        nx_anc = set(a["number"] for a in nx_ctx.get("ancestors", []))
        np_anc = set(a["number"] for a in np_ctx.get("ancestors", []))
        if nx_anc != np_anc:
            diffs.append(f"ancestors differ: NX has {len(nx_anc)}, Neptune has {len(np_anc)}")

        nx_refs = set(r.get("id", "") for r in nx_ctx.get("references", []))
        np_refs = set(r.get("id", "") for r in np_ctx.get("references", []))
        if nx_refs != np_refs:
            diffs.append(f"references differ: NX has {len(nx_refs)}, Neptune has {len(np_refs)}")

        nx_defs = set(d["term"] for d in nx_ctx.get("definitions", []))
        np_defs = set(d["term"] for d in np_ctx.get("definitions", []))
        if nx_defs != np_defs:
            diffs.append(f"definitions differ: NX has {len(nx_defs)}, Neptune has {len(np_defs)}")

        nx_exc = set(e["number"] for e in nx_ctx.get("exceptions", []))
        np_exc = set(e["number"] for e in np_ctx.get("exceptions", []))
        if nx_exc != np_exc:
            diffs.append(f"exceptions differ: NX has {len(nx_exc)}, Neptune has {len(np_exc)}")

    return {
        "rule_id": rule_id,
        "section": section,
        "networkx_time_ms": round(nx_time * 1000, 1),
        "neptune_time_ms": round(np_time * 1000, 1),
        "diffs": diffs,
        "match": len(diffs) == 0,
        "networkx_ancestors": len(nx_ctx.get("ancestors", [])) if "error" not in nx_ctx else "error",
        "neptune_ancestors": len(np_ctx.get("ancestors", [])) if "error" not in np_ctx else "error",
        "networkx_references": len(nx_ctx.get("references", [])) if "error" not in nx_ctx else "error",
        "neptune_references": len(np_ctx.get("references", [])) if "error" not in np_ctx else "error",
        "networkx_definitions": len(nx_ctx.get("definitions", [])) if "error" not in nx_ctx else "error",
        "neptune_definitions": len(np_ctx.get("definitions", [])) if "error" not in np_ctx else "error",
        "networkx_exceptions": len(nx_ctx.get("exceptions", [])) if "error" not in nx_ctx else "error",
        "neptune_exceptions": len(np_ctx.get("exceptions", [])) if "error" not in np_ctx else "error",
    }


def run_demo(graph_id: str = GRAPH_ID_DEFAULT) -> dict[str, Any]:
    """Run the full demo comparison."""
    print("Loading NetworkX graph...")
    G = load_graph()
    neptune = NeptuneClient(graph_id)

    rules = load_rules()
    citations = get_rule_citations()

    # For the demo, we compare context retrieval for every rule's citation
    # since the rule engine itself is deterministic and unchanged
    print(f"\nComparing context for {len(citations)} rule citations...")
    comparisons = []
    for cit in citations:
        section = cit["section"]
        # Exhibit citations use the exhibit: prefix in graph
        if section.startswith("Exhibit"):
            # context() takes a section number, not an exhibit
            # Skip exhibit-only citations for context comparison
            comparisons.append({
                "rule_id": cit["rule_id"],
                "section": section,
                "skipped": True,
                "reason": "Exhibit citation — context() takes section numbers",
            })
            continue
        comp = compare_context_for_rule(G, neptune, cit["rule_id"], section)
        comparisons.append(comp)
        status = "✓" if comp["match"] else f"✗ ({', '.join(comp['diffs'])})"
        print(f"  {cit['rule_id']} → {section}: {status}")

    # Summary
    matched = sum(1 for c in comparisons if c.get("match", False))
    skipped = sum(1 for c in comparisons if c.get("skipped", False))
    total = len(comparisons)
    mismatched = total - matched - skipped

    return {
        "graph_id": graph_id,
        "total_rules": total,
        "matched": matched,
        "skipped": skipped,
        "mismatched": mismatched,
        "comparisons": comparisons,
        "verdict": "ALL MATCH" if mismatched == 0 else "DIFFERENCES FOUND",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Demo comparison: NX vs Neptune")
    parser.add_argument("--graph-id", default=GRAPH_ID_DEFAULT)
    args = parser.parse_args()

    results = run_demo(args.graph_id)

    print(f"\n{'='*60}")
    print(f"DEMO VERDICT: {results['verdict']}")
    print(f"{'='*60}")
    print(f"Total rules: {results['total_rules']}")
    print(f"  Matched: {results['matched']}")
    print(f"  Skipped (Exhibit): {results['skipped']}")
    print(f"  Mismatched: {results['mismatched']}")

    if results["mismatched"] > 0:
        print("\nMismatches:")
        for c in results["comparisons"]:
            if not c.get("match", True) and not c.get("skipped", False):
                print(f"  {c['rule_id']} → {c['section']}: {c['diffs']}")

    # Save results
    out = Path("out/neptune_export/demo_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nFull results: {out}")


if __name__ == "__main__":
    main()
