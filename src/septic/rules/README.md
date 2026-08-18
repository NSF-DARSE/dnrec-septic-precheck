# rules

Rule schema, engine, rule set and regulation graph.

The verdict is computed here and only here. Given the same facts and the same rule
set the engine returns the same verdict every time. Three outcomes per rule: PASS,
FAIL or UNKNOWN. UNKNOWN covers an unverified threshold, a fact the extractor could
not read, and a value that will not parse as a number.

Start here: `rules_7101.yaml` defines all 15 requirements with their thresholds,
citations and regulation quotes. `schema.py` is the data model for a rule and its
evaluation. `engine.py` loads the rule set and evaluates it against a set of facts.
`graph.py` parses the regulation PDF into a section graph used for cross references
and context. `candidates.py` scans the regulation for obligation language to find
sections a rule might cover.
