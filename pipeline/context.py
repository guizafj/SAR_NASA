"""Case geography: real OpenStreetMap geometry, with a declared synthetic fallback.

What changed and why
--------------------
This module used to *invent* every line it published: an ellipse "lake" for Mexico City, a
5-vertex zigzag "coastline" for Jakarta and Málaga, a 4-vertex "river" through each AOI, and a
straight 14-18 km "industrial corridor" at a fixed azimuth. The land-use rings around the coverage
ellipse (`rho < 0.38` dense, `rho < 0.80` residential, a hash-noised `bare` fringe) were the result
of that geometry, not of any city. A judge is entitled to disbelieve a map whose coastline is a
zigzag, and the measured cost was large: against a real Malaga extract, the published assignment
agreed with the OSM labels for only a small minority of the cells inside a test box, and the
invented corridor called most of those cells `industrial` where the extract maps only a handful. The
measurement protocol is in `docs/context-sources.md`; the point here is that the agreement was poor
enough that the rings, not the city, were being described.

So the geography is now derived from a real extract (`pipeline/ingest/osm_context.py`) and mapped
onto the frozen vocabulary by the one tag table (`pipeline/enrich_context.py`). Three things stay
declared inventions, each because it is a property of the *model* and not of the city:

* the displacement field itself (`velocity_field` in `model.py`) — the mock has no SAR data;
* the coherence field — modelled, then filtered by the frozen threshold;
* the coverage ellipse — which cells exist at all. It is not published as an AOI any more: the
  `admin_boundary / aoi` feature is the real OSM city limit from `config/aoi/<case>.geojson`.

`land_use` is no longer split by an invented density rule. Measured on two real cities, the built-up
fraction per H3-r9 cell is a continuum with no privileged threshold (the numbers are in
`docs/context-sources.md` §4), so a "dense vs residential" cut would be an artefact of the constant,
not of the ground. The class now comes from OSM tag *semantics*: `landuse=residential` is residential fabric,
`landuse=commercial|retail`, a campus or a public square is the dense/service fabric, and a cell
whose centroid no mapped polygon contains is `unknown` — published, never extrapolated.

The synthetic fallback exists so a clone without the (gitignored) raw extract still runs. It is
opt-out-able, it is flagged on the returned context (`synthetic=True`), and the artefacts it
produces say `mock (synthetic land-cover rule)` in every `source` string.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from pipeline import enrich_context as EC
from pipeline.ingest import osm_context as OSM
from src.domain import contracts as C

KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQ = 111.320

SYNTHETIC_LAND_USE_SOURCE = "mock (synthetic land-cover rule)"
SYNTHETIC_WATER_SOURCE = "mock (synthetic hydrology)"
SYNTHETIC_INFRASTRUCTURE_SOURCE = "mock (synthetic utility POI)"
SYNTHETIC_BOUNDARY_SOURCE = "mock (synthetic AOI)"


def km_per_deg_lon(lat: float) -> float:
    return KM_PER_DEG_LON_EQ * math.cos(math.radians(lat))


def lonlat_to_local_km(
    lon: np.ndarray, lat: np.ndarray, lon0: float, lat0: float
) -> tuple[np.ndarray, np.ndarray]:
    """Equirectangular projection around (lon0, lat0). Adequate: AOIs are < 60 km wide."""
    return (np.asarray(lon, float) - lon0) * km_per_deg_lon(lat0), (
        np.asarray(lat, float) - lat0
    ) * KM_PER_DEG_LAT


def local_km_to_lonlat(x: np.ndarray, y: np.ndarray, lon0: float, lat0: float) -> tuple[np.ndarray, np.ndarray]:
    return lon0 + np.asarray(x, float) / km_per_deg_lon(lat0), lat0 + np.asarray(y, float) / KM_PER_DEG_LAT


def ellipse_rho(
    lon: np.ndarray, lat: np.ndarray, center_lon: float, center_lat: float, a_km: float, b_km: float, rotation_deg: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalised elliptical radius and the rotated frame (u along the a-axis, v along b).

    `rho <= 1` == centroid inside the coverage ellipse. The ellipse now decides only *which cells
    exist*; it no longer decides what they are.
    """
    x, y = lonlat_to_local_km(lon, lat, center_lon, center_lat)
    theta = math.radians(rotation_deg)
    u = x * math.cos(theta) + y * math.sin(theta)
    v = -x * math.sin(theta) + y * math.cos(theta)
    return u, v, np.sqrt((u / a_km) ** 2 + (v / b_km) ** 2)


