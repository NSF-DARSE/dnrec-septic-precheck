"""Tests for the Streamlit shell and for how an unread check is worded.

This file exists because of a defect the rest of the suite could not see. Every
colour was measured, sixteen contrast pairs passed, and the app still rendered
unreadable: there was no .streamlit/config.toml, so Streamlit built its own chrome
from the operating system's preference, drew a near black shell on a machine set to
dark mode, and painted the light token palette onto it. Body copy measured about
1.7 to 1 and the three verdict names could not be read at all.

Nothing here renders a browser, so none of it proves what a projector shows. What
it does is pin the two causes: that the shell's palette is committed and comes from
the same tokens as everything else, and that no rule in app.py reaches into
Streamlit's internals in a way that breaks its own rendering. Both were verified by
eye at 1400x900 as well, which is the part a test cannot do.
"""
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from septic.report import compose as compose_mod
from septic.report.assets import STREAMLIT_THEME, TOKENS, contrast_ratio
from septic.report.render import render_html, render_text
from septic.report.wording import (
    PARAMETER_LOCATION,
    UNREAD_HEADING,
    UNREAD_INTRO,
    parameter_location,
    parameter_name,
    unread_note,
)
from septic.rules import engine

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".streamlit" / "config.toml"
APP = ROOT / "app.py"


@pytest.fixture(scope="module")
def config_text() -> str:
    assert CONFIG.is_file(), (
        f"{CONFIG} is missing. Without it Streamlit renders its shell from the "
        f"operating system's theme, which is how the console ended up drawing a "
        f"dark chrome under a light palette. Run python scripts/build_theme.py"
    )
    return CONFIG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def config(config_text) -> dict:
    return tomllib.loads(config_text)


@pytest.fixture(scope="module")
def app_source() -> str:
    return APP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_css(app_source) -> str:
    """The console stylesheet with its comments removed.

    The comments here name the selectors that caused each defect, on purpose, so
    the next person to touch this file does not reintroduce them. A comment cannot
    change what a browser renders, so the rules are what gets asserted on.
    """
    style = app_source.split("STYLE_TEMPLATE = ", 1)[1]
    return re.sub(r"/\*.*?\*/", "", style, flags=re.DOTALL)


class TestTheShellIsPinnedLight:
    def test_the_theme_file_is_committed(self, config_text):
        """It has to be in the repository, not on the presenter's machine."""
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".streamlit/config.toml"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        assert tracked.returncode == 0, (
            ".streamlit/config.toml is not tracked by git, so the demo machine "
            "would render whatever theme it feels like"
        )

    def test_it_pins_a_light_base(self, config):
        """The user asked for light. Light is the decision, not a fallback."""
        assert config["theme"]["base"] == "light"

    def test_the_shell_takes_every_colour_from_the_tokens(self, config):
        """One palette. Not two palettes that agree for now."""
        theme = config["theme"]
        for option, token in STREAMLIT_THEME:
            assert theme[option] == TOKENS["colour"][token], (
                f"theme.{option} is {theme[option]}, but colour.{token} is "
                f"{TOKENS['colour'][token]}"
            )

    def test_a_dark_operating_system_cannot_change_the_palette(self, config):
        """Inheriting the visitor's theme is exactly what broke this.

        Streamlit will switch to its dark slot when the browser reports a dark
        preference, so the dark slot is pinned to the same light values. The
        rendering has to be identical whichever machine drives the projector.
        """
        dark = config["theme"]["dark"]
        for option, token in STREAMLIT_THEME:
            assert dark[option] == TOKENS["colour"][token], (
                f"theme.dark.{option} would let a dark machine change the palette"
            )

    def test_the_body_text_clears_wcag_on_the_shell_background(self, config):
        """The measurement that was passing while the screen was unreadable.

        The contrast suite checked ink against the token surface, which was right,
        and the shell was painting a different background underneath it. Now that
        the shell's background is a token too, the pair can actually be measured.
        """
        background = config["theme"]["backgroundColor"]
        assert contrast_ratio(TOKENS["colour"]["ink"], background) >= 4.5
        assert contrast_ratio(TOKENS["colour"]["muted"], background) >= 4.5
        sidebar = config["theme"]["secondaryBackgroundColor"]
        assert contrast_ratio(TOKENS["colour"]["ink"], sidebar) >= 4.5
        assert contrast_ratio(TOKENS["colour"]["muted"], sidebar) >= 4.5

    def test_the_three_verdict_names_are_readable_on_the_shell(self, config):
        """These are the most important words in the product.

        On the dark shell they measured about 1.05 to 1 in ink, which is to say
        they were not there. The empty state prints them in their own verdict
        colours, so each one is measured against the page it is printed on.
        """
        background = config["theme"]["backgroundColor"]
        for token in ("deficiency_fg", "clear_fg", "unverified_fg"):
            ratio = contrast_ratio(TOKENS["colour"][token], background)
            assert ratio >= 4.5, f"{token} measures {ratio:.2f} on the page"

    def test_the_file_is_generated_rather_than_typed(self, config_text):
        """The tokens are the source of truth and the file is derived from them."""
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import build_theme
        finally:
            sys.path.pop(0)
        assert config_text == build_theme.render(), (
            ".streamlit/config.toml is out of date. Run "
            "python scripts/build_theme.py"
        )

    def test_it_says_where_the_values_come_from(self, config_text):
        """Somebody will open this file before they open the tokens."""
        assert "src/septic/report/assets.py" in config_text
        assert "scripts/build_theme.py" in config_text

    def test_no_usage_ping_at_start_up(self, config):
        """Venue wifi fails. Nothing may be fetched to render the page."""
        assert config["browser"]["gatherUsageStats"] is False


