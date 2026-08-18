"""Tests for the local assets and the design token set.

Two properties matter here and both are about what happens in the room.

Nothing may be fetched while a page renders, because venue wifi will fail. So
every image is on disk, loads to a data URI, and the font stacks name system fonts
only.

Every foreground and background pair has to clear WCAG AA, and the ratio is
computed here rather than asserted from a comment. A projector washes out low
contrast that looks fine on a laptop, and this project has already had to replace a
pale yellow in src/septic/maps.py that measured 1.8 to 1.
"""
from pathlib import Path

import pytest

from septic.report.assets import (
    ASSET_FILES,
    ASSETS_DIR,
    CONTRAST_PAIRS,
    FONT_MONO,
    FONT_SANS,
    TOKENS,
    asset_path,
    contrast_ratio,
    contrast_report,
    logo_data_uri,
    relative_luminance,
)

ROOT = Path(__file__).resolve().parent.parent
LOGOS = ("dnrec-logo.png", "fsaii-logo.png", "udel-logo.png", "delaware-seal.png")


class TestAssetFiles:
    def test_the_assets_directory_is_where_the_module_thinks(self):
        assert ASSETS_DIR == ROOT / "assets"
        assert ASSETS_DIR.is_dir()

    @pytest.mark.parametrize("name", sorted(ASSET_FILES))
    def test_every_declared_asset_exists(self, name):
        assert asset_path(name).is_file()

    @pytest.mark.parametrize("name", sorted(ASSET_FILES))
    def test_every_asset_loads_to_a_data_uri(self, name):
        uri = logo_data_uri(name)
        assert uri.startswith("data:image/")
        assert ";base64," in uri
        assert len(uri) > 500

    def test_an_unknown_name_is_refused(self):
        """The loader takes a name, so it must not be able to read a path."""
        with pytest.raises(KeyError):
            logo_data_uri("../../etc/passwd")
        with pytest.raises(KeyError):
            logo_data_uri("logo.png")

    def test_the_loader_is_cached(self):
        """The console re-runs its whole script on every widget interaction."""
        logo_data_uri.cache_clear()
        first = logo_data_uri("udel-logo.png")
        assert logo_data_uri.cache_info().misses == 1
        second = logo_data_uri("udel-logo.png")
        assert logo_data_uri.cache_info().hits == 1
        assert first is second

    def test_the_readme_states_the_attribution_rule(self):
        """Getting the placement wrong is worse than having no logos."""
        readme = (ASSETS_DIR / "README.md").read_text(encoding="utf-8")
        assert "not a DNREC product" in readme
        assert "has not endorsed" in readme
        assert "alt" in readme
        for name in ASSET_FILES:
            assert name in readme, f"{name} is not documented"


