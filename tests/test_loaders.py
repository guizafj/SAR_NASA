"""Tests for `src.data.loaders` config parsing, layout resolution, caching and immutability."""

from __future__ import annotations

import json
import pathlib
import time

import pandas as pd
import pytest

from src.data import (
    ArtifactValidationError,
    CaseNotFoundError,
    list_available_cases,
    load_all_available_bundles,
    load_case_bundle,
    load_study_cases_config,
)
from src.domain import contracts as C
from tests.conftest import write_synthetic_case


def test_load_study_cases_config_matches_repo_yaml():
    cfg = load_study_cases_config()
    assert cfg.schema_version == "1.0"
    assert cfg.primary == "mexico-city"
    assert set(cfg.cases) == set(C.CASE_IDS)
    assert cfg.cases["malaga"].null_result is True
    assert cfg.cases["malaga"].detection_limit_mm_yr == 1.5
    assert cfg.processing.h3_resolution == 9


def test_list_and_load_canonical_cases_orders_primary_first(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "malaga")
    write_synthetic_case(tmp_path, "mexico-city")

    available = list_available_cases(processed_dir=tmp_path)
    assert available == ["mexico-city", "malaga"]

    bundles = load_all_available_bundles(processed_dir=tmp_path)
    assert list(bundles) == ["mexico-city", "malaga"]
    assert bundles["mexico-city"].is_mock is True
    assert bundles["malaga"].case_config is not None
    assert bundles["malaga"].case_config.expected_regime == "elastic"


def test_flat_root_single_case_fallback_loads_transparently(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "malaga", flat=True)

    assert list_available_cases(processed_dir=tmp_path) == ["malaga"]
    bundle = load_case_bundle("malaga", processed_dir=tmp_path)
    assert bundle.case_id == "malaga"
    assert len(bundle.grid) == 7


def test_cache_returns_defensive_copies_and_invalidates_on_file_swap(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city", sensor="nisar", product="mock")
    b1 = load_case_bundle("mexico-city", processed_dir=tmp_path)
    assert b1.is_mock is True

    # Mutate returned frame and meta in-place; cached copy must stay untouched
    b1.grid.loc[0, "velocity_mm_yr"] = 999999.0
    b1.meta["name"] = "CORRUPTED"  # type: ignore[index]

    b2 = load_case_bundle("mexico-city", processed_dir=tmp_path)
    assert float(b2.grid.loc[0, "velocity_mm_yr"]) != 999999.0
    assert b2.meta["name"] == "Mexico City, MX"

    # Simulate Phase 3 hot-swap from 'mock' to real 'nisar-gunw' on disk
    time.sleep(0.01)
    write_synthetic_case(tmp_path, "mexico-city", sensor="nisar", product="nisar-gunw")
    b3 = load_case_bundle("mexico-city", processed_dir=tmp_path)
    assert b3.is_mock is False
    assert b3.product == "nisar-gunw"


def test_loader_rejects_unknown_missing_and_corrupted_cases(tmp_path: pathlib.Path):
    with pytest.raises(C.ContractError, match="Unknown case_id"):
        load_case_bundle("madrid", processed_dir=tmp_path)

    with pytest.raises(CaseNotFoundError):
        load_case_bundle("mexico-city", processed_dir=tmp_path)

    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    # Inject undeclared column (e.g. 'polarization' from legacy doc) -> must fail closed
    grid = pd.read_parquet(case_dir / "nisar_grid.parquet")
    grid["polarization"] = "HH"
    grid.to_parquet(case_dir / "nisar_grid.parquet", index=False)

    with pytest.raises(ArtifactValidationError, match="polarization"):
        load_case_bundle("mexico-city", processed_dir=tmp_path)
