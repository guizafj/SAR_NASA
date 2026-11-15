"""Guard the OSM half of the geography: element geometry, the pinned context and its tag table.

Why this module exists
----------------------
The published `land_use` of a cell, the water/infrastructure points on the map and the sea polygon
that turns sea cells into `water` all come from one path: raw Overpass elements -> geometry
(`pipeline/ingest/osm_context.py`) -> frozen vocabulary (`pipeline/enrich_context.py`) -> either the
committed pin (`config/context/<case>.json`) or a live centroid join. Each link has a failure mode
that is silent in the artefact and only visible here:

* a self-intersecting ring published as-is is an invalid polygon a consumer will choke on;
* an unassemblable relation "repaired" into something plausible invents land that was never mapped;
* a deduplication keyed on the tile instead of on `(type, id)` doubles a coastline and moves it;
* a pin whose labels and features disagree makes the map and the grid tell different stories;
* a tag rule pointing outside `contracts.LAND_USE` (a typo, a class edited in the wrong place) puts
  a value in `nisar_grid.parquet` that the contract does not declare.

Hermetic on purpose: every extract below is a handful of elements written under `tmp_path`, so this
runs on a clone with no `data/raw/` (gitignored) and without touching Overpass. No test here reads
`data/processed/`, so a concurrent artefact regeneration cannot make it flaky.
"""

from __future__ import annotations

