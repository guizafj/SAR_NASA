"""File-level and cross-artefact validation gate (Rol 1 — pozo).

`src/domain/contracts.py` freezes the schema of each artefact individually. This module wraps
those validators for real files on disk and adds the cross-artefact invariants that keep the UI
safe before a single component renders:

1. All four artefacts (`nisar_grid.parquet`, `timeseries.csv`, `meta.json`,
   `context_layers.geojson`) exist, parse cleanly and agree on `case_id`, `sensor`, `product`
   and `provenance_id` prefix.
2. `grid.risk_level` matches `risk_level_from_velocity(grid.velocity_mm_yr)` row by row.
3. `grid.cell_id` values are valid H3 resolution-9 indices.
4. `timeseries.csv` includes the mandatory `"aoi"` aggregate series with finite values and no
   duplicate `(cell_id, date)` rows; any cell-level series references cells present in `grid`.
5. `meta.stats.n_cells` and `meta.stats.n_dates` match the actual emitted rows/dates.
6. Total size under `data/processed/` stays within the 15 MiB budget (README §8).

CLI usage (CI gate & local check):
    ./scripts/python.sh -m src.data.validation --processed data/processed
    ./scripts/python.sh -m src.data.validation --processed data/processed --require-cases
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import h3
import pandas as pd

from src.domain import contracts as C

#: 15 MiB hard ceiling for versioned processed artefacts (README §8, contract §3).
MAX_PROCESSED_BYTES: int = 15 * 1024 * 1024

#: Expected H3 resolution frozen in contract §3 and config/study_cases.yaml.
DEFAULT_H3_RESOLUTION: int = 9

#: Canonical filenames inside each case directory (`data/processed/{case_id}/`).
CASE_ARTIFACT_FILENAMES: Mapping[str, str] = {
    "grid": "nisar_grid.parquet",
    "timeseries": "timeseries.csv",
    "meta": "meta.json",
    "context": "context_layers.geojson",
}


class ArtifactValidationError(C.ContractError):
    """Raised when one or more processed artefacts violate the frozen contract."""

    def __init__(self, case_id: str, report: Mapping[str, Sequence[str]]) -> None:
        self.case_id = case_id
        self.report: dict[str, list[str]] = {k: list(v) for k, v in report.items() if v}
        lines = [f"Artefact validation failed for case {case_id!r}:"]
        for section, problems in self.report.items():
            for problem in problems:
                lines.append(f"  [{section}] {problem}")
        super().__init__("\n".join(lines))


@dataclass(frozen=True)
class ValidationResult:
    """Structured validation outcome for a single study case."""

    case_id: str
    problems_by_section: Mapping[str, tuple[str, ...]]
    total_bytes: int = 0
    contract_fingerprint: str = field(default_factory=C.contract_fingerprint)

    @property
    def is_valid(self) -> bool:
        return not any(self.problems_by_section.values())

    @property
    def all_problems(self) -> list[str]:
        out: list[str] = []
        for section, items in self.problems_by_section.items():
            for item in items:
                out.append(f"[{section}] {item}" if not item.startswith(f"{section}:") else item)
        return out

    def raise_for_problems(self) -> None:
        if not self.is_valid:
            raise ArtifactValidationError(self.case_id, self.problems_by_section)


@dataclass(frozen=True)
class TreeValidationReport:
    """Validation outcome across the entire `data/processed/` directory."""

    processed_dir: pathlib.Path
    total_bytes: int
    max_bytes: int
    budget_problems: tuple[str, ...]
    case_results: Mapping[str, ValidationResult]

    @property
    def is_valid(self) -> bool:
        if self.budget_problems:
            return False
        return all(res.is_valid for res in self.case_results.values())


def compute_processed_bytes(
    processed_dir: pathlib.Path,
    *,
    exclude_internal: bool = True,
) -> int:
    """Sum file sizes under `processed_dir`, skipping internal `_availability` probes by default."""
    if not processed_dir.exists():
        return 0
    total = 0
    for path in processed_dir.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(processed_dir).parts
        if exclude_internal and any(part.startswith("_") for part in rel_parts):
            continue
        total += path.stat().st_size
    return total


def discover_case_dirs(processed_dir: pathlib.Path) -> dict[str, pathlib.Path]:
    """Discover case directories under `processed_dir`.

    Canonical layout:
        data/processed/{case_id}/{nisar_grid.parquet,timeseries.csv,meta.json,context_layers.geojson}

    Legacy single-case fallback (supported transparently so flat H0 mocks still boot):
        data/processed/{nisar_grid.parquet,timeseries.csv,meta.json,context_layers.geojson}
    """
    discovered: dict[str, pathlib.Path] = {}
    if not processed_dir.exists():
        return discovered

    # 1. Canonical per-case subdirectories, ordered by CASE_IDS first.
    for slug in C.CASE_IDS:
        candidate = processed_dir / slug
        if candidate.is_dir() and any(
            (candidate / fname).exists() for fname in CASE_ARTIFACT_FILENAMES.values()
        ):
            discovered[slug] = candidate

    # Also detect any unexpected non-underscore case directory so validation flags it instead of hiding it.
    for child in sorted(processed_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("_") or child.name in discovered:
            continue
        if any((child / fname).exists() for fname in CASE_ARTIFACT_FILENAMES.values()):
            discovered[child.name] = child

    # 2. Flat root fallback if no per-case directories exist (or if root meta.json is present).
    root_meta = processed_dir / CASE_ARTIFACT_FILENAMES["meta"]
    if root_meta.is_file():
        try:
            payload = json.loads(root_meta.read_text(encoding="utf-8"))
            flat_case_id = str(payload.get("case_id") or "flat-root")
        except Exception:  # noqa: BLE001
            flat_case_id = "flat-root"
        if flat_case_id not in discovered:
            discovered[flat_case_id] = processed_dir

    return discovered


def _validate_cross_artifact(
    *,
    grid: pd.DataFrame | None,
    timeseries: pd.DataFrame | None,
    meta: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    expected_case_id: str | None,
    check_h3: bool,
    h3_resolution: int,
    strict_stats_count: bool,
    strict_cell_refs: bool,
) -> list[str]:
    problems: list[str] = []
    if not isinstance(meta, Mapping):
        return problems

    meta_case = meta.get("case_id")
    if expected_case_id is not None and meta_case != expected_case_id:
        problems.append(
            f"cross_check: meta.case_id is {meta_case!r}, expected {expected_case_id!r}"
        )

    case_id = str(meta_case or expected_case_id or "")
    meta_sensor = meta.get("sensor")
    meta_product = meta.get("product")
    expected_prov_prefix = (
        f"{case_id}+{meta_product}+" if case_id and isinstance(meta_product, str) else None
    )

    grid_cells: set[str] = set()
    if isinstance(grid, pd.DataFrame) and not grid.empty:
        if "cell_id" in grid.columns:
            cell_strs = grid["cell_id"].dropna().astype(str).tolist()
            grid_cells = set(cell_strs)
            if check_h3:
                invalid_h3 = [c for c in cell_strs if not h3.is_valid_cell(c)]
                if invalid_h3:
                    problems.append(
                        f"cross_check: grid has {len(invalid_h3)} invalid H3 cell_id(s), "
                        f"sample {invalid_h3[:3]}"
                    )
                else:
                    wrong_res = [
                        c for c in cell_strs if h3.get_resolution(c) != h3_resolution
                    ]
                    if wrong_res:
                        problems.append(
                            f"cross_check: grid has {len(wrong_res)} H3 cell(s) not at "
                            f"resolution {h3_resolution}, sample {wrong_res[:3]}"
                        )

        if "sensor" in grid.columns and meta_sensor is not None:
            observed_sensors = set(grid["sensor"].dropna().astype(str).unique())
            if observed_sensors and observed_sensors != {str(meta_sensor)}:
                problems.append(
                    f"cross_check: grid.sensor {sorted(observed_sensors)} != meta.sensor {meta_sensor!r}"
                )

        if "product" in grid.columns and meta_product is not None:
            observed_products = set(grid["product"].dropna().astype(str).unique())
            if observed_products and observed_products != {str(meta_product)}:
                problems.append(
                    f"cross_check: grid.product {sorted(observed_products)} != meta.product {meta_product!r}"
                )

        if {"velocity_mm_yr", "risk_level"} <= set(grid.columns):
            velocities = pd.to_numeric(grid["velocity_mm_yr"], errors="coerce")
            if velocities.notna().all():
                expected_risk = velocities.map(C.risk_level_from_velocity)
                actual_risk = grid["risk_level"].astype(str)
                mismatches = int((expected_risk != actual_risk).sum())
                if mismatches:
                    problems.append(
                        f"cross_check: grid.risk_level disagrees with risk_level_from_velocity "
                        f"for {mismatches} row(s)"
                    )

        if expected_prov_prefix and "provenance_id" in grid.columns:
            bad_prov = [
                p
                for p in grid["provenance_id"].dropna().astype(str).unique()
                if not p.startswith(expected_prov_prefix)
            ]
            if bad_prov:
                problems.append(
                    f"cross_check: grid.provenance_id {bad_prov[:3]} does not match "
                    f"prefix {expected_prov_prefix!r}"
                )

        stats = meta.get("stats")
        if strict_stats_count and isinstance(stats, Mapping):
            n_cells = stats.get("n_cells")
            if isinstance(n_cells, int) and n_cells != len(grid):
                problems.append(
                    f"cross_check: meta.stats.n_cells ({n_cells}) != len(grid) ({len(grid)})"
                )

    if strict_stats_count and isinstance(meta.get("stats"), Mapping) and isinstance(
        meta.get("dates"), (list, tuple)
    ):
        n_dates = meta["stats"].get("n_dates")
        if isinstance(n_dates, int) and n_dates != len(meta["dates"]):
            problems.append(
                f"cross_check: meta.stats.n_dates ({n_dates}) != len(meta.dates) ({len(meta['dates'])})"
            )

    if isinstance(timeseries, pd.DataFrame) and not timeseries.empty:
        if "case_id" in timeseries.columns and case_id:
            ts_cases = set(timeseries["case_id"].dropna().astype(str).unique())
            if ts_cases != {case_id}:
                problems.append(
                    f"cross_check: timeseries.case_id {sorted(ts_cases)} != {case_id!r}"
                )

        if "sensor" in timeseries.columns and meta_sensor is not None:
            ts_sensors = set(timeseries["sensor"].dropna().astype(str).unique())
            if ts_sensors and ts_sensors != {str(meta_sensor)}:
                problems.append(
                    f"cross_check: timeseries.sensor {sorted(ts_sensors)} != meta.sensor {meta_sensor!r}"
                )

        if "product" in timeseries.columns and meta_product is not None:
            ts_products = set(timeseries["product"].dropna().astype(str).unique())
            if ts_products and ts_products != {str(meta_product)}:
                problems.append(
                    f"cross_check: timeseries.product {sorted(ts_products)} != meta.product {meta_product!r}"
                )

        if "value" in timeseries.columns:
            numeric_vals = pd.to_numeric(timeseries["value"], errors="coerce")
            if numeric_vals.isna().any():
                problems.append(
                    f"cross_check: timeseries.value has {int(numeric_vals.isna().sum())} null/non-numeric row(s)"
                )
            elif not numeric_vals.map(math.isfinite).all():
                problems.append("cross_check: timeseries.value contains non-finite values")

        if "cell_id" in timeseries.columns:
            cell_ids_ts = timeseries["cell_id"].dropna().astype(str)
            if not (cell_ids_ts == "aoi").any():
                problems.append(
                    "cross_check: timeseries is missing mandatory cell_id == 'aoi' aggregate rows"
                )
            if strict_cell_refs and grid_cells:
                detail_cells = set(cell_ids_ts.unique()) - {"aoi"}
                orphan_cells = detail_cells - grid_cells
                if orphan_cells:
                    problems.append(
                        f"cross_check: timeseries references {len(orphan_cells)} cell_id(s) "
                        f"absent from grid, sample {sorted(orphan_cells)[:3]}"
                    )

        if {"cell_id", "date"} <= set(timeseries.columns):
            dups = int(timeseries.duplicated(subset=["cell_id", "date"]).sum())
            if dups:
                problems.append(
                    f"cross_check: timeseries has {dups} duplicate (cell_id, date) row(s)"
                )

        if expected_prov_prefix and "provenance_id" in timeseries.columns:
            bad_prov = [
                p
                for p in timeseries["provenance_id"].dropna().astype(str).unique()
                if not p.startswith(expected_prov_prefix)
            ]
            if bad_prov:
                problems.append(
                    f"cross_check: timeseries.provenance_id {bad_prov[:3]} does not match "
                    f"prefix {expected_prov_prefix!r}"
                )

    if isinstance(context, Mapping):
        features = context.get("features")
        if isinstance(features, (list, tuple)):
            bad_case_features: list[int] = []
            bad_prov_features: list[int] = []
            for idx, feat in enumerate(features[:5000]):
                props = (feat or {}).get("properties") or {}
                if case_id and props.get("case_id") != case_id:
                    bad_case_features.append(idx)
                if expected_prov_prefix and not str(props.get("provenance_id", "")).startswith(
                    expected_prov_prefix
                ):
                    bad_prov_features.append(idx)
            if bad_case_features:
                problems.append(
                    f"cross_check: context has {len(bad_case_features)} feature(s) whose "
                    f"case_id != {case_id!r}"
                )
            if bad_prov_features:
                problems.append(
                    f"cross_check: context has {len(bad_prov_features)} feature(s) whose "
                    f"provenance_id does not start with {expected_prov_prefix!r}"
                )

    return problems


def validate_case_bundle(
    *,
    grid: pd.DataFrame | None,
    timeseries: pd.DataFrame | None,
    meta: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    case_id: str | None = None,
    check_h3: bool = True,
    h3_resolution: int = DEFAULT_H3_RESOLUTION,
    strict_stats_count: bool = True,
    strict_cell_refs: bool = True,
    total_bytes: int = 0,
    max_bytes: int = MAX_PROCESSED_BYTES,
) -> ValidationResult:
    """Validate an in-memory artefact bundle against `contracts.py` and cross-artefact rules."""
    resolved_case_id = str(
        case_id or (meta.get("case_id") if isinstance(meta, Mapping) else None) or "unknown"
    )

    sections: dict[str, list[str]] = {
        "files": [],
        "grid": [],
        "timeseries": [],
        "meta": [],
        "context": [],
        "cross_check": [],
        "budget": [],
    }

    if grid is None:
        sections["files"].append("missing grid dataframe (nisar_grid.parquet)")
    if timeseries is None:
        sections["files"].append("missing timeseries dataframe (timeseries.csv)")
    if meta is None:
        sections["files"].append("missing meta mapping (meta.json)")
    if context is None:
        sections["files"].append("missing context GeoJSON (context_layers.geojson)")

    contract_report = C.validate_all(
        grid=grid,
        timeseries=timeseries,
        meta=meta,
        context=context,
        case_id=case_id,
    )
    for key, probs in contract_report.items():
        sections.setdefault(key, []).extend(probs)

    sections["cross_check"].extend(
        _validate_cross_artifact(
            grid=grid,
            timeseries=timeseries,
            meta=meta,
            context=context,
            expected_case_id=case_id,
            check_h3=check_h3,
            h3_resolution=h3_resolution,
            strict_stats_count=strict_stats_count,
            strict_cell_refs=strict_cell_refs,
        )
    )

    if total_bytes > max_bytes:
        sections["budget"].append(
            f"case artefacts occupy {total_bytes / (1024 * 1024):.2f} MiB, exceeding "
            f"budget {max_bytes / (1024 * 1024):.2f} MiB"
        )

    frozen_sections = {k: tuple(v) for k, v in sections.items() if v}
    return ValidationResult(
        case_id=resolved_case_id,
        problems_by_section=frozen_sections,
        total_bytes=total_bytes,
    )


def validate_case_directory(
    case_dir: pathlib.Path,
    *,
    case_id: str | None = None,
    check_h3: bool = True,
    h3_resolution: int = DEFAULT_H3_RESOLUTION,
    strict_stats_count: bool = True,
    strict_cell_refs: bool = True,
    max_bytes: int = MAX_PROCESSED_BYTES,
) -> ValidationResult:
    """Read the four artefacts from `case_dir` and validate them without raising on I/O errors."""
    file_problems: list[str] = []
    total_bytes = 0
    for key, filename in CASE_ARTIFACT_FILENAMES.items():
        path = case_dir / filename
        if not path.is_file():
            file_problems.append(f"missing required file {path.name!r} in {case_dir}")
        else:
            total_bytes += path.stat().st_size

    grid: pd.DataFrame | None = None
    timeseries: pd.DataFrame | None = None
    meta: Mapping[str, Any] | None = None
    context: Mapping[str, Any] | None = None

    grid_path = case_dir / CASE_ARTIFACT_FILENAMES["grid"]
    if grid_path.is_file():
        try:
            grid = pd.read_parquet(grid_path)
        except Exception as exc:  # noqa: BLE001
            file_problems.append(f"failed to read {grid_path.name}: {exc}")

    ts_path = case_dir / CASE_ARTIFACT_FILENAMES["timeseries"]
    if ts_path.is_file():
        try:
            timeseries = pd.read_csv(ts_path, dtype={"case_id": "string", "cell_id": "string", "date": "string"})
        except Exception as exc:  # noqa: BLE001
            file_problems.append(f"failed to read {ts_path.name}: {exc}")

    meta_path = case_dir / CASE_ARTIFACT_FILENAMES["meta"]
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            file_problems.append(f"failed to parse {meta_path.name}: {exc}")

    ctx_path = case_dir / CASE_ARTIFACT_FILENAMES["context"]
    if ctx_path.is_file():
        try:
            context = json.loads(ctx_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            file_problems.append(f"failed to parse {ctx_path.name}: {exc}")

    inferred_case = case_id or (
        str(meta.get("case_id")) if isinstance(meta, Mapping) and meta.get("case_id") else case_dir.name
    )

    if file_problems:
        # Still run contract validators on whichever artefacts succeeded so the author gets one full report.
        partial = validate_case_bundle(
            grid=grid,
            timeseries=timeseries,
            meta=meta,
            context=context,
            case_id=case_id,
            check_h3=check_h3,
            h3_resolution=h3_resolution,
            strict_stats_count=strict_stats_count,
            strict_cell_refs=strict_cell_refs,
            total_bytes=total_bytes,
            max_bytes=max_bytes,
        )
        merged = dict(partial.problems_by_section)
        merged["files"] = tuple(file_problems)
        return ValidationResult(
            case_id=inferred_case,
            problems_by_section=merged,
            total_bytes=total_bytes,
        )

    return validate_case_bundle(
        grid=grid,
        timeseries=timeseries,
        meta=meta,
        context=context,
        case_id=case_id,
        check_h3=check_h3,
        h3_resolution=h3_resolution,
        strict_stats_count=strict_stats_count,
        strict_cell_refs=strict_cell_refs,
        total_bytes=total_bytes,
        max_bytes=max_bytes,
    )


def validate_processed_tree(
    processed_dir: pathlib.Path | str = pathlib.Path("data/processed"),
    *,
    cases: Sequence[str] | None = None,
    require_cases: bool = False,
    check_h3: bool = True,
    strict_stats_count: bool = True,
    max_bytes: int = MAX_PROCESSED_BYTES,
) -> TreeValidationReport:
    """Validate the `data/processed/` budget and every published case inside it."""
    root = pathlib.Path(processed_dir)
    budget_problems: list[str] = []
    total_bytes = compute_processed_bytes(root, exclude_internal=True)
    if total_bytes > max_bytes:
        budget_problems.append(
            f"data/processed total is {total_bytes / (1024 * 1024):.2f} MiB, exceeding "
            f"15.00 MiB ceiling ({max_bytes} bytes)"
        )

    discovered = discover_case_dirs(root)
    if cases is not None:
        selected: dict[str, pathlib.Path] = {}
        for slug in cases:
            selected[slug] = discovered.get(slug, root / slug)
        discovered = selected

    if require_cases and not discovered:
        budget_problems.append(
            f"no published case directories found under {root} (expected one or more of {list(C.CASE_IDS)})"
        )

    results: dict[str, ValidationResult] = {}
    for slug, folder in discovered.items():
        expected_id = slug if slug in C.CASE_IDS else slug
        results[slug] = validate_case_directory(
            folder,
            case_id=expected_id,
            check_h3=check_h3,
            strict_stats_count=strict_stats_count,
            max_bytes=max_bytes,
        )

    return TreeValidationReport(
        processed_dir=root,
        total_bytes=total_bytes,
        max_bytes=max_bytes,
        budget_problems=tuple(budget_problems),
        case_results=results,
    )


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate processed SAR artefacts against frozen contract v1.0."
    )
    parser.add_argument(
        "--processed",
        default="data/processed",
        help="Root directory containing processed case artefacts (default: data/processed)",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Validate specific case slug(s); repeatable. Defaults to all discovered cases.",
    )
    parser.add_argument(
        "--require-cases",
        action="store_true",
        help="Fail with exit code 1 if zero case artefacts have been generated yet.",
    )
    parser.add_argument(
        "--no-strict-stats-count",
        action="store_true",
        help="Allow meta.stats.n_cells / n_dates to differ from row counts (unit fixtures only).",
    )
    args = parser.parse_args(argv)

    report = validate_processed_tree(
        args.processed,
        cases=args.cases,
        require_cases=args.require_cases,
        strict_stats_count=not args.no_strict_stats_count,
    )

    mb = report.total_bytes / (1024 * 1024)
    limit_mb = report.max_bytes / (1024 * 1024)
    print(
        f"[validation] {report.processed_dir} — size {mb:.2f} / {limit_mb:.2f} MiB "
        f"(contract v{C.SCHEMA_VERSION}, fingerprint {C.contract_fingerprint()[:12]})"
    )
    for prob in report.budget_problems:
        print(f"  FAIL [budget] {prob}")

    if not report.case_results and not report.budget_problems:
        print("  SKIP no published case directories yet (only _availability probes present)")
        return 0

    for slug, res in report.case_results.items():
        if res.is_valid:
            print(f"  PASS [{slug}] ({res.total_bytes / 1024:.1f} KiB)")
        else:
            print(f"  FAIL [{slug}]")
            for problem in res.all_problems:
                print(f"    - {problem}")

    return 0 if report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
