"""Tests for `src.domain.filters` per-sensor defaults, slicing and enum validation."""

from __future__ import annotations

import pathlib

import pytest

from src.data import load_case_bundle
from src.domain import contracts as C
from src.domain.filters import (
    FilterCriteria,
    apply_filters,
    available_filter_options,
    default_filter_criteria,
)
from tests.conftest import write_synthetic_case


def test_default_filter_criteria_respects_sensor_profile_coherence(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city", sensor="nisar", product="nisar-gunw")
    write_synthetic_case(tmp_path, "malaga", sensor="sentinel-1", product="egms")
    write_synthetic_case(tmp_path, "jakarta", sensor="sentinel-1", product="hyp3-s1")

    cdmx = load_case_bundle("mexico-city", processed_dir=tmp_path)
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)
    jakarta = load_case_bundle("jakarta", processed_dir=tmp_path)

    assert default_filter_criteria(cdmx).coherence_min == 0.35
    assert default_filter_criteria(malaga).coherence_min == 0.30
    assert default_filter_criteria(jakarta).coherence_min == 0.40


def test_grid_and_context_filters_do_not_mutate_inputs(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city", include_cell_series=True)
    bundle = load_case_bundle("mexico-city", processed_dir=tmp_path)
    opts = available_filter_options(bundle)

    assert opts.cell_series_available is True
    assert opts.emitted_series_cells_count == 1
    assert opts.revisit_days == 12

    crit = FilterCriteria(
        coherence_min=0.45,
        land_uses=("urban-dense", "infrastructure"),
        risk_levels=("critico",),
        min_abs_velocity_mm_yr=100.0,
        context_layers=("infrastructure",),
    )
    view = apply_filters(bundle, crit)

    assert len(view.grid) < len(bundle.grid)
    assert (view.grid["coherence"] >= 0.45).all()
    assert set(view.grid["risk_level"].unique()) <= {"critico"}
    assert (view.grid["velocity_mm_yr"].abs() >= 100.0).all()
    assert len(view.context["features"]) == 1
    assert view.context["features"][0]["properties"]["layer"] == "infrastructure"
    # Original bundle untouched
    assert len(bundle.context["features"]) == 2


def test_cell_series_drilldown_and_honest_aoi_fallback(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city", include_cell_series=True)
    bundle = load_case_bundle("mexico-city", processed_dir=tmp_path)

    emitted_cell = str(bundle.grid.iloc[0]["cell_id"])
    unemitted_cell = str(bundle.grid.iloc[-1]["cell_id"])

    v_direct = apply_filters(bundle, FilterCriteria(selected_cell_id=emitted_cell))
    assert v_direct.selected_series_cell_id == emitted_cell
    assert v_direct.is_selected_series_fallback is False

    v_fallback = apply_filters(bundle, FilterCriteria(selected_cell_id=unemitted_cell))
    assert v_fallback.selected_series_cell_id == "aoi"
    assert v_fallback.is_selected_series_fallback is True


def test_invalid_filter_enums_and_ranges_fail_fast(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city")
    bundle = load_case_bundle("mexico-city", processed_dir=tmp_path)

    with pytest.raises(C.ContractError, match="land_uses"):
        apply_filters(bundle, FilterCriteria(land_uses=("agricola",)))

    with pytest.raises(C.ContractError, match="risk_levels"):
        apply_filters(bundle, FilterCriteria(risk_levels=("alto",)))

    with pytest.raises(C.ContractError, match="date_range"):
        apply_filters(bundle, FilterCriteria(date_range=("2026-09-01", "2026-07-02")))
