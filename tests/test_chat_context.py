"""Context assembly: retrieval, prompt content, and the interlocks in wording.

The prompt is the only thing standing between a reviewer and a confidently
wrong regulatory number, so these tests assert on what it says as much as on
what retrieval returns.
"""
from __future__ import annotations

import json

import pytest

from septic.chat import context as ctx
from septic.rules import engine
from septic.rules.schema import Citation, Operator, Outcome, Rule, Severity, Verdict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


GRAPH = {
    "schema_version": 1,
    "nodes": [
        {"id": "section:5.3", "type": "Section", "number": "5.3",
         "title": "Design Criteria", "page": "50", "text": "General criteria."},
        {"id": "section:5.3.4", "type": "Section", "number": "5.3.4",
         "title": "Isolation Distances", "page": "57",
         "text": "The minimum isolation distances in Exhibit C shall be maintained."},
        {"id": "section:5.3.4.1", "type": "Section", "number": "5.3.4.1",
         "title": "Shellfish waters", "page": "57",
         "text": "Modified isolation distances apply near shellfish waters."},
        {"id": "section:5.3.4.2", "type": "Section", "number": "5.3.4.2",
         "title": "Greater distances", "page": "57",
         "text": "The Department may require greater distances."},
        {"id": "section:9.9", "type": "Section", "number": "9.9",
         "title": "Unrelated administrative matter", "page": "200",
         "text": "Filing procedures for annual reports."},
        {"id": "exhibit:C", "type": "Exhibit", "letter": "C",
         "title": "Minimum Isolation Distances", "page": "173",
         "text": "Disposal area to well 100 feet."},
        {"id": "definition:escarpment", "type": "Definition",
         "term": "Escarpment", "defined_in": "2.1",
         "text": "A naturally occurring slope greater than 30%."},
        {"id": "rule:ISO-001", "type": "Rule", "rule_id": "ISO-001-well",
         "description": "The disposal area must be at least 100 feet from a well.",
         "parameter": "dist_to_well", "operator": ">=", "severity": "return",
         "verified": "True", "citation_section": "Exhibit C",
         "citation_page": "173"},
    ],
    "edges": [
        {"source": "section:5.3", "target": "section:5.3.4", "type": "CONTAINS"},
        {"source": "section:5.3.4", "target": "section:5.3.4.1", "type": "CONTAINS"},
        {"source": "section:5.3.4", "target": "section:5.3.4.2", "type": "CONTAINS"},
    ],
}

PASSAGES = [
    {"section": "5.3.4", "page": 57,
     "quote": "The disposal area shall be at least 100 feet from any well.",
     "units": ["distance"], "numbers": ["100"],
     "obligation": True, "setback": True},
    {"section": "6.1", "page": 100,
     "quote": "Reports shall be filed within 30 days.",
     "units": ["time"], "numbers": ["30"],
     "obligation": True, "setback": False},
]


@pytest.fixture
def graph(tmp_path):
    path = tmp_path / "reg_graph.json"
    path.write_text(json.dumps(GRAPH), encoding="utf-8")
    return ctx.RegulationGraph(path)


@pytest.fixture
def passages():
    return ctx.CandidateIndex(passages=PASSAGES)


def make_rule(**kw) -> Rule:
    defaults = dict(
        id="R1",
        description="A requirement.",
        citation=Citation(section="5.3.4", page=57, quote="shall be maintained"),
        parameter="dist_to_well",
        operator=Operator.GE,
        threshold=100,
        units="feet",
        severity=Severity.RETURN,
        verified=True,
        remedy="Move the disposal area.",
    )
    defaults.update(kw)
    return Rule(**defaults)


def report_for(rules, facts) -> engine.Report:
    return engine.evaluate(facts, rules)


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------


class TestGraphLoading:
    def test_missing_file_is_a_normal_unavailable_state(self, tmp_path):
        g = ctx.RegulationGraph(tmp_path / "absent.json")
        assert g.available is False
        assert g.search("anything") == []
        assert g.find_sections_in_query("5.3.4") == []

    def test_loads_nodes_and_hierarchy(self, graph):
        assert graph.available is True
        assert graph.get_section("5.3.4")["title"] == "Isolation Distances"
        assert graph.get_parent("section:5.3.4")["number"] == "5.3"
        assert {c["number"] for c in graph.get_children("section:5.3.4")} == {
            "5.3.4.1", "5.3.4.2"
        }


