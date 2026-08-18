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

Runs with no network and no AWS credentials, serving Textract output from the
on-disk cache keyed by document SHA256. Nothing is fetched from a CDN.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
TESTDATA = ROOT / "testdata"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from septic import config  # noqa: E402
from septic import review as review_mod  # noqa: E402
from septic.ingest.textract import TextractClient, document_hash  # noqa: E402
from septic.rules import engine  # noqa: E402

st.set_page_config(
    page_title="DNREC septic permit review",
    page_icon="\U0001F4CB",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Local styling only. No Google Fonts, no CDN, nothing to fetch. Venue wifi will
# fail and this has to look identical when it does.
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.4rem; max-width: 1600px; }
      div[data-testid="stSidebar"] { min-width: 340px; }
      div[data-testid="stSidebar"] .block-container { padding-top: 1rem; }
      h2 { font-size: 30px !important; letter-spacing: -0.01em; }
      .provenance {
        font-size: 15px; color: #4b5563; padding: 8px 0 14px;
        border-bottom: 1px solid #e5e7eb; margin-bottom: 4px;
      }
      .provenance b { color: #111827; }
      .banner {
        border: 2px solid currentColor; border-radius: 10px;
        padding: 18px 24px; margin: 10px 0 14px;
      }
      .banner-verdict {
        font-size: 40px; font-weight: 700; letter-spacing: -0.02em;
        line-height: 1.05;
      }
      .banner-coverage {
        font-size: 26px; font-weight: 650; margin-top: 6px;
        letter-spacing: -0.01em;
      }
      .banner-tail { font-size: 16px; margin-top: 8px; color: #111827; }
      .rule-state {
        border-left: 5px solid #b45309; background: #fffbeb;
        padding: 12px 16px; font-size: 15.5px; margin-top: 8px;
      }
      .rule-row {
        display: flex; justify-content: space-between; align-items: baseline;
        gap: 10px; padding: 7px 0; border-bottom: 1px solid #e5e7eb;
        font-size: 14px;
      }
      .rule-row b { font-weight: 600; }
      .rule-cite { color: #6b7280; font-variant-numeric: tabular-nums;
                   white-space: nowrap; }
      .rule-need { color: #1b4332; font-weight: 600; white-space: nowrap;
                   font-variant-numeric: tabular-nums; }
      .rule-quote {
        color: #374151; font-size: 13.5px; line-height: 1.5;
        padding: 6px 0 12px 14px; border-left: 3px solid #e5e7eb;
        margin: 0 0 4px 2px;
      }
      .empty {
        border: 2px dashed #d1d5db; border-radius: 10px; padding: 34px;
        text-align: center; color: #4b5563; font-size: 17px; margin: 18px 0 22px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


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
def review_from_cache(pdf_path: str) -> tuple[str, dict] | None:
    """Run the whole chain from the on-disk cache. No network, no credentials.

    Delegates to septic.review rather than repeating the chain here. An earlier
    version rebuilt it inline and silently omitted the location screening, so the
    map never appeared on this screen even though the command line report carried
    it. That is exactly the drift the module docstring warns about: two paths
    through the same pipeline, and the one nobody is watching loses a stage.

    Returns the rendered HTML and the composed payload, or None when there is no
    cached analysis, so the caller can explain instead of crashing.
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
    return result.html, result.composed.to_json()


BANNER_COLOR = {
    "NO DEFICIENCIES FOUND": ("#1b4332", "#d8f3dc"),
    "DEFICIENCIES FOUND": ("#7f1d1d", "#fee2e2"),
    "CANNOT VERIFY": ("#78350f", "#fef3c7"),
}


def banner(payload: dict) -> str:
    """The verdict and the coverage figure, above the embedded report.

    The report body carries both already. This repeats them outside the iframe
    because the iframe starts scrolled to the top only until somebody scrolls it,
    and because the first question a reviewer asks across a room is answered by
    two lines of text. Coverage is shown at the same weight as the verdict on
    purpose: NO DEFICIENCIES FOUND over seven of fifteen checks is not the same
    statement as NO DEFICIENCIES FOUND over all fifteen, and showing the headline
    without the number would be worse than showing neither.

    Every number here is read out of the composed payload. Nothing on this screen
    counts anything: the coverage line is coverage["text"] verbatim, which is the
    same string the report body a few pixels below it renders, and the sentence
    under it carries no figures at all so it cannot drift from them.
    """
    headline = payload.get("headline", "")
    coverage = payload.get("coverage") or {}
    text = coverage.get("text", "")
    fg, bg = BANNER_COLOR.get(headline, ("#111827", "#f3f4f6"))
    if coverage.get("unreadable"):
        tail = (
            "The checks that could not be read are itemised in the report below. "
            "A check that did not run is not a check that passed."
        )
    elif coverage.get("not_applicable"):
        tail = (
            "The checks that do not govern this kind of system are listed "
            "separately below, and are not requirements this packet met."
        )
    else:
        tail = "Every check in the rule set ran against this packet."
    return (
        f"<div class='banner' style='color:{fg};background:{bg}'>"
        f"<div class='banner-verdict'>{headline}</div>"
        f"<div class='banner-coverage'>{text}</div>"
        f"<div class='banner-tail'>{tail}</div>"
        f"</div>"
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
# Left panel
# ---------------------------------------------------------------------------

warm_layers()
load_graph_once()

with st.sidebar:
    st.markdown("### Application packet")
    st.caption(
        "Drop a scanned application PDF here. Packets analysed before are served "
        "from the local cache, with no network and no credentials."
    )
    uploaded = st.file_uploader(
        "Application PDF", type=["pdf"], label_visibility="collapsed"
    )

    st.divider()
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

st.markdown("## Septic permit application review")
st.caption(
    "A first pass over an application packet for the reviewer assessing it. It "
    "flags deficiencies and puts the regulation citation next to each one. It "
    "does not approve or deny anything. The reviewer decides."
)

if uploaded is not None:
    # An uploaded packet may already be cached. Analysing a new one needs
    # Textract, which needs credentials. Say so calmly and never hang.
    data = uploaded.getvalue()
    doc_hash = document_hash(data)
    client = TextractClient()
    cached = client.cached_by_hash(doc_hash)

    if cached is not None and cached.ok:
        st.success(
            f"{uploaded.name} is already in the local Textract cache. "
            "Reviewing it with no network."
        )
        uploads = config.OUT_DIR / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / uploaded.name
        target.write_bytes(data)
        started = time.perf_counter()
        with st.spinner("Reading the packet and applying the rules"):
            result = review_from_cache(str(target))
        elapsed = time.perf_counter() - started
        if result:
            html, payload = result
            subject = payload.get("subject") or {}
            st.markdown(
                f"<div class='provenance'><b>{subject.get('document', '')}</b>"
                f" &nbsp;|&nbsp; {subject.get('pages', 0)} pages"
                f" &nbsp;|&nbsp; {subject.get('source', '')}"
                f" &nbsp;|&nbsp; reviewed in {elapsed * 1000:.0f} ms</div>",
                unsafe_allow_html=True,
            )
            st.markdown(banner(payload), unsafe_allow_html=True)
            components.html(html, height=estimate_height(payload), scrolling=True)
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
    st.markdown(f"### The {len(rules)} requirements this checks")
    st.caption(
        "Every one is quoted from the 2014 regulation. The section and page are "
        "shown so any of them can be read back at the source."
    )
    for r in rules:
        cite = r.citation.section or ""
        if r.citation.page:
            cite = f"{cite}, page {r.citation.page}"
        threshold = ""
        if r.threshold is not None:
            threshold = f"{r.operator.value} {r.threshold}"
            if r.units:
                threshold += f" {r.units}"
        else:
            threshold = r.operator.value
        st.markdown(
            f"<div class='rule-row'><b>{r.parameter}</b>"
            f"<span class='rule-need'>{threshold}</span>"
            f"<span class='rule-cite'>{cite}</span></div>",
            unsafe_allow_html=True,
        )
        if r.citation.quote:
            st.markdown(
                f"<div class='rule-quote'>{r.citation.quote}</div>",
                unsafe_allow_html=True,
            )

else:
    st.markdown(
        "<div class='empty'>Drop an application packet into the panel on the "
        "left to review it.</div>",
        unsafe_allow_html=True,
    )
