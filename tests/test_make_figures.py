"""Tests for `scripts/make_figures.py` — the pitch figures must be honest *and* reproducible.

What is proven here, and why each assertion earns its place:

1. the script runs to completion through `sys.executable` into a throwaway directory (exit 0);
2. all three PNGs exist, are > 20 KB and are exactly 2400×1350 px (16:9 at 150 dpi), read back from
   the PNG header so no PIL dependency is assumed;
3. the printed summary is machine-checkable: one row per figure with path, pixel dimensions, size
   in KB and the number of cells drawn, and those counts equal what is actually in the artefacts
   (a summary that reports invented numbers would be worse than no summary);
4. `--out` is honoured, including a directory that does not exist yet;
5. a case directory that is absent is *skipped with a note*, not fatal, and the remaining panels
   are still produced;
6. rerunning is deterministic in size (no random subsampling, no timestamped layout);
7. the script never writes into `data/processed/` (it is a read-only consumer);
8. the local projection and the shared colour limit are metric and symmetric (unit-level).

The module skips with a clear reason if `data/processed` holds no complete case: the figures are a
view of the artefacts, and there is nothing to test without them.

Hermetic: every write goes to `tmp_path`; the only read outside it is the committed
`data/processed/` tree. No network.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_figures.py"
PROCESSED = ROOT / "data" / "processed"

CASE_IDS = ("mexico-city", "jakarta", "malaga")
FIGURES = (
    "velocity_shared_scale.png",
    "timeseries_three_cities.png",
    "coherence.png",
)
DPI, WIDTH_IN, HEIGHT_IN = 150, 16, 9
EXPECTED_PX = (WIDTH_IN * DPI, HEIGHT_IN * DPI)
MIN_BYTES = 20 * 1024
TIMEOUT = 300

ARTEFACTS = ("nisar_grid.parquet", "timeseries.csv", "meta.json")

SUMMARY_ROW = re.compile(
    r"^(?P<path>.+?)\s+(?P<w>\d+)x(?P<h>\d+)\s+(?P<kb>[\d.]+) KB\s+(?P<cells>[\d,]+)\s*$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def available_cases(root: Path = PROCESSED) -> list[str]:
    """Case directories holding the complete frozen artefact set."""
    return [
        case
        for case in CASE_IDS
        if all((root / case / name).is_file() for name in ARTEFACTS)
    ]


def png_size(path: Path) -> tuple[int, int]:
    """Width/height parsed from the PNG IHDR chunk — no PIL, no matplotlib."""
    raw = path.read_bytes()[:24]
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    assert raw[12:16] == b"IHDR", f"{path.name} has no IHDR chunk"
    width, height = struct.unpack(">II", raw[16:24])
    return int(width), int(height)


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """path -> (size, mtime_ns) for a tree, used to prove the script does not write into it."""
    if not root.is_dir():
        return {}
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_script(out: Path, processed: Path | None = None, mpl_config: Path | None = None):
    """Invoke the script exactly as the README/wrapper does, but into a temp directory."""
    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"
    env["MPLCONFIGDIR"] = str(mpl_config or out / "_mplconfig")
    command = [sys.executable, str(SCRIPT), "--out", str(out)]
    if processed is not None:
        command += ["--processed", str(processed)]
    return subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
    )


@dataclass
class Run:
    proc: subprocess.CompletedProcess[str]
    out: Path
    processed: Path
    cases: list[str]
    before: dict[str, tuple[int, int]]
    after: dict[str, tuple[int, int]]

    @property
    def stdout(self) -> str:
        return self.proc.stdout

    def output(self) -> str:
        """stdout+stderr, for a helpful assertion message."""
        return (self.proc.stdout + self.proc.stderr)[-2500:]


def assert_ok(run: Run) -> None:
    assert run.proc.returncode == 0, f"script failed (exit {run.proc.returncode}):\n{run.output()}"


def summary_rows(stdout: str) -> dict[str, dict]:
    rows = {}
    for match in SUMMARY_ROW.finditer(stdout):
        name = Path(match.group("path")).name
        rows[name] = {
            "path": match.group("path"),
            "width": int(match.group("w")),
            "height": int(match.group("h")),
            "kb": float(match.group("kb")),
            "cells": int(match.group("cells").replace(",", "")),
        }
    return rows


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> Run:
    cases = available_cases()
    if not cases:
        pytest.skip(
            f"data/processed has no complete case artefact set ({', '.join(ARTEFACTS)}); "
            "run ./scripts/python.sh pipeline/generate_mock.py first"
        )
    # Nested path on purpose: --out must create missing parents.
    out = tmp_path_factory.mktemp("figures") / "docs" / "figures"
    before = snapshot(PROCESSED)
    proc = run_script(out, mpl_config=tmp_path_factory.mktemp("mplconfig"))
    after = snapshot(PROCESSED)
    return Run(
        proc=proc, out=out, processed=PROCESSED, cases=cases, before=before, after=after
    )


@pytest.fixture(scope="module")
def run_malaga_only(tmp_path_factory) -> Run:
    """A processed tree with only one case: the other two must be skipped, not fatal."""
    source = PROCESSED / "malaga"
    if not all((source / name).is_file() for name in ARTEFACTS):
        pytest.skip("data/processed/malaga is incomplete; cannot exercise the skip path")
    root = tmp_path_factory.mktemp("partial") / "processed"
    shutil.copytree(source, root / "malaga")
    before = snapshot(root)
    out = tmp_path_factory.mktemp("figures_partial") / "figures"
    proc = run_script(
        out, processed=root, mpl_config=tmp_path_factory.mktemp("mplconfig_partial")
    )
    after = snapshot(root)
    return Run(
        proc=proc, out=out, processed=root, cases=["malaga"], before=before, after=after
    )


@pytest.fixture(scope="module")
def module():
    """The script as a module, for the two pure helpers (no side effects at import)."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("make_figures_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules[cls.__module__]; register before exec.
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


