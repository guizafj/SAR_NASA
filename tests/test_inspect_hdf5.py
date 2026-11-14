"""Hermetic tests for `pipeline/ingest/inspect_hdf5.py`.

These build tiny synthetic HDF5 files in `tmp_path`; they never download a granule and never
read data chunks from a real product. The fixture mimics the provisional NISAR GUNW layout,
including a deliberately unrelated `amplitude` dataset that must not be called phase.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # running pytest from another cwd
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "inspect_hdf5", ROOT / "pipeline" / "ingest" / "inspect_hdf5.py"
)
assert _SPEC is not None and _SPEC.loader is not None
I = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(I)


def _build_gunw(path: pathlib.Path) -> pathlib.Path:
    """A plausible-enough NISAR GUNW layout, small enough to build in milliseconds."""
    with h5py.File(path, "w") as f:
        grid = f.create_group("/science/LSAR/GUNW/grids/frequencyA")
        unwrapped = grid.create_group("unwrappedInterferogram/HH")

        phase = unwrapped.create_dataset(
            "unwrappedPhase",
            data=np.zeros((3, 4), dtype=np.float32),
            chunks=(2, 2),
            compression="gzip",
        )
        phase.attrs["units"] = "radian"
        phase.attrs["_FillValue"] = np.float32(-999.0)
        phase.attrs["long_name"] = "unwrapped phase"
        phase.attrs["description"] = "phase after unwrapping"
        phase.attrs["scale_factor"] = np.float32(1.0)
        phase.attrs["add_offset"] = np.float32(0.0)

        coh = unwrapped.create_dataset(
            "coherenceMagnitude", data=np.full((3, 4), 0.8, dtype=np.float32)
        )
        coh.attrs["units"] = "1"
        coh.attrs["_FillValue"] = np.float32(0.0)

        cc = unwrapped.create_dataset(
            "connectedComponents", data=np.ones((3, 4), dtype=np.int32)
        )
        cc.attrs["units"] = "1"

        # Must stay unlabelled: it lives under unwrappedInterferogram but is not phase.
        unwrapped.create_dataset("amplitude", data=np.ones((3, 4), dtype=np.float32))

        geo = grid.create_group("geolocationGrid")
        geo.create_dataset("xCoordinates", data=np.linspace(-3.0, 0.0, 4))
        geo.create_dataset("yCoordinates", data=np.linspace(40.0, 41.0, 3))
        projection = geo.create_dataset("projection", data=np.array(32614, dtype=np.int32))
        projection.attrs["long_name"] = "UTM zone/CRS"

        metadata = f.create_group("/science/LSAR/GUNW/metadata")
        orbit = metadata.create_group("orbit")
        orbit.create_dataset("position", data=np.zeros((2, 3), dtype=np.float64))
        ident = f.create_group("/science/LSAR/GUNW/identification")
        ident.create_dataset("missionId", data=np.bytes_("NISAR"))
    return path


@pytest.fixture()
def gunw_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return _build_gunw(tmp_path / "NISAR_GUNW_fixture.h5")


def _datasets_by_path(report: dict) -> dict[str, dict]:
    return {dataset["path"]: dataset for dataset in report["datasets"]}


# ---------------------------------------------------------------------------------------------
# 1. Tree, shapes, dtypes, attributes
# ---------------------------------------------------------------------------------------------


def test_tree_is_enumerated_with_shapes_dtypes_and_attributes(gunw_file: pathlib.Path) -> None:
    report = I.inspect_file(gunw_file)
    assert report["ok"], report["error"]
    assert report["error"] is None
    assert "/science/LSAR/GUNW/grids/frequencyA" in report["groups"]
    assert "/science/LSAR/GUNW/metadata/orbit" in report["groups"]

    by_path = _datasets_by_path(report)
    phase = by_path[
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase"
    ]
    assert phase["shape"] == [3, 4]
    assert phase["dtype"] == "float32"
    assert phase["chunks"] == [2, 2]
    assert phase["compression"] == "gzip"
    assert phase["units"] == "radian"
    assert phase["fill_value"] == -999.0
    assert phase["long_name"] == "unwrapped phase"
    assert phase["description"] == "phase after unwrapping"
    assert phase["scale_factor"] == 1.0
    assert phase["add_offset"] == 0.0

    projection = by_path["/science/LSAR/GUNW/grids/frequencyA/geolocationGrid/projection"]
    assert projection["shape"] == []
    assert projection["dtype"] == "int32"
    assert projection["long_name"] == "UTM zone/CRS"

    assert report["summary"]["dataset_count"] == len(report["datasets"])
    assert report["summary"]["logical_bytes"] == sum(
        dataset["logical_bytes"] for dataset in report["datasets"]
    )
    assert report["summary"]["logical_bytes"] > 0


# ---------------------------------------------------------------------------------------------
# 2. Candidate heuristics
# ---------------------------------------------------------------------------------------------


def test_candidates_label_phase_coherence_connected_components_and_geolocation() -> None:
    assert I.classify_dataset(
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase"
    ) == ("phase", "unwrapped phase")
    assert I.classify_dataset(
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/coherenceMagnitude"
    ) == ("coherence", "coherence")
    assert I.classify_dataset(
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/connectedComponents"
    ) == ("connected-components", "connected components")
    assert I.classify_dataset(
        "/science/LSAR/GUNW/grids/frequencyA/geolocationGrid/xCoordinates"
    ) == ("geolocation", "geolocation")
    assert I.classify_dataset(
        "/science/LSAR/GUNW/grids/frequencyA/geolocationGrid/projection"
    ) == ("geolocation", "geolocation")
    assert I.classify_dataset("/science/LSAR/GUNW/metadata/orbit/position") == (
        "metadata",
        "metadata (identification/orbit/attitude)",
    )


def test_unrelated_dataset_is_not_labelled_as_phase(gunw_file: pathlib.Path) -> None:
    by_path = _datasets_by_path(I.inspect_file(gunw_file))
    amplitude = by_path[
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/amplitude"
    ]
    assert amplitude["candidate"] is None
    assert amplitude["reason"] is None
    assert I.classify_dataset(
        "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/amplitude"
    ) == (None, None)


# ---------------------------------------------------------------------------------------------
# 3. Human and JSON reports agree
# ---------------------------------------------------------------------------------------------


def test_json_round_trips_and_has_the_same_dataset_count(
    gunw_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert I.main([str(gunw_file)]) == 0
    human = capsys.readouterr().out
    human_count = sum(1 for line in human.splitlines() if line.startswith("  DATASET  "))

    assert I.main([str(gunw_file), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["datasets"]) == human_count
    assert payload["summary"]["dataset_count"] == human_count
    assert {dataset["path"] for dataset in payload["datasets"]} == {
        dataset["path"] for dataset in I.inspect_file(gunw_file)["datasets"]
    }
    phase = next(dataset for dataset in payload["datasets"] if dataset["candidate"] == "phase")
    assert phase["units"] == "radian"
    assert phase["fill_value"] == -999.0


# ---------------------------------------------------------------------------------------------
# 4. Product expectations are advisory
# ---------------------------------------------------------------------------------------------


def test_product_expectations_report_present_and_absent_without_failing(
    gunw_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The fixture intentionally has no metadata/processingInformation and no metadata/attitude.
    assert I.main([str(gunw_file), "--product", "nisar-gunw"]) == 0
    output = capsys.readouterr().out
    assert "[PASS] group /science/LSAR/GUNW/grids/frequencyA" in output
    assert "[absent] group /science/LSAR/GUNW/metadata/processingInformation" in output
    assert "[PASS] dataset phase" in output

    # A product that shares almost nothing with the fixture is still a successful discovery run.
    assert I.main([str(gunw_file), "--product", "opera-disp-s1"]) == 0
    opera_output = capsys.readouterr().out
    assert "[absent] group /displacement" in opera_output


# ---------------------------------------------------------------------------------------------
# 5. Directory mode
# ---------------------------------------------------------------------------------------------


def test_directory_argument_inspects_multiple_files(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "granules"
    directory.mkdir()
    _build_gunw(directory / "a.h5")
    (directory / "nested").mkdir()
    _build_gunw(directory / "nested" / "b.hdf5")
    (directory / "notes.txt").write_text("not an HDF5 file")

    assert I.main([str(directory)]) == 0
    output = capsys.readouterr().out
    assert output.count("=== FILE ") == 2
    assert "=== COMBINED SUMMARY ===" in output
    assert "files=2 ok=2 errors=0" in output

    assert I.main([str(directory), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "directory"
    assert len(payload["files"]) == 2
    assert all(item["ok"] for item in payload["files"])
    assert payload["combined"]["file_count"] == 2
    assert payload["combined"]["dataset_count"] == sum(
        item["summary"]["dataset_count"] for item in payload["files"]
    )


# ---------------------------------------------------------------------------------------------
# 6. Graceful errors
# ---------------------------------------------------------------------------------------------


def test_non_hdf5_file_gives_a_clear_error_not_a_traceback(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "not_hdf5.h5"
    bad.write_text("plain text pretending to be an HDF5 granule\n")

    assert I.main([str(bad)]) != 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "not an HDF5 file" in combined
    assert "Traceback" not in combined


def test_missing_path_and_missing_science_group_are_clear_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert I.main([str(tmp_path / "does-not-exist.h5")]) == 2
    assert "path does not exist" in capsys.readouterr().err

    no_science = tmp_path / "no_science.h5"
    with h5py.File(no_science, "w") as f:
        f.create_dataset("random", data=np.arange(3))
    assert I.main([str(no_science)]) != 0
    captured = capsys.readouterr()
    assert "no /science group" in captured.out
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------------------------
# 7. Depth limit
# ---------------------------------------------------------------------------------------------


def test_max_depth_limits_traversal(
    gunw_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shallow = I.inspect_file(gunw_file, max_depth=1)
    assert shallow["ok"]
    assert "/science" in shallow["groups"]
    assert "/science/LSAR" not in shallow["groups"]
    assert shallow["datasets"] == []

    deeper = I.inspect_file(gunw_file, max_depth=4)
    assert "/science/LSAR/GUNW" in deeper["groups"]
    assert "/science/LSAR/GUNW/grids/frequencyA" not in deeper["groups"]
    deep_path = "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase"
    assert deep_path not in _datasets_by_path(deeper)

    assert I.main([str(gunw_file), "--max-depth", "1"]) == 0
    output = capsys.readouterr().out
    assert "DATASET" not in output
    assert "unwrappedPhase" not in output


# ---------------------------------------------------------------------------------------------
# 8. Lazy by construction
# ---------------------------------------------------------------------------------------------


def test_no_dataset_contents_are_read_when_sample_rows_is_zero(
    gunw_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def forbidden_getitem(self: h5py.Dataset, key: object) -> None:
        calls.append(self.name)
        raise AssertionError(f"dataset contents were read: {self.name}[{key!r}]")

    monkeypatch.setattr(h5py.Dataset, "__getitem__", forbidden_getitem)

    report = I.inspect_file(gunw_file, sample_rows=0)
    assert report["ok"], report["error"]
    assert report["datasets"]
    assert all("sample" not in dataset for dataset in report["datasets"])
    assert calls == []
