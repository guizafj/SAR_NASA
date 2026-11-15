"""Hermetic tests for the NISAR L2 GUNW reader (`pipeline/ingest/nisar_gunw.py`).

Every fixture is a small synthetic HDF5 file written to ``tmp_path`` with the layout documented in
``docs/nisar-gunw-layout.md`` §2–§3. Nothing is downloaded, no real granule is required, and the
whole file runs in well under a few seconds.

The tests are written to fail when the reader starts being helpful in the wrong way: inventing a
wavelength, guessing an EPSG, turning nodata into 0, or treating an empty/missing dataset as valid.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

# HDF5 is needed for the fixture builder, not for the pure functions under test.
h5py = pytest.importorskip("h5py")

from pipeline.ingest import nisar_gunw as G  # noqa: E402

BASE = "/science/LSAR/GUNW"
GRIDS = f"{BASE}/grids/frequencyA"
UNWRAPPED = f"{GRIDS}/unwrappedInterferogram"
MODULE = ROOT / "pipeline" / "ingest" / "nisar_gunw.py"

#: A plausible NISAR filename: the two zero-Doppler tokens are the pair the fallback parser reads.
GRANULE_NAME = (
    "NISAR_L2_PR_GUNW_001_005_A_001_002_4000_SH_"
    "20260702T031245_20260714T031245.h5"
)

WKT_UTM14N = (
    'PROJCS["WGS 84 / UTM zone 14N",'
    'GEOGCS["WGS 84",AUTHORITY["EPSG","4326"]],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AUTHORITY["EPSG","32614"]]'
)


# ---------------------------------------------------------------------------------------------
# Synthetic granule builder
# ---------------------------------------------------------------------------------------------


def default_arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A 4×6 grid with mask values 0/2/127, two columns of padding and three non-finite pixels.

    Valid pixels = 24 − 8 padding − 3 non-finite = 13.  Zero phase appears at (0, 1) and must
    survive; that is the Málaga case where 0 is a real measurement.
    """
    shape = (4, 6)
    phase = np.full(shape, 0.8, dtype=np.float32)
    phase[0, 1] = 0.0
    phase[1, 1] = np.nan
    phase[3, 2] = -0.5

    coherence = np.linspace(0.10, 0.95, num=shape[0] * shape[1]).reshape(shape).astype(np.float32)
    coherence[2, 2] = np.nan
    coherence[3, 2] = np.inf

    mask = np.array(
        [
            [0, 0, 2, 2, 127, 127],
            [0, 2, 2, 0, 127, 127],
            [0, 0, 2, 2, 127, 127],
            [0, 2, 2, 0, 127, 127],
        ],
        dtype=np.int8,
    )
    connected = np.array(
        [
            [1, 1, 2, 2, 0, 0],
            [1, 3, 3, 0, 0, 0],
            [1, 1, 3, 3, 0, 0],
            [1, 3, 3, 0, 0, 0],
        ],
        dtype=np.uint32,
    )
    return phase, coherence, mask, connected


