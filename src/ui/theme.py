"""Palette, type and the only CSS injection point. No data and no science."""

from __future__ import annotations

from typing import Any

import streamlit as st

# Dark is the locked cockpit. Light is the same instrument on paper: same roles,
# high-contrast inks (AAA body & secondary text, 6.7:1 accent).
DARK = {
    "bg": "#0B0F19",
    "surface": "#0F172A",
    "border": "#1E293B",
    "accent": "#06B6D4",
    "coherence": "#10B981",
    "alert": "#F59E0B",
    "coral": "#F43F5E",
    "text": "#F8FAFC",
    "muted": "#CBD5E1",
    "focus": "#141F36",
    "on-accent": "#0B0F19",
}
LIGHT = {
    "bg": "#F1F5F9",
    "surface": "#FFFFFF",
    "border": "#94A3B8",
    "accent": "#0B647D",
    "coherence": "#047857",
    "alert": "#9A3412",
    "coral": "#BE123C",
    "text": "#0B1220",
    "muted": "#334155",
    "focus": "#E0F2FE",
    "on-accent": "#FFFFFF",
}
BG = DARK["bg"]
SURFACE = DARK["surface"]
BORDER = DARK["border"]
ACCENT = DARK["accent"]
COHERENCE = DARK["coherence"]
ALERT = DARK["alert"]
CORAL = DARK["coral"]
TEXT = DARK["text"]
MUTED = "#94A3B8"
FOCUS = DARK["focus"]
RADIUS = "4px"

# Design tokens: dimensions, typography, borders, shadow and motion are declared
# here so the presentation layer has one source of visual values.
DESIGN_TOKENS = {
    "space-0": "0",
    "space-1": "4px",
    "space-2": "8px",
    "space-3": "12px",
    "space-4": "16px",
    "space-5": "20px",
    "space-6": "24px",
    "space-8": "32px",
    "radius-sm": "2px",
    "radius-md": RADIUS,
    "border-width": "1px",
    "shadow-menu": "0 4px 12px rgba(0, 0, 0, 0.14)",
    "type-xs": "0.6875rem",
    "type-sm": "0.75rem",
    "type-md": "0.875rem",
    "type-lg": "1rem",
    "motion-fast": "180ms",
    "motion-standard": "200ms",
}

#: Categorical identity slots for the three cities, fixed order, validated with
#: `dataviz/scripts/validate_palette.py` on both cockpit surfaces (light: one WARN
#: at 2.82:1 for Málaga, hence the mandatory direct label). Never cycled.
CASE_COLORS = {
    "light": {"mexico-city": "#2a78d6", "jakarta": "#eb6834", "malaga": "#1baf7a"},
    "dark": {"mexico-city": "#3987e5", "jakarta": "#d95926", "malaga": "#199e70"},
}

# Diverging velocity ramps, all stops at the same normalized positions.
# `colorblind` uses a blue / neutral / vermilion sequence (not red vs green).
MAP_RAMPS = {
    "classic": (
        (0.0, (244, 63, 94, 200)),
        (0.5, (245, 158, 11, 200)),
        (1.0, (6, 182, 212, 200)),
    ),
    "colorblind": (
        (0.0, (0, 114, 178, 200)),
        (0.5, (225, 225, 225, 200)),
        (1.0, (213, 94, 0, 200)),
    ),
}
MAP_RAMP_LABELS = {
    "classic": "Classic · coral / amber / cyan",
    "colorblind": "Colorblind-friendly · blue / neutral / orange",
}

FONT_DISPLAY = '"Space Grotesk", "Segoe UI", system-ui, sans-serif'
FONT_BODY = '"Inter", "Segoe UI", system-ui, sans-serif'
FONT_MONO = '"JetBrains Mono", "DejaVu Sans Mono", ui-monospace, monospace'
_CHART_FONT = "JetBrains Mono, ui-monospace, monospace"


def _decls(tokens: dict[str, str]) -> str:
    return "\n".join(f"  --{name}: {value};" for name, value in tokens.items())


def reported_scheme() -> str | None:
    """Resolve active scheme from user override first, then Streamlit system context."""
    try:
        chosen = st.session_state.get("ui_theme_mode")
    except Exception:
        chosen = None
    if chosen in ("light", "dark"):
        return str(chosen)
    try:
        kind = st.context.theme.type
    except Exception:
        return None
    if kind in ("light", "dark"):
        return kind
    return None


def active_palette() -> dict[str, str]:
    """Palette for server-rendered figures. Chrome CSS does not need this."""
    if reported_scheme() == "light":
        return LIGHT
    return DARK


def case_color(case_id: str) -> str:
    """Identity color for a case (fixed slot, never by rank or filter order)."""
    scheme = "light" if reported_scheme() == "light" else "dark"
    palette = CASE_COLORS[scheme]
    return palette.get(str(case_id), active_palette()["muted"])


