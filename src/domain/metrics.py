"""Server-side KPIs, provenance badges, time-series summaries and cross-city comparisons (Rol 1 — pozo).

All geophysical and statistical aggregations happen here so `src/ui/*` only formats and renders
numbers that already carry their declared unit and provenance (README §3, flujo §3.2).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import h3
import numpy as np
import pandas as pd

from src.domain import contracts as C
from src.domain.filters import FilterCriteria, FilteredCaseView, apply_filters

if TYPE_CHECKING:
    from src.data.loaders import CaseBundle

VOLUME_UNIT = "m3/year"
AREA_UNIT = "km2"


@dataclass(frozen=True)
class ProvenanceBadge:
    """Complete traceability payload required on every view (README §5 badge rule)."""

    case_id: str
    case_name: str
    sensor: str
    product: str
    source: str
    product_id: str
    is_mock: bool
    revisit_days: int
    coherence_threshold: float
    posting_m: int | None
    displacement_unit: str
    velocity_unit: str
    date_start: str
    date_end: str
    n_dates: int
    processing_date: str
    retrieved_utc: str
    method: str
    reference: str
    license: str
    null_policy: str
    limitations: tuple[str, ...]
    schema_version: str
    contract_fingerprint: str


@dataclass(frozen=True)
class RiskBreakdown:
    """Cell counts and shares across the frozen risk bands (`estable`, `moderado`, `critico`)."""

    counts: Mapping[str, int]
    fractions: Mapping[str, float]
    percentages: Mapping[str, float]


@dataclass(frozen=True)
class CaseKPIs:
    """Reactive KPI bundle computed from a `FilteredCaseView`."""

    case_id: str
    case_name: str
    total_cells: int
    filtered_cells: int
    coverage_ratio: float
    filtered_area_km2: float
    median_velocity_mm_yr: float | None
    p05_velocity_mm_yr: float | None
    p95_velocity_mm_yr: float | None
    min_velocity_mm_yr: float | None
    max_velocity_mm_yr: float | None
    median_delta_mm: float | None
    min_delta_mm: float | None
    max_delta_mm: float | None
    mean_coherence: float | None
    risk_breakdown: RiskBreakdown
    critical_cells_count: int
    critical_area_km2: float
    net_volume_change_m3_yr: float | None
    subsidence_volume_loss_m3_yr: float | None
    dominant_land_use: str | None
    active_date: str | None
    aoi_cumulative_mm_at_date: float | None
    aoi_window_delta_mm: float | None
    is_null_result: bool
    detection_limit_mm_yr: float | None
    expected_regime: str | None
    is_mock: bool
    # Optional, always None under schema v1.0: the seasonal/regime trio was evaluated and rejected
    # (docs/decisions.md D-28 / O-05 — the annual harmonic is not identifiable over 132 days), so
    # no artefact publishes it. Kept as an explicit `None` slot so a future schema can fill it
    # without every consumer changing shape; never render it as "missing data".
    median_seasonal_amp_mm: float | None = None
    median_ratio_R: float | None = None
    dominant_regime: str | None = None
    displacement_unit: str = C.UNIT
    velocity_unit: str = C.VELOCITY_UNIT
    area_unit: str = AREA_UNIT
    volume_unit: str = VOLUME_UNIT


@dataclass(frozen=True)
class SeriesView:
    """Time-series slice ready for Plotly rendering, with honest drill-down metadata."""

    case_id: str
    requested_cell_id: str
    resolved_cell_id: str
    is_fallback_to_aoi: bool
    data: pd.DataFrame
    n_points: int
    date_start: str | None
    date_end: str | None
    net_delta_mm: float | None
    fitted_trend_mm_yr: float | None
    residual_std_mm: float | None
    unit: str
    velocity_unit: str
    sensor: str
    product: str
    revisit_days: int
    cell_static_velocity_mm_yr: float | None = None
    cell_coherence: float | None = None
    cell_risk_level: str | None = None


@dataclass(frozen=True)
class CityComparisonResult:
    """Cross-city comparison table and explicit cross-sensor / revisit warnings."""

    table: pd.DataFrame
    shared_velocity_range_mm_yr: tuple[float, float]
    sensor_warning: str | None
    has_mixed_sensors: bool
    has_mixed_revisit: bool


def _cell_areas_km2(cell_ids: Sequence[str]) -> np.ndarray:
    areas = np.empty(len(cell_ids), dtype=np.float64)
    for idx, cid in enumerate(cell_ids):
        if h3.is_valid_cell(cid):
            areas[idx] = float(h3.cell_area(cid, unit="km^2"))
        else:
            # Fallback to nominal H3 res-9 area (~0.1053 km²) only if non-H3 synthetic fixture is passed.
            areas[idx] = 0.1053325
    return areas


def build_provenance_badge(bundle: CaseBundle) -> ProvenanceBadge:
    """Extract the complete `ProvenanceBadge` from a validated `CaseBundle`."""
    meta = bundle.meta
    sensor = str(meta["sensor"])
    product = str(meta["product"])
    profile = C.sensor_profile(sensor, product)
    prov = meta.get("provenance") or {}
    thresholds = meta.get("thresholds") or {}
    dates = bundle.dates

    return ProvenanceBadge(
        case_id=bundle.case_id,
        case_name=bundle.name,
        sensor=sensor,
        product=product,
        source=str(prov.get("source", "")),
        product_id=str(prov.get("product_id", "")),
        is_mock=(product == "mock"),
        revisit_days=int(meta["revisit_days"]),
        coherence_threshold=float(thresholds.get("coherence_min", profile.coherence_min)),
        posting_m=profile.posting_m,
        displacement_unit=C.UNIT,
        velocity_unit=C.VELOCITY_UNIT,
        date_start=dates[0] if dates else "",
        date_end=dates[-1] if dates else "",
        n_dates=len(dates),
        processing_date=str(prov.get("processing_date", "")),
        retrieved_utc=str(prov.get("retrieved_utc", "")),
        method=str(prov.get("method", "")),
        reference=str(prov.get("reference", "")),
        license=str(prov.get("license", "")),
        null_policy=str(meta.get("null_policy", "")),
        limitations=tuple(str(item) for item in (meta.get("limitations") or ())),
        schema_version=str(meta.get("schema_version", C.SCHEMA_VERSION)),
        contract_fingerprint=C.contract_fingerprint(),
    )


def _compute_risk_breakdown(grid: pd.DataFrame) -> RiskBreakdown:
    total = len(grid)
    if total == 0:
        zeros_i = {level: 0 for level in C.RISK_LEVELS}
        zeros_f = {level: 0.0 for level in C.RISK_LEVELS}
        return RiskBreakdown(counts=zeros_i, fractions=zeros_f, percentages=zeros_f)

    vc = grid["risk_level"].astype(str).value_counts()
    counts = {level: int(vc.get(level, 0)) for level in C.RISK_LEVELS}
    fractions = {level: counts[level] / total for level in C.RISK_LEVELS}
    percentages = {level: round(fractions[level] * 100.0, 2) for level in C.RISK_LEVELS}
    return RiskBreakdown(counts=counts, fractions=fractions, percentages=percentages)


def _fit_linear_trend_mm_yr(dates: Sequence[str], values_mm: np.ndarray) -> tuple[float | None, float | None]:
    """Fit ordinary least-squares trend [mm/year] and residual std [mm] over ISO dates."""
    if len(dates) < 2:
        return None, None
    try:
        parsed = [dt.date.fromisoformat(str(d)) for d in dates]
    except ValueError:
        return None, None
    t0 = parsed[0]
    years = np.array([(d - t0).days / 365.25 for d in parsed], dtype=np.float64)
    span = float(years.max() - years.min())
    if span <= 0.0:
        return None, None

    slope, intercept = np.polyfit(years, values_mm.astype(np.float64), 1)
    fitted = slope * years + intercept
    residuals = values_mm - fitted
    resid_std = float(np.std(residuals, ddof=1)) if len(values_mm) > 2 else float(np.std(residuals))
    return float(slope), resid_std


def compute_kpis(
    bundle_or_view: CaseBundle | FilteredCaseView,
    criteria: FilterCriteria | None = None,
    *,
    case_config_regime: str | None = None,
) -> CaseKPIs:
    """Compute reactive KPIs from a `FilteredCaseView` (or directly from a `CaseBundle`)."""
    if isinstance(bundle_or_view, FilteredCaseView):
        view = bundle_or_view
        expected_regime = case_config_regime
    else:
        view = apply_filters(bundle_or_view, criteria)
        expected_regime = (
            case_config_regime
            or (bundle_or_view.case_config.expected_regime if bundle_or_view.case_config else None)
        )

    meta = view.meta
    stats = meta.get("stats") or {}
    grid = view.grid
    total_cells = view.total_grid_cells
    filtered_cells = len(grid)
    coverage_ratio = (filtered_cells / total_cells) if total_cells > 0 else 0.0

    meta_median = stats.get("median_velocity_mm_yr")
    det_limit_raw = stats.get("detection_limit_mm_yr")
    detection_limit = float(det_limit_raw) if isinstance(det_limit_raw, (int, float)) else None
    is_null_result = (
        isinstance(meta_median, (int, float)) and abs(float(meta_median)) < C.NULL_RESULT_MM_YR
    )

    risk_breakdown = _compute_risk_breakdown(grid)
    critical_count = risk_breakdown.counts["critico"]

    if filtered_cells == 0:
        filtered_area_km2 = 0.0
        critical_area_km2 = 0.0
        median_vel = p05_vel = p95_vel = min_vel = max_vel = None
        median_delta = min_delta = max_delta = mean_coh = None
        net_vol = sub_loss_vol = None
        dominant_lu = None
        med_seasonal = med_ratio_r = None
        dom_regime = str(stats["dominant_regime"]) if stats.get("dominant_regime") else None
    else:
        cell_ids = grid["cell_id"].astype(str).tolist()
        areas_km2 = _cell_areas_km2(cell_ids)
        filtered_area_km2 = float(areas_km2.sum())

        vel = grid["velocity_mm_yr"].to_numpy(dtype=np.float64)
        delta = grid["delta_change"].to_numpy(dtype=np.float64)
        coh = grid["coherence"].to_numpy(dtype=np.float64)
        is_crit = (grid["risk_level"].astype(str) == "critico").to_numpy()
        critical_area_km2 = float(areas_km2[is_crit].sum())

        median_vel = float(np.median(vel))
        p05_vel = float(np.percentile(vel, 5))
        p95_vel = float(np.percentile(vel, 95))
        min_vel = float(np.min(vel))
        max_vel = float(np.max(vel))

        median_delta = float(np.median(delta))
        min_delta = float(np.min(delta))
        max_delta = float(np.max(delta))
        mean_coh = float(np.mean(coh))

        # README §6: dV/dt = sum(v_up * A_px). 1 mm/yr * 1 km² = 1e-3 m/yr * 1e6 m² = 1e3 m³/yr.
        cell_vol_m3_yr = vel * areas_km2 * 1000.0
        net_vol = float(cell_vol_m3_yr.sum())
        subsiding_mask = vel < 0.0
        sub_loss_vol = float(np.abs(cell_vol_m3_yr[subsiding_mask]).sum())

        lu_counts = grid["land_use"].astype(str).value_counts()
        dominant_lu = str(lu_counts.index[0]) if not lu_counts.empty else None
        med_seasonal = (
            float(np.median(grid["seasonal_amp_mm"].to_numpy(dtype=np.float64)))
            if "seasonal_amp_mm" in grid.columns
            else None
        )
        med_ratio_r = (
            float(np.median(grid["ratio_R"].to_numpy(dtype=np.float64)))
            if "ratio_R" in grid.columns
            else None
        )
        if "regime" in grid.columns:
            reg_counts = grid["regime"].astype(str).value_counts()
            dom_regime = str(reg_counts.index[0]) if not reg_counts.empty else None
        else:
            dom_regime = str(stats["dominant_regime"]) if stats.get("dominant_regime") else None

    ts_aoi = view.timeseries_aoi
    active_date: str | None = view.active_date
    aoi_at_date: float | None = None
    aoi_window_delta: float | None = None

    if not ts_aoi.empty:
        dates_col = ts_aoi["date"].astype(str)
        vals_col = ts_aoi["value"].to_numpy(dtype=np.float64)
        aoi_window_delta = float(vals_col[-1] - vals_col[0])

        if active_date is not None:
            le_mask = dates_col <= active_date
            if le_mask.any():
                idx = int(np.flatnonzero(le_mask.to_numpy())[-1])
                active_date = str(dates_col.iloc[idx])
                aoi_at_date = float(vals_col[idx])
            else:
                active_date = str(dates_col.iloc[0])
                aoi_at_date = float(vals_col[0])
        else:
            active_date = str(dates_col.iloc[-1])
            aoi_at_date = float(vals_col[-1])

    return CaseKPIs(
        case_id=view.case_id,
        case_name=str(meta.get("name", view.case_id)),
        total_cells=total_cells,
        filtered_cells=filtered_cells,
        coverage_ratio=coverage_ratio,
        filtered_area_km2=filtered_area_km2,
        median_velocity_mm_yr=median_vel,
        p05_velocity_mm_yr=p05_vel,
        p95_velocity_mm_yr=p95_vel,
        min_velocity_mm_yr=min_vel,
        max_velocity_mm_yr=max_vel,
        median_delta_mm=median_delta,
        min_delta_mm=min_delta,
        max_delta_mm=max_delta,
        mean_coherence=mean_coh,
        risk_breakdown=risk_breakdown,
        critical_cells_count=critical_count,
        critical_area_km2=critical_area_km2,
        net_volume_change_m3_yr=net_vol,
        subsidence_volume_loss_m3_yr=sub_loss_vol,
        dominant_land_use=dominant_lu,
        active_date=active_date,
        aoi_cumulative_mm_at_date=aoi_at_date,
        aoi_window_delta_mm=aoi_window_delta,
        is_null_result=is_null_result,
        detection_limit_mm_yr=detection_limit,
        expected_regime=expected_regime,
        is_mock=(str(meta.get("product")) == "mock"),
        median_seasonal_amp_mm=med_seasonal,
        median_ratio_R=med_ratio_r,
        dominant_regime=dom_regime,
    )


def extract_series_view(
    bundle_or_view: CaseBundle | FilteredCaseView,
    criteria: FilterCriteria | None = None,
) -> SeriesView:
    """Build a `SeriesView` for the AOI or clicked H3 cell, including linear trend & cell context."""
    if isinstance(bundle_or_view, FilteredCaseView):
        view = bundle_or_view
        full_grid = view.grid
    else:
        view = apply_filters(bundle_or_view, criteria)
        full_grid = bundle_or_view.grid

    requested = view.criteria.selected_cell_id or "aoi"
    resolved = view.selected_series_cell_id
    df = view.timeseries_selected.copy()
    meta = view.meta

    cell_vel: float | None = None
    cell_coh: float | None = None
    cell_risk: str | None = None
    if requested != "aoi" and not full_grid.empty:
        match = full_grid.loc[full_grid["cell_id"].astype(str) == requested]
        if not match.empty:
            row = match.iloc[0]
            cell_vel = float(row["velocity_mm_yr"])
            cell_coh = float(row["coherence"])
            cell_risk = str(row["risk_level"])

    if df.empty:
        return SeriesView(
            case_id=view.case_id,
            requested_cell_id=requested,
            resolved_cell_id=resolved,
            is_fallback_to_aoi=view.is_selected_series_fallback,
            data=df,
            n_points=0,
            date_start=None,
            date_end=None,
            net_delta_mm=None,
            fitted_trend_mm_yr=None,
            residual_std_mm=None,
            unit=C.UNIT,
            velocity_unit=C.VELOCITY_UNIT,
            sensor=str(meta["sensor"]),
            product=str(meta["product"]),
            revisit_days=int(meta["revisit_days"]),
            cell_static_velocity_mm_yr=cell_vel,
            cell_coherence=cell_coh,
            cell_risk_level=cell_risk,
        )

    dates = df["date"].astype(str).tolist()
    values = df["value"].to_numpy(dtype=np.float64)
    net_delta = float(values[-1] - values[0])
    slope, resid_std = _fit_linear_trend_mm_yr(dates, values)

    return SeriesView(
        case_id=view.case_id,
        requested_cell_id=requested,
        resolved_cell_id=resolved,
        is_fallback_to_aoi=view.is_selected_series_fallback,
        data=df,
        n_points=len(df),
        date_start=dates[0],
        date_end=dates[-1],
        net_delta_mm=net_delta,
        fitted_trend_mm_yr=slope,
        residual_std_mm=resid_std,
        unit=C.UNIT,
        velocity_unit=C.VELOCITY_UNIT,
        sensor=str(meta["sensor"]),
        product=str(meta["product"]),
        revisit_days=int(meta["revisit_days"]),
        cell_static_velocity_mm_yr=cell_vel,
        cell_coherence=cell_coh,
        cell_risk_level=cell_risk,
    )


def build_city_comparison(
    bundles: Sequence[CaseBundle],
    *,
    enforce_sensor_coherence: bool = True,
    shared_velocity_range_mm_yr: tuple[float, float] = (-425.0, 425.0),
) -> CityComparisonResult:
    """Compare multiple cities on one shared physical basis and warn if sensors/revisits differ."""
    rows: list[dict[str, Any]] = []
    sensors_seen: set[str] = set()
    revisits_seen: set[int] = set()

    for bundle in bundles:
        crit = FilterCriteria(
            coherence_min=(
                float(bundle.meta["thresholds"]["coherence_min"])
                if enforce_sensor_coherence
                else 0.0
            )
        )
        kpis = compute_kpis(bundle, crit)
        sensors_seen.add(bundle.sensor)
        revisits_seen.add(int(bundle.meta["revisit_days"]))
        rows.append(
            {
                "case_id": bundle.case_id,
                "name": bundle.name,
                "sensor": bundle.sensor,
                "product": bundle.product,
                "is_mock": bundle.is_mock,
                "revisit_days": int(bundle.meta["revisit_days"]),
                "expected_regime": kpis.expected_regime or "unknown",
                "n_cells": kpis.filtered_cells,
                "area_km2": round(kpis.filtered_area_km2, 2),
                "median_velocity_mm_yr": kpis.median_velocity_mm_yr,
                "p05_velocity_mm_yr": kpis.p05_velocity_mm_yr,
                "min_velocity_mm_yr": kpis.min_velocity_mm_yr,
                "critical_pct": kpis.risk_breakdown.percentages["critico"],
                "subsidence_volume_loss_m3_yr": kpis.subsidence_volume_loss_m3_yr,
                "is_null_result": kpis.is_null_result,
                "detection_limit_mm_yr": kpis.detection_limit_mm_yr,
                "velocity_unit": C.VELOCITY_UNIT,
            }
        )

    has_mixed_sensors = len(sensors_seen) > 1
    has_mixed_revisit = len(revisits_seen) > 1
    warning: str | None = None
    if has_mixed_sensors or has_mixed_revisit:
        warning = (
            f"Cross-sensor comparison active (sensors={sorted(sensors_seen)}, "
            f"revisit_days={sorted(revisits_seen)}). Velocities [mm/year] are physically "
            f"comparable, but coherence values are NOT comparable across revisit intervals."
        )

    return CityComparisonResult(
        table=pd.DataFrame(rows),
        shared_velocity_range_mm_yr=shared_velocity_range_mm_yr,
        sensor_warning=warning,
        has_mixed_sensors=has_mixed_sensors,
        has_mixed_revisit=has_mixed_revisit,
    )


__all__ = [
    "AREA_UNIT",
    "CaseKPIs",
    "CityComparisonResult",
    "ProvenanceBadge",
    "RiskBreakdown",
    "SeriesView",
    "VOLUME_UNIT",
    "build_city_comparison",
    "build_provenance_badge",
    "compute_kpis",
    "extract_series_view",
]
