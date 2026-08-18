"""Context assembly for the chatbot.

Everything the model is allowed to know arrives through here: the rule
evaluations for the packet on screen, the regulation graph, and the verbatim
numeric passages pulled out of the regulation PDF.

The context is read-only by construction. The verdict is computed by the rule
engine before this module is called and is passed in as a finished result, so
nothing the model says can alter a finding. The prompt says so explicitly,
because a model asked to explain a decision will otherwise drift into making
one.

Retrieval here is lexical, not semantic. Titan embeddings are available but a
1000 section regulation with a fixed vocabulary answers term overlap well, and
a scored keyword match is inspectable in a way a cosine distance is not.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..rules.engine import Report
from ..rules.schema import Evaluation

GRAPH_PATH = config.OUT_DIR / "reg_graph.json"
CANDIDATES_PATH = config.OUT_DIR / "rule_candidates.json"

# A section reference needs at least one dot. Without that requirement every
# bare number in a question ("is 100 feet enough") reads as a section number and
# drags in an unrelated part of the regulation.
SECTION_RE = re.compile(r"\b(\d{1,2}(?:\.\d{1,3}){1,5})\b")

# Dropped before scoring. These carry no signal and, when every term has to
# match, they are what stops a real question from matching anything at all.
STOPWORDS = frozenset("""
a an and are as at be been but by can cannot could did do does doesn for from
get got had has have how i if in into is it its me must my need needs of on or
should show shows so some tell that the their them then there these they this
those to underup was were what when where which who why will with would you your
does rule rules regulation section sections say says mean means require required
requires requirement requirements about explain
""".split())

MAX_GRAPH_NODES = 8
MAX_CANDIDATES = 6
MAX_NODE_TEXT = 600
MAX_QUOTE = 700


def _terms(query: str) -> list[str]:
    """Content words from a question, lowercased, stopwords and noise dropped."""
    words = re.findall(r"[a-z0-9]+", query.lower())
    return [w for w in words if len(w) > 2 and w not in STOPWORDS]


# The graph holds four node types and they do not share a shape. Sections are
# numbered, exhibits are lettered, definitions carry a term, and rule nodes
# carry a description. Reading "number" off all of them yields blank labels for
# three of the four.


def _node_heading(node: dict) -> str:
    """Human readable identifier for a node, whatever type it is."""
    kind = node.get("type", "Section")

    if kind == "Exhibit":
        return f"Exhibit {node.get('letter', '?')}"
    if kind == "Definition":
        where = node.get("defined_in")
        term = node.get("term", "?")
        return f'Definition of "{term}"' + (f" (defined in {where})" if where else "")
    if kind == "Rule":
        cite = node.get("citation_section")
        page = node.get("citation_page")
        where = f" cites {cite}" + (f", p.{page}" if page else "") if cite else ""
        return f"Rule {node.get('rule_id', node.get('id', '?'))}{where}"
    return f"Section {node.get('number', '?')}"


def _node_title(node: dict) -> str:
    """The high signal line for a node: heading text, term, or description."""
    kind = node.get("type", "Section")
    if kind == "Definition":
        return node.get("term") or ""
    if kind == "Rule":
        return node.get("description") or node.get("rule_id") or ""
    return node.get("title") or ""


def _node_body(node: dict) -> str:
    """The supporting text for a node."""
    if node.get("type") == "Rule":
        bits = []
        if node.get("parameter"):
            bits.append(f"parameter {node['parameter']}")
        if node.get("operator"):
            bits.append(f"test {node['operator']}")
        if node.get("severity"):
            bits.append(f"severity {node['severity']}")
        if node.get("verified") is not None:
            bits.append(f"verified {node['verified']}")
        return ", ".join(bits)
    return node.get("text") or ""


# ---------------------------------------------------------------------------
# Regulation graph
# ---------------------------------------------------------------------------


class RegulationGraph:
    """Read-only index over out/reg_graph.json.

    Absent file is a normal state, not an error: the graph is a build artifact
    and a fresh checkout has not run `septic graph build` yet. Callers check
    .available and degrade to answering without regulation text.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path or GRAPH_PATH)
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._by_number: dict[str, dict] = {}
        self._children: dict[str, list[dict]] = {}
        self._parent: dict[str, str] = {}
        self._loaded = False

        if not self.path.exists():
            return

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.nodes = data.get("nodes", [])
        self.edges = data.get("edges", [])
        self._by_id = {n["id"]: n for n in self.nodes if "id" in n}
        # First occurrence wins. Duplicate numbers exist in the parse and the
        # earlier one is the heading, the later one usually a cross reference.
        for node in self.nodes:
            number = node.get("number")
            if number and number not in self._by_number:
                self._by_number[number] = node

        for edge in self.edges:
            if edge.get("type") != "CONTAINS":
                continue
            child = self._by_id.get(edge.get("target", ""))
            if child is None:
                continue
            self._children.setdefault(edge["source"], []).append(child)
            self._parent[edge["target"]] = edge["source"]

        self._loaded = True

    @property
    def available(self) -> bool:
        return self._loaded

    def get_section(self, number: str) -> dict | None:
        return self._by_number.get(number)

    def get_children(self, section_id: str) -> list[dict]:
        return self._children.get(section_id, [])

    def get_parent(self, section_id: str) -> dict | None:
        parent_id = self._parent.get(section_id)
        return self._by_id.get(parent_id) if parent_id else None

    def get_section_context(self, number: str, max_children: int = 6) -> list[dict]:
        """A section with the parent above it and the subsections under it.

        The hierarchy matters for this regulation: a threshold in 5.3.12.1.3
        is scoped by what 5.3.12 says it applies to, and quoting the leaf alone
        loses that.
        """
        node = self.get_section(number)
        if node is None:
            return []

        out: list[dict] = []
        parent = self.get_parent(node["id"])
        if parent is not None:
            out.append(parent)
        out.append(node)
        out.extend(self.get_children(node["id"])[:max_children])
        return out

    def find_sections_in_query(self, query: str) -> list[str]:
        """Section numbers named in a question, only those that exist."""
        seen: list[str] = []
        for number in SECTION_RE.findall(query):
            if number in self._by_number and number not in seen:
                seen.append(number)
        return seen

    def search(self, query: str, limit: int = MAX_GRAPH_NODES) -> list[dict]:
        """Rank sections by term overlap against the title and body.

        Scored rather than filtered. Requiring every term to appear is what made
        "isolation distances from shellfish waters" return nothing while
        "shellfish" alone returned three sections.
        """
        if not self._loaded:
            return []

        terms = _terms(query)
        if not terms:
            return []

        phrase = " ".join(terms)
        scored: list[tuple[int, int, dict]] = []

        for node in self.nodes:
            title = _node_title(node).lower()
            text = _node_body(node).lower()
            if not title and not text:
                continue

            # Title carries more signal than body: it is the heading the
            # drafters chose for the requirement.
            score = sum(3 for t in terms if t in title)
            score += sum(1 for t in terms if t in text)
            if not score:
                continue
            if phrase and phrase in title:
                score += 5
            elif phrase and phrase in text:
                score += 3

            # Break ties toward sections that actually carry body text, since a
            # heading with no text tells a reviewer nothing.
            scored.append((score, len(text), node))

        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [node for _, _, node in scored[:limit]]


