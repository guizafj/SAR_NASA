"""The H3 payload must carry what the layer draws and the tooltip reads — and nothing else.

The frame holds ~21 columns per cell. The deck used to serialise all of them for 11 600 cells, so most
of a multi-megabyte payload was columns no consumer read. These tests pin the trim, and pin the tie
between the hover card's placeholders and the fields the record actually carries: a new `{field}` in
the tooltip that the record does not hold would render as `undefined` in the browser, silently.
"""
from __future__ import annotations

import pandas as pd

from src.ui.map_view import (
    _DECK_FIELDS,
    _MAP_TOOLTIP_TEXT,
    _TOOLTIP_FIELDS,
    _deck_records,
)


def _frame(rows: int = 4) -> pd.DataFrame:
    base = {
        "cell_id": [f"8949864802b{ i }ffff" for i in range(rows)],
        "color": [[180, 90, 70, 160] for _ in range(rows)],
        "height": [12.0] * rows,
        "delta_change": [-1.5] * rows,
        "unit": ["mm/year"] * rows,
        "velocity_mm_yr": [-62.065] * rows,
        "velocity_unit": ["mm/year"] * rows,
        "coherence": [0.83] * rows,
        "ratio_R": [41.377] * rows,
        "risk_level": ["estable"] * rows,
        "land_use": ["urban-residential"] * rows,
        "regime": ["inelastic"] * rows,
        # Columns the payload must NOT carry: a long source string and a full-precision float.
        "cell_land_use_source": ["OpenStreetMap contributors (ODbL 1.0), Overpass API extract"] * rows,
        "unused_metric": [1.2345678901234567] * rows,
    }
    return pd.DataFrame(base)


def test_every_tooltip_placeholder_is_carried_by_the_record() -> None:
    records = _deck_records(_frame(), extrude=False)
    assert records, "no records built"
    carried = set(records[0])
    missing = _TOOLTIP_FIELDS - carried
    assert not missing, f"tooltip prints fields the payload no longer carries: {sorted(missing)}"


def test_records_carry_nothing_the_layer_and_tooltip_do_not_need() -> None:
    records = _deck_records(_frame(), extrude=False)
    expected = set(_DECK_FIELDS) | {"risk_band", "land_use_label", "regime_label"}
    for record in records:
        extra = set(record) - expected
        assert not extra, f"payload carries unused columns: {sorted(extra)}"


def test_flat_mode_zeroes_the_height() -> None:
    records = _deck_records(_frame(), extrude=False)
    assert [r["height"] for r in records] == [0, 0, 0, 0]


def test_extruded_mode_keeps_the_loader_height() -> None:
    records = _deck_records(_frame(), extrude=True)
    assert [r["height"] for r in records] == [12.0, 12.0, 12.0, 12.0]


def test_the_tooltip_text_is_the_constant_the_records_are_derived_from() -> None:
    assert "{cell_id}" in _MAP_TOOLTIP_TEXT
    assert "risk_band" in _TOOLTIP_FIELDS and "land_use_label" in _TOOLTIP_FIELDS
