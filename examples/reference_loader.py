#!/usr/bin/env python3
"""Reference loader — ``docs/handoff-mock.md`` §1–§4 as working, copyable code.

Copy this file to ``src/data/loaders.py`` (or import it from ``examples/`` with ``examples/`` on
``sys.path`` at the call site — this module never edits ``sys.path``):

.. code-block:: python

    from reference_loader import load_case, map_payload, aoi_series, kpis

    case = load_case("mexico-city")                                  # ./data/processed, from cwd
    case = load_case("mexico-city", processed="/srv/data/processed") # or pass it explicitly
    rows = map_payload(case, coherence_min=0.6, land_use=["urban-dense", "urban-residential"])
    dates, values = aoi_series(case)      # chart: mm, not mm/yr
    cards = kpis(case)                    # median (KPI) != mean x T (chart); both correct

``processed`` defaults to ``data/processed`` under the **caller's cwd**, never ``__file__``.

* **Never recompute science.** Colour is ``velocity_mm_yr`` *as stored*, height is
  ``delta_change`` *as stored*, the chart is the ``cell_id == "aoi"`` rows *as stored*. No
  derivative, smoothing, interpolation or unit conversion.
* **Nodata is absent, never zero.** Filters only remove rows; matching nothing raises
  ``LoaderError`` naming the filter and case instead of returning an empty frame.
* **Fail closed at load.** ``src.domain.contracts`` validates the frozen schema (called, not
  re-implemented); only the loader's promises — bindings, dtypes, cross-artefact identity and the
  AOI aggregate — are checked here.
* **No UI imports.** No Streamlit, pydeck, plotly, geopandas or h3.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, NoReturn

import numpy as np
import pandas as pd

from src.domain import contracts as C

__all__ = ["Case", "LoaderError", "aoi_series", "grid_frame", "kpis", "load_case", "map_payload"]

ARTEFACTS = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")
#: Handoff §2 bindings: visual -> stored column. The loader reads these, never derives them.
COLOR_COLUMN, HEIGHT_COLUMN = "velocity_mm_yr", "delta_change"
CATEGORY_COLUMN, RISK_COLUMN = "land_use", "risk_level"
MAP_ROW_KEYS = ("lat", "lon", "color_value", "height", "category", "risk", "cell_id")  # handoff §2


class LoaderError(RuntimeError):
    """An artefact broke the frozen contract, or a filter matched nothing.

    The message names the case and the field/filter, so a bug reads as an error, not an empty map.
    """


@dataclass(frozen=True, eq=False)
class Case:
    """One case as published: the four artefacts, typed, nothing derived.

    ``grid`` is one row per published cell, ``series`` the ``cell_id == "aoi"`` aggregate (plus
    per-cell rows with ``--per-cell-series``); ``meta``/``context`` are decoded JSON.
    """

    case_id: str
    grid: pd.DataFrame = field(repr=False)
    series: pd.DataFrame = field(repr=False)
    meta: Mapping[str, Any] = field(repr=False)
    context: Mapping[str, Any] = field(repr=False)
    path: pathlib.Path


# Public API ----------------------------------------------------------------------------------


def load_case(case_id: str, processed: str | pathlib.Path | None = None) -> Case:
    """Load one case and assert every invariant the handoff promises.

    ``processed`` is the artefact root; when omitted it is ``data/processed`` under the caller's
    cwd, and relative values resolve against the cwd too — never against this file. Pass an
    absolute path for anything else (the tests use a temporary copy). Raises :class:`LoaderError`,
    never asserts, on a missing directory/artefact, an invalid column/enum or a broken invariant.
    """
    if not isinstance(case_id, str) or not case_id or pathlib.PurePath(case_id).name != case_id:
        raise LoaderError(f"{case_id!r}: case_id: expected a bare slug such as 'mexico-city', not a path")

    directory = _processed_root(processed) / case_id
    if not directory.is_dir():
        raise LoaderError(f"{case_id}: case directory not found at {directory}. Cases are directories "
                          f"under data/processed/ carrying {list(ARTEFACTS)}; `_availability/` is not a case.")
    missing = [name for name in ARTEFACTS if not (directory / name).is_file()]
    if missing:
        raise LoaderError(f"{case_id}: missing artefact(s) {missing} in {directory}; regenerate with "
                          f"./scripts/python.sh pipeline/generate_mock.py")

    try:
        grid = pd.read_parquet(directory / "nisar_grid.parquet")
    except Exception as exc:  # noqa: BLE001 - surface the file and cause, never a bare traceback
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
    if not isinstance(meta, dict) or not isinstance(context, dict):
        raise LoaderError(f"{case_id}: meta.json/context_layers.geojson: expected a JSON object at the "
                          f"top level, got {type(meta).__name__}/{type(context).__name__}")

    _validate(case_id, grid, series, meta, context)
    return Case(case_id=case_id, grid=grid, series=series, meta=meta, context=context, path=directory)


def grid_frame(case: Case) -> pd.DataFrame:
    """The published grid as a fresh DataFrame, with the documented dtypes asserted.

    Handoff §2: numeric columns are ``float64``, text columns strings (pandas 3 reports ``str``,
    pandas 2 ``object`` — the string representation is asserted, not the dtype object). Callers may
    mutate the copy; the ``Case`` stays untouched.
    """
    frame = case.grid.copy()
    _assert_dtypes(case.case_id, frame, "grid", C.GRID_FIELDS)
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
    ``category <- land_use``, ``risk <- risk_level``, plus ``lat``, ``lon``, ``cell_id``: the
    stored floats, nothing recomputed or rounded. Filters are ANDed and only *remove* rows —
    nodata is absent, never zero. A filter that empties the frame raises :class:`LoaderError`
    naming it, never ``[]``. ``coherence_min`` is inclusive; row order is the artefact's.
    """
    frame = case.grid
    _assert_dtypes(case.case_id, frame, "grid", C.GRID_FIELDS)
    selected, described = _apply_filters(
        case.case_id, frame, coherence_min=coherence_min, land_use=land_use, risk_level=risk_level
    )
    if selected.empty:
        summary = ", ".join(described) or "filters"
        raise LoaderError(
            f"{case.case_id}: filter: {summary} matched none of the {len(frame)} published cells. "
            f"Nodata is absent, not zero, so there is nothing to draw; widen the filter or check "
            f"the requested values against the artefact."
        )
    return [
        {
            "lat": float(r.lat),
            "lon": float(r.lon),
            "color_value": float(getattr(r, COLOR_COLUMN)),
            "height": float(getattr(r, HEIGHT_COLUMN)),
            "category": str(getattr(r, CATEGORY_COLUMN)),
            "risk": str(getattr(r, RISK_COLUMN)),
            "cell_id": str(r.cell_id),
        }
        for r in selected.itertuples(index=False)
    ]


