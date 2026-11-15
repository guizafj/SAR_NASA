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

from pipeline import context as G
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
# context_layers.geojson — same land_use array as the grid, plus the mapped extract
# (or, on the fallback path, the declared synthetic geography)
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


#: Tolerance applied to published context geometry (≈22 m at these latitudes). Real OSM polygons
#: carry far more vertices than a 200 m cell map can show, and the artefact size budget is a hard
#: gate (`docs/context-sources.md` records the measured sizes), so published context geometry is
#: always simplified and the tolerance is a declared number, not a hidden default.
CONTEXT_SIMPLIFY_DEG = 2e-4
#: Published-feature budget per layer, applied per geometry kind (see
#: `context.select_for_publication`): the largest polygons, the longest lines and the points, so a
#: cap can never delete the coastline again.
FEATURE_CAP_PER_KIND = G.FEATURE_CAP_PER_KIND


def _simplified(geometry: Any, tolerance: float = CONTEXT_SIMPLIFY_DEG) -> Any:
    """Round + simplify a published geometry, keeping the polygon valid."""
    import shapely  # noqa: PLC0415 - optional dependency, guarded by the caller

    rounded = shapely.set_precision(geometry, 1e-5, mode="valid_output")
    simplified = rounded.simplify(tolerance, preserve_topology=True)
    simplified = shapely.set_precision(simplified, 1e-5, mode="valid_output")
    return simplified if not simplified.is_empty else rounded


