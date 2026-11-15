"""KPI presentation. Values, units and sources come from the loader."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src.ui.formatters import NOT_DECLARED, SOURCE_NOT_DECLARED, UNIT_NOT_DECLARED


def _shown(value: Any) -> str:
    if value in (None, ""):
        return NOT_DECLARED
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def render_kpi_cards(items: list[dict[str, Any]] | None) -> None:
    if not items:
        st.caption("KPIs not declared by the backend.")
        return
    columns = st.columns(len(items))
    for column, item in zip(columns, items):
        unit = item.get("unit")
        unit_text = f" {unit}" if unit else f" ({UNIT_NOT_DECLARED})"
        source = item.get("provenance_id") or SOURCE_NOT_DECLARED
        note = item.get("note") or ""
        note_html = (
            f'<p class="kpi-note">{html.escape(str(note))}</p>' if note else ""
        )
        column.markdown(
            '<div class="cockpit-card">'
            f'<p class="kicker">{html.escape(str(item.get("label") or "kpi"))}</p>'
            f'<p class="kpi-value">{html.escape(_shown(item.get("value")))}{html.escape(unit_text)}</p>'
            f'<p class="kpi-source" title="{html.escape(str(source))}">{html.escape(str(source))}</p>'
            f"{note_html}</div>",
            unsafe_allow_html=True,
        )
