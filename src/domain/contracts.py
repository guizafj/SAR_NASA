"""Frozen data contract v1.1 — NASA Space Apps 2026, "Dancing with the SARs".

This module is the machine-readable half of `docs/data-contract-v1.md`. Both are normative and
must agree; when they disagree, this file wins and the doc is wrong.

Why it exists
-------------
`flujo_trabajo_y_responsabilidades.md` §5 freezes one interface between the offline pipeline
(Rol 3 / yuls) and the Streamlit app (Rol 1 / pozo, Rol 2 / fradiaz). Every artefact is validated
against this module, so replacing mock data with real data never touches the UI (§7 Fase 3).

Hard rules
----------
1. No Streamlit, no geopandas, no pydeck, no network imports here: pipeline, loaders and CI all
   share this module (§3.2 modularity rules). `pandas` is used only if the caller passes a frame.
2. Field names, dtypes, enums, units and thresholds live *only* here. Never re-declare them in a
   component.
3. Any change needs `SCHEMA_VERSION` bumped, a line in `CHANGELOG` and review by all three roles
   (§5.4, §8). Additive optional fields still bump the revision.
4. Thresholds are per `(sensor, product)` and never pooled. NISAR's 12-day revisit is not
   Sentinel-1's 6-day revisit; the same coherence number means different things.

What 1.1 adds (all required, so a 1.0-only generator fails closed)
-----------------------------------------------------------------
The thesis metrics, as *fitted* values rather than typed constants: ``seasonal_amp_mm`` /
``ratio_R`` / ``regime`` on the grid, ``trend_mm_yr`` / ``seasonal_mm`` on the timeseries, and
``median_seasonal_amp_mm`` / ``median_ratio_R`` / ``dominant_regime`` in ``meta.stats``. The
seasonal pair is only publishable when the stack's own identifiability gate passes
(``pipeline/science/transforms.py``: >= 0.75 yr per harmonic, design condition <= 30), which is
why the study window is two years long: at the previous 132-day window the pipeline measured
``annual_coverage = 0.3614`` and ``cond = 66.84``, i.e. an amplitude that must not be published.

One narrow exception, because the columns are required: a stack shorter than the gate fits **no**
seasonal harmonic, and then ``seasonal_amp_mm`` is ``0.0`` (the model has no seasonal term) while
``ratio_R`` is **null** — ``|trend| / 0`` is undefined, and a number there would be fabricated.
Null is legal in ``ratio_R`` exactly where ``seasonal_amp_mm == 0``, ``meta.stats.median_ratio_R``
may be JSON null for the same reason, and the fit reports ``regime = "transitional"`` (this module's
spelling for "no regime information", see ``transforms.harmonic_fit``). Everywhere else both
columns are finite, non-negative numbers.

Freeze record
-------------
    SCHEMA_VERSION = "1.1"    CONTRACT_REVISION = 2    frozen 2026-11-14
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "1.1"
CONTRACT_REVISION = 2
CONTRACT_FROZEN_UTC = "2026-11-14T00:00:00Z"

#: Ceiling on a published ``ratio_R`` (``|trend| / seasonal_amp_mm``). A ratio this large does not
#: describe a physical regime: it means the amplitude is indistinguishable from numerical zero — an
#: exactly-linear series fits ~1e-14 mm and would otherwise publish ~1e15, the "sentinel" this
#: contract's prose forbids. Real cells top out near 277 and a genuinely non-seasonal series fits the
#: noise floor (~0.2 mm), not zero, so this only catches a numerical artefact. A fail-closed guard,
#: not a scientific detectability threshold.
MAX_PUBLISHABLE_RATIO_R: float = 1e9

CHANGELOG: tuple[tuple[str, str], ...] = (
    (
        "1.1",
        "Adds the thesis metrics: seasonal_amp_mm, ratio_R and regime to the grid, plus "
        "trend_mm_yr and seasonal_mm to timeseries and aggregate thesis statistics to meta. "
        "They are fitted values (harmonics of the README §6 trend/seasonal split), never typed "
        "constants, and the seasonal trio is only publishable when the span/conditioning gate of "
        "pipeline/science/transforms.py passes.",
    ),
    (
        "1.0",
        "Initial freeze. Resolves six ambiguities in flujo §5: delta_change is cumulative mm and "
        "velocity_mm_yr carries the rate; product must be a declared enum (OPERA DISP-S1 is NOT "
        "available for these AOIs); sensor is enumerated; timeseries has an explicit cell_id "
        "aggregation key; thresholds are per sensor+product with revisit_days; stats carries "
        "detection_limit_mm_yr for null-result cases.",
    ),
)

#: Case slugs. Which one is *primary* is a config decision (`config/study_cases.yaml`), not part
#: of the contract: flujo §2.1 leaves it open, and README §14 records the recommendation.
#: (The three *informe* documents that originally framed this question were removed in b097b5b;
#: they remain in git history, not in the tree.)
CASE_IDS: tuple[str, ...] = ("mexico-city", "jakarta", "malaga")

SENSORS: tuple[str, ...] = ("nisar", "sentinel-1")

#: Declared products. `mock` is a first-class member so a demo can never be mistaken for real data.
PRODUCTS: tuple[str, ...] = (
    "nisar-gunw",
    "nisar-gcov",
    "opera-disp-s1",
    "hyp3-s1",
    "egms",
    "mock",
)

#: Units for `val_t1`, `val_t2`, `delta_change`. Rates always use `VELOCITY_UNIT`.
UNIT = "mm"
VELOCITY_UNIT = "mm/year"
BACKSCATTER_UNIT = "dB"
UNITS: tuple[str, ...] = (UNIT, VELOCITY_UNIT, BACKSCATTER_UNIT, "-")

LAND_USE: tuple[str, ...] = (
    "urban-dense",
    "urban-residential",
    "industrial",
    "infrastructure",
    "water",
    "bare",
    "vegetation",
    "unknown",
)

RISK_LEVELS: tuple[str, ...] = ("estable", "moderado", "critico")

QUALITY: tuple[str, ...] = ("good", "fair", "poor")

REGIMES: tuple[str, ...] = ("inelastic", "elastic", "transitional")

CONTEXT_LAYERS: tuple[str, ...] = ("land_use", "infrastructure", "water", "admin_boundary")

CRS_OUTPUT = "EPSG:4326"

#: `provenance_id` format: {case_id}+{product}+{YYYYMMDD}+{sha256_8}. Traceable by eye and by grep.
PROVENANCE_ID_RE = re.compile(r"^[a-z0-9-]+\+[a-z0-9-]+\+\d{8}\+[0-9a-f]{8}$")


@dataclass(frozen=True)
class Field:
    name: str
    dtype: str
    required: bool
    unit: str | None = None
    enum: tuple[str, ...] | None = None
    note: str = ""


#: `data/processed/nisar_grid.parquet` — one row per H3 cell per case (§5.1 + frozen additions).
GRID_FIELDS: tuple[Field, ...] = (
    Field("cell_id", "string", True, note="H3 index, resolution 9 (~200 m edge). Unique per case"),
    Field("lat", "float64", True, note="Cell centroid, EPSG:4326"),
    Field("lon", "float64", True, note="Cell centroid, EPSG:4326"),
    Field("val_t1", "float64", True, UNIT, note="Modelled displacement at first date (mm, up +)"),
    Field("val_t2", "float64", True, UNIT, note="Modelled displacement at last date (mm, up +)"),
    Field("delta_change", "float64", True, UNIT, note="val_t2 - val_t1 (negative = subsidence)"),
    Field("velocity_mm_yr", "float64", True, VELOCITY_UNIT, note="Fitted linear trend (FROZEN ADDITION)"),
    Field("seasonal_amp_mm", "float64", True, UNIT, note="Fitted seasonal amplitude"),
    Field(
        "ratio_R",
        "float64",
        True,
        "-",
        note="abs(trend) / seasonal amplitude; null iff seasonal_amp_mm == 0 (no harmonic fitted)",
    ),
    Field("regime", "string", True, enum=REGIMES, note="Secular versus seasonal regime"),
    Field("coherence", "float64", True, "-", note="0..1, bias-corrected temporal mean"),
    Field("land_use", "string", True, enum=LAND_USE),
    Field("risk_level", "string", True, enum=RISK_LEVELS, note="Derived by risk_level_from_velocity"),
    Field("case_id", "string", True, note="One value per file"),
    Field("sensor", "string", True, enum=SENSORS),
    Field("product", "string", True, enum=PRODUCTS),
    Field("unit", "string", True, enum=(UNIT,), note="Unit of val_t1/val_t2/delta_change"),
    Field("velocity_unit", "string", True, enum=(VELOCITY_UNIT,), note="Unit of velocity_mm_yr"),
    Field("source", "string", True, note="Human-readable provider, e.g. 'ASF DAAC'"),
    Field("acquisition_date", "string", False, note="ISO date of the last acquisition, when meaningful"),
    Field("provenance_id", "string", True, note="See PROVENANCE_ID_RE; must resolve in meta.json"),
)

#: `data/processed/timeseries.csv` — §5.2 plus the frozen aggregation key.
TIMESERIES_FIELDS: tuple[Field, ...] = (
    Field("case_id", "string", True),
    Field("cell_id", "string", True, note="'aoi' for the case aggregate; an H3 index for drill-down"),
    Field("date", "string", True, note="ISO YYYY-MM-DD, member of meta.dates"),
    Field("value", "float64", True, UNIT, note="Cumulative displacement, mm, up positive"),
    Field("trend_mm_yr", "float64", True, VELOCITY_UNIT, note="Offline secular trend"),
    Field("seasonal_mm", "float64", True, UNIT, note="Offline seasonal component"),
    Field("unit", "string", True, enum=(UNIT,)),
    Field("sensor", "string", True, enum=SENSORS),
    Field("product", "string", True, enum=PRODUCTS),
    Field("quality", "string", True, enum=QUALITY),
    Field("provenance_id", "string", True),
)

#: `data/processed/context_layers.geojson` — FeatureCollection, EPSG:4326 Feature properties.
CONTEXT_FIELDS: tuple[Field, ...] = (
    Field("layer", "string", True, enum=CONTEXT_LAYERS),
    Field("class", "string", True, note="Sub-category within the layer, vocabularised below"),
    Field("name", "string", False),
    Field("case_id", "string", True),
    Field("crs", "string", True, enum=(CRS_OUTPUT,), note="Declared per feature: consumers do not guess"),
    Field("source", "string", True),
    Field("provenance_id", "string", True),
)

#: Vocabulary for `context_layers.geojson` `class`, per layer.
CONTEXT_CLASSES: Mapping[str, tuple[str, ...]] = {
    "land_use": LAND_USE,
    "infrastructure": ("water-well", "water-treatment", "metro-station", "pipeline", "levee", "power"),
    "water": ("reservoir", "river", "canal", "aquifer", "coastline"),
    "admin_boundary": ("municipality", "district", "aoi"),
}

META_REQUIRED_KEYS: tuple[str, ...] = (
    "schema_version",
    "case_id",
    "name",
    "bbox",
    "crs",
    "reference_point",
    "dates",
    "revisit_days",
    "sensor",
    "product",
    "thresholds",
    "provenance",
    "stats",
    "limitations",
    "null_policy",
)

PROVENANCE_REQUIRED_KEYS: tuple[str, ...] = (
    "source",
    "product_id",
    "processing_date",
    "method",
    "retrieved_utc",
    "reference",
    "license",
)

STATS_REQUIRED_KEYS: tuple[str, ...] = (
    "n_cells",
    "n_dates",
    "median_velocity_mm_yr",
    "median_seasonal_amp_mm",
    "median_ratio_R",
    "dominant_regime",
    "p05_velocity_mm_yr",
    "p95_velocity_mm_yr",
)


@dataclass(frozen=True)
class SensorProfile:
    """Per-(sensor, product) processing parameters. Never pool across profiles (contract §5.4;
    the physical reasons are in README §13)."""

    sensor: str
    product: str
    revisit_days: int | None
    coherence_min: float
    k_sigma: float
    min_ddB: float
    persistence: str
    min_area_m2: int
    posting_m: int | None
    note: str


#: Keyed by product. `revisit_days` is what makes coherence comparable — never mix entries.
SENSOR_PROFILES: Mapping[str, SensorProfile] = {
    "nisar-gunw": SensorProfile(
        "nisar", "nisar-gunw", 12, 0.35, 2.5, 3.0, "2of3", 500, 80,
        "NISAR L2 GUNW (provisional, cryosphere-derived): verify urban coverage before trusting it.",
    ),
    "nisar-gcov": SensorProfile(
        "nisar", "nisar-gcov", 12, 0.25, 3.0, 2.0, "2of3", 500, 30,
        "Amplitude-only change detection: no phase, so no displacement. Do not report mm/yr here.",
    ),
    "opera-disp-s1": SensorProfile(
        "sentinel-1", "opera-disp-s1", 12, 0.30, 2.5, 3.0, "2of3", 500, 30,
        "Calibrated displacement product. ABSENT for our three AOIs as of 2026-11-14 (verified).",
    ),
    "hyp3-s1": SensorProfile(
        "sentinel-1", "hyp3-s1", 6, 0.40, 2.5, 3.0, "2of3", 500, 20,
        "On-demand InSAR from Sentinel-1 SLC pairs: 6-day revisit, so its coherence is NOT "
        "comparable with NISAR's 12-day coherence.",
    ),
    "egms": SensorProfile(
        "sentinel-1", "egms", 6, 0.30, 2.5, 3.0, "2of3", 500, 50,
        "Copernicus EGMS, EU only: ready-made velocity, manual portal download, no amplitude.",
    ),
    "mock": SensorProfile(
        "nisar", "mock", 12, 0.35, 2.5, 3.0, "2of3", 500, 80,
        "Synthetic stub. Provenance must say mock and the UI must badge it as such.",
    ),
}

#: Velocity (mm/yr, up positive) → risk band. Defaults documented, overridable only in config.
RISK_THRESHOLDS_MM_YR: tuple[tuple[float, str], ...] = (
    (5.0, "estable"),
    (25.0, "moderado"),
    (math.inf, "critico"),
)

#: Below this |median velocity| a case is a declared null result and must publish its bound.
NULL_RESULT_MM_YR = 5.0


class ContractError(ValueError):
    """Raised by the `require_*` helpers; validators return problem lists instead."""


def risk_level_from_velocity(velocity_mm_yr: float) -> str:
    """Map a velocity to a risk band. Sign is ignored: -420 mm/yr is as critical as +420."""
    magnitude = abs(float(velocity_mm_yr))
    if not math.isfinite(magnitude):
        return "estable"
    for limit, label in RISK_THRESHOLDS_MM_YR:
        if magnitude < limit:
            return label
    return "critico"


def sensor_profile(sensor: str, product: str) -> SensorProfile:
    """Return the frozen profile for a declared (sensor, product) pair, or explain why not."""
    profile = SENSOR_PROFILES.get(product)
    if profile is None:
        raise ContractError(
            f"unknown product {product!r}; declared products are {sorted(SENSOR_PROFILES)}"
        )
    if sensor not in SENSORS:
        raise ContractError(f"unknown sensor {sensor!r}; declared sensors are {list(SENSORS)}")
    if profile.sensor != sensor:
        raise ContractError(
            f"product {product!r} is produced from sensor {profile.sensor!r}, not {sensor!r}"
        )
    return profile


def provenance_id(case_id: str, product: str, when: dt.date | str, digest: str) -> str:
    """Build the frozen provenance id: {case_id}+{product}+{YYYYMMDD}+{sha256[:8]}."""
    day = when if isinstance(when, str) else when.strftime("%Y%m%d")
    day = day.replace("-", "")[:8]
    return f"{case_id}+{product}+{day}+{digest[:8]}"


def contract_fingerprint() -> str:
    """Stable hash of the frozen schema, for provenance records and cache busting."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "revision": CONTRACT_REVISION,
        "grid": [(f.name, f.dtype, f.required, f.unit, f.enum) for f in GRID_FIELDS],
        "timeseries": [(f.name, f.dtype, f.required, f.unit, f.enum) for f in TIMESERIES_FIELDS],
        "context": [(f.name, f.dtype, f.required, f.unit, f.enum) for f in CONTEXT_FIELDS],
        "meta_required": META_REQUIRED_KEYS,
        "stats_required": STATS_REQUIRED_KEYS,
        "profiles": {
            k: [v.sensor, v.product, v.revisit_days, v.coherence_min, v.k_sigma, v.min_ddB]
            for k, v in sorted(SENSOR_PROFILES.items())
        },
        "risk": [[limit, label] for limit, label in RISK_THRESHOLDS_MM_YR],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------------------------
