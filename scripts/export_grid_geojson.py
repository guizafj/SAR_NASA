#!/usr/bin/env python3
"""Export an artefact grid as GeoJSON points, for QGIS / kepler.gl inspection.

`data/processed/{case}/nisar_grid.parquet` is the product; QGIS wants a vector format it can style,
and Parquet support depends on the GDAL version. This writes one Point per H3 cell with the
colourable fields only — EPSG:4326, no smoothing, no interpolation, cells absent in the grid simply
do not appear (the contract's nodata rule).

    ./scripts/python.sh scripts/export_grid_geojson.py            # all cases
    ./scripts/python.sh scripts/export_grid_geojson.py --cases mexico-city
"""

from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd

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
                "geometry": {"type": "Point", "coordinates": [round(float(lon), 6), round(float(lat), 6)]},
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
        print(f"{case_dir.name:14s} {count:6d} points  {target}  {target.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
