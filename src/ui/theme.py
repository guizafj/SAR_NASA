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
  padding-top: 1.25rem;
  max-width: 1440px;
}}

.mission-bar {{
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.75rem;
  margin-bottom: 0.75rem;
}}

.mission-bar h1 {{
  font-family: var(--font-display);
  font-size: 1.75rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--text);
  margin: 0;
}}

.telemetry {{
  font-family: var(--font-mono);
  font-size: 0.8rem;
  letter-spacing: 0.04em;
  color: var(--muted);
  margin: 0.4rem 0 0;
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
  margin: 0 0 0.35rem;
}}

.cockpit-card p,
.viewport p {{
  font-family: var(--font-body);
  color: var(--muted);
  margin: 0;
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
"""


def inject_css() -> None:
    """Inject cockpit CSS. Call once per run, after set_page_config."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
