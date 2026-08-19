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
    reason_sentence,
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
  padding-top:$k_top_clearance; padding-bottom:$s_sm; max-width:100%;
  padding-left:$s_xl; padding-right:$s_xl;
}

html, body { font-family:$f_sans; background:$c_surface_sunken; }
[data-testid="stApp"], [data-testid="stMain"] { background:$c_surface_sunken; }
[data-testid="stHeader"] { background:transparent; }

/* Brand band */
.brand-band {
  background:$c_band; color:$c_on_band; padding:$s_md $s_xl;
  border-radius:$r_lg; margin-bottom:$s_md; min-height:60px;
  display:flex; align-items:center; gap:$s_lg;
}
/* A small mark so the band is an identity rather than a dark rectangle. Three
   bars standing for the three outcomes, in the colours they carry everywhere
   else, which is the one visual idea this product actually has. */
.brand-mark {
  display:flex; align-items:flex-end; gap:3px; height:26px; flex:none;
}
.brand-mark i {
  display:block; width:5px; border-radius:2px;
}
.brand-mark i:nth-child(1) { height:16px; background:$c_deficiency_edge; }
.brand-mark i:nth-child(2) { height:26px; background:$c_clear_edge; }
.brand-mark i:nth-child(3) { height:10px; background:$c_unverified_edge; }
.brand-band-text { display:flex; flex-direction:column; gap:1px; }
.brand-band-title {
  font-size:$t_subhead; font-weight:$w_bold; line-height:$lh_tight;
  letter-spacing:-0.02em;
}
.brand-band-sub {
  font-size:$t_micro; color:$c_on_band_muted; letter-spacing:0.08em;
  text-transform:uppercase; font-weight:$w_medium;
}
.brand-band-doc {
  margin-left:auto; text-align:right; display:flex; flex-direction:column;
  gap:1px; padding-left:$s_xl;
  border-left:$b_hairline solid rgba(255,255,255,0.14);
}
.brand-band-doc-name {
  font-family:$f_mono; font-size:$t_caption; color:$c_on_band;
}
.brand-band-doc-meta {
  font-size:$t_micro; color:$c_on_band_muted;
  letter-spacing:0.06em; text-transform:uppercase;
}

/* Verdict strip, pinned under the brand band */
.verdict-strip {
  display:flex; align-items:center; gap:$s_xxl; padding:$s_lg $s_xl;
  background:$c_surface; border:$b_hairline solid var(--line);
  border-radius:$r_lg; margin-bottom:$s_lg;
  position:sticky; top:0; z-index:100; flex-wrap:wrap;
}
.verdict-strip-main { display:flex; flex-direction:column; gap:2px; min-width:0; }
.verdict-strip-headline {
  font-size:clamp(24px, 2.6vw, $t_verdict); font-weight:$w_bold;
  line-height:1.05; letter-spacing:-0.03em; white-space:nowrap;
}
.verdict-strip-summary {
  font-size:$t_body; color:var(--ink); line-height:$lh_normal;
}
.verdict-strip-metrics {
  flex:1 1 260px; display:flex; flex-direction:column; gap:$s_sm;
  min-width:220px;
}
.verdict-strip-counts {
  display:flex; flex-wrap:wrap; gap:$s_md;
  font-size:$t_caption; color:var(--muted);
}
.verdict-strip-counts .count { white-space:nowrap; }
.verdict-strip-counts .dot {
  display:inline-block; width:8px; height:8px; border-radius:50%;
  margin-right:5px; vertical-align:middle;
}
.verdict-strip-meta {
  font-size:$t_caption; color:var(--muted); white-space:nowrap;
  margin-left:auto; align-self:flex-start;
}
.verdict-strip-actions {
  margin-left:auto; display:flex; gap:$s_md; align-items:center;
}
/* Outcome bar inside the strip */
.seg-bar {
  width:100%; height:10px; border-radius:$r_sm; overflow:hidden;
  display:flex; background:var(--line);
}
.seg-bar-segment { height:100%; }

