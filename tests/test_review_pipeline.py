"""Tests for the application to report path.

The property that matters most here is that the network cannot change an answer.
A report composed with no credentials, no cache warmth and no Bedrocklaims the
same verdict, the same findings and the same citations as one composed with all
three. Several tests below exist only to hold that line.
"""
import json
from pathlib import Path

import pytest

from septic.ingest import layout
from septic.ingest.extract import FACTS, Fact, _label_score, extract_facts
from septic.report import compose as compose_mod
from septic.report import render as render_mod
from septic.rules import engine
from septic.rules.schema import Citation, Operator, Rule, Severity, Verdict


# ---------------------------------------------------------------------------
# Synthetic Textract blocks, so these tests need no AWS and no fixture files.
# ---------------------------------------------------------------------------

def _kv_blocks(pairs, page=1, start=100):
    """Build KEY_VALUE_SET blocks for a list of (key, value) pairs."""
    blocks = []
    n = start
    for key, value in pairs:
        key_id, value_id = f"k{n}", f"v{n}"
        kw_id, vw_id = f"kw{n}", f"vw{n}"
        geometry = {"BoundingBox": {"Left": 0.1, "Top": 0.1 + n / 1000,
                                    "Width": 0.2, "Height": 0.02}}
        blocks.append({
            "Id": key_id, "BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"],
            "Confidence": 99.0, "Page": page, "Geometry": geometry,
            "Relationships": [
                {"Type": "CHILD", "Ids": [kw_id]},
                {"Type": "VALUE", "Ids": [value_id]},
            ],
        })
        blocks.append({
            "Id": kw_id, "BlockType": "WORD", "Text": key,
            "Confidence": 99.0, "Page": page, "Geometry": geometry,
        })
        blocks.append({
            "Id": value_id, "BlockType": "KEY_VALUE_SET",
            "EntityTypes": ["VALUE"], "Confidence": 99.0, "Page": page,
            "Geometry": geometry,
            "Relationships": [{"Type": "CHILD", "Ids": [vw_id]}],
        })
        blocks.append({
            "Id": vw_id, "BlockType": "WORD", "Text": value,
            "Confidence": 99.0, "Page": page, "Geometry": geometry,
        })
        n += 1
    return blocks


def _line_blocks(texts, page=1, start=500):
    blocks = []
    for i, text in enumerate(texts):
        blocks.append({
            "Id": f"l{start + i}", "BlockType": "LINE", "Text": text,
            "Confidence": 99.0, "Page": page,
            "Geometry": {"BoundingBox": {"Left": 0.1, "Top": 0.1 + i / 100,
                                         "Width": 0.6, "Height": 0.02}},
        })
    return blocks


@pytest.fixture
def clean_document():
    """A packet whose fields are unambiguous."""
    blocks = _kv_blocks([
        ("Avg. Percolation Rate", "45"),
        ("Gallons Per Day Flow", "480"),
        ("Number of Bedrooms", "4"),
        ("Site Evaluation Number", "SE-2026-1183"),
    ])
    blocks += _line_blocks(["Full Depth Gravity system", "single family dwelling"],
                           page=1)
    return layout.parse_blocks(blocks)


# ---------------------------------------------------------------------------
# The label matching bug that fabricated nine values.
# ---------------------------------------------------------------------------

