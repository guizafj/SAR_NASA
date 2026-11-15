#!/usr/bin/env python3
"""Read the cached OSM context extract of a case into one GeoDataFrame.

This module is the *file* half of the context ingest: it knows where the extracted tiles live, how
a raw Overpass element becomes a shapely geometry, and what the extraction record is. It does not
fetch anything (a run is offline and deterministic once the tiles are on disk), does not classify
tags (`pipeline/enrich_context.py` owns the tag table) and does not touch the artefacts.

Layout it reads, per case, all of it under `data/raw/<case_id>/context/`:

    manifest.json          what was fetched, from where, when, and the sha256 of every tile
    tiles/<case>_r<r>_c<c>.json   verbatim Overpass responses, one per bbox tile

`data/raw/` is gitignored on purpose: an Overpass dump is tens of MB and does not belong in the
repository. The published artefacts (`data/processed/<case>/`) are what the app reads, and they
carry the retrieval record in `source` / `provenance`, so losing the raw tiles never makes a
published polygon unattributable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = ROOT / "data" / "raw"

#: Provider string published in `context_layers.geojson` `source`. The ODbL attribution the licence
#: requires lives in `README.md` §17; this string is what a feature-level consumer sees, so it names
#: both the provider and the licence.
SOURCE = "OpenStreetMap contributors (ODbL 1.0), Overpass API extract"

#: Tag keys carried through from the extract. `enrich_context` reads them positionally from the
#: frame, and keeping the list explicit stops an accidental 40-column frame of irrelevant tags.
TAG_KEYS: tuple[str, ...] = (
    "landuse",
    "natural",
    "waterway",
    "leisure",
    "man_made",
    "amenity",
    "power",
    "railway",
    "public_transport",
    "place",
    "boundary",
    "building",
    "name",
)


@dataclass(frozen=True)
class ExtractRecord:
    """What the cached extract is: its provenance, not a decoration.

    `source` is the string that reaches `context_layers.geojson`; `retrieved_utc` and `tiles` come
    from the manifest so a published polygon can be traced back to the pull that produced it.
    """

    case_id: str
    source: str
    retrieved_utc: str | None
    timestamp_osm_base: str | None
    tiles_ok: int
    tiles_failed: int
    elements: int
    bbox: tuple[float, float, float, float] | None
    tiles_dir: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "source": self.source,
            "retrieved_utc": self.retrieved_utc,
            "timestamp_osm_base": self.timestamp_osm_base,
            "tiles_ok": self.tiles_ok,
            "tiles_failed": self.tiles_failed,
            "elements": self.elements,
            "bbox": list(self.bbox) if self.bbox else None,
        }


def context_dir(case_id: str, root: Path | None = None) -> Path:
    return (root or CASE_ROOT) / case_id / "context"


def read_manifest(case_id: str, root: Path | None = None) -> dict[str, Any] | None:
    path = context_dir(case_id, root) / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _manifest_bbox(manifest: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    """The AOI bbox as `[W, S, E, N]`, from either manifest shape this repo has written.

    A fetcher may record the bbox as the four numbers the case declares, or as a dict of named
    edges (and the Overpass argument order it derived from them). Both are read; nothing is guessed
    from the tiles, because a bbox reconstructed from tiles would hide a missing tile.
    """
    bbox = manifest.get("bbox")
    if isinstance(bbox, Mapping):
        bbox = bbox.get("as_given") or bbox.get("wsen")
        if bbox is None and {"west", "south", "east", "north"} <= set(manifest["bbox"]):
            bbox = [
                manifest["bbox"]["west"],
                manifest["bbox"]["south"],
                manifest["bbox"]["east"],
                manifest["bbox"]["north"],
            ]
    if not bbox:
        return None
    try:
        values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None
    return tuple(values[:4]) if len(values) == 4 else None


def _tile_ok(tile: Mapping[str, Any]) -> bool:
    """One tile counts as fetched on any of the spellings a fetcher may use."""
    if tile.get("ok") is True:
        return True
    for key in ("status", "http_status", "httpStatusCode"):
        value = tile.get(key)
        if value is not None:
            try:
                return int(value) == 200
            except (TypeError, ValueError):
                return False
    return False


def _osm_data_stamp(manifest: Mapping[str, Any], ok_tiles: list[Mapping[str, Any]]) -> str | None:
    """When the OSM data itself was current, from any of the spellings a fetcher may record.

    A fetcher that tiles an AOI legitimately has no single global stamp: the tiles were answered at
    different moments. The honest value is therefore the range it recorded (`osm_timestamp_base_range`)
    or the latest per-tile stamp, never `None` dressed up as a date — the disclosure prints the value
    it finds, and prints "not recorded" only when nothing was recorded.
    """
    for key in ("timestamp_osm_base", "osm_timestamp_base"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    recorded = [
        str(tile.get("osm_timestamp")).strip()
        for tile in ok_tiles
        if isinstance(tile.get("osm_timestamp"), str) and str(tile.get("osm_timestamp")).strip()
    ]
    if recorded:
        return max(recorded)
    span = manifest.get("osm_timestamp_base_range")
    if isinstance(span, str) and span.strip():
        return span.strip()
    if isinstance(span, (list, tuple)) and span:
        return str(span[-1])
    return None


def _manifest_tile_states(manifest: Mapping[str, Any] | None) -> dict[str, bool]:
    """Per-tile `ok` flag of a manifest, keyed by the tile file stem (`<case>_r<r>_c<c>`).

    A manifest that names no tile `id` (the hand-built shape some fixtures use) yields an empty map:
    an absent record is not a failure, so the directory is trusted exactly as before. Only a tile
    the manifest *does* name and marks not-ok is skipped by `load_extract`.
    """
    if not isinstance(manifest, Mapping):
        return {}
    states: dict[str, bool] = {}
    for tile in manifest.get("tiles") or ():
        if not isinstance(tile, Mapping):
            continue
        ident = tile.get("id")
        if isinstance(ident, str) and ident.strip():
            states[ident.strip()] = _tile_ok(tile)
    return states


def extract_record(case_id: str, root: Path | None = None) -> ExtractRecord | None:
    """The retrieval record of a cached extract, or `None` when nothing was ever fetched."""
    manifest = read_manifest(case_id, root)
    if manifest is None:
        return None
    tiles = manifest.get("tiles") or []
    ok = [tile for tile in tiles if isinstance(tile, Mapping) and _tile_ok(tile)]
    return ExtractRecord(
        case_id=case_id,
        source=SOURCE,
        retrieved_utc=manifest.get("retrieved_utc") or manifest.get("extraction_utc"),
        timestamp_osm_base=_osm_data_stamp(manifest, ok),
        tiles_ok=len(ok),
        tiles_failed=len(tiles) - len(ok),
        elements=int(sum(int(tile.get("elements") or 0) for tile in ok)),
        bbox=_manifest_bbox(manifest),
        tiles_dir=context_dir(case_id, root) / "tiles",
    )


# ---------------------------------------------------------------------------------------------
# Element -> geometry
# ---------------------------------------------------------------------------------------------


def _way_geometry(element: Mapping[str, Any]) -> BaseGeometry | None:
    """A closed way is a polygon; anything else is a line. Degenerate ways are dropped."""
    coords = [
        (float(node["lon"]), float(node["lat"]))
        for node in (element.get("geometry") or ())
        if isinstance(node, Mapping) and "lon" in node and "lat" in node
    ]
    if len(coords) < 2:
        return None
    if coords[0] == coords[-1] and len(coords) >= 4:
        polygon = Polygon(coords)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon if not polygon.is_empty else None
    return LineString(coords)


def _relation_geometry(element: Mapping[str, Any], counters: dict[str, int]) -> BaseGeometry | None:
    """Assemble a multipolygon relation from its way members (outer minus inner).

    `out geom` gives every member's node list, so no node store is needed. A relation whose members
    do not close into a ring is counted in `counters["unassembled"]` and dropped rather than
    guessed at — publishing a fabricated polygon would be worse than publishing none.
    """
    outer: list[LineString] = []
    inner: list[LineString] = []
    closed_outer: list[Polygon] = []
    for member in element.get("members") or ():
        if not isinstance(member, Mapping) or member.get("type") != "way":
            counters["non_way_member"] += 1
            continue
        coords = [
            (float(node["lon"]), float(node["lat"]))
            for node in (member.get("geometry") or ())
            if isinstance(node, Mapping) and "lon" in node and "lat" in node
        ]
        if len(coords) < 2:
            continue
        line = LineString(coords)
        if (member.get("role") or "").lower() == "inner":
            inner.append(line)
            continue
        outer.append(line)
        if coords[0] == coords[-1] and len(coords) >= 4:
            closed_outer.append(Polygon(coords))

    geometry: BaseGeometry | None = None
    if outer:
        try:
            rings = list(polygonize(unary_union(outer)))
        except Exception:  # noqa: BLE001 - a broken member must not abort the whole case
            rings = []
        if rings:
            geometry = unary_union(rings)
    if geometry is None and closed_outer:
        geometry = unary_union(closed_outer)
    if geometry is None or geometry.is_empty:
        counters["unassembled"] += 1
        return None
    if inner:
        try:
            holes = list(polygonize(unary_union(inner)))
            if holes:
                geometry = geometry.difference(unary_union(holes))
        except Exception:  # noqa: BLE001 - holes are optional detail, not the feature
            counters["holes_failed"] += 1
    counters["assembled"] += 1
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    return geometry if not geometry.is_empty else None


def _element_geometry(element: Mapping[str, Any], counters: dict[str, int]) -> BaseGeometry | None:
    kind = element.get("type")
    if kind == "way":
        return _way_geometry(element)
    if kind == "relation":
        return _relation_geometry(element, counters)
    if kind == "node":
        lat, lon = element.get("lat"), element.get("lon")
        if lat is None or lon is None:
            return None
        counters["node"] += 1
        return Point(float(lon), float(lat))
    return None


# ---------------------------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------------------------


def load_extract(case_id: str, root: Path | None = None) -> gpd.GeoDataFrame:
    """Every fetched element of a case as one GeoDataFrame, deduplicated and sorted.

    Tiles are queried independently and a way that touches two tiles comes back twice, so the
    (type, id) pair is the identity: duplicates are dropped, and the order is sorted, which makes
    the frame independent of tile order and of Overpass's element order.

    Raises `FileNotFoundError` when the case has no cached extract: the caller decides whether that
    is a declared fallback or a hard stop.

    When the manifest names a tile and marks it not-ok (a failed `--force` that kept the previous
    file as evidence, say), that file is **skipped**: the manifest is the retrieval record, and a
    stale tile parsed as if it had been fetched is data the run never retrieved. A manifest that
    names no tile (or no manifest at all, which is already a hard stop above) changes nothing.
    """
    record = extract_record(case_id, root)
    if record is None:
        raise FileNotFoundError(
            f"no OSM extract for {case_id!r}: expected {context_dir(case_id, root) / 'manifest.json'} "
            "(see pipeline/ingest/README.md for the offline retrieval recipe)"
        )
    states = _manifest_tile_states(read_manifest(case_id, root))
    on_disk = sorted(record.tiles_dir.glob("*.json"))
    tiles = [tile for tile in on_disk if states.get(tile.stem, True)]
    skipped = [tile.stem for tile in on_disk if not states.get(tile.stem, True)]
    if not on_disk:
        raise FileNotFoundError(f"manifest exists but no tiles under {record.tiles_dir}")
    if not tiles:
        raise FileNotFoundError(
            f"the manifest marks every tile under {record.tiles_dir} as not fetched: "
            f"{len(skipped)} stale file(s) skipped rather than parsed"
        )

    counters = {"assembled": 0, "unassembled": 0, "non_way_member": 0, "holes_failed": 0, "node": 0}
    rows: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []
    seen: set[tuple[str, int]] = set()

    for tile in tiles:
        try:
            payload = json.loads(tile.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue  # counted by the manifest; a truncated file must not poison the rest
        for element in payload.get("elements") or ():
            if not isinstance(element, Mapping):
                continue
            ident = (str(element.get("type")), int(element.get("id") or 0))
            if ident in seen or ident[1] == 0:
                continue
            geometry = _element_geometry(element, counters)
            if geometry is None:
                continue
            seen.add(ident)
            tags = element.get("tags") or {}
            row: dict[str, Any] = {"osm_type": ident[0], "osm_id": ident[1]}
            for key in TAG_KEYS:
                value = tags.get(key) if isinstance(tags, Mapping) else None
                row[key] = str(value) if value is not None else None
            rows.append(row)
            geometries.append(geometry)

    frame = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
    frame = frame.sort_values(["osm_type", "osm_id"], kind="mergesort").reset_index(drop=True)
    frame.attrs["counters"] = counters
    frame.attrs["record"] = record
    frame.attrs["source"] = record.source
    # The skipped files are reported, not silently dropped: a reader that asks why a mapped polygon
    # is missing needs the manifest's answer, and the counters stay exactly what they were.
    frame.attrs["skipped_tiles"] = skipped
    return frame


def tile_files(case_id: str, root: Path | None = None) -> list[Path]:
    return sorted((context_dir(case_id, root) / "tiles").glob("*.json"))


def summarise(case_id: str, root: Path | None = None) -> dict[str, Any]:
    """Cheap census of the cached extract, for logs and for the run report (no geometry built)."""
    record = extract_record(case_id, root)
    if record is None:
        return {"case_id": case_id, "cached": False}
    counts: dict[str, int] = {}
    elements = 0
    for tile in tile_files(case_id, root):
        try:
            payload = json.loads(tile.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for element in payload.get("elements") or ():
            elements += 1
            for key in ("landuse", "natural", "waterway", "leisure"):
                value = (element.get("tags") or {}).get(key)
                if value:
                    counts[f"{key}={value}"] = counts.get(f"{key}={value}", 0) + 1
    return {
        "case_id": case_id,
        "cached": True,
        "elements": elements,
        "record": record.as_dict(),
        "tags": dict(sorted(counts.items(), key=lambda item: -item[1])),
    }


def tag_census(counts: Mapping[str, int], limit: int = 15) -> Iterable[tuple[str, int]]:
    return list(sorted(counts.items(), key=lambda item: -item[1])[:limit])
