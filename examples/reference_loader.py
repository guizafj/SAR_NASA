#!/usr/bin/env python3
"""Reference loader — ``docs/handoff-mock.md`` §1–§4 as working, copyable code.

Copy this file to ``src/data/loaders.py`` (or import it from ``examples/``) when you build the
Streamlit app. It is the only place that turns ``data/processed/{case}/`` into the objects the UI
binds to, and it deliberately knows nothing about drawing:

.. code-block:: python

    import sys; sys.path.insert(0, "examples")
    from reference_loader import load_case, map_payload, aoi_series, kpis

    case = load_case("mexico-city")
    rows = map_payload(case, coherence_min=0.6, land_use=["urban-dense", "urban-residential"])
    dates, values = aoi_series(case)      # chart: mm, not mm/yr
    cards = kpis(case)                    # median (KPI) != mean x T (chart); both correct

Rules this module implements, all taken from the handoff — nothing here is invented:

* **Never recompute science.** The colour is ``velocity_mm_yr`` *as stored*, the height is
  ``delta_change`` *as stored*, and the chart is the ``cell_id == "aoi"`` rows *as stored*. No
  derivative, no smoothing, no interpolation, no unit conversion.
* **Nodata is absent, never zero.** Filters only remove rows; a filter that matches nothing raises
  ``LoaderError`` naming the filter and the case instead of returning an empty payload.
* **Fail closed at load.** ``load_case`` asserts the invariants the handoff promises and raises
  ``LoaderError`` naming the case and the field. A tampered artefact never reaches the UI.
* **No UI imports.** Nothing here imports Streamlit, pydeck, plotly, geopandas or h3. Field names,
  dtypes, enums and thresholds come from the frozen contract, never re-declared locally.

The contract module is the source of truth for the schema (``docs/data-contract-v1.md`` §3–§7);
this file only encodes the *bindings* (which stored column feeds which visual) and the load-time
invariants.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, NoReturn

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # running from anywhere: `examples/` is not a package
    sys.path.insert(0, str(ROOT))

from src.domain import contracts as C  # noqa: E402

__all__ = ["Case", "LoaderError", "aoi_series", "grid_frame", "kpis", "load_case", "map_payload"]

#: The four artefacts that make one case (handoff §1). Filenames are layout, not schema.
ARTEFACTS: tuple[str, ...] = (
    "nisar_grid.parquet",
    "timeseries.csv",
    "context_layers.geojson",
    "meta.json",
)

#: Handoff §2, "Map bindings": visual -> stored column. The loader reads these, never derives them.
COLOR_COLUMN = "velocity_mm_yr"
HEIGHT_COLUMN = "delta_change"
CATEGORY_COLUMN = "land_use"
RISK_COLUMN = "risk_level"

#: Exactly the seven keys a map row may carry (handoff §2). No extras, no omissions.
MAP_ROW_KEYS: tuple[str, ...] = (
    "lat",
    "lon",
    "color_value",
    "height",
    "category",
    "risk",
    "cell_id",
)

#: Numeric / text columns, derived from the frozen field list so they cannot drift.
FLOAT_COLUMNS: tuple[str, ...] = tuple(f.name for f in C.GRID_FIELDS if f.dtype == "float64")
STRING_COLUMNS: tuple[str, ...] = tuple(f.name for f in C.GRID_FIELDS if f.dtype == "string")

#: Tolerances stated by the contract (§3: delta_change arithmetic, tolerance 1e-6).
DELTA_TOLERANCE_MM = 1e-6


class LoaderError(RuntimeError):
    """An artefact broke the frozen contract, or a filter matched nothing.

    The message always names the case and the offending field/filter, so a bug surfaces as a
    readable error in the app instead of an empty map or a silently wrong number.
    """


@dataclass(frozen=True, eq=False)
class Case:
    """One case, exactly as published: the four artefacts, typed, nothing derived.

    ``grid`` holds one row per published cell, ``series`` holds the ``cell_id == "aoi"``
    aggregate (plus per-cell rows when the artefact was generated with ``--per-cell-series``),
    and ``meta``/``context`` are the decoded JSON documents. Use the module functions rather than
    reaching into these frames from a component.
    """

    case_id: str
    grid: pd.DataFrame = field(repr=False)
    series: pd.DataFrame = field(repr=False)
    meta: Mapping[str, Any] = field(repr=False)
    context: Mapping[str, Any] = field(repr=False)
    path: pathlib.Path


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------


def load_case(case_id: str, processed: str | pathlib.Path = "data/processed") -> Case:
    """Load one case and assert every invariant the handoff promises.

    ``processed`` is resolved against the repository root when relative, so the call works from the
    app, from ``tests/`` and from a notebook without a chdir. Pass an absolute path for anything
    else (the tests use a temporary copy to prove tampered artefacts are refused).

    Raises :class:`LoaderError` — never asserts, never warns-and-continues — when the directory or
    an artefact is missing, a required column is absent, an enum value is undeclared, the
    ``delta_change``/``val_t1``/coherence/risk/provenance invariants do not hold, or the frozen
    validators report a problem.
    """
    if not isinstance(case_id, str) or not case_id or pathlib.PurePath(case_id).name != case_id:
        raise LoaderError(
            f"{case_id!r}: case_id: expected a bare slug such as 'mexico-city', not a path"
        )

    directory = _resolve(processed) / case_id
    if not directory.is_dir():
        raise LoaderError(
            f"{case_id}: case directory not found at {directory}. Cases are directories under "
            f"data/processed/ carrying {list(ARTEFACTS)}; `_availability/` is not a case."
        )
    missing = [name for name in ARTEFACTS if not (directory / name).is_file()]
    if missing:
        raise LoaderError(
            f"{case_id}: missing artefact(s) {missing} in {directory}; regenerate the case with "
            f"./scripts/python.sh pipeline/generate_mock.py"
        )

    try:
        grid = pd.read_parquet(directory / "nisar_grid.parquet")
    except Exception as exc:  # noqa: BLE001 - surface the file and the cause, never a bare traceback
        raise LoaderError(f"{case_id}: nisar_grid.parquet: cannot read parquet: {exc}") from exc
    try:
        series = pd.read_csv(directory / "timeseries.csv")
    except Exception as exc:  # noqa: BLE001
        raise LoaderError(f"{case_id}: timeseries.csv: cannot read CSV: {exc}") from exc
    try:
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        context = json.loads((directory / "context_layers.geojson").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoaderError(f"{case_id}: meta.json/context_layers.geojson: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise LoaderError(f"{case_id}: cannot read JSON artefact: {exc}") from exc

    _check_artefacts(case_id, grid, series, meta, context)
    return Case(case_id=case_id, grid=grid, series=series, meta=meta, context=context, path=directory)


def grid_frame(case: Case) -> pd.DataFrame:
    """The published grid as a fresh DataFrame, with the documented dtypes asserted.

    Handoff §2: the numeric columns are ``float64`` and the text columns are strings (pandas 3
    reports ``str``, pandas 2 ``object`` — the *string* representation is asserted, not the dtype
    object). A caller may mutate the returned copy; the ``Case`` stays untouched.
    """
    frame = case.grid.copy()
    _assert_grid_dtypes(case.case_id, frame)
    return frame


def map_payload(
    case: Case,
    *,
    coherence_min: float | None = None,
    land_use: str | Iterable[str] | None = None,
    risk_level: str | Iterable[str] | None = None,
) -> list[dict]:
    """One dict per *published* cell, carrying exactly the seven map keys.

    Bindings (handoff §2): ``color_value <- velocity_mm_yr``, ``height <- delta_change``,
    ``category <- land_use``, ``risk <- risk_level``, plus ``lat``, ``lon`` and ``cell_id``. The
    values are the stored floats; nothing is recomputed and nothing is rounded.

    Filters are ANDed and may only *remove* rows — nodata is absent, never zero. A filter that
    removes every cell raises :class:`LoaderError` naming the filter and the case; it never returns
    ``[]``, because an empty map is indistinguishable from a broken loader.
    """
    frame = case.grid
    _assert_grid_dtypes(case.case_id, frame)
    selected, described = _apply_filters(
        case.case_id,
        frame,
        coherence_min=coherence_min,
        land_use=land_use,
        risk_level=risk_level,
    )
    if selected.empty:
        summary = ", ".join(described) or "filters"
        raise LoaderError(
            f"{case.case_id}: filter: {summary} matched none of the {len(frame)} published cells. "
            f"Nodata is absent, not zero, so there is nothing to draw; widen the filter or check "
            f"the requested values against the artefact."
        )

    rows: list[dict] = []
    for record in selected.itertuples(index=False):
        rows.append(
            {
                "lat": float(record.lat),
                "lon": float(record.lon),
                "color_value": float(getattr(record, COLOR_COLUMN)),
                "height": float(getattr(record, HEIGHT_COLUMN)),
                "category": str(getattr(record, CATEGORY_COLUMN)),
                "risk": str(getattr(record, RISK_COLUMN)),
                "cell_id": str(record.cell_id),
            }
        )
    return rows


def aoi_series(case: Case, *, chart_unit: str = "mm") -> tuple[list[str], list[float]]:
    """The case aggregate series for the chart: ``(dates, values_in_mm)``, as stored.

    Reads the ``cell_id == "aoi"`` rows (contract §4) in ``meta.dates`` order. ``chart_unit`` exists
    to make the unit explicit at the call site; only ``"mm"`` is accepted because the loader never
    converts units — convert at display time, in the component, if the axis needs another label.
    """
    unit = str(chart_unit)
    if unit != C.UNIT:
        raise LoaderError(
            f"{case.case_id}: chart_unit: got {unit!r}, only {C.UNIT!r} is published in the "
            f"artefact. The loader never converts units; converting here would silently mix the "
            f"chart (mm) with the map colour (mm/year)."
        )

    dates = [str(value) for value in case.meta["dates"]]
    frame = case.series
    aoi = frame.loc[frame["cell_id"].astype(str) == "aoi", ["date", "value"]].copy()
    if aoi.empty:
        raise LoaderError(
            f"{case.case_id}: timeseries.cell_id: no 'aoi' rows; the case aggregate is mandatory "
            f"(contract §4). Generate it with `--per-cell-series` off (the default)."
        )
    aoi["date"] = aoi["date"].astype(str)
    if aoi["date"].duplicated().any():
        raise LoaderError(
            f"{case.case_id}: timeseries.date: the 'aoi' series has duplicate dates; one row per "
            f"date per case is the contract"
        )
    missing = [value for value in dates if value not in set(aoi["date"])]
    if missing:
        raise LoaderError(
            f"{case.case_id}: timeseries.date: the 'aoi' series is missing date(s) {missing}. "
            f"A missing date is a missing row, never an interpolated value (handoff §3)."
        )

    ordered = aoi.set_index("date").loc[dates]
    return dates, [float(value) for value in ordered["value"]]


def kpis(case: Case) -> dict[str, Any]:
    """KPI-card values, read from ``meta.json`` — never re-derived from the grid.

    Keys: ``cells``, ``median_velocity_mm_yr``, ``p05``, ``p95``, ``spread``, ``dates``,
    ``window_days``, ``coverage_fraction`` (only when ``meta.stats`` publishes it), ``product``,
    ``provenance_id``.

    ``spread`` is ``p95 - p05`` from the *published* percentiles (the handoff's legend table defines
    it that way) and ``window_days`` is calendar arithmetic on ``meta.dates``; neither touches a
    geospatial quantity. The KPI median and the chart's mean x T are different aggregations and both
    are correct (handoff §3c): this function never reconciles them.
    """
    meta = case.meta
    stats = meta["stats"]
    p05 = float(stats["p05_velocity_mm_yr"])
    p95 = float(stats["p95_velocity_mm_yr"])
    dates = [str(value) for value in meta["dates"]]

    result: dict[str, Any] = {
        "cells": int(stats["n_cells"]),
        "median_velocity_mm_yr": float(stats["median_velocity_mm_yr"]),
        "p05": p05,
        "p95": p95,
        "spread": float(stats.get("spread_mm_yr", p95 - p05)),
        "dates": dates,
        "window_days": _window_days(dates),
    }
    if "coverage_fraction" in stats:  # real pipeline cases publish it; the mock does not
        result["coverage_fraction"] = float(stats["coverage_fraction"])
    result["product"] = str(meta["product"])
    result["provenance_id"] = _provenance_id(case)
    return result


# ---------------------------------------------------------------------------------------------
# Load-time invariants. Each one is a promise in docs/handoff-mock.md or docs/data-contract-v1.md.
# ---------------------------------------------------------------------------------------------


def _check_artefacts(
    case_id: str,
    grid: pd.DataFrame,
    series: pd.DataFrame,
    meta: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    _check_columns(case_id, "grid", grid, C.GRID_FIELDS)
    _check_columns(case_id, "timeseries", series, C.TIMESERIES_FIELDS)
    _assert_grid_dtypes(case_id, grid)
    _assert_series_dtypes(case_id, series)
    _check_meta_identity(case_id, meta)
    sensor, product, profile = _check_case_and_profile(case_id, grid, series, meta)
    _check_enums(case_id, "grid", grid, C.GRID_FIELDS)
    _check_enums(case_id, "timeseries", series, C.TIMESERIES_FIELDS)
    _check_grid_values(case_id, grid, meta, profile)
    _check_series_values(case_id, series, meta)
    _check_context(case_id, context)
    _check_provenance(case_id, grid, series, meta, context)
    _check_stats(case_id, grid, meta)
    _check_contract_gate(case_id, grid, series, meta, context)


def _check_columns(case_id: str, artefact: str, frame: pd.DataFrame, fields: Iterable[Any]) -> None:
    declared = {f.name for f in fields}
    required = [f.name for f in fields if f.required]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        _fail(case_id, artefact, f"missing required column(s) {missing} (contract §3/§4)")
    undeclared = [str(name) for name in frame.columns if str(name) not in declared]
    if undeclared:
        _fail(
            case_id,
            artefact,
            f"undeclared column(s) {undeclared}; adding one is a contract change, not a loader "
            f"one (bump SCHEMA_VERSION in src/domain/contracts.py)",
        )


def _assert_grid_dtypes(case_id: str, frame: pd.DataFrame) -> None:
    for column in FLOAT_COLUMNS:
        if column not in frame.columns:
            _fail(case_id, column, "missing from the grid")
        actual = str(frame[column].dtype)
        if actual != "float64":
            _fail(
                case_id,
                column,
                f"dtype is {actual!r}, the contract says float64 (handoff §2: do not coerce in "
                f"the UI — fix the artefact)",
            )
    for column in STRING_COLUMNS:
        if column not in frame.columns:
            _fail(case_id, column, "missing from the grid")
        actual = str(frame[column].dtype)
        # pandas 3 reports `str`, pandas 2 `object`, either is documented; a numeric dtype is not.
        if actual not in {"str", "string", "object"}:
            _fail(case_id, column, f"dtype is {actual!r}, the handoff says string (str/object)")


def _assert_series_dtypes(case_id: str, frame: pd.DataFrame) -> None:
    for field_ in C.TIMESERIES_FIELDS:
        if field_.name not in frame.columns:
            continue  # reported by _check_columns
        actual = str(frame[field_.name].dtype)
        if field_.dtype == "float64" and actual != "float64":
            _fail(case_id, f"timeseries.{field_.name}", f"dtype is {actual!r}, expected float64")
        if field_.dtype == "string" and actual not in {"str", "string", "object"}:
            _fail(case_id, f"timeseries.{field_.name}", f"dtype is {actual!r}, expected a string")


def _check_meta_identity(case_id: str, meta: Mapping[str, Any]) -> None:
    if str(meta.get("case_id")) != case_id:
        _fail(
            case_id,
            "meta.case_id",
            f"is {meta.get('case_id')!r}; the directory, the grid and meta.json must agree",
        )
    if meta.get("schema_version") != C.SCHEMA_VERSION:
        _fail(
            case_id,
            "meta.schema_version",
            f"is {meta.get('schema_version')!r}, contract is {C.SCHEMA_VERSION!r}",
        )


def _check_case_and_profile(
    case_id: str,
    grid: pd.DataFrame,
    series: pd.DataFrame,
    meta: Mapping[str, Any],
) -> tuple[str, str, C.SensorProfile]:
    if _unique(grid, "case_id") != {case_id}:
        _fail(case_id, "case_id", f"grid values {sorted(_unique(grid, 'case_id'))} do not match")
    sensors = _unique(grid, "sensor")
    products = _unique(grid, "product")
    if len(sensors) != 1:
        _fail(case_id, "sensor", f"expected one value per file, found {sorted(sensors)}")
    if len(products) != 1:
        _fail(case_id, "product", f"expected one value per file, found {sorted(products)}")
    sensor, product = next(iter(sensors)), next(iter(products))
    try:
        profile = C.sensor_profile(sensor, product)
    except C.ContractError as exc:
        _fail(case_id, "sensor/product", str(exc))

    if str(meta.get("sensor")) != sensor:
        _fail(case_id, "meta.sensor", f"is {meta.get('sensor')!r}, grid says {sensor!r}")
    if str(meta.get("product")) != product:
        _fail(case_id, "meta.product", f"is {meta.get('product')!r}, grid says {product!r}")

    for column, expected in (("case_id", case_id), ("sensor", sensor), ("product", product)):
        observed = _unique(series, column)
        if observed != {expected}:
            _fail(
                case_id,
                f"timeseries.{column}",
                f"values {sorted(observed)} do not match the grid ({expected!r})",
            )
    return sensor, product, profile


def _check_enums(case_id: str, artefact: str, frame: pd.DataFrame, fields: Iterable[Any]) -> None:
    for field_ in fields:
        if field_.enum is None or field_.name not in frame.columns:
            continue
        observed = {str(value) for value in frame[field_.name].dropna().unique().tolist()}
        unknown = sorted(observed - set(field_.enum))
        if unknown:
            _fail(
                case_id,
                f"{artefact}.{field_.name}",
                f"undeclared value(s) {unknown}; allowed {list(field_.enum)} (contract §7). "
                f"An unknown label is a schema drift, not something the UI may colour.",
            )


def _check_grid_values(
    case_id: str,
    grid: pd.DataFrame,
    meta: Mapping[str, Any],
    profile: C.SensorProfile,
) -> None:
    for column in FLOAT_COLUMNS:
        if column not in grid.columns:
            continue
        nulls = int(grid[column].isna().sum())
        if nulls:
            _fail(
                case_id,
                column,
                f"{nulls} null(s); nodata is absent, never written as 0 or NaN (handoff §3.1)",
            )
        if not np.isfinite(grid[column].to_numpy(dtype=float)).all():
            _fail(case_id, column, "contains non-finite value(s)")

    for column, low, high in (("lat", -90.0, 90.0), ("lon", -180.0, 180.0)):
        values = grid[column]
        if not values.between(low, high).all():
            _fail(case_id, column, f"outside [{low:g}, {high:g}] degrees")

    # Handoff §3.2: val_t1 is exactly 0 by construction (cumulative, referenced to the first date).
    nonzero = int((grid["val_t1"] != 0).sum())
    if nonzero:
        _fail(
            case_id,
            "val_t1",
            f"{nonzero} cell(s) with val_t1 != 0. Cumulative displacement is referenced to the "
            f"first acquisition, so val_t1 is 0 by construction — do not re-add an offset.",
        )

    # Contract §3: delta_change == val_t2 - val_t1 (tolerance 1e-6).
    residual = (grid["val_t2"] - grid["val_t1"] - grid["delta_change"]).abs()
    broken = int((residual > DELTA_TOLERANCE_MM).sum())
    if broken:
        _fail(
            case_id,
            "delta_change",
            f"{broken} row(s) where delta_change != val_t2 - val_t1 "
            f"(max residual {float(residual.max()):.6g} mm). delta_change is cumulative mm; "
            f"velocity_mm_yr is the rate — never derive one from the other in the UI.",
        )

    # Handoff §1: every published cell satisfies coherence >= thresholds.coherence_min.
    coherence = grid["coherence"]
    below = int((coherence < profile.coherence_min).sum())
    if below:
        _fail(
            case_id,
            "coherence",
            f"{below} cell(s) below the {profile.product!r} profile coherence_min="
            f"{profile.coherence_min}: they should be absent, not published",
        )
    above = int((coherence > 1.0).sum())
    if above:
        _fail(case_id, "coherence", f"{above} cell(s) above 1.0; coherence is a 0–1 quantity")

    # Contract §7.2: risk_level is risk_level_from_velocity(velocity_mm_yr), sign-agnostic.
    expected_risk = grid["velocity_mm_yr"].map(C.risk_level_from_velocity)
    wrong_risk = int((expected_risk.astype(str) != grid["risk_level"].astype(str)).sum())
    if wrong_risk:
        _fail(
            case_id,
            "risk_level",
            f"{wrong_risk} cell(s) disagree with risk_level_from_velocity on the stored velocity "
            f"(bands: |v| < 5 estable, 5–25 moderado, >= 25 critico)",
        )

    if grid["cell_id"].duplicated().any():
        _fail(case_id, "cell_id", "duplicate H3 index; one row per cell per case")
    if (grid["cell_id"].astype(str).str.len() == 0).any():
        _fail(case_id, "cell_id", "empty value; every row needs an H3 index")

    # Handoff §2: acquisition_date is the last date, constant per file (optional field).
    if "acquisition_date" in grid.columns and grid["acquisition_date"].notna().any():
        observed = _unique(grid.loc[grid["acquisition_date"].notna()], "acquisition_date")
        expected = str(meta["dates"][-1])
        if observed != {expected}:
            _fail(
                case_id,
                "acquisition_date",
                f"values {sorted(observed)}; expected the constant last date {expected!r}",
            )


def _check_series_values(case_id: str, series: pd.DataFrame, meta: Mapping[str, Any]) -> None:
    values = series["value"]
    if values.isna().any():
        _fail(case_id, "timeseries.value", "contains null(s); a missing date is a missing row")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        _fail(case_id, "timeseries.value", "contains non-finite value(s)")

    dates = series["date"].astype(str)
    if not dates.str.match(r"^\d{4}-\d{2}-\d{2}$").all():
        _fail(case_id, "timeseries.date", "every date must be ISO YYYY-MM-DD")
    published = [str(value) for value in meta["dates"]]
    stray = sorted(set(dates.unique()) - set(published))
    if stray:
        _fail(case_id, "timeseries.date", f"date(s) {stray} not in meta.dates")

    aoi = series.loc[series["cell_id"].astype(str) == "aoi"]
    if aoi.empty:
        _fail(
            case_id,
            "timeseries.cell_id",
            "no 'aoi' rows; the case aggregate series is mandatory (contract §4)",
        )
    if aoi["date"].astype(str).duplicated().any():
        _fail(case_id, "timeseries.date", "the 'aoi' series has duplicate dates")
    missing = sorted(set(published) - set(aoi["date"].astype(str)))
    if missing:
        _fail(
            case_id,
            "timeseries.date",
            f"the 'aoi' series is missing date(s) {missing}; a gap is a missing row, never a "
            f"sentinel or an interpolated value",
        )


def _check_context(case_id: str, context: Mapping[str, Any]) -> None:
    problems = C.validate_context_layers(context)
    if problems:
        _fail(case_id, "context_layers.geojson", "; ".join(problems[:3]))
    features = context.get("features") or []
    wrong = sorted(
        {
            str((feature.get("properties") or {}).get("case_id"))
            for feature in features
            if str((feature.get("properties") or {}).get("case_id")) != case_id
        }
    )
    if wrong:
        _fail(case_id, "context_layers.geojson", f"feature case_id value(s) {wrong} do not match")


def _check_provenance(
    case_id: str,
    grid: pd.DataFrame,
    series: pd.DataFrame,
    meta: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    identifiers: set[str] = set()
    identifiers |= _unique(grid, "provenance_id")
    identifiers |= _unique(series, "provenance_id")
    for feature in context.get("features") or []:
        identifiers.add(str((feature.get("properties") or {}).get("provenance_id")))
    meta_id = (meta.get("provenance") or {}).get("provenance_id")
    if meta_id is not None:
        identifiers.add(str(meta_id))

    if not identifiers:
        _fail(case_id, "provenance_id", "no provenance id in grid, series, context or meta")
    malformed = sorted(value for value in identifiers if not C.PROVENANCE_ID_RE.match(value))
    if malformed:
        _fail(
            case_id,
            "provenance_id",
            f"value(s) {malformed} do not match {C.PROVENANCE_ID_RE.pattern}",
        )
    if len(identifiers) > 1:
        _fail(
            case_id,
            "provenance_id",
            f"artefacts disagree: {sorted(identifiers)}; contract §8 requires the same id in the "
            f"grid, the series, the context and meta.provenance",
        )

    if str(meta.get("product")) == "mock":
        source = str((meta.get("provenance") or {}).get("source", ""))
        if "mock" not in source.lower():
            _fail(
                case_id,
                "provenance.source",
                f"product is 'mock' but provenance.source={source!r} does not say so. The badge is "
                f"not decoration: a mock must never be mistaken for a measurement (handoff §3.3).",
            )


def _check_stats(case_id: str, grid: pd.DataFrame, meta: Mapping[str, Any]) -> None:
    stats = meta.get("stats") or {}
    n_cells = stats.get("n_cells")
    if isinstance(n_cells, int) and n_cells != len(grid):
        _fail(
            case_id,
            "meta.stats.n_cells",
            f"is {n_cells} but the grid publishes {len(grid)} row(s); trust the grid counts, so a "
            f"disagreement means one of the two artefacts is stale",
        )
    n_dates = stats.get("n_dates")
    if isinstance(n_dates, int) and n_dates != len(meta.get("dates") or []):
        _fail(
            case_id,
            "meta.stats.n_dates",
            f"is {n_dates} but meta.dates has {len(meta.get('dates') or [])} entries",
        )
    for key in ("median_velocity_mm_yr", "p05_velocity_mm_yr", "p95_velocity_mm_yr"):
        value = stats.get(key)
        if isinstance(value, (int, float)) and not np.isfinite(float(value)):
            _fail(case_id, f"meta.stats.{key}", "is not a finite number")


def _check_contract_gate(
    case_id: str,
    grid: pd.DataFrame,
    series: pd.DataFrame,
    meta: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    """Belt and braces: run the frozen validators so no rule is re-implemented incompletely."""
    report = C.validate_all(grid=grid, timeseries=series, meta=meta, context=context, case_id=case_id)
    problems = [f"{artefact}: {problem}" for artefact, items in report.items() for problem in items]
    if problems:
        shown = "; ".join(problems[:5])
        if len(problems) > 5:
            shown += f" (and {len(problems) - 5} more)"
        _fail(case_id, "contract", shown)


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def _apply_filters(
    case_id: str,
    frame: pd.DataFrame,
    *,
    coherence_min: float | None,
    land_use: str | Iterable[str] | None,
    risk_level: str | Iterable[str] | None,
) -> tuple[pd.DataFrame, list[str]]:
    mask = pd.Series(True, index=frame.index)
    described: list[str] = []

    if coherence_min is not None:
        threshold = _coherence_threshold(case_id, coherence_min)
        mask &= frame["coherence"] >= threshold
        described.append(f"coherence_min={threshold:g}")

    if land_use is not None:
        wanted = _wanted_labels(case_id, "land_use", land_use, C.LAND_USE)
        mask &= frame["land_use"].astype(str).isin(wanted)
        described.append(f"land_use={sorted(wanted)}")

    if risk_level is not None:
        wanted = _wanted_labels(case_id, "risk_level", risk_level, C.RISK_LEVELS)
        mask &= frame["risk_level"].astype(str).isin(wanted)
        described.append(f"risk_level={sorted(wanted)}")

    return frame.loc[mask], described


def _coherence_threshold(case_id: str, requested: Any) -> float:
    if isinstance(requested, bool) or not isinstance(requested, (int, float, np.number)):
        _fail(case_id, "coherence_min", f"expected a number in [0, 1], got {requested!r}")
    threshold = float(requested)
    if not np.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        _fail(case_id, "coherence_min", f"expected a number in [0, 1], got {threshold!r}")
    return threshold


def _wanted_labels(
    case_id: str, field_name: str, requested: Any, vocabulary: Iterable[str]
) -> set[str]:
    if isinstance(requested, str):
        values = [requested]
    else:
        try:
            values = list(requested)
        except TypeError:
            _fail(
                case_id,
                field_name,
                f"expected a string or an iterable of strings, got {type(requested).__name__}",
            )
    if not values:
        _fail(case_id, field_name, "empty filter list; pass None to mean 'no filter'")
    labels = {str(value) for value in values}
    unknown = sorted(labels - set(vocabulary))
    if unknown:
        _fail(
            case_id,
            field_name,
            f"undeclared value(s) {unknown}; allowed {list(vocabulary)} (contract §7)",
        )
    return labels


def _unique(frame: pd.DataFrame, column: str) -> set[str]:
    return {str(value) for value in frame[column].dropna().unique().tolist()}


def _provenance_id(case: Case) -> str:
    """The one id the contract requires to be identical across the four artefacts (§8)."""
    meta_id = (case.meta.get("provenance") or {}).get("provenance_id")
    if meta_id:
        return str(meta_id)
    return str(case.grid["provenance_id"].iloc[0])


def _window_days(dates: Iterable[str]) -> int:
    first, last = list(dates)[0], list(dates)[-1]
    start = dt.date.fromisoformat(str(first))
    end = dt.date.fromisoformat(str(last))
    return (end - start).days


def _resolve(processed: str | pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(processed)
    if not root.is_absolute():
        root = ROOT / root
    return root


def _fail(case_id: str, field_name: str, problem: str) -> NoReturn:
    raise LoaderError(f"{case_id}: {field_name}: {problem}")
