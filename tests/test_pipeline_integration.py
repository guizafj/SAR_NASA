"""End-to-end integration: the real-data stages actually compose into one artefact set.

Every other test file proves one stage in isolation. This file proves the *chain*: three synthetic
NISAR L2 GUNW granules (documented layout of `docs/nisar-gunw-layout.md` §2–§3) go through the
reader, the phase→mm conversion, `process_case`, the four frozen artefacts, the file-level gate,
GIS context enrichment, and back through processing with the `land_use.csv` sidecar — all inside
`tmp_path`, with no network, credentials or downloaded NASA data.

One test per link plus one full-chain test:

1. synthetic granule stack (same track/direction, 12-day cadence, HH, mask 0/2/127, NaN patch)
2. reader → `phase_to_los_displacement_mm` → `process_case`, referenced series and trend recovery
3. `write_artefacts` → the four files, contract-valid
4. `pipeline/validate_artifacts.py --processed … --json` as a subprocess
5. `pipeline/enrich_context.py` CLI → valid context + `land_use.csv`, then `--land-use-csv`
6. refusals stay refusals across stage boundaries (non-zero CLI exit codes)
7. one `provenance_id` in all four artefacts, resolving inside `meta.json`
8. the same chain twice → byte-identical (or frame-identical) grid and series

Fixture conventions are copied from `tests/test_nisar_gunw.py` (granule builder) and
`tests/test_process_case.py` (projected grid, injected field, H3 cells), deliberately not by
importing across test modules. Nothing here reaches into private helpers of the modules under test.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

# HDF5, H3 and pyproj are hard requirements of this chain, not of its pure helpers.
h5py = pytest.importorskip("h5py")
h3 = pytest.importorskip("h3")
pytest.importorskip("pyproj")

from pyproj import Transformer  # noqa: E402

from pipeline import process_case as P  # noqa: E402
from pipeline import validate_artifacts as gate  # noqa: E402
from pipeline.ingest import nisar_gunw as G  # noqa: E402
from pipeline.science import transforms as V  # noqa: E402
from src.domain import contracts as C  # noqa: E402

CASE = "mexico-city"
BASE = "/science/LSAR/GUNW"
GRIDS = f"{BASE}/grids/frequencyA"
UNWRAPPED = f"{GRIDS}/unwrappedInterferogram"
RADAR = f"{BASE}/metadata/radarGrid"

PROCESS_MODULE = ROOT / "pipeline" / "process_case.py"
ENRICH_MODULE = ROOT / "pipeline" / "enrich_context.py"
GATE_MODULE = ROOT / "pipeline" / "validate_artifacts.py"

#: Geometry used to generate and to invert the synthetic phase; both sides must agree.
INCIDENCE_DEG = 39.0
HEADING_DEG = 192.0
WAVELENGTH_M = 0.2384  # docs/nisar-gunw-layout.md §4
EPSG = 32614  # Mexico City, UTM 14N (config crs_analysis)

#: 20 x 20 pixels at NISAR's 80 m posting, centred on the configured reference point.
NX, NY = 20, 20
SPACING_M = 80.0
REF_IX, REF_IY = NX // 2, NY // 2
SPACING_DAYS = 12
PAIRS = 3  # 3 granules -> 4 epochs, the minimum a harmonic fit can use
START = "2026-07-02"

#: The two masked patches: 127 padding (dropped) and non-finite phase (dropped). Both are away
#: from the reference pixel so the spatial reference stays usable.
MASK_PATCH = (0, 6, 0, 8)  # iy0:iy1, ix0:ix1 -> mask 127
FLAG_PATCH = (8, 10, 8, 14)  # mask 2: a quality flag, kept (not padding)
NAN_PATCH = (14, 17, 14, 17)  # iy0:iy1, ix0:ix1 -> NaN phase/coherence
LOW_COHERENCE_PIXEL = (18, 1)  # below the frozen coherence_min -> dropped, never 0

OFFSET_MM = 25.0
PEAK_MM_YR = -180.0
FALLOFF_PX = 4.0
COHERENCE_MIN = C.SENSOR_PROFILES["nisar-gunw"].coherence_min
H3_RESOLUTION = P.load_case_config(CASE).h3_resolution


# ---------------------------------------------------------------------------------------------
# Synthetic granule stack (one fixture builder, no downloaded data)
# ---------------------------------------------------------------------------------------------


def _transformers() -> tuple[Transformer, Transformer]:
    forward = Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True)
    inverse = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True)
    return forward, inverse


def grid_coordinates() -> tuple[np.ndarray, np.ndarray]:
    """Projected x/y of the fixture, centred on the configured reference point."""
    cfg = P.load_case_config(CASE)
    forward, _ = _transformers()
    x0, y0 = forward.transform(*cfg.reference_lonlat)
    x = x0 + (np.arange(NX) - REF_IX) * SPACING_M
    y = y0 + (np.arange(NY) - REF_IY) * SPACING_M
    return x, y


def velocity_field() -> np.ndarray:
    """Injected secular velocity (mm/yr), exactly 0 at the reference pixel."""
    columns = np.arange(NX)[None, :]
    rows = np.arange(NY)[:, None]
    distance = np.hypot(columns - REF_IX, rows - REF_IY)
    return PEAK_MM_YR * (1.0 - np.exp(-distance / FALLOFF_PX))


@dataclass
class Stack:
    """One synthetic stack plus everything needed to predict the published numbers."""

    granules: list[pathlib.Path]
    dates: list[str]
    x: np.ndarray
    y: np.ndarray
    velocity: np.ndarray  # (NY, NX) injected mm/yr
    valid: np.ndarray  # (NY, NX) pixels the chain should keep
    cells: np.ndarray  # (NY, NX) H3 res-9 cell of each pixel centroid

    @property
    def valid_cells(self) -> set[str]:
        return {
            str(cell)
            for cell, ok in zip(self.cells.ravel(), self.valid.ravel(), strict=True)
            if ok
        }

    def expected_velocity(self) -> dict[str, float]:
        """Mean injected velocity per valid H3 cell, minus the reference pixel's own velocity."""
        reference = float(self.velocity[REF_IY, REF_IX])
        buckets: dict[str, list[float]] = defaultdict(list)
        for row in range(NY):
            for column in range(NX):
                if not self.valid[row, column]:
                    continue
                buckets[str(self.cells[row, column])].append(
                    float(self.velocity[row, column]) - reference
                )
        return {cell: float(np.mean(values)) for cell, values in buckets.items()}


