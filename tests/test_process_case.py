"""Hermetic tests for the real-data chain (`pipeline/process_case.py`).

Every fixture is a small synthetic HDF5 stack written to ``tmp_path`` with the documented GUNW
layout (`docs/nisar-gunw-layout.md` §2–§3) and a *known* synthetic deformation, so the numbers the
chain publishes can be predicted independently of the chain. Nothing is downloaded, no real granule
is required and no network is touched.

The injected field is a radial subsidence bowl relative to the reference pixel plus a constant
offset::

    v_px(i, j) = PEAK * (1 - exp(-d(i, j) / FALLOFF)),   d = pixel distance from the reference
    d_px(t)    = OFFSET + v_px * (t - t0)               [mm, up positive]

`v_px` is exactly 0 at the reference pixel, so the referenced (InSAR) velocity of a cell is the mean
of `v_px` over the cell's pixels, and `OFFSET` must cancel completely: the first epoch is 0 mm.

A note on epochs. The brief says "two or three epochs of 12 days"; the main fixture uses three
granules, i.e. **four epochs**, because `transforms.harmonic_fit` refuses fewer than
`MIN_FIT_POINTS = 4` samples (a 2-parameter fit on 2–3 points is underdetermined, and the mock's
`velocity_mm_yr` is defined as that fitted trend). A single-granule stack is exercised explicitly to
prove the refusal, not silently fitted.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
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

#: Geometry used to *generate* and to *invert* the synthetic phase; the chain must use the same.
INCIDENCE_DEG = 39.0
HEADING_DEG = 192.0
WAVELENGTH_M = 0.2384  # docs/nisar-gunw-layout.md §4
EPSG = 32614  # Mexico City, UTM 14N (config crs_analysis)

#: Fixture geometry: 20 x 20 pixels at NISAR's 80 m posting, centred on the case reference point.
NX, NY = 20, 20
SPACING_M = 80.0
REF_IX, REF_IY = NX // 2, NY // 2
MASK_PATCH = (0, 8, 0, 8)  # iy0:iy1, ix0:ix1 -> mask 127 (nodata), away from the reference pixel
BAD_COHERENCE_PIXEL = (15, 15)  # below the frozen coherence_min -> dropped, never 0
OFFSET_MM = 25.0
PEAK_MM_YR = -180.0
FALLOFF_PX = 4.0
START = "2026-07-02"
STEP_DAYS = 12
COHERENCE_MIN = C.SENSOR_PROFILES["nisar-gunw"].coherence_min
H3_RESOLUTION = P.load_case_config(CASE).h3_resolution


# ---------------------------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------------------------


def _transformers() -> tuple[Transformer, Transformer]:
    forward = Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True)
    inverse = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True)
    return forward, inverse


def grid_coordinates(center_lonlat: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Projected x/y of the fixture, centred on the configured reference point by default."""
    cfg = P.load_case_config(CASE)
    forward, _ = _transformers()
    x0, y0 = forward.transform(*(center_lonlat or cfg.reference_lonlat))
    x = x0 + (np.arange(NX) - REF_IX) * SPACING_M
    y = y0 + (np.arange(NY) - REF_IY) * SPACING_M
    return x, y


