"""Tests verifying `src.data.loaders` satisfies all 18 Rol 2 cockpit hooks (`docs/ui-data-needs.md`)."""

from __future__ import annotations

from src.data import loaders
from src.domain import contracts as C

REQUIRED_BY_APP_PY = (
    "load_study_cases",
    "load_meta",
    "load_land_use_options",
    "load_risk_levels",
    "get_map_layer_df",
    "get_view_state",
    "get_cell_detail",
    "load_timeseries",
    # Tab 1..4 optional/extended hooks
    "get_color_scale",
    "get_layer_counts",
    "get_signal_status",
    "get_map_kpis",
    "get_series_kpis",
    "get_case_series",
    "get_land_use_bars",
    "get_scale_comparison",
    "load_context_layers",
    "load_documented_limits",
)


def test_loaders_exports_every_hook_expected_by_fradiaz_ui():
    for attr in REQUIRED_BY_APP_PY:
        assert hasattr(loaders, attr), f"src.data.loaders missing UI hook {attr!r}"


def test_ui_hooks_return_real_artefact_payloads_across_all_tabs():
    cases = loaders.load_study_cases()
    assert [c["id"] for c in cases] == list(C.CASE_IDS)
    assert cases[0]["label"] == "Mexico City, MX"

    meta_cdmx = loaders.load_meta("mexico-city")
    assert meta_cdmx["product"] == "mock"
    assert meta_cdmx["unit"] == "mm"

    lu_opts = loaders.load_land_use_options()
    assert "urban-dense" in lu_opts
    assert "water" not in lu_opts  # dropped by coherence filter per handoff-mock

    assert loaders.load_risk_levels() == C.RISK_LEVELS

    filters = {
        "case_id": "mexico-city",
        "land_use": "urban-dense",
        "coherence_min": 0.5,
        "risk_level": None,
        "extrude_3d": True,
        "scale_mode": "shared",
    }
    df_map = loaders.get_map_layer_df("mexico-city", filters)
    assert not df_map.empty
    assert {
        "cell_id",
        "lat",
        "lon",
        "delta_change",
        "color",
        "height",
        "regime",
        "ratio_R",
        "seasonal_amp_mm",
    } <= set(df_map.columns)

    # Default when extrude_3d is omitted must be flat (height == 0.0) per docs/requests.md
    df_flat = loaders.get_map_layer_df("mexico-city", {"case_id": "mexico-city"})
    assert (df_flat["height"] == 0.0).all()

    counts = loaders.get_layer_counts("mexico-city", filters)
    assert 0 < counts["shown"] < counts["total"] == 11694

    scale = loaders.get_color_scale("mexico-city", filters)
    assert scale["min"] == -425.0 and scale["max"] == 425.0
    assert scale["fallback"] is False
    assert "stops" in scale and len(scale["stops"]) >= 3

    vs = loaders.get_view_state("mexico-city")
    assert vs is not None and 19.0 < vs["latitude"] < 20.0

    # Málaga null-result signal check
    sig_malaga = loaders.get_signal_status("malaga")
    assert sig_malaga["above_limit"] is False
    assert sig_malaga["detection_limit"] == 1.5
    assert sig_malaga["detection_limit_unit"] == "mm/year"

    first_cid = str(df_map.iloc[0]["cell_id"])
    detail = loaders.get_cell_detail("mexico-city", first_cid)
    assert detail is not None and detail["cell_id"] == first_cid
    assert detail["series_scope"] == "aoi" and len(detail["series"]) == 62

    ts = loaders.load_timeseries("mexico-city", "aoi")
    assert len(ts) == 62  # 2-year mock window: 62 dates, 2024-11-10 -> 2026-11-12

    s_kpis = loaders.get_series_kpis("mexico-city", filters)
    assert [k["label"] for k in s_kpis] == [
        "Median velocity",
        "Cells above threshold",
        "Critical cells",
        "Coherent coverage",
        # v1.1: fitted seasonal amplitude, published because the 2-year window identifies it
        "Median seasonal amplitude",
    ]
    assert s_kpis[-1]["value"] == 1.5  # meta.stats.median_seasonal_amp_mm for Mexico City
    assert s_kpis[-1]["unit"] == "mm"

    case_series = loaders.get_case_series("mexico-city", filters)
    assert len(case_series) == 62
    assert case_series[0]["scaled"] is False and case_series[0]["scale_factor"] == 1.0
    assert len(loaders.get_land_use_bars("mexico-city", filters)) == 1

    comp = loaders.get_scale_comparison("shared")
    shares = {c["id"]: c["share"] for c in comp["cases"]}
    assert shares["mexico-city"] == 1.0
    assert shares["malaga"] < 0.05

    ctx = loaders.load_context_layers("mexico-city")
    assert ctx is not None and ctx["summary"]["n_features"] == 12

    limits = loaders.load_documented_limits()
    assert limits["section_8_caveat"] is not None
    assert len(limits["section_8_caveat"].split()) >= 20
    assert len(limits["section_13"]) == 13
    assert limits["section_13"][4].endswith("detection, not thresholding.")
    assert all(item.count("**") % 2 == 0 for item in limits["section_13"])


def test_load_availability_cites_the_asf_record_without_downloading() -> None:
    from src.data import loaders

    info = loaders.load_availability("mexico-city")
    assert info is not None
    assert info["available"] is True
    assert info["http_status"] == 200
    assert isinstance(info["stacks"], list) and info["stacks"]
    stack = next((s for s in info["stacks"] if s["recommended"]), info["stacks"][0])
    assert stack["track"]
    assert stack["n_granules"] and stack["n_granules"] > 0
    assert isinstance(stack["date_range"], list) and len(stack["date_range"]) == 2
    assert info["coverage_note"]
    assert loaders.load_availability("atlantis") is None
