"""DNREC septic permit reviewer console.

    streamlit run app.py

This is the interface a permitting reviewer would be handed. It is deliberately
not a chatbot. There is no message thread, no assistant persona, and nothing that
implies a model produced the answer, because the product claim is that rules
decide and a model does not, and a conversational interface would contradict that
claim before anyone read a word.

The report body is render_html from src/septic/report/render.py, embedded as is.
It is not reimplemented here. The text report, the HTML file written by
python -m septic review, and this screen therefore cannot disagree with each
other, which matters more than having per-surface control of the layout: two
renderers drift, and the one nobody is looking at drifts first.

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
)
from septic.rules import engine  # noqa: E402

st.set_page_config(
    page_title="DNREC septic permit application review",
    # A local file, read off disk by Streamlit. Nothing is fetched.
    page_icon=str(asset_path("favicon.png")),
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Styling. Local only, and every value comes from the shared token set.
# ---------------------------------------------------------------------------

STYLE_TEMPLATE = """
:root { --ink:$c_ink; --muted:$c_muted; --line:$c_line; }

/* Clear the Streamlit toolbar. It is fixed to the top of the main column and
   overlays the content rather than pushing it down, so the 12 pixels this used to
   carry put the product title underneath it and cut the tool name off above the
   viewport edge. The clearance is a token, measured against the toolbar's real
   height, so content never sits under the top edge. */
.block-container {
  padding-top:$k_top_clearance; padding-bottom:$s_sm; max-width:1600px;
}

/* Streamlit's own elements are matched on the test id alone, never on the tag
   name it happens to use. The sidebar is a section in this version and these
   three rules were written as div[data-testid="stSidebar"], so all of them were
   silently dead: the sidebar kept its own width, its inner padding was never
   applied, and the print stylesheet below did not hide it. A selector that stops
   working when somebody else changes an element name is not a selector worth
   keeping. */
[data-testid="stSidebar"] { min-width:${k_sidebar_width}px; }
[data-testid="stSidebar"] .block-container { padding-top:$s_lg; }

/* Our own markup only. This deliberately does not reach into Streamlit's
   classes: the selector here used to be [class*="st-"], which matches every
   emotion generated class, including the span Streamlit draws its icons in. Those
   icons are ligatures in a bundled icon font, so overriding their family made
   each one render as the text of its own ligature name, and the upload control
   read uploadUpload. Streamlit's own body font is set from the same token in
   .streamlit/config.toml instead, which is the supported way in and cannot reach
   the icon font. */
html, body { font-family:$f_sans; }

/* The product identity band. The name of the tool and one line saying what it
   does, so somebody seeing the screen for the first time is not guessing. No
   sponsor mark goes here: this is not state software. */
.appbar {
  border-bottom:$b_rule solid var(--ink); padding:0 0 $s_md; margin:0 0 $s_lg;
}
.appbar-title {
  font-size:$t_title; font-weight:$w_bold; letter-spacing:-0.015em;
  line-height:$lh_tight; color:var(--ink);
}
.appbar-sub {
  font-size:$t_body; color:var(--muted); margin-top:$s_sm; max-width:100ch;
}
.provenance {
  font-size:$t_caption; color:var(--muted); padding:$s_sm 0 $s_md;
  border-bottom:1px solid var(--line); margin-bottom:$s_xs;
}
.provenance b { color:var(--ink); }
.provenance code { font-family:$f_mono; font-size:$t_caption; }

/* The verdict banner. The single most important element on a projected screen,
   and both numbers in it are read straight out of the composed payload. */
.banner {
  border:2px solid currentColor; border-radius:$r_lg; padding:$s_lg $s_xl;
  margin:$s_md 0 $s_lg;
}
.banner-verdict {
  font-size:$t_verdict; font-weight:$w_bold; letter-spacing:-0.02em;
  line-height:$lh_tight;
}
.banner-coverage {
  font-size:$t_section; font-weight:$w_medium; margin-top:$s_xs;
  letter-spacing:-0.01em;
}
.banner-tail { font-size:$t_body; margin-top:$s_sm; color:var(--ink); }

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

/* The drop target itself. The uploader used to be a small control in the sidebar
   while a large dashed box in the middle of the screen told the reviewer to go
   and use it, so the obvious target was the one thing that did not accept a
   packet. The dashed box is now the uploader's own dropzone, which means the
   thing that looks droppable is the thing a drop lands on.

   Scoped to the container key so the compact uploader offered once a packet is
   loaded keeps Streamlit's own small form. */
.st-key-dropzone [data-testid="stFileUploaderDropzone"] {
  padding:$s_xxxl $s_xl; border:$b_accent dashed var(--line);
  border-radius:$r_lg; flex-direction:column; justify-content:center;
  align-items:center; gap:$s_md; text-align:center;
}
/* Inside the drop target the instruction is that box's caption, not a second
   box of its own, so it keeps the words and gives up the border. */