/* Loading skeleton */
.skeleton { padding:$s_lg 0; }
.sk-strip {
  border:$b_hairline solid var(--line); border-radius:$r_lg;
  padding:$s_lg $s_xl; background:$c_surface; margin-bottom:$s_lg;
}
.sk-note {
  font-size:$t_caption; color:var(--muted); margin-bottom:$s_lg;
}
.sk-row { margin-bottom:$s_md; }
.sk-bar {
  height:12px; border-radius:$r_sm;
  background:linear-gradient(90deg, $c_surface_sunken 25%, var(--line) 37%,
    $c_surface_sunken 63%);
  background-size:400% 100%;
  animation:sk-shimmer 1.4s ease-in-out infinite;
}
.sk-headline { height:30px; width:44%; margin-bottom:$s_md; }
.sk-summary { height:14px; width:66%; }
@keyframes sk-shimmer {
  0% { background-position:100% 50%; }
  100% { background-position:0 50%; }
}
@media (prefers-reduced-motion: reduce) {
  .sk-bar { animation:none; }
}


/* Section identity. Each group of findings carries the colour it means. */
.findings-section { margin-bottom:$s_xxl; }
.findings-section h2, .findings-section h3 { display:flex; align-items:center; gap:$s_sm; }
.section-head {
  display:flex; align-items:baseline; gap:$s_sm;
  padding:$s_sm $s_md; border-radius:$r_md $r_md 0 0;
  border-left:4px solid var(--line); background:$c_surface_sunken;
  margin-bottom:0;
}
.section-head.fail { border-left-color:$c_deficiency_edge; background:$c_deficiency_bg; }
.section-head.pass { border-left-color:$c_clear_edge; background:$c_clear_bg; }
.section-head.unread { border-left-color:$c_unverified_edge; background:$c_unverified_bg; }
.section-head.na { border-left-color:$c_out_of_scope_edge; }

/* Cards read as cards against the tinted ground. */
.findings-table {
  background:$c_surface; border:$b_hairline solid var(--line);
  border-radius:$r_md; overflow:hidden;
}
.findings-table thead th {
  background:$c_surface_sunken; border-bottom:$b_hairline solid var(--line);
}
.verdict-strip { box-shadow:0 1px 2px rgba(17,24,39,0.04); }

/* The verdict strip takes a tint from the verdict itself, so the top of the
   screen is not the same neutral card whatever the answer is. */
.verdict-strip.v-fail { border-left:5px solid $c_deficiency_edge; }
.verdict-strip.v-pass { border-left:5px solid $c_clear_edge; }
.verdict-strip.v-unknown { border-left:5px solid $c_unverified_edge; }

/* Provenance line */
.provenance {
  font-size:$t_caption; color:var(--muted); padding:$s_sm 0 $s_md;
  border-bottom:1px solid var(--line); margin-bottom:$s_xs;
}
.provenance b { color:var(--ink); }

/* Findings section headers */
.findings-section {
  font-size:$t_subhead; font-weight:$w_bold; margin:$s_xxl 0 $s_md;
  letter-spacing:-0.01em; color:var(--ink);
}
.findings-section-count {
  font-weight:$w_regular; color:var(--muted);
}

/* Findings table continued */
.findings-table th {
  text-align:left; font-size:$t_micro; text-transform:uppercase;
  letter-spacing:0.07em; color:var(--muted); padding:$s_sm $s_md;
  border-bottom:$b_hairline solid var(--line); white-space:nowrap;
}
.findings-table th.right { text-align:right; }
.findings-table td.value { text-align:left; vertical-align:top; }
.findings-table td {
  padding:$s_sm $s_md; border-bottom:$b_hairline solid var(--line); vertical-align:top;
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
  font-variant-numeric:tabular-nums; display:block;
  overflow-wrap:anywhere; hyphens:none; text-align:left;
}
.ft-threshold {
  font-family:$f_sans; font-size:$t_caption; color:var(--muted);
  display:block; margin-top:1px; text-align:left;
  overflow-wrap:anywhere;
}
.ft-citation-chip {
  display:inline-block; background:$c_surface_sunken; color:$c_citation_fg;
  padding:2px $s_sm; border-radius:$r_sm; font-size:$t_micro;
  font-family:$f_mono; white-space:normal;
}
.ft-status-pill {
  display:inline-block; padding:2px $s_md; border-radius:$r_md;
  font-size:$t_micro; font-weight:$w_medium; white-space:nowrap;
}
/* De-emphasised not-applicable group */
.findings-table.deemphasised td {
  color:var(--muted);
}
.findings-table.deemphasised tr {
  border-left:$b_accent solid $c_out_of_scope_edge;
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
  padding:$s_xl; background:$c_surface; margin:$s_md 0 $s_xl;
}
.map-card-caption {
  font-size:$t_caption; color:var(--muted); margin-bottom:$s_md;
  text-transform:uppercase; letter-spacing:0.07em;
}
.map-card img {
  max-width:100%; max-height:420px; width:auto; display:block;
  margin:0 auto; border-radius:$r_sm;
}
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

