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
from src.ui.formatters import (
    NOT_DECLARED,
    SCALE_LABELS,
    SOURCE_NOT_DECLARED,
    UNIT_NOT_DECLARED,
    format_dates_span,
    is_shared_scale,
    risk_label,
)
from src.ui.theme import active_palette, reported_scheme, style_figure

LAYER_ID = "h3-cells"
MAP_HEIGHT = 560
ROW_WARN = 50_000
SELECT_PROMPT = "Select a cell"
SATURATION_NOTE = "Values outside the range are saturated."
LIMIT_MISSING = "detection limit not available"

_RISK_CLASS = {
    "critico": "chip chip-coral",
    "moderado": "chip chip-alert",
    "estable": "chip",
}
# Presentation strokes only. Not a risk scale. Unknown layers fall back to muted.
_CONTEXT_ROLE = {
    "land_use": "accent",
    "water": "coherence",
    "infrastructure": "alert",
    "admin_boundary": "muted",
}


def detection_caption(above_limit: bool | None, limit: Any, unit: str | None) -> str | None:
    """Format the control message. Does not decide the science."""
    if above_limit is not False:
        return None
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        return LIMIT_MISSING
    unit_text = f" {unit}" if unit else ""
    return f"No detectable signal above the detection limit of {limit:g}{unit_text}"


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
    unit = scale.get("unit") or UNIT_NOT_DECLARED
    metric = scale.get("metric") or "delta_change"
    stops = scale.get("stops")
    if isinstance(stops, (list, tuple)) and stops:
        gradient = ", ".join(html.escape(str(color)) for color in stops)
    else:
        # Fallback for `_stubs`, whose scale has no stop list.
        gradient = ", ".join(f"rgb({red}, {green}, {blue})" for _, (red, green, blue, _alpha) in STOPS)
    ticks = ""
    if isinstance(vmin, (int, float)) and isinstance(vmax, (int, float)):
        mid = round(vmin + (vmax - vmin) / 2, 2)
        ticks = (
            f"<span>{vmin:g} {html.escape(str(unit))}</span>"
            f"<span>{mid:g} {html.escape(str(unit))}</span>"
            f"<span>{vmax:g} {html.escape(str(unit))}</span>"
        )
    note = SATURATION_NOTE if scale.get("saturated") else ""
    fallback = " Provisional domain." if scale.get("fallback") else ""
    caption = scale.get("caption")
    caption_html = f"<p>{html.escape(str(caption))}</p>" if caption else ""
    note_html = f"<p>{html.escape(note)}</p>" if note else ""
    return (
        '<div class="cockpit-card legend">'
        f'<p class="kicker">{html.escape(str(metric))} · {html.escape(str(unit))}</p>'
        f'<div class="legend-bar" style="background: linear-gradient(90deg, {gradient})"></div>'
        f'<div class="legend-ticks">{ticks}</div>'
        f"<p>{html.escape(SCALE_LABELS.get(mode, str(mode)))} · range {html.escape(str(vmin))} – {html.escape(str(vmax))} {html.escape(str(unit))}{html.escape(fallback)}</p>"
        f"{caption_html}"
        f"{note_html}"
        "</div>"
    )


def _provenance(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict):
        return f"provenance {NOT_DECLARED}"
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
        else:
            parts.append(f"{label} {NOT_DECLARED}")
    # provenance_id before dates so a long acquisition list cannot push it off-screen.
    provenance_id = meta.get("provenance_id") or provenance.get("provenance_id")
    parts.append(
        f"provenance_id {provenance_id}" if provenance_id else f"provenance_id {NOT_DECLARED}"
    )
    span = format_dates_span(meta.get("dates") if isinstance(meta.get("dates"), list) else None)
    parts.append(span if span else f"dates {NOT_DECLARED}")
    return " · ".join(parts)


def _figure_line(label: str, value: Any, unit: str | None, provenance_id: str | None) -> str:
    if value in (None, ""):
        return f"{label}: {NOT_DECLARED}"
    number = (
        f"{round(float(value), 3):g}"
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else str(value)
    )
    unit_text = f" {unit}" if unit else f" ({UNIT_NOT_DECLARED})"
    source = provenance_id or SOURCE_NOT_DECLARED
    return f"{label}: {number}{unit_text} · {source}"


def _candidate_cells(frame: Any, current: str | None, limit: int = 25) -> list[str]:
    """Return top cells from the active map frame for the manual backup selector."""
    if frame is None or not hasattr(frame, "columns") or "cell_id" not in frame.columns or len(frame) == 0:
        return [current] if current else []
    metric_col = "velocity_mm_yr" if "velocity_mm_yr" in frame.columns else (
        "delta_change" if "delta_change" in frame.columns else None
    )
    if metric_col is not None:
        ordered = frame.assign(_mag=frame[metric_col].abs()).sort_values("_mag", ascending=False)
    else:
        ordered = frame
    cells = [str(cid) for cid in ordered["cell_id"].head(limit).tolist() if cid]
    if current and current not in cells:
        cells.insert(0, str(current))
    return cells


