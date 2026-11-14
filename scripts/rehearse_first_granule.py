#!/usr/bin/env python3
"""Offline rehearsal of ``docs/first-granule-runbook.md``.

The runbook tells an operator to run seven steps against a real NISAR L2 GUNW granule. Nobody has
executed that sequence yet. This script rehearses every step that does not need credentials or the
network:

1.  Build a synthetic three-granule GUNW stack in a temporary directory. The layout follows
    ``docs/nisar-gunw-layout.md`` §2 (frequency A, co-pol ``HH``, unwrapped/coherence/components,
    the int8 mask, pixel offsets, wrapped layers, ``metadata/radarGrid``) plus CF-style x/y
    coordinates and a ``geolocationGrid`` container. The stack carries a known, temporally linear
    subsidence field, a 127-padding patch, a ``2`` quality-flag patch, a non-finite patch and a
    water-like low-coherence patch.
2.  Run the runbook's offline commands with :mod:`subprocess` and the current interpreter, in the
    runbook's order, and check what the commands actually print.
3.  Print a table: runbook step -> command -> exit code -> verdict.
4.  Fail loudly (exit 1) on the first rehearsable step that does not behave as documented, naming
    the exact command and the tail of its stderr.

Not rehearsed here, and deliberately named in the table:

* the Earthdata credentials and the ASF EULA/authorised-app precondition;
* ASF SearchAPI search, the ``--dry-run`` plan and the real download (network/credentials);
* the real granule's coverage/geometry decision (the *threshold logic* is rehearsed);
* the GNSS/levelling sign-convention validation and removal of the provisional provenance sentence;
* the full ``scripts/check_phase_a.sh`` run, which reads ``data/processed`` and runs the whole test
  suite. Only its existence and executable bit are checked, as the brief allows.

Usage
-----
    ./scripts/python.sh scripts/rehearse_first_granule.py
    ./scripts/python.sh scripts/rehearse_first_granule.py --workdir /tmp/rehearsal
    ./scripts/python.sh scripts/rehearse_first_granule.py --json

Exit status: 0 when every rehearsable step behaved as documented, 1 when one did not, 2 for a bad
work directory or usage error. Nothing is written outside ``--workdir`` (default: a fresh
``mkdtemp`` under ``/tmp``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import h3
import h5py
import numpy as np
import pandas as pd
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python scripts/...` puts scripts/, not the repo root, on sys.path
    sys.path.insert(0, str(ROOT))

from pipeline.process_case import load_case_config  # noqa: E402
from pipeline.science import transforms as V  # noqa: E402
from src.domain import contracts as C  # noqa: E402

# ---------------------------------------------------------------------------------------------
# The rehearsal plan. Values come from the runbook, the layout note and the frozen contract.
# ---------------------------------------------------------------------------------------------

CASE = "mexico-city"
DOC_RUNBOOK = "docs/first-granule-runbook.md"

INSPECT = ROOT / "pipeline" / "ingest" / "inspect_hdf5.py"
READER = ROOT / "pipeline" / "ingest" / "nisar_gunw.py"
PROCESS = ROOT / "pipeline" / "process_case.py"
GATE = ROOT / "pipeline" / "validate_artifacts.py"
CHECK_PHASE_A = ROOT / "scripts" / "check_phase_a.sh"

#: ``docs/nisar-gunw-layout.md`` §2. Defined here rather than imported from the reader so the
#: fixture is evidence for the *documented* layout, not a copy of the code that reads it.
BASE = "/science/LSAR/GUNW"
GRIDS = f"{BASE}/grids/frequencyA"
UNWRAPPED = f"{GRIDS}/unwrappedInterferogram"
WRAPPED = f"{GRIDS}/wrappedInterferogram"
OFFSETS = f"{GRIDS}/pixelOffsets"
RADAR = f"{BASE}/metadata/radarGrid"
PARAMS = f"{BASE}/metadata/processingInformation/parameters/frequencyA"
GEOLOCATION = f"{GRIDS}/geolocationGrid"
POL = "HH"  # the fixture's co-pol; the reader must still discover it, not assume it

#: Geometry used to generate and invert the synthetic phase. The flags passed to process_case are
#: deliberately 1 degree off, to prove the documented "metadata wins" rule.
INCIDENCE_DEG = 39.0
HEADING_DEG = 192.0
FLAG_INCIDENCE_DEG = INCIDENCE_DEG - 1.0
FLAG_HEADING_DEG = HEADING_DEG - 1.0
WAVELENGTH_M = 0.2384  # docs/nisar-gunw-layout.md §4
EPSG = 32614  # Mexico City, UTM 14N (config/study_cases.yaml crs_analysis)
LOOKS = 20

#: 24 x 24 pixels at NISAR's 80 m posting, centred on the configured reference point.
NX, NY = 24, 24
SPACING_M = 80.0
REF_IX, REF_IY = NX // 2, NY // 2

SPACING_DAYS = 12
PAIRS = 3  # 3 chained pairs -> 4 epochs, transforms.MIN_FIT_POINTS = 4
START_DATE = "2026-07-02"
OFFSET_MM = 25.0
PEAK_MM_YR = -420.0  # synthetic amplitude; the shape is what is asserted, not this number
FALLOFF_PX = 4.0

#: Mask / data patches, all away from the reference pixel.
PADDING_PATCH = (0, 2, 0, 4)  # iy0:iy1, ix0:ix1 -> mask 127 (dropped)
FLAG_PATCH = (2, 4, 8, 12)  # mask 2 (kept, carried)
NAN_PATCH = (20, 23, 20, 23)  # non-finite phase/coherence (dropped)
WATER_PATCH = (20, 24, 2, 8)  # low coherence, below the frozen coherence_min (dropped)

COHERENCE_MIN = C.SENSOR_PROFILES["nisar-gunw"].coherence_min
COVERAGE_WARNING_FRACTION = 0.5  # process_case.py; docs/data-source-decision.md
PUBLISHED_RATE_BAND = (-900.0, -225.0)  # factor-2 band on -450 mm/yr (runbook Step 6)
DATE_WINDOW = ("2026-07-02", "2026-11-11")  # config/study_cases.yaml processing.dates_*

COVERAGE_RE = re.compile(
    r"coverage\s+([0-9]+(?:\.[0-9]+)?)% of (\d+) AOI cells \((\d+) covered\)"
)
JQ_ROLE_FILTER = '.expectations[]|select(.kind=="dataset")|"\\(.role)\\t\\(.present)\\t\\(.found//"-")"'
JQ_DATASET_FILTER = (
    '.datasets[]|select(.path|test("unwrappedPhase|coherenceMagnitude|connectedComponents|'
    '/mask$|numberOfLooks|radarGrid|xCoord|spatial_ref|crs"))|'
    '"\\(.path)\\t\\(.dtype)\\t\\(.units)\\t\\(.fill_value)"'
)


# ---------------------------------------------------------------------------------------------
# Reporting model
# ---------------------------------------------------------------------------------------------


@dataclass
class Row:
    """One row of the runbook rehearsal table."""

    id: str
    step: str
    check: str
    command: str
    exit_code: int | None
    verdict: str
    rehearsable: bool
    note: str = ""
    executed_command: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "runbook_step": self.step,
            "check": self.check,
            "command": self.command,
            "executed_command": self.executed_command,
            "exit_code": self.exit_code,
            "verdict": self.verdict,
            "rehearsable": self.rehearsable,
            "note": self.note,
        }


class WorkdirError(RuntimeError):
    """The work directory cannot be created or written; the CLI refuses cleanly."""


class RehearsalError(RuntimeError):
    """A rehearsable step did not behave as the runbook says it should."""

    def __init__(
        self,
        step: str,
        command: str,
        exit_code: int | None,
        stderr_tail: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.step = step
        self.command = command
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail
        self.rows: list[Row] = []
        self.checks: dict[str, Any] = {}
        self.findings: list[str] = []
        self.elapsed_seconds = 0.0


@dataclass(frozen=True)
class SyntheticStack:
    """The fixture plus the predictions needed to check the recovered numbers."""

    granules: tuple[Path, ...]
    dates: tuple[str, ...]
    x: np.ndarray
    y: np.ndarray
    velocity_mm_yr: np.ndarray
    cells: np.ndarray
    valid: np.ndarray


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def _tail(text: str | None, max_lines: int = 40, max_chars: int = 6000) -> str:
    if not text:
        return ""
    lines = str(text).rstrip().splitlines()[-max_lines:]
    joined = "\n".join(lines)
    return joined[-max_chars:]


def _python_sh(*args: object) -> str:
    """The command an operator would type: the runbook always goes through scripts/python.sh."""
    return "./scripts/python.sh " + " ".join(shlex.quote(str(arg)) for arg in args)


def _executed(argv: Sequence[object]) -> str:
    return shlex.join(str(arg) for arg in argv)


def _run(
    step: str,
    argv: Sequence[object],
    *,
    expected: Sequence[int] = (0,),
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[str]:
    """Run one command, failing loudly on an unexpected exit or an execution problem."""
    actual = [str(arg) for arg in argv]
    display = _executed(actual)
    try:
        completed = subprocess.run(
            actual,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RehearsalError(
            step,
            display,
            None,
            _tail(exc.stderr if isinstance(exc.stderr, str) else ""),
            f"timed out after {timeout:.0f}s",
        ) from exc
    except OSError as exc:
        raise RehearsalError(step, display, None, "", f"cannot execute: {exc}") from exc

    if completed.returncode not in expected:
        raise RehearsalError(
            step,
            display,
            completed.returncode,
            _tail(completed.stderr),
            f"exit {completed.returncode}, expected {list(expected)}",
        )
    return completed


def _require(
    condition: bool,
    step: str,
    message: str,
    *,
    command: str = "-",
    exit_code: int | None = None,
    stderr: str = "",
) -> None:
    if not condition:
        raise RehearsalError(step, command, exit_code, _tail(stderr), message)


def _prepare_workdir(raw: str | None) -> tuple[Path, Path]:
    """Create the work directory and a clean ``rehearsal/`` root inside it.

    An explicit ``--workdir`` is never deleted; only the namespaced ``rehearsal/`` subtree is
    cleaned. That keeps a rerun reproducible without risking unrelated files.
    """
    if raw is None:
        workdir = Path(tempfile.mkdtemp(prefix="rehearse-first-granule-"))
    else:
        workdir = Path(raw).expanduser().resolve()

    root = workdir / "rehearsal"
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        probe = root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise WorkdirError(f"work directory is not writable: {workdir} ({exc})") from exc
    return workdir, root


# ---------------------------------------------------------------------------------------------
# Synthetic stack (same fixture approach as tests/test_nisar_gunw.py and test_pipeline_integration)
# ---------------------------------------------------------------------------------------------


def _grid_coordinates() -> tuple[np.ndarray, np.ndarray]:
    cfg = load_case_config(CASE)
    forward = Transformer.from_crs("EPSG:4326", cfg.crs_analysis, always_xy=True)
    x0, y0 = forward.transform(*cfg.reference_lonlat)
    x = x0 + (np.arange(NX) - REF_IX) * SPACING_M
    y = y0 + (np.arange(NY) - REF_IY) * SPACING_M
    return x, y


def _velocity_field() -> np.ndarray:
    """Known secular vertical velocity, exactly 0 at the reference pixel, negative outward."""
    columns = np.arange(NX)[None, :]
    rows = np.arange(NY)[:, None]
    distance = np.hypot(columns - REF_IX, rows - REF_IY)
    return PEAK_MM_YR * (1.0 - np.exp(-distance / FALLOFF_PX))


def _expected_cell_velocities(stack: SyntheticStack) -> dict[str, float]:
    """Mean injected velocity per H3 cell over the pixels the chain is expected to keep.

    The field is linear in time, so a cell's fitted trend is the mean of its pixels' injected
    velocities. This is the known truth the rehearsal compares the published grid against; it is
    not read from ``nisar_grid.parquet``.
    """
    buckets: dict[str, list[float]] = {}
    for row in range(NY):
        for column in range(NX):
            if not bool(stack.valid[row, column]):
                continue
            cell = str(stack.cells[row, column])
            buckets.setdefault(cell, []).append(float(stack.velocity_mm_yr[row, column]))
    return {cell: float(np.mean(values)) for cell, values in buckets.items()}


def _cf_coordinate(
    group: h5py.Group, name: str, values: np.ndarray, *, axis: str
) -> h5py.Dataset:
    dataset = group.create_dataset(name, data=values.astype(np.float64))
    dataset.attrs["standard_name"] = (
        "projection_x_coordinate" if axis == "X" else "projection_y_coordinate"
    )
    dataset.attrs["long_name"] = f"{axis.lower()} coordinate of projection"
    dataset.attrs["units"] = "m"
    dataset.attrs["axis"] = axis
    dataset.attrs["grid_mapping"] = "projection"
    return dataset


def _write_granule(
    path: Path,
    *,
    phase: np.ndarray,
    coherence: np.ndarray,
    mask: np.ndarray,
    connected: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    reference_date: str,
    secondary_date: str,
) -> Path:
    """Write one synthetic granule with the documented GUNW layout; no value is invented."""
    with h5py.File(path, "w") as handle:
        unwrapped = handle.require_group(f"{UNWRAPPED}/{POL}")
        phase_ds = unwrapped.create_dataset("unwrappedPhase", data=phase.astype(np.float32))
        phase_ds.attrs["units"] = "rad"
        phase_ds.attrs["long_name"] = "unwrapped phase"
        coherence_ds = unwrapped.create_dataset(
            "coherenceMagnitude", data=coherence.astype(np.float32)
        )
        coherence_ds.attrs["units"] = "1"
        coherence_ds.attrs["long_name"] = "coherence magnitude"
        components_ds = unwrapped.create_dataset(
            "connectedComponents", data=connected.astype(np.uint32)
        )
        components_ds.attrs["units"] = "1"
        unwrapped.create_dataset(
            "ionospherePhaseScreen", data=np.zeros(phase.shape, dtype=np.float32)
        )
        unwrapped.create_dataset(
            "ionospherePhaseScreenUncertainty", data=np.zeros(phase.shape, dtype=np.float32)
        )

        mask_ds = handle.create_dataset(f"{UNWRAPPED}/mask", data=mask.astype(np.int8))
        mask_ds.attrs["units"] = "1"
        mask_ds.attrs["long_name"] = "quality flags (127 = padding)"

        wrapped = handle.require_group(f"{WRAPPED}/{POL}")
        wrapped.create_dataset(
            "wrappedInterferogram",
            data=np.exp(1j * np.nan_to_num(phase, nan=0.0)).astype(np.complex64),
        )
        wrapped.create_dataset("coherenceMagnitude", data=coherence.astype(np.float32))

        offsets = handle.require_group(f"{OFFSETS}/{POL}")
        offsets.create_dataset(
            "alongTrackOffset", data=np.zeros(phase.shape, dtype=np.float32)
        )
        offsets.create_dataset(
            "slantRangeOffset", data=np.zeros(phase.shape, dtype=np.float32)
        )
        offsets.create_dataset(
            "correlationSurfacePeak", data=coherence.astype(np.float32)
        )

        # CF-style coordinates where nisar_gunw.py probes them; the provisional geolocationGrid
        # that inspect_hdf5.py expects. The real location is explicitly unverified (layout §3).
        grids = handle.require_group(GRIDS)
        _cf_coordinate(grids, "xCoordinates", x, axis="X")
        _cf_coordinate(grids, "yCoordinates", y, axis="Y")
        projection = grids.create_dataset("projection", data=np.int32(EPSG))
        projection.attrs["long_name"] = "WGS 84 / UTM zone 14N"
        projection.attrs["epsg_code"] = np.int32(EPSG)

        geo = handle.require_group(GEOLOCATION)
        _cf_coordinate(geo, "xCoordinates", x, axis="X")
        _cf_coordinate(geo, "yCoordinates", y, axis="Y")
        geo.create_dataset("projection", data=np.int32(EPSG))

        radar = handle.require_group(RADAR)
        radar.create_dataset("wavelength", data=np.float64(WAVELENGTH_M))
        radar.create_dataset(
            "centerFrequency", data=np.float64(299_792_458.0 / WAVELENGTH_M)
        )
        radar.create_dataset("numberOfLooks", data=np.int32(LOOKS))
        radar.create_dataset("incidenceAngleCenter", data=np.float64(INCIDENCE_DEG))
        radar.create_dataset("incidenceAngle", data=np.float64(INCIDENCE_DEG))
        radar.create_dataset("heading", data=np.float64(HEADING_DEG))

        parameters = handle.require_group(PARAMS)
        parameters.create_dataset(
            "centerFrequency", data=np.float64(299_792_458.0 / WAVELENGTH_M)
        )
        parameters.create_dataset("numberOfLooks", data=np.int32(LOOKS))

        handle.require_group(f"{BASE}/metadata/orbit").create_dataset(
            "position", data=np.zeros((2, 3), dtype=np.float64)
        )
        handle.require_group(f"{BASE}/metadata/attitude").create_dataset(
            "quaternions", data=np.zeros((2, 4), dtype=np.float64)
        )

        identification = handle.require_group(f"{BASE}/identification")
        identification.create_dataset(
            "referenceZeroDopplerStartTime", data=f"{reference_date}T03:12:45Z"
        )
        identification.create_dataset(
            "secondaryZeroDopplerStartTime", data=f"{secondary_date}T03:12:45Z"
        )
        identification.create_dataset("missionId", data="NISAR")
        identification.create_dataset("productType", data="GUNW")
        identification.create_dataset("trackNumber", data=np.int32(1))
        identification.create_dataset("frameNumber", data=np.int32(5))
        identification.create_dataset("orbitPassDirection", data="A")
    return path


def build_stack(directory: Path) -> SyntheticStack:
    """Three chained 12-day pairs with a known, temporally linear deformation field."""
    directory.mkdir(parents=True, exist_ok=True)
    start = dt.date.fromisoformat(START_DATE)
    dates = [
        (start + dt.timedelta(days=SPACING_DAYS * index)).isoformat()
        for index in range(PAIRS + 1)
    ]
    x, y = _grid_coordinates()
    velocity = _velocity_field()

    t_years = {
        date: (dt.date.fromisoformat(date) - start).days / V.DAYS_PER_YEAR for date in dates
    }
    vertical = {date: OFFSET_MM + velocity * t_years[date] for date in dates}

    mask = np.zeros((NY, NX), dtype=np.int8)
    mask[PADDING_PATCH[0] : PADDING_PATCH[1], PADDING_PATCH[2] : PADDING_PATCH[3]] = 127
    mask[FLAG_PATCH[0] : FLAG_PATCH[1], FLAG_PATCH[2] : FLAG_PATCH[3]] = 2

    coherence = np.full((NY, NX), 0.80, dtype=float)
    coherence[WATER_PATCH[0] : WATER_PATCH[1], WATER_PATCH[2] : WATER_PATCH[3]] = 0.12
    coherence[PADDING_PATCH[0] : PADDING_PATCH[1], PADDING_PATCH[2] : PADDING_PATCH[3]] = 0.70

    connected = np.ones((NY, NX), dtype=np.uint32)
    connected[PADDING_PATCH[0] : PADDING_PATCH[1], PADDING_PATCH[2] : PADDING_PATCH[3]] = 0
    connected[NAN_PATCH[0] : NAN_PATCH[1], NAN_PATCH[2] : NAN_PATCH[3]] = 0
    connected[WATER_PATCH[0] : WATER_PATCH[1], WATER_PATCH[2] : WATER_PATCH[3]] = 2

    cfg = load_case_config(CASE)
    inverse = Transformer.from_crs(cfg.crs_analysis, "EPSG:4326", always_xy=True)
    lon2d, lat2d = inverse.transform(*np.meshgrid(x, y))
    cells = np.array(
        [
            h3.latlng_to_cell(float(lat), float(lon), cfg.h3_resolution)
            for lat, lon in zip(lat2d.ravel(), lon2d.ravel(), strict=True)
        ],
        dtype=object,
    ).reshape(NY, NX)
    valid = (mask != 127) & np.isfinite(coherence) & (coherence >= COHERENCE_MIN)
    valid[NAN_PATCH[0] : NAN_PATCH[1], NAN_PATCH[2] : NAN_PATCH[3]] = False

    granules: list[Path] = []
    for index, (reference_date, secondary_date) in enumerate(
        zip(dates[:-1], dates[1:], strict=True)
    ):
        increment = vertical[secondary_date] - vertical[reference_date]
        los_mm = V.project_to_los(
            increment, INCIDENCE_DEG, HEADING_DEG, v_east=0.0, v_north=0.0
        )
        # Invert d_LOS = -lambda/(4*pi) * phi  ->  phi = -4*pi*d / lambda (d in metres).
        phase = -4.0 * np.pi * (los_mm / 1000.0) / WAVELENGTH_M
        phase = np.asarray(phase, dtype=np.float32)
        phase[NAN_PATCH[0] : NAN_PATCH[1], NAN_PATCH[2] : NAN_PATCH[3]] = np.nan
        pair_coherence = coherence.copy()
        pair_coherence[NAN_PATCH[0] : NAN_PATCH[1], NAN_PATCH[2] : NAN_PATCH[3]] = np.nan

        name = (
            f"NISAR_L2_PR_GUNW_001_005_A_{index + 1:03d}_002_4000_SH_"
            f"{reference_date.replace('-', '')}T031245_"
            f"{secondary_date.replace('-', '')}T031245.h5"
        )
        granules.append(
            _write_granule(
                directory / name,
                phase=phase,
                coherence=pair_coherence,
                mask=mask,
                connected=connected,
                x=x,
                y=y,
                reference_date=reference_date,
                secondary_date=secondary_date,
            )
        )
    return SyntheticStack(
        granules=tuple(granules),
        dates=tuple(dates),
        x=x,
        y=y,
        velocity_mm_yr=velocity,
        cells=cells,
        valid=valid,
    )


def _copy_variant(
    source: Sequence[Path],
    directory: Path,
    *,
    remove: Sequence[str] = (),
    per_pixel_incidence: bool = False,
) -> tuple[Path, ...]:
    """Copy the synthetic granules and delete/replace only the metadata under test."""
    directory.mkdir(parents=True, exist_ok=True)
    variants: list[Path] = []
    for granule in source:
        destination = directory / granule.name
        shutil.copy2(granule, destination)
        with h5py.File(destination, "r+") as handle:
            for path in remove:
                if path in handle:
                    del handle[path]
            if per_pixel_incidence:
                for path in (f"{RADAR}/incidenceAngleCenter", f"{RADAR}/incidenceAngle"):
                    if path in handle:
                        del handle[path]
                handle.create_dataset(
                    f"{RADAR}/incidenceAngle",
                    data=np.full((NY, NX), INCIDENCE_DEG, dtype=np.float64),
                )
        variants.append(destination)
    return tuple(variants)


# ---------------------------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------------------------


def _process_argv(
    granules: Sequence[Path],
    out_dir: Path,
    *,
    flags: bool,
    force: bool = False,
) -> list[str]:
    argv = [sys.executable, str(PROCESS), "--case", CASE, "--out", str(out_dir)]
    for granule in granules:
        argv += ["--granule", str(granule)]
    if flags:
        argv += [
            "--incidence",
            f"{FLAG_INCIDENCE_DEG:g}",
            "--heading",
            f"{FLAG_HEADING_DEG:g}",
        ]
    if force:
        argv.append("--force")
    return argv


def _process_display(
    granules: Sequence[Path],
    out_dir: Path,
    *,
    flags: bool,
    force: bool = False,
) -> str:
    parts: list[object] = [
        "pipeline/process_case.py",
        "--case",
        CASE,
        "--out",
        str(out_dir),
    ]
    # The table shows the runbook's <g1>/<g2>/<g3> shape; the exact argv is carried in
    # `executed_command` and printed on failure.
    for index, _granule in enumerate(granules, 1):
        parts += ["--granule", f"g{index}.h5"]
    if flags:
        parts += [
            "--incidence",
            f"{FLAG_INCIDENCE_DEG:g}",
            "--heading",
            f"{FLAG_HEADING_DEG:g}",
        ]
    if force:
        parts.append("--force")
    return _python_sh(*parts)


def _parse_coverage(
    step: str,
    completed: subprocess.CompletedProcess[str],
    *,
    display: str,
) -> tuple[float, int, int, bool]:
    """Parse the coverage line and prove the 50% threshold logic fired or stayed quiet."""
    match = COVERAGE_RE.search(completed.stdout)
    _require(
        match is not None,
        step,
        "process_case.py did not print the documented 'coverage  XX.XX% of N AOI cells (M covered)' line",
        command=display,
        exit_code=completed.returncode,
        stderr=completed.stderr,
    )
    fraction = float(match.group(1)) / 100.0
    aoi_cells = int(match.group(2))
    covered = int(match.group(3))
    warning = "WARNING: COVERAGE:" in completed.stderr
    if fraction < COVERAGE_WARNING_FRACTION:
        _require(
            warning,
            step,
            f"coverage {fraction:.2%} is below {COVERAGE_WARNING_FRACTION:.0%} but no "
            "'WARNING: COVERAGE:' line was printed",
            command=display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
    else:
        _require(
            not warning,
            step,
            f"coverage {fraction:.2%} is above {COVERAGE_WARNING_FRACTION:.0%} but a "
            "'WARNING: COVERAGE:' line was printed",
            command=display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
    _require(
        "contract  PASS" in completed.stdout and "gate      PASS" in completed.stdout,
        step,
        "process_case.py did not report both 'contract  PASS' and 'gate      PASS'",
        command=display,
        exit_code=completed.returncode,
        stderr=completed.stderr,
    )
    return fraction, aoi_cells, covered, warning


# ---------------------------------------------------------------------------------------------
# The rehearsal
# ---------------------------------------------------------------------------------------------


def rehearse(workdir: Path, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    rows: list[Row] = []
    checks: dict[str, Any] = {}
    findings: list[str] = []

    runbook_path = ROOT / DOC_RUNBOOK
    runbook_text = runbook_path.read_text(encoding="utf-8") if runbook_path.is_file() else ""

    try:
        stack = build_stack(root / "granules")
        checks["granule_count"] = len(stack.granules)
        checks["dates"] = list(stack.dates)
        checks["stack"] = {
            "polarisation": POL,
            "track": 1,
            "frame": 5,
            "direction": "ascending",
            "spacing_days": SPACING_DAYS,
            "shape": [NY, NX],
            "posting_m": SPACING_M,
        }

        # -------------------------------------------------------------------------------------
        # Step 1 — preconditions and the download. Named, not executed.
        # -------------------------------------------------------------------------------------
        rows.append(
            Row(
                id="step1-preconditions",
                step="§1",
                check="preconditions",
                command="export EARTHDATA_USER=... EARTHDATA_PASS=... / ASF EULA",
                exit_code=None,
                verdict=(
                    "not rehearsable offline: needs Earthdata credentials and an accepted ASF "
                    "EULA / authorised application"
                ),
                rehearsable=False,
                note="The 401/403 distinction and the .part resume behaviour are network-only.",
            )
        )
        rows.append(
            Row(
                id="step1-get-granule",
                step="§2",
                check="get the granule",
                command=(
                    "pipeline/ingest/02_search_download.py search --cities mexico-city; "
                    "download --dry-run; download"
                ),
                exit_code=None,
                verdict=(
                    "not rehearsable offline: ASF SearchAPI search/plan and the actual download "
                    "need network (download also needs credentials and writes data/raw)"
                ),
                rehearsable=False,
                note="The dry-run identity/geometry guards and budget stop are network-plan steps.",
            )
        )

        # -------------------------------------------------------------------------------------
        # Step 2 — inspect the HDF5 file, then run the runbook's jq filters.
        # -------------------------------------------------------------------------------------
        tree_json = root / "tree.json"
        inspect_argv = [
            sys.executable,
            str(INSPECT),
            str(stack.granules[0]),
            "--product",
            "nisar-gunw",
            "--json",
        ]
        inspect_display = _python_sh(
            "pipeline/ingest/inspect_hdf5.py",
            "GRANULE.h5",
            "--product",
            "nisar-gunw",
            "--json",
        ) + " > /tmp/tree.json"
        completed = _run("Step 2 (inspect_hdf5.py)", inspect_argv)
        tree_json.write_text(completed.stdout, encoding="utf-8")
        report = json.loads(completed.stdout)

        _require(
            report.get("ok") is True and report.get("error") is None,
            "Step 2 (inspect_hdf5.py)",
            f"inspect_hdf5.py did not report a successful inspection: {report.get('error')!r}",
            command=inspect_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        expectations = {
            item["role"]: item
            for item in report.get("expectations", [])
            if item.get("kind") == "dataset"
        }
        expected_roles = (
            "phase",
            "coherence",
            "connected-components",
            "mask",
            "pixel-offsets",
            "wrapped-interferogram",
            "geolocation",
        )
        for role in expected_roles:
            item = expectations.get(role)
            _require(
                item is not None and item.get("present") is True,
                "Step 2 (inspect_hdf5.py)",
                f"expected dataset role {role!r} is absent from the documented layout",
                command=inspect_display,
                exit_code=completed.returncode,
                stderr=completed.stderr,
            )
        found = {role: expectations[role].get("found") for role in expected_roles}
        _require(
            found["phase"] == f"{UNWRAPPED}/{POL}/unwrappedPhase",
            "Step 2 (inspect_hdf5.py)",
            f"phase resolved to {found['phase']!r}, not the documented §2 path",
            command=inspect_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        _require(
            found["coherence"] == f"{UNWRAPPED}/{POL}/coherenceMagnitude"
            and found["connected-components"] == f"{UNWRAPPED}/{POL}/connectedComponents"
            and found["mask"] == f"{UNWRAPPED}/mask",
            "Step 2 (inspect_hdf5.py)",
            f"phase/coherence/components/mask resolved to unexpected paths: {found}",
            command=inspect_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )

        datasets = {dataset["path"]: dataset for dataset in report.get("datasets", [])}
        expected_dtypes = {
            f"{UNWRAPPED}/{POL}/unwrappedPhase": ("float32", "rad"),
            f"{UNWRAPPED}/{POL}/coherenceMagnitude": ("float32", "1"),
            f"{UNWRAPPED}/{POL}/connectedComponents": ("uint32", None),
            f"{UNWRAPPED}/mask": ("int8", None),
        }
        for path, (dtype, units) in expected_dtypes.items():
            dataset = datasets.get(path)
            _require(
                dataset is not None,
                "Step 2 (inspect_hdf5.py)",
                f"dataset {path!r} is missing from the JSON tree",
                command=inspect_display,
                exit_code=completed.returncode,
                stderr=completed.stderr,
            )
            _require(
                dataset.get("dtype") == dtype,
                "Step 2 (inspect_hdf5.py)",
                f"{path}: dtype {dataset.get('dtype')!r}, expected {dtype!r} (§2)",
                command=inspect_display,
                exit_code=completed.returncode,
                stderr=completed.stderr,
            )
            if units is not None:
                _require(
                    dataset.get("units") == units,
                    "Step 2 (inspect_hdf5.py)",
                    f"{path}: units {dataset.get('units')!r}, expected {units!r} (§2)",
                    command=inspect_display,
                    exit_code=completed.returncode,
                    stderr=completed.stderr,
                )

        checks["inspect_expectations_present"] = list(expected_roles)
        checks["inspect_found"] = found
        checks["inspect_dtypes"] = {
            path: {
                "dtype": datasets[path].get("dtype"),
                "units": datasets[path].get("units"),
            }
            for path in expected_dtypes
        }
        rows.append(
            Row(
                id="step2-inspect",
                step="Step 2",
                check="inspect_hdf5.py --product nisar-gunw --json",
                command=inspect_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(inspect_argv),
                note=(
                    "all §2 roles present with the documented dtypes/units; "
                    f"tree written to {tree_json}"
                ),
            )
        )

        # The two jq lines from §3. They are environment-dependent (jq may be absent), so a
        # missing jq is named as not rehearsable rather than failing the whole rehearsal.
        jq = shutil.which("jq")
        if jq is None:
            rows.append(
                Row(
                    id="step2-jq",
                    step="Step 2",
                    check="jq filters",
                    command="jq -r '<role/dataset filters>' /tmp/tree.json",
                    exit_code=None,
                    verdict="not rehearsable offline: jq is not installed in this environment",
                    rehearsable=False,
                    note="The JSON tree is still parsed and asserted by this script.",
                )
            )
        else:
            jq_role_argv = [jq, "-r", JQ_ROLE_FILTER, str(tree_json)]
            jq_role = _run("Step 2 (jq roles)", jq_role_argv)
            jq_dataset_argv = [jq, "-r", JQ_DATASET_FILTER, str(tree_json)]
            jq_dataset = _run("Step 2 (jq datasets)", jq_dataset_argv)
            _require(
                any(
                    line.startswith("phase\ttrue\t") for line in jq_role.stdout.splitlines()
                ),
                "Step 2 (jq roles)",
                "the runbook jq role filter did not report the phase role present",
                command=_executed(jq_role_argv),
                exit_code=jq_role.returncode,
                stderr=jq_role.stderr,
            )
            _require(
                "unwrappedPhase\tfloat32\trad\t" in jq_dataset.stdout,
                "Step 2 (jq datasets)",
                "the runbook jq dataset filter did not report unwrappedPhase float32 rad",
                command=_executed(jq_dataset_argv),
                exit_code=jq_dataset.returncode,
                stderr=jq_dataset.stderr,
            )
            rows.append(
                Row(
                    id="step2-jq",
                    step="Step 2",
                    check="jq filters",
                    command=(
                        "jq -r '<role filter>' /tmp/tree.json; "
                        "jq -r '<dataset filter>' /tmp/tree.json"
                    ),
                    exit_code=0,
                    verdict="rehearsed",
                    rehearsable=True,
                    executed_command=(
                        f"{_executed(jq_role_argv)} ; {_executed(jq_dataset_argv)}"
                    ),
                )
            )

        # Structural read with the real reader.
        reader_argv = [sys.executable, str(READER), str(stack.granules[0]), "--json"]
        reader_display = _python_sh(
            "pipeline/ingest/nisar_gunw.py", "GRANULE.h5", "--json"
        )
        completed = _run("Step 2 (nisar_gunw.py)", reader_argv)
        summary = json.loads(completed.stdout)
        _require(
            summary.get("polarisation") == POL,
            "Step 2 (nisar_gunw.py)",
            f"reader polarisation {summary.get('polarisation')!r}, expected {POL!r}",
            command=reader_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        _require(
            summary.get("epsg") == EPSG,
            "Step 2 (nisar_gunw.py)",
            f"reader epsg {summary.get('epsg')!r}, expected {EPSG}",
            command=reader_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        _require(
            summary.get("wavelength_m") is not None
            and abs(float(summary["wavelength_m"]) - WAVELENGTH_M) < 1e-12
            and str(summary.get("wavelength_source", "")).startswith("metadata:"),
            "Step 2 (nisar_gunw.py)",
            f"wavelength not read from metadata: {summary.get('wavelength_m')!r} "
            f"({summary.get('wavelength_source')!r})",
            command=reader_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        _require(
            summary.get("mask_values") == [0, 2, 127]
            and summary.get("mask_unknown_values") == [],
            "Step 2 (nisar_gunw.py)",
            f"unexpected mask semantics: {summary.get('mask_values')!r} "
            f"(unknown {summary.get('mask_unknown_values')!r})",
            command=reader_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        _require(
            summary.get("warnings") == [],
            "Step 2 (nisar_gunw.py)",
            f"reader emitted unexpected warnings: {summary.get('warnings')!r}",
            command=reader_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        date_pair = summary.get("date_pair") or []
        _require(
            date_pair == list(stack.dates[:2]),
            "Step 2 (nisar_gunw.py)",
            f"reader date_pair {date_pair!r}, expected {list(stack.dates[:2])!r}",
            command=reader_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        checks["reader"] = {
            "polarisation": summary.get("polarisation"),
            "epsg": summary.get("epsg"),
            "wavelength_m": summary.get("wavelength_m"),
            "wavelength_source": summary.get("wavelength_source"),
            "mask_values": summary.get("mask_values"),
            "mask_unknown_values": summary.get("mask_unknown_values"),
            "date_pair": date_pair,
            "valid_fraction": summary.get("valid_fraction"),
        }
        rows.append(
            Row(
                id="step2-reader",
                step="Step 2",
                check="nisar_gunw.py --json",
                command=reader_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(reader_argv),
                note=(
                    "HH discovered, wavelength from metadata, mask {0,2,127}, "
                    "dates from identification"
                ),
            )
        )

        # -------------------------------------------------------------------------------------
        # Step 3 — the coverage/coherence probe into /tmp.
        # -------------------------------------------------------------------------------------
        probe_root = root / "gunw-probe"
        probe_out = probe_root / CASE
        probe_argv = _process_argv(stack.granules, probe_out, flags=True)
        probe_display = _process_display(stack.granules, probe_out, flags=True)
        probe_completed = _run("Step 3 (process_case.py probe)", probe_argv)
        coverage_fraction, aoi_cells, covered, coverage_warning = _parse_coverage(
            "Step 3 (process_case.py probe)", probe_completed, display=probe_display
        )
        probe_meta = json.loads((probe_out / "meta.json").read_text(encoding="utf-8"))
        _require(
            all(DATE_WINDOW[0] <= date <= DATE_WINDOW[1] for date in probe_meta["dates"]),
            "Step 3 (process_case.py probe)",
            f"dates {probe_meta['dates']!r} fall outside the configured {DATE_WINDOW}",
            command=probe_display,
            exit_code=probe_completed.returncode,
            stderr=probe_completed.stderr,
        )
        checks["coverage"] = {
            "fraction": coverage_fraction,
            "percent": coverage_fraction * 100.0,
            "aoi_cells": aoi_cells,
            "covered_cells": covered,
            "warning_fired": coverage_warning,
            "threshold": COVERAGE_WARNING_FRACTION,
        }
        rows.append(
            Row(
                id="step3-probe",
                step="Step 3",
                check="process_case.py coverage probe",
                command=probe_display,
                exit_code=probe_completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(probe_argv),
                note=(
                    f"coverage {coverage_fraction:.2%} of {aoi_cells} AOI cells "
                    f"({covered} covered); "
                    + (
                        "below 50% so 'WARNING: COVERAGE:' fired as documented"
                        if coverage_warning
                        else "above 50% so no COVERAGE warning fired"
                    )
                ),
            )
        )

        # The runbook's separate coherence-median snippet.
        grid_path = probe_out / "nisar_grid.parquet"
        snippet = (
            "import pandas as pd;"
            f"print(pd.read_parquet({str(grid_path)!r}).coherence.median())"
        )
        coherence_argv = [sys.executable, "-c", snippet]
        coherence_display = (
            './scripts/python.sh -c "import pandas as pd;'
            f"print(pd.read_parquet('{grid_path}').coherence.median())\""
        )
        completed = _run("Step 3 (coherence median)", coherence_argv)
        coherence_median = float(completed.stdout.strip().splitlines()[-1])
        _require(
            coherence_median >= COHERENCE_MIN,
            "Step 3 (coherence median)",
            f"coherence median {coherence_median:.6f} < frozen coherence_min {COHERENCE_MIN}",
            command=coherence_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        frame_median = float(pd.read_parquet(grid_path)["coherence"].median())
        _require(
            abs(frame_median - coherence_median) < 1e-9,
            "Step 3 (coherence median)",
            f"the runbook snippet printed {coherence_median!r} but the frame median is "
            f"{frame_median!r}",
            command=coherence_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        checks["coherence_median"] = coherence_median
        rows.append(
            Row(
                id="step3-coherence",
                step="Step 3",
                check="pd.read_parquet(...).coherence.median()",
                command=coherence_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(coherence_argv),
                note=(
                    f"median {coherence_median:.4f} >= SENSOR_PROFILES[...].coherence_min "
                    f"{COHERENCE_MIN}"
                ),
            )
        )

        # This part is the real granule's number, not the fixture's.
        rows.append(
            Row(
                id="step3-real-coverage-decision",
                step="Step 3",
                check="real-granule coverage decision",
                command="compare the real coverage line with the 50% threshold (docs/data-source-decision.md)",
                exit_code=None,
                verdict=(
                    "not rehearsable offline: the coverage number belongs to the real granule; "
                    "the threshold logic was rehearsed with the synthetic "
                    f"{coverage_fraction:.2%} result"
                ),
                rehearsable=False,
                note=(
                    "The fixture footprint is ~2 km across, so a below-50% warning is expected; "
                    "that proves the warning/decision path, not the real city coverage."
                ),
            )
        )
        if "WARNING: COVERAGE" in runbook_text:
            findings.append(
                "Step 3: process_case.py exits 0 on a below-threshold coverage result; the "
                "runbook's 'WARNING: COVERAGE: is a FAIL' is an operator-read line, not an exit "
                "code. The rehearsal asserts the line is printed."
            )

        # -------------------------------------------------------------------------------------
        # Step 4 — geometry: metadata wins, and the two documented refusals.
        # -------------------------------------------------------------------------------------
        probe_geometry = str(probe_meta.get("provenance", {}).get("geometry", ""))
        _require(
            "incidence:" in probe_geometry
            and "heading:" in probe_geometry
            and "CLI --incidence" not in probe_geometry
            and "CLI --heading" not in probe_geometry,
            "Step 4 (metadata wins)",
            f"meta.provenance.geometry did not record the metadata source: {probe_geometry!r}",
            command=probe_display,
            exit_code=probe_completed.returncode,
            stderr=probe_completed.stderr,
        )
        _require(
            "the granule metadata wins" in probe_completed.stderr,
            "Step 4 (metadata wins)",
            "the --incidence/--heading flags differed from metadata but no "
            "'the granule metadata wins' warning was printed",
            command=probe_display,
            exit_code=probe_completed.returncode,
            stderr=probe_completed.stderr,
        )
        checks["geometry"] = probe_geometry
        rows.append(
            Row(
                id="step4-metadata-wins",
                step="Step 4",
                check="metadata wins over --incidence/--heading",
                command=probe_display,
                exit_code=0,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(probe_argv),
                note=(
                    f"flags {FLAG_INCIDENCE_DEG:g}/{FLAG_HEADING_DEG:g} were ignored; "
                    f"meta.provenance.geometry = {probe_geometry}"
                ),
            )
        )

        no_incidence = _copy_variant(
            stack.granules,
            root / "granules-no-incidence",
            remove=(f"{RADAR}/incidenceAngleCenter", f"{RADAR}/incidenceAngle"),
        )
        refusal_out = root / "refusals" / "missing-incidence" / CASE
        refusal_argv = _process_argv(no_incidence, refusal_out, flags=False)
        refusal_display = _process_display(no_incidence, refusal_out, flags=False)
        completed = _run("Step 4 (missing incidence)", refusal_argv, expected=(2,))
        _require(
            "incidence angle missing" in completed.stderr
            and "incidenceAngle" in completed.stderr
            and "refused:" in completed.stderr,
            "Step 4 (missing incidence)",
            "process_case.py did not refuse with the documented incidence message and probed paths",
            command=refusal_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        rows.append(
            Row(
                id="step4-missing-incidence",
                step="Step 4",
                check="refusal when incidence metadata is absent",
                command=refusal_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(refusal_argv),
                note="exit 2 with 'incidence angle missing' and the candidate paths",
            )
        )

        no_heading = _copy_variant(
            stack.granules,
            root / "granules-no-heading",
            remove=(f"{RADAR}/heading", f"{RADAR}/platformHeading"),
        )
        refusal_out = root / "refusals" / "missing-heading" / CASE
        refusal_argv = _process_argv(no_heading, refusal_out, flags=False)
        refusal_display = _process_display(no_heading, refusal_out, flags=False)
        completed = _run("Step 4 (missing heading)", refusal_argv, expected=(2,))
        _require(
            "heading angle missing" in completed.stderr
            and "heading" in completed.stderr
            and "refused:" in completed.stderr,
            "Step 4 (missing heading)",
            "process_case.py did not refuse with the documented heading message and probed paths",
            command=refusal_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        rows.append(
            Row(
                id="step4-missing-heading",
                step="Step 4",
                check="refusal when heading metadata is absent",
                command=refusal_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(refusal_argv),
                note="exit 2 with 'heading angle missing' and the candidate paths",
            )
        )

        grid_incidence = _copy_variant(
            stack.granules, root / "granules-grid-incidence", per_pixel_incidence=True
        )
        refusal_out = root / "refusals" / "grid-incidence" / CASE
        refusal_argv = _process_argv(grid_incidence, refusal_out, flags=False)
        refusal_display = _process_display(grid_incidence, refusal_out, flags=False)
        completed = _run("Step 4 (per-pixel incidence)", refusal_argv, expected=(2,))
        _require(
            "multi-pixel grid" in completed.stderr and "refused:" in completed.stderr,
            "Step 4 (per-pixel incidence)",
            "a per-pixel incidence grid was not refused with the documented multi-pixel message",
            command=refusal_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        rows.append(
            Row(
                id="step4-grid-incidence",
                step="Step 4",
                check="per-pixel incidence is a refusal, not an average",
                command=refusal_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(refusal_argv),
                note="exit 2 with the 'multi-pixel grid' diagnostic",
            )
        )
        if "heading angle missing" in runbook_text and "incidence angle missing" in runbook_text:
            findings.append(
                "Step 4: when both incidence and heading are absent, process_case.py reports only "
                "the first missing angle (incidence), not both lists as the runbook wording "
                "implies. The rehearsal removes one at a time and proves both messages."
            )

        rows.append(
            Row(
                id="step4-real-geometry-decision",
                step="Step 4",
                check="real-granule geometry choice",
                command="choose a defensible scalar from metadata/radarGrid or published geometry",
                exit_code=None,
                verdict=(
                    "not rehearsable offline: the real values and the choice of reference pixel "
                    "require the real granule; the metadata-wins and refusal paths were rehearsed"
                ),
                rehearsable=False,
            )
        )

        # -------------------------------------------------------------------------------------
        # Step 5 — the mock-overwrite guard, --force, the gate, and the preflight.
        # -------------------------------------------------------------------------------------
        processed_root = root / "processed"
        processed_out = processed_root / CASE
        processed_out.mkdir(parents=True)
        (processed_out / "meta.json").write_text(
            json.dumps({"product": "mock"}), encoding="utf-8"
        )

        guard_argv = _process_argv(stack.granules, processed_out, flags=True)
        guard_display = _process_display(stack.granules, processed_out, flags=True)
        completed = _run("Step 5 (--force guard)", guard_argv, expected=(2,))
        _require(
            "committed mock artefact set" in completed.stderr and "refused:" in completed.stderr,
            "Step 5 (--force guard)",
            "process_case.py did not refuse to overwrite a mock meta.json without --force",
            command=guard_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        _require(
            not (processed_out / "nisar_grid.parquet").exists(),
            "Step 5 (--force guard)",
            "a refused run still wrote nisar_grid.parquet",
            command=guard_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        rows.append(
            Row(
                id="step5-force-guard",
                step="Step 5",
                check="--force is mandatory over the mock tree",
                command=guard_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(guard_argv),
                note="exit 2 with 'committed mock artefact set'; nothing overwritten",
            )
        )

        process_argv = _process_argv(
            stack.granules, processed_out, flags=True, force=True
        )
        process_display = _process_display(
            stack.granules, processed_out, flags=True, force=True
        )
        completed = _run("Step 5 (process_case.py --force)", process_argv)
        process_fraction, process_aoi, process_covered, process_warning = _parse_coverage(
            "Step 5 (process_case.py --force)", completed, display=process_display
        )
        _require(
            abs(process_fraction - coverage_fraction) < 1e-12
            and process_aoi == aoi_cells
            and process_covered == covered
            and process_warning == coverage_warning,
            "Step 5 (process_case.py --force)",
            "the Step 5 run disagrees with the Step 3 probe: "
            f"probe {coverage_fraction:.6f}/{aoi_cells}/{covered}, "
            f"process {process_fraction:.6f}/{process_aoi}/{process_covered}",
            command=process_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        processed_meta = json.loads(
            (processed_out / "meta.json").read_text(encoding="utf-8")
        )
        source = str(processed_meta.get("provenance", {}).get("source", ""))
        _require(
            processed_meta.get("product") == "nisar-gunw"
            and "NISAR L2 GUNW" in source
            and "mock" not in source.lower(),
            "Step 5 (process_case.py --force)",
            f"provenance/product is not a real NISAR GUNW claim: product="
            f"{processed_meta.get('product')!r}, source={source!r}",
            command=process_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        checks["processed"] = {
            "product": processed_meta.get("product"),
            "provenance_source": source,
            "provenance_method": processed_meta.get("provenance", {}).get("method"),
            "dates": processed_meta.get("dates"),
            "stats": processed_meta.get("stats"),
        }
        rows.append(
            Row(
                id="step5-process",
                step="Step 5",
                check="process_case.py --force into /tmp",
                command=process_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(process_argv),
                note=(
                    "contract PASS + gate PASS; provenance.source names NISAR L2 GUNW and "
                    "contains no 'mock'"
                ),
            )
        )

        gate_argv = [sys.executable, str(GATE), "--processed", str(processed_root)]
        gate_display = _python_sh(
            "pipeline/validate_artifacts.py", "--processed", str(processed_root)
        )
        completed = _run("Step 5 (validate_artifacts.py)", gate_argv)
        _require(
            completed.returncode == 0,
            "Step 5 (validate_artifacts.py)",
            "the file-level gate did not exit 0 on the /tmp rehearsal artefacts",
            command=gate_display,
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
        checks["gate_exit_code"] = completed.returncode
        rows.append(
            Row(
                id="step5-gate",
                step="Step 5",
                check="validate_artifacts.py --processed /tmp/...",
                command=gate_display,
                exit_code=completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(gate_argv),
                note="exit 0 on the /tmp rehearsal tree",
            )
        )

        _require(
            CHECK_PHASE_A.is_file() and os.access(CHECK_PHASE_A, os.X_OK),
            "Step 5 (check_phase_a.sh preflight)",
            f"{CHECK_PHASE_A} is missing or not executable",
            command="test -x scripts/check_phase_a.sh",
            exit_code=1 if not CHECK_PHASE_A.is_file() else 0,
        )
        rows.append(
            Row(
                id="step5-check-phase-a-preflight",
                step="Step 5",
                check="check_phase_a.sh exists and is executable",
                command="test -x scripts/check_phase_a.sh",
                exit_code=0,
                verdict="rehearsed",
                rehearsable=True,
                executed_command="test -x scripts/check_phase_a.sh",
                note=(
                    "preflight only: the script hard-codes data/processed and runs the whole "
                    "suite, so it is not run against the /tmp tree"
                ),
            )
        )
        rows.append(
            Row(
                id="step5-check-phase-a-full",
                step="Step 5",
                check="check_phase_a.sh full gate",
                command="./scripts/check_phase_a.sh",
                exit_code=None,
                verdict=(
                    "not rehearsable offline: it reads data/processed (the committed mock tree) "
                    "and runs the whole regression suite; only its executable preflight was checked"
                ),
                rehearsable=False,
            )
        )

        # -------------------------------------------------------------------------------------
        # Step 6 — the sign/median/p05 gate against the injected deformation, plus a direct
        # per-cell comparison with the known field (the fixture's truth, not a re-read artefact).
        # -------------------------------------------------------------------------------------
        processed_grid = pd.read_parquet(processed_out / "nisar_grid.parquet")
        expected_cell_velocity = _expected_cell_velocities(stack)
        published_cell_velocity = {
            str(cell): float(velocity)
            for cell, velocity in zip(
                processed_grid["cell_id"], processed_grid["velocity_mm_yr"], strict=True
            )
        }
        _require(
            set(published_cell_velocity) == set(expected_cell_velocity),
            "Step 6 (known field)",
            "the published H3 cell set differs from the known field's valid cells: "
            f"published-only={sorted(set(published_cell_velocity) - set(expected_cell_velocity))[:5]}, "
            f"expected-only={sorted(set(expected_cell_velocity) - set(published_cell_velocity))[:5]}",
            command=process_display,
            exit_code=0,
        )
        worst_error = max(
            abs(published_cell_velocity[cell] - expected_cell_velocity[cell])
            for cell in expected_cell_velocity
        )
        _require(
            worst_error <= 0.1,
            "Step 6 (known field)",
            f"worst recovered-vs-injected cell velocity error is {worst_error:.6f} mm/yr "
            "(> 0.1 mm/yr)",
            command=process_display,
            exit_code=0,
        )
        checks["known_deformation"] = {
            "cells": len(expected_cell_velocity),
            "worst_abs_error_mm_yr": worst_error,
            "tolerance_mm_yr": 0.1,
        }
        rows.append(
            Row(
                id="step6-known-field",
                step="Step 6",
                check="recover the injected per-cell velocity",
                command="compare nisar_grid.parquet velocity_mm_yr with the synthetic field",
                exit_code=0,
                verdict="rehearsed",
                rehearsable=True,
                note=(
                    f"{len(expected_cell_velocity)} cells, worst |recovered - injected| = "
                    f"{worst_error:.6f} mm/yr (tolerance 0.1)"
                ),
            )
        )

        stats = processed_meta.get("stats", {})
        median_velocity = float(stats["median_velocity_mm_yr"])
        p05_velocity = float(stats["p05_velocity_mm_yr"])
        _require(
            median_velocity < 0.0,
            "Step 6 (sign gate)",
            f"median_velocity_mm_yr = {median_velocity!r} is not negative (subsidence sign)",
            command=process_display,
            exit_code=0,
        )
        _require(
            abs(median_velocity) >= 5.0,
            "Step 6 (sign gate)",
            f"|median_velocity_mm_yr| = {abs(median_velocity)!r} is below the 5 mm/yr "
            "'estable' floor",
            command=process_display,
            exit_code=0,
        )
        _require(
            PUBLISHED_RATE_BAND[0] <= p05_velocity <= PUBLISHED_RATE_BAND[1],
            "Step 6 (sign gate)",
            f"p05_velocity_mm_yr = {p05_velocity!r} is outside the published "
            f"{PUBLISHED_RATE_BAND} mm/yr band",
            command=process_display,
            exit_code=0,
        )
        checks["velocity"] = {
            "median_mm_yr": median_velocity,
            "p05_mm_yr": p05_velocity,
            "published_band_mm_yr": list(PUBLISHED_RATE_BAND),
            "injected_peak_mm_yr": PEAK_MM_YR,
        }
        rows.append(
            Row(
                id="step6-sign-gate",
                step="Step 6",
                check="median/p05 sign convention",
                command=(
                    "inspect meta.stats.median_velocity_mm_yr and "
                    "meta.stats.p05_velocity_mm_yr"
                ),
                exit_code=0,
                verdict="rehearsed",
                rehearsable=True,
                note=(
                    f"median {median_velocity:.3f} mm/yr (negative), p05 {p05_velocity:.3f} "
                    f"mm/yr inside {PUBLISHED_RATE_BAND}"
                ),
            )
        )
        method = str(processed_meta.get("provenance", {}).get("method", ""))

        # The pipeline is moving while the runbook is frozen. Check what the CLI advertises now,
        # so the report names runbook drift instead of silently assuming yesterday's text.
        help_argv = [sys.executable, str(PROCESS), "--help"]
        help_display = _python_sh("pipeline/process_case.py", "--help")
        help_completed = _run("Step 6 (process_case.py --help)", help_argv)
        convention_flags = {
            flag: flag in help_completed.stdout
            for flag in (
                "--los-sign",
                "--heading-convention",
                "--validated-against",
                "--method-note",
            )
        }
        checks["process_case_convention_flags"] = convention_flags
        rows.append(
            Row(
                id="step6-cli-conventions",
                step="Step 6",
                check="process_case.py --help convention flags",
                command=help_display,
                exit_code=help_completed.returncode,
                verdict="rehearsed",
                rehearsable=True,
                executed_command=_executed(help_argv),
                note="advertised convention flags: "
                + ", ".join(flag for flag, present in convention_flags.items() if present),
            )
        )
        stale_sign_claim = "no CLI switch" in runbook_text
        stale_override_claim = (
            "provenance_overrides` is a Python argument, not a flag" in runbook_text
            or "provenance_overrides is a Python argument, not a flag" in runbook_text
        )
        if stale_sign_claim and (
            convention_flags["--los-sign"] or convention_flags["--heading-convention"]
        ):
            findings.append(
                "Step 6: the runbook says 'There is no CLI switch for the sign and heading cannot "
                "fix it', but process_case.py --help now advertises "
                + ", ".join(
                    flag
                    for flag in ("--los-sign", "--heading-convention")
                    if convention_flags[flag]
                )
                + ". The runbook text is stale; the rehearsal deliberately kept the default "
                "(away-from-sensor/platform) so the sign check still exercises the documented "
                "Step 5 command."
            )
        if stale_override_claim and (
            convention_flags["--validated-against"] or convention_flags["--method-note"]
        ):
            findings.append(
                "Step 6: the runbook says provenance_overrides is a Python argument, not a flag, "
                "but process_case.py --help now advertises "
                + ", ".join(
                    flag
                    for flag in ("--validated-against", "--method-note")
                    if convention_flags[flag]
                )
                + ". The default Step 5 command still leaves the 'NOT yet validated against GNSS' "
                "hedge; the new flags can clear it once a real GNSS/levelling check exists, so the "
                "runbook text is stale on this point too."
            )

        provisional_sentence = "NOT yet validated against GNSS" in method
        checks["provenance_method_provisional"] = provisional_sentence
        if provisional_sentence:
            if "that sentence must be gone" in runbook_text:
                findings.append(
                    "Step 6: the default (no convention flag) meta.provenance.method still contains "
                    "'NOT yet validated against GNSS'. The runbook's Step 6 gate ('that sentence "
                    "must be gone') is therefore not met by the documented Step 5 command; it "
                    "needs either a real GNSS/levelling validation or one of the pipeline's "
                    "convention flags."
                )
        else:
            findings.append(
                "Step 6: meta.provenance.method no longer contains the provisional "
                "'NOT yet validated against GNSS' sentence, so the runbook's sentence gate is "
                "satisfied by the current pipeline; the GNSS/levelling residual itself is still "
                "not rehearsable offline."
            )
        rows.append(
            Row(
                id="step6-gnss-provenance",
                step="Step 6",
                check="GNSS/levelling residual + provenance sentence removal",
                command="GNSS/levelling series at the reference pixel; validate the convention",
                exit_code=None,
                verdict=(
                    "not rehearsable offline: needs a real GNSS/levelling series; "
                    + (
                        "the pipeline now exposes a CLI convention flag, but the default command "
                        "still leaves the hedge and the synthetic sign check passed"
                        if convention_flags["--validated-against"]
                        or convention_flags["--method-note"]
                        else "the only documented route is a code change; the synthetic sign "
                        "check passed"
                    )
                ),
                rehearsable=False,
                note=(
                    "Target is < 5 mm/yr RMSE (README §13); the provisional sentence is "
                    + ("still present in the default run." if provisional_sentence else "absent.")
                ),
            )
        )

        # -------------------------------------------------------------------------------------
        # Step 7 — recording the real evidence.
        # -------------------------------------------------------------------------------------
        rows.append(
            Row(
                id="step7-record",
                step="Step 7",
                check="record dataset/mask/geometry/wavelength/coverage learnings",
                command="edit nisar_gunw.DATASETS, inspect_hdf5.PRODUCT_LAYOUTS, layout §2/§3/§5",
                exit_code=None,
                verdict=(
                    "not rehearsable offline: requires the real granule's evidence; the inspection "
                    "commands that supply it were rehearsed above"
                ),
                rehearsable=False,
                note=(
                    "The rehearsal does show the shape of the evidence: "
                    f"mask_values={summary.get('mask_values')}, "
                    f"epsg={summary.get('epsg')}, "
                    f"wavelength_source={summary.get('wavelength_source')}, "
                    f"geometry={probe_geometry}"
                ),
            )
        )

        findings.append(
            "Step 7: the x/y dataset names and the EPSG location remain unverified (layout §3). "
            "The fixture carries CF coordinates at the reader's candidate and a provisional "
            "geolocationGrid for the inspector; a real granule with only one of those will make "
            "the other report absence/None, which is exactly the correction event to record."
        )
        findings.append(
            "Step 3: process_case.py does not print a coherence line itself; the runbook's "
            "separate `pd.read_parquet(...).coherence.median()` snippet was executed and printed "
            f"{coherence_median:.6f} (coherence_min {COHERENCE_MIN})."
        )

        elapsed = time.monotonic() - started
        return _payload(
            status="pass",
            workdir=workdir,
            root=root,
            stack=stack,
            rows=rows,
            checks=checks,
            findings=findings,
            elapsed=elapsed,
            processed_root=processed_root,
            processed_out=processed_out,
            probe_root=probe_root,
            probe_out=probe_out,
        )
    except RehearsalError as exc:
        exc.rows = list(rows)
        exc.checks = dict(checks)
        exc.findings = list(findings)
        exc.elapsed_seconds = time.monotonic() - started
        raise


def _payload(
    *,
    status: str,
    workdir: Path,
    root: Path,
    stack: SyntheticStack,
    rows: list[Row],
    checks: dict[str, Any],
    findings: list[str],
    elapsed: float,
    processed_root: Path,
    processed_out: Path,
    probe_root: Path,
    probe_out: Path,
) -> dict[str, Any]:
    rehearsed = sum(1 for row in rows if row.rehearsable)
    not_rehearsable = sum(1 for row in rows if not row.rehearsable)
    return {
        "status": status,
        "runbook": DOC_RUNBOOK,
        "case": CASE,
        "workdir": str(workdir),
        "rehearsal_root": str(root),
        "elapsed_seconds": round(elapsed, 3),
        "summary": {
            "rehearsed": rehearsed,
            "not_rehearsable_offline": not_rehearsable,
            "failed": 0 if status == "pass" else 1,
        },
        "granules": [str(granule) for granule in stack.granules],
        "dates": list(stack.dates),
        "artifacts": {
            "probe_root": str(probe_root),
            "probe_case": str(probe_out),
            "processed_root": str(processed_root),
            "processed_case": str(processed_out),
            "nisar_grid": str(processed_out / "nisar_grid.parquet"),
            "timeseries": str(processed_out / "timeseries.csv"),
            "context_layers": str(processed_out / "context_layers.geojson"),
            "meta": str(processed_out / "meta.json"),
        },
        "rows": [row.as_dict() for row in rows],
        "checks": checks,
        "findings": findings,
    }


# ---------------------------------------------------------------------------------------------
# Human rendering
# ---------------------------------------------------------------------------------------------


def _cell_lines(text: object, width: int) -> list[str]:
    value = "" if text is None else str(text)
    return textwrap.wrap(value, width=width) or [""]


def print_table(rows: Sequence[Row], stream: Any = None) -> None:
    stream = stream or sys.stdout
    widths = (6, 30, 56, 4, 50)
    headers = ("step", "check", "command", "exit", "verdict")
    header = (
        f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  "
        f"{headers[2]:<{widths[2]}}  {headers[3]:>{widths[3]}}  {headers[4]:<{widths[4]}}"
    )
    print(header, file=stream)
    print("-" * len(header), file=stream)
    for row in rows:
        cells = [
            _cell_lines(row.step, widths[0]),
            _cell_lines(row.check, widths[1]),
            _cell_lines(row.command, widths[2]),
            _cell_lines("" if row.exit_code is None else str(row.exit_code), widths[3]),
            _cell_lines(row.verdict, widths[4]),
        ]
        height = max(len(cell) for cell in cells)
        for index in range(height):
            step = cells[0][index] if index < len(cells[0]) else ""
            check = cells[1][index] if index < len(cells[1]) else ""
            command = cells[2][index] if index < len(cells[2]) else ""
            exit_code = cells[3][index] if index < len(cells[3]) else ""
            verdict = cells[4][index] if index < len(cells[4]) else ""
            print(
                f"{step:<{widths[0]}}  {check:<{widths[1]}}  {command:<{widths[2]}}  "
                f"{exit_code:>{widths[3]}}  {verdict:<{widths[4]}}",
                file=stream,
            )
        print("-" * len(header), file=stream)


def print_human(payload: dict[str, Any]) -> None:
    print(f"Offline rehearsal of {payload['runbook']}")
    print(f"case      : {payload['case']}")
    print(f"workdir   : {payload['workdir']}")
    dates = payload.get("dates") or []
    span = f"{dates[0]} … {dates[-1]}" if dates else "dates unavailable (run failed early)"
    print(f"stack     : {len(payload['granules'])} synthetic granules, {POL}, track 001, "
          f"ascending, {span}")
    print(f"artefacts : {payload['artifacts']['processed_case']}")
    print()
    print_table([_row_from_dict(item) for item in payload["rows"]])
    print()
    checks = payload["checks"]
    coverage = checks.get("coverage", {})
    velocity = checks.get("velocity", {})
    print("Observed (synthetic evidence):")
    print(
        f"  coverage        {coverage.get('percent', float('nan')):.2f}% of "
        f"{coverage.get('aoi_cells', 0)} AOI cells "
        f"({coverage.get('covered_cells', 0)} covered); "
        f"WARNING: COVERAGE fired={coverage.get('warning_fired')}"
    )
    print(f"  coherence median {checks.get('coherence_median', float('nan')):.6f} "
          f"(coherence_min {COHERENCE_MIN})")
    print(
        f"  velocity        median {velocity.get('median_mm_yr', float('nan')):.3f} mm/yr, "
        f"p05 {velocity.get('p05_mm_yr', float('nan')):.3f} mm/yr "
        f"(published band {PUBLISHED_RATE_BAND})"
    )
    print(f"  geometry        {checks.get('geometry', 'n/a')}")
    print(f"  gate            exit {checks.get('gate_exit_code', 'n/a')}")
    print()
    print("Findings (reported, not fixed here):")
    for index, finding in enumerate(payload.get("findings", []), 1):
        print(f"  {index}. {textwrap.fill(finding, width=118, subsequent_indent='     ')}")
    print()
    summary = payload["summary"]
    print(
        f"STATUS: {payload['status'].upper()} — {summary['rehearsed']} rehearsed, "
        f"{summary['not_rehearsable_offline']} not rehearsable offline, "
        f"{summary['failed']} failed"
    )
    print(f"elapsed: {payload['elapsed_seconds']:.2f}s")


def _row_from_dict(item: dict[str, Any]) -> Row:
    return Row(
        id=item["id"],
        step=item["runbook_step"],
        check=item["check"],
        command=item["command"],
        exit_code=item["exit_code"],
        verdict=item["verdict"],
        rehearsable=item["rehearsable"],
        note=item.get("note", ""),
        executed_command=item.get("executed_command"),
    )


def print_failure(exc: RehearsalError) -> None:
    print(
        f"\nREHEARSAL FAILED — {exc.step}\n"
        f"  reason : {exc}\n"
        f"  command: {exc.command}\n"
        f"  exit   : {exc.exit_code!r}",
        file=sys.stderr,
    )
    if exc.stderr_tail:
        print("  stderr (tail):", file=sys.stderr)
        for line in exc.stderr_tail.splitlines():
            print(f"    {line}", file=sys.stderr)


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rehearse_first_granule.py",
        description=(
            "Rehearse every offline step of docs/first-granule-runbook.md on a synthetic NISAR "
            "L2 GUNW stack under /tmp. No credentials, no network, no data/processed writes."
        ),
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="directory for granules and artefacts (default: a fresh mkdtemp under /tmp)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the table, checks and findings as JSON instead of human text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workdir, root = _prepare_workdir(args.workdir)
    except WorkdirError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        payload = rehearse(workdir, root)
    except RehearsalError as exc:
        payload = _payload(
            status="fail",
            workdir=workdir,
            root=root,
            stack=SyntheticStack(
                granules=tuple(sorted((root / "granules").glob("*.h5"))),
                dates=(),
                x=np.array([]),
                y=np.array([]),
                velocity_mm_yr=np.array([[]]),
                cells=np.array([[]], dtype=object),
                valid=np.array([[]], dtype=bool),
            ),
            rows=exc.rows,
            checks=exc.checks,
            findings=exc.findings,
            elapsed=exc.elapsed_seconds,
            processed_root=root / "processed",
            processed_out=root / "processed" / CASE,
            probe_root=root / "gunw-probe",
            probe_out=root / "gunw-probe" / CASE,
        )
        payload["error"] = {
            "step": exc.step,
            "reason": str(exc),
            "command": exc.command,
            "exit_code": exc.exit_code,
            "stderr_tail": exc.stderr_tail,
        }
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            print_human(payload)
            print_failure(exc)
        return 1

    payload["error"] = None
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print_human(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
