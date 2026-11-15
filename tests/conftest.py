"""Shared synthetic case builders for Rol 1 backend tests (valid against frozen contract v1.1)."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

import h3
import numpy as np
import pandas as pd
import pytest

from pipeline.science import transforms as V
from src.data.loaders import clear_loader_cache
from src.domain import contracts as C

CITY_CENTERS: dict[str, tuple[str, list[float], tuple[float, float]]] = {
    "mexico-city": ("Mexico City, MX", [-99.35, 19.20, -98.85, 19.65], (-99.13, 19.43)),
    "jakarta": ("Jakarta, ID", [106.70, -6.40, 107.00, -6.10], (106.85, -6.21)),
    "malaga": ("Málaga, ES", [-4.60, 36.60, -4.30, 36.85], (-4.42, 36.72)),
}

#: First acquisition of the synthetic stack and how many acquisitions it carries. Mirrors
#: `processing.dates_start` / `processing.dates_count` in `config/study_cases.yaml`: contract 1.1
#: makes `seasonal_amp_mm` a **required, fitted** column, and `pipeline/science/transforms.py`
#: only publishes an amplitude when the span clears its identifiability gate (>= 0.75 annual
#: cycle per harmonic, design condition <= 30). At the 12-day revisit the window is
#: 61 x 12 d = 732 d = 2.00 annual cycles (condition ~4.1), which is what makes the seasonal
#: columns honest to publish. The 132-day window this fixture used under contract 1.0 measured
#: 0.36 cycles / condition 66.8, i.e. an amplitude that must not be published (§ contract 1.1).
DATES_START = dt.date(2024, 11, 10)
DATES_COUNT = 62

#: Injected seasonal amplitude per case, mirroring `mock.seasonal_amplitude_mm` in
#: `config/study_cases.yaml`. The three values exercise every member of `C.REGIMES`:
#: Mexico City 1.5 mm (|trend| >> A -> inelastic), Jakarta 12 mm (mixed: 6 inelastic + 1
#: transitional), Malaga 3.5 mm (|trend| < A -> elastic, the declared negative control).
CASE_SEASONAL_AMP_MM: dict[str, float] = {
    "mexico-city": 1.5,
    "jakarta": 12.0,
    "malaga": 3.5,
}


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_loader_cache()
    yield
    clear_loader_cache()


def _elapsed_years(dates: list[str]) -> np.ndarray:
    """Year offsets from the first acquisition, on the 365.25 d year the pipeline and the
    cross-artefact checks (`src/data/validation.py`) both use."""
    first = dt.date.fromisoformat(dates[0])
    return np.array(
        [(dt.date.fromisoformat(d) - first).days / 365.25 for d in dates], dtype=np.float64
    )


def _seasonal_series(t_years: np.ndarray, amplitude_mm: float) -> np.ndarray:
    """``A·(cos(2π(t−c)/T) − cos(2πc/T))`` — the centred annual harmonic.

    The same expression as `pipeline/mock/model.py::cumulative_series` (README §6 trend/seasonal
    split), and the reason the published decomposition is exact: centring the harmonic on the
    middle of the window gives both endpoints the same seasonal offset, so ``val_t1 == 0``,
    ``val_t2 == trend·span`` and ``delta_change`` tracks the secular rate. The subtracted constant
    is absorbed by the fit intercept, which the contract does not publish, so
    ``value − trend_mm_yr·t − seasonal_mm`` stays constant along every series — the invariant
    `src/data/validation.py` cross-checks on every bundle.
    """
    centre = float(t_years[-1]) / 2.0 if t_years.size else 0.0
    anchor = float(np.cos(2.0 * np.pi * centre / V.SEASONAL_PERIOD_YEARS))
    phase = 2.0 * np.pi * (t_years - centre) / V.SEASONAL_PERIOD_YEARS
    return float(amplitude_mm) * (np.cos(phase) - anchor)


def classify_regime(trend_mm_yr: float, seasonal_amp_mm: float) -> str:
    """The pipeline's own regime rule, never a local copy of the R bands.

    `transforms.classify_regime` is called by `harmonic_fit` on every real cell, so binding the
    fixture to it means a change to the R > 2 / R < 1 cuts moves the fixture with it instead of
    letting a second, hand-written rule drift. Public so that other synthetic fixtures which
    rewrite `velocity_mm_yr` (e.g. `tests/test_map_adapter.py`) can keep `regime` consistent with
    it instead of pasting the thresholds again. The membership assert fails loudly if the
    pipeline ever returns something `contracts.REGIMES` does not declare.
    """
    regime = V.classify_regime(trend_mm_yr, seasonal_amp_mm)
    assert regime in C.REGIMES, f"{regime!r} is not a declared C.REGIMES member {C.REGIMES}"
    return regime


def _series_rows(
    *,
    case_id: str,
    cell_id: str,
    dates: list[str],
    t_years: np.ndarray,
    trend_mm_yr: float,
    seasonal_mm: np.ndarray,
    sensor: str,
    product: str,
    quality: str,
    provenance: str,
) -> list[dict[str, Any]]:
    """Build the contract-1.1 timeseries rows for one series.

    ``value`` is written **unrounded** on purpose: ``value − trend_mm_yr·t − seasonal_mm`` must be
    constant to 1e-6 mm across every cell (`src/data/validation.py::_seasonal_column_problems`),
    and 4-decimal rounding (what this fixture did under 1.0) would break that by 1e-4 mm.
    """
    rows: list[dict[str, Any]] = []
    for index, iso_date in enumerate(dates):
        rows.append(
            {
                "case_id": case_id,
                "cell_id": cell_id,
                "date": iso_date,
                "value": float(trend_mm_yr) * float(t_years[index]) + float(seasonal_mm[index]),
                "trend_mm_yr": float(trend_mm_yr),
                "seasonal_mm": float(seasonal_mm[index]),
                "unit": C.UNIT,
                "sensor": sensor,
                "product": product,
                "quality": quality,
                "provenance_id": provenance,
            }
        )
    return rows


def write_synthetic_case(
    processed_root: pathlib.Path,
    case_id: str = "mexico-city",
    *,
    sensor: str = "nisar",
    product: str = "mock",
    n_rings: int = 1,
    include_cell_series: bool = False,
    flat: bool = False,
) -> pathlib.Path:
    """Write a complete, 100% contract-conformant four-artefact case to `processed_root`."""
    name, bbox, (ref_lon, ref_lat) = CITY_CENTERS[case_id]
    profile = C.sensor_profile(sensor, product)
    prov = C.provenance_id(case_id, product, dt.date(2026, 11, 14), "deadbeef")
    source_label = "MOCK synthetic generator" if product == "mock" else "ASF DAAC"

    center_h3 = h3.latlng_to_cell(ref_lat, ref_lon, 9)
    cells = sorted(h3.grid_disk(center_h3, n_rings))

    step = profile.revisit_days or 12
    dates = [(DATES_START + dt.timedelta(days=i * step)).isoformat() for i in range(DATES_COUNT)]
    t_years = _elapsed_years(dates)
    span_years = float(t_years[-1])
    # Guard, not decoration: if this window ever shrinks below the pipeline's per-harmonic
    # coverage gate, the fixture would publish a seasonal amplitude that
    # `transforms._seasonal_identifiability` (and `harmonic_fit`) rejects — the exact dishonesty
    # contract 1.1 exists to make impossible. Fail here, not in a reviewer's head.
    coverage = span_years / V.SEASONAL_PERIOD_YEARS
    assert coverage >= V.MIN_SEASONAL_COVERAGE_PER_HARMONIC, (
        f"{case_id}: the synthetic stack spans {coverage:.2f} annual cycles, but "
        f"seasonal_amp_mm needs >= {V.MIN_SEASONAL_COVERAGE_PER_HARMONIC} to be publishable"
    )
    #: > 0 by construction: `ratio_R = |trend| / amp` must stay finite and the contract calls a
    #: zero amplitude "undefined ratio" (a fitted amplitude of exactly 0 means "not fitted").
    seasonal_amp_mm = CASE_SEASONAL_AMP_MM[case_id]
    seasonal_mm = _seasonal_series(t_years, seasonal_amp_mm)

    grid_rows: list[dict[str, Any]] = []
    land_uses = ("urban-dense", "urban-residential", "infrastructure", "vegetation")

    for idx, cid in enumerate(cells):
        lat, lon = h3.cell_to_latlng(cid)
        if case_id == "mexico-city":
            # Strong subsidence gradient: -380 down to -50 mm/yr
            vel = -380.0 + idx * 55.0
        elif case_id == "jakarta":
            vel = -210.0 + idx * 32.0
        else:
            # Málaga negative control: near-zero rates within [-2.4, +0.6] mm/yr
            vel = -2.4 + idx * 0.5

        val_t1 = 0.0
        # The centred harmonic is zero at both endpoints, so the endpoint values stay purely
        # secular: val_t2 - val_t1 == trend x span, exactly as `pipeline/mock/model.py` builds it.
        val_t2 = round(vel * span_years, 4)
        delta = val_t2 - val_t1
        coh = round(min(0.95, 0.28 + 0.09 * idx), 3)

        grid_rows.append(
            {
                "cell_id": cid,
                "lat": float(lat),
                "lon": float(lon),
                "val_t1": val_t1,
                "val_t2": val_t2,
                "delta_change": delta,
                "velocity_mm_yr": float(vel),
                "seasonal_amp_mm": float(seasonal_amp_mm),
                "ratio_R": float(abs(vel) / seasonal_amp_mm),
                "regime": classify_regime(vel, seasonal_amp_mm),
                "coherence": coh,
                "land_use": land_uses[idx % len(land_uses)],
                "risk_level": C.risk_level_from_velocity(vel),
                "case_id": case_id,
                "sensor": sensor,
                "product": product,
                "unit": C.UNIT,
                "velocity_unit": C.VELOCITY_UNIT,
                "source": source_label,
                "acquisition_date": dates[-1],
                "provenance_id": prov,
            }
        )

    grid_df = pd.DataFrame(grid_rows)
    vel_np = grid_df["velocity_mm_yr"].to_numpy(dtype=np.float64)
    median_vel = float(np.median(vel_np))

    # AOI aggregate series (true median velocity) + optional drill-down series for the first cell.
    ts_rows = _series_rows(
        case_id=case_id,
        cell_id="aoi",
        dates=dates,
        t_years=t_years,
        trend_mm_yr=median_vel,
        seasonal_mm=seasonal_mm,
        sensor=sensor,
        product=product,
        quality="good",
        provenance=prov,
    )
    if include_cell_series:
        ts_rows += _series_rows(
            case_id=case_id,
            cell_id=str(grid_rows[0]["cell_id"]),
            dates=dates,
            t_years=t_years,
            trend_mm_yr=float(grid_rows[0]["velocity_mm_yr"]),
            seasonal_mm=seasonal_mm,
            sensor=sensor,
            product=product,
            quality="good",
            provenance=prov,
        )

    ts_df = pd.DataFrame(ts_rows)

    #: Contract 1.1 aggregates: the *grid's* own medians and modal regime, computed with the same
    #: estimator and the same label tie-break as `src/data/validation.py::_seasonal_stat_problems`
    #: so a downstream check can recompute them from the parquet and get the same answer.
    amp_np = grid_df["seasonal_amp_mm"].to_numpy(dtype=np.float64)
    ratio_np = grid_df["ratio_R"].to_numpy(dtype=np.float64)
    regime_counts = grid_df["regime"].astype(str).value_counts()
    dominant_regime = sorted(
        regime_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))
    )[0][0]

    stats: dict[str, Any] = {
        "n_cells": len(grid_df),
        "n_dates": len(dates),
        "median_velocity_mm_yr": round(median_vel, 3),
        # Rounding stays well inside the cross-check tolerances (1e-2 mm for the amplitude,
        # 1e-4 for the dimensionless ratio) and keeps meta.json readable.
        "median_seasonal_amp_mm": round(float(np.median(amp_np)), 3),
        "median_ratio_R": round(float(np.median(ratio_np)), 6),
        "dominant_regime": dominant_regime,
        "p05_velocity_mm_yr": round(float(np.percentile(vel_np, 5)), 3),
        "p95_velocity_mm_yr": round(float(np.percentile(vel_np, 95)), 3),
    }
    if abs(median_vel) < C.NULL_RESULT_MM_YR:
        stats["detection_limit_mm_yr"] = 1.5

    meta = {
        "schema_version": C.SCHEMA_VERSION,
        "case_id": case_id,
        "name": name,
        "bbox": bbox,
        "crs": C.CRS_OUTPUT,
        "reference_point": [ref_lon, ref_lat],
        "dates": dates,
        "revisit_days": step,
        "sensor": sensor,
        "product": product,
        "thresholds": {
            "coherence_min": profile.coherence_min,
            "k_sigma": profile.k_sigma,
            "min_ddB": profile.min_ddB,
            "persistence": profile.persistence,
            "min_area_m2": profile.min_area_m2,
        },
        "provenance": {
            "source": source_label,
            "product_id": f"{product.upper()}_{case_id}_TEST",
            "processing_date": "2026-11-14",
            "method": "LOS->vertical (v_north=0), H3 res 9 mean",
            "retrieved_utc": "2026-11-14T16:00:00Z",
            "reference": "Herrera-Garcia et al. 2021, doi:10.1126/science.abb8549",
            "license": "NASA open data",
        },
        "stats": stats,
        "limitations": [
            "GUNW posting is 80 m: city scale, not building scale.",
            "Coherence is revisit-dependent; never pool 12-day NISAR with 6-day Sentinel-1.",
            "Synthetic case: the seasonal term is an injected harmonic recovered exactly by "
            "harmonic_fit because the series carries no temporal noise; a real stack of the same "
            f"span ({span_years:.2f} annual cycles) would carry an amplitude uncertainty.",
        ],
        "null_policy": "Cells without data are absent from the grid; never written as 0.",
    }

    w, s, e, n = bbox
    context = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [ref_lon, ref_lat]},
                "properties": {
                    "layer": "infrastructure",
                    "class": "water-well",
                    "name": f"Reference Well {case_id}",
                    "case_id": case_id,
                    "crs": C.CRS_OUTPUT,
                    "source": "OSM",
                    "provenance_id": prov,
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
                },
                "properties": {
                    "layer": "admin_boundary",
                    "class": "aoi",
                    "name": f"AOI {name}",
                    "case_id": case_id,
                    "crs": C.CRS_OUTPUT,
                    "source": "Config",
                    "provenance_id": prov,
                },
            },
        ],
    }

    out_dir = processed_root if flat else (processed_root / case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    grid_df.to_parquet(out_dir / "nisar_grid.parquet", index=False)
    ts_df.to_csv(out_dir / "timeseries.csv", index=False)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out_dir / "context_layers.geojson").write_text(json.dumps(context, indent=2), encoding="utf-8")
    return out_dir
