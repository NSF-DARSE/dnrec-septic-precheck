"""Annotated correction PDF generation.

Produces a separate annotated copy of a permit PDF with reviewer-selected
findings marked as visible callout boxes or page-level notes. The original
PDF bytes are never modified.

Callout design:
    - FAIL: red border, light-red background, "CONFIRMED DEFICIENCY" heading
    - UNKNOWN: amber border, light-yellow background, "INFORMATION NEEDED" heading
    - Each callout shows: heading, plain-language request, rule ID, citation
    - Visible without clicking comment icons, readable when printed/flattened

Automatic page selection:
    - Uses OCR page/bounding box from the review payload when available
    - Classifies pages by keyword content (site plan, form, cross-section)
    - For isolation-distance findings, prefers site-plan pages with relevant features
    - Reports confidence and reasoning; requires reviewer confirmation when low

Coordinate handling:
    Textract normalised boxes are [0,1] with origin at top-left.
    PDF coordinates have origin at bottom-left.
    Page dimensions from cropbox (visible area) when present.
    Rotated pages fall back to page-level notes.

License: Uses pypdf (BSD-3-Clause).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import pypdf
from pypdf.annotations import FreeText, Highlight, Text
from pypdf.generic import ArrayObject, FloatObject, NameObject


# ---------------------------------------------------------------------------
# Colours (RGB normalised [0,1])
# ---------------------------------------------------------------------------

COLOR_RED = (0.863, 0.149, 0.149)       # #DC2626
COLOR_RED_BG = (1.0, 0.937, 0.937)      # #FFEFEF light red bg
COLOR_AMBER = (0.961, 0.620, 0.043)     # #F59E0B
COLOR_AMBER_BG = (1.0, 0.976, 0.918)    # #FFF9EA light yellow bg
COLOR_BLUE = (0.145, 0.388, 0.922)      # #2563EB

MIN_ANNOTATION_PTS = 5.0
CALLOUT_WIDTH = 220  # points
CALLOUT_LINE_HEIGHT = 11  # points per line
CALLOUT_PADDING = 8
CALLOUT_FONT_SIZE = 8
CALLOUT_MARGIN_RIGHT = 15  # from right edge
CALLOUT_MARGIN_TOP = 40  # from top edge


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PageClassification:
    """Classification of a PDF page by content."""

    page_num: int  # 1-based
    is_site_plan: bool = False
    is_form: bool = False
    is_cross_section: bool = False
    has_well: bool = False
    has_disposal: bool = False
    has_watercourse: bool = False
    has_property_line: bool = False
    has_tank: bool = False
    has_escarpment: bool = False
    has_scale: bool = False
    orientation: str = "portrait"  # portrait or landscape

    @property
    def label(self) -> str:
        if self.is_site_plan and not self.is_form:
            return "Site plan"
        if self.is_form and self.is_site_plan:
            return "Application form with site details"
        if self.is_cross_section:
            return "Cross-section detail"
        if self.is_form:
            return "Application form"
        return "Document page"


@dataclass
class PageSuggestion:
    """A suggested page for an annotation with reasoning."""

    page_num: int | None  # 1-based, or None when no suitable page found
    confidence: str  # "high", "medium", "low"
    reason: str
    label: str  # page classification label
    requires_reviewer_confirmation: bool = False
    candidates: list[tuple[int, float, str]] = field(default_factory=list)  # [(page, score, label)]

    @property
    def display(self) -> str:
        if self.page_num is None:
            return "No page identified, reviewer must select"
        return f"Page {self.page_num}, {self.label}"


@dataclass
class AnnotationRequest:
    """A single annotation the reviewer wants to place."""

    finding_id: str
    outcome: str  # "FAIL" or "UNKNOWN"
    text: str  # The correction/information-request message
    citation: str
    page: int  # 1-based, reviewer-confirmed
    box: dict | None = None
    use_precise: bool = True
    reviewer_note: str | None = None


@dataclass
class AnnotationResult:
    """Result of generating an annotated PDF."""

    pdf_bytes: bytes
    page_count: int
    annotation_count: int
    title: str
    filename: str
    original_hash: str


# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------

@dataclass
class PageGeometry:
    """Resolved geometry for a single PDF page."""

    width: float
    height: float
    x_offset: float
    y_offset: float
    rotation: int
    can_highlight: bool


def _get_page_geometry(page: pypdf.PageObject) -> PageGeometry:
    """Extract visible area geometry from a PDF page."""
    box = page.cropbox if "/CropBox" in page else page.mediabox
    x_offset = float(box.left)
    y_offset = float(box.bottom)
    width = float(box.width)
    height = float(box.height)
    rotation = int(page.get("/Rotate") or 0) % 360
    can_highlight = rotation == 0
    return PageGeometry(
        width=width, height=height,
        x_offset=x_offset, y_offset=y_offset,
        rotation=rotation, can_highlight=can_highlight,
    )


def textract_to_pdf_rect(
    box: dict, geom: PageGeometry,
) -> tuple[float, float, float, float] | None:
    """Convert Textract normalised box to PDF coordinates.

    Returns (x0, y0, x1, y1) or None if unsafe.
    """
    if not geom.can_highlight:
        return None

    left = box.get("left", 0)
    top = box.get("top", 0)
    w = box.get("width", 0)
    h = box.get("height", 0)

    if not (0 <= left <= 1 and 0 <= top <= 1 and w > 0 and h > 0):
        return None
    if left + w > 1.01 or top + h > 1.01:
        return None

    x0 = geom.x_offset + left * geom.width
    x1 = geom.x_offset + (left + w) * geom.width
    y1 = geom.y_offset + (1.0 - top) * geom.height
    y0 = geom.y_offset + (1.0 - (top + h)) * geom.height

    if (x1 - x0) < MIN_ANNOTATION_PTS or (y1 - y0) < MIN_ANNOTATION_PTS:
        return None
    page_x1 = geom.x_offset + geom.width
    page_y1 = geom.y_offset + geom.height
    if x0 < geom.x_offset - 1 or y0 < geom.y_offset - 1:
        return None
    if x1 > page_x1 + 1 or y1 > page_y1 + 1:
        return None

    return (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# Page classification and auto-selection
# ---------------------------------------------------------------------------

# Keywords for page classification
_SITE_PLAN_KEYWORDS = {"site plan", "disposal area", "disposal field", "setback", "absorption"}
_FORM_KEYWORDS = {"permit number", "applicant", "application", "owner name"}
_CROSS_SECTION_KEYWORDS = {"cross section", "cross-section", "profile", "trench detail", "elevation view"}
_FEATURE_KEYWORDS = {
    "well": {"well", "wells"},
    "disposal": {"disposal", "absorption", "drain field"},
    "watercourse": {"watercourse", "stream", "ditch", "creek", "pond", "water"},
    "property_line": {"property line", "property", "lot line", "boundary"},
    "tank": {"tank", "septic tank"},
    "escarpment": {"escarpment", "bank", "slope", "top of bank"},
    "scale": {"scale"},
}

# Finding parameters that should target a site plan
_SITE_PLAN_PARAMETERS = {
    "dist_disposal_to_well", "dist_disposal_to_watercourse",
    "dist_disposal_to_property_line", "dist_disposal_to_escarpment",
    "dist_tank_to_well", "dist_tank_to_watercourse",
    "wells_within_150_feet_shown", "disposal_slope",
}


def classify_pages(blocks: list[dict], page_count: int, reader: pypdf.PdfReader) -> list[PageClassification]:
    """Classify each page of the document by content."""
    classifications = []
    for page_num in range(1, page_count + 1):
        page_blocks = [b for b in blocks if b.get("Page") == page_num and b.get("BlockType") == "LINE"]
        all_text = " ".join(b.get("Text", "") for b in page_blocks).lower()

        mb = reader.pages[page_num - 1].mediabox
        orient = "landscape" if float(mb.width) > float(mb.height) else "portrait"

        cls = PageClassification(page_num=page_num, orientation=orient)
        cls.is_site_plan = any(k in all_text for k in _SITE_PLAN_KEYWORDS)
        cls.is_form = any(k in all_text for k in _FORM_KEYWORDS)
        cls.is_cross_section = any(k in all_text for k in _CROSS_SECTION_KEYWORDS)

        for feature, keywords in _FEATURE_KEYWORDS.items():
            if any(k in all_text for k in keywords):
                setattr(cls, f"has_{feature}", True)

        classifications.append(cls)
    return classifications


def suggest_page(
    finding: dict,
    classifications: list[PageClassification],
    blocks: list[dict] | None = None,
) -> PageSuggestion:
    """Suggest the best page for a finding based on deterministic evidence.

    Uses keyword frequency, dimensional evidence, and spatial relationship
    indicators, not just keyword presence. Returns ranked alternatives when
    confidence is not high.

    Priority:
    1. fact_page from the review payload (OCR evidence)
    2. Scored site-plan pages with relevant features for isolation-distance findings
    3. First dedicated site-plan page as fallback
    """
    fact_page = finding.get("fact_page")
    parameter = finding.get("parameter", "")

    # Priority 1: OCR-derived page
    if fact_page and 1 <= fact_page <= len(classifications):
        cls = classifications[fact_page - 1]
        return PageSuggestion(
            page_num=fact_page,
            confidence="high",
            reason="Value was read from this page by OCR",
            label=cls.label,
        )

    # Priority 2: Score site-plan pages using deep evidence
    if parameter in _SITE_PLAN_PARAMETERS and blocks is not None:
        scored = _score_pages_for_finding(parameter, classifications, blocks)
        if scored:
            best = scored[0]
            best_score, best_cls = best

            # Check if the best page has strong negative signals despite scoring
            # A notes/spec page can score highly from keyword mentions in narrative.
            # Require MULTIPLE notes indicators. A single "contractor shall" on a
            # site plan drawing is normal and should not disqualify it.
            page_blocks_best = [
                b for b in blocks
                if b.get("Page") == best_cls.page_num and b.get("BlockType") == "LINE"
            ]
            best_text = " ".join(b.get("Text", "") for b in page_blocks_best).lower()
            notes_indicators = sum(1 for k in (
                "general notes", "notes to contractor",
                "installer shall", "responsibility of the contractor",
            ) if k in best_text)
            # Also check "contractor shall" frequency. On a notes page it appears
            # many times; on a site plan it might appear once or twice in a note box.
            contractor_shall_count = best_text.count("contractor shall")
            best_is_notes = notes_indicators >= 2 or contractor_shall_count >= 4

            # Check if the top two are ambiguous (within 10% of each other)
            if len(scored) >= 2:
                second = scored[1]
                if best_score > 0 and second[0] / best_score > 0.9:
                    return PageSuggestion(
                        page_num=best_cls.page_num,
                        confidence="low",
                        reason=(
                            f"Pages {best_cls.page_num} and {second[1].page_num} "
                            f"are similarly likely, reviewer confirmation required"
                        ),
                        label=best_cls.label,
                        requires_reviewer_confirmation=True,
                    )

            # If best page is a notes page, do not recommend it, return None
            # with candidates for reviewer selection
            if best_is_notes:
                candidates = [
                    (cls.page_num, sc, cls.label) for sc, cls in scored[:5]
                ]
                return PageSuggestion(
                    page_num=None,
                    confidence="low",
                    reason=(
                        "No page could be confidently identified as the relevant "
                        "site plan. The highest-scoring pages appear to be "
                        "notes/specifications rather than spatial drawings."
                    ),
                    label="",
                    requires_reviewer_confirmation=True,
                    candidates=candidates,
                )

            if best_score >= 8:
                return PageSuggestion(
                    page_num=best_cls.page_num,
                    confidence="medium",
                    reason=f"Site plan with relevant features and dimensional evidence ({best_cls.label})",
                    label=best_cls.label,
                )
            elif best_score >= 4:
                return PageSuggestion(
                    page_num=best_cls.page_num,
                    confidence="low",
                    reason="Possible site plan page, reviewer confirmation required",
                    label=best_cls.label,
                    requires_reviewer_confirmation=True,
                )

    # Priority 2b: Keyword-only fallback when blocks not available
    if parameter in _SITE_PLAN_PARAMETERS:
        best_page = None
        best_score = 0
        for cls in classifications:
            if not cls.is_site_plan:
                continue
            score = 0.0
            if cls.has_disposal:
                score += 3
            if cls.has_scale:
                score += 1
            if "well" in parameter and cls.has_well:
                score += 3
            if "watercourse" in parameter and cls.has_watercourse:
                score += 3
            if "property" in parameter and cls.has_property_line:
                score += 3
            if "escarpment" in parameter and cls.has_escarpment:
                score += 3
            if "tank" in parameter and cls.has_tank:
                score += 2
            # Tiebreaker: richer pages
            feature_count = sum([
                cls.has_well, cls.has_disposal, cls.has_watercourse,
                cls.has_property_line, cls.has_tank, cls.has_escarpment,
                cls.has_scale,
            ])
            score += feature_count * 0.1
            # Penalise form pages and cross-sections
            if cls.is_form:
                score -= 2
            if cls.is_cross_section:
                score -= 3
            if score > best_score:
                best_score = score
                best_page = cls

        if best_page and best_score >= 4:
            return PageSuggestion(
                page_num=best_page.page_num,
                confidence="medium",
                reason=f"Site plan with relevant features ({best_page.label})",
                label=best_page.label,
            )

    # Priority 3: First dedicated site-plan page
    site_plans = [c for c in classifications if c.is_site_plan and not c.is_form]
    if site_plans:
        return PageSuggestion(
            page_num=site_plans[0].page_num,
            confidence="low",
            reason="First site plan page (reviewer confirmation required)",
            label=site_plans[0].label,
            requires_reviewer_confirmation=True,
        )

    # Fallback
    return PageSuggestion(
        page_num=1,
        confidence="low",
        reason="No site plan identified, reviewer must select page",
        label=classifications[0].label if classifications else "Page 1",
        requires_reviewer_confirmation=True,
    )


def _score_pages_for_finding(
    parameter: str,
    classifications: list[PageClassification],
    blocks: list[dict],
) -> list[tuple[float, PageClassification]]:
    """Score pages for a specific finding using evidence-based analysis.

    Scoring categories (strongest to weakest):
    1. Co-occurrence of rule subject + target on same page (required baseline)
    2. Spatial evidence: dimensions, scale, bearings, contours
    3. Overhead-plan indicators: north arrow, boundaries, benchmark
    4. Relevant feature labels specific to this finding
    5. Small text-density bonus (capped, not dominant)

    Negative signals: form pages, cross-sections, narrative-heavy pages.
    Keyword contributions are capped so repetition cannot dominate.
    """
    # What the finding needs to see on the page
    _FINDING_SUBJECTS = {
        "dist_disposal_to_well": ({"disposal", "disposal field", "absorption"}, {"well", "wells"}),
        "dist_disposal_to_watercourse": ({"disposal", "disposal field"}, {"watercourse", "stream", "ditch", "creek"}),
        "dist_disposal_to_property_line": ({"disposal", "disposal field"}, {"property", "lot line", "boundary"}),
        "dist_disposal_to_escarpment": ({"disposal", "disposal field"}, {"escarpment", "bank", "top of bank"}),
        "dist_tank_to_well": ({"tank", "septic tank"}, {"well", "wells"}),
        "dist_tank_to_watercourse": ({"tank", "septic tank"}, {"watercourse", "stream", "ditch"}),
        "wells_within_150_feet_shown": ({"disposal", "disposal field"}, {"well", "wells", "150"}),
        "disposal_slope": ({"disposal", "disposal field"}, {"slope", "contour", "grade"}),
    }

    subject_kws, target_kws = _FINDING_SUBJECTS.get(
        parameter, ({"disposal"}, set())
    )

    # Spatial evidence keywords
    spatial_kws = {"feet", "ft", "scale", "elev", "elevation", "distance",
                   "setback", "bearing", "contour", "dimension"}
    # Overhead plan indicators
    overhead_kws = {"north", "benchmark", "property", "boundary", "lot line",
                    "right-of-way", "r/w", "parcel", "mag"}
    # Negative keywords
    form_kws = {"applicant", "permit number", "application", "owner name"}
    crosssection_kws = {"cross section", "cross-section", "profile view",
                        "trench detail", "elevation view"}
    # Notes/specification pages mention features but in construction narrative
    notes_kws = {"general notes", "notes to contractor", "contractor shall",
                 "installer shall", "responsibility of the contractor"}

    scored: list[tuple[float, PageClassification]] = []

    for cls in classifications:
        if not cls.is_site_plan:
            continue

        page_blocks = [
            b for b in blocks
            if b.get("Page") == cls.page_num and b.get("BlockType") == "LINE"
        ]
        all_text = " ".join(b.get("Text", "") for b in page_blocks).lower()

        score = 0.0

        # --- Category 1: Co-occurrence (required baseline, 0 or 6 points) ---
        has_subject = any(k in all_text for k in subject_kws)
        has_target = any(k in all_text for k in target_kws)
        if has_subject and has_target:
            score += 6
        elif has_subject:
            score += 2  # partial credit for subject only
        # If neither subject nor target present, this page is unlikely correct

        # --- Category 2: Spatial evidence (up to 6 points, capped) ---
        spatial_count = sum(1 for k in spatial_kws if k in all_text)
        score += min(spatial_count * 2, 6)

        # --- Category 3: Overhead-plan indicators (up to 5 points, capped) ---
        overhead_count = sum(1 for k in overhead_kws if k in all_text)
        score += min(overhead_count * 2, 5)

        # --- Category 4: Feature labels (up to 4 points, capped) ---
        feature_count = sum([
            cls.has_well, cls.has_disposal, cls.has_watercourse,
            cls.has_property_line, cls.has_tank, cls.has_escarpment,
        ])
        score += min(feature_count, 4)

        # --- Category 5: Small text-density bonus (up to 2, NOT dominant) ---
        text_len = len(all_text)
        if text_len > 1000:
            score += 2
        elif text_len > 500:
            score += 1
        # Note: sparse graphical pages with strong co-occurrence and spatial
        # evidence can still score highly. Density is a minor tiebreaker.

        # --- Negative signals ---
        is_form_page = any(k in all_text for k in form_kws)
        is_cross_section = any(k in all_text for k in crosssection_kws)
        is_notes_page = any(k in all_text for k in notes_kws)
        if is_form_page:
            score -= 4
        if is_cross_section:
            score -= 5
        if is_notes_page:
            score -= 6  # Notes pages mention features in narrative, not on drawing
        # Note: landscape orientation is NOT penalised independently.
        # Valid site plans are often landscape. Only penalise based on
        # OCR/layout evidence (cross-section, form, etc.).

        scored.append((score, cls))

    scored.sort(key=lambda x: -x[0])
    return scored


# ---------------------------------------------------------------------------
# Callout rendering
# ---------------------------------------------------------------------------

def _outcome_label(outcome: str) -> str:
    if outcome == "FAIL":
        return "CONFIRMED DEFICIENCY"
    return "INFORMATION NEEDED"


def _outcome_colors(outcome: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return (border_color, background_color) for an outcome."""
    if outcome == "FAIL":
        return COLOR_RED, COLOR_RED_BG
    return COLOR_AMBER, COLOR_AMBER_BG


