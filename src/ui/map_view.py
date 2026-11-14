"""Thin map presentation. No file reads and no geospatial calculations.

Colors, height, view and cell detail come from the loaders. This module only
draws them.
"""

from __future__ import annotations

import html
from typing import Any

import pydeck as pdk
import streamlit as st

from src.ui.color_scale import FALLBACK_DOMAIN, STOPS

LAYER_ID = "h3-cells"
MAP_HEIGHT = 400
ROW_WARN = 50_000
SELECT_PROMPT = "Selecciona una celda"
SATURATION_NOTE = "Valores fuera de rango se saturan."
LIMIT_MISSING = "límite de detección no disponible"

_RISK_CLASS = {
    "critico": "chip chip-coral",
    "moderado": "chip chip-alert",
    "estable": "chip",
}


def detection_caption(above_limit: bool | None, limit: Any, unit: str | None) -> str | None:
    """Format the control message. Does not decide the science."""
    if above_limit is not False:
        return None
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        return LIMIT_MISSING
    unit_text = f" {unit}" if unit else ""
    return f"Sin señal detectable por encima del límite de detección de {limit:g}{unit_text}"


def _limit_from_meta(meta: dict[str, Any] | None) -> tuple[Any, str | None]:
    if not isinstance(meta, dict):
        return None, None
    stats = meta.get("stats")
    if not isinstance(stats, dict):
        return None, None
    for key, value in stats.items():
        if key.startswith("detection_limit") and isinstance(value, (int, float)) and not isinstance(value, bool):
            unit = "mm/year" if key.endswith("_mm_yr") else meta.get("unit")
            return value, str(unit) if unit else None
    return None, None


