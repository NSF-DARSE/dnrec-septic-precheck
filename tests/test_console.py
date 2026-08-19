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
from septic.report.render import (  # noqa: E402
    VERDICT_COLOR,
    render_html,
    render_text,
)
from septic.report.wording import UNREAD_INTRO  # noqa: E402
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

        The console renders the composed payload natively and offers render_html
        as a downloadable printable report rather than embedding it in an iframe.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "from septic import review as review_mod" in source
        assert "review_mod.review(" in source
        assert "render_html(payload" in source
        assert "st.download_button" in source
        assert "engine.evaluate(" not in source, (
            "the console is evaluating rules itself instead of delegating"
        )

    def test_app_references_no_remote_resource(self):
        """Venue wifi will fail. Nothing may be fetched at render time."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for pattern in ("http://", "https://", "fonts.googleapis", "cdn."):
            assert pattern not in source, f"app.py references {pattern}"

    def test_app_is_not_a_chat_interface(self):
        """The review display is not a chatbot. Rules decide, not a conversation.

        The reviewer chatbot section (below the report) deliberately uses
        st.chat_input and st.chat_message, but only inside _chatbot_section().
        The review results themselves are never presented as a chat thread.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        # The chat widgets must appear only inside the chatbot function
        chatbot_section = source.split("def _chatbot_section(")[1] if "def _chatbot_section(" in source else ""
        before_chatbot = source.split("def _chatbot_section(")[0] if "def _chatbot_section(" in source else source
        before_lower = before_chatbot.lower()
        for banned in ("chat_input", "chat_message", "st.chat"):
            assert banned not in before_lower, (
                f"app.py uses {banned} outside the chatbot section"
            )

    def test_console_banner_shows_coverage_with_the_verdict(self):
        """The screen may not show a headline without saying how much ran.

        The console is the surface a reviewer sees first and from furthest away.
        It reads both numbers out of the composed payload, so it cannot disagree
        with the report body. The verdict strip carries the verdict, the coverage
        bar and the counts in one horizontal line.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "def verdict_strip(payload: dict" in source
        assert "verdict_strip(payload" in source
        strip_body = source.split("def verdict_strip(payload: dict")[1].split(
            "\ndef "
        )[0]
        assert 'payload.get("headline"' in strip_body
        assert 'payload.get("coverage")' in strip_body
        assert "verdict-strip-headline" in strip_body

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


class TestTheVerdictIsStatedOnceOnScreen:
    """The console banner and the report body were both stating the verdict.

    Each surface was correct on its own, which is why no test caught it: the banner
    read the payload and rendered the headline, the coverage line and a tail
    sentence, and the report body in the iframe below rendered its own headline,
    the same coverage string and an explanation paragraph saying the same thing.
    On screen that was the whole block twice inside about a hundred pixels.

    The fix is a render mode, not a deletion, because the standalone report is
    printed and forwarded with no console around it and has to say which document
    it covers and what the verdict was. So both are asserted here: embedded says it
    once, standalone still says everything.
    """

    def payload(self):
        from septic.rules.schema import Citation, Operator, Rule, Severity

        def make(rule_id, parameter, **overrides):
            defaults = dict(
                id=rule_id, description="d",
                citation=Citation(section="TEST-0.0", page=1, quote="q"),
                parameter=parameter, operator=Operator.GE, threshold=1,
                units="feet", severity=Severity.RETURN, verified=True,
                remedy="r",
            )
            defaults.update(overrides)
            return Rule(**defaults)

        rules = [
            make("RAN", "perc_rate"),
            make("OUT", "design_flow", applies_to={"system_type": "mound"}),
            make("UNREAD", "dist_disposal_to_well"),
        ]
        report = engine.evaluate({"perc_rate": 5, "system_type": "gravity"}, rules)
        return compose_mod.compose(
            report, subject={"document": "packet.pdf", "pages": 13}
        ).to_json()

    def test_the_embedded_report_states_the_headline_once(self):
        payload = self.payload()
        embedded = render_html(payload, embedded=True)
        assert embedded.count(payload["headline"]) == 1, (
            "the headline appears more than once in the embedded body, and the "
            "banner above it makes that twice on screen"
        )
        # The one occurrence is the page title, which is the browser tab and not
        # anything a reviewer reads on the page.
        assert f"<title>Septic permit review: {payload['headline']}" in embedded

    def test_the_embedded_report_states_the_coverage_figure_no_times(self):
        """The banner owns it. Two copies of a count is how numbers drift."""
        payload = self.payload()
        text = payload["coverage"]["text"]
        assert text, "the fixture produced no coverage line"
        assert render_html(payload, embedded=True).count(text) == 0

    def test_the_embedded_report_drops_the_explanation_paragraph(self):
        payload = self.payload()
        embedded = render_html(payload, embedded=True)
        assert payload["explanation"] not in embedded
        assert "class='verdict'" not in embedded
        assert "class='counts'" not in embedded

    def test_the_standalone_report_still_carries_all_of_it(self):
        """Printed or opened from disk, it has to stand on its own."""
        payload = self.payload()
        standalone = render_html(payload)
        assert payload["coverage"]["text"] in standalone
        assert payload["explanation"] in standalone
        assert "class='verdict'" in standalone
        assert "DNREC septic permit application review" in standalone
        assert "packet.pdf" in standalone
        assert UNREAD_INTRO in standalone

    def test_both_modes_carry_the_same_findings(self):
        """Only the header block differs. A reviewer must not lose a finding."""
        payload = self.payload()
        embedded = render_html(payload, embedded=True)
        standalone = render_html(payload)
        for rule_id in ("RAN", "OUT", "UNREAD"):
            assert rule_id in embedded
            assert rule_id in standalone
        assert UNREAD_INTRO in embedded, (
            "the itemised list keeps its own lead paragraph, which is the right "
            "length beside the list it introduces"
        )

    def test_the_console_renders_natively_and_offers_download(self):
        """The console renders the payload natively and offers the HTML report
        as a downloadable file rather than embedding it in an iframe.

        This ensures the console is not an iframe wrapper around a print document
        but an application that reads the same payload.
        """
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "render_html(payload, embedded=False)" in source, (
            "the console must produce the standalone report for download"
        )
        assert "st.download_button" in source, (
            "the printable report must be offered as a download"
        )
        assert "render_findings(payload)" in source, (
            "the console must render findings natively"
        )

    def test_the_banner_tail_is_one_short_sentence(self):
        """It is read across a room. The paragraph belongs beside the list."""
        from septic.report.wording import (
            NOT_APPLICABLE_BANNER,
            UNREAD_BANNER,
        )

        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "tail = UNREAD_BANNER" in source
        assert "tail = NOT_APPLICABLE_BANNER" in source
        assert "UNREAD_INTRO" not in source, (
            "the banner is printing the report's paragraph again"
        )
        for sentence in (UNREAD_BANNER, NOT_APPLICABLE_BANNER):
            assert sentence.count(".") == 1, f"more than one sentence: {sentence}"
            assert len(sentence.split()) <= 26, f"too long for a banner: {sentence}"


class TestConsoleAndReportAgree:
    """The console and the printable report must show the same coverage and verdict.

    One payload, two presentations. The metric row on screen and the report
    offered for download both read from compose() output, so they carry the same
    numbers. This test guards against the drift that happens when a surface
    derives a count of its own.
    """

    def payload(self):
        from septic.rules.schema import Citation, Operator, Rule, Severity

        def make(rule_id, parameter, **overrides):
            defaults = dict(
                id=rule_id, description="d",
                citation=Citation(section="TEST-0.0", page=1, quote="q"),
                parameter=parameter, operator=Operator.GE, threshold=1,
                units="feet", severity=Severity.RETURN, verified=True,
                remedy="r",
            )
            defaults.update(overrides)
            return Rule(**defaults)

        rules = [
            make("RAN", "perc_rate"),
            make("OUT", "design_flow", applies_to={"system_type": "mound"}),
            make("UNREAD", "dist_disposal_to_well"),
        ]
        report = engine.evaluate({"perc_rate": 5, "system_type": "gravity"}, rules)
        return compose_mod.compose(
            report, subject={"document": "packet.pdf", "pages": 13}
        ).to_json()

    def test_same_coverage_text(self):
        """The coverage text in the console metric row is the same string
        the printable report renders."""
        payload = self.payload()
        coverage_text = payload["coverage"]["text"]
        html_report = render_html(payload)
        assert coverage_text in html_report, (
            "the printable report does not contain the coverage text"
        )

    def test_same_verdict(self):
        """The headline in the console metric row is the same string
        the printable report renders."""
        payload = self.payload()
        headline = payload["headline"]
        html_report = render_html(payload)
        assert headline in html_report, (
            "the printable report does not contain the verdict headline"
        )


class TestNothingLeaksHowTheAnalysisWasObtained:
    """A reviewer needs the document, not the plumbing.

    The subject used to carry a source line naming the service and saying whether a
    cache was used, and on the harvested permit path that line held the full S3
    key. Everything in subject is rendered, so it went onto a projected screen and
    into any report a reviewer forwarded. It also read as a caveat that made a real
    review look like a replayed demo, which is backwards: the cache is keyed by the
    SHA256 of the document, so a hit means this exact packet was analysed before.

    The offline guarantee itself is untouched and is asserted elsewhere in this
    file. It just is not narrated on screen.
    """

    def rendered_surfaces(self):
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")
        client = TextractClient()
        analysis = client.cached_by_hash(document_hash(pdfs[0].read_bytes()))
        extraction = extract_facts(layout.parse_blocks(analysis.blocks))
        composed = compose_mod.compose(
            engine.evaluate(extraction.facts), extraction=extraction,
            subject={"document": pdfs[0].name, "pages": 1},
        )
        payload = composed.to_json()
        return {
            "text report": render_text(payload),
            "standalone html": render_html(payload),
            "embedded html": render_html(payload, embedded=True),
        }

    def test_no_rendered_surface_contains_an_s3_path(self):
        for name, surface in self.rendered_surfaces().items():
            assert "s3://" not in surface, f"{name} renders an S3 path"

    def test_no_rendered_surface_narrates_the_cache_or_the_service(self):
        for name, surface in self.rendered_surfaces().items():
            lowered = surface.lower()
            for leak in ("textract", "no network used", "cached analysis",
                         "bedrock", "boto3"):
                assert leak not in lowered, f"{name} renders {leak!r}"

    def test_the_console_does_not_narrate_it_either(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        rendered = [
            line for line in source.splitlines()
            if ("st.markdown(" in line or "st.success(" in line
                or "st.caption(" in line)
        ]
        assert not any("s3://" in line for line in rendered)
        assert "already in the local Textract cache" not in source, (
            "this reads as a caveat and names the cache on a projected screen"
        )
        assert "subject.get('source'" not in source

    def test_the_review_module_puts_no_storage_path_in_the_subject(self):
        """subject is rendered in full, so a key must never reach it."""
        source = (ROOT / "src" / "septic" / "review.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if "subject[" in line and "=" in line:
                assert "s3://" not in line, line.strip()
                assert "Textract" not in line, line.strip()

    def test_a_harvested_permit_subject_holds_only_the_file_name(self):
        """The bucket and the key prefix must not survive into the subject.

        Uses a permit with no staged example PDF, so analyze falls through to the
        harvested document branch, which is the one that used to print the key.
        """
        from septic.ingest.textract import Analysis

        key = "documents/permits/permit_999999/60839580.pdf"

        class FakeClient:
            def cached(self, requested):
                assert requested == key
                return Analysis(
                    s3_key=requested, job_id=None, status="SUCCEEDED",
                    pages=1, blocks=[{"BlockType": "PAGE"}], from_cache=True,
                )

            def cached_by_hash(self, digest):
                return None

        def fake_key(permit, manifest=None):
            return key

        import septic.review as review_module

        assert review_module.find_local_pdf("999999") is None, (
            "this permit was expected to have no staged example"
        )
        original = review_module.s3_key_for_permit
        review_module.s3_key_for_permit = fake_key
        try:
            _, subject, offline = review_module.analyze(
                permit="999999", client=FakeClient(), allow_network=False
            )
        finally:
            review_module.s3_key_for_permit = original

        assert offline is True
        assert subject["document"] == "60839580.pdf"
        assert "source" not in subject, (
            "the subject is rendered in full, so a mechanism line reaches the "
            "screen and the printed report"
        )
        for value in subject.values():
            assert "s3://" not in str(value)
            assert "documents/" not in str(value)


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
        # Use a packet that has unresolved checks. The real permits always do,
        # and the demonstration packet C is all-unknown. Skip fully-resolved
        # demo packets that have no unknowns.
        client = TextractClient()
        candidate = None
        for pdf in pdfs:
            analysis = client.cached_by_hash(document_hash(pdf.read_bytes()))
            extraction = extract_facts(layout.parse_blocks(analysis.blocks))
            composed = compose_mod.compose(
                engine.evaluate(extraction.facts), extraction=extraction
            )
            if composed.counts["unknown"] > 0:
                candidate = composed
                break
        if candidate is None:
            pytest.skip("no cached example with unresolved checks")
        composed = candidate
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

    def test_an_uploader_is_the_only_way_in_and_no_preselected_list(self, app_test):
        """A reviewer brings the packet in front of them, not one off a menu.

        The console used to preselect from a fixed list, which reads as a canned
        demo. Selection is now the upload itself, so an uploader must be the only
        way in.

        This asserted that the uploader was in the sidebar. It is now the large
        drop target in the main column, because a big dashed box telling a reviewer
        to use a smaller control somewhere else is an instruction to ignore the
        obvious target. The sidebar was never what the test was protecting. The
        property is that there is exactly one way to load a packet and it is an
        upload, which is asserted here over the whole page rather than one column
        of it, so this is stricter than it was: a reinstated list in either column
        now fails it.
        """
        app_test.run()
        uploaders = app_test.get("file_uploader")
        assert len(uploaders) == 1, f"{len(uploaders)} uploaders rendered, want 1"
        assert not app_test.radio, "a preselected application list came back"
        assert not app_test.sidebar.radio, "a list came back in the sidebar"
        assert not app_test.selectbox, "a list of applications came back"

    def test_a_loaded_packet_does_not_leave_a_dropzone_above_the_findings(
        self, app_test
    ):
        """Once there is a report the drop target gives up the column.

        The uploader is the large dashed box while there is nothing to show, which
        is the whole point of it being in the main column. A reviewer reading
        findings should not have to scroll past an empty one to reach them, so the
        loaded state folds the same control into a closed expander. The way in has
        to survive that fold: exactly one uploader either way.
        """
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")

        pdf = pdfs[0]

        class Packet:
            name = pdf.name

            def getvalue(self):
                return pdf.read_bytes()

        app_test.session_state["application_packet"] = Packet()
        app_test.run()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]
        empty = [
            m.value for m in app_test.markdown
            if m.value and "class='empty'" in m.value
        ]
        assert not empty, "the empty dropzone is still taking the column"
        # The property is that the uploader folds away and stays reachable, not
        # how many expanders the page happens to have. Passed and not applicable
        # findings are collapsed too, because they are reference rather than the
        # answer, so counting expanders pins the wrong thing.
        assert app_test.get("expander"), "the uploader was not folded away"
        assert len(app_test.get("file_uploader")) == 1, "the way in disappeared"
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert "verdict-strip-headline" in text, "no verdict rendered for a loaded packet"

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

    def test_a_cold_page_load_constructs_no_aws_client(self, app_test,
                                                       monkeypatch):
        """The page has to render with no credentials in the environment.

        Nothing on the first paint needs AWS: the GIS layers and the regulation
        graph come off disk, and the Textract cache is only consulted once a
        packet is uploaded. This breaks the session factory for the duration of
        the run, so any attempt to build a client fails the test rather than
        surfacing as a slow spinner in front of an audience.
        """
        def explode(*args, **kwargs):
            raise AssertionError("the console built an AWS client on page load")

        monkeypatch.setattr(config, "session", explode)
        app_test.run()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert "Septic permit application review" in text
        assert "Drop an application packet" in text

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

    def test_rule_count_is_visible(self, app_test):
        """A reviewer has to see the scope of what was checked.

        The count of requirements and the fact they come from the regulation must
        be on screen regardless of where the control is placed. This test pins
        the property (visible rule count matching the loaded rule set) rather
        than the location it renders in.
        """
        app_test.run()
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert str(len(engine.load_rules())) in text
        assert "regulation" in text.lower()


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


class TestPDFViewer:
    """Tests for the split PDF viewer and fact box provenance."""

    def test_fact_box_survives_into_payload(self):
        """A fact with a box must carry it through compose into the payload.

        The viewer needs the box to highlight the source on the rendered page,
        so dropping it anywhere on the path from extraction to composed output
        means a highlight disappears.
        """
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")

        pdf = pdfs[0]
        client = TextractClient()
        analysis = client.cached_by_hash(document_hash(pdf.read_bytes()))
        assert analysis is not None and analysis.ok

        document = layout.parse_blocks(analysis.blocks)
        extraction = extract_facts(document)

        # At least one fact must have a box from the real packet.
        facts_with_box = [
            f for f in extraction.provenance.values() if f.box is not None
        ]
        assert facts_with_box, "no fact has a box, so the viewer cannot highlight"

        # Compose the payload and verify the box appears in facts_read.
        from septic.rules import engine as eng
        report = eng.evaluate(extraction.facts)
        composed = compose_mod.compose(report, extraction=extraction)
        payload = composed.to_json()

        facts_read = payload.get("facts_read") or []
        boxes_in_payload = [f for f in facts_read if f.get("box") is not None]
        assert boxes_in_payload, "fact boxes did not survive into the composed payload"
        # Verify the box structure.
        sample = boxes_in_payload[0]["box"]
        assert "left" in sample and "top" in sample
        assert "width" in sample and "height" in sample
        assert "page" in sample

    def test_fact_box_appears_on_findings(self):
        """A finding whose fact has a box must expose fact_box in the payload."""
        pdfs = cached_examples()
        if not pdfs:
            pytest.skip("no cached examples present")

        pdf = pdfs[0]
        client = TextractClient()
        analysis = client.cached_by_hash(document_hash(pdf.read_bytes()))
        assert analysis is not None and analysis.ok

        document = layout.parse_blocks(analysis.blocks)
        extraction = extract_facts(document)
        from septic.rules import engine as eng
        report = eng.evaluate(extraction.facts)
        composed = compose_mod.compose(report, extraction=extraction)
        payload = composed.to_json()

        all_findings = (
            payload.get("deficiencies", [])
            + payload.get("satisfied", [])
            + payload.get("unresolved", [])
        )
        findings_with_box = [f for f in all_findings if f.get("fact_box")]
        # If any fact has a box, the corresponding finding should too.
        facts_with_box = [
            f for f in extraction.provenance.values() if f.box is not None
        ]
        if facts_with_box:
            assert findings_with_box, (
                "facts have boxes but no finding exposes fact_box"
            )

    def test_no_http_in_app(self):
        """The console must not reference any remote resource."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for pattern in ("http://", "https://", "fonts.googleapis", "cdn."):
            assert pattern not in source, f"found {pattern!r} in app.py"


