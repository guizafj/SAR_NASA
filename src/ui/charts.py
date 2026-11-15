"""Series and land-use charts. No aggregations and no file reads."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from src.ui.kpi_cards import render_kpi_cards
from src.ui.theme import active_palette, style_figure


def _call(loaders: Any, name: str, *args: Any) -> Any:
    function = getattr(loaders, name, None)
    if function is None:
        return None
    return function(*args)


def _line(series: list[dict[str, Any]] | None) -> None:
    if not series:
        st.caption("Serie no declarada por el backend.")
        return
    unit = next((row.get("unit") for row in series if row.get("unit")), None)
    source = next((row.get("provenance_id") for row in series if row.get("provenance_id")), None)
    figure = go.Figure(
        go.Scatter(
            x=[row.get("date") for row in series],
            y=[row.get("value") for row in series],
            mode="lines+markers",
            line={"color": active_palette()["accent"], "width": 2},
        )
    )
    style_figure(
        figure,
        height=280,
        x_title="date",
        y_title=f"value ({unit})" if unit else "value (unidad no declarada)",
    )
    st.plotly_chart(figure, width="stretch", theme=None, config={"displayModeBar": False})
    st.caption(f"serie · {unit or 'unidad no declarada'} · {source or 'fuente no declarada'}")


def _bars(rows: list[dict[str, Any]] | None) -> None:
    if not rows:
        st.caption("Barras por land_use no declaradas por el backend.")
        return
    unit = next((row.get("unit") for row in rows if row.get("unit")), None)
    source = next((row.get("provenance_id") for row in rows if row.get("provenance_id")), None)
    figure = go.Figure(
        go.Bar(
            x=[row.get("land_use") for row in rows],
            y=[row.get("value") for row in rows],
            marker={"color": active_palette()["accent"]},
        )
    )
    style_figure(
        figure,
        height=280,
        x_title="land_use",
        y_title=f"value ({unit})" if unit else "value (unidad no declarada)",
    )
    st.plotly_chart(figure, width="stretch", theme=None, config={"displayModeBar": False})
    st.caption(f"land_use · {unit or 'unidad no declarada'} · {source or 'fuente no declarada'}")


def render_series_tab(filters: dict[str, Any], loaders: Any, meta: dict[str, Any] | None) -> None:
    # The persistent demo banner lives in app.py; do not duplicate it per tab.
    del meta
    case_id = filters.get("case_id")
    render_kpi_cards(_call(loaders, "get_series_kpis", case_id, filters))
    _line(_call(loaders, "get_case_series", case_id, filters))
    _bars(_call(loaders, "get_land_use_bars", case_id, filters))
