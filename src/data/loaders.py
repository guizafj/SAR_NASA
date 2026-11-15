"""Artefact and configuration loaders with mtime + contract-fingerprint caching (Rol 1 — pozo).

Responsibilities:
1. Parse `config/study_cases.yaml` into typed immutable configurations.
2. Discover and load the four frozen artefacts per city (`nisar_grid.parquet`, `timeseries.csv`,
   `meta.json`, `context_layers.geojson`), supporting both canonical `data/processed/{case_id}/`
   folders and a flat single-case `data/processed/` directory.
3. Enforce schema + cross-artefact validation (`src.data.validation`) BEFORE any UI component
   touches the data.
4. Cache parsed artefacts in memory keyed by file modification timestamps and
   `contract_fingerprint()`, returning defensive DataFrame copies so callers can never mutate
   cached state across Streamlit reruns.
"""

from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd
import yaml

from src.data.validation import (
    CASE_ARTIFACT_FILENAMES,
    ArtifactValidationError,
    ValidationResult,
    discover_case_dirs,
    validate_case_bundle,
)
from src.domain import contracts as C

# `examples/` is a runtime dependency of this module (see .dockerignore). Guard the
# import so a deployment that ships without it can still import `src.data.loaders`
# and let the UI fall back instead of crashing at startup.
try:  # pragma: no cover - trivial branch, exercised by the deployment guard test
    from examples.reference_loader import (
        Case,
        LoaderError,
        aoi_series,
        grid_frame,
        kpis,
        load_case,
        map_payload,
    )
except ImportError as _reference_loader_exc:  # pragma: no cover
    class LoaderError(RuntimeError):  # type: ignore[no-redef]
        """Raised when the reference loader is unavailable in this deployment."""

    Case = Any  # type: ignore[misc,assignment]
    aoi_series = grid_frame = kpis = load_case = map_payload = None  # type: ignore[assignment]
    _REFERENCE_LOADER_IMPORT_ERROR: ImportError | None = _reference_loader_exc
else:  # pragma: no cover
    _REFERENCE_LOADER_IMPORT_ERROR = None

ROOT_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_PROCESSED_DIR: pathlib.Path = ROOT_DIR / "data" / "processed"
DEFAULT_STUDY_CASES_PATH: pathlib.Path = ROOT_DIR / "config" / "study_cases.yaml"
DEFAULT_AOI_DIR: pathlib.Path = ROOT_DIR / "config" / "aoi"


class CaseNotFoundError(FileNotFoundError):
    """Raised when a requested study case has no published artefacts on disk."""


@dataclass(frozen=True)
class StudyCaseConfig:
    """Typed view of one entry under `cases:` in `config/study_cases.yaml`."""

    case_id: str
    name: str
    role: str
    bbox: tuple[float, float, float, float]
    aoi_polygon: tuple[tuple[float, float], ...]
    crs_publish: str
    crs_analysis: str
    reference_point: tuple[float, float]
    revisit_days: int
    expected_regime: str
    published_rate_mm_yr: float
    null_result: bool
    detection_limit_mm_yr: float | None
    limitations: tuple[str, ...]
    mock: Mapping[str, Any]


@dataclass(frozen=True)
class ProcessingConfig:
    """Shared offline processing constants from `config/study_cases.yaml`."""

    h3_resolution: int
    coherence_min: float
    k_sigma: float
    min_area_m2: int
    dates_start: str
    dates_step_days: int
    dates_count: int


@dataclass(frozen=True)
class StudyCasesConfig:
    """Complete parsed `config/study_cases.yaml`."""

    schema_version: str
    primary: str
    cases: Mapping[str, StudyCaseConfig]
    processing: ProcessingConfig


