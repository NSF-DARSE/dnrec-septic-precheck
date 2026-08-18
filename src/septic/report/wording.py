"""How a check that did not run is worded, in one place for every surface.

The console banner, the text report and the HTML report all read from here, so
they cannot describe the same missing value in three different ways. That mattered
enough to be its own module: the reviewer sees the banner across a room and reads
the itemised list up close, and if those two disagree the tool looks like it is
guessing.

What changed and why. The old wording said a value could not be extracted from the
document. That is true and it stays true, but it describes the extractor's problem
rather than the reviewer's next task. A reviewer does not need to know that form
analysis reads form fields; they need to know which value is missing, where in the
packet it normally lives, and which section it has to be compared against once
they have read it off the drawing by hand.

What is deliberately unchanged is the honesty. Ten of the fifteen rules need a
value that is dimensioned on a scanned site plan, so on a typical packet a mean of
9.14 checks of 15 cannot be evaluated. Reporting those as UNKNOWN rather than as a
pass is the single most important safety property in this product. Nothing here
softens that, defaults a value, or lets a check read as satisfied. It only changes
who the sentence is addressed to.

PARAMETER_LOCATION is the whole of the new content: for each fact the rules ask
for, the name a reviewer would use for it and where it is normally found. The fact
names are the ones in septic.ingest.extract.FACTS, and tests/test_theme.py asserts
every parameter any rule needs has an entry, so a rule added without one fails the
suite rather than falling back to a machine name on screen.
"""
from __future__ import annotations

# name a reviewer would use, and where the packet normally carries it.
PARAMETER_LOCATION: dict[str, tuple[str, str]] = {
    "dist_disposal_to_well": (
        "Isolation distance from the disposal area to the nearest well",
        "dimensioned on the site plan, from the edge of the disposal area to the "
        "well symbol",
    ),
    "dist_disposal_to_watercourse": (
        "Isolation distance from the disposal area to the nearest watercourse",
        "dimensioned on the site plan, from the disposal area to the ditch, "
        "stream, pond or tidal water drawn on it",
    ),
    "dist_disposal_to_property_line": (
        "Isolation distance from the disposal area to the nearest property line",
        "dimensioned on the site plan against the surveyed lot line",
    ),
    "dist_disposal_to_escarpment": (
        "Isolation distance from the disposal area to the top of a bank or "
        "escarpment",
        "dimensioned on the site plan where the contour lines close up at the top "
        "of a bank",
    ),
    "dist_tank_to_well": (
        "Isolation distance from the septic tank to the nearest well",
        "dimensioned on the site plan, from the tank symbol to the well symbol",
    ),
    "dist_tank_to_watercourse": (
        "Isolation distance from the septic tank to the nearest watercourse",
        "dimensioned on the site plan, from the tank symbol to the water drawn "
        "on it",
    ),
    "perc_rate": (
        "Site average percolation rate",
        "written on the percolation test sheet and repeated in the site "
        "evaluation report",
    ),
    "perc_test_holes": (
        "Number of percolation test holes",
        "counted on the percolation test sheet, and marked as test hole symbols "
        "on the site plan",
    ),
    "limiting_zone_depth": (
        "Depth from the surface to the limiting zone",
        "recorded in the soil evaluation log, and noted beside each boring on "
        "the site plan",
    ),
    "limiting_zone_below_trench_bottom": (
        "Depth of soil between the trench bottom and the limiting zone",
        "taken off the trench cross section detail on the site plan, where the "
        "invert elevation and the limiting zone elevation are both called out",
    ),
    "design_flow": (
        "Design flow",
        "stated on the application form and repeated in the design calculations",
    ),
    "design_flow_per_bedroom": (
        "Design flow allowed for each bedroom",
        "worked out from the design flow and the bedroom count on the "
        "application form",
    ),
    "disposal_slope": (
        "Slope across the disposal area",
        "read off the contour lines on the site plan, or stated in the site "
        "evaluation report",
    ),
    "site_evaluation_report": (
        "Site evaluation report",
        "a separate signed sheet in the packet, referenced by date on the "
        "application form",
    ),
    "wells_within_150_feet_shown": (
        "Marking of any well within 150 feet of the disposal area",
        "drawn on the site plan with a dimension to each one",
    ),
    "bedrooms": (
        "Number of bedrooms",
        "stated on the application form as the type of structure",
    ),
    # The facts that decide whether a rule applies at all. A reviewer needs these
    # named too, because an unread one takes a rule out of the count without
    # anything failing.
    "system_scale": (
        "Design flow category, small or large",
        "worked out from the design flow on the application form, small below "
        "2500 gallons per day",
    ),
    "system_type": (
        "System type",
        "the ticked box in the system type list on the construction permit "
        "application",
    ),
    "construction_type": (
        "Nature of the work, new construction, replacement or repair",
        "the ticked box on the construction permit application",
    ),
    "use_type": (
        "Property use, residential or commercial",
        "stated on the application form as the type of structure",
    ),
    "absorption_type": (
        "Absorption facility type, trench or bed",
        "the ticked box on the construction permit application, and labelled on "
        "the site plan",
    ),
}

