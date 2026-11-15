"""Exact Rol 1 -> Rol 2 adapter functions requested by `app.py` & `docs/ui-data-needs.md`.

Re-exported from `src.data.loaders` so `app.py._loaders()` automatically switches away from
`src.ui._stubs` to real validated artefacts under `data/processed/{case_id}/` with zero UI edits.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.data.loaders import (
    DEFAULT_PROCESSED_DIR,
    DEFAULT_STUDY_CASES_PATH,
    ROOT_DIR,
    list_available_cases,
    load_all_available_bundles,
    load_case_bundle,
    load_study_cases_config,
)
from src.domain import contracts as C
from src.domain.filters import FilterCriteria, apply_filters
from src.domain.map_adapter import (
    ScaleMode,
    build_map_payload,
    compute_view_state,
)
from src.domain.metrics import compute_kpis

_SHARED_LABELS = frozenset({"común a todos los casos", "compartida", "shared"})


def _resolve_scale_mode(raw_mode: Any) -> ScaleMode:
    if str(raw_mode or "").strip().lower() in _SHARED_LABELS:
        return "shared"
    return "independent"


def _dict_to_criteria(filters: Mapping[str, Any] | None) -> FilterCriteria:
    if not isinstance(filters, Mapping):
        return FilterCriteria(coherence_min=0.0)
    lu = filters.get("land_use")
    rl = filters.get("risk_level")
    coh = filters.get("coherence_min")
    coh_val = (
        float(coh)
        if isinstance(coh, (int, float)) and not isinstance(coh, bool)
        else 0.0
    )
    return FilterCriteria(
        coherence_min=coh_val,
        land_uses=(str(lu),) if lu not in (None, "") else None,
        risk_levels=(str(rl),) if rl not in (None, "") else None,
    )


def _case_provenance_id(bundle: Any) -> str:
    prov = bundle.meta.get("provenance") or {}
    if prov.get("provenance_id"):
        return str(prov["provenance_id"])
    if not bundle.grid.empty and "provenance_id" in bundle.grid.columns:
        return str(bundle.grid["provenance_id"].iloc[0])
    return ""


def load_study_cases(
    config_path: pathlib.Path | str = DEFAULT_STUDY_CASES_PATH,
) -> list[dict[str, str]]:
    """Return `[{'id': slug, 'label': name}, ...]` in frozen `CASE_IDS` order."""
    cfg_file = pathlib.Path(config_path)
    if cfg_file.is_file():
        cfg = load_study_cases_config(cfg_file)
        return [
            {"id": slug, "label": cfg.cases[slug].name if slug in cfg.cases else slug}
            for slug in C.CASE_IDS
        ]
    return [{"id": slug, "label": slug} for slug in C.CASE_IDS]


def load_meta(
    case_id: str,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, Any]:
    """Return validated `meta.json` enriched with top-level display telemetry keys."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    meta = dict(bundle.meta)
    meta.setdefault("unit", C.UNIT)
    prov_id = _case_provenance_id(bundle)
    if prov_id:
        meta.setdefault("provenance_id", prov_id)
    return meta