@dataclass(frozen=True)
class CaseBundle:
    """Validated, ready-to-filter artefact bundle for a single study case."""

    case_id: str
    grid: pd.DataFrame
    timeseries: pd.DataFrame
    meta: Mapping[str, Any]
    context: Mapping[str, Any]
    case_config: StudyCaseConfig | None
    validation: ValidationResult
    source_dir: pathlib.Path

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or (self.case_config.name if self.case_config else self.case_id))

    @property
    def sensor(self) -> str:
        return str(self.meta["sensor"])

    @property
    def product(self) -> str:
        return str(self.meta["product"])

    @property
    def is_mock(self) -> bool:
        return self.product == "mock"

    @property
    def dates(self) -> tuple[str, ...]:
        return tuple(str(d) for d in self.meta.get("dates", ()))


# Keyed by (resolved_dir_str, file_sig, fingerprint, validate, check_h3, strict_stats_count)
_BUNDLE_CACHE: dict[tuple[Any, ...], CaseBundle] = {}
_CONFIG_CACHE: dict[tuple[str, int], StudyCasesConfig] = {}


def clear_loader_cache() -> None:
    """Clear in-memory artefact and config caches (useful in tests and hot-reload hooks)."""
    _BUNDLE_CACHE.clear()
    _CONFIG_CACHE.clear()


def load_study_cases_config(
    config_path: pathlib.Path | str = DEFAULT_STUDY_CASES_PATH,
) -> StudyCasesConfig:
    """Load and validate `config/study_cases.yaml`."""
    path = pathlib.Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Study cases config not found: {path}")

    mtime_ns = path.stat().st_mtime_ns
    cache_key = (str(path), mtime_ns)
    if cache_key in _CONFIG_CACHE:
        return _CONFIG_CACHE[cache_key]

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise C.ContractError(f"Invalid YAML root in {path}: expected mapping")

    schema_version = str(raw.get("schema_version", ""))
    if schema_version != C.SCHEMA_VERSION:
        raise C.ContractError(
            f"study_cases.yaml schema_version is {schema_version!r}, expected {C.SCHEMA_VERSION!r}"
        )

    primary = str(raw.get("primary", ""))
    if primary not in C.CASE_IDS:
        raise C.ContractError(
            f"study_cases.yaml primary={primary!r} is not in frozen CASE_IDS {list(C.CASE_IDS)}"
        )

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, Mapping) or not raw_cases:
        raise C.ContractError(f"study_cases.yaml has no 'cases' mapping in {path}")

    cases: dict[str, StudyCaseConfig] = {}
    for slug, entry in raw_cases.items():
        if slug not in C.CASE_IDS:
            raise C.ContractError(
                f"Undeclared case {slug!r} in study_cases.yaml; allowed {list(C.CASE_IDS)}"
            )
        bbox_raw = entry.get("bbox", ())
        if len(bbox_raw) != 4:
            raise C.ContractError(f"Case {slug!r} bbox must have 4 numbers [W, S, E, N]")
        ref_raw = entry.get("reference_point", ())
        if len(ref_raw) != 2:
            raise C.ContractError(f"Case {slug!r} reference_point must be [lon, lat]")
        poly_raw = entry.get("aoi_polygon") or ()
        if not poly_raw:
            aoi_geojson = (path.parent / "aoi" / f"{slug}.geojson")
            if aoi_geojson.is_file():
                try:
                    geo = json.loads(aoi_geojson.read_text(encoding="utf-8"))
                    feats = geo.get("features") if geo.get("type") == "FeatureCollection" else [geo]
                    geom = (feats[0].get("geometry") if feats else None) or {}
                    gtype = geom.get("type")
                    coords = geom.get("coordinates") or ()
                    if gtype == "Polygon" and coords:
                        poly_raw = coords[0]
                    elif gtype == "MultiPolygon" and coords and coords[0]:
                        poly_raw = coords[0][0]
                except Exception:  # noqa: BLE001
                    poly_raw = ()
        det_lim = entry.get("detection_limit_mm_yr")

        cases[slug] = StudyCaseConfig(
            case_id=slug,
            name=str(entry.get("name", slug)),
            role=str(entry.get("role", "")),
            bbox=(float(bbox_raw[0]), float(bbox_raw[1]), float(bbox_raw[2]), float(bbox_raw[3])),
            aoi_polygon=tuple((float(pt[0]), float(pt[1])) for pt in poly_raw),
            crs_publish=str(entry.get("crs_publish", C.CRS_OUTPUT)),
            crs_analysis=str(entry.get("crs_analysis", C.CRS_OUTPUT)),
            reference_point=(float(ref_raw[0]), float(ref_raw[1])),
            revisit_days=int(entry.get("revisit_days", 12)),
            expected_regime=str(entry.get("expected_regime", "unknown")),
            published_rate_mm_yr=float(entry.get("published_rate_mm_yr", 0.0)),
            null_result=bool(entry.get("null_result", False)),
            detection_limit_mm_yr=float(det_lim) if det_lim is not None else None,
            limitations=tuple(str(item) for item in (entry.get("limitations") or ())),
            mock=dict(entry.get("mock") or {}),
        )

    proc_raw = raw.get("processing") or {}
    processing = ProcessingConfig(
        h3_resolution=int(proc_raw.get("h3_resolution", 9)),
        coherence_min=float(proc_raw.get("coherence_min", 0.35)),
        k_sigma=float(proc_raw.get("k_sigma", 2.5)),
        min_area_m2=int(proc_raw.get("min_area_m2", 500)),
        dates_start=str(proc_raw.get("dates_start", "2026-07-02")),
        dates_step_days=int(proc_raw.get("dates_step_days", 12)),
        dates_count=int(proc_raw.get("dates_count", 12)),
    )

    parsed = StudyCasesConfig(
        schema_version=schema_version,
        primary=primary,
        cases=cases,
        processing=processing,
    )
    _CONFIG_CACHE[cache_key] = parsed
    return parsed


