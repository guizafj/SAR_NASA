#!/usr/bin/env python3
"""Export an artefact grid as GeoJSON hexagons, for QGIS / kepler.gl inspection.

`data/processed/{case}/nisar_grid.parquet` is the product; QGIS wants a vector format it can style,
and Parquet support depends on the GDAL version. This writes one **Polygon** per H3 cell with the
colourable fields only — EPSG:4326, no smoothing, no interpolation, cells absent in the grid simply
do not appear (the contract's nodata rule).

Why polygons and not points
---------------------------
This exported centroids first, and styled them as hexagon markers sized in map units. That failed
twice over, both times caused by the *symbol* rather than the data:

* QGIS map units follow the project CRS. With the project in EPSG:4326 the unit is **degrees**, so a
  349-unit hexagon covered 349 degrees of longitude — a world map of three giant hexagons.
* The artefact's per-case lattices are not one global H3 tessellation (measured spacing 341.8 m in
  Jakarta, 372.1 m in Mexico City), so no single marker width tiles all three anyway.

The real cell boundary is exact, needs no size, and is CRS-proof, so the geometry in the file is now
the geometry on the map. It is the same ring the app draws (`map_adapter._cell_polygon_lonlat`), so
a QGIS map and a PyDeck map cannot disagree about where a cell is.

    ./scripts/python.sh scripts/export_grid_geojson.py            # all cases
    ./scripts/python.sh scripts/export_grid_geojson.py --cases mexico-city
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Imported rather than reimplemented: this is the exact ring the Streamlit/PyDeck map draws, so the
# two maps cannot disagree about a cell boundary. Private, but sharing it beats a second copy.
from src.domain.map_adapter import _cell_polygon_lonlat as cell_polygon  # noqa: E402

NUMERIC = ("velocity_mm_yr", "delta_change", "val_t2", "coherence")
TEXT = ("cell_id", "land_use", "risk_level")


def export(case_dir: pathlib.Path, target: pathlib.Path) -> int:
    grid = pd.read_parquet(case_dir / "nisar_grid.parquet")
    features = []
    for lat, lon, vel, delta, val2, coh, cell, land, risk in zip(
        grid["lat"], grid["lon"], grid["velocity_mm_yr"], grid["delta_change"],
        grid["val_t2"], grid["coherence"], grid["cell_id"], grid["land_use"], grid["risk_level"],
    ):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [cell_polygon(str(cell), float(lon), float(lat))],
                },
                "properties": {
                    "cell_id": str(cell),
                    "velocity_mm_yr": round(float(vel), 3),
                    "delta_change": round(float(delta), 3),
                    "val_t2": round(float(val2), 3),
                    "coherence": round(float(coh), 4),
                    "land_use": str(land),
                    "risk_level": str(risk),
                },
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return len(features)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", default="data/processed")
    parser.add_argument("--out", default="data/staging/qgis")
    parser.add_argument("--cases", nargs="*", default=None)
    args = parser.parse_args()

    root = pathlib.Path(args.processed)
    cases = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
    if not cases:
        print(f"no case directories under {root}")
        return 1

    out = pathlib.Path(args.out)
    for case_dir in cases:
        if args.cases and case_dir.name not in args.cases:
            continue
        target = out / f"{case_dir.name}.geojson"
        count = export(case_dir, target)
        print(f"{case_dir.name:14s} {count:6d} cells  {target}  {target.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