def aoi_series(case: Case, *, chart_unit: str = "mm") -> tuple[list[str], list[float]]:
    """The case aggregate series for the chart: ``(dates, values_in_mm)``, as stored.

    Reads the stored ``cell_id == "aoi"`` rows (contract §4) in ``meta.dates`` order. Only
    ``"mm"`` is accepted: the chart (mm) and the map colour (mm/year) must never be mixed.
    """
    unit = str(chart_unit)
    if unit != C.UNIT:
        raise LoaderError(
            f"{case.case_id}: chart_unit: got {unit!r}, only {C.UNIT!r} is published in the "
            f"artefact; convert at display time in the component, never here."
        )
    dates = [str(value) for value in case.meta["dates"]]
    ordered = _aoi_values(case.case_id, case.series, dates)
    return dates, [float(value) for value in ordered]


def kpis(case: Case) -> dict[str, Any]:
    """KPI-card values, read from ``meta.json`` — never re-derived from the grid.

    Keys: ``cells``, ``median_velocity_mm_yr``, ``p05``, ``p95``, ``spread``, ``dates``,
    ``window_days``, ``product``, ``provenance_id``, plus ``coverage_fraction`` and
    ``detection_limit_mm_yr`` when ``meta.stats`` publishes them. The detection limit is copied (not
    inferred) so a null result such as Málaga (``|median| = 1.358 < 5 mm/yr``) renders "below the
    detection floor" instead of a missing card. ``spread`` is ``p95 - p05`` from the published
    percentiles (handoff legend); ``window_days`` is calendar arithmetic on ``meta.dates``. KPI
    median and chart mean x T are different aggregations and both are correct (handoff §3c).
    """
    meta, stats = case.meta, case.meta["stats"]
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
        "window_days": (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days,
        "product": str(meta["product"]),
        # contract §8: the same id across grid/series/context/meta; meta is authoritative
        "provenance_id": str((meta.get("provenance") or {}).get("provenance_id")
                             or case.grid["provenance_id"].iloc[0]),
    }
    for key in ("coverage_fraction", "detection_limit_mm_yr"):
        if key in stats:  # real pipeline cases publish them; the mock publishes the limit only
            result[key] = float(stats[key])
    return result


