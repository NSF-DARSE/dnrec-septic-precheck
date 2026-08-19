"""Rendering a composed report to text or HTML.

Every finding shows its citation: section number, page, and the verbatim quote. A
reviewer has to be able to check the requirement against the regulation without
taking this tool's word for it, and anyone told to move a drainfield 40 feet is
owed the sentence that requires it.

The text renderer is for the terminal. The HTML renderer is for projection, so it
is sized for reading at a distance: one clear verdict at the top, the coverage
figure directly under it in the same box, findings as a scannable list, citation
attached to each one. Coverage is not optional decoration. A verdict of NO
DEFICIENCIES FOUND over seven of fifteen checks and the same verdict over all
fifteen are different statements, so neither renderer is allowed to show one
without the other. No external stylesheet, no fonts to fetch, no JavaScript,
because it has to open from a file on a laptop with no network.
"""
from __future__ import annotations

import html
from string import Template

from .assets import TOKENS
from .wording import (UNREAD_HEADING, UNREAD_INTRO, reason_sentence,
                      requirement_sentence, unread_note)

RULE = "=" * 78
THIN = "-" * 78


def _data_uri(path) -> str | None:
    """Read an image off disk and return it as a data URI.

    The HTML report is viewed two ways that both break a relative src: embedded
    in the console through an iframe, where a relative path resolves against the
    server rather than the out directory, and opened straight from the file
    system after being moved. Inlining the bytes makes the file self contained,
    which is also what the no remote reference rule already requires of the SVG.

    Returns None when the figure is missing, so a report without a map still
    renders rather than showing a broken image.
    """
    import base64
    from pathlib import Path

    p = Path(path)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[3] / "out" / p).resolve()
    if not p.is_file():
        return None
    kind = "svg+xml" if p.suffix.lower() == ".svg" else p.suffix.lower().lstrip(".")
    return f"data:image/{kind};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"

# Foreground and background per verdict. The meanings are load bearing and a
# reviewer reads the page by them: one colour for a deficiency found, one for
# nothing found, one for no answer. The values come from the token set, which the
# console imports as well, so the banner on screen and the box in the report are
# the same colour by construction rather than by two people remembering a hex.
VERDICT_COLOR = {
    "NO DEFICIENCIES FOUND": (
        TOKENS["colour"]["clear_fg"], TOKENS["colour"]["clear_bg"],
    ),
    "DEFICIENCIES FOUND": (
        TOKENS["colour"]["deficiency_fg"], TOKENS["colour"]["deficiency_bg"],
    ),
    "CANNOT VERIFY": (
        TOKENS["colour"]["unverified_fg"], TOKENS["colour"]["unverified_bg"],
    ),
}


