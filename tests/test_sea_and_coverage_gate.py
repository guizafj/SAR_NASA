"""Guard the DERIVED sea rule, the coastline it needs, and the coverage the context pin has to cover.

Why this module exists
----------------------
Four defects motivated it, and none of them is visible in the four artefacts:

1. **A published cell at sea that is not `water`.** OSM publishes the coastline as a *line*, and a
   line contains no centroid, so the sea has to be derived: `pipeline/context.py::sea_polygons`
   polygonizes the coastline together with the AOI bbox and calls a candidate sea when mapped
   `land_use` covers at most 5 % of it. A cell the pin does not name must come out `water` — never
   `unknown` — because `unknown` publishes the modelled displacement and the map shows "ocean with
   subsidence".

2. **A sea/land rule keyed on one declared side.** `CASE_SEA_SIDE` (one sign per case) is gone and
   so is `Coast.signed_distance`: a bay's shoreline runs in both directions, and with
   `sea_side=+1` Jakarta labelled 7 055 of 7 935 cells `water`. Tests 2-4 pin the geometry that
   replaced the sign.

3. **A coastline that disappears from the pinned water layer.** The cap on derived features used to
   rank every feature by area in one pass; area is ~0 for a line, so the water layer kept 150
   reservoirs and dropped every coastline, and the sea rule stopped firing without any artefact
   changing shape. `FEATURE_CAP_PER_KIND` caps per geometry kind; test 7 pins the committed pins
   against a return of that defect.

4. **A pin that labels only the AOI ring.** The model publishes bbox ∩ coverage ellipse, so every
   cell between the ring and the ellipse had no pinned label and published as `unknown` even where a
   mapped OSM polygon contains its centroid (1 243 of 3 601 cells on Málaga). Test 5 pins the union,
   for every case the study config declares — never for a hardcoded one.

Refactor note (what this file used to be). The previous version pinned `CASE_SEA_SIDE` and
`Coast.signed_distance`, parametrised the labelling test over a declared side, and drove the gate
through a "declared coastal case". All of that described the sign, which no longer exists: those
tests are deleted and the survivors keep their original intent, now stated against the derived
polygon.

Offline and deterministic: every fixture is built in memory or under `tmp_path`, no test reads
`data/raw/`, nothing here writes `data/processed/`. Test 6 does read the published grid, because
"the gate cannot pass vacuously" has to be about the artefact a judge sees; that tree belongs to
another role and can be mid-regeneration, so a failure there is reported rather than retried.
Exactly two skips are allowed and both come from the brief: a missing `config/context/<case>.json`
(test 5) and a missing pin or case directory (test 6). Nothing is skipped silently.

Scope note (do not silently widen it). The *number* of sea cells a pin labels is a property of the
OSM extract, not of the rule, so it is not pinned here; the *relations* are (no published cell
inside the stored sea, every coastline case keeps a coastline feature, every model cell labelled).
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from pipeline import context as CTX
from pipeline import enrich_context as EC
from pipeline.generate_mock import DEFAULT_SEED, generate_case
from pipeline.ingest import pin_context as PIN
from pipeline.mock import checks as K
from pipeline.mock import model as M
from pipeline.mock.cases import load_study_cases
from src.domain import contracts as C

#: An AOI-sized box with a west→east coastline across its full width. Wall to wall on purpose: a
#: line that stops inside the box dangles, `polygonize` builds no face from it, and the fixture would
#: then say nothing about which side of the shoreline became sea.
BBOX = (-4.60, 36.60, -4.30, 36.82)
COASTLINE = ((BBOX[0], 36.70), (BBOX[2], 36.70))

#: Fixture points, all at the same longitude so only latitude decides. `SOUTH`/`NORTH_INSIDE` sit on
#: opposite sides of the shoreline inside the box; `NORTH_OUTSIDE` is beyond the box's north edge.
SOUTH = (-4.45, 36.65)
NORTH_INSIDE = (-4.45, 36.78)
NORTH_OUTSIDE = (-4.45, 37.50)

#: The gate's own wording, matched as fragments inside ITS verdict rather than searched for in the
#: source: each one is the thing a *driven* case has to produce. (1) is the line the synthetic
#: fallback appends (it derives no sea, so it must report the exclusion it does perform); (2) is the
#: failure that lets a water cell survive under that fallback; (3) is the failure when a context
#: carries a coastline (recorded count or layer row) and no sea polygon; (4) is the line a derived
#: sea appends, which has to name what it measured; (5) is the failure when a published cell sits
#: inside the derived sea.
SEA_SYNTHETIC_LINE = "sea: synthetic fallback excluded the sea"
SEA_WATER_IN_GRID = "published water cells"
SEA_NO_POLYGON = "carries coastline features but no sea polygon was derived"
SEA_POLYGON_LINE = "sea: the derived sea polygon excludes all"
SEA_INSIDE_POLYGON = "fall inside the derived sea polygon"

#: The two findings the checker appends *before* its sea block for a doctored grid that carries a
#: `water` cell, matched as fragments. They are the anchor the order assertion below uses: the sea
#: verdict has to come after them, because that is the sequence `assert_case` appends in.
WATER_SURVIVED = "water cell(s) survived the quality filter"
LAND_USE_UNBACKED = "has no context polygon"

#: Where the two published trees live. Read-only for this module.
PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


# ---------------------------------------------------------------------------------------------
# Fixtures / helpers (in memory or under tmp_path; nothing read from data/raw)
# ---------------------------------------------------------------------------------------------


def provenance_for(case_id: str) -> str:
    """A shape-valid provenance id. The pin reader only stamps it; no test asserts a digest."""
    return f"{case_id}+mock+20261114+abc12345"


def empty_features() -> gpd.GeoDataFrame:
    """A context frame with no geometry: the pinned-label path never touches it."""
    return gpd.GeoDataFrame({"layer": [], "class": []}, geometry=[], crs=C.CRS_OUTPUT)


def case_context(**overrides) -> CTX.CaseContext:
    base = {
        "case_id": "malaga",
        "features": empty_features(),
        "coast": None,
        "synthetic": False,
        "source": "test fixture",
    }
    base.update(overrides)
    return CTX.CaseContext(**base)


def context_frame(rows: list[tuple[str, str, object]]) -> gpd.GeoDataFrame:
    """An extract-shaped frame: `(layer, class, geometry)` is all `sea_polygons` reads."""
    return gpd.GeoDataFrame(
        {"layer": [layer for layer, _, _ in rows], "class": [klass for _, klass, _ in rows]},
        geometry=[geometry for _, _, geometry in rows],
        crs=C.CRS_OUTPUT,
    )


def land_use_features(rows: list[tuple[str, Polygon]]) -> gpd.GeoDataFrame:
    """A `mmap_source_features`-shaped frame: the join reads `layer`/`class`/geometry, not tags."""
    return gpd.GeoDataFrame(
        {
            "layer": ["land_use"] * len(rows),
            "class": [klass for klass, _ in rows],
            "name": [f"{klass} (fixture)" for klass, _ in rows],
            "case_id": ["malaga"] * len(rows),
            "crs": [C.CRS_OUTPUT] * len(rows),
            "source": ["test fixture"] * len(rows),
            "provenance_id": [provenance_for("malaga")] * len(rows),
        },
        geometry=[geometry for _, geometry in rows],
        crs=C.CRS_OUTPUT,
    )


def pinned_sea_payload(case_id: str) -> dict | None:
    """The `sea` member of a committed pin, when it is a GeoJSON polygon; else `None`."""
    payload = CTX.read_pin(case_id)
    if payload is None:
        return None
    sea = payload.get("sea")
    if isinstance(sea, dict) and sea.get("type") in ("Polygon", "MultiPolygon") and sea.get("coordinates"):
        return sea
    return None


def pinned_water_classes(case_id: str) -> list[str]:
    """The `class` of every water feature a pin publishes (empty when the pin is absent)."""
    payload = CTX.read_pin(case_id)
    if payload is None:
        return []
    return [str((feature.get("properties") or {}).get("class", "")) for feature in payload.get("water") or ()]


def coordinate_cell(lon: float, lat: float) -> tuple[str, float, float]:
    """The res-9 cell holding a point, plus that cell's own centroid (what the labels see)."""
    cell = h3.latlng_to_cell(lat, lon, 9)
    centroid_lat, centroid_lon = h3.cell_to_latlng(cell)
    return str(cell), float(centroid_lat), float(centroid_lon)


