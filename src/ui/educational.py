"""Tab 4. Quotes README text and the case meta. Does not invent science or a DAAC check."""

from __future__ import annotations

import html
from typing import Any, Mapping

import streamlit as st

from src.ui.formatters import (
    DEMO_BANNER,
    NOT_DECLARED,
    format_json_block,
    format_number,
    humanize,
    safe_inline,
)


def _public_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    return {key: value for key, value in meta.items() if "verif" not in key.lower()}


def _shown(value: Any) -> str:
    if value is None or value == "":
        return NOT_DECLARED
    if isinstance(value, bool):
        return "emitted" if value else "not emitted"
    return format_number(value)


def _stat_card(column: Any, label: str, value: Any, unit: str | None) -> None:
    unit_text = f" {unit}" if unit and value not in (None, "") else ""
    column.markdown(
        '<div class="cockpit-card">'
        f'<p class="kicker">{html.escape(humanize(label))}</p>'
        f'<p class="kpi-value">{html.escape(_shown(value))}{html.escape(unit_text)}</p>'
        "</div>",
        unsafe_allow_html=True,
    )


def _case_block(meta: dict[str, Any]) -> None:
    stats = meta.get("stats") if isinstance(meta.get("stats"), Mapping) else {}
    cards: list[tuple[str, Any, str | None]] = []
    if isinstance(stats, dict):
        if "dominant_regime" in stats:
            regime = stats.get("dominant_regime")
            cards.append(("dominant_regime", humanize(regime) if regime else regime, None))
        if "median_ratio_R" in stats:
            cards.append(("median_ratio_R", stats.get("median_ratio_R"), None))
        if "median_velocity_mm_yr" in stats:
            cards.append(("median_velocity_mm_yr", stats.get("median_velocity_mm_yr"), "mm/year"))
        if "median_seasonal_amp_mm" in stats:
            cards.append(("median_seasonal_amp_mm", stats.get("median_seasonal_amp_mm"), "mm"))
        if "detection_limit_mm_yr" in stats:
            cards.append(("detection_limit_mm_yr", stats.get("detection_limit_mm_yr"), "mm/year"))
        if isinstance(stats.get("n_cells"), int) and not isinstance(stats.get("n_cells"), bool):
            cards.append(("n_cells", stats.get("n_cells"), None))
        series = stats.get("per_cell_series")
        if isinstance(series, dict) and "enabled" in series:
            cards.append(("per_cell_series.enabled", series.get("enabled"), None))
    case_note = (
        "Values come from the delivered mock artefact; they are not an observation."
        if meta.get("product") == "mock"
        else "Values come from the delivered artefact."
    )
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Case</p>'
        f"<p>{html.escape(case_note)}</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    if cards:
        columns = st.columns(len(cards))
        for column, (label, value, unit) in zip(columns, cards):
            _stat_card(column, label, value, unit)
        provenance = meta.get("provenance") if isinstance(meta.get("provenance"), Mapping) else {}
        source_id = meta.get("provenance_id") or provenance.get("provenance_id") or NOT_DECLARED
        st.caption(f"Provenance ID {source_id}")
    null_policy = meta.get("null_policy")
    if null_policy:
        st.markdown(
            '<div class="cockpit-card">'
            '<p class="kicker">How missing data is recorded</p>'
            f"<p>{safe_inline(null_policy)}</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    limitations = meta.get("limitations")
    if isinstance(limitations, list) and limitations:
        st.markdown(
            '<div class="cockpit-card">'
            '<p class="kicker">Limits of this artefact</p>'
            + "".join(f"<p>- {safe_inline(item)}</p>" for item in limitations)
            + "</div>",
            unsafe_allow_html=True,
        )


def render_physics_tab(loaders: Any, meta: dict[str, Any] | None) -> None:
    limits = getattr(loaders, "load_documented_limits", lambda: {})()
    has_meta = isinstance(meta, dict) and bool(meta)
    has_docs = isinstance(limits, dict) and bool(limits.get("section_8_caveat") or limits.get("section_13"))
    if not has_meta and not has_docs:
        st.markdown(
            '<div class="cockpit-card is-empty">'
            '<p class="kicker">Education</p>'
            '<p class="empty-prompt">Educational notes are not available for this case yet.</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    if has_meta:
        _case_block(meta)
    else:
        st.markdown(
            '<div class="cockpit-card is-empty">'
            '<p class="kicker">Case</p>'
            '<p class="empty-prompt">The loader did not return case meta.</p>'
            "</div>",
            unsafe_allow_html=True,
        )

    caveat = limits.get("section_8_caveat") if isinstance(limits, dict) else None
    caveat_body = (
        f"<p>{safe_inline(caveat)}</p>"
        if caveat
        else "<p>No documented caveat for this case.</p>"
    )
    items = limits.get("section_13") if isinstance(limits, dict) else None
    items_body = (
        "".join(f"<p>- {safe_inline(item)}</p>" for item in items)
        if items
        else "<p>No documented limitations for this case.</p>"
    )
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Known caveat</p>'
        '<p class="kpi-note">From the project documentation, not from the case artefact.</p>'
        f"{caveat_body}"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Known limitations</p>'
        '<p class="kpi-note">From the project documentation, not from the case artefact.</p>'
        f"{items_body}"
        "</div>",
        unsafe_allow_html=True,
    )

    shown = _public_meta(meta)
    if not shown:
        meta_note = "The loader did not return meta.json."
    elif shown.get("product") == "mock":
        meta_note = f"Full record from disk. product is mock: {DEMO_BANNER}, not an observation."
    else:
        meta_note = "Full record returned by the loader."
    with st.expander("Full meta.json record", expanded=False):
        st.caption(meta_note)
        st.markdown(
            format_json_block(shown, "meta.json contents"),
            unsafe_allow_html=True,
        )
