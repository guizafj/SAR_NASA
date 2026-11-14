"""Tests for `pipeline/enrich_context.py` — GIS context enrichment against the frozen contract.

Hermetic: every fixture is built in `tmp_path` or in memory; no network, no `data/processed`, no
RNG without a fixed seed. Each test names the contract rule it protects, so a failure points at
the schema or the science rather than at the plumbing.
"""

from __future__ import annotations

import json
import math
import pathlib
import subprocess
import sys

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python pipeline/enrich_context.py` and pytest from the repo root
    sys.path.insert(0, str(ROOT))

from pipeline import enrich_context as E  # noqa: E402
from src.domain import contracts as C  # noqa: E402

CASE = "mexico-city"
#: A valid frozen provenance id (the same shape the grid carries).
PROVENANCE = "mexico-city+mock+20261114+abc12345"
#: Inside the mexico-city AOI, and inside the bbox of config/study_cases.yaml.
LON, LAT = -99.13, 19.43
MODULE = ROOT / "pipeline" / "enrich_context.py"


# ---------------------------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------------------------


def make_gdf(geometries, **tags) -> gpd.GeoDataFrame:
    """A GeoDataFrame in EPSG:4326 whose columns are exactly the source tags given."""
    length = len(geometries)
    lengths = {len(v) for v in tags.values() if isinstance(v, (list, tuple)) and not isinstance(v, str)}
    assert not lengths or lengths == {length}, f"tag columns disagree with {length} geometries"
    frame = {key: (value if not isinstance(value, str) else [value] * length) for key, value in tags.items()}
    return gpd.GeoDataFrame(frame, geometry=list(geometries), crs=C.CRS_OUTPUT)


def mapped(geometries, **tags) -> gpd.GeoDataFrame:
    return E.mmap_source_features(make_gdf(geometries, **tags), CASE, PROVENANCE)


def grid_frame(rows) -> pd.DataFrame:
    """`nisar_grid`-shaped frame: (cell_id, lat, lon) plus whatever else the test needs."""
    return pd.DataFrame(rows)


def grid_bytes(path: pathlib.Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------------------------
# 1. The mapping table is the only source of classes, and it stays inside the frozen vocabulary
# ---------------------------------------------------------------------------------------------


def test_mapping_table_targets_are_inside_the_frozen_vocabulary():
    assert E.mapping_problems() == [], "TAG_RULES has a defect; see mapping_problems()"
    for rule in E.TAG_RULES:
        assert rule.layer in C.CONTEXT_LAYERS, f"{rule.key}: layer {rule.layer!r} is not frozen"
        assert rule.klass in C.CONTEXT_CLASSES[rule.layer], (
            f"{rule.key}={rule.values}: class {rule.klass!r} is not in "
            f"{list(C.CONTEXT_CLASSES[rule.layer])} for layer {rule.layer!r}"
        )


def test_every_frozen_context_class_is_reachable():
    """No class in §7.4 may exist only in the contract: that would make it unreachable by design."""
    reachable = {(rule.layer, rule.klass) for rule in E.TAG_RULES}
    # Layers that *are* a land use by themselves (§7.1 and §7.4 share the vocabulary).
    for layer, land_use in E.LAYER_TO_LAND_USE.items():
        reachable.add(("land_use", land_use))
        assert E.land_use_of(layer, C.CONTEXT_CLASSES[layer][0]) == land_use
    reachable.add(("land_use", "unknown"))  # the documented unmapped-tag fallback
    for layer, classes in C.CONTEXT_CLASSES.items():
        for klass in classes:
            assert (layer, klass) in reachable, (
                f"({layer!r}, {klass!r}) is frozen but no rule or fallback can ever produce it"
            )


@pytest.mark.parametrize("layer", list(C.CONTEXT_CLASSES))
def test_land_use_projection_covers_every_class(layer):
    for klass in C.CONTEXT_CLASSES[layer]:
        projected = E.land_use_of(layer, klass)
        if layer == "admin_boundary":
            assert projected is None, "a boundary covers every land use and must never win the join"
        else:
            assert projected in C.LAND_USE


def test_every_frozen_land_use_value_is_producible():
    producible = {rule.klass for rule in E.TAG_RULES if rule.layer == "land_use"}
    producible |= set(E.LAYER_TO_LAND_USE.values())
    producible.add("unknown")
    assert producible == set(C.LAND_USE)


def test_mapping_problems_catches_a_typo_in_the_table(monkeypatch):
    """The guard has to be able to fail, otherwise it is decoration."""
    typo = E.TagRule("barrier", ("fence",), "land_use", "urban-densed", "deliberate typo")
    monkeypatch.setattr(E, "TAG_RULES", E.TAG_RULES + (typo,))
    problems = E.mapping_problems()
    assert any("urban-densed" in problem for problem in problems), problems


def test_mapping_problems_catches_a_wildcard_that_shadows_later_rules(monkeypatch):
    shadowing = (
        E.TagRule("landuse", None, "land_use", "urban-dense", "wildcard too early"),
        E.TagRule("landuse", ("residential",), "land_use", "urban-residential", "dead rule"),
    )
    monkeypatch.setattr(E, "TAG_RULES", shadowing)
    assert any("shadows" in problem for problem in E.mapping_problems())


def test_unmapped_tags_become_unknown_and_are_never_dropped():
    geometries = [box(LON, LAT, LON + 0.01, LAT + 0.01) for _ in range(3)]
    source = make_gdf(
        geometries,
        highway=["residential", None, None],
        barrier=[None, "fence", None],
        landuse=[None, None, "residential"],
    )
    out = E.mmap_source_features(source, CASE, PROVENANCE)
    assert len(out) == len(source), "an unmapped feature was silently dropped"
    assert out["layer"].tolist() == ["land_use", "land_use", "land_use"]
    assert out["class"].tolist() == ["unknown", "unknown", "urban-residential"]
    # ...and the unmapped rows still carry the frozen properties.
    assert set(out["case_id"]) == {CASE}
    assert set(out["crs"]) == {C.CRS_OUTPUT}
    assert set(out["provenance_id"]) == {PROVENANCE}


def test_output_columns_are_exactly_the_frozen_set():
    out = mapped([box(0, 0, 1, 1)], landuse="residential")
    assert list(out.columns) == [
        "layer",
        "class",
        "name",
        "case_id",
        "crs",
        "source",
        "provenance_id",
        "geometry",
    ]


def test_inputs_are_validated_loudly():
    source = make_gdf([box(0, 0, 1, 1)], landuse="residential")
    with pytest.raises(ValueError, match="undeclared case_id"):
        E.mmap_source_features(source, "atlantis", PROVENANCE)
    with pytest.raises(ValueError, match="provenance_id"):
        E.mmap_source_features(source, CASE, "not-a-provenance-id")
    with pytest.raises(ValueError, match="no CRS"):
        E.mmap_source_features(gpd.GeoDataFrame({"landuse": ["residential"]}, geometry=[box(0, 0, 1, 1)]), CASE, PROVENANCE)


def test_multipart_geometries_are_exploded_into_publishable_parts():
    """§6 admits Point/LineString/Polygon/MultiPolygon only; a MultiLineString must not leak."""
    line = MultiLineString([[(0, 0), (1, 1)], [(2, 2), (3, 3)]])
    poly = MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)])
    out = E.mmap_source_features(
        make_gdf([line, poly, None], waterway=["river", None, None], landuse=[None, "residential", "forest"]),
        CASE,
        PROVENANCE,
    )
    # The MultiLineString becomes two LineStrings; the MultiPolygon is already admissible (§6).
    assert len(out) == 3
    assert set(out.geometry.geom_type) <= E.ALLOWED_GEOMETRY_TYPES
    assert out["class"].tolist() == ["river", "river", "urban-residential"]
    # The geometry-less feature cannot be published, but the loss is counted, not hidden.
    assert out.attrs["dropped_no_geometry"] == 1


# ---------------------------------------------------------------------------------------------
# 2. Overlap precedence and deterministic joins
# ---------------------------------------------------------------------------------------------


def overlapping_context(shuffle: bool) -> gpd.GeoDataFrame:
    """Residential neighbourhood, industrial block, river polygon — all containing one point."""
    residential = box(LON - 0.10, LAT - 0.10, LON + 0.10, LAT + 0.10)
    industrial = box(LON - 0.05, LAT - 0.05, LON + 0.05, LAT + 0.05)
    water = box(LON - 0.02, LAT - 0.02, LON + 0.02, LAT + 0.02)
    source = make_gdf(
        [residential, industrial, water],
        landuse=["residential", "industrial", None],
        natural=[None, None, "water"],
        name=["colonia", "parque industrial", "rio"],
    )
    if shuffle:
        source = source.iloc[[2, 0, 1]].reset_index(drop=True)
    return E.mmap_source_features(source, CASE, PROVENANCE)


def test_overlap_resolves_by_precedence_not_by_row_order():
    grid = grid_frame([{"cell_id": "8928308280fff", "lat": LAT, "lon": LON}])
    forward = E.join_land_use(grid, overlapping_context(shuffle=False))
    backward = E.join_land_use(grid, overlapping_context(shuffle=True))
    assert forward["land_use"].tolist() == ["water"]
    assert backward["land_use"].tolist() == ["water"]
    # The winning feature id is content-derived, so it survives the shuffle too.
    assert forward["context_id"].tolist() == backward["context_id"].tolist()


def test_precedence_is_configurable_but_must_stay_a_permutation():
    grid = grid_frame([{"cell_id": "c", "lat": LAT, "lon": LON}])
    context = overlapping_context(shuffle=False)
    # A caller may demote water (e.g. to test the map): then the next-rank candidate wins.
    water_last = ("unknown", "bare", "vegetation", "urban-residential", "urban-dense", "industrial", "infrastructure", "water")
    assert E.join_land_use(grid, context, precedence=water_last)["land_use"].tolist() == ["urban-residential"]
    assert E.join_land_use(grid, context, precedence=tuple(reversed(water_last)))["land_use"].tolist() == ["water"]
    with pytest.raises(ValueError, match="permutation"):
        E.join_land_use(grid, context, precedence=E.LAND_USE_PRECEDENCE[:-1])


#: One publishable `(layer, class)` source per `land_use` value, for precedence fixtures.
LAND_USE_SOURCE = {
    "water": ("water", "reservoir"),
    "infrastructure": ("infrastructure", "power"),
    "industrial": ("land_use", "industrial"),
    "urban-dense": ("land_use", "urban-dense"),
    "urban-residential": ("land_use", "urban-residential"),
    "vegetation": ("land_use", "vegetation"),
    "bare": ("land_use", "bare"),
}


def context_rows(entries) -> gpd.GeoDataFrame:
    """Mapped-schema rows: [(layer, class, geometry), ...] in EPSG:4326."""
    return gpd.GeoDataFrame(
        {
            "layer": [entry[0] for entry in entries],
            "class": [entry[1] for entry in entries],
            "name": [None] * len(entries),
            "case_id": [CASE] * len(entries),
            "crs": [C.CRS_OUTPUT] * len(entries),
            "source": ["test fixture"] * len(entries),
            "provenance_id": [PROVENANCE] * len(entries),
        },
        geometry=[entry[2] for entry in entries],
        crs=C.CRS_OUTPUT,
    )


def test_every_precedence_pair_resolves_to_the_higher_class():
    """Precedence must beat area: the higher class is the *larger* polygon in every pair."""
    entries = []
    rows = []
    expected = []
    pairs = list(zip(E.LAND_USE_PRECEDENCE, E.LAND_USE_PRECEDENCE[1:], strict=False))
    # The last pair (bare > unknown) has no "unknown feature": unknown is the absence of a match,
    # and that case is covered by test_cell_with_no_containing_feature_is_unknown.
    for index, (higher, lower) in enumerate(pair for pair in pairs if pair[1] != "unknown"):
        lon, lat = LON + index * 0.05, LAT
        rows.append({"cell_id": f"cell-{index:02d}", "lat": lat, "lon": lon})
        higher_layer, higher_class = LAND_USE_SOURCE[higher]
        lower_layer, lower_class = LAND_USE_SOURCE[lower]
        entries.append((higher_layer, higher_class, box(lon - 0.02, lat - 0.02, lon + 0.02, lat + 0.02)))
        entries.append((lower_layer, lower_class, box(lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001)))
        expected.append(higher)
    result = E.join_land_use(pd.DataFrame(rows), context_rows(entries))
    assert result["land_use"].tolist() == expected


def test_cell_with_no_containing_feature_is_unknown():
    context = mapped([box(LON, LAT, LON + 0.01, LAT + 0.01)], landuse="residential")
    grid = grid_frame(
        [
            {"cell_id": "inside", "lat": LAT + 0.005, "lon": LON + 0.005},
            {"cell_id": "outside", "lat": LAT + 1.0, "lon": LON + 1.0},
        ]
    )
    result = E.join_land_use(grid, context)
    assert result.set_index("cell_id")["land_use"].to_dict() == {
        "inside": "urban-residential",
        "outside": "unknown",
    }
    assert result.set_index("cell_id").loc["outside", "context_id"] == ""


def test_an_admin_boundary_never_assigns_a_land_use():
    """A boundary covers everything; if it won the join, the whole grid would read 'unknown'."""
    boundary = E.mmap_source_features(
        make_gdf([box(LON - 1, LAT - 1, LON + 1, LAT + 1)], place="city"), CASE, PROVENANCE
    )
    assert boundary["layer"].tolist() == ["admin_boundary"]
    grid = grid_frame([{"cell_id": "c", "lat": LAT, "lon": LON}])
    result = E.join_land_use(grid, boundary)
    assert result["land_use"].tolist() == ["unknown"]


def test_join_rejects_a_grid_that_breaks_the_grid_contract():
    context = mapped([box(LON, LAT, LON + 0.01, LAT + 0.01)], landuse="residential")
    duplicate = grid_frame(
        [{"cell_id": "same", "lat": LAT, "lon": LON}, {"cell_id": "same", "lat": LAT, "lon": LON}]
    )
    with pytest.raises(ValueError, match="duplicate cell_id"):
        E.join_land_use(duplicate, context)
    with pytest.raises(ValueError, match="null lat/lon"):
        E.join_land_use(grid_frame([{"cell_id": "c", "lat": None, "lon": LON}]), context)
    with pytest.raises(ValueError, match="missing columns"):
        E.join_land_use(pd.DataFrame({"cell_id": ["c"]}), context)
    with pytest.raises(ValueError, match="mmap_source_features"):
        E.join_land_use(grid_frame([{"cell_id": "c", "lat": LAT, "lon": LON}]), make_gdf([box(0, 0, 1, 1)], landuse="x"))


# ---------------------------------------------------------------------------------------------
# 3. Reprojection
# ---------------------------------------------------------------------------------------------


def test_a_projected_source_is_reprojected_and_lands_in_the_right_place():
    known = gpd.GeoSeries([Point(LON, LAT)], crs=C.CRS_OUTPUT).to_crs("EPSG:32614").iloc[0]
    square = box(known.x - 500, known.y - 500, known.x + 500, known.y + 500)
    source = gpd.GeoDataFrame({"landuse": ["residential"]}, geometry=[square], crs="EPSG:32614")

    out = E.mmap_source_features(source, CASE, PROVENANCE)
    assert str(out.crs).upper() == C.CRS_OUTPUT
    assert out.attrs["reprojected"] is True
    assert out.attrs["source_crs"] == "EPSG:32614"

    # 1 m tolerance, measured in the metric CRS the source came from.
    back = gpd.GeoSeries([out.geometry.iloc[0]], crs=C.CRS_OUTPUT).to_crs("EPSG:32614").iloc[0]
    assert back.bounds == pytest.approx(square.bounds, abs=1.0)
    centroid = out.geometry.iloc[0].centroid
    assert centroid.x == pytest.approx(LON, abs=1e-5)
    assert centroid.y == pytest.approx(LAT, abs=1e-5)


# ---------------------------------------------------------------------------------------------
# 4. Risk levels are re-derived, never re-thresholded
# ---------------------------------------------------------------------------------------------


def test_derive_risk_level_reproduces_the_contract_and_reports_disagreements():
    velocities = [-0.0, 1e-9, 4.999, 5.0, 24.999, 25.0, -25.0, -420.0, math.nan]
    derived = [C.risk_level_from_velocity(value) for value in velocities]
    grid = pd.DataFrame(
        {
            "cell_id": [f"cell-{index}" for index in range(len(velocities))],
            "velocity_mm_yr": velocities,
            "risk_level": derived,
        }
    )
    assert E.derive_risk_level(grid).empty, "a conformant grid must report no disagreement"

    corrupt = grid.copy()
    corrupt.loc[corrupt["cell_id"] == "cell-3", "risk_level"] = "estable"  # 5.0 mm/yr is moderado
    report = E.derive_risk_level(corrupt)
    assert list(report.columns) == ["cell_id", "velocity_mm_yr", "risk_level_stored", "risk_level_derived"]
    assert report["cell_id"].tolist() == ["cell-3"]
    assert report.iloc[0]["risk_level_stored"] == "estable"
    assert report.iloc[0]["risk_level_derived"] == "moderado"
    assert report.iloc[0]["risk_level_derived"] == C.risk_level_from_velocity(5.0)


def test_derive_risk_level_never_invents_a_threshold():
    """A velocity just under and just over the frozen 5 mm/yr edge must fall on opposite sides."""
    grid = pd.DataFrame(
        {
            "cell_id": ["a", "b"],
            "velocity_mm_yr": [4.999999, 5.000001],
            "risk_level": ["estable", "moderado"],
        }
    )
    assert E.derive_risk_level(grid).empty
    assert E.derive_risk_level(grid.assign(risk_level=["moderado", "estable"]))["cell_id"].tolist() == ["a", "b"]


# ---------------------------------------------------------------------------------------------
# 5. The emitted GeoJSON is a valid artefact
# ---------------------------------------------------------------------------------------------


def test_emitted_geojson_passes_the_frozen_validator():
    bowtie = Polygon([(LON, LAT), (LON + 0.01, LAT + 0.01), (LON + 0.01, LAT), (LON, LAT + 0.01)])
    source = gpd.GeoDataFrame(
        {
            "landuse": ["residential", None, None, None, None, "quarry"],
            "natural": [None, "water", None, None, None, None],
            "waterway": [None, None, "canal", None, None, None],
            "man_made": [None, None, None, "pipeline", None, None],
            "amenity": [None, None, None, None, "school", None],
            "name": ["colonia", "lago", "canal", "tubo", "escuela", "cantera"],
        },
        geometry=[
            bowtie,  # invalid polygon: must be repaired, not dropped
            Polygon([(LON + 0.02, LAT), (LON + 0.04, LAT), (LON + 0.04, LAT + 0.02), (LON + 0.02, LAT + 0.02)]),
            LineString([(LON, LAT), (LON + 0.02, LAT + 0.01)]),
            MultiLineString([[(LON, LAT), (LON + 0.01, LAT + 0.01)], [(LON + 0.02, LAT), (LON + 0.03, LAT + 0.01)]]),
            Point(LON + 0.03, LAT + 0.03),
            MultiPolygon([box(LON, LAT, LON + 0.005, LAT + 0.005), box(LON + 0.01, LAT, LON + 0.015, LAT + 0.005)]),
        ],
        crs=C.CRS_OUTPUT,
    )
    out = E.mmap_source_features(source, CASE, PROVENANCE)
    assert set(out.geometry.geom_type) <= E.ALLOWED_GEOMETRY_TYPES
    collection = E.build_feature_collection(out)
    assert C.validate_context_layers(collection) == []

    # The file is deterministic: building it twice gives the same bytes, shuffled input or not.
    shuffled = E.mmap_source_features(source.iloc[[5, 3, 1, 4, 0, 2]], CASE, PROVENANCE)
    assert E.build_feature_collection(shuffled) == collection

    # Name is optional, and only present when the source had one.
    for feature in collection["features"]:
        properties = feature["properties"]
        assert set(properties) <= {"layer", "class", "name", "case_id", "crs", "source", "provenance_id"}
        assert properties["crs"] == C.CRS_OUTPUT
        assert C.PROVENANCE_ID_RE.match(properties["provenance_id"])
        if "name" in properties:
            assert properties["name"].strip()


def test_geojson_coordinates_are_rounded_and_json_safe():
    out = mapped([box(LON, LAT, LON + 0.000123456789, LAT + 0.000123456789)], landuse="residential")
    collection = E.build_feature_collection(out)
    ring = collection["features"][0]["geometry"]["coordinates"][0]
    assert all(isinstance(point, list) for point in ring)
    assert all(len(str(value).split(".")[-1]) <= E.COORD_DECIMALS for point in ring for value in point)
    # allow_nan=False in the writer: a NaN coordinate would be a bug, not a missing value.
    json.dumps(collection, allow_nan=False)


def test_select_in_aoi_reprojects_and_reports_nothing_when_empty():
    aoi = E.load_case(CASE).aoi_geometry()
    inside = gpd.GeoDataFrame({"landuse": ["residential"]}, geometry=[box(LON, LAT, LON + 0.01, LAT + 0.01)], crs="EPSG:4326")
    selected = E.select_in_aoi(inside, aoi)
    assert len(selected) == 1 and str(selected.crs).upper() == C.CRS_OUTPUT
    outside = gpd.GeoDataFrame({"landuse": ["residential"]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    assert E.select_in_aoi(outside, aoi).empty


# ---------------------------------------------------------------------------------------------
# 6. CLI: writes the artefact, never the parquet, and fails closed
# ---------------------------------------------------------------------------------------------


def write_source_geojson(path: pathlib.Path, features: list[dict]) -> pathlib.Path:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return path


def feature(geometry: dict, properties: dict) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def cli_grid(path: pathlib.Path) -> pd.DataFrame:
    """A minimal but contract-shaped grid: three cells, one wrong risk_level on purpose."""
    frame = pd.DataFrame(
        {
            "cell_id": ["8928308280ff1", "8928308280ff2", "8928308280ff3"],
            "lat": [19.400, 19.450, 19.500],
            "lon": [-99.200, -99.120, -99.250],
            "velocity_mm_yr": [-120.0, -8.0, 1.0],
            "risk_level": ["critico", "moderado", "estable"],
            "provenance_id": [PROVENANCE] * 3,
        }
    )
    frame.to_parquet(path, index=False)
    return frame


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MODULE), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )


def test_cli_writes_a_valid_artefact_end_to_end(tmp_path):
    grid_path = tmp_path / "nisar_grid.parquet"
    cli_grid(grid_path)
    before = grid_bytes(grid_path)

    context_path = write_source_geojson(
        tmp_path / "context.geojson",
        [
            feature(
                {"type": "Polygon", "coordinates": [[[-99.25, 19.38], [-99.15, 19.38], [-99.15, 19.42], [-99.25, 19.42], [-99.25, 19.38]]]},
                {"landuse": "residential", "name": "colonia"},
            ),
            feature(
                {"type": "Polygon", "coordinates": [[[-99.13, 19.44], [-99.11, 19.44], [-99.11, 19.46], [-99.13, 19.46], [-99.13, 19.44]]]},
                {"natural": "water", "name": "lago"},
            ),
        ],
    )
    out_path = tmp_path / "context_layers.geojson"
    land_use_path = tmp_path / "land_use.csv"

    completed = run_cli(
        "--case",
        CASE,
        "--context",
        str(context_path),
        "--grid",
        str(grid_path),
        "--out",
        str(out_path),
        "--land-use-out",
        str(land_use_path),
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["features_in_aoi"] == 2
    assert summary["aoi_feature_added"] is True
    assert summary["validation_problems"] == []
    assert summary["cells_total"] == 3
    assert summary["cells_by_land_use"] == {"unknown": 1, "urban-residential": 1, "water": 1}
    assert summary["cells_unknown"] == 1
    assert summary["grid_file_modified"] is False
    assert "fold it into nisar_grid.parquet" in summary["sidecar_note"]
    assert summary["risk_level_check"]["disagreements"] == 0

    collection = json.loads(out_path.read_text(encoding="utf-8"))
    assert C.validate_context_layers(collection) == []
    assert any(f["properties"]["class"] == "aoi" for f in collection["features"])

    sidecar = pd.read_csv(land_use_path)
    assert list(sidecar.columns) == ["cell_id", "land_use"]
    assert set(sidecar["land_use"]) <= set(C.LAND_USE)
    merged = (
        pd.read_parquet(grid_path)
        .merge(sidecar, on="cell_id", how="left", validate="one_to_one")
        .rename(columns={"land_use": "land_use_context"})
    )
    assert merged["land_use_context"].isna().sum() == 0, "sidecar does not cover every grid cell"
    assert set(merged["land_use_context"]) == {"unknown", "urban-residential", "water"}

    assert grid_bytes(grid_path) == before, "the CLI must not rewrite nisar_grid.parquet"


def test_cli_status_level_output_names_the_sidecar(tmp_path):
    grid_path = tmp_path / "nisar_grid.parquet"
    cli_grid(grid_path)
    context_path = write_source_geojson(
        tmp_path / "context.geojson",
        [feature({"type": "Point", "coordinates": [LON, LAT]}, {"amenity": "school", "name": "escuela"})],
    )
    completed = run_cli(
        "--case",
        CASE,
        "--context",
        str(context_path),
        "--grid",
        str(grid_path),
        "--out",
        str(tmp_path / "out.geojson"),
        "--land-use-out",
        str(tmp_path / "land_use.csv"),
    )
    assert completed.returncode == 0, completed.stderr
    assert "NOT rewritten" in completed.stdout
    assert "validate_artifacts.py" in completed.stdout  # the staging-sidecar reminder
    # A single point feature contains no centroid, so every cell stays unknown — honestly.
    sidecar = pd.read_csv(tmp_path / "land_use.csv")
    assert set(sidecar["land_use"]) == {"unknown"}


def test_cli_fails_when_the_context_has_no_features_in_the_aoi(tmp_path):
    grid_path = tmp_path / "nisar_grid.parquet"
    cli_grid(grid_path)
    context_path = write_source_geojson(
        tmp_path / "context.geojson",
        [feature({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}, {"landuse": "residential"})],
    )
    out_path = tmp_path / "context_layers.geojson"
    land_use_path = tmp_path / "land_use.csv"
    completed = run_cli(
        "--case",
        CASE,
        "--context",
        str(context_path),
        "--grid",
        str(grid_path),
        "--out",
        str(out_path),
        "--land-use-out",
        str(land_use_path),
    )
    assert completed.returncode != 0
    assert "no features inside the AOI" in completed.stderr
    assert not out_path.exists(), "an empty artefact must not be written"
    assert not land_use_path.exists()


def test_cli_fails_cleanly_on_a_missing_grid_or_unknown_case(tmp_path):
    context_path = write_source_geojson(
        tmp_path / "context.geojson",
        [feature({"type": "Polygon", "coordinates": [[[LON, LAT], [LON + 0.01, LAT], [LON + 0.01, LAT + 0.01], [LON, LAT + 0.01], [LON, LAT]]]}, {"landuse": "residential"})],
    )
    missing = run_cli(
        "--case", CASE, "--context", str(context_path), "--grid", str(tmp_path / "nope.parquet"),
        "--out", str(tmp_path / "out.geojson"), "--land-use-out", str(tmp_path / "lu.csv"),
    )
    assert missing.returncode == 2
    assert "grid not found" in missing.stderr

    unknown_case = run_cli(
        "--case", "atlantis", "--context", str(context_path), "--grid", str(tmp_path / "nope.parquet"),
        "--out", str(tmp_path / "out.geojson"), "--land-use-out", str(tmp_path / "lu.csv"),
    )
    assert unknown_case.returncode == 2
    assert "undeclared case" in unknown_case.stderr


def test_cli_print_mapping_documents_the_table():
    completed = run_cli("--print-mapping")
    assert completed.returncode == 0, completed.stderr
    assert "landuse=residential" in completed.stdout
    assert " > ".join(E.LAND_USE_PRECEDENCE) in completed.stdout


def test_cli_fails_closed_and_leaves_the_previous_artefact_untouched(tmp_path, monkeypatch, capsys):
    """A generated artefact that misses the contract must not replace a valid one on disk."""
    grid_path = tmp_path / "nisar_grid.parquet"
    cli_grid(grid_path)
    context_path = write_source_geojson(
        tmp_path / "context.geojson",
        [feature({"type": "Polygon", "coordinates": [[[LON, LAT], [LON + 0.01, LAT], [LON + 0.01, LAT + 0.01], [LON, LAT + 0.01], [LON, LAT]]]}, {"landuse": "residential"})],
    )
    out_path = tmp_path / "context_layers.geojson"
    out_path.write_text('{"previous": "valid artefact"}', encoding="utf-8")
    land_use_path = tmp_path / "land_use.csv"

    monkeypatch.setattr(E, "build_feature_collection", lambda _: {"type": "FeatureCollection", "features": []})
    exit_code = E.main(
        [
            "--case", CASE,
            "--context", str(context_path),
            "--grid", str(grid_path),
            "--out", str(out_path),
            "--land-use-out", str(land_use_path),
            "--json",
        ]
    )
    assert exit_code == 1
    assert "nothing written" in capsys.readouterr().err
    assert out_path.read_text(encoding="utf-8") == '{"previous": "valid artefact"}'
    assert not land_use_path.exists()
