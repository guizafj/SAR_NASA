"""Small presentation primitives shared by the cockpit views."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src.ui.formatters import format_note, format_number


def kpi_card(
    label: Any,
    value: Any,
    unit: Any = None,
    *,
    note: Any = None,
    tooltip: Any = None,
    indicator: str | None = None,
) -> str:
    """Return an accessible KPI card; callers decide its Streamlit layout."""
    shown = "not declared" if value in (None, "") else format_number(value)
    unit_html = f'<span class="kpi-unit">{html.escape(str(unit))}</span>' if unit else ""
    note_html = f'<p class="kpi-note">{html.escape(format_note(note))}</p>' if note else ""
    indicator_html = (
        f'<span class="status-chip status-{html.escape(indicator)}">'
        f"{html.escape(indicator.replace('-', ' '))}</span>"
        if indicator
        else ""
    )
    title = f' title="{html.escape(str(tooltip), quote=True)}"' if tooltip else ""
    label_html = html.escape(str(label or "KPI"))
    return (
        f'<div class="cockpit-card kpi-card"{title}>'
        f'<p class="kicker">{label_html}</p>'
        f'<p class="kpi-value">{html.escape(shown)}{unit_html}</p>'
        f"{indicator_html}{note_html}</div>"
    )


def status_badge(label: Any, tone: str = "neutral") -> str:
    """Return a labeled badge; tone is visual only and never carries meaning alone."""
    allowed = {"neutral", "coherence", "moderate", "critical", "quality"}
    safe_tone = tone if tone in allowed else "neutral"
    return (
        f'<span class="status-chip status-{safe_tone}" role="status">'
        f"{html.escape(str(label))}</span>"
    )


def panel(title: str, *, expanded: bool = False, **kwargs: Any) -> Any:
    """Streamlit-native collapsible panel, kept as a shared construction point."""
    return st.expander(title, expanded=expanded, **kwargs)


def empty_state(
    title: str,
    explanation: str,
    *,
    icon: str = "⌕",
    action: str | None = None,
    key: str | None = None,
) -> bool:
    """Render a compact empty state and optionally a native action button."""
    st.markdown(
        '<section class="cockpit-card is-empty" role="status">'
        f'<span class="empty-icon" aria-hidden="true">{html.escape(icon)}</span>'
        f'<div><p class="empty-prompt">{html.escape(title)}</p>'
        f'<p class="kpi-note">{html.escape(explanation)}</p></div>'
        "</section>",
        unsafe_allow_html=True,
    )
    return bool(action and st.button(action, key=key))


def skeleton(label: str = "Loading", lines: int = 3) -> str:
    """Return an accessible static loading placeholder for future async panels."""
    count = max(1, min(int(lines), 6))
    bars = "".join('<span class="skeleton-bar" aria-hidden="true"></span>' for _ in range(count))
    return (
        f'<div class="cockpit-card cockpit-skeleton" role="status" '
        f'aria-label="{html.escape(label, quote=True)}">'
        f'<span class="sr-only">{html.escape(label)}</span>{bars}</div>'
    )
