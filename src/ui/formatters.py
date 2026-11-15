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
_RISK_WORD = re.compile(r"\b(estable|moderado|critico)\b")


def safe_inline(text: Any) -> str:
    """Escape HTML, then turn `**bold**` and `*italic*` into tags.

    Delivered copy carries markdown emphasis. Wrapping it in a raw <p> without
    this leaves the asterisks visible.
    """
    escaped = html.escape(str(text))

    def _replace(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return f"<strong>{match.group(1)}</strong>"
        return f"<em>{match.group(2)}</em>"

    return _INLINE_EMPHASIS.sub(_replace, escaped)


def format_note(note: Any) -> str:
    """Display-only translation of contract risk values inside a note.

    The note is backend copy. The enum value stays unchanged in the payload.
    """
    return _RISK_WORD.sub(lambda match: RISK_LABELS.get(match.group(0), match.group(0)), str(note))


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
    """Empty filter option, or a contract value with a display label when one exists."""
    if value == "":
        return "Unfiltered"
    return RISK_LABELS.get(value, value)


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