def resolve_case_dir(
    case_id: str,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> pathlib.Path:
    """Resolve the directory holding the four artefacts for `case_id`."""
    if case_id not in C.CASE_IDS:
        raise C.ContractError(
            f"Unknown case_id {case_id!r}; frozen CASE_IDS are {list(C.CASE_IDS)}"
        )

    root = pathlib.Path(processed_dir)
    discovered = discover_case_dirs(root)
    if case_id in discovered:
        folder = discovered[case_id]
        missing = [
            fname for fname in CASE_ARTIFACT_FILENAMES.values() if not (folder / fname).is_file()
        ]
        if missing:
            raise CaseNotFoundError(
                f"Case {case_id!r} directory {folder} is incomplete; missing {missing}"
            )
        return folder

    raise CaseNotFoundError(
        f"No processed artefacts found for case {case_id!r} under {root}. "
        f"Expected {root / case_id} with {list(CASE_ARTIFACT_FILENAMES.values())}."
    )


def list_available_cases(
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
    config_path: pathlib.Path | str | None = DEFAULT_STUDY_CASES_PATH,
) -> list[str]:
    """List case slugs that have a complete four-artefact set on disk (`primary` ordered first)."""
    root = pathlib.Path(processed_dir)
    discovered = discover_case_dirs(root)
    complete: list[str] = []
    for slug, folder in discovered.items():
        if slug not in C.CASE_IDS:
            continue
        if all((folder / fname).is_file() for fname in CASE_ARTIFACT_FILENAMES.values()):
            complete.append(slug)

    primary: str | None = None
    if config_path is not None:
        cfg_file = pathlib.Path(config_path)
        if cfg_file.is_file():
            try:
                primary = load_study_cases_config(cfg_file).primary
            except Exception:  # noqa: BLE001
                primary = None

    ordered: list[str] = []
    if primary and primary in complete:
        ordered.append(primary)
    for slug in C.CASE_IDS:
        if slug in complete and slug not in ordered:
            ordered.append(slug)
    return ordered


def _normalise_grid(grid: pd.DataFrame) -> pd.DataFrame:
    df = grid.copy()
    numeric_cols = (
        "lat",
        "lon",
        "val_t1",
        "val_t2",
        "delta_change",
        "velocity_mm_yr",
        "seasonal_amp_mm",
        "ratio_R",
        "coherence",
    )
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="raise").astype("float64")
    string_cols = (
        "cell_id",
        "land_use",
        "risk_level",
        "regime",
        "case_id",
        "sensor",
        "product",
        "unit",
        "velocity_unit",
        "source",
        "provenance_id",
    )
    for col in string_cols:
        if col in df.columns:
            df[col] = df[col].astype(str)
    if "acquisition_date" in df.columns:
        df["acquisition_date"] = df["acquisition_date"].where(
            df["acquisition_date"].isna(), df["acquisition_date"].astype(str)
        )
    return df.reset_index(drop=True)


