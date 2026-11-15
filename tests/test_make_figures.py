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
8. the local projection and the shared colour limit are metric and symmetric (unit-level);
9. the *committed* `docs/figures/*.png` are not stale: a fresh regeneration into `tmp_path` is
   compared with them pixel by pixel (decoded RGBA, not md5), and every differing pixel is counted.

Why 9 exists (the defect it guards, which nothing else caught): the footer of each figure prints
its artefacts' `provenance_id`. When the provenance ids changed, all three committed PNGs kept
showing the retired ids while the artefacts carried new ones, and every gate stayed green —
`docs/submission-checklist.md` checks only mtimes, and `scripts/preflight.sh` gate 6 regenerates
into a temp dir but never compares with `docs/figures/`. Byte equality is *not* asserted (a PNG
encoder may legitimately differ); decoded pixels are.

Version skip policy: Matplotlib stamps its version into every PNG (`Software: Matplotlib
version…`). If the committed figure's stamp differs from the running `matplotlib.__version__`, the
test **skips** naming both versions and saying which figure must be regenerated under the running
version — a cross-version rendering difference is not honest evidence of staleness. Under the same
version there is no such excuse, so a stale figure fails. The gate reads the version, then asserts
on pixels; byte equality is never the assertion.

The module skips with a clear reason if `data/processed` holds no complete case: the figures are a
view of the artefacts, and there is nothing to test without them.

Hermetic: every write goes to `tmp_path`; the only reads outside it are the committed
`data/processed/` tree and the committed `docs/figures/` PNGs (read-only). No network.
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
DOCS_FIGURES = ROOT / "docs" / "figures"

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

#: Matplotlib writes this into the PNG tEXt chunk on save, e.g.
#: ``Software: Matplotlib version3.11.2, https://matplotlib.org/`` (no space after "version" in
#: 3.x). It is the only honest way to separate "the renderer changed" from "the figure is stale".
SOFTWARE_VERSION = re.compile(r"Matplotlib version\s*(?P<version>[^,\s]+)")

#: Fraction of the canvas (from the top) below which the footer provenance block is drawn.
FOOTER_BAND_FRACTION = 0.75


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


def decode_png(path: Path):
    """The pixels a viewer sees, as an (H, W, 4) uint8 RGBA array.

    Deliberately not `read_bytes()`: PNG compression level, chunk ordering and an encoder bump can
    change the bytes without changing one visible pixel, and a guard that fails on those is a
    flaky guard. The price is decoding ~13 MB per figure, which is a few hundred milliseconds.
    """
    import numpy as np
    from PIL import Image

    with Image.open(path) as handle:
        assert handle.format == "PNG", f"{path.name} is {handle.format}, not PNG"
        return np.asarray(handle.convert("RGBA"), dtype=np.uint8)


def embedded_matplotlib_version(path: Path) -> str | None:
    """The `Matplotlib version…` stamp Matplotlib wrote into this PNG, or None if unparseable."""
    from PIL import Image

    with Image.open(path) as handle:
        software = str(handle.info.get("Software") or "")
    match = SOFTWARE_VERSION.search(software)
    return match.group("version").strip() if match else None


def running_matplotlib_version() -> str:
    import matplotlib

    return str(matplotlib.__version__)


@dataclass(frozen=True)
class PixelDiff:
    """How one committed PNG differs from the freshly regenerated one (decoded pixels)."""

    name: str
    committed_shape: tuple[int, ...]
    fresh_shape: tuple[int, ...]
    differing: int
    total: int
    max_delta: int
    #: (row0, row1, col0, col1) inclusive bounding box of the differing pixels, or None.
    bbox: tuple[int, int, int, int] | None
    footer_differing: int

    @property
    def shape_mismatch(self) -> bool:
        return self.committed_shape != self.fresh_shape

    @property
    def has_drift(self) -> bool:
        return self.shape_mismatch or self.differing > 0

    def describe(self) -> str:
        if self.shape_mismatch:
            return (
                f"{self.name}: canvas changed {self.committed_shape} -> {self.fresh_shape}; "
                "the panel layout or the figure size is not what the committed upload shows"
            )
        if not self.has_drift:
            return f"{self.name}: identical decoded RGBA canvas ({self.total:,} pixels)"
        assert self.bbox is not None
        row0, row1, col0, col1 = self.bbox
        share = 100.0 * self.differing / self.total
        return (
            f"{self.name}: {self.differing:,} of {self.total:,} pixels differ ({share:.4f}%), "
            f"max channel delta {self.max_delta}/255, bbox x={col0}..{col1} y={row0}..{row1}, "
            f"{self.footer_differing:,} of them in the bottom-quarter footer band"
        )


