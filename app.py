"""Streamlit shell. Composition only: no file reads and no geospatial math."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src.ui.charts import render_series_tab
from src.ui.context_view import render_hazard_tab
from src.ui.educational import render_physics_tab
from src.ui.formatters import DEMO_BANNER, SCALE_LABELS, choice_label, format_dates_span
from src.ui.map_view import render_map_tab
from src.ui.theme import inject_css

TAB_MAP = "1. Map"
TAB_SERIES = "2. Series and KPIs"
TAB_HAZARD = "3. Hazard and infrastructure"
TAB_PHYSICS = "4. Provenance and limits"
TITLE = "Dancing with the SARs: NISAR Interferometry Cockpit"

# Header telemetry may use only these keys. Missing keys are omitted.
_TELEMETRY_KEYS = ("sensor", "product", "unit", "dates", "coherence_threshold")
_TELEMETRY_LABELS = {
    "sensor": "Sensor",
    "product": "Product",
    "unit": "Unit",
    "coherence_threshold": "Coherence threshold",
}


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
        "get_map_layer_df",
        "get_view_state",
        "get_cell_detail",
        "load_timeseries",
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
            span = format_dates_span(value if isinstance(value, list) else None)
            if span:
                parts.append(span.capitalize())
            continue
        parts.append(f"{_TELEMETRY_LABELS.get(key, key)} {value}")
    if "coherence_threshold" not in meta:
        thresholds = meta.get("thresholds")
        floor = thresholds.get("coherence_min") if isinstance(thresholds, dict) else None
        if isinstance(floor, (int, float)) and not isinstance(floor, bool):
            parts.append(f"Coherence floor {floor:g}")
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


# URL state: keys are short query params; values map to session_state keys.
# A shared URL must reopen the cockpit on the same case, filters and camera choice.
_THEME_MODES = ("system", "light", "dark")
_SCALE_MODES = ("independent", "shared")


def _seed_state_from_query(case_ids: list[str], coherence_floor: float | None) -> None:
    """Apply query params to session_state on the first run only.

    Unknown or out-of-range values are ignored; stale land/risk values are
    dropped later by the existing per-case option check. Seeding never
    overrides a key the current session already holds.
    """
    qp = st.query_params
    if not qp:
        return
    if "filter_case_id" not in st.session_state and qp.get("case") in case_ids:
        st.session_state.filter_case_id = qp["case"]
    if "filter_land_use" not in st.session_state and "land" in qp:
        st.session_state.filter_land_use = qp["land"]
    if "filter_risk_level" not in st.session_state and "risk" in qp:
        st.session_state.filter_risk_level = qp["risk"]
    if "filter_extrude_3d" not in st.session_state and qp.get("extrude") in ("0", "1"):
        st.session_state.filter_extrude_3d = qp["extrude"] == "1"
    if "ui_theme_mode" not in st.session_state and qp.get("theme") in _THEME_MODES:
        st.session_state.ui_theme_mode = qp["theme"]
    if "scale_mode" not in st.session_state and qp.get("scale") in _SCALE_MODES:
        st.session_state.scale_mode = qp["scale"]
    if "selected_cell_id" not in st.session_state and qp.get("cell"):
        st.session_state.selected_cell_id = qp.get("cell")
    if "filter_coherence_min" not in st.session_state:
        try:
            value = float(qp["coh"])
        except (KeyError, TypeError, ValueError):
            value = None
        if value is not None and 0.0 <= value <= 1.0:
            # Seed the applied floor too, or render_filters would reset the slider
            # to the declared default on this first run.
            if isinstance(coherence_floor, (int, float)):
                st.session_state.filter_coherence_floor = coherence_floor
            st.session_state.filter_coherence_min = value


def _sync_query_params(filters: dict[str, Any]) -> None:
    """Mirror the live state into the URL so a copied link restores it."""
    qp = st.query_params
    qp["case"] = str(filters["case_id"])
    for key, value in (
        ("land", filters["land_use"]),
        ("risk", filters["risk_level"]),
        ("cell", st.session_state.get("selected_cell_id")),
    ):
        if value:
            qp[key] = str(value)
        elif key in qp:
            del qp[key]
    coherence = filters.get("coherence_min")
    if isinstance(coherence, (int, float)) and not isinstance(coherence, bool):
        qp["coh"] = f"{float(coherence):g}"
    qp["extrude"] = "1" if filters["extrude_3d"] else "0"
    qp["theme"] = str(st.session_state.get("ui_theme_mode", "system"))
    qp["scale"] = str(st.session_state.get("scale_mode", "independent"))


def _case_options(loaders: Any, name: str, case_id: str) -> tuple[str, ...]:
    """Prefer case-scoped options; fall back for stubs whose hook takes no argument."""
    function = getattr(loaders, name)
    try:
        return tuple(function(case_id))
    except TypeError:
        return tuple(function())


def render_header(meta: dict[str, Any] | None) -> None:
    line = telemetry_line(meta)
    telemetry = f'<p class="telemetry">{html.escape(line)}</p>' if line else ""
    st.markdown(
        '<header class="mission-bar">'
        f'<p class="mission-title">{html.escape(TITLE)}</p>'
        f"{telemetry}"
        "</header>",
        unsafe_allow_html=True,
    )
    if is_demonstration(meta):
        st.markdown(
            f'<div class="demo-banner" role="status">{DEMO_BANNER}</div>',
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
    theme_labels = {"system": "System", "light": "Light", "dark": "Dark"}
    if st.session_state.get("ui_theme_mode") not in theme_labels:
        st.session_state.ui_theme_mode = "system"
    # Streamlit has no viewport query on the server. A collapsed-by-choice
    # expander is the portable way to keep filters from owning the first screen
    # on a phone while staying open on desktop. `?compact=1` ships it closed.
    compact = st.query_params.get("compact") == "1"
    with st.expander("Filters", expanded=not compact, key="filters"):
        case_col, land_col, coherence_col, risk_col, extrude_col, theme_col = st.columns(6)
        with case_col:
            st.selectbox(
                "Case",
                options=case_ids,
                format_func=lambda case_id: labels.get(case_id, case_id),
                key="filter_case_id",
            )
        with land_col:
            st.selectbox(
                "Land use",
                options=["", *land_uses],
                format_func=choice_label,
                key="filter_land_use",
            )
        with coherence_col:
            if coherence_floor is None:
                st.caption("Coherence threshold not declared.")
            elif not 0.0 <= coherence_floor <= 1.0:
                st.caption("Coherence threshold is outside [0, 1]. It is not modified.")
            else:
                # Init when the key is missing. Reset only when this case declares
                # a different floor, so a user value survives an unchanged threshold.
                applied = st.session_state.get("filter_coherence_floor")
                if "filter_coherence_min" not in st.session_state or applied != coherence_floor:
                    st.session_state.filter_coherence_min = coherence_floor
                st.session_state.filter_coherence_floor = coherence_floor
                st.slider(
                    "Minimum coherence",
                    min_value=0.0,
                    max_value=1.0,
                    key="filter_coherence_min",
                )
        with risk_col:
            st.selectbox(
                "Risk level",
                options=["", *risk_levels],
                format_func=choice_label,
                key="filter_risk_level",
            )
        with extrude_col:
            if "filter_extrude_3d" not in st.session_state:
                st.session_state.filter_extrude_3d = False
            st.toggle("3D extrusion", key="filter_extrude_3d")
        with theme_col:
            st.selectbox(
                "Theme",
                options=list(theme_labels),
                format_func=lambda mode: theme_labels.get(mode, mode),
                key="ui_theme_mode",
            )

    return {
        "case_id": st.session_state.get("filter_case_id"),
        "land_use": _blank(st.session_state.get("filter_land_use")),
        "coherence_min": (
            st.session_state.get("filter_coherence_min", coherence_floor)
            if isinstance(coherence_floor, (int, float)) and 0.0 <= coherence_floor <= 1.0
            else None
        ),
        "risk_level": _blank(st.session_state.get("filter_risk_level")),
        "extrude_3d": bool(st.session_state.get("filter_extrude_3d", False)),
    }


def _render_map(
    filters: dict[str, Any],
    loaders: Any,
    meta: dict[str, Any] | None,
    case_labels: dict[str, str],
) -> None:
    render_map_tab(filters, loaders, meta, case_labels)


def render_app() -> None:
    loaders = _loaders()
    cases = list(loaders.load_study_cases())
    case_ids = [str(case["id"]) for case in cases if case.get("id")]
    labels = {str(case["id"]): str(case.get("label") or case["id"]) for case in cases}

    if not case_ids:
        st.error("No cases. config/study_cases.yaml is missing.")
        return

    if "filter_case_id" not in st.session_state:
        url_case = st.query_params.get("case")
        st.session_state.filter_case_id = url_case if url_case in case_ids else case_ids[0]
    if st.session_state.filter_case_id not in case_ids:
        st.session_state.filter_case_id = case_ids[0]

    meta = loaders.load_meta(st.session_state.filter_case_id)
    _seed_state_from_query(
        case_ids,
        coherence_default(meta if isinstance(meta, dict) else None),
    )
    render_header(meta if isinstance(meta, dict) else None)
    land_uses = _case_options(loaders, "load_land_use_options", st.session_state.filter_case_id)
    risk_levels = _case_options(loaders, "load_risk_levels", st.session_state.filter_case_id)
    # Drop stale filter values when options shrink (stub no longer lists absent classes).
    if st.session_state.get("filter_land_use") not in ("", *land_uses):
        st.session_state.filter_land_use = ""
    if st.session_state.get("filter_risk_level") not in ("", *risk_levels):
        st.session_state.filter_risk_level = ""
    filters = render_filters(
        case_ids,
        labels,
        land_uses,
        risk_levels,
        coherence_default(meta if isinstance(meta, dict) else None),
    )

    map_tab, series_tab, hazard_tab, physics_tab = st.tabs(
        [TAB_MAP, TAB_SERIES, TAB_HAZARD, TAB_PHYSICS]
    )
    with map_tab:
        _render_map(filters, loaders, meta if isinstance(meta, dict) else None, labels)
    with series_tab:
        render_series_tab(filters, loaders, meta if isinstance(meta, dict) else None)
    with hazard_tab:
        render_hazard_tab(filters, loaders)
    with physics_tab:
        render_physics_tab(loaders, meta if isinstance(meta, dict) else None)
    # Written last so a cell picked by the map in this run lands in the URL too.
    _sync_query_params(filters)


st.set_page_config(
    page_title=TITLE,
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_app()
