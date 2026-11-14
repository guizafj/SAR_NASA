"""Palette, type and the only CSS injection point. No data and no science."""

from __future__ import annotations

import streamlit as st

BG = "#0B0F19"
SURFACE = "#0F172A"
BORDER = "#1E293B"
ACCENT = "#06B6D4"
COHERENCE = "#10B981"
ALERT = "#F59E0B"
CORAL = "#F43F5E"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
FOCUS = "#141F36"
RADIUS = "4px"

FONT_DISPLAY = '"Space Grotesk", "Segoe UI", system-ui, sans-serif'
FONT_BODY = '"Inter", "Segoe UI", system-ui, sans-serif'
FONT_MONO = '"JetBrains Mono", "DejaVu Sans Mono", ui-monospace, monospace'

# Stitch paints the active tab as a solid cyan fill. DESIGN.md forbids a
# full-width solid primary button, so the active tab keeps a dark fill and a
# cyan border. Shadows from the HTML reference are not copied.
_CSS = f"""
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap");

:root {{
  --bg: {BG};
  --surface: {SURFACE};
  --border: {BORDER};
  --accent: {ACCENT};
  --coherence: {COHERENCE};
  --alert: {ALERT};
  --coral: {CORAL};
  --text: {TEXT};
  --muted: {MUTED};
  --focus: {FOCUS};
  --radius: {RADIUS};
  --font-display: {FONT_DISPLAY};
  --font-body: {FONT_BODY};
  --font-mono: {FONT_MONO};
}}

.stApp, [data-testid="stAppViewContainer"] {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
}}

[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {{
  display: none !important;
}}

.block-container {{
  padding-top: 0.6rem;
  padding-bottom: 0.4rem;
  max-width: 1440px;
}}

.mission-bar {{
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  border-bottom: 1px solid var(--border);
  padding: 0.15rem 0 0.85rem;
  margin: 0 0 0.75rem;
}}

/* Not an <h1>: Streamlit wraps markdown headings in a clipped span (~36px). */
.mission-bar .mission-title,
.mission-bar .mission-title span {{
  font-family: var(--font-display) !important;
  font-size: clamp(1.35rem, 2.1vw, 1.75rem) !important;
  font-weight: 600 !important;
  letter-spacing: -0.02em;
  line-height: 1.35 !important;
  color: var(--text) !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: visible !important;
  white-space: normal !important;
  height: auto !important;
  max-height: none !important;
  max-width: 52rem;
}}

.mission-bar .telemetry {{
  margin: 0;
}}

.telemetry {{
  font-family: var(--font-mono);
  font-size: 0.8rem;
  letter-spacing: 0.04em;
  color: var(--muted);
  margin: 0.4rem 0 0;
  line-height: 1.45;
  overflow: visible;
  word-break: break-word;
  white-space: normal;
}}

.st-key-map-meta .telemetry {{
  margin-top: 0.35rem;
}}

.demo-banner {{
  margin: 0.75rem 0 0.25rem;
  padding: 0.45rem 0.75rem;
  border: 1px solid var(--alert);
  border-radius: var(--radius);
  background: rgba(245, 158, 11, 0.12);
  color: var(--alert);
  font-family: var(--font-mono);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.08em;
}}

.cockpit-card,
.viewport {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.75rem 1rem;
  box-shadow: none;
  min-width: 0;
  box-sizing: border-box;
}}

.cockpit-card {{
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  overflow: hidden;
}}

.cockpit-card.is-empty {{
  min-height: 7.5rem;
  justify-content: center;
  gap: 0.5rem;
}}

.viewport {{
  min-height: 420px;
}}

.kicker {{
  font-family: var(--font-mono);
  font-size: 0.68rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  margin: 0;
}}

.cockpit-card p,
.viewport p {{
  font-family: var(--font-body);
  color: var(--muted);
  margin: 0;
  line-height: 1.4;
}}

.cockpit-card .empty-prompt {{
  font-family: var(--font-body);
  font-size: 0.92rem;
  line-height: 1.45;
  color: var(--muted);
  margin: 0;
}}

.cockpit-card .kpi-value {{
  font-family: var(--font-mono);
  font-size: 1.15rem;
  font-weight: 500;
  line-height: 1.3;
  color: var(--text);
  margin: 0;
  word-break: break-word;
}}

.cockpit-card .kpi-source {{
  font-family: var(--font-mono);
  font-size: 0.68rem;
  line-height: 1.35;
  color: var(--muted);
  margin: 0;
  margin-top: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  max-width: 100%;
}}

.cockpit-card .kpi-note {{
  font-family: var(--font-body);
  font-size: 0.72rem;
  line-height: 1.35;
  color: var(--muted);
  margin: 0;
}}

.cockpit-card .detail-line {{
  font-family: var(--font-mono);
  font-size: 0.72rem;
  line-height: 1.45;
  color: var(--muted);
  margin: 0;
  word-break: break-word;
  overflow-wrap: anywhere;
}}

.cockpit-card .chip {{
  align-self: flex-start;
  margin: 0.1rem 0 0.25rem;
}}

.chip {{
  display: inline-block;
  height: 20px;
  padding: 2px 6px;
  border-radius: 2px;
  border: 1px solid rgba(16, 185, 129, 0.3);
  background: rgba(16, 185, 129, 0.12);
  color: var(--coherence);
  font-family: var(--font-mono);
  font-size: 0.68rem;
  line-height: 14px;
}}

.chip-alert {{
  border-color: rgba(245, 158, 11, 0.45);
  background: rgba(245, 158, 11, 0.12);
  color: var(--alert);
}}

.chip-coral {{
  border-color: rgba(244, 63, 94, 0.45);
  background: rgba(244, 63, 94, 0.12);
  color: var(--coral);
}}

.chip-muted {{
  border-color: var(--border);
  background: transparent;
  color: var(--muted);
}}

.legend-bar {{
  height: 12px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
}}

.legend-ticks {{
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 0.68rem;
  color: var(--muted);
}}

.null-note {{
  margin: 0.5rem 0 0.65rem;
  padding: 0.45rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--muted);
  font-family: var(--font-body);
  font-size: 0.85rem;
  line-height: 1.4;
}}

button[data-baseweb="tab"] {{
  background: var(--bg);
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  letter-spacing: 0.04em;
}}

button[data-baseweb="tab"][aria-selected="true"] {{
  background: rgba(6, 182, 212, 0.12);
  color: var(--accent);
  border-color: var(--accent);
}}

.stButton > button {{
  width: auto;
  background: rgba(6, 182, 212, 0.12);
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}

.stButton > button:hover {{
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
}}

[data-testid="stWidgetLabel"] p {{
  font-family: var(--font-mono);
  font-size: 0.68rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}}

/* Keep chrome above the map. Deck.gl canvases are position:absolute and
   otherwise paint over filters, tabs and the inspector after a selection. */
.mission-bar,
.demo-banner,
.st-key-filter-strip,
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  position: relative;
  z-index: 6;
  background: var(--bg);
}}

.st-key-filter-strip {{
  background: var(--surface);
}}

[data-testid="stHorizontalBlock"] {{
  align-items: flex-start;
  width: 100%;
  min-width: 0;
}}

[data-testid="stColumn"],
[data-testid="column"] {{
  min-width: 0 !important;
  max-width: 100%;
  overflow: hidden;
}}

.st-key-map-slot {{
  position: relative;
  z-index: 0;
  isolation: isolate;
  width: 100%;
  max-width: 100%;
  height: 400px !important;
  max-height: 400px !important;
  overflow: hidden !important;
  padding: 0 !important;
  contain: layout paint;
}}

.st-key-map-slot [data-testid="stVerticalBlock"],
.st-key-map-slot [data-testid="stElementContainer"],
.st-key-map-slot [data-testid="stDeckGlJsonChart"] {{
  position: relative !important;
  width: 100% !important;
  max-width: 100% !important;
  height: 400px !important;
  max-height: 400px !important;
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
  max-height: 400px !important;
  overflow: hidden !important;
}}

.st-key-map-slot canvas {{
  max-width: 100% !important;
  max-height: 400px !important;
}}

.st-key-inspector-slot {{
  position: relative;
  z-index: 2;
  min-width: 0;
  overflow-x: hidden;
}}

.st-key-inspector-slot .cockpit-card.is-empty {{
  min-height: 400px;
  justify-content: center;
}}

.st-key-map-meta {{
  position: relative;
  z-index: 2;
  margin-top: 0.5rem;
  background: var(--bg);
  overflow: visible;
}}

.compare-row {{
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.2rem 0;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: var(--muted);
  min-width: 0;
}}

.compare-row > span:first-child {{
  flex: 0 0 auto;
}}

.compare-row > span:last-child {{
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}

.compare-track {{
  flex: 1 1 auto;
  min-width: 0;
  height: 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
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
"""


def inject_css() -> None:
    """Inject cockpit CSS. Call once per run, after set_page_config."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
