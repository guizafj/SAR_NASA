#!/usr/bin/env python3
"""GIS context enrichment: an external land-cover source → the frozen context artefact.

Two pure functions and a CLI, in that order of importance. Both functions are offline and
deterministic: no network, no clock, no global state. The caller passes a local file.

    mmap_source_features(gdf, case_id, provenance_id)   # source tags -> frozen layer/class
    join_land_use(grid, context)                        # centroids -> one land_use per cell
    derive_risk_level(grid)                             # replay the frozen risk rule, report drift

Why this file exists
--------------------
`docs/data-contract-v1.md` §6 freezes `context_layers.geojson` and §7.1/§7.4 freeze the
vocabularies. The mock (`pipeline/mock/`) *consumes* this module's join to publish its context
layers from a real OSM/Overture/GHSL-style extract the caller supplies; the invented geography
survives only as the declared synthetic fallback (`pipeline/context.py::SYNTHETIC`), used when
neither a pin nor an extract exists.
It never invents a class that is not in `contracts.CONTEXT_CLASSES`, never drops a
feature just because its tags are unmapped (it becomes `land_use = "unknown"`), and never guesses a
risk threshold (it calls `contracts.risk_level_from_velocity`, the only definition).

The AOI is the only geography this module knows: `config/study_cases.yaml` gives the bbox, the AOI
ring and the analysis CRS per case. That config is read here as *case parameters* (not contract
values); nothing in this file re-declares a field name, enum, unit or threshold from
`src/domain/contracts.py`.

What this module deliberately does NOT do
------------------------------------------
* The `land_use.csv` sidecar it writes carries the centroid join only: it does NOT apply the sea
  polygon rule (`pipeline/context.py::label_cells`), because the sea is a property of the AOI and of
  the case, not of the tag table. A cell over the sea can therefore be `unknown` in the sidecar and
  `water` in a generated case; the generator is the authority.
* It does not rewrite `nisar_grid.parquet`. That artefact belongs to the producer that wrote it
  (`pipeline/generate_mock.py`, and later the real ingest pipeline). Enrichment writes a
  `land_use.csv` sidecar keyed by `cell_id`; folding it in is a one-line join owned by whoever
  owns the grid (see `docs`/report). Overwriting another producer's artefact from a context step is
  how two processes start disagreeing about provenance.
* The default sidecar location is `data/staging/<case>/land_use.csv`, deliberately outside the
  case directory. `pipeline/validate_artifacts.py` admits only the four frozen artefacts in a case
  directory, so the sidecar is a *staging* file: fold it into the grid and remove it (or point
  `--land-use-out` somewhere else) before running the gate. The CLI prints that reminder.
* It does not rasterise GHSL built-up surfaces or any other raster source. A raster has no
  `layer`/`class` properties to map; it would need a polygonisation + threshold step that belongs
  in a raster ingester (see "Left out" at the bottom of this docstring).
* It does not clip to the AOI inside the pure functions; the CLI does, because clipping is a
  file-level decision (see `select_in_aoi`).
* It does not download anything. No Overpass, no GHSL, no network imports.

Mapping table (single source of truth: `TAG_RULES`, first match wins)
---------------------------------------------------------------------
Rules are evaluated in table order, so *specific* tags precede wildcards and water precedes
everything else. `values=None` is a wildcard. Tags not named below fall through to
`land_use / unknown`, which is a legitimate frozen class, not an error.

    water (win over everything: never claim a built cell over water)
      natural=coastline                  -> water/coastline
      natural=water                      -> water/reservoir        (reservoir = frozen generic inland water)
      landuse=reservoir|basin            -> water/reservoir
      natural=wetland                    -> water/reservoir        (no wetland class; water is the safe direction)
      waterway=river|stream|riverbank    -> water/river
      waterway=canal|drain|ditch         -> water/canal
      natural=spring                     -> water/aquifer          (a spring is the surface expression of the aquifer)

    infrastructure (utility assets; point/line features carry the map, areal ones also rank)
      man_made=water_works|wastewater_plant        -> infrastructure/water-treatment
      amenity=wastewater_plant                     -> infrastructure/water-treatment
      man_made=water_well                          -> infrastructure/water-well
      amenity=drinking_water|water_point|fountain|water_tap -> infrastructure/water-well
      man_made=pipeline                            -> infrastructure/pipeline
      man_made=dyke|embankment|levee                -> infrastructure/levee
      power=plant|line|substation|generator         -> infrastructure/power
      railway=station|halt|subway_entrance          -> infrastructure/metro-station
      public_transport=station                      -> infrastructure/metro-station
      amenity=bus_station|ferry_terminal            -> infrastructure/metro-station

    land_use (areal land cover; the classes the grid column uses)
      landuse=residential                          -> land_use/urban-residential
      landuse=industrial                           -> land_use/industrial
      landuse=commercial|retail                    -> land_use/urban-dense
      landuse=railway|port|depot                   -> land_use/infrastructure
      landuse=landfill                             -> land_use/industrial
      landuse=quarry|construction|brownfield       -> land_use/bare
      landuse=forest|meadow|farmland|orchard|vineyard|allotments -> land_use/vegetation
      natural=wood|scrub|grassland|heath           -> land_use/vegetation
      natural=bare_rock|scree|sand|shingle|beach   -> land_use/bare
      amenity=* (anything else)                    -> land_use/urban-dense   (services sit in the dense fabric)

    admin_boundary (never a land use: see `land_use_of`)
      place=city|town|municipality                 -> admin_boundary/municipality
      place=suburb|borough|quarter|neighbourhood|city_district|district -> admin_boundary/district
      boundary=administrative                      -> admin_boundary/district   (admin_level is not a class)
      pipeline=aoi                                 -> admin_boundary/aoi        (reserved tag: the config AOI)

    buildings (last: any other tag on the same feature refines a footprint)
      building=apartments|hotel|dormitory          -> land_use/urban-dense
      building=industrial|warehouse|factory|manufacture -> land_use/industrial
      building=commercial|retail|office|supermarket|hospital|school|university|train_station|stadium
                                                   -> land_use/urban-dense
      building=*                                   -> land_use/urban-residential (most footprints are housing)

Land-use precedence when polygons overlap (`LAND_USE_PRECEDENCE`, deterministic)
-------------------------------------------------------------------------------
    water > infrastructure > industrial > urban-dense > urban-residential > vegetation > bare > unknown

Justification, one line each:
    water            — inundation/subsidence is physically first: a flooded cell is not a built cell.
    infrastructure   — a utility asset (power plant, treatment works) outranks the land around it.
    industrial       — extraction/industry is the strongest built-up hazard class after utilities.
    urban-dense      — dense fabric is where exposed population and value concentrate.
    urban-residential— lower exposure per cell than dense fabric, still built.
    vegetation       — open, vegetated ground: low exposure.
    bare             — exposed ground: lowest exposure of all mapped ground.
    unknown          — no mapped feature contains the centroid; never silently promoted.

Within the same class the tie-break is (smallest area, then geometry digest): "most specific
polygon wins", and it is a function of feature *content*, not of row order, so shuffling the input
cannot change the assignment.

Geometry fidelity
-----------------
`contracts.validate_context_layers` admits only `Point`, `LineString`, `Polygon` and
`MultiPolygon`. OSM routinely emits `MultiLineString`/`GeometryCollection`, so mapped features are
exploded into allowed parts (all parts are kept, none are dropped) and invalid polygons go through
`shapely.make_valid`. Features with no geometry at all cannot be published; they are counted in
`attrs["dropped_no_geometry"]` so the CLI can report the loss instead of hiding it.

Left out on purpose, and where it belongs
-----------------------------------------
* Raster sources (GHSL built-up surface, WorldPop, Copernicus land cover): they need a
  raster → polygon/vocabulary step. Belongs in a raster ingester, not here. If GHSL is used as a
  *class* source, the honest mapping (`built-up` → `urban-dense`) is a threshold decision that
  must be documented with the raster's resolution; a GeoJSON of polygons derived from it can be
  fed to `mmap_source_features` unchanged.
* OSM relations/multipolygons with holes are handled by shapely, but relation *assembly* from a
  raw `.osm.pbf` is an ingest job (`pipeline/ingest/`), not an enrichment job.
* Streaming huge extracts: whole file in memory. An AOI-sized extract (the caller's contract) is
  a few MB; a country extract is not, and this module will not pretend to stream it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

import numpy as np
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python pipeline/enrich_context.py` and pytest from the repo root
    sys.path.insert(0, str(ROOT))

import geopandas as gpd  # noqa: E402  (import order: sys.path first, then the repo)
import yaml  # noqa: E402
from shapely.geometry import LineString, mapping  # noqa: E402
from shapely.geometry.base import BaseGeometry  # noqa: E402

from src.domain import contracts as C  # noqa: E402
from pipeline.aoi import load_aoi  # noqa: E402

CONFIG_PATH = ROOT / "config" / "study_cases.yaml"

#: Source provider recorded when the input has no `source` column and the caller passes nothing.
DEFAULT_SOURCE = "external context source (caller-supplied file)"

#: Tag values that mean "not this thing" in OSM. `building=no` must not become a building.
NEGATIVE_TAG_VALUES = frozenset({"no", "none", "false"})

#: Ordered candidate columns for the published `name` property (optional in §6).
NAME_KEYS: tuple[str, ...] = ("name", "official_name", "name:en", "ref")

#: The four geometry types `validate_context_layers` admits.
ALLOWED_GEOMETRY_TYPES = frozenset({"Point", "LineString", "Polygon", "MultiPolygon"})

#: Coordinates are rounded before writing: 1e-7 deg ≈ 1 cm, far below any SAR posting.
COORD_DECIMALS = 7


# ---------------------------------------------------------------------------------------------
# 1. The mapping table. One table, explicit, ordered; nothing else maps tags.
# ---------------------------------------------------------------------------------------------


class TagRule(NamedTuple):
    """One row of the frozen vocabulary map: a source tag → `(layer, class)`.

    `values is None` means wildcard ("any non-negative value of this key"). Rules are matched in
    `TAG_RULES` order and the first match wins, which is why the table is ordered by specificity.
    """

    key: str
    values: tuple[str, ...] | None
    layer: str
    klass: str
    note: str


TAG_RULES: tuple[TagRule, ...] = (
    # --- water: first because a cell over water must never be reported as built -----------------
    TagRule("natural", ("coastline",), "water", "coastline", "the land/sea line"),
    TagRule("natural", ("water",), "water", "reservoir", "generic inland water; reservoir is the frozen generic class"),
    TagRule("landuse", ("reservoir", "basin"), "water", "reservoir", "OSM also tags water bodies under landuse"),
    TagRule("natural", ("wetland",), "water", "reservoir", "no wetland class: water is the safe direction"),
    TagRule("waterway", ("river", "stream", "riverbank"), "water", "river", "flowing water, any size"),
    TagRule("waterway", ("canal", "drain", "ditch"), "water", "canal", "engineered channel"),
    TagRule("natural", ("spring",), "water", "aquifer", "a spring is the surface expression of the aquifer"),
    # --- infrastructure: utility assets, the layer the map needs next ---------------------------
    TagRule("man_made", ("water_works", "wastewater_plant"), "infrastructure", "water-treatment", "water utility plant"),
    TagRule("amenity", ("wastewater_plant",), "infrastructure", "water-treatment", "OSM amenity spelling of the same plant"),
    TagRule("man_made", ("water_well",), "infrastructure", "water-well", "groundwater abstraction point"),
    TagRule(
        "amenity",
        ("drinking_water", "water_point", "fountain", "water_tap"),
        "infrastructure",
        "water-well",
        "public water supply point; water-well is the only frozen point-water class",
    ),
    TagRule("man_made", ("pipeline",), "infrastructure", "pipeline", "linear utility, hazard to subsidence"),
    TagRule("man_made", ("dyke", "embankment", "levee"), "infrastructure", "levee", "flood defence"),
    TagRule("power", ("plant", "line", "substation", "generator"), "infrastructure", "power", "one 'power' class for every power asset"),
    TagRule("railway", ("station", "halt", "subway_entrance"), "infrastructure", "metro-station", "transit node; metro-station is the only frozen transit class"),
    TagRule("public_transport", ("station",), "infrastructure", "metro-station", "transit node, schema.org spelling"),
    TagRule("amenity", ("bus_station", "ferry_terminal"), "infrastructure", "metro-station", "transit node outside the rail network"),
    # --- land use: the classes the grid column uses --------------------------------------------
    TagRule("landuse", ("residential",), "land_use", "urban-residential", "housing land"),
    TagRule("landuse", ("industrial",), "land_use", "industrial", "industry/extraction land"),
    TagRule("landuse", ("commercial", "retail"), "land_use", "urban-dense", "commercial core is the dense fabric"),
    TagRule("landuse", ("railway", "port", "depot"), "land_use", "infrastructure", "transport/utility land: infrastructure is the frozen class"),
    TagRule("landuse", ("landfill",), "land_use", "industrial", "engineered waste facility"),
    TagRule("landuse", ("quarry", "construction", "brownfield"), "land_use", "bare", "exposed/cleared ground"),
    # Institutional land: a campus or a church precinct is built land, not open ground. Without
    # these two rules they fall to `unknown` (measured: 18 `education` + 9 `religious` polygons in a
    # 3.3 x 3.5 km Malaga box), which is the wrong answer for land that carries buildings.
    TagRule("landuse", ("education",), "land_use", "urban-dense", "school/university campus: built service land"),
    TagRule("landuse", ("religious",), "land_use", "urban-dense", "church/monastery precinct: built service land"),
    TagRule(
        "landuse",
        ("forest", "meadow", "farmland", "orchard", "vineyard", "allotments"),
        "land_use",
        "vegetation",
        "green/agricultural land; the vocabulary has no separate agricultural class",
    ),
    # `landuse=grass` is the second most common landuse polygon in a real Malaga extract (measured:
    # 903 of 2709 features in a 3.3 x 3.5 km box) and mapped to `unknown` before this rule existed,
    # which is why a real extract used to publish an unmapped area that was mostly grass.
    TagRule(
        "landuse",
        ("grass", "village_green", "greenfield", "recreation_ground", "meadow", "cemetery", "flowerbed"),
        "land_use",
        "vegetation",
        "open green land, including cemeteries and undeveloped (greenfield) plots",
    ),
    TagRule("natural", ("wood", "scrub", "grassland", "heath"), "land_use", "vegetation", "natural green cover"),
    TagRule("natural", ("tree_row", "tree"), "land_use", "vegetation", "tree cover (usually linear; lines never assign a cell)"),
    TagRule("natural", ("bare_rock", "scree", "sand", "shingle", "beach", "cliff", "earth_bank"), "land_use", "bare", "unvegetated surface"),
    # Recreation land is the single largest OSM gap in the two American/Asian AOIs (measured: 903
    # `leisure` polygons in a 4 x 4 km Mexico City box, 305 in a 4 x 4 km Jakarta box). A park or a
    # pitch is open ground with low exposure: `vegetation`, never `urban-dense`.
    TagRule(
        "leisure",
        ("park", "garden", "common", "playground", "pitch", "track", "nature_reserve", "golf_course", "recreation_ground"),
        "land_use",
        "vegetation",
        "open recreation land: no buildings to expose",
    ),
    TagRule("leisure", ("sports_centre", "stadium", "water_park"), "land_use", "urban-dense", "built sports complex"),
    TagRule("place", ("square",), "land_use", "urban-dense", "hard-surface public square inside the built fabric"),
    TagRule("amenity", None, "land_use", "urban-dense", "any other amenity is a service in the built-up fabric"),
    # --- admin boundaries: published for the map, never used as a land use ---------------------
    TagRule("place", ("city", "town", "municipality"), "admin_boundary", "municipality", "top-level administered place"),
    TagRule(
        "place",
        ("suburb", "borough", "quarter", "neighbourhood", "city_district", "district"),
        "admin_boundary",
        "district",
        "sub-municipal unit",
    ),
    TagRule("boundary", ("administrative",), "admin_boundary", "district", "admin_level is not a class; default to district"),
    TagRule("pipeline", ("aoi",), "admin_boundary", "aoi", "reserved tag set by this CLI on the config AOI polygon"),
    # --- buildings: last, so any land-use/infrastructure tag on the same feature wins ------------
    TagRule("building", ("apartments", "hotel", "dormitory"), "land_use", "urban-dense", "multi-unit footprint"),
    TagRule("building", ("industrial", "warehouse", "factory", "manufacture"), "land_use", "industrial", "industrial footprint"),
    TagRule(
        "building",
        ("commercial", "retail", "office", "supermarket", "hospital", "school", "university", "train_station", "stadium"),
        "land_use",
        "urban-dense",
        "large-footprint service building",
    ),
    TagRule("building", None, "land_use", "urban-residential", "default footprint: most OSM buildings are housing"),
)

#: Deterministic resolution order for overlapping polygons. Must be a permutation of `LAND_USE`.
LAND_USE_PRECEDENCE: tuple[str, ...] = (
    "water",
    "infrastructure",
    "industrial",
    "urban-dense",
    "urban-residential",
    "vegetation",
    "bare",
    "unknown",
)

#: Layers that *are* a land use by themselves (§7.1/§7.4 share the vocabulary).
LAYER_TO_LAND_USE: Mapping[str, str] = {"water": "water", "infrastructure": "infrastructure"}


def mapping_problems() -> list[str]:
    """Return every defect in `TAG_RULES` (empty list == table is inside the frozen vocabulary).

    This is the guard the tests and the CLI use: a typo (`urban-dense` → `urbandense`) is caught
    here instead of producing a GeoJSON that fails the contract on someone else's machine.
    """
    problems: list[str] = []
    seen: dict[tuple[str, tuple[str, ...] | None], int] = {}
    for index, rule in enumerate(TAG_RULES):
        where = f"TAG_RULES[{index}] {rule.key}={rule.values or '*'}"
        if rule.layer not in C.CONTEXT_LAYERS:
            problems.append(f"{where}: layer {rule.layer!r} not in {list(C.CONTEXT_LAYERS)}")
            continue
        allowed = C.CONTEXT_CLASSES[rule.layer]
        if rule.klass not in allowed:
            problems.append(f"{where}: class {rule.klass!r} not in {list(allowed)} for layer {rule.layer!r}")
        if rule.values is not None:
            if not rule.values:
                problems.append(f"{where}: empty value tuple (use None for a wildcard)")
            for value in rule.values:
                if value != value.strip().lower() or not value:
                    problems.append(f"{where}: value {value!r} is not normalised (lowercase, stripped)")
        key = (rule.key, rule.values)
        if key in seen:
            problems.append(f"{where}: duplicate of TAG_RULES[{seen[key]}] (one of them is dead)")
        seen[key] = index
        if rule.values is None:
            # A wildcard must be the last rule for its key, otherwise it shadows the next rules.
            later = [r for r in TAG_RULES[index + 1 :] if r.key == rule.key]
            if later:
                problems.append(f"{where}: wildcard shadows {len(later)} later rule(s) for key {rule.key!r}")
    if set(LAND_USE_PRECEDENCE) != set(C.LAND_USE):
        problems.append(
            f"LAND_USE_PRECEDENCE {list(LAND_USE_PRECEDENCE)} is not a permutation of contracts.LAND_USE "
            f"{list(C.LAND_USE)}"
        )
    if len(set(LAND_USE_PRECEDENCE)) != len(LAND_USE_PRECEDENCE):
        problems.append("LAND_USE_PRECEDENCE contains duplicates")
    return problems


def land_use_of(layer: str, klass: str) -> str | None:
    """Project a `(layer, class)` pair onto the grid's `land_use` vocabulary.

    `land_use` features carry their class directly; `water`/`infrastructure` features are that land
    use by definition (§7.1 shares the vocabulary); `admin_boundary` features return `None` because
    a boundary covers every land use and must never win the join.
    """
    if layer == "land_use":
        if klass not in C.LAND_USE:
            raise ValueError(f"land_use class {klass!r} is not in the frozen vocabulary {list(C.LAND_USE)}")
        return klass
    if layer in LAYER_TO_LAND_USE:
        return LAYER_TO_LAND_USE[layer]
    if layer == "admin_boundary":
        return None
    raise ValueError(f"undeclared layer {layer!r}; allowed {list(C.CONTEXT_LAYERS)}")


# ---------------------------------------------------------------------------------------------
# 2. Source tags -> frozen vocabulary
# ---------------------------------------------------------------------------------------------


def _clean_str(value: Any) -> str:
    """Tag value as a stripped, lowercased string; `""` for None/NaN/whitespace."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip().lower()
    return "" if text in {"nan", "none"} else text