# ---------------------------------------------------------------------------------------------
# 1. `_line_coords`: the geometry a coastline way can arrive as, or the label layer dies on it
# ---------------------------------------------------------------------------------------------


def test_line_coords_takes_a_linestring_as_one_polyline_and_a_multilinestring_as_two():
    """A coastline way is a LineString; a harbour or an island group is a MultiLineString.

    A MultiLineString iterated naively yields parts, but one that is read as `.coords` raises, so
    the two-type split is what keeps a case with several shoreline ways from falling to `None`.
    Silent-break cost: no polylines means no derived sea, so every sea cell publishes a
    displacement and the sea gate skips the case instead of failing it.
    """
    single = CTX._line_coords(LineString([(0.0, 0.0), (1.0, 1.0), (2.0, 0.5)]))
    assert single == [((0.0, 0.0), (1.0, 1.0), (2.0, 0.5))], "a LineString must stay ONE polyline"

    double = CTX._line_coords(MultiLineString([[(0.0, 0.0), (1.0, 1.0)], [(2.0, 2.0), (3.0, 3.0)]]))
    assert len(double) == 2, f"both parts of a MultiLineString must survive, got {double}"


def test_line_coords_takes_a_closed_polygon_as_its_exterior_ring_not_a_crash():
    """An island outline arrives as a Polygon — this is the Jakarta crash, pinned.

    `Polygon.geoms` yields RINGS in shapely 2, and a ring has no `.coords`, so iterating `.geoms`
    raised `AttributeError` on the first closed coastline way and took the whole case down. An
    island's exterior ring *is* its coastline, so it must come back as a LINE (closed, first vertex
    == last) rather than being dropped. Silent-break cost: either the case cannot be generated at
    all, or the island silently contributes no shoreline and its cells publish as land.
    """
    island = Polygon([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)])
    lines = CTX._line_coords(island)
    assert len(lines) == 1, f"one island outline is one polyline, got {lines}"
    ring = lines[0]
    assert len(ring) == len(island.exterior.coords), "the exterior ring must be kept vertex for vertex"
    assert ring[0] == ring[-1], "the ring must stay closed: piecewise distance needs the last segment"


def test_line_coords_expands_a_multipolygon_part_by_part():
    """Islands come as a MultiPolygon; each part's ring is a separate shoreline.

    Skipping the multipart case would let the parts' cells be labelled from mapped land polygons
    only — i.e. the island's own area and the water around it both come out as land.
    """
    multi = MultiPolygon(
        [
            Polygon([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]),
            Polygon([(2.0, 2.0), (2.0, 3.0), (3.0, 3.0), (3.0, 2.0)]),
        ]
    )
    assert len(CTX._line_coords(multi)) == 2, "each Polygon part contributes one shoreline"


def test_line_coords_returns_nothing_for_an_empty_geometry():
    """An empty geometry contributes no shoreline, and must not raise on the way to saying so.

    The pinned extract carries simplified features, and simplification can empty a tiny way. A raise
    here would abort the case for a feature that legitimately says "nothing"; a fabricated vertex
    would be worse.
    """
    assert CTX._line_coords(LineString()) == []
    assert CTX._line_coords(Polygon()) == []
    assert CTX._line_coords(GeometryCollection()) == []


# ---------------------------------------------------------------------------------------------
# 2. `sea_polygons`: the sea is what the coastline plus the bbox enclose and land_use does not cover
# ---------------------------------------------------------------------------------------------