# Validators. They return a list of human-readable problems (empty list == valid) so the pipeline,
# the CI gate and the loaders all surface the same wording.
# ---------------------------------------------------------------------------------------------


def _columns(frame: Any) -> list[str]:
    return [str(c) for c in getattr(frame, "columns", [])]


def _series(frame: Any, column: str) -> Any:
    try:
        return frame[column]
    except Exception:  # noqa: BLE001 - caller may pass any frame-like object
        return None


def _enum_problems(frame: Any, field: Field, problems: list[str]) -> None:
    if field.enum is None or field.name not in _columns(frame):
        return
    try:
        observed = set(_series(frame, field.name).dropna().unique().tolist())
    except Exception:  # noqa: BLE001
        return
    unknown = {str(v) for v in observed} - set(field.enum)
    if unknown:
        problems.append(
            f"{field.name}: undeclared value(s) {sorted(unknown)}; allowed {list(field.enum)}"
        )


def validate_grid(
    grid: Any,
    *,
    case_id: str | None = None,
    require_single_case: bool = True,
) -> list[str]:
    """Validate `nisar_grid.parquet` against §5.1. Returns problems, never raises."""
    problems: list[str] = []
    columns = _columns(grid)
    if not columns:
        return ["grid: empty or not a dataframe"]

    for field in GRID_FIELDS:
        if field.required and field.name not in columns:
            problems.append(f"grid: missing required column {field.name!r} ({field.dtype})")
    for name in columns:
        if name not in {f.name for f in GRID_FIELDS}:
            problems.append(f"grid: undeclared column {name!r} (bump SCHEMA_VERSION if intended)")

    for field in GRID_FIELDS:
        if field.name in columns:
            _enum_problems(grid, field, problems)

    import math as _math

    def numeric(column: str) -> Any:
        try:
            return _series(grid, column).astype("float64", errors="ignore")
        except Exception:  # noqa: BLE001
            return None

    for column in (
        "lat",
        "lon",
        "val_t1",
        "val_t2",
        "delta_change",
        "velocity_mm_yr",
        "seasonal_amp_mm",
        "ratio_R",
        "coherence",
    ):
        if column not in columns:
            continue
        values = numeric(column)
        if values is None:
            continue
        try:
            if column == "ratio_R":
                # A fitted amplitude is a number; a not-fitted one (amplitude exactly 0, because
                # `transforms.harmonic_order` returned 0) makes |trend|/0 undefined, and null is
                # the only honest value. The window is narrow on purpose: a null where an
                # amplitude WAS fitted would be a computed ratio the publisher hid.
                nulls = values.isna()
                amplitude = numeric("seasonal_amp_mm")
                if nulls.any():
                    if amplitude is None:
                        problems.append(
                            f"grid: ratio_R has {int(nulls.sum())} null(s) but there is no "
                            "seasonal_amp_mm column to justify them"
                        )
                    else:
                        fitted = amplitude.fillna(0.0) > 0.0
                        unjustified = nulls & fitted
                        if unjustified.any():
                            problems.append(
                                f"grid: ratio_R has {int(unjustified.sum())} null(s) where "
                                "seasonal_amp_mm > 0; a fitted harmonic has a defined ratio, so "
                                "publish it (null is only legal where nothing was fitted)"
                            )
                if amplitude is not None:
                    # The reverse direction: where nothing was fitted the ratio MUST be null. A
                    # number there — inf, or a sentinel such as 1e9 read as "extremely
                    # inelastic" — is exactly the fabrication the contract forbids.
                    unfitted = amplitude.fillna(0.0) <= 0.0
                    invented = values.notna() & unfitted
                    if invented.any():
                        problems.append(
                            f"grid: ratio_R has {int(invented.sum())} value(s) where "
                            "seasonal_amp_mm == 0; an undefined ratio must be null, never inf "
                            "or a sentinel"
                        )
                velocity = numeric("velocity_mm_yr")
                if amplitude is not None and velocity is not None:
                    # `ratio_R` is DEFINED as |velocity_mm_yr| / seasonal_amp_mm, so a published
                    # ratio has to equal it. Without this a sentinel (1e9) or any fabricated
                    # ratio passes every other check here: the prose forbids it, the code did not.
                    fitted_amp = amplitude.fillna(0.0) > 0.0
                    defined = fitted_amp & values.notna() & velocity.notna()
                    if defined.any():
                        expected = velocity.abs() / amplitude
                        tolerance = expected.abs() * 5e-3 + 5e-3
                        inconsistent = defined & ((values - expected).abs() > tolerance)
                        if inconsistent.any():
                            problems.append(
                                "grid: ratio_R disagrees with |velocity_mm_yr| / "
                                f"seasonal_amp_mm on {int(inconsistent.sum())} row(s); publish "
                                "the computed ratio rather than a typed or sentinel value"
                            )
                    # A ratio this large is not a measurement: it means the amplitude is
                    # indistinguishable from numerical zero (an exactly-linear series fits
                    # ~1e-14 mm and would otherwise publish ~1e15). Fail closed instead of
                    # shipping the sentinel the contract's prose forbids.
                    ratio_num = pd.to_numeric(values, errors="coerce")
                    absurd = ratio_num.notna() & (ratio_num > MAX_PUBLISHABLE_RATIO_R)
                    if absurd.any():
                        problems.append(
                            f"grid: ratio_R exceeds {MAX_PUBLISHABLE_RATIO_R:g} on "
                            f"{int(absurd.sum())} row(s); that magnitude means the amplitude is "
                            "numerically zero, so publish amplitude 0 with a null ratio instead"
                        )
                observed = values.dropna()
            else:
                if values.isna().any():
                    problems.append(f"grid: {column} has {int(values.isna().sum())} null(s)")
                observed = values
            if column == "lat" and not values.between(-90, 90).all():
                problems.append("grid: lat outside [-90, 90]")
            if column == "lon" and not values.between(-180, 180).all():
                problems.append("grid: lon outside [-180, 180]")
            if column == "coherence" and not values.dropna().between(0, 1).all():
                problems.append("grid: coherence outside [0, 1]")
            if column in (
                "val_t1",
                "val_t2",
                "delta_change",
                "velocity_mm_yr",
                "seasonal_amp_mm",
                "ratio_R",
            ):
                if not observed.map(_math.isfinite).all():
                    problems.append(f"grid: {column} contains non-finite values")
            if column == "seasonal_amp_mm" and not observed.ge(0).all():
                problems.append("grid: seasonal_amp_mm must be non-negative (an amplitude)")
            if column == "ratio_R" and not observed.ge(0).all():
                problems.append("grid: ratio_R must be non-negative (abs(trend)/amplitude)")
        except Exception:  # noqa: BLE001 - non-numeric column reported below
            problems.append(f"grid: {column} is not numeric")

    if {"val_t1", "val_t2", "delta_change"} <= set(columns):
        try:
            residual = (_series(grid, "val_t2") - _series(grid, "val_t1") - _series(grid, "delta_change")).abs()
            if (residual > 1e-6).any():
                problems.append(
                    "grid: delta_change != val_t2 - val_t1 for "
                    f"{int((residual > 1e-6).sum())} rows (contract: delta_change is cumulative mm)"
                )
        except Exception:  # noqa: BLE001
            pass

    seen_cases: set[str] = set()
    if "case_id" in columns:
        try:
            seen_cases = {str(v) for v in _series(grid, "case_id").dropna().unique()}
        except Exception:  # noqa: BLE001
            pass
        if require_single_case and len(seen_cases) > 1:
            problems.append(f"grid: mixed case_id values {sorted(seen_cases)}; one file per case")
        undeclared = seen_cases - set(CASE_IDS)
        if undeclared:
            problems.append(f"grid: undeclared case_id {sorted(undeclared)}; allowed {list(CASE_IDS)}")
        if case_id is not None and seen_cases and seen_cases != {case_id}:
            problems.append(f"grid: case_id {sorted(seen_cases)} does not match expected {case_id!r}")

    for column in ("provenance_id",):
        if column in columns:
            try:
                ids = _series(grid, column).dropna().astype(str)
                if ids.empty or (ids.str.len() == 0).any():
                    problems.append(f"grid: {column} must be non-empty for every row")
                bad = [v for v in ids.unique() if not PROVENANCE_ID_RE.match(v)]
                if bad:
                    problems.append(
                        f"grid: {column} {bad[:3]} does not match {PROVENANCE_ID_RE.pattern}"
                    )
            except Exception:  # noqa: BLE001
                pass

    if "cell_id" in columns:
        try:
            duplicate = _series(grid, "cell_id").duplicated().any()
            if duplicate:
                problems.append("grid: cell_id is not unique (one row per H3 cell per case)")
        except Exception:  # noqa: BLE001
            pass

    if {"sensor", "product"} <= set(columns):
        try:
            pairs = _series(grid, "sensor").astype(str) + "/" + _series(grid, "product").astype(str)
            for pair in sorted(set(pairs)):
                sensor, product = pair.split("/", 1)
                try:
                    sensor_profile(sensor, product)
                except ContractError as exc:
                    problems.append(f"grid: {exc}")
        except Exception:  # noqa: BLE001
            pass

    return problems


