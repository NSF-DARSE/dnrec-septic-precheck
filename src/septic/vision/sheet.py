"""Finding which page of a permit packet is the site plan.

Why this exists. Every measurement the drawing reader takes needs a page to take
it from, and until now that page was passed in by hand: vision_measure.py has a
--page flag, and the Bedrock Data Automation probe recorded in docs/DECISIONS.md
identified "page 6 of permit 281364" by eye. A reviewer opening a 13 page packet
should not have to know which sheet carries the drawing, and neither should the
console.

How it works, and why not a model. The page is chosen by scoring the text the OCR
step already read, so this costs nothing, needs no network, and returns the same
answer every time. A site plan page carries printed text that a form page does
not: a title block, a graphic scale, a scale notation, and the labels beside the
drawn symbols. Those words are the signal.

That is deliberately a weaker instrument than asking a model, and it is the right
one for this job. The pick has to be explainable to whoever is looking at the
screen, because the next thing that happens is an overlay drawn on the page it
chose, and a wrong page produces an annotated picture of the wrong sheet. So every
pick carries the phrases that produced it, and a caller can show them.

Confirming the pick. Text alone proposes; the graphic scale bar confirms. A page
with a bar the scale reader can measure is a plan sheet, and a page without one
cannot be measured off anyway, so the confirmation costs nothing extra: the
annotation needs the scale regardless. `find_site_plan` does the proposing, and
septic.vision.scale does the confirming. This module never calls a model.

Feed this OCR text, not PDF text. Measured on the 245 page regulation: scoring
pypdfium2's embedded text put 23 pages at or above the floor and correctly ranked
the plot plan figure on page 72 top at 13.0, but the four exhibit pages the symbol
detector has already found septic tanks and wells on, 180, 182, 187 and 189, all
scored 0.0. They are scanned images and carry no embedded text at all. That is the
same fact ingest/textract.py opens with: the sheets in this corpus are raster, so
there is nothing to extract without OCR. A drawing is exactly the page where PDF
text extraction returns nothing, so a finder built on it would be blind to its
only target.

Run against a Document from the OCR step and the markers are there. The Bedrock
Data Automation probe in docs/DECISIONS.md recorded that both Textract and BDA read
117 lines off the real site plan page, specifically the title block, the legend and
the scale notation, which are the three strongest things scored here.

What the regulation run also showed is that prose scores. Pages 100, 101, 130 and
48 are regulation text about site plans and reached 7.0 to 9.0 without being
drawings. `looks_drawn` is what separates them: it requires a graphic scale, a
scale ratio, or dimension callouts, and it was False on every one of them.

Known limit. The three demonstration packets built by scripts/build_synthetic_packet.py
carry a page headed "SITE PLAN (PLACEHOLDER)" which is text, not a drawing. This
scorer will pick it, correctly, because it is the page that says it is the site
plan. There is nothing on it to annotate, and `looks_drawn` is False.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ingest.layout import Document

# Phrases that appear on a site plan and effectively nowhere else in a packet.
# Weighted, because they are not equally diagnostic: a title block or a graphic
# scale settles it, whereas "well" on its own appears in prose all over an
# application form.
STRONG = {
    "site plan": 5.0,
    "graphic scale": 5.0,
    "plot plan": 5.0,
    "site & cross section": 4.0,
    "cross section": 2.5,
    "not to scale": 2.0,
}

SUPPORTING = {
    "existing well": 2.0,
    "proposed well": 2.0,
    "new well": 1.5,
    "septic tank": 1.5,
    "dosing chamber": 1.5,
    "distribution box": 1.5,
    "disposal area": 1.5,
    "sand mound": 1.5,
    "spare area": 1.5,
    "isolation distance": 2.0,
    "property line": 1.5,
    "tax map": 1.0,
    "tax parcel": 0.5,
    "benchmark": 1.0,
    "limiting zone": 1.0,
    "perc rate": 0.5,
    "north": 0.5,
}

# A written scale ratio, e.g. 1" = 100', 1"=50 ft, SCALE: 1 inch = 30 feet.
SCALE_NOTATION = re.compile(
    r"""1\s*(?:"|''|\u201d|\s*in(?:ch)?\.?)\s*=\s*\d+\s*(?:'|\u2019|ft|feet)?""",
    re.IGNORECASE,
)
SCALE_NOTATION_WEIGHT = 4.0