def _normalise_timeseries(series: pd.DataFrame) -> pd.DataFrame:
    df = series.copy()
    for col in ("value", "trend_mm_yr", "seasonal_mm"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="raise").astype("float64")
    for col in ("case_id", "cell_id", "date", "unit", "sensor", "product", "quality", "provenance_id"):
        if col in df.columns:
            df[col] = df[col].astype(str)
    if {"cell_id", "date"} <= set(df.columns):
        df = df.sort_values(["cell_id", "date"], kind="mergesort").reset_index(drop=True)
    return df


def _clone_bundle(bundle: CaseBundle) -> CaseBundle:
    """Return a defensive copy so caller mutations cannot poison the cache."""
    return CaseBundle(
        case_id=bundle.case_id,
        grid=bundle.grid.copy(deep=True),
        timeseries=bundle.timeseries.copy(deep=True),
        meta=copy.deepcopy(dict(bundle.meta)),
        # Shallow on purpose. `context` is a GeoJSON FeatureCollection whose nested geometry is the
        # largest object in the bundle (~700 features, ~1 MB per case) and no consumer mutates it:
        # `prepare_context_for_map` copies only each feature's `properties` before styling it, and the
        # feature dicts with their geometry stay shared — so nothing may write into them. A deep copy
        # here cost 61 % of every warm rerun once the OSM
        # context layers landed (0.63 s -> 1.99 s for mexico-city, measured). Top-level isolation is
        # kept, so a caller still cannot rebind a key or add one and poison the cache.
        context=dict(bundle.context),
        case_config=bundle.case_config,
        validation=bundle.validation,
        source_dir=bundle.source_dir,
    )


def load_case_bundle(
    case_id: str,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
    config_path: pathlib.Path | str | None = DEFAULT_STUDY_CASES_PATH,
    validate: bool = True,
    check_h3: bool = True,
    strict_stats_count: bool = True,
) -> CaseBundle:
    """Load, validate and cache the four artefacts for `case_id`.

    Raises:
        ContractError: if `case_id` is not in `contracts.CASE_IDS`.
        CaseNotFoundError: if the artefact files are missing on disk.
        ArtifactValidationError: if `validate=True` and any contract or cross-artefact rule fails.
    """
    folder = resolve_case_dir(case_id, processed_dir=processed_dir).resolve()

    paths = {k: folder / fname for k, fname in CASE_ARTIFACT_FILENAMES.items()}
    file_sig = tuple((k, p.stat().st_mtime_ns, p.stat().st_size) for k, p in sorted(paths.items()))
    total_bytes = sum(size for _, _, size in file_sig)
    fingerprint = C.contract_fingerprint()

    cfg_mtime = 0
    case_cfg: StudyCaseConfig | None = None
    h3_res = 9
    if config_path is not None:
        cfg_file = pathlib.Path(config_path)
        if cfg_file.is_file():
            cfg_mtime = cfg_file.stat().st_mtime_ns
            study_cfg = load_study_cases_config(cfg_file)
            case_cfg = study_cfg.cases.get(case_id)
            h3_res = study_cfg.processing.h3_resolution

    cache_key = (
        str(folder),
        file_sig,
        cfg_mtime,
        fingerprint,
        validate,
        check_h3,
        strict_stats_count,
    )
    if cache_key in _BUNDLE_CACHE:
        return _clone_bundle(_BUNDLE_CACHE[cache_key])

    raw_grid = pd.read_parquet(paths["grid"])
    raw_ts = pd.read_csv(
        paths["timeseries"],
        dtype={"case_id": "string", "cell_id": "string", "date": "string"},
    )
    raw_meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    raw_context = json.loads(paths["context"].read_text(encoding="utf-8"))

    validation_res = validate_case_bundle(
        grid=raw_grid,
        timeseries=raw_ts,
        meta=raw_meta,
        context=raw_context,
        case_id=case_id,
        check_h3=check_h3,
        h3_resolution=h3_res,
        strict_stats_count=strict_stats_count,
        total_bytes=total_bytes,
    )
    if validate:
        validation_res.raise_for_problems()

    bundle = CaseBundle(
        case_id=case_id,
        grid=_normalise_grid(raw_grid),
        timeseries=_normalise_timeseries(raw_ts),
        meta=raw_meta,
        context=raw_context,
        case_config=case_cfg,
        validation=validation_res,
        source_dir=folder,
    )
    _BUNDLE_CACHE[cache_key] = bundle
    return _clone_bundle(bundle)


