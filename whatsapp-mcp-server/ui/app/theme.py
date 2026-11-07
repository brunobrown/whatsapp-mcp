"""Colours, type and the font of the interface.

Direction: ink on paper. Quiet neutrals tinted toward green, one action colour, hairlines instead of shadows. Every
colour of the interface comes from here (a test forbids a literal anywhere else) and every pair that carries text
or marks a control is checked for contrast, in both themes. Flet is imported only where a Flet object is built.
"""
from typing import Dict, List, Tuple

TITLE = "WhatsApp MCP"
FONT_FAMILY = "Atkinson"
# family name -> file inside the assets folder (Atkinson Hyperlegible Next, SIL OFL; fonts/OFL.txt)
FONT_FILES = {
    "Atkinson": "fonts/AtkinsonHyperlegibleNext-Regular.ttf",
    "Atkinson SemiBold": "fonts/AtkinsonHyperlegibleNext-SemiBold.ttf",
    "Atkinson Bold": "fonts/AtkinsonHyperlegibleNext-Bold.ttf",
}

LIGHT: Dict[str, str] = {
    "paper": "#F4F3EE", "surface": "#FBFAF6", "ink": "#1B2421", "muted": "#4A5551",
    "action": "#1F5C54", "on_action": "#FBFAF6", "warning": "#8A5A00", "error": "#A3281C", "on_error": "#FBFAF6",
    "line": "#D5D3C8", "outline": "#76807B",
}
DARK: Dict[str, str] = {
    "paper": "#14191A", "surface": "#1C2324", "ink": "#E6E8E3", "muted": "#A9B3AE",
    "action": "#6FB8A8", "on_action": "#10201D", "warning": "#E0B060", "error": "#F0877B", "on_error": "#2A0E0A",
    "line": "#36403F", "outline": "#7C8884",
}

# (foreground, background): text needs 4.5:1; the border of a control and an icon that stands alone need 3:1.
TEXT_PAIRS: List[Tuple[str, str]] = [
    ("ink", "paper"), ("ink", "surface"), ("muted", "paper"), ("muted", "surface"),
    ("action", "paper"), ("action", "surface"), ("on_action", "action"),
    ("warning", "paper"), ("warning", "surface"), ("error", "paper"), ("error", "surface"), ("on_error", "error"),
]
COMPONENT_PAIRS: List[Tuple[str, str]] = [("outline", "paper"), ("outline", "surface"), ("action", "paper"), ("action", "surface")]


def _luminance(colour: str) -> float:
    channels = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    """The WCAG contrast ratio between two #RRGGBB colours."""
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def failures(tokens: Dict[str, str]) -> List[str]:
    """The pairs of a palette that are below their minimum, as text."""
    out = []
    for pairs, minimum in ((TEXT_PAIRS, 4.5), (COMPONENT_PAIRS, 3.0)):
        for fg, bg in pairs:
            ratio = contrast(tokens[fg], tokens[bg])
            if ratio < minimum:
                out.append(f"{fg} on {bg} is {ratio:.2f}:1, below {minimum}:1")
    return out


def _text_theme(ft, tokens: Dict[str, str]):
    """A modular scale (about 1.2) from a 16 px body; nothing below 14 px."""
    def style(size, family=FONT_FAMILY, colour="ink", height=1.4):
        return ft.TextStyle(size=size, font_family=family, color=tokens[colour], height=height)
    return ft.TextTheme(
        headline_small=style(28, "Atkinson Bold", height=1.25), title_large=style(23, "Atkinson Bold", height=1.3),
        title_medium=style(19, "Atkinson SemiBold", height=1.3), title_small=style(16, "Atkinson SemiBold"),
        body_large=style(18), body_medium=style(16), body_small=style(14, colour="muted"),
        label_large=style(14, "Atkinson SemiBold"), label_medium=style(14, colour="muted"), label_small=style(14, colour="muted"))


def _theme(tokens: Dict[str, str]):
    import flet as ft      # noqa: PLC0415
    scheme = ft.ColorScheme(
        primary=tokens["action"], on_primary=tokens["on_action"], secondary=tokens["action"], on_secondary=tokens["on_action"],
        tertiary=tokens["warning"], error=tokens["error"], on_error=tokens["on_error"],
        surface=tokens["paper"], on_surface=tokens["ink"], on_surface_variant=tokens["muted"],
        surface_container_lowest=tokens["surface"], surface_container_low=tokens["surface"], surface_container=tokens["surface"],
        surface_container_high=tokens["surface"], surface_container_highest=tokens["surface"],
        outline=tokens["outline"], outline_variant=tokens["line"])
    return ft.Theme(color_scheme=scheme, font_family=FONT_FAMILY, text_theme=_text_theme(ft, tokens),
                    visual_density=ft.VisualDensity.COMFORTABLE)


def light_theme():
    return _theme(LIGHT)


def dark_theme():
    return _theme(DARK)


def configure(page) -> None:
    """Set the title, fonts and both themes once, at the entry point; the theme follows the system."""
    import flet as ft      # noqa: PLC0415
    page.title = TITLE
    page.fonts = dict(FONT_FILES)
    page.theme = light_theme()
    page.dark_theme = dark_theme()
    page.theme_mode = ft.ThemeMode.SYSTEM
