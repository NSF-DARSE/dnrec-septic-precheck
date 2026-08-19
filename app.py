"""DNREC septic permit reviewer console.

    streamlit run app.py

This is the interface a permitting reviewer would be handed. It is deliberately
not a chatbot. There is no message thread, no assistant persona, and nothing that
implies a model produced the answer, because the product claim is that rules
decide and a model does not, and a conversational interface would contradict that
claim before anyone read a word.

The report body is rendered natively from the composed payload. The printable HTML
report produced by render_html is offered as a download and never embedded on
screen. One payload, two presentations: both read compose() output, which is the
single source of truth, so the numbers cannot drift.

Every colour, size and spacing value comes from septic.report.assets, which the
report imports as well. There is no hex literal in this file. The logos are loaded
through that module as data URIs and no logo markup appears here, because the
Delaware seal is an SVG whose root element declares the SVG namespace as a plain
web address, and there is a test asserting that no such address appears anywhere in
this file. That test is what guarantees the console cannot phone home on venue
wifi, so the seal is loaded as a rendered PNG and never inlined as markup.

The sponsor logos sit in a labelled band at the foot of the page, separated from
the product identity. This tool is not a DNREC product and DNREC has not endorsed
it, so putting the department seal beside the product title would be a
misrepresentation in front of the agency itself. See assets/README.md.

Runs with no network and no AWS credentials, serving Textract output from the
on-disk cache keyed by document SHA256. Nothing is fetched from a CDN.
"""
from __future__ import annotations

import base64
import hashlib
import html as html_lib
import sys
import time
from pathlib import Path
from string import Template

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from septic import config  # noqa: E402
from septic import review as review_mod  # noqa: E402
from septic.ingest.textract import TextractClient, document_hash  # noqa: E402
from septic.report.assets import (  # noqa: E402
    ASSET_FILES,
    TOKENS,
    asset_path,
    logo_data_uri,
)
from septic.report.render import VERDICT_COLOR, render_html  # noqa: E402
from septic.report.wording import (  # noqa: E402
    NOT_APPLICABLE_BANNER,
    UNREAD_BANNER,
    unread_note,
)
from septic.rules import engine  # noqa: E402

