"""Temporary loaders. # TODO(rol1): replace with src/data/loaders.py.

Synthetic grid only. Values are placeholders so the cockpit can render.
They are not measurements. Do not copy them into scientific copy.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import h3
import pandas as pd

from src.domain.contracts import (
    CASE_IDS,
    LAND_USE,
    NULL_RESULT_MM_YR,
    SCHEMA_VERSION,
    UNIT,
    risk_level_from_velocity,
    sensor_profile,
)
from src.ui.color_scale import FALLBACK_DOMAIN, color_for, values_saturate
from src.ui.formatters import is_shared_scale

# TODO(rol1): parse config/study_cases.yaml. File absent; labels stay as slugs.
_STUDY_CASES = Path(__file__).resolve().parents[2] / "config" / "study_cases.yaml"
_AOIS = Path(__file__).resolve().parents[2] / "pipeline" / "ingest" / "aois.json"
_DATES = ("2026-01-01", "2026-01-13", "2026-01-25")
_DISK_K = 4
_RES = 9
# TODO(rol1): camera zoom is a view default, not a measurement.
_ZOOM = 13.2
_README = Path(__file__).resolve().parents[2] / "README.md"
_CONTEXT = Path(__file__).resolve().parents[2] / "data" / "processed" / "context_layers.geojson"
_REFERENCE_UNIT = "mm/year"


def _provenance_id(case_id: str) -> str:
    # Not a hash. Real provenance_id is pending from Rol 1.
    return f"{case_id}+mock+stub"


def load_study_cases() -> list[dict[str, str]]:
    """# TODO(rol1): read config/study_cases.yaml and return id + label."""
    if _STUDY_CASES.is_file():
        # TODO(rol1): parse the yaml. Do not guess display names.
        return [{"id": case_id, "label": case_id} for case_id in CASE_IDS]
    return [{"id": case_id, "label": case_id} for case_id in CASE_IDS]


def _aoi(case_id: str) -> dict[str, Any]:
    if not _AOIS.is_file():
        return {}
    payload = json.loads(_AOIS.read_text(encoding="utf-8"))
    cities = payload.get("cities") if isinstance(payload, dict) else None
    info = cities.get(case_id, {}) if isinstance(cities, dict) else {}
    return info if isinstance(info, dict) else {}


def _is_control(case_id: str) -> bool:
    """Control comes from the AOI note, not from a hardcoded slug."""
    regime = str(_aoi(case_id).get("expected_regime", "")).lower()
    return "null" in regime or "control" in regime


def load_meta(case_id: str) -> dict[str, Any]:
    """# TODO(rol1): load meta.json. In-memory mock. No detection limit invented."""
    profile = sensor_profile("nisar", "mock")
    info = _aoi(case_id)
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "sensor": profile.sensor,
        "product": "mock",
        "unit": _display_unit(case_id),
        "dates": list(_DATES),
        "provenance_id": _provenance_id(case_id),
        "thresholds": {
            "coherence_min": profile.coherence_min,
            "k_sigma": profile.k_sigma,
            "min_ddB": profile.min_ddB,
            "persistence": profile.persistence,
            "min_area_m2": profile.min_area_m2,
        },
        "provenance": {
            "source": "mock",
            "product_id": "",
            "processing_date": "",
            "method": "mock",
            "provenance_id": _provenance_id(case_id),
        },
    }
    if isinstance(info.get("name"), str):
        meta["name"] = info["name"]
    return meta


def load_land_use_options() -> tuple[str, ...]:
    """# TODO(rol1): distinct land_use from the artefact, not the full enum.

    Select is global, so this is the union across mock cases. Empty categories
    must not appear: they zero the layer (e.g. vegetation never occurs in the disk).
    """
    values = sorted({str(row["land_use"]) for case_id in CASE_IDS for row in _grid(case_id)})
    return tuple(values)


def load_risk_levels() -> tuple[str, ...]:
    """# TODO(rol1): distinct risk_level from the artefact, not the full enum."""
    values = sorted({str(row["risk_level"]) for case_id in CASE_IDS for row in _grid(case_id)})
    return tuple(values)