def ellipse_ring(
    center_lon: float, center_lat: float, a_km: float, b_km: float, rotation_deg: float, n: int = 72
) -> list[list[float]]:
    """Closed lon/lat ring for an ellipse given in km (EPSG:4326 output)."""
    t = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    u, v = a_km * np.cos(t), b_km * np.sin(t)
    theta = math.radians(rotation_deg)
    x = u * math.cos(theta) - v * math.sin(theta)
    y = u * math.sin(theta) + v * math.cos(theta)
    lon, lat = local_km_to_lonlat(x, y, center_lon, center_lat)
    ring = [[round(float(lo), 6), round(float(la), 6)] for lo, la in zip(lon, lat, strict=True)]
    ring.append(ring[0])
    return ring


def polyline_metrics(
    lon: np.ndarray, lat: np.ndarray, verts: tuple[tuple[float, float], ...] | list[tuple[float, float]]
) -> tuple[np.ndarray, np.ndarray]:
    """Distance (km) from points to a polyline and the side sign of the nearest segment.

    Side sign is `sign(cross(b-a, p-a))` in the local metric frame: +1 left, -1 right.
    """
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    lon0 = float(np.mean([v[0] for v in verts]))
    lat0 = float(np.mean([v[1] for v in verts]))
    px, py = lonlat_to_local_km(lon, lat, lon0, lat0)
    pts = np.array([lonlat_to_local_km(np.array([v[0]]), np.array([v[1]]), lon0, lat0) for v in verts])
    ax, ay = pts[:, 0], pts[:, 1]
    best = np.full(px.shape, np.inf)
    side = np.zeros(px.shape)
    for i in range(len(verts) - 1):
        dx, dy = ax[i + 1] - ax[i], ay[i + 1] - ay[i]
        length2 = dx * dx + dy * dy
        if length2 == 0.0:
            continue
        t = np.clip(((px - ax[i]) * dx + (py - ay[i]) * dy) / length2, 0.0, 1.0)
        d = np.hypot(px - (ax[i] + t * dx), py - (ay[i] + t * dy))
        closer = d < best
        best = np.where(closer, d, best)
        cross = dx * (py - ay[i]) - dy * (px - ax[i])
        side = np.where(closer, np.sign(cross), side)
    return best, side