def _write_granule(
    path: pathlib.Path,
    *,
    phase: np.ndarray,
    coherence: np.ndarray,
    mask: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    reference_date: str,
    secondary_date: str,
) -> pathlib.Path:
    """Write one granule with the documented GUNW layout; no optional value is invented."""
    with h5py.File(path, "w") as handle:
        group = handle.require_group(f"{UNWRAPPED}/HH")
        group.create_dataset("unwrappedPhase", data=phase.astype(np.float32))
        group.create_dataset("coherenceMagnitude", data=coherence.astype(np.float32))
        group.create_dataset(
            "connectedComponents", data=np.ones(phase.shape, dtype=np.uint32)
        )
        handle.create_dataset(f"{UNWRAPPED}/mask", data=mask.astype(np.int8))

        handle.create_dataset(f"{GRIDS}/xCoordinates", data=x.astype(np.float64))
        handle.create_dataset(f"{GRIDS}/yCoordinates", data=y.astype(np.float64))
        handle.create_dataset(f"{GRIDS}/projection", data=np.int32(EPSG))

        handle.create_dataset(f"{RADAR}/wavelength", data=np.float64(WAVELENGTH_M))
        handle.create_dataset(f"{RADAR}/numberOfLooks", data=np.int32(20))
        handle.create_dataset(f"{RADAR}/incidenceAngle", data=np.float64(INCIDENCE_DEG))
        handle.create_dataset(f"{RADAR}/heading", data=np.float64(HEADING_DEG))

        handle.create_dataset(
            f"{BASE}/identification/referenceZeroDopplerStartTime",
            data=f"{reference_date}T03:12:45Z",
        )
        handle.create_dataset(
            f"{BASE}/identification/secondaryZeroDopplerStartTime",
            data=f"{secondary_date}T03:12:45Z",
        )
    return path


