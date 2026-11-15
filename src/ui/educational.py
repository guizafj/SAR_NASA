"""Tab 4. Quotes README text and the case meta. Does not invent science or a DAAC check."""

from __future__ import annotations

import html
from typing import Any, Mapping

import streamlit as st


def _public_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {key: value for key, value in meta.items() if "verif" not in key.lower()}


def _shown(value: Any) -> str:
    if value is None or value == "":
        return "no declarado"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _regime_cards(meta: dict[str, Any] | None) -> None:
    if not isinstance(meta, dict):
        return
    stats = meta.get("stats") if isinstance(meta.get("stats"), Mapping) else None
    if not isinstance(stats, dict) or not stats:
        return
    cards = [
        ("dominant_regime", stats.get("dominant_regime"), None),
        ("median_ratio_R", stats.get("median_ratio_R"), None),
        ("median_velocity_mm_yr", stats.get("median_velocity_mm_yr"), "mm/year"),
    ]
    columns = st.columns(len(cards))
    for column, (label, value, unit) in zip(columns, cards):
        unit_text = f" {unit}" if unit and value not in (None, "") else ""
        column.markdown(
            '<div class="cockpit-card">'
            f'<p class="kicker">{html.escape(label)}</p>'
            f"<p>{html.escape(_shown(value))}{html.escape(unit_text)}</p>"
            "</div>",
            unsafe_allow_html=True,
        )

    null_policy = meta.get("null_policy")
    if null_policy:
        st.markdown(
            '<div class="cockpit-card">'
            '<p class="kicker">null_policy</p>'
            f"<p>{html.escape(str(null_policy))}</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    limitations = meta.get("limitations")
    if isinstance(limitations, list) and limitations:
        st.markdown(
            '<div class="cockpit-card">'
            '<p class="kicker">limitaciones del caso</p>'
            + "".join(f"<p>- {html.escape(str(item))}</p>" for item in limitations)
            + "</div>",
            unsafe_allow_html=True,
        )


def render_physics_tab(loaders: Any, meta: dict[str, Any] | None) -> None:
    limits = getattr(loaders, "load_documented_limits", lambda: {})()
    st.markdown(
        '<div class="cockpit-card is-empty">'
        '<p class="kicker">Educación</p>'
        '<p class="empty-prompt">Sin texto científico aprobado por el Rol 3.</p>'
        "</div>",
        unsafe_allow_html=True,
    )

    _regime_cards(meta)

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
    st.markdown('<p class="kicker">meta.json</p>', unsafe_allow_html=True)
    if not shown:
        st.caption("El loader no devolvió meta.json.")
    elif shown.get("product") == "mock":
        st.caption("meta.json en disco. product es mock: DATOS DE DEMOSTRACIÓN, no una observación.")
    else:
        st.caption("Contenido devuelto por el loader.")
    st.json(shown)
