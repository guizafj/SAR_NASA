"""KPI presentation. Values, units and sources come from the loader."""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.ui.formatters import (
    SOURCE_NOT_DECLARED,
)
from src.ui.components import kpi_card


def render_kpi_cards(items: list[dict[str, Any]] | None) -> None:
    if not items:
        st.caption("KPIs not declared by the backend.")
        return
    columns = st.columns(len(items))
    sources: list[str] = []
    for column, item in zip(columns, items):
        source = item.get("provenance_id")
        if source:
            sources.append(str(source))
        column.markdown(
            kpi_card(
                item.get("label"),
                item.get("value"),
                item.get("unit"),
                note=item.get("note"),
                tooltip=item.get("tooltip") or item.get("help"),
                indicator=item.get("indicator"),
            ),
            unsafe_allow_html=True,
        )
    # One source line for the row instead of repeating the same id under every card.
    unique = list(dict.fromkeys(sources))
    st.caption(f"Provenance ID {' · '.join(unique) if unique else SOURCE_NOT_DECLARED}")
