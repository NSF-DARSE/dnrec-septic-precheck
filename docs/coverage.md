# Coverage

How much of the regulation this tool checks, and how much it does not.
Counts only.

Source: Delaware Regulations Governing On-Site Wastewater Treatment and
Disposal Systems, January 11, 2014, 245 pages. Counts are produced by
`scripts/coverage_report.py` from the parsed regulation graph at
`out/reg_graph.json` and the rule set at
`src/septic/rules/rules_7101.yaml`.

## The regulation

| | count |
| --- | --- |
| Numbered sections parsed | 2102 |
| Sections carrying obligation language | 1000 |
| Exhibits | 40 |
| Exhibits with a readable text layer | 20 |

Obligation language means the section text contains shall, must, may not,
is required, minimum, maximum, no less than, no more than, at least, not
exceed, or prohibited.

## Rules

| | count |
| --- | --- |
| Rules in the rule set | 15 |
| Verified by a person | 0 |
| Staged, awaiting verification | 15 |
| Sections cited by at least one rule | 8 |
| Exhibits cited by at least one rule | 1 |
| Obligation sections cited by at least one rule | 7 |
| Obligation sections cited by no rule | 993 |

A rule that is not verified is not evaluated. The engine returns UNKNOWN
for it and the verdict for any application is CANNOT VERIFY.

## Uncited obligation sections by topic area

Topic areas a residential reviewer touches. A section is counted under a
topic when its text matches that topic, and a section can match more than
one, so these do not sum to the total and are not a partition.

| topic | obligation sections | cited by a rule | not cited |
| --- | --- | --- | --- |
| isolation distances | 46 | 1 | 45 |
| depth to water table | 28 | 1 | 27 |
| percolation | 37 | 2 | 35 |
| sizing | 47 | 1 | 46 |
| siting | 54 | 2 | 52 |

Obligation sections matching at least one topic: 173 of 1000.
Obligation sections matching no topic above: 827.

## The rule set as it stands

| rule | requirement | citation | verified |
| --- | --- | --- | --- |
| `ISO-001-disposal-area-to-well` | >= 100 feet | Exhibit C p.173 | False |
| `ISO-002-disposal-area-to-watercourse` | >= 100 feet | Exhibit C p.173 | False |
| `ISO-003-disposal-area-to-property-line` | >= 10 feet | Exhibit C p.173 | False |
| `ISO-004-disposal-area-to-escarpment` | >= 15 feet | Exhibit C p.173 | False |
| `ISO-005-septic-tank-to-well` | >= 50 feet | Exhibit C p.173 | False |
| `ISO-006-septic-tank-to-watercourse` | >= 25 feet | Exhibit C p.173 | False |
| `PERC-001-site-maximum-percolation-rate` | <= 120 minutes per inch | 5.2.4.2.5.7 p.52 | False |
| `PERC-002-percolation-test-hole-count` | >= 3 holes | 5.2.4.2.2 p.51 | False |
| `SEP-001-limiting-zone-below-trench-bottom` | >= 36 inches | 5.3.12.1.3 p.61 | False |
| `SEP-002-conventional-limiting-zone-minimum-depth` | >= 20 inches | 5.2.4.2.4.2 p.51 | False |
| `FLOW-001-residential-minimum-design-flow` | >= 240 gallons per day | 5.3.3.3 p.56 | False |
| `FLOW-002-residential-flow-per-bedroom` | >= 120 gallons per day per bedroom | 5.3.3.3 p.56 | False |
| `SLOPE-001-gravity-bed-maximum-slope` | <= 2 percent | 5.3.12.1.2 p.60 | False |
| `SITE-001-site-evaluation-report-present` | presence check | 5.2.1.1 p.43 | False |
| `SITE-002-wells-within-150-feet-shown` | presence check | 5.2.1.5 p.44 | False |

## Parameters the rules require

| parameter | rules using it | available from |
| --- | --- | --- |
| `design_flow` | 1 | permit CSV |
| `design_flow_per_bedroom` | 1 | permit CSV |
| `disposal_slope` | 1 | packet, via Textract |
| `dist_disposal_to_escarpment` | 1 | packet, via Textract |
| `dist_disposal_to_property_line` | 1 | packet, via Textract |
| `dist_disposal_to_watercourse` | 1 | packet, via Textract |
| `dist_disposal_to_well` | 1 | packet, via Textract |
| `dist_tank_to_watercourse` | 1 | packet, via Textract |
| `dist_tank_to_well` | 1 | packet, via Textract |
| `limiting_zone_below_trench_bottom` | 1 | packet, via Textract |
| `limiting_zone_depth` | 1 | packet, via Textract |
| `perc_rate` | 1 | permit CSV |
| `perc_test_holes` | 1 | packet, via Textract |
| `site_evaluation_report` | 1 | packet, via Textract |
| `wells_within_150_feet_shown` | 1 | packet, via Textract |