def make_granule(
    directory: pathlib.Path,
    *,
    name: str = GRANULE_NAME,
    pol: str = "HH",
    omit: frozenset[str] = frozenset(),
    arrays: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None,
    coords: bool = True,
    epsg: int | None = 32614,
    epsg_from_wkt: bool = False,
    wavelength: float | None = 0.2384,
    center_frequency: float | None = None,
    times: bool = True,
    extra_pol: str | None = None,
) -> pathlib.Path:
    """Write a synthetic granule that mirrors `docs/nisar-gunw-layout.md` §2–§3.

    ``omit`` accepts ``phase``, ``coherence``, ``connected_components``, ``mask`` to build the
    missing-dataset failure cases. Everything optional can be disabled to exercise the warning
    paths; no fixture ever contains an invented value.
    """
    path = directory / name
    phase, coherence, mask, connected = arrays if arrays is not None else default_arrays()

    with h5py.File(path, "w") as f:
        group = f.require_group(f"{UNWRAPPED}/{pol}")
        if "phase" not in omit:
            group.create_dataset("unwrappedPhase", data=phase)
        if "coherence" not in omit:
            group.create_dataset("coherenceMagnitude", data=coherence)
        if "connected_components" not in omit:
            group.create_dataset("connectedComponents", data=connected)
        if "mask" not in omit:
            f.create_dataset(f"{UNWRAPPED}/mask", data=mask)

        if coords:
            x = f.create_dataset(
                f"{GRIDS}/xCoordinates", data=np.arange(phase.shape[1], dtype=np.float64) * 80.0
            )
            x.attrs["standard_name"] = "projection_x_coordinate"
            x.attrs["long_name"] = "x coordinate of projection"
            x.attrs["units"] = "m"
            y = f.create_dataset(
                f"{GRIDS}/yCoordinates",
                data=(np.arange(phase.shape[0], dtype=np.float64) * -80.0) + 500_000.0,
            )
            y.attrs["standard_name"] = "projection_y_coordinate"
            y.attrs["long_name"] = "y coordinate of projection"
            y.attrs["units"] = "m"

        if epsg_from_wkt:
            f.create_dataset(f"{GRIDS}/projection", data=WKT_UTM14N)
        elif epsg is not None:
            f.create_dataset(f"{GRIDS}/projection", data=np.int32(epsg))

        # `metadata/radarGrid/` is present as documented; wavelength is optional on purpose.
        f.create_dataset(f"{BASE}/metadata/radarGrid/numberOfLooks", data=np.int32(1))
        if wavelength is not None:
            f.create_dataset(
                f"{BASE}/metadata/radarGrid/wavelength", data=np.float64(wavelength)
            )
        if center_frequency is not None:
            f.create_dataset(
                f"{BASE}/metadata/processingInformation/parameters/frequencyA/centerFrequency",
                data=np.float64(center_frequency),
            )

        if times:
            f.create_dataset(
                f"{BASE}/identification/referenceZeroDopplerStartTime",
                data="2026-07-02T03:12:45Z",
            )
            f.create_dataset(
                f"{BASE}/identification/secondaryZeroDopplerStartTime",
                data="2026-07-14T03:12:45Z",
            )

        if extra_pol is not None:
            f.require_group(f"{UNWRAPPED}/{extra_pol}")
            if extra_pol == "VV":
                # Enough of the layout for an explicit-selection test; values are irrelevant.
                vv = f[f"{UNWRAPPED}/VV"]
                vv.create_dataset("unwrappedPhase", data=phase)
                vv.create_dataset("coherenceMagnitude", data=coherence)
                vv.create_dataset("connectedComponents", data=connected)

    return path


