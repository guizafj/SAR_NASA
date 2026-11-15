"""Design-system invariants for the Streamlit presentation layer."""

from __future__ import annotations

import re
from pathlib import Path

from src.ui import components, theme


ROOT = Path(__file__).resolve().parents[1]


def test_css_tokens_are_declared_and_used_by_components() -> None:
    css = theme._CSS
    for token in (
        "--space-1",
        "--space-2",
        "--radius-md",
        "--border-width",
        "--shadow-menu",
        "--type-xs",
        "--motion-fast",
    ):
        assert token in css
    assert "box-shadow: var(--shadow-menu)" in css
    assert "animation: cockpit-plot-in var(--motion-fast)" in css
    assert "font-size: var(--type-xs)" in css


def test_no_sub_eleven_pixel_css_type_sizes() -> None:
    sizes = re.findall(r"font-size:\s*([0-9.]+)px", theme._CSS)
    assert all(float(size) >= 11 for size in sizes)
    rem_sizes = re.findall(r"font-size:\s*([0-9.]+)rem", theme._CSS)
    assert all(float(size) * 16 >= 11 for size in rem_sizes)


def test_ui_hex_colors_are_owned_by_the_theme_module() -> None:
    color = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    for path in (ROOT / "src" / "ui").glob("*.py"):
        if path.name == "theme.py":
            continue
        assert not color.search(path.read_text()), path


def test_shared_components_escape_and_label_user_content() -> None:
    card = components.kpi_card("<value>", 4.2, "mm", tooltip='"note"', indicator="critical")
    assert "&lt;value&gt;" in card
    assert "title=\"&quot;note&quot;\"" in card
    assert "status-critical" in card
    assert 'role="status"' in components.status_badge("Critical", "critical")
    empty = components.skeleton("Loading values", 2)
    assert 'aria-label="Loading values"' in empty
    assert empty.count("skeleton-bar") == 2


def test_accessible_map_ramp_has_a_neutral_midpoint_and_distinct_endpoints() -> None:
    from src.ui.color_scale import color_for
    from src.ui.theme import MAP_RAMPS

    ramp = MAP_RAMPS["colorblind"]
    assert color_for(-1.0, -1.0, 1.0, ramp) == [0, 114, 178, 200]
    assert color_for(0.0, -1.0, 1.0, ramp) == [225, 225, 225, 200]
    assert color_for(1.0, -1.0, 1.0, ramp) == [213, 94, 0, 200]


def test_primary_and_secondary_text_tokens_meet_wcag_aa() -> None:
    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    for palette in (theme.DARK, theme.LIGHT):
        for foreground in ("text", "muted"):
            for background in ("bg", "surface"):
                values = sorted((luminance(palette[foreground]), luminance(palette[background])))
                ratio = (values[1] + 0.05) / (values[0] + 0.05)
                assert ratio >= 4.5, (foreground, background, ratio)
