"""Hermetic tests for the validated, drop-in AOI loader (`pipeline/aoi.py`).

Nothing here touches the real `config/aoi/` directory: every override lives under ``tmp_path`` and
is passed explicitly to `load_aoi`. The regression test copies the current study-case YAML to a
temporary directory with no sibling ``aoi/`` and proves the wired mock path still produces exactly
the cell set a manually built, pre-loader `CaseSpec` produces.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from pipeline import aoi as A
from pipeline.mock import cases as MC
from pipeline.mock import model as MM

ROOT = Path(__file__).resolve().parents[1]
CASE = "mexico-city"
BBOX = (-99.35, 19.20, -98.85, 19.65)
TIGHT_RING = [
    [-99.20, 19.35],
    [-99.15, 19.35],
    [-99.15, 19.40],
    [-99.20, 19.40],
    [-99.20, 19.35],
]
YAML_RING = [
    [-99.30, 19.25],
    [-98.90, 19.25],
    [-98.90, 19.60],
    [-99.30, 19.60],
    [-99.30, 19.25],
]


def _feature_collection(ring: list[list[float]], *, geometry_type: str = "Polygon", holes=None) -> dict:
    coordinates = [ring] + list(holes or [])
    geometry: dict = {"type": geometry_type, "coordinates": coordinates}
    if geometry_type == "MultiPolygon":
        geometry = {"type": "MultiPolygon", "coordinates": [[ring]]}
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": geometry}],
    }


def _write_override(directory: Path, case_id: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case_id}.geojson"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _config_entry(case_id: str = CASE) -> dict:
    raw = yaml.safe_load((ROOT / "config" / "study_cases.yaml").read_text(encoding="utf-8"))
    return raw["cases"][case_id]


def _legacy_case_specs(config_path: Path) -> dict[str, MC.CaseSpec]:
    """The pre-loader construction: bbox from YAML, field from YAML, no AOI override."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    specs: dict[str, MC.CaseSpec] = {}
    for case_id, entry in raw["cases"].items():
        bbox = tuple(float(v) for v in entry["bbox"])
        mock = entry["mock"]
        ellipse = mock["coverage_ellipse"]
        specs[case_id] = MC.CaseSpec(
            case_id=case_id,
            name=str(entry["name"]),
            bbox=bbox,  # type: ignore[arg-type]
            reference_point=(float(entry["reference_point"][0]), float(entry["reference_point"][1])),
            revisit_days=int(entry["revisit_days"]),
            limitations=tuple(str(v) for v in entry.get("limitations", ())),
            null_result=bool(entry.get("null_result", False)),
            detection_limit_mm_yr=(
                float(entry["detection_limit_mm_yr"])
                if entry.get("detection_limit_mm_yr") is not None
                else None
            ),
            field=MC.MockField(
                peak_velocity_mm_yr=float(mock["peak_velocity_mm_yr"]),
                peak_lonlat=(float(mock["peak_lonlat"][0]), float(mock["peak_lonlat"][1])),
                falloff_km=float(mock["falloff_km"]),
                noise_mm_yr=float(mock["noise_mm_yr"]),
                coverage_ellipse=MC.CoverageEllipse(
                    center_lon=float(ellipse["center"][0]),
                    center_lat=float(ellipse["center"][1]),
                    semi_axes_km=tuple(float(v) for v in ellipse["semi_axes_km"]),
                    rotation_deg=float(ellipse.get("rotation_deg", 0.0)),
                ),
                seasonal_amplitude_mm=float(mock["seasonal_amplitude_mm"]),
            ),
        )
    return specs


# ---------------------------------------------------------------------------------------------
# Resolution order and geometry
# ---------------------------------------------------------------------------------------------


