#!/usr/bin/env python3
"""Validated, drop-in AOI loader.

The study cases in `config/study_cases.yaml` carry a rectangular (or simple polygonal) AOI. A GIS
teammate can replace that footprint for a case without editing the YAML:

    config/aoi/{case_id}.geojson

Resolution order (first hit wins, and nothing silently falls through a broken override):

    1. config/aoi/{case_id}.geojson    (EPSG:4326, one Polygon, no holes)
    2. entry["aoi_polygon"]            (the configured ring)
    3. entry["bbox"]                   (the rectangle, as a closed ring)

The loader is deliberately strict: a projected export, a MultiPolygon, a polygon with holes, an
unclosed ring, a degenerate/tiny polygon, or a footprint outside the configured case bbox is an
`AOIError` with a message that says what to fix. The same checks are available from the CLI:

    ./scripts/python.sh pipeline/aoi.py --case mexico-city
    ./scripts/python.sh pipeline/aoi.py --case mexico-city --check
    ./scripts/python.sh pipeline/aoi.py --all --json

Area is a rough equirectangular estimate (a local lon/lat plane scaled by cos(latitude)); it is for
sanity checks, not cadastral measurement. Cell counts are H3 resolution 9, matching
`processing.h3_resolution` in the study case config.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import h3
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "study_cases.yaml"
#: Drop-in directory. A file here always wins over the YAML ring.
AOI_DIR = ROOT / "config" / "aoi"

#: The validation resolution is the study config's H3 resolution (res 9 ≈ 200 m edge).
H3_RESOLUTION = 9
#: Anything below this is almost certainly a coordinate mistake, not an urban AOI.
MIN_H3_CELLS = 10
EARTH_RADIUS_KM = 6371.0088
EPS = 1e-12


class AOIError(ValueError):
    """One actionable validation failure. Subclasses ValueError so existing CLI handlers see it."""


@dataclass(frozen=True)
class AOI:
    """A validated, closed lon/lat ring plus the metadata every call site needs."""

    case_id: str
    ring: tuple[tuple[float, float], ...]  # (lon, lat), closed (first == last)
    bbox: tuple[float, float, float, float]  # [W, S, E, N], derived from the ring
    source: str  # config/aoi/<case>.geojson | config/study_cases.yaml:aoi_polygon | bbox
    area_km2: float  # rough equirectangular estimate
    h3_cells: int  # number of H3 res-9 cells the ring yields

    def open_ring(self) -> tuple[tuple[float, float], ...]:
        """The ring without the repeated closing point (what H3/Shapely want)."""
        if len(self.ring) >= 2 and self.ring[0] == self.ring[-1]:
            return self.ring[:-1]
        return self.ring


# ---------------------------------------------------------------------------------------------
# Geometry helpers (pure Python; shapely is optional by design)
# ---------------------------------------------------------------------------------------------


def _same_point(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= EPS and abs(a[1] - b[1]) <= EPS


def _orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    if abs(_orient(a, b, p)) > 1e-10:
        return False
    return (
        min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
        and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
    )


def _segments_intersect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    """True when two closed segments touch or cross (proper or collinear overlap)."""
    o1, o2 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    o3, o4 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    if ((o1 > EPS and o2 < -EPS) or (o1 < -EPS and o2 > EPS)) and (
        (o3 > EPS and o4 < -EPS) or (o3 < -EPS and o4 > EPS)
    ):
        return True
    if abs(o1) <= 1e-10 and _on_segment(p1, p2, p3):
        return True
    if abs(o2) <= 1e-10 and _on_segment(p1, p2, p4):
        return True
    if abs(o3) <= 1e-10 and _on_segment(p3, p4, p1):
        return True
    if abs(o4) <= 1e-10 and _on_segment(p3, p4, p2):
        return True
    return False


def _ring_self_intersects(open_ring: Sequence[tuple[float, float]]) -> bool:
    """A simple segment-crossing test: consecutive segments may share an endpoint, no others."""
    n = len(open_ring)
    if n < 3:
        return False
    for i in range(n):
        a, b = open_ring[i], open_ring[(i + 1) % n]
        for j in range(i + 1, n):
            if j == (i + 1) % n or i == (j + 1) % n:
                continue
            c, d = open_ring[j], open_ring[(j + 1) % n]
            if _segments_intersect(a, b, c, d):
                return True
    return False


def _point_in_ring(point: tuple[float, float], open_ring: Sequence[tuple[float, float]]) -> bool:
    """Ray casting, with boundary points reported as inside."""
    x, y = point
    inside = False
    n = len(open_ring)
    for i in range(n):
        x1, y1 = open_ring[i]
        x2, y2 = open_ring[(i + 1) % n]
        if _on_segment((x1, y1), (x2, y2), point):
            return True
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (x2 - x1) * (y - y1) / (y2 - y1)
            if x < crossing_x:
                inside = not inside
    return inside


def _ring_intersects_bbox(
    open_ring: Sequence[tuple[float, float]], bbox: tuple[float, float, float, float]
) -> bool:
    """True when the ring touches/overlaps the configured [W, S, E, N] rectangle."""
    west, south, east, north = bbox
    for lon, lat in open_ring:
        if west - EPS <= lon <= east + EPS and south - EPS <= lat <= north + EPS:
            return True
    corners = ((west, south), (east, south), (east, north), (west, north))
    if any(_point_in_ring(corner, open_ring) for corner in corners):
        return True
    bbox_edges = ((corners[0], corners[1]), (corners[1], corners[2]), (corners[2], corners[3]), (corners[3], corners[0]))
    n = len(open_ring)
    for i in range(n):
        a, b = open_ring[i], open_ring[(i + 1) % n]
        for c, d in bbox_edges:
            if _segments_intersect(a, b, c, d):
                return True
    return False


def _ring_area_km2(ring: Sequence[tuple[float, float]]) -> float:
    """Rough equirectangular area: a local lon/lat plane scaled by cos(mean latitude)."""
    if len(ring) < 3:
        return 0.0
    mean_lat = sum(lat for _, lat in ring) / len(ring)
    mean_lon = sum(lon for lon, _ in ring) / len(ring)
    cos_lat = math.cos(math.radians(mean_lat))
    xy = [
        (
            math.radians(lon - mean_lon) * cos_lat * EARTH_RADIUS_KM,
            math.radians(lat - mean_lat) * EARTH_RADIUS_KM,
        )
        for lon, lat in ring
    ]
    twice_area = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        twice_area += x1 * y2 - x2 * y1
    return abs(twice_area) * 0.5


def _derive_bbox(ring: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    lons = [lon for lon, _ in ring]
    lats = [lat for _, lat in ring]
    return (min(lons), min(lats), max(lons), max(lats))


def _h3_cell_count(
    open_ring: Sequence[tuple[float, float]], resolution: int, source: str
) -> int:
    try:
        polygon = h3.LatLngPoly([(lat, lon) for lon, lat in open_ring])
        return len(h3.polygon_to_cells(polygon, resolution))
    except Exception as exc:  # noqa: BLE001 - convert H3's error into one actionable AOIError
        raise AOIError(
            f"{source}: H3 could not build cells from this polygon ({exc}); check that the ring is "
            "a simple, non-self-intersecting polygon in EPSG:4326"
        ) from exc


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------


def _coerce_bbox(
    bbox: Sequence[float] | None, source: str
) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise AOIError(f"{source}: configured bbox must be [W, S, E, N], got {bbox!r}") from exc
    if not (west < east and south < north):
        raise AOIError(
            f"{source}: configured bbox must be [W, S, E, N] ordered (W<E, S<N), got "
            f"{[west, south, east, north]!r}"
        )
    return (west, south, east, north)


def _check_declared_crs(candidate: Mapping[str, Any], source: str) -> None:
    """Reject a GeoJSON that explicitly declares a non-WGS84 CRS."""
    declared = candidate.get("crs")
    if not declared:
        return
    name: Any = declared
    if isinstance(declared, Mapping):
        properties = declared.get("properties")
        if isinstance(properties, Mapping):
            name = properties.get("name") or properties.get("code") or properties.get("href") or ""
        else:
            name = declared.get("name") or declared.get("code") or ""
    text = str(name).upper()
    if text and not any(token in text for token in ("4326", "CRS84", "WGS84", "WGS 84")):
        raise AOIError(
            f"{source}: GeoJSON declares CRS {name!r}, which is not EPSG:4326; export the AOI in "
            "EPSG:4326 (WGS 84 lon/lat)"
        )


def _ring_from_geojson(candidate: Mapping[str, Any], source: str) -> Any:
    _check_declared_crs(candidate, source)
    kind = candidate.get("type")
    if kind == "FeatureCollection":
        features = candidate.get("features")
        if not isinstance(features, list) or len(features) != 1:
            count = len(features) if isinstance(features, list) else "no"
            raise AOIError(
                f"{source}: expected a FeatureCollection with exactly one Polygon feature, got "
                f"{count} feature(s); export one AOI polygon, not a whole layer"
            )
        return _ring_from_geojson(features[0], source)
    if kind == "Feature":
        geometry = candidate.get("geometry")
        if not isinstance(geometry, Mapping):
            raise AOIError(
                f"{source}: Feature has no geometry; export a single Polygon in EPSG:4326 "
                "(not a Point, LineString or table row)"
            )
        return _ring_from_geojson(geometry, source)
    if kind == "MultiPolygon":
        raise AOIError(
            f"{source}: MultiPolygon is unsupported — flatten it to a single Polygon and export "
            "again (or tell the pipeline owner)"
        )
    if kind != "Polygon":
        raise AOIError(
            f"{source}: expected a GeoJSON FeatureCollection, Feature or Polygon, got {kind!r}; "
            "export the AOI as one Polygon in EPSG:4326"
        )
    coordinates = candidate.get("coordinates")
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        raise AOIError(
            f"{source}: Polygon has no coordinates; re-export the AOI from QGIS in EPSG:4326"
        )
    if len(coordinates) > 1:
        raise AOIError(
            f"{source}: polygon has {len(coordinates) - 1} interior ring(s) (holes) — holes are "
            "unsupported; flatten the AOI to one ring without holes (or tell the pipeline owner)"
        )
    return coordinates[0]


def _coerce_ring(candidate: Any, source: str) -> list[tuple[float, float]]:
    if not isinstance(candidate, (list, tuple)):
        raise AOIError(
            f"{source}: expected a GeoJSON FeatureCollection, Feature or Polygon, or a list of "
            f"[lon, lat] pairs, got {type(candidate).__name__}"
        )
    points: list[tuple[float, float]] = []
    for index, point in enumerate(candidate):
        try:
            lon, lat = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError, KeyError) as exc:
            raise AOIError(
                f"{source}: point {index} is not a [lon, lat] pair ({point!r}); export the AOI in "
                "EPSG:4326"
            ) from exc
        points.append((lon, lat))
    return points


def validate_aoi(
    aoi: Any,
    bbox: Sequence[float] | None = None,
    *,
    source: str = "AOI",
    resolution: int = H3_RESOLUTION,
) -> tuple[tuple[float, float], ...]:
    """Validate a GeoJSON object or ring and return it as a closed lon/lat ring.

    Every failure is one `AOIError` with an action. `bbox`, when supplied, is the configured case
    bbox that the ring must intersect; the returned ring is closed (first point repeated last).
    """
    raw_ring = _ring_from_geojson(aoi, source) if isinstance(aoi, Mapping) else aoi
    points = _coerce_ring(raw_ring, source)

    # A projected export is the most common QGIS mistake; check it before anything else.
    for lon, lat in points:
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise AOIError(f"{source}: ring contains a non-finite coordinate; export the AOI in EPSG:4326")
        if abs(lon) > 180 or abs(lat) > 90:
            raise AOIError(
                f"{source}: this AOI looks projected (lon={lon:g}, lat={lat:g}); |lon| > 180 or "
                "|lat| > 90 is a projection mistake — export the AOI in EPSG:4326 (WGS 84 lon/lat)"
            )

    if len(points) >= 2 and not _same_point(points[0], points[-1]):
        raise AOIError(
            f"{source}: ring is not closed — first point {list(points[0])} != last point "
            f"{list(points[-1])}; close the polygon in QGIS or repeat the first point at the end"
        )
    if len(points) < 4:
        raise AOIError(
            f"{source}: ring has {len(points)} points; a closed polygon needs at least 4 "
            "(3 distinct vertices + the closing point). Re-export the AOI."
        )

    ring = tuple(points)
    open_ring = ring[:-1]
    if _ring_self_intersects(open_ring):
        raise AOIError(
            f"{source}: ring self-intersects (QGIS 'invalid geometry'); fix the overlap in QGIS or "
            "run Fix Geometries before exporting in EPSG:4326"
        )

    area_km2 = _ring_area_km2(ring)
    if area_km2 <= 0.0:
        raise AOIError(
            f"{source}: ring has zero area (all points are collinear?); check the AOI coordinates "
            "and export a real polygon in EPSG:4326"
        )

    cells = _h3_cell_count(open_ring, resolution, source)
    if cells < MIN_H3_CELLS:
        raise AOIError(
            f"{source}: AOI covers {area_km2:.6g} km² and yields only {cells} H3 res-{resolution} "
            f"cell(s); a polygon this small is a mistake, not a city (need >= {MIN_H3_CELLS} cells). "
            "Check the coordinates/CRS."
        )

    configured_bbox = _coerce_bbox(bbox, source)
    if configured_bbox is not None and not _ring_intersects_bbox(open_ring, configured_bbox):
        raise AOIError(
            f"{source}: ring does not intersect the configured bbox {list(configured_bbox)}; check "
            "that the AOI and the case bbox are in the same area/CRS (this footprint is outside the "
            "study case)"
        )

    return ring


# ---------------------------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------------------------


def _make_aoi(case_id: str, ring: tuple[tuple[float, float], ...], source: str) -> AOI:
    open_ring = ring[:-1] if len(ring) >= 2 and _same_point(ring[0], ring[-1]) else ring
    return AOI(
        case_id=case_id,
        ring=ring,
        bbox=_derive_bbox(ring),
        source=source,
        area_km2=_ring_area_km2(ring),
        h3_cells=_h3_cell_count(open_ring, H3_RESOLUTION, source),
    )


def load_aoi(
    case_id: str,
    entry: Mapping[str, Any],
    *,
    aoi_dir: str | Path | None = None,
) -> AOI:
    """Resolve and validate one case AOI: GeoJSON override -> YAML ring -> bbox rectangle."""
    if not isinstance(entry, Mapping):
        raise AOIError(f"{case_id}: case entry must be a mapping, got {type(entry).__name__}")
    configured_bbox = _coerce_bbox(entry.get("bbox"), case_id)
    if configured_bbox is None:
        raise AOIError(
            f"{case_id}: case entry has no bbox; the yaml must declare [W, S, E, N] so the AOI can "
            "be checked against the study area"
        )

    directory = Path(aoi_dir) if aoi_dir is not None else AOI_DIR
    override_path = directory / f"{case_id}.geojson"
    if override_path.is_file():
        source = f"config/aoi/{override_path.name}"
        try:
            payload = json.loads(override_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AOIError(
                f"{source}: cannot parse GeoJSON ({exc}); re-export the AOI from QGIS as "
                "EPSG:4326 GeoJSON"
            ) from exc
        ring = validate_aoi(payload, configured_bbox, source=source)
        return _make_aoi(case_id, ring, source)

    ring_raw = entry.get("aoi_polygon")
    if ring_raw:
        source = "config/study_cases.yaml:aoi_polygon"
        ring = validate_aoi(ring_raw, configured_bbox, source=source)
        return _make_aoi(case_id, ring, source)

    west, south, east, north = configured_bbox
    ring = ((west, south), (east, south), (east, north), (west, north), (west, south))
    return _make_aoi(case_id, ring, "bbox")


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _summary(aoi: AOI) -> dict[str, Any]:
    return {
        "case_id": aoi.case_id,
        "source": aoi.source,
        "bbox": list(aoi.bbox),
        "area_km2": round(aoi.area_km2, 3),
        "ring_points": len(aoi.ring),
        "h3_res9_cells": aoi.h3_cells,
    }


def _print_summary(aoi: AOI) -> None:
    print(f"case            : {aoi.case_id}")
    print(f"source          : {aoi.source}")
    print(f"bbox [W,S,E,N]  : [{aoi.bbox[0]:.6f}, {aoi.bbox[1]:.6f}, {aoi.bbox[2]:.6f}, {aoi.bbox[3]:.6f}]")
    print(f"area_km2        : {aoi.area_km2:.3f}")
    print(f"ring points     : {len(aoi.ring)} (closed)")
    print(f"H3 res-9 cells  : {aoi.h3_cells}")


def _read_cases(config_path: Path) -> Mapping[str, Any]:
    if not config_path.is_file():
        raise AOIError(f"case config not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report the YAML parse failure as one error
        raise AOIError(f"cannot parse {config_path}: {exc}") from exc
    cases = raw.get("cases") if isinstance(raw, Mapping) else None
    if not isinstance(cases, Mapping):
        raise AOIError(f"{config_path}: no 'cases' mapping found")
    return cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/aoi.py",
        description=(
            "Resolve and validate a study-case AOI: config/aoi/<case>.geojson first, then the yaml "
            "aoi_polygon, then the bbox rectangle."
        ),
    )
    parser.add_argument("--case", help="case id from config/study_cases.yaml")
    parser.add_argument("--all", action="store_true", help="report every configured case")
    parser.add_argument("--check", action="store_true", help="exit non-zero if any AOI is invalid")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="study cases YAML")
    parser.add_argument("--aoi-dir", help="override the config/aoi directory (testing/CI)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.case and not args.all:
        parser.error("--case or --all is required")

    try:
        cases = _read_cases(Path(args.config))
    except AOIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.all:
        case_ids = list(cases)
    else:
        if args.case not in cases:
            print(
                f"error: undeclared case {args.case!r}; config declares {sorted(cases)}",
                file=sys.stderr,
            )
            return 2
        case_ids = [args.case]

    aoi_dir = Path(args.aoi_dir) if args.aoi_dir else None
    results: dict[str, Any] = {}
    failures = 0
    for case_id in case_ids:
        try:
            aoi = load_aoi(case_id, cases[case_id], aoi_dir=aoi_dir)
        except AOIError as exc:
            failures += 1
            results[case_id] = {"case_id": case_id, "error": str(exc)}
            print(f"error: {exc}", file=sys.stderr)
            continue
        results[case_id] = _summary(aoi)
        if not args.json:
            if len(case_ids) > 1:
                print(f"--- {case_id} ---")
            _print_summary(aoi)
            if len(case_ids) > 1:
                print()

    if args.json:
        payload: Any = results[case_ids[0]] if len(case_ids) == 1 else results
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
