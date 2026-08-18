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
from septic.ingest import layout  # noqa: E402
from septic.ingest.extract import extract_facts  # noqa: E402
from septic.ingest.textract import TextractClient, document_hash  # noqa: E402
from septic.report import compose as compose_mod  # noqa: E402
from septic.report.render import render_html  # noqa: E402
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
      .rule-flag { color: #b45309; font-size: 12px; text-transform: uppercase;
                   letter-spacing: 0.04em; white-space: nowrap; }
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

    Returns the rendered HTML and the composed payload, or None when there is no
    cached analysis, so the caller can explain instead of crashing.
    """
    client = TextractClient()
    path = Path(pdf_path)
    analysis = client.cached_by_hash(document_hash(path.read_bytes()))
    if analysis is None or not analysis.ok:
        return None

    document = layout.parse_blocks(analysis.blocks)
    extraction = extract_facts(document)
    # The rules are the only thing that produces a verdict.
    report = engine.evaluate(extraction.facts)
    composed = compose_mod.compose(
        report,
        extraction=extraction,
        graph=load_graph_once(),
        precedents=None,
        subject={
            "document": path.name,
            "pages": document.pages,
            "source": "cached Textract analysis, no network used",
        },
    )
    return render_html(composed), composed.to_json()


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
    height += 30 * len(payload.get("facts_read") or [])
    height += 120 * len(payload.get("notices") or [])
    return min(height, 26000)


# ---------------------------------------------------------------------------
# Left panel
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Application packet")
    st.caption(
        "Drop a scanned application PDF here. Packets analysed before are served "
        "from the local cache, with no network and no credentials."
    )
    uploaded = st.file_uploader(
        "Application PDF", type=["pdf"], label_visibility="collapsed"
    )
    st.caption(f"Sample packets: `{TESTDATA.name}/`")

    st.divider()
    rules = engine.load_rules()
    verified = sum(1 for r in rules if r.verified)
    st.markdown("### Rule set")
    st.markdown(
        f"- **{len(rules)}** rules drawn from the regulation  \n"
        f"- **{verified}** certified by a person  \n"
        f"- source: the 2014 regulation, 245 pages"
    )
    if verified == 0:
        st.markdown(
            "<div class='rule-state'>No rule has been certified yet, so the "
            "engine will not evaluate any of them and the verdict is CANNOT "
            "VERIFY. That is the interlock, not a failure.</div>",
            unsafe_allow_html=True,
        )

    # Previously a caption pointing at a file path, which read as an empty
    # heading on screen. The rules themselves are the useful thing to show: a
    # reviewer can see what is being checked and where each one comes from.
    with st.expander(f"What the {len(rules)} rules check", expanded=False):
        for r in rules:
            cite = r.citation.section or ""
            if r.citation.page:
                cite = f"{cite}, p.{r.citation.page}"
            mark = "certified" if r.verified else "awaiting certification"
            st.markdown(
                f"<div class='rule-row'><b>{r.parameter}</b>"
                f"<span class='rule-cite'>{cite}</span>"
                f"<span class='rule-flag'>{mark}</span></div>",
                unsafe_allow_html=True,
            )


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

else:
    ready = [p for p in sorted(TESTDATA.glob("*.pdf"))] if TESTDATA.exists() else []
    st.markdown(
        "<div class='empty'>Drop an application packet into the panel on the "
        "left to review it.</div>",
        unsafe_allow_html=True,
    )
    if ready:
        st.markdown(
            f"Sample packets are in <code>{TESTDATA.name}/</code>, already "
            f"analysed and cached so they review with no network:",
            unsafe_allow_html=True,
        )
        for p in ready:
            st.markdown(
                f"<div class='rule-row'><b>{p.name}</b>"
                f"<span class='rule-cite'>{p.stat().st_size / 1e6:.1f} MB</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
