#!/usr/bin/env python3
"""Mock artefact generator — NASA Space Apps 2026, "Dancing with the SARs" (Rol 3).

Writes the four frozen artefacts of `docs/data-contract-v1.md` v1.1 for one or more study cases:

    data/processed/{case}/nisar_grid.parquet
    data/processed/{case}/timeseries.csv
    data/processed/{case}/context_layers.geojson
    data/processed/{case}/meta.json

The point of this file is to unblock the app (Rol 1/2) before real NISAR data lands: the schema
is the contract's, the numbers are synthetic and every artefact says so. Nothing here writes
outside `pipeline/` and `data/processed/`.

Usage
-----
    ./scripts/python.sh pipeline/generate_mock.py --out data/processed
    ./scripts/python.sh pipeline/generate_mock.py --cities mexico-city jakarta malaga --seed 7
    ./scripts/python.sh pipeline/generate_mock.py --per-cell-series     # opt-in drill-down rows
    ./scripts/python.sh pipeline/generate_mock.py --check               # validate what is on disk

Determinism: fixed default seed, no wall clock, sorted rows, sorted JSON keys, atomic writes.
Rerunning from a clean `data/processed/` reproduces byte-identical files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python pipeline/generate_mock.py` puts pipeline/, not the repo, on sys.path
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from pipeline import context as G  # noqa: E402
from pipeline.mock import artifacts as A  # noqa: E402
from pipeline.mock import checks as K  # noqa: E402
from pipeline.mock import model as M  # noqa: E402
from pipeline.mock.cases import load_study_cases  # noqa: E402
from src.domain import contracts as C  # noqa: E402

DEFAULT_SEED = 7
DEFAULT_OUT = "data/processed"
#: Rough CSV bytes per series row, used only to pick a starting `n`; the real gate is the file size.
BYTES_PER_SERIES_ROW = 110

PER_CELL_CRITERION = (
    "opt-in drill-down: cells with coherence >= thresholds.coherence_min, ranked by |velocity_mm_yr| "
    "descending (cell_id ascending on ties), truncated so this case's timeseries.csv stays "
    f"<= {A.PER_CELL_SERIES_BUDGET_BYTES // (1024 * 1024)} MiB"
)
NO_PER_CELL_CRITERION = (
    "aoi aggregate only (default): one row per date per case. Per-cell rows are opt-in "
    "(--per-cell-series) and capped at "
    f"{A.PER_CELL_SERIES_BUDGET_BYTES // (1024 * 1024)} MiB per case"
)


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def write_timeseries(
    model: M.CaseModel,
    provenance: str,
    case_dir: Path,
    *,
    per_cell_series: bool,
) -> tuple[int, int, dict[str, object]]:
    """Write `timeseries.csv`; returns (rows, bytes, stats['per_cell_series'] entry).

    The mandatory `aoi` rows always go out. Per-cell rows are opt-in and re-written with a smaller
    `n` until the file fits the per-case cap — deterministic, because the ranking is deterministic.
    """
    aoi = M.aoi_series_frame(model, A.SENSOR, A.PRODUCT, provenance)
    path = case_dir / "timeseries.csv"
    threshold = C.SENSOR_PROFILES[A.PRODUCT].coherence_min
    n_dates = len(model.dates)

    if not per_cell_series:
        size = A.write_csv(aoi, path)
        return len(aoi), size, {
            "enabled": False,
            "criterion": NO_PER_CELL_CRITERION,
            "n_cells": 0,
            "max_bytes": A.PER_CELL_SERIES_BUDGET_BYTES,
            "file_bytes": size,
        }

    max_cells = max(1, (A.PER_CELL_SERIES_BUDGET_BYTES - 4096) // (n_dates * BYTES_PER_SERIES_ROW))
    limit = min(model.n_cells, max_cells)
    while True:
        selected = M.select_per_cell_targets(model, threshold, limit)
        frame = pd.concat(
            [aoi, M.per_cell_series_frame(model, selected, A.SENSOR, A.PRODUCT, provenance, threshold)],
            ignore_index=True,
        ).sort_values(["cell_id", "date"], kind="stable", ignore_index=True)
        size = A.write_csv(frame, path)
        if size <= A.PER_CELL_SERIES_BUDGET_BYTES or limit == 0:
            break
        limit //= 2
        _log(f"    per-cell series: {size / 1024 / 1024:.2f} MiB over budget, retrying with {limit} cells")
    return len(frame), size, {
        "enabled": True,
        "criterion": PER_CELL_CRITERION,
        "n_cells": int(selected.size),
        "max_bytes": A.PER_CELL_SERIES_BUDGET_BYTES,
        "file_bytes": size,
    }


def published_context_is_osm(case_dir: Path) -> bool:
    """Does this directory currently publish an OpenStreetMap-backed context layer?

    Read from the artefact, not from the tree: the artefact is what a reader has already seen.
    """
    path = case_dir / "context_layers.geojson"
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for feature in payload.get("features") or []:
        source = str(((feature or {}).get("properties") or {}).get("source") or "").strip()
        if source and "synthetic" not in source.lower():
            return True
    return False


def generate_case(
    case, study, out_dir: Path, seed: int, per_cell_series: bool, allow_context_downgrade: bool = False
) -> K.CaseReport:
    # The window is part of the id: a 12-date and a 62-date stack must not share a provenance. It is
    # computed BEFORE the model because the context is mapped with it (the gate requires one id
    # across grid, series and context, so the extract cannot carry an id of its own).
    provenance = A.provenance_id(case.case_id, seed, study.dates)
    model = M.build_case(case, study, seed, provenance=provenance)

    # Refuse to overwrite a published OpenStreetMap context with the declared synthetic fallback.
    # Not hypothetical: a generator run that could not see `config/context/` rewrote
    # `data/processed` in place, replacing OSM-backed layers (hundreds of features) with 12 invented
    # ones, and every gate still passed because the fallback is a legal artefact. Losing real
    # provenance has to be a decision, never a side effect of forgetting a directory.
    case_dir = out_dir / case.case_id
    if model.context.synthetic and published_context_is_osm(case_dir) and not allow_context_downgrade:
        raise SystemExit(
            f"{case.case_id}: refusing to overwrite an OpenStreetMap-backed context with the declared "
            f"synthetic fallback (pin not found: {G.pin_path(case.case_id)}). Restore the pin, or "
            "pass --allow-context-downgrade to publish invented geography on purpose."
        )
    case_dir.mkdir(parents=True, exist_ok=True)

    _log(
        f"[{case.case_id}] {model.n_cells} cells, {len(model.dates)} dates "
        f"({model.dates[0]}..{model.dates[-1]}), provenance {provenance}"
    )
    # `summarise_context` names the branch that produced the labels: pinned context, freshly mapped
    # extract, or the declared synthetic fallback. Printing "OSM extract" for a pin-only run would be
    # a claim about provenance the run cannot make.
    _log(f"    land_use: {M.land_use_summary(model)}")
    _log(f"    context:  {G.summarise_context(model.context)}")

    grid = M.grid_frame(model, A.SENSOR, A.PRODUCT, A.MOCK_SOURCE, provenance)
    grid_size = A.write_parquet(grid, case_dir / "nisar_grid.parquet")

    n_series_rows, csv_size, per_cell = write_timeseries(
        model, provenance, case_dir, per_cell_series=per_cell_series
    )

    context = A.build_context(model, provenance)
    geojson_size = A.write_geojson(context, case_dir / "context_layers.geojson")

    meta = A.build_meta(
        model,
        provenance,
        n_series_rows=n_series_rows,
        per_cell=per_cell,
        n_context_features=len(context["features"]),
    )
    meta_size = A.write_json(meta, case_dir / "meta.json")

    # Contract gate + this task's assertions, on the files just written (not on in-memory frames).
    report_meta = C.validate_all(grid=grid, timeseries=pd.read_csv(case_dir / "timeseries.csv"), meta=meta, context=context, case_id=case.case_id)
    _log(f"    validate_all (in memory): {report_meta}")
    report = K.assert_case(case, study, case_dir)
    _log(
        f"    wrote grid {grid_size / 1024:.1f} KiB, csv {csv_size / 1024:.1f} KiB, "
        f"geojson {geojson_size / 1024:.1f} KiB, meta {meta_size / 1024:.1f} KiB"
    )
    for check in report.checks:
        _log(f"    PASS {check}")
    return report


def run_check(cases, study, out_dir: Path) -> list[K.CaseReport]:
    reports: list[K.CaseReport] = []
    for case in cases:
        case_dir = out_dir / case.case_id
        _log(f"[{case.case_id}] validating {case_dir}")
        report = K.assert_case(case, study, case_dir)
        reports.append(report)
        for check in report.checks:
            _log(f"    PASS {check}")
    return reports


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the frozen mock artefacts (data contract v1.1) for the study cases.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Deterministic: same seed + same config => byte-identical output.",
    )
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"output root (default: {DEFAULT_OUT})")
    parser.add_argument(
        "--cities",
        nargs="+",
        choices=list(C.CASE_IDS),
        metavar="CASE",
        help=f"subset of cases (default: all of {list(C.CASE_IDS)})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"noise seed, also part of every provenance digest (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--per-cell-series",
        action="store_true",
        help="also emit per-cell drill-down rows (capped at 3 MiB per case)",
    )
    parser.add_argument(
        "--allow-context-downgrade",
        action="store_true",
        help=(
            "allow the declared synthetic fallback to overwrite an artefact directory that currently "
            "publishes an OpenStreetMap-backed context (refused by default)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not generate: validate the artefacts already on disk (contract + task gates)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    study = load_study_cases()
    cases = study.ordered(args.cities)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()

    try:
        if args.check:
            reports = run_check(cases, study, out_dir)
        else:
            _log(
                f"study_cases: {[c.case_id for c in cases]} (primary: {study.primary}), "
                f"seed={args.seed}, out={out_dir}"
            )
            reports = [
                generate_case(
                    case, study, out_dir, args.seed, args.per_cell_series, args.allow_context_downgrade
                )
                for case in cases
            ]
    except AssertionError as exc:
        print(f"FAIL gate:\n{exc}", file=sys.stderr)
        return 1

    total = sum(report.total_bytes for report in reports)
    print()
    print(K.summary_table(reports))
    print()
    print("validate_all (contracts.validate_all, re-read from disk):")
    for report in reports:
        print(f"  {report.case_id}: {report.contract_report}")
    print(f"total {out_dir} for these cases: {total / 1024 / 1024:.2f} MiB")
    if total > A.TOTAL_BUDGET_BYTES:
        print(
            f"FAIL budget: {total / 1024 / 1024:.2f} MiB > {A.TOTAL_BUDGET_BYTES / 1024 / 1024:.0f} MiB",
            file=sys.stderr,
        )
        return 1
    print("OK: contract + task gates passed, budget respected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
