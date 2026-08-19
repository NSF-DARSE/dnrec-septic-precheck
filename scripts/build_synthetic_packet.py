"""Build the synthetic demonstration packet.

This produces a PDF named synthetic_demonstration_packet.pdf and seeds the
Textract cache with synthetic blocks carrying facts that violate two rules:

    ISO-001  disposal area to well distance is 60 feet, below the 100 foot minimum
    PERC-001 percolation rate is 140 minutes per inch, above the 120 maximum

Both are severity: return, unambiguous against the cited section, and produce
DEFICIENCIES FOUND. Other facts are set so that several rules pass normally and
several remain unreadable (because isolation distances on a site plan cannot be
read from this synthetic document either).

The PDF carries a prominent notice on every page stating that it is a constructed
demonstration packet and not a real permit application. That notice is also
injected into the report payload so both the console and the printable report
render it from the same source.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from septic import config
from septic.ingest.textract import document_hash

NOTICE = (
    "SYNTHETIC DEMONSTRATION PACKET. This is a constructed example built to "
    "show the DEFICIENCIES FOUND outcome. It is not a real permit application, "
    "it was never submitted to DNREC, and no applicant or property is associated "
    "with it. Every value in it was chosen to exercise the rules, not read from "
    "a real document."
)

PACKET_NAME = "synthetic_demonstration_packet.pdf"


def build_pdf() -> bytes:
    """Create a minimal PDF labelled as synthetic on every page.

    Uses fpdf2 if available, otherwise falls back to raw PDF bytes with the
    notice text embedded.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return _raw_pdf()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Page 1: cover
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 20, "SYNTHETIC DEMONSTRATION PACKET", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 7, NOTICE)
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Application Summary (constructed values)", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_font("Courier", "", 11)
    lines = [
        "Applicant:               Jane Doe (fictional)",
        "Property:                123 Example Lane, Dover, DE (fictional)",
        "Parcel:                  00-000.00-000 (fictional)",
        "System Type:             Full Depth Gravity",
        "Absorption Type:         Trench",
        "Construction Type:       New Construction",
        "Use Type:                Residential",
        "Number of Bedrooms:      4",
        "Design Flow:             480 gallons per day",
        "Avg. Percolation Rate:   140 MPI   ** EXCEEDS 120 MPI LIMIT **",
        "Percolation Test Holes:  3",
        "Disposal Area to Well:   60 feet   ** BELOW 100 FOOT MINIMUM **",
        "Site Evaluation Report:  SE-DEMO-0000 (present)",
        "Wells within 150 ft:     Shown on plan",
    ]
    for line in lines:
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(180, 0, 0)
    pdf.multi_cell(0, 5, (
        "This document exists solely to demonstrate the DEFICIENCIES FOUND "
        "verdict. It must not be mistaken for a real permit application."
    ))

    # Page 2: site plan placeholder
    pdf.add_page()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 15, "SITE PLAN (PLACEHOLDER)", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 7, (
        "A real packet would carry a scanned site plan here showing the "
        "disposal area, well locations, property lines, and dimensioned "
        "isolation distances. This synthetic packet carries no drawing because "
        "the violations are expressed as form field values, not measurements "
        "on a plan. The isolation distances that cannot be read from this "
        "packet are reported as UNKNOWN, which is the same outcome a real "
        "packet produces when its site plan cannot be measured by Textract."
    ))
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(180, 0, 0)
    pdf.multi_cell(0, 5, NOTICE)

    return pdf.output()