@dataclass(frozen=True)
class Coast:
    """One or more coastline polylines, with the water on the left (+1) or right (-1).

    A real coastline enters an AOI as several lines (Málaga's harbour, the Guadalhorce mouth,
    Jakarta's bay), so this holds a tuple of polylines and takes the nearest one per point. The
    synthetic version held a single 5-vertex zigzag.
    """

    lines: tuple[tuple[tuple[float, float], ...], ...]
    sea_side: int  # +1 = left/north of a w->e line, -1 = right/south

    @property
    def verts(self) -> tuple[tuple[float, float], ...]:
        """The longest line, for callers that can only draw one (kept for the published feature)."""
        return max(self.lines, key=len) if self.lines else ()

    def distance(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Unsigned distance in km to the nearest coastline vertex path.

        This is what the coherence model needs (a coastal decorrelation gradient). The signed
        variant is gone on purpose: determining which side of a shoreline is sea from a single sign
        cannot describe a bay, and a wrong sign labelled Jakarta's land as water. Sea membership is
        decided geometrically by `sea_polygons` instead.
        """
        best = np.full(np.shape(np.asarray(lon, float)), np.inf)
        for verts in self.lines:
            if len(verts) < 2:
                continue
            dist, _side = polyline_metrics(lon, lat, verts)
            best = np.minimum(best, dist)
        return best


@dataclass(frozen=True)
class CaseContext:
    """Everything the model needs to know about the ground of one case.

    `features` is the mapped extract (columns §6 + geometry), already in EPSG:4326 and restricted to
    the frozen layers/classes. `synthetic` marks the declared fallback: when it is true, every
    published `source` says so and the numbers are not evidence about the city.
    """

    case_id: str
    features: gpd.GeoDataFrame
    coast: Coast | None
    synthetic: bool
    source: str
    retrieved_utc: str | None = None
    osm_timestamp: str | None = None
    counters: dict[str, int] = field(default_factory=dict)
    #: `{cell_id: class}` when the labels come from the pinned context file. `None` means "join the
    #: centroids against `features`" — the path a freshly built extract takes.
    labels: Mapping[str, str] | None = None
    #: The open sea inside the AOI, DERIVED from the coastline and the AOI bbox (see `sea_polygons`).
    #: A declared per-case side (`CASE_SEA_SIDE`) was wrong: a bay's shoreline runs in both
    #: directions, so one global sign labelled Jakarta's land as water.
    sea: BaseGeometry | None = None
    #: True when the geometry came from the pinned context, which stores it already simplified and
    #: rounded. The artefact writer must not simplify it a second time: that made the pinned path
    #: and the fresh-extract path produce different bytes for the same seed.
    pinned: bool = False

    @property
    def land_use_source(self) -> str:
        return SYNTHETIC_LAND_USE_SOURCE if self.synthetic else self.source

    @property
    def water_source(self) -> str:
        return SYNTHETIC_WATER_SOURCE if self.synthetic else self.source

    @property
    def infrastructure_source(self) -> str:
        return SYNTHETIC_INFRASTRUCTURE_SOURCE if self.synthetic else self.source

    def features_of(self, layer: str) -> gpd.GeoDataFrame:
        if self.features.empty:
            return self.features
        return self.features[self.features["layer"] == layer]


# ---------------------------------------------------------------------------------------------
# Pinned context (config/context/<case>.json): the committed, deterministic half
# ---------------------------------------------------------------------------------------------

#: Where the pinned context of each case lives. Committed on purpose: roughly 0.8-1.5 MB per case
#: buys byte-identical regeneration on a machine that never fetched the (gitignored) extract, which
#: is cheaper than the 100-300 MB extract it replaces.
PIN_DIR = Path(__file__).resolve().parents[1] / "config" / "context"

#: Tolerance and precision the pin and the artefact share, so both describe the same geometry.
PIN_SIMPLIFY_DEG = 2e-4
PIN_PRECISION_DEG = 1e-5
#: Caps on how many derived features a case may carry, applied **per geometry kind**. A city extract
#: holds thousands of ponds and substations, so the largest are kept — but ranking every feature by
#: area silently dropped every LINE, which is how the published coastline disappeared from the water
#: layer when the context moved to the pin (a coastline polygon does not exist: the sea is derived
#: from the line). Water polygons are the bulk of the artefact, hence the smaller polygon cap.
FEATURE_CAP_PER_KIND = 150
ADMIN_FEATURE_CAP = 50
#: H3 resolution of a pinned cell label. It mirrors `pin_context.CELL_LABEL_RESOLUTION`: the label
#: answers a question the grid asks about one 200 m cell, so an id at another resolution — or one
#: that is not an H3 index at all — names a cell that can never be looked up.
PIN_CELL_RESOLUTION = 9


def pin_path(case_id: str, directory: Path | None = None) -> Path:
    return (directory or PIN_DIR) / f"{case_id}.json"


def validate_cell_land_use(
    labels: Mapping, *, case_id: str | None = None, path: Path | None = None
) -> None:
    """Reject a label table whose keys are not res-9 H3 cells or whose values leave `LAND_USE`.

    A pinned label is published verbatim as `land_use` in `nisar_grid.parquet`, so an id nobody can
    look up and a class the contract never declared are both silent: the file parses, the schema is
    intact, and the artefact carries a value (or a cell) that does not exist. Cheap by construction:
    one pass over the table, no geometry and no H3 library objects beyond the validity test.
    """
    import h3  # noqa: PLC0415 - the pin is optional geography, so is its H3 dependency

    where = str(path) if path is not None else f"the pin of {case_id!r}"
    allowed = frozenset(C.LAND_USE)
    total = len(labels)
    for key, value in labels.items():
        cell = str(key)
        if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != PIN_CELL_RESOLUTION:
            raise ValueError(
                f"{where}: cell_land_use key {cell!r} is not a resolution-{PIN_CELL_RESOLUTION} H3 "
                f"index ({total} labels read): a pinned label must name the cell the grid asks about"
            )
        if str(value) not in allowed:
            raise ValueError(
                f"{where}: cell_land_use[{cell!r}] = {value!r} is not a declared land_use class "
                f"({total} labels read; declared: {', '.join(C.LAND_USE)})"
            )


def read_pin(case_id: str, directory: Path | None = None) -> dict | None:
    """Read the pinned context, verifying the required digest and the case it claims to be.

    A pin that fails its own `pin_sha256` is corrupted or hand-edited, and reading it anyway would
    silently publish labels nobody derived. It raises instead: the artefact gate is not the place to
    discover that a committed file was edited by hand. The digest is therefore **required**: a pin
    that simply dropped the field is not a pin whose body was verified, it is one whose body was
    never checked. The re-hash removes the field and canonicalises exactly as the writer
    (`pipeline/ingest/pin_context.py::write_pin`) does, so the committed pins still verify. The
    `cell_land_use` vocabulary is checked one step later, in `pinned_context`.

    The body must also name the case it was asked for: a pin filed under the wrong slug would stamp
    one city's geography with another city's `provenance_id`.
    """
    path = pin_path(case_id, directory)
    if not path.is_file():
        return None
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{path} is not readable JSON: {exc}") from exc
    recorded = payload.pop("pin_sha256", None)
    if not isinstance(recorded, str) or not recorded.strip():
        raise ValueError(
            f"{path} carries no pin_sha256 ({recorded!r}): the digest is missing, so nothing was "
            "verified — a pinned context must be produced by "
            "`python -m pipeline.ingest.pin_context`, never edited by hand"
        )
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if actual != recorded:
        raise ValueError(
            f"{path} fails its own pin_sha256 ({actual[:12]} != {recorded[:12]}): a pinned "
            "context must be produced by `python -m pipeline.ingest.pin_context`, never edited "
            "by hand"
        )
    body_case = payload.get("case_id")
    if body_case != case_id:
        raise ValueError(
            f"{path} pins case_id {body_case!r} but was read as {case_id!r}: a pin may only answer "
            "for the case it was derived from"
        )
    return payload


def pin_features(payload: Mapping, provenance: str) -> gpd.GeoDataFrame:
    """Pinned water/infrastructure/admin features -> the `CaseContext.features` frame."""
    from shapely.geometry import shape  # noqa: PLC0415

    rows: list[dict] = []
    geometries: list[BaseGeometry] = []
    for layer, key in (("water", "water"), ("infrastructure", "infrastructure"), ("admin_boundary", "admin")):
        for feature in payload.get(key) or ():
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            if not geometry:
                continue
            rows.append(
                {
                    "layer": layer,
                    "class": str(properties.get("class") or "unknown"),
                    "name": properties.get("name"),
                    "case_id": payload.get("case_id"),
                    "crs": C.CRS_OUTPUT,
                    "source": str(properties.get("source") or payload.get("source") or OSM.SOURCE),
                    "provenance_id": provenance,
                }
            )
            geometries.append(shape(geometry))
    return gpd.GeoDataFrame(rows, geometry=geometries, crs=C.CRS_OUTPUT)


def pinned_context(case_id: str, provenance: str, payload: Mapping) -> CaseContext:
    """`CaseContext` from the committed pin: labels come from the file, geometry from its features.

    `cell_land_use` is checked here rather than in `read_pin`: this is the point where the table
    stops being JSON and becomes the published `land_use` column, so an undeclared class or an id
    that is not a res-9 cell is refused before it can reach an artefact.
    """
    labels = {str(k): str(v) for k, v in (payload.get("cell_land_use") or {}).items()}
    validate_cell_land_use(labels, case_id=case_id)
    features = pin_features(payload, provenance)
    record = payload.get("record") or {}
    return CaseContext(
        case_id=case_id,
        features=features,
        coast=coast_from_features(features, case_id),
        synthetic=False,
        source=str(payload.get("source") or OSM.SOURCE),
        retrieved_utc=record.get("retrieved_utc"),
        osm_timestamp=record.get("timestamp_osm_base"),
        counters={
            "pinned_cells": len(payload.get("cell_land_use") or {}),
            "coastline_lines": int(payload.get("coastline_lines") or 0),
        },
        labels=labels,
        sea=_pinned_sea(payload),
        pinned=True,
    )


def geometry_kind(geometry) -> str:
    """`polygon` / `line` / `point`, from the geometry type (multipart types included)."""
    kind = getattr(geometry, "geom_type", "")
    if not kind:
        return "point"
    if "Polygon" in kind:
        return "polygon"
    if "LineString" in kind or "LinearRing" in kind:
        return "line"
    return "point"


def select_for_publication(rows: gpd.GeoDataFrame, cap: int = FEATURE_CAP_PER_KIND) -> gpd.GeoDataFrame:
    """The features that get published: the largest `cap` of EACH geometry kind.

    Deterministic on ties (class, then name, then the geometry's own WKB order) so a rerun cannot
    reorder the artefact. Area and length are measured in Web Mercator: within one AOI the
    distortion is monotone, and ranking in degrees would put degrees where metres belong.
    """
    if rows.empty:
        return rows
    work = rows.copy()
    work["_kind"] = work.geometry.map(geometry_kind)
    projected = work.to_crs("EPSG:3857") if getattr(work, "crs", None) else work
    work["_size"] = np.where(
        work["_kind"].to_numpy() == "polygon",
        projected.area.fillna(0.0).to_numpy(),
        projected.length.fillna(0.0).to_numpy(),
    )
    work["_class"] = work["class"].astype(str)
    work["_name"] = work["name"].fillna("").astype(str) if "name" in work.columns else ""
    work["_wkb"] = work.geometry.map(
        lambda geometry: geometry.wkb_hex if geometry is not None and not geometry.is_empty else ""
    )
    kept = []
    for kind in ("polygon", "line", "point"):
        part = work[work["_kind"] == kind].sort_values(
            ["_size", "_class", "_name", "_wkb"], ascending=[False, True, True, True], kind="mergesort"
        )
        kept.append(part.head(cap))
    out = pd.concat(kept) if kept else rows.iloc[0:0]
    return out if not out.empty else rows.iloc[0:0]


def bbox_cells(bbox: tuple[float, float, float, float], resolution: int) -> list[str]:
    """Every H3 cell of `resolution` whose centroid falls inside the bbox quad."""
    import h3  # noqa: PLC0415

    west, south, east, north = bbox
    ring = h3.LatLngPoly([(south, west), (south, east), (north, east), (north, west)])
    return sorted(h3.polygon_to_cells(ring, resolution))


def prefilter_cells(
    bbox: tuple[float, float, float, float],
    center_lon: float,
    center_lat: float,
    a_km: float,
    b_km: float,
    rotation_deg: float,
    resolution: int,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """The cells the model can ever publish: bbox AND coverage ellipse.

    This is the nodata rule, and it is also the set the context pin labels. It lives here rather than
    in `pipeline/mock/model.py` because the pin must cover the *same* cells the generator will ask
    about: pinning only the AOI ring silently left the cells between the ring and the ellipse
    unlabelled, so they published as `unknown` even where a mapped polygon contains them.
    """
    import h3  # noqa: PLC0415

    hexes = bbox_cells(bbox, resolution)
    lat = np.empty(len(hexes))
    lon = np.empty(len(hexes))
    for index, cell in enumerate(hexes):
        lat[index], lon[index] = h3.cell_to_latlng(cell)
    west, south, east, north = bbox
    _, _, rho = ellipse_rho(lon, lat, center_lon, center_lat, a_km, b_km, rotation_deg)
    keep = (rho <= 1.0) & (lat >= south) & (lat <= north) & (lon >= west) & (lon <= east)
    return [cell for cell, ok in zip(hexes, keep, strict=True) if ok], lat[keep], lon[keep]


def _pinned_sea(payload: Mapping):
    """The sea polygon stored in the pin, or `None` for a pin written before it existed."""
    geometry = payload.get("sea")
    if not isinstance(geometry, Mapping) or not geometry.get("type"):
        return None
    from shapely.geometry import shape  # noqa: PLC0415

    try:
        return shape(geometry)
    except Exception:  # noqa: BLE001 - a corrupt sea polygon must not abort the whole case
        return None


def label_cells(context: CaseContext, cell_id, lon, lat) -> np.ndarray:
    """One frozen land_use class per cell, from the pinned labels or a centroid join.

    Water is the one class that is *derived* rather than contained: OSM publishes the coastline as
    a line and a line contains no centroid, so cells on the sea side of the real coastline are
    labelled `water` from that geometry. Nothing else is extrapolated — a cell no polygon contains
    (and the pin does not name) is `unknown`, and `unknown` is published.
    """
    import pandas as pd  # noqa: PLC0415

    ids = np.asarray(cell_id, dtype=object)
    if context.labels is not None:
        land_use = np.array([context.labels.get(str(cell), "unknown") for cell in ids], dtype=object)
    else:
        grid = pd.DataFrame(
            {
                "cell_id": ids,
                "lat": np.asarray(lat, dtype=float),
                "lon": np.asarray(lon, dtype=float),
            }
        )
        land_use = EC.join_land_use(grid, context.features)["land_use"].to_numpy(dtype=object)
    if context.sea is not None:
        from shapely.geometry import Point  # noqa: PLC0415 - only this branch needs it

        sea_mask = np.array(
            [context.sea.contains(Point(float(lo), float(la))) for lo, la in zip(lon, lat, strict=True)]
        )
        land_use = np.where(sea_mask, "water", land_use)
    return land_use


# ---------------------------------------------------------------------------------------------
# Real geometry
# ---------------------------------------------------------------------------------------------


def _line_coords(geometry) -> list[tuple[tuple[float, float], ...]]:
    """Every polyline a geometry can contribute to a coastline, handled by explicit type.

    `Polygon.geoms` yields its RINGS in shapely 2, so iterating `.geoms` and then reading `.coords`
    crashed on Jakarta: a coastline way that closes on itself (an island outline) arrives as a
    Polygon, and a Polygon has no `.coords`. An island's exterior ring *is* its coastline, so it is
    taken as a line rather than skipped.
    """
    kind = getattr(geometry, "geom_type", "")
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    if kind == "LineString":
        return [tuple((float(x), float(y)) for x, y in geometry.coords)]
    if kind in ("MultiLineString", "GeometryCollection"):
        out: list[tuple[tuple[float, float], ...]] = []
        for part in geometry.geoms:
            out.extend(_line_coords(part))
        return out
    if kind == "Polygon":
        rings = [tuple((float(x), float(y)) for x, y in geometry.exterior.coords)]
        rings.extend(tuple((float(x), float(y)) for x, y in ring.coords) for ring in geometry.interiors)
        return rings
    if kind == "MultiPolygon":
        out = []
        for part in geometry.geoms:
            out.extend(_line_coords(part))
        return out
    return []


def coast_lines(features: gpd.GeoDataFrame) -> list[tuple[tuple[float, float], ...]]:
    """Every coastline polyline in the mapped extract (an island outline counts as one)."""
    if features.empty:
        return []
    rows = features[(features["layer"] == "water") & (features["class"] == "coastline")]
    lines: list[tuple[tuple[float, float], ...]] = []
    for geometry in rows.geometry:
        lines.extend(coords for coords in _line_coords(geometry) if len(coords) >= 2)
    return lines


def coast_from_features(features: gpd.GeoDataFrame, case_id: str | None = None) -> Coast | None:
    """The coastline as distance geometry. Kept for the coherence gradient, not for labelling.

    The sea/land side is NOT declared here any more: it is derived per AOI by `sea_polygons`, because
    a single per-case sign cannot describe a bay.
    """
    lines = coast_lines(features)
    if not lines:
        return None
    return Coast(lines=tuple(lines), sea_side=0)


def sea_polygons(
    features: gpd.GeoDataFrame,
    bbox: tuple[float, float, float, float] | None,
    *,
    land_use_share_max: float = 0.05,  # documented threshold, not a contract value: a candidate
    # closed area whose surface is at most 5 % covered by mapped land use is treated as sea. Higher
    # values absorb coastal parks; lower values leave port basins labelled `unknown`.
) -> BaseGeometry | None:
    """The open sea inside the AOI, derived from the coastline plus the bbox, not from a side flag.

    Method: polygonize the coastline together with the bbox ring. That yields closed candidates on
    BOTH sides of the shoreline; the sea is the candidate whose area is (almost) not covered by
    mapped land-use polygons. A `coastline` way that closes on itself (an island) is handled by the
    same rule, and a bay needs no global direction — which is exactly what the declared-side version
    got wrong: with `sea_side=+1` Jakarta's land came out as water.

    Returns `None` when there is no coastline, no bbox, or no candidate that looks like water; the
    caller then labels from the mapped water polygons alone and the sea cells stay `unknown` rather
    than being guessed.
    """
    from shapely.geometry import LineString, box  # noqa: PLC0415
    from shapely.ops import polygonize, unary_union  # noqa: PLC0415

    lines = coast_lines(features)
    if not lines or not bbox:
        return None
    west, south, east, north = bbox
    frame = box(west, south, east, north).exterior
    candidates = [
        polygon
        for polygon in polygonize(unary_union([frame, *[LineString(coords) for coords in lines]]))
        if not polygon.is_empty and polygon.area > 0
    ]
    if not candidates:
        return None
    areal = features[
        (features["layer"] == "land_use") & features.geometry.geom_type.isin(("Polygon", "MultiPolygon"))
    ]
    land = areal.geometry.union_all() if not areal.empty else None
    sea: list[BaseGeometry] = []
    for candidate in candidates:
        if land is None:
            share = 0.0
        else:
            covered = candidate.intersection(land).area
            share = float(covered / candidate.area) if candidate.area else 1.0
        if share <= land_use_share_max:
            sea.append(candidate)
    return unary_union(sea) if sea else None


@lru_cache(maxsize=8)
def _load_mapped(case_id: str, provenance: str, root: Path | None) -> tuple[gpd.GeoDataFrame, dict]:
    """Read + assemble + map the cached extract once per process (assembly is the slow part)."""
    extract = OSM.load_extract(case_id, root)
    record = extract.attrs.get("record")
    source = str(extract.attrs.get("source") or OSM.SOURCE)
    mapped = EC.mmap_source_features(extract, case_id, provenance, source=source)
    counters = dict(extract.attrs.get("counters") or {})
    return mapped, {
        "counters": counters,
        "record": record,
        "source": source,
    }


def load_context(
    case_id: str,
    provenance: str,
    *,
    root: Path | None = None,
    allow_synthetic: bool = True,
    pin_directory: Path | None = None,
    use_pin: bool = True,
) -> CaseContext:
    """Pinned context if there is one, else a freshly mapped extract, else the declared fallback.

    Order matters for reproducibility: the pin is committed and identical on every machine, so it
    wins whenever it exists. `use_pin=False` forces the (slow) join against the raw extract, which
    is what regenerating the pin itself needs.

    `provenance` is the case's own `provenance_id` (the same one every artefact carries): the
    artefact gate requires one id across grid, series and context, so the context is stamped with
    it rather than with an id of its own. What identifies the *source* is the `source` property,
    which names OpenStreetMap and the ODbL.
    """
    if case_id not in C.CASE_IDS:
        raise ValueError(f"undeclared case_id {case_id!r}; the frozen cases are {list(C.CASE_IDS)}")
    if use_pin:
        payload = read_pin(case_id, pin_directory)
        if payload is not None:
            return pinned_context(case_id, provenance, payload)
    try:
        mapped, meta = _load_mapped(case_id, provenance, root)
    except FileNotFoundError:
        if not allow_synthetic:
            raise
        return synthetic_context(case_id, provenance)

    record = meta.get("record")
    bbox = getattr(record, "bbox", None)
    coast = coast_from_features(mapped, case_id)
    sea = sea_polygons(mapped, bbox)
    return CaseContext(
        case_id=case_id,
        features=mapped,
        coast=coast,
        synthetic=False,
        source=str(meta.get("source") or OSM.SOURCE),
        retrieved_utc=getattr(record, "retrieved_utc", None),
        osm_timestamp=getattr(record, "timestamp_osm_base", None),
        counters=dict(meta.get("counters") or {}),
        sea=sea,
    )


# ---------------------------------------------------------------------------------------------
# Declared fallback: the old invented geometry, kept only so a clone without the extract runs
# ---------------------------------------------------------------------------------------------

#: lake / water body: (center_lon, center_lat, a_km, b_km, rotation_deg)
Lake = tuple[float, float, float, float, float]
#: river or canal: (context class, name, verts)
Waterway = tuple[str, str, tuple[tuple[float, float], ...]]


@dataclass(frozen=True)
class SyntheticGeography:
    """The invented shapes this module used to publish unconditionally.

    Kept for the offline fallback only. Nothing here is a claim about the real city, which is why
    every feature built from it carries a `source` that says `mock (synthetic ...)`.
    """

    coast: Coast | None
    lake: Lake | None
    waterway: Waterway | None
    corridor: tuple[float, float, str]  # azimuth_deg clockwise from east, length_km, name
    points: tuple[tuple[str, str, float, float], ...] = ()
    lake_name: str | None = None


SYNTHETIC: dict[str, SyntheticGeography] = {
    "mexico-city": SyntheticGeography(
        coast=None,
        lake=(-99.015, 19.445, 3.4, 2.2, 20.0),
        lake_name="Lago de Texcoco (sintético)",
        waterway=(
            "river",
            "Río Churubusco (sintético)",
            ((-99.245, 19.352), (-99.160, 19.365), (-99.075, 19.376), (-98.995, 19.398)),
        ),
        corridor=(20.0, 18.0, "Corredor industrial Vallejo (sintético)"),
        points=(
            ("metro-station", "Estación Metro Zócalo (sintética)", -99.133, 19.433),
            ("water-well", "Pozo Roma (sintético)", -99.160, 19.420),
            ("water-treatment", "Planta de tratamiento Texcoco (sintética)", -99.010, 19.452),
        ),
    ),
    "jakarta": SyntheticGeography(
        coast=Coast(
            lines=(
                (
                    (106.700, -6.132),
                    (106.780, -6.140),
                    (106.860, -6.134),
                    (106.940, -6.142),
                    (107.000, -6.136),
                ),
            ),
            sea_side=+1,
        ),
        lake=None,
        waterway=(
            "canal",
            "Kanal Banjir Barat (sintético)",
            ((106.788, -6.140), (106.805, -6.210), (106.820, -6.280), (106.835, -6.360)),
        ),
        corridor=(100.0, 16.0, "Koridor industri Cakung (sintético)"),
        points=(
            ("metro-station", "Estación Metro Bundaran HI (sintética)", 106.823, -6.193),
            ("water-well", "Pozo Pluit (sintético)", 106.851, -6.155),
            ("water-treatment", "Planta de tratamiento Marunda (sintética)", 106.905, -6.220),
        ),
    ),
    "malaga": SyntheticGeography(
        coast=Coast(
            lines=(
                (
                    (-4.600, 36.688),
                    (-4.530, 36.681),
                    (-4.470, 36.690),
                    (-4.400, 36.678),
                    (-4.300, 36.684),
                ),
            ),
            sea_side=-1,
        ),
        lake=None,
        waterway=(
            "river",
            "Río Guadalhorce (sintético)",
            ((-4.480, 36.840), (-4.462, 36.760), (-4.446, 36.700), (-4.436, 36.684)),
        ),
        corridor=(260.0, 14.0, "Corredor industrial Guadalhorce (sintético)"),
        points=(
            ("metro-station", "Estación Metro El Perchel (sintética)", -4.430, 36.715),
            ("water-well", "Pozo Campanillas (sintético)", -4.470, 36.735),
            ("water-treatment", "Planta de tratamiento Guadalhorce (sintética)", -4.400, 36.700),
        ),
    ),
}


def synthetic_context(case_id: str, provenance: str) -> CaseContext:
    """A `CaseContext` built from the invented shapes, for a clone with no cached extract."""
    geo = SYNTHETIC[case_id]
    rows: list[dict] = []
    geometries: list[BaseGeometry] = []
    if geo.coast is not None:
        rows.append({"layer": "water", "class": "coastline", "name": "Línea de costa (sintética)"})
        geometries.append(_coast_geometry(geo.coast))
    if geo.lake is not None:
        lake_lon, lake_lat, lake_a, lake_b, lake_rot = geo.lake
        rows.append({"layer": "water", "class": "reservoir", "name": geo.lake_name})
        geometries.append(_lake_geometry(lake_lon, lake_lat, lake_a, lake_b, lake_rot))
    if geo.waterway is not None:
        klass, name, verts = geo.waterway
        rows.append({"layer": "water", "class": klass, "name": name})
        geometries.append(_waterway_geometry(verts))
    for klass, name, lon, lat in geo.points:
        rows.append({"layer": "infrastructure", "class": klass, "name": name})
        geometries.append(_point_geometry(lon, lat))
    features = gpd.GeoDataFrame(
        {
            "layer": [row["layer"] for row in rows],
            "class": [row["class"] for row in rows],
            "name": [row["name"] for row in rows],
            "case_id": case_id,
            "crs": C.CRS_OUTPUT,
            "source": [
                SYNTHETIC_WATER_SOURCE if row["layer"] == "water" else SYNTHETIC_INFRASTRUCTURE_SOURCE
                for row in rows
            ],
            "provenance_id": provenance,
        },
        geometry=geometries,
        crs=C.CRS_OUTPUT,
    )
    return CaseContext(
        case_id=case_id,
        features=features,
        coast=geo.coast,
        synthetic=True,
        source=SYNTHETIC_LAND_USE_SOURCE,
    )


def summarise_context(context: CaseContext) -> str:
    """One-line description of where a case's geography came from, for the run log."""
    if context.synthetic:
        return (
            "declared synthetic fallback: no pinned context and no readable extract "
            "(pipeline/context.py::SYNTHETIC)"
        )
    # Keyed on `pinned`, the same flag the artefact writer uses; keying on `labels` meant two flags
    # for one fact and could describe a run differently from how it was published.
    parts = [
        f"pinned context config/context/{context.case_id}.json"
        if context.pinned
        else "freshly mapped OpenStreetMap extract"
    ]
    if context.osm_timestamp:
        parts.append(f"OSM data {context.osm_timestamp}")
    n_water = len(context.features_of("water"))
    n_infra = len(context.features_of("infrastructure"))
    # These are the counts BEFORE the per-geometry-kind publication cap, which `pin_context` stores.
    parts.append(f"{n_water} water / {n_infra} infrastructure features carried (before the publish cap)")
    return "; ".join(parts)


def corridor_segment(
    geo: SyntheticGeography, center_lon: float, center_lat: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Corridor axis as a lon/lat segment starting at the coverage-ellipse centre (fallback only)."""
    azimuth_deg, length_km, _ = geo.corridor
    theta = math.radians(azimuth_deg)
    x, y = length_km * math.cos(theta), length_km * math.sin(theta)
    end_lon, end_lat = local_km_to_lonlat(np.array([x]), np.array([y]), center_lon, center_lat)
    return (center_lon, center_lat), (float(end_lon[0]), float(end_lat[0]))


def _coast_geometry(coast: Coast) -> BaseGeometry:
    from shapely.geometry import LineString, MultiLineString  # noqa: PLC0415 - optional import site

    lines = [LineString(verts) for verts in coast.lines if len(verts) >= 2]
    return lines[0] if len(lines) == 1 else MultiLineString(lines)


def _lake_geometry(lon: float, lat: float, a_km: float, b_km: float, rotation_deg: float) -> BaseGeometry:
    from shapely.geometry import Polygon  # noqa: PLC0415

    return Polygon(ellipse_ring(lon, lat, a_km, b_km, rotation_deg, n=48))


def _waterway_geometry(verts: tuple[tuple[float, float], ...]) -> BaseGeometry:
    from shapely.geometry import LineString  # noqa: PLC0415

    return LineString(verts)


def _point_geometry(lon: float, lat: float) -> BaseGeometry:
    from shapely.geometry import Point  # noqa: PLC0415

    return Point(lon, lat)
