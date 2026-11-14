"""The QGIS exporter must stay faithful to the artefact it reads.

`scripts/export_grid_geojson.py` exists so a reviewer can open the grid in QGIS/kepler.gl without
depending on Parquet support. It is a convenience, which makes silent distortion tempting — so this
pins the properties that matter: one point per published cell, no invented cells, coordinates inside
the case bbox, and the colourable fields present and of the right type.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pandas as pd
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data/processed"
CONFIG = yaml.safe_load((ROOT / "config/study_cases.yaml").read_text())

CASES = sorted(
    p.name
    for p in PROCESSED.iterdir()
    if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "nisar_grid.parquet").exists()
) if PROCESSED.exists() else []

pytestmark = pytest.mark.skipif(not CASES, reason="no artefacts yet — run pipeline/generate_mock.py")

NUMERIC = ("velocity_mm_yr", "delta_change", "val_t2", "coherence")
TEXT = ("cell_id", "land_use", "risk_level")


@pytest.fixture(scope="module")
def exported(tmp_path_factory) -> pathlib.Path:
    out = tmp_path_factory.mktemp("qgis")
    completed = subprocess.run(
        [sys.executable, "scripts/export_grid_geojson.py", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (completed.stdout + completed.stderr)[-1500:]
    return out


@pytest.mark.parametrize("case", CASES)
def test_one_point_per_published_cell(case: str, exported: pathlib.Path) -> None:
    grid = pd.read_parquet(PROCESSED / case / "nisar_grid.parquet")
    collection = json.loads((exported / f"{case}.geojson").read_text())

    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == len(grid), (
        f"{case}: exported {len(collection['features'])} points for {len(grid)} cells — the "
        f"exporter must not drop, invent or round-trip cells"
    )
    ids = {f["properties"]["cell_id"] for f in collection["features"]}
    assert ids == set(grid["cell_id"].astype(str)), f"{case}: cell ids differ from the artefact"


@pytest.mark.parametrize("case", CASES)
def test_coordinates_are_wgs84_inside_the_case_bbox(case: str, exported: pathlib.Path) -> None:
    west, south, east, north = CONFIG["cases"][case]["bbox"]
    collection = json.loads((exported / f"{case}.geojson").read_text())
    for feature in collection["features"]:
        assert feature["geometry"]["type"] == "Point"
        lon, lat = feature["geometry"]["coordinates"]
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert west <= lon <= east, f"{case}: longitude {lon} outside bbox"
        assert south <= lat <= north, f"{case}: latitude {lat} outside bbox"


@pytest.mark.parametrize("case", CASES)
def test_colourable_fields_survive_with_their_types(case: str, exported: pathlib.Path) -> None:
    collection = json.loads((exported / f"{case}.geojson").read_text())
    sample = collection["features"][0]["properties"]
    for field in NUMERIC:
        assert field in sample, f"{case}: {field} missing from the export"
        assert isinstance(sample[field], (int, float)), f"{case}: {field} is not numeric"
    for field in TEXT:
        assert field in sample, f"{case}: {field} missing from the export"
        assert isinstance(sample[field], str), f"{case}: {field} is not text"


def test_no_nulls_or_nan_leak_into_geojson(exported: pathlib.Path) -> None:
    """JSON has no NaN; a null coordinate or a NaN velocity would mean the artefact has nodata."""
    for case in CASES:
        text = (exported / f"{case}.geojson").read_text()
        assert "NaN" not in text, f"{case}: NaN leaked into the GeoJSON"
        collection = json.loads(text)
        for feature in collection["features"]:
            for field, value in feature["properties"].items():
                assert value is not None, f"{case}: null {field} in the export"
