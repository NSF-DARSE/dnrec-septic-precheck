"""Finding numeric thresholds in the regulation.

This module locates candidate requirements. It does not create rules. Output is
a report of every passage that states a number with a unit that could become a
check, each with its section, page, and the sentence quoted verbatim so a human
can confirm it against the PDF.

Nothing here writes to rules_7101.yaml. That step is manual on purpose. The
extractor cannot tell a binding minimum from an example in a table or a figure
caption, and a threshold copied without that judgement would be wrong in a way
that is hard to notice.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

# The PDF was produced with a legacy encoding, so smart punctuation arrives as
# mojibake. Left uncorrected it corrupts the verbatim quotes.
MOJIBAKE = {
    "\u00f4": '"', "\u00f6": '"', "\u00f5": "'", "\u00d5": "'",
    "\u00c6": "-", "\u00d0": "-", "\u00b7": "-", "\u2013": "-", "\u2014": "-",
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
}

# Running header repeated on every page. Dropping it keeps quotes readable.
HEADER_FRAGMENTS = (
    "Delaware Department of Natural Resources and Environmental Control",
    "Division of Water",
    "Groundwater Discharges Section",
    "Del.C. Ch. 60",
)

SECTION_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3}){0,4})\s+(?=[A-Z(\"'])")
INLINE_SECTION_RE = re.compile(r"\b(\d{1,2}(?:\.\d{1,3}){1,4})\b")

# A number that could be a threshold. Excludes bare section numbers by requiring
# either a unit nearby or a decimal/comma grouped magnitude.
NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b")

# Unit families. Order matters: longer phrases are tested first.
UNIT_PATTERNS: list[tuple[str, str]] = [
    ("percolation", r"minutes?\s+per\s+inch|min(?:ute)?s?/inch|\bmpi\b|percolation\s+rate"),
    ("area", r"square\s+feet|square\s+foot|sq\.?\s*ft\.?|\bacres?\b|\bsf\b"),
    ("volume", r"gallons?\s+per\s+day|\bgpd\b|gallons?|\bcubic\s+(?:feet|yards?)\b"),
    ("slope", r"percent\s+slope|\bslope\b|\bpercent\b|%"),
    ("distance", r"\bfeet\b|\bfoot\b|\bft\.?\b|\binches\b|\binch\b|\bin\.\b|\bmiles?\b"),
    ("depth", r"\bdepth\b|\bdeep\b|\bthick(?:ness)?\b"),
    ("concentration", r"\bmg/l\b|\bmilligrams?\s+per\s+liter\b|\bppm\b"),
    ("time", r"\bdays?\b|\byears?\b|\bhours?\b|\bmonths?\b"),
]
UNIT_RES = [(name, re.compile(pattern, re.I)) for name, pattern in UNIT_PATTERNS]

# Words that signal a binding requirement rather than a description.
OBLIGATION_RE = re.compile(
    r"\b(shall|must|may\s+not|shall\s+not|is\s+required|are\s+required|"
    r"minimum|maximum|no\s+less\s+than|no\s+more\s+than|at\s+least|"
    r"not\s+exceed|greater\s+than|less\s+than|prohibited)\b",
    re.I,
)

# Setback language, called out separately because setbacks are the most common
# reason a site plan is rejected.
SETBACK_RE = re.compile(
    r"\b(setback|separation|isolation\s+distance|horizontal\s+distance|"
    r"vertical\s+separation|buffer|property\s+line|well|water\s+body|"
    r"surface\s+water|dwelling|foundation)\b",
    re.I,
)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"'])")


def clean(text: str) -> str:
    for bad, good in MOJIBAKE.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFKC", text)
    return text


def strip_header(line: str) -> bool:
    return any(fragment in line for fragment in HEADER_FRAGMENTS)


@dataclass
class Candidate:
    """One passage that states a number which might be a requirement."""

    section: str
    page: int
    quote: str
    units: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    obligation: bool = False
    setback: bool = False

    @property
    def score(self) -> int:
        """Rough ordering hint. Obligation plus setback language ranks highest."""
        return (2 if self.obligation else 0) + (1 if self.setback else 0)

    def to_json(self) -> dict:
        return {
            "section": self.section,
            "page": self.page,
            "quote": self.quote,
            "units": self.units,
            "numbers": self.numbers,
            "obligation": self.obligation,
            "setback": self.setback,
        }


def _units_in(sentence: str) -> list[str]:
    found = []
    for name, pattern in UNIT_RES:
        if pattern.search(sentence):
            found.append(name)
    return found


def _numbers_in(sentence: str) -> list[str]:
    # Drop tokens that are part of a section reference such as "3.31".
    numbers = []
    for match in NUMBER_RE.finditer(sentence):
        token = match.group(0)
        start, end = match.span()
        before = sentence[max(0, start - 1):start]
        after = sentence[end:end + 1]
        if before == "." or after == ".":
            if re.match(r"^\d", after or "") or before == ".":
                continue
        numbers.append(token)
    return numbers


def _sentences(block: str) -> list[str]:
    parts = SENTENCE_SPLIT_RE.split(block)
    return [" ".join(p.split()) for p in parts if p and p.strip()]


def _dehyphenate(text: str) -> str:
    """Rejoin words split across a line break."""
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", text)


def _extract_text_pypdfium2(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract text from each page using pypdfium2.

    Returns a list of (page_number, text) tuples with 1-based page numbers.
    PDFium resolves embedded fonts correctly where pdfminer emits (cid:NNN)
    markers, and produces clean text without needing a scrubber. Line endings
    are normalized from CRLF to LF.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    pages = []
    for i in range(len(doc)):
        raw = doc[i].get_textpage().get_text_range()
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        pages.append((i + 1, text))
    return pages


def extract(pdf_path: Path | None = None,
            require_units: bool = True) -> list[Candidate]:
    """Scan the regulation and return every numeric threshold passage.

    Lines are grouped into blocks by section before sentences are split, because
    the PDF wraps sentences across lines and a quote cut at the wrap is useless
    for verification.

    require_units keeps the output to sentences that state a unit, since a bare
    number is almost always a cross reference.
    """
    pdf_path = Path(pdf_path or config.REGULATION_PDF)
    if not pdf_path.exists():
        raise FileNotFoundError(f"regulation PDF not found at {pdf_path}")

    candidates: list[Candidate] = []
    current_section = "front matter"

    pages = _extract_text_pypdfium2(pdf_path)

    for page_index, raw in pages:
        if not raw.strip():
            continue

        lines = [
            clean(line)
            for line in raw.splitlines()
            if line.strip() and not strip_header(clean(line))
        ]

        # Group consecutive lines under the section heading they follow.
        blocks: list[tuple[str, list[str]]] = []
        for line in lines:
            heading = SECTION_RE.match(line)
            if heading:
                current_section = heading.group(1)
                blocks.append((current_section, [line]))
            elif blocks and blocks[-1][0] == current_section:
                blocks[-1][1].append(line)
            else:
                blocks.append((current_section, [line]))

        for section, block_lines in blocks:
            block = _dehyphenate(" ".join(block_lines))
            for sentence in _sentences(block):
                units = _units_in(sentence)
                numbers = _numbers_in(sentence)
                if not numbers:
                    continue
                if require_units and not units:
                    continue

                candidates.append(
                    Candidate(
                        section=section,
                        page=page_index,
                        quote=sentence,
                        units=units,
                        numbers=numbers[:24],
                        obligation=bool(OBLIGATION_RE.search(sentence)),
                        setback=bool(SETBACK_RE.search(sentence)),
                    )
                )

    return candidates


def section_sort_key(section: str) -> tuple:
    try:
        return (0,) + tuple(int(p) for p in section.split("."))
    except ValueError:
        return (1, section)


def counts_by_section(candidates: list[Candidate]) -> list[tuple[str, int]]:
    counter = Counter(c.section for c in candidates)
    return sorted(counter.items(), key=lambda kv: section_sort_key(kv[0]))


def counts_by_unit(candidates: list[Candidate]) -> list[tuple[str, int]]:
    counter: Counter = Counter()
    for c in candidates:
        for unit in c.units:
            counter[unit] += 1
    return counter.most_common()


def render_markdown(candidates: list[Candidate], pdf_path: Path) -> str:
    by_section: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_section.setdefault(c.section, []).append(c)

    obligations = sum(1 for c in candidates if c.obligation)
    setbacks = sum(1 for c in candidates if c.setback)

    lines: list[str] = [
        "# Rule candidates",
        "",
        "Every passage in the regulation that states a number with a unit.",
        "Generated by scripts/extract_rule_candidates.py. Do not edit by hand.",
        "",
        f"Source: `{pdf_path.name}`",
        "",
        "These are candidates, not rules. No value here has been checked by a",
        "person, and none of it belongs in rules_7101.yaml until it has been. The",
        "extractor cannot tell a binding minimum from a number in an example, a",
        "table header, or a figure caption, so promoting a value without reading",
        "the cited page is how a wrong regulatory number reaches a reviewer.",
        "",
        "## Totals",
        "",
        f"- candidate passages: {len(candidates)}",
        f"- sections represented: {len(by_section)}",
        f"- passages using obligation language (shall, must, minimum): {obligations}",
        f"- passages mentioning setback or separation: {setbacks}",
        "",
        "### By unit family",
        "",
        "| unit family | passages |",
        "| --- | --- |",
    ]
    for unit, count in counts_by_unit(candidates):
        lines.append(f"| {unit} | {count} |")

    lines += [
        "",
        "### By section",
        "",
        "| section | passages |",
        "| --- | --- |",
    ]
    for section, count in counts_by_section(candidates):
        lines.append(f"| {section} | {count} |")

    lines += ["", "## Passages", ""]

    for section in sorted(by_section, key=section_sort_key):
        entries = sorted(by_section[section], key=lambda c: (-c.score, c.page))
        lines.append(f"### Section {section}")
        lines.append("")
        for c in entries:
            flags = []
            if c.obligation:
                flags.append("obligation")
            if c.setback:
                flags.append("setback")
            flag_text = f" [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"- p.{c.page} units={','.join(c.units) or 'none'} "
                f"numbers={','.join(c.numbers)}{flag_text}"
            )
            lines.append(f"  > {c.quote}")
        lines.append("")

    return "\n".join(lines)
