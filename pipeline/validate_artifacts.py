#!/usr/bin/env python3
"""File-level contract gate for the four frozen artefacts of `docs/data-contract-v1.md`.

This is the step-2 gate (contract §9, README §4): it checks that what is actually on disk
*cross-references correctly*, on top of `src/domain/contracts.py`, which owns the schema itself.
Nothing here re-declares field names, enums, units or thresholds: those come from
`contracts.GRID_FIELDS`, `contracts.TIMESERIES_FIELDS`, `contracts.META_REQUIRED_KEYS`,
`contracts.SENSOR_PROFILES`, etc.

What it adds to `contracts.validate_all()`
------------------------------------------
1. Case directory hygiene: the four artefacts exist and nothing undeclared sits beside them.
2. Size budget: per case and total (default 15 MB, contract §14).
3. Cross-artefact consistency: dates, case ids, sensor/product/unit, provenance ids, H3 cells.
4. Honesty checks: zero-coherence/zero-velocity cells, declared null results and their bound.
5. PASS/FAIL blocks, `--json` for CI, `--strict` to promote warnings to failures.
6. `--self-test`: builds a valid fixture in a temp dir, proves it passes, then injects one
   defect at a time and proves each is caught. Safe to run while data/processed is empty.

Fail-closed rule
----------------
A run that validates zero cases is a FAILURE, and so is a `--case` filter that matches
nothing. Only an explicit `--allow-empty` accepts a zero-case run, and such a run is then
reported as VACUOUS (it proves nothing about the artefacts). A check that cannot run is never
silently dropped: a missing H3 engine fails the run, a missing case config is a run-level
warning (a failure under `--strict`), and a directory or artefact the gate skipped is named in
the report.

Layouts accepted
----------------
    data/processed/<case-slug>/{nisar_grid.parquet,timeseries.csv,meta.json,context_layers.geojson}
    data/processed/{...the same four files...}              # single-case flat layout

`_availability/` at the processed root and any dot-directory are ignored, per the brief.

Usage
-----
    ./scripts/python.sh pipeline/validate_artifacts.py
    ./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed --json
    ./scripts/python.sh pipeline/validate_artifacts.py --case mexico-city --strict
    ./scripts/python.sh pipeline/validate_artifacts.py --allow-empty
    ./scripts/python.sh pipeline/validate_artifacts.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # running as `python pipeline/validate_artifacts.py`
    sys.path.insert(0, str(ROOT))

from src.domain import contracts as C  # noqa: E402

try:  # pandas is a hard pipeline dependency; report clearly instead of tracebacking.
    import pandas as pd
except Exception as _exc:  # noqa: BLE001
    pd = None
    _PANDAS_IMPORT_ERROR = _exc
else:
    _PANDAS_IMPORT_ERROR = None

try:
    import yaml
except Exception:  # noqa: BLE001 - case config is optional
    yaml = None

try:
    import h3
except Exception:  # noqa: BLE001 - H3 validation is a guarded extra
    h3 = None


ARTEFACTS: tuple[str, ...] = (
    "nisar_grid.parquet",
    "timeseries.csv",
    "meta.json",
    "context_layers.geojson",
)

GRID_ARTEFACT, TIMESERIES_ARTEFACT, META_ARTEFACT, CONTEXT_ARTEFACT = ARTEFACTS

#: The contract's size budget for `data/processed/` (§14). Overridable with --strict-size-mb.
DEFAULT_BUDGET_MB = 15.0

CONFIG_PATH = ROOT / "config" / "study_cases.yaml"

PROBLEM_ORDER = ("files", "size", "grid", "timeseries", "meta", "context", "cross", "honesty")
WARNING_ORDER = PROBLEM_ORDER


# =============================================================================================
# Report model
# =============================================================================================


@dataclass
class CaseReport:
    """Everything the gate learned about one case directory."""

    case_id: str
    path: Path
    dir_name: str | None = None
    problems: dict[str, list[str]] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    total_bytes: int = 0

    def problem(self, area: str, message: str) -> None:
        self.problems.setdefault(area, []).append(message)

    def warn(self, area: str, message: str) -> None:
        self.warnings.setdefault(area, []).append(message)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    @property
    def failed(self) -> bool:
        return any(self.problems.values())

    @property
    def warned(self) -> bool:
        return any(self.warnings.values())

    @property
    def status(self) -> str:
        if self.failed:
            return "FAIL"
        return "WARN" if self.warned else "PASS"

    @property
    def problem_count(self) -> int:
        return sum(len(v) for v in self.problems.values())

    @property
    def warning_count(self) -> int:
        return sum(len(v) for v in self.warnings.values())


@dataclass
class RunReport:
    processed: Path
    budget_mb: float
    strict: bool
    allow_empty: bool = False
    only_case: str | None = None
    cases: list[CaseReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # run-level, e.g. skipped config checks
    problems: list[str] = field(default_factory=list)  # run-level, e.g. total size
    config_loaded: bool = False
    vacuous: bool = False  # zero cases validated, explicitly accepted with --allow-empty

    @property
    def failed(self) -> bool:
        if self.problems or any(case.failed for case in self.cases):
            return True
        if not self.strict:
            return False
        return bool(self.warnings) or any(case.warned for case in self.cases)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    @property
    def status(self) -> str:
        # Always derived from `failed`, so `--json` status never disagrees with the exit code.
        return "fail" if self.failed else "pass"

    @property
    def cases_validated(self) -> int:
        return len(self.cases)

    @property
    def total_bytes(self) -> int:
        return sum(case.total_bytes for case in self.cases)


# =============================================================================================
# Small helpers
# =============================================================================================


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _mb(byte_count: int) -> float:
    return byte_count / 1_000_000.0


def _fmt_mb(byte_count: int) -> str:
    return f"{_mb(byte_count):.3f} MB"


def _order_keys(mapping: Mapping[str, list[str]], order: Iterable[str]) -> list[str]:
    seen = list(order)
    return [k for k in seen if k in mapping] + sorted(k for k in mapping if k not in seen)


def _iter_strings(value: Any) -> Iterable[str]:
    """Yield every string inside a JSON-like object (keys excluded)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _iter_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_strings(nested)


