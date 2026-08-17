# Project state

Findings and measurements from the work so far. Numbers recorded here have been
measured and should not be re-derived.

## Goal

A pre-submission review tool for septic permit applications, built to pitch to
DNREC. An applicant runs a draft application through it and gets back one of
three answers: ready to submit, likely to be returned with an itemised list of
what to fix, or cannot verify because information is missing.

The product is not an approve/reject classifier. DNREC rarely denies outright.
It returns applications for correction, the applicant fixes them, and they are
then accepted. What the tool predicts is a return.

Scope is 2014 onward. Permits from 2014 forward fall under the current
regulation, and earlier ones fall under superseded law.

## Architecture

    application PDF
      -> Textract     OCR text, form fields, bounding boxes
      -> extractor    facts: lot area, setbacks, perc rate, system type, parcel
      -> rule engine  per rule PASS, FAIL, or UNKNOWN with a citation
      -> retrieval    similar prior permits and their outcomes, as context
      -> composer     report

The verdict is computed from rule evaluation and nothing else. Retrieved permits
supply context and wording for the report. A language model is given the verdict
as an input and is never asked to produce it. Same facts and same rule set give
the same verdict every time.

## The finding that shapes the product

The return signal is not in the status column.

For 2014 onward there are 3 permits with status `Application Returned` and 99
with `Denied`. `permitStatus` records only the permit's current state, so an
application that was returned, corrected, and later approved reads as
`Completion Report Received`.

Return history lives in the Permit Events grid on each detail page, which the
harvester does not yet parse. Grounding a recommender on "would this be returned"
requires that grid, not a filter on `permitStatus`.

A second consequence: return letters are not published. Only 5 of 87 returned
permits have any document attached. Deficiency rules therefore come from the
regulation, not from the corpus. With roughly 253 negative examples in total,
training a classifier on this data is not viable, which is why the rule engine
decides and retrieval only explains.

## The Documents grid is served non-deterministically

Verified with three refetches each across 16 permits.

Five of eight permits known to have documents returned different document counts
across refetches, including zero. The HTML length changed with the count, for
example 29,352 bytes falling to 27,532, so the grid is genuinely absent from some
responses rather than being missed by the parser. None of the eight permits
recorded as having zero documents ever produced one, over a stable 27,321 to
30,311 byte range.

A single crawl pass under-collects. Any count of zero needs two agreeing
responses before it is believed, which is what `septic verify` does. The 134
zero-document permits from the completed run still need rechecking.

## Data source

The permit dataset published as a Socrata table contains no PDFs. Documents are
reached in three steps.

1. The CSV export gives one row per permit revision, with a detail page URL.
2. The detail page carries a Documents grid whose links point to the document
   host.
3. The document host returns `application/pdf`.

Document URLs encode their own metadata using `<I>`, `<R>`, and `<G>` markers in
the path tail: document type, program, permit number, and a description ending in
the tax parcel id. Reading those markers as field delimiters gives an exact
document type. An earlier keyword match put 120 of 143 documents into an "Other"
bucket because the two most common type names, `Permit` and
`Permit Supporting Document`, were not in the keyword table. Structural
extraction reduced that to zero and filled the parcel id for all 143, which is
the join key from a permit document to a site plan.

Closing markers contain a literal slash, so the path tail has to be rejoined
after the document id and hash rather than split on `/`.

## Measured facts

| Metric | Value | Basis |
| --- | --- | --- |
| Average PDF size | 8.92 MB | 15 real PDFs, range 0.14 to 31.2 MB |
| Documents per permit | about 1.6 | 30 permits across 5 eras |
| Permits with at least one document | about 90 percent | same sample |
| Full corpus estimate | about 180,000 PDFs, 1.5 TB | extrapolation |
| 2014+ estimate | about 45,000 documents, 396 GB | extrapolation |

An earlier estimate of 200 to 350 GB for the full corpus was wrong. It rested on
a single 2 MB file.

Row counts: 117,802 CSV rows over 112,643 unique detail pages. For 2014 onward,
28,706 rows over 28,408 unique detail pages. Before 2014, 74,813 rows over 69,972
unique pages. A further 14,283 rows have no parseable year and are dropped
silently by a naive year filter, so `select_permits` counts them separately and
only includes them when asked.

