from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


ROOT = Path(__file__).parent
CASES = {
    "mexico-city": "Ciudad de México",
    "jakarta": "Yakarta",
    "malaga": "Málaga",
}
LAYERS = {
    "velocity": "Velocidad",
    "coherence": "Coherencia",
    "backscatter": "Retrodispersión",
    "change": "Cambio",
    "events": "Eventos",
}


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
def load_grid(case_id: str) -> pd.DataFrame | None:
    grid_path = ROOT / "data" / "processed" / case_id / "nisar_grid.parquet"
    if not grid_path.exists():
        return None
    return pd.read_parquet(grid_path)


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
    columns = st.columns(4)
    columns[0].metric("Fuente", provenance.get("source", "No disponible"))
    columns[1].metric("Revisita", f"{metadata.get('revisit_days', '—')} días")
    columns[2].metric("Fechas", len(metadata.get("dates", [])))
    columns[3].metric("CRS", metadata.get("crs", "No disponible"))


def render_map_placeholder(
    grid: pd.DataFrame | None,
    metadata: dict[str, Any] | None,
    layer: str,
) -> None:
    if grid is None or metadata is None:
        st.container(border=True).markdown(
            "### Mapa pendiente de datos\n"
            "El backend geoespacial publicará las capas map-ready. "
            "Cuando estén disponibles, este panel consumirá sus coordenadas, "
            "colores, elevación y tooltips."
        )
        return

    if not {"lat", "lon"}.issubset(grid.columns):
        st.error("El artefacto no contiene las coordenadas requeridas por el mapa.")
        return

    st.map(grid[["lat", "lon"]].dropna(), size=10)
    st.caption(
        f"Capa seleccionada: **{LAYERS[layer]}** · "
        f"{len(grid):,} celdas · fuente: {metadata.get('provenance', {}).get('source', '—')}"
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
        case_id = st.selectbox(
            "Ciudad",
            options=list(CASES),
            format_func=CASES.get,
        )
        layer = st.selectbox(
            "Capa",
            options=list(LAYERS),
            format_func=LAYERS.get,
        )
        scale = st.radio("Escala", ["Compartida", "Independiente"])
        st.divider()
        st.caption(f"Contrato de datos: `{case_id}`")
        st.caption(f"Escala: {scale.lower()}")

    metadata = load_case_metadata(case_id)
    grid = load_grid(case_id)

    render_provenance(metadata, case_id)
    st.divider()
    render_map_placeholder(grid, metadata, layer)


if __name__ == "__main__":
    render_app()