class TestImageProperties:
    @pytest.fixture(autouse=True)
    def pillow(self):
        pytest.importorskip("PIL")

    def test_the_favicon_is_square_and_big_enough(self):
        from PIL import Image

        icon = Image.open(asset_path("favicon.png"))
        assert icon.width == icon.height
        assert icon.width >= 32
        assert icon.mode == "RGBA"

    def test_the_favicon_still_reads_at_thirty_two_pixels(self):
        """A blur is not a favicon.

        Which crop to ship was decided by eye against a 32 pixel render of both
        candidates: the full seal loses its ring of type to grey mush, the inner
        scene keeps a yellow sun over green and blue. What this test holds is the
        property that made the crop worth choosing, so a future recrop cannot
        quietly ship something that vanishes at tab size: at 32 pixels the mark
        still fills the icon and still carries colour rather than grey.
        """
        import numpy as np
        from PIL import Image

        icon = Image.open(asset_path("favicon.png")).convert("RGBA")
        small = np.asarray(icon.resize((32, 32), Image.Resampling.LANCZOS))
        visible = small[small[..., 3] > 32]
        assert len(visible) > 32 * 32 * 0.5, "the mark all but disappeared"
        channels = visible[:, :3].astype(int)
        saturation = (channels.max(axis=1) - channels.min(axis=1)).mean()
        assert saturation > 40, (
            f"mean channel spread is {saturation:.0f}, so what survived the "
            f"downscale is grey mush"
        )

    @pytest.mark.parametrize("name", LOGOS)
    def test_no_logo_carries_a_matte(self, name):
        """A filled background shows as a block on the dark attribution band.

        Transparent corners are what distinguish a keyed background from a white
        or coloured rectangle. Checked on the corners rather than on white pixels
        anywhere, because two of these marks contain white as part of the design.
        """
        from PIL import Image

        image = Image.open(asset_path(name)).convert("RGBA")
        alpha = image.getchannel("A")
        width, height = image.size
        corners = [
            alpha.getpixel((0, 0)),
            alpha.getpixel((width - 1, 0)),
            alpha.getpixel((0, height - 1)),
            alpha.getpixel((width - 1, height - 1)),
        ]
        assert corners == [0, 0, 0, 0], f"{name} has an opaque corner: {corners}"

    def test_rasterising_the_seal_was_worth_it(self):
        """The reason delaware-seal.png exists at all."""
        png = len(logo_data_uri("delaware-seal.png"))
        svg = len(logo_data_uri("delaware-seal.svg"))
        assert png < svg / 2, (
            f"the rendered seal is {png} bytes inlined against {svg} for the "
            f"source, so rendering it bought nothing"
        )


class TestDesignTokens:
    def test_the_contrast_helper_is_right_before_it_is_trusted(self):
        """Otherwise every ratio below is self certifying."""
        assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
        assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
        assert relative_luminance("#ffffff") == pytest.approx(1.0, abs=0.001)
        assert relative_luminance("#000000") == pytest.approx(0.0, abs=0.001)
        # A known WCAG value: #767676 is the lightest grey that clears 4.5 on white.
        assert contrast_ratio("#767676", "#ffffff") >= 4.5
        assert contrast_ratio("#777777", "#ffffff") < 4.5
        with pytest.raises(ValueError):
            relative_luminance("#fff")

    @pytest.mark.parametrize(
        "label,foreground,background,required", CONTRAST_PAIRS,
        ids=[pair[0].replace(" ", "_") for pair in CONTRAST_PAIRS],
    )
    def test_every_pair_clears_wcag_aa(self, label, foreground, background, required):
        fg = TOKENS["colour"][foreground]
        bg = TOKENS["colour"][background]
        ratio = contrast_ratio(fg, bg)
        assert ratio >= required, (
            f"{label}: {fg} on {bg} measures {ratio:.2f} to 1 and needs "
            f"{required} to 1"
        )

    def test_the_contrast_report_covers_every_pair_and_passes(self):
        rows = contrast_report()
        assert len(rows) == len(CONTRAST_PAIRS)
        failures = [(label, fg, bg, ratio) for label, fg, bg, ratio, _, ok in rows
                    if not ok]
        assert not failures, failures

    def test_every_colour_is_a_six_digit_hex(self):
        for name, value in TOKENS["colour"].items():
            assert isinstance(value, str), name
            assert value.startswith("#") and len(value) == 7, f"{name} is {value}"
            int(value[1:], 16)

    def test_the_three_verdict_colours_are_distinct(self):
        """A reviewer reads the page by these, so they may not converge."""
        colour = TOKENS["colour"]
        pairs = [
            ("deficiency_bg", "clear_bg"),
            ("deficiency_bg", "unverified_bg"),
            ("clear_bg", "unverified_bg"),
        ]
        for first, second in pairs:
            assert colour[first] != colour[second]
            ratio = contrast_ratio(colour[first], colour[second])
            assert ratio < 4.5, (
                "these are three backgrounds of similar lightness by design; if "
                "one has drifted dark the banner will read as a different state"
            )

    def test_the_font_stacks_are_local_only(self):
        """A web font is a network request while the page renders."""
        for stack in (FONT_SANS, FONT_MONO, TOKENS["font"]["sans"],
                      TOKENS["font"]["mono"]):
            lowered = stack.lower()
            for banned in ("http", "googleapis", "cdn.", "@import", "url("):
                assert banned not in lowered, f"{stack} references {banned}"
        assert "sans-serif" in FONT_SANS
        assert "monospace" in FONT_MONO

    def test_the_type_and_space_scales_ascend(self):
        for scale in ("type_scale", "space", "radius"):
            values = list(TOKENS[scale].values())
            assert values == sorted(values), f"{scale} is not in ascending order"
            assert all(v > 0 for v in values)

    def test_the_verdict_is_the_largest_thing_on_the_screen(self):
        """It is read from the back of a room."""
        scale = TOKENS["type_scale"]
        assert scale["verdict"] > scale["title"] > scale["section"]
        assert scale["verdict"] >= 40

    def test_the_module_owns_the_tokens_rather_than_the_surfaces(self):
        """Both surfaces have to be able to import every value they need."""
        for group in ("colour", "type_scale", "space", "font", "radius",
                      "line_height", "weight", "border", "sponsor_strip"):
            assert TOKENS[group], f"{group} is empty"


