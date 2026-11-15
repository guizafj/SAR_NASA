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
  --radius: {RADIUS};
  --card-pad-y: 0.85rem;
  --card-pad-x: 1.05rem;
  --gap: 0.75rem;
  --map-height: clamp(380px, calc(100vh - 430px), 640px);
  --font-display: {FONT_DISPLAY};
  --font-body: {FONT_BODY};
  --font-mono: {FONT_MONO};
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
  padding-top: 0.75rem !important;
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
  gap: 0.35rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.85rem 1.1rem;
  margin: 0;
  box-sizing: border-box;
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
  padding: 0.55rem 1.1rem;
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

.cockpit-card .kpi-source {{
  font-family: var(--font-mono);
  font-size: 0.72rem;
  line-height: 1.4;
  color: var(--muted) !important;
  margin: 0;
  margin-top: auto;
  padding-top: 0.15rem;
  overflow-wrap: anywhere;
  word-break: break-word;
  white-space: normal;
  min-width: 0;
  max-width: 100%;
}}

.cockpit-card .kpi-note {{
  font-family: var(--font-body);
  font-size: 0.76rem;
  line-height: 1.4;
  color: var(--muted) !important;
  margin: 0;
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
  max-height: 28rem;
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

.cockpit-table tbody tr:hover td {{
  background: color-mix(in srgb, var(--accent) 8%, var(--surface)) !important;
  color: var(--text) !important;
  -webkit-text-fill-color: var(--text) !important;
}}

.legend {{
  gap: 0.5rem;
}}

.legend-bar {{
  height: 12px;
  width: 100%;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  box-sizing: border-box;
}}

.legend-ticks {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  font-family: var(--font-mono);
  font-size: 0.74rem;
  font-weight: 500;
  color: var(--text) !important;
}}

.legend-ticks span {{
  color: var(--text) !important;
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
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.14) !important;
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
.st-key-filter-strip,
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  position: relative;
  z-index: 6;
}}

.st-key-filter-strip {{
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  padding: 0.85rem 1.05rem !important;
  box-sizing: border-box;
}}

.st-key-filter-strip > div {{
  border: 0 !important;
  padding: 0 !important;
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

.st-key-filter-strip [data-testid="stHorizontalBlock"] {{
  align-items: center !important;
}}

.st-key-filter-strip [data-testid="stColumn"],
.st-key-filter-strip [data-testid="column"] {{
  min-width: 10.5rem !important;
  flex: 1 1 10.5rem !important;
}}

.st-key-map-slot {{
  position: relative;
  z-index: 0;
  isolation: isolate;
  width: 100%;
  max-width: 100%;
  height: var(--map-height) !important;
  max-height: var(--map-height) !important;
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

.st-key-inspector-slot {{
  position: relative;
  z-index: 2;
  min-width: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--card-pad-y) var(--card-pad-x);
  box-sizing: border-box;
  overflow-x: hidden;
}}

.st-key-inspector-slot .cockpit-card {{
  border: 0;
  padding: 0;
  background: transparent;
}}

.st-key-inspector-slot .cockpit-card.is-empty {{
  min-height: calc(var(--map-height) - 5.5rem);
  justify-content: center;
}}

.st-key-inspector-slot [data-testid="stPlotlyChart"] {{
  padding: 0.35rem !important;
  background: var(--bg) !important;
}}

.st-key-map-meta {{
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  margin-top: 0.15rem;
  overflow: visible;
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
  .st-key-filter-strip [data-testid="stColumn"],
  .st-key-filter-strip [data-testid="column"] {{
    flex: 1 1 calc(33.333% - 0.75rem) !important;
    min-width: 11.5rem !important;
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
  .st-key-inspector-slot .cockpit-card.is-empty {{
    min-height: 7rem;
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
    padding-top: 0.5rem !important;
    padding-left: 0.65rem !important;
    padding-right: 0.65rem !important;
  }}
  [data-testid="stColumn"],
  [data-testid="column"],
  .st-key-filter-strip [data-testid="stColumn"],
  .st-key-filter-strip [data-testid="column"] {{
    flex: 1 1 100% !important;
    min-width: 100% !important;
    width: 100% !important;
  }}
  .mission-bar,
  .st-key-filter-strip {{
    padding: 0.75rem 0.85rem !important;
  }}
  .compare-row {{
    grid-template-columns: 1fr;
    gap: 0.25rem;
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
