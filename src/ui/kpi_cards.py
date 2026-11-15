"""KPI presentation. Values, units and sources come from the loader."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src.ui.formatters import (
    NOT_DECLARED,
    SOURCE_NOT_DECLARED,
    UNIT_NOT_DECLARED,
    format_note,
    format_number,
)


def _shown(value: Any) -> str:
    if value in (None, ""):
        return NOT_DECLARED
    return format_number(value)


def render_kpi_cards(items: list[dict[str, Any]] | None) -> None:
    if not items:
        st.caption("KPIs not declared by the backend.")
        return
    columns = st.columns(len(items))
    sources: list[str] = []
    for column, item in zip(columns, items):
        unit = item.get("unit")
        # A card must never print a bare number. Same fallback as the three sites fixed in `map_view.py`:
        # the artefact may omit `unit`, and "unit not declared" is information, an empty string is not.
        unit_text = f" {unit}" if unit else f" ({UNIT_NOT_DECLARED})"
        source = item.get("provenance_id")
        if source:
            sources.append(str(source))
        note = item.get("note") or ""
        note_html = (
            f'<p class="kpi-note">{html.escape(format_note(note))}</p>' if note else ""
        )
        column.markdown(
            '<div class="cockpit-card">'
            f'<p class="kicker">{html.escape(str(item.get("label") or "kpi"))}</p>'
            f'<p class="kpi-value">{html.escape(_shown(item.get("value")))}{html.escape(unit_text)}</p>'
            f"{note_html}</div>",
            unsafe_allow_html=True,
        )
    # One source line for the row instead of repeating the same id under every card.
    unique = list(dict.fromkeys(sources))
    st.caption(f"Provenance ID {' · '.join(unique) if unique else SOURCE_NOT_DECLARED}")
