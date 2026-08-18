# harvest

Fetching permits and documents from the DNREC permit portal into S3.

Per permit: fetch the detail page, parse the Documents grid, download each PDF,
verify the signature, upload to S3, and emit one manifest record joining the CSV
permit fields to the documents.

Start here: `cli.py` orchestrates the harvest. `detail.py` fetches a single
permit's detail page and `doc_parse.py` extracts document URLs from it. `s3sink.py`
handles the upload and `audit.py` checks what has already been fetched.
`csv_index.py` reads the permit CSV export.
