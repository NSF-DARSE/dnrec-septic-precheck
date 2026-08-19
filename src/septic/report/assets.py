"""Local assets and the single design token set.

Two things live here and nothing else: a loader that turns a file in assets/ into
a base64 data URI, and the design tokens that both the console and the HTML report
read. Everything else about presentation belongs to the surface rendering it.

Why a data URI rather than a path. The report is viewed two ways that both break a
relative src: embedded in the console through a sandboxed iframe, where a relative
path resolves against the server rather than the file, and opened from the file
system after being moved. Inlining the bytes makes both work, and it is also what
the no remote reference rule requires: the console has to look identical on venue
wifi that has failed, so nothing may be fetched while a page renders.

Why one token set. app.py and render.py have drifted apart before, and a reviewer
reads this page by colour: one colour means a deficiency was found, one means
nothing was found, one means the tool has no answer. Two copies of those values
eventually disagree, and the surface nobody is looking at is the one that goes
wrong. So the colours, the type scale, the spacing scale and the font stack are
defined once, here, and imported.

Every foreground and background pair in CONTRAST_PAIRS is checked by
tests/test_assets.py, which computes the WCAG ratio rather than trusting a comment.
This is projected: a projector washes out low contrast that looks fine on a laptop,
and the maps module has already had to replace a pale yellow that measured 1.8 to 1.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parents[3] / "assets"

# The files this module will load, and what each one is for. Naming them is also
# the allowlist: logo_data_uri takes a name, so it must not be able to read an
# arbitrary path.
ASSET_FILES: dict[str, str] = {
    "dnrec-logo.png": "Delaware Department of Natural Resources and Environmental Control",
    "fsaii-logo.png": "First State AI Institute",
    "udel-logo.png": "University of Delaware",
    "delaware-seal.png": "Great Seal of the State of Delaware",
    "delaware-seal.svg": "Great Seal of the State of Delaware, vector source",
    "favicon.png": "DNREC mark, cropped for the browser tab",
}

MIME = {".png": "image/png", ".svg": "image/svg+xml"}


def asset_path(name: str) -> Path:
    """Resolve an asset by name, refusing anything not in the allowlist."""
    if name not in ASSET_FILES:
        raise KeyError(
            f"unknown asset {name!r}. Known assets: {sorted(ASSET_FILES)}"
        )
    path = ASSETS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing. Run python scripts/build_assets.py to rebuild "
            f"the derived files."
        )
    return path


@lru_cache(maxsize=None)
def logo_data_uri(name: str) -> str:
    """One asset as a base64 data URI, read once per process.

    Cached because the console re-runs its whole script on every widget
    interaction. Without the cache a reviewer ticking a checkbox would re-read
    and re-encode five files, and the seal alone is 148 KB.
    """
    path = asset_path(name)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{MIME[path.suffix.lower()]};base64,{encoded}"


# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

# System fonts only. A web font is a network request at render time, which is
# forbidden here, and a font that fails to load reflows the page in front of an
# audience. Segoe UI first because the demo machine is Windows.
FONT_SANS = (
    '"Segoe UI", "Inter", system-ui, -apple-system, "Helvetica Neue", Arial, '
    "sans-serif"
)
# Values and citations are read digit by digit and compared against a page of the
# regulation, so they get a real monospace stack and tabular figures.
FONT_MONO = (
    'ui-monospace, "Cascadia Mono", SFMono-Regular, Menlo, Consolas, '
    '"Liberation Mono", monospace'
)

TOKENS: dict[str, dict] = {
    "colour": {
        # Text and structure.
        "ink": "#111827",
        "muted": "#4b5563",
        "line": "#d1d5db",
        "surface": "#ffffff",
        "surface_sunken": "#f3f4f6",
        "surface_quote": "#f9fafb",
        # The verdict colours. Their meanings are load bearing and a reviewer
        # reads the page by them, so the hues may be tuned for contrast but never
        # reassigned. One colour for a deficiency found, one for nothing found,
        # one for no answer.
        "deficiency_fg": "#7f1d1d",
        "deficiency_bg": "#fee2e2",
        "clear_fg": "#1b4332",
        "clear_bg": "#d8f3dc",
        "unverified_fg": "#78350f",
        "unverified_bg": "#fef3c7",
        # Accents used inside findings. edge_* are borders and rules, never text.
        "deficiency_edge": "#b91c1c",
        "clear_edge": "#15803d",
        "unverified_edge": "#b45309",
        "out_of_scope_edge": "#6b7280",
        "notice_fg": "#92400e",
        "notice_bg": "#fffbeb",
        "remedy_fg": "#1e3a8a",
        "remedy_bg": "#eff6ff",
        "citation_fg": "#374151",
        # The sponsor and attribution band. Dark deliberately: the FSAII wordmark
        # is white, so on a light band it disappears, and altering a sponsor's
        # mark to suit our layout is not an option. A dark band also separates the
        # attribution strip from the product identity, which is the point of the
        # strip: this tool is not DNREC software and must not look like it.
        "band": "#111827",
        "on_band": "#f9fafb",
        "on_band_muted": "#d1d5db",
    },
    # Pixels, not rem. The report is a standalone file opened at whatever zoom a
    # reviewer left the browser on, and the console is projected, so the sizes are
    # absolute on purpose.
    "type_scale": {
        "micro": 13,       # uppercase table headings and labels
        "caption": 15,     # captions, asides, provenance
        "body": 17,        # dense body text and tables
        "body_large": 19,  # report prose and quoted regulation text
        "subhead": 22,     # the requirement line of a finding
        "section": 27,     # section headings, the coverage figure
        "title": 34,       # page and report title
        "verdict": 44,     # the headline, read from the back of a room
    },
    "line_height": {"tight": 1.15, "normal": 1.5, "relaxed": 1.6},
    "weight": {"regular": 400, "medium": 600, "bold": 700},
    # A 4 pixel base, so everything lines up on one grid.
    "space": {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32, "xxxl": 48},
    "radius": {"sm": 6, "md": 9, "lg": 12},
    "border": {"hairline": 1, "rule": 3, "accent": 7},
    "font": {"sans": FONT_SANS, "mono": FONT_MONO},
    # The Streamlit shell the console is drawn inside. These are measurements of
    # somebody else's chrome rather than design choices, but they live here for
    # the same reason the colours do: app.py is not allowed to carry a value of
    # its own, and .streamlit/config.toml is generated from this table.
    #
    # Streamlit 1.61 draws a fixed toolbar across the top of the main column,
    # 60 pixels tall in Chrome at 1400x900, and it overlays the content rather
    # than pushing it down. Clearing it cost 76 pixels of padding, which read as
    # a band of empty grey above the product identity, and what sat in that band
    # was a Deploy button and a Streamlit menu. Neither belongs on a console
    # handed to a permitting reviewer, so the toolbar is hidden in app.py and
    # the clearance is now just the gap the band needs from the top edge.
    # header_height stays at zero as the measured height of what is drawn there,
    # which is nothing, and the test pairs the two numbers so restoring the
    # toolbar restores the padding that clears it.
    "chrome": {
        "header_height": 0,
        "top_clearance": 20,
    },
    # The sponsor strip. FSAII is a horizontal wordmark at roughly 1.95 to 1 and
    # the other three are circular. They share one height band with width left
    # free, so the strip has one baseline and one cap line, and the circular marks
    # are given about 12 percent more height than the wordmark because a circle
    # carries visibly less ink than a rectangle of the same height. Judged against
    # a render at the sidebar's real width and again in the footer band.
    #
    # max_width is the tight case, the sidebar, where four marks in a row do not
    # fit: at a common 52 pixels they need 318 and there are about 300. Three
    # circular marks at 74 with a 20 pixel gap need 262, so the fallback lockup is
    # the circles on one row with the wordmark centred beneath. In the footer band
    # there is room for all four on one row, which is what the console uses.
    #
    # Both heights were raised about fifteen percent, from 64 and 56, after the
    # band was seen on a projector: at the old sizes the marks read as a footnote
    # from the back of a room. The ratio between them is unchanged.
    "sponsor_strip": {
        "circular_logo_height": 74,
        "wordmark_height": 64,
        "gap": 20,
        "max_width": 300,
        "layout": (
            "one row where the width allows it, otherwise three circular marks on "
            "one row with the FSAII wordmark centred beneath"
        ),
    },
}

# Every pair a surface may put together, with the ratio it has to clear. WCAG AA
# is 4.5 to 1 for body text and 3 to 1 for large text, where large means at least
# 24 pixels or 19 pixels bold. Borders and non text marks are not covered by AA at
# all, so the edge colours are held to 3 to 1 against their own background anyway:
# they carry meaning here, and a border a projector loses is a meaning lost.
CONTRAST_PAIRS: tuple[tuple[str, str, str, float], ...] = (
    ("body text on the page", "ink", "surface", 4.5),
    ("secondary text on the page", "muted", "surface", 4.5),
    ("body text on a sunken panel", "ink", "surface_sunken", 4.5),
    ("secondary text on a sunken panel", "muted", "surface_sunken", 4.5),
    ("citation text on a quote panel", "citation_fg", "surface_quote", 4.5),
    ("verdict headline, deficiencies found", "deficiency_fg", "deficiency_bg", 4.5),
    ("verdict headline, nothing found", "clear_fg", "clear_bg", 4.5),
    ("verdict headline, cannot verify", "unverified_fg", "unverified_bg", 4.5),
    ("notice text", "notice_fg", "notice_bg", 4.5),
    ("remedy text", "remedy_fg", "remedy_bg", 4.5),
    ("attribution text on the band", "on_band", "band", 4.5),
    ("secondary attribution text on the band", "on_band_muted", "band", 4.5),
    ("deficiency edge", "deficiency_edge", "surface", 3.0),
    ("nothing found edge", "clear_edge", "surface", 3.0),
    ("cannot verify edge", "unverified_edge", "surface", 3.0),
    ("out of scope edge on a sunken panel", "out_of_scope_edge", "surface_sunken", 3.0),
)


# The palette the Streamlit shell itself is drawn from, as a mapping from the
# config option to the token it is taken from. .streamlit/config.toml is
# generated from this table by scripts/build_theme.py, and tests/test_theme.py
# fails if the committed file and these tokens ever disagree.
#
# Why the file has to exist at all. With no theme config, Streamlit renders its
# own chrome from whatever the operating system reports. On a machine set to dark
# mode that drew a near black shell and then painted this light palette on top of
# it: body copy at #4b5563 on #0e1117 measures 1.7 to 1, and the three verdict
# names, which are the most important words in the product, could not be read at
# all. A light theme is the decision here, not a default to fall back to, so it
# is pinned rather than inherited from whoever is presenting.
STREAMLIT_THEME: tuple[tuple[str, str], ...] = (
    ("primaryColor", "remedy_fg"),
    ("backgroundColor", "surface"),
    ("secondaryBackgroundColor", "surface_sunken"),
    ("textColor", "ink"),
    ("borderColor", "line"),
    ("linkColor", "remedy_fg"),
    ("codeBackgroundColor", "surface_quote"),
    ("codeTextColor", "citation_fg"),
)


def _channel(value: int) -> float:
    channel = value / 255.0
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: str) -> float:
    """WCAG 2.1 relative luminance of a #rrggbb colour."""
    hex_digits = colour.lstrip("#")
    if len(hex_digits) != 6:
        raise ValueError(f"expected #rrggbb, got {colour!r}")
    red, green, blue = (int(hex_digits[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.1 contrast ratio between two #rrggbb colours, 1.0 to 21.0."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def contrast_report() -> list[tuple[str, str, str, float, float, bool]]:
    """Every declared pair with its measured ratio. Used by the tests and by eye."""
    rows = []
    for label, fg_name, bg_name, required in CONTRAST_PAIRS:
        fg = TOKENS["colour"][fg_name]
        bg = TOKENS["colour"][bg_name]
        ratio = contrast_ratio(fg, bg)
        rows.append((label, fg, bg, ratio, required, ratio >= required))
    return rows