def _rule_mask(series: pd.Series, values: tuple[str, ...] | None) -> pd.Series:
    """True where the tag column satisfies the rule (wildcard = any positive value)."""
    normalised = series.map(_clean_str)
    if values is None:
        return (normalised != "") & ~normalised.isin(NEGATIVE_TAG_VALUES)
    allowed = frozenset(values)
    # OSM allows semicolon-separated multi-values (`landuse=industrial;commercial`).
    return normalised.map(lambda text: any(token in allowed for token in text.split(";") if token))


def _classify(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Apply `TAG_RULES` in order; unmapped rows become `land_use / unknown`."""
    n = len(frame)
    layer: np.ndarray = np.full(n, "land_use", dtype=object)
    klass: np.ndarray = np.full(n, "unknown", dtype=object)
    taken = np.zeros(n, dtype=bool)
    for rule in TAG_RULES:
        if rule.key not in frame.columns:
            continue
        mask = _rule_mask(frame[rule.key], rule.values).to_numpy(dtype=bool) & ~taken
        if not mask.any():
            continue
        layer[mask] = rule.layer
        klass[mask] = rule.klass
        taken |= mask
    return layer, klass


def _geometry_parts(geometry: Any) -> list[BaseGeometry]:
    """Split a geometry into the part types the frozen contract admits (all parts kept)."""
    if geometry is None or not isinstance(geometry, BaseGeometry) or geometry.is_empty:
        return []
    if not geometry.is_valid:
        # OSM polygons are frequently self-intersecting. Repair instead of dropping.
        try:
            geometry = shapely.make_valid(geometry)
        except Exception:  # noqa: BLE001 - a repair that fails must not take the whole run down
            return []
    kind = geometry.geom_type
    if kind in ALLOWED_GEOMETRY_TYPES:
        return [geometry]
    if kind == "LinearRing":
        return [LineString(geometry)]
    if kind == "GeometryCollection" or kind.startswith("Multi"):
        parts: list[BaseGeometry] = []
        for part in geometry.geoms:
            parts.extend(_geometry_parts(part))
        return parts
    return []


def _feature_name(row: Mapping[str, Any]) -> str | None:
    for key in NAME_KEYS:
        text = _clean_str(row.get(key))
        if text:
            return str(row.get(key)).strip()
    return None


def mmap_source_features(
    gdf: gpd.GeoDataFrame,
    case_id: str,
    provenance_id: str,
    *,
    source: str = DEFAULT_SOURCE,
) -> gpd.GeoDataFrame:
    """Map any GeoDataFrame of OSM/GHSL/Overture-style features onto the frozen vocabulary.

    Output columns, exactly and in this order (§6):
        layer, class, name, case_id, crs, source, provenance_id, geometry

    Behaviour worth knowing:
    * reprojects to EPSG:4326 if needed and records the original CRS in
      `out.attrs["source_crs"]` / `out.attrs["reprojected"]` (attrs are diagnostics, never columns);
    * a per-row `source` column wins over the `source` argument (so a mixed extract stays honest);
    * unmapped tags → `land_use / unknown`; nothing is dropped for being unmapped;
    * `MultiLineString`/`GeometryCollection`/`LinearRing` are exploded into admissible parts;
    * features with no usable geometry are dropped but counted in `attrs["dropped_no_geometry"]`.
    """
    if case_id not in C.CASE_IDS:
        raise ValueError(f"undeclared case_id {case_id!r}; the frozen cases are {list(C.CASE_IDS)}")
    if not C.PROVENANCE_ID_RE.match(str(provenance_id)):
        raise ValueError(
            f"provenance_id {provenance_id!r} does not match {C.PROVENANCE_ID_RE.pattern} "
            "(build it with contracts.provenance_id)"
        )
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise TypeError(f"expected a GeoDataFrame, got {type(gdf).__name__}")
    if gdf.crs is None:
        raise ValueError(
            "source GeoDataFrame has no CRS: refusing to guess. GeoJSON is EPSG:4326 by RFC 7946, "
            "so call .set_crs('EPSG:4326', allow_override=True) after reading a bare GeoJSON."
        )

    source_crs = str(gdf.crs)
    work = gdf.copy()
    reprojected = False
    if work.crs is not None and str(work.crs).upper() != C.CRS_OUTPUT:
        work = work.to_crs(C.CRS_OUTPUT)
        reprojected = True

    dropped_no_geometry = int(work.geometry.isna().sum())

    # Classify on the original rows, then explode geometry so every part keeps the same class.
    layer, klass = _classify(work)
    names = pd.Series([_feature_name(row) for row in work.to_dict("records")], index=work.index, dtype=object)
    if "source" in work.columns:
        row_sources = work["source"].map(_clean_str).map(lambda text: text if text else source)
    else:
        row_sources = pd.Series(source, index=work.index, dtype=object)

    work = work.assign(_layer=layer, _klass=klass, _name=names, _source=row_sources)
    work["_parts"] = work.geometry.map(_geometry_parts)
    # Null, empty and unrepairable geometries all end up with no part: count all of them, not
    # just the nulls, so the CLI can report a loss instead of hiding it.
    dropped_no_geometry = int((work["_parts"].map(len) == 0).sum())
    exploded = pd.DataFrame(work.drop(columns=[work.geometry.name])).explode("_parts", ignore_index=True)
    exploded = exploded[exploded["_parts"].notna()]

    out = gpd.GeoDataFrame(
        {
            "layer": exploded["_layer"].to_numpy(dtype=object),
            "class": exploded["_klass"].to_numpy(dtype=object),
            "name": exploded["_name"].to_numpy(dtype=object),
            "case_id": case_id,
            "crs": C.CRS_OUTPUT,
            "source": exploded["_source"].to_numpy(dtype=object),
            "provenance_id": provenance_id,
        },
        geometry=list(exploded["_parts"]),
        crs=C.CRS_OUTPUT,
    )
    out = out.reset_index(drop=True)
    out.attrs["source_crs"] = source_crs
    out.attrs["reprojected"] = reprojected
    out.attrs["dropped_no_geometry"] = dropped_no_geometry
    out.attrs["features_in"] = int(len(gdf))
    out.attrs["parts_out"] = int(len(out))
    return out


# ---------------------------------------------------------------------------------------------
# 3. Context -> grid cells
# ---------------------------------------------------------------------------------------------


def _check_precedence(precedence: Sequence[str]) -> tuple[str, ...]:
    order = tuple(precedence)
    if len(set(order)) != len(order) or set(order) != set(C.LAND_USE):
        raise ValueError(
            f"precedence must be a permutation of contracts.LAND_USE {list(C.LAND_USE)}, got {list(order)}"
        )
    return order


def _feature_id(layer: str, klass: str, geometry: BaseGeometry) -> str:
    """Stable, content-derived id: survives row shuffles, dedup and reruns (a row number does not)."""
    digest = hashlib.sha1(shapely.to_wkb(geometry), usedforsecurity=False).hexdigest()[:8]
    return f"{layer}:{klass}:{digest}"


def _areal_context(context: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach rank/area/id to the areal features that can contain a centroid.

    Points and lines cannot contain anything (a point is 0-D, a line is 1-D), so they are published
    to the map but never assign a land use. `admin_boundary` features are dropped here for the same
    reason the contract asks for them at all: a boundary covers everything and would win every tie.
    """
    missing = {"layer", "class", "geometry"} - set(context.columns)
    if missing:
        raise ValueError(
            f"context is missing columns {sorted(missing)}; pass the output of mmap_source_features "
            "(it carries layer/class), not a raw source file"
        )
    # Positional access on purpose: `class` is a Python keyword, so itertuples renames any column
    # called `class` and attribute access becomes position-dependent. Never rely on that.
    columns = list(context.columns)
    i_layer, i_class = columns.index("layer"), columns.index("class")
    i_geom = columns.index(context.geometry.name)
    rows = []
    for record in context.itertuples(index=False, name=None):
        layer, klass, geometry = str(record[i_layer]), str(record[i_class]), record[i_geom]
        if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
            continue
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            continue
        land_use = land_use_of(layer, klass)
        if land_use is None:
            continue
        rows.append(
            {
                "_land_use": land_use,
                "_id": _feature_id(layer, klass, geometry),
                "_area": float(geometry.area),
                "geometry": geometry,
            }
        )
    if not rows:
        return gpd.GeoDataFrame(
            {"_land_use": [], "_id": [], "_area": []}, geometry=[], crs=C.CRS_OUTPUT
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=C.CRS_OUTPUT)


def join_land_use(
    grid: pd.DataFrame,
    context: gpd.GeoDataFrame,
    precedence: Sequence[str] = LAND_USE_PRECEDENCE,
) -> pd.DataFrame:
    """One `land_use` per grid cell: the class of the areal feature containing its centroid.

    `grid` is the `nisar_grid.parquet`-shaped frame (`cell_id`, `lat`, `lon`; other columns are
    ignored). `context` is the output of `mmap_source_features`. Centroids are rebuilt from
    `lat`/`lon` in EPSG:4326, so a stale geometry column on the grid cannot change the answer.

    Overlaps resolve by `precedence`, then by smallest area, then by geometry digest — never by
    row order. A cell with no containing feature gets `"unknown"`.

    Returns `cell_id`, `land_use`, `context_id` (content-derived id of the winning feature, `""`
    when unmatched). One row per input row, in input order.
    """
    order = _check_precedence(precedence)
    rank = {label: index for index, label in enumerate(order)}

    required = {"cell_id", "lat", "lon"}
    missing = required - set(getattr(grid, "columns", []))
    if missing:
        raise ValueError(f"grid is missing columns {sorted(missing)}; expected nisar_grid columns")
    if grid["cell_id"].duplicated().any():
        raise ValueError("grid has duplicate cell_id; the contract allows one row per H3 cell")
    if grid[["lat", "lon"]].isna().any().any():
        raise ValueError("grid has null lat/lon; the contract forbids nodata rows in the grid")

    cells = gpd.GeoDataFrame(
        {"cell_id": grid["cell_id"].astype(str).to_numpy()},
        geometry=gpd.points_from_xy(
            pd.to_numeric(grid["lon"]).to_numpy(), pd.to_numeric(grid["lat"]).to_numpy()
        ),
        crs=C.CRS_OUTPUT,
    )
    cells = cells.reset_index(drop=True)

    joined = pd.DataFrame({"cell_id": cells["cell_id"], "land_use": "unknown", "context_id": ""})
    areal = _areal_context(context)
    if areal.empty:
        return joined

    areal = areal.assign(_rank=areal["_land_use"].map(rank).astype(float))
    candidates = gpd.sjoin(
        cells[["cell_id", "geometry"]],
        areal[["_land_use", "_id", "_area", "_rank", "geometry"]],
        how="inner",
        predicate="intersects",  # boundary-touching centroids count: no unknown seam between polygons
    )
    if candidates.empty:
        return joined

    candidates = candidates.sort_values(
        ["cell_id", "_rank", "_area", "_id"], kind="mergesort"
    ).drop_duplicates("cell_id", keep="first")
    winner = candidates.set_index("cell_id")[["_land_use", "_id"]]
    joined = joined.set_index("cell_id")
    joined.loc[winner.index, "land_use"] = winner["_land_use"].astype(str)
    joined.loc[winner.index, "context_id"] = winner["_id"].astype(str)
    return joined.reset_index()


def derive_risk_level(grid: pd.DataFrame) -> pd.DataFrame:
    """Recompute `risk_level` with `contracts.risk_level_from_velocity`; return the disagreements.

    The frozen rule has exactly one definition and it is not in this file. A grid whose stored
    `risk_level` disagrees with the rule is a data bug (a hand-written threshold, a relabelled
    column, a re-run with an old contract); this returns those cells so the caller can surface it.

    Columns: `cell_id`, `velocity_mm_yr`, `risk_level_stored`, `risk_level_derived`. An empty frame
    means the grid is consistent.
    """
    if "velocity_mm_yr" not in grid.columns:
        raise ValueError("grid has no velocity_mm_yr column; risk_level cannot be re-derived")
    if "risk_level" not in grid.columns:
        raise ValueError("grid has no risk_level column; there is nothing to disagree with")
    velocity = pd.to_numeric(grid["velocity_mm_yr"], errors="coerce")
    derived = velocity.map(C.risk_level_from_velocity)
    stored = grid["risk_level"].astype(object)
    disagree = stored.astype(str) != derived.astype(str)
    result = pd.DataFrame(
        {
            "cell_id": (
                grid["cell_id"].astype(str) if "cell_id" in grid.columns else pd.Series(range(len(grid)))
            ),
            "velocity_mm_yr": velocity,
            "risk_level_stored": stored,
            "risk_level_derived": derived,
        }
    )
    return result.loc[disagree.to_numpy(dtype=bool)].reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# 4. GeoJSON assembly and file I/O (the CLI's plumbing; pure enough to test)
# ---------------------------------------------------------------------------------------------


def _round_coordinates(value: Any, decimals: int = COORD_DECIMALS) -> Any:
    if isinstance(value, float):
        return round(value, decimals)
    if isinstance(value, Mapping):
        return {key: _round_coordinates(item, decimals) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_coordinates(item, decimals) for item in value]
    return value


def _geometry_digest(geometry: BaseGeometry) -> str:
    return hashlib.sha1(shapely.to_wkb(geometry), usedforsecurity=False).hexdigest()


def build_feature_collection(mapped: gpd.GeoDataFrame) -> dict[str, Any]:
    """Turn mapped features into the §6 FeatureCollection, deterministically ordered.

    Property order is fixed (`layer, class, name?, case_id, crs, source, provenance_id`) and
    features are sorted by `(layer, class, name, geometry digest)`, so the same input set produces
    the same bytes regardless of the order the source file happened to list its features in.
    """
    expected = ["layer", "class", "name", "case_id", "crs", "source", "provenance_id"]
    missing = [name for name in expected if name not in mapped.columns]
    if missing:
        raise ValueError(f"mapped features are missing {missing}; expected {expected} + geometry")
    if mapped.crs is None or str(mapped.crs).upper() != C.CRS_OUTPUT:
        raise ValueError(f"mapped features must be in {C.CRS_OUTPUT}, got {mapped.crs}")

    # Positional access: `class` is a keyword, so itertuples attribute names are not trustworthy.
    columns = expected + [mapped.geometry.name]
    rows = []
    for record in mapped[columns].itertuples(index=False, name=None):
        layer, klass, name, case_id, crs, source, provenance, geometry = record
        if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
            continue
        if geometry.geom_type not in ALLOWED_GEOMETRY_TYPES:
            raise ValueError(
                f"geometry type {geometry.geom_type!r} is not publishable; allowed {sorted(ALLOWED_GEOMETRY_TYPES)}"
            )
        props: dict[str, Any] = {"layer": str(layer), "class": str(klass)}
        if isinstance(name, str) and name.strip():
            props["name"] = name.strip()
        props.update(
            {
                "case_id": str(case_id),
                "crs": str(crs),
                "source": str(source),
                "provenance_id": str(provenance),
            }
        )
        rows.append((props, geometry))

    rows.sort(key=lambda item: (item[0]["layer"], item[0]["class"], item[0].get("name", ""), _geometry_digest(item[1])))
    features = [
        {
            "type": "Feature",
            "geometry": _round_coordinates(mapping(geometry)),
            "properties": props,
        }
        for props, geometry in rows
    ]
    return {"type": "FeatureCollection", "features": features}


def _atomic_write_text(path: Path, text: str) -> int:
    """Write via a sibling temp file + `os.replace`, so a killed run leaves no half artefact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path.stat().st_size


def write_context_geojson(collection: Mapping[str, Any], path: Path) -> int:
    blob = json.dumps(collection, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    return _atomic_write_text(path, blob)


def write_land_use_csv(frame: pd.DataFrame, path: Path) -> int:
    """`cell_id,land_use`, sorted by cell_id: a sidecar to join onto `nisar_grid.parquet`."""
    missing = [name for name in ("cell_id", "land_use") if name not in frame.columns]
    if missing:
        raise ValueError(f"land_use frame is missing {missing}")
    ordered = frame[["cell_id", "land_use"]].sort_values("cell_id", kind="mergesort").reset_index(drop=True)
    return _atomic_write_text(path, ordered.to_csv(index=False, lineterminator="\n"))


# ---------------------------------------------------------------------------------------------
# 5. Case config + reading the caller's file
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StudyCase:
    """The subset of `config/study_cases.yaml` this module needs (case parameters, not contract)."""

    case_id: str
    name: str
    bbox: tuple[float, float, float, float]  # W, S, E, N
    aoi_ring: tuple[tuple[float, float], ...]
    crs_analysis: str
    reference_point: tuple[float, float]
    aoi_source: str = "config/study_cases.yaml:aoi_polygon"

    def aoi_geometry(self) -> BaseGeometry:
        from shapely.geometry import Polygon

        return Polygon(self.aoi_ring)


def load_case(case_id: str, config_path: Path = CONFIG_PATH) -> StudyCase:
    """Read one case from `config/study_cases.yaml` and validate the AOI ring it declares."""
    if not config_path.exists():
        raise ValueError(f"case config not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cases = raw.get("cases") or {}
    if case_id not in cases:
        raise ValueError(f"undeclared case {case_id!r}; config declares {sorted(cases)}")
    entry = cases[case_id]
    aoi = load_aoi(case_id, entry, aoi_dir=config_path.parent / "aoi")
    return StudyCase(
        case_id=case_id,
        name=str(entry.get("name", case_id)),
        bbox=aoi.bbox,
        aoi_ring=aoi.ring,
        crs_analysis=str(entry.get("crs_analysis", "")),
        reference_point=(
            float(entry["reference_point"][0]),
            float(entry["reference_point"][1]),
        ),
        aoi_source=aoi.source,
    )


def read_context(path: Path) -> gpd.GeoDataFrame:
    """Read a local vector file. No network, no crawling, no format guessing beyond the suffix."""
    if not path.exists():
        raise ValueError(f"context file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        gdf = gpd.read_parquet(path)
    elif suffix in {".geojson", ".json", ".gpkg", ".shp", ".gdb", ".fgb"}:
        gdf = gpd.read_file(path)
    else:
        raise ValueError(
            f"unsupported context suffix {suffix!r}; pass .geojson/.json/.gpkg/.shp/.fgb/.parquet"
        )
    if gdf.crs is None and suffix in {".geojson", ".json"}:
        # RFC 7946 (GeoJSON) fixes the CRS: assuming EPSG:4326 here is the spec, not a guess.
        gdf = gdf.set_crs(C.CRS_OUTPUT, allow_override=True)
    return gdf


def select_in_aoi(
    gdf: gpd.GeoDataFrame,
    aoi: BaseGeometry,
    *,
    clip: bool = True,
) -> gpd.GeoDataFrame:
    """Keep the features that intersect the AOI (and clip them to it when `clip`).

    Reprojects to EPSG:4326 first: intersecting a UTM layer with a lon/lat AOI is a silent
    coordinate error. Clipping bounds the artefact and is honest — the file is "context in the AOI".
    Counting happens in the CLI *before* the AOI feature is appended, so an AOI-only artefact can
    never masquerade as a successful enrichment.
    """
    if gdf.crs is None:
        raise ValueError("context has no CRS; cannot select against the AOI")
    work = gdf if str(gdf.crs).upper() == C.CRS_OUTPUT else gdf.to_crs(C.CRS_OUTPUT)
    if work.empty:
        return work
    mask = work.geometry.notna() & work.geometry.intersects(aoi)
    selected = work[mask].copy()
    if clip and not selected.empty:
        selected["geometry"] = selected.geometry.intersection(aoi)
        selected = selected[~selected.geometry.is_empty]
    selected = selected[selected.geometry.notna()]
    return selected.reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------------------------


def _default_grid(case_id: str) -> Path:
    return ROOT / "data" / "processed" / case_id / "nisar_grid.parquet"


def _default_out(case_id: str) -> Path:
    return ROOT / "data" / "processed" / case_id / "context_layers.geojson"


def _default_land_use_out(case_id: str) -> Path:
    # Staging, NOT the case directory: pipeline/validate_artifacts.py admits only the four frozen
    # artefacts there, so the old default made the project's own gate reject a case it had just
    # enriched. Two independent reviews flagged it.
    return ROOT / "data" / "staging" / case_id / "land_use.csv"


def _class_counts(values: Iterable[str]) -> dict[str, int]:
    series = pd.Series(list(values), dtype=object)
    return {str(k): int(v) for k, v in series.value_counts().sort_index().items()}


def _print_mapping() -> None:
    print(f"{'source tag':<52} {'-> target':<40} why")
    for rule in TAG_RULES:
        values = "*" if rule.values is None else "|".join(rule.values)
        tag = f"{rule.key}={values}"
        print(f"{tag:<52} {f'{rule.layer}/{rule.klass}':<40} {rule.note}")
    print(f"\nprecedence: {' > '.join(LAND_USE_PRECEDENCE)}")


def _run(args: argparse.Namespace) -> int:
    case = load_case(args.case, Path(args.config))
    context_path = Path(args.context)
    grid_path = Path(args.grid) if args.grid else _default_grid(case.case_id)
    out_path = Path(args.out) if args.out else _default_out(case.case_id)
    land_use_path = Path(args.land_use_out) if args.land_use_out else _default_land_use_out(case.case_id)

    if not grid_path.exists():
        raise ValueError(
            f"grid not found: {grid_path}. The enrichment joins onto the existing grid and never "
            "creates it; run the pipeline for this case first or pass --grid."
        )
    grid = pd.read_parquet(grid_path)
    missing = {"cell_id", "lat", "lon"} - set(grid.columns)
    if missing:
        raise ValueError(f"grid {grid_path} is missing {sorted(missing)}; not an nisar_grid frame")

    source = args.source or f"caller-supplied file: {context_path.name}"
    if args.provenance_id:
        provenance = args.provenance_id
    else:
        ids = grid["provenance_id"].astype(str).unique() if "provenance_id" in grid.columns else []
        if len(ids) != 1:
            raise ValueError(
                "cannot infer provenance_id: the grid must carry exactly one provenance_id "
                f"(found {len(ids)}); pass --provenance-id {C.provenance_id(case.case_id, 'mock', '20260101', '00000000')}"
            )
        provenance = str(ids[0])

    raw = read_context(context_path)
    source_crs = str(raw.crs)
    in_aoi = select_in_aoi(raw, case.aoi_geometry(), clip=not args.no_clip)
    if in_aoi.empty:
        raise ValueError(
            f"{context_path} has no features inside the AOI of case {case.case_id!r} "
            f"(bbox {list(case.bbox)}, CRS {C.CRS_OUTPUT}). Nothing written: an empty "
            "context_layers.geojson is not a valid artefact (§6 requires features)."
        )

    mapped = mmap_source_features(in_aoi, case.case_id, provenance, source=source)
    dropped_no_geometry = int(mapped.attrs.get("dropped_no_geometry", 0))

    if args.aoi_feature:
        aoi_row = gpd.GeoDataFrame(
            {"pipeline": ["aoi"], "name": [f"{case.name} AOI"]},
            geometry=[case.aoi_geometry()],
            crs=C.CRS_OUTPUT,
        )
        mapped_aoi = mmap_source_features(
            aoi_row, case.case_id, provenance, source=f"{case.aoi_source} (AOI polygon)"
        )
        mapped = gpd.GeoDataFrame(
            pd.concat([mapped, mapped_aoi], ignore_index=True), geometry="geometry", crs=C.CRS_OUTPUT
        )
    mapped.attrs.update(
        {
            "source_crs": source_crs,
            "reprojected": str(source_crs).upper() != C.CRS_OUTPUT,
        }
    )

    collection = build_feature_collection(mapped)
    problems = C.validate_context_layers(collection)

    land_use = join_land_use(grid, mapped)
    risk_problems = (
        derive_risk_level(grid)
        if {"velocity_mm_yr", "risk_level"}.issubset(grid.columns)
        else None
    )

    # `pipeline/validate_artifacts.py` admits only the four frozen files in a case directory. The
    # land_use sidecar is a staging file by construction, so say so when we drop it next to them.
    sidecar_in_case_dir = (
        land_use_path.parent.resolve() == grid_path.parent.resolve()
        and grid_path.name == "nisar_grid.parquet"
    )
    sidecar_note = (
        "land_use.csv is a staging sidecar written beside the four frozen artefacts; "
        "pipeline/validate_artifacts.py only admits those four, so fold it into nisar_grid.parquet "
        "and delete it (or pass --land-use-out outside the case directory) before running the gate."
        if sidecar_in_case_dir
        else ""
    )

    summary: dict[str, Any] = {
        "case_id": case.case_id,
        "context_file": str(context_path),
        "source_crs": source_crs,
        "reprojected": str(source_crs).upper() != C.CRS_OUTPUT,
        "aoi_bbox": list(case.bbox),
        "features_in": int(len(raw)),
        "features_in_aoi": int(len(in_aoi)),
        "dropped_no_geometry": dropped_no_geometry,
        "aoi_feature_added": bool(args.aoi_feature),
        "features_emitted": len(collection["features"]),
        "features_mapped_by_class": _class_counts(
            f"{f['properties']['layer']}/{f['properties']['class']}" for f in collection["features"]
        ),
        "cells_total": int(len(grid)),
        "cells_matched": int((land_use["land_use"] != "unknown").sum()),
        "cells_by_land_use": _class_counts(land_use["land_use"]),
        "cells_unknown": int((land_use["land_use"] == "unknown").sum()),
        "grid_file": str(grid_path),
        "grid_file_modified": False,
        "land_use_join": "join land_use.csv onto nisar_grid.parquet on cell_id (this CLI does not rewrite the parquet)",
        "sidecar_note": sidecar_note,
        "risk_level_check": (
            "skipped: grid lacks velocity_mm_yr/risk_level"
            if risk_problems is None
            else {
                "disagreements": int(len(risk_problems)),
                "cells": risk_problems["cell_id"].tolist()[:20],
            }
        ),
        "validation_problems": problems,
        "written": {},
    }

    if problems:
        # Fail closed: an invalid artefact must not replace a valid one on disk.
        summary["written"] = {}
        _emit(args, summary)
        print(
            f"error: generated context does not satisfy contracts.validate_context_layers "
            f"({len(problems)} problem(s)); nothing written",
            file=sys.stderr,
        )
        for problem in problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    summary["written"]["context_layers_geojson"] = {
        "path": str(out_path),
        "bytes": write_context_geojson(collection, out_path),
    }
    summary["written"]["land_use_csv"] = {
        "path": str(land_use_path),
        "bytes": write_land_use_csv(land_use, land_use_path),
    }
    _emit(args, summary)
    return 0


def _emit(args: argparse.Namespace, summary: Mapping[str, Any]) -> None:
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False))
        return
    problems = summary.get("validation_problems") or []
    print(f"case            : {summary['case_id']}")
    print(
        f"context source  : {summary['context_file']} "
        f"({summary['features_in']} features, {summary['source_crs']}"
        f"{', reprojected to EPSG:4326' if summary['reprojected'] else ''})"
    )
    print(f"features in AOI : {summary['features_in_aoi']} → emitted {summary['features_emitted']}")
    for key, count in summary["features_mapped_by_class"].items():
        print(f"  {key:<32} {count}")
    print(f"cells           : {summary['cells_total']} total, {summary['cells_matched']} matched, "
          f"{summary['cells_unknown']} unknown")
    for key, count in summary["cells_by_land_use"].items():
        print(f"  {key:<24} {count}")
    risk = summary["risk_level_check"]
    if isinstance(risk, Mapping):
        print(f"risk_level      : {risk['disagreements']} cell(s) disagree with contracts.risk_level_from_velocity")
    else:
        print(f"risk_level      : {risk}")
    print(f"validation      : {'PASS' if not problems else f'FAIL ({len(problems)})'}")
    if summary["written"]:
        for name, info in summary["written"].items():
            print(f"wrote {name:<22} {info['path']} ({info['bytes']} bytes)")
        print("nisar_grid.parquet was NOT rewritten: land_use.csv is a sidecar, join it on cell_id.")
        if summary.get("sidecar_note"):
            print(f"note            : {summary['sidecar_note']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/enrich_context.py",
        description=(
            "Map an external GIS source to the frozen context_layers.geojson vocabulary and derive a "
            "per-cell land_use sidecar. Offline: the caller passes a local file."
        ),
        epilog="Run with --print-mapping to see the tag→class table and the overlap precedence.",
    )
    parser.add_argument("--case", help="case id from config/study_cases.yaml (required unless --print-mapping)")
    parser.add_argument("--context", help="local source file .geojson/.gpkg/.shp/.parquet (required unless --print-mapping)")
    parser.add_argument("--grid", help="nisar_grid.parquet (default data/processed/<case>/nisar_grid.parquet)")
    parser.add_argument("--out", help="output context_layers.geojson (default data/processed/<case>/)")
    parser.add_argument("--land-use-out", help="output land_use.csv (default data/processed/<case>/)")
    parser.add_argument("--provenance-id", help="override; default: the grid's single provenance_id")
    parser.add_argument("--source", help="provider label for features without a source column")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="study cases YAML (default config/study_cases.yaml)")
    parser.add_argument("--json", action="store_true", help="print the machine-readable summary")
    parser.add_argument("--no-aoi", dest="aoi_feature", action="store_false", help="do not append the config AOI polygon")
    parser.add_argument("--no-clip", action="store_true", help="select features intersecting the AOI without clipping them")
    parser.add_argument("--print-mapping", action="store_true", help="print the tag→class table and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.print_mapping:
        _print_mapping()
        return 0
    if not args.case or not args.context:
        parser.error("--case and --context are required (or run --print-mapping)")
    defects = mapping_problems()
    if defects:
        print("error: the mapping table is not inside the frozen vocabulary:", file=sys.stderr)
        for defect in defects:
            print(f"  - {defect}", file=sys.stderr)
        return 2
    try:
        return _run(args)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
