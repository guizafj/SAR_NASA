"""Context layers. Empty unless Rol 3 has delivered the GeoJSON."""

from __future__ import annotations

from typing import Any

import streamlit as st

def render_hazard_tab(filters: dict[str, Any], loaders: Any) -> None:
    del filters
    loader = getattr(loaders, "load_context_layers", None)
    payload = loader() if loader is not None else None
    if not payload:
        st.markdown(
            '<div class="cockpit-card is-empty">'
            '<p class="kicker">Contexto</p>'
            '<p class="empty-prompt">Sin context_layers.geojson. El Rol 3 no lo ha entregado.</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        return
    st.caption(f"Capas entregadas: {payload.get('path', 'declarada')}")
    st.json(payload.get("summary") or {})
