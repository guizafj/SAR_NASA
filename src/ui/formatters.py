"""Presentation-only string helpers. No file reads and no science."""

from __future__ import annotations

import html
import json
from typing import Any, Sequence


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
