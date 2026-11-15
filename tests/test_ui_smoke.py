"""Smoke test: the shell starts on the in-memory mock and renders four tabs."""

from __future__ import annotations

from pathlib import Path

import re

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
    assert "Inelastic" in text
    assert "SAR physics and regime classification" in text
    assert "Declared role:" in text
    assert "Zócalo Metro Station (synthetic)" in text
    captions = "\n".join(getattr(node, "value", "") for node in getattr(app, "caption", []))
    assert "Linear trend" in captions
    assert "Provenance ID" in captions
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
    from src.data import loaders

    valid_cell = str(loaders.get_map_layer_df("jakarta", {})["cell_id"].iloc[0])
    app = AppTest.from_file(APP, default_timeout=60)
    app.query_params["case"] = "jakarta"
    app.query_params["risk"] = "critico"
    app.query_params["extrude"] = "1"
    app.query_params["scale"] = "shared"
    app.query_params["cell"] = valid_cell
    app.run()

    assert not app.exception
    assert app.session_state["filter_case_id"] == "jakarta"
    assert app.session_state["filter_risk_level"] == "critico"
    assert app.session_state["filter_extrude_3d"] is True
    assert app.session_state["scale_mode"] == "shared"
    assert app.session_state["selected_cell_id"] == valid_cell

    qp = app.query_params
    assert qp["case"] == "jakarta"
    assert qp["risk"] == "critico"
    assert qp["extrude"] == "1"
    assert qp["scale"] == "shared"
    assert qp["cell"] == valid_cell


def test_unknown_url_values_are_ignored_and_replaced() -> None:
    app = AppTest.from_file(APP, default_timeout=60)
    app.query_params["case"] = "atlantis"
    app.query_params["coh"] = "7"
    app.query_params["theme"] = "sepia"
    app.query_params["cell"] = "not-a-valid-h3-cell"
    app.run()

    assert not app.exception
    assert app.session_state["filter_case_id"] == CASE_IDS[0]
    assert app.session_state["ui_theme_mode"] == "system"
    assert app.session_state["selected_cell_id"] is None
    # The slider keeps the declared floor and the URL is rewritten to the live state.
    assert 0.0 <= float(app.query_params["coh"]) <= 1.0
    assert app.query_params["case"] == CASE_IDS[0]
    assert "cell" not in app.query_params


def test_tab4_reader_plus_technical_equals_loader_total() -> None:
    from src.data import loaders
    from src.ui.educational import split_caveat, split_technical

    limits = loaders.load_documented_limits()
    items = limits["section_13"]
    visible, technical = split_technical(items)
    assert len(visible) + len(technical) == len(items)
    assert visible and technical, "the fixture must exercise both buckets"
    for text in technical:
        assert len(text) > 220 or "`" in text or re.search(r"O-\d\d", text)

    caveat = limits["section_8_caveat"]
    lead, rest = split_caveat(caveat)
    assert lead and rest
    assert lead.count(". ") < 2
    assert caveat.startswith(lead.rstrip(".")) and caveat.endswith(rest)

    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception
    text = "\n".join(node.value for node in app.markdown)
    n_visible = text.count('class="limit-line"')
    n_tech_items = text.count('class="tech-line item"')
    n_tech_caveat = text.count('class="tech-line caveat"')
    assert n_visible == len(visible)
    assert n_tech_items == len(technical)
    assert n_tech_caveat == 1
    labels = [exp.label for exp in app.expander]
    assert f"Technical notes ({n_tech_items + n_tech_caveat})" in labels