def _validate(case_id: str, grid: pd.DataFrame, series: pd.DataFrame, meta: Mapping[str, Any],
              context: Mapping[str, Any]) -> None:
    report = C.validate_all(grid=grid, timeseries=series, meta=meta, context=context, case_id=case_id)
    problems = [f"{artefact}: {problem}" for artefact, items in report.items() for problem in items]
    if problems:
        shown = "; ".join(problems[:5]) + (f" (and {len(problems) - 5} more)" if len(problems) > 5 else "")
        _fail(case_id, "contract", shown)
    _assert_dtypes(case_id, grid, "grid", C.GRID_FIELDS)
    _assert_dtypes(case_id, series, "timeseries", C.TIMESERIES_FIELDS)
    _check_identity(case_id, grid, series, meta, context)
    _check_grid_handoff(case_id, grid, meta)
    values = series["value"]
    if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
        _fail(case_id, "timeseries.value", "contains null or non-finite value(s); a missing date is a missing row")
    _aoi_values(case_id, series, [str(value) for value in meta["dates"]])
    _check_provenance_identity(case_id, grid, series, meta, context)
    stats = meta["stats"]
    if int(stats["n_cells"]) != len(grid):
        _fail(case_id, "meta.stats.n_cells", f"is {stats['n_cells']} but the grid publishes {len(grid)} "
              f"row(s); a disagreement means one artefact is stale")
    if int(stats["n_dates"]) != len(meta["dates"]):
        _fail(case_id, "meta.stats.n_dates", f"is {stats['n_dates']} but meta.dates has "
              f"{len(meta['dates'])} entries")
    for key in ("median_velocity_mm_yr", "p05_velocity_mm_yr", "p95_velocity_mm_yr"):
        value = stats.get(key)
        if isinstance(value, (int, float)) and not np.isfinite(float(value)):
            _fail(case_id, f"meta.stats.{key}", "is not a finite number")


def _assert_dtypes(case_id: str, frame: pd.DataFrame, label: str, fields: Iterable[C.Field]) -> None:
    for field_ in fields:
        if field_.name not in frame.columns:
            continue  # the contract validator already reported a missing column
        actual = str(frame[field_.name].dtype)
        if field_.dtype == "float64" and actual != "float64":
            _fail(case_id, f"{label}.{field_.name}", f"dtype is {actual!r}, the contract says float64 "
                  f"(do not coerce in the UI — fix the artefact)")
        if field_.dtype == "string" and actual not in {"str", "string", "object"}:
            # pandas 3 reports `str`, pandas 2 `object`; either is documented, a numeric is not.
            _fail(case_id, f"{label}.{field_.name}", f"dtype is {actual!r}, the handoff says string")


def _check_identity(case_id: str, grid: pd.DataFrame, series: pd.DataFrame, meta: Mapping[str, Any],
                    context: Mapping[str, Any]) -> None:
    """Directory, grid, series, context and meta.json must describe the same case/product."""
    if str(meta.get("case_id")) != case_id:
        _fail(case_id, "meta.case_id", f"is {meta.get('case_id')!r}; the directory must match")
    for frame, label in ((grid, "grid"), (series, "timeseries")):
        for column in ("case_id", "sensor", "product"):
            expected = case_id if column == "case_id" else str(meta.get(column))
            observed = _unique(frame, column)
            if observed != {expected}:
                _fail(case_id, f"{label}.{column}", f"values {sorted(observed)} != {expected!r}")
    wrong = sorted(
        {str((f.get("properties") or {}).get("case_id")) for f in context.get("features") or []
         if str((f.get("properties") or {}).get("case_id")) != case_id}
    )
    if wrong:
        _fail(case_id, "context_layers.geojson", f"feature case_id value(s) {wrong} do not match")


def _check_grid_handoff(case_id: str, grid: pd.DataFrame, meta: Mapping[str, Any]) -> None:
    """Handoff promises the frozen schema cannot express about the map columns."""
    profile = C.sensor_profile(str(meta["sensor"]), str(meta["product"]))
    nonzero = int((grid["val_t1"] != 0).sum())
    if nonzero:
        _fail(case_id, "val_t1", f"{nonzero} cell(s) with val_t1 != 0; cumulative displacement is "
              f"referenced to the first acquisition, so val_t1 is 0 by construction")
    below = int((grid["coherence"] < profile.coherence_min).sum())
    if below:
        _fail(case_id, "coherence", f"{below} cell(s) below the {profile.product!r} profile "
              f"coherence_min={profile.coherence_min}: absent, not published")
    wrong_risk = int((grid["velocity_mm_yr"].map(C.risk_level_from_velocity).astype(str)
                      != grid["risk_level"].astype(str)).sum())
    if wrong_risk:
        _fail(case_id, "risk_level", f"{wrong_risk} cell(s) disagree with risk_level_from_velocity on "
              f"the stored velocity (bands: |v| < 5 estable, 5–25 moderado, >= 25 critico)")
    if (grid["cell_id"].astype(str).str.len() == 0).any():
        _fail(case_id, "cell_id", "empty value; every row needs an H3 index")
    if "acquisition_date" in grid.columns and grid["acquisition_date"].notna().any():
        observed = _unique(grid.loc[grid["acquisition_date"].notna()], "acquisition_date")
        if observed != {str(meta["dates"][-1])}:
            _fail(case_id, "acquisition_date", f"values {sorted(observed)}; expected the constant "
                  f"last date {meta['dates'][-1]!r}")