class TestLabelMatching:
    """Regression tests for a matcher that invented data.

    The original implementation tested raw substrings in both directions, so the
    OCR label "N" matched "distance to well" because the letter n occurs inside
    the word distance. On one real 19 page packet a single stray character
    matched nine separate facts and gave them all the same value. With a verified
    rule set that would have shown a reviewer nine fabricated measurements.
    """

    def test_single_letter_label_matches_nothing(self):
        for name, spec in FACTS.items():
            labels = spec.get("labels") or []
            if not labels:
                continue
            assert _label_score("N", labels) == 0, (
                f"single character label matched {name}"
            )

    @pytest.mark.parametrize("noise", ["N", "S", "E", "W", "X", "RPM", "A", "1"])
    def test_ocr_noise_labels_match_nothing(self, noise):
        matched = [
            name for name, spec in FACTS.items()
            if spec.get("labels") and _label_score(noise, spec["labels"])
        ]
        assert not matched, f"label {noise!r} matched {matched}"

    def test_weak_single_token_does_not_claim_a_fact(self):
        """A drawing scale is not a system scale."""
        assert _label_score("SCALE", FACTS["system_scale"]["labels"]) == 0
        assert _label_score("SITE", FACTS["disposal_slope"]["labels"]) == 0

    def test_real_labels_still_match(self):
        assert _label_score("Avg. Percolation Rate", FACTS["perc_rate"]["labels"])
        assert _label_score("Gallons Per Day Flow", FACTS["design_flow"]["labels"])
        assert _label_score(
            "Number of Bedrooms", FACTS["bedrooms"]["labels"]
        )
        assert _label_score(
            "Site Evaluation Number", FACTS["site_evaluation_report"]["labels"]
        )

    def test_limiting_zone_label_does_not_claim_the_separation_distance(self):
        """Two different measurements must not share one reading.

        A real packet carries a field marked "LIMITING ZONE = 30", which is the
        depth from the surface. An earlier matcher also handed that 30 to
        limiting_zone_below_trench_bottom, which is the separation between the
        trench bottom and the limiting zone. Those are different quantities, and
        the packet stated only one of them, so the rule comparing the other one
        would have produced a confident and wrong deficiency.
        """
        depth = _label_score("LIMITING ZONE =", FACTS["limiting_zone_depth"]["labels"])
        separation = _label_score(
            "LIMITING ZONE =", FACTS["limiting_zone_below_trench_bottom"]["labels"]
        )
        assert depth > 0, "the depth fact should still match its own field"
        assert separation == 0, (
            "a limiting zone depth reading must not fill in the separation distance"
        )

    def test_noise_label_does_not_populate_facts(self, ):
        """End to end version of the bug: one junk field, no facts."""
        document = layout.parse_blocks(_kv_blocks([("N", "4")] * 3))
        extraction = extract_facts(document)
        assert extraction.facts == {}, (
            f"a single junk field produced facts: {extraction.facts}"
        )