def _wrap(text: str, width: int = 76, indent: str = "") -> list[str]:
    """Wrap without importing textwrap, keeping indentation explicit."""
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) + len(indent) > width and current:
            lines.append(indent + current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(indent + current)
    return lines


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def render_text(composed) -> str:
    c = composed if isinstance(composed, dict) else composed.to_json()
    L: list[str] = []
    add = L.append

    add(RULE)
    add("DNREC SEPTIC PERMIT APPLICATION REVIEW")
    add(RULE)

    subject = c.get("subject") or {}
    for key in ("document", "permit_number", "detail_id", "pages"):
        if subject.get(key):
            add(f"{key.replace('_', ' '):<16}{subject[key]}")
    add(f"{'generated':<16}{c.get('generated_at', '')}")
    add("")

    coverage = c.get("coverage") or {}
    counts = c.get("counts") or {}
    # Read verbatim. Deriving this from counts would double count the rules that
    # did not apply, because the engine reports those as passes.
    coverage_text = coverage.get("text", "")

    add(f"VERDICT:  {c['headline']}")
    add(f"COVERAGE: {coverage_text.upper()}")
    add("")
    for line in _wrap(c["explanation"]):
        add(line)
    add("")

    add(f"checks: {coverage.get('evaluated', 0)} compared a value, "
        f"{counts.get('fail', 0)} of those failed, "
        f"{coverage.get('not_applicable', 0)} not applicable to this system, "
        f"{coverage.get('unreadable', 0)} could not be read")
    add("")

    for notice in c.get("notices") or []:
        add("NOTICE")
        for line in _wrap(notice, indent="  "):
            add(line)
        add("")

    deficiencies = c.get("deficiencies") or []
    if deficiencies:
        add(RULE)
        add(f"DEFICIENCIES ({len(deficiencies)})")
        add(RULE)
        for i, f in enumerate(deficiencies, 1):
            add("")
            add(f"{i}. {requirement_sentence(f)}")
            add(f"   rule {f['rule_id']}  severity {f['severity']}")
            for line in _wrap(reason_sentence(f), indent="   "):
                add(line)
            if f.get("observed") is not None:
                add(f"   read from the packet: {f['observed']}")
            if f.get("provenance"):
                add(f"   value came from {f['provenance']}")
            add(f"   CITATION {f['citation']}")
            if f.get("quote"):
                for line in _wrap(f'"{f["quote"]}"', indent="     "):
                    add(line)
            if f.get("remedy"):
                add("   TO FIX")
                for line in _wrap(f["remedy"], indent="     "):
                    add(line)
            for ref in f.get("cross_references") or []:
                add(f"   see also {ref['label']}: {ref['title']}")
            for exc in f.get("exceptions") or []:
                add(f"   exception in {exc['section']}")
                for line in _wrap(exc["text"], indent="     "):
                    add(line)
        add("")

    unresolved = c.get("unresolved") or []
    if unresolved:
        add(RULE)
        add(f"{UNREAD_HEADING.upper()} ({len(unresolved)})")
        add(RULE)
        add("")
        for line in _wrap(UNREAD_INTRO):
            add(line)
        add("")
        groups = c.get("unresolved_groups") or []
        if groups:
            for group in groups:
                blocked_by = group.get("blocked_by", "")
                description = group.get("description", blocked_by)
                location = group.get("location", "")
                count = group.get("count", 0)
                findings = group.get("findings", [])
                if count > 1:
                    header = (f"{count} checks could not run because "
                              f"{description} was not machine readable.")
                else:
                    header = (f"1 check could not run because "
                              f"{description} was not machine readable.")
                if location:
                    header += f" It is normally {location}."
                for line in _wrap(header, indent="  "):
                    add(line)
                for f in findings:
                    citation = f.get("citation", "")
                    add(f"    {f['rule_id']}  [{citation}]")
                add("")
        else:
            for f in unresolved:
                add(f"  {f['rule_id']}")
                for line in _wrap(unread_note(f), indent="    "):
                    add(line)
                add("")

    missing = c.get("missing_information") or []
    if missing:
        add(RULE)
        add(f"MISSING INFORMATION ({len(missing)})")
        add(RULE)
        add("")
        for line in _wrap(
            "Values the rules needed and this packet did not provide. A missing "
            "field is itself a reason an application gets returned."
        ):
            add(line)
        add("")
        for m in missing:
            add(f"  {m['parameter']}")
            add(f"    {m['means']}")
            if m.get("normally_found"):
                for line in _wrap(
                    f"normally {m['normally_found']}", indent="    "
                ):
                    add(line)
            add(f"    blocks: {', '.join(m['blocks_rules'])}")
        add("")

    discarded = c.get("discarded_readings") or []
    if discarded:
        add(RULE)
        add(f"READINGS DISCARDED AS UNRELIABLE ({len(discarded)})")
        add(RULE)
        add("")
        for line in _wrap(
            "Values that were found on the page and then thrown away, because they "
            "were implausible or came from a field that could not be paired "
            "confidently. They are listed so that nothing is discarded silently. "
            "Each one is treated as unreadable, never as a pass."
        ):
            add(line)
        add("")
        for d in discarded:
            page = f" page {d['page']}" if d.get("page") else ""
            add(f"  {d['parameter']}{page}")
            for line in _wrap(reason_sentence(d), indent="    "):
                add(line)
        add("")

    not_applicable = c.get("not_applicable") or []
    if not_applicable:
        add(RULE)
        add(f"NOT APPLICABLE TO THIS SYSTEM ({len(not_applicable)})")
        add(RULE)
        add("")
        for line in _wrap(
            "These rules were not applied to this packet, because they govern a "
            "different kind of system. They are not requirements this application "
            "met, and they are not counted as checks that ran. The value that "
            "took each one out of scope is shown, so a reviewer who reads that "
            "value differently knows exactly which check to bring back."
        ):
            add(line)
        add("")
        for f in not_applicable:
            add(f"  {f['rule_id']}: {requirement_sentence(f)}")
            add(f"    {reason_sentence(f)}")
            excluded = f.get("excluded_by") or {}
            if excluded.get("parameter"):
                add(f"    {excluded['parameter']} read as "
                    f"{excluded.get('value')!r}")
                if excluded.get("where"):
                    add(f"    value came from {excluded['where']}")
            add(f"    citation {f['citation']}")
        add("")

    satisfied = c.get("satisfied") or []
    if satisfied:
        add(RULE)
        add(f"REQUIREMENTS MET ({len(satisfied)})")
        add(RULE)
        for f in satisfied:
            add(f"  {f['rule_id']}: {reason_sentence(f)}  [{f['citation']}]")
        add("")

    facts = c.get("facts_read") or []
    if facts:
        add(RULE)
        add(f"VALUES READ FROM THE PACKET ({len(facts)})")
        add(RULE)
        for fact in facts:
            add(f"  {fact['parameter']:<36}{fact['value']}")
            add(f"    from {fact['where']}")
        add("")

    screening = c.get("screening") or {}
    if screening.get("flags"):
        add(RULE)
        add("LOCATION SCREENING")
        add(RULE)
        add("")
        for line in _wrap(
            "Computed from the permit's mapped coordinates against state "
            "hydrography layers. This is a screening prompt, not a measurement of "
            "compliance: the regulation measures isolation distance from the "
            "disposal area, and this measures from the geocoded address point, "
            "which is somewhere else on the parcel. It tells a reviewer what to "
            "check on the site plan."
        ):
            add(line)
        add("")
        point = screening.get("point") or {}
        if point:
            add(f"  location  {point.get('lat')}, {point.get('lon')}  "
                f"from {point.get('source')}")
            if point.get("cross_checked"):
                add("  coordinates cross checked against the Geocoded Location "
                    "column")
        nearest = screening.get("nearest_water")
        if nearest:
            add(f"  nearest mapped surface water  "
                f"{nearest['distance_feet']:.0f} ft, {nearest['label']} "
                f"({nearest['layer']})")
        add("")
        for flag in screening["flags"]:
            for line in _wrap(flag, indent="  "):
                add(line)
            add("")

    precedents = c.get("precedents") or {}
    entries = precedents.get("precedents") or []
    if entries:
        add(RULE)
        add(f"SIMILAR PRIOR PERMITS ({len(entries)})")
        add(RULE)
        add("")
        for line in _wrap(precedents.get("caveat", "")):
            add(line)
        add("")
        for p in entries:
            add(f"  permit {p.get('permit_number') or p['detail_id']}  "
                f"status {p['status']}  similarity {p['score']:.3f}")
            for line in _wrap(p["summary"], indent="    "):
                add(line)
        add("")
        for line in _wrap(precedents.get("limits", "")):
            add(line)
        add("")

    add(RULE)
    for line in _wrap(
        "This is a first pass for the reviewer, not a decision. The verdict and "
        "finding above were produced by rules traced to the 2014 Delaware On-Site "
        "Wastewater regulation. The reviewer decides."
    ):
        add(line)
    add(f"wording: {c.get('wording_source', '')}")
    add(RULE)

    return "\n".join(L)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

# The stylesheet is a template rather than an f-string because CSS is mostly
# braces, and every value in it comes from the token set the console reads too.
# Substitution is by $name, which CSS never uses, so nothing has to be escaped.
CSS_TEMPLATE = """
:root {
  --ink:$c_ink; --muted:$c_muted; --line:$c_line; --bg:$c_surface;
}
* { box-sizing:border-box; }
body {
  margin:0; padding:$s_xxl $s_xxxl $s_xxxl; background:var(--bg); color:var(--ink);
  font:$t_body_large/$lh_normal $f_sans;
}
.wrap { max-width:1100px; margin:0 auto; }
header {
  border-bottom:$b_rule solid var(--ink); padding-bottom:$s_lg;
  margin-bottom:$s_xl;
}
h1 { font-size:$t_title; margin:0 0 $s_xs; letter-spacing:-0.015em; }
.meta { color:var(--muted); font-size:$t_caption; }
.meta span { margin-right:$s_xl; white-space:nowrap; }
.meta b { color:var(--ink); font-variant-numeric:tabular-nums; }

/* The verdict box. Coverage sits inside it on purpose: a headline of NO
   DEFICIENCIES FOUND means nothing without it, and anything placed outside the
   box gets skimmed past or cropped out of a screenshot. */
.verdict {
  padding:$s_xl $s_xl; border-radius:$r_lg; margin:0 0 $s_md;
  border:2px solid currentColor;
}
.verdict h2 {
  margin:0; font-size:$t_verdict; letter-spacing:-0.02em; line-height:$lh_tight;
}
.verdict .coverage {
  margin:$s_md 0 0; font-size:$t_section; font-weight:$w_medium;
  letter-spacing:-0.01em;
}
.verdict p {
  margin:$s_md 0 0; font-size:$t_body; max-width:74ch; color:var(--ink);
}
.counts { font-size:$t_body; color:var(--muted); margin:$s_lg 0 $s_xxl; }
.counts b { color:var(--ink); font-variant-numeric:tabular-nums; }
.notice {
  border-left:$b_accent solid $c_unverified_edge; background:$c_notice_bg;
  color:$c_notice_fg; padding:$s_lg $s_xl; margin:0 0 $s_lg;
  font-size:$t_body; border-radius:0 $r_sm $r_sm 0;
}
h3 {
  font-size:$t_caption; text-transform:uppercase; letter-spacing:0.09em;
  color:var(--muted); margin:$s_xxl 0 $s_lg; padding-bottom:$s_sm;
  border-bottom:1px solid var(--line);
}

/* A finding. The failed ones have to be unmissable from across a desk, so they
   get the accent, the larger requirement line and the severity chip, and their
   citation sits immediately under the reason rather than at the end of the
   page. */
.finding {
  border:1px solid var(--line); border-radius:$r_md; padding:$s_xl $s_xl;
  margin:0 0 $s_lg;
}
.finding.fail { border-left:$b_accent solid $c_deficiency_edge; }
.finding.unknown { border-left:$b_accent solid $c_unverified_edge; }
.finding.pass { border-left:$b_accent solid $c_clear_edge; }
.req { font-size:$t_subhead; font-weight:$w_medium; margin:0 0 $s_xs; }
.finding.fail .req { font-size:$t_section; letter-spacing:-0.01em; }
.rule-id {
  font:$t_micro/1.4 $f_mono; color:var(--muted); text-transform:none;
}
.chip {
  display:inline-block; font:$w_bold $t_micro/1 $f_sans; text-transform:uppercase;
  letter-spacing:0.08em; padding:$s_xs $s_sm; border-radius:$r_sm;
  color:$c_deficiency_fg; background:$c_deficiency_bg; margin-left:$s_sm;
}
.chip.advisory { color:$c_unverified_fg; background:$c_unverified_bg; }
.reason { margin:$s_md 0; font-size:$t_body_large; }
.observed { font-size:$t_body; color:var(--muted); margin:$s_xs 0; }
.observed b { color:var(--ink); font-family:$f_mono; }
.cite {
  margin:$s_lg 0 0; padding:$s_lg $s_xl; background:$c_surface_quote;
  border-left:$b_accent solid $c_citation_fg; border-radius:0 $r_md $r_md 0;
}
.cite .where {
  font-weight:$w_bold; font-size:$t_micro; text-transform:uppercase;
  letter-spacing:0.08em; color:$c_citation_fg; margin-bottom:$s_sm;
  font-variant-numeric:tabular-nums;
}
/* The verbatim regulation text. This is the difference between a tool that
   asserts and a tool that cites, so it outweighs the finding text above it
   rather than sitting below it as a footnote. */
.cite blockquote {
  margin:0; font-style:italic; color:var(--ink); font-size:$t_body_large;
  line-height:$lh_normal; max-width:88ch;
}
.fix {
  margin:$s_lg 0 0; padding:$s_md $s_lg; background:$c_remedy_bg;
  color:$c_remedy_fg; border-radius:$r_sm; font-size:$t_body;
}
.fix b {
  display:block; font-size:$t_micro; text-transform:uppercase;
  letter-spacing:0.07em; color:$c_remedy_fg; margin-bottom:$s_xs;
}
.aside { font-size:$t_caption; color:var(--muted); margin:$s_md 0 0; }
table { border-collapse:collapse; width:100%; font-size:$t_body; }
th,td {
  text-align:left; padding:$s_sm $s_md; border-bottom:1px solid var(--line);
  vertical-align:top;
}
th {
  font-size:$t_micro; text-transform:uppercase; letter-spacing:0.07em;
  color:var(--muted);
}
td code { font-variant-numeric:tabular-nums; }
/* A rule that was never applied must not look like a requirement that was met.
   The met table carries the same green the passing findings use. The out of
   scope table is deliberately grey and set back, because it is context, not a
   result: nothing on this packet was compared against these rules. */
table.met { border-left:$b_accent solid $c_clear_edge; }
table.out-of-scope {
  border-left:$b_accent solid $c_out_of_scope_edge; background:$c_surface_sunken;
  color:$c_citation_fg;
}
table.met th, table.met td,
table.out-of-scope th, table.out-of-scope td { padding-left:$s_lg; }
table.out-of-scope code { color:$c_citation_fg; }
code { font:$t_caption $f_mono; }
.caveat { font-size:$t_body; color:var(--muted); max-width:84ch; margin:0 0 $s_lg; }

/* The location map. It is the one picture in the report and it is the easiest
   thing to misread, so it is framed as a figure with its caption attached rather
   than dropped into the flow. */
figure.map {
  margin:$s_lg 0 0; padding:0; border:1px solid var(--line);
  border-radius:$r_md; overflow:hidden; background:var(--bg);
}
figure.map img { display:block; width:100%; height:auto; }
figure.map figcaption {
  padding:$s_lg $s_xl; border-top:1px solid var(--line);
  background:$c_surface_sunken; font-size:$t_body; color:var(--ink);
}
figure.map figcaption b { display:block; margin-bottom:$s_xs; }
figure.map figcaption .not-a-determination {
  display:block; margin-top:$s_sm; color:$c_notice_fg; font-weight:$w_medium;
}
figure.map figcaption .figure-detail {
  display:block; margin-top:$s_sm; color:var(--muted); font-size:$t_caption;
  font-variant-numeric:tabular-nums;
}
footer {
  margin-top:$s_xxxl; padding-top:$s_lg; border-top:1px solid var(--line);
  font-size:$t_body; color:var(--muted); max-width:84ch;
}

/* A reviewer prints this and puts it in the file. Backgrounds are kept, because
   the verdict box and the out of scope table carry meaning in their colour, and
   nothing that belongs together is allowed to break across a page. */
@media print {
  body { padding:0; font-size:11pt; }
  .wrap { max-width:none; }
  .verdict, .notice, .fix, table.out-of-scope, table.met {
    -webkit-print-color-adjust:exact; print-color-adjust:exact;
  }
  .finding, figure.map, tr { break-inside:avoid; }
  .cite { break-inside:avoid; }
  h3 { break-after:avoid; }
}
"""


def _css() -> str:
    """The stylesheet with every token substituted in.

    Built once at import. The console reads the same tokens, so the report and the
    screen around it cannot drift: there is one palette, one type scale and one
    spacing scale in this project and both surfaces import it.
    """
    values: dict[str, object] = {
        f"c_{name}": value for name, value in TOKENS["colour"].items()
    }
    values.update(
        {f"t_{name}": f"{value}px" for name, value in TOKENS["type_scale"].items()}
    )
    values.update(
        {f"s_{name}": f"{value}px" for name, value in TOKENS["space"].items()}
    )
    values.update(
        {f"r_{name}": f"{value}px" for name, value in TOKENS["radius"].items()}
    )
    values.update(
        {f"b_{name}": f"{value}px" for name, value in TOKENS["border"].items()}
    )
    values.update(
        {f"lh_{name}": value for name, value in TOKENS["line_height"].items()}
    )
    values.update({f"w_{name}": value for name, value in TOKENS["weight"].items()})
    values["f_sans"] = TOKENS["font"]["sans"]
    values["f_mono"] = TOKENS["font"]["mono"]
    return Template(CSS_TEMPLATE).substitute(values)


CSS = _css()


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def render_html(composed, embedded: bool = False) -> str:
    """The report as an HTML page.

    embedded is for the console, which puts this report in an iframe with its own
    verdict banner a few pixels above it. Both surfaces were doing their job
    correctly and independently, and the result on screen was the headline, the
    coverage line and the explanation paragraph printed twice within about a
    hundred pixels of each other. In embedded mode the identity header, the
    verdict box and the counts line are left out, because the banner directly
    above already carries every number in them.

    The default renders all of it, and that is not a detail. A reviewer prints this
    report or opens it from disk with no console around it, so on its own it has to
    say which document it is about and what the verdict was. That is why this is a
    render mode rather than a deletion.

    Nothing else differs between the two modes. The findings, the citations, the
    quoted regulation text, the screening and the footer are identical, so the page
    a reviewer prints cannot say anything the screen did not.
    """
    c = composed if isinstance(composed, dict) else composed.to_json()
    fg, bg = VERDICT_COLOR.get(
        c["headline"], (TOKENS["colour"]["ink"], TOKENS["colour"]["surface_sunken"])
    )

    H: list[str] = []
    add = H.append

    add("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    add("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    add(f"<title>Septic permit review: {_esc(c['headline'])}</title>")
    add(f"<style>{CSS}</style></head><body><div class='wrap'>")

    coverage = c.get("coverage") or {}
    counts = c.get("counts") or {}

    if not embedded:
        add("<header><h1>DNREC septic permit application review</h1>"
            "<div class='meta'>")
        subject = c.get("subject") or {}
        for key in ("document", "permit_number", "detail_id", "pages"):
            if subject.get(key):
                label = key.replace("_", " ")
                add(f"<span>{_esc(label)}: <b>{_esc(subject[key])}</b></span>")
        add(f"<span>generated {_esc(c.get('generated_at'))}</span>")
        add("</div></header>")

        add(f"<div class='verdict' style='color:{fg};background:{bg}'>")
        add(f"<h2>{_esc(c['headline'])}</h2>")
        # Verbatim, like every other surface. Deriving it from counts would count
        # the rules that never applied, since the engine reports those as passes.
        add(f"<p class='coverage'>{_esc(coverage.get('text', ''))}</p>")
        add(f"<p>{_esc(c['explanation'])}</p></div>")

        add(f"<p class='counts'><b>{coverage.get('evaluated', 0)}</b> compared a "
            f"value &nbsp; <b>{counts.get('fail', 0)}</b> of those failed &nbsp; "
            f"<b>{coverage.get('not_applicable', 0)}</b> not applicable to this "
            f"system &nbsp; <b>{coverage.get('unreadable', 0)}</b> could not be "
            f"read</p>")

    for notice in c.get("notices") or []:
        add(f"<div class='notice'>{_esc(notice)}</div>")

    deficiencies = c.get("deficiencies") or []
    if deficiencies:
        add(f"<h3>Deficiencies ({len(deficiencies)})</h3>")
        for i, f in enumerate(deficiencies, 1):
            add("<div class='finding fail'>")
            severity = str(f.get("severity") or "")
            chip = (
                "<span class='chip'>Would be returned for correction</span>"
                if severity == "return"
                else "<span class='chip advisory'>Advisory</span>"
            )
            add(f"<p class='req'>{i}. {_esc(requirement_sentence(f))}{chip}</p>")
            add(f"<div class='rule-id'>{_esc(f['rule_id'])}</div>")
            add(f"<p class='reason'>{_esc(reason_sentence(f))}</p>")
            if f.get("observed") is not None:
                add(f"<p class='observed'>Read from the packet: "
                    f"<b>{_esc(f['observed'])}</b></p>")
            if f.get("provenance"):
                add(f"<p class='observed'>Value came from {_esc(f['provenance'])}</p>")
            add("<div class='cite'>")
            add(f"<div class='where'>{_esc(f['citation'])}</div>")
            if f.get("quote"):
                add(f"<blockquote>{_esc(f['quote'])}</blockquote>")
            add("</div>")
            if f.get("remedy"):
                add(f"<div class='fix'><b>To fix</b>{_esc(f['remedy'])}</div>")
            for ref in f.get("cross_references") or []:
                add(f"<p class='aside'>See also {_esc(ref['label'])}: "
                    f"{_esc(ref['title'])}</p>")
            for exc in f.get("exceptions") or []:
                add(f"<p class='aside'>Exception in {_esc(exc['section'])}: "
                    f"{_esc(exc['text'])}</p>")
            add("</div>")

    unresolved = c.get("unresolved") or []
    if unresolved:
        add(f"<h3>{_esc(UNREAD_HEADING)} ({len(unresolved)})</h3>")
        add(f"<p class='caveat'>{_esc(UNREAD_INTRO)}</p>")
        groups = c.get("unresolved_groups") or []
        if groups:
            for group in groups:
                blocked_by = group.get("blocked_by", "")
                description = _esc(group.get("description", blocked_by))
                location = group.get("location", "")
                count = group.get("count", 0)
                findings = group.get("findings", [])
                if count > 1:
                    header = (f"{count} checks could not run because "
                              f"<b>{description}</b> was not machine readable.")
                else:
                    header = (f"1 check could not run because "
                              f"<b>{description}</b> was not machine readable.")
                if location:
                    header += f" It is normally {_esc(location)}."
                add(f"<div class='finding unknown'><p class='reason'>"
                    f"{header}</p>")
                add("<table><tr><th>rule</th><th>requirement</th>"
                    "<th>citation</th></tr>")
                for f in findings:
                    add(f"<tr><td><code>{_esc(f['rule_id'])}</code></td>"
                        f"<td>{_esc(requirement_sentence(f))}</td>"
                        f"<td>{_esc(f['citation'])}</td></tr>")
                add("</table></div>")
        else:
            add("<table><tr><th>rule</th>"
                "<th>what has to be read, where it is, and what it is measured "
                "against</th></tr>")
            for f in unresolved:
                add(f"<tr><td><code>{_esc(f['rule_id'])}</code></td>"
                    f"<td>{_esc(unread_note(f))}</td></tr>")
            add("</table>")

    # The screening sits here, directly under the checks that could not run,
    # because most of those are isolation distances on a scanned drawing and this
    # is the prompt for exactly them. It is context, not a finding, and says so in
    # its own heading and caption.
    H.extend(_screening_block(c))

    missing = c.get("missing_information") or []
    if missing:
        add(f"<h3>Missing information ({len(missing)})</h3>")
        add("<p class='caveat'>Values the rules needed that this packet did not "
            "provide. A missing field is itself a reason an application gets "
            "returned.</p>")
        add("<table><tr><th>value</th><th>meaning</th><th>normally found</th>"
            "<th>blocks</th></tr>")
        for m in missing:
            add(f"<tr><td><code>{_esc(m['parameter'])}</code></td>"
                f"<td>{_esc(m['means'])}</td>"
                f"<td>{_esc(m.get('normally_found') or '')}</td>"
                f"<td><code>{_esc(', '.join(m['blocks_rules']))}</code></td></tr>")
        add("</table>")

    discarded = c.get("discarded_readings") or []
    if discarded:
        add(f"<h3>Readings discarded as unreliable ({len(discarded)})</h3>")
        add("<p class='caveat'>Values found on the page and then thrown away, "
            "because they were implausible or came from a field that could not be "
            "paired confidently. Listed so that nothing is discarded silently. "
            "Each one is treated as unreadable, never as a pass.</p>")
        add("<table><tr><th>value</th><th>page</th><th>why it was discarded</th></tr>")
        for d in discarded:
            add(f"<tr><td><code>{_esc(d['parameter'])}</code></td>"
                f"<td>{_esc(d.get('page') or '')}</td>"
                f"<td>{_esc(reason_sentence(d))}</td></tr>")
        add("</table>")

    not_applicable = c.get("not_applicable") or []
    if not_applicable:
        add(f"<h3>Not applicable to this system ({len(not_applicable)})</h3>")
        add("<p class='caveat'>These rules were not applied to this packet, "
            "because they govern a different kind of system. They are not "
            "requirements this application met, and they are not counted as "
            "checks that ran. The value that took each one out of scope is shown "
            "with it, so a reviewer who reads that value differently knows which "
            "check to bring back.</p>")
        add("<table class='out-of-scope'><tr><th>rule</th><th>requirement</th>"
            "<th>why it was not applied</th><th>value that excluded it</th>"
            "<th>citation</th></tr>")
        for f in not_applicable:
            excluded = f.get("excluded_by") or {}
            where = ""
            if excluded.get("parameter"):
                where = (f"{excluded['parameter']} = "
                         f"{excluded.get('value')!r}")
                if excluded.get("where"):
                    where += f", from {excluded['where']}"
            add(f"<tr><td><code>{_esc(f['rule_id'])}</code></td>"
                f"<td>{_esc(requirement_sentence(f))}</td>"
                f"<td>{_esc(reason_sentence(f))}</td>"
                f"<td>{_esc(where)}</td>"
                f"<td>{_esc(f['citation'])}</td></tr>")
        add("</table>")

    satisfied = c.get("satisfied") or []
    if satisfied:
        add(f"<h3>Requirements met ({len(satisfied)})</h3>")
        add("<p class='caveat'>Each of these compared a value read off the packet "
            "against the threshold in the regulation.</p>")
        add("<table class='met'><tr><th>rule</th><th>result</th>"
            "<th>citation</th></tr>")
        for f in satisfied:
            add(f"<tr><td><code>{_esc(f['rule_id'])}</code></td>"
                f"<td>{_esc(reason_sentence(f))}</td><td>{_esc(f['citation'])}</td></tr>")
        add("</table>")

    facts = c.get("facts_read") or []
    if facts:
        add(f"<h3>Values read from the packet ({len(facts)})</h3>")
        add("<table><tr><th>value</th><th>read as</th><th>where it came from</th></tr>")
        for fact in facts:
            add(f"<tr><td><code>{_esc(fact['parameter'])}</code></td>"
                f"<td><b>{_esc(fact['value'])}</b></td>"
                f"<td>{_esc(fact['where'])}</td></tr>")
        add("</table>")

    precedents = c.get("precedents") or {}
    entries = precedents.get("precedents") or []
    if entries:
        add(f"<h3>Similar prior permits ({len(entries)})</h3>")
        add(f"<p class='caveat'>{_esc(precedents.get('caveat'))}</p>")
        add("<table><tr><th>permit</th><th>recorded status</th>"
            "<th>similarity</th><th>characteristics</th></tr>")
        for p in entries:
            add(f"<tr><td>{_esc(p.get('permit_number') or p['detail_id'])}</td>"
                f"<td>{_esc(p['status'])}</td>"
                f"<td>{p['score']:.3f}</td>"
                f"<td>{_esc(p['summary'])}</td></tr>")
        add("</table>")
        add(f"<p class='caveat'>{_esc(precedents.get('limits'))}</p>")

    add("<footer>This is a first pass for the reviewer, not a decision. The "
        "verdict, the coverage figure and every finding were produced by rules "
        "traced to the Delaware Regulations "
        "Governing On-Site Wastewater Treatment and Disposal Systems, January 11, "
        "2014. The reviewer decides.<br>")
    add(f"Wording: {_esc(c.get('wording_source'))}</footer>")
    add("</div></body></html>")

    return "\n".join(H)


def _screening_block(c: dict) -> list[str]:
    """The location screening, as a figure with its caption attached.

    The map is the one picture in the report and the easiest thing in it to
    misread, because it looks like a measurement. Every claim about what it is and
    is not travels with the image rather than sitting in a paragraph somewhere
    above it, so a screenshot of the figure carries its own caveat.
    """
    screening = c.get("screening") or {}
    if not screening.get("flags"):
        return []

    out: list[str] = []
    add = out.append
    subject = c.get("subject") or {}
    permit = subject.get("permit_number")
    point = screening.get("point") or {}
    nearest = screening.get("nearest_water")
    radius = screening.get("screen_radius_feet")

    add("<h3>Location screening, not a finding</h3>")
    add("<p class='caveat'>Computed from the permit's mapped coordinates against "
        "state hydrography layers. It tells a reviewer what to check on the site "
        "plan. No rule cites it and it cannot change the verdict above.</p>")

    add("<table>")
    if point:
        checked = " (cross checked against Geocoded Location)" if point.get(
            "cross_checked") else ""
        add(f"<tr><th>coordinates</th><td><code>{_esc(point.get('lat'))}, "
            f"{_esc(point.get('lon'))}</code>{_esc(checked)}, from "
            f"{_esc(point.get('source'))}</td></tr>")
    if nearest:
        add(f"<tr><th>nearest mapped surface water</th><td>"
            f"<b>{nearest['distance_feet']:.0f} ft</b> to "
            f"{_esc(nearest['label'])} ({_esc(nearest['layer'])})</td></tr>")
    if radius:
        add(f"<tr><th>screening radius</th><td>{_esc(f'{radius:.0f}')} ft from the "
            f"address point</td></tr>")
    add("</table>")

    for flag in screening["flags"]:
        add(f"<div class='notice'>{_esc(flag)}</div>")

    figure = screening.get("figure_png")
    src = _data_uri(figure) if figure else None
    if not src:
        return out

    subject_label = f"permit {_esc(permit)}" if permit else "this permit"
    alt = (
        f"Map of mapped surface water and hydrography around the geocoded address "
        f"point for {subject_label}, with a scale bar in feet and a north arrow"
    )
    add("<figure class='map'>")
    add(f"<img src='{src}' alt='{alt}'>")
    add("<figcaption>")
    add(f"<b>Mapped surface water around the address point for "
        f"{subject_label}.</b>")
    add("Centred on the geocoded address point, which is marked, with every mapped "
        "hydrography feature in the window drawn and the nearest one labelled. The "
        "scale bar is in feet, because the regulation is written in feet, and north "
        "is up the arrow.")
    if nearest:
        add(f"<span class='figure-detail'>Nearest mapped surface water "
            f"{nearest['distance_feet']:.0f} ft, {_esc(nearest['label'])}"
            + (f". Screening radius {radius:.0f} ft." if radius else ".")
            + "</span>")
    add("<span class='not-a-determination'>This is a screening prompt, never a "
        "compliance determination. The regulation measures an isolation distance "
        "from the absorption facility. This is measured from the geocoded address "
        "point, which is somewhere else on the parcel, so the tool supplies "
        "dist_point_to_mapped_water and deliberately never "
        "dist_disposal_to_watercourse. The distance on the site plan is the one "
        "that counts.</span>")
    add("</figcaption></figure>")
    return out
