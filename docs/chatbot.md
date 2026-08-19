# Reviewer chatbot

A Gemini-powered assistant that helps DNREC reviewers understand the
deterministic review results. It appears below the embedded report after a
permit is reviewed.

## What it does

- Explains rule outcomes (PASS, FAIL, UNKNOWN) with cited regulation text
- Summarizes findings and identifies missing information
- Suggests what the reviewer should verify next
- Labels AI-generated explanation separately from facts and rule results

## What it does not do

- Approve or deny a permit
- Override or change any deterministic rule result
- Make regulatory claims without a citation from the review data
- Access information beyond the current review payload

## Setup

### Prerequisites

- Python 3.11
- GCP project with Vertex AI API enabled
- Application Default Credentials configured (`gcloud auth application-default login`)
- The `gemini-2.5-flash` model available in your project

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GOOGLE_CLOUD_PROJECT` | Yes | — | GCP project id |
| `GOOGLE_CLOUD_LOCATION` | No | `global` | GCP location |
| `GOOGLE_GENAI_USE_VERTEXAI` | Yes | `false` | Set to `true` for Vertex AI |
| `SEPTIC_GEMINI_MODEL` | No | `gemini-2.5-flash` | Model name override |

### Install

```bash
pip install -r requirements.txt
```

This adds `google-genai==2.18.1` alongside existing dependencies. The existing
`requests==2.28.2` pin is compatible (google-genai requires `>=2.28.1,<3.0.0`).

### Run

```bash
export GOOGLE_CLOUD_PROJECT=hackathon-2026-dnrec
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_GENAI_USE_VERTEXAI=true

streamlit run app.py
```

The chatbot section appears after uploading and reviewing a permit. If the
environment variables are not set or credentials are unavailable, the chatbot
section is hidden and the review continues working normally.

### Credentials

Uses Application Default Credentials (ADC). Do not store credentials in Git.

```bash
gcloud auth application-default login
```

No API keys, service account files, or tokens are committed to the repository.

## Architecture

```
src/septic/chatbot/
├── __init__.py        Package marker
├── config.py          Environment variable loading and availability check
├── context.py         Grounded context builder with PII filtering
├── client.py          Gemini chat session wrapper
└── instructions.py    System instruction (auditable constant)
```

The chatbot module is separate from the rule engine and report pipeline. It
receives the composed review payload (the same JSON the report renders) and
builds a compact grounded context from it. No OCR text, owner names, or
document hashes are sent to Gemini.

## Testing

```bash
pytest tests/test_chatbot.py -v
```

All tests mock Gemini calls. No cloud credits are spent. The test suite covers:
- Configuration loading from environment
- Grounded context construction
- PII exclusion and redaction
- System instruction safety boundaries
- Citation preservation (no fabrication)
- API failure fallback
- Empty/missing payload handling

## Security

- Uploaded documents, extracted text, and reviewer questions are treated as
  untrusted content that cannot override system instructions
- Owner names, addresses, phone numbers, emails, and document hashes are
  stripped before sending to Gemini
- No prompts, responses, or permit contents are logged
- Temperature 0 for consistent answers
- System instruction enforces reviewer-assistant role boundaries
