"""Adversarial, hermetic tests for the NISAR L2 GUNW reader (`pipeline/ingest/nisar_gunw.py`).

`tests/test_nisar_gunw.py` proves the reader works on a fixture built to match
``docs/nisar-gunw-layout.md``. This file assumes the *first real granule* will disagree with that
document somewhere and asks one question of every disagreement: does the reader fail loudly, or
does it hand back a plausible-looking number?

Files are written to ``tmp_path`` with ``h5py.File``; nothing is downloaded and no real granule is
needed. Paths in the builders are transcribed from ``docs/nisar-gunw-layout.md`` §2 rather than
from ``nisar_gunw.DATASETS`` on purpose: if the module's table ever drifts from the document,
these fixtures are the thing that notices.

Policy for defects found here
-----------------------------
Where the module is *correct* (a typed ``GranuleError`` naming the path/condition, or a documented
warning that reaches :func:`nisar_gunw.summary`), the test is a plain assertion and passes.

Where the module was *wrong*, the test asserted the behaviour we need and was temporarily marked
``@pytest.mark.xfail(strict=True, reason=...)``. Strict xfail kept each defect visible and named
instead of indistinguishable from an unrelated red test (other agents edit other files here) and
forced a deliberate edit — an unfixed module could not be closed silently. The four defects it
recorded (all-NaN granule read as clean, undocumented mask flags swallowed, silently flattened 2-D
coordinates, fabricated/silently dropped acquisition date) are fixed, so every test below is now a
plain assertion. There must be **no** ``xfail`` in this file: a regression shows up as a failure,
not as an expected one.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
from collections.abc import Callable

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

h5py = pytest.importorskip("h5py")

from pipeline.ingest import nisar_gunw as G  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Layout, transcribed from `docs/nisar-gunw-layout.md` §2 (not from G.DATASETS — see module doc).
# ---------------------------------------------------------------------------------------------

BASE = "/science/LSAR/GUNW"
GRIDS = f"{BASE}/grids/frequencyA"
UNWRAPPED = f"{GRIDS}/unwrappedInterferogram"

#: Two zero-Doppler tokens, the pair the filename fallback is documented to read.
DATED_NAME = (
    "NISAR_L2_PR_GUNW_001_005_A_001_002_4000_SH_"
    "20260702T031245_20260714T031245.h5"
)
#: No `YYYYMMDDTHHMMSS` token anywhere: the reader has nothing to fall back on.
UNDATED_NAME = "granule_no_date_tokens.h5"

SHAPE = (4, 6)


def default_arrays() -> dict[str, np.ndarray]:
    """A 4x6 grid with mask 0/2/127 and no non-finite pixels, so each test varies one thing."""
    phase = np.full(SHAPE, 0.8, dtype=np.float32)
    phase[0, 1] = 0.0  # zero is a real measurement in Málaga, never nodata
    coherence = np.linspace(0.10, 0.95, num=SHAPE[0] * SHAPE[1]).reshape(SHAPE).astype(np.float32)
    mask = np.tile(np.array([0, 0, 2, 2, 127, 127], dtype=np.int8), (SHAPE[0], 1))
    connected = np.tile(np.array([1, 1, 2, 2, 0, 0], dtype=np.uint32), (SHAPE[0], 1))
    return {"phase": phase, "coherence": coherence, "mask": mask, "connected": connected}


def write_granule(  # noqa: PLR0913 - one fixture builder beats fifteen near-copies
    directory: pathlib.Path,
    *,
    name: str = UNDATED_NAME,
    pol: str = "HH",
    omit: frozenset[str] = frozenset(),
    overrides: dict[str, np.ndarray] | None = None,
    coords: str = "1d",
    wavelength: float | np.ndarray | None = 0.2384,
    center_frequency: float | None = None,
    times: str | tuple[object, object] = "iso",
    extra_pols: tuple[str, ...] = (),
    with_group: bool = True,
    edit: Callable[[pathlib.Path], None] | None = None,
) -> pathlib.Path:
    """Write one synthetic granule and return its path.

    ``coords``: ``"1d"`` (documented case), ``"none"``, ``"2d-full"`` (an ``x2D``/``y2D`` grid
    over the whole grid) or ``"2d-cropped"`` (a 2-D grid whose *flattened* size happens to equal
    the axis length — the case a reader can flatten without noticing).
    ``times``: ``"iso"``, ``"none"``, or a ``(reference, secondary)`` pair written verbatim
    (``None`` omits that dataset).
    ``edit``: optional callable receiving the path, run with the file reopened, for one-off
    surgery that does not deserve a builder flag.
    """
    path = directory / name
    data = default_arrays()
    data.update(overrides or {})

    with h5py.File(path, "w") as f:
        if with_group:
            group = f.require_group(f"{UNWRAPPED}/{pol}")
            if "phase" not in omit:
                group.create_dataset("unwrappedPhase", data=data["phase"])
            if "coherence" not in omit:
                group.create_dataset("coherenceMagnitude", data=data["coherence"])
            if "connected" not in omit:
                group.create_dataset("connectedComponents", data=data["connected"])
        # `mask` lives at the interferogram root, not under {pol} (§2).
        if "mask" not in omit:
            f.create_dataset(f"{UNWRAPPED}/mask", data=data["mask"])

        if coords == "1d":
            f.create_dataset(f"{GRIDS}/xCoordinates", data=np.arange(SHAPE[1]) * 80.0)
            f.create_dataset(
                f"{GRIDS}/yCoordinates", data=np.arange(SHAPE[0]) * -80.0 + 500_000.0
            )
        elif coords == "2d-full":
            f.create_dataset(
                f"{GRIDS}/xCoordinates", data=np.tile(np.arange(SHAPE[1]) * 80.0, (SHAPE[0], 1))
            )
            f.create_dataset(
                f"{GRIDS}/yCoordinates",
                data=np.tile((np.arange(SHAPE[0]) * -80.0 + 500_000.0)[:, None], (1, SHAPE[1])),
            )
        elif coords == "2d-cropped":
            # (1, ncols) and (nrows, 1): flattened size == axis length, so a reader that only
            # compares sizes accepts them as 1-D axes without a word.
            f.create_dataset(
                f"{GRIDS}/xCoordinates", data=np.arange(SHAPE[1], dtype=float).reshape(1, -1) * 80.0
            )
            f.create_dataset(
                f"{GRIDS}/yCoordinates",
                data=(np.arange(SHAPE[0], dtype=float) * -80.0 + 500_000.0).reshape(-1, 1),
            )
        elif coords != "none":
            raise AssertionError(f"unknown coords mode {coords!r}")

        f.create_dataset(f"{GRIDS}/projection", data=np.int32(32614))

        if wavelength is not None:
            f.create_dataset(f"{BASE}/metadata/radarGrid/wavelength", data=wavelength)
        if center_frequency is not None:
            f.create_dataset(
                f"{BASE}/metadata/processingInformation/parameters/frequencyA/centerFrequency",
                data=np.float64(center_frequency),
            )

        if times == "iso":
            f.create_dataset(
                f"{BASE}/identification/referenceZeroDopplerStartTime",
                data="2026-07-02T03:12:45Z",
            )
            f.create_dataset(
                f"{BASE}/identification/secondaryZeroDopplerStartTime",
                data="2026-07-14T03:12:45Z",
            )
        elif times != "none":
            ref, sec = times  # type: ignore[misc]
            if ref is not None:
                f.create_dataset(
                    f"{BASE}/identification/referenceZeroDopplerStartTime", data=ref
                )
            if sec is not None:
                f.create_dataset(
                    f"{BASE}/identification/secondaryZeroDopplerStartTime", data=sec
                )

        for extra in extra_pols:
            extra_group = f.require_group(f"{UNWRAPPED}/{extra}")
            extra_group.create_dataset("unwrappedPhase", data=data["phase"])
            extra_group.create_dataset("coherenceMagnitude", data=data["coherence"])
            extra_group.create_dataset("connectedComponents", data=data["connected"])

    if edit is not None:
        edit(path)
    return path


def snapshot(directory: pathlib.Path) -> dict[str, tuple[int, int, str]]:
    """Every file under ``directory`` -> (size, mtime_ns, sha256). Directories are ignored."""
    state: dict[str, tuple[int, int, str]] = {}
    for entry in sorted(directory.rglob("*")):
        if not entry.is_file():
            continue
        stat = entry.stat()
        state[str(entry.relative_to(directory))] = (
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(entry.read_bytes()).hexdigest(),
        )
    return state


def assert_typed_error(excinfo: pytest.ExceptionInfo[BaseException], *fragments: str) -> None:
    """A GranuleError, never a bare KeyError/AttributeError/TypeError/HDF5 OSError."""
    error = excinfo.value
    assert isinstance(error, G.GranuleError), f"expected GranuleError, got {type(error).__name__}"
    assert not isinstance(error, (KeyError, AttributeError, TypeError, OSError, ValueError))
    message = str(error)
    assert message.strip(), "the error message is empty: unreportable failure"
    for fragment in fragments:
        assert fragment in message, f"expected {fragment!r} in message: {message!r}"


# ---------------------------------------------------------------------------------------------
# 1. Missing required dataset: the message must name the path and what was actually there.
#    (tests/test_nisar_gunw.py already proves the four documented paths are named; this covers
#    the realistic near-miss variants a real granule produces: a differently-cased name and a
#    dataset that moved into the wrong group.)
# ---------------------------------------------------------------------------------------------


def test_missing_phase_lists_the_near_miss_name_and_does_not_accept_it(tmp_path):
    def misname(path: pathlib.Path) -> None:
        with h5py.File(path, "r+") as f:
            f.create_dataset(
                f"{UNWRAPPED}/HH/unwrappedphase", data=default_arrays()["phase"]
            )  # lower-case 'p': a plausible real-world rename

    path = write_granule(tmp_path, omit=frozenset({"phase"}), edit=misname)
    expected = G.DATASETS["phase"].format(pol="HH")

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    assert_typed_error(excinfo, "required GUNW dataset not found", expected, "HH")
    # The near-miss is shown, not silently accepted: the operator can diff the name.
    assert "unwrappedphase" in str(excinfo.value)
    assert "Contents of" in str(excinfo.value)


def test_mask_moved_under_the_pol_group_is_reported_with_the_parent_listing(tmp_path):
    def move_mask(path: pathlib.Path) -> None:
        with h5py.File(path, "r+") as f:
            mask = f[f"{UNWRAPPED}/mask"][()]
            del f[f"{UNWRAPPED}/mask"]
            f.create_dataset(f"{UNWRAPPED}/HH/mask", data=mask)  # documented as NOT under {pol}

    path = write_granule(tmp_path, edit=move_mask)

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    assert_typed_error(excinfo, "required GUNW dataset not found", f"{UNWRAPPED}/mask")
    assert "HH" in str(excinfo.value)  # shows what it did find instead


# ---------------------------------------------------------------------------------------------
# 2. Wrong dtypes: accepted-with-a-warning, or refused naming the expected dtype.
#    Silent reinterpretation is the failure mode; scale factors are the trap.
# ---------------------------------------------------------------------------------------------


def test_float64_phase_is_cast_to_float32_with_a_recorded_warning(tmp_path):
    phase = default_arrays()["phase"].astype(np.float64)
    granule = G.read_granule(write_granule(tmp_path, overrides={"phase": phase}))

    assert granule.phase_rad.dtype == np.float32
    expected = G.DATASETS["phase"].format(pol="HH")
    assert any(
        expected in warning and "float64" in warning and "cast to float32" in warning
        for warning in granule.warnings
    ), granule.warnings
    # The dtype change is provenance: it must reach summary(), not just the dataclass.
    assert any(expected in w for w in G.summary(granule)["warnings"])


def test_int64_mask_is_cast_to_int8_with_a_recorded_warning(tmp_path):
    mask = default_arrays()["mask"].astype(np.int64)
    granule = G.read_granule(write_granule(tmp_path, overrides={"mask": mask}))

    assert granule.mask.dtype == np.int8
    assert any(
        G.DATASETS["mask"] in warning and "int64" in warning and "cast to int8" in warning
        for warning in granule.warnings
    ), granule.warnings


def test_int64_labels_are_cast_to_uint32_with_a_recorded_warning(tmp_path):
    connected = default_arrays()["connected"].astype(np.int64)
    granule = G.read_granule(write_granule(tmp_path, overrides={"connected": connected}))

    assert granule.connected_components.dtype == np.uint32
    assert any(
        "connectedComponents" in warning and "cast to uint32" in warning
        for warning in granule.warnings
    ), granule.warnings


def test_float32_connected_components_are_refused_not_rounded(tmp_path):
    connected = default_arrays()["connected"].astype(np.float32)
    connected[0, 1] = 1.7  # a rounded label would silently reassign pixels to another component

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(write_granule(tmp_path, overrides={"connected": connected}))
    assert_typed_error(
        excinfo,
        G.DATASETS["connected_components"].format(pol="HH"),
        "expected an integer dtype",
        "float32",
    )


def test_integer_phase_with_a_scale_factor_is_refused_not_rescaled(tmp_path):
    def scaled(path: pathlib.Path) -> None:
        with h5py.File(path, "r+") as f:
            dataset = f.create_dataset(
                f"{UNWRAPPED}/HH/unwrappedPhase",
                data=(default_arrays()["phase"] * 1000).astype(np.int16),
            )
            dataset.attrs["scale_factor"] = np.float32(1e-3)

    path = write_granule(tmp_path, omit=frozenset({"phase"}), edit=scaled)
    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    assert_typed_error(
        excinfo,
        G.DATASETS["phase"].format(pol="HH"),
        "expected a float dtype",
        "int16",
    )


def test_labels_beyond_uint32_are_refused_not_wrapped(tmp_path):
    connected = default_arrays()["connected"].astype(np.int64)
    connected[0, 0] = 2**40

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(write_granule(tmp_path, overrides={"connected": connected}))
    assert_typed_error(excinfo, "does not fit uint32", "1099511627776")


# ---------------------------------------------------------------------------------------------
# 3. All-NaN phase (fully masked granule): an empty-but-*labelled* result, never a zero grid.
# ---------------------------------------------------------------------------------------------


def test_all_nan_phase_yields_no_valid_pixels_and_no_zero_fill(tmp_path):
    phase = np.full(SHAPE, np.nan, dtype=np.float32)
    mask = np.zeros(SHAPE, dtype=np.int8)  # nothing marked as padding: only the floats are NaN
    granule = G.read_granule(write_granule(tmp_path, overrides={"phase": phase, "mask": mask}))
    report = G.summary(granule)

    assert report["valid_pixels"] == 0
    assert report["total_pixels"] == int(np.prod(SHAPE))
    assert report["valid_fraction"] == 0.0
    assert report["coherence_percentiles"] == {"p05": None, "p50": None, "p95": None}
    assert report["coherence_mean"] is None
    assert report["connected_component_count"] == 0
    assert report["connected_component_max"] is None

    # Nothing was imputed: phase stays NaN and every converted pixel stays NaN (never 0.0).
    assert np.isnan(granule.phase_rad).all()
    displacement = G.phase_to_los_displacement_mm(granule.phase_rad, granule.wavelength_m)
    assert np.isnan(displacement).all()
    assert not np.any(displacement == 0.0)
    assert not G.valid_mask(granule).any()


def test_all_nan_phase_records_a_warning_that_nothing_is_valid(tmp_path):
    phase = np.full(SHAPE, np.nan, dtype=np.float32)
    mask = np.zeros(SHAPE, dtype=np.int8)
    report = G.summary(
        G.read_granule(write_granule(tmp_path, overrides={"phase": phase, "mask": mask}))
    )

    assert report["valid_pixels"] == 0
    assert any(
        "valid" in warning.lower() for warning in report["warnings"]
    ), f"a fully masked granule is silent: warnings={report['warnings']!r}"


# ---------------------------------------------------------------------------------------------
# 4. Mask values the documentation does not list (4, 7, 255).
# ---------------------------------------------------------------------------------------------


def test_undocumented_mask_flags_are_carried_through_by_the_documented_rule(tmp_path):
    mask = default_arrays()["mask"].copy()
    mask[0, 3] = 4
    mask[2, 0] = 7
    granule = G.read_granule(write_granule(tmp_path, overrides={"mask": mask}))

    # `valid_mask` removes 127 and non-finite floats only; unknown flags stay for a later filter.
    valid = G.valid_mask(granule)
    assert valid[0, 3] and valid[2, 0]
    assert not valid[0, 4]  # 127 padding still dropped
    # The raw array is handed through unmodified, so a downstream filter can see 4/7.
    assert granule.mask[0, 3] == 4 and granule.mask[2, 0] == 7
    assert {int(value) for value in np.unique(granule.mask)} == {0, 2, 4, 7, 127}


def test_mask_value_255_is_refused_rather_than_wrapped_to_minus_one(tmp_path):
    """255 cannot fit `int8`; a real granule that stores it must be reported, not wrapped."""
    mask = default_arrays()["mask"].astype(np.uint8)
    mask[0, 0] = 255  # e.g. an alternative padding convention arriving as uint8

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(write_granule(tmp_path, overrides={"mask": mask}))
    assert_typed_error(excinfo, G.DATASETS["mask"], "255", "does not fit int8")


def test_undocumented_mask_flags_are_recorded_in_the_summary(tmp_path):
    mask = default_arrays()["mask"].copy()
    mask[0, 3] = 4
    mask[2, 0] = 7
    report = G.summary(G.read_granule(write_granule(tmp_path, overrides={"mask": mask})))

    recorded = " ".join(report["warnings"]) + " " + repr(
        {key: value for key, value in report.items() if "mask" in key}
    )
    assert "4" in recorded and "7" in recorded, (
        "undocumented mask values are silently swallowed by summary(): "
        f"warnings={report['warnings']!r}"
    )


# ---------------------------------------------------------------------------------------------
# 5. 2-D coordinate grids where a 1-D axis is expected: refuse, never flatten/resample.
# ---------------------------------------------------------------------------------------------


def test_full_2d_coordinate_grids_are_refused_with_a_warning(tmp_path):
    granule = G.read_granule(write_granule(tmp_path, coords="2d-full"))

    assert granule.x is None and granule.y is None
    assert any(
        "x coordinate" in warning and "length 24" in warning and "expected 6" in warning
        for warning in granule.warnings
    ), granule.warnings
    assert any(
        "y coordinate" in warning and "length 24" in warning and "expected 4" in warning
        for warning in granule.warnings
    ), granule.warnings
    assert any("left None" in warning for warning in granule.warnings)


def test_2d_coordinate_grid_that_size_matches_is_not_flattened_silently(tmp_path):
    granule = G.read_granule(write_granule(tmp_path, coords="2d-cropped"))

    flattened_silently = (granule.x is not None or granule.y is not None) and not any(
        "coordinate" in warning for warning in granule.warnings
    )
    assert not flattened_silently, (
        f"a 2-D coordinate grid was flattened without a warning: x={granule.x!r} y={granule.y!r} "
        f"warnings={granule.warnings!r}"
    )


# ---------------------------------------------------------------------------------------------
# 6. No wavelength anywhere: None + warning, and the converter must refuse.
# ---------------------------------------------------------------------------------------------


def test_absent_wavelength_and_frequency_leave_none_and_block_the_conversion(tmp_path):
    granule = G.read_granule(write_granule(tmp_path, wavelength=None, center_frequency=None))
    report = G.summary(granule)

    assert granule.wavelength_m is None
    assert granule.wavelength_source is None
    assert report["wavelength_m"] is None
    assert report["wavelength_source"] == "absent"
    assert granule.wavelength_m != pytest.approx(0.2384)  # the L-band assumption is not applied
    assert any("wavelength_m absent" in warning for warning in report["warnings"])

    with pytest.raises(ValueError) as excinfo:
        G.phase_to_los_displacement_mm(granule.phase_rad, granule.wavelength_m)
    message = str(excinfo.value)
    assert "wavelength_m is None" in message
    assert "refusing to assume" in message
    assert "nisar-gunw-layout.md" in message


def test_vector_wavelength_metadata_is_not_reduced_to_its_first_element(tmp_path):
    """NISAR metadata often stores per-frequency arrays; picking element 0 would be a guess."""
    granule = G.read_granule(
        write_granule(tmp_path, wavelength=np.array([0.2384, 0.2390]), center_frequency=None)
    )

    assert granule.wavelength_m is None
    assert any("wavelength" in warning for warning in granule.warnings), granule.warnings
    with pytest.raises(ValueError):
        G.phase_to_los_displacement_mm(granule.phase_rad, granule.wavelength_m)


# ---------------------------------------------------------------------------------------------
# 7. Acquisition times: None + warning, nothing invented.
# ---------------------------------------------------------------------------------------------


def test_no_time_metadata_and_no_filename_tokens_leave_none_with_a_warning(tmp_path):
    granule = G.read_granule(write_granule(tmp_path, name=UNDATED_NAME, times="none"))
    report = G.summary(granule)

    assert granule.reference_time is None and granule.secondary_time is None
    assert report["dates"] == {"reference": None, "secondary": None}
    assert report["date_pair"] == [None, None]
    assert any("acquisition date pair incomplete" in warning for warning in report["warnings"])


def test_single_filename_token_leaves_the_second_date_none_with_a_warning(tmp_path):
    name = "NISAR_L2_PR_GUNW_001_20260702T031245.h5"  # one token only
    granule = G.read_granule(write_granule(tmp_path, name=name, times="none"))
    report = G.summary(granule)

    assert report["dates"] == {"reference": "2026-07-02", "secondary": None}
    assert any("parsed from the filename" in warning for warning in report["warnings"])
    assert any("acquisition date pair incomplete" in warning for warning in report["warnings"])


def test_filename_fallback_lists_the_dates_it_adopted_so_a_misordering_is_visible(tmp_path):
    name = "NISAR_L2_PR_GUNW_001_20260101T000000_20260702T031245_20260714T031245.h5"
    granule = G.read_granule(write_granule(tmp_path, name=name, times="none"))
    report = G.summary(granule)

    # Three tokens: the reader takes the first two. That may be wrong on a real granule, so the
    # adopted pair must be quoted in the warning for a human to check, not silently applied.
    assert report["date_pair"] == ["2026-01-01", "2026-07-02"]
    assert any(
        "parsed from the filename" in warning
        and "2026-01-01" in warning
        and "2026-07-02" in warning
        for warning in report["warnings"]
    ), report["warnings"]


@pytest.mark.parametrize(
    "times",
    [
        pytest.param((np.float64(1783000000.5), np.float64(1784000000.5)), id="epoch-seconds"),
        pytest.param((np.int64(1783000000123456789), np.int64(1784000000123456789)), id="epoch-ns"),
        pytest.param(("unknown", "unknown"), id="opaque-string"),
    ],
)
def test_unparseable_acquisition_time_never_becomes_a_fabricated_date(tmp_path, times):
    report = G.summary(G.read_granule(write_granule(tmp_path, times=times)))
    reference = report["dates"]["reference"]

    if reference is None:
        assert any(
            "acquisition" in warning for warning in report["warnings"]
        ), f"date reported as None with no warning: {report['warnings']!r}"
        return

    year, month, day = (int(part) for part in reference.split("-"))
    assert 1 <= month <= 12 and 1 <= day <= 31, (
        f"summary() invented a date from a non-date metadata value: {reference!r}"
    )


# ---------------------------------------------------------------------------------------------
# 8. Degenerate datasets: zero-length and scalar — typed error, no numpy exception.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "array", "fragment"),
    [
        pytest.param(
            "phase", np.zeros((0, 0), dtype=np.float32), "is empty (shape (0, 0))", id="phase-0x0"
        ),
        pytest.param(
            "mask", np.zeros((0, 0), dtype=np.int8), "is empty (shape (0, 0))", id="mask-0x0"
        ),
        pytest.param(
            "phase", np.float32(0.5), "is 0-D with shape ()", id="phase-scalar"
        ),
        pytest.param(
            "mask", np.int8(0), "is 0-D with shape ()", id="mask-scalar"
        ),
        pytest.param(
            "connected",
            np.zeros((0, 0), dtype=np.uint32),
            "is empty (shape (0, 0))",
            id="labels-0x0",
        ),
    ],
)
def test_degenerate_datasets_raise_a_typed_error(tmp_path, key, array, fragment):
    dataset_name = {
        "phase": "unwrappedPhase",
        "mask": None,  # at the top of the group, not under {pol}
        "connected": "connectedComponents",
    }[key]

    def shrink(path: pathlib.Path) -> None:
        target = f"{UNWRAPPED}/mask" if dataset_name is None else f"{UNWRAPPED}/HH/{dataset_name}"
        with h5py.File(path, "r+") as f:
            if target in f:
                del f[target]
            f.create_dataset(target, data=array)

    path = write_granule(tmp_path, omit=frozenset({key}), edit=shrink)
    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    expected_path = f"{UNWRAPPED}/mask" if dataset_name is None else f"{UNWRAPPED}/HH/{dataset_name}"
    assert_typed_error(excinfo, expected_path, fragment)


# ---------------------------------------------------------------------------------------------
# 9-10. Polarisation ambiguity: the caller resolves it, the reader never guesses.
#    (tests/test_nisar_gunw.py covers the plain HH+VV and neither-present messages; these add the
#    paths the message must quote and the case-mismatch variant a real filename may produce.)
# ---------------------------------------------------------------------------------------------


def test_both_polarisations_are_named_and_the_reader_defers_to_the_caller(tmp_path):
    path = write_granule(tmp_path, pol="HH", extra_pols=("VV",))

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    message = str(excinfo.value)
    assert_typed_error(excinfo, "ambiguous polarisation", UNWRAPPED, "HH", "VV")
    assert "explicit" in message  # points at the caller's decision (--polarisation)

    # The caller can resolve it, and the choice is recorded rather than silently made.
    granule = G.read_granule(path, polarisation="HH")
    assert granule.polarisation == "HH"
    assert any("explicitly requested" in warning for warning in granule.warnings)


@pytest.mark.parametrize(
    ("pol", "with_group", "expected_children"),
    [
        pytest.param("hh", True, "'hh'", id="lowercase-hh-only"),
        pytest.param("HH", False, "'mask'", id="mask-only-child"),
    ],
)
def test_no_polarisation_error_names_what_it_actually_found(
    tmp_path, pol, with_group, expected_children
):
    path = write_granule(tmp_path, pol=pol, with_group=with_group)

    with pytest.raises(G.GranuleError) as excinfo:
        G.discover_polarisation(path)
    assert_typed_error(excinfo, "no polarisation found", UNWRAPPED, "HH", "VV")
    assert expected_children in str(excinfo.value)


def test_pol_group_entry_that_is_a_dataset_does_not_raise_a_bare_typeerror(tmp_path):
    def flatten_group(path: pathlib.Path) -> None:
        with h5py.File(path, "r+") as f:
            del f[f"{UNWRAPPED}/HH"]
            f.create_dataset(f"{UNWRAPPED}/HH", data=default_arrays()["phase"])

    path = write_granule(tmp_path, edit=flatten_group)
    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    assert_typed_error(excinfo, G.DATASETS["phase"].format(pol="HH"))


# ---------------------------------------------------------------------------------------------
# 11. Corrupt / truncated / empty files: a GranuleError, never an HDF5 OSError escaping.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("content", ["random-bytes", "empty", "truncated"])
def test_corrupt_files_are_a_typed_error(tmp_path, content):
    path = tmp_path / "granule.h5"
    if content == "random-bytes":
        path.write_bytes(np.random.default_rng(0).bytes(512))
    elif content == "empty":
        path.write_bytes(b"")
    else:
        good = write_granule(tmp_path, name="good.h5")
        raw = good.read_bytes()
        path.write_bytes(raw[: len(raw) // 2])  # e.g. a download that stopped early

    for call in (G.read_granule, G.discover_polarisation):
        with pytest.raises(G.GranuleError) as excinfo:
            call(path)
        assert_typed_error(excinfo, str(path), "nisar-gunw-layout.md")
        message = str(excinfo.value)
        # Either the file cannot be opened at all, or a dataset cannot be read: both are named.
        assert "cannot open" in message or "cannot read required" in message, message
        assert "HDF5" in message, message  # says what failed, not a bare OSError repr


# ---------------------------------------------------------------------------------------------
# 12. A path that is a directory or does not exist.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call_name", ["read_granule", "discover_polarisation"], ids=["read", "discover"]
)
@pytest.mark.parametrize("kind", ["directory", "missing"], ids=["directory", "missing"])
def test_bad_paths_are_a_typed_error(tmp_path, call_name, kind):
    target = tmp_path / "granule.h5"
    if kind == "directory":
        target.mkdir()
    call = getattr(G, call_name)

    with pytest.raises(G.GranuleError) as excinfo:
        call(target)
    assert_typed_error(excinfo, str(target))


# ---------------------------------------------------------------------------------------------
# 13. No side effects: reading is read-only, down to mtime and bytes.
# ---------------------------------------------------------------------------------------------


def test_reading_a_valid_granule_creates_and_modifies_nothing(tmp_path):
    path = write_granule(tmp_path, name=DATED_NAME)
    before = snapshot(tmp_path)
    assert str(DATED_NAME) in before

    granule = G.read_granule(path)
    G.summary(granule)
    G.valid_mask(granule)
    G.phase_to_los_displacement_mm(granule.phase_rad, granule.wavelength_m)

    after = snapshot(tmp_path)
    assert set(after) == set(before), f"reading created or removed files: {set(after) ^ set(before)}"
    assert after == before, "reading modified a file (size/mtime/sha256 changed)"


# ---------------------------------------------------------------------------------------------
# Extras: two traps that are documented-and-warned rather than silent, asserted so they stay
# visible in summary() and never become a bare reinterpretation.
# ---------------------------------------------------------------------------------------------


def test_declared_fill_value_is_warned_not_silently_reinterpreted(tmp_path):
    phase = default_arrays()["phase"].copy()
    phase[0, 0] = -9999.0  # documented for SME2, NOT confirmed for GUNW (§5.4)

    def declare_fill(path: pathlib.Path) -> None:
        with h5py.File(path, "r+") as f:
            f[f"{UNWRAPPED}/HH/unwrappedPhase"].attrs["_FillValue"] = np.float32(-9999.0)

    granule = G.read_granule(write_granule(tmp_path, overrides={"phase": phase}, edit=declare_fill))
    report = G.summary(granule)

    assert float(granule.phase_rad[0, 0]) == -9999.0  # not nan_to_num'd, not "fixed" silently
    assert any(
        "_FillValue=-9999.0" in warning and "NOT interpreted as nodata" in warning
        for warning in report["warnings"]
    ), report["warnings"]


def test_epsg_is_never_invented_when_the_grid_has_no_crs(tmp_path):
    def drop_projection(path: pathlib.Path) -> None:
        with h5py.File(path, "r+") as f:
            del f[f"{GRIDS}/projection"]

    granule = G.read_granule(write_granule(tmp_path, edit=drop_projection))
    report = G.summary(granule)

    assert granule.epsg is None and report["epsg"] is None
    assert any("epsg not found" in warning for warning in report["warnings"])