def figure_diff(name: str, committed: Path, fresh: Path) -> PixelDiff:
    """Compare one pair by decoded channels, and quantify the difference."""
    import numpy as np

    old = decode_png(committed)
    new = decode_png(fresh)
    if old.shape != new.shape:
        return PixelDiff(name, old.shape, new.shape, 0, 0, 0, None, 0)
    # int16 so unsigned subtraction cannot wrap around.
    delta = np.abs(old.astype(np.int16) - new.astype(np.int16))
    mask = delta.max(axis=2) > 0
    differing = int(mask.sum())
    if not differing:
        return PixelDiff(name, old.shape, new.shape, 0, int(mask.size), 0, None, 0)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    footer_from = int(old.shape[0] * FOOTER_BAND_FRACTION)
    return PixelDiff(
        name=name,
        committed_shape=old.shape,
        fresh_shape=new.shape,
        differing=differing,
        total=int(mask.size),
        max_delta=int(delta.max()),
        bbox=(int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])),
        footer_differing=int(mask[footer_from:].sum()),
    )


def figure_diffs(
    committed_dir: Path, fresh_dir: Path, names: tuple[str, ...] = FIGURES
) -> list[PixelDiff]:
    """One PixelDiff per figure pair present in both directories (missing files asserted upstream)."""
    assert committed_dir.is_dir(), f"no committed figures directory: {committed_dir}"
    return [
        figure_diff(name, committed_dir / name, fresh_dir / name)
        for name in names
        if (committed_dir / name).is_file() and (fresh_dir / name).is_file()
    ]


def committed_provenance_ids() -> dict[str, str]:
    """The provenance ids the footers must print, straight from the frozen artefacts."""
    import json

    ids = {}
    for case in CASE_IDS:
        meta = PROCESSED / case / "meta.json"
        if not meta.is_file():
            continue
        with meta.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        value = (payload.get("provenance") or {}).get("provenance_id")
        if value:
            ids[case] = str(value)
    return ids


def stale_figures_report(diffs: list[PixelDiff], fresh_dir: Path) -> str:
    """A failure message that names the figure, the pixel count and the likely cause."""
    lines = [
        "committed docs/figures/*.png do not match a fresh regeneration of data/processed.",
        f"fresh run: {fresh_dir}",
        "",
        "measured difference per figure (decoded RGBA pixels):",
    ]
    lines += [f"  - {diff.describe()}" for diff in diffs]
    ids = committed_provenance_ids()
    if ids:
        lines += [
            "",
            "provenance_id the artefact footers must print (from data/processed/*/meta.json):",
            *(f"  {case} = {value}" for case, value in ids.items()),
        ]
    lines += [
        "",
        "The footer draws each artefact's provenance_id. An un-regenerated figure after the",
        "artefacts were re-frozen looks exactly like this — it can be stale while every mtime",
        "gate stays green (see this module's docstring for the 2026 provenance-id incident).",
        "Regenerate and commit:",
        "  ./scripts/python.sh scripts/make_figures.py",
    ]
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------------------------
# 9: committed figures must match a fresh regeneration (the provenance-footer drift guard)
# ---------------------------------------------------------------------------------------------


