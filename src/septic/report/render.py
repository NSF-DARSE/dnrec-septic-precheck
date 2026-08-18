"""Rendering a composed report to text or HTML.

Every finding shows its citation: section number, page, and the verbatim quote. A
reviewer has to be able to check the requirement against the regulation without
taking this tool's word for it, and anyone told to move a drainfield 40 feet is
owed the sentence that requires it.

The text renderer is for the terminal. The HTML renderer is for projection, so it
is sized for reading at a distance: one clear verdict at the top, findings as a
scannable list, citation attached to each one. No external stylesheet, no fonts to
fetch, no JavaScript, because it has to open from a file on a laptop with no
network.
"""
from __future__ import annotations

import html

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

VERDICT_COLOR = {
    "NO DEFICIENCIES FOUND": ("#1b4332", "#d8f3dc"),
    "DEFICIENCIES FOUND": ("#7f1d1d", "#fee2e2"),
    "CANNOT VERIFY": ("#78350f", "#fef3c7"),
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
    for key in ("document", "permit_number", "detail_id", "pages", "source"):
        if subject.get(key):
            add(f"{key.replace('_', ' '):<16}{subject[key]}")
    add(f"{'generated':<16}{c.get('generated_at', '')}")
    add("")

    add(f"VERDICT: {c['headline']}")
    add("")
    for line in _wrap(c["explanation"]):
        add(line)
    add("")

    counts = c.get("counts") or {}
    add(f"checks: {counts.get('pass', 0)} passed, {counts.get('fail', 0)} failed, "
        f"{counts.get('unknown', 0)} could not be evaluated")
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
            add(f"{i}. {f['requirement']}")
            add(f"   rule {f['rule_id']}  severity {f['severity']}")
            for line in _wrap(f["reason"], indent="   "):
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
        add(f"COULD NOT BE EVALUATED ({len(unresolved)})")
        add(RULE)
        add("")
        for line in _wrap(
            "These checks did not run. That is not the same as passing. A rule "
            "listed here either needs a value the packet did not supply, or needs "
            "a person to confirm its threshold against the regulation."
        ):
            add(line)
        add("")
        for f in unresolved:
            add(f"  {f['rule_id']}")
            add(f"    {f['reason']}")
            add(f"    citation {f['citation']}")
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
            for line in _wrap(d["reason"], indent="    "):
                add(line)
        add("")

    satisfied = c.get("satisfied") or []
    if satisfied:
        add(RULE)
        add(f"REQUIREMENTS MET ({len(satisfied)})")
        add(RULE)
        for f in satisfied:
            add(f"  {f['rule_id']}: {f['reason']}  [{f['citation']}]")
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

CSS = """
:root { --ink:#111827; --muted:#4b5563; --line:#d1d5db; --bg:#ffffff; }
* { box-sizing:border-box; }
body {
  margin:0; padding:32px 40px 64px; background:var(--bg); color:var(--ink);
  font:19px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
.wrap { max-width:1100px; margin:0 auto; }
header { border-bottom:3px solid var(--ink); padding-bottom:14px; margin-bottom:26px; }
h1 { font-size:26px; margin:0 0 6px; letter-spacing:-0.01em; }
.meta { color:var(--muted); font-size:16px; }
.meta span { margin-right:22px; white-space:nowrap; }
.verdict {
  padding:24px 28px; border-radius:10px; margin:0 0 12px;
  border:2px solid currentColor;
}
.verdict h2 { margin:0; font-size:42px; letter-spacing:-0.02em; line-height:1.1; }
.verdict p { margin:12px 0 0; font-size:18px; max-width:74ch; color:var(--ink); }
.counts { font-size:17px; color:var(--muted); margin:14px 0 30px; }
.counts b { color:var(--ink); }
.notice {
  border-left:5px solid #b45309; background:#fffbeb; padding:14px 18px;
  margin:0 0 18px; font-size:17px;
}
h3 {
  font-size:15px; text-transform:uppercase; letter-spacing:0.09em;
  color:var(--muted); margin:36px 0 14px; padding-bottom:7px;
  border-bottom:1px solid var(--line);
}
.finding { border:1px solid var(--line); border-radius:9px; padding:20px 22px; margin:0 0 16px; }
.finding.fail { border-left:7px solid #b91c1c; }
.finding.unknown { border-left:7px solid #b45309; }
.finding.pass { border-left:7px solid #15803d; }
.req { font-size:22px; font-weight:650; margin:0 0 6px; }
.rule-id { font:14px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; color:var(--muted); }
.reason { margin:10px 0; font-size:18px; }
.observed { font-size:17px; color:var(--muted); margin:6px 0; }
.cite {
  margin:16px 0 0; padding:16px 20px; background:#f9fafb;
  border-left:6px solid #374151; border-radius:0 8px 8px 0;
}
.cite .where {
  font-weight:700; font-size:14px; text-transform:uppercase;
  letter-spacing:0.08em; color:#374151; margin-bottom:9px;
}
/* The verbatim regulation text. This is the difference between a tool that
   asserts and a tool that cites, so it outweighs the finding text above it
   rather than sitting below it as a footnote. */
.cite blockquote {
  margin:0; font-style:italic; color:#111827; font-size:19px;
  line-height:1.5; max-width:88ch;
}
.fix { margin:14px 0 0; padding:13px 16px; background:#eff6ff; border-radius:6px; font-size:17.5px; }
.fix b { display:block; font-size:14px; text-transform:uppercase; letter-spacing:0.07em; color:#1e40af; margin-bottom:5px; }
.aside { font-size:15.5px; color:var(--muted); margin:10px 0 0; }
table { border-collapse:collapse; width:100%; font-size:16.5px; }
th,td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:13.5px; text-transform:uppercase; letter-spacing:0.07em; color:var(--muted); }
code { font:15px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.caveat { font-size:16px; color:var(--muted); max-width:84ch; margin:0 0 14px; }
footer {
  margin-top:44px; padding-top:16px; border-top:1px solid var(--line);
  font-size:16px; color:var(--muted); max-width:84ch;
}
@media print { body { padding:0; font-size:11pt; } .finding { break-inside:avoid; } }
"""


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def render_html(composed) -> str:
    c = composed if isinstance(composed, dict) else composed.to_json()
    fg, bg = VERDICT_COLOR.get(c["headline"], ("#111827", "#f3f4f6"))

    H: list[str] = []
    add = H.append

    add("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    add("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    add(f"<title>Septic permit review: {_esc(c['headline'])}</title>")
    add(f"<style>{CSS}</style></head><body><div class='wrap'>")

    add("<header><h1>DNREC septic permit application review</h1><div class='meta'>")
    subject = c.get("subject") or {}
    for key in ("document", "permit_number", "detail_id", "pages"):
        if subject.get(key):
            label = key.replace("_", " ")
            add(f"<span>{_esc(label)}: <b>{_esc(subject[key])}</b></span>")
    add(f"<span>generated {_esc(c.get('generated_at'))}</span>")
    add("</div></header>")

    add(f"<div class='verdict' style='color:{fg};background:{bg}'>")
    add(f"<h2>{_esc(c['headline'])}</h2>")
    add(f"<p>{_esc(c['explanation'])}</p></div>")

    counts = c.get("counts") or {}
    add(f"<p class='counts'><b>{counts.get('pass', 0)}</b> passed &nbsp; "
        f"<b>{counts.get('fail', 0)}</b> failed &nbsp; "
        f"<b>{counts.get('unknown', 0)}</b> could not be evaluated</p>")

    for notice in c.get("notices") or []:
        add(f"<div class='notice'>{_esc(notice)}</div>")

    deficiencies = c.get("deficiencies") or []
    if deficiencies:
        add(f"<h3>Deficiencies ({len(deficiencies)})</h3>")
        for i, f in enumerate(deficiencies, 1):
            add("<div class='finding fail'>")
            add(f"<p class='req'>{i}. {_esc(f['requirement'])}</p>")
            add(f"<div class='rule-id'>{_esc(f['rule_id'])} &middot; "
                f"severity {_esc(f['severity'])}</div>")
            add(f"<p class='reason'>{_esc(f['reason'])}</p>")
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
        add(f"<h3>Could not be evaluated ({len(unresolved)})</h3>")
        add("<p class='caveat'>These checks did not run, which is not the same as "
            "passing. Each one either needs a value the packet did not supply, or "
            "needs a person to confirm its threshold against the regulation.</p>")
        add("<table><tr><th>rule</th><th>why</th><th>citation</th></tr>")
        for f in unresolved:
            add(f"<tr><td><code>{_esc(f['rule_id'])}</code></td>"
                f"<td>{_esc(f['reason'])}</td><td>{_esc(f['citation'])}</td></tr>")
        add("</table>")

    missing = c.get("missing_information") or []
    if missing:
        add(f"<h3>Missing information ({len(missing)})</h3>")
        add("<p class='caveat'>Values the rules needed that this packet did not "
            "provide. A missing field is itself a reason an application gets "
            "returned.</p>")
        add("<table><tr><th>value</th><th>meaning</th><th>blocks</th></tr>")
        for m in missing:
            add(f"<tr><td><code>{_esc(m['parameter'])}</code></td>"
                f"<td>{_esc(m['means'])}</td>"
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
                f"<td>{_esc(d['reason'])}</td></tr>")
        add("</table>")

    satisfied = c.get("satisfied") or []
    if satisfied:
        add(f"<h3>Requirements met ({len(satisfied)})</h3>")
        add("<table><tr><th>rule</th><th>result</th><th>citation</th></tr>")
        for f in satisfied:
            add(f"<tr><td><code>{_esc(f['rule_id'])}</code></td>"
                f"<td>{_esc(f['reason'])}</td><td>{_esc(f['citation'])}</td></tr>")
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

    screening = c.get("screening") or {}
    if screening.get("flags"):
        add("<h3>Location screening</h3>")
        add("<p class='caveat'>Computed from the permit's mapped coordinates "
            "against state hydrography layers. This is a screening prompt, not a "
            "measurement of compliance: the regulation measures isolation distance "
            "from the disposal area, and this measures from the geocoded address "
            "point, which is somewhere else on the parcel. It tells a reviewer "
            "what to check on the site plan.</p>")
        point = screening.get("point") or {}
        nearest = screening.get("nearest_water")
        add("<table>")
        if point:
            checked = " (cross checked against Geocoded Location)" if point.get(
                "cross_checked") else ""
            add(f"<tr><th>coordinates</th><td>{_esc(point.get('lat'))}, "
                f"{_esc(point.get('lon'))}{_esc(checked)}</td></tr>")
        if nearest:
            add(f"<tr><th>nearest mapped surface water</th><td>"
                f"<b>{nearest['distance_feet']:.0f} ft</b> to "
                f"{_esc(nearest['label'])} ({_esc(nearest['layer'])})</td></tr>")
        add("</table>")
        for flag in screening["flags"]:
            add(f"<div class='notice'>{_esc(flag)}</div>")
        figure = screening.get("figure_png")
        if figure:
            src = _data_uri(figure)
            if src:
                add(f"<p><img src='{src}' alt='Location map' "
                    f"style='max-width:100%;border:1px solid #d1d5db;"
                    f"border-radius:8px'></p>")

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
        "every finding were produced by rules traced to the Delaware Regulations "
        "Governing On-Site Wastewater Treatment and Disposal Systems, January 11, "
        "2014. The reviewer decides.<br>")
    add(f"Wording: {_esc(c.get('wording_source'))}</footer>")
    add("</div></body></html>")

    return "\n".join(H)