class TestLabelsFromTheSurveyedCorpus:
    """Every label below produced a false deficiency on a real DNREC packet.

    A survey of 145 approved packets returned 63 with DEFICIENCIES FOUND. All of
    them were reading errors, and each one came from a form field label that
    Textract had paired with whatever text sat nearest it. These are the exact
    OCR strings, with the exact values that were read from them. They are approved
    permits, so a reviewer opening the cited page would not agree with any of
    these findings, which is the standard the matcher has to meet.
    """

    WELL_LABELS_THAT_ARE_NOT_DISTANCES = [
        # label, the value that was read as a well setback in feet
        ("A copy of this page must be submitted with both septic system and "
         "well construction report(s)", "8 of 20"),
        ("A copy of this page must be submitted with both the septic system and "
         "well contruction report(s).", "7 of 11"),
        ("PROP WELL", "60 PROP 4 BEDROOM DWELLING"),
        ("PROPOSED WELL", "34'"),
        ("PROPOSED WELL-", "LPP 50'+ TO TANKS"),
        ("EXISTING WELL (TO BE ABANDONED)", "1-2%"),
        ("Existing Well", "60"),
        ("W PROPOSED WELL PROPOSED DRIVEWA", ",08 54.9' 422.2' 229.9'"),
        ("WELL ARC", "2 BEDROOM HOUSE"),
        ("Abandonment date for old well", "Aug 23"),
        ("Desired capacity of the well", "6 gpm"),
        ("Water Supply Well", "The property is served by a 2-inch well."),
        ("DWELLING (WELL >100')", "20.0'"),
        ("AT OUTLET AND ACCESS RISERS AT INLET AND OUTLET 50' MIN TO WELL 10' "
         "MIN TO HOME & PROPERTY LINES", "PROPOSED WELL TO LPP 50'+ TO TANKS"),
    ]

    @pytest.mark.parametrize("label,value", WELL_LABELS_THAT_ARE_NOT_DISTANCES)
    def test_a_well_on_the_page_is_not_a_distance_to_a_well(self, label, value):
        assert _label_score(
            label, FACTS["dist_disposal_to_well"]["labels"], distance_sense=True
        ) == 0, f"{label!r} claimed a well setback"

    @pytest.mark.parametrize("label,value", WELL_LABELS_THAT_ARE_NOT_DISTANCES)
    def test_none_of_them_reaches_the_facts(self, label, value):
        extraction = extract_facts(layout.parse_blocks(_kv_blocks([(label, value)])))
        assert "dist_disposal_to_well" not in extraction.facts, (
            f"{label!r} = {value!r} produced "
            f"{extraction.facts.get('dist_disposal_to_well')}"
        )

    def test_property_line_labels_that_are_not_setbacks(self):
        labels = FACTS["dist_disposal_to_property_line"]["labels"]
        for label in ("Property line abandoned",
                      "9 AMOUNT OF AREA AFFECTED BY THE LOT LINE ADJUSTMENT"):
            assert _label_score(label, labels, distance_sense=True) == 0, label

    def test_a_conditions_block_is_not_a_field_label(self):
        """The label that supplied 5.3 feet to a watercourse.

        The value it was paired with contained the words "Section 5.3.31 of the
        Regulations", and the regulation's own section number became a setback.
        """
        conditions = (
            "Conditions for Owner 17 The property owner shall connect to the "
            "county or municipal sewer system within one year of the date that "
            "such services become available and shall abandon the on-site system "
            "in accordance with the Regulations"
        )
        assert _label_score(
            conditions, FACTS["dist_disposal_to_watercourse"]["labels"],
            distance_sense=True,
        ) == 0
        extraction = extract_facts(layout.parse_blocks(_kv_blocks([
            (conditions,
             "accordance with Section 5.3.31 of the Regulations On-Site "
             "Wastewater Treatment and Disposal submitted by your designer"),
        ])))
        assert "dist_disposal_to_watercourse" not in extraction.facts

    def test_a_page_of_prose_cannot_be_a_limiting_zone_depth(self):
        """The reading that appeared on 37 packets at once.

        The label is legitimate, the value is not: it is the soil scientist's
        descriptive paragraph, which carries five numbers. Which one is the depth
        was never established, so there is no value here to read.
        """
        value = (
            "12 (no deeper at average elevations, but variable with increasing "
            "elevation to 14) inches to prolonged (7 to 14 continuous days in "
            "> 5 years in a 10 year cycle) indications of seasonal high water "
            "table"
        )
        extraction = extract_facts(layout.parse_blocks(
            _kv_blocks([("Limiting Zone Depth(s):", value)])
        ))
        assert "limiting_zone_depth" not in extraction.facts
        assert any(
            r["parameter"] == "limiting_zone_depth" for r in extraction.rejected
        ), extraction.rejected

    def test_a_depth_in_feet_is_not_read_as_inches(self):
        """LIMITING ZONE = 5' is 60 inches, and reading it as 5 fails the rule.

        Rejected rather than converted: the value that carried this on a real
        packet was "5' in 3'", two numbers in feet on a trench cross section, and
        guessing which one is the depth is how a confident wrong deficiency gets
        made.
        """
        extraction = extract_facts(layout.parse_blocks(
            _kv_blocks([("LIMITING ZONE", "5' in 3'")])
        ))
        assert "limiting_zone_depth" not in extraction.facts

    def test_a_bedroom_count_answered_in_employees_is_not_a_bedroom_count(self):
        """The last false deficiency in the survey of 145 packets.

        A commercial packet answered the form's "# of Bedrooms" field with
        "9 Employees". Nine bedrooms against a design flow of 360 gallons per day
        derives 40 gallons per bedroom, which fails the 120 gallon requirement and
        would have been the one deficiency the survey reported.
        """
        extraction = extract_facts(layout.parse_blocks(
            _kv_blocks([("# of Bedrooms:", "9 Employees"),
                        ("Gallons Per Day Flow:", "360")])
        ))
        assert "bedrooms" not in extraction.facts
        assert "design_flow_per_bedroom" not in extraction.facts
        assert extraction.facts["design_flow"] == 360.0
        assert any(r["parameter"] == "bedrooms" for r in extraction.rejected)

    @pytest.mark.parametrize(
        "value", ["45 MPI", "40mpl", "45 PMI", "35 -", "30 MPI Assigned"]
    )
    def test_ocr_variants_of_the_percolation_unit_still_read(self, value):
        """Real values on the form, and what Textract makes of MPI.

        mpl and pmi are one character slips on the same unit, and a bare dash is
        the empty column beside the number. Three packets lost a real percolation
        rate to these before the unit check knew about them.
        """
        extraction = extract_facts(layout.parse_blocks(
            _kv_blocks([("Avg. Percolation Rate:", value)])
        ))
        assert "perc_rate" in extraction.facts, value
        assert extraction.facts["perc_rate"] in (45.0, 40.0, 35.0, 30.0)

    def test_a_percolation_rate_answered_not_applicable_is_not_read(self):
        extraction = extract_facts(layout.parse_blocks(
            _kv_blocks([("Avg. Percolation Rate:", "[X] N/A")])
        ))
        assert "perc_rate" not in extraction.facts

    def test_a_unit_the_fact_does_not_use_is_not_read(self):
        """Values that state the wrong dimension entirely."""
        cases = [
            ("Distance to Well", "6 gpm", "dist_disposal_to_well"),
            ("Distance to Property Line", "8.003 ACRES",
             "dist_disposal_to_property_line"),
        ]
        for label, value, parameter in cases:
            extraction = extract_facts(
                layout.parse_blocks(_kv_blocks([(label, value)]))
            )
            assert parameter not in extraction.facts, f"{label} = {value}"

    def test_the_real_labels_on_the_dnrec_application_still_read(self):
        """The other half of the deal. These are the fields that must survive.

        Taken from the construction permit application form and the site
        evaluation, as Textract reports them.
        """
        document = layout.parse_blocks(_kv_blocks([
            ("Avg. Percolation Rate:", "35 MPI Assigned"),
            ("Gallons Per Day Flow:", "840"),
            ("# of Bedrooms:", "7"),
            ("Site Evaluation Number", "SE-2026-1183"),
            ("Limiting Zone Depth(s):", "36 inches"),
        ]))
        extraction = extract_facts(document)
        assert extraction.facts["perc_rate"] == 35.0
        assert extraction.facts["design_flow"] == 840.0
        assert extraction.facts["bedrooms"] == 7.0
        assert extraction.facts["site_evaluation_report"] == "present"
        assert extraction.facts["limiting_zone_depth"] == 36.0

    def test_a_spelled_out_endpoint_pair_still_reads_as_a_distance(self):
        """A label that names both ends is a distance even without the word.

        This is the shape a site evaluation form uses, and it has to keep working,
        because it is the only route by which these six rules can ever run.
        """
        document = layout.parse_blocks(_kv_blocks([
            ("Disposal Area to Well", "125 ft"),
            ("Distance to Property Line", "22'"),
        ]))
        extraction = extract_facts(document)
        assert extraction.facts["dist_disposal_to_well"] == 125.0
        assert extraction.facts["dist_disposal_to_property_line"] == 22.0