def validate_timeseries(series: Any, *, dates: Sequence[str] | None = None) -> list[str]:
    """Validate `timeseries.csv` against §5.2 + the frozen `cell_id` aggregation rule."""
    problems: list[str] = []
    columns = _columns(series)
    if not columns:
        return ["timeseries: empty or not a dataframe"]

    for field in TIMESERIES_FIELDS:
        if field.required and field.name not in columns:
            problems.append(f"timeseries: missing required column {field.name!r} ({field.dtype})")
    for field in TIMESERIES_FIELDS:
        if field.name in columns:
            _enum_problems(series, field, problems)

    # The numeric columns are what every consumer plots; a null or a non-finite value would be
    # rendered as a real measurement. `trend_mm_yr`/`seasonal_mm` are 1.1 additions and were not
    # validated at all before this check, so a NaN fit could reach the app unchallenged.
    for column in ("value", "trend_mm_yr", "seasonal_mm"):
        if column not in columns:
            continue
        try:
            values = _series(series, column).astype("float64")
        except Exception:  # noqa: BLE001 - non-numeric column reported here, not below
            problems.append(f"timeseries: {column} is not numeric")
            continue
        if values.isna().any():
            problems.append(f"timeseries: {column} has {int(values.isna().sum())} null(s)")
        elif not values.map(math.isfinite).all():
            problems.append(f"timeseries: {column} contains non-finite values")

    if "date" in columns:
        try:
            values = _series(series, "date").astype(str)
            if not values.str.match(r"^\d{4}-\d{2}-\d{2}$").all():
                problems.append("timeseries: date must be ISO YYYY-MM-DD")
            if dates is not None:
                stray = set(values.unique()) - set(dates)
                if stray:
                    problems.append(f"timeseries: date(s) {sorted(stray)[:3]} not in meta.dates")
        except Exception:  # noqa: BLE001
            pass

    if "provenance_id" in columns:
        try:
            ids = _series(series, "provenance_id").dropna().astype(str)
            bad = [v for v in ids.unique() if not PROVENANCE_ID_RE.match(v)]
            if bad:
                problems.append(f"timeseries: provenance_id {bad[:3]} does not match the frozen format")
        except Exception:  # noqa: BLE001
            pass

    return problems