def test_committed_figures_match_a_fresh_regeneration(run: Run):
    """The three uploaded PNGs must show what `data/processed` publishes today.

    The `run` fixture is the same temp-dir regeneration the other tests use (`--out` only; the
    default `--processed` is the committed artefact tree), so this costs one PNG decode per figure
    and no extra generator run. Comparison is on decoded RGBA pixels; bytes are never asserted.

    Skip, not fail, when the committed PNG was rendered by another Matplotlib: that is a renderer
    difference, not evidence that the figure is stale. When the stamp names the running version,
    there is no such excuse and any differing pixel fails.
    """
    assert_ok(run)
    missing = [name for name in FIGURES if not (DOCS_FIGURES / name).is_file()]
    assert not missing, (
        f"committed figure(s) absent from {DOCS_FIGURES}: {', '.join(missing)}; the submission "
        "ships those PNGs, so generate and commit them: ./scripts/python.sh "
        "scripts/make_figures.py"
    )

    running = running_matplotlib_version()
    embedded: dict[str, str | None] = {
        name: embedded_matplotlib_version(DOCS_FIGURES / name) for name in FIGURES
    }
    # A fresh PNG is written by the running Matplotlib right now: it says what the stamp looks
    # like on this host, so "unparseable" can be told apart from "committed file carries none".
    fresh_stamp = embedded_matplotlib_version(run.out / FIGURES[0])
    if fresh_stamp is None:
        pytest.skip(
            "cannot read a Matplotlib version from the freshly written PNG either "
            f"(Software chunk = {embedded[FIGURES[0]]!r}, running matplotlib {running}); the "
            "metadata format changed, so cross-version and stale cannot be told apart here"
        )

    unverifiable = sorted(name for name, version in embedded.items() if version is None)
    assert not unverifiable, (
        f"{', '.join(unverifiable)}: the committed PNG carries no readable 'Matplotlib version…' "
        f"Software stamp, while a fresh render under {running} does ({fresh_stamp!r}). That is not "
        "a cross-version difference — the file was not written by this script (stripped metadata "
        "or foreign tool) — so it cannot be excused as one. Regenerate and commit it: "
        "./scripts/python.sh scripts/make_figures.py"
    )

    foreign = {name: version for name, version in embedded.items() if version != running}
    if foreign:
        detail = "; ".join(
            f"{name} was rendered by Matplotlib {version}" for name, version in foreign.items()
        )
        pytest.skip(
            f"{detail}, while the running Matplotlib is {running}. Rendered pixels are not "
            "comparable across versions, so staleness cannot be proven here; regenerate the "
            f"committed figure(s) under the running version {running} "
            "(./scripts/python.sh scripts/make_figures.py) and commit the result"
        )

    diffs = figure_diffs(DOCS_FIGURES, run.out)
    assert {diff.name for diff in diffs} == set(FIGURES), (
        f"compared {sorted(diff.name for diff in diffs)} but expected {sorted(FIGURES)}"
    )
    stale = [diff for diff in diffs if diff.has_drift]
    assert not stale, stale_figures_report(diffs, run.out)


def test_pixel_comparison_rejects_a_single_altered_pixel(tmp_path: Path):
    """Non-vacuity: the guard above must reject a mutated committed figure, with an exact count.

    A throwaway copy of `docs/figures/` gets one pixel XORed to its complement, never the tracked
    file. The comparison must then report exactly one differing pixel on that figure and nowhere
    else, with max channel delta 255 — proving the mask, the count and the message are real, and
    that a stale upload cannot pass by being "roughly the same picture".
    """
    import numpy as np
    from PIL import Image

    if not all((DOCS_FIGURES / name).is_file() for name in FIGURES):
        pytest.skip(f"no committed figures in {DOCS_FIGURES} to mutate")
    copy = tmp_path / "figures"
    shutil.copytree(DOCS_FIGURES, copy)
    target = copy / "coherence.png"
    pixels = decode_png(target).copy()
    row, col = int(pixels.shape[0] * 0.9), pixels.shape[1] // 2
    pixels[row, col, :3] ^= 0xFF  # complement the RGB channels: every channel moves by 255
    Image.fromarray(pixels, mode="RGBA").save(target)

    diffs = figure_diffs(DOCS_FIGURES, copy)
    changed = [diff for diff in diffs if diff.has_drift]
    assert [diff.name for diff in changed] == ["coherence.png"], (
        f"a one-pixel change to coherence.png must be reported for exactly that figure, got "
        f"{[diff.name for diff in changed]} (all: {[diff.describe() for diff in diffs]})"
    )
    mutated = changed[0]
    assert mutated.differing == 1, mutated.describe()
    assert mutated.max_delta == 255, mutated.describe()
    assert mutated.total == EXPECTED_PX[0] * EXPECTED_PX[1]
    assert mutated.bbox is not None and mutated.bbox[0] == row and mutated.bbox[1] == row

    report = stale_figures_report(diffs, copy)
    assert "coherence.png: 1 of 3,240,000 pixels differ" in report, report
    assert "max channel delta 255/255" in report, report
    assert "provenance_id" in report, report