/* Split-pane layout: left scrolls normally, right sticks */
[data-testid="stHorizontalBlock"]:has(.st-key-viewer_pane) {
  position:relative; align-items:flex-start;
}
/* Sticky has to sit on the column, not on the container inside it. Streamlit
   gives the column the height of its own content, which is exactly the height
   of the pane, so a sticky child had no room to travel and scrolled away with
   it. The row is the full height of the findings, so the column can travel
   inside that. The page also scrolls section[data-testid="stMain"] rather than
   the window, and the offset is relative to that scrollport. */
[data-testid="stHorizontalBlock"]:has(.st-key-viewer_pane)
  > [data-testid="stColumn"]:has(.st-key-viewer_pane) {
  position:sticky; top:${k_top_clearance}; align-self:flex-start;
}
.st-key-viewer_pane {
  height:calc(100vh - ${k_top_clearance} - ${s_lg});
  overflow-y:auto;
}
@media (max-width:1100px) {
  [data-testid="stHorizontalBlock"]:has(.st-key-viewer_pane)
    > [data-testid="stColumn"]:has(.st-key-viewer_pane) {
    position:static;
  }
  .st-key-viewer_pane {
    height:auto; overflow-y:visible;
  }
}

/* Responsive table overflow */
.findings-table { border-collapse:collapse; width:100%; font-size:$t_body; table-layout:fixed; }
@media (max-width:1400px) {
  .findings-table { font-size:$t_caption; }
  .findings-table .ft-value { font-size:$t_caption; }
}
@media (max-width:1100px) {
  .findings-table { table-layout:auto; }
  .findings-table th.right, .findings-table td.right { display:none; }
}