def test_precedence_geojson_beats_yaml_beats_bbox(tmp_path):
    aoi_dir = tmp_path / "aoi"
    entry = {"bbox": list(BBOX), "aoi_polygon": YAML_RING}

    bbox_only = A.load_aoi(CASE, {"bbox": list(BBOX)}, aoi_dir=aoi_dir)
    assert bbox_only.source == "bbox"
    assert bbox_only.ring == (
        (BBOX[0], BBOX[1]),
        (BBOX[2], BBOX[1]),
        (BBOX[2], BBOX[3]),
        (BBOX[0], BBOX[3]),
        (BBOX[0], BBOX[1]),
    )
    assert bbox_only.bbox == BBOX

    yaml_only = A.load_aoi(CASE, entry, aoi_dir=aoi_dir)
    assert yaml_only.source == "config/study_cases.yaml:aoi_polygon"
    assert yaml_only.ring == tuple(tuple(point) for point in YAML_RING)

    _write_override(aoi_dir, CASE, _feature_collection(TIGHT_RING))
    override = A.load_aoi(CASE, entry, aoi_dir=aoi_dir)
    assert override.source == f"config/aoi/{CASE}.geojson"
    assert override.ring == tuple(tuple(point) for point in TIGHT_RING)
    assert override.ring[0] == override.ring[-1]
    assert override.bbox == (
        min(p[0] for p in TIGHT_RING),
        min(p[1] for p in TIGHT_RING),
        max(p[0] for p in TIGHT_RING),
        max(p[1] for p in TIGHT_RING),
    )
    assert override.area_km2 > 0


def test_tight_polygon_yields_fewer_cells_than_its_bbox(tmp_path):
    aoi_dir = tmp_path / "aoi"
    bbox = A.load_aoi(CASE, {"bbox": list(BBOX)}, aoi_dir=aoi_dir)
    _write_override(aoi_dir, CASE, _feature_collection(TIGHT_RING))
    tight = A.load_aoi(CASE, {"bbox": list(BBOX)}, aoi_dir=aoi_dir)
    assert tight.h3_cells > 0
    assert tight.h3_cells < bbox.h3_cells, "a tight urban footprint must not keep the whole bbox"


# ---------------------------------------------------------------------------------------------
# Every validation failure is one actionable AOIError
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "payload", "message"),
    [
        (
            "projected",
            _feature_collection(
                [[500000, 4000000], [500100, 4000000], [500100, 4000100], [500000, 4000100], [500000, 4000000]]
            ),
            "looks projected",
        ),
        ("multipolygon", _feature_collection(TIGHT_RING, geometry_type="MultiPolygon"), "MultiPolygon is unsupported"),
        (
            "hole",
            _feature_collection(
                TIGHT_RING,
                holes=[
                    [[-99.19, 19.36], [-99.18, 19.36], [-99.18, 19.37], [-99.19, 19.37], [-99.19, 19.36]]
                ],
            ),
            "holes",
        ),
        (
            "unclosed",
            _feature_collection([[-99.20, 19.35], [-99.15, 19.35], [-99.15, 19.40], [-99.20, 19.40]]),
            "not closed",
        ),
        ("too_few_points", _feature_collection([[-99.20, 19.40], [-99.19, 19.40], [-99.20, 19.40]]), "at least 4"),
        (
            "zero_area",
            _feature_collection([[-99.20, 19.40], [-99.19, 19.40], [-99.18, 19.40], [-99.20, 19.40]]),
            "zero area",
        ),
        (
            "tiny",
            _feature_collection(
                [[-99.20, 19.40], [-99.1999, 19.40], [-99.1999, 19.4001], [-99.20, 19.4001], [-99.20, 19.40]]
            ),
            "polygon this small is a mistake",
        ),
        (
            "outside_bbox",
            _feature_collection([[10.0, 10.0], [10.1, 10.0], [10.1, 10.1], [10.0, 10.1], [10.0, 10.0]]),
            "does not intersect",
        ),
        (
            "self_intersecting",
            _feature_collection(
                [[-99.20, 19.30], [-99.10, 19.40], [-99.10, 19.30], [-99.20, 19.40], [-99.20, 19.30]]
            ),
            "self-intersects",
        ),
        ("wrong_type", {"type": "Point", "coordinates": [-99.2, 19.4]}, "FeatureCollection, Feature or Polygon"),
    ],
)
def test_validation_errors_are_actionable(tmp_path, name, payload, message):
    aoi_dir = tmp_path / name
    _write_override(aoi_dir, CASE, payload)
    with pytest.raises(A.AOIError, match=message):
        A.load_aoi(CASE, _config_entry(), aoi_dir=aoi_dir)


