"""Parity between the two validators of the contract-1.1 seasonal surface.

`src/domain/contracts.py` is normative and `src/data/validation.py` is the app-level gate the CI
runs (`-m src.data.validation --processed data/processed --require-cases`). The second module
re-checks some of the same rules as cross-artefact invariants, so a drift between the two means CI
and the frozen contract can return opposite verdicts on the same file: a sentinel `ratio_R` could
ship, or a legal not-fitted grid could fail the gate.

Each test therefore drives **both** validators on the very same input and asserts the *same*
verdict, plus the expected direction so a case cannot pass by both rejecting for an unrelated
reason. The rule set covered is the 1.1 seasonal surface only:

* null ``ratio_R`` exactly where ``seasonal_amp_mm == 0`` (nothing fitted) and nowhere else;
* numeric ``ratio_R`` consistent with ``|velocity_mm_yr| / seasonal_amp_mm`` where fitted;
* the ``MAX_PUBLISHABLE_RATIO_R`` ceiling;
* ``median_ratio_R`` null-or-finite, and not null beside evidence that a harmonic was fitted;
* the ``dominant_regime`` enum;
* the "seasonal term not fitted" limitation that must accompany a null ratio;
* numeric validation of ``trend_mm_yr`` / ``seasonal_mm``;
* the ``regime`` enum.
"""

from __future__ import annotations

import copy
import math
from typing import Any

import pandas as pd
import pytest

from src.data import validation as app_validation
from src.data.validation import validate_case_bundle
from src.domain import contracts as C

CASE_ID = "mexico-city"
PROVENANCE = C.provenance_id(CASE_ID, "nisar-gunw", "2026-11-14", "abc12345")
TREND = -128.0
AMPLITUDE = 9.4

GRID_ROW: dict[str, Any] = {
    "cell_id": "8928308280fffff",
    "lat": 19.4326,
    "lon": -99.1332,
    "val_t1": 0.0,
    "val_t2": -7.4,
    "delta_change": -7.4,
    "velocity_mm_yr": TREND,
    "seasonal_amp_mm": AMPLITUDE,
    "ratio_R": abs(TREND) / AMPLITUDE,
    "regime": "inelastic",
    "coherence": 0.82,
    "land_use": "urban-dense",
    "risk_level": C.risk_level_from_velocity(TREND),
    "case_id": CASE_ID,
    "sensor": "nisar",
    "product": "nisar-gunw",
    "unit": "mm",
    "velocity_unit": "mm/year",
    "source": "ASF DAAC",
    "acquisition_date": "2026-07-02",
    "provenance_id": PROVENANCE,
}

META: dict[str, Any] = {
    "schema_version": C.SCHEMA_VERSION,
    "case_id": CASE_ID,
    "name": "Mexico City, MX",
    "bbox": [-99.35, 19.20, -98.85, 19.65],
    "crs": C.CRS_OUTPUT,
    "reference_point": [-99.13, 19.43],
    "dates": ["2026-07-02"],
    "revisit_days": 12,
    "sensor": "nisar",
    "product": "nisar-gunw",
    "thresholds": {
        "coherence_min": 0.35,
        "k_sigma": 2.5,
        "min_ddB": 3.0,
        "persistence": "2of3",
        "min_area_m2": 500,
    },
    "provenance": {
        "source": "ASF DAAC",
        "product_id": "NISAR_L2_PR_GUNW",
        "processing_date": "2026-11-14",
        "method": "LOS to vertical (v_north=0), H3 res 9 mean",
        "retrieved_utc": C.CONTRACT_FROZEN_UTC,
        "reference": "Herrera-García et al. 2021",
        "license": "NASA open data",
    },
    "stats": {
        "n_cells": 1,
        "n_dates": 1,
        "median_velocity_mm_yr": TREND,
        "median_seasonal_amp_mm": AMPLITUDE,
        "median_ratio_R": abs(TREND) / AMPLITUDE,
        "dominant_regime": "inelastic",
        "p05_velocity_mm_yr": -410.0,
        "p95_velocity_mm_yr": 12.0,
    },
    "limitations": ["GUNW posting 80 m: city scale, not building scale."],
    "null_policy": "Cells without data are absent from the grid; never written as 0.",
}

