"""Tab 4. Quotes README text and the case meta. Does not invent science or a DAAC check."""

from __future__ import annotations

import html
import re
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


# Presentation triage (P0-2): the README §13 lines are verbatim backend copy; the UI only
# decides what a jury reads first. Nothing here edits or paraphrases the text.
_TECHNICAL_ID = re.compile(r"O-\d\d")
_TECHNICAL_MAX_CHARS = 220
_CAVEAT_LEAD_SENTENCES = 2


def split_technical(items: list[str] | None) -> tuple[list[str], list[str]]:
    """(reader items, technical items). Technical: >220 chars, backticks, or O-NN ids."""
    visible: list[str] = []
    technical: list[str] = []
    for item in items or []:
        text = str(item)
        if (
            len(text) <= _TECHNICAL_MAX_CHARS
            and "`" not in text
            and not _TECHNICAL_ID.search(text)
        ):
            visible.append(text)
        else:
            technical.append(text)
    return visible, technical


def split_caveat(caveat: str | None) -> tuple[str, str]:
    """First two sentences lead; the rest goes to the technical notes. No text is lost."""
    text = str(caveat or "")
    if not text:
        return "", ""
    segments = text.split(". ")
    if len(segments) <= _CAVEAT_LEAD_SENTENCES:
        return text, ""
    lead = ". ".join(segments[:_CAVEAT_LEAD_SENTENCES]).rstrip(".")
    rest = ". ".join(segments[_CAVEAT_LEAD_SENTENCES:]).strip()
    return f"{lead}.", rest


def _median_from_bridge(loaders: Any, meta: dict[str, Any] | None) -> tuple[Any, Any]:
    """Same key as tab 1: the 'Median velocity' item of `get_map_kpis`, unfiltered."""
    case_id = (meta or {}).get("case_id")
    function = getattr(loaders, "get_map_kpis", None)
    if function is None or not case_id:
        return None, None
    try:
        items = function(str(case_id), {})
    except TypeError:
        return None, None
    for item in items or []:
        if isinstance(item, Mapping) and str(item.get("label", "")).strip().lower() == "median velocity":
            return item.get("value"), item.get("unit")
    return None, None


def _case_block(
    meta: dict[str, Any],
    case_roles: Mapping[str, Any] | None = None,
    median: Any = None,
    median_unit: Any = None,
) -> None:
    stats = meta.get("stats") if isinstance(meta.get("stats"), Mapping) else {}
    cards: list[tuple[str, Any, str | None]] = []
    if isinstance(stats, dict):
        if "dominant_regime" in stats:
            regime = stats.get("dominant_regime")
            cards.append(("dominant_regime", humanize(regime) if regime else regime, None))
        if "median_ratio_R" in stats:
            cards.append(("median_ratio_R", stats.get("median_ratio_R"), None))
        if "median_velocity_mm_yr" in stats:
            # One figure for one magnitude: read the bridge key when available.
            value = median if median is not None else stats.get("median_velocity_mm_yr")
            unit = median_unit if isinstance(median_unit, str) else "mm/year"
            cards.append(("median_velocity_mm_yr", value, unit))
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
    case_id = str(meta.get("case_id") or "")
    role_info = (case_roles or {}).get(case_id) if isinstance(case_roles, Mapping) else None
    role_lines = ""
    if isinstance(role_info, Mapping):
        role_text = role_info.get("role")
        exp_regime = role_info.get("expected_regime")
        pub_rate = role_info.get("published_rate_mm_yr")
        details: list[str] = []
        if role_text:
            details.append(f"Declared role: {role_text}")
        if exp_regime:
            details.append(f"Expected regime: {humanize(exp_regime)}")
        if isinstance(pub_rate, (int, float)) and not isinstance(pub_rate, bool):
            details.append(f"Literature reference rate: {format_number(pub_rate)} mm/year")
        if details:
            role_lines = f'<p class="kpi-note">{" · ".join(html.escape(part) for part in details)}</p>'
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Case</p>'
        f"<p>{html.escape(case_note)}</p>"
        f"{role_lines}"
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
        case_roles = limits.get("case_roles") if isinstance(limits, Mapping) else None
        median, median_unit = _median_from_bridge(loaders, meta)
        _case_block(
            meta,
            case_roles if isinstance(case_roles, Mapping) else None,
            median,
            median_unit,
        )
    else:
        st.markdown(
            '<div class="cockpit-card is-empty">'
            '<p class="kicker">Case</p>'
            '<p class="empty-prompt">The loader did not return case meta.</p>'
            "</div>",
            unsafe_allow_html=True,
        )

    science_summary = limits.get("science_section_1") if isinstance(limits, Mapping) else None
    if science_summary:
        st.markdown(
            '<div class="cockpit-card">'
            '<p class="kicker">SAR physics and regime classification</p>'
            '<p class="kpi-note">From docs/science-guide.md §1 and docs/data-contract-v1.md §7.5.</p>'
            f"<p>{safe_inline(science_summary)}</p>"
            '<p class="detail-line"><strong>Regime index R = |velocity| / seasonal amplitude:</strong> '
            "R &gt; 2 Inelastic (secular trend dominates, permanent compaction) · "
            "1 &le; R &le; 2 Transitional (mixed regime) · "
            "R &lt; 1 Elastic (seasonal cycle dominates, recoverable motion).</p>"
            "</div>",
            unsafe_allow_html=True,
        )

    caveat = limits.get("section_8_caveat") if isinstance(limits, dict) else None
    caveat_lead, caveat_rest = split_caveat(caveat)
    items = limits.get("section_13") if isinstance(limits, dict) else None
    visible, technical = split_technical(items)
    note_lines = [f'<p class="tech-line caveat">{safe_inline(caveat_rest)}</p>'] if caveat_rest else []
    note_lines += [f'<p class="tech-line item">- {safe_inline(item)}</p>' for item in technical]
    notes_count = len(note_lines)
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Known caveat</p>'
        '<p class="kpi-note">From the project documentation, not from the case artefact.</p>'
        + (f'<p class="caveat-lead">{safe_inline(caveat_lead)}</p>' if caveat_lead
           else "<p>No documented caveat for this case.</p>")
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="cockpit-card">'
        '<p class="kicker">Known limitations</p>'
        '<p class="kpi-note">From the project documentation, not from the case artefact.</p>'
        + ("".join(f'<p class="limit-line">- {safe_inline(item)}</p>' for item in visible)
           or "<p>No reader-level limitations; see the technical notes below.</p>")
        + "</div>",
        unsafe_allow_html=True,
    )
    if notes_count:
        with st.expander(f"Technical notes ({notes_count})", expanded=False):
            st.caption(
                "Same loader text, not trimmed. Internal ids, file paths and long method notes "
                "live here."
            )
            st.markdown("".join(note_lines), unsafe_allow_html=True)

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
