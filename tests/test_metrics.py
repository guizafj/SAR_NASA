"""Tests for `src.domain.metrics` KPIs, storage loss, null-result bounds and comparisons."""

from __future__ import annotations

import pathlib

import pytest

from src.data import load_case_bundle
from src.domain.filters import FilterCriteria
from src.domain.metrics import (
    build_city_comparison,
    build_provenance_badge,
    compute_kpis,
    extract_series_view,
)
from tests.conftest import write_synthetic_case


def test_kpis_distinguish_extreme_subsidence_from_malaga_null_result(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city", sensor="nisar", product="nisar-gunw")
    write_synthetic_case(tmp_path, "malaga", sensor="nisar", product="nisar-gunw")

    cdmx = load_case_bundle("mexico-city", processed_dir=tmp_path)
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    kpis_cdmx = compute_kpis(cdmx, FilterCriteria(coherence_min=0.0))
    assert kpis_cdmx.is_null_result is False
    assert kpis_cdmx.critical_cells_count > 0
    assert kpis_cdmx.filtered_area_km2 > 0.5
    assert kpis_cdmx.subsidence_volume_loss_m3_yr is not None
    assert kpis_cdmx.subsidence_volume_loss_m3_yr > 10_000.0
    assert kpis_cdmx.expected_regime == "inelastic"

    kpis_malaga = compute_kpis(malaga, FilterCriteria(coherence_min=0.0))
    assert kpis_malaga.is_null_result is True
    assert kpis_malaga.detection_limit_mm_yr == 1.5
    assert kpis_malaga.critical_cells_count == 0
    assert kpis_malaga.expected_regime == "elastic"


def test_empty_filtered_view_produces_safe_none_kpis(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "malaga")
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    kpis = compute_kpis(malaga, FilterCriteria(min_abs_velocity_mm_yr=500.0))
    assert kpis.filtered_cells == 0
    assert kpis.coverage_ratio == 0.0
    assert kpis.median_velocity_mm_yr is None
    assert kpis.subsidence_volume_loss_m3_yr is None
    # Null-result metadata from meta.json still survives so UI never loses the thesis
    assert kpis.is_null_result is True
    assert kpis.detection_limit_mm_yr == 1.5


def test_time_scrub_and_series_ols_trend(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city", include_cell_series=True)
    cdmx = load_case_bundle("mexico-city", processed_dir=tmp_path)

    dates = cdmx.dates
    first_cell = str(cdmx.grid.iloc[0]["cell_id"])
    expected_vel = float(cdmx.grid.iloc[0]["velocity_mm_yr"])

    series_view = extract_series_view(
        cdmx,
        FilterCriteria(
            coherence_min=0.0,
            selected_cell_id=first_cell,
            date_range=(dates[0], dates[3]),
            selected_date=dates[2],
        ),
    )
    assert series_view.is_fallback_to_aoi is False
    assert series_view.n_points == 4
    assert series_view.fitted_trend_mm_yr == pytest.approx(expected_vel, rel=1e-2)
    assert series_view.cell_static_velocity_mm_yr == pytest.approx(expected_vel)

    badge = build_provenance_badge(cdmx)
    assert badge.is_mock is True
    assert badge.revisit_days == 12
    assert len(badge.limitations) >= 1


def test_city_comparison_emits_cross_sensor_warning_when_revisit_differs(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city", sensor="nisar", product="nisar-gunw")
    write_synthetic_case(tmp_path, "malaga", sensor="sentinel-1", product="egms")

    cdmx = load_case_bundle("mexico-city", processed_dir=tmp_path)
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    comp = build_city_comparison([cdmx, malaga])
    assert len(comp.table) == 2
    assert comp.has_mixed_sensors is True
    assert comp.has_mixed_revisit is True
    assert comp.sensor_warning is not None
    assert "NOT comparable" in comp.sensor_warning