def load_land_use_options(
    case_id: str | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> tuple[str, ...]:
    """Return distinct `land_use` categories actually present in the published grid(s)."""
    present: set[str] = set()
    if case_id:
        bundle = load_case_bundle(case_id, processed_dir=processed_dir)
        present.update(bundle.grid["land_use"].astype(str).unique())
    else:
        for bundle in load_all_available_bundles(processed_dir=processed_dir).values():
            present.update(bundle.grid["land_use"].astype(str).unique())
    if not present:
        return C.LAND_USE
    return tuple(lu for lu in C.LAND_USE if lu in present)


def load_risk_levels(
    case_id: str | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> tuple[str, ...]:
    """Return distinct `risk_level` bands present in the published grid(s) (or frozen tuple)."""
    if case_id:
        bundle = load_case_bundle(case_id, processed_dir=processed_dir)
        present = set(bundle.grid["risk_level"].astype(str).unique())
        if present:
            return tuple(lvl for lvl in C.RISK_LEVELS if lvl in present)
    return C.RISK_LEVELS


def get_map_layer_df(
    case_id: str,
    filters: Mapping[str, Any] | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> pd.DataFrame:
    """Return H3 map DataFrame with pre-computed `color` (RGBA) and `height` (3D metres)."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    crit = _dict_to_criteria(filters)
    mode = _resolve_scale_mode((filters or {}).get("scale_mode"))
    extrude = bool((filters or {}).get("extrude_3d", True))

    payload = build_map_payload(
        bundle,
        crit,
        metric="velocity_mm_yr",
        scale_mode=mode,
        extrude_3d=extrude,
        include_h3_polygons=False,
    )
    columns = [
        "cell_id",
        "lat",
        "lon",
        "delta_change",
        "velocity_mm_yr",
        "coherence",
        "land_use",
        "risk_level",
        "unit",
        "velocity_unit",
        "color",
        "height",
    ]
    if payload.is_empty:
        return pd.DataFrame(columns=columns)

    df = payload.grid_frame.copy()
    df["color"] = df["fill_color"]
    df["height"] = df["elevation_m"]
    return df[columns]


def get_color_scale(
    case_id: str,
    filters: Mapping[str, Any] | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, Any]:
    """Return the active colour scale domain `[min, max]` and saturation status."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    crit = _dict_to_criteria(filters)
    mode = _resolve_scale_mode((filters or {}).get("scale_mode"))
    payload = build_map_payload(
        bundle, crit, metric="velocity_mm_yr", scale_mode=mode, include_h3_polygons=False
    )
    vmin = float(payload.legend.domain_min or -425.0)
    vmax = float(payload.legend.domain_max or 425.0)
    if payload.is_empty:
        saturated = False
    else:
        vels = payload.grid_frame["velocity_mm_yr"].to_numpy(dtype=np.float64)
        saturated = bool(((vels < vmin) | (vels > vmax)).any())
    return {
        "min": vmin,
        "max": vmax,
        "unit": payload.legend.unit,
        "saturated": saturated,
        "fallback": False,
        "mixed_units": False,
    }


def get_layer_counts(
    case_id: str,
    filters: Mapping[str, Any] | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, int]:
    """Return `{'shown': filtered_count, 'total': total_count}` without UI-side aggregation."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    view = apply_filters(bundle, _dict_to_criteria(filters))
    return {
        "shown": len(view.grid),
        "total": view.total_grid_cells,
    }


def get_view_state(
    case_id: str,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, float] | None:
    """Return `{'latitude', 'longitude', 'zoom'}` computed from `meta.bbox`."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    vs = compute_view_state(bundle.meta, extrude_3d=False)
    return {
        "latitude": vs.latitude,
        "longitude": vs.longitude,
        "zoom": vs.zoom,
    }


def get_signal_status(
    case_id: str,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, Any]:
    """Return null-result detection status echoed directly from `meta.stats`."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    kpis = compute_kpis(bundle, FilterCriteria(coherence_min=0.0))
    return {
        "above_limit": not kpis.is_null_result,
        "detection_limit": kpis.detection_limit_mm_yr,
        "detection_limit_unit": C.VELOCITY_UNIT if kpis.detection_limit_mm_yr is not None else None,
    }


def get_map_kpis(
    case_id: str,
    filters: Mapping[str, Any] | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> list[dict[str, Any]]:
    """Return summary KPI cards displayed above the PyDeck viewport."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    view = apply_filters(bundle, _dict_to_criteria(filters))
    kpis = compute_kpis(view)
    prov_id = _case_provenance_id(bundle)
    coh_min = float(view.grid["coherence"].min()) if not view.grid.empty else None
    return [
        {
            "label": "velocidad mediana",
            "value": round(kpis.median_velocity_mm_yr, 2) if kpis.median_velocity_mm_yr is not None else None,
            "unit": C.VELOCITY_UNIT,
            "provenance_id": prov_id,
        },
        {
            "label": "delta_change mediana",
            "value": round(kpis.median_delta_mm, 2) if kpis.median_delta_mm is not None else None,
            "unit": C.UNIT,
            "provenance_id": prov_id,
        },
        {
            "label": "coherence min",
            "value": round(coh_min, 3) if coh_min is not None else None,
            "unit": None,
            "provenance_id": prov_id,
        },
        {
            # v1.1: fitted, not typed — `seasonal_amp_mm` comes from the harmonic fit of each
            # cell's own series, so the card and the parquet can never disagree.
            "label": "amplitud estacional mediana",
            "value": (
                round(kpis.median_seasonal_amp_mm, 2)
                if kpis.median_seasonal_amp_mm is not None
                else None
            ),
            "unit": C.UNIT,
            "provenance_id": prov_id,
        },
    ]


def get_cell_detail(
    case_id: str,
    cell_id: str,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, Any] | None:
    """Return attributes of one selected H3 cell plus its per-cell series (when emitted)."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    matches = bundle.grid.loc[bundle.grid["cell_id"].astype(str) == str(cell_id)]
    if matches.empty:
        return None
    row = matches.iloc[0]
    detail: dict[str, Any] = {
        "cell_id": str(row["cell_id"]),
        "case_id": str(row["case_id"]),
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "val_t1": float(row["val_t1"]),
        "val_t2": float(row["val_t2"]),
        "delta_change": float(row["delta_change"]),
        "velocity_mm_yr": float(row["velocity_mm_yr"]),
        # v1.1: the fitted seasonal pair. Present only when the artefact carries it, so a v1.0
        # tree still renders instead of raising a KeyError in the detail panel. A not-fitted cell
        # publishes amplitude 0.0 with a *null* ratio (|trend|/0 is undefined), which is reported
        # as None rather than as NaN — NaN is not valid JSON and would poison a JSON consumer.
        "seasonal_amp_mm": float(row["seasonal_amp_mm"]) if "seasonal_amp_mm" in row else None,
        "ratio_R": (
            float(row["ratio_R"])
            if "ratio_R" in row and pd.notna(row["ratio_R"])
            else None
        ),
        "regime": str(row["regime"]) if "regime" in row else None,
        "coherence": float(row["coherence"]),
        "land_use": str(row["land_use"]),
        "risk_level": str(row["risk_level"]),
        "sensor": str(row["sensor"]),
        "product": str(row["product"]),
        "unit": str(row["unit"]),
        "velocity_unit": str(row["velocity_unit"]),
        "source": str(row["source"]),
        "provenance_id": str(row["provenance_id"]),
        "acquisition_date": (
            str(row["acquisition_date"])
            if "acquisition_date" in row and pd.notna(row["acquisition_date"])
            else None
        ),
    }
    cell_ts = bundle.timeseries.loc[bundle.timeseries["cell_id"].astype(str) == str(cell_id)]
    detail["series"] = (
        [
            {
                "case_id": str(r.case_id),
                "cell_id": str(r.cell_id),
                "date": str(r.date),
                "value": float(r.value),
                "trend_mm_yr": (
                    float(r.trend_mm_yr) if hasattr(r, "trend_mm_yr") else None
                ),
                "seasonal_mm": (
                    float(r.seasonal_mm) if hasattr(r, "seasonal_mm") else None
                ),
                "unit": str(r.unit),
                "sensor": str(r.sensor),
                "product": str(r.product),
                "quality": str(r.quality),
                "provenance_id": str(r.provenance_id),
            }
            for r in cell_ts.itertuples(index=False)
        ]
        if not cell_ts.empty
        else []
    )
    return detail


def load_timeseries(
    case_id: str,
    cell_id: str | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> pd.DataFrame:
    """Return `timeseries.csv` filtered to `cell_id` (or full case series if `cell_id is None`)."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    if cell_id is None:
        return bundle.timeseries.copy()
    return bundle.timeseries.loc[
        bundle.timeseries["cell_id"].astype(str) == str(cell_id)
    ].reset_index(drop=True)


def get_series_kpis(
    case_id: str,
    filters: Mapping[str, Any] | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> list[dict[str, Any]]:
    """Return the four KPI cards rendered in Tab 2 (`Series y KPIs`)."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    view = apply_filters(bundle, _dict_to_criteria(filters))
    kpis = compute_kpis(view)
    prov_id = _case_provenance_id(bundle)
    coh_floor = float((bundle.meta.get("thresholds") or {}).get("coherence_min", 0.35))

    total = len(view.grid)
    if total == 0:
        pct_above = None
        pct_coh = None
    else:
        vels = view.grid["velocity_mm_yr"].to_numpy(dtype=np.float64)
        cohs = view.grid["coherence"].to_numpy(dtype=np.float64)
        pct_above = round(float((np.abs(vels) >= C.NULL_RESULT_MM_YR).mean() * 100.0), 1)
        pct_coh = round(float((cohs >= coh_floor).mean() * 100.0), 1)

    return [
        {
            "label": "velocidad mediana",
            "value": round(kpis.median_velocity_mm_yr, 2) if kpis.median_velocity_mm_yr is not None else None,
            "unit": C.VELOCITY_UNIT,
            "provenance_id": prov_id,
            "note": "mediana del subconjunto filtrado",
        },
        {
            "label": "% de celdas por encima del umbral",
            "value": pct_above,
            "unit": "%",
            "provenance_id": prov_id,
            "note": f"umbral {C.NULL_RESULT_MM_YR:g} mm/year",
        },
        {
            "label": "celdas críticas",
            "value": kpis.critical_cells_count,
            "unit": "celdas",
            "provenance_id": prov_id,
            "note": "risk_level critico",
        },
        {
            "label": "cobertura coherente",
            "value": pct_coh,
            "unit": "%",
            "provenance_id": prov_id,
            "note": f"coherence >= {coh_floor:g}",
        },
        {
            "label": "amplitud estacional mediana",
            "value": (
                round(kpis.median_seasonal_amp_mm, 2)
                if kpis.median_seasonal_amp_mm is not None
                else None
            ),
            "unit": C.UNIT,
            "provenance_id": prov_id,
            "note": (
                f"ajuste armónico, R = |tendencia|/A; régimen dominante {kpis.dominant_regime}"
                if kpis.dominant_regime
                else "ajuste armónico, no un valor de configuración"
            ),
        },
    ]


def get_case_series(
    case_id: str,
    filters: Mapping[str, Any] | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> list[dict[str, Any]]:
    """Return the published `cell_id == 'aoi'` series (`mm`) for Tab 2's line chart.

    v1.1 adds the fitted ``trend_mm_yr`` / ``seasonal_mm`` columns of each row (``None`` when a
    v1.0 tree is on disk), so the chart can draw the decomposition without re-fitting anything.
    """
    del filters  # AOI series is a case-level artefact; per-cell series are optional (contract §4)
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    aoi = bundle.timeseries.loc[bundle.timeseries["cell_id"].astype(str) == "aoi"]
    return [
        {
            "date": str(r.date),
            "value": float(r.value),
            "trend_mm_yr": float(r.trend_mm_yr) if hasattr(r, "trend_mm_yr") else None,
            "seasonal_mm": float(r.seasonal_mm) if hasattr(r, "seasonal_mm") else None,
            "unit": str(r.unit),
            "provenance_id": str(r.provenance_id),
        }
        for r in aoi.itertuples(index=False)
    ]


def get_land_use_bars(
    case_id: str,
    filters: Mapping[str, Any] | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> list[dict[str, Any]]:
    """Return median `velocity_mm_yr` per present `land_use` category (no absent classes filled)."""
    bundle = load_case_bundle(case_id, processed_dir=processed_dir)
    view = apply_filters(bundle, _dict_to_criteria(filters))
    if view.grid.empty:
        return []
    prov_id = _case_provenance_id(bundle)
    grouped = view.grid.groupby("land_use", sort=False)["velocity_mm_yr"].median()
    ordered_lus = [lu for lu in C.LAND_USE if lu in grouped.index]
    return [
        {
            "land_use": lu,
            "value": round(float(grouped[lu]), 2),
            "unit": C.VELOCITY_UNIT,
            "provenance_id": prov_id,
        }
        for lu in ordered_lus
    ]


def get_scale_comparison(
    scale_mode: str = "Común a todos los casos",
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, Any]:
    """Compare peak `|velocity_mm_yr|` across all published cases on one shared physical axis."""
    shared = _resolve_scale_mode(scale_mode) == "shared"
    bundles = load_all_available_bundles(processed_dir=processed_dir)
    peaks: list[tuple[str, float, str]] = []
    for slug in C.CASE_IDS:
        if slug not in bundles:
            continue
        b = bundles[slug]
        peak = float(b.grid["velocity_mm_yr"].abs().max()) if not b.grid.empty else 0.0
        peaks.append((slug, round(peak, 2), _case_provenance_id(b)))

    span = max((p for _, p, _ in peaks), default=0.0)
    cases = [
        {
            "id": slug,
            "unit": C.VELOCITY_UNIT,
            "peak": peak,
            "share": (0.0 if span == 0.0 else peak / span) if shared else (1.0 if peak > 0 else 0.0),
            "provenance_id": prov_id,
        }
        for slug, peak, prov_id in peaks
    ]
    return {
        "reference_unit": C.VELOCITY_UNIT if shared else None,
        "mixed_units": False,
        "cases": cases,
    }


def load_context_layers(
    case_id: str | None = None,
    *,
    processed_dir: pathlib.Path | str = DEFAULT_PROCESSED_DIR,
) -> dict[str, Any] | None:
    """Return summary and GeoJSON for `context_layers.geojson` of `case_id` (or primary case)."""
    available = list_available_cases(processed_dir=processed_dir)
    if not available:
        return None
    target = case_id if (case_id and case_id in available) else available[0]
    bundle = load_case_bundle(target, processed_dir=processed_dir)
    features = bundle.context.get("features") or []
    layer_counts: dict[str, int] = {}
    for feat in features:
        lname = str(((feat or {}).get("properties") or {}).get("layer", "unknown"))
        layer_counts[lname] = layer_counts.get(lname, 0) + 1
    ctx_path = bundle.source_dir / "context_layers.geojson"
    try:
        rel_path = ctx_path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        rel_path = str(ctx_path)
    return {
        "path": rel_path,
        "geojson": bundle.context,
        "summary": {
            "case_id": target,
            "n_features": len(features),
            "layers": layer_counts,
        },
    }


def load_documented_limits(
    readme_path: pathlib.Path | str = ROOT_DIR / "README.md",
) -> dict[str, Any]:
    """Extract documented GNSS caveat and numbered §13 limitations verbatim from `README.md`."""
    path = pathlib.Path(readme_path)
    if not path.is_file():
        return {"section_8_caveat": None, "section_13": []}
    text = path.read_text(encoding="utf-8")
    caveat: str | None = None
    for line in text.splitlines():
        # Match the verb in any form: the caveat sentence uses "validated"/"unvalidated"
        # as often as "validate", and a tense change must not silently drop the caveat.
        if re.search(r"validat\w* against GNSS", line):
            caveat = " ".join(line.strip("* ").split())
            break

    start = text.find("## 13.")
    end = text.find("## 14.")
    items: list[str] = []
    if start != -1:
        block = text[start : end if end != -1 else None]
        for line in block.splitlines():
            stripped = line.strip()
            if stripped[:2].rstrip(".").isdigit() and ". " in stripped:
                items.append(stripped.split(". ", 1)[1])
    return {"section_8_caveat": caveat, "section_13": items}


__all__ = [
    "get_case_series",
    "get_cell_detail",
    "get_color_scale",
    "get_land_use_bars",
    "get_layer_counts",
    "get_map_kpis",
    "get_map_layer_df",
    "get_scale_comparison",
    "get_series_kpis",
    "get_signal_status",
    "get_view_state",
    "load_context_layers",
    "load_documented_limits",
    "load_land_use_options",
    "load_meta",
    "load_risk_levels",
    "load_study_cases",
    "load_timeseries",
]
