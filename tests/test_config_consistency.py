"""The case geometry must be identical wherever it is written down.

A case is declared twice: `config/study_cases.yaml` feeds the pipeline, `pipeline/ingest/aois.json`
feeds the ASF/NISAR availability probe. If they drift, the probe can prove coverage for one AOI
while the pipeline processes another, and every provenance claim downstream becomes wrong without
anything failing. This test is the tripwire.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config/study_cases.yaml").read_text())
AOIS = json.loads((ROOT / "pipeline/ingest/aois.json").read_text())

CASES = sorted(CONFIG["cases"])


def test_both_files_declare_the_same_cases():
    assert sorted(AOIS["cities"]) == CASES, (
        "case ids differ between config/study_cases.yaml and pipeline/ingest/aois.json"
    )


@pytest.mark.parametrize("case", CASES)
def test_geometry_and_projection_agree(case):
    config_case = CONFIG["cases"][case]
    aoi = AOIS["cities"][case]

    assert aoi["bbox"] == config_case["bbox"], (
        f"{case}: bbox differs ({aoi['bbox']} vs {config_case['bbox']}) — the probe and the "
        f"pipeline would look at different footprints"
    )
    assert aoi["crs"] == config_case["crs_analysis"], (
        f"{case}: projected CRS differs ({aoi['crs']} vs {config_case['crs_analysis']})"
    )
    assert aoi["reference_point"] == config_case["reference_point"], (
        f"{case}: InSAR reference point differs — displacement would be referenced to a "
        f"different pixel than the one documented"
    )
    assert aoi["name"] == config_case["name"]


#: One run per shipped case against the real drop-in directory, plus one run of the same contract
#: with an empty drop-in directory so the *no override* arm executes. All three shipped cases have a
#: `config/aoi/<case>.geojson`, so without that extra case the "no override -> ring is the configured
#: bbox" branch below was unreachable (and its `entry["aoi_polygon"]` read would now KeyError — no
#: case declares an inline ring, on purpose).
AOI_RING_PARAMS = [pytest.param(case, True, id=f"{case}-shipped-override") for case in CASES]
if CASES:
    AOI_RING_PARAMS.append(pytest.param(CASES[0], False, id=f"{CASES[0]}-no-override"))


@pytest.mark.parametrize(("case", "use_override"), AOI_RING_PARAMS)
def test_aoi_ring_matches_the_declared_bbox(
    case: str, use_override: bool, tmp_path: pathlib.Path
) -> None:
    """The AOI ring and the configured bbox must agree, with or without a QGIS override.

    `use_override=True` reads the shipped `config/aoi/` (what the pipeline does);
    `use_override=False` points the loader at an empty directory, which is the no-override arm.
    """
    entry = CONFIG["cases"][case]
    west, south, east, north = entry["bbox"]
    directory = ROOT / "config" / "aoi" if use_override else tmp_path
    override = directory / f"{case}.geojson"

    if override.exists():
        # A QGIS drop-in is allowed to be tighter than the configured bbox. It must stay inside
        # that bbox, and the AOI's published bbox must be derived from the actual ring.
        from pipeline.aoi import load_aoi

        aoi = load_aoi(case, entry, aoi_dir=override.parent)
        ring = list(aoi.ring)
        assert aoi.source == f"config/aoi/{case}.geojson"
        assert aoi.bbox == (
            min(p[0] for p in ring),
            min(p[1] for p in ring),
            max(p[0] for p in ring),
            max(p[1] for p in ring),
        ), f"{case}: AOI bbox is not derived from its ring"
        for lon, lat in ring:
            assert west <= lon <= east and south <= lat <= north, (
                f"{case}: override ring point {(lon, lat)} lies outside the configured bbox "
                f"{[west, south, east, north]}"
            )
    else:
        # No override: the shipped rectangle is the contract, so the ring the loader returns and
        # the bbox it publishes must be exactly the configured envelope.
        from pipeline.aoi import load_aoi

        aoi = load_aoi(case, entry, aoi_dir=directory)
        ring = list(aoi.ring)
        assert aoi.source == "bbox", (
            f"{case}: with no override in {directory} the loader fell back to "
            f"{aoi.source!r}, not the configured bbox"
        )
        assert ring == [
            (west, south),
            (east, south),
            (east, north),
            (west, north),
            (west, south),
        ], f"{case}: ring {ring} is not the configured bbox rectangle"
        assert aoi.bbox == pytest.approx((west, south, east, north)), (
            f"{case}: published bbox {aoi.bbox} differs from the configured "
            f"{[west, south, east, north]}"
        )
        assert ring[0] == ring[-1], f"{case}: AOI ring is not closed"
        assert min(p[0] for p in ring) == pytest.approx(west)
        assert max(p[0] for p in ring) == pytest.approx(east)
        assert min(p[1] for p in ring) == pytest.approx(south)
        assert max(p[1] for p in ring) == pytest.approx(north)

    for lon, lat in ring:
        assert -180 <= lon <= 180 and -90 <= lat <= 90, f"{case}: ring point outside WGS84"


def test_primary_case_is_declared_and_known():
    primary = CONFIG["primary"]
    assert primary in CONFIG["cases"], f"primary {primary!r} is not one of {CASES}"
    # the shipped artefacts must exist for the case the demo opens on
    artefacts = ROOT / "data/processed" / primary
    if artefacts.exists():
        for name in ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json"):
            assert (artefacts / name).exists(), f"primary case {primary} is missing {name}"


def test_revisit_matches_the_declared_product():
    """Config claims a revisit per case; the contract owns the truth per (sensor, product)."""
    from src.domain import contracts as C

    for case in CASES:
        revisit = CONFIG["cases"][case]["revisit_days"]
        mock_profile = C.SENSOR_PROFILES["mock"]
        assert revisit == mock_profile.revisit_days, (
            f"{case}: configured revisit {revisit} d != {mock_profile.revisit_days} d implied by "
            f"the mock profile — coherence would be published against the wrong sensor cadence"
        )
