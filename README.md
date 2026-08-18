# dnrec-septic-precheck

[![tests](https://github.com/NSF-DARSE/dnrec-septic-precheck/actions/workflows/ci.yml/badge.svg)](https://github.com/NSF-DARSE/dnrec-septic-precheck/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11-3776AB)](https://www.python.org/downloads/release/python-3110/)
[![AWS](https://img.shields.io/badge/AWS-S3%20%7C%20Textract%20%7C%20Bedrock-232F3E)](docs/evidence/preflight.txt)
[![status](https://img.shields.io/badge/status-prototype-orange)](docs/coverage.md)
[![rules verified](https://img.shields.io/badge/rules%20verified-0%20of%2015-red)](docs/rules_review.md)

A first pass over a septic permit application that flags deficiencies and puts the
regulation citation next to each one.

## The problem

A DNREC reviewer gets a packet and checks it by hand against a 245 page
regulation. Most packets come back for correction, and each correction means
reading the whole packet again from the start. Because the checking is manual, two
reviewers can read the same requirement differently. The slow part is not the
decision, it is finding the handful of things that are wrong.

## What it does not do

It does not approve or deny anything. It does not predict what DNREC will decide.
When it cannot confirm a rule against the regulation, it says so and gives no
answer. The reviewer decides.

## The pipeline

```mermaid
flowchart TB
    portal["DNREC permit portal<br/>den.dnrec.delaware.gov"]
    csv["Permit CSV export<br/>data.delaware.gov"]
    harvest["harvest<br/>septic/harvest/"]
    s3[("S3<br/>dnrec-septic-permits-241809646258")]
    textract["Textract<br/>StartDocumentAnalysis<br/>FORMS and TABLES"]
    cache[("Textract cache on disk<br/>keyed by document SHA256")]
    layout["block parser<br/>ingest/layout.py"]
    extract["field extractor<br/>ingest/extract.py"]
    engine["rule engine<br/>rules/engine.py"]
    yaml[("rules_7101.yaml<br/>15 rules, 0 certified")]
    pdf[("Regulation PDF<br/>2014, 245 pages")]
    graph[("Regulation graph<br/>reg_graph.json")]
    embed["Bedrock Titan v2<br/>embeddings only"]
    index[("Local vector index<br/>permit_index.json")]
    compose["report composer<br/>report/compose.py"]
    wording["Bedrock text model<br/>remedy wording only"]
    report["Reviewer report<br/>text and HTML"]

    csv -->|"which permits to fetch"| harvest
    portal -->|"permit pages and PDFs"| harvest
    harvest -->|"PDFs and manifest"| s3
    s3 -->|"S3 object reference"| textract
    textract -->|"blocks, cached once"| cache
    cache -->|"blocks"| layout
    layout -->|"lines, form fields, tables"| extract
    extract -->|"facts with provenance"| engine
    yaml -->|"thresholds and citations"| engine
    pdf -->|"parsed once into"| graph
    graph -->|"cross references and definitions"| compose
    engine -->|"PASS, FAIL, UNKNOWN per rule"| compose
    csv -->|"permit summaries"| embed
    embed -->|"vectors"| index
    index -->|"similar prior permits, context only"| compose
    compose -->|"optional rephrasing"| wording
    wording -->|"plainer wording, no findings"| compose
    compose -->|"verdict, findings, citations"| report

    style engine fill:#1b4332,color:#fff
    style yaml fill:#1b4332,color:#fff
    style embed fill:#555,color:#fff
    style wording fill:#555,color:#fff
    style index fill:#555,color:#fff
```

The green boxes decide. Everything grey is context or wording and cannot change a
finding. No model is ever asked whether an application complies.

Every stage above runs today. One correction: the vector index is a JSON file
scored with a dot product, not FAISS, because 1460 permits fit in memory many times
over. Not built, and not shown, is the Permit Events grid, which is where a return
is actually recorded.

## The three outcomes

**NO DEFICIENCIES FOUND.** Nothing was flagged among the checks that ran. This is
not an approval.

**DEFICIENCIES FOUND.** One or more requirements are not met, each itemised with
the section and page it comes from.

**CANNOT VERIFY.** No answer. Either a value could not be read off the packet, or
the rule needed has not been confirmed by a person.

## Why CANNOT VERIFY exists

Showing a reviewer a wrong setback distance is worse than showing nothing, because
a wrong number that looks official gets passed to an applicant. So a rule counts
only once a person has read the cited page and confirmed it.

## Data provenance

**Permit CSV.** data.delaware.gov, dataset mv7j-tx3u, exported 2026-08-17. 117,802
rows, 112,643 unique detail pages, 112,613 permits. 45 MB, gitignored.

**Regulation PDF.** Delaware Regulations Governing On-Site Wastewater Treatment and
Disposal Systems, January 11, 2014. 245 pages. Tracked here, because every
threshold has to be traceable to it.

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
pytest
```

Build the regulation graph, then review a cached example offline:

```bash
python -m septic graph build
python -m septic review --pdf out/examples/permit_281364_60839580.pdf --offline
```

The reviewer console, which is what a reviewer would actually be handed:

```bash
pip install streamlit          # demo only, not a pipeline dependency
streamlit run app.py
```

It serves the cached packets with no network and no credentials. Uploading a new
PDF needs credentials to run Textract, and says so rather than hanging.

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

## Status

No rule has been certified by a person, so the engine returns UNKNOWN for all 15
and the verdict for any application is CANNOT VERIFY. That is the interlock
working. `docs/rules_review.md` is the checklist for certifying them, and
`docs/coverage.md` gives the counts.

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