def test_sea_polygons_derives_the_sea_from_the_coastline_and_the_bbox():
    """With no mapped land use both sides of the shoreline are sea; mapped land use picks the side.

    Method, as implemented: polygonize the coastline together with the bbox ring, then keep the
    candidates that mapped `land_use` covers at most `land_use_share_max` of. Polygonize alone
    cannot tell an ocean from a city — with no land-use polygons BOTH candidates come back — so the
    two halves below pin the two things that actually hold: the result is clipped to the AOI (a
    point beyond the bbox is never "sea") and a candidate covered by mapped land use is dropped.
    That second half is the whole rule: silent-break cost is a bay labelled land (Jakarta's 7 055 of
    7 935 cells with the old single sign) or an inland city labelled sea, and neither shows up in
    the artefacts — only in the map a judge reads.
    """
    coastline = context_frame([("water", "coastline", LineString(COASTLINE))])
    sea = CTX.sea_polygons(coastline, BBOX)
    assert sea is not None, (
        "a coastline plus a bbox derived no sea: every sea cell would keep its modelled "
        "displacement, which the sea gate cannot see because it has no polygon to test against"
    )
    assert sea.contains(Point(*SOUTH)), "the sea must contain the open-water side of the shoreline"
    assert sea.contains(Point(*NORTH_INSIDE)), (
        "with no mapped land use the land side is as un-covered as the sea side, so it is still a "
        "candidate: asserting this documents that the rule needs land_use to separate the two"
    )
    assert not sea.contains(Point(*NORTH_OUTSIDE)), (
        "the sea must be clipped to the AOI bbox: an unbounded result would label every cell of a "
        "neighbouring case as water"
    )

    north_half = box(BBOX[0], 36.70, BBOX[2], BBOX[3])
    with_land = context_frame(
        [("water", "coastline", LineString(COASTLINE)), ("land_use", "urban-residential", north_half)]
    )
    narrowed = CTX.sea_polygons(with_land, BBOX)
    assert narrowed is not None, "mapped land use on one side left no sea at all"
    assert narrowed.contains(Point(*SOUTH)), "the un-covered (sea) side must survive the land_use mask"
    assert not narrowed.contains(Point(*NORTH_INSIDE)), (
        "the candidate covered by mapped land use was not dropped: the city is labelled sea, and its "
        "real subsidence disappears from the map while the gate reports nothing"
    )


#: The mapped-land-use share of one candidate, and whether the candidate survives as sea. The two
#: values straddle `sea_polygons`'s documented 5 % threshold by one percentage point each, so the
#: constant is pinned from both sides: lowering it to 3 % drops the kept case, raising it to 7 %
#: keeps the dropped one, and either change is red.
LAND_USE_SHARE_CASES = (
    pytest.param(0.04, True, id="4pct-covered-stays-sea"),
    pytest.param(0.06, False, id="6pct-covered-becomes-land"),
)


@pytest.mark.parametrize(("covered_share", "kept_as_sea"), LAND_USE_SHARE_CASES)
def test_sea_polygons_decides_a_candidate_at_the_land_use_share_boundary(covered_share, kept_as_sea):
    """A candidate is sea at 4 % mapped land use and land at 6 %: the 5 % threshold itself.

    The other two sea tests exercise the extremes — no land use at all (both sides kept) and a
    candidate wholly covered (dropped) — so neither can tell a 5 % threshold from 1 % or 50 %. This
    one builds the covered fraction explicitly (`land_use_share_cases`) and asserts it before the
    verdict, so the fixture cannot drift into a different fraction while the assertions still pass.
    Silent-break cost: at a threshold of 0 a whole bay's un-mapped shoreline is relabelled land and
    keeps its displacement; at a lax one the coastal parks and port basins become sea and lose the
    subsidence the map is about. Neither moves an artefact's shape.
    """
    north_candidate = box(BBOX[0], 36.70, BBOX[2], BBOX[3])
    strip = box(BBOX[0], 36.70, BBOX[2], 36.70 + covered_share * (BBOX[3] - 36.70))
    measured = north_candidate.intersection(strip).area / north_candidate.area
    assert measured == pytest.approx(covered_share, abs=1e-6), (
        f"the fixture covers {measured:.6f} of the candidate, not the intended {covered_share}: the "
        "assertions below would then be about another fraction of the threshold"
    )

    sea = CTX.sea_polygons(
        context_frame(
            [
                ("water", "coastline", LineString(COASTLINE)),
                ("land_use", "urban-residential", strip),
            ]
        ),
        BBOX,
    )
    assert sea is not None, (
        f"a candidate covered {covered_share:.0%} by mapped land use left no sea at all: the "
        "open-water side must survive whatever the land side costs"
    )
    assert sea.contains(Point(*SOUTH)), "the un-covered (sea) side must always survive the land_use mask"
    assert sea.contains(Point(*NORTH_INSIDE)) is kept_as_sea, (
        f"a candidate {covered_share:.0%} covered by mapped land use came out "
        f"{'land' if kept_as_sea else 'sea'}: the 5 % threshold in `sea_polygons` was moved, so "
        "the boundary no longer decides either side"
    )


def test_sea_polygons_returns_none_without_a_coastline_or_a_bbox():
    """No coastline or no bbox must yield `None`, not a guessed half-plane.

    `None` is the honest answer and it is what makes the gate state (a) fire: the caller labels from
    mapped water polygons only, and `checks` refuses a case whose context carries coastline features
    without a sea. Guessing instead (say, "everything south of the first line") is exactly the
    declared-side bug. Returns `None` on an empty frame too, which is what a synthetic context that
    never had a coastline looks like.
    """
    land_only = context_frame([("land_use", "vegetation", box(BBOX[0], BBOX[1], BBOX[2], BBOX[3]))])
    assert CTX.sea_polygons(land_only, BBOX) is None, "no coastline, no sea: nothing was enclosed"

    coastline = context_frame([("water", "coastline", LineString(COASTLINE))])
    assert CTX.sea_polygons(coastline, None) is None, "no bbox means no frame to polygonize"
    assert CTX.sea_polygons(context_frame([]), BBOX) is None, "an empty extract carries no sea"


# ---------------------------------------------------------------------------------------------
# 3. `label_cells`: the sea override, and the centroid join underneath it
# ---------------------------------------------------------------------------------------------