# The heading and the standing explanation for the group. Both surfaces use these
# verbatim, which is what stops the console and the report wording it differently.
# No apostrophe in either of them on purpose: the HTML renderer escapes quotes, so
# a contraction would make the two surfaces carry different bytes for what is
# meant to be one sentence, and the test that they agree could not be written.
UNREAD_HEADING = "Could not be evaluated"

UNREAD_INTRO = (
    "These checks did not run, and a check that did not run is not a check that "
    "passed. Each one below names the value it needs, where the packet normally "
    "carries it, and the section it has to be compared against, because reading "
    "it off the drawing is now a task for the reviewer rather than for the tool."
)

# What the console banner says, which is not the same job as the paragraph above.
# The banner is two short lines read across a room and the report body sits a few
# pixels underneath it, so printing UNREAD_INTRO in both put the same fifty words
# on screen twice and read as the tool repeating itself. The banner points at the
# list, the list does the explaining.
UNREAD_BANNER = (
    "The checks that could not be read are listed below, each naming the value to "
    "read and where the packet carries it."
)

# The same job for the rules that never governed this packet. One sentence, for
# the same reason.
NOT_APPLICABLE_BANNER = (
    "The checks that do not govern this kind of system are listed below, and are "
    "not requirements this packet met."
)


def parameter_name(parameter: str) -> str:
    """What a reviewer calls this value. Falls back to the fact name."""
    entry = PARAMETER_LOCATION.get(parameter)
    return entry[0] if entry else (parameter or "This value")


def parameter_location(parameter: str) -> str | None:
    """Where the packet normally carries this value, or None if unrecorded."""
    entry = PARAMETER_LOCATION.get(parameter)
    return entry[1] if entry else None


def unread_note(finding: dict) -> str:
    """One sentence group for a check that did not run.

    Names the value, says where it is normally found, and cites the section that
    needs it. Takes a composed finding as JSON so every surface passes the same
    thing and nothing has to be re-derived per surface.

    There are three reasons a check does not run and they are three different
    instructions to a reviewer, so they are worded separately. A value that could
    not be read is theirs to read. A threshold nobody has confirmed against the
    regulation is not theirs at all and must not look like a packet problem. A
    rule whose applicability could not be settled is neither: nothing is known
    yet about whether it even governs this system.
    """
    citation = finding.get("citation") or ""
    against = f" against {citation}" if citation else ""
    parameter = finding.get("parameter") or ""

    if finding.get("verified") is False:
        cited = citation or "the section it comes from"
        return (
            f"{parameter_name(parameter)} was not compared. The threshold in "
            f"{cited} has not been confirmed against the regulation by a person, "
            f"so this check does not run at all and nothing about this packet "
            f"caused that. Confirm the cited page before relying on it."
        )

    if finding.get("applicability") == "undetermined":
        gate = (finding.get("excluded_by") or {}).get("parameter") or parameter
        location = parameter_location(gate)
        where = f" It is normally {location}." if location else ""
        return (
            f"Whether this rule applies was not established, because "
            f"{parameter_name(gate).lower()} was not machine readable.{where} "
            f"Read it there, then decide whether {citation or 'the rule'} governs "
            f"this system."
        )

    location = parameter_location(parameter)
    if location:
        return (
            f"{parameter_name(parameter)} was not machine readable. It is "
            f"normally {location}. Read it there and compare it{against}."
        )
    reason = (finding.get("reason") or "").strip().rstrip(".")
    return (
        f"{parameter_name(parameter)} was not machine readable, so this check did "
        f"not run. Read it off the packet and compare it{against}."
        + (f" Recorded as: {reason}." if reason else "")
    )
