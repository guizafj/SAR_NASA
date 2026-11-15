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


def test_url_params_seed_the_session_and_mirror_back() -> None:
    app = AppTest.from_file(APP, default_timeout=60)
    app.query_params["case"] = "jakarta"
    app.query_params["risk"] = "critico"
    app.query_params["extrude"] = "1"
    app.query_params["scale"] = "shared"
    app.run()

    assert not app.exception
    assert app.session_state["filter_case_id"] == "jakarta"
    assert app.session_state["filter_risk_level"] == "critico"
    assert app.session_state["filter_extrude_3d"] is True
    assert app.session_state["scale_mode"] == "shared"

    qp = app.query_params
    assert qp["case"] == "jakarta"
    assert qp["risk"] == "critico"
    assert qp["extrude"] == "1"
    assert qp["scale"] == "shared"


def test_unknown_url_values_are_ignored_and_replaced() -> None:
    app = AppTest.from_file(APP, default_timeout=60)
    app.query_params["case"] = "atlantis"
    app.query_params["coh"] = "7"
    app.query_params["theme"] = "sepia"
    app.run()

    assert not app.exception
    assert app.session_state["filter_case_id"] == CASE_IDS[0]
    assert app.session_state["ui_theme_mode"] == "system"
    # The slider keeps the declared floor and the URL is rewritten to the live state.
    assert 0.0 <= float(app.query_params["coh"]) <= 1.0
    assert app.query_params["case"] == CASE_IDS[0]