class TestTheConsoleDoesNotBreakTheShell:
    def test_the_font_override_cannot_reach_streamlit_icons(self, app_css):
        """The bug that made the upload control read uploadUpload.

        Streamlit draws its icons as ligatures in a bundled icon font. The
        stylesheet here used to set font-family on a selector matching every
        emotion generated class, which includes the icon span, so each icon
        rendered as the text of its own ligature name. Measured in Chrome: the
        icon span computed to the Segoe stack before and to Material Symbols
        Rounded after. The body font is set through the theme file instead.
        """
        assert '[class*="st-"]' not in app_css, (
            "this selector reaches Streamlit's icon font and turns every icon "
            "into the text of its ligature name"
        )
        assert "font-family:$f_sans" in app_css
        assert "html, body {" in app_css

    def test_the_content_clears_the_streamlit_toolbar(self, app_css):
        """Content must never sit under the top edge.

        The toolbar is fixed over the top of the main column and does not push
        content down, so a padding smaller than its height put the product title
        underneath it and cut the tool name off. Both numbers are tokens because
        the clearance is meaningless without the height it has to clear.
        """
        chrome = TOKENS["chrome"]
        assert chrome["top_clearance"] > chrome["header_height"], (
            "the block container would start underneath the Streamlit toolbar"
        )
        assert "padding-top:$k_top_clearance" in app_css

    def test_the_drop_target_is_the_uploaders_own_dropzone(self, app_css,
                                                           app_source):
        """The dashed box has to be the element that accepts a drop.

        It used to be a plain div, with the real uploader as a small control in
        the sidebar, so the largest target on the screen was the one thing a
        packet could not be dropped on. The styling is scoped to the container
        key, and the key is read out of the source here rather than typed twice:
        renaming the container would otherwise leave a selector that matches
        nothing, which is the failure mode this file already has a test for.
        """
        keys = re.findall(r'st\.container\(key="([^"]+)"\)', app_source)
        assert keys, "the drop target is not in a keyed container"
        for key in keys:
            assert f'.st-key-{key} [data-testid="stFileUploaderDropzone"]' in app_css
        assert "dashed" in app_css

    def test_streamlit_elements_are_matched_on_test_id_alone(self, app_css):
        """A tag qualified selector dies silently when the tag changes.

        The sidebar is a section in this version of Streamlit and three rules were
        written against a div of the same test id, so the sidebar width was never
        applied and the print stylesheet never hid it. Nothing failed and nothing
        looked wrong until the DOM was read.
        """
        qualified = re.findall(r"\b([a-z]+)\[data-testid=", app_css)
        assert not qualified, (
            f"these selectors are tied to an element name Streamlit chose: "
            f"{sorted(set(qualified))}"
        )

    def test_printing_still_drops_the_chrome(self, app_css):
        printed = app_css.split("@media print", 1)[1]
        for testid in ("stFileUploader", "stToolbar", "stHeader"):
            assert f'[data-testid="{testid}"]' in printed


