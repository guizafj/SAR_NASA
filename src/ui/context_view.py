"""Context layers. Empty unless Rol 3 has delivered the GeoJSON.

Presentation only: reads the loader payload, never the artefact files. The proximity
cross-reference (`get_context_exposure`) says which H3 cell is nearest to each feature
and what that cell's velocity band is; it is **not** a hazard assessment.
"""

from __future__ import annotations

import html
from typing import Any, Mapping

import pandas as pd
import streamlit as st

from src.ui.formatters import (
    CONTEXT_LAYER_GLOSSARY,
    choice_label,
    humanize,
    risk_label,
    translate_feature_name,
)

_TABLE_FIELDS = (
    "layer",
    "class",
    "name",
    "source",
    "nearest_cell_id",
    "risk_level",
    "distance_m",
    "provenance_id",
)
_NO_VALUE = ("", "None", "nan")


def _features(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    geo = payload.get("geojson")
    features = geo.get("features") if isinstance(geo, dict) else None
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
        risk = str(getattr(row, "risk_level", "") or "")
        row_class = ' class="row-alert"' if risk == "critico" else (
            ' class="row-warn"' if risk == "moderado" else ""
        )
        cells = []
        for col, val in zip(shown.columns, row):
            text = str(val).replace(chr(10), " ")
            if text in _NO_VALUE:
                cells.append("<td>—</td>")
            elif col == "risk_level":
                cells.append(f"<td>{html.escape(risk_label(text))}</td>")
            else:
                cells.append(f"<td>{html.escape(text)}</td>")
        body_rows.append(f"<tr{row_class}>{''.join(cells)}</tr>")
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


def _load(loaders: Any, case_id: Any, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """Prefer the exposure hook (nearest cell + risk); fall back to the plain GeoJSON."""
    exposure_fn = getattr(loaders, "get_context_exposure", None)
    if exposure_fn is not None:
        try:
            exposure = exposure_fn(case_id, filters)
        except TypeError:
            exposure = None
        if isinstance(exposure, Mapping) and exposure.get("features"):
            summary = exposure.get("summary") if isinstance(exposure.get("summary"), Mapping) else {}
            return list(exposure["features"]), dict(summary), "exposure"
    loader = getattr(loaders, "load_context_layers", None)
    if loader is None:
        return [], {}, None
    try:
        payload = loader(case_id)
    except TypeError:
        payload = loader()
    if not payload:
        return [], {}, None
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return _features(payload), summary, "plain"


def render_hazard_tab(filters: dict[str, Any], loaders: Any) -> None:
    case_id = filters.get("case_id")
    rows, summary, source = _load(loaders, case_id, filters)
    if not rows:
        st.markdown(
            '<div class="cockpit-card is-empty">'
            '<p class="kicker">Context</p>'
            '<p class="empty-prompt">No context layers are available for this case.</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

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

    frame = pd.DataFrame(rows, columns=list(_TABLE_FIELDS)).fillna("")
    for column in ("layer", "class"):
        frame[column] = frame[column].map(lambda value: humanize(value) if str(value) else value)
    if "name" in frame.columns:
        frame["name"] = frame["name"].map(translate_feature_name)

    # Display-only filters over the delivered rows. Keys are tab-scoped: never reuse
    # the global `filter_*` keys or the map would change underneath the user.
    control_cols = st.columns(3)
    with control_cols[0]:
        layers_present = sorted(str(value) for value in frame["layer"].unique() if str(value))
        picked_layers = st.multiselect(
            "Layer",
            options=layers_present,
            key=f"ctx-layer-{case_id}",
        )
    with control_cols[1]:
        classes_present = sorted(str(value) for value in frame["class"].unique() if str(value))
        picked_class = st.selectbox(
            "Class",
            options=["", *classes_present],
            format_func=choice_label,
            key=f"ctx-class-{case_id}",
        )
    with control_cols[2]:
        picked_risk = st.selectbox(
            "Nearest cell risk",
            options=["", "estable", "moderado", "critico"],
            format_func=choice_label,
            key=f"ctx-risk-{case_id}",
        )

    # One provenance line per view when every row shares the same case artefact;
    # repeating the identical id in 12 rows is noise, not traceability.
    provenance_ids = sorted(
        {str(value) for value in frame["provenance_id"] if str(value) not in ("", "None", "nan")}
    )
    single_source = provenance_ids[0] if len(provenance_ids) == 1 else None
    if single_source:
        frame = frame.drop(columns=["provenance_id"])

    shown = frame
    if picked_layers:
        shown = shown[shown["layer"].isin(picked_layers)]
    if picked_class:
        shown = shown[shown["class"] == humanize(picked_class)]
    if picked_risk:
        shown = shown[shown["risk_level"] == picked_risk]

    has_exposure = source == "exposure" and "risk_level" in shown.columns
    critical = int((shown["risk_level"] == "critico").sum()) if has_exposure else 0
    moderate = int((shown["risk_level"] == "moderado").sum()) if has_exposure else 0

    note = (
        "Delivered context only, not a hazard assessment. "
        "A context class is not the Land use filter."
    )
    exposure_note = (
        f'<p class="kpi-note">Nearest filtered H3 cell per feature — {critical} on Critical, '
        f"{moderate} on Moderate. Proximity is informative, not an exposure model.</p>"
        if has_exposure
        else ""
    )
    st.markdown('<p class="kicker">Context layers</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="kpi-note">{html.escape(note)}</p>',
        unsafe_allow_html=True,
    )
    if exposure_note:
        st.markdown(exposure_note, unsafe_allow_html=True)
    st.markdown(
        f"<p>{len(shown)} features shown of {len(frame)}</p>",
        unsafe_allow_html=True,
    )
    st.markdown(_render_table(shown, single_source), unsafe_allow_html=True)

    text_cols = st.columns(2)
    with text_cols[0]:
        st.download_button(
            "Export context (CSV)",
            data=shown.to_csv(index=False),
            file_name=f"{case_id}_context.csv",
            mime="text/csv",
            key=f"ctx-export-{case_id}",
        )
    with text_cols[1]:
        jumpable = shown[
            ~shown["nearest_cell_id"].astype(str).isin([""] + list(_NO_VALUE))
        ] if "nearest_cell_id" in shown.columns else shown.iloc[0:0]
        if not jumpable.empty:
            options = {
                f"{row['name'] or row['class']} · {risk_label(row['risk_level'])}": row[
                    "nearest_cell_id"
                ]
                for _, row in jumpable.iterrows()
            }
            picked = st.selectbox(
                "Jump to nearest cell",
                options=["", *options],
                format_func=lambda key: "Select a feature" if key == "" else key,
                key=f"ctx-jump-{case_id}",
            )
            clicked = st.button("Jump to cell on Map", key=f"ctx-jump-btn-{case_id}")
            if picked and clicked:
                st.session_state.selected_cell_id = options[picked]
                st.session_state[f"inspector_cell_{case_id}"] = ""
                st.rerun()
