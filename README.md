# dnrec-septic-precheck

[![tests](https://github.com/NSF-DARSE/dnrec-septic-precheck/actions/workflows/ci.yml/badge.svg)](https://github.com/NSF-DARSE/dnrec-septic-precheck/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11-3776AB)](https://www.python.org/downloads/release/python-3110/)
[![AWS](https://img.shields.io/badge/AWS-S3%20%7C%20Textract%20%7C%20Bedrock-232F3E)](docs/evidence/preflight.txt)
[![status](https://img.shields.io/badge/status-prototype-orange)](docs/coverage.md)

A first pass over a septic permit application that flags deficiencies and puts the
regulation citation next to each one.

## The problem

A DNREC reviewer gets a packet and checks it by hand against a 245 page
regulation. Most packets come back for correction, and each correction means
reading the whole packet again from the start. Because the checking is manual, two
reviewers can read the same requirement differently. The slow part is not the
decision, it is finding the handful of things that are wrong.

## What it does

It reads the packet, checks it against the regulation, and hands back a worklist
with the citation already attached to every line.

- **Reads the submission.** Amazon Textract over the scanned PDF, forms and
  tables, cached on disk under the SHA256 of the document so the same packet is
  never sent twice and a review of a cached packet needs no network at all.
- **Checks 15 requirements** taken from the 2014 regulation and confirmed against
  the pages they cite. The rule set is a YAML file a person can read, and the
  engine that evaluates it is the only thing that produces a finding.
- **Cites everything.** Each finding carries the section, the page and the quoted
  sentence it comes from, so the requirement can be checked against the
  regulation rather than taken on trust.
- **Says what it could not read.** A value missing from the packet returns
  UNKNOWN, never a failure, and the report counts those separately from the
  checks that ran and the ones that did not apply to this kind of system.
- **Screens the location.** Distance from the geocoded point to mapped surface
  water, against Delaware FirstMap hydrography, drawn on a map at the top of the
  report. It is a prompt to check the site plan, not a measurement of the
  disposal area.
- **Drafts the correction letter.** Where requirements are not met, it writes the
  itemised letter, each item with its value found, its threshold and its
  citation, ready to be edited and signed.
- **Puts it in a console.** `streamlit run app.py` gives an upload box, the
  findings beside the packet they came from, and the full rule set with its
  regulation text behind one toggle.

## What it does not do

It does not approve or deny, and it does not predict what DNREC will decide.
Rules out of the regulation produce every finding; no model determines, alters or
suppresses one. Where a rule has not been confirmed against the page it cites,
the tool returns no answer for it rather than a guess.

## The pipeline

![Pipeline](docs/pipeline.svg)

<details>
<summary>Diagram source</summary>

The image above is rendered from this definition and committed, so the figure
does not depend on a diagram renderer running in the reader's browser.

```mermaid
flowchart TB
    portal["DNREC permit portal"]
    csv["Permit CSV export"]
    harvest["harvest"]
    s3[("S3 bucket")]
    textract["Amazon Textract<br/>FORMS and TABLES"]
    cache[("Textract cache<br/>keyed by SHA256")]
    layout["block parser"]
    extract["field extractor"]
    pdf[("Regulation PDF<br/>2014, 245 pages")]
    reggraph[("Regulation graph")]
    yaml[("rules_7101.yaml<br/>15 requirements")]
    engine["rule engine"]
    embed["Bedrock Titan<br/>embeddings only"]
    index[("Permit index")]
    compose["report composer"]
    wording["Bedrock text<br/>wording only"]
    report["Reviewer report"]

    portal --> harvest
    csv --> harvest
    harvest --> s3
    s3 --> textract
    textract --> cache
    cache --> layout
    layout --> extract
    pdf --> reggraph
    extract --> engine
    yaml --> engine
    engine --> compose
    reggraph --> compose
    csv --> embed
    embed --> index
    index --> compose
    compose --> wording
    compose --> report

    linkStyle 8 stroke-width:2px
    linkStyle 9 stroke-width:2px
    linkStyle 10 stroke-width:2px
    style engine fill:#1b4332,color:#fff
    style yaml fill:#1b4332,color:#fff
    style embed fill:#6b7280,color:#fff
    style wording fill:#6b7280,color:#fff
    style index fill:#6b7280,color:#fff
```

</details>

The green boxes decide. Everything grey is context or wording and cannot change a
finding. No model is ever asked whether an application complies.

Every stage above runs today. One correction: the vector index is a JSON file
scored with a dot product, not FAISS, because 1460 permits fit in memory many times
over. Not built, and not shown, is the Permit Events grid, which is where a return
is actually recorded.

## The three outcomes

Every review reports the verdict alongside a coverage figure that says how much
of the rule set actually reached a decision: how many checks ran, how many did not
apply to this kind of system, and how many could not be read. The verdict is only
readable next to that number.

**NO DEFICIENCIES FOUND.** Nothing was flagged among the checks that ran. This is
not an approval, and it is not a statement about the checks that did not run.

**DEFICIENCIES FOUND.** One or more requirements are not met, each itemised with
the section and page it comes from. A draft correction letter is offered for the
reviewer to edit and sign. It is a draft, not a determination, and says so on its
face. The tool does not decide what to send.

**CANNOT VERIFY.** No check reached a decision. Either every value the rules need
could not be read off the packet, or no rule has been confirmed by a person.

## Why CANNOT VERIFY exists

Showing a reviewer a wrong setback distance is worse than showing nothing, because
a wrong number that looks official gets passed to an applicant. So a rule counts
only once a person has read the cited page and confirmed it. All 15 rules are now
confirmed, but the interlock stays active: any rule added later starts unverified
and the engine returns UNKNOWN for it until someone repeats the check.

## Data provenance

Everything the tool reads comes from a public Delaware or federal source. Nothing
was bought, scraped from behind a login, or generated.

| What | Where it comes from |
|------|---------------------|
| Permit records | [Permitted Septic Systems, dataset `mv7j-tx3u`](https://data.delaware.gov/d/mv7j-tx3u) on the Delaware Open Data Portal |
| Permit detail pages | [DNREC Environmental Navigator](https://den.dnrec.delaware.gov/Detail/PermitDetail.aspx) |
| Permit documents | `docs.dnrec.delaware.gov`, linked from each detail page |
| Regulation | Delaware On-Site Septic System Regulations with Exhibits, January 11, 2014, tracked at `docs/regulations/` |
| Hydrography | [Delaware FirstMap](https://enterprise.firstmap.delaware.gov/arcgis/rest/services), Hydrology/DE_Water |
| Aerial imagery | [USGS National Map](https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer), public domain |

**Permit CSV.** Exported 2026-08-17. 117,802 rows, 112,643 unique detail pages,
112,613 permits. 45 MB, gitignored, so it is fetched from the portal rather than
tracked here.

**Regulation PDF.** 245 pages, tracked in this repository because every threshold
has to be traceable to it. `docs/regulations/SOURCE.md` records the published
title, the statutory authority, and how the file arrived.

**Harvesting was polite.** One quarter of a second between requests, an
identifying user agent, and only pages the portal already serves to the public.
`src/septic/config.py` holds both settings.

**Scope and harvest.** 2014 onward, because earlier permits fall under superseded
law and a finding against them would cite a rule that no longer applies. 1226
approved permits from 2014 onward, of which only 218 carry any document. The other
1008 expose nothing but their CSV row. Separately, 234 denied and returned permits
across all years.

**Refusals are rare and undocumented.** 147 permits were ever denied and 87
returned. From 2014 onward that is 101 denied and 3 returned. The letters saying
why an application was returned are not published anywhere. That is why the rules
come out of the regulation rather than from past decisions: the decisions do not
record their reasons.

## Layout

```
docs/                 handoff notes, decisions, the rule review checklist
docs/coverage.md      how much of the regulation is checked, in counts
docs/evidence/        captured AWS output, so the demo does not need credentials
docs/regulations/     the 2014 regulation PDF, source of every threshold
data/gis/             Delaware FirstMap hydrography, downloaded once
src/septic/harvest/   fetching permits and documents into S3
src/septic/ingest/    Textract, block parsing, field extraction
src/septic/rules/     rule schema, engine, rule set, regulation graph
src/septic/retrieval/ embeddings and the local permit index
src/septic/report/    report composition and rendering
src/septic/chatbot/   the reviewer chatbot, optional, outside the pipeline
src/septic/geo.py     coordinate parsing, projection, distance screening
src/septic/maps.py    the figures
app.py                the reviewer console
scripts/              runnable wrappers, no business logic
tests/                the suite, run with pytest
out/                  run artifacts, gitignored
```

## Quickstart

Needs Python 3.11. AWS is needed only to harvest, to OCR a new document, or to
build the index. Reviewing a cached example needs no network.

```bash
python -m venv .venv
.venv\Scripts\activate                # Windows
source .venv/bin/activate             # macOS and Linux
pip install -r requirements.txt
pip install -e .
pytest
```

Build the regulation graph, then review a cached example offline:

```bash
python -m septic graph build
python -m septic review --pdf out/examples/permit_281364_60839580.pdf --offline
```

Three constructed packets cover the three outcomes, one each. They exist because
every real packet in the corpus is an approved permit, so none of them produces
DEFICIENCIES FOUND, and because a real packet reaches a verdict on a fraction of
the rule set while these exercise all fifteen:

```bash
python scripts/build_synthetic_packet.py
python -m septic review --pdf out/examples/permit_284102_60862118.pdf --offline  # deficiencies, 15 of 15
python -m septic review --pdf out/examples/permit_284517_60864903.pdf --offline  # no deficiencies, 15 of 15
python -m septic review --pdf out/examples/permit_284933_60867441.pdf --offline  # cannot verify, 0 of 15
```

The reviewer console, which is what a reviewer would actually be handed:

```bash
pip install streamlit          # demo only, not a pipeline dependency
streamlit run app.py
```

It serves the cached packets with no network and no credentials. Uploading a new
PDF needs credentials to run Textract, and says so rather than hanging.

The reviewer chatbot is optional and needs a Google Cloud project. Without one it
is hidden and everything else works unchanged:

```bash
set GOOGLE_CLOUD_PROJECT=your-project
set GOOGLE_CLOUD_LOCATION=global
set GOOGLE_GENAI_USE_VERTEXAI=true
gcloud auth application-default login
```

On Windows, `start_console.bat` does all of this in one double click. It reads
those settings from a gitignored `.env.local` beside it, binds the server to the
local network, and prints the address to hand out before opening the browser.

Other things that work:

```bash
python -m septic rules                       # evaluate the rule set
python -m septic graph context 5.3.12.1.3    # one section, fully resolved
python -m septic graph orphans               # requirements no rule covers yet
python -m septic preflight                   # check AWS access
python scripts/verify_rule_quotes.py         # every citation against the PDF
python scripts/rule_discrimination.py        # denied versus approved
python scripts/validate_geo.py               # coordinate parsing and distances
python scripts/make_figures.py               # maps and the comparison figure
python scripts/coverage_report.py            # regenerate docs/coverage.md
```

## Location screening

The regulation is full of isolation distances and Textract cannot measure a scanned
raster site plan. 105,801 of the CSV rows are geocoded, so distance to mapped
surface water is computed from coordinates instead, against Delaware FirstMap
hydrography committed under `data/gis`.

This is a screening prompt, never a determination. The regulation measures from the
disposal area and a geocoded point is somewhere else on the parcel, so the output
tells a reviewer what to check on the site plan. `src/septic/geo.py` records all
four reasons it cannot be a compliance answer, and no rule cites it, because no
provision of the regulation measures from an address point. A permit with no
coordinates produces no fact and so reads as CANNOT VERIFY.

## Reviewer chatbot

After a review, the console offers a chat box that answers questions about the
result: why a requirement failed, what the cited section says, what is still
missing. It runs on Gemini through Vertex AI and it is the one part of the system
that is a model talking.

It sits outside the pipeline on purpose. It is handed the composed report, the
same JSON the report renders, and nothing else. It never sees the OCR text, the
owner name or the document hash, it cannot reach the rule engine, and it cannot
change, add or remove a finding. The verdict on screen is the same verdict with
the chatbot switched off, which is how it is switched off by default: no Google
Cloud project configured means no chat box and no other difference.

`docs/chatbot.md` covers the setup, the grounding, and what is stripped before
anything is sent.

## Status

All 15 rules have been read against the pages they cite and confirmed with the
DNREC subject matter expert, so the certification interlock no longer holds any of
them back. `docs/rules_review.md` records that review and `docs/coverage.md` gives
the counts.

What limits the tool now is reading the packet, not certifying the rules. A review
reports three things separately: how many checks ran, how many did not apply to the
system, and how many could not be read. Across the corpus a mean of 3.72 checks of
15 actually compare a value, because 10 of the 15 need a measurement that lives on
a scanned drawing. Permit 281364 returns NO DEFICIENCIES FOUND with 5 of 15 checks
run, which is a real answer over a real fraction of the regulation, and the report
says so on its face rather than implying the other 10 passed.

Known gaps:

- 993 of the 1000 sections that use obligation language are cited by no rule.
- 12 of the 15 rules need a measurement off a site plan, so the discrimination
  harness cannot test them from the CSV.
- No public well layer exists, so the well setback cannot be screened from
  coordinates and has to be read off the plan.
- `applies_to` cannot express the replacement exemption in Section 5.2.4.2.4.2.
- Six thresholds were read and rejected, four because PDFium drops the
  less-than-or-equal glyph in this PDF and the direction could not be read.
- The Permit Events grid is not parsed, and it is where a return is recorded.
