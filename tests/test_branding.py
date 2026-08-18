"""Tests for the console's branding, and for where the sponsor marks may appear.

Placement is the point of this file, not decoration. This tool is not a DNREC
product and DNREC has not endorsed it, so the department seal and the state seal may
appear only in a labelled attribution band set apart from the product identity, and
never in the report body a reviewer prints or forwards. Getting that wrong in front
of the agency is worse than having no logos at all, so it is pinned here rather than
left to whoever edits the page next.

The other half is token discipline. app.py and render.py have drifted apart before,
and a reviewer reads the page by colour: one colour means a deficiency was found,
one means nothing was found, one means the tool has no answer. So neither surface is
allowed to carry a colour of its own.
"""
import re
from pathlib import Path

import pytest

from septic.report.assets import ASSET_FILES, TOKENS, asset_path, logo_data_uri

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
RENDER = ROOT / "src" / "septic" / "report" / "render.py"

# The marks that belong in the attribution band, and the organisation each one
# names. Read from the assets module, so the names live in one place.
STRIP = ("dnrec-logo.png", "delaware-seal.png", "udel-logo.png", "fsaii-logo.png")

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
IMG = re.compile(r"<img[^>]*>", re.IGNORECASE)
ALT = re.compile(r"alt=['\"]([^'\"]*)['\"]", re.IGNORECASE)


@pytest.fixture(scope="module")
def app_source() -> str:
    return APP.read_text(encoding="utf-8")


@pytest.fixture
def app_test():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(APP), default_timeout=120)


def rendered_markdown(app) -> str:
    return " ".join(m.value or "" for m in app.markdown)


class TestTokensAreTheOnlySourceOfStyle:
    def test_the_console_carries_no_hex_colour(self, app_source):
        """One palette, imported. Not two palettes that agree for now."""
        found = sorted(set(HEX.findall(app_source)))
        assert not found, f"app.py hard codes colours: {found}"

    def test_the_console_imports_the_token_set(self, app_source):
        assert "from septic.report.assets import" in app_source
        assert "TOKENS" in app_source

    def test_the_console_does_not_reach_into_the_assets_directory(self, app_source):
        """It asks the assets module for a name, not the file system for a path.

        Checked on the parsed source rather than the text, because the docstring
        cites assets/README.md deliberately and a reference in prose is not a file
        the console opens.
        """
        import ast

        tree = ast.parse(app_source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                first = node.body[0] if node.body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstrings.add(id(first.value))
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and ("assets/" in node.value or "assets\\" in node.value)
        ]
        assert not literals, f"app.py builds its own asset paths: {literals}"
        assert "logo_data_uri(" in app_source
        assert "asset_path(" in app_source

    def test_both_surfaces_use_the_same_font_stack(self, app_source):
        """The report is embedded in the console. Two stacks would show."""
        from septic.report.render import CSS

        assert TOKENS["font"]["sans"] in CSS
        assert TOKENS["font"]["sans"] in app_source or "f_sans" in app_source
        for banned in ("googleapis", "@import"):
            assert banned not in CSS
            assert banned not in app_source

    def test_the_verdict_colours_are_not_redefined_in_the_console(self, app_source):
        """It imports them from the renderer, so the two cannot disagree."""
        assert "from septic.report.render import VERDICT_COLOR" in app_source


class TestTheAttributionBand:
    def test_the_band_renders_with_nothing_uploaded(self, app_test):
        app_test.run()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]
        text = rendered_markdown(app_test)
        assert "class='band'" in text, "the attribution band did not render"
        assert "sponsor-strip" in text

    def test_every_sponsor_mark_is_inlined_from_disk(self, app_test):
        """No remote image, and the real file rather than a placeholder."""
        app_test.run()
        text = rendered_markdown(app_test)
        for name in STRIP:
            payload = logo_data_uri(name).split(";base64,", 1)[1][:96]
            assert payload in text, f"{name} is not inlined in the page"

    def test_every_logo_names_its_organisation_in_alt_text(self, app_test):
        app_test.run()
        text = rendered_markdown(app_test)
        band = text.split("class='band'", 1)[1]
        images = IMG.findall(band)
        assert len(images) == len(STRIP), (
            f"expected {len(STRIP)} marks in the band, found {len(images)}"
        )
        alts = []
        for tag in images:
            match = ALT.search(tag)
            assert match, f"a mark in the band has no alt attribute: {tag[:80]}"
            assert match.group(1).strip(), f"empty alt: {tag[:80]}"
            alts.append(match.group(1))
        for name in STRIP:
            assert ASSET_FILES[name] in alts, (
                f"{name} does not name its organisation in alt text"
            )

    def test_the_band_says_it_is_not_a_dnrec_product(self, app_test):
        """A strip of state marks with nothing said implies endorsement."""
        app_test.run()
        text = rendered_markdown(app_test)
        band = text.split("class='band'", 1)[1]
        assert "not a DNREC product" in band
        assert "has not endorsed" in band
        assert "nothing it produces is a determination" in band
        assert "The reviewer decides" in band

    def test_the_band_is_labelled_rather_than_decorative(self, app_test):
        app_test.run()
        band = rendered_markdown(app_test).split("class='band'", 1)[1]
        assert "band-heading" in band
        assert "HENnovate" in band

    def test_no_sponsor_mark_sits_beside_the_product_title(self, app_source):
        """The header states the product. It must not state the agency.

        A DNREC seal next to the title says this is official state software, which
        it is not, and that misrepresentation in front of the agency itself is the
        one branding mistake worth failing a build over.
        """
        appbar = app_source.split("class='brand-band'", 1)[1].split("</div>\"", 1)[0]
        assert "<img" not in appbar
        assert "logo" not in appbar.lower()

    def test_the_band_comes_after_the_report(self, app_source):
        """Attribution belongs at the foot of the page, not above the finding."""
        assert app_source.index("class='brand-band'") < app_source.index(
            "st.markdown(attribution_band()"
        )
        assert app_source.index("render_findings(payload)") < app_source.index(
            "st.markdown(attribution_band()"
        )

    def test_the_band_carries_the_dark_surface_the_wordmark_needs(self):
        """FSAII is a white wordmark. On a light band it disappears."""
        from septic.report.assets import contrast_ratio

        assert contrast_ratio("#ffffff", TOKENS["colour"]["band"]) >= 4.5


