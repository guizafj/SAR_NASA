"""Every rendered number must carry a unit, or say it has none.

The submission checklist claims "every rendered number carries a unit or says it is not declared". Three render
paths in `src/ui/map_view.py` printed the number alone when the artefact omitted `unit`, which made that claim false
as written.

`main` then refactored two of the three sites away: the detection caption stopped printing a limit at all, and the
KPI renderer moved into the shared `src/ui/components.py::kpi_card`. This file follows the fixes rather than the old
file layout — the point is the property, not the location.

Note what is deliberately *not* asserted here: the KPI card's markup and the caption's wording are presentation
choices owned by the UI role. These tests only pin that no path can print a number with a silent unit.
"""
from __future__ import annotations

from src.ui.components import kpi_card
from src.ui.formatters import UNIT_NOT_DECLARED
from src.ui.map_view import _figure_line, detection_caption


def test_figure_line_states_a_missing_unit() -> None:
    row = _figure_line("Velocity", -62.065, None)
    assert UNIT_NOT_DECLARED in row, row


def test_figure_line_stays_unitless_when_dimensionless() -> None:
    """A ratio must not acquire a fabricated unit."""
    row = _figure_line("Ratio", 41.377, None, dimensionless=True)
    assert UNIT_NOT_DECLARED not in row, row


def test_kpi_card_states_a_missing_unit() -> None:
    """The shared card component is where the KPI path renders now."""
    markup = kpi_card("Median velocity", -62.065, None)
    assert UNIT_NOT_DECLARED in markup, markup


def test_kpi_card_prints_a_declared_unit() -> None:
    markup = kpi_card("Median velocity", -62.065, "mm/year")
    assert "mm/year" in markup, markup
    assert UNIT_NOT_DECLARED not in markup, markup


def test_detection_caption_prints_no_bare_number() -> None:
    """`main` removed the numeric limit from this caption; with no number there is nothing to unit.

    Asserting "no digits" rather than the exact sentence keeps the UI free to reword it while pinning the property
    that made the old version wrong.
    """
    text = detection_caption(False, 1.5, None)
    assert text is not None
    assert not any(character.isdigit() for character in text), text