.st-key-dropzone .empty {
  border:0; padding:0; margin:0 0 $s_md;
}

/* The rule reference. A reviewer asks what failed, and then asks what gets
   checked at all and on whose authority. This is the second answer, so it reads
   as a reference table rather than a wall of prose. */
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

/* Attribution. Dark on purpose: it separates the sponsors from the product
   identity, and the First State AI Institute wordmark is white, so on a light
   band it would disappear. Altering a sponsor's mark to suit our layout is not
   an option. */
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

/* Keyboard use. A focus ring that is actually visible on a projector. */
a:focus-visible, button:focus-visible, input:focus-visible,
select:focus-visible, textarea:focus-visible,
[role="button"]:focus-visible, [data-testid="stFileUploader"] :focus-visible {
  outline:$b_rule solid $c_remedy_fg; outline-offset:2px; border-radius:$r_sm;
}

/* Printing the screen. The chrome goes, the finding stays. The embedded report
   carries its own print stylesheet. */
@media print {
  [data-testid="stSidebar"], [data-testid="stFileUploader"],
  [data-testid="stToolbar"], [data-testid="stHeader"] { display:none; }
  .block-container { padding:0; max-width:none; }
  .banner, .band {
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
    # Measurements of the Streamlit shell, in pixels where they are used as a
    # length and bare where the template appends the unit itself.
    values["k_top_clearance"] = f"{TOKENS['chrome']['top_clearance']}px"
    values["k_sidebar_width"] = TOKENS["chrome"]["sidebar_width"]
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
    """Load the GIS layers once at start up rather than on the first review.

    Roughly 100,000 geometries across five layers. With the on disk WKB cache
    this is a fraction of a second, but it still belongs at boot: the first
    upload of a session should not be the one that pays for it, because that is
    the one somebody is watching.
    """
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

    Delegates to septic.review rather than repeating the chain here. An earlier
    version rebuilt it inline and silently omitted the location screening, so the
    map never appeared on this screen even though the command line report carried
    it. That is exactly the drift the module docstring warns about: two paths
    through the same pipeline, and the one nobody is watching loses a stage.

    Returns the composed payload, or None when there is no cached analysis, so the
    caller can explain instead of crashing. The report body is rendered from this
    payload by the shared renderer in embedded mode, which is why the HTML the
    review already produced is not what goes on screen: that one is the standalone
    page, complete with its own verdict header for printing.
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
# Pieces of the page
# ---------------------------------------------------------------------------

# The colours a verdict is read by, imported from the report renderer so this
# screen and the report embedded in it cannot disagree about what a verdict looks
# like. Their meanings are load bearing: one for a deficiency found, one for
# nothing found, one for no answer.
BANNER_COLOR = VERDICT_COLOR

# The attribution strip, in reading order. The alt text comes from the assets
# module, so the organisation each mark belongs to is named in one place.
SPONSOR_LOGOS = (
    ("dnrec-logo.png", "circular"),
    ("delaware-seal.png", "circular"),
    ("udel-logo.png", "circular"),
    ("fsaii-logo.png", "wordmark"),
)


def banner(payload: dict) -> str:
    """The verdict and the coverage figure, above the embedded report.

    This is the single on screen statement of the verdict. The report body below it
    is rendered in embedded mode, which leaves out its own headline, coverage line
    and explanation, because this box carries them and the screen was otherwise
    showing all three twice within about a hundred pixels. Coverage is shown at the
    same weight as the verdict on purpose: NO DEFICIENCIES FOUND over seven of
    fifteen checks is not the same statement as NO DEFICIENCIES FOUND over all
    fifteen, and showing the headline without the number would be worse than
    showing neither.

    The tail is one sentence. It used to be the full paragraph the report prints
    above its itemised list, which is the right length there and the wrong length
    here.

    Every number here is read out of the composed payload. Nothing on this screen
    counts anything: the coverage line is coverage["text"] verbatim, which is the
    same string the report body renders when it is opened on its own, and the
    sentence under it carries no figures at all so it cannot drift from them.
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


def attribution_band() -> str:
    """The sponsor strip, labelled, and set apart from the product identity.

    Three circular marks and one horizontal wordmark. They share one height band
    with width left free, so the strip has a single baseline, and the circular
    marks are given a little more height than the wordmark because a circle
    carries visibly less ink than a rectangle of the same height.

    The non endorsement sentence is not decoration. This tool is not DNREC
    software, and a strip of state marks with nothing said would imply it is.
    """
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
    """Every requirement this checks, as a reference table.

    Section, page, threshold and the verbatim regulation text, so any of the
    fifteen can be read back at the source. A reviewer is entitled to ask on whose
    authority a threshold is applied, and the answer is a page number.
    """
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


def estimate_height(payload: dict) -> int:
    """Pick an iframe height that fits the report without an inner scrollbar.

    A nested scrollbar is the one thing that makes an embedded report feel like a
    widget rather than a document, and on a projector it is close to unusable.
    Sized from the actual content rather than a fixed guess.
    """
    height = 900
    height += 320 * len(payload.get("deficiencies") or [])
    height += 34 * len(payload.get("unresolved") or [])
    height += 30 * len(payload.get("missing_information") or [])
    height += 30 * len(payload.get("discarded_readings") or [])
    height += 300 * len(payload.get("satisfied") or [])
    height += 40 * len(payload.get("not_applicable") or [])
    height += 30 * len(payload.get("facts_read") or [])
    height += 120 * len(payload.get("notices") or [])
    return min(height, 26000)


# ---------------------------------------------------------------------------
# Left panel. The rule reference only. The uploader lives in the main column,
# because that is where the large dashed box a reviewer aims a packet at is.
# ---------------------------------------------------------------------------

warm_layers()
load_graph_once()

# The uploader's session state key. Read before the widget is built, so the page
# knows whether a packet is loaded and can put the control where it belongs:
# large and central while there is nothing to show, folded away once there is.
PACKET = "application_packet"

with st.sidebar:
    rules = engine.load_rules()
    st.markdown("### Rules applied")
    st.markdown(
        f"**{len(rules)}** requirements taken from the 2014 regulation, each one "
        f"carrying the section and page it comes from."
    )
    show_rules = st.toggle("Show all rules", value=False)


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

st.markdown(
    "<div class='appbar'>"
    "<div class='appbar-title'>Septic permit application review</div>"
    "<div class='appbar-sub'>A first pass over an application packet for the "
    "reviewer assessing it. It flags deficiencies and puts the regulation "
    "citation next to each one. It does not approve or deny anything. The "
    "reviewer decides.</div>"
    "</div>",
    unsafe_allow_html=True,
)

def drop_zone():
    """The empty state and the uploader as one control.

    The instruction and the thing it refers to used to be different elements in
    different columns: a large dashed box in the middle of the screen reading
    "drop a packet into the panel on the left", and the actual uploader as a small
    control in that panel. A reviewer following the biggest target on the screen
    got nothing, so the box is now the uploader's own dropzone. The stylesheet
    gives it the dashed border and the size, scoped to this container's key.

    The three outcomes stay, because they are what somebody facing an empty screen
    needs to know before they upload anything. The three verdict names take their
    colours from the same table the banner reads, so the legend here and the real
    verdict a reviewer sees later cannot disagree about what a colour means.
    """
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
    # A packet is loaded, so the findings need the column. The same uploader,
    # folded shut: a reviewer reading a report should not have to scroll past a
    # large empty dropzone to reach it, and still has to be able to load the next
    # packet without hunting for the control.
    with st.expander("Review a different packet", expanded=False):
        uploaded = st.file_uploader(
            "Application PDF", type=["pdf"], key=PACKET,
            label_visibility="collapsed",
        )

if uploaded is not None:
    # An uploaded packet may already be cached. Analysing a new one needs
    # Textract, which needs credentials. Say so calmly and never hang.
    data = uploaded.getvalue()
    doc_hash = document_hash(data)
    client = TextractClient()
    cached = client.cached_by_hash(doc_hash)

    if cached is not None and cached.ok:
        # No banner announcing a cache hit. It named the service and the cache, and
        # it read as a caveat: the cache is keyed by the SHA256 of these bytes, so a
        # hit means this exact packet was analysed before, not that anything was
        # staged. The document is named in the provenance line below either way.
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
            st.markdown(
                f"<div class='provenance'><b>{subject.get('document', '')}</b>"
                f" &nbsp;|&nbsp; {subject.get('pages', 0)} pages"
                f" &nbsp;|&nbsp; reviewed in {elapsed * 1000:.0f} ms</div>",
                unsafe_allow_html=True,
            )
            st.markdown(banner(payload), unsafe_allow_html=True)
            # Embedded, so the report leaves out the headline, the coverage line
            # and the explanation the banner directly above already carries.
            components.html(
                render_html(payload, embedded=True),
                height=estimate_height(payload),
                scrolling=True,
            )
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

elif show_rules:
    # The report itemises what a packet failed. This is the other question a
    # reviewer asks, which is what gets checked at all and on whose authority.
    # It sits under the drop target rather than replacing it, so the way in stays
    # on the screen while the reference is open.
    st.markdown(f"### The {len(rules)} requirements this checks")
    st.caption(
        "Every one is quoted from the 2014 regulation. The section and page are "
        "shown so any of them can be read back at the source."
    )
    st.markdown(rules_reference(rules), unsafe_allow_html=True)

st.markdown(attribution_band(), unsafe_allow_html=True)
