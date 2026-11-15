#!/usr/bin/env python3
"""Rebuild `config/context/<case>.json` from the cached OSM extract (the pin writer).

The reader lives in `pipeline/context.py`, because that is what the generator imports on every run;
this file is the offline tool that produces the file the reader consumes, so the dependency runs
one way only (writer → context → generator) and there is no import cycle.

    ./scripts/python.sh -m pipeline.ingest.pin_context --case malaga
    ./scripts/python.sh -m pipeline.ingest.pin_context            # all three cases

Why a pin and not the extract: `data/raw/` is gitignored (tens of MB), but artefact regeneration
must be reproducible on a machine that never fetched it, including CI. Two guarantees, stated
precisely because they differ: (a) from the committed pin, regeneration is **byte-identical** — the
pin fixes the labels, the published feature set AND their order; (b) a machine that regenerates the
pin from its own extract gets the same features and geometry, but `context_layers.geojson` may list
them in a different order, because the pin writer ranks raw geometry while the artefact writer
publishes the pin's order. Only (a) is promised to a consumer. The pin keeps the
*decisions* the extract implies — one class per H3 res-9 cell plus the simplified water / utility /
admin features — in roughly a megabyte of readable JSON. `source` still names OpenStreetMap and the
ODbL, so a pinned polygon is as attributable as a freshly mapped one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `python pipeline/ingest/pin_context.py` from the repo root
    sys.path.insert(0, str(ROOT))

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from pipeline import context as CTX  # noqa: E402
from pipeline import enrich_context as EC  # noqa: E402
from pipeline.ingest import osm_context as OSM  # noqa: E402
from src.domain import contracts as C  # noqa: E402

PIN_SIMPLIFY_DEG = CTX.PIN_SIMPLIFY_DEG
PIN_PRECISION_DEG = 1e-5
FEATURE_CAP_PER_KIND = CTX.FEATURE_CAP_PER_KIND
ADMIN_FEATURE_CAP = CTX.ADMIN_FEATURE_CAP
CELL_LABEL_RESOLUTION = 9


def cells_to_label(case_id: str) -> gpd.GeoDataFrame:
    """Every cell the generator can ask about: the AOI ring **and** the coverage ellipse.

    Pinning only the AOI ring was a real defect: the model publishes bbox ∩ ellipse, so the cells
    between the ring and the ellipse had no pinned label and published as `unknown` even where a
    mapped OSM polygon contains their centroid.
    The union keeps every published cell labelled by the join it was meant to come from.
    """
    import h3  # noqa: PLC0415
    import yaml  # noqa: PLC0415

    from pipeline.aoi import load_aoi  # noqa: PLC0415

    config = yaml.safe_load((ROOT / "config" / "study_cases.yaml").read_text(encoding="utf-8"))
    entry = config["cases"][case_id]
    aoi = load_aoi(case_id, entry)
    ring = h3.LatLngPoly([(lat, lon) for lon, lat in aoi.open_ring()])
    cells = set(h3.polygon_to_cells(ring, CELL_LABEL_RESOLUTION))
    ellipse = entry["mock"]["coverage_ellipse"]
    centre = ellipse["center"]
    semi = ellipse["semi_axes_km"]
    bbox = tuple(float(value) for value in entry["bbox"])
    model_cells, _, _ = CTX.prefilter_cells(
        bbox, float(centre[0]), float(centre[1]), float(semi[0]), float(semi[1]),
        float(ellipse["rotation_deg"]), CELL_LABEL_RESOLUTION,
    )
    cells |= set(model_cells)
    rows = []
    for cell in sorted(cells):
        lat, lon = h3.cell_to_latlng(cell)
        rows.append({"cell_id": cell, "lon": float(lon), "lat": float(lat)})
    frame = pd.DataFrame(rows)
    return gpd.GeoDataFrame(
        frame, geometry=gpd.points_from_xy(frame["lon"], frame["lat"]), crs="EPSG:4326"
    ).sort_values("cell_id", kind="mergesort", ignore_index=True)


def _round_geometry(geometry: Any) -> dict[str, Any]:
    from shapely.geometry import mapping  # noqa: PLC0415

    if geometry is None or geometry.is_empty:
        return {}
    if geometry.geom_type in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
        geometry = shapely.set_precision(geometry, PIN_PRECISION_DEG, mode="valid_output")
    return mapping(geometry)


def _simplify(geometry: Any) -> Any:
    if geometry is None or geometry.is_empty:
        return geometry
    rounded = shapely.set_precision(geometry, PIN_PRECISION_DEG, mode="valid_output")
    simplified = rounded.simplify(PIN_SIMPLIFY_DEG, preserve_topology=True)
    simplified = shapely.set_precision(simplified, PIN_PRECISION_DEG, mode="valid_output")
    return rounded if simplified.is_empty else simplified


def _layer_features(mapped: gpd.GeoDataFrame, layer: str, cap: int, rank_by: str) -> list[dict[str, Any]]:
    rows = mapped[mapped["layer"] == layer]
    if rows.empty:
        return []
    ordered = CTX.select_for_publication(rows, cap=cap)
    out: list[dict[str, Any]] = []
    # Read by column name: `class` is a Python keyword, so namedtuple attribute access renames it
    # and a mis-typed attribute becomes a runtime crash instead of a contract failure.
    for _, row in ordered.iterrows():
        properties: dict[str, Any] = {"class": str(row["class"]), "source": str(row["source"])}
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            properties["name"] = name
        out.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": _round_geometry(_simplify(row.geometry)),
            }
        )
    return out


def build_pin(case_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Build the pinned context of a case from its cached extract (no network, no wall clock)."""
    record = OSM.extract_record(case_id, root)
    if record is None:
        raise SystemExit(
            f"no cached extract for {case_id}: expected {OSM.context_dir(case_id, root)}/manifest.json"
        )
    # Any valid-shape id satisfies the mapper; it is replaced by the case's own provenance when the
    # pin is read back, so nothing invented is ever published.
    mapped = EC.mmap_source_features(
        OSM.load_extract(case_id, root),
        case_id,
        f"{case_id}+mock+19700101+00000000",
        source=OSM.SOURCE,
    )
    context = CTX.CaseContext(
        case_id=case_id,
        features=mapped,
        coast=CTX.coast_from_features(mapped, case_id),
        synthetic=False,
        source=OSM.SOURCE,
    )
    bbox = tuple(record.bbox) if record.bbox else None
    # The sea is derived HERE, where the whole extract is available, and stored in the pin: the pin
    # keeps only the largest water features, so a reader could not re-derive land-vs-sea from it.
    sea = CTX.sea_polygons(mapped, bbox)
    context = CTX.CaseContext(
        case_id=context.case_id,
        features=context.features,
        coast=context.coast,
        synthetic=False,
        source=context.source,
        sea=sea,
    )
    cells = cells_to_label(case_id)
    land_use = CTX.label_cells(context, cells["cell_id"], cells["lon"], cells["lat"])
    payload: dict[str, Any] = {
        "case_id": case_id,
        "crs": "EPSG:4326",
        "source": OSM.SOURCE,
        "record": {
            "source": OSM.SOURCE,
            "retrieved_utc": record.retrieved_utc,
            "timestamp_osm_base": record.timestamp_osm_base,
            "tiles_ok": record.tiles_ok,
            "tiles_failed": record.tiles_failed,
            "elements": record.elements,
            "bbox": list(record.bbox) if record.bbox else None,
        },
        "sea": _round_geometry(_simplify(sea)) if sea is not None else None,
        # Recorded so the artefact gate can tell "no coastline in this AOI" (Mexico City: honest
        # `sea: null`) from "the coastline was lost and the sea rule silently switched off".
        "coastline_lines": len(CTX.coast_lines(mapped)),
        "cell_land_use": {
            str(cell): str(label) for cell, label in zip(cells["cell_id"], land_use, strict=True)
        },
        "water": _layer_features(mapped, "water", FEATURE_CAP_PER_KIND, "area"),
        "infrastructure": _layer_features(mapped, "infrastructure", FEATURE_CAP_PER_KIND, "length"),
        "admin": [
            feature
            for feature in _layer_features(mapped, "admin_boundary", ADMIN_FEATURE_CAP, "area")
            if feature["properties"]["class"] != "aoi"
        ],
    }
    counts = pd.Series(list(payload["cell_land_use"].values()))
    payload["class_counts"] = {str(key): int(value) for key, value in counts.value_counts().items()}
    return payload


def write_pin(payload: dict[str, Any], path: Path) -> int:
    """Write the pin with sorted keys and its own sha256, atomically enough for a rerun."""
    body = dict(payload)
    body.pop("pin_sha256", None)
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    body["pin_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return len(text.encode("utf-8"))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pin a case's OSM context to config/context/<case>.json")
    parser.add_argument("--case", action="append", help="case id (repeatable); default: all three")
    parser.add_argument("--out", default=str(CTX.PIN_DIR), help="pin directory (default config/context)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    cases = args.case or list(C.CASE_IDS)
    out = Path(args.out)
    for case_id in cases:
        payload = build_pin(case_id)
        path = CTX.pin_path(case_id, out)
        size = write_pin(payload, path)
        print(
            f"[{case_id}] {len(payload['cell_land_use'])} labelled cells, "
            f"{len(payload['water'])} water, {len(payload['infrastructure'])} infrastructure, "
            f"{len(payload['admin'])} admin -> {path} ({size / 1024:.0f} KiB)"
        )
        print(f"    classes: {payload['class_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
