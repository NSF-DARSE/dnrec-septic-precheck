# dnrec-septic-precheck

Pre-submission review for Delaware septic permit applications. An applicant runs
a draft application through it and gets one of three answers: ready to submit,
likely to be returned with an itemised list of what to fix, or cannot verify
because required information could not be read.

DNREC rarely denies a septic application outright. It returns the application for
correction, the applicant fixes it, and the application is then accepted. This
tool predicts the return, before submission, so the correction round trip does not
happen.

## How the verdict is decided

    application PDF
      -> Textract     OCR text, form fields, bounding boxes
      -> extractor    facts: lot area, setbacks, perc rate, system type, parcel
      -> rule engine  per rule PASS, FAIL, or UNKNOWN with a regulation citation
      -> retrieval    similar prior permits and their outcomes, as context
      -> composer     report

The verdict comes from rule evaluation and from nothing else. Retrieved permits
give the report context and concrete wording, and a language model may be used to
write prose, but the model is handed the verdict as an input and is never asked to
decide it. The same facts and the same rule set produce the same verdict every
time.

Three outcomes per rule rather than two. UNKNOWN covers an unverified threshold, a
value the extractor could not read, and a value that will not parse as a number.
All three occur in scanned documents. Treating any of them as a pass would hide a
problem, and treating them as a failure would invent one.

## Regulation thresholds

`docs/regulations/de-onsite-wastewater-2014.pdf` is the only authority for
threshold values. `scripts/extract_rule_candidates.py` scans it and writes
`src/septic/rules/candidates.md`, listing every passage that states a number with
a unit, each with its section, page, and the sentence quoted verbatim.

Candidates are not rules. A number moves into `src/septic/rules/rules_7101.yaml`
only after a person opens the cited page and confirms the value, the units, and
the conditions it applies under. Until then the rule carries `verified: false` and
the engine returns UNKNOWN for it.

Nothing in the shipped rule set is verified, so every application currently comes
back as cannot verify. That is intended. A wrong setback distance shown to
permitting staff is a worse outcome than no answer.

## Layout

    docs/                 project documentation and the regulation
    src/septic/
      config.py           bucket, region, profile, and paths
      harvest/            scraping permits and documents into S3
      ingest/             Textract submission and block parsing
      rules/              rule schema, engine, rule set, candidates
      retrieval/          similarity search over prior permits
      report/             report composition and rendering
      cli.py              entry point
    scripts/              runnable wrappers, no logic
    tests/
    out/                  run artifacts, not tracked

## Running it

    pip install -r requirements.txt
    python -m septic preflight
    python -m septic candidates
    python -m septic rules
    python -m septic harvest --status Denied "Application Returned"
    python -m septic audit
    python -m septic verify --limit 15

Scripts under `scripts/` call the same code and exist so a single file can be run
directly.

## Configuration

Bucket, region, profile, model ids, and the year cutoff live in
`src/septic/config.py` and are overridable by environment variable
(`SEPTIC_S3_BUCKET`, `SEPTIC_AWS_PROFILE`, `AWS_REGION`, `SEPTIC_YEAR_MIN`, and
others). No other module hardcodes them.

`python -m septic preflight` checks S3, Textract, and Bedrock against the current
credentials and prints a pass/fail table. Run it first. Text generation on Bedrock
is currently denied for the development account; embeddings and Textract work.

## Scope

Permits from 2014 onward. That cutoff is not arbitrary: 2014 and later permits
fall under the current regulation, and earlier ones fall under superseded law, so a
finding against an older permit would cite a rule that no longer applies.

## Data handling

The permit CSV export is not tracked. It is 45 MB and is not ours to redistribute.
Harvested documents are FOIA releasable but are stored in a private bucket with
public access blocked and default encryption on. The permit site's `robots.txt`
disallows the paths that expose document links, so a full crawl needs written
DNREC agreement first. The 234 permit prototype run is small enough to be
reasonable without it.

See `docs/HANDOFF.md` for measured figures, the harvest already completed, and the
open items.
