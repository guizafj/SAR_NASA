"""Every rendered number must carry a unit or say it has none.

The submission checklist claims "units and sources always visible". Three render paths in
`src/ui/map_view.py` printed the number alone when the artefact omitted `unit`, which made the claim
false as written. These tests pin the fallback at each site.
"""
from __future__ import annotations

import pytest

from src.ui.formatters import UNIT_NOT_DECLARED
from src.ui.map_view import _figure_line, _render_kpis, detection_caption


class _FakeColumn:
    def __init__(self) -> None:
        self.bodies: list[str] = []

    def markdown(self, body: str, **_kwargs: object) -> None:
        self.bodies.append(body)


def test_detection_caption_states_a_missing_unit() -> None:
    text = detection_caption(False, 1.5, None)
    assert text is not None
    assert UNIT_NOT_DECLARED in text, text


def test_figure_line_states_a_missing_unit() -> None:
    row = _figure_line("Velocity", -62.065, None)
    assert UNIT_NOT_DECLARED in row, row


def test_figure_line_stays_unitless_when_dimensionless() -> None:
    row = _figure_line("Ratio", 41.377, None, dimensionless=True)
    assert UNIT_NOT_DECLARED not in row, row


def test_kpi_card_states_a_missing_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.ui.map_view as mv

    column = _FakeColumn()
    monkeypatch.setattr(mv.st, "columns", lambda n: [column])
    monkeypatch.setattr(mv.st, "caption", lambda *a, **k: None)
    _render_kpis([{"label": "Median velocity", "value": -62.065, "unit": None, "provenance_id": "x"}])
    body = "".join(column.bodies)
    assert UNIT_NOT_DECLARED in body, body