def test_label_cells_turns_an_unpinned_sea_cell_into_water():
    """A cell the pin does not name must be `water` when the sea polygon contains its centroid.

    This is the bug the sea polygon exists to close: the pinned labels are a `{cell: class}` mapping
    produced when the pin was written, and a cell that mapping misses used to fall back to
    `unknown` — which publishes the modelled displacement. The context in this fixture carries NO
    `Coast`: the labelling must not consult the shoreline any more, only the derived sea. Silent-break
    cost: the first artefact a judge opens shows subsidence over open water, and nothing in the
    four files says which cells were never asked about.
    """
    sea_cell, sea_lat, sea_lon = coordinate_cell(-4.45, 36.70)
    inland_cell, inland_lat, _ = coordinate_cell(-4.45, 36.78)
    assert sea_cell != inland_cell, "the fixture points must fall in different res-9 cells"

    # A sea polygon drawn around the sea cell's OWN centroid: the override is per cell, so a polygon
    # that also swallowed the inland centroid would make the second assertion meaningless.
    sea = box(sea_lon - 0.01, sea_lat - 0.01, sea_lon + 0.01, sea_lat + 0.01)
    assert sea.contains(Point(sea_lon, sea_lat)) and not sea.contains(Point(-4.45, inland_lat))

    context = case_context(sea=sea, labels={inland_cell: "vegetation"})
    assert sea_cell not in context.labels, "the pin must deliberately NOT name the sea cell"

    labels = CTX.label_cells(
        context,
        np.array([inland_cell, sea_cell], dtype=object),
        np.array([-4.45, -4.45], dtype=float),
        np.array([inland_lat, sea_lat], dtype=float),
    )
    assert labels.tolist() == ["vegetation", "water"]


def test_label_cells_centroid_join_resolves_overlaps_by_precedence_not_area():
    """`labels=None` must take its classes from `join_land_use`, and overlap by declared precedence.

    The vegetation polygon is *smaller* than the industrial one, so the "smallest area wins"
    tie-break would pick vegetation while the precedence table picks industrial. Asserting the areas
    first is what makes the class assertion evidence about precedence rather than about the
    accidental sizes of the fixture. Silent-break cost: a project reusing the join with a shuffled
    precedence relabels industrial ground as open vegetation, understating exposure exactly where
    groundwater extraction concentrates.
    """
    assert EC.LAND_USE_PRECEDENCE.index("industrial") < EC.LAND_USE_PRECEDENCE.index("vegetation")

    cell_a = (-4.50, 36.70)  # inside BOTH polygons
    cell_b = (-4.44, 36.70)  # inside vegetation only
    industrial = box(-4.60, 36.68, -4.45, 36.72)
    vegetation = box(-4.51, 36.69, -4.43, 36.71)
    assert industrial.area > vegetation.area, (
        "the fixture must make the smallest-area tie-break disagree with precedence, otherwise this "
        "test cannot tell the two rules apart"
    )
    assert industrial.contains(Point(cell_a)) and not industrial.contains(Point(cell_b))
    assert vegetation.contains(Point(cell_a)) and vegetation.contains(Point(cell_b))

    context = case_context(
        features=land_use_features([("industrial", industrial), ("vegetation", vegetation)]),
        labels=None,
        coast=None,
        sea=None,
    )
    labels = CTX.label_cells(
        context,
        np.array(
            [h3.latlng_to_cell(cell_a[1], cell_a[0], 9), h3.latlng_to_cell(cell_b[1], cell_b[0], 9)],
            dtype=object,
        ),
        np.array([cell_a[0], cell_b[0]], dtype=float),
        np.array([cell_a[1], cell_b[1]], dtype=float),
    )
    assert labels.tolist() == ["industrial", "vegetation"]
    assert set(labels.tolist()) <= set(C.LAND_USE)


# ---------------------------------------------------------------------------------------------
# 4. `Coast.distance` is UNSIGNED: the sign could not describe a bay and was removed
# ---------------------------------------------------------------------------------------------


def test_coast_distance_is_unsigned_and_no_signed_variant_survives():
    """Both sides of a shoreline measure ~1.0 km, and no `signed_distance` exists to flip a side.

    The coherence model only needs a distance to the coast; the sea is decided by a polygon. A
    restored sign would make one of these two points negative and, by the rule that used to be built
    on it, put a whole case's land under water (Jakarta: 7 055 of 7 935 cells) — that is why the
    absence of `signed_distance` and of a declared per-case side is asserted, not merely unused.
    Silent-break cost: the artefacts keep their shape while the map labels the wrong half of the AOI.
    """
    one_km_deg = 1.0 / CTX.KM_PER_DEG_LAT
    lon = np.array([-4.45, -4.45], dtype=float)
    lat = np.array([36.70 + one_km_deg, 36.70 - one_km_deg], dtype=float)

    distance = CTX.Coast(lines=(COASTLINE,), sea_side=0).distance(lon, lat)
    assert float(distance[0]) == pytest.approx(1.0, abs=0.05), "1 km north of the line must be 1 km"
    assert float(distance[1]) == pytest.approx(1.0, abs=0.05), (
        "1 km south of the line must be the same distance: an unsigned distance cannot depend on a side"
    )

    assert not hasattr(CTX.Coast, "signed_distance"), (
        "a signed distance came back: one sign cannot describe a bay, and every caller that used it "
        "labelled a bay's land as water"
    )
    assert not hasattr(CTX, "CASE_SEA_SIDE"), (
        "the per-case declared sea side came back: it is the mechanism the derived polygon replaced"
    )


def test_coast_distance_takes_the_nearest_of_several_lines():
    """With two shorelines the nearest one decides, at the same unsigned distance as before.

    A real AOI carries several lines (a harbour mole, a river mouth, a bay), and the coherence model
    needs the closest coast, not the longest or the first. Pinned against a far line 21 km away so
    `min` over the lines is the only rule that produces 1.0 km: a first-line, longest-line or
    last-line rule would return the far distance. Silent-break cost: the coastal decorrelation
    gradient is applied at the wrong distance and the coastal cells' opening phase moves without any
    gate noticing.
    """
    one_km_deg = 1.0 / CTX.KM_PER_DEG_LAT
    lon = np.array([-4.45], dtype=float)
    lat = np.array([36.70 + one_km_deg], dtype=float)
    far_line = ((BBOX[0], 36.90), (BBOX[2], 36.90))

    only_far = CTX.Coast(lines=(far_line,), sea_side=0).distance(lon, lat)
    assert float(only_far[0]) > 5.0, (
        f"the far line is {float(only_far[0]):.2f} km away: the fixture must place it far enough "
        "for the nearest-line result to be distinguishable"
    )
    nearest = CTX.Coast(lines=(COASTLINE, far_line), sea_side=0).distance(lon, lat)
    assert float(nearest[0]) == pytest.approx(1.0, abs=0.05), (
        f"two shorelines 1 km and {float(only_far[0]):.1f} km away gave {float(nearest[0]):.2f} km: "
        "the nearest line must decide, not the first or the longest"
    )


