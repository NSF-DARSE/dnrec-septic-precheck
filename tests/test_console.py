"""Tests for the reviewer console's data path.

Streamlit's own rendering is not tested here. What is tested is everything the
console depends on: that a cached packet reviews with no network, that the
rendered body is the shared renderer's output rather than a second
implementation, and that an uncached upload is detectable so the console can
explain rather than hang.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from septic import config  # noqa: E402
from septic.ingest import layout  # noqa: E402
from septic.ingest.extract import extract_facts  # noqa: E402
from septic.ingest.textract import TextractClient, document_hash  # noqa: E402
from septic.report import compose as compose_mod  # noqa: E402
from septic.report.render import VERDICT_COLOR, render_html  # noqa: E402
from septic.rules import engine  # noqa: E402


def cached_examples():
    examples_dir = config.OUT_DIR / "examples"
    if not examples_dir.exists():
        return []
    client = TextractClient()
    out = []
    for pdf in sorted(examples_dir.glob("*.pdf")):
        if client.cached_by_hash(document_hash(pdf.read_bytes())) is not None:
            out.append(pdf)
    return out


class TestConsoleModule:
    def test_app_file_exists_at_repo_root(self):
        """streamlit run app.py is the documented command."""
        assert (ROOT / "app.py").exists()

    def test_app_reuses_the_shared_pipeline(self):
        """The console must not carry a second copy of the review chain.

        It originally imported the shared renderer but rebuilt the stages feeding
        it, and silently omitted the location screening, so the map never reached
        the screen while the command line report carried it. Delegating to
        septic.review makes that class of drift impossible: there is one chain.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "from septic import review as review_mod" in source
        assert "review_mod.review(" in source
        assert "components.html" in source
        assert "engine.evaluate(" not in source, (
            "the console is evaluating rules itself instead of delegating"
        )

    def test_app_references_no_remote_resource(self):
        """Venue wifi will fail. Nothing may be fetched at render time."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for pattern in ("http://", "https://", "fonts.googleapis", "cdn."):
            assert pattern not in source, f"app.py references {pattern}"

    def test_app_is_not_a_chat_interface(self):
        """The product claim is that rules decide. A chat UI contradicts it."""
        source = (ROOT / "app.py").read_text(encoding="utf-8").lower()
        for banned in ("chat_input", "chat_message", "st.chat"):
            assert banned not in source, f"app.py uses {banned}"

    def test_console_banner_shows_coverage_with_the_verdict(self):
        """The screen may not show a headline without saying how much ran.

        The console is the surface a reviewer sees first and from furthest away.
        It reads both numbers out of the composed payload, so it cannot disagree
        with the report body embedded underneath it.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "def banner(payload: dict) -> str:" in source
        assert "st.markdown(banner(payload), unsafe_allow_html=True)" in source
        banner_body = source.split("def banner(payload: dict) -> str:")[1].split(
            "\ndef "
        )[0]
        assert 'payload.get("headline"' in banner_body
        assert 'payload.get("coverage")' in banner_body
        assert "banner-coverage" in banner_body

    def test_console_banner_reads_the_same_coverage_the_report_shows(self):
        """One number, produced by the rules, positioned twice.

        Guards the drift the module docstring warns about: if the banner ever
        computed coverage itself it could disagree with the report body a few
        pixels below it.

        The string pinned here is now the three way figure, because that is the
        one a reviewer reads on a real packet: a check that compared a value, a
        rule that does not govern this system, and a value that could not be read
        are three different things and the banner has to say which is which. The
        single rule case still reads "1 of 1 checks ran", since a count of zero is
        left out of the phrasing rather than printed as a zero.
        """
        from septic.rules.schema import Citation, Operator, Rule, Severity

        def make(rule_id, parameter, **overrides):
            defaults = dict(
                id=rule_id, description="d",
                citation=Citation(section="TEST-0.0", page=1, quote="q"),
                parameter=parameter, operator=Operator.GE, threshold=1,
                units="feet", severity=Severity.RETURN, verified=True,
                remedy="r", notes="n",
            )
            defaults.update(overrides)
            return Rule(**defaults)

        simple = compose_mod.compose(
            engine.evaluate({"p": 5}, [make("T", "p")])
        ).to_json()
        assert simple["coverage"]["text"] == "1 of 1 checks ran"
        assert simple["coverage"]["text"] in render_html(simple)

        rules = [
            make("RAN", "p_ran"),
            make("OUT", "p_out", applies_to={"system_type": "mound"}),
            make("UNREAD", "p_unread"),
        ]
        payload = compose_mod.compose(
            engine.evaluate({"p_ran": 5, "system_type": "gravity"}, rules)
        ).to_json()
        assert payload["coverage"]["text"] == (
            "1 of 3 checks ran, 1 not applicable to this system, "
            "1 could not be read"
        )
        html = render_html(payload)
        assert payload["coverage"]["text"] in html

    def test_the_banner_computes_no_coverage_number_of_its_own(self):
        """It may position the rules' numbers. It may never derive one.

        counts["pass"] includes the rules that were never applied, so any surface
        that adds up the outcome tally to describe coverage overstates what ran.
        The banner reads coverage["text"] verbatim and its supporting sentence
        carries no figures at all.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        banner_body = source.split("def banner(payload: dict) -> str:")[1].split(
            "\ndef "
        )[0]
        assert 'coverage.get("text"' in banner_body
        assert 'payload.get("counts")' not in banner_body, (
            "the banner is reading the outcome tally, which double counts the "
            "rules that never applied"
        )
        assert "counts.get(" not in banner_body


class TestOfflineReviewPath:
    """The exact chain the console runs, with no AWS client constructed."""

    @pytest.fixture(autouse=True)
    def forbid_aws(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("the console path must not reach AWS")
        monkeypatch.setattr(config, "session", explode)

    def test_every_cached_example_reviews_offline(self):
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")
        client = TextractClient()
        for pdf in pdfs:
            analysis = client.cached_by_hash(document_hash(pdf.read_bytes()))
            assert analysis is not None and analysis.ok
            document = layout.parse_blocks(analysis.blocks)
            extraction = extract_facts(document)
            report = engine.evaluate(extraction.facts)
            composed = compose_mod.compose(report, extraction=extraction)
            html = render_html(composed)
            assert composed.headline in VERDICT_COLOR
            assert "<!doctype html>" in html
            assert document.pages >= 1

    def test_rendered_body_carries_citations(self):
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")
        client = TextractClient()
        analysis = client.cached_by_hash(document_hash(pdfs[0].read_bytes()))
        document = layout.parse_blocks(analysis.blocks)
        extraction = extract_facts(document)
        composed = compose_mod.compose(
            engine.evaluate(extraction.facts), extraction=extraction
        )
        html = render_html(composed)
        # Unverified rules land in the unresolved group, which still cites.
        for finding in composed.unresolved:
            assert finding.section
            assert finding.section in html

    def test_unevaluated_checks_are_never_reported_as_passing(self):
        """A check that did not run must be visibly separate from one that passed.

        This is the property the whole report rests on. Most isolation distances
        live on a scanned drawing that cannot be measured, so those rules come
        back unevaluated, and folding them into the passes would tell a reviewer
        an application is clean when most of it was never checked.

        Moved premise: satisfied used to be asserted equal to every PASS. It no
        longer is, because a rule that does not govern this system is also a PASS
        internally and must not appear beside requirements that were met. The two
        groups together account for every PASS, and neither may hold the other's
        findings.
        """
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")
        client = TextractClient()
        analysis = client.cached_by_hash(document_hash(pdfs[0].read_bytes()))
        extraction = extract_facts(layout.parse_blocks(analysis.blocks))
        composed = compose_mod.compose(
            engine.evaluate(extraction.facts), extraction=extraction
        )
        counts = composed.counts
        assert counts["unknown"] > 0, "expected some checks to be unevaluable"
        assert len(composed.unresolved) == counts["unknown"]
        assert len(composed.satisfied) + len(composed.not_applicable) == counts["pass"]
        assert all(f.applicability == "applies" for f in composed.satisfied)
        assert all(
            f.applicability == "not_applicable" for f in composed.not_applicable
        )
        assert composed.coverage["evaluated"] == len(composed.satisfied) + len(
            composed.deficiencies
        )
        html = render_html(composed)
        assert "could not be evaluated" in html.lower()

    def test_uncached_document_is_detectable(self):
        """So the console can explain instead of hanging on an upload."""
        client = TextractClient()
        assert client.cached_by_hash("0" * 32) is None


class TestRenderSpeed:
    def test_review_completes_quickly(self):
        """Nobody watches a spinner. Budget is two seconds per selection."""
        import time

        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")
        client = TextractClient()
        # Use the largest cached example, which is the worst case.
        pdf = max(pdfs, key=lambda p: p.stat().st_size)
        started = time.perf_counter()
        analysis = client.cached_by_hash(document_hash(pdf.read_bytes()))
        document = layout.parse_blocks(analysis.blocks)
        extraction = extract_facts(document)
        composed = compose_mod.compose(
            engine.evaluate(extraction.facts), extraction=extraction
        )
        render_html(composed)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, (
            f"{pdf.name} took {elapsed:.2f}s, over the two second budget"
        )


class TestAppRuns:
    """Run the actual Streamlit script and assert it raises nothing.

    Skipped when streamlit is absent, since it is a demo dependency rather than a
    pipeline one and CI installs only requirements.txt.
    """

    @pytest.fixture
    def app_test(self):
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)

    def test_script_runs_without_exception(self, app_test):
        app_test.run()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]

    def test_sidebar_offers_an_uploader_and_no_preselected_list(self, app_test):
        """A reviewer brings the packet in front of them, not one off a menu.

        The console used to preselect from a fixed list, which reads as a canned
        demo. Selection is now the upload itself, so the uploader must be the
        only way in.
        """
        app_test.run()
        assert app_test.sidebar.get("file_uploader"), "no uploader rendered"
        assert not app_test.sidebar.radio, "a preselected application list came back"

    def test_the_screen_addresses_the_reviewer(self, app_test):
        """The audience is the reviewer assessing an application, not an applicant.

        The heading used to say pre-submission review, which frames the tool as
        something an applicant runs before filing. It is the largest text on a
        projected screen, so the wording matters.
        """
        app_test.run()
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert "Septic permit application review" in text
        assert "pre-submission" not in text.lower()

    def test_empty_state_says_what_to_do_next(self, app_test):
        """With nothing uploaded the screen must still direct the reviewer.

        It must not advertise prepared sample packets. A reviewer is bringing the
        packet in front of them, and naming a folder of canned files makes the
        console read as a demo rather than a tool.
        """
        app_test.run()
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert "Drop an application packet" in text
        assert "sample" not in text.lower()
        assert "testdata" not in text.lower()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]

    def test_all_rules_can_be_shown(self, app_test):
        """A reviewer asks two questions: what failed, and what gets checked.

        The report answers the first. The toggle answers the second, listing every
        requirement with the section, page, and quoted regulation text behind it.
        """
        app_test.run()
        toggles = app_test.get("toggle")
        assert toggles, "no control for showing the rules"
        toggles[0].set_value(True).run()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert "requirements this checks" in text, text[:300]

    def test_sidebar_says_how_many_rules_are_applied(self, app_test):
        """A reviewer has to see the scope of what was checked."""
        app_test.run()
        sidebar_text = " ".join(m.value or "" for m in app_test.sidebar.markdown)
        assert "Rules applied" in sidebar_text
        assert str(len(engine.load_rules())) in sidebar_text


class TestUploadDegradation:
    """An upload with no cached analysis and no credentials must explain itself.

    The console branches on whether the uploaded bytes are already in the cache.
    That branch is tested directly, because it is the one that would otherwise
    hang on a Textract call in front of an audience.
    """

    def test_unknown_document_is_not_in_the_cache(self):
        client = TextractClient()
        assert client.cached_by_hash(document_hash(b"not a real pdf")) is None

    def test_known_document_is_found_without_network(self, monkeypatch):
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")

        def explode(*args, **kwargs):
            raise AssertionError("a cache hit must not construct an AWS client")
        monkeypatch.setattr(config, "session", explode)

        client = TextractClient()
        found = client.cached_by_hash(document_hash(pdfs[0].read_bytes()))
        assert found is not None and found.ok

    def test_app_explains_rather_than_calling_textract(self):
        """The uncached branch must not call analyze_file."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "analyze_file" not in source, (
            "the console must never start a Textract job, that is the CLI's job"
        )
        assert "needs AWS credentials" in source
