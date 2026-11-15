"""Context layers. Empty unless Rol 3 has delivered the GeoJSON.

Presentation only: reads the loader payload, never the artefact files.
"""

from __future__ import annotations

import html
from typing import Any

import pandas as pd
import streamlit as st

from src.ui.formatters import CONTEXT_LAYER_GLOSSARY, humanize, translate_feature_name

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


def _visible_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if "name" not in frame.columns:
        return frame
    filled = frame["name"].astype(str).str.strip()
    if filled.eq("").all() or filled.eq("None").all():
        return frame.drop(columns=["name"])
    return frame


def _render_table(frame: pd.DataFrame, provenance: str | None = None) -> str:
    shown = _visible_frame(frame)
    note = (
        f'<p class="kpi-note">Provenance ID {html.escape(provenance)}</p>'
        if provenance
        else ""
    )
    headers = "".join(f"<th>{html.escape(humanize(col))}</th>" for col in shown.columns)
    body_rows: list[str] = []
    for row in shown.itertuples(index=False):
        cells = "".join(
            f"<td>{html.escape(str(val).replace(chr(10), ' ')) if str(val) not in ('', 'None', 'nan') else '—'}</td>"
            for val in row
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        f"{note}"
        '<div class="cockpit-table-wrap" role="region" aria-label="Context layers">'
        '<table class="cockpit-table" aria-label="Context layer properties">'
        f"<thead><tr>{headers}</tr></thead>"
        f'<tbody>{"".join(body_rows)}</tbody>'
        "</table></div>"
    )


def _stat_card(column: Any, label: str, value: str, note: str | None = None) -> None:
    note_html = f'<p class="kpi-note">{html.escape(note)}</p>' if note else ""
    column.markdown(
        '<div class="cockpit-card">'
        f'<p class="kicker">{html.escape(label)}</p>'
        f'<p class="kpi-value">{html.escape(value)}</p>'
        f"{note_html}</div>",
        unsafe_allow_html=True,
    )


def _layer_counts(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, int]:
    """Prefer the loader summary; otherwise count the delivered features."""
    layers = summary.get("layers") if isinstance(summary, dict) else None
    if isinstance(layers, dict) and layers:
        return {str(key): int(value) for key, value in layers.items()}
    counts: dict[str, int] = {}
    for row in rows:
        layer = str(row.get("layer") or "")
        if layer:
            counts[layer] = counts.get(layer, 0) + 1
    return counts


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
            '<p class="kicker">Context</p>'
            '<p class="empty-prompt">No context layers are available for this case.</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = _features(payload)
    counts = _layer_counts(rows, summary)
    total = summary.get("n_features")
    if not isinstance(total, int) or isinstance(total, bool):
        total = len(rows)
    breakdown = ", ".join(
        f"{humanize(layer)} {count}" for layer, count in sorted(counts.items())
    )
    columns = st.columns(3)
    _stat_card(columns[0], "Delivered features", f"{total}", "GeoJSON features")
    _stat_card(columns[1], "Delivered layers", f"{len(counts)}", "distinct layer values")
    _stat_card(columns[2], "Layer counts", breakdown or "none", None)

    if not rows:
        return
    frame = pd.DataFrame(rows, columns=list(_TABLE_FIELDS)).fillna("")
    provenance_ids = sorted(
        {str(value) for value in frame["provenance_id"] if str(value) not in ("", "None", "nan")}
    )
    shown = frame.drop(columns=["provenance_id"]) if len(provenance_ids) == 1 else frame.copy()
    single_source = provenance_ids[0] if len(provenance_ids) == 1 else None
    if provenance_ids:
        st.caption(f"Provenance ID {' · '.join(provenance_ids)}")
    for column in ("layer", "class"):
        if column in shown.columns:
            shown[column] = shown[column].map(lambda value: humanize(value) if str(value) else value)
    if "name" in shown.columns:
        shown["name"] = shown["name"].map(translate_feature_name)
    glossary_items = "".join(
        f'<p class="detail-line"><strong>{html.escape(humanize(layer_key))}:</strong> '
        f"{html.escape(CONTEXT_LAYER_GLOSSARY[layer_key])}</p>"
        for layer_key in sorted(counts)
        if layer_key in CONTEXT_LAYER_GLOSSARY
    )
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Context layers</p>'
        '<p class="kpi-note">Delivered context only, not a hazard assessment. '
        "A context class is not the Land use filter.</p>"
        f"{glossary_items}"
        f"<p>{len(rows)} features delivered</p>"
        f"{_render_table(shown, single_source)}"
        "</div>",
        unsafe_allow_html=True,
    )