def build_stack(directory: pathlib.Path) -> Stack:
    """Build three chained 12-day GUNW pairs encoding a known, temporally linear subsidence."""
    directory.mkdir(parents=True, exist_ok=True)
    start = dt.date.fromisoformat(START)
    dates = [
        (start + dt.timedelta(days=SPACING_DAYS * index)).isoformat()
        for index in range(PAIRS + 1)
    ]
    x, y = grid_coordinates()
    velocity = velocity_field()

    # Linear in time: cumulative displacement d(t) = OFFSET + v·(t − t0), so every 12-day pair
    # carries the same increment and the offset cancels in d(secondary) − d(reference).
    t_years = {
        date: (dt.date.fromisoformat(date) - start).days / V.DAYS_PER_YEAR for date in dates
    }
    vertical = {date: OFFSET_MM + velocity * t_years[date] for date in dates}

    mask = np.zeros((NY, NX), dtype=np.int8)
    mask[MASK_PATCH[0] : MASK_PATCH[1], MASK_PATCH[2] : MASK_PATCH[3]] = G.MASK_PADDING
    mask[FLAG_PATCH[0] : FLAG_PATCH[1], FLAG_PATCH[2] : FLAG_PATCH[3]] = 2

    coherence = np.full((NY, NX), 0.80, dtype=float)
    coherence[MASK_PATCH[0] : MASK_PATCH[1], MASK_PATCH[2] : MASK_PATCH[3]] = 0.70
    coherence[LOW_COHERENCE_PIXEL] = 0.20

    _, inverse = _transformers()
    lon2d, lat2d = inverse.transform(*np.meshgrid(x, y))
    cells = np.array(
        [
            h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION)
            for lat, lon in zip(lat2d.ravel(), lon2d.ravel(), strict=True)
        ],
        dtype=object,
    ).reshape(NY, NX)

    # Pixels the chain must drop: padding, low coherence and the non-finite patch.
    valid = (mask != G.MASK_PADDING) & (coherence >= COHERENCE_MIN)
    valid[NAN_PATCH[0] : NAN_PATCH[1], NAN_PATCH[2] : NAN_PATCH[3]] = False

    granules: list[pathlib.Path] = []
    for index, (reference_date, secondary_date) in enumerate(
        zip(dates[:-1], dates[1:], strict=True)
    ):
        increment = vertical[secondary_date] - vertical[reference_date]
        los_mm = V.project_to_los(
            increment, INCIDENCE_DEG, HEADING_DEG, v_east=0.0, v_north=0.0
        )
        # Invert d_LOS = -lambda/(4*pi) * phi  ->  phi = -4*pi*d / lambda (d in metres).
        phase = -4.0 * np.pi * (los_mm / 1000.0) / WAVELENGTH_M
        phase[NAN_PATCH[0] : NAN_PATCH[1], NAN_PATCH[2] : NAN_PATCH[3]] = np.nan
        name = (
            f"NISAR_L2_PR_GUNW_{index + 1:03d}_001_A_005_{index + 2:03d}_4000_SH_"
            f"{reference_date.replace('-', '')}T031245_"
            f"{secondary_date.replace('-', '')}T031245.h5"
        )
        granules.append(
            _write_granule(
                directory / name,
                phase=phase,
                coherence=coherence,
                mask=mask,
                x=x,
                y=y,
                reference_date=reference_date,
                secondary_date=secondary_date,
            )
        )
    return Stack(
        granules=granules,
        dates=dates,
        x=x,
        y=y,
        velocity=velocity,
        valid=valid,
        cells=cells,
    )


# ---------------------------------------------------------------------------------------------
# Chain helpers
# ---------------------------------------------------------------------------------------------