def load_all_available_bundles(
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
    config_path: pathlib.Path | str | None = DEFAULT_STUDY_CASES_PATH,
    validate: bool = True,
    check_h3: bool = True,
    strict_stats_count: bool = True,
) -> dict[str, CaseBundle]:
    """Load every complete case present under `processed_dir`, ordered with `primary` first."""
    bundles: dict[str, CaseBundle] = {}
    for slug in list_available_cases(processed_dir=processed_dir, config_path=config_path):
        bundles[slug] = load_case_bundle(
            slug,
            processed_dir=processed_dir,
            config_path=config_path,
            validate=validate,
            check_h3=check_h3,
            strict_stats_count=strict_stats_count,
        )
    return bundles


# Re-export the Rol 1 -> Rol 2 UI bridge functions expected by `app.py._loaders()`.
# Resolved lazily: `src/data/ui_bridge.py` imports this module at the top, so a module-level
# `from src.data.ui_bridge import ...` here made the import order decisive — importing
# `src.data.ui_bridge` FIRST raised `ImportError: cannot import name 'get_case_series' from partially
# initialized module`, while importing `loaders` first worked. Nothing caught it because every test
# imports `loaders` first. `tests/test_import_order.py` now checks both directions in separate
# interpreters.
def __getattr__(name: str):
    from src.data import ui_bridge

    try:
        return getattr(ui_bridge, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None



__all__ = [
    "ArtifactValidationError",
    "Case",
    "CaseBundle",
    "CaseNotFoundError",
    "DEFAULT_AOI_DIR",
    "DEFAULT_PROCESSED_DIR",
    "DEFAULT_STUDY_CASES_PATH",
    "LoaderError",
    "ProcessingConfig",
    "StudyCaseConfig",
    "StudyCasesConfig",
    "aoi_series",
    "clear_loader_cache",
    "get_case_series",
    "get_cell_detail",
    "get_cell_export",
    "get_context_exposure",
    "get_filtered_export_csv",
    "get_color_scale",
    "get_land_use_bars",
    "get_layer_counts",
    "get_map_kpis",
    "get_map_layer_df",
    "get_scale_comparison",
    "get_series_kpis",
    "get_signal_status",
    "get_view_state",
    "grid_frame",
    "kpis",
    "list_available_cases",
    "load_all_available_bundles",
    "load_availability",
    "load_case",
    "load_case_bundle",
    "load_context_layers",
    "load_documented_limits",
    "load_land_use_options",
    "load_meta",
    "load_risk_levels",
    "load_study_cases",
    "load_study_cases_config",
    "load_timeseries",
    "map_payload",
    "resolve_case_dir",
]