class TestTheReportBodyCarriesNoBranding:
    """A reviewer prints the report and puts it in the file.

    A detached page carrying the state seal reads as an official finding, so the
    marks stay on the console around the report and never inside it.
    """

    def test_the_renderer_never_loads_a_logo(self):
        source = RENDER.read_text(encoding="utf-8")
        assert "logo_data_uri" not in source
        for name in ASSET_FILES:
            assert name not in source

    def test_a_rendered_report_contains_no_sponsor_mark(self):
        from septic.report import compose as compose_mod
        from septic.report.render import render_html
        from septic.rules import engine

        composed = compose_mod.compose(engine.evaluate({"perc_rate": 30}))
        html = render_html(composed)
        for name in STRIP:
            payload = logo_data_uri(name).split(";base64,", 1)[1][:96]
            assert payload not in html, f"{name} reached the report body"


class TestTheBrowserTab:
    def test_the_favicon_is_configured_from_a_local_file(self, app_source):
        assert "page_icon=" in app_source
        icon_line = next(
            line for line in app_source.splitlines() if "page_icon=" in line
        )
        assert "asset_path(" in icon_line, (
            "the favicon must be resolved through the assets module"
        )
        assert "favicon.png" in app_source
        for scheme in ("http", "www.", "://"):
            assert scheme not in icon_line

    def test_the_favicon_file_is_usable_as_a_tab_icon(self):
        pytest.importorskip("PIL")
        from PIL import Image

        path = asset_path("favicon.png")
        assert path.is_file()
        icon = Image.open(path)
        assert icon.width == icon.height >= 32

    def test_the_page_title_names_the_tool(self, app_source):
        assert "page_title=" in app_source
        title_line = next(
            line for line in app_source.splitlines() if "page_title=" in line
        )
        assert "septic" in title_line.lower()


class TestProjectedAndPrinted:
    def test_the_verdict_is_the_largest_type_on_the_screen(self, app_source):
        """It is read from the back of a room."""
        assert "$t_verdict" in app_source
        scale = TOKENS["type_scale"]
        assert scale["verdict"] > scale["section"] > scale["body"]

    def test_a_print_stylesheet_drops_the_chrome_and_keeps_the_finding(
        self, app_source
    ):
        assert "@media print" in app_source
        printed = app_source.split("@media print", 1)[1]
        assert "stSidebar" in printed
        assert "stFileUploader" in printed
        assert "print-color-adjust" in printed, (
            "the verdict box carries meaning in its colour and has to print in it"
        )

    def test_keyboard_focus_is_visible(self, app_source):
        assert "focus-visible" in app_source
        assert "outline" in app_source


class TestTheRuleReference:
    def test_it_reads_as_a_table_of_citations(self, app_test):
        """Section, page, threshold, and what the regulation actually says."""
        from septic.rules import engine

        app_test.run()
        toggles = app_test.get("toggle")
        assert toggles, "no control for showing the rules"
        toggles[0].set_value(True).run()
        assert not app_test.exception, [str(e.value) for e in app_test.exception]
        text = rendered_markdown(app_test)
        assert "rules-table" in text
        for header in ("requirement", "threshold", "citation",
                       "what the regulation says"):
            assert header in text
        rules = engine.load_rules()
        for rule in rules:
            assert rule.citation.section in text, f"{rule.id} has no section shown"
            assert f"page {rule.citation.page}" in text
        # The quoted regulation text, which is what makes it a citation rather
        # than an assertion. Checked on a rule whose quote has no markup in it.
        quoted = [r for r in rules if r.citation.quote and "<" not in r.citation.quote]
        assert quoted, "no rule carries a quotable citation"
        assert any(
            r.citation.quote[:40] in text or r.citation.quote[:40].replace(
                "&", "&amp;"
            ) in text
            for r in quoted
        )
