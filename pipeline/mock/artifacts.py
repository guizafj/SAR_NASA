"""Deterministic writers for the four frozen artefacts + the context/meta assembly.

Determinism rules enforced here (a rerun must be byte-identical):

* no wall-clock value ever reaches a file — the processing date and retrieval stamp are fixed
  constants that match `contracts.CONTRACT_FROZEN_UTC` (a mock has no retrieval event);
* no absolute path, no hostname, no dict iteration order: JSON is dumped with `sort_keys=True`,
  GeoJSON property order is fixed by construction, CSV/parquet row order is sorted by cell_id;
* every file is written to a sibling temp file and `os.replace`d, so a killed rerun cannot leave a
  half-written artefact for the app teammates to load.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from shapely.geometry import mapping

from pipeline.science import transforms as V
from src.domain import contracts as C

from . import geography as G
from .cases import CaseSpec
from .model import CaseModel, land_use_geometries

PRODUCT = "mock"
SENSOR = C.SENSOR_PROFILES[PRODUCT].sensor  # never re-declared here
MOCK_SOURCE = "mock (synthetic stub, pipeline/generate_mock.py)"
#: Fixed, not `datetime.now()`: byte-identical reruns beat a timestamp nobody reads. Matches the
#: contract freeze instant so the four artefacts can be cross-referenced.
PROCESSING_DATE = "2026-11-14"
RETRIEVED_UTC = C.CONTRACT_FROZEN_UTC

#: Size budget for the whole `data/processed/` tree (contract §3, informe §14).
TOTAL_BUDGET_BYTES = 15 * 1024 * 1024
#: Per-case cap for the optional drill-down rows, so one city cannot eat the whole budget.
PER_CELL_SERIES_BUDGET_BYTES = 3 * 1024 * 1024

ARTEFACTS = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")


def provenance_digest(case_id: str, seed: int, dates: Iterable[str]) -> str:
    """sha256(case + product + seed + sha256(epochs)): the digest behind `provenance_id`.

    **The acquisition window is part of the digest**, because it is part of the dataset: two
    stacks over different windows are two different claims and must never share an id. Before
    contract 1.1 this hashed only ``case + product + seed``, and `PROCESSING_DATE` is a fixed
    constant, so the 12-date 1.0 artefacts and the 62-date 1.1 artefacts of the same case came
    out byte-identical (``jakarta+mock+20261114+57d20da8`` for both) — an id that cannot tell
    two datasets apart cannot make "rerun and diff" work.

    The epochs are hashed **in the order given** (the stack's acquisition order, as carried by
    ``CaseModel.dates``: chronological, first to last). Nothing is sorted: a reordered window is
    a different stack and is allowed to digest differently, while the same stack always produces
    the same string regardless of platform or dict iteration order.

    Input string: ``{case_id}|{product}|{seed}|sha256(",".join(iso_dates))``.
    """
    window = hashlib.sha256(",".join(str(date) for date in dates).encode()).hexdigest()
    return hashlib.sha256(f"{case_id}|{PRODUCT}|{int(seed)}|{window}".encode()).hexdigest()


def provenance_id(case_id: str, seed: int, dates: Iterable[str]) -> str:
    return C.provenance_id(case_id, PRODUCT, PROCESSING_DATE, provenance_digest(case_id, seed, dates))


# ---------------------------------------------------------------------------------------------
# Atomic, deterministic writers
# ---------------------------------------------------------------------------------------------


def _atomic_write(path: Path, write: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        write(tmp)
        os.chmod(tmp, 0o644)  # teammates read these; mkstemp would leave 0600
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path.stat().st_size


def write_parquet(frame: pd.DataFrame, path: Path) -> int:
    return _atomic_write(
        path,
        lambda tmp: frame.to_parquet(tmp, engine="pyarrow", compression="zstd", index=False),
    )


def write_csv(frame: pd.DataFrame, path: Path) -> int:
    return _atomic_write(
        path,
        lambda tmp: frame.to_csv(tmp, index=False, lineterminator="\n", float_format="%.9f"),
    )


def write_geojson(collection: dict[str, Any], path: Path) -> int:
    blob = json.dumps(collection, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    return _atomic_write(path, lambda tmp: tmp.write_bytes(blob.encode("utf-8")))


def write_json(payload: dict[str, Any], path: Path) -> int:
    blob = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return _atomic_write(path, lambda tmp: tmp.write_bytes(blob.encode("utf-8")))


# ---------------------------------------------------------------------------------------------
# context_layers.geojson — same land_use array as the grid, plus the invented geography
# ---------------------------------------------------------------------------------------------


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _props(
    case: CaseSpec, provenance: str, layer: str, klass: str, source: str, name: str | None = None
) -> dict[str, Any]:
    """Frozen property set and order (§6): layer, class, name?, case_id, crs, source, provenance."""
    props: dict[str, Any] = {"layer": layer, "class": klass}
    if name is not None:
        props["name"] = name
    props.update(
        {
            "case_id": case.case_id,
            "crs": C.CRS_OUTPUT,
            "source": source,
            "provenance_id": provenance,
        }
    )
    return props


def _round_ring(ring: Iterable[tuple[float, float]]) -> list[list[float]]:
    return [[round(float(lon), 6), round(float(lat), 6)] for lon, lat in ring]


def build_context(model: CaseModel, provenance: str) -> dict[str, Any]:
    """EPSG:4326 FeatureCollection built from the grid's own `land_use` assignment."""
    case = model.case
    geo = G.GEOGRAPHY[case.case_id]
    features: list[dict[str, Any]] = []

    # 1. land_use: one dissolved geometry per class actually present in the grid.
    for klass, geometry in land_use_geometries(model).items():
        features.append(
            _feature(mapping(geometry), _props(case, provenance, "land_use", klass, geo.land_use_source))
        )

    # 2. infrastructure: a handful of POIs, inside the coverage ellipse by construction.
    for klass, name, lon, lat in geo.points:
        features.append(
            _feature(
                {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                _props(case, provenance, "infrastructure", klass, geo.infrastructure_source, name),
            )
        )

    # 3. water: coastline and/or inland body, plus the river/canal through the AOI.
    if geo.coast is not None:
        features.append(
            _feature(
                {"type": "LineString", "coordinates": _round_ring(geo.coast.verts)},
                _props(case, provenance, "water", "coastline", geo.water_source, "Línea de costa (sintética)"),
            )
        )
    if geo.lake is not None:
        lake_lon, lake_lat, lake_a, lake_b, lake_rot = geo.lake
        ring = G.ellipse_ring(lake_lon, lake_lat, lake_a, lake_b, lake_rot, n=48)
        features.append(
            _feature(
                {"type": "Polygon", "coordinates": [ring]},
                _props(case, provenance, "water", "reservoir", geo.water_source, geo.lake_name),
            )
        )
    if geo.waterway is not None:
        klass, name, verts = geo.waterway
        features.append(
            _feature(
                {"type": "LineString", "coordinates": _round_ring(verts)},
                _props(case, provenance, "water", klass, geo.water_source, name),
            )
        )

    # 4. admin_boundary: the coverage ellipse *is* the published AOI (cells outside are absent).
    ellipse = case.field.coverage_ellipse
    aoi = G.ellipse_ring(
        ellipse.center_lon,
        ellipse.center_lat,
        ellipse.semi_axes_km[0],
        ellipse.semi_axes_km[1],
        ellipse.rotation_deg,
    )
    features.append(
        _feature(
            {"type": "Polygon", "coordinates": [aoi]},
            _props(case, provenance, "admin_boundary", "aoi", geo.boundary_source, "AOI de cobertura (sintético)"),
        )
    )
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------------------------
# meta.json
# ---------------------------------------------------------------------------------------------


def _not_fitted_limitation(model: CaseModel) -> str:
    """The limitation that must travel with an amplitude of ``0.0`` and a null ``ratio_R``.

    Contract 1.1: a null ratio without its reason reads as "no seasonality was measured", a claim
    about the ground, when what happened is "no seasonal term was fitted", a statement about the
    model. `contracts.validate_meta` requires the phrase "not fitted" whenever
    ``stats.seasonal_n_harmonics`` is 0, and the real chain publishes the same caveat.
    """
    if float(model.case.field.seasonal_amplitude_mm) == 0.0:
        why = (
            "this case injects no seasonality (mock.seasonal_amplitude_mm: 0 in "
            "config/study_cases.yaml), so there is no annual term to recover"
        )
        instead = (
            "A non-zero mock.seasonal_amplitude_mm over a window that clears the "
            f"{V.MIN_SEASONAL_COVERAGE_PER_HARMONIC:.2f} annual-cycle coverage gate fits one"
        )
    else:
        why = (
            f"the stack spans {float(model.annual_coverage):.2f} annual cycles over "
            f"{len(model.dates)} dates, below the {V.MIN_SEASONAL_COVERAGE_PER_HARMONIC:.2f} "
            "cycle-per-harmonic gate in pipeline.science.transforms.harmonic_order"
        )
        instead = "Lengthening the window until it clears that gate fits one"
    return (
        f"Seasonal term not fitted: {why}. seasonal_amp_mm is published as 0.0 (no seasonal term "
        "is in the model — this is NOT a measurement that seasonality is zero), ratio_R as null "
        "(|trend| / 0 is undefined, so neither inf nor a sentinel is published) and regime as "
        "'transitional' (no regime information); velocity_mm_yr is identified by the span and is "
        f"unaffected. {instead}."
    )


def build_meta(
    model: CaseModel,
    provenance: str,
    *,
    n_series_rows: int,
    per_cell: dict[str, Any],
    n_context_features: int,
) -> dict[str, Any]:
    case = model.case
    profile = C.SENSOR_PROFILES[PRODUCT]
    velocities = model.velocity_mm_yr
    median = float(pd.Series(velocities).median())
    p05 = float(pd.Series(velocities).quantile(0.05))
    p95 = float(pd.Series(velocities).quantile(0.95))
    median_seasonal_amp = float(pd.Series(model.seasonal_amp_mm).median())
    # The median of an undefined ratio is null, not NaN: `ratio_R` is NaN exactly where nothing was
    # fitted (amplitude 0), and NaN is not valid JSON. Same rule as the real chain's `_stats`.
    finite_ratio = model.ratio_R[np.isfinite(model.ratio_R)]
    median_ratio_R = round(float(np.median(finite_ratio)), 6) if finite_ratio.size else None
    # Deterministic tie-break by label: `value_counts` alone would let two regimes with equal
    # counts swap places between runs and break byte-identical output.
    regime_counts = pd.Series(model.regime).value_counts()
    dominant_regime = sorted(regime_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[0][0]

    stats: dict[str, Any] = {
        "n_cells": model.n_cells,
        "n_dates": len(model.dates),
        "median_velocity_mm_yr": round(median, 3),
        "median_seasonal_amp_mm": round(median_seasonal_amp, 3),
        "median_ratio_R": median_ratio_R,
        "dominant_regime": str(dominant_regime),
        "p05_velocity_mm_yr": round(p05, 3),
        "p95_velocity_mm_yr": round(p95, 3),
        # The evidence behind the seasonal trio, so a reader can re-run the gate without trusting
        # the prose: the harmonic order actually fitted and the two diagnostics harmonic_fit used.
        "seasonal_n_harmonics": int(model.n_harmonics),
        "annual_coverage": round(float(model.annual_coverage), 6),
        "design_condition": round(float(model.design_condition), 6),
        "n_series_rows": int(n_series_rows),
        "n_context_features": int(n_context_features),
        "per_cell_series": per_cell,
    }
    if case.detection_limit_mm_yr is not None:
        stats["detection_limit_mm_yr"] = float(case.detection_limit_mm_yr)
    elif abs(median) < C.NULL_RESULT_MM_YR:
        # Contract §5: |median| < 5 mm/yr is a declared null result and MUST publish its bound.
        raise ValueError(
            f"{case.case_id}: |median velocity| {median:.3f} < {C.NULL_RESULT_MM_YR} mm/yr is a null "
            "result: config/study_cases.yaml must declare detection_limit_mm_yr"
        )

    return {
        "schema_version": C.SCHEMA_VERSION,
        "case_id": case.case_id,
        "name": case.name,
        "bbox": list(case.bbox),
        "crs": C.CRS_OUTPUT,
        "reference_point": list(case.reference_point),
        "dates": list(model.dates),
        "revisit_days": profile.revisit_days,
        "sensor": profile.sensor,
        "product": PRODUCT,
        "thresholds": {
            "coherence_min": profile.coherence_min,
            "k_sigma": profile.k_sigma,
            "min_ddB": profile.min_ddB,
            "persistence": profile.persistence,
            "min_area_m2": profile.min_area_m2,
        },
        "provenance": {
            "source": MOCK_SOURCE,
            "product_id": f"mock:{case.case_id}:{PROCESSING_DATE}:{provenance.split('+')[-1]}",
            "processing_date": PROCESSING_DATE,
            "method": (
                "Synthetic field, no observation: secular velocity v = peak*exp(-d(peak_lonlat)/falloff) "
                "+ N(0, noise) per H3 res-9 cell (config/study_cases.yaml mock block); cumulative "
                "displacement d(t) = v*(t-t_first) + A_seasonal*(cos(2*pi*(t-c)/yr) - cos(2*pi*c/yr)) "
                "with c the centre of the observation window, so both endpoints carry the same "
                "seasonal offset and val_t1 = 0 mm at the reference epoch; val_t1/val_t2 are d(t) at "
                "the first/last date; velocity_mm_yr, seasonal_amp_mm, ratio_R, regime, trend_mm_yr "
                "and seasonal_mm are ALL outputs of one joint fit per cell by "
                "pipeline.science.transforms.harmonic_fit over the whole window "
            )
            + (
                f"({model.n_harmonics} annual harmonic(s), README §6 P6), not values copied from "
                "the config; ratio_R = abs(velocity_mm_yr)/seasonal_amp_mm and regime is "
                "transforms.classify_regime on the same pair; "
                if model.seasonal_fitted
                else (
                    "(no annual harmonic, README §6 P6): no seasonal term is fitted, so "
                    "seasonal_amp_mm is 0.0 (not fitted), ratio_R is null and regime is "
                    "'transitional'; "
                )
            )
            + (
                "cells below the frozen "
                "coherence_min are dropped, so water and other decorrelating surfaces are absent "
                "rather than zero; coherence, land use, risk bands and context layers are "
                "deterministic spatial rules; land use and its polygons share one assignment."
            ),
            "retrieved_utc": RETRIEVED_UTC,
            "reference": (
                "Synthetic; peak rates parameterised from config/study_cases.yaml published_rate_mm_yr "
                "and docs/data-contract-v1.md v1.1. Not a measurement."
            ),
            "license": "Synthetic mock — no third-party data (repo-internal artefact)",
            "provenance_id": provenance,
            # Publish the seed so the published cell set is auditable: the post-write gate rebuilds
            # the model from it and must reproduce the grid cell-for-cell (see checks.assert_case).
            "mock_seed": model.seed,
        },
        "stats": stats,
        "limitations": list(case.limitations)
        + ([] if model.seasonal_fitted else [_not_fitted_limitation(model)])
        + [
            "MOCK: every number is synthesised by pipeline/generate_mock.py; validate the map and the "
            "schema, never the science.",
            "Mock geography (coastline, lake, river, corridor, POIs) is invented in "
            "pipeline/mock/geography.py and labelled as mock in every context feature.",
        ],
        "null_policy": (
            "A cell is absent from nisar_grid.parquet in exactly two cases: (a) its centroid falls "
            "outside the coverage ellipse, and (b) its modelled coherence is below "
            "thresholds.coherence_min, i.e. it is a decorrelating surface (water, specular/coastal "
            "cells, the low-look coverage edge). Nodata is never written as 0: a cell carries a "
            "displacement or it does not exist. A missing date is a missing timeseries row, never "
            "a sentinel value."
        ),
    }
