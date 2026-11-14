from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pydeck as pdk
import streamlit as st

from src.data.loaders import LoaderError, load_case_bundle
from src.domain import map_adapter as MA


ROOT = Path(__file__).parent
CASES = {
    "mexico-city": "Ciudad de México",
    "jakarta": "Yakarta",
    "malaga": "Málaga",
}

#: Only layers the frozen contract actually publishes. `backscatter` and `events` used to be
#: offered here; neither has a column in any `nisar_grid.parquet` and `contracts.py` rejects
#: undeclared grid columns, so selecting them could never render data. Layer -> (metric, label).
LAYERS: dict[str, tuple[str, str]] = {
    "velocity": ("velocity_mm_yr", "Velocidad LOS (mm/año)"),
    "coherence": ("coherence", "Coherencia (0–1)"),
    "change": ("delta_change", "Cambio acumulado (mm)"),
}
SCALES = {"Compartida": "shared", "Independiente": "independent"}


@st.cache_data(show_spinner=False)
def load_case_metadata(case_id: str) -> dict[str, Any] | None:
    metadata_path = ROOT / "data" / "processed" / case_id / "meta.json"
    if not metadata_path.exists():
        return None

    with metadata_path.open(encoding="utf-8") as metadata_file:
        metadata = json.load(metadata_file)

    if not isinstance(metadata, dict):
        raise ValueError(f"El metadato de {case_id} debe ser un objeto JSON.")

    return metadata


@st.cache_data(show_spinner=False)
def load_layer(case_id: str, metric: str, scale_mode: str) -> dict[str, Any] | None:
    """Ask the backend for the map frame for one metric; compute nothing here.

    Every colour, height and legend stop comes from `src.domain.map_adapter`, which is the same
    module that feeds `docs/figures/` and the QGIS styles. The UI only renders what it is given.
    """
    try:
        bundle = load_case_bundle(case_id, processed_dir=ROOT / "data" / "processed")
    except LoaderError:
        return None

    payload = MA.build_map_payload(
        bundle,
        metric=metric,
        scale_mode=scale_mode,
        extrude_3d=False,
        include_h3_polygons=False,
    )
    if payload.is_empty:
        return None

    frame = payload.grid_frame.copy()
    legend = payload.legend
    return {
        "frame": frame,
        "view": payload.view_state,
        "legend": {
            "metric": legend.metric,
            "unit": legend.unit,
            "mode": legend.scale_mode,
            "min": legend.domain_min,
            "max": legend.domain_max,
            "caption": legend.caption,
        },
        "is_mock": payload.is_mock,
    }


def render_header() -> None:
    st.caption("DANCING WITH THE SARS")
    st.title("Three Cities, One Pump")
    st.write(
        "Explora cómo las observaciones SAR revelan subsidencia y cambios territoriales "
        "en ciudades con distinta compresibilidad del acuífero."
    )


def render_provenance(metadata: dict[str, Any] | None, case_id: str) -> None:
    st.subheader("Provenance")
    if metadata is None:
        st.info(
            f"Aún no hay artefactos publicados para **{CASES[case_id]}**. "
            "El frontend está listo para consumir `meta.json` y `nisar_grid.parquet`."
        )
        return

    provenance = metadata.get("provenance", {})
    product = metadata.get("product", "—")
    columns = st.columns(4)
    columns[0].metric("Fuente", provenance.get("source", "No disponible"))
    columns[1].metric("Revisita", f"{metadata.get('revisit_days', '—')} días")
    columns[2].metric("Fechas", len(metadata.get("dates", [])))
    columns[3].metric("CRS", metadata.get("crs", "No disponible"))
    if product == "mock":
        st.warning(
            "**Datos sintéticos (`product: mock`).** Ninguna cifra de esta demo proviene de un "
            "granulo NISAR real; el esquema y el pipeline sí son los definitivos.",
            icon="⚠️",
        )


def render_map(
    layer_data: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    layer_key: str,
) -> None:
    metric_label = LAYERS[layer_key][1]
    if layer_data is None:
        st.container(border=True).markdown(
            "### Mapa pendiente de datos\n"
            "El backend geoespacial no devolvió celdas para esta capa y ciudad."
        )
        return

    frame: pd.DataFrame = layer_data["frame"]
    view: MA.MapViewState = layer_data["view"]
    legend: dict[str, Any] = layer_data["legend"]

    deck = pdk.Deck(
        layers=[
            pdk.Layer(
                "ScatterplotLayer",
                data=frame,
                get_position=["lon", "lat"],
                get_fill_color="fill_color",
                get_radius=60,
                pickable=True,
                opacity=0.85,
            )
        ],
        initial_view_state=pdk.ViewState(
            latitude=view.latitude,
            longitude=view.longitude,
            zoom=view.zoom,
            pitch=0,
        ),
        tooltip={
            "html": "<b>{cell_id}</b><br/>velocidad: {velocity_mm_yr} mm/año<br/>"
            "coherencia: {coherence}",
            "style": {"backgroundColor": "#0B0F19", "color": "#F8FAFC"},
        },
    )
    st.pydeck_chart(deck, use_container_width=True)

    span = "—"
    if legend["min"] is not None and legend["max"] is not None:
        span = f"{legend['min']:g} … {legend['max']:g} {legend['unit']}"
    st.caption(
        f"Capa: **{metric_label}** · escala **{legend['mode']}** · {legend['caption']} · "
        f"dominio {span} · {len(frame):,} celdas publicadas · "
        f"fuente: {(metadata or {}).get('provenance', {}).get('source', '—')}"
    )


def render_app() -> None:
    st.set_page_config(
        page_title="Three Cities, One Pump",
        page_icon="🌎",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_header()

    with st.sidebar:
        st.header("Controles")
        case_id = st.selectbox("Ciudad", options=list(CASES), format_func=CASES.get)
        layer_key = st.selectbox(
            "Capa",
            options=list(LAYERS),
            format_func=lambda key: LAYERS[key][1],
        )
        scale_label = st.radio("Escala", list(SCALES))
        st.divider()
        st.caption(f"Contrato de datos: v1.0 · `{case_id}`")
        st.caption(
            "Capas disponibles: las tres columnas que el contrato publica "
            "(`velocity_mm_yr`, `coherence`, `delta_change`)."
        )

    metric = LAYERS[layer_key][0]
    scale_mode = SCALES[scale_label]
    metadata = load_case_metadata(case_id)

    render_provenance(metadata, case_id)
    st.divider()
    render_map(load_layer(case_id, metric, scale_mode), metadata, layer_key)


if __name__ == "__main__":
    render_app()
