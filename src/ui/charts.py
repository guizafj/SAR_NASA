"""Series and land-use charts. No aggregations and no file reads."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from src.ui.kpi_cards import render_kpi_cards
from src.ui.formatters import (
    NOT_DECLARED,
    SOURCE_NOT_DECLARED,
    UNIT_NOT_DECLARED,
    format_number,
    humanize,
)
from src.ui.theme import active_palette, style_figure


def _call(loaders: Any, name: str, *args: Any) -> Any:
    function = getattr(loaders, name, None)
    if function is None:
        return None
    return function(*args)


def _line(series: list[dict[str, Any]] | None) -> None:
    if not series:
        st.caption("Series not declared by the backend.")
        return
    unit = next((row.get("unit") for row in series if row.get("unit")), None)
    source = next((row.get("provenance_id") for row in series if row.get("provenance_id")), None)
    palette = active_palette()
    dates = [row.get("date") for row in series]
    figure = go.Figure(
        go.Scatter(
            x=dates,
            y=[row.get("value") for row in series],
            name="Cumulative displacement",
            mode="lines+markers",
            line={"color": palette["accent"], "width": 2},
        )
    )
    if any(isinstance(row.get("seasonal_mm"), (int, float)) for row in series):
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=[row.get("seasonal_mm") for row in series],
                name="Seasonal component",
                mode="lines",
                line={"color": palette["coherence"], "width": 1.5, "dash": "dash"},
            )
        )
    style_figure(
        figure,
        height=280,
        x_title="Date",
        y_title=(
            f"Cumulative displacement ({unit})"
            if unit
            else f"Cumulative displacement ({UNIT_NOT_DECLARED})"
        ),
    )
    st.plotly_chart(figure, width="stretch", theme=None, config={"displayModeBar": False})
    trend = next(
        (
            row.get("trend_mm_yr")
            for row in series
            if isinstance(row.get("trend_mm_yr"), (int, float)) and not isinstance(row.get("trend_mm_yr"), bool)
        ),
        None,
    )
    trend_text = f" · Linear trend {format_number(trend)} mm/year" if trend is not None else ""
    scaled = any(row.get("scaled") for row in series)
    factor = next((row.get("scale_factor") for row in series if row.get("scaled")), None)
    scale_text = ""
    if scaled:
        factor_text = f" ×{factor:g}" if isinstance(factor, (int, float)) else ""
        scale_text = f" · scaled{factor_text} to the filtered subset"
    st.caption(
        f"Cumulative displacement · {unit or UNIT_NOT_DECLARED}{trend_text} · "
        f"Provenance ID {source or SOURCE_NOT_DECLARED}{scale_text}"
    )


def _bars(rows: list[dict[str, Any]] | None) -> None:
    if not rows:
        st.caption("Land-use bars not declared by the backend.")
        return
    unit = next((row.get("unit") for row in rows if row.get("unit")), None)
    source = next((row.get("provenance_id") for row in rows if row.get("provenance_id")), None)
    figure = go.Figure(
        go.Bar(
            x=[
                humanize(row.get("land_use")) if row.get("land_use") else NOT_DECLARED
                for row in rows
            ],
            y=[row.get("value") for row in rows],
            marker={"color": active_palette()["accent"]},
        )
    )
    style_figure(
        figure,
        height=280,
        x_title="Land use",
        y_title=f"Velocity ({unit})" if unit == "mm/year" else (
            f"value ({unit})" if unit else f"value ({UNIT_NOT_DECLARED})"
        ),
    )
    st.plotly_chart(figure, width="stretch", theme=None, config={"displayModeBar": False})
    st.caption(
        f"Median velocity by land use · {unit or UNIT_NOT_DECLARED} · "
        f"Provenance ID {source or SOURCE_NOT_DECLARED}"
    )


def render_series_tab(filters: dict[str, Any], loaders: Any, meta: dict[str, Any] | None) -> None:
    # The persistent demo banner lives in app.py; do not duplicate it per tab.
    del meta
    case_id = filters.get("case_id")
    render_kpi_cards(_call(loaders, "get_series_kpis", case_id, filters))
    _line(_call(loaders, "get_case_series", case_id, filters))
    _bars(_call(loaders, "get_land_use_bars", case_id, filters))
