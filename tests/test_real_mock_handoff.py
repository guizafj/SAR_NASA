"""End-to-end integration tests against the shipped 3-city mock artefacts in `data/processed/`.

Verifies that Rol 1 (`src.data` + `src.domain`) loads, validates, filters, aggregates and builds
PyDeck map payloads over all 20,840 real H3 cells emitted by Rol 3 (`pipeline/generate_mock.py`),
preserving every numerical expectation documented in `docs/handoff-mock.md`.
"""

from __future__ import annotations

import pytest

from src.data import (
    aoi_series,
    kpis,
    list_available_cases,
    load_all_available_bundles,
    load_case,
    load_study_cases_config,
    map_payload,
)
from src.domain.filters import FilterCriteria, apply_filters, available_filter_options
from src.domain.map_adapter import (
    GLOBAL_VELOCITY_RANGE_MM_YR,
    RISK_COLORS_RGB,
    build_map_payload,
    build_pydeck_layer_specs,
)
from src.domain.metrics import (
    build_city_comparison,
    build_provenance_badge,
    compute_kpis,
    extract_series_view,
)


def test_shipped_three_cities_load_and_match_handoff_expectations():
    cases = list_available_cases()
    assert cases == ["mexico-city", "jakarta", "malaga"]

    cfg = load_study_cases_config()
    assert len(cfg.cases["mexico-city"].aoi_polygon) > 100  # loaded from config/aoi/*.geojson

    bundles = load_all_available_bundles()
    assert len(bundles["mexico-city"].grid) == 11694
    assert len(bundles["jakarta"].grid) == 4811
    assert len(bundles["malaga"].grid) == 2645

    # Also verify drop-in reference_loader compatibility via src.data
    ref_case = load_case("mexico-city")
    assert len(map_payload(ref_case, coherence_min=0.7)) < 11694
    dates, vals = aoi_series(ref_case)
    assert len(dates) == len(vals) == 12
    assert kpis(ref_case)["cells"] == 11694

    for slug, bundle in bundles.items():
        assert bundle.validation.is_valid, f"{slug} failed validation: {bundle.validation.all_problems}"
        assert bundle.is_mock is True
        opts = available_filter_options(bundle)
        assert opts.sensor_coherence_min == pytest.approx(0.35)
        assert len(opts.available_dates) == 12


def test_shipped_kpis_and_comparison_match_handoff_mock_doc():
    bundles = load_all_available_bundles()

    cdmx_kpis = compute_kpis(bundles["mexico-city"])
    assert cdmx_kpis.median_velocity_mm_yr == pytest.approx(-62.065, abs=0.05)
    assert cdmx_kpis.is_null_result is False
    assert cdmx_kpis.critical_cells_count > 5000

    malaga_kpis = compute_kpis(bundles["malaga"])
    assert malaga_kpis.median_velocity_mm_yr == pytest.approx(-1.36, abs=0.05)
    assert malaga_kpis.is_null_result is True
    assert malaga_kpis.detection_limit_mm_yr == pytest.approx(1.5)
    assert malaga_kpis.critical_cells_count == 0

    badge = build_provenance_badge(bundles["malaga"])
    assert badge.is_mock is True
    assert badge.revisit_days == 12

    series = extract_series_view(bundles["mexico-city"])
    assert series.n_points == 12
    assert series.fitted_trend_mm_yr is not None
    assert series.fitted_trend_mm_yr < -70.0  # chart mean slope ~ -78.1 mm/yr per handoff §3c

    comp = build_city_comparison(list(bundles.values()))
    assert comp.shared_velocity_range_mm_yr == (-425.0, 425.0)
    assert comp.has_mixed_sensors is False
    assert comp.sensor_warning is None


def test_shipped_pydeck_payload_respects_symmetric_425_shared_scale():
    bundles = load_all_available_bundles()

    p_cdmx = build_map_payload(bundles["mexico-city"], scale_mode="shared", extrude_3d=True)
    p_malaga = build_map_payload(bundles["malaga"], scale_mode="shared", extrude_3d=True)

    assert p_cdmx.legend.domain_min == GLOBAL_VELOCITY_RANGE_MM_YR[0] == -425.0
    assert p_cdmx.legend.domain_max == GLOBAL_VELOCITY_RANGE_MM_YR[1] == 425.0

    # In Málaga >99.5% of cells sit inside [-5, +5] mm/yr (2,645 of 2,650) -> neutral grey on shared scale
    neutral_rgb = list(RISK_COLORS_RGB["estable"])
    for rec in p_malaga.grid_records:
        if rec["risk_level"] == "estable":
            assert rec["fill_color"][:3] == neutral_rgb
    malaga_neutral_share = sum(
        1 for r in p_malaga.grid_records if r["fill_color"][:3] == neutral_rgb
    ) / len(p_malaga.grid_records)
    assert malaga_neutral_share > 0.995
    assert max(r["elevation_m"] for r in p_malaga.grid_records) < 55.0
    assert max(r["elevation_m"] for r in p_cdmx.grid_records) > 2800.0

    # Independent toggle seeds from meta.stats p05/p95 without mutating data
    p_malaga_ind = build_map_payload(
        bundles["malaga"],
        FilterCriteria(coherence_min=0.5),
        scale_mode="independent",
        extrude_3d=False,
    )
    assert p_malaga_ind.legend.scale_mode == "independent"
    assert len(p_malaga_ind.grid_records) < 2650

    spec = build_pydeck_layer_specs(p_cdmx)
    assert len(spec["layers"]) == 2