def _reference(case_id: str) -> tuple[float, float] | None:
    point = _aoi(case_id).get("reference_point")
    if not isinstance(point, list) or len(point) < 2:
        return None
    longitude, latitude = point[0], point[1]
    if not isinstance(longitude, (int, float)) or not isinstance(latitude, (int, float)):
        return None
    return float(latitude), float(longitude)


def _display_unit(case_id: str) -> str:
    """# TODO(rol1): unit comes from meta.json. Mock only, so the shared scale can collapse."""
    return "mm" if _is_control(case_id) else "cm/year"


def _to_reference(value: float, unit: str) -> float | None:
    """Display comparison. mm is not converted into a rate."""
    if unit == "cm/year":
        return float(value) * 10.0
    if unit in {"mm", "mm/year"}:
        return float(value)
    return None


def _velocity_mm_yr(value: float, unit: str) -> float | None:
    if unit == "cm/year":
        return float(value) * 10.0
    if unit == "mm/year":
        return float(value)
    return None


def _delta(ring: int, control: bool) -> float:
    if control:
        return 0.0
    # Outer ring sits outside FALLBACK_DOMAIN so the shared ramp saturates.
    if ring >= _DISK_K:
        return -12.0
    return -float(ring)


@lru_cache(maxsize=8)
def _grid(case_id: str) -> tuple[dict[str, Any], ...]:
    """# TODO(rol1): read nisar_grid.parquet. This disk is a visible sample, not the AOI."""
    origin = _reference(case_id)
    if origin is None:
        return ()
    latitude, longitude = origin
    center = h3.latlng_to_cell(latitude, longitude, _RES)
    control = _is_control(case_id)
    profile = sensor_profile("nisar", "mock")
    rows: list[dict[str, Any]] = []
    for cell_id in sorted(h3.grid_disk(center, _DISK_K)):
        ring = h3.grid_distance(center, cell_id)
        lat, lon = h3.cell_to_latlng(cell_id)
        delta = _delta(ring, control)
        unit = _display_unit(case_id)
        velocity_mm = _velocity_mm_yr(delta, unit)
        risk = risk_level_from_velocity(velocity_mm) if velocity_mm is not None else "estable"
        # Scatter low coherence (~1/5). Ring-aligned floors open concentric gaps that
        # read as two stacked layers on multi-color cases (mexico-city / jakarta).
        coherence = 0.2 if h3.str_to_int(cell_id) % 5 == 0 else 0.9
        rows.append(
            {
                "cell_id": cell_id,
                "lat": lat,
                "lon": lon,
                "delta_change": delta,
                "coherence": coherence,
                "land_use": LAND_USE[ring % len(LAND_USE)],
                "risk_level": risk,
                "unit": unit,
                "height": 0.0 if control else abs(delta) * 8.0,
                "sensor": profile.sensor,
                "product": "mock",
                "source": "mock",
                "provenance_id": _provenance_id(case_id),
                "acquisition_date": _DATES[-1],
                "case_id": case_id,
            }
        )
    return tuple(rows)


def _scale_value(row: dict[str, Any], scale_mode: str) -> float:
    value = float(row["delta_change"])
    if not is_shared_scale(scale_mode):
        return value
    converted = _to_reference(value, str(row.get("unit") or ""))
    return value if converted is None else converted


def _domain(case_id: str, scale_mode: str) -> tuple[float, float, str]:
    if is_shared_scale(scale_mode):
        values = [
            _scale_value(row, scale_mode)
            for other in CASE_IDS
            for row in _grid(other)
        ]
        if len(values) < 2 or min(values) == max(values):
            return (*FALLBACK_DOMAIN, _REFERENCE_UNIT)
        return min(values), max(values), _REFERENCE_UNIT
    values = [float(row["delta_change"]) for row in _grid(case_id)]
    unit = _display_unit(case_id)
    if len(values) < 2 or min(values) == max(values):
        return (*FALLBACK_DOMAIN, unit)
    return min(values), max(values), unit