def run_cli(module: pathlib.Path, *args: object) -> subprocess.CompletedProcess[str]:
    """Run a module through the real interpreter, exactly as CI would (argument parsing included)."""
    return subprocess.run(
        [sys.executable, str(module), *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )


def process_argv(stack: Stack, out: pathlib.Path, *extra: object) -> list[str]:
    argv = ["--case", CASE, "--out", str(out)]
    for granule in stack.granules:
        argv += ["--granule", str(granule)]
    return argv + [str(item) for item in extra]


def write_chain(stack: Stack, out: pathlib.Path) -> P.ProcessedCase:
    """`process_case` + `write_artefacts` into one case directory."""
    processed = P.process_case(stack.granules, CASE, out_dir=out)
    sizes = P.write_artefacts(processed, out)
    assert set(sizes) == set(gate.ARTEFACTS)
    return processed


def load_artefacts(out: pathlib.Path) -> dict[str, object]:
    return {
        "grid": pd.read_parquet(out / "nisar_grid.parquet"),
        "timeseries": pd.read_csv(out / "timeseries.csv"),
        "meta": json.loads((out / "meta.json").read_text(encoding="utf-8")),
        "context": json.loads((out / "context_layers.geojson").read_text(encoding="utf-8")),
    }


def assert_contract_valid(artefacts: dict[str, object]) -> None:
    report = C.validate_all(
        grid=artefacts["grid"],
        timeseries=artefacts["timeseries"],
        meta=artefacts["meta"],
        context=artefacts["context"],
        case_id=CASE,
    )
    assert report == {name: [] for name in report}, report


def _feature(geometry: dict[str, object], properties: dict[str, object]) -> dict[str, object]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _box(lon0: float, lat0: float, lon1: float, lat1: float) -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]],
    }