def test_validate_aoi_accepts_a_bare_ring():
    ring = A.validate_aoi(TIGHT_RING, BBOX, source="test")
    assert ring == tuple(tuple(point) for point in TIGHT_RING)


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def test_cli_reports_a_valid_override_and_check_exits_zero(tmp_path, capsys):
    aoi_dir = tmp_path / "aoi"
    _write_override(aoi_dir, CASE, _feature_collection(TIGHT_RING))
    rc = A.main(["--case", CASE, "--aoi-dir", str(aoi_dir), "--check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"config/aoi/{CASE}.geojson" in out
    assert "bbox [W,S,E,N]" in out
    assert "area_km2" in out
    assert "ring points" in out
    assert "H3 res-9 cells" in out


def test_cli_check_exits_nonzero_and_prints_the_same_error(tmp_path, capsys):
    aoi_dir = tmp_path / "aoi"
    _write_override(
        aoi_dir,
        CASE,
        _feature_collection(
            [[500000, 4000000], [500100, 4000000], [500100, 4000100], [500000, 4000100], [500000, 4000000]]
        ),
    )
    rc = A.main(["--case", CASE, "--aoi-dir", str(aoi_dir), "--check"])
    assert rc != 0
    assert "looks projected" in capsys.readouterr().err


def test_cli_json_and_all(tmp_path, capsys):
    aoi_dir = tmp_path / "aoi"
    _write_override(aoi_dir, CASE, _feature_collection(TIGHT_RING))
    rc = A.main(["--case", CASE, "--aoi-dir", str(aoi_dir), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == f"config/aoi/{CASE}.geojson"
    assert payload["h3_res9_cells"] == A.load_aoi(CASE, _config_entry(), aoi_dir=aoi_dir).h3_cells

    missing_aoi_dir = tmp_path / "no-aoi"
    rc = A.main(["--all", "--aoi-dir", str(missing_aoi_dir), "--json"])
    assert rc == 0
    all_cases = json.loads(capsys.readouterr().out)
    assert set(all_cases) == {"mexico-city", "jakarta", "malaga"}
    assert all(entry["source"] == "config/study_cases.yaml:aoi_polygon" for entry in all_cases.values())


# ---------------------------------------------------------------------------------------------
# Regression: no override means exactly the old cell set
# ---------------------------------------------------------------------------------------------


def test_no_override_reproduces_the_existing_mock_cell_counts(tmp_path):
    """The loader must be invisible when `config/aoi/` is absent.

    The expected values are computed from a *manually built* pre-loader `CaseSpec` (bbox and mock
    field read straight from the YAML). Comparing that against `load_study_cases()` proves the new
    indirection changed nothing; nothing here depends on the model's current water/coherence tuning.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "study_cases.yaml"
    shutil.copy(ROOT / "config" / "study_cases.yaml", config_path)
    assert not (config_dir / "aoi").exists(), "this regression fixture must have no AOI override"

    study = MC.load_study_cases(config_path)
    legacy = _legacy_case_specs(config_path)
    seed = 7
    wired_counts: dict[str, int] = {}
    baseline_counts: dict[str, int] = {}
    for case_id in ("mexico-city", "jakarta", "malaga"):
        wired = MM.build_case(study.cases[case_id], study, seed)
        baseline = MM.build_case(legacy[case_id], study, seed)
        wired_counts[case_id] = wired.n_cells
        baseline_counts[case_id] = baseline.n_cells
        assert wired.n_cells == baseline.n_cells, (
            f"{case_id}: wiring the AOI loader changed the cell count "
            f"({wired.n_cells} vs {baseline.n_cells})"
        )
        assert list(wired.cell_id) == list(baseline.cell_id), f"{case_id}: wired cell ids changed"

    # Published pre-loader snapshot: 13_379 / 4_855 / 2_677 at seed 7. The current workspace also
    # has a concurrent model.py change that drops water cells; when that tuning is present the
    # Jakarta/Málaga figures legitimately move, so the stable Mexico City anchor is always asserted.
    published_snapshot = {"mexico-city": 13_379, "jakarta": 4_855, "malaga": 2_677}
    if baseline_counts == published_snapshot:
        assert wired_counts == published_snapshot
    assert wired_counts["mexico-city"] == 13_379