def _hex_rgba(value: str, alpha: int = 180) -> list[int]:
    color = value.lstrip("#")
    return [int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), alpha]


def _context_groups(geojson: dict[str, Any] | None) -> list[tuple[str, str, dict[str, Any]]]:
    """Split delivered features by `layer`. Does not invent a missing layer."""
    if not isinstance(geojson, dict):
        return []
    features = geojson.get("features")
    if not isinstance(features, list):
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        layer = str(props.get("layer") or "")
        if not layer:
            continue
        grouped.setdefault(layer, []).append(feature)
    palette = active_palette()
    groups: list[tuple[str, str, dict[str, Any]]] = []
    for layer, rows in grouped.items():
        role = _CONTEXT_ROLE.get(layer, "muted")
        color = str(palette.get(role, palette["muted"]))
        groups.append((layer, color, {"type": "FeatureCollection", "features": rows}))
    return groups


def _artefact_omits_cell_series(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    stats = meta.get("stats")
    block = stats.get("per_cell_series") if isinstance(stats, dict) else None
    return isinstance(block, dict) and block.get("enabled") is False


def _coord_line(label: str, value: Any) -> str:
    if value in (None, ""):
        return f"{label}: {NOT_DECLARED}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{label}: {value:.5f} deg"
    return f"{label}: {value}"


def _render_inspector(
    detail: dict[str, Any] | None,
    case_id: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    if not detail:
        st.markdown(
            '<div class="cockpit-card is-empty">'
            '<p class="kicker">Inspector</p>'
            f'<p class="empty-prompt">{html.escape(SELECT_PROMPT)}</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        return
    risk = str(detail.get("risk_level") or "")
    chip_class = _RISK_CLASS.get(risk, "chip chip-muted")
    unit = detail.get("unit")
    provenance_id = detail.get("provenance_id")
    source_id = provenance_id if isinstance(provenance_id, str) else None
    unit_text = unit if isinstance(unit, str) else None
    lines = [
        "sign: positive = up",
        f"case: {detail.get('case_id') or case_id or NOT_DECLARED}",
        _coord_line("lat", detail.get("lat")),
        _coord_line("lon", detail.get("lon")),
        _figure_line("val_t1", detail.get("val_t1"), unit_text, source_id),
        _figure_line("val_t2", detail.get("val_t2"), unit_text, source_id),
        _figure_line("delta_change", detail.get("delta_change"), unit_text, source_id),
        _figure_line(
            "velocity_mm_yr",
            detail.get("velocity_mm_yr"),
            detail.get("velocity_unit") if isinstance(detail.get("velocity_unit"), str) else None,
            source_id,
        ),
        _figure_line("coherence", detail.get("coherence"), None, source_id),
        "risk_level: derived band from |velocity|, not a separate measurement",
        f"regime: {detail.get('regime') or NOT_DECLARED} · secular versus seasonal",
        _figure_line("ratio_R", detail.get("ratio_R"), None, source_id),
        "ratio_R: abs(trend) / seasonal amplitude",
        _figure_line(
            "seasonal_amp_mm",
            detail.get("seasonal_amp_mm"),
            unit if isinstance(unit, str) else None,
            provenance_id if isinstance(provenance_id, str) else None,
        ),
        f"land_use: {detail.get('land_use') or NOT_DECLARED}",
        f"sensor: {detail.get('sensor') or NOT_DECLARED}",
        f"product: {detail.get('product') or NOT_DECLARED}",
        f"source: {detail.get('source') or NOT_DECLARED}",
        f"provenance_id: {provenance_id or NOT_DECLARED}",
        f"acquisition_date: {detail.get('acquisition_date') or NOT_DECLARED}",
    ]
    body = "".join(f'<p class="detail-line">{html.escape(line)}</p>' for line in lines)
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Inspector</p>'
        f'<span class="{chip_class}">{html.escape(risk_label(risk))}</span>'
        f"{body}</div>",
        unsafe_allow_html=True,
    )
    series = detail.get("series")
    if not isinstance(series, list) or not series:
        st.caption("No series for this cell.")
        return
    _render_series(series, str(detail.get("series_scope") or "cell"), meta)


def _render_series(
    series: list[dict[str, Any]],
    scope: str = "cell",
    meta: dict[str, Any] | None = None,
) -> None:
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
            line={"color": active_palette()["accent"], "width": 2},
            marker={"size": 6},
        )
    )
    style_figure(
        figure,
        height=160,
        x_title="date",
        y_title=f"value ({unit})" if unit else f"value ({UNIT_NOT_DECLARED})",
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
    )
    st.plotly_chart(figure, width="stretch", theme=None, config={"displayModeBar": False})
    source = provenance_id or SOURCE_NOT_DECLARED
    if scope == "aoi":
        note = "Aggregated AOI series (per-cell series not emitted)"
        if _artefact_omits_cell_series(meta):
            note += ". The artefact did not emit a per-cell series."
        st.caption(note)
        return
    st.caption(f"series · {unit or UNIT_NOT_DECLARED} · {source}")