def _published_context_features(
    model: CaseModel,
    layer: str,
    provenance: str,
    *,
    cap: int,
    rank_by: str,
    skip_classes: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Publish one context layer from `CaseContext.features`, simplified, capped and ordered.

    Attributes of a record are read by column name, not as `row.class_`: `class` is a Python
    keyword, so attribute access on a namedtuple silently renames it and a mis-typed attribute is a
    runtime crash rather than a contract failure.
    """
    case = model.case
    rows = model.context.features_of(layer)
    if rows.empty:
        return []
    if skip_classes:
        rows = rows[~rows["class"].isin(skip_classes)]
        if rows.empty:
            return []
    # The pin already holds the selected features in a deterministic order. Re-ranking them here
    # would rank SIMPLIFIED geometry on the pinned path but raw geometry on the fresh-extract path,
    # so two machines with the same seed would publish the same features in a different order — and
    # a byte-comparison of `context_layers.geojson` would fail for no scientific reason.
    ordered = rows if model.context.pinned else G.select_for_publication(rows, cap=cap)
    features: list[dict[str, Any]] = []
    for _, row in ordered.iterrows():
        geometry = row.geometry
        # The pin stores its geometry already simplified and rounded; simplifying it again made the
        # pinned path and the fresh-extract path publish different bytes for the same seed.
        if not model.context.synthetic and not model.context.pinned:
            geometry = _simplified(geometry)
        name = row.get("name")
        features.append(
            _feature(
                _round_geometry(geometry),
                _props(
                    case,
                    provenance,
                    layer,
                    str(row["class"]),
                    str(row.get("source") or model.context.source),
                    None if _is_blank(name) else str(name),
                ),
            )
        )
    return features


def _water_features(model: CaseModel, provenance: str) -> list[dict[str, Any]]:
    """The `water` layer: real polygons and lines where the extract has them."""
    return _published_context_features(
        model, "water", provenance, cap=FEATURE_CAP_PER_KIND, rank_by="area"
    )


def _infrastructure_features(model: CaseModel, provenance: str) -> list[dict[str, Any]]:
    """The `infrastructure` layer: real utility assets and transit nodes from the extract."""
    return _published_context_features(
        model, "infrastructure", provenance, cap=FEATURE_CAP_PER_KIND, rank_by="length"
    )


def _admin_features(model: CaseModel, provenance: str) -> list[dict[str, Any]]:
    """The `admin_boundary` layer: any real sub-municipal unit the extract carries."""
    return _published_context_features(
        model,
        "admin_boundary",
        provenance,
        cap=G.ADMIN_FEATURE_CAP,
        rank_by="area",
        skip_classes=("aoi",),  # the config AOI is published separately, from the authoritative ring
    )


def _aoi_feature(model: CaseModel, provenance: str) -> dict[str, Any]:
    """The published analysis extent: the real OSM city limit, not the coverage ellipse.

    The ellipse still decides which cells exist (a property of the model), but publishing it as the
    AOI told the map that the city is an ellipse. `config/aoi/<case>.geojson` is the ring the whole
    pipeline resolves cells from, so it is the ring the map gets.
    """
    case = model.case
    aoi = _aoi(case)
    # The source is whatever `load_aoi` actually resolved — `config/aoi/<case>.geojson` (an OSM city
    # limit) or the configured bbox, which is a rectangle and must never be called a city limit. A
    # hardcoded "OpenStreetMap city limit" string lied on the fallback path.
    source = str(aoi.source)
    if source.endswith(".geojson"):
        source = f"{source} (OpenStreetMap city limit, ODbL 1.0) — analysis extent, not a legal boundary"
    else:
        source = f"{source} — analysis extent, not a legal boundary"
    return _feature(
        {
            "type": "Polygon",
            "coordinates": [[[round(lon, 6), round(lat, 6)] for lon, lat in aoi.ring]],
        },
        _props(case, provenance, "admin_boundary", "aoi", source, f"{case.name} — analysis AOI"),
    )


def _aoi(case: Any) -> Any:
    from pipeline.aoi import load_aoi  # noqa: PLC0415 - avoids an import cycle at module load

    return load_aoi(case.case_id, {"bbox": list(case.bbox)})


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and value != value) or str(value).strip() == ""


def _round_geometry(geometry: Any) -> dict[str, Any]:
    """GeoJSON mapping with coordinates rounded to 6 decimals (≈11 cm)."""
    import shapely  # noqa: PLC0415

    rounded = shapely.set_precision(geometry, 1e-6, mode="valid_output") if geometry.geom_type in (
        "Polygon", "MultiPolygon", "LineString", "MultiLineString"
    ) else geometry
    return mapping(rounded)


def build_context(model: CaseModel, provenance: str) -> dict[str, Any]:
    """EPSG:4326 FeatureCollection: real geography, labelled by the grid's own assignment.

    One rule decides every land_use: the dissolve below unions the very cells the grid publishes, so
    the layer and the column cannot disagree (contract §6). The polygons are the H3 cells because
    that is what the grid is — publishing the raw OSM polygons instead measured 19.55 MB for a
    29 km² extract, which does not fit the artefact budget and would also let the layer and the
    column drift apart.
    """
    case = model.case
    context = model.context
    features: list[dict[str, Any]] = []

    # 1. land_use: one dissolved geometry per class actually present in the grid.
    for klass, geometry in land_use_geometries(model).items():
        features.append(
            _feature(mapping(geometry), _props(case, provenance, "land_use", klass, context.land_use_source))
        )

    # 2. infrastructure, 3. water: real features from the extract (or the declared fallback).
    features.extend(_infrastructure_features(model, provenance))
    features.extend(_water_features(model, provenance))

    # 4. admin_boundary: the real AOI ring plus any real district the extract carries.
    features.append(_aoi_feature(model, provenance))
    features.extend(_admin_features(model, provenance))
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


def _geography_limitation(model: CaseModel) -> str:
    """The disclosure that matches the geography actually published.

    Two states, two sentences, because only one of them can be true per artefact. The synthetic
    fallback is invented and must say so; the OpenStreetMap path is mapped and must instead disclose
    its *own* limit — the share of published cells that ended up `unknown`. That share has two
    causes and the extract alone cannot attribute them per artefact: no mapped polygon contains the
    centroid, or the containing polygon's OSM tags map to no class of the frozen vocabulary. Both
    causes are `unknown` and neither is filled in by neighbourhood or interpolation, so the sentence
    names both rather than picking one and overstating it.
    """
    context = model.context
    if context.synthetic:
        return (
            "Mock geography (coastline, lake, river, POIs) is invented by the synthetic fallback in "
            "pipeline/context.py and says so in the `source` of every synthetic context feature; the "
            "land_use classes come from an invented ring/corridor rule that is not published as "
            "geometry, and the only real feature in the file is the AOI polygon from config/aoi."
        )
    unknown = int((model.land_use == "unknown").sum())
    total = int(model.n_cells)
    share = 100.0 * unknown / total if total else 0.0
    osm_stamp = context.osm_timestamp or "timestamp not recorded in the extract manifest"
    return (
        "Context geography (land use, water, infrastructure) is derived from an OpenStreetMap "
        f"extract (data through {osm_stamp}) under the ODbL 1.0: OSM land use is de facto and "
        f"incomplete, so {unknown} of {total} published cells ({share:.1f} %) are `unknown` — "
        "either no mapped polygon contains their centroid, or the containing polygon's tags map to "
        "no class of the frozen land-use vocabulary. Both causes stay `unknown` and neither is "
        "extrapolated from a neighbouring cell."
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
                "rather than zero; coherence and risk bands are deterministic spatial rules; "
                + (
                    "land use is joined from the OpenStreetMap extract (water, infrastructure and "
                    "admin layers come from the same extract) and the published land_use polygons "
                    "are the dissolved H3 cells of this grid, one per class, so the layer and the "
                    "column share one assignment."
                    if not model.context.synthetic
                    else "land use is a deterministic spatial rule and the published land_use "
                    "polygons are the dissolved H3 cells of this grid, one per class, so the layer "
                    "and the column share one assignment."
                )
            ),
            "retrieved_utc": RETRIEVED_UTC,
            "reference": (
                "Synthetic; peak rates parameterised from config/study_cases.yaml published_rate_mm_yr "
                "and docs/data-contract-v1.md v1.1. Not a measurement."
            ),
            "license": (
                "Synthetic displacement field (mock) — no third-party data for the SAR values; "
                "context layers © OpenStreetMap contributors, ODbL 1.0"
                if not model.context.synthetic
                else "Synthetic mock — no third-party data (repo-internal artefact)"
            ),
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
            _geography_limitation(model),
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