def test_every_figure_declares_a_unit_on_the_y_axis() -> None:
    """Fase 2 (dataviz): no figure ships with a bare 'value' axis."""
    from src.data import loaders
    from src.ui.charts import bars_figure, line_figure, three_cities_figure

    series = loaders.get_case_series("malaga", {})
    bars = loaders.get_land_use_bars("malaga", {})
    meta = loaders.load_meta("malaga")
    frame = loaders.load_timeseries("malaga")
    rows = frame.to_dict(orient="records")

    figures = {
        "line": line_figure(series),
        "three-cities": three_cities_figure({"malaga": rows}, {"malaga": "Málaga, ES"}),
        "bars": bars_figure(bars, meta),
    }
    for name, figure in figures.items():
        y_title = str(figure.layout.yaxis.title.text or "")
        assert "mm" in y_title, (name, y_title)
    # Lines use unified hover; bars use per-mark hover (dataviz skill).
    assert figures["line"].layout.hovermode == "x unified"
    assert figures["three-cities"].layout.hovermode == "x unified"
    assert figures["bars"].layout.hovermode in (None, "closest")

    # The fitted seasonal term is drawn, never invented when absent.
    assert any(trace.name == "Seasonal fit" for trace in figures["line"].data)
    # Malaga declares a detection limit; the bars must show it as a reference.
    annotations = " ".join(str(a.text) for a in figures["bars"].layout.annotations)
    assert "Detection limit" in annotations
    # One Y axis only, in every figure.
    for name, figure in figures.items():
        assert not any(key.startswith("yaxis") and key != "yaxis" for key in figure.layout), name


def test_compact_query_param_closes_the_filter_strip() -> None:
    compact = AppTest.from_file(APP, default_timeout=60)
    compact.query_params["compact"] = "1"
    compact.run()
    filters = next(e for e in compact.expander if e.label == "Filters")
    assert filters.proto.expanded is False

    normal = AppTest.from_file(APP, default_timeout=60)
    normal.run()
    assert next(e for e in normal.expander if e.label == "Filters").proto.expanded is True


def test_clear_selection_resets_cell_and_url() -> None:
    from src.data import loaders

    cell = str(loaders.get_map_layer_df(CASE_IDS[0], {})["cell_id"].iloc[0])
    app = AppTest.from_file(APP, default_timeout=60)
    app.query_params["cell"] = cell
    app.run()
    assert app.session_state["selected_cell_id"] == cell

    clear = next(button for button in app.button if button.label == "Clear selection")
    clear.click().run()
    assert app.session_state["selected_cell_id"] is None
    assert "cell" not in app.query_params


def test_number_format_is_half_up_and_trimmed() -> None:
    from src.ui.formatters import format_number

    assert format_number(-62.065) == "-62.07"
    assert format_number(-62.065, 2) != "-62.06"
    assert format_number(41.3767) == "41.38"
    assert format_number(11694.0) == "11694"
    assert format_number(0.0) == "0"
    assert format_number(None) == "None"


def test_median_velocity_is_one_figure_across_tabs() -> None:
    """P0-3: the same magnitude must not read -62.07 in one tab and -62.06 in another."""
    from src.data import loaders
    from src.ui.formatters import format_number

    expected = format_number(loaders.load_meta(CASE_IDS[0])["stats"]["median_velocity_mm_yr"])
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    text = "\n".join(node.value for node in app.markdown)
    assert text.count(expected) >= 2, (expected, text.count(expected))
    # The raw artefact record may still print -62.065 verbatim; no card may print -62.06.
    assert "-62.06 mm" not in text
    assert ">-62.06<" not in text


def test_tab4_cites_real_nisar_availability() -> None:
    """The demo must prove the real catalogue was searched, without claiming a download."""
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception
    text = "\n".join(node.value for node in app.markdown)
    assert "Real NISAR GUNW availability" in text
    assert "Recommended stack" in text
    assert "Nothing was downloaded for this demo" in text
    assert "granules catalogued" in text


def test_reading_panel_separates_series_scope_from_grid_statistics() -> None:
    """Empty state: the AOI series and the grid medians must not read as one scope."""
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception
    text = "\n".join(node.value for node in app.markdown)
    captions = "\n".join(getattr(node, "value", "") for node in getattr(app, "caption", []))
    blob = text + "\n" + captions
    assert "No cell selected" in blob
    assert "Whole-area aggregate series" in captions
    assert "Grid statistics for the current filter" in captions
    # One provenance line per block: the standalone card is gone in the empty state.
    assert '<p class="kicker">Provenance</p>' not in text