def _call(loaders: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    function = getattr(loaders, name, None)
    if function is None:
        return None
    try:
        return function(*args, **kwargs)
    except TypeError:
        if kwargs:
            return function(*args)
        return None


def _selected_cell(state: Any) -> str | None:
    selection = getattr(state, "selection", None)
    if selection is None:
        return None
    objects = selection["objects"] if "objects" in selection else {}
    rows = objects.get(LAYER_ID) if isinstance(objects, dict) else None
    if not rows:
        return None
    cell_id = rows[0].get("cell_id")
    return str(cell_id) if cell_id else None


def _scale(loaders: Any, case_id: str, filters: dict[str, Any]) -> dict[str, Any]:
    scale = _call(loaders, "get_color_scale", case_id, filters)
    if isinstance(scale, dict) and "min" in scale and "max" in scale:
        return scale
    # TODO(contract): single fallback until the backend publishes the domain.
    return {
        "min": FALLBACK_DOMAIN[0],
        "max": FALLBACK_DOMAIN[1],
        "unit": None,
        "saturated": False,
        "fallback": True,
    }


def _legend(scale: dict[str, Any], mode: str) -> str:
    vmin = scale.get("min")
    vmax = scale.get("max")
    unit = scale.get("unit") or "unidad no declarada"
    gradient = ", ".join(f"rgb({red}, {green}, {blue})" for _, (red, green, blue, _alpha) in STOPS)
    ticks = ""
    if isinstance(vmin, (int, float)) and isinstance(vmax, (int, float)):
        mid = vmin + (vmax - vmin) / 2
        ticks = (
            f"<span>{vmin:g} {html.escape(str(unit))}</span>"
            f"<span>{mid:g}</span>"
            f"<span>{vmax:g} {html.escape(str(unit))}</span>"
        )
    note = SATURATION_NOTE if scale.get("saturated") else ""
    fallback = " Dominio provisional." if scale.get("fallback") else ""
    return (
        '<div class="legend">'
        f'<p class="kicker">delta_change · {html.escape(str(unit))}</p>'
        f'<div class="legend-bar" style="background: linear-gradient(90deg, {gradient})"></div>'
        f'<div class="legend-ticks">{ticks}</div>'
        f"<p>{html.escape(mode)} · rango {html.escape(str(vmin))} – {html.escape(str(vmax))} {html.escape(str(unit))}{html.escape(fallback)}</p>"
        f"<p>{html.escape(note)}</p>"
        "</div>"
    )


def _provenance(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict):
        return "procedencia no declarada"
    provenance = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
    parts: list[str] = []
    for label, value in (
        ("source", provenance.get("source") or meta.get("source")),
        ("unit", meta.get("unit")),
        ("sensor", meta.get("sensor")),
        ("product", meta.get("product")),
    ):
        if value not in (None, ""):
            parts.append(f"{label} {value}")
    dates = meta.get("dates")
    if isinstance(dates, list) and dates:
        parts.append("dates " + " … ".join(str(item) for item in dates if item not in (None, "")))
    provenance_id = meta.get("provenance_id") or provenance.get("provenance_id")
    if provenance_id:
        parts.append(f"provenance_id {provenance_id}")
    return " · ".join(parts) if parts else "procedencia no declarada"


def _figure_line(label: str, value: Any, unit: str | None, provenance_id: str | None) -> str:
    if value in (None, ""):
        return f"{label}: no declarado"
    number = f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
    unit_text = f" {unit}" if unit else " (unidad no declarada)"
    source = provenance_id or "fuente no declarada"
    return f"{label}: {number}{unit_text} · {source}"


def _render_inspector(detail: dict[str, Any] | None, case_id: str | None) -> None:
    if not detail:
        st.markdown(
            f'<div class="cockpit-card"><p>{SELECT_PROMPT}</p></div>',
            unsafe_allow_html=True,
        )
        return
    risk = str(detail.get("risk_level") or "")
    chip_class = _RISK_CLASS.get(risk, "chip chip-muted")
    unit = detail.get("unit")
    provenance_id = detail.get("provenance_id")
    lines = [
        f"caso: {html.escape(str(detail.get('case_id') or case_id or 'no declarado'))}",
        _figure_line("delta_change", detail.get("delta_change"), unit if isinstance(unit, str) else None, provenance_id if isinstance(provenance_id, str) else None),
        _figure_line("coherence", detail.get("coherence"), None, provenance_id if isinstance(provenance_id, str) else None),
        f"land_use: {detail.get('land_use') or 'no declarado'}",
        f"sensor: {detail.get('sensor') or 'no declarado'}",
        f"product: {detail.get('product') or 'no declarado'}",
        f"source: {detail.get('source') or 'no declarado'}",
        f"provenance_id: {provenance_id or 'no declarado'}",
        f"acquisition_date: {detail.get('acquisition_date') or 'no declarada'}",
    ]
    body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    st.markdown(
        '<div class="cockpit-card">'
        f'<p class="kicker">Inspector</p>'
        f'<span class="{chip_class}">{html.escape(risk or "risk no declarado")}</span>'
        f"{body}</div>",
        unsafe_allow_html=True,
    )
    series = detail.get("series")
    if not isinstance(series, list) or not series:
        st.caption("Sin serie para esta celda.")
        return
    _render_series(series)


def _render_series(series: list[dict[str, Any]]) -> None:
    import plotly.graph_objects as go

    dates = [row.get("date") for row in series if row.get("date")]
    values = [row.get("value") for row in series if row.get("date")]
    unit = next((row.get("unit") for row in series if row.get("unit")), None)
    provenance_id = next((row.get("provenance_id") for row in series if row.get("provenance_id")), None)
    figure = go.Figure(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines+markers",
            line={"color": "#06B6D4", "width": 2},
            marker={"size": 6},
        )
    )
    figure.update_layout(
        height=160,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor="#0F172A",
        plot_bgcolor="#0B0F19",
        font={"color": "#94A3B8", "family": "JetBrains Mono, ui-monospace, monospace", "size": 11},
        xaxis={"title": "date"},
        yaxis={"title": f"value ({unit})" if unit else "value (unidad no declarada)"},
    )
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    source = provenance_id or "fuente no declarada"
    st.caption(f"serie · {unit or 'unidad no declarada'} · {source}")


def _render_kpis(kpis: Any) -> None:
    if not isinstance(kpis, list) or not kpis:
        return
    columns = st.columns(len(kpis))
    for column, item in zip(columns, kpis):
        if not isinstance(item, dict):
            continue
        unit = item.get("unit")
        value = item.get("value")
        shown = "no declarado" if value in (None, "") else (
            f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        )
        unit_text = f" {unit}" if unit else ""
        source = item.get("provenance_id") or "fuente no declarada"
        column.markdown(
            '<div class="cockpit-card">'
            f'<p class="kicker">{html.escape(str(item.get("label") or "kpi"))}</p>'
            f"<p>{html.escape(shown)}{html.escape(unit_text)}</p>"
            f"<p>{html.escape(str(source))}</p></div>",
            unsafe_allow_html=True,
        )


