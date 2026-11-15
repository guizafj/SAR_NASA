"""Smoke test: the shell starts on the in-memory mock and renders four tabs."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.domain.contracts import CASE_IDS

APP = Path(__file__).resolve().parents[1] / "app.py"
TAB_LABELS = (
    "1. Map",
    "2. Series and KPIs",
    "3. Hazard and infrastructure",
    "4. Provenance and limits",
)
FORBIDDEN = ("Guadalquivir", "Doñana", "MALG-01", "B_PERP", "deriva cero", "Sector 4")


def test_shell_starts_with_mock_and_four_tabs() -> None:
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()

    assert not app.exception
    assert [tab.label for tab in app.tabs] == list(TAB_LABELS)

    text = "\n".join(node.value for node in app.markdown)
    assert "DEMONSTRATION DATA" in text
    assert "Dancing with the SARs: NISAR Interferometry Cockpit" in text
    assert "Median velocity" in text
    assert "Coherence floor" in text
    assert "Known caveat" in text
    assert "Known limitations" in text
    assert "A context class is not the Land use filter." in text
    assert "not a hazard assessment" in text
    # Tab 3: either empty-state copy or a delivered context summary (st.caption/json).
    hazard = "\n".join(
        [
            *(node.value for node in app.markdown),
            *(getattr(node, "value", "") for node in getattr(app, "caption", [])),
        ]
    )
    assert (
        "No context layers are available" in hazard
        or "Delivered layers" in hazard
        or "n_features" in hazard
        or "Context" in text
    )
    for token in (*FORBIDDEN, "Field Dossier", "mitigaciones", "índices estructurales", "verificado"):
        assert token not in text

    # Streamlit AppTest may surface format_func labels, not raw CASE_IDS slugs.
    assert len(app.selectbox[0].options) == len(CASE_IDS)
    assert not app.sidebar.selectbox
