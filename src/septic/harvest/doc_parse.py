"""Structural field extraction from DNREC document URLs.

Document URLs encode fields with <I>/<R>/<G> markers in the path tail. Decoded,
a URL looks like this:

    /<docId>/<hash>/<I>  - Permit  </I><R> WR - RESIDENTIAL WASTEWATER </R> 281442 <G>SEPTIC PERMITTING ACTIVITIES ON 13-014.00-039 </G> -FOIA: RELEASABLE

    <I> ... </I>                   document type
    <R> ... </R>                   program
    bare text between </R> and <G> permit number
    <G> ... </G>                   description, ending with the tax parcel id
    text after </G>                FOIA status

Reading the markers as delimiters rather than stripping them is what makes the
document type exact. The earlier keyword match put 84 percent of documents in
an "Other" bucket because the two most common type names never appeared in the
keyword table.
"""
from __future__ import annotations

import html
import re
import urllib.parse as up

# Parcel id at the end of the <G> field. Three format families occur:
#   13-014.00-039              short numeric
#   3-31-01.00-0012.06         medium numeric, four digit lot
#   KH-00-053.00-01-35.00.000  two letter hundred code prefix
PARCEL_RE = re.compile(
    r"(?P<parcel>"
    r"[A-Z]{2}-\d{2}-\d{3}\.\d{2}-\d{2}-\d{2}\.\d{2}\.\d{3}"
    r"|"
    r"\d{1,2}-\d{2,3}(?:-\d{2})?\.[\d]+(?:[.\-][\d]+)*"
    r")\s*$"
)

I_RE = re.compile(r"<I>(.*?)</I>", re.S)
R_RE = re.compile(r"<R>(.*?)</R>", re.S)
G_RE = re.compile(r"<G>(.*?)</G>", re.S)
FOIA_RE = re.compile(r"</G>\s*(.*?)\s*$", re.S)
BETWEEN_R_G = re.compile(r"</R>(.*?)<G>", re.S)

FIELDS = (
    "doc_type",
    "program",
    "permit_number",
    "description",
    "parcel_id",
    "foia",
    "title_raw",
)


def parse_doc_url(url: str) -> dict:
    """Pull the encoded fields out of a document URL.

    Returns every key in FIELDS, using None for anything absent. Never raises,
    because a single malformed URL must not abort a harvest run.
    """
    result: dict[str, str | None] = {k: None for k in FIELDS}

    try:
        decoded = html.unescape(url)
        path = up.unquote(up.urlsplit(decoded).path)

        # Closing markers contain a literal slash, so the tail has to be
        # rejoined after the docId and hash rather than split naively.
        segs = [s for s in path.split("/") if s]
        if len(segs) < 3:
            result["title_raw"] = path
            return result

        tail = "/".join(segs[2:])
        result["title_raw"] = tail

        m = I_RE.search(tail)
        if m:
            doc_type = m.group(1).strip().strip("-").strip()
            if doc_type:
                result["doc_type"] = doc_type

        m = R_RE.search(tail)
        if m:
            program = m.group(1).strip()
            # A lone dash is the placeholder for "no program recorded".
            if program and program.strip("- ") != "":
                result["program"] = program

        m = BETWEEN_R_G.search(tail)
        if m:
            permit_number = m.group(1).strip()
            if permit_number:
                result["permit_number"] = permit_number

        m = G_RE.search(tail)
        if m:
            description = m.group(1).strip()
            if description:
                result["description"] = description
            parcel = PARCEL_RE.search(description)
            if parcel:
                result["parcel_id"] = parcel.group("parcel")

        m = FOIA_RE.search(tail)
        if m:
            foia = m.group(1).strip().lstrip("-").strip()
            if foia:
                result["foia"] = foia

    except Exception:
        return result

    return result
