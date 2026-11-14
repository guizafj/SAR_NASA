"""Pure server-side filtering for SAR grid, time series and GIS context (Rol 1 — pozo).

Rules enforced here:
- Zero Streamlit/UI imports: filters run identically in pytest, CLI checks and Streamlit.
- Thresholds are seeded per `(sensor, product)` via `src.domain.contracts.sensor_profile` —
  never hardcoded to a pooled constant (contract §2.5, §7.3).
- Undeclared enum values (`land_use`, `risk_level`, `context_layers`, `quality`) raise
  `ContractError` immediately instead of silently returning empty views.
- Inputs are never mutated in place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Iterable, Mapping

import pandas as pd

from src.domain import contracts as C

if TYPE_CHECKING:
    from src.data.loaders import CaseBundle


@dataclass(frozen=True)
class FilterCriteria:
    """Immutable filter state passed from UI controls into the backend domain layer."""

    coherence_min: float | None = None
    land_uses: tuple[str, ...] | None = None
    risk_levels: tuple[str, ...] | None = None
    min_abs_velocity_mm_yr: float = 0.0
    velocity_range_mm_yr: tuple[float, float] | None = None
    date_range: tuple[str, str] | None = None
    selected_date: str | None = None
    selected_cell_id: str | None = None
    context_layers: tuple[str, ...] | None = None
    qualities: tuple[str, ...] | None = None


@dataclass(frozen=True)
class FilterOptions:
    """Widget bounds and valid choices derived from a loaded `CaseBundle`."""

    case_id: str
    sensor: str
    product: str
    revisit_days: int
    sensor_coherence_min: float
    coherence_bounds: tuple[float, float]
    observed_coherence_bounds: tuple[float, float]
    velocity_bounds_mm_yr: tuple[float, float]
    max_abs_velocity_mm_yr: float
    available_dates: tuple[str, ...]
    observed_land_uses: tuple[str, ...]
    all_land_uses: tuple[str, ...]
    available_risk_levels: tuple[str, ...]
    observed_context_layers: tuple[str, ...]
    all_context_layers: tuple[str, ...]
    cell_series_available: bool
    emitted_series_cells_count: int


@dataclass(frozen=True)
class FilteredCaseView:
    """Result of applying `FilterCriteria` to a `CaseBundle`."""

    case_id: str
    criteria: FilterCriteria
    grid: pd.DataFrame
    total_grid_cells: int
    timeseries_aoi: pd.DataFrame
    timeseries_selected: pd.DataFrame
    selected_series_cell_id: str
    is_selected_series_fallback: bool
    context: dict[str, Any]
    meta: Mapping[str, Any]
    active_date: str | None


def _validate_subset(
    values: Iterable[str] | None,
    allowed: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalised = tuple(str(v) for v in values)
    unknown = set(normalised) - set(allowed)
    if unknown:
        raise C.ContractError(
            f"Invalid filter value(s) for {field_name}: {sorted(unknown)}; allowed {list(allowed)}"
        )
    return normalised


def normalise_criteria(
    criteria: FilterCriteria,
    *,
    meta: Mapping[str, Any] | None = None,
) -> FilterCriteria:
    """Validate enum selections, clamp coherence bounds and resolve default dates/thresholds."""
    land_uses = _validate_subset(criteria.land_uses, C.LAND_USE, "land_uses")
    risk_levels = _validate_subset(criteria.risk_levels, C.RISK_LEVELS, "risk_levels")
    context_layers = _validate_subset(
        criteria.context_layers, C.CONTEXT_LAYERS, "context_layers"
    )
    qualities = _validate_subset(criteria.qualities, C.QUALITY, "qualities")

    coh_min = criteria.coherence_min
    if coh_min is None and isinstance(meta, Mapping):
        thresholds = meta.get("thresholds")
        if isinstance(thresholds, Mapping) and thresholds.get("coherence_min") is not None:
            coh_min = float(thresholds["coherence_min"])
    if coh_min is not None:
        if not math.isfinite(coh_min):
            raise C.ContractError(f"coherence_min must be finite, got {coh_min!r}")
        coh_min = max(0.0, min(1.0, float(coh_min)))

    min_abs_vel = float(criteria.min_abs_velocity_mm_yr)
    if not math.isfinite(min_abs_vel) or min_abs_vel < 0.0:
        raise C.ContractError(
            f"min_abs_velocity_mm_yr must be finite and >= 0, got {criteria.min_abs_velocity_mm_yr!r}"
        )

    vel_range = criteria.velocity_range_mm_yr
    if vel_range is not None:
        lo, hi = float(vel_range[0]), float(vel_range[1])
        if not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi:
            raise C.ContractError(
                f"velocity_range_mm_yr must satisfy min <= max, got {vel_range!r}"
            )
        vel_range = (lo, hi)

    meta_dates: tuple[str, ...] = ()
    if isinstance(meta, Mapping) and isinstance(meta.get("dates"), (list, tuple)):
        meta_dates = tuple(str(d) for d in meta["dates"])

    date_range = criteria.date_range
    if date_range is not None:
        d_start, d_end = str(date_range[0]), str(date_range[1])
        if d_start > d_end:
            raise C.ContractError(
                f"date_range start {d_start!r} is after end {d_end!r}"
            )
        date_range = (d_start, d_end)
    elif meta_dates:
        date_range = (meta_dates[0], meta_dates[-1])

    selected_date = criteria.selected_date
    if selected_date is not None:
        selected_date = str(selected_date)
        if date_range is not None and not (date_range[0] <= selected_date <= date_range[1]):
            # Clamp selected_date inside active date_range so slider adjustments never break state.
            selected_date = min(max(selected_date, date_range[0]), date_range[1])
    elif date_range is not None:
        selected_date = date_range[1]
    elif meta_dates:
        selected_date = meta_dates[-1]

    selected_cell_id = (
        str(criteria.selected_cell_id).strip() if criteria.selected_cell_id else None
    )

    return replace(
        criteria,
        coherence_min=coh_min,
        land_uses=land_uses,
        risk_levels=risk_levels,
        min_abs_velocity_mm_yr=min_abs_vel,
        velocity_range_mm_yr=vel_range,
        date_range=date_range,
        selected_date=selected_date,
        selected_cell_id=selected_cell_id or None,
        context_layers=context_layers,
        qualities=qualities,
    )


def default_filter_criteria(
    meta_or_bundle: Mapping[str, Any] | CaseBundle,
    *,
    enforce_sensor_coherence: bool = True,
) -> FilterCriteria:
    """Build initial `FilterCriteria` seeded from the case's frozen `(sensor, product)` profile."""
    meta = (
        meta_or_bundle
        if isinstance(meta_or_bundle, Mapping)
        else meta_or_bundle.meta
    )
    sensor = str(meta["sensor"])
    product = str(meta["product"])
    profile = C.sensor_profile(sensor, product)
    coh_min = profile.coherence_min if enforce_sensor_coherence else 0.0
    dates = tuple(str(d) for d in meta.get("dates", ()))
    date_range = (dates[0], dates[-1]) if dates else None
    selected_date = dates[-1] if dates else None

    return FilterCriteria(
        coherence_min=coh_min,
        land_uses=None,
        risk_levels=None,
        min_abs_velocity_mm_yr=0.0,
        velocity_range_mm_yr=None,
        date_range=date_range,
        selected_date=selected_date,
        selected_cell_id=None,
        context_layers=None,
        qualities=None,
    )


