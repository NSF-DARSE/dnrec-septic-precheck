"""Generate docs/rules_review.md, the human verification checklist.

A DNREC subject matter expert reads this to certify rules. It is optimized for
reading straight down a list: one self contained block per rule carrying the id,
section, page, threshold, units, the verbatim quote, and every cross reference and
definition the graph resolves, so nothing requires opening the 245 page PDF.

Reads the persisted graph from out/reg_graph.json. Run "python -m septic graph
build" first if that file does not exist.

Usage:
    python scripts/gen_rules_review.py
"""
from __future__ import annotations

from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.rules.engine import load_rules
from septic.rules.graph import context, graph_summary, load_graph, orphans, unresolved

OUT = Path("docs/rules_review.md")

# Candidates that were read and deliberately not promoted. Kept here rather than
# discarded, because a threshold nobody could confirm is a finding about the
# document, and the next person should not have to rediscover it.
REJECTIONS = [
    {
        "candidate": "Limiting zone at least 48 inches beneath the soil surface",
        "section": "5.3.12.1.3",
        "page": 61,
        "reason": (
            "The operator is unreadable. The sentence extracts as 'a minimum of "
            "three (3) feet below the bottom of the trench t 48 inches beneath the "
            "soil surface', where 't' is a glyph PDFium failed to map. Comparing "
            "the parallel wording in 5.3.12.5.3 on page 62 suggests the missing "
            "character joins two separate requirements, but that is inference, not "
            "reading. The 3 feet below trench bottom half of the same sentence was "
            "promoted as SEP-001 because it is spelled out in words."
        ),
    },
    {
        "candidate": "Minimum design percolation rate of 20 minutes per inch",
        "section": "5.3.2.1",
        "page": 55,
        "reason": (
            "Real requirement, wrong parameter. The text forbids designing with a "
            "rate below 20 minutes per inch, which constrains the design figure, "
            "not the measured site rate. Sections 5.3.2.2 through 5.3.2.5 confirm "
            "this by repeating 'minimum rate is 20 mpi for design' while still "
            "allowing faster soils, and 5.3.2.4 requires a pressurized system "
            "below 6 mpi rather than rejecting the site. Mapping this onto the "
            "measured perc rate would fail sandy sites the regulation permits."
        ),
    },
    {
        "candidate": "Minimum of three soil borings or two test pits per acre",
        "section": "5.2.1.9.8",
        "page": 45,
        "reason": (
            "Two alternative satisfying conditions joined by 'or' cannot be one "
            "numeric comparison, and the engine has no operator for 'either A or "
            "B'. Promoting the borings half alone would fail a packet that "
            "correctly used test pits."
        ),
    },
    {
        "candidate": "Trench systems permitted on slopes steeper than 15 percent",
        "section": "5.3.12.1.2",
        "page": 60,
        "reason": (
            "The condition is a licence class, not a measurement: steeper than 15 "
            "percent is allowed only where a licensed Class C designer prepared "
            "the design. The threshold is checkable but the exemption depends on a "
            "fact about the designer that the extractor does not yet produce, so "
            "the rule would fire on compliant applications."
        ),
    },
    {
        "candidate": "Public or industrial well isolation distance of 150 feet",
        "section": "Exhibit C note d",
        "page": 174,
        "reason": (
            "Confirmed in the source and worth promoting later, but it needs a fact "
            "distinguishing a public or industrial well from a domestic one, which "
            "the extractor does not produce. Shipping it without that fact would "
            "either never fire or fire on every domestic well. Recorded in "
            "ISO-001 notes so the reviewer sees the interaction."
        ),
    },
    {
        "candidate": "Assigned percolation rate floor of 60 or 75 minutes per inch",
        "section": "5.2.1.3.1.4.1",
        "page": 44,
        "reason": (
            "The operator is unreadable, same glyph problem: 'For systems with a "
            "separation distance of  24 inches'. Which side of 24 inches selects "
            "the 75 mpi floor and which selects 60 cannot be read from the text, "
            "and the two branches give different answers."
        ),
    },
]


