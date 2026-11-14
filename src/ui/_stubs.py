"""Temporary loaders. # TODO(rol1): replace with src/data/loaders.py.

No scientific magnitudes, dates, hashes or display names are invented here.
Thresholds are copied from the frozen mock profile in src/domain/contracts.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.domain.contracts import (
    CASE_IDS,
    LAND_USE,
    RISK_LEVELS,
    SCHEMA_VERSION,
    UNIT,
    sensor_profile,
)

# TODO(rol1): this path check is not a loader. Parse config/study_cases.yaml.
_STUDY_CASES = Path(__file__).resolve().parents[2] / "config" / "study_cases.yaml"


def load_study_cases() -> list[dict[str, str]]:
    """# TODO(rol1): read config/study_cases.yaml and return id + label.

    The file is absent and PyYAML is not a project dependency. Until Rol 1
    publishes the loader, labels are the contract slugs — not translated names.
    """
    if _STUDY_CASES.is_file():
        # TODO(rol1): parse the yaml. Do not guess display names.
        return [{"id": case_id, "label": case_id} for case_id in CASE_IDS]
    return [{"id": case_id, "label": case_id} for case_id in CASE_IDS]


def load_meta(case_id: str) -> dict[str, Any]:
    """# TODO(rol1): load the case meta.json. This is an in-memory mock shell.

    Declares product ``mock`` so the UI can show the demonstration banner
    without a new contract field. Omits dates, stats and provenance_id.
    """
    profile = sensor_profile("nisar", "mock")
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "sensor": profile.sensor,
        "product": "mock",
        "unit": UNIT,
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
        },
    }


def load_land_use_options() -> tuple[str, ...]:
    """# TODO(rol1): distinct land_use values present in the selected grid."""
    return LAND_USE


def load_risk_levels() -> tuple[str, ...]:
    """# TODO(rol1): distinct risk_level values present in the selected grid."""
    return RISK_LEVELS