def write_context_geojson(path: pathlib.Path, grid: pd.DataFrame) -> pathlib.Path:
    """A small source extract covering the fixture footprint: 2 land-use, 1 water, 1 point.

    The two land-use polygons split the grid's longitude extent, the water polygon contains one
    cell centroid, and the infrastructure point falls on another. All of it sits inside the AOI;
    only the ~1.6 km fixture footprint is covered, which is exactly the "covers part of the AOI"
    case the enrichment stage is for.
    """
    lons = grid["lon"].to_numpy(dtype=float)
    lats = grid["lat"].to_numpy(dtype=float)
    lo_lon, hi_lon = float(lons.min()), float(lons.max())
    lo_lat, hi_lat = float(lats.min()), float(lats.max())
    mid_lon = (lo_lon + hi_lon) / 2.0
    pad = 0.001

    water_cell = grid.iloc[0]
    water_lon, water_lat = float(water_cell["lon"]), float(water_cell["lat"])
    point_cell = grid.iloc[-1]

    collection = {
        "type": "FeatureCollection",
        "features": [
            _feature(
                _box(lo_lon - pad, lo_lat - pad, mid_lon, hi_lat + pad),
                {"landuse": "residential", "name": "colonia"},
            ),
            _feature(
                _box(mid_lon, lo_lat - pad, hi_lon + pad, hi_lat + pad),
                {"landuse": "industrial", "name": "parque industrial"},
            ),
            _feature(
                _box(
                    water_lon - 0.0005,
                    water_lat - 0.0005,
                    water_lon + 0.0005,
                    water_lat + 0.0005,
                ),
                {"natural": "water", "name": "lago"},
            ),
            _feature(
                {
                    "type": "Point",
                    "coordinates": [float(point_cell["lon"]), float(point_cell["lat"])],
                },
                {"power": "substation", "name": "subestacion"},
            ),
        ],
    }
    path.write_text(json.dumps(collection), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# Link 1 — the synthetic stack is a valid, chained GUNW stack
# ---------------------------------------------------------------------------------------------


def test_link_1_synthetic_granule_stack(tmp_path):
    stack = build_stack(tmp_path / "granules")

    assert len(stack.granules) == PAIRS == 3
    assert len(stack.dates) == PAIRS + 1 == 4
    for previous, following in zip(stack.dates[:-1], stack.dates[1:], strict=True):
        span = dt.date.fromisoformat(following) - dt.date.fromisoformat(previous)
        assert span.days == SPACING_DAYS

    granules = [G.read_granule(path) for path in stack.granules]
    for granule in granules:
        assert granule.polarisation == "HH"
        assert granule.epsg == EPSG
        assert granule.wavelength_m == pytest.approx(WAVELENGTH_M)
        assert granule.x is not None and granule.y is not None
        assert granule.x.shape == (NX,) and granule.y.shape == (NY,)
        assert granule.warnings == [], granule.warnings
        assert set(np.unique(granule.mask).tolist()) == {0, 2, G.MASK_PADDING}
        nan_patch = granule.phase_rad[
            NAN_PATCH[0] : NAN_PATCH[1], NAN_PATCH[2] : NAN_PATCH[3]
        ]
        assert np.isnan(nan_patch).all()
        valid = G.valid_mask(granule)
        assert 0.0 < float(valid.mean()) < 1.0

    # A chain: each granule's secondary date is the next granule's reference date.
    assert [granule.reference_time[:10] for granule in granules] == stack.dates[:-1]
    assert [granule.secondary_time[:10] for granule in granules] == stack.dates[1:]

    # Same relative orbit and flight direction; the identity comes from the filename.
    identities = [P.parse_granule_identity(path) for path in stack.granules]
    assert {identity.track for identity in identities} == {1}
    assert {identity.frame for identity in identities} == {5}
    assert {identity.direction for identity in identities} == {"ascending"}

    # The injected trend is temporally linear: equal 12-day pairs carry equal LOS increments.
    increments = [
        G.phase_to_los_displacement_mm(granule.phase_rad, granule.wavelength_m)
        for granule in granules
    ]
    for increment in increments[1:]:
        np.testing.assert_allclose(increment, increments[0], equal_nan=True)


# ---------------------------------------------------------------------------------------------
# Link 2 — reader → phase→mm → process_case, with a referenced series and the injected trend
# ---------------------------------------------------------------------------------------------


def test_link_2_reader_to_processed_case_recovers_the_trend(tmp_path):
    stack = build_stack(tmp_path / "granules")

    # The reader and the conversion are exercised directly before the chain repeats them.
    for path in stack.granules:
        granule = G.read_granule(path)
        los_mm = G.phase_to_los_displacement_mm(granule.phase_rad, granule.wavelength_m)
        assert los_mm.shape == granule.phase_rad.shape
        valid = G.valid_mask(granule)
        assert np.isfinite(los_mm[valid]).all()

    processed = P.process_case(
        stack.granules, CASE, out_dir=tmp_path / "processed" / CASE
    )

    # The published series is spatially referenced: the first epoch is exactly 0 mm.
    aoi = processed.timeseries.loc[processed.timeseries["cell_id"] == "aoi"].sort_values("date")
    assert list(aoi["date"]) == list(processed.epochs)
    assert processed.meta["dates"] == list(processed.epochs)
    assert float(aoi["value"].iloc[0]) == 0.0
    assert (processed.grid["val_t1"] == 0.0).all()

    expected = stack.expected_velocity()
    assert set(processed.grid["cell_id"]) == set(expected)
    residuals = [
        abs(float(row.velocity_mm_yr) - expected[row.cell_id])
        for row in processed.grid.itertuples()
    ]
    assert max(residuals) <= 1.0, (
        f"worst |velocity - injected| = {max(residuals):.4f} mm/yr "
        f"over {len(residuals)} cells"
    )


# ---------------------------------------------------------------------------------------------
# Link 3 — processed case → the four frozen artefacts, contract-valid
# ---------------------------------------------------------------------------------------------


def test_link_3_processed_case_to_contract_valid_artefacts(tmp_path):
    stack = build_stack(tmp_path / "granules")
    out = tmp_path / "processed" / CASE
    processed = write_chain(stack, out)

    assert sorted(path.name for path in out.iterdir()) == sorted(gate.ARTEFACTS)
    for name in gate.ARTEFACTS:
        path = out / name
        assert path.is_file()
        assert path.stat().st_size > 0

    # Validate the in-memory payload *and* the serialised files (the app reads the files).
    assert C.validate_all(
        grid=processed.grid,
        timeseries=processed.timeseries,
        meta=processed.meta,
        context=processed.context,
        case_id=CASE,
    ) == {name: [] for name in ("grid", "timeseries", "meta", "context")}

    assert_contract_valid(load_artefacts(out))


# ---------------------------------------------------------------------------------------------
# Link 4 — the file-level gate a CI job will call
# ---------------------------------------------------------------------------------------------


def test_link_4_file_level_gate_passes(tmp_path):
    stack = build_stack(tmp_path / "granules")
    out = tmp_path / "processed" / CASE
    write_chain(stack, out)

    completed = run_cli(GATE_MODULE, "--processed", out.parent, "--json")

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["cases_validated"] == 1
    assert payload["status"] == "pass"
    assert payload["exit_code"] == 0
    assert payload["problems"] == []
    assert payload["totals"]["failed"] == 0
    assert [case["case_id"] for case in payload["cases"]] == [CASE]


# ---------------------------------------------------------------------------------------------
# Link 5 — context enrichment composes, and the sidecar reaches the grid
# ---------------------------------------------------------------------------------------------


def test_link_5_context_enrichment_composes(tmp_path):
    stack = build_stack(tmp_path / "granules")
    out = tmp_path / "processed" / CASE
    write_chain(stack, out)

    source = write_context_geojson(tmp_path / "context.geojson", pd.read_parquet(out / "nisar_grid.parquet"))
    enriched = tmp_path / "enriched" / "context_layers.geojson"
    sidecar = tmp_path / "enriched" / "land_use.csv"

    completed = run_cli(
        ENRICH_MODULE,
        "--case",
        CASE,
        "--context",
        source,
        "--grid",
        out / "nisar_grid.parquet",
        "--out",
        enriched,
        "--land-use-out",
        sidecar,
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["validation_problems"] == []
    assert summary["cells_matched"] > 0
    assert summary["grid_file_modified"] is False

    collection = json.loads(enriched.read_text(encoding="utf-8"))
    assert C.validate_context_layers(collection) == []
    layers = {feature["properties"]["layer"] for feature in collection["features"]}
    assert {"land_use", "water", "infrastructure", "admin_boundary"} <= layers
    assert any(feature["properties"]["class"] == "aoi" for feature in collection["features"])

    assert sidecar.is_file()
    side = pd.read_csv(sidecar)
    assert list(side.columns) == ["cell_id", "land_use"]
    assert set(side["land_use"]) <= set(C.LAND_USE)
    assert side["land_use"].nunique() > 1, "the fixture must produce more than one class"

    # Re-run the processing step through the CLI with --land-use-csv; the sidecar must reach the
    # published grid, and risk_level must still be a pure function of the velocity.
    with_sidecar = tmp_path / "with-land-use" / CASE
    completed = run_cli(
        PROCESS_MODULE, *process_argv(stack, with_sidecar, "--land-use-csv", sidecar)
    )
    assert completed.returncode == 0, completed.stderr
    assert with_sidecar.is_dir()

    grid = pd.read_parquet(with_sidecar / "nisar_grid.parquet")
    assert grid["land_use"].nunique() > 1
    assert set(grid["land_use"]) <= set(C.LAND_USE)
    expected_labels = dict(zip(side["cell_id"], side["land_use"], strict=True))
    assert dict(zip(grid["cell_id"], grid["land_use"], strict=True)) == expected_labels
    assert list(grid["risk_level"]) == [
        C.risk_level_from_velocity(float(velocity)) for velocity in grid["velocity_mm_yr"]
    ]


# ---------------------------------------------------------------------------------------------
# Link 6 — refusals hold across stage boundaries
# ---------------------------------------------------------------------------------------------


def test_link_6_refusals_hold_across_stages(tmp_path):
    # (a) A stack that mixes relative orbits is refused by process_case, with a non-zero CLI exit.
    stack = build_stack(tmp_path / "granules")
    moved = stack.granules[1]
    # Only the TRACK token moves (001 -> 002); the cycle chain stays intact. The marker can only
    # be the track token under the census order.
    renamed = moved.with_name(moved.name.replace("_001_A_005_", "_002_A_005_", 1))
    assert renamed.name != moved.name, "the fixture name must contain the track token"
    moved.rename(renamed)
    stack.granules[1] = renamed

    with pytest.raises(P.TrackMismatchError):
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "refused" / CASE)

    mixed_out = tmp_path / "mixed" / CASE
    completed = run_cli(PROCESS_MODULE, *process_argv(stack, mixed_out))
    assert completed.returncode != 0
    assert "refused" in completed.stderr.lower()
    assert not mixed_out.exists() or not any(mixed_out.iterdir())

    # (b) A context extract with nothing inside the AOI is refused by enrich_context.
    clean = build_stack(tmp_path / "clean-granules")
    out = tmp_path / "processed" / CASE
    write_chain(clean, out)

    outside = tmp_path / "outside.geojson"
    outside.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    _feature(_box(0.0, 0.0, 1.0, 1.0), {"landuse": "residential"})
                ],
            }
        ),
        encoding="utf-8",
    )
    refused_context = tmp_path / "refused" / "context_layers.geojson"
    refused_sidecar = tmp_path / "refused" / "land_use.csv"
    completed = run_cli(
        ENRICH_MODULE,
        "--case",
        CASE,
        "--context",
        outside,
        "--grid",
        out / "nisar_grid.parquet",
        "--out",
        refused_context,
        "--land-use-out",
        refused_sidecar,
    )
    assert completed.returncode != 0
    assert "no features inside the AOI" in completed.stderr
    assert not refused_context.exists()
    assert not refused_sidecar.exists()