# ---------------------------------------------------------------------------
# Section references
# ---------------------------------------------------------------------------


class TestSectionReferences:
    def test_finds_a_cited_section(self, graph):
        assert graph.find_sections_in_query(
            "What does Section 5.3.4 require?"
        ) == ["5.3.4"]

    def test_bare_numbers_are_not_section_references(self, graph):
        """Regression: "100 feet" must not resolve to a section."""
        assert graph.find_sections_in_query("is 100 feet enough") == []

    def test_unknown_section_numbers_are_dropped(self, graph):
        assert graph.find_sections_in_query("section 99.99.99") == []

    def test_section_context_includes_parent_and_children(self, graph):
        numbers = [n.get("number") for n in graph.get_section_context("5.3.4")]
        assert numbers == ["5.3", "5.3.4", "5.3.4.1", "5.3.4.2"]


# ---------------------------------------------------------------------------
# Keyword retrieval
# ---------------------------------------------------------------------------


class TestSearch:
    def test_natural_language_question_matches(self, graph):
        """Regression: requiring every term matched nothing for a real question."""
        hits = graph.search("isolation distances from shellfish waters")
        assert hits, "a full question should still retrieve sections"
        assert "5.3.4.1" in [h.get("number") for h in hits]

    def test_ranks_title_matches_above_body_only_matches(self, graph):
        hits = graph.search("isolation distances")
        assert hits[0].get("title") in {
            "Isolation Distances", "Minimum Isolation Distances"
        }

    def test_irrelevant_sections_are_excluded(self, graph):
        hits = graph.search("shellfish")
        assert "9.9" not in [h.get("number") for h in hits]

    def test_stopword_only_query_returns_nothing(self, graph):
        assert graph.search("what does it mean") == []

    def test_finds_definitions_by_term(self, graph):
        hits = graph.search("escarpment")
        assert any(h.get("type") == "Definition" for h in hits)

    def test_finds_rule_nodes_by_description(self, graph):
        hits = graph.search("disposal area well")
        assert any(h.get("type") == "Rule" for h in hits)

    def test_respects_the_limit(self, graph):
        assert len(graph.search("distances", limit=2)) <= 2


# ---------------------------------------------------------------------------
# Node rendering
# ---------------------------------------------------------------------------


class TestGraphFormatting:
    def test_each_node_type_gets_a_real_label(self, graph):
        """Regression: reading "number" off every type blanked three of four."""
        rendered = ctx.format_graph_context(graph.nodes)
        assert "Section 5.3.4" in rendered
        assert "Exhibit C" in rendered
        assert 'Definition of "Escarpment"' in rendered
        assert "Rule ISO-001-well" in rendered
        assert "Section ?" not in rendered

    def test_includes_page_numbers_for_citation(self, graph):
        rendered = ctx.format_graph_context([graph.get_section("5.3.4")])
        assert "p.57" in rendered

    def test_long_text_is_truncated(self, tmp_path):
        long_node = {"id": "s", "type": "Section", "number": "1.1",
                     "title": "Long", "page": "1", "text": "word " * 500}
        rendered = ctx.format_graph_context([long_node])
        assert "[...]" in rendered
        assert len(rendered) < 2000

    def test_empty_list_renders_nothing(self):
        assert ctx.format_graph_context([]) == ""


# ---------------------------------------------------------------------------
# Candidate passages
# ---------------------------------------------------------------------------


class TestCandidates:
    def test_absent_cache_is_an_empty_index(self, tmp_path):
        index = ctx.load_candidates(tmp_path / "absent.json")
        assert index.available is False
        assert index.search("well") == []

    def test_search_matches_on_the_quote(self, passages):
        hits = passages.search("distance from a well")
        assert hits
        assert "100 feet" in hits[0]["quote"]

    def test_setback_language_outranks_unrelated_obligations(self, passages):
        hits = passages.search("shall")
        assert hits[0]["setback"] is True

    def test_rendering_carries_the_unverified_warning(self, passages):
        """These are extractions. Presenting one as operative is the failure mode."""
        rendered = ctx.format_candidates_context(passages.search("well"))
        assert "NOT verified" in rendered
        assert "unconfirmed" in rendered

    def test_rendering_includes_section_and_page(self, passages):
        rendered = ctx.format_candidates_context(passages.search("well"))
        assert "Section 5.3.4" in rendered
        assert "p.57" in rendered


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------


