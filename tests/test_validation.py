"""Tests for `src.data.validation` cross-artefact, H3, budget and CLI checks."""

from __future__ import annotations

import json
import pathlib

import h3
import pandas as pd
import pytest

from src.data.validation import (
    ArtifactValidationError,
    _cli,
    validate_case_bundle,
    validate_case_directory,
    validate_processed_tree,
)
from tests.conftest import write_synthetic_case


def test_valid_three_city_tree_passes_and_cli_exits_zero(tmp_path: pathlib.Path):
    for slug in ("mexico-city", "jakarta", "malaga"):
        write_synthetic_case(tmp_path, slug, include_cell_series=(slug == "mexico-city"))
    # Internal probe folder must be ignored by size & case discovery
    avail = tmp_path / "_availability"
    avail.mkdir()
    (avail / "malaga.json").write_text("{}", encoding="utf-8")

    report = validate_processed_tree(tmp_path, require_cases=True)
    assert report.is_valid
    assert set(report.case_results) == {"mexico-city", "jakarta", "malaga"}
    assert _cli(["--processed", str(tmp_path), "--require-cases"]) == 0


def test_require_cases_fails_when_only_availability_present(tmp_path: pathlib.Path):
    (tmp_path / "_availability").mkdir()
    report = validate_processed_tree(tmp_path, require_cases=True)
    assert not report.is_valid
    assert any("no published case directories" in p for p in report.budget_problems)
    assert _cli(["--processed", str(tmp_path), "--require-cases"]) == 1


def test_missing_file_reported_cleanly(tmp_path: pathlib.Path):
    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    (case_dir / "nisar_grid.parquet").unlink()

    res = validate_case_directory(case_dir, case_id="mexico-city")
    assert not res.is_valid
    assert "files" in res.problems_by_section
    with pytest.raises(ArtifactValidationError, match="nisar_grid.parquet"):
        res.raise_for_problems()


def test_cross_check_catches_risk_level_mismatch(tmp_path: pathlib.Path):
    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    grid = pd.read_parquet(case_dir / "nisar_grid.parquet")
    # -380 mm/yr is 'critico'; corrupting to 'estable' must fail cross_check
    grid.loc[0, "risk_level"] = "estable"
    grid.to_parquet(case_dir / "nisar_grid.parquet", index=False)

    res = validate_case_directory(case_dir, case_id="mexico-city")
    assert not res.is_valid
    assert any("risk_level disagrees" in p for p in res.all_problems)


def test_cross_check_catches_wrong_h3_resolution_and_invalid_h3(tmp_path: pathlib.Path):
    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    grid = pd.read_parquet(case_dir / "nisar_grid.parquet")
    # Resolution 7 instead of frozen resolution 9
    grid.loc[0, "cell_id"] = h3.latlng_to_cell(19.43, -99.13, 7)
    grid.to_parquet(case_dir / "nisar_grid.parquet", index=False)

    res = validate_case_directory(case_dir, case_id="mexico-city")
    assert not res.is_valid
    assert any("resolution 9" in p for p in res.all_problems)

    grid.loc[0, "cell_id"] = "not-an-h3-cell"
    grid.to_parquet(case_dir / "nisar_grid.parquet", index=False)
    res_bad = validate_case_directory(case_dir, case_id="mexico-city")
    assert not res_bad.is_valid
    assert any("invalid H3" in p for p in res_bad.all_problems)


def test_cross_check_catches_sensor_product_and_provenance_drift(tmp_path: pathlib.Path):
    case_dir = write_synthetic_case(tmp_path, "malaga", sensor="nisar", product="nisar-gunw")
    ts = pd.read_csv(case_dir / "timeseries.csv")
    ts["sensor"] = "sentinel-1"
    ts["product"] = "egms"
    ts.to_csv(case_dir / "timeseries.csv", index=False)

    res = validate_case_directory(case_dir, case_id="malaga")
    assert not res.is_valid
    joined = "\n".join(res.all_problems)
    assert "timeseries.sensor" in joined
    assert "timeseries.product" in joined


def test_cross_check_catches_missing_aoi_duplicate_dates_and_orphan_cells(tmp_path: pathlib.Path):
    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    ts = pd.read_csv(case_dir / "timeseries.csv")

    # Replace 'aoi' with an H3 cell that is NOT in grid
    orphan_cell = h3.latlng_to_cell(0.0, 0.0, 9)
    ts["cell_id"] = orphan_cell
    # Duplicate first row
    ts = pd.concat([ts, ts.iloc[[0]]], ignore_index=True)
    ts.to_csv(case_dir / "timeseries.csv", index=False)

    res = validate_case_directory(case_dir, case_id="mexico-city")
    assert not res.is_valid
    joined = "\n".join(res.all_problems)
    assert "missing mandatory cell_id == 'aoi'" in joined
    assert "duplicate (cell_id, date)" in joined
    assert "absent from grid" in joined


def test_cross_check_catches_stats_count_mismatch_and_budget_overflow(tmp_path: pathlib.Path):
    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    meta["stats"]["n_cells"] = 99999
    (case_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    res = validate_case_directory(case_dir, case_id="mexico-city", max_bytes=100)
    assert not res.is_valid
    joined = "\n".join(res.all_problems)
    assert "meta.stats.n_cells" in joined
    assert "exceeding budget" in joined