class TestNoOperatorsOnAnySurface:
    """Comparison operators and parameter names are engine vocabulary.

    This has now been missed on three separate surfaces: the highlight colour
    map, the console tables, and the printable report. The report is the one a
    reviewer forwards, so it matters most and it was the one still leaking.
    """

    def test_no_comparison_operators_reach_a_reviewer(self):
        import html as html_lib
        import re
        from pathlib import Path
        from septic.review import review
        from septic.report.render import render_html
        from septic.report.letter import render_letter

        packets = sorted(Path("out/examples").glob("permit_2849*.pdf"))
        if not packets:
            pytest.skip("no demonstration packets staged")

        operator = re.compile(r"(?:>=|<=|==)")
        for pdf in packets:
            result = review(pdf=pdf, allow_network=False, with_map=False)
            payload = result.composed.to_json()
            for surface in (render_html(payload), render_letter(payload)):
                text = html_lib.unescape(re.sub(r"<[^>]+>", " ", surface))
                found = operator.findall(text)
                assert not found, (
                    f"{pdf.name} renders a comparison operator to a reviewer: "
                    f"{found[:3]}"
                )


class TestChatbotIntegration:
    """The reviewer chatbot must render after a permit is reviewed.

    Regression: the _chatbot_section(payload) call was lost during the PR #2
    merge. This test fails when the call is absent, catching it before release.
    """

    @pytest.fixture
    def app_test(self):
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)

    def test_chatbot_function_called_in_reviewed_path(self):
        """_chatbot_section must be both defined AND called in app.py."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "def _chatbot_section(" in source, (
            "_chatbot_section function definition is missing"
        )
        assert "_chatbot_section(payload)" in source, (
            "_chatbot_section is defined but never called — "
            "the chatbot will not render after review"
        )
        # Definition must come before call
        def_pos = source.index("def _chatbot_section(")
        call_pos = source.index("_chatbot_section(payload)")
        assert def_pos < call_pos, (
            "_chatbot_section is called before it is defined"
        )

    def test_reviewer_assistant_renders_with_cached_pdf(self, app_test, monkeypatch):
        """A reviewed permit must show the 'Reviewer assistant' section."""
        testdata = ROOT / "testdata"
        if not testdata.exists():
            pytest.skip("testdata/ not present")

        pdfs = sorted(testdata.glob("*.pdf"))
        if not pdfs:
            pytest.skip("no demo PDFs in testdata/")

        from septic.ingest.textract import TextractClient, document_hash

        client = TextractClient()
        cached_pdfs = [
            p for p in pdfs
            if client.cached_by_hash(document_hash(p.read_bytes())) is not None
        ]
        if not cached_pdfs:
            pytest.skip("no cached examples present")

        # Set env so chatbot is available
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")

        pdf = cached_pdfs[0]

        class Packet:
            name = pdf.name

            def getvalue(self):
                return pdf.read_bytes()

        app_test.session_state["application_packet"] = Packet()
        app_test.run()
        assert not app_test.exception, (
            f"Exception: {[str(e.value) for e in app_test.exception]}"
        )
        text = " ".join(m.value or "" for m in app_test.markdown)
        assert "Reviewer assistant" in text, (
            "The chatbot section did not render — "
            "check that _chatbot_section(payload) is called in app.py"
        )
