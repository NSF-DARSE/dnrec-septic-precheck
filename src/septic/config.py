"""Central configuration. Every other module reads AWS names and paths from here.

Values can be overridden with environment variables so the same code runs
against a DNREC-owned account without edits.
"""
from __future__ import annotations

import os
from pathlib import Path

# Repository layout
PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
ROOT = SRC_DIR.parent

DOCS_DIR = ROOT / "docs"
REGULATIONS_DIR = DOCS_DIR / "regulations"
OUT_DIR = Path(os.environ.get("SEPTIC_OUT_DIR", ROOT / "out"))
CACHE_DIR = OUT_DIR / "cache"

# The regulation that defines every threshold the rule engine can cite.
REGULATION_PDF = REGULATIONS_DIR / "de-onsite-wastewater-2014.pdf"

# Source data export. Large, never committed.
PERMIT_CSV = Path(
    os.environ.get("SEPTIC_PERMIT_CSV", ROOT / "Permitted_Septic_Systems_20260817.csv")
)

# AWS
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE = os.environ.get("SEPTIC_AWS_PROFILE", "dnrec")
S3_BUCKET = os.environ.get("SEPTIC_S3_BUCKET", "dnrec-septic-permits-241809646258")

# S3 key prefixes
PDF_PREFIX = "pdfs"
MANIFEST_PREFIX = "manifest"
TEXTRACT_PREFIX = "textract"
PREFLIGHT_PREFIX = "preflight"

# Bedrock model ids. Overridable because availability differs per account.
BEDROCK_TEXT_MODEL = os.environ.get(
    "SEPTIC_BEDROCK_TEXT_MODEL", "us.anthropic.claude-opus-4-6-v1"
)
BEDROCK_EMBED_MODEL = os.environ.get(
    "SEPTIC_BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0"
)
# Which OCR provider reads a permit PDF. Both produce the same
# ingest.layout.Document, so everything downstream of the read, including the
# site plan symbol mapping, is unaffected by this choice.
#
#   textract  StartDocumentAnalysis with FORMS and TABLES. Gives a calibrated
#             per-block confidence and a bounding box for every value.
#   bedrock   Converse with the PDF as a document block, answering in JSON.
#             Gives text, form fields and tables, but no geometry, and a
#             confidence the model reports about itself. See ingest/bedrock_ocr.py.
OCR_PROVIDER = os.environ.get("SEPTIC_OCR_PROVIDER", "textract")

# Model used when OCR_PROVIDER is bedrock. It has to accept a document block, so
# it is a separate setting from BEDROCK_TEXT_MODEL rather than a reuse of it.
BEDROCK_OCR_MODEL = os.environ.get(
    "SEPTIC_BEDROCK_OCR_MODEL", "us.anthropic.claude-opus-4-6-v1"
)
# Vision model for reading site plan sheets: scale bars, and the objects a
# setback is measured between. Confirmed callable on the workshop role, unlike
# the anthropic.claude-sonnet-4-5 ids, which return AccessDenied there.
BEDROCK_VISION_MODEL = os.environ.get(
    "SEPTIC_BEDROCK_VISION_MODEL", "us.anthropic.claude-opus-4-6-v1"
)

# Mentor scope: 2014 onward falls under the current regulation.
YEAR_MIN = int(os.environ.get("SEPTIC_YEAR_MIN", "2014"))

# Scraping politeness
USER_AGENT = (
    "DNREC-septic-permit-archive/1.0 "
    "(DNREC prototype knowledge base; bulk permit document archival)"
)
MIN_REQUEST_INTERVAL = float(os.environ.get("SEPTIC_MIN_INTERVAL", "0.25"))

DETAIL_URL = (
    "https://den.dnrec.delaware.gov/Detail/PermitDetail.aspx?id={id}&Panel=ViewAll"
)
DOC_HOST = "docs.dnrec.delaware.gov"


def session(profile: str | None = None, region: str | None = None):
    """Return a boto3 Session wired to the configured profile.

    Falls back to the ambient credential chain when the named profile is absent,
    which is what happens in CI or on an instance role.
    """
    import boto3
    from botocore.exceptions import ProfileNotFound

    profile = profile or AWS_PROFILE
    region = region or AWS_REGION
    try:
        return boto3.Session(profile_name=profile, region_name=region)
    except ProfileNotFound:
        return boto3.Session(region_name=region)


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
