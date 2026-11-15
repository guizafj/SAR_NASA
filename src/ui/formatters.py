"""Presentation-only string helpers. No file reads and no science."""

from __future__ import annotations

import html
import json
import re
from typing import Any, Sequence

NOT_DECLARED = "not declared"
SOURCE_NOT_DECLARED = "source not declared"
UNIT_NOT_DECLARED = "unit not declared"
DEMO_BANNER = "DEMONSTRATION DATA"

# Display only. Filter values stay the contract enums.
RISK_LABELS = {
    "estable": "Stable",
    "moderado": "Moderate",
    "critico": "Critical",
}
SCALE_LABELS = {
    "independent": "Per case",
    "shared": "Shared across cases",
}
# Legacy Spanish labels still resolve, so an old session or caller does not flip the scale.
_SHARED_SCALE = frozenset(
    {
        "shared",
        "común a todos los casos",
        "comun a todos los casos",
        "compartida",
    }
)


_INLINE_EMPHASIS = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_RISK_WORD = re.compile(r"\b(estable|moderado|critico)\b")
_RISK_AFTER_NOUN = re.compile(r"\b(band|level|class)\s+(estable|moderado|critico)\b")

# Display labels for known fields. Unknown keys fall back to a readable form.
FIELD_LABELS = {
    "dominant_regime": "Dominant regime",
    "median_ratio_R": "Median ratio R",
    "median_velocity_mm_yr": "Median velocity",
    "median_seasonal_amp_mm": "Median seasonal amplitude",
    "detection_limit_mm_yr": "Detection limit",
    "n_cells": "Cells",
    "n_dates": "Dates",
    "n_series_rows": "Series rows",
    "per_cell_series.enabled": "Per-cell series",
    "annual_coverage": "Annual coverage",
    "design_condition": "Design condition",
    "seasonal_n_harmonics": "Seasonal harmonics",
    "null_policy": "Null policy",
    "velocity_mm_yr": "Velocity",
    "delta_change": "Displacement",
    "coherence": "Coherence",
    "ratio_R": "Ratio R",
    "seasonal_amp_mm": "Seasonal amplitude",
    "risk_level": "Risk band",
    "land_use": "Land use",
    "admin_boundary": "Administrative boundary",
    "infrastructure": "Infrastructure",
    "water": "Water",
    "provenance_id": "Provenance ID",
    "source": "Source",
    "class": "Class",
    "name": "Feature name",
    "layer": "Layer",
}


def humanize(value: Any) -> str:
    """Readable label for a contract key or value. Never changes stored data."""
    text = str(value)
    if text in FIELD_LABELS:
        return FIELD_LABELS[text]
    return text.replace("_", " ").replace("-", " ").strip().capitalize()


def format_number(value: Any, decimals: int = 2) -> str:
    """Trim float noise for display: 41.3767 -> 41.38, 11694.0 -> 11694."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value)}"
        return f"{round(float(value), decimals):g}"
    return str(value)


def safe_inline(text: Any) -> str:
    """Escape HTML, then render `**bold**`, `*italic*` and `code`.

    Delivered copy carries markdown emphasis. Wrapping it in a raw <p> without
    this leaves the asterisks and backticks visible.
    """
    escaped = html.escape(str(text))

    def _replace(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return f"<strong>{match.group(1)}</strong>"
        return f"<em>{match.group(2)}</em>"

    rendered = _INLINE_EMPHASIS.sub(_replace, escaped)
    rendered = _INLINE_CODE.sub(lambda match: f"<code>{match.group(1)}</code>", rendered)
    # Delivered copy can be truncated mid-emphasis; do not leak the markers.
    return rendered.replace("**", "")


def format_note(note: Any) -> str:
    """Display-only translation of contract risk values inside a note.

    The note is backend copy. The enum value stays unchanged in the payload.
    `band critico` reads as `band critical`, not `band Critical`.
    """
    text = str(note)

    def _band(match: re.Match[str]) -> str:
        return f"{match.group(1)} {RISK_LABELS.get(match.group(2), match.group(2)).lower()}"

    text = _RISK_AFTER_NOUN.sub(_band, text)
    text = _RISK_WORD.sub(lambda match: RISK_LABELS.get(match.group(0), match.group(0)), text)
    # Readable phrasing for the delivered KPI notes.
    text = text.replace("cells in contract band critical", "cells in the critical band")
    text = text.replace("coherence >=", "coherence at or above")
    text = text.replace("|velocity| >=", "|velocity| at or above")
    return text


def format_dates_span(dates: Sequence[Any] | None) -> str | None:
    """Compact acquisition dates for chrome: first … last (n).

    Listing every revisit with ' … ' between items overflows the mission bar
    and map footer, clipping provenance_id off-screen.
    """
    if not isinstance(dates, (list, tuple)):
        return None
    shown = [str(item) for item in dates if item not in (None, "")]
    if not shown:
        return None
    if len(shown) == 1:
        return f"dates {shown[0]}"
    return f"dates {shown[0]} … {shown[-1]} ({len(shown)})"


def risk_label(value: Any) -> str:
    """English chip/filter label. Unknown values are shown as stored."""
    if value in (None, ""):
        return f"risk {NOT_DECLARED}"
    key = str(value)
    return RISK_LABELS.get(key, key)


def choice_label(value: str) -> str:
    """Empty filter option, or a readable label for a contract value.

    The value sent back to the loader is unchanged; only the label is humanized.
    """
    if value == "":
        return "Unfiltered"
    return RISK_LABELS.get(value, humanize(value))


def is_shared_scale(mode: Any) -> bool:
    """True for the canonical shared mode and the legacy Spanish aliases."""
    return str(mode or "").strip().lower() in _SHARED_SCALE


def format_json_block(data: Any, aria_label: str) -> str:
    """Render JSON as a single-line HTML block without <pre> or <code>.

    Streamlit's Markdown renderer intercepts <pre>, <code> and 4-space indented
    newlines and replaces them with its Emotion syntax highlighter. Using <div>
    and <span> rows keeps the block under the active CSS theme variables.
    """
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.lstrip(" ")
        indent_level = (len(line) - len(stripped)) // 2
        pad_rem = round(indent_level * 0.9, 2)
        if ":" in stripped and stripped.startswith('"'):
            key_part, _, rest = stripped.partition(":")
            row_html = (
                f'<span class="json-key">{html.escape(key_part)}</span>'
                f'<span class="json-punct">:</span>'
                f'<span class="json-val">{html.escape(rest)}</span>'
            )
        else:
            row_html = f'<span class="json-val">{html.escape(stripped)}</span>'
        lines.append(
            f'<div class="json-line" style="padding-left:{pad_rem}rem">{row_html}</div>'
        )
    return (
        f'<div class="cockpit-json" role="region" aria-label="{html.escape(aria_label)}">'
        f'{"".join(lines)}</div>'
    )