def _raw_pdf() -> bytes:
    """Fallback: a minimal PDF with the notice as literal text."""
    content = (
        "%PDF-1.4\n"
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        "/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    )
    text = f"BT /F1 14 Tf 72 700 Td (SYNTHETIC DEMONSTRATION PACKET) Tj ET\n"
    text += f"BT /F1 10 Tf 72 670 Td ({NOTICE[:80]}) Tj ET\n"
    stream = f"4 0 obj<</Length {len(text)}>>stream\n{text}endstream\nendobj\n"
    body = content + stream
    xref_offset = len(body)
    xref = (
        f"xref\n0 6\n"
        f"0000000000 65535 f \n"
        f"{'0000000009'} 00000 n \n"
        f"{'0000000058'} 00000 n \n"
        f"{'0000000115'} 00000 n \n"
        f"{'0000000300'} 00000 n \n"
        f"{'0000000250'} 00000 n \n"
        f"trailer<</Size 6/Root 1 0 R>>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    )
    return (body + xref).encode("latin-1")


def build_textract_blocks() -> list[dict]:
    """Synthetic Textract blocks with key-value pairs for the facts.

    These mimic what Textract returns for a form: KEY_VALUE_SET blocks with
    WORD children. The extractor reads them the same way it reads a real packet.
    """
    blocks = []
    block_id = 100

    fields = [
        ("Avg. Percolation Rate", "140 MPI"),
        ("Gallons Per Day Flow", "480"),
        ("# of Bedrooms", "4"),
        ("Site Evaluation Number", "SE-DEMO-0000"),
        ("Disposal Area to Well", "60 ft"),
        ("Perc Test Holes", "3"),
    ]

    # PAGE block
    blocks.append({
        "Id": "page1",
        "BlockType": "PAGE",
        "Geometry": {"BoundingBox": {"Left": 0, "Top": 0, "Width": 1, "Height": 1}},
        "Page": 1,
    })
    blocks.append({
        "Id": "page2",
        "BlockType": "PAGE",
        "Geometry": {"BoundingBox": {"Left": 0, "Top": 0, "Width": 1, "Height": 1}},
        "Page": 2,
    })

    for key_text, value_text in fields:
        key_id = f"k{block_id}"
        value_id = f"v{block_id}"
        key_word_id = f"kw{block_id}"
        value_word_id = f"vw{block_id}"
        geometry = {
            "BoundingBox": {
                "Left": 0.1,
                "Top": 0.1 + block_id / 1000,
                "Width": 0.3,
                "Height": 0.02,
            }
        }

        blocks.append({
            "Id": key_id,
            "BlockType": "KEY_VALUE_SET",
            "EntityTypes": ["KEY"],
            "Confidence": 99.0,
            "Page": 1,
            "Geometry": geometry,
            "Relationships": [
                {"Type": "CHILD", "Ids": [key_word_id]},
                {"Type": "VALUE", "Ids": [value_id]},
            ],
        })
        blocks.append({
            "Id": key_word_id,
            "BlockType": "WORD",
            "Text": key_text,
            "Confidence": 99.0,
            "Page": 1,
            "Geometry": geometry,
        })
        blocks.append({
            "Id": value_id,
            "BlockType": "KEY_VALUE_SET",
            "EntityTypes": ["VALUE"],
            "Confidence": 99.0,
            "Page": 1,
            "Geometry": geometry,
            "Relationships": [{"Type": "CHILD", "Ids": [value_word_id]}],
        })
        blocks.append({
            "Id": value_word_id,
            "BlockType": "WORD",
            "Text": value_text,
            "Confidence": 99.0,
            "Page": 1,
            "Geometry": geometry,
        })
        block_id += 1

    # Checkbox blocks for system type and construction type
    checkbox_options = [
        ("Gravity (FD)", True, 1),
        ("Gravity (CF)", False, 1),
        ("Pressure Dose (CF)", False, 1),
        ("Elevated Sand Mound", False, 1),
        ("New Construction", True, 1),
        ("Replacement", False, 1),
        ("Trench", True, 1),
        ("Bed", False, 1),
    ]

    for label, selected, page in checkbox_options:
        geometry = {
            "BoundingBox": {
                "Left": 0.5,
                "Top": 0.1 + block_id / 1000,
                "Width": 0.3,
                "Height": 0.02,
            }
        }
        blocks.append({
            "Id": f"ck{block_id}",
            "BlockType": "KEY_VALUE_SET",
            "EntityTypes": ["KEY"],
            "Confidence": 95.0,
            "Page": page,
            "Geometry": geometry,
            "Relationships": [
                {"Type": "CHILD", "Ids": [f"ckw{block_id}"]},
                {"Type": "VALUE", "Ids": [f"cv{block_id}"]},
            ],
        })
        blocks.append({
            "Id": f"ckw{block_id}",
            "BlockType": "WORD",
            "Text": label,
            "Confidence": 95.0,
            "Page": page,
            "Geometry": geometry,
        })
        blocks.append({
            "Id": f"cv{block_id}",
            "BlockType": "KEY_VALUE_SET",
            "EntityTypes": ["VALUE"],
            "Confidence": 95.0,
            "Page": page,
            "Geometry": geometry,
            "Relationships": [{"Type": "CHILD", "Ids": [f"cs{block_id}"]}],
        })
        blocks.append({
            "Id": f"cs{block_id}",
            "BlockType": "SELECTION_ELEMENT",
            "SelectionStatus": "SELECTED" if selected else "NOT_SELECTED",
            "Confidence": 95.0,
            "Page": page,
            "Geometry": geometry,
        })
        block_id += 1

    # LINE blocks for text patterns (residential use type)
    line_texts = [
        "SYNTHETIC DEMONSTRATION PACKET",
        "single family dwelling",
        "residential use",
        "wells within 150 feet shown on plan",
    ]
    for text in line_texts:
        blocks.append({
            "Id": f"l{block_id}",
            "BlockType": "LINE",
            "Text": text,
            "Confidence": 99.0,
            "Page": 1,
            "Geometry": {
                "BoundingBox": {
                    "Left": 0.1,
                    "Top": 0.1 + block_id / 1000,
                    "Width": 0.6,
                    "Height": 0.02,
                }
            },
        })
        block_id += 1

    return blocks


def main():
    config.ensure_dirs()
    examples_dir = config.OUT_DIR / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)

    # Build the PDF
    pdf_bytes = build_pdf()
    pdf_path = examples_dir / PACKET_NAME
    pdf_path.write_bytes(pdf_bytes)
    print(f"Written {pdf_path} ({len(pdf_bytes)} bytes)")

    # Compute the document hash
    doc_hash = document_hash(pdf_bytes)
    print(f"Document hash: {doc_hash}")

    # Build the Textract cache entry
    blocks = build_textract_blocks()
    cache_dir = config.CACHE_DIR / "textract"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"sha256-{doc_hash}.json"
    payload = {
        "s3_key": f"synthetic/{PACKET_NAME}",
        "job_id": None,
        "status": "SUCCEEDED",
        "pages": 2,
        "blocks": blocks,
        "document_hash": doc_hash,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Written cache {cache_path}")

    # Verify the chain runs
    from septic.ingest import layout
    from septic.ingest.extract import extract_facts
    from septic.rules import engine

    document = layout.parse_blocks(blocks)
    extraction = extract_facts(document)
    report = engine.evaluate(extraction.facts)

    print(f"\nVerdict: {report.verdict.value}")
    print(f"Coverage: {report.coverage()['text']}")
    print(f"Failures: {len(report.failures)}")
    for f in report.failures:
        print(f"  {f.rule.id}: {f.reason}")
    print(f"Passes: {len(report.passes)}")
    for p in report.passes:
        if p.compared_a_value:
            print(f"  {p.rule.id}: {p.reason} (compared)")
        elif p.is_not_applicable:
            print(f"  {p.rule.id}: {p.reason} (not applicable)")
    print(f"Unknown: {len(report.unknowns)}")
    for u in report.unknowns:
        print(f"  {u.rule.id}: {u.reason}")

    return report


if __name__ == "__main__":
    main()
