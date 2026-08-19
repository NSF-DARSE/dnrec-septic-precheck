"""Turning located symbols and a measured scale into facts the rules can use.

This is the step the rest of the pipeline has been missing. The rule engine asks
for parameters by name and the extractor reads them off printed text, but ten of
the fifteen rules want a distance that is dimensioned between drawn features on a
scanned sheet. docs/DECISIONS.md records that neither Textract nor Bedrock Data
Automation returns those, because they are spatial relationships between line
segments relative to a scale bar, not printed text. This module measures them.

What it will and will not claim
------------------------------

Of the seven parameters named as missing, exactly one can be measured today:

    dist_tank_to_well        septic_tank to well, both of which are detected

    dist_disposal_to_well            needs a disposal area class
    dist_disposal_to_watercourse     needs a disposal area and a watercourse
    dist_disposal_to_property_line   needs a disposal area and the lot boundary
    dist_disposal_to_escarpment      needs a disposal area and a top of bank
    dist_tank_to_watercourse         needs a watercourse
    disposal_slope                   needs contour lines, not a distance at all

The detector recognises septic_tank, distribution_box and well, and nothing else.
Deriving a disposal area distance from the tank would be inventing a measurement
between the wrong two objects, and the sand mound on a real sheet sits a long way
from the tank, so the answer would be wrong rather than approximate. The six stay
unmeasured and their rules keep returning UNKNOWN, which is the honest result.

Why a measurement is withheld near its threshold
------------------------------------------------

Symbol centres are model estimates. Scored against the verified truth file on
dnrec_53, the mean centre error is 14.1 px, about 13.7 ft at that sheet's scale,
and the tank to well distance specifically came out 92.3 ft against a true 74.8,
an error of 17.5 ft. A distance carrying that much error cannot settle a rule whose
threshold it sits close to: the same reading could be a PASS or a FAIL, and
asserting either would put a wrong finding in front of a regulator.

So a value is only emitted when it is clear of the rule's threshold by more than
the uncertainty. Inside that band the fact is withheld, the rule returns UNKNOWN,
and the report says the drawing was measured but could not settle the check. That
is what the third verdict is for.

On dnrec_53 this resolves cleanly in one direction: the tank to well rule has a
threshold of 50 ft, and a reading of 92 ft with a 20 ft allowance is comfortably
clear of it, so the check can be decided from the drawing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..ingest.extract import Fact

# Rule parameter -> the two symbol classes whose separation is that parameter.
MEASURABLE: dict[str, tuple[str, str]] = {
    "dist_tank_to_well": ("septic_tank", "well"),
}

# Parameters a setback rule wants that this cannot supply, and what each needs
# before it could. Carried so the report can say why a check stayed UNKNOWN
# rather than leaving it looking like an oversight.
UNMEASURABLE: dict[str, str] = {
    "dist_disposal_to_well":
        "no disposal area is detected, and the tank is not a stand-in for it",
    "dist_disposal_to_watercourse":
        "no disposal area and no watercourse are detected",
    "dist_disposal_to_property_line":
        "no disposal area is detected and the lot boundary is not traced",
    "dist_disposal_to_escarpment":
        "no disposal area and no top of bank are detected",
    "dist_tank_to_watercourse":
        "no watercourse is detected",
    "disposal_slope":
        "slope needs contour lines read off the drawing, not a distance",
}

# Allowance on a measured distance, as a fraction of the image diagonal rather
# than a number of feet.
#
# Feet was wrong and wrong in a way that hides. The detector's centre error is a
# property of the image it looked at, not of the ground: vision_truth reports it as
# 0.92 percent of the diagonal on dnrec_53, and locate.py sizes its own refinement
# window with ERROR_ALLOWANCE_FRACTION because the error scales with the region
# viewed. Feet only enters when the scale converts it.
#
# A fixed 20 ft was therefore only correct for sheets near 0.97 ft/px. On a 1 inch
# to 30 feet sheet the real allowance is about 4 ft, so readings that could settle
# a check would have been withheld. On a 1 inch to 400 feet sheet it is about 55 ft,
# so readings that could not settle anything would have been asserted. The second
# is the dangerous direction.
MEAN_ERROR_FRACTION_OF_DIAGONAL = 0.0092

# A separation inherits the error of both endpoints. Two independent centre errors
# combine to about sqrt(2) times one of them, which the one measured pair supports:
# mean centre error 13.7 ft against a tank to well error of 17.5 ft is a ratio of
# 1.28 where sqrt(2) is 1.41. Used as the multiplier rather than doubling, which
# would be the worst case of both errors pointing along the line between them.
PAIR_ERROR_MULTIPLIER = 1.414


def uncertainty_feet(image_width: int, image_height: int,
                     feet_per_pixel: float) -> float:
    """Allowance on a distance measured between two symbols, in feet.

    Derived from the image it was measured on and that sheet's own scale, so it
    follows a change of render resolution or drawing scale instead of being
    calibrated to the one sheet the figure came from. On dnrec_53 this returns
    19.4 ft, which is the 20 ft the constant used to hardcode, so the sheet it was
    fitted to still agrees with it.
    """
    diagonal = math.hypot(image_width, image_height)
    error_px = MEAN_ERROR_FRACTION_OF_DIAGONAL * diagonal * PAIR_ERROR_MULTIPLIER
    return error_px * feet_per_pixel


@dataclass
class Measurement:
    """One distance measured between two drawn symbols."""

    parameter: str
    value_feet: float
    from_id: str
    to_id: str
    pixels: float
    feet_per_pixel: float
    page: int
    uncertainty_feet: float = 0.0
    emitted: bool = True
    withheld_because: str | None = None

    def to_json(self) -> dict:
        return {
            "parameter": self.parameter,
            "value_feet": round(self.value_feet, 1),
            "uncertainty_feet": self.uncertainty_feet,
            "from": self.from_id,
            "to": self.to_id,
            "pixels": round(self.pixels, 1),
            "feet_per_pixel": round(self.feet_per_pixel, 6),
            "page": self.page,
            "emitted": self.emitted,
            "withheld_because": self.withheld_because,
        }

    def note(self) -> str:
        return (
            f"measured on the site plan, page {self.page}, from {self.from_id} to "
            f"{self.to_id}: {self.pixels:.0f} px at {self.feet_per_pixel:.4f} ft/px, "
            f"plus or minus {self.uncertainty_feet:.0f} ft"
        )


@dataclass
class SitePlanFacts:
    """Facts measured off one sheet, with provenance and what was refused."""

    page: int
    feet_per_pixel: float
    facts: dict[str, float] = field(default_factory=dict)
    provenance: dict[str, Fact] = field(default_factory=dict)
    measurements: list[Measurement] = field(default_factory=list)
    unmeasurable: dict[str, str] = field(default_factory=lambda: dict(UNMEASURABLE))
    warnings: list[str] = field(default_factory=list)
    annotated_png: Path | None = None

    def to_json(self) -> dict:
        return {
            "page": self.page,
            "feet_per_pixel": round(self.feet_per_pixel, 6),
            "facts": {k: round(v, 1) for k, v in self.facts.items()},
            "measurements": [m.to_json() for m in self.measurements],
            "unmeasurable": self.unmeasurable,
            "warnings": self.warnings,
            "annotated_png": str(self.annotated_png) if self.annotated_png else None,
        }


def _threshold_for(parameter: str, rules) -> tuple[float | None, str | None]:
    """The threshold a parameter is compared against, and the rule id."""
    for rule in rules or []:
        if rule.parameter == parameter and rule.threshold is not None:
            try:
                return float(rule.threshold), rule.id
            except (TypeError, ValueError):
                return None, rule.id
    return None, None


def measure(located, scale, page: int = 1, rules=None) -> SitePlanFacts:
    """Measure the parameters the detected symbols can support.

    `rules` is used only to find the threshold a value would be compared against,
    so a reading too close to call can be withheld. Passing none measures
    everything and withholds nothing, which is the right behaviour for a tool that
    is showing the numbers rather than deciding with them.
    """
    fpp = scale.feet_per_pixel
    out = SitePlanFacts(page=page, feet_per_pixel=fpp)

    # Sized from this image and this sheet's scale, so a different render
    # resolution or a different drawing scale carries its own allowance.
    allowance = uncertainty_feet(
        located.image_width, located.image_height, fpp
    )
    # Two symbols closer together than the allowance are inside each other's
    # centre error, and their separation is noise rather than a short distance.
    floor = allowance

    sited = [s for s in located.locations()] if hasattr(located, "locations") \
        else list(located.symbols)
    by_label: dict[str, list] = {}
    for s in sited:
        by_label.setdefault(s.label, []).append(s)

    for parameter, (label_a, label_b) in MEASURABLE.items():
        a_list, b_list = by_label.get(label_a, []), by_label.get(label_b, [])
        if not a_list or not b_list:
            missing = label_a if not a_list else label_b
            out.unmeasurable[parameter] = (
                f"no {missing} was found on this sheet, so the separation cannot "
                f"be measured"
            )
            continue

        # The nearest pair, because a setback is against the closest instance.
        # With two wells on a sheet, compliance turns on the near one.
        best = None
        for a in a_list:
            for b in b_list:
                ax, ay = a.pixel.center
                bx, by = b.pixel.center
                px = math.dist((ax, ay), (bx, by))
                if best is None or px < best[0]:
                    best = (px, a, b)
        px, a, b = best
        feet = px * fpp

        m = Measurement(
            parameter=parameter, value_feet=feet, from_id=a.symbol_id,
            to_id=b.symbol_id, pixels=px, feet_per_pixel=fpp, page=page,
            uncertainty_feet=round(allowance, 1),
        )

        if feet < floor:
            m.emitted = False
            m.withheld_because = (
                f"{feet:.0f} ft is inside this sheet's {floor:.0f} ft allowance, so "
                f"the two symbols are within each other's centre error and the "
                f"separation is noise rather than a short distance"
            )
        else:
            threshold, rule_id = _threshold_for(parameter, rules)
            if threshold is not None and abs(feet - threshold) <= m.uncertainty_feet:
                m.emitted = False
                m.withheld_because = (
                    f"{feet:.0f} ft is within {m.uncertainty_feet:.0f} ft of the "
                    f"{threshold:.0f} ft threshold in {rule_id}, so the same reading "
                    f"could be a pass or a failure and it cannot settle the check"
                )

        out.measurements.append(m)
        if m.emitted:
            out.facts[parameter] = round(feet, 1)
            out.provenance[parameter] = Fact(
                name=parameter,
                value=round(feet, 1),
                source="site_plan_measurement",
                raw=f"{px:.1f} px",
                page=page,
                confidence=None,
                label=f"{a.symbol_id} to {b.symbol_id}",
                note=m.note(),
            )
            out.unmeasurable.pop(parameter, None)
        else:
            out.warnings.append(
                f"{parameter} was measured at {feet:.0f} ft on the site plan but not "
                f"used: {m.withheld_because}"
            )

    return out


def measure_pdf(pdf: Path, page: int, rules=None, offline: bool = True,
                render_scale: float = 3.0, annotate: bool = False) -> SitePlanFacts:
    """Render one page, read it, and measure what the symbols support.

    offline serves the symbol and scale results from cache and never constructs an
    AWS client, so a cached sheet measures with no credentials, which is the same
    guarantee the rest of the review path gives.
    """
    from . import overlay, styleguide
    from .locate import SymbolLocator
    from .scale import ScaleReader, ScaleUnavailable
    from .sheet import render_page

    image, dpi = render_page(pdf, page, render_scale)

    guide = styleguide.load()
    locator = SymbolLocator(guide=guide)
    reader = ScaleReader()

    if offline:
        located = locator.cached_for_image(image, refine=True)
        if located is None:
            raise ScaleUnavailable(
                f"no cached symbol result for page {page} of {Path(pdf).name}; run "
                f"scripts/vision_measure.py on it once with network access"
            )
        scale = reader.cached_for_image(image)
        if scale is None:
            raise ScaleUnavailable(
                f"no cached scale for page {page} of {Path(pdf).name}"
            )
    else:
        located = locator.locate(image, page=page, refine=True)
        scale = reader.read(image, dpi=dpi)

    out = measure(located, scale, page=page, rules=rules)
    if annotate:
        out.annotated_png = overlay.annotate(
            image, located, scale=scale,
            out_path=image.with_name(f"{image.stem}-annotated.png"), upscale=2.0,
        )
    return out


__all__ = [
    "Measurement", "SitePlanFacts", "measure", "measure_pdf",
    "MEASURABLE", "UNMEASURABLE", "uncertainty_feet",
    "MEAN_ERROR_FRACTION_OF_DIAGONAL", "PAIR_ERROR_MULTIPLIER",
]