def validate_meta(meta: Mapping[str, Any]) -> list[str]:
    """Validate `meta.json` against §5.3 + the frozen thresholds/statistics rules."""
    problems: list[str] = []
    if not isinstance(meta, Mapping):
        return ["meta: not a JSON object"]

    for key in META_REQUIRED_KEYS:
        if key not in meta:
            problems.append(f"meta: missing required key {key!r}")

    if meta.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"meta: schema_version is {meta.get('schema_version')!r}, contract is {SCHEMA_VERSION!r}"
        )

    case = meta.get("case_id")
    if case is not None and case not in CASE_IDS:
        problems.append(f"meta: undeclared case_id {case!r}; allowed {list(CASE_IDS)}")

    if meta.get("crs") not in (CRS_OUTPUT, None):
        problems.append(f"meta: crs must be {CRS_OUTPUT} for published artefacts")

    bbox = meta.get("bbox")
    if bbox is not None:
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            problems.append("meta: bbox must be [W, S, E, N]")
        else:
            w, s, e, n = bbox
            if not (w < e and s < n):
                problems.append("meta: bbox is not [W, S, E, N] ordered")

    dates = meta.get("dates")
    if dates is not None:
        if not isinstance(dates, (list, tuple)) or not dates:
            problems.append("meta: dates must be a non-empty list")
        else:
            if list(dates) != sorted(dates):
                problems.append("meta: dates must be sorted ascending")
            if len(set(dates)) != len(dates):
                problems.append("meta: dates contains duplicates")
            for value in dates:
                if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                    problems.append(f"meta: date {value!r} is not ISO YYYY-MM-DD")
                    break

    sensor, product = meta.get("sensor"), meta.get("product")
    if sensor is not None and sensor not in SENSORS:
        problems.append(f"meta: undeclared sensor {sensor!r}; allowed {list(SENSORS)}")
    if product is not None and product not in PRODUCTS:
        problems.append(f"meta: undeclared product {product!r}; allowed {list(PRODUCTS)}")

    thresholds = meta.get("thresholds")
    if isinstance(thresholds, Mapping):
        for key in ("coherence_min", "k_sigma", "min_ddB", "persistence", "min_area_m2"):
            if key not in thresholds:
                problems.append(f"meta: thresholds missing {key!r}")
        if sensor and product:
            try:
                profile = sensor_profile(str(sensor), str(product))
                if thresholds.get("coherence_min") != profile.coherence_min:
                    problems.append(
                        f"meta: thresholds.coherence_min {thresholds.get('coherence_min')} != frozen "
                        f"{profile.coherence_min} for {product} (never pool sensors/products)"
                    )
                if meta.get("revisit_days") != profile.revisit_days:
                    problems.append(
                        f"meta: revisit_days {meta.get('revisit_days')} != frozen "
                        f"{profile.revisit_days} for {product}"
                    )
            except ContractError as exc:
                problems.append(f"meta: {exc}")
    elif thresholds is not None:
        problems.append("meta: thresholds must be an object")

    provenance = meta.get("provenance")
    if isinstance(provenance, Mapping):
        for key in PROVENANCE_REQUIRED_KEYS:
            if key not in provenance:
                problems.append(f"meta: provenance missing {key!r}")
        if not str(provenance.get("method") or "").strip():
            problems.append("meta: provenance.method must describe the physical conversion")
        if product == "mock" and "mock" not in str(provenance.get("source", "")).lower():
            problems.append("meta: product is 'mock' but provenance.source does not say so")
    elif provenance is not None:
        problems.append("meta: provenance must be an object")

    stats = meta.get("stats")
    if isinstance(stats, Mapping):
        for key in STATS_REQUIRED_KEYS:
            if key not in stats:
                problems.append(f"meta: stats missing {key!r}")
        dominant = stats.get("dominant_regime")
        if dominant is not None and str(dominant) not in REGIMES:
            problems.append(
                f"meta: stats.dominant_regime {dominant!r} is not one of {list(REGIMES)}"
            )
        for key in ("median_seasonal_amp_mm", "median_ratio_R"):
            value = stats.get(key)
            # `median_ratio_R` may be JSON null when no cell fitted a harmonic, but a non-finite
            # number is never legal: NaN/inf cannot survive a strict JSON reader and would hide a
            # broken fit behind a value that merely looks numeric.
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isfinite(float(value)):
                    problems.append(f"meta: stats.{key} must be finite or null, got {value!r}")
                elif value < 0.0:
                    problems.append(f"meta: stats.{key} must be non-negative, got {value!r}")
        # The null `ratio_R` must travel with its explanation (docs/data-contract-v1.md §5): if the
        # case fitted no seasonal harmonic, some limitation has to say so, otherwise a reader sees
        # an amplitude of 0 and a null ratio with nothing telling them it means "not fitted".
        # Accept either producer's key spelling so this survives the planned rename.
        harmonics = stats.get("seasonal_n_harmonics", stats.get("n_harmonics"))
        if isinstance(harmonics, int) and not isinstance(harmonics, bool) and harmonics == 0:
            limitations_text = " ".join(
                str(item) for item in (meta.get("limitations") or ())
            ).lower()
            if "not fitted" not in limitations_text:
                problems.append(
                    "meta: no seasonal harmonic was fitted "
                    "(stats.seasonal_n_harmonics/n_harmonics is 0) but no limitation says so; "
                    "the null ratio_R must travel with its reason"
                )
        # The mirror inconsistency: a null median ratio beside a non-zero median amplitude. If a
        # harmonic was fitted anywhere the aggregate ratio is defined, so null there would hide a
        # computed number behind an "undefined" marker.
        if stats.get("median_ratio_R") is None:
            amp_median = stats.get("median_seasonal_amp_mm")
            if (
                isinstance(amp_median, (int, float))
                and not isinstance(amp_median, bool)
                and amp_median > 0.0
            ):
                problems.append(
                    f"meta: stats.median_ratio_R is null but median_seasonal_amp_mm is "
                    f"{amp_median!r}; a case that fitted a harmonic has a defined median ratio"
                )
        median = stats.get("median_velocity_mm_yr")
        if isinstance(median, (int, float)) and abs(median) < NULL_RESULT_MM_YR:
            if not isinstance(stats.get("detection_limit_mm_yr"), (int, float)):
                problems.append(
                    "meta: |median_velocity_mm_yr| < 5 mm/yr is a null result — "
                    "stats.detection_limit_mm_yr is mandatory (README §13: a null result must "
                    "publish its bound)"
                )
    elif stats is not None:
        problems.append("meta: stats must be an object")

    if not isinstance(meta.get("limitations"), (list, tuple)) or not meta.get("limitations"):
        problems.append("meta: limitations must be a non-empty list")
    if not isinstance(meta.get("null_policy"), str):
        problems.append("meta: null_policy must be a string describing how nodata is represented")

    return problems


