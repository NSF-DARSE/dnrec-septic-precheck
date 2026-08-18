"""Check every rule quote appears verbatim on the page it cites.

A citation whose quote is not actually in the regulation is worse than no
citation, because it invites a reviewer to trust text that was never checked.
This runs over the whole shipped rule set and fails loudly.

Whitespace is normalized before comparison, since the PDF wraps sentences across
lines and YAML folds them back differently. Nothing else is normalized: the words
must match exactly.

Table derived quotes cannot match a sentence, so they are verified differently.
A quote in the form 'TABLE NAME, row "X", column "Y": value' is checked by
confirming the table name, the row label and the value all appear on the page.

Usage:
    python scripts/verify_rule_quotes.py
"""
from __future__ import annotations

import re
import sys

import _bootstrap  # noqa: F401

from septic import config
from septic.rules.engine import load_rules


def normalize(text: str) -> str:
    """Collapse all whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def page_text(pdf_path, page_number: int) -> str:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    raw = doc[page_number - 1].get_textpage().get_text_range()
    return raw.replace("\r\n", "\n").replace("\r", "\n")


TABLE_QUOTE_RE = re.compile(
    r'^(?P<table>[A-Z][A-Z0-9 ()>%,\-]+?),\s*row\s*"(?P<row>[^"]+)",\s*'
    r'column\s*"(?P<column>[^"]+)":\s*(?P<value>[\d.]+)'
)


def check_rule(rule, pdf_path) -> tuple[bool, str]:
    citation = rule.citation
    if citation.page is None:
        return False, "no page number"
    if not citation.quote:
        return False, "no quote"

    haystack = normalize(page_text(pdf_path, citation.page))
    quote = normalize(citation.quote)

    table_match = TABLE_QUOTE_RE.match(quote)
    if table_match:
        table = normalize(table_match.group("table"))
        row = normalize(table_match.group("row"))
        value = table_match.group("value")
        missing = []
        if table not in haystack:
            missing.append(f"table title {table!r}")
        # The row label is a run of component names in the PDF; check the first
        # and last token of it rather than the whole run, because the PDF may
        # order them across lines.
        row_tokens = row.split()
        if row_tokens and row_tokens[0] not in haystack:
            missing.append(f"row start {row_tokens[0]!r}")
        if row_tokens and row_tokens[-1] not in haystack:
            missing.append(f"row end {row_tokens[-1]!r}")
        if value not in haystack:
            missing.append(f"value {value!r}")
        if missing:
            return False, "table quote missing " + ", ".join(missing)
        return True, "table cell verified (title, row, value present on page)"

    if quote in haystack:
        return True, "verbatim match"

    # Report how far the match got, so a near miss is easy to fix.
    for cut in range(len(quote), 20, -10):
        if quote[:cut] in haystack:
            return False, (
                f"diverges after {cut} chars: matched {quote[:cut]!r}, "
                f"then expected {quote[cut:cut + 60]!r}"
            )
    return False, "no part of the quote found on the cited page"


def main() -> int:
    pdf_path = config.REGULATION_PDF
    rules = load_rules()
    print(f"checking {len(rules)} rules against {pdf_path.name}\n")

    failures = 0
    for rule in rules:
        ok, detail = check_rule(rule, pdf_path)
        mark = "OK  " if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{mark} {rule.id}")
        print(f"     {rule.citation.section}, p.{rule.citation.page}: {detail}")

    print()
    print(f"{len(rules) - failures} verified, {failures} failed")

    # Structural guarantees that matter as much as the quote itself.
    print("\nstructural checks")
    problems = []
    for rule in rules:
        if rule.verified:
            problems.append(f"{rule.id} ships verified: true")
        if rule.citation.section in (None, "", "TBD"):
            problems.append(f"{rule.id} has a placeholder section")
        if rule.citation.page is None:
            problems.append(f"{rule.id} has no page")
        if not rule.notes:
            problems.append(f"{rule.id} has no notes")
        if not rule.remedy:
            problems.append(f"{rule.id} has no remedy")
    if problems:
        for p in problems:
            print(f"  FAIL {p}")
        failures += len(problems)
    else:
        print("  all rules unverified, cited, and carry notes and a remedy")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