def _render_kpis(kpis: Any) -> None:
    if not isinstance(kpis, list) or not kpis:
        return
    columns = st.columns(len(kpis))
    for column, item in zip(columns, kpis):
        if not isinstance(item, dict):
            continue
        unit = item.get("unit")
        value = item.get("value")
        shown = NOT_DECLARED if value in (None, "") else (
            f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        )
        unit_text = f" {unit}" if unit else ""
        source = item.get("provenance_id") or SOURCE_NOT_DECLARED
        column.markdown(
            '<div class="cockpit-card">'
            f'<p class="kicker">{html.escape(str(item.get("label") or "kpi"))}</p>'
            f'<p class="kpi-value">{html.escape(shown)}{html.escape(unit_text)}</p>'
            f'<p class="kpi-source" title="{html.escape(str(source))}">{html.escape(str(source))}</p>'
            "</div>",
            unsafe_allow_html=True,
        )


def _render_comparison(
    report: dict[str, Any] | None,
    mode: str,
    case_labels: dict[str, str] | None = None,
) -> None:
    if not isinstance(report, dict) or not report.get("cases"):
        return
    shared = is_shared_scale(mode)
    if shared and report.get("mixed_units"):
        st.markdown(
            '<div class="null-note">Notice: the shared scale combines distinct declared units. '
            "mm is not converted to a rate. The smaller-magnitude case sits at the end of the scale: "
            "that is the scale result, not a failure.</div>",
            unsafe_allow_html=True,
        )
    rows = []
    for item in report["cases"]:
        width = 4 if shared and float(item.get("share") or 0) < 0.05 else max(4, int(float(item.get("share") or 0) * 100))
        if not shared:
            width = 100 if float(item.get("peak") or 0) else 4
        unit = item.get("unit") or UNIT_NOT_DECLARED
        peak = item.get("peak")
        peak_text = f"{peak:g}" if isinstance(peak, (int, float)) else NOT_DECLARED
        source = item.get("provenance_id") or SOURCE_NOT_DECLARED
        case_id = str(item.get("id") or "")
        case_name = (case_labels or {}).get(case_id, case_id)
        rows.append(
            '<div class="compare-row">'
            f'<span class="compare-case">{html.escape(case_name)}</span>'
            '<div class="compare-track">'
            f'<div class="compare-fill" style="width:{width}%"></div></div>'
            f'<span class="compare-meta">{html.escape(peak_text)} {html.escape(str(unit))} · {html.escape(str(source))}</span>'
            "</div>"
        )
    st.markdown(
        '<div class="cockpit-card compare-card">'
        '<p class="kicker">Relative magnitude across cases</p>'
        f'{"".join(rows)}'
        "</div>",
        unsafe_allow_html=True,
    )


def _context_legend(groups: list[tuple[str, str, dict[str, Any]]]) -> str:
    if not groups:
        return ""
    rows = "".join(
        '<p class="detail-line">'
        f'<span class="swatch" style="background:{html.escape(color)}"></span>'
        f"{html.escape(layer)}</p>"
        for layer, color, _payload in groups
    )
    return (
        '<div class="cockpit-card">'
        '<p class="kicker">Context strokes</p>'
        '<p class="kpi-note">Stroke color is presentation only. It is not a risk class.</p>'
        f"{rows}</div>"
    )