CONTEXT: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-99.13, 19.43]},
            "properties": {
                "layer": "infrastructure",
                "class": "water-well",
                "name": "Pozo Roma",
                "case_id": CASE_ID,
                "crs": C.CRS_OUTPUT,
                "source": "SACMEX",
                "provenance_id": PROVENANCE,
            },
        }
    ],
}


def _grid(**overrides: Any) -> pd.DataFrame:
    """One otherwise-legal grid row, with `overrides` applied (only the rule under test varies)."""
    return pd.DataFrame([{**GRID_ROW, **overrides}])


def _timeseries(**overrides: Any) -> pd.DataFrame:
    """The mandatory "aoi" series; ``value - trend*t - seasonal`` is constant (here 0.0)."""
    row = {
        "case_id": CASE_ID,
        "cell_id": "aoi",
        "date": "2026-07-02",
        "value": 0.0,
        "trend_mm_yr": TREND,
        "seasonal_mm": 0.0,
        "unit": "mm",
        "sensor": "nisar",
        "product": "nisar-gunw",
        "quality": "good",
        "provenance_id": PROVENANCE,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _meta(**stats_overrides: Any) -> dict[str, Any]:
    meta = copy.deepcopy(META)
    meta["stats"].update(stats_overrides)
    return meta


def _not_fitted_meta() -> dict[str, Any]:
    """The legal short-stack case: amplitude 0, null ratio, and a limitation that says so."""
    meta = _meta(
        median_seasonal_amp_mm=0.0,
        median_ratio_R=None,
        dominant_regime="transitional",
        seasonal_n_harmonics=0,
    )
    meta["limitations"] = [
        "Seasonal term not fitted: the stack is shorter than the identifiability gate.",
    ]
    return meta


def _contract_problems(grid: pd.DataFrame, meta: dict[str, Any]) -> list[str]:
    report = C.validate_all(
        grid=grid,
        timeseries=_timeseries(),
        meta=meta,
        context=CONTEXT,
        case_id=CASE_ID,
    )
    return [problem for problems in report.values() for problem in problems]


def _meta_for(grid: pd.DataFrame) -> dict[str, Any]:
    """The meta a correct publisher would emit for `grid`, so only the grid rule varies.

    Medians/mode are recomputed from the frame (what the generator does), and a frame whose
    amplitudes are all 0 carries the "not fitted" limitation the contract requires beside the
    null ratio.
    """
    amplitude = pd.to_numeric(grid["seasonal_amp_mm"], errors="coerce")
    ratio = pd.to_numeric(grid["ratio_R"], errors="coerce").dropna()
    meta = _meta(
        n_cells=len(grid),
        median_seasonal_amp_mm=float(amplitude.median()),
        median_ratio_R=float(ratio.median()) if not ratio.empty else None,
        dominant_regime=str(grid["regime"].astype(str).mode().iloc[0]),
    )
    if bool(amplitude.notna().all() and (amplitude == 0.0).all()):
        meta["stats"]["seasonal_n_harmonics"] = 0
        meta["limitations"] = [
            "Seasonal term not fitted: the stack is shorter than the identifiability gate.",
        ]
    return meta


def _app_problems(grid: pd.DataFrame, meta: dict[str, Any]) -> tuple[list[str], bool]:
    result = validate_case_bundle(
        grid=grid,
        timeseries=_timeseries(),
        meta=meta,
        context=CONTEXT,
        case_id=CASE_ID,
    )
    return result.all_problems, result.is_valid


def _assert_parity(
    *, grid: pd.DataFrame, meta: dict[str, Any], expect_accept: bool
) -> tuple[list[str], list[str]]:
    """Assert both validators reach `expect_accept` on the same bundle, and return their problems."""
    contract = _contract_problems(grid, meta)
    app, app_valid = _app_problems(grid, meta)
    assert (not contract) is expect_accept, f"contracts.py: {contract}"
    assert app_valid is expect_accept, f"src/data/validation.py: {app}"
    return contract, app


# ---------------------------------------------------------------------------------------------
# Duplicated grid rule: `src/data/validation.py::_seasonal_column_problems` re-implements the
# seasonal-column invariants of `contracts.validate_grid`. Compared row by row here because that
# is the pair the divergence was measured on; every frame is otherwise legal, so the seasonal rule
# is the only one that can differ.
# ---------------------------------------------------------------------------------------------

GRID_CASES: tuple[tuple[str, dict[str, Any], bool], ...] = (
    ("legal not-fitted harmonic (amplitude 0, null ratio)", {"seasonal_amp_mm": 0.0, "ratio_R": None, "regime": "transitional"}, True),
    ("legal fitted harmonic", {}, True),
    ("sentinel 1e9 where nothing was fitted", {"seasonal_amp_mm": 0.0, "ratio_R": 1e9}, False),
    ("ratio withheld where a harmonic was fitted", {"ratio_R": None}, False),
    ("consistent but unmeasurable ratio (amplitude 1e-13)", {"seasonal_amp_mm": 1e-13, "ratio_R": abs(TREND) / 1e-13}, False),
    ("ratio published rounded to two decimals", {"ratio_R": round(abs(TREND) / AMPLITUDE, 2)}, True),
    ("number beside a null amplitude", {"seasonal_amp_mm": float("nan")}, False),
    ("ratio at the ceiling boundary", {"velocity_mm_yr": -128.0, "seasonal_amp_mm": 128.0e-9, "ratio_R": C.MAX_PUBLISHABLE_RATIO_R, "regime": "inelastic"}, True),
)


@pytest.mark.parametrize("label,overrides,expect_accept", GRID_CASES, ids=[c[0] for c in GRID_CASES])
def test_seasonal_column_rule_agrees_with_contracts(
    label: str, overrides: dict[str, Any], expect_accept: bool
) -> None:
    grid = _grid(**overrides)
    contract = C.validate_grid(grid, case_id=CASE_ID)
    cross_check = app_validation._seasonal_column_problems(grid=grid, timeseries=None)
    assert bool(contract) is not expect_accept, f"{label}: contracts.py said {contract}"
    assert bool(cross_check) is not expect_accept, (
        f"{label}: src/data/validation.py cross-check said {cross_check}"
    )


@pytest.mark.parametrize("label,overrides,expect_accept", GRID_CASES, ids=[c[0] for c in GRID_CASES])
def test_bundle_verdict_matches_contracts_on_seasonal_columns(
    label: str, overrides: dict[str, Any], expect_accept: bool
) -> None:
    grid = _grid(**overrides)
    _assert_parity(grid=grid, meta=_meta_for(grid), expect_accept=expect_accept)


# ---------------------------------------------------------------------------------------------
# Rule-by-rule parity on the seasonal surface (full bundle: grid + timeseries + meta + context).
# Each test states the direction AND that the seasonal rule is the reason, so "both reject" cannot
# pass on an unrelated problem.
# ---------------------------------------------------------------------------------------------


def test_legal_not_fitted_grid_is_accepted_by_both() -> None:
    """Amplitude 0 with a null ratio is legal; a limitation says why the term is not fitted."""
    contract, app = _assert_parity(
        grid=_grid(seasonal_amp_mm=0.0, ratio_R=None, regime="transitional"),
        meta=_not_fitted_meta(),
        expect_accept=True,
    )
    assert contract == [] and app == []


def test_sentinel_ratio_is_rejected_by_both() -> None:
    """A number beside `seasonal_amp_mm == 0` is fabricated: |trend|/0 is undefined."""
    contract, app = _assert_parity(
        grid=_grid(seasonal_amp_mm=0.0, ratio_R=1e9),
        meta=_not_fitted_meta(),
        expect_accept=False,
    )
    assert any("ratio_R" in p for p in contract)
    assert any("ratio_R" in p for p in app)


def test_sentinel_above_the_ceiling_is_rejected_by_both() -> None:
    """A consistent-but-absurd ratio is the numerical-zero amplitude, not a measurement."""
    contract, app = _assert_parity(
        grid=_grid(seasonal_amp_mm=1e-13, ratio_R=abs(TREND) / 1e-13),
        meta=_meta(median_seasonal_amp_mm=1e-13, median_ratio_R=abs(TREND) / 1e-13),
        expect_accept=False,
    )
    assert any(f"{C.MAX_PUBLISHABLE_RATIO_R:g}" in p for p in contract)
    assert any(f"{C.MAX_PUBLISHABLE_RATIO_R:g}" in p for p in app)


def test_null_ratio_where_a_harmonic_was_fitted_is_rejected_by_both() -> None:
    """Withholding a computed ratio is a hidden statistic, in contrast with the legal null."""
    contract, app = _assert_parity(
        grid=_grid(ratio_R=None), meta=_meta(), expect_accept=False
    )
    assert any("ratio_R" in p for p in contract)
    assert any("ratio_R" in p for p in app)


def test_non_finite_median_ratio_is_rejected_by_both() -> None:
    """NaN/inf cannot survive a strict JSON reader: null-or-finite, never non-finite."""
    for value in (float("nan"), float("inf")):
        contract, app = _assert_parity(
            grid=_grid(), meta=_meta(median_ratio_R=value), expect_accept=False
        )
        assert any("median_ratio_R" in p for p in contract), value
        assert any("median_ratio_R" in p for p in app), value


def test_null_median_ratio_beside_a_fitted_amplitude_is_rejected_by_both() -> None:
    """The mirror inconsistency: an amplitude was fitted, so the aggregate ratio is defined."""
    contract, app = _assert_parity(
        grid=_grid(), meta=_meta(median_ratio_R=None), expect_accept=False
    )
    assert any("median_ratio_R" in p for p in contract)
    assert any("median_ratio_R" in p for p in app)


def test_not_fitted_without_its_limitation_is_rejected_by_both() -> None:
    """The null ratio must travel with the reason it is null."""
    meta = _meta(median_seasonal_amp_mm=0.0, median_ratio_R=None, dominant_regime="transitional", seasonal_n_harmonics=0)
    contract, app = _assert_parity(
        grid=_grid(seasonal_amp_mm=0.0, ratio_R=None, regime="transitional"),
        meta=meta,
        expect_accept=False,
    )
    assert any("limitation" in p or "harmonic" in p for p in contract), contract
    assert any("limitation" in p or "harmonic" in p for p in app), app


def test_undeclared_dominant_regime_is_rejected_by_both() -> None:
    contract, app = _assert_parity(
        grid=_grid(), meta=_meta(dominant_regime="recoverable"), expect_accept=False
    )
    assert any("dominant_regime" in p for p in contract)
    assert any("dominant_regime" in p for p in app)


def test_undeclared_grid_regime_is_rejected_by_both() -> None:
    contract, app = _assert_parity(
        grid=_grid(regime="recoverable"), meta=_meta(), expect_accept=False
    )
    assert any("regime" in p for p in contract)
    assert any("regime" in p for p in app)


@pytest.mark.parametrize("column", ["trend_mm_yr", "seasonal_mm"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_numeric_seasonal_timeseries_is_rejected_by_both(column: str, value: float) -> None:
    """1.1 added these two columns; a broken fit must not reach the app as a measurement."""
    grid, meta = _grid(), _meta()
    series = _timeseries(**{column: value})
    contract_report = C.validate_all(
        grid=grid, timeseries=series, meta=meta, context=CONTEXT, case_id=CASE_ID
    )
    app_result = validate_case_bundle(
        grid=grid, timeseries=series, meta=meta, context=CONTEXT, case_id=CASE_ID
    )
    contract = [p for problems in contract_report.values() for p in problems]
    assert contract and not app_result.is_valid, (contract, app_result.all_problems)
    assert any(column in p for p in contract)
    assert any(column in p for p in app_result.all_problems)


def test_ceiling_constant_is_shared_not_copied() -> None:
    """The gate must read the normative ceiling; a local copy is how the two drifted apart."""
    assert app_validation.C.MAX_PUBLISHABLE_RATIO_R == C.MAX_PUBLISHABLE_RATIO_R
    assert math.isfinite(C.MAX_PUBLISHABLE_RATIO_R)