class TestExtraction:
    def test_reads_labelled_fields(self, clean_document):
        extraction = extract_facts(clean_document)
        assert extraction.facts["perc_rate"] == 45.0
        assert extraction.facts["design_flow"] == 480.0
        assert extraction.facts["bedrooms"] == 4.0
        assert extraction.facts["site_evaluation_report"] == "present"

    def test_records_provenance_for_every_fact(self, clean_document):
        extraction = extract_facts(clean_document)
        for name in extraction.facts:
            assert name in extraction.provenance, f"{name} has no provenance"
            fact = extraction.provenance[name]
            assert fact.source in ("form_field", "text_pattern", "derived")
            assert fact.describe()

    def test_derives_flow_per_bedroom(self, clean_document):
        extraction = extract_facts(clean_document)
        assert extraction.facts["design_flow_per_bedroom"] == 120.0

    def test_derives_system_scale_from_flow(self, clean_document):
        extraction = extract_facts(clean_document)
        assert extraction.facts["system_scale"] == "small"

    def test_absent_is_not_zero(self, clean_document):
        """A value that could not be read must be absent, never defaulted."""
        extraction = extract_facts(clean_document)
        assert "dist_disposal_to_well" not in extraction.facts
        assert "dist_disposal_to_well" in extraction.missing

    def test_implausible_number_is_discarded_with_a_reason(self):
        document = layout.parse_blocks(
            _kv_blocks([("Avg. Percolation Rate", "99999")])
        )
        extraction = extract_facts(document)
        assert "perc_rate" not in extraction.facts
        assert any(r["parameter"] == "perc_rate" for r in extraction.rejected)

    def test_blank_field_is_recorded_not_silently_dropped(self):
        document = layout.parse_blocks(
            _kv_blocks([("Avg. Percolation Rate", "")])
        )
        extraction = extract_facts(document)
        assert "perc_rate" not in extraction.facts
        assert any(
            "blank" in r["reason"] for r in extraction.rejected
        ), extraction.rejected

    def test_scale_derivation_needs_a_flow(self):
        """Scale must not be guessed when the flow is unreadable.

        Guessing small would silently switch on every isolation distance rule for
        a packet nobody could measure.
        """
        document = layout.parse_blocks(_kv_blocks([("Number of Bedrooms", "3")]))
        extraction = extract_facts(document)
        assert "system_scale" not in extraction.facts