/* Verdict strip responsive */
@media (max-width:1100px) {
  .verdict-strip { flex-direction:column; align-items:flex-start; gap:$s_md; }
  .verdict-strip-actions { margin-left:0; }
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
def review_from_cache(pdf_path: str, doc_hash: str = "") -> dict | None:
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


def brand_band(subject: dict | None = None) -> str:
    """The band at the top: who this is, and which packet is open."""
    subject = subject or {}
    document = subject.get("document")
    pages = subject.get("pages")
    if document:
        detail = (
            f"<div class='brand-band-doc'>"
            f"<span class='brand-band-doc-name'>{html_lib.escape(document)}</span>"
            f"<span class='brand-band-doc-meta'>{pages} pages</span>"
            f"</div>"
        )
    else:
        detail = ""
    return (
        "<div class='brand-band'>"
        "<div class='brand-mark'><i></i><i></i><i></i></div>"
        "<div class='brand-band-text'>"
        "<div class='brand-band-title'>Septic permit application review</div>"
        "<div class='brand-band-sub'>Delaware on-site wastewater regulations, "
        "January 2014</div>"
        "</div>"
        f"{detail}"
        "</div>"
    )


def loading_skeleton(doc_name: str = "") -> str:
    """The shape of the result, shown while the review runs."""
    rows = "".join(
        f"<div class='sk-row'><div class='sk-bar' style='width:{w}%'></div></div>"
        for w in (86, 72, 90, 64)
    )
    return (
        "<div class='skeleton'>"
        "<div class='sk-strip'>"
        "<div class='sk-bar sk-headline'></div>"
        "<div class='sk-bar sk-summary'></div>"
        "</div>"
        f"<div class='sk-note'>Reading {html_lib.escape(doc_name)} and applying "
        "the 15 requirements</div>"
        f"<div class='sk-rows'>{rows}</div>"
        "</div>"
    )


def verdict_strip(payload: dict) -> str:
    """The verdict, and one sentence saying what it rests on.

    This carried a segmented bar and a row of counts as well, which stated the
    same fact three times: 3 requirements not met, beside 3 failed and 12
    passed, beside a bar in the same proportions. The sentence is the one that
    reads at a glance and the only one that survives being read aloud, so it is
    the one that stayed.

    The sentence still has to carry the honest part. A verdict of NO
    DEFICIENCIES FOUND on a packet where most checks could not run must say so
    in the same breath, because that is the misreading this tool exists to
    prevent.
    """
    coverage = payload.get("coverage") or {}
    headline = payload.get("headline", "")
    unreadable = coverage.get("unreadable", 0)
    failed = len(payload.get("deficiencies") or [])
    passed = len(payload.get("satisfied") or [])
    not_applicable = coverage.get("not_applicable", 0)

    fg, _bg = BANNER_COLOR.get(
        headline, (TOKENS["colour"]["ink"], TOKENS["colour"]["surface_sunken"])
    )

    checked = failed + passed + not_applicable
    noun = "requirement" if failed == 1 else "requirements"
    if unreadable and not checked:
        summary = "Nothing on this packet could be checked."
    elif unreadable:
        summary = (
            f"{failed} {noun} not met. {unreadable} could not be checked."
            if failed else
            f"Nothing flagged. {unreadable} could not be checked."
        )
    elif failed:
        summary = f"{failed} {noun} not met."
    else:
        summary = "Nothing flagged against any requirement."

    tint = {
        "DEFICIENCIES FOUND": "v-fail",
        "NO DEFICIENCIES FOUND": "v-pass",
    }.get(headline, "v-unknown")

    return (
        f"<div class='verdict-strip {tint}'>"
        "<div class='verdict-strip-main'>"
        f"<div class='verdict-strip-headline' style='color:{fg}'>{headline}</div>"
        f"<div class='verdict-strip-summary'>{html_lib.escape(summary)}</div>"
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
            # The reason only earns a line when it says something the other two
            # columns do not. Where a value was compared against a threshold,
            # the requirement sentence already gives the threshold and the value
            # cell already gives both, so the reason states the same two numbers
            # a third time in the same row.
            compared = f.get("observed") is not None and f.get("threshold") is not None
            reason = "" if compared else reason_sentence(f)
            reason_html = (
                f"<div class='ft-reason'>{html_lib.escape(reason)}</div>"
                if reason else ""
            )

        # Value column - never show raw comparison operators to a reviewer.
        # For satisfied and not-applicable groups, show the observed value
        # plainly, with the threshold stated as a readable phrase.
        observed = f.get("observed")
        threshold = f.get("threshold")
        units = f.get("units") or ""
        if group in ("not_applicable",) and observed is None:
            # Rule did not apply; no comparison was made
            value_html = ""
        elif observed is not None:
            obs_str = str(observed)
            # Strip trailing .0 from float-like strings for display
            if obs_str.endswith(".0"):
                obs_str = obs_str[:-2]
            value_html = (
                f"<span class='ft-value'>{html_lib.escape(obs_str)}"
                f" {html_lib.escape(units)}</span>"
            )
            if threshold is not None:
                # Express the threshold as a human sentence, no operator symbols
                req_str = f.get("requirement", "")
                if "<=" in req_str:
                    direction = "at most"
                elif ">=" in req_str:
                    direction = "at least"
                elif "<" in req_str:
                    direction = "less than"
                elif ">" in req_str:
                    direction = "more than"
                else:
                    direction = "required:"
                value_html += (
                    f"<br><span class='ft-threshold'>"
                    f"{html_lib.escape(direction)} "
                    f"{html_lib.escape(str(threshold))} "
                    f"{html_lib.escape(units)}</span>"
                )
        else:
            value_html = ""

        # Citation chip - must never truncate
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
            f"<td class='value'>{value_html}</td>"
            f"<td>{citation_html}</td>"
            f"<td>{_status_pill(f)}</td>"
            f"</tr>"
        )

    return (
        f"<table class='{cls}'>"
        "<colgroup>"
        "<col style='width:11%'>"
        "<col style='width:34%'>"
        "<col style='width:21%'>"
        "<col style='width:20%'>"
        "<col style='width:14%'>"
        "</colgroup>"
        "<tr>"
        "<th>RULE</th><th>REQUIREMENT</th><th>VALUE</th>"
        "<th>CITATION</th><th>STATUS</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def map_figure_card(payload: dict) -> str:
    """The location screening as a figure card with measurements as a definition list."""
    screening = payload.get("screening") or {}
    if not screening.get("flags") and not screening.get("figure_png"):
        return ""

    # If there is no point, do not render an empty card
    point = screening.get("point") or {}
    if not point:
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

    parts.append("</dl>")

    # Screening caveat as a single amber note. This is the only place the caveat
    # appears. The monospace UNAVAILABLE LAYERS block that used to duplicate it
    # is removed.
    flags = screening.get("flags") or []
    unavailable = screening.get("unavailable")
    caveat_parts = list(flags)
    if unavailable:
        layers = ", ".join(unavailable) if isinstance(unavailable, list) else str(unavailable)
        caveat_parts.append(
            f"Layers not available for screening: {layers}."
        )
    if caveat_parts:
        caveat = " ".join(caveat_parts)
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
            f"<div class='findings-section section-head fail'>Deficiencies found "
            f"<span class='findings-section-count'>({len(deficiencies)})</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(findings_table(deficiencies, "deficiencies"), unsafe_allow_html=True)

    if unresolved:
        st.markdown(
            f"<div class='findings-section section-head unread'>Could not be evaluated "
            f"<span class='findings-section-count'>({len(unresolved)})</span></div>",
            unsafe_allow_html=True,
        )
        groups = payload.get("unresolved_groups") or []
        if groups:
            st.markdown(_grouped_unresolved_html(groups), unsafe_allow_html=True)
        else:
            st.markdown(findings_table(unresolved, "unresolved"), unsafe_allow_html=True)

    if satisfied:
        with st.expander(f"Checks that passed ({len(satisfied)})", expanded=False):
            st.markdown(
                findings_table(satisfied, "satisfied"), unsafe_allow_html=True
            )

    if not_applicable:
        with st.expander(
            f"Does not apply to this system ({len(not_applicable)})",
            expanded=False,
        ):
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
        _viewer_html(page_uris, start_page), height=780, scrolling=True
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

# The band names the packet once a review has run, so it is filled in
# after the payload exists rather than rendered twice.
brand_slot = st.empty()
brand_slot.markdown(brand_band(), unsafe_allow_html=True)


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



# ---------------------------------------------------------------------------
# Reviewer chatbot. Appears only after a permit has been reviewed, below
# the embedded report. Uses Gemini via Vertex AI to help reviewers
# understand the deterministic results. Does not approve or deny anything.
# ---------------------------------------------------------------------------

def _chatbot_section(payload: dict) -> None:
    """Render the reviewer chatbot section below the report.

    Shows only when a review payload exists. Gracefully hides if the SDK or
    configuration is unavailable. The existing review continues working
    regardless of chatbot availability.
    """
    from septic.chatbot.config import is_available as chatbot_available

    if not chatbot_available():
        return

    st.divider()
    st.markdown("### Reviewer assistant")
    st.caption(
        "⚠️ This assistant helps you understand the review results. "
        "It does not make the final decision — the reviewer decides. "
        "AI-generated explanations are labelled and separated from "
        "deterministic rule results."
    )

    # Session state for conversation
    if "chatbot_messages" not in st.session_state:
        st.session_state["chatbot_messages"] = []
    if "chatbot_instance" not in st.session_state:
        st.session_state["chatbot_instance"] = None
    # Track which payload the chatbot was created for
    payload_id = payload.get("generated_at", "") + str(
        payload.get("subject", {}).get("document", "")
    )
    if st.session_state.get("chatbot_payload_id") != payload_id:
        st.session_state["chatbot_messages"] = []
        st.session_state["chatbot_instance"] = None
        st.session_state["chatbot_payload_id"] = payload_id

    # Suggested questions
    suggested = [
        "Summarize the review findings.",
        "What information is missing?",
        "Why could some rules not be evaluated?",
        "Which regulations apply to this permit?",
        "What should the reviewer verify next?",
    ]

    # Clear chat button
    col1, col2 = st.columns([6, 1])
    with col2:
        if st.button("Clear chat", key="clear_chat"):
            st.session_state["chatbot_messages"] = []
            st.session_state["chatbot_instance"] = None
            st.rerun()

    # Show suggested questions as clickable buttons
    st.markdown("**Suggested questions:**")
    cols = st.columns(len(suggested))
    clicked_suggestion = None
    for i, (col, question) in enumerate(zip(cols, suggested)):
        with col:
            if st.button(question, key=f"suggest_{i}", use_container_width=True):
                clicked_suggestion = question

    # Display conversation history
    for msg in st.session_state["chatbot_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    user_input = st.chat_input(
        "Ask about the review results, missing information, or cited regulations…"
    )

    # Use clicked suggestion if no direct input
    query = user_input or clicked_suggestion
    if query:
        # Display user message
        with st.chat_message("user"):
            st.markdown(query)
        st.session_state["chatbot_messages"].append(
            {"role": "user", "content": query}
        )

        # Get or create chatbot instance
        with st.spinner("Thinking…"):
            try:
                from septic.chatbot.client import (
                    ChatbotError,
                    ReviewerChatbot,
                    create_chatbot,
                )

                bot = st.session_state.get("chatbot_instance")
                if bot is None:
                    bot = create_chatbot(payload)
                    if bot is None:
                        raise ChatbotError(
                            "The AI assistant is not configured. "
                            "Check that GOOGLE_CLOUD_PROJECT and "
                            "GOOGLE_GENAI_USE_VERTEXAI environment variables "
                            "are set."
                        )
                    # Replay history into the bot
                    for msg in st.session_state["chatbot_messages"][:-1]:
                        if msg["role"] == "user":
                            # Find the corresponding model response
                            pass
                    st.session_state["chatbot_instance"] = bot

                response = bot.send_message(query)

                # Display assistant response
                with st.chat_message("assistant"):
                    st.markdown(response)
                st.session_state["chatbot_messages"].append(
                    {"role": "assistant", "content": response}
                )

            except Exception as exc:  # noqa: BLE001
                error_msg = str(exc) if str(exc) else (
                    "The AI assistant encountered an error. "
                    "The review results above are unaffected."
                )
                with st.chat_message("assistant"):
                    st.warning(error_msg)
                # Remove the user message that failed
                if st.session_state["chatbot_messages"]:
                    st.session_state["chatbot_messages"].pop()


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
        # A review takes about five seconds on a real packet and the default
        # spinner is a small mark in a large empty page, which reads as frozen
        # on a projector. Hold the shape of the result instead, so the screen
        # shows the layout filling in rather than nothing happening.
        skeleton = st.empty()
        skeleton.markdown(loading_skeleton(uploaded.name), unsafe_allow_html=True)
        payload = review_from_cache(str(target), doc_hash)
        skeleton.empty()
        elapsed = time.perf_counter() - started
        if payload:
            subject = payload.get("subject") or {}
            brand_slot.markdown(brand_band(subject), unsafe_allow_html=True)

            # Verdict strip with segmented bar
            st.markdown(verdict_strip(payload), unsafe_allow_html=True)

            # Split layout: findings left, PDF viewer right.
            findings_col, viewer_col = st.columns([58, 42], gap="medium")

            with findings_col:
                # Findings first. The map is a screening prompt, not a finding,
                # and at full size above the tables it pushed the first
                # deficiency 684 pixels below the fold. It sits under the
                # findings now, and full height in the Location tab beside them.
                render_findings(payload)

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

                # Draft correction letter, only when deficiencies were found
                if payload.get("headline") == "DEFICIENCIES FOUND":
                    from septic.report.letter import render_letter
                    letter = render_letter(payload)
                    if letter:
                        with st.expander("Draft correction letter (edit before sending)"):
                            st.caption(
                                "This is a draft for you to edit and sign. It is "
                                "not a determination. Paste it into your own "
                                "template and review every line before sending."
                            )
                            st.code(letter, language=None)
                            st.download_button(
                                label="Download draft letter",
                                data=letter,
                                file_name=f"draft_correction_{doc_name}.txt",
                                mime="text/plain",
                                key="draft_letter",
                            )

            with viewer_col:
                with st.container(key="viewer_pane"):
                    packet_tab, location_tab = st.tabs(["Packet", "Location"])
                    with packet_tab:
                        render_pdf_viewer(data, doc_hash, payload)
                    with location_tab:
                        map_html_right = map_figure_card(payload)
                        if map_html_right:
                            st.markdown(map_html_right, unsafe_allow_html=True)
                        else:
                            st.caption("No coordinates available for this packet.")
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
        "Requirements taken from the 2014 regulation, each one "
        f"carrying the section and page it comes from."
    )
with rule_cols[1]:
    show_rules = st.toggle("Show all rules", value=False)

if show_rules:
    st.markdown("### The requirements this checks")
    st.caption(
        "Every one is quoted from the 2014 regulation. The section and page are "
        "shown so any of them can be read back at the source."
    )
    st.markdown(rules_reference(rules), unsafe_allow_html=True)


st.markdown(attribution_band(), unsafe_allow_html=True)