import json
import pathlib
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python pipeline/ingest/osm_context.py` and pytest from the repo root
    sys.path.insert(0, str(ROOT))

from pipeline import context as CTX  # noqa: E402
from pipeline import enrich_context as E  # noqa: E402
from pipeline.ingest import osm_context as OSM  # noqa: E402
from pipeline.ingest import pin_context as PIN  # noqa: E402
from src.domain import contracts as C  # noqa: E402

CASE = "malaga"
#: A valid frozen provenance id: the pin reader stamps whatever the artefact carries, so the shape
#: here is the same one `nisar_grid.parquet` uses.
PROVENANCE = "malaga+mock+20261114+abc12345"
#: Every counter key `_relation_geometry` touches; a missing one is a KeyError, not a default.
COUNTER_KEYS = ("assembled", "unassembled", "non_way_member", "holes_failed", "node")


# ---------------------------------------------------------------------------------------------
# Fixtures / helpers (in memory or under tmp_path; nothing is fetched)
# ---------------------------------------------------------------------------------------------


def counters() -> dict[str, int]:
    return dict.fromkeys(COUNTER_KEYS, 0)


def way_element(coords, ident: int = 1, **tags) -> dict:
    """A `way` as `out geom` returns it: an explicit node list, no node store."""
    element: dict = {
        "type": "way",
        "id": ident,
        "geometry": [{"lon": float(lon), "lat": float(lat)} for lon, lat in coords],
    }
    if tags:
        element["tags"] = tags
    return element


def node_element(ident: int, lon: float, lat: float, **tags) -> dict:
    element: dict = {"type": "node", "id": ident, "lon": lon, "lat": lat}
    if tags:
        element["tags"] = tags
    return element


def member(coords, role: str) -> dict:
    """One relation member carrying its own node list (`role` is what makes it an inner ring)."""
    return {
        "type": "way",
        "role": role,
        "geometry": [{"lon": float(lon), "lat": float(lat)} for lon, lat in coords],
    }


def relation_element(ident: int, members: list[dict]) -> dict:
    return {"type": "relation", "id": ident, "members": members}


def square(lon: float, lat: float, size: float) -> list[tuple[float, float]]:
    return [
        (lon, lat),
        (lon + size, lat),
        (lon + size, lat + size),
        (lon, lat + size),
        (lon, lat),
    ]


def build_extract(
    root: pathlib.Path,
    tiles: list[list[dict]],
    *,
    statuses: list[dict] | None = None,
    case_id: str = CASE,
) -> pathlib.Path:
    """Write `root/<case>/context/{manifest.json,tiles/*.json}` as the loader expects to find it."""
    directory = OSM.context_dir(case_id, root)
    (directory / "tiles").mkdir(parents=True, exist_ok=True)
    manifest = {
        "retrieved_utc": "2026-11-14T00:00:00Z",
        "timestamp_osm_base": "2026-11-13T00:00:00Z",
        "bbox": [0.0, 0.0, 0.2, 0.2],
        "tiles": statuses
        if statuses is not None
        else [{"status": 200, "elements": len(elements)} for elements in tiles],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for index, elements in enumerate(tiles):
        path = directory / "tiles" / f"t{index:02d}.json"
        path.write_text(json.dumps({"version": 0.6, "elements": elements}), encoding="utf-8")
    return directory


def empty_features() -> gpd.GeoDataFrame:
    """A `CaseContext.features` frame with no rows: the pinned labels are the whole answer."""
    return gpd.GeoDataFrame(
        {
            "layer": [],
            "class": [],
            "name": [],
            "case_id": [],
            "crs": [],
            "source": [],
            "provenance_id": [],
        },
        geometry=[],
        crs=C.CRS_OUTPUT,
    )


def mapped_features(entries) -> gpd.GeoDataFrame:
    """Mapped-schema rows `[(layer, class, geometry), ...]` — the shape `features` carries."""
    return gpd.GeoDataFrame(
        {
            "layer": [entry[0] for entry in entries],
            "class": [entry[1] for entry in entries],
            "name": [None] * len(entries),
            "case_id": [CASE] * len(entries),
            "crs": [C.CRS_OUTPUT] * len(entries),
            "source": [OSM.SOURCE] * len(entries),
            "provenance_id": [PROVENANCE] * len(entries),
        },
        geometry=[entry[2] for entry in entries],
        crs=C.CRS_OUTPUT,
    )


def pin_payload(labels: dict[str, str] | None = None) -> dict:
    """The `config/context/<case>.json` shape `pin_context.build_pin` writes (built by hand: no raw)."""
    return {
        "case_id": CASE,
        "crs": C.CRS_OUTPUT,
        "source": OSM.SOURCE,
        "record": {
            "source": OSM.SOURCE,
            "retrieved_utc": "2026-11-14T00:00:00Z",
            "timestamp_osm_base": "2026-11-13T00:00:00Z",
            "tiles_ok": 2,
            "tiles_failed": 0,
            "elements": 7,
            "bbox": [0.0, 0.0, 0.2, 0.2],
        },
        # Two real resolution-9 H3 cells over Málaga: the reader now refuses an id no grid cell can
        # answer for, so the fixture carries ids that exist rather than placeholders.
        "cell_land_use": dict(
            labels or {"893919584dbffff": "vegetation", "8939195b06fffff": "urban-dense"}
        ),
        "water": [
            {
                "type": "Feature",
                "properties": {"class": "river", "source": OSM.SOURCE, "name": "Rio"},
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.1, 0.1]]},
            }
        ],
        "infrastructure": [
            {
                "type": "Feature",
                "properties": {"class": "power", "source": OSM.SOURCE},
                "geometry": {"type": "Point", "coordinates": [0.05, 0.05]},
            }
        ],
        "admin": [
            {
                "type": "Feature",
                "properties": {"class": "municipality", "source": OSM.SOURCE},
                "geometry": {"type": "Polygon", "coordinates": [square(0.0, 0.0, 0.2)]},
            }
        ],
    }


def write_pin_bytes(payload: dict, path: pathlib.Path) -> tuple[bytes, dict]:
    PIN.write_pin(payload, path)
    body = path.read_bytes()
    return body, json.loads(body.decode("utf-8"))


def write_canonical(body: dict, path: pathlib.Path) -> None:
    """Write the body the way the pin writer would *without* re-stamping the digest.

    The canonicalisation is deliberately spelled out here instead of reusing `PIN.write_pin`: this is
    the hand-edit the reader must catch, so the bytes have to be canonical and the digest stale.
    """
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_text(text, encoding="utf-8")


def committed_pin(tmp_path: pathlib.Path, case_id: str = CASE) -> tuple[pathlib.Path, dict]:
    """A writable copy of a **committed** pin under `tmp_path`; `config/context/` is never touched."""
    payload = CTX.read_pin(case_id)
    if payload is None:
        pytest.skip(f"no committed pin for {case_id} (config/context/{case_id}.json is absent)")
    path = tmp_path / f"{case_id}.json"
    PIN.write_pin(payload, path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_every_contract_case_has_a_committed_pin() -> None:
    """Every case the contract names must ship its pin, or the guard below would go quiet.

    `committed_pin` and `test_the_pin_keeps_the_derived_sea_polygon` skip a case with no pin: an
    honest state for a case whose context is the declared synthetic fallback, which was never
    derived from OSM and has nothing to store. It is *not* an honest state for the three contract
    cases — their pins are committed under `config/context/` — and moving that directory aside used
    to remove 10 of this module's 47 items (`37 passed, 10 skipped`) while the file exited 0.

    Deliberately *not* parametrised over `C.CASE_IDS`: a guard that discovers its own inputs cannot
    notice its inputs disappearing, so this one asks the question directly instead.
    """
    missing = [case_id for case_id in C.CASE_IDS if CTX.read_pin(case_id) is None]
    assert not missing, (
        f"config/context/ carries no pin for {missing}. Every contract case is OSM-derived and its "
        "pin is committed, so a missing one is a defect, not a case to skip: restore "
        "config/context/<case>.json (or re-pin the extract with "
        "`./scripts/python.sh pipeline/ingest/pin_context.py`)."
    )
    assert C.CASE_IDS, "the contract declares no case at all, so no pin could be verified"


# ---------------------------------------------------------------------------------------------
# 1. A raw element becomes the geometry it actually is
# ---------------------------------------------------------------------------------------------


def test_a_closed_way_becomes_a_polygon_an_open_way_a_line():
    """The area a way encloses is the whole point: publishing a lake as a line loses it."""
    ring = way_element(square(0.0, 0.0, 0.01))
    geometry = OSM._way_geometry(ring)
    assert isinstance(geometry, Polygon), f"a closed ring came back as {getattr(geometry, 'geom_type', None)}"
    assert geometry.is_valid
    assert geometry.area == pytest.approx(0.01**2)

    line = OSM._way_geometry(way_element([(0.0, 0.0), (0.01, 0.01)]))
    assert isinstance(line, LineString), f"an open way came back as {getattr(line, 'geom_type', None)}"
    assert line.length > 0.0


def test_a_way_without_enough_nodes_is_dropped_instead_of_becoming_a_degenerate_shape():
    """A one-node way has no extent; a 3-node "ring" encloses no area, so it is a line, not a polygon."""
    assert OSM._way_geometry(way_element([(0.0, 0.0)])) is None
    assert OSM._way_geometry({"type": "way", "id": 2}) is None
    degenerate = OSM._way_geometry(way_element([(0.0, 0.0), (0.01, 0.0), (0.0, 0.0)]))
    assert not isinstance(degenerate, Polygon), "a ring with no area must never be published as a polygon"


def test_a_self_intersecting_ring_is_repaired_and_never_published_invalid():
    """OSM bows self-intersect routinely; an invalid polygon breaks every consumer downstream."""
    bowtie = [(0.0, 0.0), (0.01, 0.01), (0.01, 0.0), (0.0, 0.01), (0.0, 0.0)]
    assert not Polygon(bowtie).is_valid, "the fixture must be a real bowtie, or the repair is untested"
    geometry = OSM._way_geometry(way_element(bowtie))
    assert geometry is not None
    assert geometry.is_valid
    assert geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert not geometry.is_empty


# ---------------------------------------------------------------------------------------------
# 2. A multipolygon relation is assembled, holed and counted — never guessed at
# ---------------------------------------------------------------------------------------------


def test_a_relation_subtracts_its_inner_ring_from_the_covered_area():
    """A courtyard or an atoll is not built land: the hole has to be gone from the area, not filled."""
    outer = square(0.0, 0.0, 0.02)
    inner = square(0.005, 0.005, 0.01)
    counts = counters()
    geometry = OSM._relation_geometry(
        relation_element(9, [member(outer, "outer"), member(inner, "inner")]), counts
    )
    assert geometry is not None
    assert geometry.is_valid
    assert geometry.area == pytest.approx(Polygon(outer).area - Polygon(inner).area, rel=1e-9)
    assert not geometry.contains(Point(0.010, 0.010)), "the inner ring was filled back in"
    assert geometry.contains(Point(0.001, 0.001)), "the outer ring was lost"
    assert counts["assembled"] == 1
    assert counts["unassembled"] == 0


def test_an_unassemblable_relation_is_counted_and_returns_none_without_raising():
    """Members that never close into a ring are a gap, not an invitation to invent a polygon."""
    open_lines = [
        member([(0.0, 0.0), (0.01, 0.0)], "outer"),
        member([(0.05, 0.05), (0.06, 0.06)], "outer"),
    ]
    counts = counters()
    assert OSM._relation_geometry(relation_element(10, open_lines), counts) is None
    assert counts["unassembled"] == 1
    assert counts["assembled"] == 0

    only_nodes = counters()
    node_member = {"type": "node", "role": "outer", "lat": 0.0, "lon": 0.0}
    assert OSM._relation_geometry(relation_element(11, [node_member]), only_nodes) is None
    assert only_nodes["non_way_member"] == 1
    assert only_nodes["unassembled"] == 1


# ---------------------------------------------------------------------------------------------
# 3. The loader: identity, order, honesty about what it drops
# ---------------------------------------------------------------------------------------------


def test_an_element_carried_by_two_tiles_enters_the_frame_once(tmp_path):
    """A way touching two tiles comes back twice; a doubled coastline is a moved coastline."""
    shared = node_element(1, 0.1, 0.1, landuse="residential")
    build_extract(tmp_path, [[shared, way_element(square(0.0, 0.0, 0.01), ident=5)], [shared]])

    frame = OSM.load_extract(CASE, tmp_path)
    identities = list(zip(frame["osm_type"], frame["osm_id"], strict=True))
    assert identities == [("node", 1), ("way", 5)]
    assert not frame.duplicated(["osm_type", "osm_id"]).any()


def test_the_frame_is_sorted_so_tile_order_cannot_change_it(tmp_path):
    """Two tiles, two machines, two element orders: the published frame must be the same frame."""
    elements = [node_element(9, 0.3, 0.3, landuse="grass"), way_element(square(0.0, 0.0, 0.01), ident=2)]
    forward = tmp_path / "forward"
    backward = tmp_path / "backward"
    build_extract(forward, [elements])
    build_extract(backward, [[], list(reversed(elements))])

    first = OSM.load_extract(CASE, forward)
    second = OSM.load_extract(CASE, backward)
    ordering = list(zip(first["osm_type"], first["osm_id"], strict=True))
    assert ordering == sorted(ordering), f"frame is not sorted by (osm_type, osm_id): {ordering}"
    assert ordering == list(zip(second["osm_type"], second["osm_id"], strict=True))
    assert first.geometry.to_wkt().tolist() == second.geometry.to_wkt().tolist()


def test_an_element_with_no_usable_geometry_is_dropped(tmp_path):
    """`out geom` sometimes returns a node without coordinates; publishing it would place it nowhere."""
    build_extract(
        tmp_path,
        [
            [
                {"type": "node", "id": 1, "tags": {"landuse": "grass"}},
                way_element([(0.0, 0.0)], ident=2),
                node_element(3, 0.1, 0.1),
            ]
        ],
    )
    frame = OSM.load_extract(CASE, tmp_path)
    assert frame["osm_id"].tolist() == [3]


def test_every_tag_key_becomes_a_column_and_an_absent_tag_stays_absent(tmp_path):
    """`TAG_KEYS` is the frame's contract with the tag table; a missing column silently disables rules."""
    build_extract(tmp_path, [[node_element(1, 0.1, 0.1, landuse="grass", name="Parque")]])
    frame = OSM.load_extract(CASE, tmp_path)

    for key in OSM.TAG_KEYS:
        assert key in frame.columns, f"{key!r} from TAG_KEYS never reached the frame"
    assert frame["landuse"].iloc[0] == "grass"
    assert frame["name"].iloc[0] == "Parque"
    # Absent is absence (None/NaN), never a fabricated tag value: an invented `landuse` would be
    # a classification no extract ever made.
    assert frame["natural"].isna().all(), f"an untagged key invented a value: {frame['natural'].tolist()}"
    assert frame["building"].isna().all()


def test_a_case_without_a_manifest_is_a_hard_stop(tmp_path):
    """The caller decides whether absence is a fallback; the loader must not guess a directory."""
    (OSM.context_dir(CASE, tmp_path)).mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        OSM.load_extract(CASE, tmp_path)


# ---------------------------------------------------------------------------------------------
# 4. The retrieval record counts what was actually retrieved
# ---------------------------------------------------------------------------------------------


def test_extract_record_counts_only_the_tiles_that_succeeded(tmp_path):
    """A 502 tile carries no summary; counting its `elements` would claim data we never received."""
    build_extract(
        tmp_path,
        [[], []],
        statuses=[{"status": 200, "elements": 5}, {"status": 502, "elements": 9}],
    )
    record = OSM.extract_record(CASE, tmp_path)
    assert record is not None
    assert record.tiles_ok == 1
    assert record.tiles_failed == 1
    assert record.elements == 5, "elements were summed over the failed tiles too"
    assert record.source == OSM.SOURCE


# ---------------------------------------------------------------------------------------------
# 5. Coastline: an unsigned distance, and a sea derived as a polygon (never a side flag)
# ---------------------------------------------------------------------------------------------

#: A bbox ring that splits into exactly two candidates: north and south of a west→east shoreline.
SEA_BBOX = (-0.1, -0.1, 0.1, 0.1)
SHORELINE = LineString([(-0.1, 0.0), (0.1, 0.0)])
#: 0.009 degrees of latitude is ~1.0 km: the scale `Coast.distance` reports in.
ONE_KM_NORTH = 0.009


def test_the_coast_distance_is_unsigned_on_both_sides_of_the_shoreline():
    """The sign is gone on purpose: one global side per case labelled Jakarta's land as water, so
    the coastline now only answers `how far`, and it answers the same on both sides."""
    coast = CTX.Coast(lines=(((-1.0, 0.0), (1.0, 0.0)),), sea_side=0)
    north = coast.distance(np.array([0.0]), np.array([ONE_KM_NORTH]))
    south = coast.distance(np.array([0.0]), np.array([-ONE_KM_NORTH]))
    assert north[0] == pytest.approx(1.0, abs=0.05), f"1 km north measured {north[0]:.3f} km"
    assert south[0] == pytest.approx(1.0, abs=0.05), f"1 km south measured {south[0]:.3f} km"
    assert north[0] == pytest.approx(south[0]), "the distance still carries a side"
    assert not hasattr(CTX.Coast, "signed_distance"), (
        "a signed distance came back: a bay's shoreline runs in both directions, so a sign cannot "
        "say which side is sea"
    )


def test_the_nearest_coastline_is_the_one_that_measures_the_distance():
    """A real AOI carries several lines (harbour, river mouth); the far one must not win."""
    near = ((-1.0, 0.0), (1.0, 0.0))
    far = ((-1.0, 5.0), (1.0, 5.0))
    lon = np.array([0.0])
    lat = np.array([ONE_KM_NORTH])
    both = CTX.Coast(lines=(near, far), sea_side=0).distance(lon, lat)
    near_only = CTX.Coast(lines=(near,), sea_side=0).distance(lon, lat)
    far_only = CTX.Coast(lines=(far,), sea_side=0).distance(lon, lat)
    assert far_only[0] > 100.0 * both[0], f"the far line measured {far_only[0]:.3f} km, not far away"
    assert both[0] == pytest.approx(near_only[0], rel=1e-9), "the far line won over the near one"
    assert both[0] == pytest.approx(1.0, abs=0.05)


def test_the_sea_is_the_polygon_the_mapped_land_use_does_not_cover():
    """Polygonizing the coastline with the bbox ring yields a candidate on BOTH sides; the sea is the
    one mapped `land_use` does not cover. No declared side, no global direction, no guess."""
    coast_only = mapped_features([("water", "coastline", SHORELINE)])
    sea = CTX.sea_polygons(coast_only, SEA_BBOX)
    assert sea is not None, "a coastline plus a bbox produced no sea polygon at all"
    assert sea.contains(Point(0.0, -0.05)), "the half south of the shoreline is not sea"
    assert not sea.contains(Point(0.0, 5.0)), "the sea polygon escaped the AOI bbox"

    # Land use over the north half: that candidate is fully covered, so it stops being sea.
    with_land = mapped_features(
        [("water", "coastline", SHORELINE), ("land_use", "urban-dense", box(-0.1, 0.0, 0.1, 0.1))]
    )
    land_sea = CTX.sea_polygons(with_land, SEA_BBOX)
    assert land_sea is not None, "mapped land use over one half removed the sea as well"
    assert land_sea.contains(Point(0.0, -0.05))
    assert not land_sea.contains(Point(0.0, 0.05)), "mapped land use was still taken as sea"

    # Absence is absence: no coastline or no bbox means no sea, and the caller keeps `unknown`.
    assert CTX.sea_polygons(mapped_features([]), SEA_BBOX) is None
    assert CTX.sea_polygons(coast_only, None) is None


@pytest.mark.parametrize("case_id", C.CASE_IDS)
def test_the_pin_keeps_the_derived_sea_polygon(case_id):
    """The pin holds only the largest water features, so a reader cannot re-derive land-vs-sea: the
    sea is stored with the pin, and a pin that forgot it publishes sea cells with a displacement."""
    payload = CTX.read_pin(case_id)
    if payload is None:
        pytest.skip(f"no committed pin for {case_id} (config/context/{case_id}.json is absent)")
    assert "sea" in payload, f"the pin of {case_id} carries no `sea` key"
    sea = payload["sea"]
    if sea is not None:
        assert isinstance(sea, dict), f"`sea` is a {type(sea).__name__}, not a GeoJSON object"
        assert sea.get("type") in {"Polygon", "MultiPolygon"}, (
            f"`sea` is a {sea.get('type')!r}: the sea is areal, so a line or point is not a sea"
        )
        assert sea.get("coordinates"), "the sea polygon has no coordinates"


# ---------------------------------------------------------------------------------------------
# 6. `label_cells`: the pin answers, water is derived, and absence is `unknown`
# ---------------------------------------------------------------------------------------------


def test_a_pinned_cell_answers_its_label_and_an_unlisted_cell_answers_unknown():
    """`unknown` is published: it is the honest answer for a cell no mapped polygon contains."""
    context = CTX.CaseContext(
        case_id=CASE,
        features=empty_features(),
        coast=None,
        synthetic=False,
        source=OSM.SOURCE,
        labels={"listed": "vegetation"},
    )
    labels = CTX.label_cells(context, ["listed", "unlisted"], np.array([0.0, 0.0]), np.array([0.0, 0.0]))
    assert labels.tolist() == ["vegetation", "unknown"]


def test_a_cell_inside_the_derived_sea_is_water_even_when_the_pin_names_it():
    """A centroid inside `context.sea` is `water` whatever the pin says. Without this a sea cell
    published the displacement of the urban class its pin happened to carry."""
    context = CTX.CaseContext(
        case_id="jakarta",
        features=empty_features(),
        coast=None,
        synthetic=False,
        source=OSM.SOURCE,
        labels={"sea": "urban-dense", "land": "urban-dense"},
        sea=box(0.0, -0.1, 0.2, 0.0),
    )
    labels = CTX.label_cells(
        context, ["sea", "land"], np.array([0.05, 0.05]), np.array([-0.05, 0.05])
    )
    assert labels.tolist() == ["water", "urban-dense"]


def test_without_a_pin_the_labels_come_from_the_centroid_join():
    """The freshly built extract takes the join path; a regression here maps every cell to `unknown`."""
    source = gpd.GeoDataFrame(
        {"landuse": ["residential"]},
        geometry=[box(0.0, 0.0, 0.01, 0.01)],
        crs=C.CRS_OUTPUT,
    )
    features = E.mmap_source_features(source, "mexico-city", "mexico-city+mock+20261114+abc12345")
    context = CTX.CaseContext(
        case_id="mexico-city",
        features=features,
        coast=None,
        synthetic=False,
        source=OSM.SOURCE,
        labels=None,
    )
    labels = CTX.label_cells(
        context, ["inside", "outside"], np.array([0.005, 0.5]), np.array([0.005, 0.5])
    )
    assert labels.tolist() == ["urban-residential", "unknown"]


# ---------------------------------------------------------------------------------------------
# 7. The pin file: deterministic bytes, a self-describing digest, one provenance
# ---------------------------------------------------------------------------------------------


def test_write_pin_read_pin_round_trips_and_the_bytes_are_stable(tmp_path):
    """The pin is what makes regeneration byte-identical on a clone that never fetched the extract."""
    payload = pin_payload()
    path = tmp_path / f"{CASE}.json"
    raw, body = write_pin_bytes(payload, path)

    assert body["pin_sha256"], "the pin carries no digest of itself"
    # Compared after a JSON round-trip of the source: the file stores tuples as arrays, and that
    # change of container is the only difference a round-trip is allowed to make.
    assert {key: value for key, value in body.items() if key != "pin_sha256"} == json.loads(
        json.dumps(payload)
    )
    # `read_pin` verifies `pin_sha256` and returns the payload without it: the digest is a guard, not
    # a field a consumer should re-use. A corrupted or hand-edited pin raises instead of returning.
    assert CTX.read_pin(CASE, tmp_path) == {k: v for k, v in body.items() if k != "pin_sha256"}

    text = raw.decode("utf-8")
    assert text.strip() == json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")), (
        "the pin is not canonical JSON (sorted keys, no spaces): a rewrite would show up as a diff"
    )
    keys = [key for key, _ in json.loads(text, object_pairs_hook=list)]
    assert keys == sorted(keys), f"top-level keys are not written in sorted order: {keys}"

    PIN.write_pin(payload, path)
    assert path.read_bytes() == raw, "writing the same pin twice produced different bytes"


def test_the_pin_digest_changes_when_a_cell_label_changes(tmp_path):
    """A digest that ignores the labels would let a relabelled cell ship without a version change."""
    path = tmp_path / f"{CASE}.json"
    payload = pin_payload()
    _, first = write_pin_bytes(payload, path)

    relabelled = pin_payload()
    relabelled["cell_land_use"]["893919584dbffff"] = "water"
    assert relabelled["cell_land_use"] != payload["cell_land_use"]
    _, second = write_pin_bytes(relabelled, path)
    assert second["pin_sha256"] != first["pin_sha256"]


def test_pinned_context_stamps_the_case_provenance_on_every_feature(tmp_path):
    """The artefact gate requires one `provenance_id` across grid, series and context; the pin has
    no id of its own for a reason."""
    path = tmp_path / f"{CASE}.json"
    _, body = write_pin_bytes(pin_payload(), path)
    context = CTX.pinned_context(CASE, PROVENANCE, body)

    assert not context.synthetic, "a pinned context is fetched geography, not the declared fallback"
    assert context.pinned, "a pinned context does not declare its geometry as already simplified"
    assert not context.features.empty
    assert set(context.features["provenance_id"]) == {PROVENANCE}
    assert set(context.features["crs"]) == {C.CRS_OUTPUT}
    assert set(context.features["layer"]) <= set(C.CONTEXT_LAYERS)
    assert dict(context.labels or {}) == body["cell_land_use"], "the labels drifted from the pin"
    assert context.counters["pinned_cells"] == len(body["cell_land_use"])
    assert context.retrieved_utc == body["record"]["retrieved_utc"]
    assert context.osm_timestamp == body["record"]["timestamp_osm_base"]


@pytest.mark.parametrize("damage", ["absent", "null", "empty"], ids=["deleted", "null", "empty"])
def test_a_pin_without_a_usable_digest_is_refused_instead_of_accepted_unverified(tmp_path, damage):
    """A pin that simply drops `pin_sha256` used to be accepted with no check at all: absence is
    not a verified body, it is a body nobody verified. The field is required, and the failure names
    the file so the corrupt pin is findable without a bisect."""
    path, body = committed_pin(tmp_path)
    # Positive control: the untouched copy reads, so the raise below is attributable to the digest.
    assert CTX.read_pin(CASE, tmp_path)["cell_land_use"]

    if damage == "absent":
        del body["pin_sha256"]
    else:
        body["pin_sha256"] = None if damage == "null" else ""
    write_canonical(body, path)

    with pytest.raises(ValueError) as excinfo:
        CTX.read_pin(CASE, tmp_path)
    message = str(excinfo.value)
    assert str(path) in message, f"the failure does not name the file: {message}"
    assert "pin_sha256" in message and "missing" in message, (
        f"a pin with no digest failed for some other reason: {message}"
    )


def test_a_tampered_pin_body_still_fails_the_digest_it_carries(tmp_path):
    """The canonical re-hash is the whole guard: one relabelled cell under the old digest must raise
    before the label can reach `nisar_grid.parquet`."""
    path, body = committed_pin(tmp_path)
    assert CTX.read_pin(CASE, tmp_path)["cell_land_use"]

    cell = next(iter(body["cell_land_use"]))
    body["cell_land_use"][cell] = "water" if body["cell_land_use"][cell] != "water" else "bare"
    write_canonical(body, path)

    with pytest.raises(ValueError) as excinfo:
        CTX.read_pin(CASE, tmp_path)
    assert "pin_sha256" in str(excinfo.value), (
        f"a tampered body raised, but not as a digest failure: {excinfo.value}"
    )


def test_a_pin_filed_under_the_wrong_case_is_refused(tmp_path):
    """A pin copied onto another case's slug carries a perfectly valid digest over the wrong city;
    only the `case_id` check can catch it, and its labels would be published under the wrong case."""
    path, body = committed_pin(tmp_path)
    assert body["case_id"] == CASE, f"the committed pin of {CASE} claims {body['case_id']!r}"

    body["case_id"] = "jakarta"
    PIN.write_pin(body, path)  # re-stamped: the digest is valid, so only the case check can raise
    # Positive control: filed under its own slug the same bytes read, so the raise is the case check.
    PIN.write_pin(body, tmp_path / "jakarta.json")
    assert CTX.read_pin("jakarta", tmp_path)["case_id"] == "jakarta"

    with pytest.raises(ValueError) as excinfo:
        CTX.read_pin(CASE, tmp_path)
    message = str(excinfo.value)
    assert "'jakarta'" in message and repr(CASE) in message, (
        f"the failure does not name both cases: {message}"
    )


@pytest.mark.parametrize(
    "damage", ["key", "value"], ids=["key-not-an-h3-cell", "value-outside-the-vocabulary"]
)
def test_pinned_context_refuses_a_label_table_outside_the_contract(tmp_path, damage):
    """`cell_land_use` is published verbatim as the grid's `land_use`: an id no cell can match and a
    class the contract never declared are both silent in the artefact, so they raise here."""
    path, body = committed_pin(tmp_path)
    labels = dict(body["cell_land_use"])
    if damage == "key":
        labels["8928308280fff"] = "vegetation"  # 13 chars: not an H3 index at all
        offending, expected = "8928308280fff", "not a resolution-9 H3 index"
    else:
        cell = next(iter(labels))
        labels[cell] = "urban-dense-ish"  # a typo, not a class: LAND_USE has `urban-dense`
        offending, expected = "urban-dense-ish", "not a declared land_use class"
    body["cell_land_use"] = labels
    PIN.write_pin(body, path)  # valid digest: the label check must be what refuses it
    payload = CTX.read_pin(CASE, tmp_path)
    assert payload["cell_land_use"] == labels, "the fixture's bad label never reached the payload"

    with pytest.raises(ValueError) as excinfo:
        CTX.pinned_context(CASE, PROVENANCE, payload)
    message = str(excinfo.value)
    assert offending in message, f"the failure does not name {offending!r}: {message}"
    assert str(len(labels)) in message, f"the failure does not report the label count: {message}"
    assert expected in message, f"the failure does not say what is wrong with {offending!r}: {message}"


# ---------------------------------------------------------------------------------------------
# 8. The synthetic fallback still labels itself as synthetic
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", C.CASE_IDS)
def test_synthetic_context_is_declared_and_inside_the_frozen_vocabulary(case_id):
    """The fallback exists so a clone without the extract runs; it must never look like a map of a
    real city, and it must not smuggle an undeclared class into the artefacts."""
    provenance = f"{case_id}+mock+20261114+abc12345"
    context = CTX.synthetic_context(case_id, provenance)

    assert context.synthetic is True
    assert "synthetic" in context.source
    assert not context.features.empty
    for row in context.features.itertuples(index=False, name=None):
        columns = list(context.features.columns)
        layer, klass = str(row[columns.index("layer")]), str(row[columns.index("class")])
        source = str(row[columns.index("source")])
        provenance_id = str(row[columns.index("provenance_id")])
        assert "synthetic" in source, f"({layer}, {klass}) does not declare itself synthetic: {source!r}"
        assert klass in C.CONTEXT_CLASSES[layer], (
            f"({layer!r}, {klass!r}) is not a frozen (layer, class) pair: {C.CONTEXT_CLASSES[layer]}"
        )
        assert C.PROVENANCE_ID_RE.match(provenance_id), (
            f"({layer}, {klass}) carries provenance_id {provenance_id!r}"
        )


# ---------------------------------------------------------------------------------------------
# 9. The tag table stays inside the frozen vocabulary
# ---------------------------------------------------------------------------------------------


def test_the_tag_table_reports_no_problem_and_every_rule_is_inside_the_frozen_vocabulary():
    """A class edited out of `contracts.LAND_USE` becomes an artefact that fails the gate on someone
    else's machine; this test names the offending rows instead of counting a total."""
    problems = E.mapping_problems()
    assert problems == [], "TAG_RULES leaves the frozen vocabulary:\n" + "\n".join(problems)

    offenders = [
        f"TAG_RULES {rule.key}={rule.values or '*'} -> {rule.layer}/{rule.klass}"
        for rule in E.TAG_RULES
        if rule.layer not in C.CONTEXT_LAYERS
        or rule.klass not in C.CONTEXT_CLASSES.get(rule.layer, ())
    ]
    assert offenders == [], "rules outside contracts.CONTEXT_CLASSES:\n" + "\n".join(offenders)
    assert set(E.LAND_USE_PRECEDENCE) == set(C.LAND_USE), (
        "LAND_USE_PRECEDENCE is not a permutation of contracts.LAND_USE: "
        f"{sorted(set(E.LAND_USE_PRECEDENCE) ^ set(C.LAND_USE))}"
    )


# ---------------------------------------------------------------------------------------------
# 10. The tags a real extract is dominated by still map where they should
# ---------------------------------------------------------------------------------------------

#: `(tag key, tag value, expected layer, expected class)`. These are the OSM tags the counts in
#: `docs/data-source-decision.md` show a real extract is made of: if one of them falls back to
#: `unknown`, the published land use stops describing the ground.
DOMINANT_TAGS = [
    ("landuse", "grass", "land_use", "vegetation"),
    ("landuse", "greenfield", "land_use", "vegetation"),
    ("landuse", "cemetery", "land_use", "vegetation"),
    ("leisure", "park", "land_use", "vegetation"),
    ("leisure", "pitch", "land_use", "vegetation"),
    ("leisure", "garden", "land_use", "vegetation"),
    ("landuse", "education", "land_use", "urban-dense"),
    ("landuse", "religious", "land_use", "urban-dense"),
    ("place", "square", "land_use", "urban-dense"),
    # A private pool is NOT a water surface at 200 m: mapping it to `water/reservoir` labelled
    # 7 716 Malaga pools as water in a real extract, which floods the water layer (published by the
    # largest features) and pins the `water` class on cells whose only surface water is a garden
    # pool. The vocabulary has no pool class, so it stays unmapped.
    ("leisure", "swimming_pool", "land_use", "unknown"),
    ("landuse", "military", "land_use", "unknown"),
    ("man_made", "bridge", "land_use", "unknown"),
]


@pytest.mark.parametrize(
    ("tag_key", "tag_value", "expected_layer", "expected_class"),
    DOMINANT_TAGS,
    ids=[f"{key}={value}" for key, value, _, _ in DOMINANT_TAGS],
)
def test_a_dominant_tag_still_maps_to_the_class_the_science_expects(
    tag_key, tag_value, expected_layer, expected_class
):
    """`military` and `bridge` land in `unknown` on purpose: the vocabulary cannot express them, and
    `unknown` is published rather than routed to a class that would claim something false."""
    frame = gpd.GeoDataFrame(
        {tag_key: [tag_value]},
        geometry=[box(0.0, 0.0, 0.01, 0.01)],
        crs=C.CRS_OUTPUT,
    )
    layer, klass = E._classify(frame)
    assert (layer[0], klass[0]) == (expected_layer, expected_class), (
        f"{tag_key}={tag_value} mapped to ({layer[0]!r}, {klass[0]!r}), "
        f"expected ({expected_layer!r}, {expected_class!r})"
    )
    assert klass[0] in C.CONTEXT_CLASSES[layer[0]]
    assert klass[0] in C.LAND_USE or layer[0] in {"water", "infrastructure", "admin_boundary"}


def test_an_unmapped_tag_falls_back_to_unknown_without_raising():
    """The fallback the two `unknown` rows above rely on: nothing is dropped, nothing is invented."""
    frame = gpd.GeoDataFrame(
        {"barrier": ["fence"]},
        geometry=[box(0.0, 0.0, 0.01, 0.01)],
        crs=C.CRS_OUTPUT,
    )
    layer, klass = E._classify(frame)
    assert (layer[0], klass[0]) == ("land_use", "unknown")
