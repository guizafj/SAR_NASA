"""The QGIS exporter must stay faithful to the artefact it reads.

`scripts/export_grid_geojson.py` exists so a reviewer can open the grid in QGIS/kepler.gl without
depending on Parquet support. It is a convenience, which makes silent distortion tempting — so this
pins the properties that matter: one **polygon** per published cell, no invented cells, closed
rings inside the case bbox, and the colourable fields present and of the right type.
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
        f"{case}: exported {len(collection['features'])} polygons for {len(grid)} cells — the "
        f"exporter must not drop, invent or round-trip cells"
    )
    ids = {f["properties"]["cell_id"] for f in collection["features"]}
    assert ids == set(grid["cell_id"].astype(str)), f"{case}: cell ids differ from the artefact"


@pytest.mark.parametrize("case", CASES)
def test_rings_are_closed_wgs84_polygons_inside_the_case_bbox(case: str, exported: pathlib.Path) -> None:
    """The export carries the cell *boundary*, not its centroid.

    It carried centroids once, styled as hexagon markers sized in map units. QGIS map units follow
    the project CRS, so in EPSG:4326 the unit is degrees and every marker was 349 degrees wide: the
    world map came out as three giant hexagons. Real rings need no size and cannot be misread.
    """
    west, south, east, north = CONFIG["cases"][case]["bbox"]
    pad = 0.01  # ~1 km: a cell on the AOI edge legitimately has vertices outside its envelope
    collection = json.loads((exported / f"{case}.geojson").read_text())
    for feature in collection["features"]:
        geometry = feature["geometry"]
        assert geometry["type"] == "Polygon", f"{case}: {geometry['type']} is not a cell polygon"
        ring = geometry["coordinates"][0]
        assert len(ring) >= 5, f"{case}: a polygon ring needs at least 4 vertices"
        assert ring[0] == ring[-1], f"{case}: GeoJSON rings must be closed"
        for lon, lat in ring:
            assert -180 <= lon <= 180 and -90 <= lat <= 90
            assert west - pad <= lon <= east + pad, f"{case}: vertex longitude {lon} outside bbox"
            assert south - pad <= lat <= north + pad, f"{case}: vertex latitude {lat} outside bbox"
        # the cell itself must sit inside the envelope, whatever its vertices do
        cx = sum(point[0] for point in ring[:-1]) / (len(ring) - 1)
        cy = sum(point[1] for point in ring[:-1]) / (len(ring) - 1)
        assert west <= cx <= east and south <= cy <= north, f"{case}: cell centroid outside bbox"


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
