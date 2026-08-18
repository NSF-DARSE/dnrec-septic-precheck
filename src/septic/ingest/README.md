# ingest

Reading scanned permit documents into structured text and coordinates.

The pipeline is: Textract OCR, then block parsing into a document model, then
field extraction to produce the facts the rule engine needs.

Start here: `textract.py` wraps the Textract client and the on-disk cache keyed
by document SHA256. `layout.py` turns Textract blocks into a structured document
of pages, form fields and tables. `extract.py` reads specific values (design flow,
percolation rate, system type, distances) out of the structured document.
