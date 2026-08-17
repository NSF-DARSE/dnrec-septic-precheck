"""Tests for Textract block parsing and permit year selection."""
from septic.harvest.csv_index import parse_year, permit_year
from septic.ingest.layout import parse_blocks


def word(block_id: str, text: str, page: int = 1):
    return {
        "Id": block_id,
        "BlockType": "WORD",
        "Text": text,
        "Page": page,
        "Confidence": 99.0,
        "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.1, "Height": 0.02}},
    }


def kv(block_id: str, entity: str, child_ids: list[str], value_id: str | None = None):
    block = {
        "Id": block_id,
        "BlockType": "KEY_VALUE_SET",
        "EntityTypes": [entity],
        "Page": 1,
        "Confidence": 90.0,
        "Geometry": {"BoundingBox": {"Left": 0.2, "Top": 0.3, "Width": 0.2, "Height": 0.03}},
        "Relationships": [{"Type": "CHILD", "Ids": child_ids}],
    }
    if value_id:
        block["Relationships"].append({"Type": "VALUE", "Ids": [value_id]})
    return block


class TestLayout:
    def test_lines_and_pages(self):
        blocks = [
            {
                "Id": "l1",
                "BlockType": "LINE",
                "Text": "PERCOLATION RATE",
                "Page": 1,
                "Confidence": 98.0,
                "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.02}},
            },
            {
                "Id": "l2",
                "BlockType": "LINE",
                "Text": "30 minutes per inch",
                "Page": 2,
                "Confidence": 97.0,
                "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.3, "Height": 0.02}},
            },
        ]
        doc = parse_blocks(blocks)
        assert doc.pages == 2
        assert len(doc.lines) == 2
        assert "PERCOLATION RATE" in doc.text(page=1)
        assert "PERCOLATION RATE" not in doc.text(page=2)

    def test_form_field_key_value_pairing(self):
        blocks = [
            word("w1", "Lot"),
            word("w2", "Area"),
            word("w3", "20000"),
            kv("k1", "KEY", ["w1", "w2"], value_id="v1"),
            kv("v1", "VALUE", ["w3"]),
        ]
        doc = parse_blocks(blocks)
        assert len(doc.fields) == 1
        field = doc.fields[0]
        assert field.key == "Lot Area"
        assert field.value == "20000"
        assert field.key_box is not None
        assert doc.field_map()["lot area"] == "20000"

    def test_selected_checkbox_becomes_a_value(self):
        blocks = [
            word("w1", "Gravity"),
            {
                "Id": "s1",
                "BlockType": "SELECTION_ELEMENT",
                "SelectionStatus": "SELECTED",
                "Page": 1,
                "Confidence": 95.0,
                "Geometry": {"BoundingBox": {"Left": 0.5, "Top": 0.5, "Width": 0.01, "Height": 0.01}},
            },
            kv("k1", "KEY", ["w1"], value_id="v1"),
            kv("v1", "VALUE", ["s1"]),
        ]
        doc = parse_blocks(blocks)
        assert doc.fields[0].value == "[X]"

    def test_table_grid(self):
        cells = []
        for row in (1, 2):
            for col in (1, 2):
                wid = f"w{row}{col}"
                cells.append(word(wid, f"r{row}c{col}"))
                cells.append(
                    {
                        "Id": f"c{row}{col}",
                        "BlockType": "CELL",
                        "RowIndex": row,
                        "ColumnIndex": col,
                        "Page": 1,
                        "Confidence": 90.0,
                        "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.1, "Height": 0.1}},
                        "Relationships": [{"Type": "CHILD", "Ids": [wid]}],
                    }
                )
        table = {
            "Id": "t1",
            "BlockType": "TABLE",
            "Page": 1,
            "Confidence": 90.0,
            "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.5, "Height": 0.5}},
            "Relationships": [
                {"Type": "CHILD", "Ids": [f"c{r}{c}" for r in (1, 2) for c in (1, 2)]}
            ],
        }
        doc = parse_blocks(cells + [table])
        assert doc.tables[0].rows == [["r1c1", "r1c2"], ["r2c1", "r2c2"]]

    def test_empty_input_is_safe(self):
        doc = parse_blocks([])
        assert doc.pages == 0
        assert doc.text() == ""


class TestYearParsing:
    def test_reads_year_from_us_date(self):
        assert parse_year("05/22/2026") == 2026

    def test_rejects_missing(self):
        assert parse_year(None) is None
        assert parse_year(float("nan")) is None

    def test_rejects_implausible_year(self):
        assert parse_year("12/31/9999") is None

    def test_falls_back_across_date_columns(self):
        row = {"AppReceivedDate": None, "ApprovedDate": "01/02/2015"}
        assert permit_year(row) == 2015

    def test_returns_none_when_no_column_parses(self):
        assert permit_year({"AppReceivedDate": None, "ApprovedDate": None}) is None