_graph: RegulationGraph | None = None


def get_graph(path: Path | None = None) -> RegulationGraph:
    """Process wide graph singleton. Pass a path to bypass the cache."""
    global _graph
    if path is not None:
        return RegulationGraph(path)
    if _graph is None:
        _graph = RegulationGraph()
    return _graph


# ---------------------------------------------------------------------------
# Verbatim numeric passages
# ---------------------------------------------------------------------------


@dataclass
class CandidateIndex:
    """Numeric threshold passages extracted from the regulation PDF.

    These are the sentences that state a number with a unit, quoted verbatim
    with their section and page. They are candidates, not rules: nothing here
    has been confirmed by a person, and the prompt says so, because a number
    lifted out of this file and presented as a requirement is exactly the
    failure the verified flag exists to prevent.
    """

    passages: list[dict]

    @property
    def available(self) -> bool:
        return bool(self.passages)

    def search(self, query: str, limit: int = MAX_CANDIDATES) -> list[dict]:
        terms = _terms(query)
        if not terms or not self.passages:
            return []

        scored: list[tuple[int, dict]] = []
        for passage in self.passages:
            quote = (passage.get("quote") or "").lower()
            units = " ".join(passage.get("units") or []).lower()
            if not quote:
                continue

            score = sum(2 for t in terms if t in quote)
            score += sum(3 for t in terms if t in units)
            if not score:
                continue
            # Obligation and setback language is what a reviewer is asking
            # about when they ask about a distance.
            score += (2 if passage.get("obligation") else 0)
            score += (1 if passage.get("setback") else 0)
            scored.append((score, passage))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [p for _, p in scored[:limit]]


def load_candidates(path: Path | None = None) -> CandidateIndex:
    """Load the cached passages. Empty index when the cache is absent."""
    path = Path(path or CANDIDATES_PATH)
    if not path.exists():
        return CandidateIndex(passages=[])
    data = json.loads(path.read_text(encoding="utf-8"))
    passages = data.get("candidates", data) if isinstance(data, dict) else data
    return CandidateIndex(passages=passages or [])