class TestSponsorStrip:
    def test_the_dark_band_carries_the_white_wordmark(self):
        """FSAII is a white wordmark and disappears on a light band."""
        band = TOKENS["colour"]["band"]
        assert contrast_ratio("#ffffff", band) >= 4.5, (
            f"the sponsor band {band} is too light for a white wordmark"
        )

    def test_the_strip_fits_the_sidebar(self):
        """About 300 pixels of usable width, or the circles collapse to dots."""
        strip = TOKENS["sponsor_strip"]
        circles = 3 * strip["circular_logo_height"] + 2 * strip["gap"]
        assert circles <= strip["max_width"], (
            f"three circular marks need {circles}px and the sidebar has "
            f"{strip['max_width']}px"
        )

    def test_the_circular_marks_are_given_more_height_than_the_wordmark(self):
        """A circle carries less ink than a rectangle of the same height."""
        strip = TOKENS["sponsor_strip"]
        assert strip["circular_logo_height"] > strip["wordmark_height"]
        assert strip["circular_logo_height"] <= strip["wordmark_height"] * 1.35, (
            "lifted so far that the wordmark reads as an afterthought"
        )

    def test_the_wordmark_is_still_legible_at_strip_height(self):
        """1.95 to 1, so its height is what limits it, not its width."""
        pytest.importorskip("PIL")
        from PIL import Image

        wordmark = Image.open(asset_path("fsaii-logo.png"))
        aspect = wordmark.width / wordmark.height
        assert 1.8 < aspect < 2.1, f"the wordmark aspect changed to {aspect:.2f}"
        rendered_width = TOKENS["sponsor_strip"]["wordmark_height"] * aspect
        assert rendered_width <= TOKENS["sponsor_strip"]["max_width"]
        assert rendered_width >= 70, "the wordmark is too small to read"


class TestNoSponsorLogoInTheReportBody:
    """A reviewer prints or forwards the report.

    A detached page carrying the state seal reads as an official finding, and this
    tool is not a DNREC product. The report body may carry the map and nothing
    else, so the strip lives on the console around it.
    """

    def test_the_rendered_report_carries_no_sponsor_logo(self):
        from septic.report import compose as compose_mod
        from septic.report import render as render_mod
        from septic.rules import engine

        composed = compose_mod.compose(engine.evaluate({"perc_rate": 30}))
        html = render_mod.render_html(composed)
        for name in ASSET_FILES:
            payload = logo_data_uri(name).split(";base64,", 1)[1][:64]
            assert payload not in html, f"{name} is inlined in the report body"

    def test_the_renderer_does_not_import_the_logo_loader(self):
        source = (ROOT / "src" / "septic" / "report" / "render.py").read_text(
            encoding="utf-8"
        )
        assert "logo_data_uri" not in source