# ---------------------------------------------------------------------------------------------
# Link 7 — provenance is one id, carried by all four artefacts
# ---------------------------------------------------------------------------------------------


def test_link_7_provenance_survives_the_chain(tmp_path):
    stack = build_stack(tmp_path / "granules")
    out = tmp_path / "processed" / CASE
    processed = write_chain(stack, out)
    artefacts = load_artefacts(out)

    meta = artefacts["meta"]
    grid = artefacts["grid"]
    series = artefacts["timeseries"]
    context = artefacts["context"]

    provenance_id = meta["provenance"]["provenance_id"]
    assert C.PROVENANCE_ID_RE.match(provenance_id)
    assert provenance_id == processed.provenance_id
    assert provenance_id.startswith(f"{CASE}+nisar-gunw+")
    assert provenance_id in json.dumps(meta), "the id must resolve inside meta.json"

    assert set(grid["provenance_id"]) == {provenance_id}
    assert set(series["provenance_id"]) == {provenance_id}
    assert {
        feature["properties"]["provenance_id"] for feature in context["features"]
    } == {provenance_id}

    assert meta["product"] == "nisar-gunw"
    assert set(grid["product"]) == {"nisar-gunw"}
    assert set(series["product"]) == {"nisar-gunw"}

    source = meta["provenance"]["source"]
    lowered = source.lower()
    assert "nisar" in lowered and "gunw" in lowered
    assert "mock" not in lowered
    assert not any("mock" in str(value).lower() for value in (meta["product"], grid["product"].iloc[0]))
    for granule in stack.granules:
        assert granule.stem in meta["provenance"]["product_id"]