def build_candidates_cache(path: Path | None = None) -> Path:
    """Extract passages from the regulation PDF and cache them as JSON.

    Separated from loading and never run implicitly: it parses 245 pages with
    pdfplumber and takes tens of seconds, which is not something to do inside a
    request while a reviewer waits on an answer.
    """
    from ..rules import candidates as cand

    path = Path(path or CANDIDATES_PATH)
    found = cand.extract(config.REGULATION_PDF)
    config.ensure_dirs()
    path.write_text(
        json.dumps(
            {
                "source": str(config.REGULATION_PDF.name),
                "count": len(found),
                "candidates": [c.to_json() for c in found],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


_candidates: CandidateIndex | None = None


def get_candidates() -> CandidateIndex:
    global _candidates
    if _candidates is None:
        _candidates = load_candidates()
    return _candidates


def reset_caches() -> None:
    """Drop the singletons so a rebuilt artifact is picked up."""
    global _graph, _candidates
    _graph = None
    _candidates = None


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_BASE = """\
You are helping a DNREC reviewer understand the output of a septic permit
pre-check. You explain findings. You do not make them.

The verdict and every rule outcome below were computed by a deterministic rule
engine before you were called. They are settled. Explain them, and never restate
one as something other than what it says. If you think a finding is wrong, say
which rule and why, and say that a person has to change the rule set. Do not
issue a corrected verdict of your own.

How to read a rule outcome:
- PASS: the requirement was checked against a value read off the packet and met.
- FAIL: it was checked and not met. This is what gets an application returned.
- UNKNOWN: no answer, and reviewers see it as CANNOT VERIFY. There are three
  separate causes and they need different things from the applicant:
    1. The rule is unverified. The threshold has not been read off the
       regulation PDF and confirmed by a person, so the engine refuses to use
       it. Nothing is wrong with the application. Someone has to certify the
       rule.
    2. A value was missing or unreadable on the packet. The applicant has to
       supply it.
    3. A value was present but would not parse as a number.
  Always say which of the three applies. The reason line on the finding tells
  you.

Severity: a FAIL at "return" severity is a return reason on its own. "advisory"
is worth fixing but does not by itself get the application returned.

Citing:
- Give the section, and the page when you have it: "Section 5.3.12.1.3, p.42".
- Quote the regulation only from the text supplied below. If a passage you need
  is not here, say you do not have it rather than reconstructing it.
- Passages under "Candidate passages" are unconfirmed extractions. You may quote
  them, but say they have not been verified against the PDF by a person and must
  not be relied on as the operative number.
- Never state a threshold that appears nowhere in the context below. A wrong
  regulatory number shown to permitting staff is worse than no number, which is
  the whole reason unverified rules return UNKNOWN.

Be brief and concrete. A reviewer wants to know what is wrong, what the
regulation says, and what the applicant has to do.
"""

NO_PACKET_NOTE = """\
## No packet loaded

No application is on screen. Answer from the rule set and the regulation. If the
question is about a specific application, say that one has to be loaded first.
"""


def format_evaluation_context(report: Report) -> str:
    """The engine's findings, rendered for the model."""
    counts = report.counts()
    lines = [
        "## Findings for the packet on screen",
        "",
        f"Verdict: {report.verdict.value}",
        f"Pass {counts['pass']}, fail {counts['fail']}, "
        f"cannot verify {counts['unknown']}, "
        f"return reasons {counts['return_reasons']}.",
        "",
        "This verdict is final for this packet. It was computed before you were "
        "called.",
        "",
    ]

    if report.facts:
        lines += ["### Values read off the packet", ""]
        for key, value in sorted(report.facts.items()):
            shown = "(empty)" if value in (None, "") else repr(value)
            lines.append(f"- {key}: {shown}")
        lines.append("")

    lines += ["### Rule results", ""]
    for ev in report.evaluations:
        lines.extend(_format_evaluation(ev))

    return "\n".join(lines)


def _format_evaluation(ev: Evaluation) -> list[str]:
    rule = ev.rule
    lines = [f"#### {rule.id} — {ev.outcome.value}"]
    if ev.outcome.value == "UNKNOWN":
        lines[0] += " (shown to the reviewer as CANNOT VERIFY)"

    lines.append(f"- What it checks: {rule.description.strip()}")
    lines.append(f"- Engine reason: {ev.reason}")
    lines.append(f"- Parameter: {rule.parameter}")

    observed = "nothing was read" if ev.observed in (None, "") else repr(ev.observed)
    lines.append(f"- Observed: {observed}")

    if rule.threshold is not None:
        units = f" {rule.units}" if rule.units else ""
        lines.append(f"- Requires: {rule.operator.value} {rule.threshold}{units}")
    else:
        lines.append(f"- Test: {rule.operator.value}")

    lines.append(f"- Severity: {rule.severity.value}")
    if rule.applies_to:
        lines.append(f"- Only applies when: {rule.applies_to}")

    citation = rule.citation
    where = citation.section + (f", p.{citation.page}" if citation.page else "")
    lines.append(f"- Citation: {where}")
    if citation.quote:
        lines.append(f'- Regulation text: "{citation.quote}"')

    if rule.verified:
        lines.append("- Verified: yes, a person confirmed this threshold.")
    else:
        lines.append(
            "- Verified: NO. The threshold has not been confirmed against the "
            "regulation PDF, which is why this returns UNKNOWN regardless of "
            "the packet."
        )
    if rule.remedy:
        lines.append(f"- Suggested fix: {rule.remedy}")
    if rule.notes:
        lines.append(f"- Notes: {rule.notes}")
    lines.append("")
    return lines


def format_graph_context(nodes: list[dict]) -> str:
    if not nodes:
        return ""

    lines = ["## Regulation text", ""]
    for node in nodes:
        heading = _node_heading(node)
        page = node.get("page")
        where = f", p.{page}" if page else ""
        title = " ".join(_node_title(node).split())
        body = " ".join(_node_body(node).split())

        lines.append(f"**{heading}{where}** {title}".rstrip())
        if body:
            if len(body) > MAX_NODE_TEXT:
                body = body[:MAX_NODE_TEXT].rstrip() + " [...]"
            lines.append(body)
        lines.append("")
    return "\n".join(lines)


def format_candidates_context(passages: list[dict]) -> str:
    if not passages:
        return ""

    lines = [
        "## Candidate passages (extracted, NOT verified by a person)",
        "",
        "Numeric passages from the regulation PDF. Quote them only with the "
        "caveat that they are unconfirmed.",
        "",
    ]
    for p in passages:
        section = p.get("section", "?")
        page = p.get("page")
        quote = " ".join((p.get("quote") or "").split())
        if len(quote) > MAX_QUOTE:
            quote = quote[:MAX_QUOTE].rstrip() + " [...]"
        marks = []
        if p.get("obligation"):
            marks.append("obligation language")
        if p.get("setback"):
            marks.append("setback language")
        units = ", ".join(p.get("units") or [])

        where = f"Section {section}" + (f", p.{page}" if page else "")
        tail = f" [{'; '.join(marks)}]" if marks else ""
        lines.append(f"- {where}{tail}")
        if units:
            lines.append(f"  units: {units}")
        lines.append(f'  "{quote}"')
        lines.append("")
    return "\n".join(lines)


def build_system_prompt(
    report: Report | None = None,
    graph_context: list[dict] | None = None,
    candidate_context: list[dict] | None = None,
    graph_available: bool = True,
) -> str:
    """Assemble the prompt from whichever sources are present."""
    parts = [SYSTEM_PROMPT_BASE]

    def section(body: str) -> None:
        if body:
            parts.extend(["", "---", "", body])

    section(format_evaluation_context(report) if report else NO_PACKET_NOTE)
    section(format_graph_context(graph_context or []))
    section(format_candidates_context(candidate_context or []))

    if not graph_available:
        section(
            "## Regulation text unavailable\n\n"
            "The regulation graph has not been built, so no section text is "
            "available. Do not quote or paraphrase the regulation. Say it is "
            "not loaded and that `python -m septic graph build` will load it."
        )

    return "\n".join(parts)


def gather_context_for_query(
    query: str,
    report: Report | None = None,
    graph: RegulationGraph | None = None,
    candidates: CandidateIndex | None = None,
) -> str:
    """Retrieve for one question and return the finished system prompt.

    Called per question rather than once per session so the sections retrieved
    track what was actually asked.
    """
    graph = graph if graph is not None else get_graph()
    candidates = candidates if candidates is not None else get_candidates()

    nodes: list[dict] = []
    seen: set[str] = set()

    def add(node: dict) -> None:
        node_id = node.get("id")
        if node_id and node_id not in seen:
            seen.add(node_id)
            nodes.append(node)

    if graph.available:
        # A named section is an explicit request. Resolve it with its hierarchy
        # and let it take precedence over keyword hits.
        for number in graph.find_sections_in_query(query):
            for node in graph.get_section_context(number):
                add(node)
        for node in graph.search(query, limit=MAX_GRAPH_NODES):
            add(node)

    return build_system_prompt(
        report=report,
        graph_context=nodes[: MAX_GRAPH_NODES + 8],
        candidate_context=candidates.search(query, limit=MAX_CANDIDATES),
        graph_available=graph.available,
    )