def test_polyline_metrics_reports_a_side_per_line():
    """`polyline_metrics` returns `(distance_km, side)`, with +1 north/left of a west→east line.

    The side is what a caller that asks for one gets per line; the sea does NOT use it any more (a
    bay needs the polygon), and that separation is the point of pinning both: the primitive keeps its
    documented contract, the labelling keeps its polygon. Silent-break cost: a flipped cross product
    would leave `Coast.distance` correct — it discards the side — so the sign could rot silently and
    the next caller that trusts it would put a case's land under water.
    """
    one_km_deg = 1.0 / CTX.KM_PER_DEG_LAT
    lon = np.array([-4.45, -4.45], dtype=float)
    lat = np.array([36.70 + one_km_deg, 36.70 - one_km_deg], dtype=float)

    distance, side = CTX.polyline_metrics(lon, lat, COASTLINE)
    assert float(distance[0]) == pytest.approx(1.0, abs=0.05), "1 km north of the line must be 1 km"
    assert float(distance[1]) == pytest.approx(1.0, abs=0.05), "1 km south of the line must be 1 km"
    assert side.tolist() == [1.0, -1.0], (
        f"a west→east line reported sides {side.tolist()} for a point 1 km north and 1 km south: "
        "the documented sign (+1 left/north, -1 right/south) is no longer what the function returns"
    )


# ---------------------------------------------------------------------------------------------
# 5. The pin must cover every cell the model can publish, and must store the derived sea
# ---------------------------------------------------------------------------------------------


def test_pin_labels_cover_every_cell_the_model_can_publish():
    """`cells_to_label` must be a superset of `expected_cells` for EVERY case, not just the AOI ring.

    The measured defect: the model publishes bbox ∩ ellipse, the pin labelled the ring, and the cells
    in between published as `unknown` (1 243 of 3 601 on Málaga). Nothing in the artefacts
    distinguishes "no mapped feature contains this cell" from "we never asked about it", so the
    regression is invisible downstream — this set comparison is the only cheap place to catch it. A
    label written for a non-res-9 H3 id would additionally mean the pin describes a different grid
    than the parquet publishes. Cases come from `config/study_cases.yaml`, never from a literal.
    """
    study = load_study_cases()
    checked: list[str] = []
    unpinned: list[str] = []
    for case_id in C.CASE_IDS:
        pin = CTX.read_pin(case_id)
        if pin is None:
            unpinned.append(case_id)
            continue
        case = study.cases[case_id]

        labelled = PIN.cells_to_label(case_id)
        labelled_ids = labelled["cell_id"].astype(str).tolist()
        assert labelled_ids, f"cells_to_label({case_id}) returned nothing: the pin labels no cell"
        assert len(set(labelled_ids)) == len(labelled_ids), f"cells_to_label({case_id}) repeated a cell"

        assert PIN.CELL_LABEL_RESOLUTION == study.h3_resolution, (
            f"the pin labels res-{PIN.CELL_LABEL_RESOLUTION} while the model publishes "
            f"res-{study.h3_resolution}: no pinned label would ever be found"
        )
        expected, _, _ = M.expected_cells(case, study.h3_resolution)
        expected_ids = {str(value) for value in expected}
        assert expected_ids, f"expected_cells returned nothing: the model publishes no cell for {case_id}"

        missing = expected_ids - set(labelled_ids)
        assert not missing, (
            f"{len(missing)} cell(s) {case_id} publishes have no pinned label "
            f"(e.g. {sorted(missing)[:3]}): they would publish as `unknown` even where a mapped "
            "polygon contains their centroid"
        )
        offenders = [
            cell
            for cell in labelled_ids
            if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != study.h3_resolution
        ]
        assert not offenders, (
            f"cells_to_label({case_id}) returned {len(offenders)} cell id(s) at another resolution: "
            f"{offenders[:3]}"
        )

        # A coastline feature in the published water layer means the extract had one, and the pin
        # must then carry the sea derived from it. The reverse direction is the cap guard: a stored
        # sea cannot have been derived without a coastline line, so land use alone must not explain it.
        classes = pinned_water_classes(case_id)
        if "coastline" in classes:
            assert pinned_sea_payload(case_id) is not None, (
                f"config/context/{case_id}.json publishes a coastline feature but no `sea` polygon: "
                "the labelling falls back to `unknown` for every sea cell the labels do not cover"
            )
        if pinned_sea_payload(case_id) is not None:
            assert "coastline" in classes, (
                f"config/context/{case_id}.json stores a sea but keeps no coastline feature in its "
                "water layer: the pin cannot be re-derived, and the map draws a coast the grid "
                "does not believe in"
            )
        checked.append(case_id)

    if not checked:
        pytest.skip(f"no committed pin to check the coverage of (unpinned: {', '.join(unpinned)})")


# ---------------------------------------------------------------------------------------------
# 6. The gate cannot pass vacuously: all three of its paths, and the artefact agrees with them
# ---------------------------------------------------------------------------------------------


def _gate_with_standin_context(monkeypatch, case, study, directory, context):
    """Run `checks.assert_case` on real artefacts while the model carries ONE fixture context.

    The seam is the model, not the gate: `assert_case` rebuilds the model from `meta.json` and hands
    its context to the sea block, so a stand-in has to arrive there. The model is rebuilt with the
    case's own context — keeping the prefilter arrays the nodata audit compares against the parquet
    — and only its `context` is swapped. Nothing inside `pipeline/mock/checks.py` is patched, so the
    verdict asserted below is the gate's own.
    """
    real_build_case = K.M.build_case

    def build_case(*args, **kwargs):
        return dataclasses.replace(real_build_case(*args, **kwargs), context=context)

    monkeypatch.setattr(K.M, "build_case", build_case)
    return K.assert_case(case, study, directory)


def _sea_lines(report: K.CaseReport) -> list[str]:
    """The `sea:` verdicts a run reported: the only place the gate says what it did."""
    return [line for line in report.checks if line.startswith("sea:")]