def fmt_threshold(rule) -> str:
    if rule.threshold is None:
        return "no threshold (presence check)"
    units = f" {rule.units}" if rule.units else ""
    return f"{rule.operator.value} {rule.threshold}{units}"


def main() -> int:
    G = load_graph()
    rules = load_rules()
    summary = graph_summary(G)

    L: list[str] = []
    add = L.append

    add("# Rule verification checklist")
    add("")
    add(f"{len(rules)} rules are staged for certification. Every one is")
    add("`verified: false`, so the engine returns UNKNOWN for all of them and the")
    add("verdict for any application is CANNOT VERIFY. That is the intended state.")
    add("")
    add("Each block below is self contained. The verbatim quote is the text the")
    add("threshold came from, and the cross references and definitions the rule")
    add("depends on are inlined underneath it, so a value can be confirmed without")
    add("opening the PDF. Page numbers are given for the cases where you want to.")
    add("")
    add("## How to certify a rule")
    add("")
    add("1. Read the quote and confirm it says what the threshold claims.")
    add("2. Read the caveats. Several distances have reductions the Department can")
    add("   approve, and one rule must not fire on replacement systems at all.")
    add("3. Confirm `applies_to` matches the systems the requirement governs.")
    add("4. If correct, set `verified: true` in")
    add("   `src/septic/rules/rules_7101.yaml` and record your name and the date")
    add("   in `notes`.")
    add("5. If wrong, leave it unverified and record why. An unverified rule is")
    add("   invisible to reviewers, which is the safe direction to fail.")
    add("")
    add("## Regulation")
    add("")
    add("Delaware Regulations Governing On-Site Wastewater Treatment and Disposal")
    add("Systems, January 11, 2014. 245 pages.")
    add("`docs/regulations/de-onsite-wastewater-2014.pdf`")
    add("")
    add("Graph backing this document: "
        f"{summary['nodes_by_type'].get('Section', 0)} sections, "
        f"{summary['nodes_by_type'].get('Exhibit', 0)} exhibits, "
        f"{summary['nodes_by_type'].get('Definition', 0)} definitions, "
        f"{summary['total_edges']} edges.")
    add("")

    # Summary table first, so the reviewer can see the whole job at a glance.
    add("## All rules at a glance")
    add("")
    add("| # | rule | requirement | citation | page |")
    add("| --- | --- | --- | --- | --- |")
    for i, rule in enumerate(rules, 1):
        add(f"| {i} | `{rule.id}` | {fmt_threshold(rule)} | "
            f"{rule.citation.section} | {rule.citation.page} |")
    add("")
    add("---")
    add("")

    for i, rule in enumerate(rules, 1):
        add(f"## {i}. {rule.id}")
        add("")
        add(f"- Requirement: **{fmt_threshold(rule)}**")
        add(f"- Parameter checked: `{rule.parameter}`")
        add(f"- Citation: **{rule.citation.section}, page {rule.citation.page}**")
        add(f"- Severity if failed: {rule.severity.value}")
        applies = rule.applies_to or {}
        if applies:
            parts = []
            for k, v in applies.items():
                shown = ", ".join(v) if isinstance(v, list) else str(v)
                parts.append(f"`{k}` = {shown}")
            add(f"- Applies when: {'; '.join(parts)}")
        else:
            add("- Applies when: always, the requirement is unconditional")
        add(f"- Verified: **{rule.verified}**")
        add("")
        add(f"{rule.description.strip()}")
        add("")
        add("**Verbatim text from the regulation**")
        add("")
        quote = " ".join(rule.citation.quote.split())
        add(f"> {quote}")
        add("")

        # Graph context for the cited section, inlined.
        section = rule.citation.section
        if not section.lower().startswith("exhibit"):
            ctx = context(G, section)
            if "error" not in ctx:
                if ctx.get("ancestors"):
                    trail = " > ".join(
                        f"{a['number']} {a['title'][:40]}".strip()
                        for a in ctx["ancestors"]
                    )
                    add(f"**Where this sits:** {trail}")
                    add("")
                body = " ".join((ctx.get("text") or "").split())
                if body:
                    add("**Rest of the cited section**")
                    add("")
                    add(f"> {body[:900]}")
                    add("")
                refs = ctx.get("references") or []
                if refs:
                    add("**Cross references, resolved**")
                    add("")
                    for r in refs[:6]:
                        label = f"{r['type']} {r['number']}".strip()
                        add(f"- {label}: {r['title'][:70]}")
                        rtext = " ".join((r.get("text") or "").split())
                        if rtext:
                            add(f"  > {rtext[:320]}")
                    add("")
                defs = ctx.get("definitions") or []
                if defs:
                    add("**Defined terms used in this section**")
                    add("")
                    for d in defs[:8]:
                        add(f"- \"{d['term']}\" (defined in {d['defined_in']})")
                    add("")
                excs = ctx.get("exceptions") or []
                if excs:
                    add("**Sections that carry an exception to this one**")
                    add("")
                    for e in excs[:4]:
                        etext = " ".join((e.get("text") or "").split())
                        add(f"- {e['number']}: {etext[:300]}")
                    add("")
        else:
            letter = section.split()[-1].upper()
            node_id = f"exhibit:{letter}"
            if node_id in G:
                ex = G.nodes[node_id]
                add(f"**{section} content, page {ex.get('page')}**")
                add("")
                extext = ex.get("text") or ""
                if extext:
                    # Show enough lines for the whole table. Truncating mid table
                    # would hide the row the rule depends on, which defeats the
                    # purpose of inlining it.
                    add("```")
                    for line in extext.splitlines()[:44]:
                        add(line)
                    add("```")
                else:
                    add("Not readable from the text layer. This exhibit is a "
                        "scanned figure, so the value must be confirmed on paper.")
                add("")

        # Unread dependencies. The whole point of the graph.
        dep = unresolved(G, rule.id)
        if "error" not in dep:
            if dep["unresolved_count"]:
                add("**Dependencies nobody has read yet**")
                add("")
                for u in dep["unresolved"]:
                    add(f"- {u['type']} {u['number']} reached via "
                        f"{u['reached_via']}: content not extracted")
                add("")
                add("Do not certify this rule until these are read on paper.")
                add("")
            else:
                add("Dependency check: every section and exhibit this rule "
                    "depends on has been read.")
                add("")

        add("**What was read, and what to watch for**")
        add("")
        add(" ".join(rule.notes.split()))
        add("")
        add("**Remedy shown to the applicant**")
        add("")
        add(" ".join(rule.remedy.split()))
        add("")
        add("**Certification**")
        add("")
        add("- [ ] Quote matches the PDF at the cited page")
        add("- [ ] Threshold and units are correct")
        add("- [ ] `applies_to` matches the systems governed")
        add("- [ ] Caveats above are acceptable")
        add("- Checked by: ____________________  Date: ____________")
        add("")
        add("---")
        add("")

    # Rejections.
    add("## Candidates read and not promoted")
    add("")
    add("A threshold nobody could confirm is a rejection, not a guess. These were")
    add("read and left out, with the reason. Several are promotable once the")
    add("extractor produces one more fact, or once someone checks the paper copy.")
    add("")
    for r in REJECTIONS:
        add(f"### {r['candidate']}")
        add("")
        add(f"- Source: {r['section']}, page {r['page']}")
        add(f"- Why not promoted: {r['reason']}")
        add("")

    # Coverage gap.
    gaps = orphans(G)
    add("## Coverage gap")
    add("")
    add(f"{len(gaps)} sections in the regulation use obligation language (shall,")
    add("must, minimum) and are not cited by any rule. That is the backlog, and it")
    add("is the honest measure of how much of the regulation this tool does not yet")
    add("check. The first 40 are listed as a starting point for the next round.")
    add("")
    add("| section | page | opening text |")
    add("| --- | --- | --- |")
    for g in gaps[:40]:
        title = g["title"][:80].replace("|", " ")
        add(f"| {g['section']} | {g['page']} | {title} |")
    add("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(L)} lines, {len(rules)} rules, "
          f"{len(REJECTIONS)} rejections, {len(gaps)} coverage gaps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
