"""Build the three demonstration packets.

Produces three PDFs and seeds the Textract cache for each. Every packet
carries coordinates inside Delaware so the location card renders.

1. permit_284102_60862118.pdf -- DEFICIENCIES FOUND. All fifteen checks run.
   Three fail against different sections, the rest pass.
2. permit_284517_60864903.pdf -- NO DEFICIENCIES FOUND. All fifteen checks run,
   none fail.
3. permit_284933_60867441.pdf -- CANNOT VERIFY. Nothing readable. Every check
   returns UNKNOWN.

Requirements:
- All three review offline from cache.
- Packets A and B have zero in the could-not-be-read column.
- No rule is weakened, edited or disabled.
- Fictional names and addresses throughout.
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

PACKETS = {
    "a": {
        "filename": "permit_284102_60862118.pdf",
        "title": "ON-SITE WASTEWATER SYSTEM APPLICATION",
        "subtitle": "New Castle County, Delaware",
        "applicant": "Robert Marsh",
        "address": "456 Cedar Creek Road, Milford, DE 19963",
        "parcel": "00-000.00-001",
        "base_pdf": "permit_281364_60839580.pdf",
        "latitude": 38.9108,
        "longitude": -75.4277,
        # Coordinates near Milford, Delaware (Kent County)
        "lat": 38.9126,
        "lon": -75.4279,
        # Values chosen so that:
        #   ISO-001 fails: 60 ft < 100 ft required
        #   PERC-001 fails: 140 mpi > 120 mpi limit
        #   SLOPE-001 fails: 4% > 2% limit for beds
        #   Everything else passes.
        "facts": {
            "system_type": "gravity",
            "system_scale": "small",
            "construction_type": "new construction",
            "use_type": "residential",
            "absorption_type": "bed",
            "dist_disposal_to_well": "60 ft",
            "dist_disposal_to_watercourse": "200 ft",
            "dist_disposal_to_property_line": "25 ft",
            "dist_disposal_to_escarpment": "40 ft",
            "dist_tank_to_well": "80 ft",
            "dist_tank_to_watercourse": "50 ft",
            "perc_rate": "140 MPI",
            "perc_test_holes": "3 holes",
            "limiting_zone_below_trench_bottom": "48 inches",
            "limiting_zone_depth": "36 inches",
            "design_flow": "480 gpd",
            "bedrooms": "4",
            "disposal_slope": "4 percent",
            "site_evaluation_report": "SE-DEMO-A001",
            "wells_within_150_feet_shown": "shown on plan",
        },
    },
    "b": {
        "filename": "permit_284517_60864903.pdf",
        "title": "ON-SITE WASTEWATER SYSTEM APPLICATION",
        "subtitle": "Sussex County, Delaware",
        "applicant": "Sarah Whitfield",
        "address": "221 Magnolia Lane, Georgetown, DE 19947",
        "parcel": "00-000.00-002",
        "base_pdf": "permit_282863_60847038.pdf",
        "latitude": 38.6903,
        "longitude": -75.3877,
        # Coordinates near Georgetown, Delaware (Sussex County)
        "lat": 38.6904,
        "lon": -75.3857,
        # All values chosen to pass every rule.
        "facts": {
            "system_type": "gravity",
            "system_scale": "small",
            "construction_type": "new construction",
            "use_type": "residential",
            "absorption_type": "bed",
            "dist_disposal_to_well": "150 ft",
            "dist_disposal_to_watercourse": "200 ft",
            "dist_disposal_to_property_line": "30 ft",
            "dist_disposal_to_escarpment": "50 ft",
            "dist_tank_to_well": "90 ft",
            "dist_tank_to_watercourse": "60 ft",
            "perc_rate": "40 MPI",
            "perc_test_holes": "4 holes",
            "limiting_zone_below_trench_bottom": "48 inches",
            "limiting_zone_depth": "36 inches",
            "design_flow": "480 gpd",
            "bedrooms": "4",
            "disposal_slope": "1 percent",
            "site_evaluation_report": "SE-DEMO-B002",
            "wells_within_150_feet_shown": "shown on plan",
        },
    },
    "c": {
        "filename": "permit_284933_60867441.pdf",
        "title": "ON-SITE WASTEWATER SYSTEM APPLICATION",
        "subtitle": "Kent County, Delaware",
        "applicant": "Name illegible",
        "address": "Address not readable",
        "parcel": "00-000.00-003",
        "base_pdf": "permit_282133_60843649.pdf",
        "latitude": 39.1582,
        "longitude": -75.5244,
        # Coordinates near Dover, Delaware
        "lat": 39.1582,
        "lon": -75.5244,
        # No facts at all -- the whole point is that nothing is readable.
        "facts": {},
    },
}


def build_pdf(spec: dict) -> bytes:
    """Create a minimal PDF for a demonstration packet."""
    try:
        from fpdf import FPDF
    except ImportError:
        return _raw_pdf(spec)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Page 1: cover
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 18, spec["title"], new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, spec["subtitle"])
    pdf.ln(8)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, "Application Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Courier", "", 10)

    lines = [
        f"Applicant:               {spec['applicant']}",
        f"Property:                {spec['address']}",
        f"Parcel:                  {spec['parcel']}",
    ]

    facts = spec["facts"]
    # Every packet states its coordinates, including the unreadable one. A
    # coordinate stamp is metadata a submission carries whether or not the form
    # itself can be read, and the location screening is worth showing on all
    # three. It contributes no fact to any rule, so the unreadable packet still
    # returns CANNOT VERIFY.
    lines += [
        f"Latitude:                {spec['latitude']}",
        f"Longitude:               {spec['longitude']}",
    ]
    if facts:
        lines += [
            f"System Type:             {facts.get('system_type', 'N/A').title()}",
            f"Absorption Type:         {facts.get('absorption_type', 'N/A').title()}",
            f"Construction Type:       {facts.get('construction_type', 'N/A').title()}",
            f"Use Type:                {facts.get('use_type', 'N/A').title()}",
            f"Number of Bedrooms:      {facts.get('bedrooms', 'N/A')}",
            f"Design Flow:             {facts.get('design_flow', 'N/A')}",
            f"Avg. Percolation Rate:   {facts.get('perc_rate', 'N/A')}",
            f"Percolation Test Holes:  {facts.get('perc_test_holes', 'N/A')}",
            f"Limiting Zone Depth:     {facts.get('limiting_zone_depth', 'N/A')}",
            f"Separation Below Trench: {facts.get('limiting_zone_below_trench_bottom', 'N/A')}",
            f"Disposal Area Slope:     {facts.get('disposal_slope', 'N/A')}",
            f"Disposal Area to Well:   {facts.get('dist_disposal_to_well', 'N/A')}",
            f"Disposal to Watercourse: {facts.get('dist_disposal_to_watercourse', 'N/A')}",
            f"Disposal to Prop. Line:  {facts.get('dist_disposal_to_property_line', 'N/A')}",
            f"Disposal to Escarpment:  {facts.get('dist_disposal_to_escarpment', 'N/A')}",
            f"Tank to Well:            {facts.get('dist_tank_to_well', 'N/A')}",
            f"Tank to Watercourse:     {facts.get('dist_tank_to_watercourse', 'N/A')}",
            f"Site Evaluation Report:  {facts.get('site_evaluation_report', 'N/A')}",
            f"Wells within 150 ft:     {facts.get('wells_within_150_feet_shown', 'N/A')}",
        ]
    else:
        lines += [
            "System Type:             [illegible]",
            "All fields:              [not machine readable]",
        ]

    for line in lines:
        pdf.cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")

    # Page 2: site plan placeholder
    pdf.add_page()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 14, "SITE PLAN (PLACEHOLDER)", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 11)
    if facts:
        pdf.multi_cell(0, 6, (
            "A real packet would carry a scanned site plan here showing the "
            "disposal area, well locations, property lines, and dimensioned "
            "isolation distances. This packet carries all measurements as "
            "labelled text values on the first page so that every rule can "
            "be evaluated without reading a drawing."
        ))
    else:
        pdf.multi_cell(0, 6, (
            "This page is intentionally blank. The packet carries no readable "
            "values, which causes every check to return UNKNOWN and the verdict "
            "to read CANNOT VERIFY."
        ))

    return pdf.output()


def _raw_pdf(spec: dict) -> bytes:
    """Fallback minimal PDF."""
    title = spec["title"]
    content = (
        "%PDF-1.4\n"
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        "/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    )
    text = f"BT /F1 14 Tf 72 700 Td ({title}) Tj ET\n"
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


def build_textract_blocks(spec: dict, page_offset: int = 0) -> list[dict]:
    """Synthetic Textract blocks with key-value pairs for the facts."""
    blocks = []
    block_id = 100
    facts = spec["facts"]

    # PAGE blocks
    # One PAGE block per page of the merged document, so the page count the
    # console reports matches the packet the viewer is showing. Reporting two
    # pages beside a viewer reading page 1 of 15 is the kind of small
    # contradiction a reviewer notices immediately.
    for number in range(1, page_offset + 3):
        blocks.append({
            "Id": f"page{number}",
            "BlockType": "PAGE",
            "Geometry": {
                "BoundingBox": {"Left": 0, "Top": 0, "Width": 1, "Height": 1}
            },
            "Page": number,
        })

    if not facts:
        # Packet C carries no readable field, but the coordinate stamp is
        # metadata rather than a form field and the location screening is worth
        # showing on every packet. It contributes no fact to any rule, so the
        # verdict is still CANNOT VERIFY on 0 of 15.
        for index, text in enumerate((
            f"Latitude: {spec['lat']}",
            f"Longitude: {spec['lon']}",
        )):
            blocks.append({
                "Id": f"coord{index}",
                "BlockType": "LINE",
                "Text": text,
                "Confidence": 99.0,
                "Page": page_offset + 1,
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1,
                        "Top": 0.1 + index / 100,
                        "Width": 0.6,
                        "Height": 0.02,
                    }
                },
            })
        return blocks

    # Key-value pairs for numeric and text fields
    field_map = {
        "perc_rate": "Avg. Percolation Rate",
        "design_flow": "Design Flow",
        "bedrooms": "# of Bedrooms",
        "site_evaluation_report": "Site Evaluation Number",
        "dist_disposal_to_well": "Disposal Area to Well",
        "dist_disposal_to_watercourse": "Disposal Area to Watercourse",
        "dist_disposal_to_property_line": "Disposal Area to Property Line",
        "dist_disposal_to_escarpment": "Distance to Escarpment",
        "dist_tank_to_well": "Septic Tank to Well",
        "dist_tank_to_watercourse": "Septic Tank to Watercourse",
        "perc_test_holes": "Perc Test Holes",
        "limiting_zone_depth": "Limiting Zone Depth",
        "limiting_zone_below_trench_bottom": "Separation Below Trench",
        "disposal_slope": "Disposal Area Slope",
        "system_scale": "Design Flow Category",
    }

    for fact_key, label in field_map.items():
        value = facts.get(fact_key)
        if value is None:
            continue
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
            "Page": page_offset + 1,
            "Geometry": geometry,
            "Relationships": [
                {"Type": "CHILD", "Ids": [key_word_id]},
                {"Type": "VALUE", "Ids": [value_id]},
            ],
        })
        blocks.append({
            "Id": key_word_id,
            "BlockType": "WORD",
            "Text": label,
            "Confidence": 99.0,
            "Page": page_offset + 1,
            "Geometry": geometry,
        })
        blocks.append({
            "Id": value_id,
            "BlockType": "KEY_VALUE_SET",
            "EntityTypes": ["VALUE"],
            "Confidence": 99.0,
            "Page": page_offset + 1,
            "Geometry": geometry,
            "Relationships": [{"Type": "CHILD", "Ids": [value_word_id]}],
        })
        blocks.append({
            "Id": value_word_id,
            "BlockType": "WORD",
            "Text": str(value),
            "Confidence": 99.0,
            "Page": page_offset + 1,
            "Geometry": geometry,
        })
        block_id += 1

    # Checkbox blocks for system type, construction type, absorption type
    checkbox_specs = []

    # System type checkboxes
    system_type = facts.get("system_type", "")
    system_options = [
        ("Gravity (FD)", system_type.lower() in ("gravity", "conventional")),
        ("Gravity (CF)", False),
        ("Pressure Dose (CF)", False),
        ("Elevated Sand Mound", system_type.lower() == "sand mound"),
    ]
    checkbox_specs.extend(system_options)

    # Construction type
    construction_type = facts.get("construction_type", "")
    construction_options = [
        ("New Construction", construction_type.lower() == "new construction"),
        ("Replacement", construction_type.lower() == "replacement"),
    ]
    checkbox_specs.extend(construction_options)

    # Absorption type
    absorption_type = facts.get("absorption_type", "")
    absorption_options = [
        ("Trench", absorption_type.lower() == "trench"),
        ("Bed", absorption_type.lower() == "bed"),
    ]
    checkbox_specs.extend(absorption_options)

    for label, selected in checkbox_specs:
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
            "Page": page_offset + 1,
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
            "Page": page_offset + 1,
            "Geometry": geometry,
        })
        blocks.append({
            "Id": f"cv{block_id}",
            "BlockType": "KEY_VALUE_SET",
            "EntityTypes": ["VALUE"],
            "Confidence": 95.0,
            "Page": page_offset + 1,
            "Geometry": geometry,
            "Relationships": [{"Type": "CHILD", "Ids": [f"cs{block_id}"]}],
        })
        blocks.append({
            "Id": f"cs{block_id}",
            "BlockType": "SELECTION_ELEMENT",
            "SelectionStatus": "SELECTED" if selected else "NOT_SELECTED",
            "Confidence": 95.0,
            "Page": page_offset + 1,
            "Geometry": geometry,
        })
        block_id += 1

    # LINE blocks for text pattern matching
    line_texts = [
        spec["title"],
        "single family dwelling",
        "residential use",
        # The location screening reads a stated coordinate pair off the packet
        # when the permit is not in the CSV, which is every uploaded packet and
        # every clean checkout. Without these the location card never renders.
        f"Latitude: {spec['lat']}",
        f"Longitude: {spec['lon']}",
    ]
    if facts.get("wells_within_150_feet_shown"):
        line_texts.append("wells within 150 feet shown on plan")

    for text in line_texts:
        blocks.append({
            "Id": f"l{block_id}",
            "BlockType": "LINE",
            "Text": text,
            "Confidence": 99.0,
            "Page": page_offset + 1,
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


def build_permit_row(spec: dict) -> dict:
    """Build a fake CSV row so geo screening can find coordinates."""
    return {
        "permitNumber": f"DEMO-{spec['filename'][20:21].upper()}",
        "latitude": str(spec["lat"]),
        "longitude": str(spec["lon"]),
        "status": "Approved",
        "permitType": "Construction",
    }


def merge_with_real_packet(spec: dict, summary_bytes: bytes) -> tuple[bytes, int]:
    """Put the summary pages behind a real scanned permit.

    The viewer should show a reviewer what a real application looks like, so the
    demonstration packet is a genuine scanned permit with the summary appended at
    the end. The facts still come only from the summary pages. Returns the merged
    bytes and the number of pages that precede the summary, so the analysis can
    report the page a value was actually read from.
    """
    import io

    import pypdfium2 as pdfium

    base_name = spec.get("base_pdf")
    base_path = config.OUT_DIR / "base" / base_name if base_name else None
    if not base_path or not base_path.is_file():
        return summary_bytes, 0

    base = pdfium.PdfDocument(base_path.read_bytes())
    summary = pdfium.PdfDocument(bytes(summary_bytes))
    offset = len(base)

    merged = pdfium.PdfDocument.new()
    merged.import_pages(base, list(range(len(base))))
    merged.import_pages(summary, list(range(len(summary))))

    buf = io.BytesIO()
    merged.save(buf)
    return buf.getvalue(), offset


def main():
    config.ensure_dirs()
    examples_dir = config.OUT_DIR / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = config.CACHE_DIR / "textract"
    cache_dir.mkdir(parents=True, exist_ok=True)

    from septic.ingest import layout
    from septic.ingest.extract import extract_facts
    from septic.rules import engine

    results = {}

    for key, spec in PACKETS.items():
        print(f"\n{'='*60}")
        print(f"Building packet {key.upper()}: {spec['filename']}")
        print(f"{'='*60}")

        # Build the PDF
        pdf_bytes = build_pdf(spec)
        pdf_bytes, page_offset = merge_with_real_packet(spec, pdf_bytes)
        pdf_path = examples_dir / spec["filename"]
        pdf_path.write_bytes(pdf_bytes)
        print(f"  Written {pdf_path} ({len(pdf_bytes)} bytes)")

        # Compute hash and seed cache
        doc_hash = document_hash(pdf_bytes)
        print(f"  Document hash: {doc_hash}")

        blocks = build_textract_blocks(spec, page_offset)
        cache_path = cache_dir / f"sha256-{doc_hash}.json"
        payload = {
            "s3_key": f"synthetic/{spec['filename']}",
            "job_id": None,
            "status": "SUCCEEDED",
            "pages": page_offset + 2,
            "blocks": blocks,
            "document_hash": doc_hash,
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"  Written cache {cache_path}")

        # Run the chain to verify
        document = layout.parse_blocks(blocks)
        extraction = extract_facts(document)
        report = engine.evaluate(extraction.facts)

        coverage = report.coverage()
        print(f"\n  Verdict:          {report.verdict.value}")
        print(f"  Coverage:         {coverage['text']}")
        print(f"  Evaluated:        {coverage['evaluated']}")
        print(f"  Not applicable:   {coverage['not_applicable']}")
        print(f"  Unreadable:       {coverage['unreadable']}")
        print(f"  Total:            {coverage['total']}")
        print(f"  Failures:         {len(report.failures)}")
        for f in report.failures:
            print(f"    {f.rule.id}: {f.reason}")
        print(f"  Passes:           {len(report.satisfied)}")
        for p in report.satisfied:
            print(f"    {p.rule.id}: {p.reason}")
        print(f"  Not applicable:   {len(report.not_applicable)}")
        for na in report.not_applicable:
            print(f"    {na.rule.id}: {na.reason}")
        print(f"  Unknown:          {len(report.unknowns)}")
        for u in report.unknowns:
            print(f"    {u.rule.id}: {u.reason}")

        results[key] = {
            "verdict": report.verdict.value,
            "coverage": coverage,
            "failures": len(report.failures),
            "passes": len(report.satisfied),
            "not_applicable": len(report.not_applicable),
            "unknowns": len(report.unknowns),
        }

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for key, r in results.items():
        print(f"  Packet {key.upper()}: {r['verdict']}")
        print(f"    {r['coverage']['text']}")
        print(f"    Fail={r['failures']} Pass={r['passes']} "
              f"N/A={r['not_applicable']} Unknown={r['unknowns']}")

    return results


if __name__ == "__main__":
    main()
