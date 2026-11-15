"""Tab 4. Quotes README text and the case meta. Does not invent science or a DAAC check."""

from __future__ import annotations

import html
from typing import Any, Mapping

import streamlit as st

from src.ui.formatters import DEMO_BANNER, NOT_DECLARED, format_json_block


def _public_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {key: value for key, value in meta.items() if "verif" not in key.lower()}


def _shown(value: Any) -> str:
    if value is None or value == "":
        return NOT_DECLARED
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
            f'<p class="kpi-value">{html.escape(_shown(value))}{html.escape(unit_text)}</p>'
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
            '<p class="kicker">case limitations</p>'
            + "".join(f"<p>- {html.escape(str(item))}</p>" for item in limitations)
            + "</div>",
            unsafe_allow_html=True,
        )


def render_physics_tab(loaders: Any, meta: dict[str, Any] | None) -> None:
    limits = getattr(loaders, "load_documented_limits", lambda: {})()
    st.markdown(
        '<div class="cockpit-card is-empty">'
        '<p class="kicker">Education</p>'
        '<p class="empty-prompt">No scientific text approved by Role 3.</p>'
        "</div>",
        unsafe_allow_html=True,
    )

    _regime_cards(meta)

    caveat = limits.get("section_8_caveat") if isinstance(limits, dict) else None
    caveat_body = (
        f"<p>{html.escape(str(caveat))}</p>"
        if caveat
        else "<p>README §8 does not provide a citable limitation.</p>"
    )
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">README §8</p>'
        f"{caveat_body}"
        "</div>",
        unsafe_allow_html=True,
    )
    items = limits.get("section_13") if isinstance(limits, dict) else None
    items_body = (
        "".join(f"<p>- {html.escape(str(item))}</p>" for item in items)
        if items
        else "<p>README §13 is not available.</p>"
    )
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">README §13 limitations</p>'
        '<p class="kpi-note">The numbered limitation list is in README §13, not in §8.</p>'
        f"{items_body}"
        "</div>",
        unsafe_allow_html=True,
    )

    shown = _public_meta(meta)
    if not shown:
        meta_note = "The loader did not return meta.json."
    elif shown.get("product") == "mock":
        meta_note = f"meta.json on disk. product is mock: {DEMO_BANNER}, not an observation."
    else:
        meta_note = "Content returned by the loader."
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">meta.json</p>'
        f"<p>{html.escape(meta_note)}</p>"
        f'{format_json_block(shown, "meta.json contents")}'
        "</div>",
        unsafe_allow_html=True,
    )
