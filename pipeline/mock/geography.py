"""Compatibility shim: the case geography now lives in `pipeline/context.py`.

The module moved out of `pipeline/mock/` when the OSM extract became its real source — `pipeline/`
imports it, `src/` does not, and the mock package no longer owns it. Everything that used to be
exported from here is re-exported unchanged, so old imports keep working; new code should import
`pipeline.context`.
"""

from __future__ import annotations

from pipeline.context import (  # noqa: F401  (re-exported API)
    KM_PER_DEG_LAT,
    KM_PER_DEG_LON_EQ,
    SYNTHETIC,
    SYNTHETIC_BOUNDARY_SOURCE,
    SYNTHETIC_INFRASTRUCTURE_SOURCE,
    SYNTHETIC_LAND_USE_SOURCE,
    SYNTHETIC_WATER_SOURCE,
    CaseContext,
    Coast,
    SyntheticGeography,
    coast_from_features,
    coast_lines,
    corridor_segment,
    ellipse_rho,
    ellipse_ring,
    km_per_deg_lon,
    label_cells,
    load_context,
    local_km_to_lonlat,
    lonlat_to_local_km,
    polyline_metrics,
    prefilter_cells,
    sea_polygons,
    select_for_publication,
    summarise_context,
    synthetic_context,
)

__all__ = [
    "KM_PER_DEG_LAT",
    "KM_PER_DEG_LON_EQ",
    "SYNTHETIC",
    "SYNTHETIC_BOUNDARY_SOURCE",
    "SYNTHETIC_INFRASTRUCTURE_SOURCE",
    "SYNTHETIC_LAND_USE_SOURCE",
    "SYNTHETIC_WATER_SOURCE",
    "CaseContext",
    "Coast",
    "SyntheticGeography",
    "coast_from_features",
    "coast_lines",
    "corridor_segment",
    "ellipse_rho",
    "ellipse_ring",
    "km_per_deg_lon",
    "label_cells",
    "load_context",
    "local_km_to_lonlat",
    "lonlat_to_local_km",
    "polyline_metrics",
    "prefilter_cells",
    "sea_polygons",
    "select_for_publication",
    "summarise_context",
    "synthetic_context",
]