def _unique_values(frame: Any, column: str) -> list[str]:
    if frame is None or column not in getattr(frame, "columns", []):
        return []
    try:
        return sorted({str(v) for v in frame[column].dropna().unique()})
    except Exception:  # noqa: BLE001
        return []


def _single_value(frame: Any, column: str) -> str | None:
    values = _unique_values(frame, column)
    return values[0] if len(values) == 1 else None


def _load_config(path: Path = CONFIG_PATH) -> dict[str, Any] | None:
    if yaml is None or not path.is_file():
        return None
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - config is a convenience, not a gate
        return None
    return loaded if isinstance(loaded, dict) else None


def _peek_meta_case(path: Path) -> str | None:
    try:
        meta = json.loads((path / META_ARTEFACT).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if isinstance(meta, Mapping) and isinstance(meta.get("case_id"), str):
        return meta["case_id"]
    return None


def _h3_unavailable_reason() -> str | None:
    """Why H3 validation cannot run, or None when it can. Never a silent skip."""
    if h3 is None:
        return "h3 is not installed"
    if not (getattr(h3, "is_valid_cell", None) or getattr(h3, "is_valid_h3", None)):
        return f"h3 {getattr(h3, '__version__', '?')} exposes no is_valid_cell()/is_valid_h3()"
    return None


def _valid_h3_cell(cell: str) -> bool:
    checker = getattr(h3, "is_valid_cell", None) or getattr(h3, "is_valid_h3", None)
    if checker is None:
        return False  # cannot certify a cell: fail closed, never count it as valid
    try:
        return bool(checker(cell))
    except Exception:  # noqa: BLE001
        return False


def _h3_problems(frame: Any, column: str, artefact: str, *, skip: Iterable[str] = ()) -> list[str]:
    if h3 is None or frame is None or column not in getattr(frame, "columns", []):
        return []
    # No swallow here: the caller reports an unreadable column as a problem.
    values = {str(v) for v in frame[column].dropna().unique()} - set(skip)
    bad = sorted(cell for cell in values if not _valid_h3_cell(cell))
    if not bad:
        return []
    sample = ", ".join(bad[:3])
    return [f"{artefact}: {len(bad)} cell_id value(s) are not valid H3 cells, e.g. {sample}"]


def _fixture_provenance(case_id: str) -> str:
    return C.provenance_id(case_id, "nisar-gunw", "2026-11-14", "abcdef12")


# =============================================================================================
# Discovery
# =============================================================================================


@dataclass
class Candidate:
    dir_name: str | None  # None == flat layout at the processed root
    path: Path
    expected_case: str | None


@dataclass
class Discovery:
    """What discovery found, plus the run-level consequences of what it did not validate."""

    candidates: list[Candidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def discover_cases(
    processed: Path,
    *,
    only_case: str | None,
    config: Mapping[str, Any] | None,
) -> Discovery:
    """Find case roots. A subdirectory is a case if it holds an artefact or is a study case.

    Fail-closed: a subdirectory that looks like a case but holds nothing, and root-level
    artefacts that a per-case layout would shadow, are reported rather than silently skipped.
    """
    found = Discovery()
    if not processed.exists():
        found.problems.append(f"processed directory {_relative(processed)!r} does not exist")
        return found
    if not processed.is_dir():
        found.problems.append(f"processed path {_relative(processed)!r} is not a directory")
        return found

    cfg_cases: Mapping[str, Any] = {}
    if isinstance(config, Mapping) and isinstance(config.get("cases"), Mapping):
        cfg_cases = config["cases"]

    candidates: list[Candidate] = []

    for child in sorted(processed.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".") or child.name == "_availability":
            continue
        holds_artefact = any((child / name).exists() for name in ARTEFACTS)
        if not holds_artefact and child.name not in cfg_cases:
            found.warnings.append(
                f"subdirectory {child.name!r} holds no contract artefact and is not a configured "
                "study case: skipped, nothing in it was validated"
            )
            continue
        candidates.append(Candidate(child.name, child, child.name))

    root_artefacts = [name for name in ARTEFACTS if (processed / name).exists()]
    # Flat layout: only when no per-case subdirectory exists (a mixed tree would double-count).
    if not candidates and root_artefacts:
        meta_case = _peek_meta_case(processed)
        candidates.append(Candidate(None, processed, only_case or meta_case))
    elif candidates and root_artefacts and only_case is None:
        found.problems.append(
            "contract artefacts at the processed root "
            f"({', '.join(root_artefacts)}) are shadowed by the case subdirectories and were "
            "not validated; move them into a case directory or delete them"
        )

    if only_case is not None:
        selected: list[Candidate] = []
        for candidate in candidates:
            if candidate.dir_name == only_case:
                selected.append(candidate)
            elif candidate.dir_name is None and (
                _peek_meta_case(candidate.path) == only_case or candidate.expected_case == only_case
            ):
                selected.append(candidate)
        candidates = selected
        if not candidates:
            target = processed / only_case
            if target.is_dir():
                candidates = [Candidate(only_case, target, only_case)]

    found.candidates = candidates
    return found


# =============================================================================================
# Cross-artefact and honesty checks
# =============================================================================================


def _cross_check_identity(
    report: CaseReport,
    *,
    grid: Any,
    timeseries: Any,
    meta: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    expected_case: str | None,
    dir_name: str | None,
) -> None:
    """case_id + sensor/product/unit identity across grid, timeseries and meta."""

    def fail(message: str) -> None:
        report.problem("cross", message)

    # --- case_id: directory name, grid, timeseries, meta must all agree ---------------------
    if dir_name is not None:
        for artefact, frame in (("grid", grid), ("timeseries", timeseries)):
            values = _unique_values(frame, "case_id")
            for value in values:
                if value != dir_name:
                    fail(
                        f"{artefact}: case_id {value!r} != case directory name {dir_name!r}"
                    )
        if meta is not None and meta.get("case_id") not in (None, dir_name):
            fail(f"meta: case_id {meta.get('case_id')!r} != case directory name {dir_name!r}")

    if meta is not None and isinstance(meta.get("case_id"), str):
        meta_case = str(meta["case_id"])
        for artefact, frame in (("grid", grid), ("timeseries", timeseries)):
            values = _unique_values(frame, "case_id")
            for value in values:
                if value != meta_case:
                    fail(f"{artefact}: case_id {value!r} != meta.case_id {meta_case!r}")
        if expected_case is not None and meta_case != expected_case:
            fail(f"meta: case_id {meta_case!r} != expected case {expected_case!r}")

    for artefact, frame in (("grid", grid), ("timeseries", timeseries)):
        values = _unique_values(frame, "case_id")
        if len(values) > 1:
            fail(f"{artefact}: mixed case_id values {values}; one case per file")

    # --- sensor / product / unit -----------------------------------------------------------
    identity = ("sensor", "product", "unit")
    for field_name in identity:
        grid_values = _unique_values(grid, field_name)
        series_values = _unique_values(timeseries, field_name)
        if len(grid_values) > 1:
            fail(f"grid: mixed {field_name} values {grid_values}; one value per file")
        if len(series_values) > 1:
            fail(f"timeseries: mixed {field_name} values {series_values}; one value per file")
        grid_value = grid_values[0] if len(grid_values) == 1 else None
        series_value = series_values[0] if len(series_values) == 1 else None
        if grid_value is not None and series_value is not None and grid_value != series_value:
            fail(f"{field_name}: grid {grid_value!r} != timeseries {series_value!r}")
        if meta is not None and meta.get(field_name) is not None:
            meta_value = str(meta[field_name])
            for artefact, value in (("grid", grid_value), ("timeseries", series_value)):
                if value is not None and value != meta_value:
                    fail(f"{field_name}: {artefact} {value!r} != meta.{field_name} {meta_value!r}")
        # Unit is declared in the file, never inferred from the column name (§8).
        if field_name == "unit":
            for artefact, value in (("grid", grid_value), ("timeseries", series_value)):
                if value is not None and value != C.UNIT:
                    fail(f"{artefact}: unit {value!r} != {C.UNIT!r}")
        elif field_name == "sensor" and grid_value is not None and grid_value not in C.SENSORS:
            fail(f"grid: undeclared sensor {grid_value!r}")

    velocity_units = _unique_values(grid, "velocity_unit")
    if len(velocity_units) > 1:
        fail(f"grid: mixed velocity_unit values {velocity_units}")
    if velocity_units and velocity_units[0] != C.VELOCITY_UNIT:
        fail(f"grid: velocity_unit {velocity_units[0]!r} != {C.VELOCITY_UNIT!r}")
    if meta is not None and meta.get("velocity_unit") not in (None, C.VELOCITY_UNIT):
        fail(f"meta: velocity_unit is {meta.get('velocity_unit')!r}, expected {C.VELOCITY_UNIT!r}")

    # Pairing is a schema rule and lives in contracts.validate_grid/validate_meta; this only
    # catches the cross-file case where one artefact pairs sensor/product differently.
    grid_pair = (_single_value(grid, "sensor"), _single_value(grid, "product"))
    series_pair = (_single_value(timeseries, "sensor"), _single_value(timeseries, "product"))
    if all(grid_pair) and all(series_pair) and grid_pair != series_pair:
        fail(f"sensor/product: grid {grid_pair} != timeseries {series_pair}")

    # context case ids
    if isinstance(context, Mapping):
        stray: set[str] = set()
        for feature in (context.get("features") or [])[:5000]:
            props = (feature or {}).get("properties") or {}
            if "case_id" in props and expected_case is not None:
                if str(props["case_id"]) != expected_case:
                    stray.add(str(props["case_id"]))
        if stray:
            fail(f"context: feature case_id(s) {sorted(stray)} != expected {expected_case!r}")


def _cross_check_provenance(
    report: CaseReport,
    *,
    grid: Any,
    timeseries: Any,
    meta: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
) -> None:
    """Every provenance_id in the artefacts must resolve somewhere in meta.json (§8)."""
    if meta is None:
        return
    corpus = set(_iter_strings(meta))
    observed: dict[str, set[str]] = {}

    for artefact, frame in (("grid", grid), ("timeseries", timeseries)):
        if frame is not None and "provenance_id" in getattr(frame, "columns", []):
            try:
                observed[artefact] = {str(v) for v in frame["provenance_id"].dropna().unique()}
            except Exception as exc:  # noqa: BLE001 - an unreadable column is a failure
                report.problem(
                    "cross", f"{artefact}: could not read provenance_id column: {exc}"
                )
                observed[artefact] = set()

    if isinstance(context, Mapping):
        ids: set[str] = set()
        for feature in (context.get("features") or [])[:5000]:
            props = (feature or {}).get("properties") or {}
            if props.get("provenance_id") is not None:
                ids.add(str(props["provenance_id"]))
        observed["context"] = ids

    for artefact in ("grid", "timeseries", "context"):
        orphans = sorted(value for value in observed.get(artefact, set()) if value not in corpus)
        if orphans:
            sample = ", ".join(repr(value) for value in orphans[:3])
            report.problem(
                "cross",
                f"{artefact}: {len(orphans)} orphan provenance_id(s) not present anywhere in "
                f"meta.json: {sample}",
            )


def _check_honesty(
    report: CaseReport,
    *,
    grid: Any,
    meta: Mapping[str, Any] | None,
    case_cfg: Mapping[str, Any],
) -> None:
    """Zero-coherence/zero-velocity cells, declared null results and their detection bound."""
    if grid is not None and {"coherence", "velocity_mm_yr"} <= set(getattr(grid, "columns", [])):
        try:
            suspicious = grid[(grid["coherence"] == 0) & (grid["velocity_mm_yr"] == 0)]
            count = int(len(suspicious))
            if count:
                cells = [str(v) for v in suspicious["cell_id"].head(3)] if "cell_id" in grid else []
                sample = f", e.g. {', '.join(cells)}" if cells else ""
                report.warn(
                    "honesty",
                    f"grid: {count} cell(s) have coherence == 0 and velocity_mm_yr == 0 "
                    f"simultaneously (suspicious: a nodata sentinel would poison Málaga){sample}",
                )
        except Exception as exc:  # noqa: BLE001 - a skipped honesty check must not pass
            report.problem(
                "honesty",
                f"could not run the zero-coherence/zero-velocity check: {exc}",
            )

    if meta is None:
        return
    stats = meta.get("stats") if isinstance(meta.get("stats"), Mapping) else {}
    has_limit = isinstance(stats.get("detection_limit_mm_yr"), (int, float)) and not isinstance(
        stats.get("detection_limit_mm_yr"), bool
    )

    if case_cfg.get("null_result") is True:
        median = stats.get("median_velocity_mm_yr")
        # contracts.validate_meta already demands the bound when |median| < 5; only add the
        # config-driven case (declared null result above the 5 mm/yr heuristic).
        contract_covers = (
            isinstance(median, (int, float))
            and not isinstance(median, bool)
            and abs(float(median)) < C.NULL_RESULT_MM_YR
        )
        if not has_limit and not contract_covers:
            report.problem(
                "honesty",
                "case config declares null_result: true but meta.stats.detection_limit_mm_yr is "
                "missing; the detection bound IS the result (contract §5)",
            )
        limit = stats.get("detection_limit_mm_yr")
        if (
            isinstance(median, (int, float))
            and isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and abs(float(median)) > float(limit)
        ):
            report.warn(
                "honesty",
                f"declared null result but |median velocity| {abs(float(median)):.2f} mm/yr "
                f"exceeds detection_limit_mm_yr {float(limit):.2f}; check the claim",
            )
    else:
        median = stats.get("median_velocity_mm_yr")
        if isinstance(median, (int, float)) and abs(median) < C.NULL_RESULT_MM_YR:
            report.warn(
                "honesty",
                f"|median velocity| {abs(float(median)):.2f} mm/yr < {C.NULL_RESULT_MM_YR:g} "
                "but the case is not declared null_result in config; is this a null result?",
            )


# =============================================================================================
# One case
# =============================================================================================


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_case(
    case_dir: Path,
    *,
    expected_case: str | None,
    dir_name: str | None,
    budget_bytes: int,
    config: Mapping[str, Any] | None,
    is_root: bool = False,
) -> CaseReport:
    """Run every file-level check for one case directory."""
    report = CaseReport(
        case_id=expected_case or dir_name or case_dir.name,
        path=case_dir,
        dir_name=dir_name,
    )

    # ---- 1. file-level hygiene ------------------------------------------------------------
    try:
        entries = sorted(case_dir.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        report.problem("files", f"cannot list case directory: {exc}")
        entries = []

    present = {entry.name for entry in entries if entry.is_file()}
    for entry in entries:
        if entry.name.startswith("."):
            continue  # editor/OS droppings never gate a hackathon build
        if is_root and entry.name == "_availability":
            continue  # source probes, not contract artefacts
        if entry.name in ARTEFACTS:
            continue
        kind = "directory" if entry.is_dir() else "file"
        report.problem(
            "files",
            f"undeclared {kind} {entry.name!r} in case directory "
            f"(only the four contract artefacts are allowed)",
        )
    for artefact in ARTEFACTS:
        if not (case_dir / artefact).is_file():
            report.problem("files", f"missing required artefact {artefact!r}")

    # ---- 2. size budget -------------------------------------------------------------------
    try:
        total_bytes, per_file = _case_size(case_dir, is_root=is_root)
    except OSError as exc:
        report.problem("size", f"could not measure case size: {exc}")
        total_bytes, per_file = 0, {}
    report.total_bytes = total_bytes
    report.sizes = per_file
    if total_bytes > budget_bytes:
        largest = max(per_file.items(), key=lambda item: item[1])[0] if per_file else "n/a"
        report.problem(
            "size",
            f"case size {_fmt_mb(total_bytes)} > budget {_mb(budget_bytes):.3f} MB "
            f"(largest: {largest})",
        )

    # ---- load the four artefacts ----------------------------------------------------------
    meta: Mapping[str, Any] | None = None
    grid: Any = None
    timeseries: Any = None
    context: Mapping[str, Any] | None = None

    if META_ARTEFACT in present:
        try:
            loaded = _read_json(case_dir / META_ARTEFACT)
            if isinstance(loaded, Mapping):
                meta = loaded
            else:
                report.problem("meta", "meta.json is not a JSON object")
        except Exception as exc:  # noqa: BLE001
            report.problem("meta", f"could not parse meta.json: {exc}")

    if GRID_ARTEFACT in present:
        if pd is None:
            report.problem("grid", f"pandas unavailable, cannot read parquet: {_PANDAS_IMPORT_ERROR}")
        else:
            try:
                grid = pd.read_parquet(case_dir / GRID_ARTEFACT)
            except Exception as exc:  # noqa: BLE001
                report.problem("grid", f"could not read nisar_grid.parquet: {exc}")

    if TIMESERIES_ARTEFACT in present:
        if pd is None:
            report.problem(
                "timeseries", f"pandas unavailable, cannot read csv: {_PANDAS_IMPORT_ERROR}"
            )
        else:
            try:
                timeseries = pd.read_csv(case_dir / TIMESERIES_ARTEFACT)
            except Exception as exc:  # noqa: BLE001
                report.problem("timeseries", f"could not read timeseries.csv: {exc}")

    if CONTEXT_ARTEFACT in present:
        try:
            loaded_context = _read_json(case_dir / CONTEXT_ARTEFACT)
            if isinstance(loaded_context, Mapping):
                context = loaded_context
            else:
                report.problem("context", "context_layers.geojson is not a JSON object")
        except Exception as exc:  # noqa: BLE001
            report.problem("context", f"could not parse context_layers.geojson: {exc}")

    # ---- contracts.validate_all(): the schema itself is not re-implemented here ------------
    contract_report = C.validate_all(
        grid=grid,
        timeseries=timeseries,
        meta=meta,
        context=context,
    )
    for artefact, messages in contract_report.items():
        for message in messages:
            report.problem(artefact, message)

    # ---- H3 cells (a missing engine fails the run in validate_tree, never a silent skip) --
    h3_reason = _h3_unavailable_reason()
    if h3_reason is None:
        for artefact, frame in (("grid", grid), ("timeseries", timeseries)):
            try:
                messages = _h3_problems(
                    frame,
                    "cell_id",
                    artefact,
                    skip=("aoi",) if artefact == "timeseries" else (),
                )
            except Exception as exc:  # noqa: BLE001
                report.problem(artefact, f"could not check H3 cell_ids: {exc}")
                continue
            for message in messages:
                report.problem(artefact, message)
    else:
        report.note(f"{h3_reason}: H3 cell_id validation skipped")

    # ---- 3. cross-artefact consistency ----------------------------------------------------
    _cross_check_identity(
        report,
        grid=grid,
        timeseries=timeseries,
        meta=meta,
        context=context,
        expected_case=expected_case,
        dir_name=dir_name,
    )
    _cross_check_provenance(
        report,
        grid=grid,
        timeseries=timeseries,
        meta=meta,
        context=context,
    )

    # ---- case config: expected CRS + null-result contract --------------------------------
    cfg_cases: Mapping[str, Any] = {}
    if isinstance(config, Mapping) and isinstance(config.get("cases"), Mapping):
        cfg_cases = config["cases"]
    cfg_key = (meta or {}).get("case_id") or dir_name or expected_case
    case_cfg = cfg_cases.get(cfg_key, {}) if cfg_key in cfg_cases else {}

    if case_cfg and meta is not None:
        expected_crs = case_cfg.get("crs_publish")
        if expected_crs and meta.get("crs") not in (None, expected_crs):
            report.problem(
                "cross",
                f"meta: crs {meta.get('crs')!r} != config crs_publish {expected_crs!r}",
            )

    # ---- 4. honesty checks ----------------------------------------------------------------
    _check_honesty(report, grid=grid, meta=meta, case_cfg=case_cfg)

    if meta is not None:
        grid_cells = _single_value(grid, "provenance_id") if grid is not None else None
        if grid_cells:
            report.note(f"provenance_id in use: {grid_cells}")
    return report


def _case_size(case_dir: Path, *, is_root: bool) -> tuple[int, dict[str, int]]:
    """Total bytes and per-top-level-file bytes, ignoring dot entries and _availability.

    Raises OSError when the tree cannot be walked or a file cannot be stat'ed: an unmeasurable
    case must not pass the size budget by default.
    """
    total = 0
    per_file: dict[str, int] = {}
    for path in sorted(case_dir.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(case_dir).parts
        if any(part.startswith(".") for part in parts):
            continue
        if is_root and parts and parts[0] == "_availability":
            continue
        size = path.stat().st_size
        total += size
        if len(parts) == 1:
            per_file[parts[0]] = size
    return total, per_file


# =============================================================================================
# Whole tree
# =============================================================================================


def validate_tree(
    processed: Path,
    *,
    only_case: str | None = None,
    budget_mb: float = DEFAULT_BUDGET_MB,
    strict: bool = False,
    config: Mapping[str, Any] | None = None,
    allow_empty: bool = False,
) -> RunReport:
    """Validate every case under `processed`. `config=None` loads config/study_cases.yaml.

    Fail-closed: zero validated cases is a failure unless `allow_empty=True`, and an `only_case`
    filter that matches nothing is always a failure.
    """
    if config is None:
        config = _load_config()
    report = RunReport(
        processed=Path(processed),
        budget_mb=float(budget_mb),
        strict=bool(strict),
        allow_empty=bool(allow_empty),
        only_case=only_case,
        config_loaded=config is not None,
    )
    if not report.processed.exists():
        report.problems.append(
            f"processed directory {_relative(report.processed)!r} does not exist"
        )
        return report
    if not report.processed.is_dir():
        report.problems.append(
            f"processed path {_relative(report.processed)!r} is not a directory"
        )
        return report
    if config is None:
        reason = (
            "PyYAML is not installed"
            if yaml is None
            else f"{_relative(CONFIG_PATH)!r} is missing, unreadable or not a mapping"
        )
        report.warnings.append(
            f"case config could not be loaded ({reason}): expected-CRS and null_result checks "
            "were skipped; --strict makes skipped checks fail"
        )
    h3_reason = _h3_unavailable_reason()
    if h3_reason is not None:
        report.problems.append(
            f"{h3_reason}: H3 cell_id validation cannot run, so the grid's primary key cannot "
            "be certified; install h3 (a core dependency) and re-run"
        )

    budget_bytes = int(budget_mb * 1_000_000)
    discovery = discover_cases(report.processed, only_case=only_case, config=config)
    report.notes.extend(discovery.notes)
    report.warnings.extend(discovery.warnings)
    report.problems.extend(discovery.problems)
    candidates = discovery.candidates

    if only_case is not None and not candidates:
        report.problems.append(
            f"case {only_case!r} not found under {_relative(report.processed)!r}: nothing was "
            "validated (a --case filter must match at least one case)"
        )

    for candidate in candidates:
        is_root = candidate.dir_name is None
        report.cases.append(
            inspect_case(
                candidate.path,
                expected_case=candidate.expected_case,
                dir_name=candidate.dir_name,
                budget_bytes=budget_bytes,
                config=config,
                is_root=is_root,
            )
        )

    # Total budget is checked once, naming the biggest case (§14: whole directory < 15 MB).
    if report.cases:
        total = report.total_bytes
        if total > budget_bytes:
            biggest = max(report.cases, key=lambda case: case.total_bytes)
            report.problems.append(
                f"total size {_fmt_mb(total)} > budget {budget_mb:g} MB "
                f"(largest case: {biggest.case_id}, {_fmt_mb(biggest.total_bytes)})"
            )

    # Fail-closed rule: a run that validated nothing proves nothing.
    if not report.cases and not report.problems:
        if allow_empty:
            report.vacuous = True
            report.notes.append(
                "vacuous run: 0 cases validated (--allow-empty); this run proves nothing about "
                "the artefacts"
            )
        else:
            report.problems.append(
                f"no case directories found under {_relative(report.processed)!r}: nothing was "
                "validated (0 cases); a zero-case run is a failure unless --allow-empty is passed"
            )
    return report


# =============================================================================================
# Rendering
# =============================================================================================


def report_to_dict(report: RunReport) -> dict[str, Any]:
    """Stable JSON payload for CI."""
    return {
        "contract": {
            "schema_version": C.SCHEMA_VERSION,
            "revision": C.CONTRACT_REVISION,
            "fingerprint": C.contract_fingerprint(),
        },
        "processed": str(report.processed),
        "budget_mb": report.budget_mb,
        "strict": report.strict,
        "allow_empty": report.allow_empty,
        "requested_case": report.only_case,
        "cases_validated": report.cases_validated,
        "vacuous": report.vacuous,
        "status": report.status,
        "cases": [
            {
                "case_id": case.case_id,
                "dir_name": case.dir_name,
                "path": str(case.path),
                "status": case.status,
                "problems": {k: list(v) for k, v in case.problems.items()},
                "warnings": {k: list(v) for k, v in case.warnings.items()},
                "notes": list(case.notes),
                "sizes_bytes": dict(case.sizes),
                "total_bytes": case.total_bytes,
            }
            for case in report.cases
        ],
        "problems": list(report.problems),
        "warnings": list(report.warnings),
        "notes": list(report.notes),
        "totals": {
            "cases": len(report.cases),
            "cases_validated": report.cases_validated,
            "passed": sum(1 for case in report.cases if not case.failed),
            "failed": sum(1 for case in report.cases if case.failed),
            "warnings": sum(case.warning_count for case in report.cases)
            + len(report.warnings),
            "problems": sum(case.problem_count for case in report.cases) + len(report.problems),
            "total_bytes": report.total_bytes,
            "total_mb": round(_mb(report.total_bytes), 3),
        },
        "exit_code": report.exit_code,
    }


def render_human(report: RunReport) -> str:
    lines: list[str] = []
    lines.append(
        f"contract gate v{C.SCHEMA_VERSION} rev {C.CONTRACT_REVISION} — "
        f"processed: {_relative(report.processed)}"
    )
    lines.append(
        f"size budget: {report.budget_mb:g} MB per case and total"
        + ("  [strict: warnings fail]" if report.strict else "")
    )

    for case in report.cases:
        lines.append("")
        lines.append(f"{case.status} {case.case_id}")
        lines.append(f"  path: {_relative(case.path)}")
        lines.append(f"  size: {_fmt_mb(case.total_bytes)}")
        for area in _order_keys(case.problems, PROBLEM_ORDER):
            for message in case.problems[area]:
                lines.append(f"  FAIL [{area}] {message}")
        for area in _order_keys(case.warnings, WARNING_ORDER):
            for message in case.warnings[area]:
                lines.append(f"  WARN [{area}] {message}")
        for note in case.notes:
            lines.append(f"  note: {note}")
        if not case.failed and not case.warned:
            lines.append("  all four artefacts present, contract-valid and cross-consistent")

    if report.problems:
        lines.append("")
        lines.append("RUN-LEVEL FAILURES")
        for message in report.problems:
            lines.append(f"  FAIL {message}")

    if report.warnings:
        lines.append("")
        lines.append("RUN-LEVEL WARNINGS")
        for message in report.warnings:
            lines.append(f"  WARN {message}")

    if report.notes:
        lines.append("")
        for note in report.notes:
            lines.append(f"note: {note}")

    lines.append("")
    lines.append("summary")
    if report.cases:
        passed = sum(1 for case in report.cases if not case.failed)
        failed = sum(1 for case in report.cases if case.failed)
        warned = sum(1 for case in report.cases if case.warned)
        lines.append(f"  cases: {passed} passed, {failed} failed, {warned} with warnings")
        lines.append(
            f"  problems: {sum(c.problem_count for c in report.cases) + len(report.problems)}"
        )
        lines.append(
            f"  warnings: {sum(c.warning_count for c in report.cases) + len(report.warnings)}"
        )
        lines.append(
            f"  total size: {_fmt_mb(report.total_bytes)} / {report.budget_mb:g} MB"
        )
    elif report.vacuous:
        lines.append("  cases: 0 validated")
        lines.append(
            "  VACUOUS RUN: no case was validated (--allow-empty); this run proves nothing "
            "about the artefacts"
        )
    else:
        lines.append("  cases: 0 validated")
        lines.append(
            "  no case was validated (see run-level failures above)"
            if report.problems
            else "  no case directories found: nothing to validate"
        )
    lines.append(
        "  RESULT: "
        + ("PASS" if not report.failed else "FAIL")
        + f" (exit {report.exit_code})"
        + (" [VACUOUS: 0 cases validated]" if report.vacuous else "")
    )
    return "\n".join(lines)


# =============================================================================================
# Self-test: a valid fixture plus one injected defect at a time
# =============================================================================================

_FIXTURE_CELLS = ("8928308280fffff", "8928308280bffff", "89283082807ffff")


def build_fixture(root: Path, case_id: str = "mexico-city") -> Path:
    """Write a minimal, contract-valid case directory under `root`; return its path."""
    if pd is None:
        raise RuntimeError(f"pandas is required to build the fixture: {_PANDAS_IMPORT_ERROR}")
    case_dir = Path(root) / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    provenance = _fixture_provenance(case_id)
    dates = ["2026-07-02", "2026-07-14"]
    # The v1.1 seasonal trio is derived here by the pipeline's own rule, so the fixture cannot
    # drift from `transforms.classify_regime` without the self-test noticing.
    from pipeline.science.transforms import classify_regime  # noqa: PLC0415 - fixture-only import

    # Published cumulative series: 0 -> -7.4 mm over the 12-day window of the fixture.
    aoi_values = (0.0, -7.4)
    aoi_trend = aoi_values[-1] / (12.0 / 365.25)
    seasonal_amp_mm = 9.4

    rows = []
    for index, cell in enumerate(_FIXTURE_CELLS):
        val_t1 = 0.0
        val_t2 = (-7.4, -3.1, -0.4)[index]
        velocity = (-128.0, -60.0, 4.0)[index]
        ratio = abs(velocity) / seasonal_amp_mm
        rows.append(
            {
                "cell_id": cell,
                "lat": 19.4326 + index * 0.002,
                "lon": -99.1332 + index * 0.002,
                "val_t1": val_t1,
                "val_t2": val_t2,
                "delta_change": val_t2 - val_t1,
                "velocity_mm_yr": velocity,
                "seasonal_amp_mm": seasonal_amp_mm,
                "ratio_R": ratio,
                "regime": classify_regime(velocity, seasonal_amp_mm),
                "coherence": 0.82 - index * 0.05,
                "land_use": "urban-dense",
                "risk_level": C.risk_level_from_velocity(velocity),
                "case_id": case_id,
                "sensor": "nisar",
                "product": "nisar-gunw",
                "unit": C.UNIT,
                "velocity_unit": C.VELOCITY_UNIT,
                "source": "ASF DAAC",
                "acquisition_date": dates[-1],
                "provenance_id": provenance,
            }
        )
    grid = pd.DataFrame(rows)
    grid.to_parquet(case_dir / GRID_ARTEFACT, index=False)

    series = pd.DataFrame(
        [
            {
                "case_id": case_id,
                "cell_id": "aoi",
                "date": date,
                "value": value,
                "trend_mm_yr": aoi_trend,
                "seasonal_mm": 0.0,
                "unit": C.UNIT,
                "sensor": "nisar",
                "product": "nisar-gunw",
                "quality": "good",
                "provenance_id": provenance,
            }
            for date, value in zip(dates, aoi_values, strict=True)
        ]
    )
    series.to_csv(case_dir / TIMESERIES_ARTEFACT, index=False)

    meta = {
        "schema_version": C.SCHEMA_VERSION,
        "case_id": case_id,
        "name": "Mexico City, MX",
        "bbox": [-99.35, 19.20, -98.85, 19.65],
        "crs": C.CRS_OUTPUT,
        "reference_point": [-99.13, 19.43],
        "dates": dates,
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
            "product_id": "NISAR_L2_PR_GUNW_FIXTURE",
            "processing_date": "2026-11-14",
            "method": "LOS to vertical (v_north=0), H3 res 9 mean",
            "retrieved_utc": "2026-11-14T16:02:00Z",
            "reference": "Herrera-García et al. 2021, doi:10.1126/science.abb8549",
            "license": "NASA open data",
            "provenance_id": provenance,
        },
        "stats": {
            "n_cells": len(rows),
            "n_dates": len(dates),
            "median_velocity_mm_yr": -60.0,
            "median_seasonal_amp_mm": seasonal_amp_mm,
            "median_ratio_R": 60.0 / seasonal_amp_mm,
            "dominant_regime": "inelastic",
            "p05_velocity_mm_yr": -128.0,
            "p95_velocity_mm_yr": 4.0,
        },
        "limitations": ["Fixture: 80 m posting, city scale, not building scale."],
        "null_policy": "Cells without data are absent from the grid; never written as 0.",
    }
    (case_dir / META_ARTEFACT).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

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
                    "case_id": case_id,
                    "crs": C.CRS_OUTPUT,
                    "source": "SACMEX",
                    "provenance_id": provenance,
                },
            }
        ],
    }
    (case_dir / CONTEXT_ARTEFACT).write_text(
        json.dumps(context, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return case_dir


def _all_problems(report: RunReport) -> list[str]:
    messages: list[str] = []
    for case in report.cases:
        for area in _order_keys(case.problems, PROBLEM_ORDER):
            messages.extend(case.problems[area])
    messages.extend(report.problems)
    return messages


def _all_warnings(report: RunReport) -> list[str]:
    messages: list[str] = []
    for case in report.cases:
        for area in _order_keys(case.warnings, WARNING_ORDER):
            messages.extend(case.warnings[area])
    return messages


def _expect(
    label: str,
    report: RunReport,
    *,
    ok: bool,
    needle: str | None = None,
    verbose: bool = True,
) -> bool:
    if needle is None:
        passed = ok
    else:
        haystack = "\n".join(_all_problems(report))
        passed = (not ok) and needle in haystack
    if verbose:
        suffix = "" if passed else f" (expected {needle!r})"
        print(f"self-test: {'PASS' if passed else 'FAIL'} {label}{suffix}")
    return passed


def run_self_test(*, verbose: bool = True) -> int:
    """Prove the gate works without touching data/processed. Returns 0 when all tests pass."""
    results: list[bool] = []
    flat_config: Mapping[str, Any] = {}  # hermetic: never depend on the live config file

    with tempfile.TemporaryDirectory(prefix="contract-gate-selftest-") as tmp:
        tmp_path = Path(tmp)

        def fresh(tag: str) -> tuple[Path, Path]:
            root = tmp_path / tag
            return root, build_fixture(root)

        # Positive: a minimal fixture must pass cleanly.
        root, _case = fresh("valid")
        report = validate_tree(root, config=flat_config)
        results.append(
            _expect(
                "valid fixture passes",
                report,
                ok=bool(not report.failed and not _all_warnings(report) and report.cases),
                verbose=verbose,
            )
        )

        # Negative 1: missing file.
        root, case = fresh("missing-file")
        (case / TIMESERIES_ARTEFACT).unlink()
        report = validate_tree(root, config=flat_config)
        results.append(
            _expect(
                "missing timeseries.csv",
                report,
                ok=False,
                needle=f"missing required artefact {TIMESERIES_ARTEFACT!r}",
                verbose=verbose,
            )
        )

        # Negative 2: delta_change no longer equals val_t2 - val_t1.
        root, case = fresh("bad-delta")
        frame = pd.read_parquet(case / GRID_ARTEFACT)
        frame.loc[0, "delta_change"] = float(frame.loc[0, "delta_change"]) + 1.0
        frame.to_parquet(case / GRID_ARTEFACT, index=False)
        report = validate_tree(root, config=flat_config)
        results.append(
            _expect(
                "bad delta_change",
                report,
                ok=False,
                needle="delta_change != val_t2 - val_t1",
                verbose=verbose,
            )
        )

        # Negative 3: valid-format provenance_id that resolves nowhere in meta.json.
        root, case = fresh("orphan-provenance")
        context_path = case / CONTEXT_ARTEFACT
        context = json.loads(context_path.read_text(encoding="utf-8"))
        context["features"][0]["properties"]["provenance_id"] = C.provenance_id(
            "mexico-city", "nisar-gunw", "2026-01-01", "deadbeef"
        )
        context_path.write_text(json.dumps(context), encoding="utf-8")
        report = validate_tree(root, config=flat_config)
        results.append(
            _expect(
                "orphan provenance_id",
                report,
                ok=False,
                needle="orphan provenance_id",
                verbose=verbose,
            )
        )

        # Negative 4: size budget. One contract artefact is padded (still valid JSON) so the
        # file itself is oversized while every schema check keeps passing.
        root, case = fresh("oversized")
        context_path = case / CONTEXT_ARTEFACT
        context_path.write_text(
            context_path.read_text(encoding="utf-8") + " " * 60_000, encoding="utf-8"
        )
        report = validate_tree(root, config=flat_config, budget_mb=0.02)
        results.append(
            _expect(
                "oversized artefact",
                report,
                ok=False,
                needle="(largest: context_layers.geojson)",
                verbose=verbose,
            )
        )

        # Negative 5: a timeseries date outside meta.dates.
        root, case = fresh("date-outside-meta")
        series = pd.read_csv(case / TIMESERIES_ARTEFACT)
        series.loc[0, "date"] = "2026-08-01"
        series.to_csv(case / TIMESERIES_ARTEFACT, index=False)
        report = validate_tree(root, config=flat_config)
        results.append(
            _expect(
                "date outside meta.dates",
                report,
                ok=False,
                needle="not in meta.dates",
                verbose=verbose,
            )
        )

        # Positive safety net: the fixture still passes in strict mode (no hidden warnings).
        root, _case = fresh("strict-clean")
        report = validate_tree(root, config=flat_config, strict=True)
        results.append(
            _expect("strict mode stays clean", report, ok=not report.failed, verbose=verbose)
        )

        # Fail-closed: an empty tree is a failure unless explicitly allowed, and an accepted
        # empty run must be reported as vacuous rather than as a pass on the artefacts.
        empty = tmp_path / "empty-tree"
        empty.mkdir(parents=True)
        report = validate_tree(empty, config=flat_config)
        results.append(
            _expect(
                "empty tree fails",
                report,
                ok=report.failed and report.exit_code == 1,
                verbose=verbose,
            )
        )
        report = validate_tree(empty, config=flat_config, allow_empty=True)
        results.append(
            _expect(
                "empty tree accepted only with --allow-empty (vacuous)",
                report,
                ok=not report.failed and report.vacuous,
                verbose=verbose,
            )
        )
        report = validate_tree(empty, config=flat_config, only_case="does-not-exist")
        results.append(
            _expect(
                "--case that matches nothing fails",
                report,
                ok=report.failed,
                verbose=verbose,
            )
        )

    passed = sum(results)
    total = len(results)
    if verbose or passed != total:
        print(f"\nself-test: {passed}/{total} checks passed")
    return 0 if passed == total else 1


# =============================================================================================
# CLI
# =============================================================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "File-level gate for data/processed: contract schema via src/domain/contracts.py "
            "plus file hygiene, size budget, cross-artefact consistency and honesty checks."
        )
    )
    parser.add_argument(
        "--processed",
        default="data/processed",
        help="directory holding case subdirectories or the four artefacts (default: data/processed)",
    )
    parser.add_argument("--case", default=None, help="validate only this case slug")
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="warnings also fail the gate (size overruns always fail)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        dest="allow_empty",
        help=(
            "accept a run that validates zero cases; it is reported as VACUOUS, never as a "
            "pass on the artefacts (a --case filter that matches nothing still fails)"
        ),
    )
    parser.add_argument(
        "--strict-size-mb",
        type=float,
        default=DEFAULT_BUDGET_MB,
        metavar="MB",
        help=f"per-case and total size budget in MB (default: {DEFAULT_BUDGET_MB:g})",
    )
    parser.add_argument(
        "--require-cases",
        action="store_true",
        dest="require_cases",
        help=(
            "fail when zero case directories are found. This gate already behaves that way "
            "(a zero-case run proves nothing); the flag exists so this invocation matches the "
            "`src.data.validation` gate, which needs it explicitly"
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="build a temp fixture, prove PASS, then inject one defect at a time",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.strict_size_mb <= 0:
        print("error: --strict-size-mb must be > 0", file=sys.stderr)
        return 2
    if pd is None:
        print(f"error: pandas is required: {_PANDAS_IMPORT_ERROR}", file=sys.stderr)
        return 2

    report = validate_tree(
        Path(args.processed),
        only_case=args.case,
        budget_mb=args.strict_size_mb,
        strict=args.strict,
        allow_empty=args.allow_empty,
    )
    if args.require_cases and not report.cases:
        # Redundant by design (validate_tree already fails a zero-case run unless --allow-empty),
        # stated here so the flag can never look like it silently changed the outcome. It wins over
        # --allow-empty on purpose: "require cases" is the stricter instruction.
        report.problems.append(
            "--require-cases: zero case directories were validated under "
            f"{_relative(report.processed)!r}"
        )
    if args.as_json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(render_human(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
