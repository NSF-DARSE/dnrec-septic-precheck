# tests

The test suite. Run with pytest:

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

No network and no AWS credentials are needed. Tests that exercise Textract or
Bedrock paths mock the client and read from the on-disk cache or from fixtures in
`testdata/`.

Start here:

- `test_rules_engine.py` covers rule evaluation, the three outcomes, applicability
  exclusions and the coverage split.
- `test_review_pipeline.py` runs the full review chain end to end against cached
  packets.
- `test_console.py` asserts console layout, the uploader, the verdict banner and
  the embedded report.
- `test_graph.py` covers the regulation graph parser and section lookups.
- `test_geo.py` exercises coordinate parsing, projection and distance screening.
- `conftest.py` sets up the path so imports resolve without installation.