# A dimension callout like 100' or 47.25', which is how distances are written on a
# drawing. Counted, not just detected: a form mentions a couple, a drawing is
# covered in them.
DIMENSION = re.compile(r"\b\d{1,4}(?:\.\d+)?\s*(?:'|\u2019)(?:\s|$|[,.)])")

# Above this many dimension callouts the page is dimensioned rather than merely
# mentioning a measurement.
DIMENSION_FLOOR = 4
DIMENSION_WEIGHT = 0.4
DIMENSION_CAP = 4.0

# Below this the pick is not worth acting on. Set from the observed floor: a page
# carrying a title block and a graphic scale alone already scores 10.
MIN_SCORE = 5.0

# A drawing carries many short strings and few filled form fields. A page with
# more fields than this is a form, whatever else it says.
FORM_FIELD_CEILING = 12


@dataclass
class SheetPick:
    """One page, why it was chosen, and what may be done with it."""

    page: int
    score: float
    evidence: list[str] = field(default_factory=list)
    dimension_count: int = 0
    form_field_count: int = 0
    line_count: int = 0
    # True when the page reads like a drawing rather than a page that merely
    # mentions a site plan. A placeholder page names itself and has no dimensions.
    looks_drawn: bool = False
    runner_up: tuple[int, float] | None = None

    @property
    def confident(self) -> bool:
        """Whether the pick is clear of the next best page.

        A packet with one plan sheet produces a wide margin. Two sheets, a plan
        and a cross section on separate pages, produce a narrow one, and then the
        caller should offer the choice rather than assert it.
        """
        if self.score < MIN_SCORE:
            return False
        if self.runner_up is None:
            return True
        return self.score >= self.runner_up[1] * 1.5

    def why(self) -> str:
        found = ", ".join(self.evidence[:6]) or "nothing distinctive"
        return (
            f"page {self.page} scored {self.score:.1f} on {found}"
            + (f", {self.dimension_count} dimension callout(s)"
               if self.dimension_count else "")
        )

    def to_json(self) -> dict:
        return {
            "page": self.page,
            "score": round(self.score, 2),
            "evidence": self.evidence,
            "dimension_count": self.dimension_count,
            "form_field_count": self.form_field_count,
            "line_count": self.line_count,
            "looks_drawn": self.looks_drawn,
            "confident": self.confident,
            "runner_up": (
                {"page": self.runner_up[0], "score": round(self.runner_up[1], 2)}
                if self.runner_up else None
            ),
        }


def score_page(text: str, form_fields: int = 0, lines: int = 0) -> SheetPick:
    """Score one page's text. Exposed so the scoring can be tested directly."""
    low = " ".join(text.lower().split())
    score = 0.0
    evidence: list[str] = []

    for phrase, weight in STRONG.items():
        if phrase in low:
            score += weight
            evidence.append(phrase)
    for phrase, weight in SUPPORTING.items():
        if phrase in low:
            score += weight
            evidence.append(phrase)

    if SCALE_NOTATION.search(text):
        score += SCALE_NOTATION_WEIGHT
        evidence.append("a written scale ratio")

    dims = len(DIMENSION.findall(text))
    if dims >= DIMENSION_FLOOR:
        score += min(DIMENSION_CAP, dims * DIMENSION_WEIGHT)

    # A form page that happens to mention a site plan is still a form page. This
    # is what keeps a cover sheet listing "site plan attached" from winning.
    if form_fields > FORM_FIELD_CEILING:
        score *= 0.4
        evidence.append(f"penalised as a form, {form_fields} fields")

    return SheetPick(
        page=0,
        score=score,
        evidence=evidence,
        dimension_count=dims,
        form_field_count=form_fields,
        line_count=lines,
        # Naming itself is not enough, and neither is a scale ratio on its own.
        #
        # A lone ratio was the first attempt and it failed on a real page. Page 72
        # of the regulation is prose, section 5.3.21 listing what a site plan must
        # contain, and it contains the words "1 inch = 100 feet" as a requirement.
        # That matched, the page was picked as the sheet, it was rasterised and sent
        # to the scale reader, and the reader correctly answered that there is no
        # bar and no written scale on it, because there is no drawing on it. The
        # regulation describing a drawing is not a drawing.
        #
        # A drawing is dimensioned. So the ratio only counts alongside dimension
        # callouts, and a labelled graphic scale counts alone because only a
        # drawing carries one.
        looks_drawn=bool(
            "graphic scale" in low
            or (dims >= DIMENSION_FLOOR and SCALE_NOTATION.search(text))
            or dims >= DIMENSION_FLOOR * 3
        ),
    )


