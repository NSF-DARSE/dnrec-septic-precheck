# scripts

Runnable wrappers. No business logic lives here; each script calls into the
library under `src/septic/`.

Start here:

- `verify_rule_quotes.py` checks every citation in the rule set against the
  regulation PDF.
- `rule_discrimination.py` tests which rules separate denied from approved permits.
- `validate_geo.py` exercises coordinate parsing and distance computation.
- `make_figures.py` produces the maps and comparison figures.
- `coverage_report.py` regenerates `docs/coverage.md`.
- `build_index.py` builds the local permit similarity index.
- `fetch_gis.py` downloads the Delaware FirstMap hydrography layers into `data/gis`.
- `prepare_examples.py` stages cached example packets into `out/examples/`.
- `capture_evidence.py` saves AWS output for offline demonstration.
- `survey_packets.py` surveys the harvested packets for field availability.
- `build_assets.py` and `build_theme.py` generate the console styling assets.

`_bootstrap.py` puts `src` on `sys.path` so scripts work without installing the
package.