# ---------------------------------------------------------------------------------------------
# 1-3: the run, the files, the summary
# ---------------------------------------------------------------------------------------------


def test_script_exits_zero(run: Run):
    assert SCRIPT.is_file(), f"missing script: {SCRIPT}"
    assert_ok(run)


def test_every_figure_exists_with_expected_pixel_size(run: Run):
    assert_ok(run)
    for name in FIGURES:
        path = run.out / name
        assert path.is_file(), f"{name} was not written to {run.out}"
        assert png_size(path) == EXPECTED_PX, (
            f"{name} is {png_size(path)}, expected {EXPECTED_PX} (16:9 at {DPI} dpi); "
            "a slide-sized figure must keep its aspect, so bbox_inches='tight' is not allowed"
        )


def test_every_figure_is_larger_than_20_kb_and_not_a_duplicate(run: Run):
    assert_ok(run)
    payloads = {}
    for name in FIGURES:
        path = run.out / name
        assert path.stat().st_size > MIN_BYTES, (
            f"{name} is only {path.stat().st_size / 1024:.1f} KB; that suggests an empty or "
            "blank panel"
        )
        payloads[name] = path.read_bytes()
    assert len({hash(blob) for blob in payloads.values()}) == len(FIGURES), (
        "two figures are byte-identical — one of the builders is not drawing anything"
    )


def test_summary_reports_path_dimensions_size_and_cells(run: Run):
    assert_ok(run)
    rows = summary_rows(run.stdout)
    assert set(rows) == set(FIGURES), f"summary rows for {sorted(rows)} != {sorted(FIGURES)}"
    for name, row in rows.items():
        path = Path(row["path"])
        assert path == run.out / name, f"{name}: summary path {path} != {run.out / name}"
        assert (row["width"], row["height"]) == EXPECTED_PX
        assert row["kb"] > MIN_BYTES / 1024
        assert abs(row["kb"] * 1024 - path.stat().st_size) < 1024, (
            f"{name}: summary says {row['kb']} KB, file is {path.stat().st_size / 1024:.1f} KB"
        )
        assert row["cells"] > 0, f"{name}: cell count must be a real, non-zero number"
    assert run.stdout.count("drawn:") >= len(FIGURES), "per-panel cell counts are missing"


def test_summary_counts_match_the_artefacts(run: Run):
    """The headline counts must be countable in the parquet/CSV, not invented."""
    import pandas as pd

    assert_ok(run)
    rows = summary_rows(run.stdout)
    grid_cells = sum(
        len(pd.read_parquet(PROCESSED / case / "nisar_grid.parquet")) for case in run.cases
    )
    series_rows = sum(
        int((pd.read_csv(PROCESSED / case / "timeseries.csv")["cell_id"] == "aoi").sum())
        for case in run.cases
    )

    assert rows["velocity_shared_scale.png"]["cells"] == grid_cells
    assert rows["coherence.png"]["cells"] == grid_cells
    assert rows["timeseries_three_cities.png"]["cells"] == series_rows