def east_aoi_edge_lon(cfg: P.CaseConfig, lat: float) -> float:
    """Longitude where the effective AOI ring crosses `lat`, on the eastern (larger-lon) side.

    Derived from the ring the pipeline actually uses (`cfg.aoi_ring`, i.e. the QGIS drop-in when
    present), never from a rectangle written down separately: a fixture pinned to a retired bbox
    edge silently moves outside the AOI the day the footprint is refined.
    """
    ring = list(cfg.aoi_ring)
    crossings: list[float] = []
    for (lon1, lat1), (lon2, lat2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (lat1 > lat) != (lat2 > lat):
            crossings.append(lon1 + (lon2 - lon1) * (lat - lat1) / (lat2 - lat1))
    assert crossings, f"the AOI ring never crosses latitude {lat}"
    return max(crossings)


def velocity_field() -> np.ndarray:
    """Injected secular velocity (mm/yr), exactly 0 at the reference pixel."""
    columns = np.arange(NX)[None, :]
    rows = np.arange(NY)[:, None]
    distance = np.hypot(columns - REF_IX, rows - REF_IY)
    return PEAK_MM_YR * (1.0 - np.exp(-distance / FALLOFF_PX))


@dataclass
class Stack:
    """One synthetic stack plus everything the assertions need to predict the output."""

    granules: list[pathlib.Path]
    dates: list[str]
    x: np.ndarray
    y: np.ndarray
    velocity: np.ndarray  # (NY, NX) injected mm/yr
    valid: np.ndarray  # (NY, NX) pixels the chain should keep
    cells: np.ndarray  # (NY, NX) H3 res-9 cell of each pixel centroid

    @property
    def valid_cells(self) -> set[str]:
        return {str(cell) for cell, ok in zip(self.cells.ravel(), self.valid.ravel(), strict=True) if ok}

    @property
    def all_cells(self) -> set[str]:
        return {str(cell) for cell in self.cells.ravel()}

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
    polarisation: str,
    incidence: float | np.ndarray | None,
    heading: float | None,
    wavelength: float | None,
    looks: int | None,
    omit: frozenset[str],
) -> pathlib.Path:
    """Write one granule with the documented GUNW layout; optional metadata stays optional."""
    with h5py.File(path, "w") as handle:
        group = handle.require_group(f"{UNWRAPPED}/{polarisation}")
        if "phase" not in omit:
            group.create_dataset("unwrappedPhase", data=phase.astype(np.float32))
        if "coherence" not in omit:
            group.create_dataset("coherenceMagnitude", data=coherence.astype(np.float32))
        if "connected_components" not in omit:
            group.create_dataset(
                "connectedComponents", data=np.ones(phase.shape, dtype=np.uint32)
            )
        if "mask" not in omit:
            handle.create_dataset(f"{UNWRAPPED}/mask", data=mask.astype(np.int8))

        handle.create_dataset(f"{GRIDS}/xCoordinates", data=x.astype(np.float64))
        handle.create_dataset(f"{GRIDS}/yCoordinates", data=y.astype(np.float64))
        handle.create_dataset(f"{GRIDS}/projection", data=np.int32(EPSG))

        if wavelength is not None:
            handle.create_dataset(f"{RADAR}/wavelength", data=np.float64(wavelength))
        if looks is not None:
            handle.create_dataset(f"{RADAR}/numberOfLooks", data=np.int32(looks))
        if incidence is not None:
            handle.create_dataset(f"{RADAR}/incidenceAngle", data=np.asarray(incidence, dtype=np.float64))
        if heading is not None:
            handle.create_dataset(f"{RADAR}/heading", data=np.float64(heading))

        handle.create_dataset(
            f"{BASE}/identification/referenceZeroDopplerStartTime",
            data=f"{reference_date}T03:12:45Z",
        )
        handle.create_dataset(
            f"{BASE}/identification/secondaryZeroDopplerStartTime",
            data=f"{secondary_date}T03:12:45Z",
        )
    return path


