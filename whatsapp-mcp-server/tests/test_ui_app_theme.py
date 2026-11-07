"""Colours, type and the font of the interface: contrast is a test, not a hope (UX-02)."""
import glob
import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.app import theme  # noqa: E402

SERVER = os.path.dirname(os.path.dirname(__file__))
THEMES = {"light": theme.LIGHT, "dark": theme.DARK}


def test_the_contrast_ratio_follows_wcag():  # UX-02
    assert theme.contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert theme.contrast("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert theme.contrast("#777777", "#777777") == pytest.approx(1.0)
    assert theme.contrast("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)         # the well known AA limit grey


def test_theme_contrast_meets_aa_in_both_themes():  # UX-02
    for name, tokens in THEMES.items():
        for fg, bg in theme.TEXT_PAIRS:
            assert theme.contrast(tokens[fg], tokens[bg]) >= 4.5, f"{name}: {fg} on {bg} = {theme.contrast(tokens[fg], tokens[bg]):.2f}"
        for fg, bg in theme.COMPONENT_PAIRS:
            assert theme.contrast(tokens[fg], tokens[bg]) >= 3.0, f"{name}: {fg} on {bg} = {theme.contrast(tokens[fg], tokens[bg]):.2f}"


def test_the_pairs_cover_every_colour_that_carries_text_or_marks_a_control():  # UX-02
    text_roles = {fg for fg, _ in theme.TEXT_PAIRS}
    assert {"ink", "muted", "action", "on_action", "warning", "error"} <= text_roles
    assert {fg for fg, _ in theme.COMPONENT_PAIRS} >= {"outline", "action"}
    assert theme.failures(theme.LIGHT) == [] and theme.failures(theme.DARK) == []


def test_a_palette_that_fails_is_reported_with_the_pair_and_the_ratio():  # UX-02
    weak = dict(theme.LIGHT, muted="#B8B8B0")

    problems = theme.failures(weak)

    assert problems and any("muted" in p and "paper" in p for p in problems)


def test_both_themes_define_the_same_tokens_as_plain_hex_colours():  # UX-02
    assert set(theme.LIGHT) == set(theme.DARK)
    for tokens in THEMES.values():
        assert all(re.fullmatch(r"#[0-9A-F]{6}", value) for value in tokens.values())


def test_no_colour_of_the_palette_is_pure_black_or_white():  # UX-02
    for tokens in THEMES.values():
        assert not {"#000000", "#FFFFFF"} & set(tokens.values())


def test_the_font_files_are_bundled_with_their_licence():  # UX-02
    fonts = os.path.join(SERVER, "ui", "assets")
    for family, relative in theme.FONT_FILES.items():
        assert os.path.getsize(os.path.join(fonts, relative)) > 10_000, family
    assert os.path.isfile(os.path.join(fonts, "fonts", "OFL.txt"))


def test_colours_come_only_from_the_theme_tokens():  # UX-02
    for path in glob.glob(os.path.join(SERVER, "ui", "app", "*.py")):
        if os.path.basename(path) == "theme.py":
            continue
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", open(path, encoding="utf-8").read()), path


ft = pytest.importorskip("flet")


def test_the_light_theme_uses_the_light_tokens_and_the_bundled_font():  # UX-02
    built = theme.light_theme()

    assert isinstance(built, ft.Theme) and built.font_family == theme.FONT_FAMILY
    assert built.color_scheme.primary == theme.LIGHT["action"] and built.color_scheme.on_primary == theme.LIGHT["on_action"]
    assert built.color_scheme.error == theme.LIGHT["error"] and built.color_scheme.outline == theme.LIGHT["outline"]


def test_the_dark_theme_uses_the_dark_tokens():  # UX-02
    built = theme.dark_theme()

    assert built.color_scheme.primary == theme.DARK["action"] and built.color_scheme.surface == theme.DARK["paper"]
    assert built.color_scheme.surface_container == theme.DARK["surface"]


def test_body_text_is_never_smaller_than_14_pixels():  # UX-02
    sizes = theme.light_theme().text_theme
    for style in (sizes.body_large, sizes.body_medium, sizes.body_small, sizes.label_large, sizes.label_medium):
        assert style.size >= 14


def test_configure_sets_the_fonts_the_two_themes_and_follows_the_system():  # UX-02
    page = SimpleNamespace()

    theme.configure(page)

    assert set(page.fonts) == set(theme.FONT_FILES) and page.theme_mode == ft.ThemeMode.SYSTEM
    assert page.theme.color_scheme.primary == theme.LIGHT["action"] and page.dark_theme.color_scheme.primary == theme.DARK["action"]
    assert page.title == theme.TITLE