def validate_context_layers(collection: Mapping[str, Any]) -> list[str]:
    """Validate `context_layers.geojson` (FeatureCollection, EPSG:4326)."""
    problems: list[str] = []
    if not isinstance(collection, Mapping) or collection.get("type") != "FeatureCollection":
        return ["context: not a GeoJSON FeatureCollection"]
    features = collection.get("features") or []
    if not isinstance(features, (list, tuple)) or not features:
        return ["context: FeatureCollection has no features"]

    declared = {f.name for f in CONTEXT_FIELDS}
    for index, feature in enumerate(features[:5000]):
        props = (feature or {}).get("properties") or {}
        missing = {f.name for f in CONTEXT_FIELDS if f.required and f.name not in props}
        if missing:
            problems.append(f"context: feature {index} missing properties {sorted(missing)}")
            continue
        undeclared = set(props) - declared
        if undeclared:
            problems.append(f"context: feature {index} has undeclared properties {sorted(undeclared)}")
        layer = props.get("layer")
        if layer not in CONTEXT_LAYERS:
            problems.append(f"context: feature {index} undeclared layer {layer!r}")
        elif props.get("class") not in CONTEXT_CLASSES.get(layer, ()):
            problems.append(
                f"context: feature {index} class {props.get('class')!r} not in {list(CONTEXT_CLASSES.get(layer, ()))}"
            )
        if props.get("crs") != CRS_OUTPUT:
            problems.append(f"context: feature {index} crs must be {CRS_OUTPUT}")
        if not PROVENANCE_ID_RE.match(str(props.get("provenance_id", ""))):
            problems.append(f"context: feature {index} provenance_id does not match the frozen format")
        geometry = (feature or {}).get("geometry") or {}
        if geometry.get("type") not in {"Point", "LineString", "Polygon", "MultiPolygon"}:
            problems.append(f"context: feature {index} has unsupported geometry {geometry.get('type')!r}")
        if problems and len(problems) > 25:
            problems.append("context: stopped after 25 problems")
            break
    return problems


