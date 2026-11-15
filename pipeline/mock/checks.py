"""Post-write gates for the four mock artefacts.

Two layers, deliberately separate:

1. `contracts.validate_all(...)` — the frozen contract, owned by Rol 1/2/3 together. Its word is
   final; nothing here re-implements a schema rule.
2. The assertions this task adds on top of the contract, because the contract cannot see them:
   nodata is *absent* and exactly explained (the grid cell set equals the coverage-ellipse cell set
   minus precisely the cells below `thresholds.coherence_min`), the arithmetic holds to 1e-9, the
   dates/statistics/provenance ids are mutually consistent, and the mock-specific ranges (published
   coherence in [coherence_min, 0.95], Málaga's null result) are respected.

`assert_case()` raises `AssertionError` with every failed check, so `--check` is a real gate and
not a linter with opinions.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pipeline.science import transforms as V
from src.domain import contracts as C

from . import model as M
from .artifacts import ARTEFACTS, PRODUCT, TOTAL_BUDGET_BYTES
from .cases import CaseSpec, StudyCases


@dataclass
class CaseReport:
    case_id: str
    n_cells: int
    n_dates: int
    n_series_rows: int
    n_context_features: int
    land_use: dict[str, int]
    median_velocity: float
    median_seasonal_amp: float
    median_ratio_R: float | None  # None == no cell fitted a harmonic (undefined median)
    dominant_regime: str
    p05_velocity: float
    p95_velocity: float
    median_coherence: float
    sizes: dict[str, int] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)
    contract_report: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(self.sizes.values())


@dataclass
class LoadedCase:
    directory: Path
    grid: pd.DataFrame
    series: pd.DataFrame
    meta: dict[str, Any]
    context: dict[str, Any]
    meta_text: str
    sizes: dict[str, int]


def load_case(directory: Path) -> LoadedCase:
    missing = [name for name in ARTEFACTS if not (directory / name).is_file()]
    if missing:
        raise AssertionError(f"{directory}: missing artefact(s) {missing}")
    meta_path = directory / "meta.json"
    meta_text = meta_path.read_text(encoding="utf-8")
    return LoadedCase(
        directory=directory,
        grid=pd.read_parquet(directory / "nisar_grid.parquet"),
        series=pd.read_csv(directory / "timeseries.csv"),
        meta=json.loads(meta_text),
        context=json.loads((directory / "context_layers.geojson").read_text(encoding="utf-8")),
        meta_text=meta_text,
        sizes={name: (directory / name).stat().st_size for name in ARTEFACTS},
    )


def _require(condition: Any, message: str, failures: list[str]) -> bool:
    if not condition:
        failures.append(message)
        return False
    return True


def assert_case(case: CaseSpec, study: StudyCases, directory: Path) -> CaseReport:
    """Run every gate for one case. Raises AssertionError listing every failure, not just the first."""
    loaded = load_case(directory)
    grid, series, meta, context, meta_text = (
        loaded.grid,
        loaded.series,
        loaded.meta,
        loaded.context,
        loaded.meta_text,
    )
    failures: list[str] = []
    checks: list[str] = []

    # -- 1. the frozen contract has the last word --------------------------------------------
    report = C.validate_all(grid=grid, timeseries=series, meta=meta, context=context, case_id=case.case_id)
    for name, problems in report.items():
        for problem in problems:
            failures.append(f"contract.{name}: {problem}")
    checks.append(f"contracts.validate_all empty for grid/timeseries/meta/context")

    # -- 2. no nulls in the required fields ---------------------------------------------------
    for spec in C.GRID_FIELDS:
        if not spec.required:
            continue
        if spec.name == "ratio_R":
            # The COLUMN is required even when nothing was fitted; a null VALUE is legal exactly
            # where seasonal_amp_mm == 0 (|trend|/0 has no value). Section 7 checks both
            # directions cell by cell, so this only has to insist the column exists.
            _require(spec.name in grid.columns, "grid: required column 'ratio_R' is missing", failures)
            continue
        _require(spec.name in grid.columns and grid[spec.name].notna().all(), f"grid.{spec.name} has nulls", failures)
    for spec in C.TIMESERIES_FIELDS:
        if spec.required:
            _require(
                spec.name in series.columns and series[spec.name].notna().all(),
                f"timeseries.{spec.name} has nulls",
                failures,
            )
    checks.append("no nulls in required grid/timeseries columns")

    # -- 3. arithmetic: delta_change == val_t2 - val_t1 exactly (gate: 1e-9) -----------------
    residual = (grid["val_t2"] - grid["val_t1"] - grid["delta_change"]).abs().max()
    _require(float(residual) <= 1e-9, f"delta_change arithmetic residual {residual:.3e} > 1e-9", failures)
    checks.append(f"delta_change = val_t2 - val_t1 (max residual {float(residual):.2e} <= 1e-9)")

    # -- 4. nodata is ABSENT and exactly explained by the quality filter -----------------------
    # The cell set is an equation, not a percentage: a cell inside the ellipse is published iff its
    # modelled coherence is >= thresholds.coherence_min. The model publishes its seed, so the check
    # rebuilds the pre-filter cell set and its coherence deterministically and compares sets.
    expected_ids, _, _ = M.expected_cells(case, study.h3_resolution)
    expected = {str(v) for v in expected_ids}
    actual = set(grid["cell_id"].astype(str))
    threshold = float(meta["thresholds"]["coherence_min"])
    try:
        seed = int(meta["provenance"]["mock_seed"])
    except (KeyError, TypeError, ValueError):
        seed = -1
    _require(
        seed >= 0,
        "meta.provenance.mock_seed is missing: the published cell set cannot be re-derived, so "
        "the quality filter cannot be audited",
        failures,
    )
    model = (
        M.build_case(case, study, seed, provenance=str(meta["provenance"]["provenance_id"]))
        if seed >= 0
        else None
    )
    if model is not None:
        modelled_prefilter = {str(v) for v in model.prefilter_cell_id}
        _require(
            modelled_prefilter == expected,
            f"the model covers {len(modelled_prefilter)} cells but the coverage ellipse holds "
            f"{len(expected)}: pre-filter cell set and nodata rule disagree",
            failures,
        )
        expected_keep = {
            str(cell)
            for cell, coherence in zip(model.prefilter_cell_id, model.prefilter_coherence, strict=True)
            if float(coherence) >= threshold
        }
        invented = actual - modelled_prefilter
        _require(
            not invented,
            f"grid contains {len(invented)} cell(s) outside the coverage ellipse — cells must "
            f"never be invented or zero-filled",
            failures,
        )
        missing = expected_keep - actual
        _require(
            not missing,
            f"{len(missing)} cell(s) inside the coverage ellipse with modelled coherence >= "
            f"{threshold:.2f} are missing from the grid: the filter must drop exactly the cells "
            f"below it (e.g. {sorted(missing)[:3]})",
            failures,
        )
        unexplained = actual - expected_keep
        _require(
            not unexplained,
            f"{len(unexplained)} published cell(s) have modelled coherence below "
            f"{threshold:.2f}: a decorrelating surface must be absent, not published "
            f"(e.g. {sorted(unexplained)[:3]})",
            failures,
        )
        dropped = modelled_prefilter - expected_keep
        checks.append(
            f"nodata absent, not zero: {len(expected)} coverage cells - {len(dropped)} below "
            f"coherence_min {threshold:.2f} == {len(actual)} written cells (exact set equality)"
        )

    # Zero is a legitimate velocity — a null-result city can sit at exactly 0 — so this is a
    # bounded guard, not a ban. What it must catch is a masked region written as 0: a 133-cell
    # blob (CDMX, ~14 km²) is nodata in disguise, and so is a whole neighbourhood at 0.
    zero_counts = {
        column: int((grid[column].to_numpy() == 0.0).sum())
        for column in ("velocity_mm_yr", "delta_change", "val_t2")
    }
    _require(
        all(count <= 2 for count in zero_counts.values()),
        f"exact zeros beyond coincidence: {zero_counts} (guard: <= 2 cells per column). A zero "
        f"blob is nodata written as 0; zero itself is a valid velocity, so a handful is fine",
        failures,
    )
    checks.append(f"no zero blobs: exact zeros per column {zero_counts} (<= 2)")

    # -- 5. dates: every series date in meta.dates; one 'aoi' row per date -------------------
    meta_dates = list(meta["dates"])
    _require(set(series["date"].astype(str)) <= set(meta_dates), "series has a date outside meta.dates", failures)
    aoi = series[series["cell_id"].astype(str) == "aoi"]
    _require(sorted(aoi["date"].astype(str)) == sorted(meta_dates), "aoi series is not one row per meta date", failures)
    _require(len(meta_dates) == len(study.dates), "meta.dates differs from the configured grid", failures)
    per_cell = series[series["cell_id"].astype(str) != "aoi"]
    if len(per_cell):
        counts = per_cell.groupby("cell_id").size()
        _require(
            bool((counts == len(meta_dates)).all()),
            f"per-cell series incomplete: {int((counts != len(meta_dates)).sum())} cell(s) without every date",
            failures,
        )
    checks.append(f"dates: {len(meta_dates)} in meta.dates, aoi rows == n_dates, {len(per_cell)} per-cell rows")

    # -- 6. provenance: format + resolves inside meta.json + consistent across artefacts -----
    provenance_ids = {
        "grid": set(grid["provenance_id"].astype(str)),
        "timeseries": set(series["provenance_id"].astype(str)),
        "context": set(f["properties"]["provenance_id"] for f in context["features"]),
    }
    all_ids = set().union(*provenance_ids.values())
    _require(len(all_ids) == 1, f"artefacts carry {len(all_ids)} provenance ids: {sorted(all_ids)}", failures)
    for artefact, ids in provenance_ids.items():
        bad = [v for v in ids if not C.PROVENANCE_ID_RE.match(v)]
        _require(not bad, f"{artefact}: provenance_id {bad[:3]} does not match PROVENANCE_ID_RE", failures)
        _require(
            all(v in meta_text for v in ids),
            f"{artefact}: provenance_id {sorted(ids)} does not appear in meta.json",
            failures,
        )
    _require(
        meta["provenance"].get("provenance_id") in all_ids,
        "meta.provenance.provenance_id does not match the artefacts",
        failures,
    )
    checks.append(f"provenance_id {sorted(all_ids)[0]} matches the regex and resolves in meta.json")

    # -- 7. derived fields are derived, not hand-written -------------------------------------
    stats = meta["stats"]
    expected_risk = [C.risk_level_from_velocity(v) for v in grid["velocity_mm_yr"]]
    _require(
        (grid["risk_level"].astype(str).tolist() == expected_risk),
        "risk_level was not produced by contracts.risk_level_from_velocity",
        failures,
    )
    # The 1.1 seasonal trio must be derived from the fitted pair, not copied from the config:
    # ratio_R is |trend|/amplitude and regime is the pipeline's own classify_regime on the same
    # numbers, so a hard-coded amplitude cannot survive this check.
    # `fitted` splits the two legal states. Where a harmonic WAS fitted, ratio_R is the finite
    # quotient and regime is classify_regime on the published pair. Where nothing was fitted
    # (amplitude exactly 0.0, because the mock case injects no seasonality or its window is below
    # the annual gate) the quotient is |trend|/0 — undefined — so the only honest values are a null
    # ratio and the contract's cannot-classify regime. Both the inf and the sentinel are rejected:
    # an undefined ratio must NOT be a number, and `regime` must not claim "inelastic" from a term
    # that was never fitted.
    amplitude = grid["seasonal_amp_mm"].to_numpy(dtype=float)
    velocity = grid["velocity_mm_yr"].to_numpy(dtype=float)
    ratio = grid["ratio_R"].to_numpy(dtype=float)
    fitted = amplitude > 0.0
    ratio_residual = 0.0
    if fitted.any():
        expected_ratio = np.abs(velocity[fitted]) / amplitude[fitted]
        ratio_residual = float(np.max(np.abs(ratio[fitted] - expected_ratio)))
    _require(
        ratio_residual <= 1e-6,
        f"ratio_R is not abs(velocity_mm_yr)/seasonal_amp_mm (max residual {ratio_residual:.3e})",
        failures,
    )
    defined_where_not_fitted = int(np.count_nonzero(~fitted & np.isfinite(ratio)))
    _require(
        defined_where_not_fitted == 0,
        f"{defined_where_not_fitted} cell(s) publish a ratio_R where seasonal_amp_mm == 0: "
        "|trend|/0 is undefined, so it must be null — never inf and never a sentinel",
        failures,
    )
    expected_regime = [
        V.classify_regime(float(trend), float(amp)) if amp > 0.0 else "transitional"
        for trend, amp in zip(velocity, amplitude, strict=True)
    ]
    _require(
        grid["regime"].astype(str).tolist() == expected_regime,
        "regime was not produced by pipeline.science.transforms.classify_regime on the published "
        "(velocity_mm_yr, seasonal_amp_mm) pair ('transitional' where no harmonic was fitted)",
        failures,
    )
    # One state per case, never a mixture: the order is chosen once, so a case either fitted the
    # seasonal term on every cell or on none. A mixture would mean the per-cell fit lost it.
    published_order = stats.get("seasonal_n_harmonics", stats.get("n_harmonics"))
    if not fitted.any():
        # The not-fitted branch: this is a legitimate publication, and it must be COMPLETE. An
        # amplitude of 0.0 with a populated ratio, or a null median with a fitted-looking
        # amplitude, is the half-state that reads as "we measured no seasonality".
        _require(
            int(published_order or 0) == 0,
            f"stats.seasonal_n_harmonics is {published_order!r} but no cell carries a fitted "
            "amplitude: the declared order and the grid must agree",
            failures,
        )
        _require(
            stats.get("median_ratio_R") is None,
            f"meta.stats.median_ratio_R is {stats.get('median_ratio_R')!r} while no cell fitted a "
            "harmonic: the aggregate of undefined ratios is null (NaN is not valid JSON)",
            failures,
        )
        _require(
            set(grid["regime"].astype(str)) == {"transitional"},
            f"no harmonic was fitted, so no cell can be classified against one, but regime(s) "
            f"{sorted(set(grid['regime'].astype(str)))} were published",
            failures,
        )
        _require(
            "not fitted" in " ".join(str(item) for item in meta["limitations"]).lower(),
            "no limitation says the seasonal term was not fitted: a null ratio_R without its "
            "reason reads as 'no seasonality measured', which is a claim about the ground",
            failures,
        )
        checks.append(
            f"seasonal term NOT fitted: amplitude 0.0 on all {len(grid)} cells (order "
            f"{published_order}), ratio_R null, regime 'transitional', median_ratio_R null, "
            "limitation present"
        )
    else:
        _require(
            bool(fitted.all()) and int(published_order or 0) > 0,
            f"{int((~fitted).sum())} of {len(grid)} cell(s) publish a 0.0 amplitude (the "
            "'not fitted' value) while others publish a fitted one, or stats declares "
            f"seasonal_n_harmonics = {published_order!r}: the seasonal term is fitted once per "
            "case, so the state must be the same on every cell",
            failures,
        )
    _require(
        bool(grid["coherence"].between(float(meta["thresholds"]["coherence_min"]), 0.95).all()),
        f"coherence outside [{meta['thresholds']['coherence_min']}, 0.95]: a published cell must "
        f"have passed the filter and the model must not exceed 0.95",
        failures,
    )
    known_land_use = set(C.LAND_USE)
    _require(set(grid["land_use"].astype(str)) <= known_land_use, "land_use outside the frozen vocabulary", failures)
    land_use_counts = {str(k): int(v) for k, v in grid["land_use"].value_counts().items()}
    # Three classes is the floor, not a target: the labels come from real OSM tags now, and a
    # case whose extract carries only a few classes is telling the truth about its AOI.
    _require(len(land_use_counts) >= 3, f"land_use only uses {sorted(land_use_counts)}: rule is too coarse", failures)
    # Water must be ABSENT from the grid (a specular surface has no usable phase, so the quality
    # filter drops those cells) while remaining a context layer, so the app can draw the coastline
    # without publishing "ocean with subsidence".
    _require(
        "water" not in land_use_counts,
        f"{land_use_counts.get('water')} water cell(s) survived the quality filter — a specular "
        f"surface must not carry a displacement",
        failures,
    )
    for layer in ("water", "infrastructure"):
        if model is None or model.context.synthetic or not model.context.features_of(layer).empty:
            _require(
                layer in {f["properties"]["layer"] for f in context.get("features", [])},
                f"no {layer} feature in context_layers although the context carries one",
                failures,
            )
        else:
            checks.append(f"context: no {layer} feature in the extract for this AOI (declared absent)")
    checks.append(
        f"derived: risk bands from velocity, ratio_R = |trend|/amplitude on the {int(fitted.sum())} "
        f"fitted cell(s) (max residual {ratio_residual:.1e}), ratio_R null on the "
        f"{int((~fitted).sum())} not-fitted one(s), regime from classify_regime, coherence in "
        f"[{meta['thresholds']['coherence_min']}, 0.95], land_use classes {len(land_use_counts)}"
    )

    # -- 8. aggregate consistency: aoi first/last == mean grid val_t1/val_t2 ------------------
    aoi_values = aoi.sort_values("date")["value"].to_numpy()
    _require(
        abs(float(aoi_values[0]) - float(grid["val_t1"].mean())) <= 1e-6,
        "aoi first value != mean(val_t1)",
        failures,
    )
    _require(
        abs(float(aoi_values[-1]) - float(grid["val_t2"].mean())) <= 1e-6,
        "aoi last value != mean(val_t2)",
        failures,
    )
    checks.append("aoi series endpoints == mean(val_t1)/mean(val_t2) of the grid")

    # -- 9. stats describe the actual file ---------------------------------------------------
    _require(stats["n_cells"] == len(grid), f"stats.n_cells {stats['n_cells']} != {len(grid)}", failures)
    _require(stats["n_dates"] == len(meta_dates), "stats.n_dates != len(meta.dates)", failures)
    regime_counts = grid["regime"].astype(str).value_counts()
    dominant = sorted(regime_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[0][0]
    # The median of an undefined ratio is null, not NaN: `ratio_R` is NaN exactly where nothing was
    # fitted, and `float(Series([nan]).median())` is NaN — which is neither JSON nor a number a
    # reader can compare. Recompute with the same rule the producer uses (finite values only).
    finite_ratio = ratio[np.isfinite(ratio)]
    expected_ratio_median = float(np.median(finite_ratio)) if finite_ratio.size else None
    for key, actual_value, tolerance in (
        ("median_velocity_mm_yr", float(grid["velocity_mm_yr"].median()), 1e-2),
        ("median_seasonal_amp_mm", float(grid["seasonal_amp_mm"].median()), 1e-2),
        ("median_ratio_R", expected_ratio_median, 1e-4),
        ("p05_velocity_mm_yr", float(grid["velocity_mm_yr"].quantile(0.05)), 1e-2),
        ("p95_velocity_mm_yr", float(grid["velocity_mm_yr"].quantile(0.95)), 1e-2),
    ):
        published = stats[key]
        if actual_value is None or published is None:
            _require(
                published is None and actual_value is None,
                f"stats.{key} {published!r} != recomputed {actual_value!r}",
                failures,
            )
            continue
        _require(
            abs(float(published) - actual_value) <= tolerance,
            f"stats.{key} {published} != recomputed {actual_value:.3f}",
            failures,
        )
    _require(
        str(stats["dominant_regime"]) == str(dominant),
        f"stats.dominant_regime {stats['dominant_regime']!r} != most frequent grid regime {dominant!r}",
        failures,
    )
    checks.append(
        "stats recomputed from the parquet (n_cells, median velocity/amplitude/ratio_R, "
        "p05, p95, dominant_regime)"
    )

    # -- 9b. the seasonal columns are the fit of the published series ------------------------
    # `value = intercept + trend_mm_yr*t + seasonal_mm` must hold for every row: the published
    # trend/seasonal pair has to be the decomposition of the very numbers next to it. Then re-run
    # harmonic_fit on the aoi series and compare its seasonal component with the published column,
    # which is the check a typed constant cannot pass.
    #
    # The decomposition is a claim only a FIT can make. With n_harmonics = 0 the model has no
    # seasonal basis, so a seasonal wiggle in the series cannot be absorbed and the residual is
    # deliberately not constant — that is the honest outcome of a window that cannot carry the
    # term, not a defect. What must hold there is that the published seasonal column is exactly
    # zero (no term fitted) and that the trend is the plain linear fit of the same series.
    years = np.array(
        [(dt.date.fromisoformat(str(day)) - dt.date.fromisoformat(meta_dates[0])).days for day in series["date"]],
        dtype=float,
    ) / V.DAYS_PER_YEAR
    spread = 0.0
    if fitted.any():
        residuals = (
            series["value"] - series["trend_mm_yr"] * years - series["seasonal_mm"]
        ).to_numpy(dtype=float)
        spread = float(np.max(np.abs(residuals - residuals[0]))) if residuals.size else 0.0
        _require(
            spread <= 1e-6,
            f"value - trend_mm_yr*t - seasonal_mm is not constant across dates (spread {spread:.3e} mm); "
            "the published split does not decompose the published series",
            failures,
        )
    else:
        non_zero = int(np.count_nonzero(series["seasonal_mm"].to_numpy(dtype=float)))
        _require(
            non_zero == 0,
            f"no annual harmonic was fitted, so timeseries.seasonal_mm must be exactly 0.0 (the "
            f"model contains no seasonal term), but {non_zero} row(s) carry a non-zero value",
            failures,
        )
    order = published_order
    if not isinstance(order, int) or isinstance(order, bool):
        # Producer without the statistics key: fall back to the order the span supports.
        order = V.harmonic_order(len(meta_dates), float(years.max()) - float(years.min()))
    aoi_sorted = aoi.sort_values("date", kind="stable")
    aoi_years = np.array(
        [
            (dt.date.fromisoformat(str(day)) - dt.date.fromisoformat(meta_dates[0])).days
            for day in aoi_sorted["date"]
        ],
        dtype=float,
    ) / V.DAYS_PER_YEAR
    refit = V.harmonic_fit(aoi_years, aoi_sorted["value"].to_numpy(dtype=float), n_harmonics=order)
    seasonal_refit = V.seasonal_component_mm(aoi_years, refit)
    published_aoi_seasonal = aoi_sorted["seasonal_mm"].to_numpy(dtype=float)
    seasonal_residual = float(np.max(np.abs(seasonal_refit - published_aoi_seasonal)))
    _require(
        refit.seasonal_identifiable,
        f"the published stack is not seasonally identifiable: {refit.warnings}",
        failures,
    )
    _require(
        seasonal_residual <= 1e-6,
        f"timeseries.seasonal_mm disagrees with a fresh harmonic_fit of the aoi series by "
        f"{seasonal_residual:.3e} mm",
        failures,
    )
    _require(
        abs(refit.trend_mm_yr - float(aoi["trend_mm_yr"].iloc[0])) <= 1e-6,
        "timeseries.trend_mm_yr disagrees with a fresh harmonic_fit of the aoi series",
        failures,
    )
    checks.append(
        f"seasonal columns decompose the series (value-trend*t-seasonal constant to {spread:.1e} mm "
        f"on the {int(fitted.sum())} fitted cell(s), seasonal_mm exactly 0 where nothing was "
        f"fitted) and a refit of the aoi series reproduces seasonal_mm to {seasonal_residual:.1e} "
        f"mm (order {order}, coverage {refit.annual_coverage:.3f}, cond {refit.design_condition:.2f})"
    )

    # -- 10. null-result case: Málaga stays flat and publishes its bound ---------------------
    median = float(grid["velocity_mm_yr"].median())
    if case.null_result:
        _require(abs(median) < C.NULL_RESULT_MM_YR, f"null-result case median |v| = {abs(median):.2f} >= 5", failures)
        _require(
            isinstance(stats.get("detection_limit_mm_yr"), (int, float)),
            "null-result case missing stats.detection_limit_mm_yr",
            failures,
        )
        checks.append(
            f"null result: median |v| = {abs(median):.2f} < 5 mm/yr with detection_limit "
            f"{stats['detection_limit_mm_yr']} mm/yr"
        )

    # -- 11. meta carries the mock badge and the null policy ---------------------------------
    _require("mock" in str(meta["provenance"]["source"]).lower(), "provenance.source does not say mock", failures)
    _require("absent" in str(meta["null_policy"]).lower(), "null_policy does not state that nodata is absent", failures)
    _require(bool(meta["limitations"]), "meta.limitations is empty", failures)
    checks.append("meta: provenance.source says mock, null_policy states absence, limitations present")

    # -- 12. context layers come from the same land_use assignment ---------------------------
    context_classes = {
        f["properties"]["class"] for f in context["features"] if f["properties"]["layer"] == "land_use"
    }
    _require(
        set(land_use_counts) <= context_classes,
        f"grid land_use {sorted(set(land_use_counts) - context_classes)} has no context polygon",
        failures,
    )
    layers = {f["properties"]["layer"] for f in context["features"]}
    _require({"land_use", "infrastructure", "water"} <= layers, f"context layers {sorted(layers)} incomplete", failures)
    # Sea cells must not carry a displacement. The context derives the sea geometrically from the
    # real coastline (`context.sea_polygons`), so the gate is stated against that polygon:
    #   (a) an extract with coastline features must have produced a sea polygon — a cap that ranked
    #       the water layer by area once dropped every line, and the rule then failed silently;
    #   (b) no published cell may sit inside it.
    # Testing a *sign* against the coastline would pass vacuously; that is why it is not done.
    if model is not None and model.context.synthetic:
        # The declared fallback has no derived sea polygon: it excludes the sea with the side it
        # declares per case (`Coast.sea_side`) inside `_synthetic_land_use`. What must hold there is
        # that the rule actually fired and the filtered cells are gone.
        _require(
            "water" not in land_use_counts,
            "the synthetic fallback published water cells: its declared-side rule did not fire, so a "
            "specular surface carries a displacement",
            failures,
        )
        checks.append("sea: synthetic fallback excluded the sea with its declared side")
    elif model is not None:
        water_rows = model.context.features_of("water")
        # `coastline_lines` is recorded by the pin writer: a pinned context keeps only the largest
        # water features, so losing every coastline row to the per-kind cap would otherwise look
        # exactly like an inland AOI and this check would pass vacuously.
        has_coastline = bool(
            int(model.context.counters.get("coastline_lines") or 0) > 0
            or (not water_rows.empty and (water_rows["class"] == "coastline").any())
        )
        _require(
            model.context.sea is not None or not has_coastline,
            "the context carries coastline features but no sea polygon was derived: nothing excludes "
            "the sea, so the artefact could publish a displacement over it",
            failures,
        )
        if model.context.sea is not None:
            from shapely.geometry import Point  # noqa: PLC0415 - only this branch needs it

            inside_sea = sum(
                1
                for lon, lat in zip(model.lon, model.lat, strict=True)
                if model.context.sea.contains(Point(float(lon), float(lat)))
            )
            _require(
                inside_sea == 0,
                f"{inside_sea} published cell(s) fall inside the derived sea polygon: a specular "
                "surface must not carry a displacement",
                failures,
            )
            checks.append(
                f"sea: the derived sea polygon excludes all {int(model.n_cells)} published cells"
            )
    checks.append(f"context: {len(context['features'])} features, land_use classes match the grid")

    # -- 13. budget: this case cannot own the whole 15 MB tree -------------------------------
    _require(
        loaded.sizes and sum(loaded.sizes.values()) <= TOTAL_BUDGET_BYTES,
        "case artefacts exceed the total budget",
        failures,
    )

    if failures:
        raise AssertionError(f"{case.case_id}: {len(failures)} gate(s) failed\n  - " + "\n  - ".join(failures))

    return CaseReport(
        case_id=case.case_id,
        n_cells=len(grid),
        n_dates=len(meta_dates),
        n_series_rows=len(series),
        n_context_features=len(context["features"]),
        land_use=land_use_counts,
        median_velocity=median,
        median_seasonal_amp=float(grid["seasonal_amp_mm"].median()),
        median_ratio_R=expected_ratio_median,
        dominant_regime=str(dominant),
        p05_velocity=float(grid["velocity_mm_yr"].quantile(0.05)),
        p95_velocity=float(grid["velocity_mm_yr"].quantile(0.95)),
        median_coherence=float(grid["coherence"].median()),
        sizes=loaded.sizes,
        checks=checks,
        contract_report=report,
    )


def summary_table(reports: list[CaseReport]) -> str:
    """Per-case summary table, plus the grand total against the 15 MB budget."""
    header = (
        f"{'case':<12} {'cells':>7} {'dates':>5} {'series':>7} {'ctx':>4} "
        f"{'median v':>9} {'A_med':>8} {'R_med':>9} {'regime':<13} {'p05':>9} {'p95':>9} "
        f"{'parquet':>8} {'csv':>8} {'geojson':>8} {'meta':>6} {'case MB':>8}"
    )
    lines = [header, "-" * len(header)]
    total = 0
    for report in reports:
        total += report.total_bytes
        sizes = report.sizes
        # A null ratio is a state, not a missing number: print it as one instead of "nan".
        ratio_cell = "not fitted" if report.median_ratio_R is None else f"{report.median_ratio_R:.3f}"
        lines.append(
            f"{report.case_id:<12} {report.n_cells:>7} {report.n_dates:>5} {report.n_series_rows:>7} "
            f"{report.n_context_features:>4} {report.median_velocity:>9.2f} "
            f"{report.median_seasonal_amp:>8.3f} {ratio_cell:>9} "
            f"{report.dominant_regime:<13} "
            f"{report.p05_velocity:>9.2f} "
            f"{report.p95_velocity:>9.2f} {sizes['nisar_grid.parquet'] / 1024:>8.1f} "
            f"{sizes['timeseries.csv'] / 1024:>8.1f} {sizes['context_layers.geojson'] / 1024:>8.1f} "
            f"{sizes['meta.json'] / 1024:>6.1f} {report.total_bytes / 1024 / 1024:>8.2f}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<12} {'':>7} {'':>5} {'':>7} {'':>4} {'':>9} {'':>8} {'':>9} {'':<13}"
        f"{'':>9} {'':>9} "
        f"{'':>8} {'':>8} {'':>8} {'':>6} {total / 1024 / 1024:>8.2f}"
        f"   (budget {TOTAL_BUDGET_BYTES / 1024 / 1024:.0f} MB, KiB per file)"
    )
    return "\n".join(lines)
