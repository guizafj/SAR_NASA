"""The LOS sign / heading convention flags of `pipeline/process_case.py`.

`docs/first-granule-runbook.md` §7 gates publication on a sentence: *"sign convention of
docs/nisar-gunw-layout.md §4, NOT yet validated against GNSS"* must be **gone** from
`meta.provenance.method` before any number is shown — and until these flags existed there was no way
to record a validated convention from the CLI. This file tests the four flags that close that gap.

Two things are asserted, and the first matters more:

1. **Arithmetic, not prose.** The flags change numbers, so the fixtures below make the change
   predictable:

   * `--los-sign` flips `d_LOS = sign*lambda/(4*pi)*phi`, hence every vertical value. The fixture is
     a pure-vertical field (`v_east = v_north = 0`), where the flip is exact and nothing else can
     explain it. The **default** label is `toward-sensor` with factor **-1.0** (O-08: the label was
     renamed to match `project_to_los`, whose `u_up = cos(theta) > 0` makes uplift positive LOS; the
     arithmetic and therefore every default number are unchanged). `away-from-sensor` is the +1.0
     flip.
   * `--heading-convention look` feeds `alpha = 180 - alpha_platform` to `los_to_vertical`. That
     changes `u_east = -sin(theta)*cos(alpha)` and leaves `u_north`, and `u_up = cos(theta)`, alone.
     So it moves the east term by `-2*tan(theta)*cos(alpha)*v_east` and does **nothing at all** when
     the motion is purely vertical, or purely north (`sin(180 - alpha) == sin(alpha)`). That
     asymmetry *is* the physics of a single geometry, so it is asserted deliberately rather than
     sidestepped.
2. **The record.** With any of the four flags the hedge is gone and the convention plus what it was
   validated against is stated in `meta.provenance.method`, and
   `meta.provenance.geometry_convention` carries `los_sign`, `heading_convention` and
   `validated_against` as data. With no flags the text is exactly what it was before.

Fixtures are synthetic granules in the documented GUNW layout (`docs/nisar-gunw-layout.md` §2–§3),
written to `tmp_path`; the builder is deliberately local to this file (the same discipline as
`tests/test_pipeline_integration.py`) so it can carry a **horizontal** velocity field, which the
inherited fixture does not. Nothing is downloaded and no network is touched.
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
import pathlib
import sys
from collections import defaultdict

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
from pipeline.ingest import nisar_gunw as G  # noqa: E402
from pipeline.science import transforms as V  # noqa: E402
from src.domain import contracts as C  # noqa: E402

CASE = "mexico-city"
BASE = "/science/LSAR/GUNW"
GRIDS = f"{BASE}/grids/frequencyA"
UNWRAPPED = f"{GRIDS}/unwrappedInterferogram"
RADAR = f"{BASE}/metadata/radarGrid"

INCIDENCE_DEG = 39.0
HEADING_DEG = 192.0  # platform heading of the synthetic acquisitions
WAVELENGTH_M = 0.2384  # docs/nisar-gunw-layout.md §4
EPSG = 32614  # Mexico City, UTM 14N (config crs_analysis)

NX, NY = 20, 20
SPACING_M = 80.0
REF_IX, REF_IY = NX // 2, NY // 2
PEAK_MM_YR = -180.0  # subsidence bowl: negative = down, 0 at the reference pixel
EAST_PEAK_MM_YR = 20.0
FALLOFF_PX = 4.0
START = "2026-07-02"
STEP_DAYS = 12

H3_RESOLUTION = P.load_case_config(CASE).h3_resolution

#: The hedge the runbook's Step 6 gate is about. It must survive a flag-less run and vanish otherwise.
HEDGE = "NOT yet validated against GNSS"

#: `u_east = -sin(theta)*cos(alpha)` with `alpha = 180 - alpha_platform` becomes `+sin(theta)*cos`
#: `(alpha_platform)`, so a vertical inversion under the wrong reading is off by this factor on
#: v_east: v_up(look) - v_up(platform) = -2*tan(theta)*cos(alpha)*v_east.
def _east_leak_factor() -> float:
    theta = np.radians(INCIDENCE_DEG)
    alpha = np.radians(HEADING_DEG)
    return float(-2.0 * np.tan(theta) * np.cos(alpha))


# ---------------------------------------------------------------------------------------------
# Fixture: chained GUNW pairs with a *known* 3-vector velocity field
# ---------------------------------------------------------------------------------------------


def _transformers() -> tuple[Transformer, Transformer]:
    return (
        Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True),
        Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True),
    )


def _distance_from_reference() -> np.ndarray:
    columns = np.arange(NX)[None, :]
    rows = np.arange(NY)[:, None]
    return np.hypot(columns - REF_IX, rows - REF_IY)


def field(peak: float) -> np.ndarray:
    """Radial bowl in mm/yr that is exactly 0 at the reference pixel (so referencing cancels 0)."""
    return peak * (1.0 - np.exp(-_distance_from_reference() / FALLOFF_PX))


def grid_coordinates() -> tuple[np.ndarray, np.ndarray]:
    cfg = P.load_case_config(CASE)
    forward, _ = _transformers()
    x0, y0 = forward.transform(*cfg.reference_lonlat)
    x = x0 + (np.arange(NX) - REF_IX) * SPACING_M
    y = y0 + (np.arange(NY) - REF_IY) * SPACING_M
    return x, y


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
    polarisation: str = "HH",
) -> pathlib.Path:
    with h5py.File(path, "w") as handle:
        group = handle.require_group(f"{UNWRAPPED}/{polarisation}")
        group.create_dataset("unwrappedPhase", data=phase.astype(np.float32))
        group.create_dataset("coherenceMagnitude", data=coherence.astype(np.float32))
        group.create_dataset("connectedComponents", data=np.ones(phase.shape, dtype=np.uint32))
        handle.create_dataset(f"{UNWRAPPED}/mask", data=mask.astype(np.int8))
        handle.create_dataset(f"{GRIDS}/xCoordinates", data=x.astype(np.float64))
        handle.create_dataset(f"{GRIDS}/yCoordinates", data=y.astype(np.float64))
        handle.create_dataset(f"{GRIDS}/projection", data=np.int32(EPSG))
        handle.create_dataset(f"{RADAR}/wavelength", data=np.float64(WAVELENGTH_M))
        handle.create_dataset(f"{RADAR}/numberOfLooks", data=np.int32(20))
        handle.create_dataset(f"{RADAR}/incidenceAngle", data=np.float64(INCIDENCE_DEG))
        handle.create_dataset(f"{RADAR}/heading", data=np.float64(HEADING_DEG))
        handle.create_dataset(
            f"{BASE}/identification/referenceZeroDopplerStartTime", data=f"{reference_date}T03:12:45Z"
        )
        handle.create_dataset(
            f"{BASE}/identification/secondaryZeroDopplerStartTime",
            data=f"{secondary_date}T03:12:45Z",
        )
    return path


class Stack:
    """Synthetic stack + the injected 3-vector velocity field it was built from."""

    def __init__(
        self,
        granules: list[pathlib.Path],
        dates: list[str],
        up: np.ndarray,
        east: np.ndarray,
        north: np.ndarray,
        cells: np.ndarray,
    ) -> None:
        self.granules = granules
        self.dates = dates
        self.up = up
        self.east = east
        self.north = north
        self.cells = cells

    def los_rate_mm_yr(self) -> np.ndarray:
        """`d_LOS` per year that the granule phase encodes (default `-lambda/4pi` sign)."""
        return V.project_to_los(
            self.up, INCIDENCE_DEG, HEADING_DEG, v_east=self.east, v_north=self.north
        )

    def cell_means(self, field_mm_yr: np.ndarray) -> dict[str, float]:
        buckets: dict[str, list[float]] = defaultdict(list)
        for value, cell in zip(field_mm_yr.ravel(), self.cells.ravel(), strict=True):
            buckets[str(cell)].append(float(value))
        return {cell: float(np.mean(values)) for cell, values in buckets.items()}

    def expected_vertical(
        self, azimuth_deg: float, *, v_east: object = 0.0, v_north: object = 0.0
    ) -> dict[str, float]:
        """Cell velocity the chain must recover if `los_to_vertical` is fed `azimuth_deg`.

        The granule phase holds `d_LOS = u(alpha_platform) . (v_up, v_east, v_north)`; inverting it
        with another azimuth is a linear operation, so the truth is recovered by running the same
        frozen formula with that azimuth plus the trusted horizontals the chain was given, and
        referencing to the reference pixel (0 by construction).
        """
        per_pixel = V.los_to_vertical(
            self.los_rate_mm_yr(),
            INCIDENCE_DEG,
            azimuth_deg,
            v_east=v_east,
            v_north=v_north,
        )
        reference = float(per_pixel[REF_IY, REF_IX])
        return {cell: value - reference for cell, value in self.cell_means(per_pixel).items()}


def build_stack(
    directory: pathlib.Path,
    *,
    up_peak: float = PEAK_MM_YR,
    east_peak: float = 0.0,
    north_peak: float = 0.0,
    day_offsets: list[int] | None = None,
) -> Stack:
    """Chain 3 pairs / 4 epochs. `up_peak`/`east_peak`/`north_peak` add a velocity field that is
    also exactly 0 at the reference pixel, so the spatial reference stays interpretable. `up_peak`
    is positive for uplift, negative (the module default) for subsidence."""
    directory.mkdir(parents=True, exist_ok=True)
    if day_offsets is None:
        day_offsets = [STEP_DAYS * index for index in range(4)]
    start = dt.date.fromisoformat(START)
    dates = [(start + dt.timedelta(days=offset)).isoformat() for offset in day_offsets]

    up, east, north = field(up_peak), field(east_peak), field(north_peak)
    t_years = {
        date: (dt.date.fromisoformat(date) - start).days / V.DAYS_PER_YEAR for date in dates
    }

    x, y = grid_coordinates()
    _, inverse = _transformers()
    lon2d, lat2d = inverse.transform(*np.meshgrid(x, y))
    cells = np.array(
        [
            h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION)
            for lat, lon in zip(lat2d.ravel(), lon2d.ravel(), strict=True)
        ],
        dtype=object,
    ).reshape(NY, NX)

    coherence = np.full((NY, NX), 0.80, dtype=float)
    mask = np.zeros((NY, NX), dtype=np.int8)  # every pixel valid: expectations stay exact

    granules: list[pathlib.Path] = []
    for index in range(len(dates) - 1):
        reference_date, secondary_date = dates[index], dates[index + 1]
        span = t_years[secondary_date] - t_years[reference_date]
        los_mm = V.project_to_los(
            up * span,
            INCIDENCE_DEG,
            HEADING_DEG,
            v_east=east * span,
            v_north=north * span,
        )
        # d_LOS = -lambda/(4*pi)*phi  ->  phi = -4*pi*d/lambda (d in metres), the default sign.
        phase = -4.0 * np.pi * (los_mm / 1000.0) / WAVELENGTH_M
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
    return Stack(granules, dates, up, east, north, cells)


def run(stack: Stack, tmp_path: pathlib.Path, **kwargs) -> P.ProcessedCase:
    """Run the chain in memory (nothing is written, so one out dir can serve several runs)."""
    return P.process_case(stack.granules, CASE, out_dir=tmp_path / "out", **kwargs)


def cli_argv(stack: Stack, out: pathlib.Path, *extra: str) -> list[str]:
    argv = ["--case", CASE, "--out", str(out)]
    for granule in stack.granules:
        argv += ["--granule", str(granule)]
    return argv + list(extra)


def velocities(processed: P.ProcessedCase) -> dict[str, float]:
    return {
        str(row.cell_id): float(row.velocity_mm_yr) for row in processed.grid.itertuples()
    }


# ---------------------------------------------------------------------------------------------
# 1. No flags: today's text and today's arithmetic, with the defaults recorded as data
# ---------------------------------------------------------------------------------------------


def test_no_flags_keep_the_hedge_and_record_the_defaults(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed = run(stack, tmp_path, provenance_overrides={"retrieved_utc": "2026-11-14T00:00:00Z"})
    provenance = processed.meta["provenance"]

    # The gate sentence is present while nothing has been recorded, exactly as before these flags.
    assert HEDGE in provenance["method"]
    assert "sign convention of docs/nisar-gunw-layout.md §4" in provenance["method"]
    assert "Convention validated against:" not in provenance["method"]

    # ... and the choice is still machine-readable, defaults included.
    convention = provenance["geometry_convention"]
    assert convention["los_sign"] == "toward-sensor"  # O-08: the label now matches u_up = cos(theta)
    assert convention["los_sign_factor"] == -1.0
    assert convention["los_sign_formula"] == "d_LOS = -lambda/(4*pi)*phi"
    assert "uplift" in convention["los_sign_meaning"]
    assert convention["heading_convention"] == "platform"
    assert convention["validated_against"] is None
    assert convention["heading_platform_deg"] == [HEADING_DEG]
    assert convention["heading_look_azimuth_deg"] == [348.0]  # (180 - 192) % 360
    assert convention["recorded_from_flags"] is False
    assert convention["method_notes"] == []

    # The default sign this module advertises must be the reader's actual default: if
    # `phase_to_los_displacement_mm` ever changes its default, this fails instead of the numbers
    # silently meaning something else.
    default_sign = inspect.signature(G.phase_to_los_displacement_mm).parameters["sign"].default
    assert P.LOS_SIGN_FACTORS[P.DEFAULT_LOS_SIGN] == default_sign == -1.0
    phase = np.array([0.0, 1.0, -2.5])
    np.testing.assert_array_equal(
        G.phase_to_los_displacement_mm(phase, WAVELENGTH_M),
        -1.0 * G.phase_to_los_displacement_mm(phase, WAVELENGTH_M, sign=+1.0),
    )


# ---------------------------------------------------------------------------------------------
# 2. --los-sign: reaches the reader, and flips every vertical value
# ---------------------------------------------------------------------------------------------


def test_los_sign_reaches_phase_conversion_and_flips_the_vertical(tmp_path, monkeypatch):
    stack = build_stack(tmp_path / "granules")  # pure vertical: v_east = v_north = 0
    seen: list[float] = []
    original = G.phase_to_los_displacement_mm

    def spy(phase_rad, wavelength_m, sign=-1.0):
        seen.append(float(sign))
        return original(phase_rad, wavelength_m, sign=sign)

    monkeypatch.setattr(P.G, "phase_to_los_displacement_mm", spy)

    away = run(stack, tmp_path / "away")  # the historical arithmetic: default, factor -1.0
    toward = run(stack, tmp_path / "toward", los_sign="away-from-sensor")

    # Wiring: 3 granules each, with the conventional factor actually passed through.
    assert seen == [-1.0] * len(stack.granules) + [+1.0] * len(stack.granules)

    # Arithmetic: the whole map negates, not just its label. The default run (`away` here only
    # because it reproduces pre-O-08 numbers) is toward-sensor-positive: subsidence is negative.
    cells = sorted(velocities(away))
    np.testing.assert_allclose(
        [velocities(toward)[cell] for cell in cells],
        [-velocities(away)[cell] for cell in cells],
        rtol=0.0,
        atol=1e-6,
    )
    assert np.all(np.asarray([velocities(away)[cell] for cell in cells]) < 0.0)
    assert np.all(np.asarray([velocities(toward)[cell] for cell in cells]) > 0.0)
    # Subsidence sign flips in the published statistics too, and that is the whole point: the
    # runbook's Step 6 test is `median < 0`, so a wrong convention would silently PASS as uplift.
    assert away.meta["stats"]["median_velocity_mm_yr"] < 0.0
    assert toward.meta["stats"]["median_velocity_mm_yr"] > 0.0
    np.testing.assert_allclose(
        toward.meta["stats"]["median_velocity_mm_yr"],
        -away.meta["stats"]["median_velocity_mm_yr"],
        rtol=0.0,
        atol=1e-6,
    )

    # val_t1 / val_t2 / delta_change keep their contract identity and all flip sign.
    away_grid = away.grid.set_index("cell_id").loc[cells]
    toward_grid = toward.grid.set_index("cell_id").loc[cells]
    for column in ("val_t1", "val_t2", "delta_change"):
        np.testing.assert_allclose(
            toward_grid[column].to_numpy(), -away_grid[column].to_numpy(), rtol=0.0, atol=1e-6
        )
    np.testing.assert_allclose(
        toward_grid["delta_change"].to_numpy(),
        toward_grid["val_t2"].to_numpy() - toward_grid["val_t1"].to_numpy(),
        rtol=0.0,
        atol=1e-12,
    )
    np.testing.assert_allclose(toward_grid["val_t1"].to_numpy(), np.zeros(len(cells)), atol=1e-12)

    # Prose: the hedge is replaced by the convention, in words -- and the words match the sign.
    method = toward.meta["provenance"]["method"]
    assert HEDGE not in method
    assert "los_sign = away-from-sensor" in method
    assert "+lambda/(4*pi)*phi" in method
    assert "away from the sensor" in method
    assert "subsidence is positive" in method
    assert toward.meta["provenance"]["geometry_convention"]["los_sign_factor"] == 1.0
    # The default run states the same convention it applies, in machine-readable form: the label
    # says toward-sensor and the factor is still -1.0, so no number moved with the rename.
    default_convention = away.meta["provenance"]["geometry_convention"]
    assert default_convention["los_sign"] == "toward-sensor"
    assert default_convention["los_sign_factor"] == -1.0
    assert "toward the sensor" in default_convention["los_sign_meaning"]
    assert "uplift" in default_convention["los_sign_meaning"]


# ---------------------------------------------------------------------------------------------
# 3. --heading-convention look is a no-op without horizontal motion (and the record still moves)
# ---------------------------------------------------------------------------------------------


def test_heading_convention_look_is_a_no_op_for_pure_vertical_motion(tmp_path):
    stack = build_stack(tmp_path / "granules")  # v_east = v_north = 0

    platform = run(stack, tmp_path / "platform")
    look = run(stack, tmp_path / "look", heading_convention="look")

    # The physics: u_up = cos(theta) either way, so with v_east = v_north = 0 the inversions are
    # bit-identical. Asserting this is the point -- it is what makes the ambiguity invisible.
    assert platform.grid["velocity_mm_yr"].equals(look.grid["velocity_mm_yr"])
    assert platform.grid["delta_change"].equals(look.grid["delta_change"])
    assert platform.meta["stats"] == look.meta["stats"]

    # The record is an *input*, so it does move even when the numbers cannot: different convention,
    # different provenance id (documented in `_provenance_id`, not an accident).
    assert platform.provenance_id != look.provenance_id
    np.testing.assert_allclose(
        V.los_unit_vector(INCIDENCE_DEG, 180.0 - HEADING_DEG)[0],
        V.los_unit_vector(INCIDENCE_DEG, HEADING_DEG)[0],
        rtol=0.0,
        atol=1e-12,
    )  # u_up identical
    np.testing.assert_allclose(
        V.los_unit_vector(INCIDENCE_DEG, 180.0 - HEADING_DEG)[1],
        -V.los_unit_vector(INCIDENCE_DEG, HEADING_DEG)[1],
        rtol=0.0,
        atol=1e-12,
    )  # u_east reflected
    convention = look.meta["provenance"]["geometry_convention"]
    assert convention["heading_convention"] == "look"
    assert convention["heading_platform_deg"] == [HEADING_DEG]
    assert convention["heading_look_azimuth_deg"] == [348.0]  # (180 - 192) % 360
    assert "alpha = 180 deg - alpha_platform = 348 deg" in look.meta["provenance"]["method"]
    assert HEDGE not in look.meta["provenance"]["method"]
    assert P._look_azimuth(HEADING_DEG) == 348.0
    assert P._look_azimuth(12.0) == 168.0


# ---------------------------------------------------------------------------------------------
# 4. ... and it moves exactly the east term when there *is* horizontal motion
# ---------------------------------------------------------------------------------------------


def test_heading_convention_look_shifts_the_east_term_by_the_documented_factor(tmp_path):
    """The one fixture where `--heading-convention` must move the numbers.

    `u_up = cos(theta)` is heading-independent, so the mapping is felt **only** through the trusted
    horizontals `los_to_vertical` subtracts. This stack carries a real east velocity field and the
    chain is given it as `assumed_v_east_mm_yr`: under `platform` the inversion returns the injected
    field, under `look` it is off by `-2*tan(theta)*cos(alpha)*v_east` per pixel. The east field is
    exactly 0 at the reference pixel, so that shift is not swallowed by the spatial reference (a
    scene-constant assumption would cancel there, which is why the assumption has to be a field).
    """
    stack = build_stack(tmp_path / "granules", east_peak=EAST_PEAK_MM_YR)
    leak = _east_leak_factor()
    assert leak == pytest.approx(1.584, abs=5e-3)  # -2*tan(39 deg)*cos(192 deg)

    platform = run(stack, tmp_path / "platform", assumed_v_east_mm_yr=stack.east)
    look = run(
        stack,
        tmp_path / "look",
        assumed_v_east_mm_yr=stack.east,
        heading_convention="look",
    )

    # `platform` reproduces the injected truth: the phase was generated with the platform heading.
    expected_platform = stack.expected_vertical(HEADING_DEG, v_east=stack.east)
    cells = sorted(expected_platform)
    assert set(velocities(platform)) == set(cells)
    worst = max(abs(velocities(platform)[cell] - expected_platform[cell]) for cell in cells)
    assert worst < 1.0, f"platform convention should recover the injected field; worst {worst}"

    # `look` is wrong by exactly the east-leak term, cell by cell (aggregated as an H3 mean).
    east_cell = stack.cell_means(stack.east)
    expected_look = stack.expected_vertical(180.0 - HEADING_DEG, v_east=stack.east)
    for cell in cells:
        look_velocity = velocities(look)[cell]
        assert look_velocity == pytest.approx(expected_look[cell], abs=1.0)
        assert look_velocity - velocities(platform)[cell] == pytest.approx(
            leak * east_cell[cell], abs=1.0
        )
    # Not a rounding difference: the far cells move by tens of mm/yr under the wrong reading.
    shifts = [abs(velocities(look)[cell] - velocities(platform)[cell]) for cell in cells]
    assert max(shifts) > 20.0, f"heading convention should matter here; max shift {max(shifts)}"
    # ... and the shift follows the east field, so it is smallest at the reference cell (the only
    # place where the injected v_east is exactly 0).
    shifts_by_cell = {cell: abs(velocities(look)[cell] - velocities(platform)[cell]) for cell in cells}
    reference_cell = str(stack.cells[REF_IY, REF_IX])
    assert min(shifts_by_cell, key=shifts_by_cell.get) == reference_cell

    # Without the trusted horizontals the same stack cannot reveal the convention at all: the chain
    # supplies zero, the correction terms are exactly zero, and the two runs agree to the last bit.
    blind_platform = run(stack, tmp_path / "blind-platform")
    blind_look = run(stack, tmp_path / "blind-look", heading_convention="look")
    assert blind_platform.grid["velocity_mm_yr"].equals(blind_look.grid["velocity_mm_yr"])
    assert blind_platform.provenance_id != blind_look.provenance_id  # the record still differs

    # North-only motion is the other half of the asymmetry: sin(180 - alpha) == sin(alpha), so the
    # look convention cannot move it even when the horizontals are supplied. Asserted deliberately,
    # because it is why heading cannot be validated from a single pass with no east information.
    north_stack = build_stack(tmp_path / "granules-north", north_peak=EAST_PEAK_MM_YR)
    north_platform = run(
        north_stack, tmp_path / "north-platform", assumed_v_north_mm_yr=north_stack.north
    )
    north_look = run(
        north_stack,
        tmp_path / "north-look",
        assumed_v_north_mm_yr=north_stack.north,
        heading_convention="look",
    )
    np.testing.assert_allclose(
        north_look.grid["velocity_mm_yr"].to_numpy(),
        north_platform.grid["velocity_mm_yr"].to_numpy(),
        rtol=0.0,
        atol=1e-9,
    )
    # The north assumption is recorded, and it is *not* the default zero.
    north_summary = north_platform.meta["provenance"]["geometry_convention"][
        "assumed_v_north_mm_yr"
    ]
    assert north_summary["grid"] is True
    assert north_summary["max"] == pytest.approx(EAST_PEAK_MM_YR, rel=0.05)


# ---------------------------------------------------------------------------------------------
# 5. Any of the four flags removes the hedge and states what was validated
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"los_sign": "away-from-sensor"}, "los_sign = away-from-sensor"),
        ({"heading_convention": "platform"}, "heading_convention = platform"),
        ({"method_notes": ["operator inspected the fringe on 2026-11-14"]},
         "Convention note (--method-note): operator inspected the fringe on 2026-11-14."),
        ({"validated_against": "Cigna & Tapete 2022, -450 mm/yr"},
         "Convention validated against: Cigna & Tapete 2022, -450 mm/yr."),
    ],
)
def test_each_flag_removes_the_hedge_and_states_the_convention(tmp_path, kwargs, expected):
    stack = build_stack(tmp_path / "granules")
    processed = run(stack, tmp_path, **kwargs)
    provenance = processed.meta["provenance"]

    assert HEDGE not in provenance["method"]
    assert "not yet validated" not in provenance["method"].lower()
    assert expected in provenance["method"]
    assert provenance["geometry_convention"]["recorded_from_flags"] is True
    # A flagged run always says where the convention stands, even when it stands nowhere.
    if "validated_against" not in kwargs:
        assert "no --validated-against was supplied" in provenance["method"]


def test_full_combination_states_both_conventions_and_the_validation(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed = run(
        stack,
        tmp_path,
        los_sign="toward-sensor",
        heading_convention="look",
        method_notes=["2*pi slip at 2026-07-14 checked against MintPy"],
        validated_against="GNSS station MEX1, 2019-2024, vertical residual 1.8 mm/yr RMSE",
    )
    method = processed.meta["provenance"]["method"]
    assert HEDGE not in method
    assert "los_sign = toward-sensor" in method
    assert "heading_convention = look" in method
    assert (
        "Convention validated against: GNSS station MEX1, 2019-2024, vertical residual 1.8 mm/yr RMSE."
        in method
    )
    assert "Convention note (--method-note): 2*pi slip at 2026-07-14 checked against MintPy." in method
    convention = processed.meta["provenance"]["geometry_convention"]
    assert convention["los_sign"] == "toward-sensor"
    assert convention["los_sign_factor"] == -1.0
    assert convention["heading_convention"] == "look"
    assert convention["validated_against"] == (
        "GNSS station MEX1, 2019-2024, vertical residual 1.8 mm/yr RMSE"
    )
    assert convention["method_notes"] == ["2*pi slip at 2026-07-14 checked against MintPy"]

    # Still contract-valid: the extra `meta.provenance` keys did not break the frozen schema.
    report = C.validate_all(
        grid=processed.grid,
        timeseries=processed.timeseries,
        meta=processed.meta,
        context=processed.context,
        case_id=CASE,
    )
    assert report == {name: [] for name in report}, report


def test_validated_against_alone_is_allowed_but_warned(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed = run(
        stack, tmp_path, validated_against="published -450 mm/yr (Cigna & Tapete 2022)"
    )

    assert any(
        "--validated-against was given without --los-sign or --heading-convention" in warning
        for warning in processed.warnings
    ), processed.warnings
    # Allowed: the default convention is what got validated, and the record says so.
    convention = processed.meta["provenance"]["geometry_convention"]
    assert convention["los_sign"] == "toward-sensor"
    assert convention["heading_convention"] == "platform"
    assert convention["validated_against"] == "published -450 mm/yr (Cigna & Tapete 2022)"
    assert HEDGE not in processed.meta["provenance"]["method"]
    assert (
        "Convention validated against: published -450 mm/yr (Cigna & Tapete 2022)."
        in processed.meta["provenance"]["method"]
    )


def test_convention_change_without_a_validation_is_warned_but_allowed(tmp_path):
    stack = build_stack(tmp_path / "granules")
    processed = run(stack, tmp_path, los_sign="away-from-sensor")
    assert any("without --validated-against" in warning for warning in processed.warnings)
    assert "no --validated-against was supplied" in processed.meta["provenance"]["method"]


# ---------------------------------------------------------------------------------------------
# 6. Refusals: a convention is never guessed, and a blank sentence records nothing
# ---------------------------------------------------------------------------------------------


def test_unknown_convention_values_are_refused_before_any_granule_is_read(tmp_path):
    missing = tmp_path / "never-read.h5"  # the refusal must fire before the file is opened
    with pytest.raises(P.ProcessCaseError) as excinfo:
        P.process_case([missing], CASE, out_dir=tmp_path / "out", los_sign="flip-it")
    message = str(excinfo.value)
    assert "away-from-sensor" in message and "toward-sensor" in message
    assert "--los-sign" in message

    with pytest.raises(P.ProcessCaseError) as excinfo:
        P.process_case([missing], CASE, out_dir=tmp_path / "out", heading_convention="azimuth")
    message = str(excinfo.value)
    assert "platform" in message and "look" in message
    assert "--heading-convention" in message


@pytest.mark.parametrize("kwargs", [{"method_notes": ["   "]}, {"validated_against": ""}])
def test_blank_convention_text_is_refused(tmp_path, kwargs):
    with pytest.raises(P.ProcessCaseError, match="empty string"):
        P.process_case([tmp_path / "never-read.h5"], CASE, out_dir=tmp_path / "out", **kwargs)


def test_cli_rejects_a_value_outside_the_documented_choices(tmp_path):
    stack = build_stack(tmp_path / "granules")
    with pytest.raises(SystemExit) as excinfo:  # argparse `choices` is the CLI's own refusal
        P.main(cli_argv(stack, tmp_path / "cli", "--los-sign", "flip-it"))
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------------------------
# 7. Determinism: flags are inputs, so identical flags stay byte-identical
# ---------------------------------------------------------------------------------------------


def test_identical_flags_give_byte_identical_artefacts(tmp_path):
    stack = build_stack(tmp_path / "granules")
    frozen = {"retrieved_utc": "2026-11-14T00:00:00Z", "processing_date": "2026-11-14"}

    def write(out: pathlib.Path) -> dict[str, bytes]:
        processed = P.process_case(
            stack.granules,
            CASE,
            out_dir=out,
            provenance_overrides=frozen,
            los_sign="toward-sensor",
            heading_convention="look",
            method_notes=["same note"],
            validated_against="same validation",
        )
        P.write_artefacts(processed, out)
        return {name: (out / name).read_bytes() for name in sorted(p.name for p in out.iterdir())}

    first = write(tmp_path / "a")
    second = write(tmp_path / "b")
    assert set(first) == {"context_layers.geojson", "meta.json", "nisar_grid.parquet",
                          "timeseries.csv"}
    for name in first:
        assert first[name] == second[name], f"{name} is not byte-identical across reruns"


def test_provenance_id_follows_the_convention_but_not_the_prose(tmp_path):
    stack = build_stack(tmp_path / "granules")
    frozen = {"retrieved_utc": "2026-11-14T00:00:00Z", "processing_date": "2026-11-14"}

    default = run(stack, tmp_path / "default", provenance_overrides=frozen)
    # The historical digest: two positional arguments, no convention -> unchanged id.
    assert default.provenance_id == P._provenance_id(CASE, stack.granules)
    assert default.provenance_id == default.meta["provenance"]["provenance_id"]
    assert set(default.grid["provenance_id"]) == {default.provenance_id}
    assert set(default.timeseries["provenance_id"]) == {default.provenance_id}

    # Prose alone names no number, so it does not move the id; nor does restating the default
    # convention (same physical claim).
    prose_only = run(
        stack,
        tmp_path / "prose",
        provenance_overrides=frozen,
        method_notes=["checked the fringe"],
        validated_against="published rate",
    )
    assert prose_only.provenance_id == default.provenance_id
    restated_default = run(
        stack,
        tmp_path / "restated",
        provenance_overrides=frozen,
        los_sign="toward-sensor",
        heading_convention="platform",
    )
    assert restated_default.provenance_id == default.provenance_id

    flipped = run(
        stack, tmp_path / "flipped", provenance_overrides=frozen, los_sign="away-from-sensor"
    )
    assert flipped.provenance_id != default.provenance_id
    assert C.PROVENANCE_ID_RE.match(flipped.provenance_id)

    # Trusted horizontals change the numbers too, so they also enter the id -- and only when they
    # are non-zero: the zero-assumption run keeps the historical digest above.
    east_stack = build_stack(tmp_path / "granules-east", east_peak=EAST_PEAK_MM_YR)
    with_east = run(
        east_stack, tmp_path / "with-east", provenance_overrides=frozen,
        assumed_v_east_mm_yr=east_stack.east,
    )
    same_east = run(
        east_stack, tmp_path / "with-east-again", provenance_overrides=frozen,
        assumed_v_east_mm_yr=east_stack.east,
    )
    blind = run(east_stack, tmp_path / "blind", provenance_overrides=frozen)
    assert with_east.provenance_id == same_east.provenance_id
    assert with_east.provenance_id != blind.provenance_id
    with_east_summary = with_east.meta["provenance"]["geometry_convention"]["assumed_v_east_mm_yr"]
    assert with_east_summary["grid"] is True
    assert with_east_summary["max"] == pytest.approx(EAST_PEAK_MM_YR, rel=0.05)
    assert blind.meta["provenance"]["geometry_convention"]["assumed_v_east_mm_yr"] == 0.0


def test_horizontal_assumptions_are_validated(tmp_path):
    stack = build_stack(tmp_path / "granules")
    with pytest.raises(P.ProcessCaseError, match="finite everywhere"):
        run(stack, tmp_path / "nan", assumed_v_east_mm_yr=float("nan"))
    with pytest.raises(P.ProcessCaseError, match="does not broadcast"):
        run(stack, tmp_path / "shape", assumed_v_east_mm_yr=np.zeros((3, 7)))


# ---------------------------------------------------------------------------------------------
# 8. The CLI carries all four flags end to end
# ---------------------------------------------------------------------------------------------


def test_cli_accepts_the_four_flags_and_reports_the_convention(tmp_path, capsys):
    stack = build_stack(tmp_path / "granules")
    out = tmp_path / "processed" / CASE

    code = P.main(
        cli_argv(
            stack,
            out,
            "--los-sign",
            "away-from-sensor",
            "--heading-convention",
            "look",
            "--method-note",
            "heading checked against the frozen unit vector",
            "--method-note",
            "no 2*pi slip across the stack",
            "--validated-against",
            "GNSS station MEX1, residual 1.8 mm/yr RMSE",
            "--validated-against",
            "published rate -450 mm/yr (Cigna & Tapete 2022)",
            "--json",
        )
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    payload = json.loads(captured.out)

    provenance = payload["provenance"]
    assert HEDGE not in provenance["method"]
    assert provenance["geometry_convention"]["los_sign"] == "away-from-sensor"
    assert provenance["geometry_convention"]["heading_convention"] == "look"
    assert provenance["geometry_convention"]["method_notes"] == [
        "heading checked against the frozen unit vector",
        "no 2*pi slip across the stack",
    ]
    # `--validated-against` is repeatable too: every check lands in one sentence.
    assert provenance["geometry_convention"]["validated_against"] == (
        "GNSS station MEX1, residual 1.8 mm/yr RMSE; "
        "published rate -450 mm/yr (Cigna & Tapete 2022)"
    )
    assert (
        "Convention validated against: GNSS station MEX1, residual 1.8 mm/yr RMSE; "
        "published rate -450 mm/yr (Cigna & Tapete 2022)."
    ) in provenance["method"]
    assert payload["contract"] == {name: [] for name in payload["contract"]}

    # ... and the same four files on disk still pass the file-level gate.
    from pipeline import validate_artifacts as gate  # noqa: PLC0415

    report = gate.validate_tree(out)
    assert not report.failed, gate.render_human(report)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert HEDGE not in meta["provenance"]["method"]

    # Human output names the convention and what it was validated against.
    capsys.readouterr()
    assert P.main(
        cli_argv(
            stack,
            tmp_path / "processed-human" / CASE,
            "--los-sign",
            "away-from-sensor",
            "--validated-against",
            "GNSS station MEX1",
        )
    ) == 0
    human = capsys.readouterr().out
    assert "convention los_sign=away-from-sensor" in human
    assert "heading_convention=platform" in human
    assert "validated_against=GNSS station MEX1" in human


def test_grid_frames_stay_contract_shaped_with_a_convention(tmp_path):
    """The convention lives in meta only: no new column, no reordered column, no extra row."""
    stack = build_stack(tmp_path / "granules")
    default = run(stack, tmp_path / "default")
    flipped = run(stack, tmp_path / "flipped", los_sign="away-from-sensor")

    assert list(default.grid.columns) == [field.name for field in C.GRID_FIELDS]
    assert list(flipped.grid.columns) == [field.name for field in C.GRID_FIELDS]
    pd.testing.assert_frame_equal(
        default.grid[["cell_id", "lat", "lon", "coherence", "land_use", "risk_level", "case_id"]],
        flipped.grid[["cell_id", "lat", "lon", "coherence", "land_use", "risk_level", "case_id"]],
        check_dtype=True,
    )
    np.testing.assert_allclose(
        default.grid["delta_change"].to_numpy(),
        default.grid["val_t2"].to_numpy() - default.grid["val_t1"].to_numpy(),
        rtol=0.0,
        atol=1e-12,
    )


# ---------------------------------------------------------------------------------------------
# 9. Physical meaning: the label must describe the direction the numbers actually have (O-08)
# ---------------------------------------------------------------------------------------------
#
# O-08 is closed by `toward-sensor` (factor -1.0) being the default NAME for the factor the chain
# always applied. These tests state the expected signs from the physics instead of trusting a
# forward/inverse round trip, because a round trip is satisfied by any self-consistent pair of
# wrong signs -- that is exactly how the label and the arithmetic drifted apart in the first place.

UPLIFT_MM_YR = -PEAK_MM_YR  # +180: the same bowl, upward


def test_purely_vertical_uplift_is_positive_los_and_subsidence_negative(tmp_path):
    """Uplift ⇒ positive `d_LOS`, subsidence ⇒ negative, end to end and through the reader.

    `project_to_los` puts `u_up = cos(theta) = 0.777 > 0` in front of `v_up`, so a positive (upward)
    vertical velocity projects to a positive `d_LOS`: the axis points from the target toward the
    satellite. Reading that phase back with the reader's own default factor must return the same
    positive number, and the chain must record the convention as `toward-sensor`. Building the same
    bowl downward must be negative. A rename that flipped the arithmetic would fail both.
    """
    uplift = build_stack(tmp_path / "granules-uplift", up_peak=UPLIFT_MM_YR)
    u = V.los_unit_vector(INCIDENCE_DEG, HEADING_DEG)
    assert u[0] > 0.0  # u_up = cos(theta) > 0 is *why* uplift is positive LOS

    # The LOS step in isolation: the projection, then the reader's default phase conversion.
    los_rate = uplift.los_rate_mm_yr()
    pixel = (REF_IY, REF_IX + 4)  # away from the reference pixel, where the field is not 0
    assert los_rate[pixel] > 0.0
    np.testing.assert_allclose(los_rate[pixel], u[0] * uplift.up[pixel], rtol=0.0, atol=1e-12)
    phase = -4.0 * np.pi * (los_rate / 1000.0) / WAVELENGTH_M  # what the fixture writes
    recovered = G.phase_to_los_displacement_mm(phase, WAVELENGTH_M)  # default sign=-1.0
    np.testing.assert_allclose(recovered, los_rate, rtol=0.0, atol=1e-9)

    # End to end: uplift is a positive velocity under the default, and the record says so.
    rising = run(uplift, tmp_path / "rising")
    assert rising.meta["stats"]["median_velocity_mm_yr"] > 0.0
    convention = rising.meta["provenance"]["geometry_convention"]
    assert convention["los_sign"] == "toward-sensor"
    assert convention["los_sign_factor"] == -1.0
    assert "uplift" in convention["los_sign_meaning"]

    # ... and the same bowl built downward (the module fixture) is negative.
    sinking = build_stack(tmp_path / "granules-sink")
    subsiding = run(sinking, tmp_path / "subsiding")
    assert sinking.los_rate_mm_yr()[pixel] < 0.0
    assert subsiding.meta["stats"]["median_velocity_mm_yr"] < 0.0
    assert "subsidence" in convention["los_sign_meaning"]


def test_known_horizontal_motion_keeps_the_sign_the_unit_vector_declares(tmp_path):
    """A +east motion at heading 192 projects toward the satellite (`u_east > 0`), so LOS > 0.

    At theta = 39 deg, alpha = 192 deg the frozen unit vector is `u = [0.777, +0.616, +0.131]`, so
    eastward motion is toward-sensor-positive. Supplying that field as the trusted horizontals must
    recover **zero** vertical motion under the default label -- the LOS sign and `u` then agree
    exactly. Under `away-from-sensor` the same trusted horizontals recover
    `-2*u_east*v_east/u_up`, so the flip is a change of physical direction, not of formatting.
    """
    east_mm_yr = 40.0
    stack = build_stack(tmp_path / "granules", up_peak=0.0, east_peak=east_mm_yr)
    u = V.los_unit_vector(INCIDENCE_DEG, HEADING_DEG)
    assert u[1] > 0.0  # -sin(theta)*cos(192 deg) > 0: +east has a component toward the sensor

    los_rate = stack.los_rate_mm_yr()
    np.testing.assert_allclose(los_rate, u[1] * stack.east, rtol=0.0, atol=1e-12)
    pixel = (REF_IY, REF_IX + 4)
    assert los_rate[pixel] > 0.0  # the declared toward-sensor sign of a +east motion

    toward = run(stack, tmp_path / "toward", assumed_v_east_mm_yr=stack.east)
    np.testing.assert_allclose(
        toward.grid["velocity_mm_yr"].to_numpy(), np.zeros(len(toward.grid)), rtol=0.0, atol=0.05
    )

    away = run(
        stack, tmp_path / "away", los_sign="away-from-sensor", assumed_v_east_mm_yr=stack.east
    )
    leak = -2.0 * u[1] / u[0]  # what the flipped LOS sign leaves behind, per mm/yr of east motion
    expected = {cell: leak * value for cell, value in stack.cell_means(stack.east).items()}
    assert set(velocities(away)) == set(expected)
    for cell, velocity in velocities(away).items():
        assert velocity == pytest.approx(expected[cell], abs=0.05)
    assert max(abs(value) for value in velocities(away).values()) > 10.0  # not a rounding choice


def test_the_two_sign_conventions_are_exact_negatives_and_restating_default_is_identical(tmp_path):
    """`d_LOS = sign*lambda/(4*pi)*phi`: the labels differ only by the sign of every vertical value.

    Also the O-08 no-drift guarantee: restating the default (``toward-sensor``, -1.0) is
    **byte-identical** to omitting the flag, so the rename moved no number, only the label.
    """
    stack = build_stack(tmp_path / "granules")
    default = run(stack, tmp_path / "default")
    stated = run(stack, tmp_path / "stated", los_sign="toward-sensor")
    away = run(stack, tmp_path / "away", los_sign="away-from-sensor")

    # Restating the default names the same physical claim: identical frames, statistics and id.
    pd.testing.assert_frame_equal(default.grid, stated.grid)
    pd.testing.assert_frame_equal(default.timeseries, stated.timeseries)
    assert default.meta["stats"] == stated.meta["stats"]
    assert default.provenance_id == stated.provenance_id
    assert stated.meta["provenance"]["geometry_convention"]["los_sign_factor"] == -1.0

    # The flip negates every published vertical column, so no number is merely relabelled.
    for column in ("velocity_mm_yr", "val_t1", "val_t2", "delta_change"):
        np.testing.assert_allclose(
            away.grid[column].to_numpy(),
            -default.grid[column].to_numpy(),
            rtol=0.0,
            atol=1e-6,
        )
    assert away.meta["stats"]["median_velocity_mm_yr"] == pytest.approx(
        -default.meta["stats"]["median_velocity_mm_yr"], abs=1e-6
    )
    assert away.provenance_id != default.provenance_id
    assert P.LOS_SIGN_FACTORS == {"toward-sensor": -1.0, "away-from-sensor": 1.0}
    assert P.DEFAULT_LOS_SIGN == "toward-sensor"
    assert P.LOS_SIGN_FACTORS[P.DEFAULT_LOS_SIGN] == -1.0  # the historical factor, unchanged
