"""Generate docs/rules_review.md, the human verification checklist.

One self-contained block per rule so a subject matter expert can read down the
list without opening the 245 page regulation PDF. Each block carries the rule id,
section number, page, threshold and units, the verbatim quote, and every cross
reference and definition the graph resolves for that section.

Reads the persisted graph from out/reg_graph.json. Run "python -m septic graph
build" first if that file does not exist.
"""
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from septic.rules.graph import load_graph, context, graph_summary
from septic.rules.engine import load_rules
from septic.rules.candidates import extract

G = load_graph()
rules = load_rules()
candidates = extract()

by_section = {}
for c in candidates:
    by_section.setdefault(c.section, []).append(c)

lines = []
lines.append("# Rules Review")
lines.append("")
lines.append("Each candidate rule is presented with its full graph context:")
lines.append("cited section text, cross-references resolved and inlined,")
lines.append("definitions inlined, and exceptions listed.")
lines.append("")
lines.append("A person confirms rules against the regulation. This file")
lines.append("makes that review possible without jumping around a 245-page PDF.")
lines.append("")
lines.append("Do not promote any rule to verified: true based on this file alone.")
lines.append("Open the PDF at the cited page and confirm the value, units, and")
lines.append("conditions before moving anything to rules_7101.yaml.")
lines.append("")

summary = graph_summary(G)
lines.append("## Graph statistics")
lines.append("")
lines.append(f"- Sections: {summary['nodes_by_type'].get('Section', 0)}")
lines.append(f"- Exhibits: {summary['nodes_by_type'].get('Exhibit', 0)}")
lines.append(f"- Definitions: {summary['nodes_by_type'].get('Definition', 0)}")
lines.append(f"- Rules: {summary['nodes_by_type'].get('Rule', 0)}")
lines.append(f"- Total edges: {summary['total_edges']}")
lines.append("")
lines.append("## Current rules (from rules_7101.yaml)")
lines.append("")

for rule in rules:
    lines.append(f"### {rule.id}")
    lines.append("")
    lines.append(f"**Description:** {rule.description}")
    lines.append(f"**Citation:** Section {rule.citation.section}, p.{rule.citation.page}")
    lines.append(f"**Verified:** {rule.verified}")
    lines.append(f"**Severity:** {rule.severity.value}")
    lines.append("")

    if rule.citation.section and rule.citation.section != "TBD":
        ctx = context(G, rule.citation.section)
        if "error" not in ctx:
            text = ctx.get("text", "(no text)")[:500]
            lines.append(f"**Section text:** {text}")
            lines.append("")
            refs = ctx.get("references", [])
            if refs:
                lines.append("**Cross-references:**")
                for ref in refs:
                    rtype = ref["type"]
                    rnum = ref["number"]
                    rtitle = ref["title"]
                    lines.append(f"- {rtype} {rnum}: {rtitle}")
                    rtext = ref.get("text", "")
                    if rtext:
                        lines.append(f"  > {rtext[:200]}")
                lines.append("")
            defs = ctx.get("definitions", [])
            if defs:
                lines.append("**Definitions used:**")
                for d in defs:
                    lines.append(f'- "{d["term"]}" (defined in Section {d["defined_in"]})')
                lines.append("")
            excs = ctx.get("exceptions", [])
            if excs:
                lines.append("**Exceptions:**")
                for e in excs:
                    lines.append(f"- Section {e['number']}: {e['title'][:60]}")
                    etext = e.get("text", "")
                    if etext:
                        lines.append(f"  > {etext[:200]}")
                lines.append("")
    lines.append("---")
    lines.append("")

lines.append("## Top candidate sections for review")
lines.append("")
lines.append("Sections with the highest concentration of obligation language")
lines.append("and numeric thresholds, ordered by candidate count.")
lines.append("")

top_sections = sorted(by_section.items(), key=lambda kv: -len(kv[1]))[:30]
for section, cands in top_sections:
    obl = sum(1 for c in cands if c.obligation)
    setb = sum(1 for c in cands if c.setback)
    lines.append(
        f"### Section {section} "
        f"({len(cands)} candidates, {obl} obligations, {setb} setbacks)"
    )
    lines.append("")

    ctx = context(G, section)
    if "error" not in ctx:
        title = ctx.get("title", "")
        if title:
            lines.append(f"**Title:** {title}")
        page = ctx.get("page")
        if page:
            lines.append(f"**Page:** {page}")
        ancestors = ctx.get("ancestors", [])
        if ancestors:
            path_parts = []
            for a in ancestors:
                path_parts.append(a["number"] + " " + a["title"][:30])
            lines.append(f"**Path:** {' > '.join(path_parts)}")
        lines.append("")

        text = ctx.get("text", "")
        if text:
            lines.append("**Section text:**")
            lines.append(f"> {text[:400]}")
            lines.append("")

        refs = ctx.get("references", [])
        if refs:
            lines.append("**Cross-references:**")
            for ref in refs[:5]:
                lines.append(f"- {ref['type']} {ref['number']}: {ref['title'][:50]}")
                rtext = ref.get("text", "")
                if rtext:
                    lines.append(f"  > {rtext[:150]}")
            lines.append("")

        defs = ctx.get("definitions", [])
        if defs:
            lines.append("**Definitions used:**")
            for d in defs[:5]:
                lines.append(f'- "{d["term"]}"')
            lines.append("")

        excs = ctx.get("exceptions", [])
        if excs:
            lines.append("**Exceptions:**")
            for e in excs[:3]:
                lines.append(f"- Section {e['number']}: {e['text'][:100]}")
            lines.append("")

    lines.append("**Candidates:**")
    for c in cands[:5]:
        flags = []
        if c.obligation:
            flags.append("obligation")
        if c.setback:
            flags.append("setback")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        nums = ",".join(c.numbers[:5])
        lines.append(f"- p.{c.page} numbers={nums}{flag_str}")
        lines.append(f"  > {c.quote[:120]}")
    if len(cands) > 5:
        lines.append(f"  ... and {len(cands) - 5} more")
    lines.append("")
    lines.append("---")
    lines.append("")

Path("docs").mkdir(exist_ok=True)
Path("docs/rules_review.md").write_text("\n".join(lines), encoding="utf-8")
print(f"Written docs/rules_review.md ({len(lines)} lines)")