def style_figure(
    figure: Any,
    *,
    height: int,
    x_title: str,
    y_title: str,
    margin: dict[str, int] | None = None,
) -> None:
    """Paint a Plotly figure with the active palette. No data transform."""
    palette = active_palette()
    figure.update_layout(
        height=height,
        margin=margin or {"l": 52, "r": 18, "t": 20, "b": 42},
        paper_bgcolor=palette["surface"],
        plot_bgcolor=palette["bg"],
        font={"color": palette["text"], "family": _CHART_FONT, "size": 11},
        hoverlabel={
            "bgcolor": palette["surface"],
            "bordercolor": palette["border"],
            "font": {"color": palette["text"], "family": _CHART_FONT, "size": 11},
        },
    )
    axis = {
        "gridcolor": palette["border"],
        "zerolinecolor": palette["border"],
        "linecolor": palette["border"],
        "tickfont": {"color": palette["text"], "family": _CHART_FONT, "size": 11},
        "title_font": {"color": palette["muted"], "family": _CHART_FONT, "size": 11},
        "automargin": True,
        "title_standoff": 8,
    }
    figure.update_xaxes(title=x_title, **axis)
    figure.update_yaxes(title=y_title, **axis)


_CSS = f"""
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap");

:root {{
  --radius: var(--radius-md);
  --card-pad-y: 0.6rem;
  --card-pad-x: 1.05rem;
  --gap: 0.75rem;
  --map-height: clamp(415px, calc(100vh - 485px), 700px);
  --font-display: {FONT_DISPLAY};
  --font-body: {FONT_BODY};
  --font-mono: {FONT_MONO};
{_decls(DESIGN_TOKENS)}
{_decls(DARK)}
  color-scheme: dark;
}}

@media (prefers-color-scheme: light) {{
  :root {{
{_decls(LIGHT)}
    color-scheme: light;
  }}
}}

:root:has([data-cockpit="dark"]),
.stApp:has([data-cockpit="dark"]) {{
{_decls(DARK)}
  color-scheme: dark;
}}

:root:has([data-cockpit="light"]),
.stApp:has([data-cockpit="light"]) {{
{_decls(LIGHT)}
  color-scheme: light;
}}

html,
body,
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"] {{
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: var(--font-body);
}}

[data-testid="stHeader"] {{
  background: transparent !important;
  height: 1.75rem !important;
}}

[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {{
  display: none !important;
}}

.block-container {{
  padding-top: 2.5rem !important;
  padding-bottom: 1.5rem !important;
  padding-left: clamp(0.85rem, 2.2vw, 2.25rem) !important;
  padding-right: clamp(0.85rem, 2.2vw, 2.25rem) !important;
  width: 100% !important;
  max-width: none !important;
  box-sizing: border-box;
}}

[data-testid="stVerticalBlock"] {{
  gap: 0.75rem !important;
}}

.mission-bar {{
  display: flex;
  flex-direction: column;
  gap: 0.22rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.32rem 1.1rem 0.4rem;
  margin: 0;
  box-sizing: border-box;
}}

/* Streamlit's element container carries margin-bottom: -1rem; with that native
   pull the banner started 4 px inside the header box. Neutralise it for the two
   chrome blocks and keep a clean 7 px separation. */
/* Streamlit's markdown container carries margin-bottom: -1rem natively; that is
   what pulled the banner 4 px inside the header box. Neutralise it on the two
   chrome blocks and keep a clean ~8 px separation. */
[data-testid="stMarkdownContainer"]:has(> .mission-bar),
[data-testid="stMarkdownContainer"]:has(> .demo-banner) {{
  margin-bottom: -4px !important;
}}

.mission-bar .mission-title,
.mission-bar .mission-title span {{
  font-family: var(--font-display) !important;
  font-size: clamp(1.2rem, 2vw, 1.65rem) !important;
  font-weight: 600 !important;
  letter-spacing: -0.02em;
  line-height: 1.3 !important;
  color: var(--text) !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: visible !important;
  white-space: normal !important;
  height: auto !important;
  max-height: none !important;
  max-width: none;
}}

.mission-bar .mission-title .case-pill {{
  display: inline-flex;
  vertical-align: middle;
  margin-left: var(--space-3) !important;
  padding: var(--space-1) var(--space-2) !important;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--accent) !important;
  font-family: var(--font-mono) !important;
  font-size: var(--type-xs) !important;
  font-weight: 500 !important;
  letter-spacing: 0;
  line-height: 1.2;
}}

.mission-bar .telemetry {{
  margin: 0;
}}

.telemetry {{
  font-family: var(--font-mono);
  font-size: 0.78rem;
  letter-spacing: 0.03em;
  color: var(--muted) !important;
  margin: 0;
  line-height: 1.5;
  overflow: visible;
  word-break: break-word;
  overflow-wrap: anywhere;
  white-space: normal;
}}

.demo-banner {{
  margin: 0;
  padding: 0.32rem 1.1rem;
  border: 1px solid var(--alert);
  border-radius: var(--radius);
  background: color-mix(in srgb, var(--alert) 12%, var(--surface));
  color: var(--alert) !important;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  box-sizing: border-box;
}}

.cockpit-card,
.viewport {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--card-pad-y) var(--card-pad-x);
  box-shadow: none;
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
}}

.cockpit-card {{
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  height: 100%;
  overflow: visible;
  margin-bottom: 1rem;
}}

.cockpit-card.is-empty {{
  min-height: 6.5rem;
  justify-content: center;
  gap: 0.45rem;
}}

.viewport {{
  min-height: var(--map-height);
}}

.kicker {{
  font-family: var(--font-mono);
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent) !important;
  margin: 0;
  line-height: 1.3;
}}

.cockpit-card p,
.viewport p {{
  font-family: var(--font-body);
  font-size: 0.88rem;
  color: var(--text) !important;
  margin: 0;
  line-height: 1.5;
  overflow-wrap: anywhere;
}}

.cockpit-card .kicker {{
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--accent) !important;
}}

.cockpit-card .empty-prompt {{
  font-family: var(--font-body);
  font-size: 0.92rem;
  line-height: 1.45;
  color: var(--text) !important;
  margin: 0;
}}

.cockpit-card .kpi-value {{
  font-family: var(--font-mono);
  font-size: clamp(1.05rem, 1.35vw, 1.25rem);
  font-weight: 600;
  line-height: 1.3;
  color: var(--text) !important;
  margin: 0;
  word-break: break-word;
}}

.cockpit-card .kpi-note {{
  font-family: var(--font-body);
  font-size: 0.76rem;
  line-height: 1.4;
  color: var(--muted) !important;
  margin: 0;
}}

.cockpit-card p code,
.cockpit-card li code {{
  background: var(--bg);
  color: var(--accent);
  border: 1px solid var(--border);
  border-radius: 2px;
  padding: 0 0.25rem;
  font-family: var(--font-mono);
  font-size: 0.78rem;
}}

.cockpit-card .detail-head {{
  font-family: var(--font-mono);
  font-size: var(--type-xs);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent) !important;
  margin: 0.45rem 0 0.1rem !important;
}}

.cockpit-card .detail-head:first-of-type {{
  margin-top: 0 !important;
}}

.cockpit-card .detail-line {{
  font-family: var(--font-mono);
  font-size: 0.75rem;
  line-height: 1.45;
  color: var(--text) !important;
  margin: 0;
  padding: 0.16rem 0;
  border-bottom: 1px solid color-mix(in srgb, var(--border) 45%, transparent);
  word-break: break-word;
  overflow-wrap: anywhere;
}}

.cockpit-card.inspector {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
  gap: 0.2rem 0.75rem;
  align-content: start;
}}

.cockpit-card.inspector .detail-line {{
  font-size: var(--type-xs);
  line-height: 1.3;
  padding: 0.1rem 0;
}}

.cockpit-card.inspector .detail-head {{
  grid-column: 1 / -1;
  margin-top: 0.35rem;
}}

.cockpit-card.inspector .detail-head:first-child {{
  margin-top: 0;
}}

.cockpit-card .detail-line:last-child {{
  border-bottom: 0;
}}

.cockpit-card .chip {{
  align-self: flex-start;
  margin: 0.1rem 0 0.25rem;
}}

.chip {{
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 8px;
  border-radius: 2px;
  border: 1px solid color-mix(in srgb, var(--coherence) 45%, transparent);
  background: color-mix(in srgb, var(--coherence) 14%, var(--surface));
  color: var(--coherence) !important;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  font-weight: 600;
  line-height: 1.2;
}}

.chip-alert {{
  border-color: color-mix(in srgb, var(--alert) 50%, transparent);
  background: color-mix(in srgb, var(--alert) 14%, var(--surface));
  color: var(--alert) !important;
}}

.chip-coral {{
  border-color: color-mix(in srgb, var(--coral) 50%, transparent);
  background: color-mix(in srgb, var(--coral) 14%, var(--surface));
  color: var(--coral) !important;
}}

.swatch {{
  display: inline-block;
  width: 0.7rem;
  height: 0.7rem;
  margin-right: 0.45rem;
  border: 1px solid var(--border);
  vertical-align: middle;
}}

.chip-muted {{
  border-color: var(--border);
  background: var(--bg);
  color: var(--text) !important;
}}

.cockpit-json {{
  margin: 0.35rem 0 0;
  padding: 0.75rem 0.95rem;
  background-color: var(--bg) !important;
  color: var(--text) !important;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 0.76rem;
  line-height: 1.55;
  overflow-x: auto;
  box-sizing: border-box;
  opacity: 1 !important;
}}

.cockpit-json .json-line {{
  font-family: var(--font-mono) !important;
  font-size: 0.76rem !important;
  line-height: 1.55 !important;
  color: var(--text) !important;
  white-space: pre-wrap;
  word-break: break-word;
}}

.cockpit-json .json-key,
[data-testid="stMarkdownContainer"] .cockpit-json .json-key {{
  color: var(--accent) !important;
  -webkit-text-fill-color: var(--accent) !important;
  font-weight: 600 !important;
}}

.cockpit-json .json-punct,
.cockpit-json .json-val,
[data-testid="stMarkdownContainer"] .cockpit-json .json-punct,
[data-testid="stMarkdownContainer"] .cockpit-json .json-val {{
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
}}

[data-testid="stMarkdownPre"],
[data-testid="stCode"],
[data-testid="stMarkdownPre"] pre,
[data-testid="stMarkdownPre"] code {{
  background-color: var(--bg) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
  border-color: var(--border) !important;
}}

.cockpit-table-wrap {{
  width: 100%;
  overflow-x: auto;
  margin-top: 0.35rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background-color: var(--bg) !important;
  color: var(--text) !important;
  color-scheme: inherit;
}}

.cockpit-table {{
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  background-color: var(--bg) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
}}

.cockpit-table th,
.cockpit-table td,
[data-testid="stMarkdownContainer"] table th,
[data-testid="stMarkdownContainer"] table td {{
  padding: 0.5rem 0.75rem !important;
  text-align: left;
  border: 1px solid color-mix(in srgb, var(--border) 65%, transparent) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
  background-color: var(--bg) !important;
  vertical-align: top;
}}

.cockpit-table th,
[data-testid="stMarkdownContainer"] table th {{
  background-color: color-mix(in srgb, var(--accent) 12%, var(--surface)) !important;
  color: var(--accent) !important;
  -webkit-text-fill-color: var(--accent) !important;
  font-weight: 600 !important;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  white-space: nowrap;
}}

.cockpit-table tr:last-child td {{
  border-bottom: 0;
}}

.cockpit-table tbody tr.row-alert > td {{
  background: color-mix(in srgb, var(--coral) 12%, transparent) !important;
}}

.cockpit-table tbody tr.row-alert > td:first-child {{
  border-left: 3px solid var(--coral) !important;
  font-weight: 500;
}}

.cockpit-table tbody tr.row-warn > td {{
  background: color-mix(in srgb, var(--alert) 10%, transparent) !important;
}}

.cockpit-table tbody tr.row-warn > td:first-child {{
  border-left: 3px solid var(--alert) !important;
}}

.cockpit-table tbody tr:hover td {{
  background: color-mix(in srgb, var(--accent) 8%, var(--surface)) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
}}

/* Compact strip under the map (plan Fase 1): ramp + ticks + mode + provenance. */
/* Compact ramp row for the reading panel: label, gradient, range, scale mode. */
.legend-inline {{
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 0.6rem;
  padding: 0.35rem 0.7rem;
  min-height: 0;
}}

@media (max-width: 640px) {{
  .legend-inline {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
  }}
  .legend-inline-label {{
    grid-column: 1 / -1;
    white-space: normal;
  }}
  .legend-inline .legend-bar {{
    grid-column: 1 / -1;
    width: 100%;
    flex: none;
    order: 0;
  }}
  .legend-inline-range {{
    grid-column: 1;
    margin-left: 0;
    white-space: normal;
  }}
  .legend-flag {{
    grid-column: 2;
    justify-self: end;
  }}
}}

.legend-inline .legend-bar {{
  flex: 1 1 auto;
  min-width: 70px;
  height: 8px;
  margin: 0;
}}

.legend-inline-label,
.legend-inline-range {{
  font-family: var(--font-mono);
  font-size: var(--type-xs);
  letter-spacing: 0.03em;
  color: var(--muted) !important;
  white-space: nowrap;
  min-width: 0;
}}

.legend-inline-label {{
  text-transform: uppercase;
  overflow: visible;
  text-overflow: clip;
  max-width: none;
  flex: 0 0 auto;
}}

.legend-flag {{
  color: var(--alert) !important;
}}

.legend-details {{
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}}

.legend-details .legend-meta,
.legend-details .kpi-note {{
  margin: 0;
}}

.legend-meta {{
  margin: 0;
  font-family: var(--font-body);
  font-size: 0.74rem;
  color: var(--muted) !important;
  line-height: 1.45;
}}

/* Reading panel (right column). */
.reading-head .reading-title {{
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
  margin: 0;
  font-family: var(--font-mono);
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text) !important;
  word-break: break-all;
}}

.reading-stats {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.6rem;
  padding: 0.6rem var(--card-pad-x);
}}

.reading-stat {{
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}}

.reading-stat .stat-value {{
  font-family: var(--font-mono);
  font-size: 1rem;
  font-weight: 600;
  color: var(--text) !important;
  white-space: nowrap;
}}

.reading-stat .stat-unit {{
  font-size: 0.72rem;
  font-weight: 500;
  color: var(--muted) !important;
}}

.reading-stat .stat-label {{
  font-family: var(--font-body);
  font-size: 0.7rem;
  letter-spacing: 0.02em;
  color: var(--muted) !important;
  text-transform: none;
}}

.legend-bar {{
  height: 12px;
  width: 100%;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  box-sizing: border-box;
}}

.null-note {{
  margin: 0;
  padding: 0.65rem 0.95rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text) !important;
  font-family: var(--font-body);
  font-size: 0.86rem;
  line-height: 1.45;
  box-sizing: border-box;
}}

/* Streamlit 1.67 React Aria Selectbox & VirtualDropdown (Selectbox.7rsxE6LC.js) */
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] label,
[data-testid="stWidgetLabel"] span {{
  font-family: var(--font-mono) !important;
  font-size: 0.7rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted) !important;
  opacity: 1 !important;
}}

[data-testid="stSelectbox"] div:has(> input),
[data-testid="stSelectbox"] .equpb2c0,
[data-baseweb="select"] > div {{
  background-color: var(--bg) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  color: var(--text) !important;
}}

[data-testid="stSelectbox"] div:has(> input):focus-within,
[data-testid="stSelectbox"] .equpb2c0[data-focus-within] {{
  border-color: var(--accent) !important;
}}

[data-testid="stSelectbox"] input,
[data-testid="stSelectbox"] .equpb2c4 {{
  background-color: transparent !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
  caret-color: var(--text) !important;
  font-family: var(--font-mono) !important;
  font-size: 0.82rem !important;
  font-weight: 500 !important;
  opacity: 1 !important;
}}

[data-testid="stSelectbox"] input::placeholder {{
  color: var(--muted) !important;
  -webkit-text-fill-color: var(--muted) !important;
  opacity: 1 !important;
}}

[data-testid="stSelectbox"] button,
[data-testid="stSelectbox"] svg {{
  color: var(--text) !important;
  fill: var(--text) !important;
  background: transparent !important;
}}

/* Virtualized dropdown popup portal */
[data-testid="stSelectboxVirtualDropdown"],
[data-testid="stSelectboxVirtualDropdown"] [role="listbox"],
[data-testid="stSelectboxVirtualDropdown"] ul,
[data-baseweb="popover"],
[data-baseweb="menu"],
ul[data-baseweb="menu"] {{
  background-color: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  color: var(--text) !important;
  box-shadow: var(--shadow-menu) !important;
}}

[data-testid="stSelectboxVirtualDropdown"] [role="option"],
[data-testid="stSelectboxVirtualDropdown"] [data-item-hl],
[data-testid="stSelectboxVirtualDropdown"] li,
[data-baseweb="popover"] [role="option"],
ul[data-baseweb="menu"] li {{
  background-color: var(--surface) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
  font-family: var(--font-mono) !important;
  font-size: 0.82rem !important;
  font-weight: 500 !important;
  opacity: 1 !important;
}}

[data-testid="stSelectboxVirtualDropdown"] [role="option"] *,
[data-testid="stSelectboxVirtualDropdown"] [data-item-hl] * {{
  color: inherit !important;
  -webkit-text-fill-color: inherit !important;
}}

[data-testid="stSelectboxVirtualDropdown"] [role="option"][data-hovered] [data-item-hl],
[data-testid="stSelectboxVirtualDropdown"] [role="option"][data-focused] [data-item-hl],
[data-testid="stSelectboxVirtualDropdown"] [role="option"]:hover [data-item-hl],
[data-testid="stSelectboxVirtualDropdown"] [role="option"][aria-selected="true"] [data-item-hl],
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [aria-selected="true"] {{
  background-color: color-mix(in srgb, var(--accent) 16%, var(--surface)) !important;
  color: var(--accent) !important;
  -webkit-text-fill-color: var(--accent) !important;
  font-weight: 600 !important;
}}

/* Radio, Toggle, Slider, Captions, Markdown */
/* Toggle: the off track needs to read against the dark surface. */
[data-testid="stToggle"] [role="switch"],
[data-testid="stToggle"] [data-baseweb="checkbox"] [role="switch"] {{
  background-color: color-mix(in srgb, var(--muted) 42%, var(--surface)) !important;
  border: 1px solid var(--muted) !important;
  opacity: 1 !important;
}}

[data-testid="stToggle"] [role="switch"][aria-checked="true"],
[data-testid="stToggle"] [data-baseweb="checkbox"] [role="switch"][aria-checked="true"] {{
  background-color: var(--accent) !important;
  border-color: var(--accent) !important;
}}

[data-testid="stToggle"] [role="switch"] > div {{
  background-color: var(--text) !important;
  border: 1px solid var(--border) !important;
}}

/* Compact expander header so the filter strip does not push the map down. */
[data-testid="stExpander"] details {{
  border: 0 !important;
  background: transparent !important;
}}

[data-testid="stExpander"] summary {{
  padding: 0.3rem 0.1rem !important;
  font-family: var(--font-mono) !important;
  font-size: 0.72rem !important;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted) !important;
}}

[data-testid="stExpander"] summary p {{
  color: var(--muted) !important;
  font-size: 0.72rem !important;
}}

[data-testid="stRadio"] label,
[data-testid="stRadio"] label *,
[data-testid="stCheckbox"] label,
[data-testid="stCheckbox"] label *,
[data-testid="stToggle"] label,
[data-testid="stToggle"] label *,
[data-testid="stSlider"] *,
[data-testid="stSliderThumbValue"],
[data-testid="stSliderTickBar"],
[data-testid="stSliderTickBar"] *,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span {{
  color: var(--text) !important;
  opacity: 1 !important;
}}

[data-testid="stRadioOption"] > div > div:first-child {{
  border-color: var(--border) !important;
  background-color: var(--bg) !important;
}}

[data-testid="stRadioOption"][data-selected] > div > div:first-child {{
  border-color: var(--accent) !important;
  background-color: var(--accent) !important;
}}

[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] * {{
  color: var(--muted) !important;
  font-family: var(--font-mono) !important;
  font-size: 0.75rem !important;
  line-height: 1.4 !important;
  opacity: 1 !important;
}}

/* Plotly & Deck.gl tooltips */
[data-testid="stPlotlyChart"] {{
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  padding: 0.5rem 0.65rem !important;
  color: var(--text) !important;
  box-sizing: border-box;
  overflow: hidden;
}}

[data-testid="stPlotlyChart"] svg text {{
  fill: var(--text) !important;
}}

.deck-tooltip {{
  background: var(--surface) !important;
  color: var(--text) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  font-family: var(--font-mono) !important;
  font-size: 0.74rem !important;
  line-height: 1.45 !important;
  padding: 0.45rem 0.65rem !important;
  box-shadow: none !important;
}}

/* Tabs: force resting text color on inner <p>/<span> so it never hides until hover */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  display: flex;
  flex-wrap: nowrap;
  overflow-x: auto;
  gap: 0.5rem;
  padding: 0.15rem 0 0.55rem;
  border-bottom: 1px solid var(--border);
  background: transparent !important;
  scrollbar-width: thin;
}}

button[data-baseweb="tab"] {{
  flex: 0 0 auto;
  padding: 0.45rem 0.85rem !important;
  min-height: 2.25rem !important;
  background: var(--surface) !important;
  color: var(--text) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  font-family: var(--font-mono) !important;
  font-size: 0.76rem !important;
  letter-spacing: 0.04em;
  white-space: nowrap;
  opacity: 1 !important;
  transition: color var(--motion-fast) ease, background-color var(--motion-fast) ease,
    border-color var(--motion-fast) ease;
}}

button[data-baseweb="tab"] *,
button[data-baseweb="tab"] p,
button[data-baseweb="tab"] span {{
  color: var(--text) !important;
  font-family: var(--font-mono) !important;
  font-size: 0.76rem !important;
  opacity: 1 !important;
}}

button[data-baseweb="tab"]:hover {{
  border-color: var(--accent) !important;
  color: var(--accent) !important;
}}

button[data-baseweb="tab"]:hover *,
button[data-baseweb="tab"]:hover p,
button[data-baseweb="tab"]:hover span {{
  color: var(--accent) !important;
}}

button[data-baseweb="tab"][aria-selected="true"] {{
  background: color-mix(in srgb, var(--accent) 14%, var(--surface)) !important;
  color: var(--accent) !important;
  border-color: var(--accent) !important;
}}

button[data-baseweb="tab"][aria-selected="true"] *,
button[data-baseweb="tab"][aria-selected="true"] p,
button[data-baseweb="tab"][aria-selected="true"] span {{
  color: var(--accent) !important;
  font-weight: 600 !important;
}}

.stButton > button {{
  width: auto;
  background: color-mix(in srgb, var(--accent) 12%, var(--surface)) !important;
  color: var(--accent) !important;
  border: 1px solid var(--accent) !important;
  border-radius: var(--radius) !important;
  font-family: var(--font-mono) !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  transition: color var(--motion-fast) ease, background-color var(--motion-fast) ease,
    border-color var(--motion-fast) ease;
}}

.stButton > button * {{
  color: var(--accent) !important;
}}

.stButton > button:hover {{
  background: var(--accent) !important;
  color: var(--on-accent) !important;
  border-color: var(--accent) !important;
}}

.stButton > button:hover * {{
  color: var(--on-accent) !important;
}}

.stButton > button:focus-visible,
button[data-baseweb="tab"]:focus-visible {{
  outline: 1px solid var(--accent);
  outline-offset: 2px;
}}

/* Keep chrome above the map. Deck.gl canvases are position:absolute. */
.mission-bar,
.demo-banner,
.st-key-filters,
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  position: relative;
  z-index: 6;
}}

/* Responsive flex grid across all column blocks */
[data-testid="stHorizontalBlock"] {{
  display: flex !important;
  flex-wrap: wrap !important;
  align-items: stretch !important;
  gap: 0.75rem !important;
  width: 100% !important;
  min-width: 0;
}}

[data-testid="stColumn"],
[data-testid="column"] {{
  min-width: 13rem !important;
  flex: 1 1 13rem !important;
  max-width: 100%;
  box-sizing: border-box;
}}

.st-key-filters [data-testid="stHorizontalBlock"] {{
  align-items: center !important;
}}

.st-key-filters [data-testid="stColumn"],
.st-key-filters [data-testid="column"] {{
  min-width: 10.5rem !important;
  flex: 1 1 10.5rem !important;
}}

.st-key-filter-actions [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
.st-key-filter-actions [data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child {{
  flex: 0 0 12rem !important;
  min-width: 12rem !important;
}}

.st-key-filter-actions [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
.st-key-filter-actions [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child {{
  flex: 0 0 10rem !important;
  min-width: 10rem !important;
  margin-left: auto;
}}

.st-key-filter-actions [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:not(:first-child):not(:last-child),
.st-key-filter-actions [data-testid="stHorizontalBlock"] > [data-testid="column"]:not(:first-child):not(:last-child) {{
  flex: 1 1 8rem !important;
  min-width: 8rem !important;
}}

.st-key-filters [data-testid="stHorizontalBlock"]:has([data-testid="stPopover"]) {{
  margin-top: -4px;
}}

.st-key-filters [data-testid="stPopover"] > button,
.st-key-filters .stButton > button {{
  min-height: 28px !important;
  padding: 2px 8px !important;
  font-size: var(--type-xs) !important;
  line-height: 1.2 !important;
}}

.st-key-filters button[aria-label^="Help for"] {{
  color: var(--muted) !important;
  background: transparent !important;
}}

.st-key-filters button[aria-label^="Help for"] svg,
.st-key-filters button[aria-label^="Help for"] svg * {{
  color: var(--muted) !important;
  stroke: var(--muted) !important;
  fill: none !important;
}}

/* Plan Fase 1: the map row is 2:1, not the flat equal split of the global rule. */
.st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
.st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child {{
  flex: 2 1 0 !important;
  min-width: 0 !important;
}}

.st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
.st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child {{
  flex: 1 1 0 !important;
  min-width: 0 !important;
}}

.st-key-map-slot {{
  position: relative;
  z-index: 0;
  isolation: isolate;
  width: 100%;
  max-width: 100%;
  height: var(--map-height) !important;
  min-height: var(--map-height) !important;
  max-height: var(--map-height) !important;
  flex: 0 0 auto !important;
  overflow: hidden !important;
  padding: 0 !important;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-sizing: border-box;
  contain: layout paint;
}}

.st-key-map-slot [data-testid="stVerticalBlock"],
.st-key-map-slot [data-testid="stElementContainer"],
.st-key-map-slot [data-testid="stDeckGlJsonChart"] {{
  position: relative !important;
  width: 100% !important;
  max-width: 100% !important;
  height: var(--map-height) !important;
  max-height: var(--map-height) !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  gap: 0 !important;
}}

.st-key-map-slot #deckgl-wrapper,
.st-key-map-slot .maplibregl-map,
.st-key-map-slot .mapboxgl-map {{
  position: absolute !important;
  inset: 0 !important;
  width: 100% !important;
  height: 100% !important;
  max-height: var(--map-height) !important;
  overflow: hidden !important;
}}

.st-key-map-slot canvas {{
  max-width: 100% !important;
  max-height: var(--map-height) !important;
}}

/* KPI cards in one row must share a height: stretch the column chain and let
   the card grow, so a two-line note no longer leaves a ragged bottom. */
[data-testid="stHorizontalBlock"]:has(.kpi-value) {{
  align-items: stretch !important;
}}

[data-testid="stHorizontalBlock"]:has(.kpi-value) > [data-testid="stColumn"],
[data-testid="stHorizontalBlock"]:has(.kpi-value) > [data-testid="stColumn"] > div,
[data-testid="stHorizontalBlock"]:has(.kpi-value) > [data-testid="stColumn"] [data-testid="stVerticalBlock"],
[data-testid="stHorizontalBlock"]:has(.kpi-value) > [data-testid="stColumn"] [data-testid="stElementContainer"],
[data-testid="stHorizontalBlock"]:has(.kpi-value) > [data-testid="stColumn"] [data-testid="stMarkdown"],
[data-testid="stHorizontalBlock"]:has(.kpi-value) > [data-testid="stColumn"] [data-testid="stMarkdown"] > div,
[data-testid="stHorizontalBlock"]:has(.kpi-value) > [data-testid="stColumn"] [data-testid="stMarkdownContainer"] {{
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  height: 100%;
  min-height: 0;
}}

[data-testid="stHorizontalBlock"]:has(.kpi-value) [data-testid="stMarkdownContainer"] {{
  margin-bottom: 0 !important;
}}

/* Six-up rows (the case stats) are single-line notes: a shorter equal card keeps
   tab 4 under 1.5 screens without breaking the 5/3-up rows. */
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(6)) .cockpit-card:has(.kpi-value) {{
  min-height: 96px;
}}

.cockpit-card:has(.kpi-value) {{
  flex: 1 1 auto;
  /* Above the tallest note in the three tabs (2-line notes reach 112 px, the
     layer-count list 121 px), so every card in a row ends at the same edge. */
  min-height: 124px;
  justify-content: space-between;
}}

.compare-card {{
  gap: 0.5rem;
}}

.compare-row {{
  display: grid;
  grid-template-columns: minmax(6.5rem, 8.5rem) minmax(5rem, 1fr) minmax(11rem, auto);
  align-items: center;
  gap: 0.75rem;
  padding: 0.2rem 0;
  font-family: var(--font-mono);
  font-size: 0.74rem;
  color: var(--text) !important;
  min-width: 0;
}}

.compare-case {{
  color: var(--text) !important;
  font-weight: 600;
}}

.compare-meta {{
  color: var(--muted) !important;
  text-align: right;
  overflow-wrap: anywhere;
}}

.compare-track {{
  width: 100%;
  min-width: 0;
  height: 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}}

.compare-fill {{
  height: 100%;
  background: var(--accent);
}}

[data-baseweb="tab-panel"][aria-hidden="true"],
[data-baseweb="tab-panel"][hidden] {{
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
  overflow: hidden !important;
  pointer-events: none !important;
}}

[data-baseweb="tab-panel"][aria-hidden="true"] canvas,
[data-baseweb="tab-panel"][hidden] canvas {{
  display: none !important;
}}

.stApp {{
  scrollbar-color: var(--border) var(--bg);
}}

::selection {{
  background: color-mix(in srgb, var(--accent) 28%, transparent);
  color: var(--text);
}}

/* Tablet landscape / small laptop */
@media (max-width: 1180px) {{
  .st-key-filters [data-testid="stColumn"],
  .st-key-filters [data-testid="column"] {{
    flex: 1 1 calc(33.333% - 0.75rem) !important;
    min-width: 11.5rem !important;
  }}
}}

/* Phone: the header must not eat the first screen */
@media (max-width: 768px) {{
  .mission-bar {{
    padding: 0.1rem 0 0.45rem;
    gap: 0.25rem;
  }}
  .mission-bar .mission-title,
  .mission-bar .mission-title span {{
    font-size: clamp(1.05rem, 4.2vw, 1.25rem) !important;
    line-height: 1.2 !important;
  }}
  .mission-bar .telemetry,
  .telemetry {{
    font-size: 0.7rem;
    line-height: 1.3;
  }}
}}

/* Tablet portrait: stack map and inspector full width */
@media (max-width: 960px) {{
  :root {{
    --map-height: clamp(360px, 52vh, 520px);
  }}
  [data-testid="stHorizontalBlock"]:has(.st-key-map-slot) > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"]:has(.st-key-map-slot) > [data-testid="column"] {{
    flex: 1 1 100% !important;
    min-width: 100% !important;
    width: 100% !important;
  }}
  /* The 2:1 map row must stack too. The 2:1 rule wins on specificity because of
     :first-child, so the stacking override needs the same pseudo-classes. */
  .st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
  .st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
  .st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child,
  .st-key-map-grid [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child {{
    flex: 1 1 100% !important;
    min-width: 100% !important;
    width: 100% !important;
  }}
  .compare-row {{
    grid-template-columns: minmax(6rem, auto) 1fr;
  }}
  .compare-meta {{
    grid-column: 1 / -1;
    text-align: left;
  }}
}}

/* Mobile phones */
@media (max-width: 640px) {{
  :root {{
    --card-pad-y: 0.75rem;
    --card-pad-x: 0.85rem;
    --map-height: clamp(300px, 46vh, 420px);
  }}
  .block-container {{
    padding-top: 2rem !important;
    padding-left: 0.65rem !important;
    padding-right: 0.65rem !important;
  }}
  [data-testid="stColumn"],
  [data-testid="column"],
  .st-key-filters [data-testid="stColumn"],
  .st-key-filters [data-testid="column"] {{
    flex: 1 1 100% !important;
    min-width: 100% !important;
    width: 100% !important;
  }}
  .mission-bar {{
    padding: 0.75rem 0.85rem !important;
  }}
  .compare-row {{
    grid-template-columns: 1fr;
    gap: 0.25rem;
  }}
}}

/* Keyboard focus must be visible on every control (plan Fase 3). */
a:focus-visible,
button:focus-visible,
summary:focus-visible,
input:focus-visible,
[role="tab"]:focus-visible,
[role="switch"]:focus-visible,
[data-baseweb="select"] [role="combobox"]:focus-visible {{
  outline: 2px solid var(--accent) !important;
  outline-offset: 2px !important;
}}

/* One animation only: the hero figure fades in when the selection changes. */
@keyframes cockpit-plot-in {{
  from {{ opacity: 0.35; }}
  to {{ opacity: 1; }}
}}

.js-plotly-plot {{
  animation: cockpit-plot-in var(--motion-fast) ease-out both;
}}

.kpi-unit {{
  color: var(--muted);
  font: 500 var(--type-xs) var(--font-mono);
  margin-left: var(--space-1);
}}

.status-chip {{
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px var(--space-2);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
  font: 600 var(--type-xs) var(--font-mono);
  line-height: 1.2;
}}

.status-coherence,
.status-quality {{
  border-color: var(--coherence);
  color: var(--coherence);
}}

.status-moderate {{
  border-color: var(--alert);
  color: var(--alert);
}}

.status-critical {{
  border-color: var(--coral);
  color: var(--coral);
}}

.empty-icon {{
  display: grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  flex: 0 0 2rem;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--accent);
  font: 600 var(--type-lg) var(--font-mono);
}}

.cockpit-card.is-empty {{
  flex-direction: row;
  align-items: center;
  justify-content: flex-start;
}}

.cockpit-skeleton {{
  gap: var(--space-2);
}}

.skeleton-bar {{
  display: block;
  height: 12px;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--muted) 18%, var(--surface));
}}

.skeleton-bar:nth-of-type(2) {{ width: 76%; }}
.skeleton-bar:nth-of-type(3) {{ width: 58%; }}

.sr-only {{
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}}

@media (prefers-reduced-motion: reduce) {{
  *,
  *::before,
  *::after {{
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }}
}}
"""


def inject_css() -> None:
    """Inject cockpit CSS. Call once per run, after set_page_config."""
    if st.session_state.get("ui_theme_mode") not in ("system", "light", "dark"):
        st.session_state.ui_theme_mode = "system"
    compact = "\n".join(line for line in _CSS.splitlines() if line.strip())
    st.markdown(f"<style>\n{compact}\n</style>", unsafe_allow_html=True)
    chosen = st.session_state.get("ui_theme_mode")
    if chosen in ("light", "dark"):
        st.markdown(f'<div data-cockpit="{chosen}" hidden></div>', unsafe_allow_html=True)