Unique permits per year from 2014: 2585, 2072, 2259, 2336, 2353, 2369, 2616,
2675, 2296, 1939, 1964, 1831, 1113.

Status counts for 2014 onward, largest first: Completion Report Received 21,256,
Expired 2,833, Approved 1,226, Call Notification Received 1,041, System
Inspection 1,004, Void 381, System Abandoned 218, Application Received 155,
Denied 99, On Hold 84, Withdrawn 83, Application Returned 3.

## Completed harvest

The `Denied` and `Application Returned` slice, all years.

    elapsed             500s
    permits processed   234   (253 CSV rows over 234 unique detail pages)
    permits_ok          234
    permits_no_docs     134   needs rechecking, see the non-determinism section
    docs_uploaded       143
    docs_failed         0
    uploaded            401.9 MB

S3 inventory read through the API: 146 objects, 144 of them PDFs (143 harvested
plus one round-trip test), 421,418,079 bytes. Size minimum, median, mean, and
maximum: 0.08, 1.56, 2.79, 21.74 MB. All 143 manifest uploads reconcile against
143 objects with none missing. Counties: Kent 116, New Castle 70, Sussex 48.

## Storage layout

    pdfs/status=<Status-Slug>/<detail_id>_<permitNumber>/<NN>_<DocType>.pdf
    manifest/manifest_<tag>.jsonl

Each object carries metadata for permit number, permit status, detail id,
document type, parcel id, and source URL.

The manifest is JSONL with one record per permit: the permit fields joined from
the CSV, plus a `documents` array whose entries hold the document type, program,
permit number, description, parcel id, FOIA status, URL, S3 key and URI, byte
count, and checksums. This is the input to retrieval.

## Environment

Python 3.11 on Windows. Dependencies are pinned in `requirements.txt`.

AWS access uses a named profile that bridges a login session into the SDK,
because a login session written to the CLI config is not readable by the SDK
directly. The profile uses `credential_process` to export credentials on demand,
which also refreshes them as the short-lived credentials rotate.

    [profile dnrec]
    region = us-east-1
    credential_process = aws configure export-credentials --profile default --format process

Bucket and region come from `src/septic/config.py` and are overridable by
environment variable. No other module hardcodes them.

Two shell behaviours cost time on Windows and are worth knowing. Console output
wraps and truncates, so scripts write their reports to a file under `out/` and the
file is the source of truth. Number formatting is locale dependent and produced
`0,0 MB` for a 401 MB total, so size arithmetic is done in Python.

## Service access

Checked against the workshop account with `septic preflight`.

S3 read and write work. Textract `StartDocumentAnalysis` works and returns
blocks. Bedrock lists 122 models. Titan text embeddings invoke successfully at
1024 dimensions.

Bedrock text generation is denied. The role cannot call `InvokeModel` on any
Anthropic model, on either the foundation model path or the inference profile
path, across all 16 candidates tried. Report composition needs a different
provider or a different account. Retrieval is unaffected because embeddings work.

## Open items

1. Parse the Permit Events grid. This is the actual return signal and it is not
   captured.
2. Recheck the 134 zero-document permits, requiring two agreeing responses.
3. Decide what to do with the 14,283 rows that have no parseable year.
4. Verify regulation thresholds and promote them from `candidates.md` into
   `rules_7101.yaml`. Nothing is verified yet, so every rule returns UNKNOWN and
   every application comes back as cannot verify.
5. Build the fact extractor that turns Textract output into the parameters the
   rules reference.
6. Resolve text generation access.
7. `robots.txt` on the permit site disallows the detail and search paths, which
   are the only paths exposing document links. Acceptable at 234 permits for a
   prototype. Get written DNREC sign-off before a 28,000 or 112,000 permit run.
8. The account is a temporary workshop account. Acceptable for the prototype, but
   the real corpus belongs in a DNREC-owned account.
9. Cost for the 2014+ run is dominated by crawl time, not storage. About 396 GB
   is roughly 9 USD per month in S3 Standard, against 28,408 detail pages and
   about 45,000 document downloads.