def render_map_tab(
    filters: dict[str, Any],
    loaders: Any,
    meta: dict[str, Any] | None,
    case_labels: dict[str, str] | None = None,
) -> None:
    case_id = filters.get("case_id")
    if st.session_state.get("scale_mode") not in SCALE_LABELS:
        st.session_state.scale_mode = "independent"
    mode = st.radio(
        "Scale",
        list(SCALE_LABELS),
        format_func=lambda key: SCALE_LABELS[key],
        horizontal=True,
        key="scale_mode",
    )
    active = {**filters, "scale_mode": mode}
    _render_comparison(_call(loaders, "get_scale_comparison", mode), mode, case_labels)
    frame = _call(loaders, "get_map_layer_df", case_id, active)
    counts = _call(loaders, "get_layer_counts", case_id, active) or {}
    shown = counts.get("shown")
    total = counts.get("total")
    if shown is None and frame is not None:
        shown = len(frame)
    if isinstance(shown, int) and isinstance(total, int) and (shown > ROW_WARN or total > ROW_WARN):
        st.warning("More than 50000 rows. Ask the backend to aggregate; the UI does not aggregate.")
    if isinstance(shown, int) and isinstance(total, int):
        st.caption(f"{shown} cells shown of {total}")

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
                '<div class="cockpit-card is-empty">'
                '<p class="kicker">Map</p>'
                '<p class="empty-prompt">View not available.</p>'
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            records = frame.to_dict(orient="records") if frame is not None and hasattr(frame, "to_dict") else []
            extrude = bool(active.get("extrude_3d"))
            # Flat mode must not keep loader heights: non-zero elevation still
            # produces z-fighting that reads as overlapping hexes.
            if not extrude and records:
                records = [{**row, "height": 0} for row in records]
            layer = pdk.Layer(
                "H3HexagonLayer",
                data=records,
                id=LAYER_ID,
                get_hexagon="cell_id",
                get_fill_color="color",
                get_elevation="height",
                pickable=True,
                auto_highlight=True,
                extruded=extrude,
                elevation_scale=1 if extrude else 0,
                coverage=0.9,
            )
            layers = [layer]
            ctx_payload = _call(loaders, "load_context_layers", case_id)
            ctx_geojson = ctx_payload.get("geojson") if isinstance(ctx_payload, dict) else None
            context_groups = _context_groups(ctx_geojson if isinstance(ctx_geojson, dict) else None)
            for layer_name, color, collection in context_groups:
                layers.append(
                    pdk.Layer(
                        "GeoJsonLayer",
                        data=collection,
                        id=f"context-{layer_name}",
                        pickable=False,
                        stroked=True,
                        filled=False,
                        get_line_color=_hex_rgba(color),
                        get_line_width=20,
                        line_width_min_pixels=1,
                        point_radius_min_pixels=4,
                    )
                )
            deck = pdk.Deck(
                layers=layers,
                initial_view_state=pdk.ViewState(
                    latitude=view["latitude"],
                    longitude=view["longitude"],
                    zoom=view["zoom"],
                    pitch=40 if extrude else 0,
                    bearing=0,
                ),
                map_provider="carto",
                map_style="light" if reported_scheme() == "light" else "dark",
                tooltip={
                    "text": (
                        "cell_id: {cell_id}\n"
                        "delta_change: {delta_change} {unit}\n"
                        "velocity: {velocity_mm_yr} {velocity_unit}\n"
                        "coherence: {coherence}\n"
                        "regime: {regime}\n"
                        "ratio_R: {ratio_R}\n"
                        "risk_level: {risk_level}"
                    )
                },
            )
            with st.container(key="map-slot", border=False):
                event = st.pydeck_chart(
                    deck,
                    height=MAP_HEIGHT,
                    width="stretch",
                    on_select="rerun",
                    selection_mode="single-object",
                    # Remount when the case changes so initial_view_state applies
                    # and the previous AOI layer cannot linger on top.
                    key=f"h3-map-{case_id}",
                )
            picked = _selected_cell(event)
            selector_key = f"inspector_cell_{case_id}"
            if st.session_state.get("_map_case") != case_id:
                st.session_state.selected_cell_id = None
                st.session_state[selector_key] = ""
                picked = None
            st.session_state._map_case = case_id
            if picked:
                st.session_state.selected_cell_id = picked
                st.session_state[selector_key] = picked
            scale = _scale(loaders, str(case_id), active)
            with st.container(key="map-meta"):
                st.markdown(_legend(scale, mode), unsafe_allow_html=True)
                legend = _context_legend(context_groups)
                if legend:
                    st.markdown(legend, unsafe_allow_html=True)
                st.markdown(
                    '<div class="cockpit-card provenance-card">'
                    '<p class="kicker">Provenance</p>'
                    f'<p class="telemetry">{html.escape(_provenance(meta))}</p>'
                    "</div>",
                    unsafe_allow_html=True,
                )
    with inspector_col:
        with st.container(key="inspector-slot"):
            selector_key = f"inspector_cell_{case_id}"
            current_cell = st.session_state.get("selected_cell_id")
            candidates = _candidate_cells(frame, str(current_cell) if current_cell else None)
            if current_cell and not st.session_state.get(selector_key):
                st.session_state[selector_key] = str(current_cell)
            if candidates:
                chosen = st.selectbox(
                    "H3 cell",
                    options=["", *candidates],
                    format_func=lambda cid: SELECT_PROMPT if cid == "" else cid,
                    key=selector_key,
                )
                st.session_state.selected_cell_id = chosen or None
            cell_id = st.session_state.get("selected_cell_id")
            detail = _call(loaders, "get_cell_detail", case_id, cell_id) if cell_id else None
            _render_inspector(
                detail if isinstance(detail, dict) else None,
                str(case_id) if case_id else None,
                meta if isinstance(meta, dict) else None,
            )