def _page_of(item) -> int:
    """Which page an item sits on, however the provider expressed it.

    Textract puts the page on the bounding box. A provider with no geometry has to
    carry it on the item instead, so both are accepted rather than assuming one.
    """
    page = getattr(item, "page", 0)
    if page:
        return page
    box = getattr(item, "box", None)
    return getattr(box, "page", 1) if box is not None else 1


def rank_pages(document: Document) -> list[SheetPick]:
    """Every page, best first."""
    pages = sorted(
        {_page_of(l) for l in document.lines} | {_page_of(f) for f in document.fields}
    )
    if not pages and document.pages:
        pages = list(range(1, document.pages + 1))

    fields_per_page: dict[int, int] = {}
    for f in document.fields:
        page = _page_of(f)
        fields_per_page[page] = fields_per_page.get(page, 0) + 1

    picks: list[SheetPick] = []
    for page in pages:
        text = document.text(page=page)
        lines = sum(1 for l in document.lines if _page_of(l) == page)
        pick = score_page(text, fields_per_page.get(page, 0), lines)
        pick.page = page
        picks.append(pick)

    # Pages that read like a drawing come first, and only then by score.
    #
    # Score alone ranked wrong on a real 19 page permit. Page 6 scored 14.5 on plan
    # vocabulary with one dimension callout and looks_drawn False; page 7 scored 14.0
    # with eighteen callouts and looks_drawn True. Page 7 is the drawing. Half a
    # point of vocabulary outranked the only evidence that mattered, and the sheet
    # that got rasterised and measured would have been the wrong one.
    #
    # The target is a drawing, so being one is the first question and how much plan
    # vocabulary a page carries is the tie break.
    picks.sort(key=lambda p: (not p.looks_drawn, -p.score, p.page))
    return picks


def find_site_plan(document: Document) -> SheetPick | None:
    """The page most likely to be the site plan, or None if nothing qualifies.

    None rather than a low confidence guess. The caller draws an overlay on
    whatever comes back, so a page returned here is a claim that the drawing is
    on it, and there is no useful way to draw "probably not this one".
    """
    ranked = rank_pages(document)
    if not ranked or ranked[0].score < MIN_SCORE:
        return None
    best = ranked[0]
    if len(ranked) > 1 and ranked[1].score > 0:
        best.runner_up = (ranked[1].page, ranked[1].score)
    return best


def render_page(pdf, page: int, scale: float = 3.0, out_dir=None):
    """Rasterise one page of a PDF and return (path, dpi).

    Lives here rather than in the script that used to own it so the console and
    the command line rasterise identically. The drawing reader's scale check
    depends on the resolution the image was produced at, so two renderers at two
    scales would give two answers for the same sheet.

    pypdfium2 renders at scale x 72 dpi, and returning that dpi is the point: it
    is knowable here and nowhere downstream, and it is what lets the written ratio
    on the sheet cross check the measured graphic bar.
    """
    from pathlib import Path

    import pypdfium2 as pdfium

    from .. import config

    pdf = Path(pdf)
    doc = pdfium.PdfDocument(str(pdf))
    if not 1 <= page <= len(doc):
        raise ValueError(
            f"{pdf.name} has {len(doc)} page(s), so page {page} does not exist"
        )
    out_dir = Path(out_dir or config.OUT_DIR / "vision")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{pdf.stem}-p{page}.png"
    doc[page - 1].render(scale=scale).to_pil().save(target)
    return target, scale * 72.0


__all__ = [
    "SheetPick", "find_site_plan", "rank_pages", "score_page", "render_page",
    "MIN_SCORE",
]