def _wrap_text(text: str, max_chars: int = 38) -> list[str]:
    """Wrap text into lines of max_chars."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def _make_callout_freetext(
    geom: PageGeometry,
    outcome: str,
    finding_id: str,
    text: str,
    citation: str,
    index: int = 0,
) -> FreeText:
    """Create a visible callout box with full request text.

    Placed in the right margin to avoid covering main content.
    """
    label = _outcome_label(outcome)
    border_color, _ = _outcome_colors(outcome)

    # Build the visible text content
    content_lines = [label, ""]
    content_lines.extend(_wrap_text(text, 36))
    content_lines.append("")
    content_lines.append(f"Rule: {finding_id}")
    content_lines.append(f"Ref: {citation}")

    # Calculate height
    line_count = len(content_lines)
    height = max(60, line_count * CALLOUT_LINE_HEIGHT + CALLOUT_PADDING * 2)

    # Position: right margin, stacked from top
    x1 = geom.x_offset + geom.width - CALLOUT_MARGIN_RIGHT
    x0 = x1 - CALLOUT_WIDTH
    y1 = geom.y_offset + geom.height - CALLOUT_MARGIN_TOP - (index * (height + 10))
    y0 = y1 - height

    # Clamp to page bounds
    if y0 < geom.y_offset + 10:
        y0 = geom.y_offset + 10
        y1 = y0 + height

    full_text = "\n".join(content_lines)
    r, g, b = border_color

    annotation = FreeText(
        text=full_text,
        rect=(x0, y0, x1, y1),
        font_size=f"{CALLOUT_FONT_SIZE}pt",
        font_color="1a1a1a",
        border_color=f"{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}",
    )
    return annotation


def _make_highlight_with_note(
    rect: tuple[float, float, float, float],
    color: tuple[float, float, float],
    text: str,
) -> Highlight:
    """Create a highlight annotation with popup contents."""
    x0, y0, x1, y1 = rect
    quad_points = ArrayObject([
        FloatObject(x0), FloatObject(y1),
        FloatObject(x1), FloatObject(y1),
        FloatObject(x0), FloatObject(y0),
        FloatObject(x1), FloatObject(y0),
    ])
    annotation = Highlight(
        rect=(x0, y0, x1, y1),
        quad_points=quad_points,
    )
    annotation[NameObject("/C")] = ArrayObject([FloatObject(c) for c in color])
    annotation[NameObject("/Contents")] = pypdf.generic.create_string_object(text)
    return annotation


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_annotated_pdf(
    original_bytes: bytes,
    annotations: list[AnnotationRequest],
) -> AnnotationResult:
    """Generate an annotated PDF from original bytes and annotation requests.

    The original bytes are never modified. A new PDF is produced in memory.
    """
    if not annotations:
        raise ValueError("No annotations selected")

    original_hash = hashlib.sha256(original_bytes).hexdigest()
    reader = pypdf.PdfReader(BytesIO(original_bytes))
    page_count = len(reader.pages)

    for ann in annotations:
        if ann.page < 1 or ann.page > page_count:
            raise ValueError(
                f"Finding {ann.finding_id}: page {ann.page} out of range "
                f"(document has {page_count} pages)"
            )

    has_fail = any(a.outcome == "FAIL" for a in annotations)
    title = "Annotated Correction Copy" if has_fail else "Annotated Information Request"

    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.add_metadata({
        "/Title": title,
        "/Subject": "Reviewer annotations for permit correction",
        "/Creator": "DNREC Septic Permit Review Tool",
    })

    page_callout_counts: dict[int, int] = {}
    annotation_count = 0

    for ann in annotations:
        page_idx = ann.page - 1
        page = reader.pages[page_idx]
        geom = _get_page_geometry(page)
        border_color, _ = _outcome_colors(ann.outcome)

        # Try precise highlight if geometry is available and safe
        if ann.use_precise and ann.box is not None:
            rect = textract_to_pdf_rect(ann.box, geom)
            if rect is not None:
                label = _outcome_label(ann.outcome)
                note_text = f"[{label}] {ann.text}\nRule: {ann.finding_id}\nCitation: {ann.citation}"
                highlight = _make_highlight_with_note(rect, border_color, note_text)
                writer.add_annotation(page_number=page_idx, annotation=highlight)

        # Always add visible callout (readable without clicking)
        idx = page_callout_counts.get(page_idx, 0)
        callout = _make_callout_freetext(
            geom, ann.outcome, ann.finding_id, ann.text, ann.citation, index=idx,
        )
        writer.add_annotation(page_number=page_idx, annotation=callout)
        page_callout_counts[page_idx] = idx + 1
        annotation_count += 1

    output = BytesIO()
    writer.write(output)
    pdf_bytes = output.getvalue()

    filename_suffix = "annotated_corrections" if has_fail else "information_request"
    filename = f"permit_{filename_suffix}.pdf"

    return AnnotationResult(
        pdf_bytes=pdf_bytes,
        page_count=page_count,
        annotation_count=annotation_count,
        title=title,
        filename=filename,
        original_hash=original_hash,
    )


def verify_original_unchanged(original_bytes: bytes, expected_hash: str) -> bool:
    """Verify the original PDF bytes have not been modified."""
    return hashlib.sha256(original_bytes).hexdigest() == expected_hash


def safe_filename(permit_number: str | None, has_fail: bool) -> str:
    """Generate a safe download filename."""
    base = permit_number or "permit"
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", base)
    suffix = "annotated_corrections" if has_fail else "information_request"
    return f"{safe}_{suffix}.pdf"