def available_filter_options(bundle: CaseBundle) -> FilterOptions:
    """Compute UI widget bounds and observed categories from a validated `CaseBundle`."""
    meta = bundle.meta
    profile = C.sensor_profile(bundle.sensor, bundle.product)
    grid = bundle.grid
    ts = bundle.timeseries

    if grid.empty:
        obs_coh = (0.0, 1.0)
        vel_bounds = (-50.0, 50.0)
        max_abs_vel = 50.0
        obs_land_uses: tuple[str, ...] = ()
    else:
        coh_min_obs = float(grid["coherence"].min())
        coh_max_obs = float(grid["coherence"].max())
        obs_coh = (coh_min_obs, coh_max_obs)
        v_min = float(grid["velocity_mm_yr"].min())
        v_max = float(grid["velocity_mm_yr"].max())
        vel_bounds = (v_min, v_max)
        max_abs_vel = float(grid["velocity_mm_yr"].abs().max())
        present_lu = set(grid["land_use"].astype(str).unique())
        obs_land_uses = tuple(lu for lu in C.LAND_USE if lu in present_lu)

    features = bundle.context.get("features") or []
    present_layers = {
        str((feat.get("properties") or {}).get("layer", "")) for feat in features if isinstance(feat, Mapping)
    }
    obs_ctx_layers = tuple(layer for layer in C.CONTEXT_LAYERS if layer in present_layers)

    non_aoi_cells = (
        set(ts["cell_id"].astype(str).unique()) - {"aoi"}
        if not ts.empty and "cell_id" in ts.columns
        else set()
    )

    return FilterOptions(
        case_id=bundle.case_id,
        sensor=bundle.sensor,
        product=bundle.product,
        revisit_days=int(meta["revisit_days"]),
        sensor_coherence_min=profile.coherence_min,
        coherence_bounds=(0.0, 1.0),
        observed_coherence_bounds=obs_coh,
        velocity_bounds_mm_yr=vel_bounds,
        max_abs_velocity_mm_yr=max_abs_vel,
        available_dates=bundle.dates,
        observed_land_uses=obs_land_uses,
        all_land_uses=C.LAND_USE,
        available_risk_levels=C.RISK_LEVELS,
        observed_context_layers=obs_ctx_layers,
        all_context_layers=C.CONTEXT_LAYERS,
        cell_series_available=bool(non_aoi_cells),
        emitted_series_cells_count=len(non_aoi_cells),
    )