def _check_provenance_identity(case_id: str, grid: pd.DataFrame, series: pd.DataFrame,
                               meta: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    """Contract §8: the same provenance id in the grid, the series, the context and meta."""
    identifiers = _unique(grid, "provenance_id") | _unique(series, "provenance_id")
    identifiers |= {str((f.get("properties") or {}).get("provenance_id"))
                    for f in context.get("features") or []}
    meta_id = (meta.get("provenance") or {}).get("provenance_id")
    if meta_id is not None:
        identifiers.add(str(meta_id))
    malformed = sorted(value for value in identifiers if not C.PROVENANCE_ID_RE.match(value))
    if malformed or not identifiers:
        _fail(case_id, "provenance_id", f"value(s) {malformed} do not match {C.PROVENANCE_ID_RE.pattern}")
    if len(identifiers) > 1:
        _fail(case_id, "provenance_id", f"artefacts disagree: {sorted(identifiers)}; contract §8 "
              f"requires one id across grid, series, context and meta.provenance")


def _apply_filters(
    case_id: str, frame: pd.DataFrame, *, coherence_min: float | None,
    land_use: str | Iterable[str] | None, risk_level: str | Iterable[str] | None,
) -> tuple[pd.DataFrame, list[str]]:
    mask = pd.Series(True, index=frame.index)
    described: list[str] = []
    if coherence_min is not None:
        if isinstance(coherence_min, bool) or not isinstance(coherence_min, (int, float, np.number)):
            _fail(case_id, "coherence_min", f"expected a number in [0, 1], got {coherence_min!r}")
        threshold = float(coherence_min)
        if not np.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            _fail(case_id, "coherence_min", f"expected a number in [0, 1], got {threshold!r}")
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


def _wanted_labels(case_id: str, field_name: str, requested: Any, vocabulary: Iterable[str]) -> set[str]:
    if isinstance(requested, str):
        values = [requested]
    else:
        try:
            values = list(requested)
        except TypeError:
            _fail(case_id, field_name, f"expected a string or an iterable of strings, got {type(requested).__name__}")
    if not values:
        _fail(case_id, field_name, "empty filter list; pass None to mean 'no filter'")
    labels = {str(value) for value in values}
    unknown = sorted(labels - set(vocabulary))
    if unknown:
        _fail(case_id, field_name, f"undeclared value(s) {unknown}; allowed {list(vocabulary)} (contract §7)")
    return labels


def _aoi_values(case_id: str, series: pd.DataFrame, dates: list[str]) -> pd.Series:
    """The stored ``cell_id == "aoi"`` rows, indexed by date in ``meta.dates`` order."""
    aoi = series.loc[series["cell_id"].astype(str) == "aoi", ["date", "value"]].copy()
    if aoi.empty:
        _fail(case_id, "timeseries.cell_id", "no 'aoi' rows; the case aggregate is mandatory (contract §4); "
              "generate it with `--per-cell-series` off (the default)")
    aoi["date"] = aoi["date"].astype(str)
    if aoi["date"].duplicated().any():
        _fail(case_id, "timeseries.date", "the 'aoi' series has duplicate dates")
    missing = [value for value in dates if value not in set(aoi["date"])]
    if missing:
        _fail(case_id, "timeseries.date", f"the 'aoi' series is missing date(s) {missing}; a missing date "
              f"is a missing row, never an interpolated value (handoff §3)")
    return aoi.set_index("date").loc[dates]["value"]


def _unique(frame: pd.DataFrame, column: str) -> set[str]:
    return {str(value) for value in frame[column].dropna().unique().tolist()}


def _processed_root(processed: str | pathlib.Path | None) -> pathlib.Path:
    """Resolve the artefact root against the *caller's* cwd — never against ``__file__``."""
    root = pathlib.Path("data/processed") if processed is None else pathlib.Path(processed)
    if not root.is_absolute():
        root = pathlib.Path.cwd() / root
    return root.resolve()


def _fail(case_id: str, field_name: str, problem: str) -> NoReturn:
    raise LoaderError(f"{case_id}: {field_name}: {problem}")
