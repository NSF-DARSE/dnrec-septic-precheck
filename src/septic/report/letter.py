"""Draft correction letter, rendered from the composed payload.

This produces a plain text letter a reviewer can paste into their own template.
It is a draft for a person to edit and sign, never a determination, and it says
so on its face. No model decides anything here; every line is a rendering of data
that already exists in the composed payload.

What the letter does:
    - Itemises each deficiency with the value found, the requirement, and the
      section and page
    - States the checks that could not be read, grouped by cause, so the
      applicant is asked once for the missing value rather than repeatedly for
      each rule it blocks
    - Never asserts compliance or implies the rest of the application is
      satisfactory

What the letter never does:
    - Approve, deny, or determine anything
    - Invent letterhead, addresses, reference numbers, or signature lines
    - Use the word "approved", "compliant", "satisfactory", or "acceptable"
"""
from __future__ import annotations

from .wording import parameter_name, requirement_sentence

DRAFT_HEADER = (
    "DRAFT CORRECTION LETTER\n"
    "This is a draft for the reviewer to edit and sign. It is not a "
    "determination and it does not represent DNREC policy. The tool flagged "
    "deficiencies and cited the regulation for each one. The reviewer decides "
    "what to send.\n"
)

NEVER_COMPLIANCE = (
    "This letter does not address the remainder of the application. Checks "
    "that could not be evaluated are listed below so the applicant knows what "
    "additional information is needed."
)


def render_letter(composed) -> str:
    """Render a plain text draft correction letter from the composed payload.

    Takes a composed payload dict (from Composed.to_json()) and returns a plain
    text string suitable for pasting into a reviewer's own template.
    """
    c = composed if isinstance(composed, dict) else composed.to_json()

    headline = c.get("headline", "")
    if headline != "DEFICIENCIES FOUND":
        return ""

    lines: list[str] = []
    add = lines.append

    # Notices (synthetic packet label)
    for notice in c.get("notices") or []:
        add(notice)
        add("")

    add(DRAFT_HEADER)
    add("-" * 72)
    add("")

    # Subject
    subject = c.get("subject") or {}
    if subject.get("document"):
        add(f"Re: {subject['document']}")
        add("")

    add("The following deficiencies were identified in this application.")
    add("")

    # Coverage context
    coverage = c.get("coverage") or {}
    coverage_text = coverage.get("text", "")
    if coverage_text:
        add(f"Scope: {coverage_text}.")
        add("")

    add(NEVER_COMPLIANCE)
    add("")
    add("-" * 72)
    add("DEFICIENCIES")
    add("-" * 72)
    add("")

    # Itemised deficiencies
    deficiencies = c.get("deficiencies") or []
    for i, f in enumerate(deficiencies, 1):
        sentence = requirement_sentence(f)
        observed = f.get("observed")
        threshold = f.get("threshold")
        units = f.get("units") or ""
        section = f.get("section", "")
        page = f.get("page")
        citation = f"{section}, page {page}" if page else section

        add(f"{i}. {sentence}")
        add("")
        if observed is not None:
            add(f"   Value found: {observed} {units}".strip())
            if threshold is not None:
                add(f"   Required:    {f.get('reason', '')}")
        add(f"   Citation:    {citation}")

        quote = f.get("quote")
        if quote:
            # Wrap the quote at a reasonable width
            wrapped = _wrap_indent(quote, indent="   ", width=72)
            add(f"   Regulation:  \"{wrapped}\"")

        remedy = f.get("remedy")
        if remedy:
            add(f"   To correct:  {_wrap_indent(remedy, indent='                ', width=72)}")

        add("")

    # Checks that could not be read, grouped by cause
    groups = c.get("unresolved_groups") or []
    if groups:
        add("-" * 72)
        add("ADDITIONAL INFORMATION NEEDED")
        add("-" * 72)
        add("")
        add(
            "The following values could not be read from the application as "
            "submitted. Please provide or clarify:"
        )
        add("")

        for group in groups:
            description = group.get("description", group.get("blocked_by", ""))
            location = group.get("location", "")
            count = group.get("count", 0)
            findings = group.get("findings", [])

            add(f"  {description}")
            if location:
                add(f"    Normally found: {location}")
            if count > 1:
                add(f"    Needed by {count} checks:")
                for f in findings:
                    citation = f.get("citation", "")
                    add(f"      - {requirement_sentence(f)} ({citation})")
            else:
                f = findings[0] if findings else {}
                citation = f.get("citation", "")
                add(f"    Needed by: {requirement_sentence(f)} ({citation})")
            add("")

    add("-" * 72)
    add("")
    add(
        "This letter was drafted from an automated first pass over the "
        "application. Every finding cites the section and page of the Delaware "
        "Regulations Governing On-Site Wastewater Treatment and Disposal Systems "
        "(January 11, 2014) that it comes from. The reviewer has read and edited "
        "this draft before sending it."
    )

    return "\n".join(lines)


def _wrap_indent(text: str, indent: str = "", width: int = 72) -> str:
    """Wrap text to width, with indent on continuation lines only."""
    words = text.split()
    result_lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) + len(indent) > width and current:
            result_lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        result_lines.append(current)
    if not result_lines:
        return ""
    return result_lines[0] + "".join(
        f"\n{indent}{line}" for line in result_lines[1:]
    )