def filter_grid(
    grid: pd.DataFrame,
    criteria: FilterCriteria,
    *,
    meta: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Filter `nisar_grid.parquet` rows without mutating `grid`."""
    norm = normalise_criteria(criteria, meta=meta)
    if grid.empty:
        return grid.copy()

    mask = pd.Series(True, index=grid.index)

    if norm.coherence_min is not None and norm.coherence_min > 0.0:
        mask &= grid["coherence"] >= norm.coherence_min

    if norm.land_uses is not None:
        mask &= grid["land_use"].isin(norm.land_uses)

    if norm.risk_levels is not None:
        mask &= grid["risk_level"].isin(norm.risk_levels)

    if norm.min_abs_velocity_mm_yr > 0.0:
        mask &= grid["velocity_mm_yr"].abs() >= norm.min_abs_velocity_mm_yr

    if norm.velocity_range_mm_yr is not None:
        lo, hi = norm.velocity_range_mm_yr
        mask &= grid["velocity_mm_yr"].between(lo, hi, inclusive="both")

    return grid.loc[mask].reset_index(drop=True)


def filter_timeseries(
    timeseries: pd.DataFrame,
    criteria: FilterCriteria,
    *,
    cell_id: str = "aoi",
    meta: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Filter `timeseries.csv` for a target `cell_id` (`'aoi'` or H3 cell) and date/quality window."""
    norm = normalise_criteria(criteria, meta=meta)
    if timeseries.empty:
        return timeseries.copy()

    mask = timeseries["cell_id"].astype(str) == str(cell_id)

    if norm.date_range is not None:
        d_start, d_end = norm.date_range
        dates_str = timeseries["date"].astype(str)
        mask &= (dates_str >= d_start) & (dates_str <= d_end)

    if norm.qualities is not None:
        mask &= timeseries["quality"].isin(norm.qualities)

    filtered = timeseries.loc[mask].copy()
    if not filtered.empty:
        filtered = filtered.sort_values("date", kind="mergesort").reset_index(drop=True)
    return filtered


def filter_context_layers(
    context: Mapping[str, Any],
    criteria: FilterCriteria,
    *,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Filter GeoJSON `FeatureCollection` by `criteria.context_layers` without mutating input."""
    norm = normalise_criteria(criteria, meta=meta)
    raw_features = context.get("features") or []
    if norm.context_layers is None:
        selected_features = list(raw_features)
    else:
        allowed = set(norm.context_layers)
        selected_features = [
            feat
            for feat in raw_features
            if ((feat or {}).get("properties") or {}).get("layer") in allowed
        ]

    return {
        "type": "FeatureCollection",
        "features": selected_features,
    }


def apply_filters(
    bundle: CaseBundle,
    criteria: FilterCriteria | None = None,
) -> FilteredCaseView:
    """Apply `criteria` across grid, AOI series, selected-cell series and GIS context layers."""
    raw_criteria = criteria if criteria is not None else default_filter_criteria(bundle)
    norm = normalise_criteria(raw_criteria, meta=bundle.meta)

    filtered_grid = filter_grid(bundle.grid, norm, meta=bundle.meta)
    ts_aoi = filter_timeseries(bundle.timeseries, norm, cell_id="aoi", meta=bundle.meta)

    requested_cell = norm.selected_cell_id or "aoi"
    if requested_cell == "aoi":
        ts_selected = ts_aoi
        resolved_cell = "aoi"
        is_fallback = False
    else:
        candidate_ts = filter_timeseries(
            bundle.timeseries, norm, cell_id=requested_cell, meta=bundle.meta
        )
        if not candidate_ts.empty:
            ts_selected = candidate_ts
            resolved_cell = requested_cell
            is_fallback = False
        else:
            # Per contract §4, per-cell rows are optional to stay within 15 MiB; report fallback honestly.
            ts_selected = ts_aoi
            resolved_cell = "aoi"
            is_fallback = True

    filtered_ctx = filter_context_layers(bundle.context, norm, meta=bundle.meta)

    return FilteredCaseView(
        case_id=bundle.case_id,
        criteria=norm,
        grid=filtered_grid,
        total_grid_cells=len(bundle.grid),
        timeseries_aoi=ts_aoi,
        timeseries_selected=ts_selected,
        selected_series_cell_id=resolved_cell,
        is_selected_series_fallback=is_fallback,
        context=filtered_ctx,
        meta=bundle.meta,
        active_date=norm.selected_date,
    )


__all__ = [
    "FilterCriteria",
    "FilterOptions",
    "FilteredCaseView",
    "apply_filters",
    "available_filter_options",
    "default_filter_criteria",
    "filter_context_layers",
    "filter_grid",
    "filter_timeseries",
    "normalise_criteria",
]
