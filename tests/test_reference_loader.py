"""The reference loader must do what `docs/handoff-mock.md` says — no more, no less.

`examples/reference_loader.py` exists so the teammate writing `src/data/loaders.py` can copy working
code instead of translating prose. These tests pin the two things that make that copying safe:

* the *positive* path — every committed case loads, the map payload is one row per published cell
  with exactly the seven documented keys, each filter only removes rows, the chart series is the
  stored `aoi` series verbatim, and the KPI cards reproduce `meta.stats`;
* the *fail-closed* path — a tampered copy in `tmp_path` (broken `delta_change`, an undeclared
  enum, a non-zero `val_t1`, a mock without a mock provenance) raises `LoaderError` naming the
  field, and the module imports without Streamlit or pydeck.

Hermetic and offline: only the committed artefacts under `data/processed/` are read; nothing is
written outside pytest's `tmp_path`. The module is skipped with a clear reason when no cases exist.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
PROCESSED = ROOT / "data/processed"

for _path in (ROOT, EXAMPLES):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import reference_loader as R  # noqa: E402  (path set above, on purpose)
from src.domain import contracts as C  # noqa: E402

REQUIRED_FILES = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")

MAP_KEYS = {"lat", "lon", "color_value", "height", "category", "risk", "cell_id"}
KPI_KEYS = {
    "cells",
    "median_velocity_mm_yr",
    "p05",
    "p95",
    "spread",
    "dates",
    "window_days",
    "product",
    "provenance_id",
}

#: COHERENCE filter used by the reduction test. 0.7 sits inside every case's published range
#: (0.35–0.95), so it removes rows in all three cases without matching nothing.
COHERENCE_CUT = 0.7


def _case_dirs() -> dict[str, pathlib.Path]:
    """A case is a directory carrying all four artefacts — the handoff's own discovery rule."""
    if not PROCESSED.is_dir():
        return {}
    return {
        directory.name: directory
        for directory in sorted(PROCESSED.iterdir())
        if directory.is_dir()
        and not directory.name.startswith(("_", "."))
        and all((directory / name).is_file() for name in REQUIRED_FILES)
    }


CASES = _case_dirs()

pytestmark = pytest.mark.skipif(
    not CASES,
    reason=(
        "data/processed/ has no case artefacts — generate them with "
        "`./scripts/python.sh pipeline/generate_mock.py`; this test reads the committed artefacts, "
        "it cannot synthesise them"
    ),
)


@pytest.fixture(scope="module")
def loaded() -> dict[str, R.Case]:
    """Load each committed case once; the loader is deterministic and the fixtures are read-only."""
    return {case_id: R.load_case(case_id) for case_id in sorted(CASES)}


