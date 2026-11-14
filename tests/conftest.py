"""Shared synthetic case builders for Rol 1 backend tests (valid against frozen contract v1.0)."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

import h3
import numpy as np
import pandas as pd
import pytest

from src.data.loaders import clear_loader_cache
from src.domain import contracts as C

CITY_CENTERS: dict[str, tuple[str, list[float], tuple[float, float]]] = {
    "mexico-city": ("Mexico City, MX", [-99.35, 19.20, -98.85, 19.65], (-99.13, 19.43)),
    "jakarta": ("Jakarta, ID", [106.70, -6.40, 107.00, -6.10], (106.85, -6.21)),
    "malaga": ("Málaga, ES", [-4.60, 36.60, -4.30, 36.85], (-4.42, 36.72)),
}


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_loader_cache()
    yield
    clear_loader_cache()


def write_synthetic_case(
    processed_root: pathlib.Path,
    case_id: str = "mexico-city",
    *,
    sensor: str = "nisar",
    product: str = "mock",
    n_rings: int = 1,
    include_cell_series: bool = False,
    flat: bool = False,
) -> pathlib.Path:
    """Write a complete, 100% contract-conformant four-artefact case to `processed_root`."""
    name, bbox, (ref_lon, ref_lat) = CITY_CENTERS[case_id]
    profile = C.sensor_profile(sensor, product)
    prov = C.provenance_id(case_id, product, dt.date(2026, 11, 14), "deadbeef")
    source_label = "MOCK synthetic generator" if product == "mock" else "ASF DAAC"

    center_h3 = h3.latlng_to_cell(ref_lat, ref_lon, 9)
    cells = sorted(h3.grid_disk(center_h3, n_rings))

    start = dt.date(2026, 7, 2)
    step = profile.revisit_days or 12
    dates = [(start + dt.timedelta(days=i * step)).isoformat() for i in range(6)]
    span_years = ((dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days) / 365.25

    grid_rows: list[dict[str, Any]] = []
    land_uses = ("urban-dense", "urban-residential", "infrastructure", "vegetation")

    for idx, cid in enumerate(cells):
        lat, lon = h3.cell_to_latlng(cid)
        if case_id == "mexico-city":
            # Strong subsidence gradient: -380 down to -3.5 mm/yr
            vel = -380.0 + idx * 55.0
        elif case_id == "jakarta":
            vel = -210.0 + idx * 32.0
        else:
            # Málaga negative control: near-zero rates within [-2.4, +1.2] mm/yr
            vel = -2.4 + idx * 0.5

        val_t1 = 0.0
        val_t2 = round(vel * span_years, 4)
        delta = val_t2 - val_t1
        coh = round(min(0.95, 0.28 + 0.09 * idx), 3)

        grid_rows.append(
            {
                "cell_id": cid,
                "lat": float(lat),
                "lon": float(lon),
                "val_t1": val_t1,
                "val_t2": val_t2,
                "delta_change": delta,
                "velocity_mm_yr": float(vel),
                "seasonal_amp_mm": 9.0,
                "ratio_R": abs(float(vel)) / 9.0,
                "regime": "inelastic" if abs(float(vel)) / 9.0 > 2.0 else "transitional",
                "coherence": coh,
                "land_use": land_uses[idx % len(land_uses)],
                "risk_level": C.risk_level_from_velocity(vel),
                "case_id": case_id,
                "sensor": sensor,
                "product": product,
                "unit": C.UNIT,
                "velocity_unit": C.VELOCITY_UNIT,
                "source": source_label,
                "acquisition_date": dates[-1],
                "provenance_id": prov,
            }
        )

    grid_df = pd.DataFrame(grid_rows)
    vel_np = grid_df["velocity_mm_yr"].to_numpy(dtype=np.float64)
    median_vel = float(np.median(vel_np))

    # Build AOI aggregate series + optional drill-down series for the first cell
    ts_rows: list[dict[str, Any]] = []
    for d_idx, iso_date in enumerate(dates):
        frac = (d_idx * step) / 365.25
        ts_rows.append(
            {
                "case_id": case_id,
                "cell_id": "aoi",
                "date": iso_date,
                "value": round(median_vel * frac, 4),
                "trend_mm_yr": median_vel,
                "seasonal_mm": 0.0,
                "unit": C.UNIT,
                "sensor": sensor,
                "product": product,
                "quality": "good" if d_idx < 5 else "fair",
                "provenance_id": prov,
            }
        )

    if include_cell_series:
        target_cid = cells[0]
        target_vel = float(grid_rows[0]["velocity_mm_yr"])
        for d_idx, iso_date in enumerate(dates):
            frac = (d_idx * step) / 365.25
            ts_rows.append(
                {
                    "case_id": case_id,
                    "cell_id": target_cid,
                    "date": iso_date,
                    "value": round(target_vel * frac, 4),
                    "trend_mm_yr": target_vel,
                    "seasonal_mm": 0.0,
                    "unit": C.UNIT,
                    "sensor": sensor,
                    "product": product,
                    "quality": "good",
                    "provenance_id": prov,
                }
            )

    ts_df = pd.DataFrame(ts_rows)

    stats: dict[str, Any] = {
        "n_cells": len(grid_df),
        "n_dates": len(dates),
        "median_velocity_mm_yr": round(median_vel, 3),
        "median_seasonal_amp_mm": 9.0,
        "median_ratio_R": round(abs(median_vel) / 9.0, 3),
        "dominant_regime": "inelastic" if abs(median_vel) / 9.0 > 2.0 else "transitional",
        "p05_velocity_mm_yr": round(float(np.percentile(vel_np, 5)), 3),
        "p95_velocity_mm_yr": round(float(np.percentile(vel_np, 95)), 3),
    }
    if abs(median_vel) < C.NULL_RESULT_MM_YR:
        stats["detection_limit_mm_yr"] = 1.5

    meta = {
        "schema_version": C.SCHEMA_VERSION,
        "case_id": case_id,
        "name": name,
        "bbox": bbox,
        "crs": C.CRS_OUTPUT,
        "reference_point": [ref_lon, ref_lat],
        "dates": dates,
        "revisit_days": step,
        "sensor": sensor,
        "product": product,
        "thresholds": {
            "coherence_min": profile.coherence_min,
            "k_sigma": profile.k_sigma,
            "min_ddB": profile.min_ddB,
            "persistence": profile.persistence,
            "min_area_m2": profile.min_area_m2,
        },
        "provenance": {
            "source": source_label,
            "product_id": f"{product.upper()}_{case_id}_TEST",
            "processing_date": "2026-11-14",
            "method": "LOS->vertical (v_north=0), H3 res 9 mean",
            "retrieved_utc": "2026-11-14T16:00:00Z",
            "reference": "Herrera-Garcia et al. 2021, doi:10.1126/science.abb8549",
            "license": "NASA open data",
        },
        "stats": stats,
        "limitations": [
            "GUNW posting is 80 m: city scale, not building scale.",
            "Coherence is revisit-dependent; never pool 12-day NISAR with 6-day Sentinel-1.",
        ],
        "null_policy": "Cells without data are absent from the grid; never written as 0.",
    }

    w, s, e, n = bbox
    context = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [ref_lon, ref_lat]},
                "properties": {
                    "layer": "infrastructure",
                    "class": "water-well",
                    "name": f"Reference Well {case_id}",
                    "case_id": case_id,
                    "crs": C.CRS_OUTPUT,
                    "source": "OSM",
                    "provenance_id": prov,
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
                },
                "properties": {
                    "layer": "admin_boundary",
                    "class": "aoi",
                    "name": f"AOI {name}",
                    "case_id": case_id,
                    "crs": C.CRS_OUTPUT,
                    "source": "Config",
                    "provenance_id": prov,
                },
            },
        ],
    }

    out_dir = processed_root if flat else (processed_root / case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    grid_df.to_parquet(out_dir / "nisar_grid.parquet", index=False)
    ts_df.to_csv(out_dir / "timeseries.csv", index=False)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out_dir / "context_layers.geojson").write_text(json.dumps(context, indent=2), encoding="utf-8")
    return out_dir