def test_the_sea_gate_has_its_branches_and_the_published_grid_agrees(
    sea_gate_evidence, monkeypatch, tmp_path
):
    """Each of the gate's three paths is driven to its own verdict, on real artefacts.

    A gate is only as good as the branch that fires, and a string search cannot tell a live branch
    from a comment. So every path below runs through `checks.assert_case` itself, on a case
    generated offline, with only the model's context substituted:

    (a) the declared synthetic fallback derives no sea polygon, so it must report the exclusion it
        DOES perform and refuse a published water cell. Delete that `_require` and the doctored-grid
        half goes green — which is exactly what this test calls red;
    (b) a context carrying a coastline must have a sea polygon. The same context WITH a sea passes,
        and a sea that swallows a published cell fails, so the verdicts are about the polygon and
        not about the fixture;
    (c) the pin's recorded `coastline_lines` counts as a coastline on its own. The per-kind cap keeps
        the largest water features, so it can drop every coastline row while the extract had them;
        without the counter that state reads exactly like an inland AOI and the rule passes
        vacuously.

    The last half reads the published parquet, because that is what a judge sees: a cell inside the
    stored sea means the map shows subsidence over open water, whatever the pin's labels claim.
    Silent-break cost: `--check`, the phase-A gate and every downstream suite stay green while the
    one artefact that carries the message draws ocean with a displacement.
    """
    study = sea_gate_evidence["study"]
    case_id = sea_gate_evidence["coastless"]
    case = study.cases[case_id]
    directory = sea_gate_evidence["directories"][case_id]

    # (a) the declared fallback: no derived sea, so what it DID do has to be on the record, and a
    #     water cell that survived it has to be refused. The refusal is stated against the PUBLISHED
    #     grid's `land_use` column, so the pass below only means something while that grid carries no
    #     water cell: assert that first, rather than let an already-`water` grid make the branch
    #     fire whether or not the fallback worked.
    published_land_use = {
        str(klass)
        for klass in pd.read_parquet(
            directory / "nisar_grid.parquet", columns=["land_use"]
        )["land_use"].tolist()
    }
    assert "water" not in published_land_use, (
        f"{case_id} publishes {sorted(published_land_use)} in its own `land_use` column: the water "
        "refusal below would then be measuring a grid that was already wrong, not the doctored cell"
    )

    synthetic = CTX.synthetic_context(case_id, provenance_for(case_id))
    assert synthetic.synthetic and synthetic.sea is None, (
        "the stand-in is not the state the synthetic branch keys on: it must declare itself and "
        "carry no sea polygon"
    )
    report = _gate_with_standin_context(monkeypatch, case, study, directory, synthetic)
    lines = _sea_lines(report)
    assert len(lines) == 1 and SEA_SYNTHETIC_LINE in lines[0], (
        f"the synthetic fallback reported {lines!r}: the branch that stands in for the derived "
        "polygon stopped saying which exclusion it applied"
    )

    doctored = tmp_path / "synthetic-with-water"
    shutil.copytree(directory, doctored)
    grid = pd.read_parquet(doctored / "nisar_grid.parquet")
    grid.loc[grid.index[0], "land_use"] = "water"
    grid.to_parquet(doctored / "nisar_grid.parquet", index=False)
    with pytest.raises(AssertionError) as water_refusal:
        _gate_with_standin_context(monkeypatch, case, study, doctored, synthetic)
    assert SEA_WATER_IN_GRID in str(water_refusal.value), (
        "one water cell published under the synthetic fallback and the gate said nothing about it: "
        "a specular surface with a displacement would reach the map:\n" + str(water_refusal.value)
    )

    coastline_row = ("water", "coastline", LineString(COASTLINE))
    reservoir_row = ("water", "reservoir", box(BBOX[0], BBOX[1], BBOX[0] + 0.05, BBOX[1] + 0.05))

    # (b) a coastline without a derived sea fails, and fails for that reason. This stand-in reaches
    #     `has_coastline` through the water layer's `coastline` row and through nothing else, so it
    #     must carry no recorded counter: otherwise (c) below would be pinning the same path twice.
    no_sea = case_context(features=context_frame([coastline_row]))
    assert not no_sea.counters, (
        f"the row-path stand-in must not also carry a counter, got counters={no_sea.counters!r}"
    )
    assert (no_sea.features_of("water")["class"] == "coastline").any(), (
        "the row-path stand-in has no `coastline` class in its water layer, so nothing makes the "
        "context coastal"
    )
    with pytest.raises(AssertionError) as coastline_refusal:
        _gate_with_standin_context(monkeypatch, case, study, directory, no_sea)
    assert SEA_NO_POLYGON in str(coastline_refusal.value), (
        "a context carrying a coastline and no sea polygon passed the gate: the `water` labels "
        "cannot exclude the sea:\n" + str(coastline_refusal.value)
    )

    # The same context WITH a sea passes and prints the count it checked. A sea far from every AOI
    # keeps this a test of the branch rather than of the fixture: it only has to exist.
    with_sea = case_context(features=context_frame([coastline_row]), sea=box(0.0, 0.0, 0.02, 0.02))
    report = _gate_with_standin_context(monkeypatch, case, study, directory, with_sea)
    lines = _sea_lines(report)
    assert len(lines) == 1 and SEA_SYNTHETIC_LINE not in lines[0], (
        f"a context with a derived sea reported {lines!r}: the polygon path did not run, or the "
        "synthetic path claimed a fallback that was not there"
    )
    assert SEA_POLYGON_LINE in lines[0], (
        f"a context with a sea polygon reported {lines[0]!r}: the polygon path no longer says which "
        "polygon it measured",
    )
    assert str(report.n_cells) in lines[0], (
        f"the sea line does not print how many cells it checked ({report.n_cells}): {lines[0]!r}"
    )

    # ... and a sea that covers ONE published cell fails: the count has to be a measurement.
    published = pd.read_parquet(directory / "nisar_grid.parquet", columns=["lon", "lat"])
    lon, lat = float(published["lon"].iloc[0]), float(published["lat"].iloc[0])
    swallowing = case_context(
        features=context_frame([coastline_row]),
        sea=box(lon - 0.005, lat - 0.005, lon + 0.005, lat + 0.005),
    )
    with pytest.raises(AssertionError) as inside_refusal:
        _gate_with_standin_context(monkeypatch, case, study, directory, swallowing)
    assert SEA_INSIDE_POLYGON in str(inside_refusal.value), (
        "a published cell inside the derived sea did not fail the gate: the map would draw "
        "subsidence over open water:\n" + str(inside_refusal.value)
    )

    # (c) the pin's recorded coastline count ALONE makes a context coastal: the water layer below is
    #     a reservoir, i.e. exactly the state the per-kind cap leaves behind when it drops the lines.
    counter_only = case_context(
        features=context_frame([reservoir_row]), counters={"coastline_lines": 7}
    )
    assert int(counter_only.counters.get("coastline_lines") or 0) > 0, "the counter stand-in lost its count"
    assert counter_only.sea is None, "the counter stand-in must carry no sea polygon to be a test of the refusal"
    assert not (counter_only.features_of("water")["class"] == "coastline").any(), (
        "the counter stand-in still has a `coastline` row: the verdict below would then prove the "
        "row path again and leave the recorded count untested"
    )
    with pytest.raises(AssertionError) as counter_refusal:
        _gate_with_standin_context(monkeypatch, case, study, directory, counter_only)
    assert SEA_NO_POLYGON in str(counter_refusal.value), (
        "the pin recorded 7 coastline lines while the capped water layer kept no `coastline` row, "
        "and the gate called it inland: the per-kind cap can then drop the coastline and switch the "
        "rule off without any artefact changing shape:\n" + str(counter_refusal.value)
    )

    # The same stand-in without the counter is honestly inland: no sea, no complaint, no line.
    inland = case_context(features=context_frame([reservoir_row]))
    report = _gate_with_standin_context(monkeypatch, case, study, directory, inland)
    inland_lines = _sea_lines(report)
    assert not inland_lines, (
        f"a context with neither a coastline row nor a coastline counter reported {inland_lines!r}: "
        "the gate is reporting a sea it never derived"
    )

    # The artefact half: the published cells must obey the stored sea, whatever the model says.
    from shapely.geometry import shape  # noqa: PLC0415 - only this half needs it

    tested: list[str] = []
    skipped: list[str] = []
    for case_id in C.CASE_IDS:
        payload = pinned_sea_payload(case_id)
        grid_path = PROCESSED_DIR / case_id / "nisar_grid.parquet"
        if payload is None or not grid_path.is_file():
            skipped.append(case_id)
            continue
        sea = shape(payload)
        cells = pd.read_parquet(grid_path, columns=["cell_id", "lon", "lat"])
        inside = [
            str(cell)
            for cell, lon, lat in zip(cells["cell_id"], cells["lon"], cells["lat"], strict=True)
            if sea.contains(Point(float(lon), float(lat)))
        ]
        assert not inside, (
            f"{len(inside)} published cell(s) of {case_id} fall inside the sea polygon stored in its "
            f"pin (e.g. {inside[:3]}): a specular surface carries a displacement, so the sea rule "
            "was not applied to the artefact that is actually published"
        )
        tested.append(case_id)

    if not tested:
        pytest.skip(
            "no case has both a sea polygon in its pin and a published grid "
            f"({', '.join(skipped)} skipped)"
        )