# ---------------------------------------------------------------------------------------------
# Link 8 — the chain is deterministic
# ---------------------------------------------------------------------------------------------


def _assert_same_artefact(first: pathlib.Path, second: pathlib.Path) -> None:
    first_bytes = first.read_bytes()
    second_bytes = second.read_bytes()
    if first_bytes == second_bytes:
        return
    # Parquet embeds writer metadata that can change across pyarrow builds; the payload must not.
    if first.suffix == ".parquet":
        pd.testing.assert_frame_equal(pd.read_parquet(first), pd.read_parquet(second))
    else:
        pd.testing.assert_frame_equal(pd.read_csv(first), pd.read_csv(second))


def test_link_8_determinism_of_the_chain(tmp_path):
    stack = build_stack(tmp_path / "granules")
    first = tmp_path / "run-a" / CASE
    second = tmp_path / "run-b" / CASE
    write_chain(stack, first)
    write_chain(stack, second)

    for name in ("nisar_grid.parquet", "timeseries.csv"):
        _assert_same_artefact(first / name, second / name)

    # Belt and braces: even if the byte comparison above took the metadata-tolerant branch, the
    # frames are identical.
    pd.testing.assert_frame_equal(
        pd.read_parquet(first / "nisar_grid.parquet"),
        pd.read_parquet(second / "nisar_grid.parquet"),
    )
    pd.testing.assert_frame_equal(
        pd.read_csv(first / "timeseries.csv"), pd.read_csv(second / "timeseries.csv")
    )


