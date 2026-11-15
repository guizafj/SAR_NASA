"""Context layers. Empty unless Rol 3 has delivered the GeoJSON.

Presentation only: reads the loader payload, never the artefact files.
"""

from __future__ import annotations

import html
from typing import Any

import pandas as pd
import streamlit as st

from src.ui.formatters import format_json_block

_TABLE_FIELDS = ("layer", "class", "name", "source", "provenance_id")


def _features(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    geojson = payload.get("geojson") if isinstance(geojson := (payload or {}).get("geojson"), dict) else None
    features = geojson.get("features") if isinstance(geojson, dict) else None
    if not isinstance(features, list):
        return []
    rows: list[dict[str, Any]] = []
    for feature in features:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        rows.append({field: props.get(field) for field in _TABLE_FIELDS})
    return rows


def _render_table(frame: pd.DataFrame) -> str:
    headers = "".join(f"<th>{html.escape(str(col))}</th>" for col in frame.columns)
    body_rows: list[str] = []
    for row in frame.itertuples(index=False):
        cells = "".join(
            f"<td>{html.escape(str(val).replace(chr(10), ' '))}</td>" for val in row
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        '<div class="cockpit-table-wrap" role="region" aria-label="Capas contextuales">'
        '<table class="cockpit-table" aria-label="Propiedades de las capas contextuales">'
        f"<thead><tr>{headers}</tr></thead>"
        f'<tbody>{"".join(body_rows)}</tbody>'
        "</table></div>"
    )


def render_hazard_tab(filters: dict[str, Any], loaders: Any) -> None:
    loader = getattr(loaders, "load_context_layers", None)
    case_id = filters.get("case_id")
    if loader is None:
        payload = None
    else:
        try:
            payload = loader(case_id)
        except TypeError:
            payload = loader()
    if not payload:
        st.markdown(
            '<div class="cockpit-card is-empty">'
            '<p class="kicker">Contexto</p>'
            '<p class="empty-prompt">Sin context_layers.geojson. El Rol 3 no lo ha entregado.</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Capas entregadas</p>'
        f"<p>{html.escape(str(payload.get('path', 'declarada')))}</p>"
        f'{format_json_block(summary, "Resumen de las capas")}'
        "</div>",
        unsafe_allow_html=True,
    )

    rows = _features(payload)
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=list(_TABLE_FIELDS)).fillna("")
    layers = sorted(str(value) for value in frame["layer"].unique() if str(value))
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Capas contextuales</p>'
        f"<p>{len(rows)} features · {html.escape(', '.join(layers) or 'sin capas')}</p>"
        f"{_render_table(frame)}"
        "</div>",
        unsafe_allow_html=True,
    )