class TestAnUnreadCheckNamesWhatToRead:
    """The wording requirement, applied identically to both surfaces.

    What is not under test here is the honesty. Ten of the fifteen rules need a
    value dimensioned on a scanned drawing, so those checks report UNKNOWN, and
    that must never read as a pass. These tests only pin that the sentence is
    addressed to the reviewer: which value, where it normally is, and what to
    compare it against.
    """

    def test_every_parameter_a_rule_needs_can_be_named(self):
        """A rule added without wording would fall back to a machine name."""
        missing = sorted(
            rule.parameter for rule in engine.load_rules()
            if rule.parameter not in PARAMETER_LOCATION
        )
        assert not missing, (
            f"these parameters have no reviewer facing wording: {missing}. Add "
            f"them to PARAMETER_LOCATION in src/septic/report/wording.py"
        )

    def test_every_gating_parameter_can_be_named(self):
        """An unread gating fact takes a rule out of the count silently."""
        gates = {
            key for rule in engine.load_rules() for key in rule.applies_to
        }
        missing = sorted(g for g in gates if g not in PARAMETER_LOCATION)
        assert not missing, f"no wording for the gating facts {missing}"

    def test_a_location_is_a_place_in_the_packet(self):
        """Every entry has to say where to look, not what the value means."""
        for parameter, (name, location) in PARAMETER_LOCATION.items():
            assert name and name[0].isupper(), parameter
            assert location and location[0].islower(), parameter
            assert len(location.split()) >= 4, (
                f"{parameter} does not say where the packet carries it"
            )

    def test_an_unread_value_names_itself_its_place_and_its_citation(self):
        note = unread_note({
            "parameter": "dist_disposal_to_well",
            "citation": "Exhibit C, page 173",
            "verified": True,
            "applicability": "applies",
            "reason": "dist_disposal_to_well could not be read from the application",
        })
        assert parameter_name("dist_disposal_to_well") in note
        assert parameter_location("dist_disposal_to_well") in note
        assert "Exhibit C, page 173" in note
        assert "could not be read from the application" not in note, (
            "this describes the extractor rather than the reviewer's next task"
        )

    def test_an_unconfirmed_threshold_is_not_blamed_on_the_packet(self):
        """A rule nobody has certified is not a deficiency in the application."""
        note = unread_note({
            "parameter": "perc_rate",
            "citation": "5.2.4.2.5.7, page 52",
            "verified": False,
            "applicability": "undetermined",
            "reason": "threshold has not been verified against the regulation",
        })
        assert "5.2.4.2.5.7, page 52" in note
        assert "confirmed against the regulation by a person" in note
        assert "nothing about this packet caused that" in note

    def test_an_undetermined_rule_says_what_settles_it(self):
        note = unread_note({
            "parameter": "disposal_slope",
            "citation": "5.3.12.1.2, page 60",
            "verified": True,
            "applicability": "undetermined",
            "excluded_by": {"parameter": "system_type", "value": None},
            "reason": "cannot tell whether this rule applies because system_type "
                      "is unknown",
        })
        assert "system type" in note.lower()
        assert parameter_location("system_type") in note
        assert "5.3.12.1.2, page 60" in note

    def test_an_unknown_parameter_still_produces_a_usable_sentence(self):
        """Wording must degrade rather than crash on a fact nobody described."""
        note = unread_note({
            "parameter": "some_new_fact",
            "citation": "9.9.9, page 1",
            "verified": True,
            "applicability": "applies",
            "reason": "some_new_fact could not be read",
        })
        assert "some_new_fact" in note
        assert "9.9.9, page 1" in note

    def test_both_surfaces_carry_the_same_words(self, app_source):
        """The banner across the room and the list up close have to agree."""
        from septic.rules.schema import Citation, Operator, Rule, Severity

        rule = Rule(
            id="ISO-001-disposal-area-to-well", description="d",
            citation=Citation(section="Exhibit C", page=173, quote="q"),
            parameter="dist_disposal_to_well", operator=Operator.GE,
            threshold=100, units="feet", severity=Severity.RETURN,
            verified=True, remedy="r",
        )
        payload = compose_mod.compose(engine.evaluate({}, [rule])).to_json()
        assert payload["coverage"]["unreadable"] == 1
        finding = payload["unresolved"][0]
        assert finding["parameter"] == "dist_disposal_to_well"

        note = unread_note(finding)
        html = render_html(payload)
        text = render_text(payload)
        for surface in (html, text):
            # The text renderer hard wraps at 78 columns, so search with
            # normalized whitespace for the text surface.
            normalized = " ".join(surface.split())
            assert parameter_name("dist_disposal_to_well") in normalized
            assert "Exhibit C" in surface
        # The grouped format carries the same information as the per-finding note
        # but in a different shape (group header + rule table). Verify the key
        # content is present rather than the exact unread_note string.
        assert "not machine readable" in html
        assert "Exhibit C" in html
        assert UNREAD_INTRO in html
        assert UNREAD_HEADING.lower() in html.lower()
        # The terminal report hard wraps at 78 columns, so it carries the same
        # words rather than the same bytes.
        flat = " ".join(text.split())
        assert " ".join(UNREAD_INTRO.split()) in flat

        # The console banner points at the list rather than reprinting it. Both
        # sentences come from this module, so the two surfaces still cannot word
        # the same missing value differently, and neither invents its own copy.
        from septic.report.wording import UNREAD_BANNER

        assert "from septic.report.wording import" in app_source
        assert "UNREAD_BANNER" in app_source
        assert "tail = UNREAD_BANNER" in app_source
        assert UNREAD_BANNER not in html, (
            "the banner sentence belongs on the console only, since the report "
            "prints the full paragraph beside its list"
        )

    def test_the_missing_value_list_says_where_to_look_too(self):
        from septic.rules.schema import Citation, Operator, Rule, Severity

        rule = Rule(
            id="SEP-001", description="d",
            citation=Citation(section="5.3.12.1.3", page=61, quote="q"),
            parameter="limiting_zone_below_trench_bottom", operator=Operator.GE,
            threshold=20, units="inches", severity=Severity.RETURN,
            verified=True, remedy="r",
        )
        payload = compose_mod.compose(engine.evaluate({}, [rule])).to_json()
        entry = payload["missing_information"][0]
        assert entry["parameter"] == "limiting_zone_below_trench_bottom"
        assert entry["normally_found"] == parameter_location(
            "limiting_zone_below_trench_bottom"
        )
        assert entry["normally_found"] in render_html(payload)

    def test_an_unread_check_is_never_worded_as_a_pass(self):
        """The one thing that may not change. UNKNOWN stays UNKNOWN."""
        for parameter in sorted(PARAMETER_LOCATION):
            note = unread_note({
                "parameter": parameter, "citation": "X, page 1",
                "verified": True, "applicability": "applies", "reason": "",
            })
            lowered = note.lower()
            for banned in ("satisfied", "complies", "compliant", "meets the",
                           "passes", "approved", "acceptable"):
                assert banned not in lowered, f"{parameter}: {note}"


