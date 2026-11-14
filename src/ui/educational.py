"""Tab 4. Quotes README text. Does not invent science or a DAAC verification."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st


def _public_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {key: value for key, value in meta.items() if "verif" not in key.lower()}


def render_physics_tab(loaders: Any, meta: dict[str, Any] | None) -> None:
    limits = getattr(loaders, "load_documented_limits", lambda: {})()
    st.markdown(
        '<div class="cockpit-card is-empty">'
        '<p class="kicker">Educación</p>'
        '<p class="empty-prompt">Sin texto científico aprobado por el Rol 3.</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    caveat = limits.get("section_8_caveat") if isinstance(limits, dict) else None
    st.markdown('<p class="kicker">README §8</p>', unsafe_allow_html=True)
    if caveat:
        st.markdown(f"<p>{html.escape(str(caveat))}</p>", unsafe_allow_html=True)
    else:
        st.caption("README §8 no aporta una limitación citable.")
    items = limits.get("section_13") if isinstance(limits, dict) else None
    st.markdown(
        '<p class="kicker">Limitaciones README §13</p>',
        unsafe_allow_html=True,
    )
    st.caption("La lista numerada de limitaciones está en el README §13, no en el §8.")
    if items:
        for item in items:
            st.markdown(f"- {html.escape(str(item))}")
    else:
        st.caption("README §13 no disponible.")

    shown = _public_meta(meta)
    real = bool(shown) and shown.get("product") != "mock"
    st.markdown('<p class="kicker">meta.json</p>', unsafe_allow_html=True)
    if real:
        st.caption("Contenido devuelto por el loader.")
    else:
        st.caption("No hay meta.json real. Lo que sigue es el mock del loader, no una verificación.")
    st.json(shown)
