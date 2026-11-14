"""Streamlit shell. Composition only: no file reads and no geospatial math."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src.ui.theme import inject_css

TAB_MAP = "1. Mapa"
TAB_SERIES = "2. Series y KPIs"
TAB_HAZARD = "3. Peligro e infraestructura"
TAB_PHYSICS = "4. Física SAR y procedencia"
PENDING = "Se implementa en feat/ui-charts"
TITLE = "Dancing with the SARs: NISAR Interferometry Cockpit"

# Header telemetry may use only these keys. Missing keys are omitted.
_TELEMETRY_KEYS = ("sensor", "product", "unit", "dates", "coherence_threshold")


def _loaders() -> Any:
    try:
        from src.data import loaders as real
    except ImportError:
        real = None
    required = (
        "load_study_cases",
        "load_meta",
        "load_land_use_options",
        "load_risk_levels",
    )
    if real is not None and all(hasattr(real, name) for name in required):
        return real
    from src.ui import _stubs

    return _stubs


def is_demonstration(meta: dict[str, Any] | None) -> bool:
    """True when the artefact declares itself mock. No extra contract field."""
    if not isinstance(meta, dict):
        return False
    if meta.get("product") == "mock":
        return True
    provenance = meta.get("provenance")
    if isinstance(provenance, dict) and "mock" in str(provenance.get("source", "")).lower():
        return True
    return False


def telemetry_line(meta: dict[str, Any] | None) -> str:
    """Join only telemetry keys that exist and are non-empty."""
    if not isinstance(meta, dict):
        return ""
    parts: list[str] = []
    for key in _TELEMETRY_KEYS:
        if key not in meta:
            continue
        value = meta[key]
        if value is None or value == "" or value == []:
            continue
        if key == "dates":
            if not isinstance(value, list):
                continue
            shown = [str(item) for item in value if item not in (None, "")]
            if not shown:
                continue
            parts.append("dates " + " … ".join(shown))
            continue
        parts.append(f"{key} {value}")
    return " · ".join(parts)


def coherence_default(meta: dict[str, Any] | None) -> float | None:
    """Slider default from meta. Do not invent or clamp the received number.

    v1.1 stores the floor at thresholds.coherence_min. coherence_threshold is
    read only when that key is actually present.
    """
    if not isinstance(meta, dict):
        return None
    if "coherence_threshold" in meta and isinstance(meta["coherence_threshold"], (int, float)):
        return float(meta["coherence_threshold"])
    thresholds = meta.get("thresholds")
    if isinstance(thresholds, dict) and isinstance(thresholds.get("coherence_min"), (int, float)):
        return float(thresholds["coherence_min"])
    return None


def _blank(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return value


def render_header(meta: dict[str, Any] | None) -> None:
    line = telemetry_line(meta)
    telemetry = f'<p class="telemetry">{html.escape(line)}</p>' if line else ""
    st.markdown(
        '<header class="mission-bar">'
        f"<h1>{html.escape(TITLE)}</h1>"
        f"{telemetry}"
        "</header>",
        unsafe_allow_html=True,
    )
    if is_demonstration(meta):
        st.markdown(
            '<div class="demo-banner" role="status">DATOS DE DEMOSTRACIÓN</div>',
            unsafe_allow_html=True,
        )


def render_filters(
    case_ids: list[str],
    labels: dict[str, str],
    land_uses: tuple[str, ...] | list[str],
    risk_levels: tuple[str, ...] | list[str],
    coherence_floor: float | None,
) -> dict[str, Any]:
    """Draw the filter strip. Values stay in session_state; nothing is filtered."""
    with st.container(border=True):
        case_col, land_col, coherence_col, risk_col, extrude_col = st.columns(5)
        with case_col:
            st.selectbox(
                "Caso",
                options=case_ids,
                format_func=lambda case_id: labels.get(case_id, case_id),
                key="filter_case_id",
            )
        with land_col:
            st.selectbox(
                "Uso del suelo",
                options=["", *land_uses],
                format_func=lambda value: "sin filtrar" if value == "" else value,
                key="filter_land_use",
            )
        with coherence_col:
            if coherence_floor is None:
                st.caption("Umbral de coherencia no declarado.")
            elif not 0.0 <= coherence_floor <= 1.0:
                st.caption("Umbral de coherencia fuera de [0, 1]. No se modifica.")
            else:
                if "filter_coherence_min" not in st.session_state:
                    st.session_state.filter_coherence_min = coherence_floor
                st.slider(
                    "Coherencia mínima",
                    min_value=0.0,
                    max_value=1.0,
                    key="filter_coherence_min",
                )
        with risk_col:
            st.selectbox(
                "Nivel de riesgo",
                options=["", *risk_levels],
                format_func=lambda value: "sin filtrar" if value == "" else value,
                key="filter_risk_level",
            )
        with extrude_col:
            if "filter_extrude_3d" not in st.session_state:
                st.session_state.filter_extrude_3d = False
            st.toggle("Extrusión 3D", key="filter_extrude_3d")

    return {
        "case_id": st.session_state.get("filter_case_id"),
        "land_use": _blank(st.session_state.get("filter_land_use")),
        "coherence_min": st.session_state.get("filter_coherence_min", coherence_floor),
        "risk_level": _blank(st.session_state.get("filter_risk_level")),
        "extrude_3d": bool(st.session_state.get("filter_extrude_3d", False)),
    }


def render_map_tab(filters: dict[str, Any]) -> None:
    del filters  # passed through; the map view will apply them
    viewport, inspector = st.columns([2, 1])
    with viewport:
        st.markdown(
            '<div class="viewport"><p class="kicker">Mapa</p>'
            "<p>Se implementa en feat/ui-map. Sin geometría ni magnitudes.</p></div>",
            unsafe_allow_html=True,
        )
    with inspector:
        st.markdown(
            '<div class="cockpit-card"><p class="kicker">Inspector</p>'
            "<p>Sin celda seleccionada.</p></div>",
            unsafe_allow_html=True,
        )


def render_pending(note: str, filters: dict[str, Any]) -> None:
    del filters
    st.markdown(
        f'<div class="cockpit-card"><p class="kicker">Pendiente</p><p>{html.escape(note)}</p></div>',
        unsafe_allow_html=True,
    )


def render_app() -> None:
    loaders = _loaders()
    cases = list(loaders.load_study_cases())
    case_ids = [str(case["id"]) for case in cases if case.get("id")]
    labels = {str(case["id"]): str(case.get("label") or case["id"]) for case in cases}

    if not case_ids:
        st.error("No hay casos. Falta config/study_cases.yaml.")
        return

    if "filter_case_id" not in st.session_state:
        st.session_state.filter_case_id = case_ids[0]
    if st.session_state.filter_case_id not in case_ids:
        st.session_state.filter_case_id = case_ids[0]

    meta = loaders.load_meta(st.session_state.filter_case_id)
    render_header(meta if isinstance(meta, dict) else None)
    filters = render_filters(
        case_ids,
        labels,
        tuple(loaders.load_land_use_options()),
        tuple(loaders.load_risk_levels()),
        coherence_default(meta if isinstance(meta, dict) else None),
    )

    map_tab, series_tab, hazard_tab, physics_tab = st.tabs(
        [TAB_MAP, TAB_SERIES, TAB_HAZARD, TAB_PHYSICS]
    )
    with map_tab:
        render_map_tab(filters)
    with series_tab:
        render_pending(PENDING, filters)
    with hazard_tab:
        render_pending(PENDING, filters)
    with physics_tab:
        render_pending(
            PENDING + " Sin texto científico aprobado por el Rol 3.",
            filters,
        )


st.set_page_config(
    page_title=TITLE,
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_app()
