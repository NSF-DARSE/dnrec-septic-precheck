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

    def test_app_reuses_the_shared_renderer(self):
        """The console must not carry a second report implementation.

        Two renderers drift, and the one nobody is looking at drifts first.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "from septic.report.render import render_html" in source
        assert "components.html" in source

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
        assert len(composed.satisfied) == counts["pass"]
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

    def test_empty_state_points_at_the_sample_packets(self, app_test):
        """With nothing uploaded the screen must say what to do next.

        Upload only means the first screen is empty, so it has to name where the
        ready made packets live or the demo starts with a dead end.
        """
        testdata = ROOT / "testdata"
        if not list(testdata.glob("*.pdf")):
            pytest.skip("no testdata packets present")
        app_test.run()
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert "testdata" in text, "empty state does not name the sample folder"
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