def _paint(rows: list[dict[str, Any]], vmin: float, vmax: float, scale_mode: str) -> list[dict[str, Any]]:
    painted = []
    for row in rows:
        item = dict(row)
        item["color"] = color_for(_scale_value(row, scale_mode), vmin, vmax)
        painted.append(item)
    return painted


def _filtered(case_id: str, filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    """# TODO(rol1): move this to src/domain/filters.py. The UI must not reimplement it."""
    filters = filters or {}
    rows = list(_grid(case_id))
    land_use = filters.get("land_use")
    risk_level = filters.get("risk_level")
    coherence_min = filters.get("coherence_min")
    if land_use:
        rows = [row for row in rows if row["land_use"] == land_use]
    if risk_level:
        rows = [row for row in rows if row["risk_level"] == risk_level]
    if isinstance(coherence_min, (int, float)) and not isinstance(coherence_min, bool):
        rows = [row for row in rows if row["coherence"] >= float(coherence_min)]
    return rows


def get_map_layer_df(case_id: str, filters: dict[str, Any] | None = None) -> pd.DataFrame:
    """# TODO(rol1): map-ready frame. color and height are already on the row."""
    filters = filters or {}
    mode = str(filters.get("scale_mode") or "independent")
    vmin, vmax, _unit = _domain(case_id, mode)
    rows = _paint(_filtered(case_id, filters), vmin, vmax, mode)
    columns = [
        "cell_id",
        "lat",
        "lon",
        "delta_change",
        "coherence",
        "land_use",
        "risk_level",
        "unit",
        "color",
        "height",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns]


def get_color_scale(case_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """# TODO(rol1): domain actually used to paint color."""
    filters = filters or {}
    mode = str(filters.get("scale_mode") or "independent")
    vmin, vmax, unit = _domain(case_id, mode)
    values = [_scale_value(row, mode) for row in _filtered(case_id, filters)]
    return {
        "min": vmin,
        "max": vmax,
        "unit": unit,
        "saturated": values_saturate(values, vmin, vmax),
        "fallback": (vmin, vmax) == FALLBACK_DOMAIN,
        "mixed_units": len({_display_unit(other) for other in CASE_IDS}) > 1,
    }


def get_layer_counts(case_id: str, filters: dict[str, Any] | None = None) -> dict[str, int]:
    """# TODO(rol1): shown vs total. The UI must not aggregate."""
    return {
        "shown": len(_filtered(case_id, filters)),
        "total": len(_grid(case_id)),
    }


def get_view_state(case_id: str) -> dict[str, float] | None:
    """# TODO(rol1): latitude, longitude, zoom from the artefact, not from the UI."""
    origin = _reference(case_id)
    if origin is None:
        return None
    latitude, longitude = origin
    return {"latitude": latitude, "longitude": longitude, "zoom": _ZOOM}


def get_signal_status(case_id: str) -> dict[str, Any]:
    """# TODO(rol1): null-result flag. Limit is only echoed from meta.stats."""
    values = [abs(float(row["delta_change"])) for row in _grid(case_id)]
    above = any(value > 0 for value in values)
    limit, unit = None, None
    stats = load_meta(case_id).get("stats")
    if isinstance(stats, dict):
        for key, value in stats.items():
            if key.startswith("detection_limit") and isinstance(value, (int, float)):
                limit = value
                unit = "mm/year" if key.endswith("_mm_yr") else UNIT
                above = any(item > abs(float(value)) for item in values)
                break
    return {
        "above_limit": above,
        "detection_limit": limit,
        "detection_limit_unit": unit,
    }


def get_map_kpis(case_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """# TODO(rol1): aggregations belong in src/domain/metrics.py."""
    rows = _filtered(case_id, filters)
    provenance_id = _provenance_id(case_id)
    unit = _display_unit(case_id)
    if not rows:
        return [
            {"label": "delta_change max", "value": None, "unit": unit, "provenance_id": provenance_id},
            {"label": "coherence min", "value": None, "unit": None, "provenance_id": provenance_id},
        ]
    return [
        {
            "label": "delta_change max",
            "value": max(float(row["delta_change"]) for row in rows),
            "unit": unit,
            "provenance_id": provenance_id,
        },
        {
            "label": "coherence min",
            "value": min(float(row["coherence"]) for row in rows),
            "unit": None,
            "provenance_id": provenance_id,
        },
    ]


def _series_for(case_id: str, cell_id: str, delta: float) -> list[dict[str, Any]]:
    profile = sensor_profile("nisar", "mock")
    provenance_id = _provenance_id(case_id)
    return [
        {
            "case_id": case_id,
            "cell_id": cell_id,
            "date": date,
            "value": delta if _is_control(case_id) else delta * (index / max(len(_DATES) - 1, 1)),
            "unit": _display_unit(case_id),
            "sensor": profile.sensor,
            "product": "mock",
            "quality": "good",
            "provenance_id": provenance_id,
        }
        for index, date in enumerate(_DATES)
    ]


def get_cell_detail(case_id: str, cell_id: str) -> dict[str, Any] | None:
    """# TODO(rol1): one cell plus its series. No extra magnitude is computed here."""
    for row in _grid(case_id):
        if row["cell_id"] == cell_id:
            detail = dict(row)
            detail["series"] = _series_for(case_id, cell_id, float(row["delta_change"]))
            return detail
    return None


def load_timeseries(case_id: str, cell_id: str | None = None) -> pd.DataFrame:
    """# TODO(rol1): timeseries.csv. Optional cell_id avoids a second filter in the UI."""
    rows: list[dict[str, Any]] = []
    for row in _grid(case_id):
        if cell_id is not None and row["cell_id"] != cell_id:
            continue
        rows.extend(_series_for(case_id, row["cell_id"], float(row["delta_change"])))
    return pd.DataFrame(rows)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def get_series_kpis(case_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """# TODO(rol1): aggregations belong in src/domain/metrics.py."""
    rows = _filtered(case_id, filters)
    provenance_id = _provenance_id(case_id)
    unit = _display_unit(case_id)
    total = len(rows)
    velocities = []
    above = 0
    critical = 0
    coherent = 0
    coherence_floor = sensor_profile("nisar", "mock").coherence_min
    for row in rows:
        velocity = _velocity_mm_yr(float(row["delta_change"]), str(row["unit"]))
        if velocity is not None:
            velocities.append(velocity if unit == "mm/year" else float(row["delta_change"]))
            if abs(velocity) >= NULL_RESULT_MM_YR:
                above += 1
        if row["risk_level"] == "critico":
            critical += 1
        if float(row["coherence"]) >= coherence_floor:
            coherent += 1
    median = _median([float(row["delta_change"]) for row in rows])
    return [
        {
            "label": "Median velocity",
            "value": median,
            "unit": unit,
            "provenance_id": provenance_id,
            "note": "median of the filtered subset",
        },
        {
            "label": "Cells above threshold",
            "value": None if total == 0 else 100.0 * above / total,
            "unit": "%",
            "provenance_id": provenance_id,
            "note": f"threshold {NULL_RESULT_MM_YR:g} mm/year",
        },
        {
            "label": "Critical cells",
            "value": critical,
            "unit": "cells",
            "provenance_id": provenance_id,
            "note": "risk_level critico",
        },
        {
            "label": "Coherent coverage",
            "value": None if total == 0 else 100.0 * coherent / total,
            "unit": "%",
            "provenance_id": provenance_id,
            "note": f"coherence >= {coherence_floor:g}",
        },
    ]


def get_case_series(case_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """# TODO(rol1): aggregated case series. The value is the filtered median, repeated."""
    rows = _filtered(case_id, filters)
    median = _median([float(row["delta_change"]) for row in rows]) or 0.0
    unit = _display_unit(case_id)
    provenance_id = _provenance_id(case_id)
    count = max(len(_DATES) - 1, 1)
    return [
        {
            "date": date,
            "value": median if _is_control(case_id) else median * (index / count),
            "unit": unit,
            "provenance_id": provenance_id,
        }
        for index, date in enumerate(_DATES)
    ]


def get_land_use_bars(case_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """# TODO(rol1): median per present land_use. Does not fill absent categories."""
    grouped: dict[str, list[float]] = {}
    for row in _filtered(case_id, filters):
        grouped.setdefault(str(row["land_use"]), []).append(float(row["delta_change"]))
    unit = _display_unit(case_id)
    provenance_id = _provenance_id(case_id)
    return [
        {
            "land_use": land_use,
            "value": _median(values),
            "unit": unit,
            "provenance_id": provenance_id,
        }
        for land_use, values in grouped.items()
    ]


def get_scale_comparison(scale_mode: str = "shared") -> dict[str, Any]:
    """# TODO(rol1): shares on one reference axis. UI must not convert units."""
    units = {_display_unit(case_id) for case_id in CASE_IDS}
    peaks: list[tuple[str, float, float, str]] = []
    for case_id in CASE_IDS:
        rows = list(_grid(case_id))
        unit = _display_unit(case_id)
        declared = max((abs(float(row["delta_change"])) for row in rows), default=0.0)
        if is_shared_scale(scale_mode):
            converted = [
                item
                for item in (_to_reference(float(row["delta_change"]), unit) for row in rows)
                if item is not None
            ]
            reference_peak = max((abs(item) for item in converted), default=0.0)
        else:
            reference_peak = declared
        peaks.append((case_id, declared, reference_peak, unit))
    span = max((reference_peak for _case, _declared, reference_peak, _unit in peaks), default=0.0)
    cases = []
    for case_id, declared, reference_peak, unit in peaks:
        share = 0.0 if span == 0 else reference_peak / span
        peak = declared
        cases.append(
            {
                "id": case_id,
                "unit": unit,
                "peak": peak,
                "share": share,
                "provenance_id": _provenance_id(case_id),
            }
        )
    return {
        "reference_unit": _REFERENCE_UNIT if is_shared_scale(scale_mode) else None,
        "mixed_units": len(units) > 1,
        "cases": cases,
    }


def load_context_layers() -> dict[str, Any] | None:
    """# TODO(rol1): read context_layers.geojson only when Rol 3 has published it."""
    if not _CONTEXT.is_file():
        return None
    return {"path": str(_CONTEXT), "summary": {"delivered": True}}


def load_documented_limits() -> dict[str, Any]:
    """# TODO(rol3): replace with approved educational copy. Quotes README only."""
    if not _README.is_file():
        return {"section_8_caveat": None, "section_13": []}
    text = _README.read_text(encoding="utf-8")
    caveat = None
    paragraphs = [
        " ".join(line.strip() for line in block.splitlines() if line.strip())
        for block in re.split(r"\n\s*\n", text)
    ]
    gnss_re = re.compile(r"validat\w*\s+(?:the\s+convention\s+)?against\s+GNSS", re.IGNORECASE)
    for para in paragraphs:
        if gnss_re.search(para) and "Sign and heading conventions" in para:
            caveat = para
            break
    if caveat is None:
        for para in paragraphs:
            if gnss_re.search(para):
                caveat = para
                break

    start = text.find("## 13.")
    end = text.find("## 14.")
    numbered_re = re.compile(r"^(\d+)\.\s+(.*)$")
    items: list[str] = []
    if start != -1:
        block = text[start:end if end != -1 else None]
        current_parts: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            match = numbered_re.match(stripped)
            if match:
                if current_parts:
                    items.append(" ".join(current_parts))
                current_parts = [match.group(2)]
            elif current_parts:
                if not stripped or stripped.startswith(("---", "#", "|", "![")):
                    items.append(" ".join(current_parts))
                    current_parts = []
                else:
                    current_parts.append(stripped)
        if current_parts:
            items.append(" ".join(current_parts))
    return {"section_8_caveat": caveat, "section_13": items}
