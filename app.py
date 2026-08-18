"""Reviewer console for DNREC septic permit pre-checks.

Two panels. The left one shows what the rule engine decided and why, itemised
with the regulation citation next to each finding. The right one is a chat
against Claude on Bedrock that can explain any of those judgements and answer
questions about the regulation itself.

The engine decides. The model explains. A reply from the chat panel can never
change a verdict, because the verdict is computed before the model is called
and is passed to it as read-only context.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# The package lives under src/, which is on the path once `pip install -e .`
# has run. Add it anyway so the app also works from a bare checkout.
SRC = Path(__file__).resolve().parent / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from septic import config  # noqa: E402
from septic.chat import context as chat_context  # noqa: E402
from septic.chat.bedrock_client import BedrockChatError, chat_stream  # noqa: E402
from septic.rules import engine  # noqa: E402
from septic.rules.schema import Outcome, Verdict  # noqa: E402

# Tracked, not under out/, so the button still works in a fresh clone.
EXAMPLE_FACTS = config.DOCS_DIR / "examples" / "sample_facts.json"

VERDICT_HELP = {
    Verdict.READY_TO_SUBMIT: (
        "Nothing was flagged among the checks that ran. This is not an approval."
    ),
    Verdict.LIKELY_RETURN: (
        "One or more requirements are not met. Each is itemised with the section "
        "it comes from."
    ),
    Verdict.CANNOT_VERIFY: (
        "No answer. Either a value could not be read off the packet, or the rule "
        "needed has not been confirmed by a person."
    ),
}

OUTCOME_LABEL = {
    Outcome.PASS: "PASS",
    Outcome.FAIL: "FAIL",
    Outcome.UNKNOWN: "CANNOT VERIFY",
}

SUGGESTED_QUESTIONS = [
    "Why is every rule returning UNKNOWN?",
    "What does Section 5.3.12 require?",
    "What are the isolation distances from shellfish waters?",
    "Which findings would get this application returned?",
]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def load_rules():
    """Rule set, cached for the life of the server process."""
    return engine.load_rules()


@st.cache_resource(show_spinner=False)
def load_graph():
    """Regulation graph, cached because it is a 2176 node parse."""
    return chat_context.get_graph()


@st.cache_resource(show_spinner=False)
def load_candidates():
    """Verbatim numeric passages, cached. Empty index when not extracted yet."""
    return chat_context.load_candidates()


def evaluate(facts: dict) -> "engine.Report":
    return engine.evaluate(facts, load_rules())


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def init_state() -> None:
    st.session_state.setdefault("facts", {})
    st.session_state.setdefault("facts_source", None)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("pending", None)


def set_facts(facts: dict, source: str | None) -> None:
    # Underscore keys are file metadata, not extracted values. Dropping them
    # keeps them out of the findings table and out of the model's context.
    st.session_state["facts"] = {
        k: v for k, v in facts.items() if not k.startswith("_")
    }
    st.session_state["facts_source"] = source


# ---------------------------------------------------------------------------
# Sidebar: loading a packet
# ---------------------------------------------------------------------------


def render_sidebar() -> None:
    st.sidebar.header("Application under review")

    upload = st.sidebar.file_uploader(
        "Extracted facts (JSON)",
        type=["json"],
        help=(
            "A flat object of field name to value, as the extractor produces. "
            "Keys are rule parameters such as site_plan or perc_rate."
        ),
    )
    if upload is not None:
        try:
            payload = json.loads(upload.getvalue().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            st.sidebar.error(f"Could not read that file as JSON: {exc}")
        else:
            if isinstance(payload, dict):
                set_facts(payload, upload.name)
            else:
                st.sidebar.error("Expected a JSON object at the top level.")

    if EXAMPLE_FACTS.exists():
        if st.sidebar.button("Load example packet", width="stretch"):
            set_facts(
                json.loads(EXAMPLE_FACTS.read_text(encoding="utf-8")),
                EXAMPLE_FACTS.name,
            )
    else:
        st.sidebar.caption(
            f"No example at {EXAMPLE_FACTS.relative_to(config.ROOT)}."
        )

    if st.session_state["facts"]:
        if st.sidebar.button("Clear packet", width="stretch"):
            set_facts({}, None)

    st.sidebar.divider()
    render_sidebar_status()


def render_sidebar_status() -> None:
    st.sidebar.subheader("Context available to the assistant")

    rules = load_rules()
    verified = sum(1 for r in rules if r.verified)
    st.sidebar.write(f"Rules: {len(rules)} loaded, {verified} verified by a person")

    graph = load_graph()
    if graph.available:
        st.sidebar.write(f"Regulation graph: {len(graph.nodes)} nodes")
    else:
        st.sidebar.warning(
            "Regulation graph not built, so the assistant cannot quote the "
            "regulation. Build it with:\n\n`python -m septic graph build`"
        )

    passages = load_candidates()
    if passages.available:
        st.sidebar.write(f"Regulation passages: {len(passages.passages)}")
    else:
        st.sidebar.caption(
            "Verbatim numeric passages not extracted. Optional, and it reads "
            "the 245 page PDF, so it takes about a minute."
        )
        if st.sidebar.button("Extract passages", width="stretch"):
            with st.spinner("Reading the regulation PDF..."):
                chat_context.build_candidates_cache()
            chat_context.reset_caches()
            load_candidates.clear()
            load_graph.clear()
            st.rerun()

    source = st.session_state["facts_source"]
    st.sidebar.write(f"Packet: {source}" if source else "Packet: none loaded")
    st.sidebar.caption(f"Model: {config.BEDROCK_TEXT_MODEL}")


# ---------------------------------------------------------------------------
# Left panel: the dashboard
# ---------------------------------------------------------------------------


def render_verdict(report: "engine.Report") -> None:
    counts = report.counts()
    explain = VERDICT_HELP[report.verdict]

    if report.verdict is Verdict.READY_TO_SUBMIT:
        st.success(f"**{report.verdict.value}**  \n{explain}")
    elif report.verdict is Verdict.LIKELY_RETURN:
        st.error(f"**{report.verdict.value}**  \n{explain}")
    else:
        st.warning(f"**{report.verdict.value}**  \n{explain}")

    passed, failed, unknown, returns = st.columns(4)
    passed.metric("Pass", counts["pass"])
    failed.metric("Fail", counts["fail"])
    unknown.metric("Cannot verify", counts["unknown"])
    returns.metric("Return reasons", counts["return_reasons"])


def render_findings(report: "engine.Report") -> None:
    st.subheader("Findings")

    order = {Outcome.FAIL: 0, Outcome.UNKNOWN: 1, Outcome.PASS: 2}
    evaluations = sorted(report.evaluations, key=lambda e: order[e.outcome])

    st.dataframe(
        [
            {
                "Rule": ev.rule.id,
                "Outcome": OUTCOME_LABEL[ev.outcome],
                "Severity": ev.rule.severity.value,
                "Citation": ev.rule.citation.short(),
                "Reason": ev.reason,
            }
            for ev in evaluations
        ],
        hide_index=True,
        width="stretch",
    )

    st.caption("Open a finding for the citation, the observed value, and the fix.")
    for ev in evaluations:
        with st.expander(f"{OUTCOME_LABEL[ev.outcome]} — {ev.rule.id}"):
            render_finding_detail(ev)


def render_finding_detail(ev) -> None:
    st.write(ev.rule.description)
    st.write(f"**Why:** {ev.reason}")

    left, right = st.columns(2)
    left.write(f"**Parameter:** `{ev.rule.parameter}`")
    left.write(f"**Observed:** {ev.observed!r}" if ev.observed is not None
               else "**Observed:** nothing read")
    if ev.rule.threshold is not None:
        units = f" {ev.rule.units}" if ev.rule.units else ""
        right.write(
            f"**Requires:** {ev.rule.operator.value} {ev.rule.threshold}{units}"
        )
    else:
        right.write(f"**Test:** {ev.rule.operator.value}")
    right.write(f"**Severity:** {ev.rule.severity.value}")

    st.write(f"**Citation:** {ev.rule.citation.section}"
             + (f", p.{ev.rule.citation.page}" if ev.rule.citation.page else ""))
    if ev.rule.citation.quote:
        st.markdown(f"> {ev.rule.citation.quote}")
    if ev.rule.remedy:
        st.info(f"**Fix:** {ev.rule.remedy}")
    if not ev.rule.verified:
        st.caption(
            "This threshold has not been checked against the regulation PDF by a "
            "person, so the engine will not use it to pass or fail anything."
        )
    if ev.rule.notes:
        st.caption(ev.rule.notes)


def render_facts(report: "engine.Report") -> None:
    if not report.facts:
        return
    with st.expander(f"Extracted facts ({len(report.facts)})"):
        st.dataframe(
            [{"Field": k, "Value": v} for k, v in report.facts.items()],
            hide_index=True,
            width="stretch",
        )


def render_no_packet() -> None:
    rules = load_rules()
    verified = sum(1 for r in rules if r.verified)

    st.info(
        "No packet loaded. Upload an extracted facts JSON in the sidebar, or "
        "load the example, to see the findings for an application. The chat "
        "panel answers regulation questions either way."
    )

    st.subheader(f"Rule set ({len(rules)} requirements, {verified} verified)")
    st.dataframe(
        [
            {
                "Rule": r.id,
                "Parameter": r.parameter,
                "Test": r.operator.value,
                "Severity": r.severity.value,
                "Citation": r.citation.short(),
                "Verified": "yes" if r.verified else "no",
            }
            for r in rules
        ],
        hide_index=True,
        width="stretch",
    )
    if verified == 0:
        st.caption(
            "No rule has been certified by a person yet, so every check returns "
            "CANNOT VERIFY. That interlock is deliberate: a wrong regulatory "
            "number shown to permitting staff is worse than no number."
        )


def render_dashboard() -> "engine.Report | None":
    st.header("Pre-submission review")

    facts = st.session_state["facts"]
    if not facts:
        render_no_packet()
        return None

    report = evaluate(facts)
    st.caption(f"Packet: {st.session_state['facts_source']}")
    render_verdict(report)
    render_facts(report)
    render_findings(report)
    return report


# ---------------------------------------------------------------------------
# Right panel: the chat
# ---------------------------------------------------------------------------


def render_chat(report: "engine.Report | None") -> None:
    head, clear = st.columns([3, 1])
    head.header("Ask about a finding")
    if clear.button("Clear", disabled=not st.session_state["messages"]):
        st.session_state["messages"] = []
        st.session_state["pending"] = None
        st.rerun()

    if report is not None:
        st.caption(
            f"Grounded in this packet: verdict {report.verdict.value}, "
            f"{len(report.evaluations)} rule results, plus the regulation."
        )
    else:
        st.caption("Grounded in the rule set and the regulation.")

    if not st.session_state["messages"] and not st.session_state["pending"]:
        render_suggestions()

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Why did this rule fail?")

    # A suggestion click parks the question and reruns, so the button row is
    # gone by the time the reply streams in.
    pending = st.session_state["pending"]
    if pending:
        st.session_state["pending"] = None
        answer(pending, report)
    elif question:
        answer(question, report)


def render_suggestions() -> None:
    st.caption("Try one of these:")
    for i, prompt in enumerate(SUGGESTED_QUESTIONS):
        if st.button(prompt, key=f"suggest{i}", width="stretch"):
            st.session_state["pending"] = prompt
            st.rerun()


def answer(question: str, report: "engine.Report | None") -> None:
    """Send one question, with fresh context, and stream the reply."""
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Context is rebuilt per question so the sections retrieved match what was
    # just asked, and so the evaluation shown on the left is always the one the
    # model sees.
    system_prompt = chat_context.gather_context_for_query(
        question,
        report=report,
        graph=load_graph(),
        candidates=load_candidates(),
    )
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state["messages"]
    ]

    with st.chat_message("assistant"):
        try:
            reply = st.write_stream(chat_stream(history, system_prompt=system_prompt))
        except BedrockChatError as exc:
            render_chat_error(exc)
            st.session_state["messages"].pop()
            return

    st.session_state["messages"].append({"role": "assistant", "content": reply})


def render_chat_error(exc: BedrockChatError) -> None:
    # The client already produces an actionable message, so this only adds the
    # one thing it cannot know: that the findings on the left are unaffected.
    st.error(str(exc))
    st.caption(
        "The findings on the left still stand. They were computed by the rule "
        "engine, which does not call the model."
    )


# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="DNREC septic pre-check",
        page_icon="D",
        layout="wide",
    )
    init_state()
    render_sidebar()

    dashboard, chat = st.columns([2, 1], gap="large")
    with dashboard:
        report = render_dashboard()
    with chat:
        render_chat(report)


main()