class TestTheEmptyStateIsShort:
    """A reviewer facing an empty screen needs an instruction, not an essay.

    Measured in the browser: 88 words before, 34 after. Counted here off the
    rendered markdown rather than the source, because the source carries markup
    that a reader never sees.
    """

    @pytest.fixture
    def app_test(self):
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        return AppTest.from_file(str(APP), default_timeout=120)

    def test_it_is_one_instruction_and_three_outcomes(self, app_test):
        app_test.run()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]
        empty = next(
            m.value for m in app_test.markdown
            if m.value and "class='empty'" in m.value
        )
        words = len(re.sub(r"<[^>]+>", " ", empty).split())
        assert words <= 45, f"the empty state is back to {words} words"
        assert "Drop an application packet" in empty
        for verdict in ("DEFICIENCIES FOUND", "NO DEFICIENCIES FOUND",
                        "CANNOT VERIFY"):
            assert verdict in empty

    def test_the_verdict_names_carry_the_verdict_colours(self, app_test):
        """So the legend and the real banner cannot disagree about a colour."""
        from septic.report.render import VERDICT_COLOR

        app_test.run()
        empty = next(
            m.value for m in app_test.markdown
            if m.value and "class='empty'" in m.value
        )
        for verdict, (foreground, _) in VERDICT_COLOR.items():
            assert f"color:{foreground}" in empty, f"{verdict} is not in colour"