# ---------------------------------------------------------------------------------------------
# The full chain — every stage in one run, end to end
# ---------------------------------------------------------------------------------------------


def test_full_chain_composes_end_to_end(tmp_path):
    stack = build_stack(tmp_path / "granules")

    # 1-2. Reader + phase conversion + processed case.
    granules = [G.read_granule(path) for path in stack.granules]
    assert all(granule.polarisation == "HH" for granule in granules)
    for granule in granules:
        los_mm = G.phase_to_los_displacement_mm(granule.phase_rad, granule.wavelength_m)
        assert np.isfinite(los_mm[G.valid_mask(granule)]).all()

    out = tmp_path / "processed" / CASE
    processed = write_chain(stack, out)
    aoi = processed.timeseries.loc[processed.timeseries["cell_id"] == "aoi"].sort_values("date")
    assert float(aoi["value"].iloc[0]) == 0.0

    # 3. Contract-valid four-file set.
    artefacts = load_artefacts(out)
    assert_contract_valid(artefacts)

    # 4. File-level gate, through the CLI.
    completed = run_cli(GATE_MODULE, "--processed", out.parent, "--json")
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["cases_validated"] == 1

    # 5. Context enrichment writes a valid context artefact and a land_use sidecar.
    source = write_context_geojson(tmp_path / "context.geojson", artefacts["grid"])
    enriched = tmp_path / "enriched" / "context_layers.geojson"
    sidecar = tmp_path / "enriched" / "land_use.csv"
    completed = run_cli(
        ENRICH_MODULE,
        "--case",
        CASE,
        "--context",
        source,
        "--grid",
        out / "nisar_grid.parquet",
        "--out",
        enriched,
        "--land-use-out",
        sidecar,
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    collection = json.loads(enriched.read_text(encoding="utf-8"))
    assert C.validate_context_layers(collection) == []
    assert {
        feature["properties"]["provenance_id"] for feature in collection["features"]
    } == {artefacts["meta"]["provenance"]["provenance_id"]}
    assert pd.read_csv(sidecar)["land_use"].nunique() > 1

    # 6. Feed the sidecar back through the processing CLI into a fresh, final tree.
    final = tmp_path / "final" / CASE
    completed = run_cli(
        PROCESS_MODULE, *process_argv(stack, final, "--land-use-csv", sidecar)
    )
    assert completed.returncode == 0, completed.stderr

    final_artefacts = load_artefacts(final)
    assert_contract_valid(final_artefacts)
    grid = final_artefacts["grid"]
    assert grid["land_use"].nunique() > 1
    assert list(grid["risk_level"]) == [
        C.risk_level_from_velocity(float(velocity)) for velocity in grid["velocity_mm_yr"]
    ]

    # 7. Provenance is unchanged by the extra stage.
    provenance_id = final_artefacts["meta"]["provenance"]["provenance_id"]
    assert set(grid["provenance_id"]) == {provenance_id}
    assert set(final_artefacts["timeseries"]["provenance_id"]) == {provenance_id}
    assert final_artefacts["meta"]["product"] == "nisar-gunw"
    assert "mock" not in final_artefacts["meta"]["provenance"]["source"].lower()

    # 4 again, on the final tree: the property CI consumes still holds after enrichment.
    completed = run_cli(GATE_MODULE, "--processed", final.parent, "--json")
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["cases_validated"] == 1

    # 2 again: the trend survived every stage.
    expected = stack.expected_velocity()
    assert set(grid["cell_id"]) == set(expected)
    residuals = [
        abs(float(row.velocity_mm_yr) - expected[row.cell_id]) for row in grid.itertuples()
    ]
    assert max(residuals) <= 1.0