# ---------------------------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_case_loads(loaded: dict[str, R.Case], case_id: str) -> None:
    case = loaded[case_id]
    assert case.case_id == case_id
    assert case.path == CASES[case_id]
    assert len(case.grid) > 0
    assert len(case.grid) == case.meta["stats"]["n_cells"]
    assert case.meta["case_id"] == case_id
    assert case.meta["schema_version"] == C.SCHEMA_VERSION
    assert case.context["type"] == "FeatureCollection"
    assert (case.series["cell_id"].astype(str) == "aoi").any()


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_grid_frame_returns_documented_dtypes(loaded: dict[str, R.Case], case_id: str) -> None:
    frame = R.grid_frame(loaded[case_id])
    for column in ("lat", "lon", "val_t1", "val_t2", "delta_change", "velocity_mm_yr", "coherence"):
        assert str(frame[column].dtype) == "float64", f"{case_id}.{column} must be float64"
    for column in (
        "cell_id",
        "land_use",
        "risk_level",
        "case_id",
        "sensor",
        "product",
        "unit",
        "velocity_unit",
        "source",
        "acquisition_date",
        "provenance_id",
    ):
        # handoff §2: pandas 3 reports `str`, pandas 2 `object` — assert "string", not the object.
        assert str(frame[column].dtype) in {"str", "string", "object"}, (
            f"{case_id}.{column} must be a string column, got {frame[column].dtype}"
        )
    assert len(frame) == len(loaded[case_id].grid)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_map_payload_is_one_row_per_published_cell_with_seven_keys(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    case = loaded[case_id]
    rows = R.map_payload(case)
    grid = R.grid_frame(case)

    assert len(rows) == len(grid) == case.meta["stats"]["n_cells"]
    assert {row["cell_id"] for row in rows} == set(grid["cell_id"].astype(str))
    for row in rows:
        assert set(row) == MAP_KEYS, f"{case_id}: unexpected map keys {sorted(set(row) ^ MAP_KEYS)}"
        assert isinstance(row["cell_id"], str)
        assert isinstance(row["category"], str)
        assert isinstance(row["risk"], str)
        for key in ("lat", "lon", "color_value", "height"):
            assert isinstance(row[key], float)
            assert math.isfinite(row[key])


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_map_payload_binds_the_stored_columns(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    """Colour is the stored rate, height is the stored cumulative change — never derived here."""
    case = loaded[case_id]
    stored = R.grid_frame(case).set_index("cell_id")

    for row in R.map_payload(case):
        source = stored.loc[row["cell_id"]]
        assert row["lat"] == source["lat"]
        assert row["lon"] == source["lon"]
        assert row["color_value"] == source["velocity_mm_yr"], (
            f"{case_id}:{row['cell_id']} colour must be the stored velocity_mm_yr, not "
            f"delta_change / T"
        )
        assert row["height"] == source["delta_change"], (
            f"{case_id}:{row['cell_id']} height must be the stored delta_change, not velocity * T"
        )
        assert row["category"] == source["land_use"]
        assert row["risk"] == source["risk_level"]


# ---------------------------------------------------------------------------------------------
# Filters: they remove rows, never fill zeros; nothing left is an error, not []
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_coherence_filter_removes_rows(loaded: dict[str, R.Case], case_id: str) -> None:
    case = loaded[case_id]
    grid = R.grid_frame(case)
    expected_ids = set(grid.loc[grid["coherence"] >= COHERENCE_CUT, "cell_id"].astype(str))

    rows = R.map_payload(case, coherence_min=COHERENCE_CUT)

    assert len(rows) == len(expected_ids)
    assert len(rows) < len(grid), f"{case_id}: coherence_min={COHERENCE_CUT} should reduce the count"
    assert {row["cell_id"] for row in rows} == expected_ids


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_land_use_filter_removes_rows(loaded: dict[str, R.Case], case_id: str) -> None:
    case = loaded[case_id]
    grid = R.grid_frame(case)
    counts = grid["land_use"].astype(str).value_counts()
    if len(counts) < 2:
        pytest.skip(f"{case_id}: only one land-use class is published, so no filter can reduce it")
    target = str(counts.index[-1])  # least common published class
    expected_ids = set(grid.loc[grid["land_use"].astype(str) == target, "cell_id"].astype(str))

    rows = R.map_payload(case, land_use=target)

    assert len(rows) == len(expected_ids) < len(grid)
    assert {row["cell_id"] for row in rows} == expected_ids
    assert all(row["category"] == target for row in rows)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_land_use_filter_accepts_a_list(loaded: dict[str, R.Case], case_id: str) -> None:
    case = loaded[case_id]
    grid = R.grid_frame(case)
    counts = grid["land_use"].astype(str).value_counts()
    if len(counts) < 2:
        pytest.skip(f"{case_id}: only one land-use class is published")
    wanted = [str(counts.index[-1]), str(counts.index[-2])]
    expected = int(grid["land_use"].astype(str).isin(wanted).sum())
    assert len(R.map_payload(case, land_use=wanted)) == expected


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_risk_filter_removes_rows(loaded: dict[str, R.Case], case_id: str) -> None:
    case = loaded[case_id]
    grid = R.grid_frame(case)
    counts = grid["risk_level"].astype(str).value_counts()
    if len(counts) < 2:
        pytest.skip(f"{case_id}: only one risk band is published, so no filter can reduce it")
    target = str(counts.index[-1])  # rarest published band
    expected_ids = set(grid.loc[grid["risk_level"].astype(str) == target, "cell_id"].astype(str))

    rows = R.map_payload(case, risk_level=target)

    assert len(rows) == len(expected_ids) < len(grid)
    assert {row["cell_id"] for row in rows} == expected_ids
    assert all(row["risk"] == target for row in rows)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_coherence_filter_that_matches_nothing_raises(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    case = loaded[case_id]
    maximum = float(R.grid_frame(case)["coherence"].max())
    if maximum >= 0.99999:
        pytest.skip(f"{case_id}: a cell reaches coherence 1.0, so 0.99999 cannot match nothing")
    with pytest.raises(R.LoaderError) as excinfo:
        R.map_payload(case, coherence_min=0.99999)
    message = str(excinfo.value)
    assert case_id in message and "coherence_min" in message
    assert "matched none" in message


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_declared_but_absent_land_use_raises(loaded: dict[str, R.Case], case_id: str) -> None:
    """`water` is a declared class that the grid never publishes: cells are absent, not relabelled."""
    case = loaded[case_id]
    assert "water" in C.LAND_USE
    assert "water" not in set(R.grid_frame(case)["land_use"].astype(str))
    with pytest.raises(R.LoaderError) as excinfo:
        R.map_payload(case, land_use="water")
    message = str(excinfo.value)
    assert case_id in message and "land_use" in message
    assert "matched none" in message


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_risk_filter_that_matches_nothing_raises(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    case = loaded[case_id]
    published = set(R.grid_frame(case)["risk_level"].astype(str))
    absent = [level for level in C.RISK_LEVELS if level not in published]
    if not absent:
        pytest.skip(f"{case_id}: every risk band is published, so no band can match nothing")
    with pytest.raises(R.LoaderError) as excinfo:
        R.map_payload(case, risk_level=absent[0])
    message = str(excinfo.value)
    assert case_id in message and "risk_level" in message and "matched none" in message


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_undeclared_filter_value_is_refused(loaded: dict[str, R.Case], case_id: str) -> None:
    case = loaded[case_id]
    with pytest.raises(R.LoaderError) as excinfo:
        R.map_payload(case, land_use="marsh")  # not in contracts.LAND_USE
    message = str(excinfo.value)
    assert case_id in message and "land_use" in message and "undeclared" in message


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_out_of_range_coherence_filter_is_refused(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    case = loaded[case_id]
    with pytest.raises(R.LoaderError) as excinfo:
        R.map_payload(case, coherence_min=1.5)
    assert "coherence_min" in str(excinfo.value)


# ---------------------------------------------------------------------------------------------
# Chart series: the stored aoi rows, and the mean-vs-median distinction the handoff insists on
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_aoi_series_matches_the_csv_rows_exactly(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    raw = pd.read_csv(CASES[case_id] / "timeseries.csv")
    aoi = raw.loc[raw["cell_id"].astype(str) == "aoi"].sort_values("date")

    dates, values = R.aoi_series(loaded[case_id])

    assert dates == aoi["date"].astype(str).tolist()
    assert values == aoi["value"].astype(float).tolist(), (
        f"{case_id}: the chart must plot the stored aoi values, not a resampled or smoothed "
        f"version of them"
    )


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_aoi_endpoint_is_the_mean_delta_change_not_the_median_times_t(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    """Handoff §3c: the chart is the *mean* aggregate; the KPI card is the *median*. Both right."""
    case = loaded[case_id]
    grid = R.grid_frame(case)
    dates, values = R.aoi_series(case)
    window_years = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days / 365.25

    mean_delta = float(grid["delta_change"].mean())
    median_velocity = float(case.meta["stats"]["median_velocity_mm_yr"])

    # The aoi endpoint is the mean cumulative change over the published cells. The CSV stores six
    # decimals, so 1e-5 mm is the stored precision, not a fudge factor.
    assert values[-1] == pytest.approx(mean_delta, abs=1e-5)

    # It is deliberately NOT median velocity x T. Treating that difference as a bug is the mistake
    # the handoff warns about; both numbers are correct and answer different questions.
    assert abs(values[-1] - median_velocity * window_years) > 1e-4


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_aoi_series_starts_at_zero_and_uses_meta_dates(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    case = loaded[case_id]
    dates, values = R.aoi_series(case)
    assert dates == list(case.meta["dates"])
    assert values[0] == pytest.approx(0.0, abs=1e-12)
    assert math.isfinite(values[-1])


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_aoi_series_refuses_unit_conversion(loaded: dict[str, R.Case], case_id: str) -> None:
    with pytest.raises(R.LoaderError) as excinfo:
        R.aoi_series(loaded[case_id], chart_unit="cm")
    message = str(excinfo.value)
    assert case_id in message and "chart_unit" in message


# ---------------------------------------------------------------------------------------------
# KPI cards reproduce meta.stats verbatim
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_kpis_are_copied_from_meta_stats(loaded: dict[str, R.Case], case_id: str) -> None:
    case = loaded[case_id]
    stats = case.meta["stats"]
    cards = R.kpis(case)

    expected_keys = set(KPI_KEYS)
    for optional in ("coverage_fraction", "detection_limit_mm_yr"):
        if optional in stats:
            expected_keys.add(optional)
    assert set(cards) == expected_keys

    assert cards["cells"] == stats["n_cells"] == len(R.grid_frame(case))
    assert cards["median_velocity_mm_yr"] == stats["median_velocity_mm_yr"]
    assert cards["p05"] == stats["p05_velocity_mm_yr"]
    assert cards["p95"] == stats["p95_velocity_mm_yr"]
    # ``spread`` is the published statistic when it exists, otherwise the stated derivation.
    assert cards["spread"] == stats.get(
        "spread_mm_yr", stats["p95_velocity_mm_yr"] - stats["p05_velocity_mm_yr"]
    )
    assert cards["dates"] == list(case.meta["dates"])
    assert cards["window_days"] == (
        dt.date.fromisoformat(case.meta["dates"][-1])
        - dt.date.fromisoformat(case.meta["dates"][0])
    ).days
    assert cards["window_days"] == 132  # handoff §1: all three cases span 12 dates / 132 days
    assert cards["product"] == case.meta["product"] == "mock"
    assert cards["provenance_id"] == case.meta["provenance"]["provenance_id"]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_kpis_only_report_coverage_fraction_when_published(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    case = loaded[case_id]
    stats = case.meta["stats"]
    cards = R.kpis(case)
    if "coverage_fraction" in stats:
        assert cards["coverage_fraction"] == stats["coverage_fraction"]
    else:
        assert "coverage_fraction" not in cards

    # The branch must exist for the real-pipeline cases that do publish it.
    augmented = dataclasses.replace(
        case, meta={**case.meta, "stats": {**stats, "coverage_fraction": 0.8125}}
    )
    assert R.kpis(augmented)["coverage_fraction"] == 0.8125


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_kpis_only_report_the_detection_limit_when_published(
    loaded: dict[str, R.Case], case_id: str
) -> None:
    case = loaded[case_id]
    stats = case.meta["stats"]
    cards = R.kpis(case)
    if "detection_limit_mm_yr" in stats:
        assert cards["detection_limit_mm_yr"] == stats["detection_limit_mm_yr"]
    else:
        assert "detection_limit_mm_yr" not in cards

    augmented = dataclasses.replace(
        case, meta={**case.meta, "stats": {**stats, "detection_limit_mm_yr": 2.25}}
    )
    assert R.kpis(augmented)["detection_limit_mm_yr"] == 2.25


def test_malaga_null_result_publishes_its_detection_floor(loaded: dict[str, R.Case]) -> None:
    """Málaga's null result must render "below the detection floor", not as a missing card."""
    cards = R.kpis(loaded["malaga"])
    stats = loaded["malaga"].meta["stats"]
    assert stats["detection_limit_mm_yr"] == 1.5
    assert cards["detection_limit_mm_yr"] == 1.5
    assert abs(cards["median_velocity_mm_yr"]) < 5.0, "this case is the declared null result"


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_kpis_are_not_recomputed_from_the_grid(loaded: dict[str, R.Case], case_id: str) -> None:
    """Sabotage the grid: the cards must not move, because they come from meta.stats."""
    case = loaded[case_id]
    baseline = R.kpis(case)
    broken = case.grid.copy()
    broken["velocity_mm_yr"] = broken["velocity_mm_yr"] * 10.0
    assert R.kpis(dataclasses.replace(case, grid=broken)) == baseline


# ---------------------------------------------------------------------------------------------
# Fail closed: a tampered copy must raise, never load
# ---------------------------------------------------------------------------------------------


def _copy_case(tmp_path: pathlib.Path) -> tuple[str, pathlib.Path]:
    """Copy one committed case into a temp `processed/` root so it can be sabotaged."""
    case_id = sorted(CASES)[0]
    root = tmp_path / "processed"
    shutil.copytree(CASES[case_id], root / case_id)
    return case_id, root


def _edit_grid(root: pathlib.Path, case_id: str, mutate) -> None:
    path = root / case_id / "nisar_grid.parquet"
    frame = pd.read_parquet(path)
    mutate(frame)
    frame.to_parquet(path, index=False)


def _edit_meta(root: pathlib.Path, case_id: str, mutate) -> None:
    path = root / case_id / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    mutate(meta)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def test_loader_refuses_broken_delta_change(tmp_path: pathlib.Path) -> None:
    case_id, root = _copy_case(tmp_path)
    _edit_grid(
        root,
        case_id,
        lambda frame: frame.__setitem__("delta_change", frame["delta_change"] + 5.0),
    )

    with pytest.raises(R.LoaderError) as excinfo:
        R.load_case(case_id, processed=root)

    message = str(excinfo.value)
    assert case_id in message and "delta_change" in message
    assert "val_t2 - val_t1" in message


def test_map_height_carries_the_stored_delta_change_not_delta_over_t(
    tmp_path: pathlib.Path,
) -> None:
    """Structural no-recompute proof: sabotage ``delta_change`` so ``delta/T != velocity``.

    The committed artefacts satisfy ``delta_change / T == velocity_mm_yr`` to ~9e-13, so a loader
    that substituted ``delta/T`` for the colour would pass any tolerance-based test. Here the
    stored change is doubled (and ``val_t2`` moved with it, keeping the contract identity) and
    the payload must still carry the mutated ``delta_change`` and the untouched stored
    ``velocity_mm_yr`` **exactly**; a substitution is now visible by 100%, not by 13 digits.
    """
    case_id, root = _copy_case(tmp_path)

    def mutate(frame: pd.DataFrame) -> None:
        frame["delta_change"] = frame["delta_change"] * 2.0
        frame["val_t2"] = frame["val_t1"] + frame["delta_change"]

    _edit_grid(root, case_id, mutate)

    case = R.load_case(case_id, processed=root)
    stored = R.grid_frame(case).set_index("cell_id")
    rows = R.map_payload(case)
    assert len(rows) == len(stored)

    for row in rows:
        source = stored.loc[row["cell_id"]]
        assert row["height"] == source["delta_change"], "height must be the stored cumulative mm"
        assert row["color_value"] == source["velocity_mm_yr"], "colour must be the stored mm/yr"

    dates = case.meta["dates"]
    window_years = (
        dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])
    ).days / 365.25
    strongest = max(rows, key=lambda row: abs(row["color_value"]))
    implied_ratio = abs(strongest["height"] / window_years) / abs(strongest["color_value"])
    assert implied_ratio > 1.5, (
        "the sabotage must make delta/T disagree with velocity, or it proves nothing: "
        f"ratio {implied_ratio:.2f}"
    )


def test_loader_refuses_undeclared_enum(tmp_path: pathlib.Path) -> None:
    case_id, root = _copy_case(tmp_path)

    def mutate(frame: pd.DataFrame) -> None:
        frame.loc[frame.index[0], "land_use"] = "marsh"

    _edit_grid(root, case_id, mutate)

    with pytest.raises(R.LoaderError) as excinfo:
        R.load_case(case_id, processed=root)

    message = str(excinfo.value)
    assert case_id in message and "land_use" in message and "marsh" in message


def test_loader_refuses_nonzero_val_t1(tmp_path: pathlib.Path) -> None:
    case_id, root = _copy_case(tmp_path)

    def mutate(frame: pd.DataFrame) -> None:
        # shift both endpoints so the delta arithmetic still holds; only val_t1 != 0 is broken
        frame["val_t1"] = frame["val_t1"] + 1.5
        frame["val_t2"] = frame["val_t2"] + 1.5

    _edit_grid(root, case_id, mutate)

    with pytest.raises(R.LoaderError) as excinfo:
        R.load_case(case_id, processed=root)

    message = str(excinfo.value)
    assert case_id in message and "val_t1" in message


def test_loader_refuses_coherence_below_the_profile(tmp_path: pathlib.Path) -> None:
    case_id, root = _copy_case(tmp_path)

    def mutate(frame: pd.DataFrame) -> None:
        frame.loc[frame.index[0], "coherence"] = 0.01

    _edit_grid(root, case_id, mutate)

    with pytest.raises(R.LoaderError) as excinfo:
        R.load_case(case_id, processed=root)

    message = str(excinfo.value)
    assert case_id in message and "coherence" in message


def test_loader_refuses_mock_without_mock_provenance(tmp_path: pathlib.Path) -> None:
    case_id, root = _copy_case(tmp_path)

    def mutate(meta: dict) -> None:
        assert meta["product"] == "mock"
        meta["provenance"]["source"] = "ASF DAAC"

    _edit_meta(root, case_id, mutate)

    with pytest.raises(R.LoaderError) as excinfo:
        R.load_case(case_id, processed=root)

    message = str(excinfo.value)
    assert case_id in message and "provenance.source" in message


def test_loader_refuses_a_missing_case(tmp_path: pathlib.Path) -> None:
    with pytest.raises(R.LoaderError) as excinfo:
        R.load_case("no-such-case", processed=tmp_path)
    assert "no-such-case" in str(excinfo.value)


def test_grid_frame_refuses_a_wrong_dtype(loaded: dict[str, R.Case]) -> None:
    case_id = sorted(CASES)[0]
    case = loaded[case_id]
    broken = case.grid.copy()
    broken["velocity_mm_yr"] = broken["velocity_mm_yr"].astype("int64")

    with pytest.raises(R.LoaderError) as excinfo:
        R.grid_frame(dataclasses.replace(case, grid=broken))

    message = str(excinfo.value)
    assert case_id in message and "velocity_mm_yr" in message and "float64" in message



# ---------------------------------------------------------------------------------------------
# Location independence: the loader takes no guess about its own directory
# ---------------------------------------------------------------------------------------------


def _load_module_from(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_default_root_is_the_callers_cwd_not_the_module_location(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``processed`` defaults to ``<cwd>/data/processed``; the module's own path is irrelevant."""
    case_id = sorted(CASES)[0]
    root = tmp_path / "checkout" / "data" / "processed"
    root.mkdir(parents=True)
    shutil.copytree(CASES[case_id], root / case_id)

    monkeypatch.chdir(tmp_path / "checkout")
    case = R.load_case(case_id)
    assert case.path == (root / case_id).resolve()
    assert pathlib.Path(R.__file__).resolve().parent == EXAMPLES

    monkeypatch.chdir(tmp_path)  # one directory off: the error names the path it actually tried
    with pytest.raises(R.LoaderError) as excinfo:
        R.load_case(case_id)
    assert str((tmp_path / "data" / "processed" / case_id).resolve()) in str(excinfo.value)


def test_a_copy_under_src_data_still_loads_the_repo_data_processed(
    tmp_path: pathlib.Path,
) -> None:
    """Finding 1: a copy at ``src/data/loaders.py`` must not resolve ``data/processed`` under src.

    The copy is imported from its new location but the caller's cwd is still the repo root, so
    ``load_case`` must find the same artefacts the ``examples/`` module finds.
    """
    copy = tmp_path / "src" / "data" / "loaders.py"
    copy.parent.mkdir(parents=True)
    shutil.copy(EXAMPLES / "reference_loader.py", copy)

    copied = _load_module_from(copy, "reference_loader_copied")
    case_id = sorted(CASES)[0]
    case = copied.load_case(case_id)
    assert case.path == CASES[case_id]
    assert len(copied.map_payload(case)) == len(case.grid)


def test_a_copied_module_loads_its_own_checkout_and_never_touches_sys_path(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end copy: loader at ``src/data/loaders.py``, artefacts at ``data/processed/``.

    The child interpreter runs with cwd at the temp checkout, so this is the layout the teammate
    will have. It must load ``<tree>/data/processed/<case>`` (not ``<tree>/src/data/processed``)
    and must leave ``sys.path`` untouched.
    """
    tree = tmp_path / "repo"
    (tree / "src" / "data").mkdir(parents=True)
    (tree / "src" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(ROOT / "src" / "domain", tree / "src" / "domain")
    shutil.copy(EXAMPLES / "reference_loader.py", tree / "src" / "data" / "loaders.py")
    case_id = sorted(CASES)[0]
    shutil.copytree(CASES[case_id], tree / "data" / "processed" / case_id)

    script = textwrap.dedent(
        f"""
        import importlib.util
        import pathlib
        import sys

        before = list(sys.path)
        module_path = pathlib.Path({str(tree / 'src/data/loaders.py')!r})
        spec = importlib.util.spec_from_file_location("copied_loader", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        assert sys.path == before, f"importing the loader edited sys.path: {{sys.path}}"

        case = module.load_case({case_id!r})
        expected = pathlib.Path({str(tree / 'data/processed' / case_id)!r})
        assert case.path == expected, f"{{case.path}} != {{expected}}"
        assert len(case.grid) > 0
        print("cwd-resolution-ok")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=tree, capture_output=True, text=True
    )
    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    assert "cwd-resolution-ok" in completed.stdout

# ---------------------------------------------------------------------------------------------
# No UI dependencies: the loader must import in a Streamlit-less environment
# ---------------------------------------------------------------------------------------------


def test_module_imports_without_streamlit_or_pydeck() -> None:
    """Prove it in a fresh interpreter that actively blocks both UI packages."""
    script = textwrap.dedent(
        """
        import importlib.util
        import pathlib
        import sys

        class BlockUI:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {"streamlit", "pydeck"}:
                    raise ImportError(f"{name} is blocked: the loader must not need it")
                return None

        sys.meta_path.insert(0, BlockUI())

        module_path = pathlib.Path(PATH_PLACEHOLDER)
        spec = importlib.util.spec_from_file_location("reference_loader_isolated", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        leaked = [name for name in ("streamlit", "pydeck") if name in sys.modules]
        assert not leaked, f"the loader imported UI modules: {leaked}"
        for function in ("load_case", "grid_frame", "map_payload", "aoi_series", "kpis", "LoaderError"):
            assert hasattr(module, function), function
        print("no-ui-imports-ok")
        """
    ).replace("PATH_PLACEHOLDER", repr(str(EXAMPLES / "reference_loader.py")))

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    assert "no-ui-imports-ok" in completed.stdout
