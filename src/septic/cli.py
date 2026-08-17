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


def cmd_preflight(argv: list[str]) -> int:
    from . import preflight

    ap = argparse.ArgumentParser(prog="septic preflight")
    ap.add_argument("--textract-timeout", type=int, default=300)
    args = ap.parse_args(argv)

    config.ensure_dirs()
    checks, blocked = preflight.run(textract_timeout=args.textract_timeout)
    report = preflight.render(checks)
    print(report)
    (config.OUT_DIR / "preflight_report.txt").write_text(report, encoding="utf-8")
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


COMMANDS = {
    "preflight": cmd_preflight,
    "harvest": cmd_harvest,
    "audit": cmd_audit,
    "verify": cmd_verify,
    "rules": cmd_rules,
    "candidates": cmd_candidates,
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