def validate_all(
    *,
    grid: Any = None,
    timeseries: Any = None,
    meta: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    case_id: str | None = None,
) -> dict[str, list[str]]:
    """Run every applicable validator. Empty lists == artefact set is contract-conformant."""
    report: dict[str, list[str]] = {}
    if grid is not None:
        report["grid"] = validate_grid(grid, case_id=case_id)
    if timeseries is not None:
        report["timeseries"] = validate_timeseries(
            timeseries, dates=(meta or {}).get("dates") if meta else None
        )
    if meta is not None:
        report["meta"] = validate_meta(meta)
    if context is not None:
        report["context"] = validate_context_layers(context)
    return report


def _selfcheck() -> int:
    """`python -m src.domain.contracts` — proves the freeze is internally consistent."""
    import csv
    import io

    rows = [
        {
            "cell_id": "8928308280fffff",
            "lat": 19.4326,
            "lon": -99.1332,
            "val_t1": 0.0,
            "val_t2": -7.4,
            "delta_change": -7.4,
            "velocity_mm_yr": -128.0,
            "seasonal_amp_mm": 9.4,
            "ratio_R": 128.0 / 9.4,
            "regime": "inelastic",
            "coherence": 0.82,
            "land_use": "urban-dense",
            "risk_level": "critico",
            "case_id": "mexico-city",
            "sensor": "nisar",
            "product": "nisar-gunw",
            "unit": "mm",
            "velocity_unit": "mm/year",
            "source": "ASF DAAC",
            "acquisition_date": "2026-07-02",
            "provenance_id": provenance_id("mexico-city", "nisar-gunw", "2026-11-14", "abc12345"),
        }
    ]
    import pandas as pd

    grid = pd.DataFrame(rows)
    series = pd.read_csv(
        io.StringIO(
            "case_id,cell_id,date,value,trend_mm_yr,seasonal_mm,unit,sensor,product,quality,provenance_id\n"
            f"mexico-city,aoi,2026-07-02,0.0,-128.0,0.0,mm,nisar,nisar-gunw,good,{rows[0]['provenance_id']}\n"
        )
    )
    meta = {
        "schema_version": SCHEMA_VERSION,
        "case_id": "mexico-city",
        "name": "Mexico City, MX",
        "bbox": [-99.35, 19.20, -98.85, 19.65],
        "crs": CRS_OUTPUT,
        "reference_point": [-99.13, 19.43],
        "dates": ["2026-07-02", "2026-07-14"],
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
            "product_id": "NISAR_L2_PR_GUNW_...",
            "processing_date": "2026-11-14",
            "method": "LOS to vertical (v_north=0), H3 res 9 mean",
            "retrieved_utc": CONTRACT_FROZEN_UTC,
            "reference": "Herrera-García et al. 2021, doi:10.1126/science.abb8549",
            "license": "NASA open data",
        },
        "stats": {
            "n_cells": 15500,
            "n_dates": 12,
            "median_velocity_mm_yr": -128.0,
            "median_seasonal_amp_mm": 9.4,
            "median_ratio_R": 128.0 / 9.4,
            "dominant_regime": "inelastic",
            "p05_velocity_mm_yr": -410.0,
            "p95_velocity_mm_yr": 12.0,
        },
        "limitations": ["GUNW posting 80 m: city scale, not building scale."],
        "null_policy": "Cells without data are absent from the grid; never written as 0.",
    }
    context = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-99.13, 19.43]},
                "properties": {
                    "layer": "infrastructure",
                    "class": "water-well",
                    "name": "Pozo Roma",
                    "case_id": "mexico-city",
                    "crs": CRS_OUTPUT,
                    "source": "SACMEX",
                    "provenance_id": rows[0]["provenance_id"],
                },
            }
        ],
    }

    report = validate_all(grid=grid, timeseries=series, meta=meta, context=context, case_id="mexico-city")
    failed = False
    for name, problems in report.items():
        if problems:
            failed = True
            print(f"FAIL {name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"PASS {name}")

    # Negative tests: each must be caught, otherwise the freeze is decorative.
    negatives = {
        "delta_change arithmetic": validate_grid(pd.DataFrame([{**rows[0], "delta_change": 1.0}])),
        "negative seasonal amplitude": validate_grid(pd.DataFrame([{**rows[0], "seasonal_amp_mm": -1.5}])),
        "negative ratio_R": validate_grid(pd.DataFrame([{**rows[0], "ratio_R": -2.0}])),
        "undeclared regime": validate_grid(pd.DataFrame([{**rows[0], "regime": "recoverable"}])),
        "null ratio_R where a harmonic was fitted": validate_grid(
            pd.DataFrame([{**rows[0], "ratio_R": None}])
        ),
        "pooled threshold": validate_meta({**meta, "product": "nisar-gunw", "sensor": "sentinel-1"}),
        "null result without bound": validate_meta(
            {**meta, "stats": {**meta["stats"], "median_velocity_mm_yr": -1.2}}
        ),
        "undeclared dominant_regime": validate_meta(
            {**meta, "stats": {**meta["stats"], "dominant_regime": "recoverable"}}
        ),
        "non-finite median_ratio_R": validate_meta(
            {**meta, "stats": {**meta["stats"], "median_ratio_R": float("nan")}}
        ),
        "sentinel ratio where nothing was fitted": validate_grid(
            pd.DataFrame([{**rows[0], "seasonal_amp_mm": 0.0, "ratio_R": 1e9}])
        ),
        "not-fitted case without an explanation": validate_meta(
            {**meta, "stats": {**meta["stats"], "seasonal_n_harmonics": 0}}
        ),
        "null median ratio beside a fitted amplitude": validate_meta(
            {**meta, "stats": {**meta["stats"], "median_ratio_R": None}}
        ),
        "ratio_R that disagrees with its inputs": validate_grid(
            pd.DataFrame([{**rows[0], "ratio_R": 1e9}])
        ),
        "ratio_R at numerical-noise magnitude": validate_grid(
            pd.DataFrame(
                [{**rows[0], "seasonal_amp_mm": 1e-13, "ratio_R": 128.0 / 1e-13}]
            )
        ),
        "non-finite trend_mm_yr": validate_timeseries(series.assign(trend_mm_yr=float("nan"))),
        "undeclared product": validate_meta({**meta, "product": "opera-disp-s1", "sensor": "nisar"}),
    }
    for label, problems in negatives.items():
        if problems:
            print(f"PASS negative: {label} rejected ({problems[0][:88]})")
        else:
            failed = True
            print(f"FAIL negative: {label} was accepted")

    # Positive acceptance for the one narrow null: a not-fitted harmonic publishes amplitude 0
    # and an undefined (null) ratio, and that must NOT be reported as an error.
    not_fitted = pd.DataFrame(
        [{**rows[0], "seasonal_amp_mm": 0.0, "ratio_R": None, "regime": "transitional"}]
    )
    problems = validate_grid(not_fitted)
    if problems:
        failed = True
        print("FAIL positive: not-fitted amplitude with a null ratio_R rejected")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("PASS positive: not-fitted harmonic (amplitude 0, null ratio_R) accepted")

    print(f"\ncontract fingerprint: {contract_fingerprint()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_selfcheck())