def run_cli(path: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the real CLI through the venv interpreter, exactly as an operator would."""
    return subprocess.run(
        [sys.executable, str(MODULE), str(path), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )


# ---------------------------------------------------------------------------------------------
# 1. Polarisation discovery
# ---------------------------------------------------------------------------------------------


def test_discovers_hh(tmp_path):
    path = make_granule(tmp_path, pol="HH")
    assert G.discover_polarisation(path) == "HH"


def test_discovers_vv(tmp_path):
    path = make_granule(tmp_path, pol="VV")
    assert G.discover_polarisation(path) == "VV"


def test_both_polarisations_is_an_error_that_names_them(tmp_path):
    path = make_granule(tmp_path, pol="HH", extra_pol="VV")
    with pytest.raises(G.GranuleError) as excinfo:
        G.discover_polarisation(path)
    message = str(excinfo.value)
    assert "ambiguous polarisation" in message
    assert "HH" in message and "VV" in message
    assert UNWRAPPED in message


def test_no_polarisation_is_an_error_that_names_what_it_found(tmp_path):
    path = tmp_path / "no_pol.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset(f"{UNWRAPPED}/mask", data=np.zeros((2, 2), dtype=np.int8))

    with pytest.raises(G.GranuleError) as excinfo:
        G.discover_polarisation(path)
    message = str(excinfo.value)
    assert "no polarisation" in message
    assert "mask" in message  # names the unexpected child
    assert "HH" in message and "VV" in message


def test_both_polarisations_without_override_makes_read_granule_fail(tmp_path):
    path = make_granule(tmp_path, pol="HH", extra_pol="VV")
    with pytest.raises(G.GranuleError, match="ambiguous polarisation"):
        G.read_granule(path)


def test_explicit_polarisation_resolves_an_ambiguous_granule_with_a_warning(tmp_path):
    path = make_granule(tmp_path, pol="HH", extra_pol="VV")
    granule = G.read_granule(path, polarisation="VV")
    assert granule.polarisation == "VV"
    assert any("explicitly requested 'VV'" in warning for warning in granule.warnings)


def test_vv_only_granule_reads_with_vv_paths(tmp_path):
    path = make_granule(tmp_path, pol="VV")
    granule = G.read_granule(path)
    assert granule.polarisation == "VV"
    assert granule.phase_rad.shape == (4, 6)
    assert G.summary(granule)["polarisation"] == "VV"


# ---------------------------------------------------------------------------------------------
# 2. Required datasets: absent means an exception with the offending path
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["phase", "coherence", "connected_components", "mask"])
def test_missing_required_dataset_raises_with_the_exact_path(tmp_path, key):
    path = make_granule(tmp_path, omit=frozenset({key}))
    expected_path = G.DATASETS[key].format(pol="HH")

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    message = str(excinfo.value)
    assert "required GUNW dataset not found" in message
    assert expected_path in message
    assert "nisar-gunw-layout.md" in message


def test_declared_fill_value_is_surfaced_not_silently_interpreted(tmp_path):
    path = make_granule(tmp_path)
    with h5py.File(path, "r+") as f:
        f[f"{UNWRAPPED}/HH/unwrappedPhase"].attrs["_FillValue"] = np.float32(-9999.0)

    granule = G.read_granule(path)
    assert any("_FillValue=-9999.0" in warning for warning in granule.warnings)
    # The reader warns; it does not turn -9999 (or anything else) into nodata without a decision.
    assert granule.phase_rad[0, 0] == np.float32(0.8)


def test_inconsistent_shapes_are_rejected_not_resampled(tmp_path):
    phase, coherence, mask, connected = default_arrays()
    arrays = (phase, coherence, mask[:, :5], connected)  # mask is one column short
    path = make_granule(tmp_path, arrays=arrays)

    with pytest.raises(G.GranuleError) as excinfo:
        G.read_granule(path)
    message = str(excinfo.value)
    assert "inconsistent shapes" in message
    assert G.DATASETS["phase"].format(pol="HH") in message
    assert G.DATASETS["mask"].format(pol="HH") in message


# ---------------------------------------------------------------------------------------------
# 3. valid_mask: padding and non-finite out, 0 and 2 kept
# ---------------------------------------------------------------------------------------------


def test_valid_mask_drops_padding_and_non_finite_but_keeps_zero_and_two(tmp_path):
    granule = G.read_granule(make_granule(tmp_path))
    valid = G.valid_mask(granule)

    assert valid.dtype == np.bool_
    assert valid.shape == granule.phase_rad.shape
    # 127 padding is gone.
    assert not valid[0, 4] and not valid[0, 5] and not valid[3, 5]
    # 0 and 2 are both data.
    assert valid[0, 0] and valid[0, 2]
    # Zero phase is a measurement, not nodata.
    assert granule.phase_rad[0, 1] == 0.0 and valid[0, 1]
    # Non-finite phase / coherence are not data.
    assert not valid[1, 1]  # NaN phase
    assert not valid[2, 2]  # NaN coherence
    assert not valid[3, 2]  # +inf coherence

    assert int(valid.sum()) == 13
    assert set(np.unique(granule.mask).tolist()) == {0, 2, 127}


def test_valid_mask_carries_unknown_flags_through(tmp_path):
    """It removes 127 and non-finite values only; unknown flags stay for a later filter."""
    phase, coherence, mask, connected = default_arrays()
    mask[0, 3] = 4  # an undocumented flag: keep it and let the caller decide
    granule = G.read_granule(make_granule(tmp_path, arrays=(phase, coherence, mask, connected)))
    assert G.valid_mask(granule)[0, 3]


# ---------------------------------------------------------------------------------------------
# 4. Phase → LOS displacement
# ---------------------------------------------------------------------------------------------


def test_phase_conversion_matches_the_formula_for_pi():
    wavelength = 0.2384
    out = G.phase_to_los_displacement_mm(np.array([np.pi]), wavelength)
    # -λ/(4π)·φ with λ in metres, converted to mm: -0.2384·1000/4 = -59.6 mm.
    assert out[0] == pytest.approx(-0.2384 * 1000.0 / 4.0, rel=0, abs=1e-12)
    assert out[0] == pytest.approx(-59.6, rel=0, abs=1e-12)


def test_phase_conversion_returns_nan_not_zero_for_invalid_pixels():
    out = G.phase_to_los_displacement_mm(
        np.array([np.pi, np.nan, np.inf, -np.inf, -np.pi, 0.0]), 0.2384
    )
    assert np.isnan(out[1]) and np.isnan(out[2]) and np.isnan(out[3])
    assert not np.any(out[1:4] == 0.0)  # explicitly: absence is never 0
    assert out[0] == pytest.approx(-59.6)
    assert out[4] == pytest.approx(59.6)
    assert out[5] == 0.0


def test_phase_conversion_sign_switch_flips_the_result():
    phase = np.array([np.pi])
    negative = G.phase_to_los_displacement_mm(phase, 0.2384, sign=-1.0)
    positive = G.phase_to_los_displacement_mm(phase, 0.2384, sign=+1.0)
    assert positive[0] == pytest.approx(-negative[0])


def test_phase_conversion_honours_a_masked_array():
    phase = np.ma.array([np.pi, np.pi], mask=[False, True])
    out = G.phase_to_los_displacement_mm(phase, 0.2384)
    assert np.isfinite(out[0])
    assert np.isnan(out[1])


@pytest.mark.parametrize(
    ("wavelength", "reason"),
    [
        # `None` is a different refusal from an implausible number: the first says "record the
        # wavelength", the second says "that value cannot be one". Pinning one reason for both
        # would let the two checks collapse into a single "bad input" path.
        (None, r"wavelength_m is None"),
        (0.0, r"wavelength_m must be finite and positive"),
        (-0.2384, r"wavelength_m must be finite and positive"),
        (np.nan, r"wavelength_m must be finite and positive"),
        (np.inf, r"wavelength_m must be finite and positive"),
    ],
)
def test_phase_conversion_refuses_a_missing_or_implausible_wavelength(wavelength, reason):
    with pytest.raises(ValueError, match=reason):
        G.phase_to_los_displacement_mm(np.array([np.pi]), wavelength)


def test_phase_conversion_refuses_a_sign_that_is_not_a_convention():
    """`sign` is a convention (+1 / -1), not a scale factor: 2.0 is refused, and says why."""
    with pytest.raises(ValueError, match=r"sign must be -1 or \+1"):
        G.phase_to_los_displacement_mm(np.array([np.pi]), 0.2384, sign=2.0)


# ---------------------------------------------------------------------------------------------
# 5. summary: real numbers, and where the wavelength came from
# ---------------------------------------------------------------------------------------------


def test_summary_reports_the_real_numbers(tmp_path):
    granule = G.read_granule(make_granule(tmp_path))
    report = G.summary(granule)
    valid = G.valid_mask(granule)

    assert report["shape"] == [4, 6]
    assert report["polarisation"] == "HH"
    assert report["epsg"] == 32614
    assert report["dates"] == {"reference": "2026-07-02", "secondary": "2026-07-14"}
    assert report["date_pair"] == ["2026-07-02", "2026-07-14"]

    assert report["valid_pixels"] == 13
    assert report["total_pixels"] == 24
    assert report["valid_fraction"] == pytest.approx(13 / 24)

    expected = np.percentile(np.asarray(granule.coherence)[valid], [5, 50, 95])
    assert report["coherence_percentiles"]["p05"] == pytest.approx(float(expected[0]))
    assert report["coherence_percentiles"]["p50"] == pytest.approx(float(expected[1]))
    assert report["coherence_percentiles"]["p95"] == pytest.approx(float(expected[2]))

    labels = np.unique(np.asarray(granule.connected_components)[valid])
    assert report["connected_component_count"] == int(np.count_nonzero(labels != 0))
    assert report["connected_component_max"] == int(labels.max())
    assert report["connected_components"]["n_labels_including_zero"] == int(labels.size)

    assert report["wavelength_m"] == pytest.approx(0.2384)
    assert report["wavelength_source"].startswith("metadata:")
    assert "radarGrid/wavelength" in report["wavelength_source"]
    assert not any("wavelength_m absent" in warning for warning in report["warnings"])


def test_summary_of_an_all_invalid_grid_has_no_imputed_numbers(tmp_path):
    phase, coherence, mask, connected = default_arrays()
    mask = np.full(mask.shape, 127, dtype=np.int8)
    granule = G.read_granule(make_granule(tmp_path, arrays=(phase, coherence, mask, connected)))
    report = G.summary(granule)

    assert report["valid_pixels"] == 0
    assert report["valid_fraction"] == 0.0
    assert report["coherence_percentiles"] == {"p05": None, "p50": None, "p95": None}
    assert report["connected_component_max"] is None
    assert report["connected_component_count"] == 0


# ---------------------------------------------------------------------------------------------
# 6. Missing optional metadata: None + warning, never a default
# ---------------------------------------------------------------------------------------------


def test_no_wavelength_metadata_yields_none_and_a_warning(tmp_path):
    path = make_granule(tmp_path, wavelength=None)
    granule = G.read_granule(path)

    assert granule.wavelength_m is None
    assert granule.wavelength_source is None
    assert any("wavelength_m absent" in warning for warning in granule.warnings)

    report = G.summary(granule)
    assert report["wavelength_m"] is None
    assert report["wavelength_source"] == "absent"


def test_wavelength_is_derived_from_center_frequency_only_with_a_recorded_warning(tmp_path):
    path = make_granule(tmp_path, wavelength=None, center_frequency=1.2575e9)
    granule = G.read_granule(path)

    assert granule.wavelength_m == pytest.approx(299_792_458.0 / 1.2575e9)
    assert granule.wavelength_source.startswith("derived:centerFrequency:")
    assert any("derived as c/f" in warning for warning in granule.warnings)


def test_missing_epsg_is_none_with_a_warning_not_a_guess(tmp_path):
    path = make_granule(tmp_path, epsg=None)
    granule = G.read_granule(path)

    assert granule.epsg is None
    assert any("epsg not found" in warning for warning in granule.warnings)
    assert G.summary(granule)["epsg"] is None


def test_epsg_can_be_parsed_from_wkt_but_is_flagged_for_verification(tmp_path):
    path = make_granule(tmp_path, epsg=None, epsg_from_wkt=True)
    granule = G.read_granule(path)

    assert granule.epsg == 32614
    assert any("parsed from CRS description" in warning for warning in granule.warnings)


def test_missing_coordinates_are_none_with_a_warning_and_no_transform(tmp_path):
    path = make_granule(tmp_path, coords=False)
    granule = G.read_granule(path)

    assert granule.x is None and granule.y is None
    assert any("x coordinate array not found" in warning for warning in granule.warnings)
    assert any("y coordinate array not found" in warning for warning in granule.warnings)


def test_wrong_length_coordinates_are_rejected_not_stretched(tmp_path):
    path = make_granule(tmp_path, coords=False)
    with h5py.File(path, "r+") as f:
        f.create_dataset(f"{GRIDS}/xCoordinates", data=np.arange(3, dtype=np.float64))
        f.create_dataset(f"{GRIDS}/yCoordinates", data=np.arange(4, dtype=np.float64))
    granule = G.read_granule(path)

    assert granule.x is None  # 3 values for 6 columns: do not resample or ffill
    assert granule.y is not None  # 4 values for 4 rows is usable
    assert any("has length 3, expected 6" in warning for warning in granule.warnings)


def test_times_fall_back_to_the_filename_only_when_metadata_is_missing(tmp_path):
    path = make_granule(tmp_path, times=False)
    granule = G.read_granule(path)

    assert granule.reference_time is not None
    assert granule.reference_time.startswith("2026-07-02")
    assert granule.secondary_time is not None
    assert granule.secondary_time.startswith("2026-07-14")
    assert any("parsed from the filename" in warning for warning in granule.warnings)


def test_coordinate_arrays_are_exposed_when_present(tmp_path):
    granule = G.read_granule(make_granule(tmp_path))
    assert granule.x is not None and granule.y is not None
    assert granule.x.shape == (6,)
    assert granule.y.shape == (4,)
    np.testing.assert_allclose(granule.x, np.arange(6) * 80.0)


# ---------------------------------------------------------------------------------------------
# 7. CLI: exit codes, machine-readable output, and the no-wavelength case
# ---------------------------------------------------------------------------------------------


def test_cli_on_a_valid_fixture_exits_zero_and_prints_a_summary(tmp_path):
    path = make_granule(tmp_path)
    result = run_cli(path)

    assert result.returncode == 0, result.stderr
    assert "NISAR L2 GUNW" in result.stdout
    assert "HH" in result.stdout
    assert "EPSG:32614" in result.stdout
    assert "0.2384" in result.stdout
    assert "2026-07-02" in result.stdout


def test_cli_json_is_machine_readable_and_truthful(tmp_path):
    path = make_granule(tmp_path)
    result = run_cli(path, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["polarisation"] == "HH"
    assert payload["epsg"] == 32614
    assert payload["valid_pixels"] == 13
    assert payload["wavelength_m"] == pytest.approx(0.2384)


def test_cli_still_summarises_without_inventing_a_wavelength(tmp_path):
    path = make_granule(tmp_path, wavelength=None)
    result = run_cli(path)

    assert result.returncode == 0, result.stderr
    assert "absent" in result.stdout
    assert "wavelength             absent" in result.stdout
    assert "wavelength_m absent" in result.stdout


def test_cli_missing_dataset_exits_non_zero_with_the_path(tmp_path):
    path = make_granule(tmp_path, omit=frozenset({"phase"}))
    result = run_cli(path)

    assert result.returncode != 0
    expected_path = G.DATASETS["phase"].format(pol="HH")
    assert "required GUNW dataset not found" in result.stderr
    assert expected_path in result.stderr
    assert "nisar-gunw-layout.md" in result.stderr


def test_cli_nonexistent_path_exits_non_zero(tmp_path):
    result = run_cli(tmp_path / "does_not_exist.h5")

    assert result.returncode != 0
    assert "granule not found" in result.stderr
    assert "nisar-gunw-layout.md" in result.stderr


def test_cli_non_hdf5_file_exits_non_zero(tmp_path):
    path = tmp_path / "not_really.h5"
    path.write_text("this is an HTML error page saved with an .h5 name")
    result = run_cli(path)

    assert result.returncode != 0
    assert "cannot open" in result.stderr
    assert "HDF5" in result.stderr
    assert "nisar-gunw-layout.md" in result.stderr


# ---------------------------------------------------------------------------------------------
# 8. Import hygiene: no h5py at import time, pure functions work without it
# ---------------------------------------------------------------------------------------------


def test_module_imports_and_pure_functions_run_even_if_h5py_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "h5py", None)
    module = importlib.reload(G)

    # A module-level `import h5py` would now be an ImportError (None in sys.modules).
    assert "h5py" not in vars(module)

    out = module.phase_to_los_displacement_mm(np.array([np.pi]), 0.2384)
    assert out[0] == pytest.approx(-59.6)

    granule = module.GunwGranule(
        phase_rad=np.array([[0.0, np.nan]], dtype=np.float32),
        coherence=np.zeros((1, 2), dtype=np.float32),
        connected_components=np.zeros((1, 2), dtype=np.uint32),
        mask=np.array([[0, 127]], dtype=np.int8),
        polarisation="HH",
    )
    assert module.valid_mask(granule).tolist() == [[True, False]]

    # Reading, unlike the pure functions, must fail with a clear GranuleError rather than leak
    # an ImportError from inside h5py.
    with pytest.raises(module.GranuleError, match="h5py is required"):
        module.read_granule("whatever.h5")