def _render_comparison(report: dict[str, Any] | None, mode: str) -> None:
    if not isinstance(report, dict) or not report.get("cases"):
        return
    shared = mode == "Común a todos los casos"
    if shared and report.get("mixed_units"):
        st.markdown(
            '<div class="null-note">Aviso: la escala común junta unidades declaradas distintas. '
            "mm no se convierte a una tasa. El caso de menor magnitud queda en el extremo: "
            "es el resultado de la escala, no un fallo.</div>",
            unsafe_allow_html=True,
        )
    rows = []
    for item in report["cases"]:
        width = 4 if shared and float(item.get("share") or 0) < 0.05 else max(4, int(float(item.get("share") or 0) * 100))
        if not shared:
            width = 100 if float(item.get("peak") or 0) else 4
        unit = item.get("unit") or "unidad no declarada"
        peak = item.get("peak")
        peak_text = f"{peak:g}" if isinstance(peak, (int, float)) else "no declarado"
        source = item.get("provenance_id") or "fuente no declarada"
        rows.append(
            '<div class="compare-row">'
            f'<span>{html.escape(str(item.get("id")))}</span>'
            '<div class="compare-track">'
            f'<div class="compare-fill" style="width:{width}%"></div></div>'
            f"<span>{html.escape(peak_text)} {html.escape(str(unit))} · {html.escape(str(source))}</span>"
            "</div>"
        )
    st.markdown(
        f'<div class="st-key-map-meta">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def render_map_tab(filters: dict[str, Any], loaders: Any, meta: dict[str, Any] | None) -> None:
    case_id = filters.get("case_id")
    mode = st.radio(
        "Escala",
        ["Por caso", "Común a todos los casos"],
        horizontal=True,
        key="scale_mode",
    )
    active = {**filters, "scale_mode": mode}
    _render_comparison(_call(loaders, "get_scale_comparison", mode), mode)
    frame = _call(loaders, "get_map_layer_df", case_id, active)
    counts = _call(loaders, "get_layer_counts", case_id, active) or {}
    shown = counts.get("shown")
    total = counts.get("total")
    if shown is None and frame is not None:
        shown = len(frame)
    if isinstance(shown, int) and isinstance(total, int) and (shown > ROW_WARN or total > ROW_WARN):
        st.warning("Hay más de 50000 filas. Pide al backend que agregue; la UI no agrega.")
    if isinstance(shown, int) and isinstance(total, int):
        st.caption(f"{shown} celdas mostradas de {total}")

    _render_kpis(_call(loaders, "get_map_kpis", case_id, active))
    status = _call(loaders, "get_signal_status", case_id) or {}
    limit, limit_unit = _limit_from_meta(meta)
    if limit is None:
        limit = status.get("detection_limit")
        limit_unit = status.get("detection_limit_unit")
    caption = detection_caption(status.get("above_limit"), limit, limit_unit if isinstance(limit_unit, str) else None)
    if caption:
        st.markdown(f'<div class="null-note">{html.escape(caption)}</div>', unsafe_allow_html=True)

    map_col, inspector_col = st.columns([8, 4], gap="medium", vertical_alignment="top")
    with map_col:
        view = _call(loaders, "get_view_state", case_id) or {}
        if "latitude" not in view or "longitude" not in view or "zoom" not in view:
            st.markdown(
                '<div class="cockpit-card"><p>Vista no disponible.</p></div>',
                unsafe_allow_html=True,
            )
        else:
            records = frame.to_dict(orient="records") if frame is not None and hasattr(frame, "to_dict") else []
            layer = pdk.Layer(
                "H3HexagonLayer",
                data=records,
                id=LAYER_ID,
                get_hexagon="cell_id",
                get_fill_color="color",
                get_elevation="height",
                pickable=True,
                auto_highlight=True,
                extruded=bool(active.get("extrude_3d")),
                elevation_scale=1,
                coverage=0.9,
            )
            deck = pdk.Deck(
                layers=[layer],
                initial_view_state=pdk.ViewState(
                    latitude=view["latitude"],
                    longitude=view["longitude"],
                    zoom=view["zoom"],
                    pitch=40 if active.get("extrude_3d") else 0,
                    bearing=0,
                ),
                map_provider="carto",
                map_style="dark",
                tooltip={
                    "text": (
                        "cell_id: {cell_id}\n"
                        "delta_change: {delta_change} {unit}\n"
                        "coherence: {coherence}\n"
                        "risk_level: {risk_level}"
                    )
                },
            )
            with st.container(key="map-slot", height=MAP_HEIGHT, border=False):
                event = st.pydeck_chart(
                    deck,
                    height=MAP_HEIGHT,
                    width="stretch",
                    on_select="rerun",
                    selection_mode="single-object",
                    key="h3-map",
                )
            picked = _selected_cell(event)
            if st.session_state.get("_map_case") != case_id:
                st.session_state.selected_cell_id = None
                picked = None
            st.session_state._map_case = case_id
            if picked:
                st.session_state.selected_cell_id = picked
            scale = _scale(loaders, str(case_id), active)
            with st.container(key="map-meta"):
                st.markdown(_legend(scale, mode), unsafe_allow_html=True)
                st.markdown(
                    f'<p class="telemetry">{html.escape(_provenance(meta))}</p>',
                    unsafe_allow_html=True,
                )
    with inspector_col:
        with st.container(key="inspector-slot"):
            cell_id = st.session_state.get("selected_cell_id")
            detail = _call(loaders, "get_cell_detail", case_id, cell_id) if cell_id else None
            _render_inspector(detail if isinstance(detail, dict) else None, str(case_id) if case_id else None)