def _findings(exc: BaseException) -> list[str]:
    """The findings `assert_case` listed, in the order it appended them.

    The verdict is one header line plus one `  - <finding>` line per failed gate, so the bullet list
    is exactly `failures` in append order. Parsing it is what lets an ordering claim be an assertion
    rather than a reading of the source.
    """
    lines = [line.strip() for line in str(exc).splitlines() if line.strip().startswith("- ")]
    assert lines, f"the refusal lists no findings at all, so nothing can be ordered: {exc}"
    return [line[2:].strip() for line in lines]


def _assert_sea_finding_comes_last(findings: list[str], sea_fragment: str) -> None:
    """Pin the append order: the grid's own `water` findings, then the sea verdict, once each."""
    positions: list[int] = []
    for fragment in (WATER_SURVIVED, LAND_USE_UNBACKED, sea_fragment):
        where = [index for index, line in enumerate(findings) if fragment in line]
        assert len(where) == 1, (
            f"expected exactly one finding containing {fragment!r}, found {len(where)} in "
            f"{findings!r}"
        )
        positions.append(where[0])
    assert positions == sorted(set(positions)), (
        f"the findings came back in {positions} for (water survived, land use unbacked, sea): the "
        "checker appends its sea verdict after the grid's own water checks, and a reordering "
        "would mean a check moved without the register noticing\n" + "\n".join(findings)
    )


@pytest.mark.parametrize("with_sea", [False, True], ids=["coastline-no-sea", "sea-swallows-a-cell"])
def test_the_sea_failure_sentences_are_pinned_in_the_checkers_append_order(
    sea_gate_evidence, monkeypatch, tmp_path, with_sea
):
    """Both sea failure sentences fire in `assert_case`'s own place in its finding list.

    `assert_case` accumulates findings instead of raising on the first one, so the sentence a reader
    sees is a position in a list, not a single message: a check moved behind (or in front of) the
    grid's water checks would change what a refusal looks like while every individual sentence stayed
    green. Both sea sentences are driven here — (b) the coastline with no derived sea and (c) the
    sea that swallows a published cell — on a grid doctored to carry one `water` cell, so the two
    findings the grid itself produces are the anchor, and the sea finding must follow them, exactly
    once. Silent-break cost: a refactor that folds the sea checks into the land-use block, or drops
    one of them, keeps the other green while the artefact publishes the sea it should have refused.
    """
    study = sea_gate_evidence["study"]
    case_id = sea_gate_evidence["coastless"]
    case = study.cases[case_id]
    directory = tmp_path / f"water-anchor-{int(with_sea)}"
    shutil.copytree(sea_gate_evidence["directories"][case_id], directory)
    grid = pd.read_parquet(directory / "nisar_grid.parquet")
    grid.loc[grid.index[0], "land_use"] = "water"
    grid.to_parquet(directory / "nisar_grid.parquet", index=False)

    coastline_row = ("water", "coastline", LineString(COASTLINE))
    if with_sea:
        lon = float(grid["lon"].iloc[0])
        lat = float(grid["lat"].iloc[0])
        standin = case_context(
            features=context_frame([coastline_row]),
            sea=box(lon - 0.005, lat - 0.005, lon + 0.005, lat + 0.005),
        )
        expected = SEA_INSIDE_POLYGON
    else:
        standin = case_context(features=context_frame([coastline_row]))
        expected = SEA_NO_POLYGON

    with pytest.raises(AssertionError) as refusal:
        _gate_with_standin_context(monkeypatch, case, study, directory, standin)
    _assert_sea_finding_comes_last(_findings(refusal.value), expected)