def test_script_does_not_write_into_the_artefacts(run: Run):
    """A figure script that mutates its input would break provenance and rerun equality."""
    assert_ok(run)
    assert run.before == run.after, (
        "data/processed changed during the run; the figure script must be read-only"
    )


# ---------------------------------------------------------------------------------------------
# 4: --out
# ---------------------------------------------------------------------------------------------


def test_out_flag_creates_missing_directories(run: Run):
    assert_ok(run)
    assert run.out.is_dir(), f"--out directory was not created: {run.out}"
    assert run.out.name == "figures" and run.out.parent.name == "docs", (
        "the nested --out path was flattened; parents must be created as given"
    )
    assert str(run.out) in run.stdout, "the summary must say where the figures went"


# ---------------------------------------------------------------------------------------------
# 5-6: skip a missing case; rerun determinism
# ---------------------------------------------------------------------------------------------


def test_missing_case_is_skipped_not_fatal(run_malaga_only: Run):
    partial = run_malaga_only
    assert_ok(partial)
    for absent in ("mexico-city", "jakarta"):
        assert re.search(rf"note: case '{absent}' skipped", partial.stdout), (
            f"the missing case {absent} was not reported as skipped:\n{partial.output()}"
        )
    assert set(summary_rows(partial.stdout)) == set(FIGURES), (
        "the figures must still be produced for the cases that exist"
    )
    assert (partial.out / "velocity_shared_scale.png").stat().st_size > MIN_BYTES


def test_rerun_on_the_same_input_produces_equal_size_pngs(run_malaga_only: Run, tmp_path: Path):
    """Determinism: same artefacts -> same PNG sizes. Random subsampling or timestamps would fail.

    Run on the single-case tree so the check is cheap; the layout code path is identical.
    """
    assert_ok(run_malaga_only)
    second_out = tmp_path / "again"
    second = run_script(
        second_out, processed=run_malaga_only.processed, mpl_config=tmp_path / "mpl"
    )
    assert second.returncode == 0, second.stdout + second.stderr
    for name in FIGURES:
        first_size = (run_malaga_only.out / name).stat().st_size
        second_size = (second_out / name).stat().st_size
        assert first_size == second_size, (
            f"{name}: rerun changed size ({first_size} -> {second_size} bytes); the layout or the "
            "cell order is not deterministic"
        )


# ---------------------------------------------------------------------------------------------
# 7-8: unit-level guarantees behind the pictures
# ---------------------------------------------------------------------------------------------


def test_local_projection_is_metric_and_centred(module):
    """x = lon·cos(lat_mean), y = lat, in km: shapes are not stretched by the lon/lat ratio."""
    import pandas as pd

    lon = pd.Series([-4.60, -4.45, -4.30])
    lat = pd.Series([36.60, 36.72, 36.85])
    x, y = module.local_metres(lon, lat)

    assert abs(x.mean()) < 1e-9 and abs(y.mean()) < 1e-9, (
        "the data centroid must map to (0, 0), so the panel is a local frame and not an offset"
    )

    # One degree of longitude at 36.7°N is cos(lat) ≈ 0.80 of one degree of latitude.
    scale_lon = (x.iloc[2] - x.iloc[1]) / (lon.iloc[2] - lon.iloc[1])
    scale_lat = (y.iloc[2] - y.iloc[1]) / (lat.iloc[2] - lat.iloc[1])
    assert scale_lon == pytest.approx(89.2, abs=0.5), f"lon scale {scale_lon:.2f} km/deg"
    assert scale_lat == pytest.approx(110.57, abs=0.1), f"lat scale {scale_lat:.2f} km/deg"


def test_shared_colour_limit_is_symmetric_and_covers_the_data(module):
    assert module.nice_symmetric_limit(410.75) == 425.0
    assert module.nice_symmetric_limit(27.9) == 50.0
    assert module.nice_symmetric_limit(0.0) == 25.0
    assert module.nice_symmetric_limit(-3.0) == 25.0, "a negative magnitude must not raise"
