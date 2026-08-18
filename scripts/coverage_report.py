"""Generate docs/coverage.md, the scoping document.

Counts only. The audience is deciding whether to fund the work, so they get the
denominator as well as the numerator. No effort estimates and no editorialising:
the numbers are stated and the reader draws the conclusion.

Usage:
    python scripts/coverage_report.py
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.rules.engine import load_rules
from septic.rules.graph import load_graph, orphans

OUT = config.ROOT / "docs" / "coverage.md"

# Topic areas a residential reviewer touches. A section is counted under a topic
# when its text matches, and a section can match more than one, so the topic counts
# are not a partition and do not sum to the total. Stated in the document.
TOPICS = {
    "isolation distances": re.compile(
        r"isolation\s+distance|setback|separation\s+distance|"
        r"horizontal\s+distance|property\s+line|from\s+(?:a\s+)?well\b|"
        r"watercourse|surface\s+water", re.I),
    "depth to water table": re.compile(
        r"water\s+table|limiting\s+zone|seasonal\s+high|"
        r"zones?\s+of\s+saturation|redoximorphic", re.I),
    "percolation": re.compile(
        r"percolation|\bmpi\b|minutes?\s+per\s+inch|"
        r"hydraulic\s+conductivity|permeab", re.I),
    "sizing": re.compile(
        r"disposal\s+area\s+required|design\s+flow|gallons?\s+per\s+day|"
        r"\bgpd\b|square\s+feet|absorption\s+area|loading\s+rate|"
        r"tank\s+capacity", re.I),
    "siting": re.compile(
        r"slope|landscape\s+position|escarpment|flood|"
        r"site\s+evaluation|soil\s+boring|test\s+pit|"
        r"cuts?\s+and\s+fills?|unstable\s+landform", re.I),
}

OBLIGATION_RE = re.compile(
    r"\b(shall|must|may\s+not|shall\s+not|is\s+required|are\s+required|"
    r"minimum|maximum|no\s+less\s+than|no\s+more\s+than|at\s+least|"
    r"not\s+exceed|prohibited)\b",
    re.IGNORECASE,
)


def section_text(graph, node_id: str) -> str:
    attrs = graph.nodes[node_id]
    return f"{attrs.get('title', '')} {attrs.get('text', '')}"


def main() -> int:
    graph = load_graph()
    rules = load_rules()

    sections = [
        (node_id, attrs) for node_id, attrs in graph.nodes(data=True)
        if attrs.get("type") == "Section"
    ]
    exhibits = [
        attrs for _n, attrs in graph.nodes(data=True)
        if attrs.get("type") == "Exhibit"
    ]

    obligation_sections = [
        (node_id, attrs) for node_id, attrs in sections
        if OBLIGATION_RE.search(section_text(graph, node_id))
    ]

    # Which sections and exhibits any rule cites.
    cited_nodes: set[str] = set()
    for node_id, attrs in graph.nodes(data=True):
        if attrs.get("type") != "Rule":
            continue
        for _src, target, data in graph.out_edges(node_id, data=True):
            if data.get("type") == "CITES":
                cited_nodes.add(target)

    cited_obligation = [
        (node_id, attrs) for node_id, attrs in obligation_sections
        if node_id in cited_nodes
    ]

    gaps = orphans(graph)

    # Topic breakdown over the uncited obligation sections.
    gap_ids = {f"section:{item['section']}" for item in gaps}
    topic_counts: Counter = Counter()
    topic_cited: Counter = Counter()
    topic_total: Counter = Counter()
    matched_any = 0
    for node_id, _attrs in obligation_sections:
        text = section_text(graph, node_id)
        hit = False
        for topic, pattern in TOPICS.items():
            if pattern.search(text):
                topic_total[topic] += 1
                hit = True
                if node_id in cited_nodes:
                    topic_cited[topic] += 1
                elif node_id in gap_ids:
                    topic_counts[topic] += 1
        if hit:
            matched_any += 1

    verified = [r for r in rules if r.verified]
    staged = [r for r in rules if not r.verified]

    exhibits_readable = [e for e in exhibits if (e.get("text") or "").strip()]
    cited_exhibits = [n for n in cited_nodes if n.startswith("exhibit:")]

    L: list[str] = []
    add = L.append

    add("# Coverage")
    add("")
    add("How much of the regulation this tool checks, and how much it does not.")
    add("Counts only.")
    add("")
    add("Source: Delaware Regulations Governing On-Site Wastewater Treatment and")
    add("Disposal Systems, January 11, 2014, 245 pages. Counts are produced by")
    add("`scripts/coverage_report.py` from the parsed regulation graph at")
    add("`out/reg_graph.json` and the rule set at")
    add("`src/septic/rules/rules_7101.yaml`.")
    add("")

    add("## The regulation")
    add("")
    add("| | count |")
    add("| --- | --- |")
    add(f"| Numbered sections parsed | {len(sections)} |")
    add(f"| Sections carrying obligation language | {len(obligation_sections)} |")
    add(f"| Exhibits | {len(exhibits)} |")
    add(f"| Exhibits with a readable text layer | {len(exhibits_readable)} |")
    add("")
    add("Obligation language means the section text contains shall, must, may not,")
    add("is required, minimum, maximum, no less than, no more than, at least, not")
    add("exceed, or prohibited.")
    add("")

    add("## Rules")
    add("")
    add("| | count |")
    add("| --- | --- |")
    add(f"| Rules in the rule set | {len(rules)} |")
    add(f"| Verified by a person | {len(verified)} |")
    add(f"| Staged, awaiting verification | {len(staged)} |")
    add(f"| Sections cited by at least one rule | "
        f"{len([n for n in cited_nodes if n.startswith('section:')])} |")
    add(f"| Exhibits cited by at least one rule | {len(cited_exhibits)} |")
    add(f"| Obligation sections cited by at least one rule | "
        f"{len(cited_obligation)} |")
    add(f"| Obligation sections cited by no rule | {len(gaps)} |")
    add("")
    add("A rule that is not verified is not evaluated. The engine returns UNKNOWN")
    add("for it and the verdict for any application is CANNOT VERIFY.")
    add("")

    add("## Uncited obligation sections by topic area")
    add("")
    add("Topic areas a residential reviewer touches. A section is counted under a")
    add("topic when its text matches that topic, and a section can match more than")
    add("one, so these do not sum to the total and are not a partition.")
    add("")
    add("| topic | obligation sections | cited by a rule | not cited |")
    add("| --- | --- | --- | --- |")
    for topic in TOPICS:
        add(f"| {topic} | {topic_total[topic]} | {topic_cited[topic]} | "
            f"{topic_counts[topic]} |")
    add("")
    add(f"Obligation sections matching at least one topic: {matched_any} of "
        f"{len(obligation_sections)}.")
    add(f"Obligation sections matching no topic above: "
        f"{len(obligation_sections) - matched_any}.")
    add("")

    add("## The rule set as it stands")
    add("")
    add("| rule | requirement | citation | verified |")
    add("| --- | --- | --- | --- |")
    for rule in rules:
        if rule.threshold is None:
            requirement = "presence check"
        else:
            units = f" {rule.units}" if rule.units else ""
            requirement = f"{rule.operator.value} {rule.threshold}{units}"
        add(f"| `{rule.id}` | {requirement} | {rule.citation.section} "
            f"p.{rule.citation.page} | {rule.verified} |")
    add("")

    add("## Parameters the rules require")
    add("")
    add("| parameter | rules using it | available from |")
    add("| --- | --- | --- |")
    from septic.ingest.extract import FACTS

    csv_available = {"perc_rate", "design_flow", "design_flow_per_bedroom",
                     "system_type", "use_type", "system_scale", "bedrooms"}
    by_parameter: dict[str, list[str]] = {}
    for rule in rules:
        by_parameter.setdefault(rule.parameter, []).append(rule.id)
    for parameter in sorted(by_parameter):
        if parameter in csv_available:
            source = "permit CSV"
        elif parameter in FACTS:
            source = "packet, via Textract"
        else:
            source = "not yet extracted"
        add(f"| `{parameter}` | {len(by_parameter[parameter])} | {source} |")
    add("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(L)} lines)")
    print()
    print(f"  sections parsed                      {len(sections)}")
    print(f"  sections with obligation language    {len(obligation_sections)}")
    print(f"  obligation sections cited by a rule  {len(cited_obligation)}")
    print(f"  obligation sections not cited        {len(gaps)}")
    print(f"  rules staged                         {len(staged)}")
    print(f"  rules verified                       {len(verified)}")
    print()
    for topic in TOPICS:
        print(f"  {topic:<24}{topic_total[topic]:>6} obligation, "
              f"{topic_cited[topic]:>3} cited, {topic_counts[topic]:>5} not cited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