def build_stack(
    directory: pathlib.Path,
    *,
    day_offsets: list[int] | None = None,
    skip_pairs: tuple[int, ...] = (),
    track: int = 1,
    frame: int = 5,
    direction: str = "A",
    polarisations: tuple[str, ...] | None = None,
    incidence: float | np.ndarray | None = INCIDENCE_DEG,
    heading: float | None = HEADING_DEG,
    wavelength: float | None = WAVELENGTH_M,
    looks: int | None = 20,
    mask_patch: bool = True,
    bad_coherence: bool = True,
    omit: frozenset[str] = frozenset(),
    omit_in: int = 0,
    center_lonlat: tuple[float, float] | None = None,
) -> Stack:
    """Build a chained stack of synthetic GUNW pairs with a known deformation."""
    directory.mkdir(parents=True, exist_ok=True)
    if day_offsets is None:
        day_offsets = [STEP_DAYS * index for index in range(4)]  # 3 pairs, 4 epochs
    start = dt.date.fromisoformat(START)
    dates = [(start + dt.timedelta(days=offset)).isoformat() for offset in day_offsets]
    pairs = [
        (dates[index], dates[index + 1])
        for index in range(len(dates) - 1)
        if index not in skip_pairs
    ]
    x, y = grid_coordinates(center_lonlat)
    velocity = velocity_field()
    t_years = {date: (dt.date.fromisoformat(date) - start).days / V.DAYS_PER_YEAR for date in dates}
    vertical = {date: OFFSET_MM + velocity * t_years[date] for date in dates}

    mask = np.zeros((NY, NX), dtype=np.int8)
    coherence = np.full((NY, NX), 0.80, dtype=float)
    if mask_patch:
        iy0, iy1, ix0, ix1 = MASK_PATCH
        mask[iy0:iy1, ix0:ix1] = 127
        coherence[iy0:iy1, ix0:ix1] = 0.70
    if bad_coherence:
        coherence[BAD_COHERENCE_PIXEL] = 0.20  # below coherence_min: must be dropped

    _, inverse = _transformers()
    lon2d, lat2d = inverse.transform(*np.meshgrid(x, y))
    cells = np.array(
        [
            h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION)
            for lat, lon in zip(lat2d.ravel(), lon2d.ravel(), strict=True)
        ],
        dtype=object,
    ).reshape(NY, NX)
    valid = (mask != G.MASK_PADDING) & (coherence >= COHERENCE_MIN)

    granules: list[pathlib.Path] = []
    for index, (reference_date, secondary_date) in enumerate(pairs):
        increment = vertical[secondary_date] - vertical[reference_date]
        los_mm = V.project_to_los(increment, INCIDENCE_DEG, HEADING_DEG, v_east=0.0, v_north=0.0)
        # Invert d_LOS = -lambda/(4*pi) * phi  ->  phi = -4*pi*d / lambda (d in metres).
        phase = -4.0 * np.pi * (los_mm / 1000.0) / WAVELENGTH_M
        polarisation = (
            polarisations[index] if polarisations is not None else "HH"
        )
        name = (
            f"NISAR_L2_PR_GUNW_{track:03d}_{frame:03d}_{direction}_{index + 1:03d}_002_4000_SH_"
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
                polarisation=polarisation,
                incidence=incidence,
                heading=heading,
                wavelength=wavelength,
                looks=looks,
                omit=omit if index == omit_in else frozenset(),
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


def chain(stack: Stack, tmp_path: pathlib.Path, *, out: pathlib.Path | None = None, **kwargs):
    """Run `process_case` + `write_artefacts`; return (processed, out_dir, sizes, on-disk frames)."""
    out = out or (tmp_path / "processed" / CASE)
    processed = P.process_case(stack.granules, CASE, out_dir=out, **kwargs)
    sizes = P.write_artefacts(processed, out)
    grid = pd.read_parquet(out / "nisar_grid.parquet")
    series = pd.read_csv(out / "timeseries.csv")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    context = json.loads((out / "context_layers.geojson").read_text(encoding="utf-8"))
    return processed, out, sizes, (grid, series, meta, context)


def cli_argv(stack: Stack, out: pathlib.Path, *extra: str) -> list[str]:
    argv = ["--case", CASE, "--out", str(out)]
    for granule in stack.granules:
        argv += ["--granule", str(granule)]
    return argv + list(extra)


# ---------------------------------------------------------------------------------------------
# 1. The four artefacts: contract + file-level gate + real provenance
# ---------------------------------------------------------------------------------------------


def test_full_chain_emits_the_four_frozen_artefacts(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed, out, sizes, (grid, series, meta, context) = chain(stack, tmp_path)

    assert set(sizes) == set(gate.ARTEFACTS)
    for name in gate.ARTEFACTS:
        assert (out / name).is_file()
    assert sorted(path.name for path in out.iterdir() if not path.name.startswith(".")) == sorted(
        gate.ARTEFACTS
    )

    report = C.validate_all(
        grid=grid, timeseries=series, meta=meta, context=context, case_id=CASE
    )
    assert report == {name: [] for name in report}, report

    gate_report = gate.validate_tree(out)
    assert not gate_report.failed, gate.render_human(gate_report)
    assert gate_report.cases_validated == 1

    # Product identity: real data, never `mock`.
    assert meta["product"] == "nisar-gunw"
    assert meta["sensor"] == "nisar"
    assert set(grid["product"]) == {"nisar-gunw"}
    assert set(series["product"]) == {"nisar-gunw"}
    assert meta["revisit_days"] == C.SENSOR_PROFILES["nisar-gunw"].revisit_days
    assert meta["thresholds"]["coherence_min"] == C.SENSOR_PROFILES["nisar-gunw"].coherence_min

    provenance = meta["provenance"]
    for key in C.PROVENANCE_REQUIRED_KEYS:
        assert provenance.get(key), f"provenance.{key} is empty"
    assert "GUNW" in provenance["source"]
    for granule in stack.granules:
        assert granule.stem in provenance["product_id"]
    assert "v_north = 0" in provenance["method"]
    assert "h3_aggregate" in provenance["method"]
    assert provenance["retrieved_utc"].endswith("Z")
    assert "nisar" in provenance["source"].lower()

    # Limitations must carry the honesty statements, not just the config's.
    joined = " ".join(meta["limitations"])
    assert "v_north" in joined
    assert "ionospheric" in joined
    # The AOI is huge compared with a 1.6 km fixture, so the coverage caveat must be present.
    assert "data-source-decision" in joined or "50%" in joined


def test_provenance_id_resolves_across_all_artefacts(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed, out, _, (grid, series, meta, context) = chain(
        stack,
        tmp_path,
        provenance_overrides={
            "retrieved_utc": "2026-11-14T00:00:00Z",
            "processing_date": "2026-11-14",
        },
    )
    assert meta["provenance"]["retrieved_utc"] == "2026-11-14T00:00:00Z"
    assert meta["provenance"]["processing_date"] == "2026-11-14"
    provenance_id = meta["provenance"]["provenance_id"]
    assert C.PROVENANCE_ID_RE.match(provenance_id)
    # The date component is the **last acquisition date in the stack**, derived from the granule
    # names, so reruns are byte-identical across UTC midnight. It is not the wall clock and not
    # the overridden processing_date above (2026-11-14), which must not leak into the id.
    latest = max(dt.date.fromisoformat(day) for day in stack.dates)
    assert latest.isoformat() == meta["dates"][-1]
    case_part, product_part, date_part, digest_part = provenance_id.split("+")
    assert (case_part, product_part, date_part) == (CASE, "nisar-gunw", f"{latest:%Y%m%d}")
    assert len(digest_part) == 8
    assert date_part != "20261114", "the overridden processing_date leaked into the id"
    # Pure function of the input stack: no clock, no overrides, no artefact path enters it.
    assert P._provenance_id(CASE, stack.granules) == provenance_id
    assert set(grid["provenance_id"]) == {provenance_id}
    assert set(grid["acquisition_date"]) == {latest.isoformat()}
    assert set(series["provenance_id"]) == {provenance_id}
    assert {feature["properties"]["provenance_id"] for feature in context["features"]} == {
        provenance_id
    }


# ---------------------------------------------------------------------------------------------
# 2. The physics: trend recovery, spatial reference, delta arithmetic
# ---------------------------------------------------------------------------------------------


def test_recovered_velocity_matches_the_injected_trend(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed, _, _, (grid, _, _, _) = chain(stack, tmp_path)
    expected = stack.expected_velocity()

    assert set(grid["cell_id"]) == set(expected)
    residuals = [
        abs(float(row.velocity_mm_yr) - expected[row.cell_id])
        for row in grid.itertuples()
    ]
    assert max(residuals) <= 1.0, (
        f"worst |velocity - injected| = {max(residuals):.4f} mm/yr over {len(residuals)} cells"
    )

    # The reference pixel's own cell is the least-subsiding one: the map is a spatial difference,
    # so the bowl is anchored at the reference point rather than at an arbitrary absolute level.
    reference_cell = str(stack.cells[REF_IY, REF_IX])
    reference_velocity = float(
        grid.loc[grid["cell_id"] == reference_cell, "velocity_mm_yr"].iloc[0]
    )
    assert reference_velocity == max(grid["velocity_mm_yr"])
    assert reference_velocity < 0.0  # the cell mean still contains subsiding neighbour pixels


def test_series_is_referenced_to_the_first_epoch_and_delta_is_exact(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed, out, _, (grid, series, meta, _) = chain(stack, tmp_path)

    aoi = series.loc[series["cell_id"] == "aoi"].sort_values("date")
    assert list(aoi["date"]) == meta["dates"] == list(processed.epochs)
    assert float(aoi["value"].iloc[0]) == 0.0, "the AOI series must start at exactly 0 mm"
    assert np.isfinite(aoi["value"]).all()

    # Every published cell sees the same reference epoch.
    assert (grid["val_t1"] == 0.0).all()
    assert ((grid["delta_change"] - (grid["val_t2"] - grid["val_t1"])) == 0.0).all()
    assert (grid["delta_change"] < 0).all(), "the injected bowl subsides, so delta_change is negative"
    assert (grid["risk_level"] == [C.risk_level_from_velocity(v) for v in grid["velocity_mm_yr"]]).all()

    # The constant offset must have cancelled: a 25 mm offset per pixel leaves no trace.
    assert abs(float(grid["val_t2"].mean())) > 1.0
    assert abs(float(series.loc[series["cell_id"] == "aoi", "value"].iloc[0])) < 1e-12


# ---------------------------------------------------------------------------------------------
# 3. Nodata: absent from the parquet, never written as 0
# ---------------------------------------------------------------------------------------------


def test_nodata_cells_are_absent_and_never_zero(tmp_path):
    stack = build_stack(tmp_path / "granules")
    _, _, _, (grid, series, meta, _) = chain(stack, tmp_path)

    missing = stack.all_cells - stack.valid_cells
    assert missing, "fixture must contain at least one H3 cell with no valid pixel"
    published = set(grid["cell_id"])

    assert published == stack.valid_cells
    assert published <= P.aoi_cells(CASE), "cells outside the case AOI must never be published"
    assert not (missing & published), f"nodata cells leaked into the grid: {sorted(missing & published)}"
    assert not (missing & set(series["cell_id"]))

    # No sentinel zeros: every published velocity is a real number far from 0.
    assert np.isfinite(grid["velocity_mm_yr"]).all()
    assert (grid["velocity_mm_yr"].abs() > 1.0).all()
    assert (grid["coherence"] > 0).all()
    assert np.isfinite(grid[["lat", "lon", "val_t1", "val_t2", "delta_change", "coherence"]]).all().all()
    assert "never written as 0" in meta["null_policy"]


def test_h3_cells_are_valid_and_at_the_configured_resolution(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed, _, _, (grid, series, _, _) = chain(stack, tmp_path)
    resolution = P.load_case_config(CASE).h3_resolution
    for cell in grid["cell_id"]:
        assert h3.is_valid_cell(cell)
        assert h3.get_resolution(cell) == resolution
    assert grid["cell_id"].is_unique
    assert (series["cell_id"] == "aoi").all()


# ---------------------------------------------------------------------------------------------
# 4. Refusals
# ---------------------------------------------------------------------------------------------


def test_mixed_tracks_are_refused(tmp_path):
    stack = build_stack(tmp_path / "granules")
    moved = stack.granules[1]
    renamed = moved.with_name(moved.name.replace("GUNW_001_005_A", "GUNW_002_005_A", 1))
    moved.rename(renamed)
    stack.granules[1] = renamed

    with pytest.raises(P.TrackMismatchError) as excinfo:
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    message = str(excinfo.value)
    assert "relative orbits" in message and "track" in message
    assert renamed.name in message


def test_mixed_flight_directions_are_refused(tmp_path):
    stack = build_stack(tmp_path / "granules")
    moved = stack.granules[2]
    renamed = moved.with_name(moved.name.replace("GUNW_001_005_A", "GUNW_001_005_D", 1))
    moved.rename(renamed)
    stack.granules[2] = renamed

    with pytest.raises(P.TrackMismatchError, match="flight directions"):
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")


def test_polarisation_change_mid_stack_is_refused(tmp_path):
    stack = build_stack(tmp_path / "granules", polarisations=("HH", "HH", "VV"))
    with pytest.raises(P.PolarisationMismatchError) as excinfo:
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    message = str(excinfo.value)
    assert "polarisation changes mid-stack" in message
    assert "HH" in message and "VV" in message


def test_missing_required_layer_is_refused_with_the_path(tmp_path):
    stack = build_stack(tmp_path / "granules", omit=frozenset({"phase"}), omit_in=1)
    with pytest.raises(P.MissingLayerError) as excinfo:
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    message = str(excinfo.value)
    assert "missing required layer" in message
    assert "unwrappedPhase" in message
    assert "inspect_hdf5.py" in message


def test_single_granule_warns_then_refuses_to_invent_a_trend(tmp_path, capsys):
    stack = build_stack(tmp_path / "granules", day_offsets=[0, STEP_DAYS])
    assert len(stack.granules) == 1

    with pytest.raises(P.ProcessCaseError) as excinfo:
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    assert "at least 4" in str(excinfo.value)
    assert any("single granule" in warning for warning in excinfo.value.warnings)

    code = P.main(cli_argv(stack, tmp_path / "cli-out"))
    stderr = capsys.readouterr().err
    assert code == 2
    assert "single granule" in stderr
    assert "at least 4" in stderr
    assert not (tmp_path / "cli-out").exists() or not any((tmp_path / "cli-out").iterdir())


def test_mock_tree_needs_force(tmp_path, capsys):
    stack = build_stack(tmp_path / "granules")
    out = tmp_path / "data" / "processed" / CASE
    out.mkdir(parents=True)
    (out / "meta.json").write_text(
        json.dumps({"product": "mock", "case_id": CASE, "sensor": "nisar"}), encoding="utf-8"
    )

    with pytest.raises(P.MockOverwriteError, match="mock"):
        P.process_case(stack.granules, CASE, out_dir=out)

    assert P.main(cli_argv(stack, out)) == 2
    assert "mock" in capsys.readouterr().err

    processed = P.process_case(stack.granules, CASE, out_dir=out, force=True)
    P.write_artefacts(processed, out)
    assert json.loads((out / "meta.json").read_text(encoding="utf-8"))["product"] == "nisar-gunw"

    assert P.main(cli_argv(stack, out, "--force")) == 0


def test_disconnected_network_is_refused(tmp_path):
    # Pairs (0,12) and (24,48): the second pair's reference date is not in the chain.
    stack = build_stack(
        tmp_path / "granules", day_offsets=[0, 12, 24, 48], skip_pairs=(1,)
    )
    with pytest.raises(P.ProcessCaseError, match="not a connected network"):
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")


# ---------------------------------------------------------------------------------------------
# 5. Geometry: metadata first, flags second, refusal third
# ---------------------------------------------------------------------------------------------


def test_missing_geometry_fails_closed_and_flags_rescue_it(tmp_path):
    stack = build_stack(tmp_path / "granules", incidence=None, heading=None)

    with pytest.raises(P.GeometryRequiredError) as excinfo:
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    message = str(excinfo.value)
    assert "incidence angle missing" in message
    assert "--incidence" in message
    assert "never invented" in message

    processed = P.process_case(
        stack.granules,
        CASE,
        out_dir=tmp_path / "out",
        incidence_deg=INCIDENCE_DEG,
        heading_deg=HEADING_DEG,
    )
    assert processed.n_cells > 0
    assert "CLI --incidence" in processed.meta["provenance"]["geometry"]


def test_missing_heading_only_names_the_heading_flag(tmp_path):
    stack = build_stack(tmp_path / "granules", heading=None)
    with pytest.raises(P.GeometryRequiredError) as excinfo:
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    message = str(excinfo.value)
    assert "heading angle missing" in message
    assert "--heading" in message
    assert "--incidence" not in message


def test_per_pixel_incidence_grid_is_refused_not_averaged(tmp_path):
    stack = build_stack(
        tmp_path / "granules", incidence=np.full((NY, NX), INCIDENCE_DEG, dtype=float)
    )
    with pytest.raises(P.GeometryRequiredError) as excinfo:
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    message = str(excinfo.value)
    assert "multi-pixel grid" in message
    assert "--incidence" in message


def test_geometry_metadata_beats_a_conflicting_flag(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed = P.process_case(
        stack.granules,
        CASE,
        out_dir=tmp_path / "out",
        incidence_deg=INCIDENCE_DEG + 3.0,
        heading_deg=HEADING_DEG + 3.0,
    )
    assert any("the granule metadata wins" in warning for warning in processed.warnings)


# ---------------------------------------------------------------------------------------------
# 6. Coverage diagnostic and gap warnings
# ---------------------------------------------------------------------------------------------


def test_coverage_warning_fires_below_half_the_aoi(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed, _, _, (_, _, meta, _) = chain(stack, tmp_path)

    assert processed.coverage.aoi_cells > 10_000  # the CDMX AOI at res 9
    assert processed.coverage.fraction < 0.5
    warning = next(
        (w for w in processed.warnings if w.startswith("COVERAGE")), None
    )
    assert warning is not None, processed.warnings
    assert "data-source-decision" in warning
    assert meta["stats"]["coverage_fraction"] == round(processed.coverage.fraction, 6)
    assert meta["stats"]["covered_aoi_cells"] == processed.coverage.covered_in_aoi
    assert any("do not claim city-wide displacement" in line.lower() for line in meta["limitations"])


def test_granules_outside_the_case_aoi_are_refused(tmp_path):
    # The fixture sits over Mexico City; processing it as Jakarta must not publish its footprint.
    stack = build_stack(tmp_path / "granules")
    with pytest.raises(P.ProcessCaseError, match="does not intersect"):
        P.process_case(stack.granules, "jakarta", out_dir=tmp_path / "out")


def test_cells_outside_the_aoi_are_dropped_not_published(tmp_path):
    cfg = P.load_case_config(CASE)
    # Straddle the *effective* AOI's east boundary at the reference latitude. This used to be a
    # hard-coded +0.28° shift, which targeted the retired YAML rectangle's east edge (-98.85);
    # once `config/aoi/mexico-city.geojson` became the authoritative footprint its east edge moved
    # ~29 km west, so the whole fixture fell outside the AOI. Deriving the edge from the ring keeps
    # the fixture honest when the study area is refined.
    shifted = (east_aoi_edge_lon(cfg, cfg.reference_lonlat[1]), cfg.reference_lonlat[1])
    stack = build_stack(tmp_path / "granules", center_lonlat=shifted)
    out = tmp_path / "out"
    processed = P.process_case(stack.granules, CASE, out_dir=out)
    P.write_artefacts(processed, out)
    grid = pd.read_parquet(out / "nisar_grid.parquet")

    assert processed.coverage.covered_outside_aoi > 0, "fixture must straddle the AOI boundary"
    assert processed.coverage.covered_in_aoi > 0, "fixture must also have cells inside the AOI"
    assert set(grid["cell_id"]) <= P.aoi_cells(CASE)
    assert processed.coverage.covered_in_aoi == len(grid)
    assert any("outside the mexico-city AOI" in warning for warning in processed.warnings)


def test_acquisition_gap_is_a_warning_with_the_affected_dates(tmp_path):
    stack = build_stack(tmp_path / "granules", day_offsets=[0, 12, 24, 48])
    processed = P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")

    gap = next((w for w in processed.warnings if "acquisition gap" in w), None)
    assert gap is not None, processed.warnings
    assert "2026-07-26 → 2026-08-19" in gap
    assert "24 days" in gap
    assert processed.epochs[-1] == "2026-08-19"  # 2026-07-02 + 48 days


# ---------------------------------------------------------------------------------------------
# 7. Wavelength, land use, CLI
# ---------------------------------------------------------------------------------------------


def test_missing_wavelength_uses_the_documented_constant_with_a_warning(tmp_path):
    stack = build_stack(tmp_path / "granules", wavelength=None)
    processed, _, _, (grid, _, meta, _) = chain(stack, tmp_path)

    assert any("documented L-band constant" in warning for warning in processed.warnings)
    assert "0.2384" in meta["provenance"]["wavelength"]
    assert "0.2384" in meta["provenance"]["method"]
    expected = stack.expected_velocity()
    residuals = [abs(float(row.velocity_mm_yr) - expected[row.cell_id]) for row in grid.itertuples()]
    assert max(residuals) <= 1.0


def test_land_use_sidecar_join_and_unknown_default(tmp_path):
    stack = build_stack(tmp_path / "granules")
    cells = sorted(stack.expected_velocity())
    sidecar = tmp_path / "land_use.csv"
    pd.DataFrame(
        {
            "cell_id": [cells[0], cells[1], "8928308280fffff"],
            "land_use": ["urban-dense", "water", "bare"],
        }
    ).to_csv(sidecar, index=False)

    processed, _, _, (grid, _, _, context) = chain(stack, tmp_path, land_use_csv=sidecar)
    labels = dict(zip(grid["cell_id"], grid["land_use"], strict=True))
    assert labels[cells[0]] == "urban-dense"
    assert labels[cells[1]] == "water"
    assert set(labels.values()) <= {"urban-dense", "water", "unknown"}
    assert any("not cells of this grid" in warning for warning in processed.warnings)
    assert any("published as 'unknown'" in warning for warning in processed.warnings)
    assert (grid["risk_level"] == [C.risk_level_from_velocity(v) for v in grid["velocity_mm_yr"]]).all()

    layers = {feature["properties"]["layer"] for feature in context["features"]}
    assert {"admin_boundary", "land_use"} <= layers
    grid_classes = set(grid["land_use"])
    context_classes = {
        feature["properties"]["class"]
        for feature in context["features"]
        if feature["properties"]["layer"] == "land_use"
    }
    assert context_classes == grid_classes


def test_undeclared_land_use_label_is_refused(tmp_path):
    stack = build_stack(tmp_path / "granules")
    sidecar = tmp_path / "land_use.csv"
    pd.DataFrame({"cell_id": sorted(stack.expected_velocity())[:1], "land_use": ["car-park"]}).to_csv(
        sidecar, index=False
    )
    with pytest.raises(P.LandUseError, match="undeclared land_use"):
        P.process_case(stack.granules, CASE, out_dir=tmp_path / "out", land_use_csv=sidecar)


def test_cli_writes_the_tree_and_reports_the_gate(tmp_path, capsys):
    stack = build_stack(tmp_path / "granules")
    out = tmp_path / "processed" / CASE

    code = P.main(cli_argv(stack, out, "--json"))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    payload = json.loads(captured.out)

    assert payload["product"] == "nisar-gunw"
    assert payload["gate"]["status"] == "pass"
    assert payload["gate"]["cases_validated"] == 1
    assert payload["coverage"]["warning"] is True
    assert payload["provenance"]["retrieved_utc"].endswith("Z")
    assert payload["contract"] == {name: [] for name in payload["contract"]}
    assert set(payload["artefacts"]) == set(gate.ARTEFACTS)

    gate_report = gate.validate_tree(out)
    assert not gate_report.failed, gate.render_human(gate_report)


def test_cli_rejects_an_unknown_case(tmp_path, capsys):
    stack = build_stack(tmp_path / "granules")
    argv = ["--case", "atlantis", "--out", str(tmp_path / "out")]
    for granule in stack.granules:
        argv += ["--granule", str(granule)]
    assert P.main(argv) == 2
    assert "unknown case_id" in capsys.readouterr().err


def test_write_artefacts_refuses_a_mock_payload(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed = P.process_case(stack.granules, CASE, out_dir=tmp_path / "out")
    mocked = dict(processed.meta)
    mocked["product"] = "mock"
    with pytest.raises(P.ProcessCaseError, match="only produces"):
        P.write_artefacts(processed, tmp_path / "out", mocked)


def test_reordered_granules_produce_the_same_result(tmp_path):
    """Input order must not matter: the chain sorts by acquisition date."""
    stack = build_stack(tmp_path / "granules")
    shuffled = list(reversed(stack.granules))
    forward, _, _, (grid_a, series_a, meta_a, _) = chain(stack, tmp_path, out=tmp_path / "a")
    backward = P.process_case(shuffled, CASE, out_dir=tmp_path / "b")
    P.write_artefacts(backward, tmp_path / "b")
    grid_b = pd.read_parquet(tmp_path / "b" / "nisar_grid.parquet")
    series_b = pd.read_csv(tmp_path / "b" / "timeseries.csv")
    meta_b = json.loads((tmp_path / "b" / "meta.json").read_text(encoding="utf-8"))

    pd.testing.assert_frame_equal(grid_a, grid_b)
    pd.testing.assert_frame_equal(series_a, series_b)
    assert meta_a["dates"] == meta_b["dates"] == list(forward.epochs)


def test_duplicate_granule_is_reported_and_deduplicated(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed = P.process_case(
        [*stack.granules, stack.granules[0]], CASE, out_dir=tmp_path / "out"
    )
    assert any("duplicate pair" in warning for warning in processed.warnings)
    assert len(processed.epochs) == 4
