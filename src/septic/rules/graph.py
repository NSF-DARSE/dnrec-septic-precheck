"""Regulation graph.

Builds a directed graph of the Delaware On-Site Wastewater regulation (2014).
Node types: Section, Exhibit, Definition, Rule.
Edge types: CONTAINS, REFERENCES, DEFINES, USES_TERM, CITES, EXCEPTION.

Backend: networkx 3.6.1, persisted as JSON. No external database.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from .. import config

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Header fragments to exclude (running header on every page)
HEADER_FRAGMENTS = (
    "Delaware Department of Natural Resources and Environmental Control",
    "Division of Water",
    "Groundwater Discharges Section",
    "Del.C. Ch. 60",
    "7 Del.C. Ch.",
)

# Section heading: digits-dot-digits at start of line.
#
# The depth limit matters. This regulation nests six levels deep in places, for
# example 5.2.4.2.5.7 (the 120 minutes per inch percolation limit) and
# 5.2.4.2.4.2 (the 20 inch limiting zone rule). An earlier limit of five levels
# silently dropped exactly those sections, which are where the specific numeric
# thresholds live, so the graph looked healthy while missing the content rules
# are drawn from. Kept at seven to leave headroom.
HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,6})\s+(.+)", re.MULTILINE)

# Cross references in body text
XREF_SECTION_RE = re.compile(
    r"(?:"
    r"(?:Sections?|sections?)\s+"
    r"|"
    r"(?:in|per|of|under|above|below|see|from|pursuant\s+to|"
    r"referenced\s+in|accordance\s+with|required\s+(?:by|in|under)|"
    r"specified\s+in|described\s+in|defined\s+in|provided\s+in|"
    r"set\s+forth\s+in|established\s+in|identified\s+in|"
    r"listed\s+in|outlined\s+in)\s+"
    r")"
    r"(\d{1,2}(?:\.\d{1,3}){1,4})"
)
XREF_EXHIBIT_RE = re.compile(r"Exhibit\s+([A-Z][A-Z0-9]?(?:-\d+)?)")

# Definition pattern: "term" means
DEFINITION_RE = re.compile(r'"([^"]{3,60})"\s+means\b')

# Exception language
EXCEPTION_RE = re.compile(
    r"\b(except\s+(?:as|that|where|when|for)|"
    r"exception|notwithstanding|unless|"
    r"provided\s+that|does\s+not\s+apply|"
    r"shall\s+not\s+apply|excluded?\s+from|"
    r"exempted?\s+from|waiver|variance)\b",
    re.IGNORECASE,
)

# Obligation language (for orphans detection)
OBLIGATION_RE = re.compile(
    r"\b(shall|must|may\s+not|shall\s+not|is\s+required|are\s+required|"
    r"minimum|maximum|no\s+less\s+than|no\s+more\s+than|at\s+least|"
    r"not\s+exceed|prohibited)\b",
    re.IGNORECASE,
)

# Page number at end of a TOC line
TOC_TRAILING_PAGE_RE = re.compile(r"\s+\d{1,3}\s*$")

DEFAULT_GRAPH_PATH = Path(config.OUT_DIR) / "reg_graph.json"


# ---------------------------------------------------------------------------
# Text extraction (reuses pypdfium2 from candidates.py)
# ---------------------------------------------------------------------------

def _extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return (1-based page number, normalized text) for each page."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    pages = []
    for i in range(len(doc)):
        raw = doc[i].get_textpage().get_text_range()
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        pages.append((i + 1, text))
    return pages


def _is_header_line(line: str) -> bool:
    """True if the line is a running page header."""
    stripped = line.strip()
    return any(frag in stripped for frag in HEADER_FRAGMENTS)


# ---------------------------------------------------------------------------
# Heading validation (rejects false positives)
# ---------------------------------------------------------------------------

@dataclass
class RawHeading:
    """A candidate heading before validation."""
    page: int
    number: str
    title: str
    line_text: str


@dataclass
class HeadingStats:
    """Statistics from heading extraction for reporting."""
    raw_candidates: int = 0
    accepted: int = 0
    rejected_header: int = 0
    rejected_duplicate: int = 0
    rejected_list_item: int = 0
    rejected_sentence: int = 0
    rejected_toc: int = 0


def _is_list_item(number: str, title: str, page: int,
                   seen_numbers: set[str]) -> bool:
    """Detect ordered list items masquerading as section headings.

    The regulation uses dotted numbering (5.2.4.2) for sections. Ordered list
    items within a section body use plain sequential integers ("1. A well permit
    is required ..."). The key distinction:

    1. A bare single digit (no dots) that does not match a known top-level
       chapter number (1.0 through 8.0) is a list item.
    2. A number whose parent does not exist in the document AND whose immediate
       predecessor at the same level does not exist is likely a list item in an
       unnumbered context.
    """
    parts = number.split(".")
    if len(parts) == 1:
        # Bare single digit. Real chapters use X.0 form (1.0, 2.0, ..., 8.0).
        # Some pages show standalone digits that are list markers.
        num_val = int(parts[0])
        if num_val > 8:
            return True
        # For 1-8, it is only a real chapter heading if the title looks like
        # a chapter title (short, title-case or ALL CAPS, no verb phrases)
        word_count = len(title.split())
        if word_count > 8:
            return True
        return False

    # Reject numbers starting with 0 (table values like "0.00")
    if parts[0] == "0":
        return True

    # For dotted numbers, check if the parent exists
    parent = ".".join(parts[:-1])
    if parent not in seen_numbers:
        # If the grandparent also does not exist and depth > 2, likely spurious
        grandparent = ".".join(parts[:-2]) if len(parts) > 2 else ""
        if grandparent and grandparent not in seen_numbers:
            return True
    return False


def extract_headings(
    pages: list[tuple[int, str]], toc_pages: int = 8
) -> tuple[list[RawHeading], HeadingStats]:
    """Extract validated section headings from the regulation.

    This regulation numbers almost everything as sections, including individual
    requirements. A numbered item like "3.7.1 In no case shall an active OWTDS..."
    IS a section in this document, not a list item. The only false positives to
    reject are:

    - Running headers ("7 Del.C. Ch. 60" on every page)
    - Plain integer list items ("1. A well permit is required...")
    - Numbers appearing many times (page numbers, list counters)
    - TOC entries on front matter pages

    Expected: approximately 1979 unique headings after filtering.
    """
    stats = HeadingStats()

    # First pass: collect all raw candidates from body pages
    raw: list[RawHeading] = []
    for page_num, text in pages:
        if page_num <= toc_pages:
            stats.rejected_toc += 1
            continue
        for m in HEADING_RE.finditer(text):
            number = m.group(1)
            rest = m.group(2).strip().split("\n")[0].strip()
            stats.raw_candidates += 1
            raw.append(RawHeading(page=page_num, number=number,
                                  title=rest, line_text=m.group(0)))

    # Count occurrences of each number across the document
    from collections import Counter
    number_counts = Counter(h.number for h in raw)

    # Build accepted set in document order
    accepted: list[RawHeading] = []
    seen_numbers: set[str] = set()

    for heading in raw:
        # Reject running headers
        if _is_header_line(heading.line_text):
            stats.rejected_header += 1
            continue

        # Reject numbers that appear more than 3 times (headers, list counters)
        if number_counts[heading.number] > 3:
            stats.rejected_duplicate += 1
            continue

        # Reject duplicates (take only first occurrence of each number)
        if heading.number in seen_numbers:
            stats.rejected_duplicate += 1
            continue

        # Reject list items
        if _is_list_item(heading.number, heading.title, heading.page, seen_numbers):
            stats.rejected_list_item += 1
            continue

        accepted.append(heading)
        seen_numbers.add(heading.number)

    # Also incorporate headings that appear only in the TOC (pages 1 through
    # toc_pages) but not in the body. These are real sections whose body text
    # starts differently (e.g. chapter headings "1.0 AUTHORITY AND SCOPE").
    toc_only: list[RawHeading] = []
    for page_num, text in pages:
        if page_num > toc_pages:
            break
        for m in HEADING_RE.finditer(text):
            number = m.group(1)
            rest = m.group(2).strip().split("\n")[0].strip()
            if _is_header_line(m.group(0)):
                continue
            parts = number.split(".")
            if len(parts) == 1 and int(parts[0]) > 8:
                continue
            # Strip trailing page number from TOC entries
            rest = TOC_TRAILING_PAGE_RE.sub("", rest).strip()
            if number not in seen_numbers and rest:
                toc_only.append(RawHeading(
                    page=page_num, number=number, title=rest,
                    line_text=m.group(0),
                ))
                seen_numbers.add(number)

    accepted.extend(toc_only)
    # Sort by section number for stable output
    accepted.sort(key=lambda h: tuple(
        int(p) if p.isdigit() else 0 for p in h.number.split(".")
    ))

    stats.accepted = len(accepted)
    return accepted, stats


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

# A line in the exhibits table of contents, for example "C. Minimum Isolation
# Distances" or "AA. System Abandonment Report".
EXHIBIT_TOC_ENTRY_RE = re.compile(r"^([A-Z]{1,2})\.\s+(.{4,90})$")

# Words too common to identify an exhibit page by.
TITLE_STOPWORDS = {
    "and", "or", "of", "the", "for", "to", "a", "an", "in", "on", "with", "by",
    "typical", "example", "design", "designs", "report", "guide", "based",
    "upon", "system", "systems",
}


def normalize_dashes(text: str) -> str:
    """Normalize the dash variants the PDF uses so titles compare cleanly."""
    for bad in ("\u2013", "\u2014", "\u2212"):
        text = text.replace(bad, "-")
    return " ".join(text.split())


def exhibit_titles(pages: list[tuple[int, str]]) -> tuple[dict[str, str], int]:
    """Parse the exhibits table of contents into letter -> title.

    Returns the mapping and the page it was found on, so the content search can be
    restricted to pages after it.
    """
    titles: dict[str, str] = {}
    toc_page = 0
    for page_num, text in pages:
        if "8.0 Exhibits" not in text or "Table of Contents" not in text:
            continue
        toc_page = page_num
        for line in text.splitlines():
            match = EXHIBIT_TOC_ENTRY_RE.match(line.strip())
            if match:
                titles[match.group(1).upper()] = normalize_dashes(match.group(2))
        break
    return titles, toc_page


def locate_exhibit_content(
    title: str, pages: list[tuple[int, str]], after_page: int
) -> tuple[int | None, str]:
    """Find the page holding an exhibit's content, or (None, "").

    Every significant word of the exhibit title must appear on the page. Many
    exhibits are scanned figures with no text layer, and those correctly return
    nothing: an exhibit whose content cannot be read must stay visible as an
    unread dependency rather than be quietly filled in from the wrong page.
    """
    significant = [
        w for w in re.findall(r"[A-Za-z]{3,}", title.lower())
        if w not in TITLE_STOPWORDS
    ]
    if not significant:
        return None, ""

    for page_num, text in pages:
        if page_num <= after_page:
            continue
        haystack = text.lower()
        if all(word in haystack for word in significant):
            body = "\n".join(
                line for line in text.splitlines()
                if line.strip() and not _is_header_line(line)
            )
            return page_num, body
    return None, ""


def _parent_number(number: str) -> str | None:
    """Return the parent section number, or None for top-level."""
    parts = number.split(".")
    if len(parts) <= 1:
        return None
    return ".".join(parts[:-1])


EXHIBIT_CITATION_RE = re.compile(r"^Exhibit\s+([A-Z][A-Z0-9]?(?:-\d+)?)$", re.I)


def _resolve_citation(section: str | None) -> str | None:
    """Map a rule citation string onto a graph node id.

    Handles both "5.3.12.1.3" and "Exhibit C". Returns None for a placeholder or
    an unrecognised form rather than guessing, so a citation that cannot be
    resolved shows up as a rule with no outgoing CITES edge instead of an edge
    pointing at the wrong node.
    """
    if not section or section.strip() in ("", "TBD"):
        return None
    section = section.strip()
    exhibit = EXHIBIT_CITATION_RE.match(section)
    if exhibit:
        return f"exhibit:{exhibit.group(1).upper()}"
    return f"section:{section}"


def _has_readable_content(node_attrs: dict) -> bool:
    """Whether a node's content has actually been extracted.

    Sections in this regulation carry their first sentence in the heading line
    itself, so a short section can have a meaningful title and an empty body. That
    section has been read, and reporting it as an unread dependency would bury the
    genuine cases in noise. Exhibits are the genuine cases: they are figures and
    tables whose content is not in the text layer at all, so they have neither
    body text nor a real title.
    """
    if node_attrs.get("text", "").strip():
        return True
    if node_attrs.get("type") == "Section":
        # A title longer than a bare label means the content is in the heading.
        return len(node_attrs.get("title", "").split()) >= 4
    return False


def _collect_section_texts(
    headings: list[RawHeading], pages: list[tuple[int, str]]
) -> dict[str, str]:
    """Collect text bodies for all sections based on heading boundaries.

    For each section, the text is everything between its heading line and the
    next heading (or end of page span). This is more reliable than scanning
    forward from a regex match.
    """
    # Build a full document text with page boundaries marked
    # Strategy: for each heading, find its location and collect text until next heading
    all_numbers = {h.number for h in headings}
    section_texts: dict[str, str] = {}

    # Index headings by page for quick lookup
    headings_by_page: dict[int, list[RawHeading]] = {}
    for h in headings:
        headings_by_page.setdefault(h.page, []).append(h)

    # For each page, split text at heading boundaries
    for page_num, text in pages:
        lines = text.splitlines()
        # Find all heading positions on this page
        heading_positions: list[tuple[int, str]] = []  # (line_idx, number)
        for i, line in enumerate(lines):
            if _is_header_line(line):
                continue
            m = HEADING_RE.match(line)
            if m and m.group(1) in all_numbers:
                heading_positions.append((i, m.group(1)))

        # Collect text between heading positions
        for idx, (line_idx, number) in enumerate(heading_positions):
            # Find end: next heading on this page, or end of page
            if idx + 1 < len(heading_positions):
                end_idx = heading_positions[idx + 1][0]
            else:
                end_idx = len(lines)

            # Skip the heading line itself, collect body lines
            body_lines = []
            for i in range(line_idx + 1, end_idx):
                line = lines[i]
                if _is_header_line(line):
                    continue
                body_lines.append(line)

            body = "\n".join(body_lines).strip()
            if number in section_texts:
                # Append continuation (section spans pages)
                section_texts[number] += "\n" + body
            else:
                section_texts[number] = body

    return section_texts


def build_graph(
    pdf_path: Path | None = None,
    rules_path: Path | None = None,
) -> tuple[nx.DiGraph, HeadingStats]:
    """Parse the regulation PDF and build the full graph.

    Returns the graph and extraction statistics.
    """
    from .engine import load_rules

    pdf_path = Path(pdf_path or config.REGULATION_PDF)
    rules_path = Path(rules_path or (Path(__file__).parent / "rules_7101.yaml"))

    pages = _extract_pages(pdf_path)
    headings, stats = extract_headings(pages)

    G = nx.DiGraph()
    all_section_numbers = {h.number for h in headings}

    # Collect text for all sections in one pass (more reliable than per-section)
    section_texts = _collect_section_texts(headings, pages)

    # --- Section nodes ---
    for heading in headings:
        section_text = section_texts.get(heading.number, "")
        G.add_node(
            f"section:{heading.number}",
            type="Section",
            number=heading.number,
            title=heading.title,
            page=heading.page,
            text=section_text[:2000],  # cap to keep JSON reasonable
        )

    # --- CONTAINS edges (parent-child hierarchy) ---
    for heading in headings:
        parent = _parent_number(heading.number)
        if parent and f"section:{parent}" in G:
            G.add_edge(
                f"section:{parent}",
                f"section:{heading.number}",
                type="CONTAINS",
            )

    # --- Exhibit nodes ---
    # Exhibits are referenced by letter throughout the body but their content sits
    # in section 8.0 at the back. The letter to title mapping comes from the
    # exhibits table of contents, and the content page is located by matching the
    # title, so a text bearing exhibit such as C (the isolation distance table)
    # becomes readable while a scanned figure stays correctly empty.
    exhibit_refs: set[str] = set()
    full_text = "\n".join(text for _, text in pages)
    for m in XREF_EXHIBIT_RE.finditer(full_text):
        exhibit_refs.add(m.group(1))

    titles, toc_page = exhibit_titles(pages)
    exhibit_refs.update(titles)

    for letter in sorted(exhibit_refs):
        title = titles.get(letter, "")
        page, body = (None, "")
        if title:
            page, body = locate_exhibit_content(title, pages, toc_page)
        G.add_node(
            f"exhibit:{letter}",
            type="Exhibit",
            letter=letter,
            title=title or f"Exhibit {letter}",
            page=page,
            text=body[:4000],
        )

    # --- REFERENCES edges (section -> section or exhibit cross-refs) ---
    for heading in headings:
        node_id = f"section:{heading.number}"
        section_text = G.nodes[node_id].get("text", "")
        title_and_text = heading.title + " " + section_text

        # Section cross-references
        for m in XREF_SECTION_RE.finditer(title_and_text):
            target = m.group(1)
            target_id = f"section:{target}"
            if target_id in G and target != heading.number:
                G.add_edge(node_id, target_id, type="REFERENCES")

        # Exhibit references
        for m in XREF_EXHIBIT_RE.finditer(title_and_text):
            target_id = f"exhibit:{m.group(1)}"
            if target_id in G:
                G.add_edge(node_id, target_id, type="REFERENCES")

    # --- Definition nodes ---
    # Definitions are typically in section 2.x
    definitions: dict[str, str] = {}  # term -> defining section number
    for heading in headings:
        if not heading.number.startswith("2"):
            continue
        node_id = f"section:{heading.number}"
        section_text = G.nodes[node_id].get("text", "")
        for m in DEFINITION_RE.finditer(section_text):
            term = m.group(1).strip()
            if len(term) >= 3:
                definitions[term] = heading.number

    # Also scan the definitions section text that may not have subheadings
    def_section_text = ""
    for pg_num, text in pages:
        if 10 <= pg_num <= 25:  # definitions section is roughly pages 10-25
            def_section_text += text + "\n"
    for m in DEFINITION_RE.finditer(def_section_text):
        term = m.group(1).strip()
        if len(term) >= 3 and term not in definitions:
            definitions[term] = "2.0"

    for term, def_section in definitions.items():
        safe_id = f"definition:{term.lower().replace(' ', '_')}"
        G.add_node(
            safe_id,
            type="Definition",
            term=term,
            defined_in=def_section,
            text="",
        )
        # DEFINES edge from definition section to the term
        source_id = f"section:{def_section}"
        if source_id in G:
            G.add_edge(source_id, safe_id, type="DEFINES")

    # --- USES_TERM edges ---
    for heading in headings:
        if heading.number.startswith("2"):
            continue  # skip definitions section itself
        node_id = f"section:{heading.number}"
        section_text = G.nodes[node_id].get("text", "")
        full = heading.title + " " + section_text
        for term, _ in definitions.items():
            if term.lower() in full.lower():
                safe_id = f"definition:{term.lower().replace(' ', '_')}"
                if safe_id in G:
                    G.add_edge(node_id, safe_id, type="USES_TERM")

    # --- EXCEPTION edges ---
    # A section carrying exception language is linked to the sections it
    # modifies. If it cross-references another section, the exception applies
    # to that section. If no cross-ref is found, link to the parent section.
    for heading in headings:
        node_id = f"section:{heading.number}"
        section_text = G.nodes[node_id].get("text", "")
        if not EXCEPTION_RE.search(section_text):
            continue

        linked = False
        # Link to cross-referenced sections within the exception text
        for m in XREF_SECTION_RE.finditer(section_text):
            target = m.group(1)
            target_id = f"section:{target}"
            if target_id in G and target != heading.number:
                G.add_edge(node_id, target_id, type="EXCEPTION")
                linked = True

        # If no explicit cross-ref, link exception to parent section
        if not linked:
            parent = _parent_number(heading.number)
            if parent:
                parent_id = f"section:{parent}"
                if parent_id in G:
                    G.add_edge(node_id, parent_id, type="EXCEPTION")

    # --- Rule nodes and CITES edges ---
    rules = load_rules(rules_path)
    for rule in rules:
        rule_id = f"rule:{rule.id}"
        G.add_node(
            rule_id,
            type="Rule",
            rule_id=rule.id,
            description=rule.description,
            parameter=rule.parameter,
            operator=rule.operator.value,
            severity=rule.severity.value,
            verified=rule.verified,
            citation_section=rule.citation.section,
            citation_page=rule.citation.page,
        )
        # CITES edge from rule to what it cites. A citation can name a section or
        # an exhibit: several isolation distance rules cite Exhibit C directly,
        # because Section 5.3.4.1 creates the obligation but holds no numbers and
        # the values live in the exhibit table. Resolving only section citations
        # would leave those rules with no outgoing edge, which would make
        # unresolved() report nothing for exactly the dependency that matters.
        target_id = _resolve_citation(rule.citation.section)
        if target_id and target_id in G:
            G.add_edge(rule_id, target_id, type="CITES")

    return G, stats


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _graph_to_json(G: nx.DiGraph) -> dict:
    """Serialize graph to a stable JSON schema."""
    nodes = []
    for node_id, attrs in G.nodes(data=True):
        entry = {"id": node_id}
        entry.update(attrs)
        nodes.append(entry)

    edges = []
    for source, target, attrs in G.edges(data=True):
        entry = {"source": source, "target": target}
        entry.update(attrs)
        edges.append(entry)

    return {
        "schema_version": 1,
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "nodes": nodes,
        "edges": edges,
    }


def _graph_from_json(data: dict) -> nx.DiGraph:
    """Rebuild graph from JSON."""
    G = nx.DiGraph()
    for entry in data["nodes"]:
        node_id = entry.pop("id")
        G.add_node(node_id, **entry)
    for entry in data["edges"]:
        source = entry.pop("source")
        target = entry.pop("target")
        G.add_edge(source, target, **entry)
    return G


def save_graph(G: nx.DiGraph, path: Path | None = None) -> Path:
    """Write graph to JSON."""
    path = Path(path or DEFAULT_GRAPH_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _graph_to_json(G)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_graph(path: Path | None = None) -> nx.DiGraph:
    """Load graph from persisted JSON."""
    path = Path(path or DEFAULT_GRAPH_PATH)
    if not path.exists():
        raise FileNotFoundError(f"graph file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return _graph_from_json(data)


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def context(G: nx.DiGraph, section_number: str) -> dict[str, Any]:
    """Everything needed to judge a section in one call.

    Returns:
        - text: the section's own text
        - ancestors: parent sections via CONTAINS hierarchy
        - references: sections and exhibits it cross-references
        - definitions: defined terms it uses
        - exceptions: sections that carry exception language modifying it
    """
    node_id = f"section:{section_number}"
    if node_id not in G:
        return {"error": f"section {section_number} not found in graph"}

    node = G.nodes[node_id]
    result: dict[str, Any] = {
        "section": section_number,
        "title": node.get("title", ""),
        "page": node.get("page"),
        "text": node.get("text", ""),
    }

    # Ancestors via CONTAINS (walk up)
    ancestors = []
    current = node_id
    while True:
        parents = [
            src for src, tgt, d in G.in_edges(current, data=True)
            if d.get("type") == "CONTAINS"
        ]
        if not parents:
            break
        parent_id = parents[0]
        parent_data = G.nodes[parent_id]
        ancestors.append({
            "number": parent_data.get("number", ""),
            "title": parent_data.get("title", ""),
        })
        current = parent_id
    result["ancestors"] = list(reversed(ancestors))

    # References (outgoing REFERENCES edges)
    references = []
    for _, target, d in G.out_edges(node_id, data=True):
        if d.get("type") == "REFERENCES":
            target_data = G.nodes[target]
            references.append({
                "id": target,
                "type": target_data.get("type", ""),
                "number": target_data.get("number", target_data.get("letter", "")),
                "title": target_data.get("title", ""),
                "text": target_data.get("text", "")[:500],
            })
    result["references"] = references

    # Definitions used (outgoing USES_TERM edges)
    definitions = []
    for _, target, d in G.out_edges(node_id, data=True):
        if d.get("type") == "USES_TERM":
            target_data = G.nodes[target]
            definitions.append({
                "term": target_data.get("term", ""),
                "defined_in": target_data.get("defined_in", ""),
            })
    result["definitions"] = definitions

    # Exceptions (incoming EXCEPTION edges to this section)
    exceptions = []
    for src, _, d in G.in_edges(node_id, data=True):
        if d.get("type") == "EXCEPTION":
            src_data = G.nodes[src]
            exceptions.append({
                "number": src_data.get("number", ""),
                "title": src_data.get("title", ""),
                "text": src_data.get("text", "")[:300],
            })
    result["exceptions"] = exceptions

    return result


def unresolved(G: nx.DiGraph, rule_id: str) -> dict[str, Any]:
    """Sections and exhibits a rule transitively depends on that nobody has read.

    This is the check that stops a rule being promoted on a sentence that defers
    its number elsewhere. Section 5.3.4.1 reads "The minimum isolation distances
    set forth in Exhibit C shall be maintained" and contains no distance at all,
    so a rule drawn from that sentence alone would ship a missing or invented
    threshold. Walking CITES and REFERENCES from the rule surfaces Exhibit C as a
    dependency whose content is not in the text layer, which is the signal to go
    and read the exhibit before promoting anything.

    A dependency counts as unread when its content was never extracted. A short
    section whose whole sentence sits in the heading line is treated as read, since
    flagging those would bury the real cases.
    """
    rule_node = f"rule:{rule_id}"
    if rule_node not in G:
        return {"error": f"rule {rule_id} not found in graph"}

    visited: set[str] = set()
    queue = [rule_node]
    unresolved_nodes: list[dict[str, Any]] = []

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        for _, target, d in G.out_edges(current, data=True):
            edge_type = d.get("type", "")
            if edge_type in ("CITES", "REFERENCES"):
                if target not in visited:
                    queue.append(target)
                    target_data = G.nodes[target]
                    if not _has_readable_content(target_data):
                        unresolved_nodes.append({
                            "id": target,
                            "type": target_data.get("type", ""),
                            "number": target_data.get(
                                "number", target_data.get("letter", "")
                            ),
                            "title": target_data.get("title", ""),
                            "reached_via": edge_type,
                        })

    return {
        "rule_id": rule_id,
        "unresolved_count": len(unresolved_nodes),
        "unresolved": unresolved_nodes,
    }


def orphans(G: nx.DiGraph) -> list[dict[str, Any]]:
    """Sections with obligation language that no rule cites.

    These are the coverage gaps: regulatory requirements that exist in the
    document but have no corresponding check in rules_7101.yaml.
    """
    # Find all sections cited by at least one rule
    cited_sections: set[str] = set()
    for node_id, attrs in G.nodes(data=True):
        if attrs.get("type") == "Rule":
            for _, target, d in G.out_edges(node_id, data=True):
                if d.get("type") == "CITES":
                    cited_sections.add(target)

    # Find sections with obligation language not cited by any rule
    orphan_list: list[dict[str, Any]] = []
    for node_id, attrs in G.nodes(data=True):
        if attrs.get("type") != "Section":
            continue
        if node_id in cited_sections:
            continue
        text = attrs.get("text", "")
        if OBLIGATION_RE.search(text):
            orphan_list.append({
                "section": attrs.get("number", ""),
                "title": attrs.get("title", ""),
                "page": attrs.get("page"),
                "text_preview": text[:150],
            })

    # Sort by section number
    def sort_key(item: dict) -> tuple:
        try:
            return (0,) + tuple(int(p) for p in item["section"].split("."))
        except ValueError:
            return (1, item["section"])

    return sorted(orphan_list, key=sort_key)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def graph_summary(G: nx.DiGraph) -> dict[str, Any]:
    """Node and edge counts by type."""
    from collections import Counter

    node_types = Counter(d.get("type", "unknown") for _, d in G.nodes(data=True))
    edge_types = Counter(d.get("type", "unknown") for _, _, d in G.edges(data=True))

    return {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "nodes_by_type": dict(node_types.most_common()),
        "edges_by_type": dict(edge_types.most_common()),
    }