class TestEvaluationContext:
    def test_states_the_verdict_and_counts(self):
        report = report_for([make_rule()], {"dist_to_well": "80 ft"})
        rendered = ctx.format_evaluation_context(report)
        assert Verdict.LIKELY_RETURN.value in rendered
        assert "fail 1" in rendered

    def test_a_failure_carries_reason_observed_threshold_and_fix(self):
        report = report_for([make_rule()], {"dist_to_well": "80 ft"})
        rendered = ctx.format_evaluation_context(report)
        assert "'80 ft'" in rendered
        assert ">= 100 feet" in rendered
        assert "Move the disposal area." in rendered
        assert "5.3.4, p.57" in rendered

    def test_unverified_rule_context_explains_why_it_is_unknown(self):
        """The three causes of UNKNOWN need different action from the applicant."""
        report = report_for([make_rule(verified=False)], {"dist_to_well": "80 ft"})
        rendered = ctx.format_evaluation_context(report)
        assert "Verified: NO" in rendered
        assert "regardless of the packet" in rendered

    def test_missing_value_is_reported_as_nothing_read(self):
        report = report_for([make_rule()], {})
        rendered = ctx.format_evaluation_context(report)
        assert "nothing was read" in rendered

    def test_empty_facts_are_marked_rather_than_shown_blank(self):
        report = report_for([make_rule()], {"dist_to_well": "", "lot_area": "1000"})
        rendered = ctx.format_evaluation_context(report)
        assert "(empty)" in rendered

    def test_declares_the_verdict_already_settled(self):
        """The model explains findings; it must not produce its own."""
        report = report_for([make_rule()], {"dist_to_well": "80 ft"})
        rendered = ctx.format_evaluation_context(report)
        assert "computed before you were called" in rendered


# ---------------------------------------------------------------------------
# Whole prompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_forbids_inventing_thresholds(self):
        prompt = ctx.build_system_prompt()
        assert "Never state a threshold that appears nowhere" in prompt

    def test_explains_all_three_causes_of_unknown(self):
        prompt = ctx.build_system_prompt()
        assert "unverified" in prompt
        assert "missing or unreadable" in prompt
        assert "would not parse as a number" in prompt

    def test_says_it_does_not_decide(self):
        prompt = ctx.build_system_prompt()
        assert "You do not make them." in prompt

    def test_without_a_packet_it_says_so(self):
        prompt = ctx.build_system_prompt(report=None)
        assert "No packet loaded" in prompt

    def test_unbuilt_graph_forbids_quoting_the_regulation(self):
        prompt = ctx.build_system_prompt(graph_available=False)
        assert "Do not quote or paraphrase the regulation" in prompt
        assert "septic graph build" in prompt


class TestGatherContext:
    def test_named_section_is_retrieved_with_its_hierarchy(self, graph, passages):
        prompt = ctx.gather_context_for_query(
            "What does Section 5.3.4 require?", graph=graph, candidates=passages
        )
        assert "Section 5.3.4" in prompt
        assert "Section 5.3.4.1" in prompt
        assert "Section 5.3" in prompt

    def test_general_question_retrieves_without_a_packet(self, graph, passages):
        prompt = ctx.gather_context_for_query(
            "isolation distances from shellfish waters",
            graph=graph,
            candidates=passages,
        )
        assert "No packet loaded" in prompt
        assert "Shellfish waters" in prompt

    def test_packet_and_regulation_appear_together(self, graph, passages):
        report = report_for([make_rule()], {"dist_to_well": "80 ft"})
        prompt = ctx.gather_context_for_query(
            "why is the well setback failing?",
            report=report,
            graph=graph,
            candidates=passages,
        )
        assert "Findings for the packet on screen" in prompt
        assert "Regulation text" in prompt
        assert "Candidate passages" in prompt

    def test_sections_are_not_duplicated(self, graph, passages):
        prompt = ctx.gather_context_for_query(
            "Section 5.3.4 isolation distances", graph=graph, candidates=passages
        )
        assert prompt.count("**Section 5.3.4,") == 1

    def test_prompt_stays_within_a_sane_size(self, graph, passages):
        prompt = ctx.gather_context_for_query(
            "isolation distance well shellfish waters disposal area",
            graph=graph,
            candidates=passages,
        )
        assert len(prompt) < 40_000
