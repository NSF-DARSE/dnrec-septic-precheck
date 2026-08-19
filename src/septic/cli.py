"""Single entry point.

    python -m septic <subcommand>

Subcommands are thin: each one parses its own arguments and calls into a module.
Everything writes artifacts to the out directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .ingest import ocr


def cmd_preflight(argv: list[str]) -> int:
    import subprocess
    from datetime import datetime, timezone

    from . import preflight

    ap = argparse.ArgumentParser(prog="septic preflight")
    ap.add_argument("--textract-timeout", type=int, default=300)
    args = ap.parse_args(argv)

    config.ensure_dirs()
    checks, blocked = preflight.run(textract_timeout=args.textract_timeout)

    # Build the header that goes into every output format.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(config.ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        sha = "unknown"
    header = f"run: {timestamp}  commit: {sha}"

    # Render and write all formats from the same in-memory result set.
    report_text = preflight.render(checks, header=header)
    report_json = json.dumps(
        {"header": {"timestamp": timestamp, "commit": sha},
         "checks": [c.__dict__ for c in checks]},
        indent=2, default=str,
    )

    print(report_text)
    (config.OUT_DIR / "preflight_report.txt").write_text(report_text, encoding="utf-8")
    (config.OUT_DIR / "preflight_report.json").write_text(report_json, encoding="utf-8")

    return 2 if blocked else 0


def cmd_harvest(argv: list[str]) -> int:
    from .harvest.cli import main as harvest_main

    return harvest_main(argv)


def cmd_audit(argv: list[str]) -> int:
    from .harvest import audit

    ap = argparse.ArgumentParser(prog="septic audit")
    ap.add_argument("--manifest", type=Path,
                    default=config.OUT_DIR / "manifest_denied-returned.jsonl")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    result = audit.run(args.manifest)
    text = result.render()
    print(text)
    (config.OUT_DIR / "audit_report.txt").write_text(text, encoding="utf-8")
    return 0


def cmd_verify(argv: list[str]) -> int:
    from .harvest import verify

    ap = argparse.ArgumentParser(prog="septic verify")
    ap.add_argument("--manifest", type=Path,
                    default=config.OUT_DIR / "manifest_denied-returned.jsonl")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--min-interval", type=float, default=1.0)
    ap.add_argument("--all", action="store_true",
                    help="check every permit, not only those recorded with zero")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    checks = verify.recheck_manifest(
        args.manifest,
        only_zero=not args.all,
        limit=args.limit,
        attempts=args.attempts,
        min_interval=args.min_interval,
    )
    text = verify.render(checks)
    print(text)
    (config.OUT_DIR / "verify_report.txt").write_text(text, encoding="utf-8")
    (config.OUT_DIR / "verify_report.json").write_text(
        json.dumps([c.to_json() for c in checks], indent=2), encoding="utf-8"
    )
    return 0


def cmd_rules(argv: list[str]) -> int:
    from .rules import engine

    ap = argparse.ArgumentParser(prog="septic rules")
    ap.add_argument("--facts", type=Path,
                    help="JSON file of extracted facts to evaluate")
    args = ap.parse_args(argv)

    rules = engine.load_rules()
    facts = json.loads(args.facts.read_text(encoding="utf-8")) if args.facts else {}
    report = engine.evaluate(facts, rules)

    print(f"loaded {len(rules)} rules, "
          f"{sum(1 for r in rules if r.verified)} verified")
    print(f"verdict: {report.verdict.value}")
    print(f"counts: {report.counts()}")
    for ev in report.evaluations:
        print(f"  {ev.outcome.value:<8}{ev.rule.id:<32}{ev.reason}")
    return 0


def cmd_candidates(argv: list[str]) -> int:
    from .rules import candidates as cand

    ap = argparse.ArgumentParser(prog="septic candidates")
    ap.add_argument("--pdf", type=Path, default=config.REGULATION_PDF)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "rules" / "candidates.md")
    args = ap.parse_args(argv)

    found = cand.extract(args.pdf)
    args.out.write_text(cand.render_markdown(found, args.pdf), encoding="utf-8")
    print(f"{len(found)} candidates across "
          f"{len(cand.counts_by_section(found))} sections -> {args.out}")
    return 0


def cmd_graph(argv: list[str]) -> int:
    from .rules import graph as reg_graph

    ap = argparse.ArgumentParser(prog="septic graph")
    sub = ap.add_subparsers(dest="action")

    build_p = sub.add_parser("build", help="build the regulation graph")
    build_p.add_argument("--pdf", type=Path, default=config.REGULATION_PDF)

    ctx_p = sub.add_parser("context", help="show context for a section")
    ctx_p.add_argument("section", help="section number, e.g. 5.3.4")

    unr_p = sub.add_parser("unresolved", help="show unresolved deps for a rule")
    unr_p.add_argument("rule_id", help="rule id from rules_7101.yaml")

    sub.add_parser("orphans", help="sections with obligations not cited by rules")
    sub.add_parser("summary", help="node and edge counts")

    args = ap.parse_args(argv)
    config.ensure_dirs()

    if args.action == "build":
        G, stats = reg_graph.build_graph(args.pdf)
        path = reg_graph.save_graph(G)
        summary = reg_graph.graph_summary(G)
        print(f"accepted {stats.accepted} headings, "
              f"rejected {stats.raw_candidates - stats.accepted}")
        print(f"  header: {stats.rejected_header}, "
              f"duplicate: {stats.rejected_duplicate}, "
              f"list item: {stats.rejected_list_item}")
        print(f"nodes: {summary['total_nodes']} "
              f"({summary['nodes_by_type']})")
        print(f"edges: {summary['total_edges']} "
              f"({summary['edges_by_type']})")
        print(f"saved to {path}")
        return 0

    # All other actions require a built graph
    try:
        G = reg_graph.load_graph()
    except FileNotFoundError:
        print("graph not built yet. Run: python -m septic graph build")
        return 1

    if args.action == "context":
        result = reg_graph.context(G, args.section)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.action == "unresolved":
        result = reg_graph.unresolved(G, args.rule_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.action == "orphans":
        result = reg_graph.orphans(G)
        print(f"{len(result)} sections with obligation language not cited by any rule:")
        for item in result:
            sec = item["section"]
            title = item["title"][:60]
            page = item["page"]
            print(f"  {sec:<12} p.{page:<4} {title}")
        return 0

    if args.action == "summary":
        summary = reg_graph.graph_summary(G)
        print(json.dumps(summary, indent=2))
        return 0

    ap.print_help()
    return 0


def cmd_review(argv: list[str]) -> int:
    from . import review as review_mod

    ap = argparse.ArgumentParser(prog="septic review")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path, help="path to an application PDF")
    source.add_argument("--permit", help="permit number or detail id already harvested")
    ap.add_argument("--manifest", type=Path,
                    default=config.OUT_DIR / "manifest_control.jsonl")
    ap.add_argument("--offline", action="store_true",
                    help="refuse any network call, requiring a cached analysis")
    ap.add_argument("--no-precedents", action="store_true",
                    help="skip the similar prior permits lookup")
    ap.add_argument("--rephrase", action="store_true",
                    help="run the optional Bedrock plain language pass on remedies")
    ap.add_argument("--ocr", choices=ocr.PROVIDERS,
                    help=f"which OCR provider reads the document; defaults to "
                         f"OCR_PROVIDER, currently {config.OCR_PROVIDER}. bedrock "
                         f"returns no page coordinates and no calibrated confidence, "
                         f"and cannot read --permit straight from the bucket")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR,
                    help="directory for the report files")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    result = review_mod.review(
        pdf=args.pdf,
        permit=args.permit,
        manifest=args.manifest,
        allow_network=not args.offline,
        with_precedents=not args.no_precedents,
        rephrase=args.rephrase,
        provider=args.ocr,
    )

    print(result.text)
    for warning in result.warnings:
        print(f"\nwarning: {warning}")

    stem = args.pdf.stem if args.pdf else f"permit_{args.permit}"
    args.out.mkdir(parents=True, exist_ok=True)
    text_path = args.out / f"review_{stem}.txt"
    html_path = args.out / f"review_{stem}.html"
    json_path = args.out / f"review_{stem}.json"
    text_path.write_text(result.text, encoding="utf-8")
    html_path.write_text(result.html, encoding="utf-8")
    json_path.write_text(
        json.dumps(result.composed.to_json(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nwrote {text_path}")
    print(f"wrote {html_path}")
    print(f"wrote {json_path}")
    if result.offline:
        print("no network was used: the Textract analysis came from the disk cache")
    return 0


COMMANDS = {
    "preflight": cmd_preflight,
    "harvest": cmd_harvest,
    "audit": cmd_audit,
    "verify": cmd_verify,
    "rules": cmd_rules,
    "candidates": cmd_candidates,
    "graph": cmd_graph,
    "review": cmd_review,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("subcommands: " + ", ".join(COMMANDS))
        return 0
    name, rest = argv[0], argv[1:]
    if name not in COMMANDS:
        print(f"unknown subcommand: {name}")
        print("subcommands: " + ", ".join(COMMANDS))
        return 1
    return COMMANDS[name](rest)


if __name__ == "__main__":
    sys.exit(main())
