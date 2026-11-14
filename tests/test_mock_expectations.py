"""Plausibility gate for the mock artefacts.

`contracts.validate_*` proves the mock is *schema*-conformant. It cannot prove the synthetic field
is believable, and an unbelievable mock teaches the frontend the wrong thing (a flat CDMX, a
subsiding Málaga, coherence that never varies). These tests assert the physics described in
`config/study_cases.yaml` actually shows up in `data/processed/`.

Skipped automatically until `pipeline/generate_mock.py` has run.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
import pytest
import yaml

from src.domain import contracts as C

CFG = yaml.safe_load(pathlib.Path("config/study_cases.yaml").read_text())
ROOT = pathlib.Path("data/processed")
REQUIRED = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")


def _case_dirs() -> list[pathlib.Path]:
    if not ROOT.exists():
        return []
    return sorted(
        p for p in ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and all((p / f).exists() for f in REQUIRED)
    )


CASES = _case_dirs()
pytestmark = pytest.mark.skipif(
    not CASES, reason="no mock artefacts yet — run ./scripts/python.sh pipeline/generate_mock.py"
)


@pytest.fixture(params=CASES, ids=[p.name for p in CASES])
def case(request):
    directory = request.param
    grid = pd.read_parquet(directory / "nisar_grid.parquet")
    series = pd.read_csv(directory / "timeseries.csv")
    meta = json.loads((directory / "meta.json").read_text())
    context = json.loads((directory / "context_layers.geojson").read_text())
    return {
        "name": directory.name,
        "dir": directory,
        "grid": grid,
        "series": series,
        "meta": meta,
        "context": context,
        "cfg": CFG["cases"][directory.name],
    }


def test_grid_is_dense_enough_but_has_real_nodata(case):
    """A city with no gaps is a bug: the coverage ellipse must actually cut cells out."""
    grid, cfg = case["grid"], case["cfg"]
    assert len(grid) > 200, f"{case['name']}: only {len(grid)} cells — is the AOI wrong?"

    polygon = {"type": "Polygon", "coordinates": [cfg["aoi_polygon"]]}
    import h3

    bbox_cells = h3.geo_to_cells(h3.geo_to_h3shape(polygon), CFG["processing"]["h3_resolution"])
    # coverage ellipse is ~60-70% of the bbox in area; allow a wide band but require both sides
    fraction = len(grid) / len(bbox_cells)
    assert 0.2 < fraction < 0.95, (
        f"{case['name']}: {fraction:.0%} of the AOI has data — either no nodata exists "
        f"(suspicious) or the coverage ellipse is wrong"
    )


def test_velocity_field_matches_the_case_physics(case):
    grid, cfg = case["grid"], case["cfg"]
    velocity = grid["velocity_mm_yr"]
    median, peak = velocity.median(), velocity.min()

    if cfg.get("null_result"):
        assert abs(median) < 5, f"{case['name']}: control case must be near-flat, median {median:.1f}"
        assert peak > -15, f"{case['name']}: control case has an unrealistic extreme {peak:.1f}"
        assert "detection_limit_mm_yr" in case["meta"]["stats"], "null result without its bound"
    else:
        expected_peak = cfg["mock"]["peak_velocity_mm_yr"]
        assert peak < 0.5 * expected_peak, (
            f"{case['name']}: bowl never reaches near its peak ({peak:.0f} vs {expected_peak:.0f})"
        )
        assert median < -20, f"{case['name']}: median {median:.1f} mm/yr is not a subsidence story"
        assert median > peak, "median cannot be below the peak"


def test_risk_level_is_derived_not_decorative(case):
    grid = case["grid"]
    expected = grid["velocity_mm_yr"].map(C.risk_level_from_velocity)
    mismatched = int((expected != grid["risk_level"]).sum())
    assert mismatched == 0, f"{case['name']}: {mismatched} cells disagree with the frozen risk rule"
    assert set(grid["risk_level"]) <= set(C.RISK_LEVELS)


def test_delta_change_agrees_with_the_rate(case):
    grid, cfg = case["grid"], case["cfg"]
    span_years = (len(case["meta"]["dates"]) - 1) * CFG["processing"]["dates_step_days"] / 365.25
    residual = (grid["delta_change"] - grid["velocity_mm_yr"] * span_years).abs()
    # seasonal harmonic and noise are allowed to move this, but not by more than ~2 sigma
    slack = 2 * cfg["mock"]["seasonal_amplitude_mm"] + abs(cfg["mock"]["peak_velocity_mm_yr"]) * 0.1
    assert residual.median() < slack, (
        f"{case['name']}: delta_change is not tracking velocity over {span_years:.2f} yr "
        f"(median residual {residual.median():.1f} mm)"
    )
    assert (grid["delta_change"] < 0).mean() > 0.5, "most cells should be subsiding, not rising"


def test_coherence_varies_and_stays_in_the_mock_band(case):
    coherence = case["grid"]["coherence"]
    threshold = C.SENSOR_PROFILES["mock"].coherence_min
    # Honest invariant: every *published* cell passed the quality filter, and the model tops out at
    # 0.95. There is no 0.45 floor on the published side — a floor at the clip would make the
    # filter a no-op. Values below `threshold` exist only as pre-filter coherence, never here.
    assert coherence.min() >= threshold, (
        f"{case['name']}: published coherence {coherence.min():.3f} is below coherence_min {threshold}"
    )
    assert coherence.max() <= 0.95, f"{case['name']}: coherence {coherence.max():.3f} exceeds 0.95"
    assert coherence.std() > 0.01, "constant coherence means the field was not modelled"
    assert 0.6 < coherence.mean() < 0.92, f"implausible mean coherence {coherence.mean():.2f}"


def test_land_use_has_the_expected_variety(case):
    land_use = case["grid"]["land_use"]
    assert len(set(land_use)) >= 3, f"{case['name']}: only {sorted(set(land_use))}"
    assert set(land_use) <= set(C.LAND_USE)
    assert "urban" not in set(land_use), "use the frozen vocabulary, not free text"


def test_timeseries_is_a_referenced_aoi_series(case):
    series, meta = case["series"], case["meta"]
    aoi = series[series["cell_id"] == "aoi"]
    assert len(aoi) == len(meta["dates"]), (
        f"{case['name']}: {len(aoi)} aggregate rows for {len(meta['dates'])} dates"
    )
    assert list(aoi["date"]) == list(meta["dates"]), "aggregate rows must follow meta.dates exactly"
    assert abs(float(aoi["value"].iloc[0])) < 1e-6, "the series must be referenced to the first date"


def test_meta_matches_the_case_config(case):
    meta, cfg = case["meta"], case["cfg"]
    assert meta["case_id"] == case["name"]
    assert meta["bbox"] == cfg["bbox"], "meta bbox drifted from config/study_cases.yaml"
    assert meta["crs"] == C.CRS_OUTPUT
    assert meta["revisit_days"] == C.SENSOR_PROFILES[meta["product"]].revisit_days
    assert meta["thresholds"]["coherence_min"] == C.SENSOR_PROFILES[meta["product"]].coherence_min
    assert "mock" in meta["provenance"]["source"].lower()
    for limitation in cfg.get("limitations", []):
        assert limitation in meta["limitations"], f"config limitation dropped: {limitation[:40]}"


def test_delta_change_tracks_the_published_velocity(case):
    """The slider (`delta_change`) and the map (`velocity_mm_yr`) must tell the same story.

    Regression guard. With a seasonal harmonic anchored at the first date, a 132-day window made
    the seasonal term masquerade as subsidence: `delta_change / T` overstated the trend by 2-8x and
    hundreds of cells rose in the map while falling in the slider.
    """
    grid, meta = case["grid"], case["meta"]
    span = (len(meta["dates"]) - 1) * CFG["processing"]["dates_step_days"] / 365.25
    strong = grid[grid["velocity_mm_yr"].abs() > 5]
    if strong.empty:
        pytest.skip("no cell carries a meaningful velocity")

    implied = strong["delta_change"] / (strong["velocity_mm_yr"] * span)
    assert implied.median() == pytest.approx(1.0, abs=0.05), (
        f"{case['name']}: delta_change/T is {implied.median():.2f}x the published velocity — the "
        f"seasonal term is leaking into the cumulative change again"
    )
    disagreement = int((np.sign(strong["delta_change"]) != np.sign(strong["velocity_mm_yr"])).sum())
    assert disagreement == 0, (
        f"{case['name']}: {disagreement} cells disagree in sign between the map and the slider"
    )


def test_water_cells_are_filtered_not_published(case):
    """A specular surface has no usable phase: water must be absent from the grid, not coloured."""
    grid = case["grid"]
    assert (grid["land_use"] == "water").sum() == 0, (
        f"{case['name']}: water cells survived the quality filter and would carry a displacement"
    )
    assert grid["coherence"].min() >= C.SENSOR_PROFILES["mock"].coherence_min

    # ...but the coastline/lake must still exist as context, or the map loses its geography
    layers = {f["properties"]["layer"] for f in case["context"]["features"]}
    assert "water" in layers, f"{case['name']}: the water context layer disappeared with the cells"


def test_context_layers_cover_more_than_one_layer(case):
    features = case["context"]["features"]
    assert len(features) >= 5, f"{case['name']}: {len(features)} context features is too thin"
    layers = {f["properties"]["layer"] for f in features}
    assert {"land_use", "infrastructure"} <= layers, f"missing layers: {layers}"
    for feature in features:
        props = feature["properties"]
        assert props["class"] in C.CONTEXT_CLASSES[props["layer"]]
        assert props["crs"] == C.CRS_OUTPUT


def test_fresh_generation_passes_the_contract_gate(tmp_path):
    """End-to-end reproducibility: generate into a clean directory, then gate that directory.

    This is the property that matters for CI and for a teammate on a fresh clone — the scripts
    must agree with each other, not just with the artefacts someone happened to commit. It also
    catches the stale-artefact class of failure (data written before a code change).
    """
    import subprocess
    import sys

    out = tmp_path / "processed"
    generated = subprocess.run(
        [sys.executable, "pipeline/generate_mock.py", "--out", str(out), "--cities", "malaga"],
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, (generated.stdout + generated.stderr)[-800:]
    assert (out / "malaga" / "nisar_grid.parquet").exists(), "generator produced no grid"

    gated = subprocess.run(
        [sys.executable, "pipeline/validate_artifacts.py", "--processed", str(out), "--json"],
        capture_output=True,
        text=True,
    )
    assert gated.returncode == 0, (
        "a freshly generated tree must pass the gate; output:\n"
        + (gated.stdout + gated.stderr)[-1200:]
    )
