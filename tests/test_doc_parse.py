"""Tests for structural field extraction from document URLs.

URLs are taken verbatim from the harvested manifest, percent encoded exactly as
they appear in the page source.
"""
from septic.harvest.doc_parse import parse_doc_url

HOST = "https://docs.dnrec.delaware.gov"
HASH = "742890c586cbbce37f98468073ba8684710519a10bbe1fc561af22202d3bfe81366bac6faed498dabf75731d165de6d7"

PERMIT = (
    f"{HOST}/3002302/{HASH}/"
    "<I>%20%20-%20Permit%20</I><R>%20WR%20-%20RESIDENTIAL%20WASTEWATER%20</R>"
    "%20281442%20<G>SEPTIC%20PERMITTING%20ACTIVITIES%20ON%2013-014.00-039%20</G>"
    "%20-FOIA:%20RELEASABLE"
)

PERMIT_REPORT = (
    f"{HOST}/3036260/{HASH}/"
    "<I>%20%20-%20Permit%20Report%20-%20</I><R>%20WR%20-%20RESIDENTIAL%20WASTEWATER%20</R>"
    "%20244371%20<G>FACILITY%20ON%20SM-00-138.00-01-33.01.000%20</G>"
    "%20-FOIA:%20RELEASABLE"
)

SUPPORTING = (
    f"{HOST}/2518263/{HASH}/"
    "<I>%20%20-%20Permit%20Supporting%20Document%20-%20</I>"
    "<R>%20WR%20-%20SMALL%20(RESIDENTIAL)%20SYSTEMS%20</R>"
    "%20247415%20<G>SEPTIC%20PERMITTING%20ACTIVITIES%20ON%20SM-00-129.00-02-36.02.000%20</G>"
    "%20-FOIA:%20RELEASABLE"
)

CHP = (
    f"{HOST}/1690429/{HASH}/"
    "<I>%20%20-%20Permit%20Report%20-%20</I>"
    "<R>%20WR%20-%20SMALL%20(RESIDENTIAL)%20SYSTEMS%20</R>"
    "%20CHP-17391%20<G>SEPTIC%20PERMITTING%20ACTIVITIES%20ON%2015-021.00-172%20</G>"
    "%20-FOIA:%20RELEASABLE"
)

DASH_PROGRAM = (
    f"{HOST}/9999999/{HASH}/"
    "<I>%20%20-%20Permit%20Report%20-%20</I><R>%20%20-%20%20</R>"
    "%20CHP-23036%20<G>FACILITY%20ON%20KH-00-018.00-02-14.00.000%20</G>"
    "%20-FOIA:%20RELEASABLE"
)

PARCEL_MEDIUM = (
    f"{HOST}/2918721/{HASH}/"
    "<I>%20%20-%20Permit%20</I><R>%20WR%20-%20RESIDENTIAL%20WASTEWATER%20</R>"
    "%20280297%20<G>SEPTIC%20PERMITTING%20ACTIVITIES%20ON%203-31-01.00-0012.06%20</G>"
    "%20-FOIA:%20RELEASABLE"
)

PARCEL_LONG = (
    f"{HOST}/2937545/{HASH}/"
    "<I>%20%20-%20Permit%20</I><R>%20WR%20-%20RESIDENTIAL%20WASTEWATER%20</R>"
    "%20280569%20<G>SEPTIC%20PERMITTING%20ACTIVITIES%20ON%20KH-00-053.00-01-35.00.000%20</G>"
    "%20-FOIA:%20RELEASABLE"
)


def test_permit():
    r = parse_doc_url(PERMIT)
    assert r["doc_type"] == "Permit"
    assert r["program"] == "WR - RESIDENTIAL WASTEWATER"
    assert r["permit_number"] == "281442"
    assert r["description"] == "SEPTIC PERMITTING ACTIVITIES ON 13-014.00-039"
    assert r["parcel_id"] == "13-014.00-039"
    assert r["foia"] == "FOIA: RELEASABLE"


def test_permit_report():
    r = parse_doc_url(PERMIT_REPORT)
    assert r["doc_type"] == "Permit Report"
    assert r["permit_number"] == "244371"
    assert r["parcel_id"] == "SM-00-138.00-01-33.01.000"


def test_permit_supporting_document():
    r = parse_doc_url(SUPPORTING)
    assert r["doc_type"] == "Permit Supporting Document"
    assert r["program"] == "WR - SMALL (RESIDENTIAL) SYSTEMS"
    assert r["parcel_id"] == "SM-00-129.00-02-36.02.000"


def test_non_numeric_permit_number():
    r = parse_doc_url(CHP)
    assert r["permit_number"] == "CHP-17391"
    assert r["parcel_id"] == "15-021.00-172"


def test_placeholder_program_is_none():
    r = parse_doc_url(DASH_PROGRAM)
    assert r["doc_type"] == "Permit Report"
    assert r["program"] is None
    assert r["parcel_id"] == "KH-00-018.00-02-14.00.000"


def test_parcel_format_short():
    assert parse_doc_url(PERMIT)["parcel_id"] == "13-014.00-039"


def test_parcel_format_medium():
    assert parse_doc_url(PARCEL_MEDIUM)["parcel_id"] == "3-31-01.00-0012.06"


def test_parcel_format_long():
    assert parse_doc_url(PARCEL_LONG)["parcel_id"] == "KH-00-053.00-01-35.00.000"


def test_missing_fields_are_none():
    r = parse_doc_url(f"{HOST}/123/abc/plain-title.pdf")
    assert r["doc_type"] is None
    assert r["parcel_id"] is None
    assert r["title_raw"] == "plain-title.pdf"


def test_never_raises():
    for bad in ["", "not a url", f"{HOST}/1/2/<I></I><R></R><G></G>", None, 123]:
        result = parse_doc_url(bad)
        assert isinstance(result, dict)
        assert set(result) == {
            "doc_type", "program", "permit_number", "description",
            "parcel_id", "foia", "title_raw",
        }
