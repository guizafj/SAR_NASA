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
