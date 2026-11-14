"""Read `config/study_cases.yaml` into immutable case specs.

The YAML is the only place a study case is defined (owner: Rol 3). Nothing here re-declares a
contract value: sensor, product, thresholds and enums come from `src/domain/contracts.py`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

from pipeline.aoi import load_aoi
from src.domain import contracts as C

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "study_cases.yaml"


@dataclass(frozen=True)
class CoverageEllipse:
    """Cells whose centroid falls outside this ellipse are nodata (absent, never zero)."""

    center_lon: float
    center_lat: float
    semi_axes_km: tuple[float, float]
    rotation_deg: float


@dataclass(frozen=True)
class MockField:
    peak_velocity_mm_yr: float
    peak_lonlat: tuple[float, float]
    falloff_km: float
    noise_mm_yr: float
    coverage_ellipse: CoverageEllipse
    seasonal_amplitude_mm: float


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    name: str
    bbox: tuple[float, float, float, float]  # [W, S, E, N]
    reference_point: tuple[float, float]  # lon, lat
    revisit_days: int
    limitations: tuple[str, ...]
    null_result: bool
    detection_limit_mm_yr: float | None
    field: MockField


@dataclass(frozen=True)
class StudyCases:
    primary: str
    cases: dict[str, CaseSpec]
    dates: tuple[str, ...]
    h3_resolution: int

    def ordered(self, wanted: list[str] | None = None) -> list[CaseSpec]:
        ids = [c for c in self.cases if wanted is None or c in wanted] if wanted else list(self.cases)
        return [self.cases[c] for c in ids]


def _ellipse(raw: dict) -> CoverageEllipse:
    semi = tuple(float(v) for v in raw["semi_axes_km"])
    if len(semi) != 2:
        raise ValueError(f"coverage_ellipse.semi_axes_km must have two values, got {semi!r}")
    return CoverageEllipse(
        center_lon=float(raw["center"][0]),
        center_lat=float(raw["center"][1]),
        semi_axes_km=(semi[0], semi[1]),
        rotation_deg=float(raw.get("rotation_deg", 0.0)),
    )


def build_dates(processing: dict) -> tuple[str, ...]:
    """Canonical `meta.dates`: ISO, ascending, no duplicates, step = revisit interval."""
    start = dt.date.fromisoformat(str(processing["dates_start"]))
    step = int(processing["dates_step_days"])
    count = int(processing["dates_count"])
    dates = tuple((start + dt.timedelta(days=step * i)).isoformat() for i in range(count))
    if len(set(dates)) != count:
        raise ValueError("configured date grid contains duplicates")
    return dates


def load_study_cases(path: Path = CONFIG_PATH) -> StudyCases:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    processing = raw["processing"]
    dates = build_dates(processing)
    cases: dict[str, CaseSpec] = {}
    for case_id, entry in raw["cases"].items():
        if case_id not in C.CASE_IDS:
            raise ValueError(f"config declares case {case_id!r} outside the frozen CASE_IDS {C.CASE_IDS}")
        # The AOI loader is the single foot in the door for a QGIS drop-in: config/aoi/<case>.geojson
        # beats the yaml ring, which beats the bbox rectangle. The mock's cell set follows the
        # resulting bbox, so a smaller GeoJSON footprint immediately yields fewer synthetic cells.
        aoi = load_aoi(case_id, entry, aoi_dir=path.parent / "aoi")
        bbox = aoi.bbox
        mock = entry["mock"]
        field = MockField(
            peak_velocity_mm_yr=float(mock["peak_velocity_mm_yr"]),
            peak_lonlat=(float(mock["peak_lonlat"][0]), float(mock["peak_lonlat"][1])),
            falloff_km=float(mock["falloff_km"]),
            noise_mm_yr=float(mock["noise_mm_yr"]),
            coverage_ellipse=_ellipse(mock["coverage_ellipse"]),
            seasonal_amplitude_mm=float(mock["seasonal_amplitude_mm"]),
        )
        cases[case_id] = CaseSpec(
            case_id=case_id,
            name=str(entry["name"]),
            bbox=bbox,  # type: ignore[arg-type]
            reference_point=(float(entry["reference_point"][0]), float(entry["reference_point"][1])),
            revisit_days=int(entry["revisit_days"]),
            limitations=tuple(str(x) for x in entry.get("limitations", ())),
            null_result=bool(entry.get("null_result", False)),
            detection_limit_mm_yr=(
                float(entry["detection_limit_mm_yr"]) if entry.get("detection_limit_mm_yr") is not None else None
            ),
            field=field,
        )

    # The date grid must agree with the frozen revisit interval of the mocked sensor+product.
    profile = C.SENSOR_PROFILES["mock"]
    for case in cases.values():
        if case.revisit_days != profile.revisit_days:
            raise ValueError(
                f"{case.case_id}: config revisit_days {case.revisit_days} != frozen "
                f"{profile.revisit_days} for product {profile.product!r}"
            )
    if int(processing["dates_step_days"]) != profile.revisit_days:
        raise ValueError(
            f"processing.dates_step_days {processing['dates_step_days']} != frozen revisit "
            f"{profile.revisit_days}: meta.dates and revisit_days must not contradict"
        )

    primary = str(raw.get("primary") or next(iter(cases)))
    if primary not in cases:
        raise ValueError(f"primary {primary!r} is not one of the configured cases {sorted(cases)}")
    return StudyCases(
        primary=primary,
        cases=cases,
        dates=dates,
        h3_resolution=int(processing["h3_resolution"]),
    )