# ---------------------------------------------------------------------------
# The verdict comes from the rules and from nothing else.
# ---------------------------------------------------------------------------

def _verified_rule(**overrides):
    """A verified rule, built in the test only. Never shipped verified."""
    defaults = dict(
        id="TEST-well-setback",
        description="Disposal area must be at least 100 feet from a well.",
        citation=Citation(
            section="Exhibit C", page=173,
            quote="row Disposal area, column Well: 100",
        ),
        parameter="dist_disposal_to_well",
        operator=Operator.GE,
        threshold=100,
        units="feet",
        severity=Severity.RETURN,
        verified=True,
        remedy="Move the disposal area at least 100 feet from the well.",
        notes="test fixture",
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestVerdictProvenance:
    def test_verified_rule_produces_a_cited_deficiency(self):
        rules = [_verified_rule()]
        report = engine.evaluate({"dist_disposal_to_well": 40}, rules)
        composed = compose_mod.compose(report)
        assert composed.verdict == Verdict.DEFICIENCIES_FOUND.value
        assert composed.headline == "DEFICIENCIES FOUND"
        assert len(composed.deficiencies) == 1
        finding = composed.deficiencies[0]
        assert finding.section == "Exhibit C"
        assert finding.page == 173
        assert finding.quote
        assert finding.remedy

    def test_passing_rule_produces_no_deficiencies(self):
        rules = [_verified_rule()]
        report = engine.evaluate({"dist_disposal_to_well": 150}, rules)
        composed = compose_mod.compose(report)
        assert composed.verdict == Verdict.NO_DEFICIENCIES.value
        assert composed.headline == "NO DEFICIENCIES FOUND"
        assert not composed.deficiencies

    def test_unknown_is_never_folded_into_pass(self):
        """A single rule nobody could evaluate leaves the tool with no answer.

        This is the surviving case for CANNOT VERIFY: not "something could not be
        checked", which is true of every real packet, but "nothing could be".
        """
        rules = [_verified_rule()]
        report = engine.evaluate({}, rules)
        composed = compose_mod.compose(report)
        assert composed.verdict == Verdict.CANNOT_VERIFY.value
        assert composed.headline == "CANNOT VERIFY"
        assert len(composed.unresolved) == 1
        assert not composed.satisfied
        assert composed.coverage["text"] == "0 of 1 checks ran"

    def test_missing_parameter_is_reported_explicitly(self):
        rules = [_verified_rule()]
        report = engine.evaluate({}, rules)
        composed = compose_mod.compose(report)
        parameters = [m["parameter"] for m in composed.missing_information]
        assert "dist_disposal_to_well" in parameters

    def test_composition_does_not_change_the_rule_outcome(self):
        """Composition sorts and annotates. It must not reclassify."""
        rules = [
            _verified_rule(id="A", parameter="p_a"),
            _verified_rule(id="B", parameter="p_b"),
            _verified_rule(id="C", parameter="p_c"),
        ]
        facts = {"p_a": 10, "p_b": 500}
        report = engine.evaluate(facts, rules)
        composed = compose_mod.compose(report)
        assert composed.counts == report.counts()
        assert composed.coverage == report.coverage()
        assert len(composed.deficiencies) == len(report.failures)
        assert len(composed.satisfied) == len(report.passes)
        assert len(composed.unresolved) == len(report.unknowns)

    def test_retrieval_cannot_change_the_verdict(self):
        """A precedent list must not move the answer.

        Every retrieved neighbour is labelled approved here, which is the exact
        shape of the corpus, and the deficiency has to survive it.
        """
        rules = [_verified_rule()]
        report = engine.evaluate({"dist_disposal_to_well": 10}, rules)
        precedents = {
            "precedents": [
                {"detail_id": "1", "permit_number": "P1", "score": 0.99,
                 "summary": "permitStatus Approved", "status": "Approved",
                 "metadata": {"permitStatus": "Approved"}},
            ],
            "backend": "bedrock-titan-v2", "degraded": False, "index_size": 1,
            "caveat": "precedent only", "limits": "weak",
        }
        with_precedents = compose_mod.compose(report, precedents=precedents)
        without = compose_mod.compose(report)
        assert with_precedents.verdict == without.verdict
        assert with_precedents.headline == "DEFICIENCIES FOUND"
        assert len(with_precedents.deficiencies) == len(without.deficiencies)

    def test_degraded_embeddings_are_disclosed(self):
        rules = [_verified_rule()]
        report = engine.evaluate({"dist_disposal_to_well": 10}, rules)
        composed = compose_mod.compose(
            report,
            precedents={"precedents": [], "degraded": True, "backend": "local",
                        "index_size": 0, "caveat": "c", "limits": "l"},
        )
        assert any("offline stand-in" in n for n in composed.notices)


class TestNoNetworkNeeded:
    """Composition and rendering must not touch the network.

    Enforced by breaking the session factory: any attempt to build an AWS client
    raises. If a future change adds a call on this path, these fail.
    """

    @pytest.fixture(autouse=True)
    def forbid_aws(self, monkeypatch):
        from septic import config

        def explode(*args, **kwargs):
            raise AssertionError("this path must not construct an AWS client")

        monkeypatch.setattr(config, "session", explode)

    def test_full_composition_and_render_offline(self, clean_document):
        extraction = extract_facts(clean_document)
        report = engine.evaluate(extraction.facts)
        composed = compose_mod.compose(report, extraction=extraction)
        text = render_mod.render_text(composed)
        html = render_mod.render_html(composed)
        assert composed.verdict
        assert "APPLICATION REVIEW" in text
        assert "PRE-SUBMISSION" not in text
        assert "<!doctype html>" in html

    def test_shipped_rules_run_offline_against_a_real_packet(self, clean_document):
        """The rules have to be applied with no network, not merely loaded."""
        extraction = extract_facts(clean_document)
        report = engine.evaluate(extraction.facts)
        composed = compose_mod.compose(report, extraction=extraction)
        counts = composed.counts
        assert sum(counts.values()) == len(engine.load_rules())
        assert counts["pass"] + counts["fail"] > 0, (
            "no rule reached a decision on this packet, so nothing was applied"
        )


class TestRendering:
    @pytest.fixture
    def composed_with_deficiency(self):
        rules = [_verified_rule()]
        report = engine.evaluate({"dist_disposal_to_well": 40}, rules)
        return compose_mod.compose(report)

    def test_text_shows_citation_and_quote(self, composed_with_deficiency):
        text = render_mod.render_text(composed_with_deficiency)
        assert "Exhibit C" in text
        assert "page 173" in text
        assert "CITATION" in text
        assert "row Disposal area" in text

    def test_html_shows_citation_and_quote(self, composed_with_deficiency):
        html = render_mod.render_html(composed_with_deficiency)
        assert "Exhibit C, page 173" in html
        assert "row Disposal area" in html
        assert "DEFICIENCIES FOUND" in html

    def test_html_escapes_content(self):
        rules = [_verified_rule(
            remedy="Move it <script>alert(1)</script> further away",
        )]
        report = engine.evaluate({"dist_disposal_to_well": 1}, rules)
        html = render_mod.render_html(compose_mod.compose(report))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_needs_no_external_resource(self, composed_with_deficiency):
        """It has to open from a file on a laptop with no network."""
        html = render_mod.render_html(composed_with_deficiency)
        for pattern in ("http://", "https://", "<script"):
            assert pattern not in html, f"HTML references {pattern}"

    def test_every_finding_carries_a_citation(self):
        rules = [
            _verified_rule(id="A", parameter="p_a"),
            _verified_rule(id="B", parameter="p_b"),
        ]
        report = engine.evaluate({"p_a": 1}, rules)
        composed = compose_mod.compose(report)
        for group in (composed.deficiencies, composed.unresolved,
                      composed.satisfied):
            for finding in group:
                assert finding.citation
                assert finding.section

    def test_renderers_accept_json_dict(self, composed_with_deficiency):
        payload = composed_with_deficiency.to_json()
        payload = json.loads(json.dumps(payload))
        assert render_mod.render_text(payload)
        assert render_mod.render_html(payload)


class TestCoverageIsShownWithTheVerdict:
    """NO DEFICIENCIES FOUND is only honest next to how much was checked.

    The verdict no longer degrades when a check cannot run, which is the right
    answer to "is anything wrong" and a dangerous one to show alone. These tests
    hold the other half of the deal: every surface that prints the headline prints
    the coverage figure too, in the same words.
    """

    @pytest.fixture
    def partly_checked(self):
        """One rule passes, two cannot be evaluated. The realistic shape."""
        rules = [
            _verified_rule(id="A", parameter="p_a"),
            _verified_rule(id="B", parameter="p_b"),
            _verified_rule(id="C", parameter="p_c"),
        ]
        report = engine.evaluate({"p_a": 150}, rules)
        return compose_mod.compose(report)

    def test_the_verdict_is_no_deficiencies_on_partial_coverage(self, partly_checked):
        assert partly_checked.headline == "NO DEFICIENCIES FOUND"
        assert partly_checked.coverage["text"] == "1 of 3 checks ran"

    def test_text_report_puts_coverage_under_the_verdict(self, partly_checked):
        text = render_mod.render_text(partly_checked)
        lines = text.splitlines()
        verdict_line = next(i for i, l in enumerate(lines) if "VERDICT:" in l)
        assert "NO DEFICIENCIES FOUND" in lines[verdict_line]
        assert "1 OF 3 CHECKS RAN" in lines[verdict_line + 1], lines[verdict_line + 1]

    def test_html_report_puts_coverage_in_the_verdict_box(self, partly_checked):
        html = render_mod.render_html(partly_checked)
        box = html.split("class='verdict'")[1].split("</div>")[0]
        assert "NO DEFICIENCIES FOUND" in box
        assert "1 of 3 checks ran" in box

    def test_the_explanation_says_what_did_not_run(self, partly_checked):
        assert "2 could not be evaluated" in partly_checked.explanation
        assert "not a check that passed" in partly_checked.explanation

    def test_full_coverage_says_so_rather_than_going_quiet(self):
        rules = [_verified_rule(id="A", parameter="p_a")]
        composed = compose_mod.compose(engine.evaluate({"p_a": 150}, rules))
        assert composed.coverage["text"] == "1 of 1 checks ran"
        assert "1 of 1 checks ran" in render_mod.render_html(composed)
        assert "1 OF 1 CHECKS RAN" in render_mod.render_text(composed)