# ---------------------------------------------------------------------------------------------
# 7. The cap: a per-kind cap is what keeps the coastline in the pinned water layer
# ---------------------------------------------------------------------------------------------


def test_committed_pin_keeps_a_coastline_feature_in_the_water_layer():
    """Every pin that stores a sea must still publish at least one `water` / `coastline` feature.

    The regression the sea gate exists to make loud: `select_for_publication` used to rank every
    derived feature by area in one pass; area is ~0 for a line, so the water layer's cap kept 150
    reservoirs and dropped every coastline, the sea rule switched off, and none of the four
    artefacts changed shape. A stored sea polygon is proof the extract HAD shoreline lines, so a pin
    with a sea and no coastline feature is that defect coming back. Silent-break cost: the map draws
    a coastline the grid does not believe in, and sea cells publish displacements.
    """
    checked: list[str] = []
    for case_id in C.CASE_IDS:
        if pinned_sea_payload(case_id) is None:
            continue
        classes = pinned_water_classes(case_id)
        assert "coastline" in classes, (
            f"config/context/{case_id}.json stores a derived sea but has {len(classes)} water "
            "feature(s) and none is a coastline: the per-kind cap dropped every line again, so the "
            "sea rule cannot be re-derived from the pin"
        )
        checked.append(case_id)

    assert checked, (
        "no committed pin stores a derived sea, so the cap guard checked nothing: either the pins "
        "were removed or `sea_polygons` stopped deriving a sea for every coastal case"
    )


# ---------------------------------------------------------------------------------------------
# 8. The gate as the checker runs it, on artefacts generated in a scratch tree
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sea_gate_evidence(tmp_path_factory):
    """Generate one case with a derived sea and one without, and keep the checker's own reports.

    Why generated instead of copied from `data/processed/`: this file may not write that tree (it
    belongs to another role), and the gate's verdict has to be the one `checks.assert_case` actually
    returned rather than one reconstructed here. `generate_case` writes the four artefacts under
    `tmp_path` and runs the checker on them; the coastal/coastless split comes from the committed
    pins (a stored sea polygon), so no case id is hardcoded.
    """
    study = load_study_cases()
    coastal = next((case_id for case_id in C.CASE_IDS if pinned_sea_payload(case_id) is not None), None)
    coastless = next(
        (
            case_id
            for case_id in C.CASE_IDS
            if CTX.read_pin(case_id) is not None and pinned_sea_payload(case_id) is None
        ),
        None,
    )
    if coastal is None or coastless is None:
        pytest.fail(
            "the gate cannot be driven offline: coastal="
            f"{coastal!r}, coastless={coastless!r} (pins in {CTX.PIN_DIR})"
        )
    out = tmp_path_factory.mktemp("sea-gate")
    reports: dict[str, K.CaseReport] = {}
    contexts: dict[str, CTX.CaseContext] = {}
    directories: dict[str, Path] = {}
    for case_id in (coastal, coastless):
        reports[case_id] = generate_case(
            study.cases[case_id], study, out, DEFAULT_SEED, per_cell_series=False
        )
        contexts[case_id] = CTX.load_context(case_id, provenance=provenance_for(case_id))
        directories[case_id] = out / case_id
    return {
        "coastal": coastal,
        "coastless": coastless,
        "study": study,
        "reports": reports,
        "contexts": contexts,
        "directories": directories,
    }


def test_assert_case_runs_the_sea_check_for_a_case_with_a_derived_sea(sea_gate_evidence):
    """A case whose context has a sea polygon must report the `sea:` line, i.e. the rule ran.

    The gate only appends that line when it has a polygon to count against; a case whose sea was
    lost reports nothing and passes. This pins that the branch is reached and evaluated on a real,
    self-consistent case generated offline, not merely that the message text exists.
    Silent-break cost: a gate that quietly skips every coastal case reports PASS forever.
    """
    case_id = sea_gate_evidence["coastal"]
    context = sea_gate_evidence["contexts"][case_id]
    assert context.sea is not None, (
        f"{case_id} stores a sea polygon but its context resolved none: the sea gate would skip it, "
        "which is how the size-cap defect hid"
    )
    # The reader rebuilds the DISTANCE geometry as well as the stored sea, and from the pinned
    # features rather than from the (absent) extract: a reader that returned only `sea` would make
    # `Coast.distance` — the coherence model's coastal gradient — silently unavailable on the pinned
    # path, which is the path every artefact is generated from.
    assert context.coast is not None, (
        f"{case_id} publishes coastline features but its reconstructing reader returned no `Coast`: "
        "the pinned path would lose the coastline distance geometry"
    )
    assert context.coast.lines and all(len(line) >= 2 for line in context.coast.lines), (
        f"{case_id}'s rebuilt `Coast` carries {context.coast.lines!r}: a polyline needs two vertices "
        "before `polyline_metrics` can measure anything"
    )
    checks = sea_gate_evidence["reports"][case_id].checks
    lines = [line for line in checks if line.startswith("sea:")]
    assert lines, f"{case_id} has a derived sea but assert_case reported no `sea:` line; it reported {checks}"
    assert "polygon" in lines[0], f"the sea line does not mention the polygon it measured: {lines[0]!r}"


def test_assert_case_skips_the_sea_check_for_a_case_without_a_coastline(sea_gate_evidence):
    """A closed-basin case must NOT report a `sea:` line — no sea, no rule, no fiction.

    What a silent break would cost: a gate running against a synthetic or stale sea for Mexico City
    would either fail a legitimate case or (worse) label inland cells as water, deleting real
    subsidence from the primary demo.
    """
    case_id = sea_gate_evidence["coastless"]
    assert sea_gate_evidence["contexts"][case_id].sea is None, (
        f"{case_id} stores no sea polygon but its context resolved one"
    )
    lines = [line for line in sea_gate_evidence["reports"][case_id].checks if line.startswith("sea:")]
    assert not lines, f"{case_id} has no coastline but the sea gate reported {lines!r}"