st.set_page_config(
    page_title="DNREC septic permit application review",
    # A local file, read off disk by Streamlit. Nothing is fetched.
    page_icon=str(asset_path("favicon.png")),
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Styling. Local only, and every value comes from the shared token set.
# ---------------------------------------------------------------------------

STYLE_TEMPLATE = """
:root { --ink:$c_ink; --muted:$c_muted; --line:$c_line; }

.block-container {
  padding-top:$k_top_clearance; padding-bottom:$s_sm; max-width:1600px;
}

html, body { font-family:$f_sans; }

/* Brand band */
.brand-band {
  background:$c_band; color:$c_on_band; padding:$s_lg $s_xl;
  border-radius:$r_lg; margin-bottom:$s_lg;
}
.brand-band-title {
  font-size:$t_section; font-weight:$w_bold; line-height:$lh_tight;
  letter-spacing:-0.015em;
}
.brand-band-sub {
  font-size:$t_caption; color:$c_on_band_muted; margin-top:$s_xs;
}

/* Metric cards row */
.metric-row {
  display:grid; grid-template-columns:1fr 1fr 1fr 2fr;
  gap:$s_md; margin:$s_lg 0; align-items:start;
}
@media (max-width:1100px) {
  .metric-row { grid-template-columns:1fr 1fr; }
}
.metric-card {
  border:$b_hairline solid var(--line); border-radius:$r_md;
  padding:$s_lg $s_xl; background:$c_surface;
}
.metric-card-label {
  font-size:$t_micro; text-transform:uppercase; letter-spacing:0.07em;
  color:var(--muted); margin-bottom:$s_xs;
}
.metric-card-value {
  font-size:$t_verdict; font-weight:$w_bold; line-height:$lh_tight;
  color:var(--ink);
}
.verdict-card { grid-column:span 1; }
.verdict-card-headline {
  font-size:$t_verdict; font-weight:$w_bold; line-height:$lh_tight;
  letter-spacing:-0.02em;
}
.verdict-card-coverage {
  font-size:$t_caption; color:var(--muted); margin-top:$s_xs;
}
/* Segmented bar */
.seg-bar {
  width:100%; height:${s_md}; border-radius:$r_sm; overflow:hidden;
  display:flex; margin-top:$s_md; background:var(--line);
}
.seg-bar-segment { height:100%; }
.seg-legend {
  display:flex; flex-wrap:wrap; gap:$s_lg; margin-top:$s_sm;
  font-size:$t_micro; color:var(--muted);
}
.seg-legend-dot {
  display:inline-block; width:10px; height:10px; border-radius:50%;
  margin-right:$s_xs; vertical-align:middle;
}

/* Provenance line */
.provenance {
  font-size:$t_caption; color:var(--muted); padding:$s_sm 0 $s_md;
  border-bottom:1px solid var(--line); margin-bottom:$s_xs;
}
.provenance b { color:var(--ink); }

/* Findings section headers */
.findings-section {
  font-size:$t_section; font-weight:$w_bold; margin:$s_xl 0 $s_md;
  letter-spacing:-0.01em; color:var(--ink);
}
.findings-section-count {
  font-weight:$w_regular; color:var(--muted);
}

/* Findings table */
.findings-table { border-collapse:collapse; width:100%; font-size:$t_body; table-layout:fixed; }
.findings-table th {
  text-align:left; font-size:$t_micro; text-transform:uppercase;
  letter-spacing:0.07em; color:var(--muted); padding:$s_sm $s_md;
  border-bottom:$b_hairline solid var(--line); white-space:nowrap;
}
.findings-table th.right { text-align:right; }
.findings-table td {
  padding:$s_md; border-bottom:$b_hairline solid var(--line); vertical-align:top;
}
.findings-table td.right { text-align:right; }
.findings-table tr:last-child td { border-bottom:none; }
.ft-rule-id {
  font-family:$f_mono; font-size:$t_micro; color:$c_citation_fg;
  font-weight:$w_medium; white-space:nowrap;
}
.ft-section {
  font-size:$t_micro; color:var(--muted); margin-top:2px; white-space:nowrap;
}
.ft-requirement {
  font-weight:$w_medium; color:var(--ink); line-height:$lh_normal;
}
.ft-reason {
  font-size:$t_caption; color:var(--muted); margin-top:2px;
  line-height:$lh_normal;
}
.ft-value {
  font-family:$f_mono; font-size:$t_body; font-weight:$w_bold;
  white-space:nowrap; font-variant-numeric:tabular-nums;
}
.ft-threshold {
  font-family:$f_mono; font-size:$t_caption; color:var(--muted);
  white-space:nowrap;
}
.ft-citation-chip {
  display:inline-block; background:$c_surface_sunken; color:$c_citation_fg;
  padding:2px $s_sm; border-radius:$r_sm; font-size:$t_micro;
  font-family:$f_mono; white-space:nowrap;
}
.ft-status-pill {
  display:inline-block; padding:2px $s_md; border-radius:$r_md;
  font-size:$t_micro; font-weight:$w_medium; white-space:nowrap;
}
/* De-emphasised not-applicable group */
.findings-table.deemphasised td {
  color:var(--muted); border-left:$b_accent solid $c_out_of_scope_edge;
}
.findings-table.deemphasised .ft-rule-id { color:var(--muted); }
.findings-table.deemphasised .ft-requirement { color:var(--muted); font-weight:$w_regular; }

/* Disclosure for regulation quote */
.ft-quote-toggle {
  font-size:$t_caption; color:$c_citation_fg; cursor:pointer;
  margin-top:$s_xs; display:block;
}
.ft-quote-toggle summary { list-style:none; }
.ft-quote-toggle summary::before { content:'\\25B6 '; font-size:10px; }
.ft-quote-toggle[open] summary::before { content:'\\25BC '; }
.ft-quote-text {
  font-size:$t_caption; font-style:italic; color:$c_citation_fg;
  border-left:$b_rule solid var(--line); padding-left:$s_md;
  margin-top:$s_xs; max-width:70ch; line-height:$lh_normal;
}

/* Map figure card */
.map-card {
  border:$b_hairline solid var(--line); border-radius:$r_md;
  padding:$s_lg $s_xl; background:$c_surface; margin:$s_xl 0;
}
.map-card-caption {
  font-size:$t_caption; color:var(--muted); margin-bottom:$s_md;
  text-transform:uppercase; letter-spacing:0.07em;
}
.map-card img { max-width:100%; border-radius:$r_sm; }
.map-card-dl { margin-top:$s_md; font-size:$t_body; }
.map-card-dl dt {
  font-size:$t_micro; text-transform:uppercase; letter-spacing:0.05em;
  color:var(--muted); margin-top:$s_md;
}
.map-card-dl dd {
  margin:0; padding:0; color:var(--ink); font-family:$f_mono;
  font-size:$t_caption;
}
.map-card-note {
  font-size:$t_caption; color:$c_unverified_fg; background:$c_unverified_bg;
  padding:$s_sm $s_md; border-radius:$r_sm; margin-top:$s_md;
  line-height:$lh_normal;
}

/* Empty state and drop zone */
.empty {
  border:2px dashed var(--line); border-radius:$r_lg; padding:$s_xxxl $s_xl;
  text-align:center; color:var(--muted); margin:$s_lg 0 $s_xl;
}
.empty-title {
  font-size:$t_subhead; font-weight:$w_medium; color:var(--ink);
  margin-bottom:$s_md;
}
.empty p { font-size:$t_body; max-width:78ch; margin:0 auto $s_md; }
.empty b { color:var(--ink); }

.st-key-dropzone [data-testid="stFileUploaderDropzone"] {
  padding:$s_xxxl $s_xl; border:$b_accent dashed var(--line);
  border-radius:$r_lg; flex-direction:column; justify-content:center;
  align-items:center; gap:$s_md; text-align:center;
}
.st-key-dropzone .empty {
  border:0; padding:0; margin:0 0 $s_md;
}

/* Rules reference table */
.rules-table { border-collapse:collapse; width:100%; font-size:$t_body; }
.rules-table th {
  text-align:left; font-size:$t_micro; text-transform:uppercase;
  letter-spacing:0.07em; color:var(--muted); padding:$s_sm $s_md;
  border-bottom:1px solid var(--line);
}
.rules-table td {
  padding:$s_md; border-bottom:1px solid var(--line); vertical-align:top;
}
.rules-table .parameter { font-family:$f_mono; font-size:$t_caption; }
.rules-table .threshold {
  font-family:$f_mono; font-weight:$w_bold; color:$c_clear_fg;
  white-space:nowrap; font-variant-numeric:tabular-nums;
}
.rules-table .section {
  color:var(--muted); white-space:nowrap; font-variant-numeric:tabular-nums;
}
.rules-table .quote {
  color:$c_citation_fg; font-style:italic; border-left:$b_rule solid var(--line);
  padding-left:$s_md; max-width:70ch;
}
.rule-state {
  border-left:$b_accent solid $c_unverified_edge; background:$c_notice_bg;
  color:$c_notice_fg; padding:$s_md $s_lg; font-size:$t_caption;
  margin-top:$s_sm; border-radius:0 $r_sm $r_sm 0;
}

/* PDF viewer */
.pdf-viewer-controls {
  display:flex; align-items:center; gap:$s_md; margin-bottom:$s_sm;
  font-size:$t_caption; color:var(--muted);
}
.pdf-viewer-controls button {
  background:$c_surface_sunken; border:$b_hairline solid var(--line);
  border-radius:$r_sm; padding:$s_xs $s_md; cursor:pointer;
  font-size:$t_caption; color:var(--ink);
}
.pdf-viewer-controls button:disabled { opacity:0.4; cursor:default; }

/* Attribution band */
.band {
  margin:$s_xxxl 0 0; padding:$s_xl $s_xl; background:$c_band;
  color:$c_on_band; border-radius:$r_lg;
}
.band-heading {
  font-size:$t_micro; text-transform:uppercase; letter-spacing:0.11em;
  color:$c_on_band_muted; text-align:center; margin-bottom:$s_lg;
}
.sponsor-strip {
  display:flex; align-items:center; justify-content:center; gap:$s_xxl;
  flex-wrap:wrap;
}
.sponsor-logo { display:block; width:auto; }
.sponsor-logo.circular { height:${circular_h}px; }
.sponsor-logo.wordmark { height:${wordmark_h}px; }
.band-note {
  margin:$s_lg auto 0; max-width:88ch; text-align:center; font-size:$t_caption;
  color:$c_on_band_muted; line-height:$lh_normal;
}

/* Keyboard focus ring */
a:focus-visible, button:focus-visible, input:focus-visible,
select:focus-visible, textarea:focus-visible,
[role="button"]:focus-visible, [data-testid="stFileUploader"] :focus-visible {
  outline:$b_rule solid $c_remedy_fg; outline-offset:2px; border-radius:$r_sm;
}

/* Print */
@media print {
  [data-testid="stFileUploader"],
  [data-testid="stToolbar"], [data-testid="stHeader"] { display:none; }
  .block-container { padding:0; max-width:none; }
  .brand-band, .band {
    -webkit-print-color-adjust:exact; print-color-adjust:exact;
  }
  .empty { display:none; }
}
"""


def stylesheet() -> str:
    """The token set as CSS. Substituted by $name, which CSS never uses."""
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
    values["k_top_clearance"] = f"{TOKENS['chrome']['top_clearance']}px"
    values["f_sans"] = TOKENS["font"]["sans"]
    values["f_mono"] = TOKENS["font"]["mono"]
    values["circular_h"] = TOKENS["sponsor_strip"]["circular_logo_height"]
    values["wordmark_h"] = TOKENS["sponsor_strip"]["wordmark_height"]
    return Template(STYLE_TEMPLATE).substitute(values)


st.markdown(f"<style>{stylesheet()}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data access, cached so reselecting an application is instant.
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def warm_layers() -> int:
    """Load the GIS layers once at start up rather than on the first review."""
    try:
        from septic import geo
        return sum(len(geo.load_layer(n).geometries) for n in geo.available_layers())
    except Exception:  # noqa: BLE001
        return 0


@st.cache_resource(show_spinner=False)
def load_graph_once():
    """The graph adds cross references to a finding. Absence is not fatal."""
    try:
        from septic.rules.graph import load_graph
        return load_graph()
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(show_spinner=False)
def review_from_cache(pdf_path: str) -> dict | None:
    """Run the whole chain from the on-disk cache. No network, no credentials.

    Delegates to septic.review rather than repeating the chain here.
    Returns the composed payload, or None when there is no cached analysis, so the
    caller can explain instead of crashing.
    """
    path = Path(pdf_path)
    client = TextractClient()
    if client.cached_by_hash(document_hash(path.read_bytes())) is None:
        return None
    try:
        result = review_mod.review(
            pdf=path,
            allow_network=False,
            with_precedents=False,
            with_screening=True,
            with_map=True,
        )
    except Exception:  # noqa: BLE001
        return None
    return result.composed.to_json()


# ---------------------------------------------------------------------------
# Verdict colour map (imported from the report renderer)
# ---------------------------------------------------------------------------

BANNER_COLOR = VERDICT_COLOR

SPONSOR_LOGOS = (
    ("dnrec-logo.png", "circular"),
    ("delaware-seal.png", "circular"),
    ("udel-logo.png", "circular"),
    ("fsaii-logo.png", "wordmark"),
)


# ---------------------------------------------------------------------------
# Native rendering functions
# ---------------------------------------------------------------------------

def _data_uri(path_str: str) -> str | None:
    """Read an image off disk and return it as a data URI."""
    p = Path(path_str)
    if not p.is_absolute():
        p = (ROOT / "out" / p).resolve()
    if not p.is_file():
        return None
    kind = "svg+xml" if p.suffix.lower() == ".svg" else p.suffix.lower().lstrip(".")
    return f"data:image/{kind};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def brand_band() -> str:
    """The product identity band at the top of the page."""
    return (
        "<div class='brand-band'>"
        "<div class='brand-band-title'>Septic permit application review</div>"
        "<div class='brand-band-sub'>A first pass over an application packet. "
        "Flags deficiencies and puts the regulation citation next to each one. "
        "The reviewer decides.</div>"
        "</div>"
    )


def metric_row(payload: dict) -> str:
    """Four metric cards: checks ran, not applicable, could not be read, verdict."""
    coverage = payload.get("coverage") or {}
    headline = payload.get("headline", "")
    evaluated = coverage.get("evaluated", 0)
    not_applicable = coverage.get("not_applicable", 0)
    unreadable = coverage.get("unreadable", 0)
    total = coverage.get("total", 0)

    fg, bg = BANNER_COLOR.get(
        headline, (TOKENS["colour"]["ink"], TOKENS["colour"]["surface_sunken"])
    )

    # Segmented bar proportions
    seg_parts = []
    if total > 0:
        if evaluated:
            pct = evaluated / total * 100
            seg_parts.append(
                f"<div class='seg-bar-segment' style='width:{pct:.1f}%;"
                f"background:{TOKENS['colour']['clear_edge']}'></div>"
            )
        if not_applicable:
            pct = not_applicable / total * 100
            seg_parts.append(
                f"<div class='seg-bar-segment' style='width:{pct:.1f}%;"
                f"background:{TOKENS['colour']['out_of_scope_edge']}'></div>"
            )
        if unreadable:
            pct = unreadable / total * 100
            seg_parts.append(
                f"<div class='seg-bar-segment' style='width:{pct:.1f}%;"
                f"background:{TOKENS['colour']['unverified_edge']}'></div>"
            )

    seg_bar = f"<div class='seg-bar'>{''.join(seg_parts)}</div>"

    legend = (
        "<div class='seg-legend'>"
        f"<span><span class='seg-legend-dot' style='background:"
        f"{TOKENS['colour']['clear_edge']}'></span>Ran ({evaluated})</span>"
        f"<span><span class='seg-legend-dot' style='background:"
        f"{TOKENS['colour']['out_of_scope_edge']}'></span>"
        f"Not applicable ({not_applicable})</span>"
        f"<span><span class='seg-legend-dot' style='background:"
        f"{TOKENS['colour']['unverified_edge']}'></span>"
        f"Could not be read ({unreadable})</span>"
        "</div>"
    )

    coverage_text = coverage.get("text", "")

    return (
        "<div class='metric-row'>"
        # Card 1: Checks ran
        "<div class='metric-card'>"
        "<div class='metric-card-label'>CHECKS RAN</div>"
        f"<div class='metric-card-value'>{evaluated}</div>"
        "</div>"
        # Card 2: Not applicable
        "<div class='metric-card'>"
        "<div class='metric-card-label'>NOT APPLICABLE</div>"
        f"<div class='metric-card-value'>{not_applicable}</div>"
        "</div>"
        # Card 3: Could not be read
        "<div class='metric-card'>"
        "<div class='metric-card-label'>COULD NOT BE READ</div>"
        f"<div class='metric-card-value'>{unreadable}</div>"
        "</div>"
        # Card 4: Verdict with bar
        "<div class='metric-card verdict-card'>"
        f"<div class='verdict-card-headline' style='color:{fg}'>{headline}</div>"
        f"<div class='verdict-card-coverage'>{html_lib.escape(coverage_text)}</div>"
        f"{seg_bar}{legend}"
        "</div>"
        "</div>"
    )


def banner(payload: dict) -> str:
    """The verdict and the coverage figure.

    Every number here is read out of the composed payload. Nothing on this screen
    counts anything: the coverage line is coverage["text"] verbatim, which is the
    same string the report body renders when it is opened on its own.
    """
    headline = payload.get("headline", "")
    coverage = payload.get("coverage") or {}
    text = coverage.get("text", "")
    fg, bg = BANNER_COLOR.get(
        headline, (TOKENS["colour"]["ink"], TOKENS["colour"]["surface_sunken"])
    )
    if coverage.get("unreadable"):
        tail = UNREAD_BANNER
    elif coverage.get("not_applicable"):
        tail = NOT_APPLICABLE_BANNER
    else:
        tail = "Every check in the rule set ran against this packet."
    return (
        f"<div class='banner' style='color:{fg};background:{bg}'>"
        f"<div class='banner-verdict'>{headline}</div>"
        f"<div class='banner-coverage'>{text}</div>"
        f"<div class='banner-tail'>{tail}</div>"
        f"</div>"
    )


def _short_rule_id(rule_id: str) -> str:
    """Strip the descriptive tail: ISO-001-disposal-area-to-well -> ISO-001."""
    parts = rule_id.split("-")
    if len(parts) >= 2 and parts[1].isdigit():
        return f"{parts[0]}-{parts[1]}"
    return rule_id


def _requirement_sentence(finding: dict) -> str:
    """Turn the machine expression into a reviewer-readable sentence."""
    from septic.report.wording import requirement_sentence
    return requirement_sentence(finding)


def _status_pill(finding: dict) -> str:
    """A small coloured pill showing the outcome."""
    outcome = finding.get("outcome", "")
    if outcome == "FAIL":
        fg = TOKENS["colour"]["deficiency_fg"]
        bg = TOKENS["colour"]["deficiency_bg"]
        label = "FAIL"
    elif outcome == "PASS":
        if finding.get("applicability") == "not_applicable":
            fg = TOKENS["colour"]["out_of_scope_edge"]
            bg = TOKENS["colour"]["surface_sunken"]
            label = "N/A"
        else:
            fg = TOKENS["colour"]["clear_fg"]
            bg = TOKENS["colour"]["clear_bg"]
            label = "PASS"
    else:
        fg = TOKENS["colour"]["unverified_fg"]
        bg = TOKENS["colour"]["unverified_bg"]
        label = "UNKNOWN"
    return (
        f"<span class='ft-status-pill' style='color:{fg};background:{bg}'>"
        f"{label}</span>"
    )


def findings_table(findings: list[dict], group: str, deemphasised: bool = False) -> str:
    """One group of findings as a table."""
    if not findings:
        return ""

    cls = "findings-table deemphasised" if deemphasised else "findings-table"
    rows = []
    for f in findings:
        rule_id = f.get("rule_id", "")
        short_id = _short_rule_id(rule_id)
        section = html_lib.escape(f.get("section", ""))
        page = f.get("page")

        # Requirement column
        if group == "unresolved":
            req_text = html_lib.escape(unread_note(f))
            reason_html = ""
        else:
            req_text = html_lib.escape(_requirement_sentence(f))
            reason = f.get("reason", "")
            reason_html = (
                f"<div class='ft-reason'>{html_lib.escape(reason)}</div>"
                if reason else ""
            )

        # Value column
        observed = f.get("observed")
        threshold = f.get("threshold")
        units = f.get("units") or ""
        if observed is not None:
            value_html = (
                f"<span class='ft-value'>{html_lib.escape(str(observed))}"
                f" {html_lib.escape(units)}</span>"
            )
            if threshold is not None:
                value_html += (
                    f"<br><span class='ft-threshold'>req: "
                    f"{html_lib.escape(str(threshold))} {html_lib.escape(units)}</span>"
                )
        else:
            value_html = ""

        # Citation chip
        citation_parts = []
        if section:
            citation_parts.append(section)
        if page:
            citation_parts.append(f"p.{page}")
        citation_html = (
            f"<span class='ft-citation-chip'>"
            f"{html_lib.escape(', '.join(citation_parts))}</span>"
            if citation_parts else ""
        )

        # Quote disclosure
        quote = f.get("quote", "")
        quote_html = ""
        if quote:
            quote_html = (
                f"<details class='ft-quote-toggle'>"
                f"<summary>Regulation text</summary>"
                f"<div class='ft-quote-text'>{html_lib.escape(quote)}</div>"
                f"</details>"
            )

        rows.append(
            f"<tr>"
            f"<td><span class='ft-rule-id' title='{html_lib.escape(rule_id)}'>"
            f"{html_lib.escape(short_id)}</span>"
            f"<div class='ft-section'>{section}</div></td>"
            f"<td><div class='ft-requirement'>{req_text}</div>"
            f"{reason_html}{quote_html}</td>"
            f"<td class='right'>{value_html}</td>"
            f"<td>{citation_html}</td>"
            f"<td>{_status_pill(f)}</td>"
            f"</tr>"
        )

    return (
        f"<table class='{cls}'>"
        "<colgroup>"
        "<col style='width:12%'>"
        "<col style='width:40%'>"
        "<col style='width:16%'>"
        "<col style='width:18%'>"
        "<col style='width:14%'>"
        "</colgroup>"
        "<tr>"
        "<th>RULE</th><th>REQUIREMENT</th><th class='right'>VALUE</th>"
        "<th>CITATION</th><th>STATUS</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def map_figure_card(payload: dict) -> str:
    """The location screening as a figure card with measurements as a definition list."""
    screening = payload.get("screening") or {}
    if not screening.get("flags") and not screening.get("figure_png"):
        return ""

    parts = ["<div class='map-card'>"]
    parts.append(
        "<div class='map-card-caption'>Location screening</div>"
    )

    # Map image
    figure_path = screening.get("figure_png")
    if figure_path:
        uri = _data_uri(figure_path)
        if uri:
            parts.append(
                f"<img src='{uri}' alt='Location screening map'>"
            )

    # Measurements as definition list
    parts.append("<dl class='map-card-dl'>")

    point = screening.get("point") or {}
    if point:
        lat = point.get("lat", "")
        lon = point.get("lon", "")
        source = point.get("source", "")
        cross = " (cross-checked)" if point.get("cross_checked") else ""
        parts.append(
            f"<dt>COORDINATES</dt>"
            f"<dd>{lat}, {lon} &mdash; {html_lib.escape(source)}{cross}</dd>"
        )

    nearest = screening.get("nearest_water")
    if nearest:
        dist = nearest.get("distance_feet", 0)
        label = nearest.get("label", "")
        layer = nearest.get("layer", "")
        parts.append(
            f"<dt>NEAREST MAPPED SURFACE WATER</dt>"
            f"<dd>{dist:.0f} ft &mdash; {html_lib.escape(label)} "
            f"({html_lib.escape(layer)})</dd>"
        )

    radius = screening.get("screen_radius_feet")
    if radius:
        parts.append(
            f"<dt>SCREENING RADIUS</dt><dd>{radius} ft</dd>"
        )

    unavailable = screening.get("unavailable")
    if unavailable:
        parts.append(
            f"<dt>UNAVAILABLE LAYERS</dt>"
            f"<dd>{html_lib.escape(', '.join(unavailable) if isinstance(unavailable, list) else str(unavailable))}</dd>"
        )

    parts.append("</dl>")

    # Flags as screening caveat
    flags = screening.get("flags") or []
    if flags:
        caveat = " ".join(flags)
        parts.append(
            f"<div class='map-card-note'>{html_lib.escape(caveat)}</div>"
        )

    parts.append("</div>")
    return "".join(parts)


def attribution_band() -> str:
    """The sponsor strip, labelled, and set apart from the product identity."""
    logos = "".join(
        f"<img class='sponsor-logo {shape}' src='{logo_data_uri(name)}' "
        f"alt='{html_lib.escape(ASSET_FILES[name])}'>"
        for name, shape in SPONSOR_LOGOS
    )
    return (
        "<div class='band'>"
        "<div class='band-heading'>Developed at HENnovate 2026, "
        "University of Delaware, with the support of</div>"
        f"<div class='sponsor-strip'>{logos}</div>"
        "<div class='band-note'>This is a prototype and not a DNREC product. "
        "DNREC has not endorsed it and nothing it produces is a determination. "
        "The reviewer decides.</div>"
        "</div>"
    )


def rules_reference(rules) -> str:
    """Every requirement this checks, as a reference table."""
    rows = []
    for rule in rules:
        citation = html_lib.escape(rule.citation.section or "")
        page = f"page {rule.citation.page}" if rule.citation.page else ""
        if rule.threshold is None:
            threshold = html_lib.escape(rule.operator.value)
        else:
            units = f" {rule.units}" if rule.units else ""
            threshold = html_lib.escape(
                f"{rule.operator.value} {rule.threshold}{units}"
            )
        quote = html_lib.escape(rule.citation.quote or "")
        rows.append(
            "<tr>"
            f"<td><span class='parameter'>{html_lib.escape(rule.parameter)}</span>"
            f"<br>{html_lib.escape(rule.description or '')}</td>"
            f"<td class='threshold'>{threshold}</td>"
            f"<td class='section'>{citation}<br>{page}</td>"
            f"<td class='quote'>{quote}</td>"
            "</tr>"
        )
    return (
        "<table class='rules-table'><tr>"
        "<th>requirement</th><th>threshold</th><th>citation</th>"
        "<th>what the regulation says</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _grouped_unresolved_html(groups: list[dict]) -> str:
    """Render unresolved findings grouped by the parameter that blocked them.

    Instead of 14 rows each repeating the same paragraph, this leads with the
    cause and lists the rules it blocks beneath it.
    """
    parts = []
    for group in groups:
        blocked_by = group.get("blocked_by", "")
        description = html_lib.escape(group.get("description", blocked_by))
        location = group.get("location", "")
        count = group.get("count", 0)
        findings = group.get("findings", [])

        # Group header: the missing value and how many rules it blocks
        if count > 1:
            header = f"{count} checks could not run because <b>{description}</b> was not machine readable."
        else:
            header = f"1 check could not run because <b>{description}</b> was not machine readable."
        if location:
            header += f" It is normally {html_lib.escape(location)}."

        parts.append(
            f"<div style='margin-top:{TOKENS['space']['lg']}px;"
            f"border-left:{TOKENS['border']['accent']}px solid "
            f"{TOKENS['colour']['unverified_edge']};"
            f"padding-left:{TOKENS['space']['lg']}px'>"
            f"<div style='font-size:{TOKENS['type_scale']['body']}px;"
            f"color:{TOKENS['colour']['ink']};margin-bottom:{TOKENS['space']['sm']}px'>"
            f"{header}</div>"
        )

        # List the blocked rules with their citations
        parts.append("<table class='findings-table' style='margin:0'>"
                     "<colgroup>"
                     "<col style='width:14%'>"
                     "<col style='width:54%'>"
                     "<col style='width:32%'>"
                     "</colgroup>"
                     "<tr><th>RULE</th><th>REQUIREMENT</th><th>CITATION</th></tr>")
        for f in findings:
            rule_id = f.get("rule_id", "")
            short_id = _short_rule_id(rule_id)
            section = html_lib.escape(f.get("section", ""))
            page = f.get("page")
            requirement = html_lib.escape(_requirement_sentence(f))
            citation_parts = []
            if section:
                citation_parts.append(section)
            if page:
                citation_parts.append(f"p.{page}")
            citation_html = (
                f"<span class='ft-citation-chip'>"
                f"{html_lib.escape(', '.join(citation_parts))}</span>"
                if citation_parts else ""
            )
            parts.append(
                f"<tr><td><span class='ft-rule-id'>{html_lib.escape(short_id)}</span>"
                f"<div class='ft-section'>{section}</div></td>"
                f"<td>{requirement}</td>"
                f"<td>{citation_html}</td></tr>"
            )
        parts.append("</table></div>")

    return "".join(parts)


def render_findings(payload: dict) -> None:
    """Render all finding groups natively in the console."""
    deficiencies = payload.get("deficiencies") or []
    unresolved = payload.get("unresolved") or []
    satisfied = payload.get("satisfied") or []
    not_applicable = payload.get("not_applicable") or []

    if deficiencies:
        st.markdown(
            f"<div class='findings-section'>Deficiencies found "
            f"<span class='findings-section-count'>({len(deficiencies)})</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(findings_table(deficiencies, "deficiencies"), unsafe_allow_html=True)

    if unresolved:
        st.markdown(
            f"<div class='findings-section'>Could not be evaluated "
            f"<span class='findings-section-count'>({len(unresolved)})</span></div>",
            unsafe_allow_html=True,
        )
        groups = payload.get("unresolved_groups") or []
        if groups:
            st.markdown(_grouped_unresolved_html(groups), unsafe_allow_html=True)
        else:
            st.markdown(findings_table(unresolved, "unresolved"), unsafe_allow_html=True)

    if satisfied:
        st.markdown(
            f"<div class='findings-section'>Checks that passed "
            f"<span class='findings-section-count'>({len(satisfied)})</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(findings_table(satisfied, "satisfied"), unsafe_allow_html=True)

    if not_applicable:
        st.markdown(
            f"<div class='findings-section'>Does not apply to this system "
            f"<span class='findings-section-count'>({len(not_applicable)})</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            findings_table(not_applicable, "not_applicable", deemphasised=True),
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# PDF viewer: rasterise pages, page controls.
# ---------------------------------------------------------------------------

_PAGE_CACHE: dict[str, dict[int, str]] = {}


def _rasterise_pages(pdf_bytes: bytes, doc_hash: str) -> list[str]:
    """Render every page as a base64 JPEG data URI, cached by document hash.

    JPEG rather than PNG, and scale 1.5 rather than 2, because a scanned
    nineteen page packet has to travel into the iframe as one payload and a
    lossless render of every page is several times larger than the browser
    needs to display it at this width.
    """
    cached = _PAGE_CACHE.get(doc_hash)
    if cached is not None:
        return cached
    import io
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    uris = []
    for page in doc:
        img = page.render(scale=1.5).to_pil().convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80, optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        uris.append(f"data:image/jpeg;base64,{encoded}")
    _PAGE_CACHE[doc_hash] = uris
    return uris


def _viewer_html(page_uris: list[str], start_page: int = 1) -> str:
    """A scrolling stack of every page, one after another.

    Each page carries an anchor so a finding can scroll its own page into
    view, and a small label so the reviewer always knows where they are.
    """
    blocks = []
    for i, uri in enumerate(page_uris, start=1):
        blocks.append(
            f"<div class='pg' id='page-{i}'>"
            f"<div class='pg-label'>page {i} of {len(page_uris)}</div>"
            f"<img src='{uri}' loading='lazy' alt='page {i}'>"
            f"</div>"
        )
    scroll_to = ""
    if start_page > 1:
        scroll_to = (
            "<script>document.getElementById('page-%d')"
            ".scrollIntoView();</script>" % start_page
        )
    return (
        "<style>"
        "body{margin:0;background:%s;}"
        ".pg{margin:0 0 %dpx;}"
        ".pg-label{font:%dpx %s;color:%s;padding:%dpx 0;}"
        ".pg img{width:100%%;display:block;border:1px solid %s;}"
        "</style>"
        "<div class='pgs'>%s</div>%s"
    ) % (
        TOKENS["colour"]["surface_sunken"],
        TOKENS["space"]["lg"],
        TOKENS["type_scale"]["micro"],
        TOKENS["font"]["sans"],
        TOKENS["colour"]["muted"],
        TOKENS["space"]["xs"],
        TOKENS["colour"]["line"],
        "".join(blocks),
        scroll_to,
    )


def render_pdf_viewer(pdf_bytes: bytes, doc_hash: str, payload: dict) -> None:
    """Render the packet as one continuously scrollable column of pages."""
    page_uris = _rasterise_pages(pdf_bytes, doc_hash)
    if not page_uris:
        return
    page_key = f"viewer_page_{doc_hash[:16]}"
    start_page = st.session_state.get(page_key, 1)
    components.html(
        _viewer_html(page_uris, start_page), height=900, scrolling=True
    )


def jump_to_page(doc_hash: str, page: int) -> None:
    """Set the viewer to show a specific page."""
    page_key = f"viewer_page_{doc_hash[:16]}"
    st.session_state[page_key] = page


# ---------------------------------------------------------------------------
# Rules and toggle, in the main column.
# ---------------------------------------------------------------------------

warm_layers()
load_graph_once()

PACKET = "application_packet"

rules = engine.load_rules()


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

st.markdown(brand_band(), unsafe_allow_html=True)


def drop_zone():
    """The empty state and the uploader as one control."""
    legend = " ".join(
        f"<b style='color:{BANNER_COLOR[name][0]}'>{name}</b>{rest}"
        for name, rest in (
            ("DEFICIENCIES FOUND", ", each item cited."),
            ("NO DEFICIENCIES FOUND", ", which is not an approval."),
            ("CANNOT VERIFY", ", when nothing could be checked."),
        )
    )
    with st.container(key="dropzone"):
        st.markdown(
            "<div class='empty'>"
            "<div class='empty-title'>Drop an application packet here.</div>"
            f"<p>Three answers are possible. {legend}</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        return st.file_uploader(
            "Application PDF", type=["pdf"], key=PACKET,
            label_visibility="collapsed",
        )


if st.session_state.get(PACKET) is None:
    uploaded = drop_zone()
else:
    with st.expander("Review a different packet", expanded=False):
        uploaded = st.file_uploader(
            "Application PDF", type=["pdf"], key=PACKET,
            label_visibility="collapsed",
        )

if uploaded is not None:
    data = uploaded.getvalue()
    doc_hash = document_hash(data)
    client = TextractClient()
    cached = client.cached_by_hash(doc_hash)

    if cached is not None and cached.ok:
        uploads = config.OUT_DIR / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / uploaded.name
        target.write_bytes(data)
        started = time.perf_counter()
        with st.spinner("Reading the packet and applying the rules"):
            payload = review_from_cache(str(target))
        elapsed = time.perf_counter() - started
        if payload:
            subject = payload.get("subject") or {}
            # Provenance line - whole seconds, or drop it
            elapsed_str = f"{int(elapsed)} s" if elapsed >= 1.0 else None
            time_part = f" &nbsp;|&nbsp; reviewed in {elapsed_str}" if elapsed_str else ""
            st.markdown(
                f"<div class='provenance'><b>{subject.get('document', '')}</b>"
                f" &nbsp;|&nbsp; {subject.get('pages', 0)} pages"
                f"{time_part}</div>",
                unsafe_allow_html=True,
            )

            # Metric row with segmented bar
            st.markdown(metric_row(payload), unsafe_allow_html=True)

            # Notices from the payload, rendered above the findings so both the
            # console and the printable report show them from the same source.
            for notice in payload.get("notices") or []:
                st.markdown(
                    f"<div class='rule-state'>{html_lib.escape(notice)}</div>",
                    unsafe_allow_html=True,
                )

            # Split layout: findings left, PDF viewer right.
            findings_col, viewer_col = st.columns([3, 2])

            with findings_col:
                # Findings rendered natively
                render_findings(payload)

                # Map figure card
                map_html = map_figure_card(payload)
                if map_html:
                    st.markdown(map_html, unsafe_allow_html=True)

                # Download the printable HTML report
                html_report = render_html(payload, embedded=False)
                doc_name = subject.get("document", "report")
                st.download_button(
                    label="Download printable report",
                    data=html_report,
                    file_name=f"review_{doc_name}.html",
                    mime="text/html",
                )

            with viewer_col:
                render_pdf_viewer(data, doc_hash, payload)
    else:
        st.info(
            f"**{uploaded.name} has not been analysed yet.**\n\n"
            f"Reading a new packet means running Amazon Textract over it, which "
            f"needs AWS credentials. This console is serving the local cache and "
            f"is not configured for live analysis, so nothing was sent anywhere "
            f"and nothing was changed.\n\n"
            f"Document fingerprint `{doc_hash[:16]}`\n\n"
            f"To add it to the cache, run this where credentials are available:\n\n"
            f"`python -m septic review --pdf {uploaded.name}`"
        )

# ---------------------------------------------------------------------------
# Rules toggle and reference, always visible at the foot of the review.
# ---------------------------------------------------------------------------

st.markdown("---")
rule_cols = st.columns([3, 1])
with rule_cols[0]:
    st.caption(
        f"{len(rules)} requirements taken from the 2014 regulation, each one "
        f"carrying the section and page it comes from."
    )
with rule_cols[1]:
    show_rules = st.toggle("Show all rules", value=False)

if show_rules:
    st.markdown(f"### The {len(rules)} requirements this checks")
    st.caption(
        "Every one is quoted from the 2014 regulation. The section and page are "
        "shown so any of them can be read back at the source."
    )
    st.markdown(rules_reference(rules), unsafe_allow_html=True)

st.markdown(attribution_band(), unsafe_allow_html=True)
