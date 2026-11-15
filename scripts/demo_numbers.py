#!/usr/bin/env python
"""Build the demo number card from the artefacts, never from memory or a slide.

The team's video is 20 s problem / 90 s demo / 30 s architecture. Every number the presenter
says has to come from ``data/processed/{case}/meta.json`` + ``nisar_grid.parquet``, and every
"do not say this" rule has to come from the documented limitations. This script is the single
place that turns those two files into the sentences, so a stale figure in a pitch deck cannot
survive a rerun.

    ./scripts/python.sh scripts/demo_numbers.py              # plain-text card
    ./scripts/python.sh scripts/demo_numbers.py --json       # same content, structured
    ./scripts/python.sh scripts/demo_numbers.py --case malaga

What is derived (everything else is an input)
---------------------------------------------
* cells, ``median_velocity_mm_yr``, p05, p95, spread, velocity min/max, coherence min/max are
  recomputed from the parquet and only *checked* against ``meta.stats`` (a disagreement skips the
  case with a printed note instead of silently trusting either side).
* the v1.1 seasonal trio — ``median_seasonal_amp_mm``, ``median_ratio_R`` and the ``dominant_regime``
  mode — is recomputed the same way and checked against ``meta.stats``; the amplitude is a *fit*
  of each cell's own series, never a config value, and ``_seasonal_split`` prints the span that
  licenses it (the 0.75-cycle-per-harmonic gate of ``pipeline/science/transforms.py``).
* date window, ``n_dates``, ``revisit_days``, ``provenance_id`` and ``limitations[]`` come from
  ``meta.json`` verbatim.
* the contrast ratio is ``max(p95-p05) / (p95-p05 of the declared null case)``; the shared legend
  is ``ceil(max|velocity| / 25 mm/yr) * 25 mm/yr`` — a presentation granularity, not a measurement.
* the phase→mm scale check is recomputed through the library:
  ``nisar_gunw.phase_to_los_displacement_mm`` then ``transforms.vertical_from_los``.
* coverage is printed against **two** denominators at the resolution of the grid's own
  ``cell_id`` strings: the authoritative one, ``|grid cells ∩ AOI H3 cells| / |AOI H3 cells|``,
  where the AOI cell set is ``pipeline.process_case.aoi_cells`` (the AOI polygon, centre
  containment — exactly the denominator the pipeline's ``COVERAGE_WARNING_FRACTION`` warns on),
  and ``|grid cells ∩ bbox H3 cells| / |bbox H3 cells|``, which is the *envelope* of the AOI and
  is **not** the threshold denominator. Both are published because the bbox figure used to be
  printed alone and was then quoted as if it were the AOI coverage.
* the mock warning is triggered by ``product == "mock"`` in the artefacts, never by a filename.

The only literals below are formatting constants (2 decimals, 25 mm/yr legend granularity, 60-line
card budget) and the documented scale-check geometry: the 39° incidence / 192° heading used in
``docs/nisar-gunw-layout.md`` §4 when ``meta.json`` carries no geometry. Those are **inputs, not
measurements**, and are labelled as such where they appear. Wavelength comes from the repository's
documented ``DEFAULT_WAVELENGTH_M``; no physical number is typed here.

Reading rules
-------------
* Only ``data/processed/`` is read for the measurements, plus ``config/`` through
  ``pipeline.process_case.aoi_cells`` / ``load_case_config`` for the authoritative AOI cell set
  (``--processed`` exists so tests can point at a copy).
* No clock, no RNG, no sampling, no network: two runs on the same tree produce identical text.
* A case whose artefacts are missing or internally inconsistent is skipped with a note and the
  run still exits 0. Notes go to stderr when ``--json`` keeps stdout parseable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Sequence

import h3
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python scripts/demo_numbers.py` puts scripts/, not the repo
    sys.path.insert(0, str(ROOT))

from pipeline.ingest import nisar_gunw as G  # noqa: E402
from pipeline.process_case import (  # noqa: E402
    COVERAGE_WARNING_FRACTION,
    DEFAULT_WAVELENGTH_M,
    aoi_cells,
    load_case_config,
)
from pipeline.science import transforms as V  # noqa: E402
from src.domain import contracts as C  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Formatting constants and documented inputs. None of these is a measurement of any city.
# ---------------------------------------------------------------------------------------------

#: Card digits. Two decimals is the round-tripping display of the 3-dp values in meta.stats.
DECIMALS = 2

#: Shared-legend granularity in mm/yr: rounding up to the next 25 is a presentation decision from
#: docs/handoff-mock.md ("symmetric scale about 0 at ±425 mm/yr"), not a measured quantity.
LEGEND_STEP_MM_YR = 25.0

#: The card must stay terminal-friendly. Exceeding this prints a stderr note, never a truncation.
MAX_CARD_LINES = 60

#: Tolerance when checking meta.stats against the parquet. meta.stats rounds to 3 decimals, so
#: 0.05 mm/yr is a *display* tolerance (the max rounding error is 0.0005); it is not science.
STATS_TOLERANCE_MM_YR = 0.05

#: Same idea for the dimensionless `median_ratio_R`, which the generator rounds to 6 decimals.
STATS_TOLERANCE_DIMENSIONLESS = 1e-4

#: Percentile levels the card publishes (the *definition* of p05/p95, not a measurement).
P05_QUANTILE = 0.05
P95_QUANTILE = 0.95

#: Phase units for the scale check: 1 radian and one fringe (2π rad) of unwrapped phase. These
#: are arithmetic definitions of the unit, not measurements; the conversion itself is the library.
PHASE_ONE_RADIAN = 1.0
PHASE_ONE_FRINGE_RAD = 2.0 * math.pi

#: Calendar months per year, used only to restate a day span for the presenter.
MONTHS_PER_YEAR = 12.0

#: Documented scale-check geometry from docs/nisar-gunw-layout.md §4, used only when meta.json has
#: no incidence/heading. INPUTS, NOT MEASUREMENTS: the doc's scale table is computed at θ = 39°,
#: heading 192°. A real granule must supply its own geometry (process_case refuses otherwise).
DOC_INCIDENCE_DEG = 39.0  # input, not a measurement
DOC_HEADING_DEG = 192.0  # input, not a measurement

GRID_FILE = "nisar_grid.parquet"
META_FILE = "meta.json"
REQUIRED_GRID_COLUMNS = (
    "cell_id",
    "velocity_mm_yr",
    "seasonal_amp_mm",
    "ratio_R",
    "regime",
    "coherence",
    "case_id",
    "sensor",
    "product",
    "provenance_id",
)
REQUIRED_CASES = ("meta.json", "nisar_grid.parquet")


class CaseSkipped(Exception):
    """A case is unreadable or inconsistent. Caught by main and printed, never raised further."""


@dataclass(frozen=True)
class CaseNumbers:
    """One case, every field derived from its artefacts (no defaults, no guesses)."""

    case_id: str
    name: str
    product: str
    sensor: str
    cells: int
    median_velocity_mm_yr: float
    p05_velocity_mm_yr: float
    p95_velocity_mm_yr: float
    spread_mm_yr: float
    velocity_min_mm_yr: float
    velocity_max_mm_yr: float
    max_abs_velocity_mm_yr: float
    #: v1.1: the fitted seasonal trio, recomputed from the grid and checked against meta.stats.
    median_seasonal_amp_mm: float
    median_ratio_R: float
    dominant_regime: str
    coherence_min: float
    coherence_max: float
    date_first: str
    date_last: str
    window_days: int
    n_dates: int
    revisit_days: int | None
    provenance_id: str
    limitations: tuple[str, ...]
    detection_limit_mm_yr: float | None
    posting_m: int | None
    h3_resolution: int
    #: Authoritative denominator: ``pipeline.process_case.aoi_cells`` (the AOI polygon).
    aoi_cells: int
    covered_in_aoi: int
    aoi_coverage_fraction: float
    #: Envelope denominator: the cells inside ``meta.bbox``. Published, never thresholded here.
    bbox_cells: int
    covered_in_bbox: int
    bbox_coverage_fraction: float
    #: Grid cells that fall outside the AOI polygon (data the pipeline would drop).
    covered_outside_aoi: int
    aoi_source: str
    incidence_deg: float
    incidence_source: str
    heading_deg: float
    heading_source: str

    @property
    def n_limitations(self) -> int:
        return len(self.limitations)

    def as_json(self) -> dict[str, Any]:
        """Structured form. Raw floats: display rounding happens once, in :func:`render_text`."""
        return {
            "case_id": self.case_id,
            "name": self.name,
            "product": self.product,
            "sensor": self.sensor,
            "cells": self.cells,
            "median_velocity_mm_yr": self.median_velocity_mm_yr,
            "p05_velocity_mm_yr": self.p05_velocity_mm_yr,
            "p95_velocity_mm_yr": self.p95_velocity_mm_yr,
            # Short aliases for consumers that mirror the card's labels (same derived values).
            "p05": self.p05_velocity_mm_yr,
            "p95": self.p95_velocity_mm_yr,
            "spread_mm_yr": self.spread_mm_yr,
            "velocity_min_mm_yr": self.velocity_min_mm_yr,
            "velocity_max_mm_yr": self.velocity_max_mm_yr,
            "max_abs_velocity_mm_yr": self.max_abs_velocity_mm_yr,
            "median_seasonal_amp_mm": self.median_seasonal_amp_mm,
            "median_ratio_R": self.median_ratio_R,
            "dominant_regime": self.dominant_regime,
            "coherence_min": self.coherence_min,
            "coherence_max": self.coherence_max,
            "date_first": self.date_first,
            "date_last": self.date_last,
            "window_days": self.window_days,
            "n_dates": self.n_dates,
            "revisit_days": self.revisit_days,
            "provenance_id": self.provenance_id,
            "n_limitations": self.n_limitations,
            "limitations": list(self.limitations),
            "detection_limit_mm_yr": self.detection_limit_mm_yr,
            "posting_m": self.posting_m,
            "coverage": {
                "h3_resolution": self.h3_resolution,
                #: The denominator the 50 % warning is measured on. Named so no consumer can
                #: mistake the bbox figure for it again.
                "authoritative_denominator": "aoi",
                "warning_threshold": COVERAGE_WARNING_FRACTION,
                "aoi": {
                    "source": self.aoi_source,
                    "cells": self.aoi_cells,
                    "covered": self.covered_in_aoi,
                    "fraction": self.aoi_coverage_fraction,
                    "below_warning_threshold": self.aoi_coverage_fraction
                    < COVERAGE_WARNING_FRACTION,
                },
                "bbox": {
                    "source": "meta.bbox envelope (config/study_cases.yaml bbox)",
                    "cells": self.bbox_cells,
                    "covered": self.covered_in_bbox,
                    "fraction": self.bbox_coverage_fraction,
                    "below_warning_threshold": self.bbox_coverage_fraction
                    < COVERAGE_WARNING_FRACTION,
                },
                "covered_outside_aoi": self.covered_outside_aoi,
            },
            "geometry": {
                "incidence_deg": self.incidence_deg,
                "incidence_source": self.incidence_source,
                "heading_deg": self.heading_deg,
                "heading_source": self.heading_source,
            },
        }


# ---------------------------------------------------------------------------------------------
# Loading. Every branch either returns a fully-derived CaseNumbers or raises CaseSkipped.
# ---------------------------------------------------------------------------------------------


def _finite(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _first_angle(meta: dict[str, Any], keys: Sequence[str]) -> tuple[float, str] | None:
    """Find an angle in meta, meta.provenance or meta.geometry; return (value, where it came from)."""
    containers: list[tuple[str, dict[str, Any]]] = [("meta.json", meta)]
    for name in ("provenance", "geometry"):
        value = meta.get(name)
        if isinstance(value, dict):
            containers.append((f"meta.json:{name}", value))
    for label, container in containers:
        for key in keys:
            number = _finite(container.get(key))
            if number is not None:
                return number, f"{label}.{key}"
    return None


def _case_geometry(meta: dict[str, Any]) -> tuple[float, str, float, str]:
    """Per-case geometry, or the documented scale-check inputs when metadata has none."""
    incidence = _first_angle(meta, ("incidence_deg", "incidence_angle_deg", "incidence"))
    if incidence is None or not (0.0 <= incidence[0] < 90.0):
        incidence = (DOC_INCIDENCE_DEG, "documented input docs/nisar-gunw-layout.md §4")

    heading = _first_angle(meta, ("heading_deg", "heading", "platform_heading_deg"))
    if heading is None:
        heading = (DOC_HEADING_DEG, "documented input docs/nisar-gunw-layout.md §4")
    return incidence[0], incidence[1], heading[0], heading[1]


def _bbox_polygon(bbox: Sequence[float]) -> dict[str, Any]:
    w, s, e, n = (float(v) for v in bbox)
    return {
        "type": "Polygon",
        "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
    }


def load_case(case_dir: pathlib.Path) -> CaseNumbers:
    """Read one case and derive every published number, or raise CaseSkipped with a reason."""
    meta_path = case_dir / META_FILE
    grid_path = case_dir / GRID_FILE
    for name in REQUIRED_CASES:
        if not (case_dir / name).is_file():
            raise CaseSkipped(f"missing {name}")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseSkipped(f"unreadable meta.json ({exc.__class__.__name__})") from None
    if not isinstance(meta, dict):
        raise CaseSkipped("meta.json is not a JSON object")

    try:
        grid = pd.read_parquet(grid_path)
    except Exception as exc:  # noqa: BLE001 - pyarrow raises several unrelated types
        raise CaseSkipped(f"unreadable nisar_grid.parquet ({exc.__class__.__name__})") from None
    if grid.empty:
        raise CaseSkipped("nisar_grid.parquet has no rows")

    missing = [column for column in REQUIRED_GRID_COLUMNS if column not in grid.columns]
    if missing:
        raise CaseSkipped(f"nisar_grid.parquet is missing column(s) {missing}")

    case_id = str(meta.get("case_id") or "").strip()
    if not case_id:
        raise CaseSkipped("meta.case_id is missing")
    if case_id != case_dir.name:
        raise CaseSkipped(
            f"directory {case_dir.name!r} does not match meta.case_id {case_id!r}"
        )
    grid_cases = {str(value) for value in grid["case_id"].dropna().unique()}
    if grid_cases != {case_id}:
        raise CaseSkipped(
            f"grid case_id {sorted(grid_cases)} does not match meta.case_id {case_id!r}"
        )

    product = str(meta.get("product") or "").strip()
    sensor = str(meta.get("sensor") or "").strip()
    if product not in C.PRODUCTS:
        raise CaseSkipped(f"meta.product {product!r} is not in contracts.PRODUCTS")
    if sensor not in C.SENSORS:
        raise CaseSkipped(f"meta.sensor {sensor!r} is not in contracts.SENSORS")
    grid_products = {str(value) for value in grid["product"].dropna().unique()}
    grid_sensors = {str(value) for value in grid["sensor"].dropna().unique()}
    if grid_products != {product}:
        raise CaseSkipped(
            f"grid product {sorted(grid_products)} does not match meta.product {product!r}"
        )
    if grid_sensors != {sensor}:
        raise CaseSkipped(
            f"grid sensor {sorted(grid_sensors)} does not match meta.sensor {sensor!r}"
        )
    try:
        profile = C.sensor_profile(sensor, product)
    except C.ContractError as exc:
        raise CaseSkipped(str(exc)) from None

    dates = meta.get("dates")
    if not isinstance(dates, list) or not dates:
        raise CaseSkipped("meta.dates is missing or empty")
    try:
        parsed_dates = [dt.date.fromisoformat(str(value)) for value in dates]
    except ValueError:
        raise CaseSkipped("meta.dates contains a non-ISO date") from None
    if parsed_dates != sorted(parsed_dates):
        raise CaseSkipped("meta.dates is not sorted ascending")
    window_days = (parsed_dates[-1] - parsed_dates[0]).days
    if window_days <= 0:
        raise CaseSkipped("meta.dates spans zero days")

    velocity = pd.to_numeric(grid["velocity_mm_yr"], errors="coerce")
    seasonal_amp = pd.to_numeric(grid["seasonal_amp_mm"], errors="coerce")
    ratio_R = pd.to_numeric(grid["ratio_R"], errors="coerce")
    coherence = pd.to_numeric(grid["coherence"], errors="coerce")
    if len(velocity) == 0 or int(velocity.notna().sum()) != len(velocity):
        raise CaseSkipped("nisar_grid.parquet has null/non-numeric velocity_mm_yr")
    if int(seasonal_amp.notna().sum()) != len(seasonal_amp):
        raise CaseSkipped("nisar_grid.parquet has null/non-numeric seasonal_amp_mm")
    if not (seasonal_amp >= 0.0).all():
        raise CaseSkipped("seasonal_amp_mm is negative; a fitted amplitude cannot be")
    if int(ratio_R.notna().sum()) != len(ratio_R):
        raise CaseSkipped("nisar_grid.parquet has null/non-numeric ratio_R")
    grid_regimes = {str(value) for value in grid["regime"].dropna().unique()}
    if not grid_regimes <= set(C.REGIMES):
        raise CaseSkipped(
            f"grid regime {sorted(grid_regimes - set(C.REGIMES))} is outside contracts.REGIMES"
        )
    if int(coherence.notna().sum()) == 0:
        raise CaseSkipped("nisar_grid.parquet has no numeric coherence")
    if not (coherence.dropna().between(0.0, 1.0).all()):
        raise CaseSkipped("coherence values fall outside [0, 1]")

    cells = int(len(grid))
    median = float(velocity.median())
    p05 = float(velocity.quantile(P05_QUANTILE))
    p95 = float(velocity.quantile(P95_QUANTILE))
    velocity_min = float(velocity.min())
    velocity_max = float(velocity.max())
    max_abs = float(velocity.abs().max())
    median_seasonal_amp = float(seasonal_amp.median())
    median_ratio_R = float(ratio_R.median())
    regime_counts = grid["regime"].astype(str).value_counts()
    dominant_regime = sorted(
        regime_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))
    )[0][0]
    coherence_min = float(coherence.dropna().min())
    coherence_max = float(coherence.dropna().max())

    stats = meta.get("stats")
    if not isinstance(stats, dict):
        raise CaseSkipped("meta.stats is missing; declared statistics cannot be checked")
    declared_cells = _finite(stats.get("n_cells"))
    if declared_cells is None or int(declared_cells) != cells:
        raise CaseSkipped(
            f"meta.stats.n_cells={stats.get('n_cells')!r} != parquet rows {cells}"
        )
    for key, computed, tolerance in (
        ("median_velocity_mm_yr", median, STATS_TOLERANCE_MM_YR),
        ("median_seasonal_amp_mm", median_seasonal_amp, STATS_TOLERANCE_MM_YR),
        ("median_ratio_R", median_ratio_R, STATS_TOLERANCE_DIMENSIONLESS),
        ("p05_velocity_mm_yr", p05, STATS_TOLERANCE_MM_YR),
        ("p95_velocity_mm_yr", p95, STATS_TOLERANCE_MM_YR),
    ):
        declared = _finite(stats.get(key))
        if declared is None:
            raise CaseSkipped(f"meta.stats.{key} is missing or non-numeric")
        if abs(declared - computed) > tolerance:
            raise CaseSkipped(
                f"meta.stats.{key}={declared:.6f} disagrees with parquet {computed:.6f} "
                f"(> tolerance {tolerance:g})"
            )
    declared_regime = stats.get("dominant_regime")
    if str(declared_regime) != dominant_regime:
        raise CaseSkipped(
            f"meta.stats.dominant_regime={declared_regime!r} disagrees with the parquet's most "
            f"frequent regime {dominant_regime!r}"
        )
    declared_dates = _finite(stats.get("n_dates"))
    if declared_dates is not None and int(declared_dates) != len(dates):
        raise CaseSkipped(
            f"meta.stats.n_dates={int(declared_dates)} != len(meta.dates)={len(dates)}"
        )

    provenance = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
    provenance_id = str(provenance.get("provenance_id") or "").strip()
    grid_provenance = {str(value) for value in grid["provenance_id"].dropna().unique()}
    if not provenance_id and len(grid_provenance) == 1:
        provenance_id = next(iter(grid_provenance))
    if not provenance_id:
        raise CaseSkipped("provenance_id is missing from meta.json and the grid")
    if not C.PROVENANCE_ID_RE.match(provenance_id):
        raise CaseSkipped(f"provenance_id {provenance_id!r} does not match the frozen format")
    if grid_provenance and grid_provenance != {provenance_id}:
        raise CaseSkipped(
            f"grid provenance_id {sorted(grid_provenance)} != meta provenance_id "
            f"{provenance_id!r}"
        )

    limitations = meta.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        raise CaseSkipped("meta.limitations is missing or empty")
    limitations_tuple = tuple(str(value) for value in limitations)

    revisit = _finite(meta.get("revisit_days"))
    revisit_days = int(revisit) if revisit is not None else None

    bbox = meta.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise CaseSkipped("meta.bbox is missing or not [W, S, E, N]")
    bbox_floats = tuple(_finite(value) for value in bbox)
    if any(value is None for value in bbox_floats):
        raise CaseSkipped("meta.bbox contains a non-numeric value")
    w, s, e, n = (float(value) for value in bbox_floats)  # type: ignore[arg-type]
    if not (w < e and s < n):
        raise CaseSkipped("meta.bbox is not ordered [W, S, E, N]")

    cell_ids = [str(value) for value in grid["cell_id"]]
    try:
        resolutions = {h3.get_resolution(cell) for cell in cell_ids}
    except Exception as exc:  # noqa: BLE001 - h3 raises ValueError for malformed ids
        raise CaseSkipped(f"invalid H3 cell_id ({exc.__class__.__name__})") from None
    if len(resolutions) != 1:
        raise CaseSkipped(f"mixed H3 resolutions {sorted(resolutions)} in the grid")
    resolution = resolutions.pop()

    # Authoritative denominator: the very function the pipeline warns on, so the card and
    # ``process_case.py`` can never disagree about what "the AOI" means (same ring, same
    # containment). It reads config/, never the artefacts.
    try:
        case_config = load_case_config(case_id)
        aoi_cell_set = set(aoi_cells(case_id))
    except Exception as exc:  # noqa: BLE001 - missing config, unreadable ring, h3 refusal
        raise CaseSkipped(
            f"cannot derive the AOI H3 cells ({exc.__class__.__name__}: {exc})"
        ) from None
    if case_config.h3_resolution != resolution:
        raise CaseSkipped(
            f"config h3_resolution={case_config.h3_resolution} != grid cell resolution "
            f"{resolution}; the AOI denominator would not match the cells"
        )
    if not aoi_cell_set:
        raise CaseSkipped(f"the AOI polygon {case_config.aoi_source} yields no H3 cells")

    try:
        bbox_cell_set = set(h3.geo_to_cells(_bbox_polygon((w, s, e, n)), resolution))
    except Exception as exc:  # noqa: BLE001
        raise CaseSkipped(f"cannot derive bbox H3 cells ({exc.__class__.__name__})") from None
    grid_cells = set(cell_ids)
    covered_in_aoi = len(grid_cells & aoi_cell_set)
    covered_in_bbox = len(grid_cells & bbox_cell_set)
    covered_outside = len(grid_cells - aoi_cell_set)

    detection_limit = _finite(stats.get("detection_limit_mm_yr"))
    incidence, incidence_source, heading, heading_source = _case_geometry(meta)

    return CaseNumbers(
        case_id=case_id,
        name=str(meta.get("name") or case_id),
        product=product,
        sensor=sensor,
        cells=cells,
        median_velocity_mm_yr=median,
        p05_velocity_mm_yr=p05,
        p95_velocity_mm_yr=p95,
        spread_mm_yr=p95 - p05,
        velocity_min_mm_yr=velocity_min,
        velocity_max_mm_yr=velocity_max,
        max_abs_velocity_mm_yr=max_abs,
        median_seasonal_amp_mm=median_seasonal_amp,
        median_ratio_R=median_ratio_R,
        dominant_regime=dominant_regime,
        coherence_min=coherence_min,
        coherence_max=coherence_max,
        date_first=str(dates[0]),
        date_last=str(dates[-1]),
        window_days=window_days,
        n_dates=len(dates),
        revisit_days=revisit_days,
        provenance_id=provenance_id,
        limitations=limitations_tuple,
        detection_limit_mm_yr=detection_limit,
        posting_m=profile.posting_m,
        h3_resolution=resolution,
        aoi_cells=len(aoi_cell_set),
        covered_in_aoi=covered_in_aoi,
        aoi_coverage_fraction=covered_in_aoi / len(aoi_cell_set),
        bbox_cells=len(bbox_cell_set),
        covered_in_bbox=covered_in_bbox,
        bbox_coverage_fraction=(
            covered_in_bbox / len(bbox_cell_set) if bbox_cell_set else 0.0
        ),
        covered_outside_aoi=covered_outside,
        aoi_source=case_config.aoi_source,
        incidence_deg=incidence,
        incidence_source=incidence_source,
        heading_deg=heading,
        heading_source=heading_source,
    )


# ---------------------------------------------------------------------------------------------
# Derived blocks
# ---------------------------------------------------------------------------------------------


def _contrast(cases: Sequence[CaseNumbers]) -> dict[str, Any]:
    """Spread per case, strongest/null ratio, and the shared-legend recommendation."""
    spreads = {case.case_id: case.spread_mm_yr for case in cases}
    strongest = max(cases, key=lambda case: (case.spread_mm_yr, case.case_id))
    null_cases = [case for case in cases if case.detection_limit_mm_yr is not None]
    null_case = min(null_cases, key=lambda case: (case.spread_mm_yr, case.case_id)) if null_cases else None
    ratio = (
        strongest.spread_mm_yr / null_case.spread_mm_yr
        if null_case is not None and null_case.spread_mm_yr > 0.0
        else None
    )
    max_abs = max(case.max_abs_velocity_mm_yr for case in cases)
    legend = math.ceil(max_abs / LEGEND_STEP_MM_YR) * LEGEND_STEP_MM_YR
    p05_min = min(case.p05_velocity_mm_yr for case in cases)
    velocity_min = min(case.velocity_min_mm_yr for case in cases)
    return {
        "spreads_mm_yr": spreads,
        "strongest_case": strongest.case_id,
        "strongest_spread_mm_yr": strongest.spread_mm_yr,
        "null_case": null_case.case_id if null_case is not None else None,
        "null_spread_mm_yr": null_case.spread_mm_yr if null_case is not None else None,
        "ratio": ratio,
        "shared_scale_mm_yr": legend,
        "shared_legend_mm_yr": legend,
        "legend_step_mm_yr": LEGEND_STEP_MM_YR,
        "max_abs_velocity_mm_yr": max_abs,
        "p05_min_mm_yr": p05_min,
        "velocity_min_mm_yr": velocity_min,
    }


def _scale_check(cases: Sequence[CaseNumbers]) -> list[dict[str, Any]]:
    """1 radian and one fringe, converted by the library (never typed)."""
    wavelength = float(DEFAULT_WAVELENGTH_M)
    groups: dict[tuple[float, float], list[str]] = {}
    for case in cases:
        groups.setdefault((case.incidence_deg, case.heading_deg), []).append(case.case_id)

    blocks: list[dict[str, Any]] = []
    for (incidence, heading), case_ids in sorted(groups.items()):
        los_rad = float(
            G.phase_to_los_displacement_mm(PHASE_ONE_RADIAN, wavelength, sign=1.0)
        )
        vertical_rad = float(V.vertical_from_los(los_rad, incidence, heading))
        los_fringe = float(
            G.phase_to_los_displacement_mm(PHASE_ONE_FRINGE_RAD, wavelength, sign=1.0)
        )
        vertical_fringe = float(V.vertical_from_los(los_fringe, incidence, heading))
        source = next(
            (case.incidence_source for case in cases if case.incidence_deg == incidence),
            "documented input",
        )
        heading_source = next(
            (case.heading_source for case in cases if case.heading_deg == heading),
            "documented input",
        )
        blocks.append(
            {
                "cases": sorted(case_ids),
                "wavelength_m": wavelength,
                "wavelength_source": (
                    "documented L-band input from pipeline.process_case.DEFAULT_WAVELENGTH_M, "
                    "not a measurement"
                ),
                "incidence_deg": incidence,
                "incidence_source": source,
                "heading_deg": heading,
                "heading_source": heading_source,
                "one_radian_los_mm": los_rad,
                "one_radian_vertical_mm": vertical_rad,
                "one_fringe_los_mm": los_fringe,
                "one_fringe_vertical_mm": vertical_fringe,
                "formula_los": "|d_LOS| = lambda / (4*pi) * |phi|",
                "formula_vertical": "v_up = d_LOS / cos(theta)  [v_north = 0, v_east = 0]",
            }
        )
    return blocks


def _null_results(cases: Sequence[CaseNumbers]) -> list[dict[str, Any]]:
    """Every case that publishes a detection bound; the bound is the result."""
    return [
        {
            "case_id": case.case_id,
            "detection_limit_mm_yr": case.detection_limit_mm_yr,
            "median_velocity_mm_yr": case.median_velocity_mm_yr,
            "sentence": "below the detection floor — the bound is the result",
        }
        for case in cases
        if case.detection_limit_mm_yr is not None
    ]


def _seasonal_split(cases: Sequence[CaseNumbers]) -> list[dict[str, Any]]:
    """The fitted trend/seasonal split of every case, with the gate evidence behind it.

    Replaces the v1.0 "do not publish seasonal_amp_mm" branch: on the two-year window the term is
    identifiable, so the honest output is the number *and* the span/conditioning that licenses it
    (``meta.stats.annual_coverage`` / ``design_condition``, written by the fit itself).
    """
    blocks: list[dict[str, Any]] = []
    for case in cases:
        cycles = case.window_days / V.DAYS_PER_YEAR
        gate = V.MIN_SEASONAL_COVERAGE_PER_HARMONIC
        blocks.append(
            {
                "case_id": case.case_id,
                "window_days": case.window_days,
                "window_months": case.window_days / (V.DAYS_PER_YEAR / MONTHS_PER_YEAR),
                "annual_coverage": cycles,
                "coverage_gate": gate,
                "seasonal_identifiable": cycles >= gate,
                "median_seasonal_amp_mm": case.median_seasonal_amp_mm,
                "median_ratio_R": case.median_ratio_R,
                "dominant_regime": case.dominant_regime,
            }
        )
    return blocks


def _do_not_claim(cases: Sequence[CaseNumbers]) -> list[str]:
    """Build the 'do not claim' list from the limitations plus the derived evidence."""
    items: list[str] = []
    lowered = [limitation.lower() for case in cases for limitation in case.limitations]

    if any(("building scale" in text) or ("posting" in text) for text in lowered):
        postings = sorted({case.posting_m for case in cases if case.posting_m is not None})
        posting_text = ", ".join(f"{posting:g} m" for posting in postings) or "unknown"
        resolution = cases[0].h3_resolution
        edge_m = float(h3.average_hexagon_edge_length(resolution, unit="m"))
        items.append(
            f"no building-scale claim: posting is {posting_text} and one H3 res-{resolution} "
            f"cell is ~{edge_m:.0f} m across; the cell is the map unit, not a building."
        )

    seasonal_cases = [
        case
        for case in cases
        if case.window_days / V.DAYS_PER_YEAR < V.MIN_SEASONAL_COVERAGE_PER_HARMONIC
    ]
    if seasonal_cases:
        gate_days = V.MIN_SEASONAL_COVERAGE_PER_HARMONIC * V.DAYS_PER_YEAR
        for case in seasonal_cases:
            items.append(
                f"do not quote a seasonal amplitude for {case.case_id}: the window is "
                f"{case.window_days} d, below the {gate_days:.0f} d "
                f"({V.MIN_SEASONAL_COVERAGE_PER_HARMONIC:.2f} yr) identifiability gate in "
                "pipeline/science/transforms.py."
            )
    else:
        # The window clears the gate: the amplitude IS publishable, and what must not be claimed is
        # that it is a measurement of the ground rather than a fit of a synthetic series.
        highest = max(cases, key=lambda case: case.median_seasonal_amp_mm)
        items.append(
            f"do not call the seasonal amplitude a measured physical cycle: it is the fitted "
            f"first-harmonic amplitude of these series (median {highest.median_seasonal_amp_mm:.2f} "
            f"mm for {highest.case_id}); the mock is noise-free in time, so the fit recovers the "
            "injected value exactly and a real stack would not."
        )


    # The threshold is measured on the AOI H3 cells (the pipeline's denominator), never on the
    # bbox envelope: a city can fill its envelope and still cover less than half of itself.
    low = [case for case in cases if case.aoi_coverage_fraction < COVERAGE_WARNING_FRACTION]
    if low:
        coverage_text = ", ".join(
            f"{case.case_id} ({case.aoi_coverage_fraction:.2%} of {case.aoi_cells:,} AOI cells)"
            for case in low
        )
        items.append(
            f"no city-wide claim for {coverage_text}: below the "
            f"{COVERAGE_WARNING_FRACTION:.0%} coverage threshold in docs/data-source-decision.md, "
            "measured against the AOI H3 cells (pipeline aoi_cells), not the bbox envelope."
        )

    null_cases = [case for case in cases if case.detection_limit_mm_yr is not None]
    for case in null_cases:
        items.append(
            f"do not call {case.case_id} 'stable' or 'no motion': its median "
            f"{case.median_velocity_mm_yr:.{DECIMALS}f} mm/yr sits below the "
            f"{case.detection_limit_mm_yr:.{DECIMALS}f} mm/yr detection floor — publish the "
            "bound as the result."
        )
    return items


def _skeleton(
    cases: Sequence[CaseNumbers],
    contrast: dict[str, Any],
    null_results: Sequence[dict[str, Any]],
    processed_root: pathlib.Path,
) -> list[str]:
    """The 90-second demo skeleton with the numbers already in the sentences."""
    if not cases:
        return ["no cases survived; nothing to present — fix the artefacts first."]

    strongest = next(
        case for case in cases if case.case_id == contrast["strongest_case"]
    )
    mechanism = next(
        (
            limitation
            for limitation in strongest.limitations
            if any(
                token in limitation.lower()
                for token in ("clay", "karst", "compact", "inelastic", "elastic", "compress")
            )
        ),
        strongest.limitations[0],
    )
    date_counts = sorted({case.n_dates for case in cases})
    windows = sorted({case.window_days for case in cases})
    products = sorted({case.product for case in cases})
    date_span = (
        f"{date_counts[0]} dates"
        if date_counts[0] == date_counts[-1]
        else f"{date_counts[0]}-{date_counts[-1]} dates"
    )
    window_span = (
        f"{windows[0]} d" if windows[0] == windows[-1] else f"{windows[0]}-{windows[-1]} d"
    )
    measured = "; ".join(
        f"{case.case_id} {case.cells:,} cells, median "
        f"{case.median_velocity_mm_yr:.{DECIMALS}f} mm/yr"
        for case in cases
    )
    lines = [
        "what sinks and why: "
        f"{strongest.case_id} median {strongest.median_velocity_mm_yr:.{DECIMALS}f} mm/yr, "
        f"p05 {strongest.p05_velocity_mm_yr:.{DECIMALS}f}, core "
        f"{strongest.velocity_min_mm_yr:.{DECIMALS}f} mm/yr; its own limitation says: "
        f'"{mechanism}"',
        f"what we measured: {date_span} over {window_span}, {measured} "
        f"(product={', '.join(products)}).",
    ]

    if null_results:
        control = null_results[0]
        lines.append(
            f"the control case: {control['case_id']} median "
            f"{control['median_velocity_mm_yr']:.{DECIMALS}f} mm/yr vs detection limit "
            f"{control['detection_limit_mm_yr']:.{DECIMALS}f} mm/yr — "
            f"{control['sentence']}."
        )
    else:
        lines.append(
            "the control case: none declared in the artefacts (no stats.detection_limit_mm_yr); "
            "do not invent one."
        )

    # v1.1: the fitted trend/seasonal split, with the span that licenses it. It replaces the old
    # "we cannot publish seasonal_amp_mm" sentence: on a two-year window we can, and the number is
    # a fit of the published series, not a config value.
    split = "; ".join(
        f"{case.case_id} A={case.median_seasonal_amp_mm:.{DECIMALS}f} mm "
        f"R={_ratio(case.median_ratio_R)} {case.dominant_regime}"
        for case in cases
    )
    cycles = sorted({case.window_days for case in cases})
    cycle_span = (
        f"{cycles[0] / V.DAYS_PER_YEAR:.2f}"
        if cycles[0] == cycles[-1]
        else f"{cycles[0] / V.DAYS_PER_YEAR:.2f}-{cycles[-1] / V.DAYS_PER_YEAR:.2f}"
    )
    lines.append(
        f"what the seasonal fit says: {split} (annual cycles observed {cycle_span}, gate "
        f"{V.MIN_SEASONAL_COVERAGE_PER_HARMONIC:.2f} per harmonic) — fitted per cell, so the "
        "split and the map come from one fit."
    )

    artefact = processed_root / strongest.case_id / GRID_FILE
    lines.append(
        f"how you can check us: {artefact} + meta.json, provenance_id "
        f"{strongest.provenance_id}; rerun scripts/demo_numbers.py --json and diff the output."
    )
    return lines


def build_document(
    processed_root: pathlib.Path,
    cases: Sequence[CaseNumbers],
    skipped: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """The single content model: --json prints it, the text card renders from it."""
    contrast = _contrast(cases) if cases else {
        "spreads_mm_yr": {},
        "strongest_case": None,
        "strongest_spread_mm_yr": None,
        "null_case": None,
        "null_spread_mm_yr": None,
        "ratio": None,
        "shared_scale_mm_yr": None,
        "shared_legend_mm_yr": None,
        "legend_step_mm_yr": LEGEND_STEP_MM_YR,
        "max_abs_velocity_mm_yr": None,
        "p05_min_mm_yr": None,
        "velocity_min_mm_yr": None,
    }
    null_results = _null_results(cases)
    mock_cases = [case.case_id for case in cases if case.product == "mock"]
    mock_warning = None
    if mock_cases:
        mock_warning = (
            f"product == 'mock' for {len(mock_cases)}/{len(cases)} case(s) "
            f"({', '.join(mock_cases)}): every number below is synthetic, and the UI badge "
            "must say MOCK. Validate the map and the schema, never the science."
        )

    below = [case.case_id for case in cases if case.aoi_coverage_fraction < COVERAGE_WARNING_FRACTION]
    return {
        "processed_root": str(processed_root),
        "cases": [case.as_json() for case in cases],
        "coverage_summary": {
            "authoritative_denominator": "aoi",
            "threshold": COVERAGE_WARNING_FRACTION,
            "cases": len(cases),
            "below_warning_threshold": below,
            "note": (
                "the threshold is measured on the AOI H3 cells (pipeline.process_case.aoi_cells); "
                "the bbox figures in cases[].coverage.bbox describe the envelope of the AOI and "
                "must never be quoted against the threshold"
            ),
        },
        "contrast": contrast,
        "seasonal_split": _seasonal_split(cases),
        "scale_check": _scale_check(cases),
        "null_result": null_results,
        "honesty": {
            "mock_cases": mock_cases,
            "mock_warning": mock_warning,
            "limitations": {case.case_id: list(case.limitations) for case in cases},
            "do_not_claim": _do_not_claim(cases),
        },
        "skeleton": _skeleton(cases, contrast, null_results, processed_root),
        "skipped": [{"case": case_id, "reason": reason} for case_id, reason in skipped],
    }


# ---------------------------------------------------------------------------------------------
# Text rendering. Everything here is formatting; the numbers come from ``document``.
# ---------------------------------------------------------------------------------------------


def _mm(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.{DECIMALS}f}"


def _ratio(value: float | None) -> str:
    """Dimensionless R = |trend|/A. `n/a` when undefined (no harmonic was fitted)."""
    return "n/a" if value is None else f"{value:.3f}"


def _pct(value: float) -> str:
    return f"{value:.2%}"


def render_text(document: dict[str, Any]) -> str:
    """Render the card. The shared legend is declared once, as *shared*, and never per case."""
    lines: list[str] = []
    root = document["processed_root"]
    lines.append(
        "DEMO NUMBER CARD — every number below is derived from "
        f"{root}/{{case}}/{META_FILE} + {GRID_FILE}"
    )

    honesty = document["honesty"]
    if honesty["mock_warning"]:
        lines.append(f"!! {honesty['mock_warning']}")

    for case in document["cases"]:
        lines.append(f"CASE {case['case_id']} — {case['name']} [product={case['product']}]")
        lines.append(
            f"  cells={case['cells']:,} median={_mm(case['median_velocity_mm_yr'])} "
            f"p05={_mm(case['p05_velocity_mm_yr'])} p95={_mm(case['p95_velocity_mm_yr'])} "
            f"spread={_mm(case['spread_mm_yr'])} mm/yr"
        )
        lines.append(
            f"  coherence={_mm(case['coherence_min'])}..{_mm(case['coherence_max'])} "
            f"window={case['date_first']}..{case['date_last']} "
            f"({case['window_days']} d, {case['n_dates']} dates) "
            f"revisit_days={case['revisit_days']}"
        )
        coverage = case["coverage"]
        aoi = coverage["aoi"]
        bbox = coverage["bbox"]
        bar = coverage["warning_threshold"]
        verdict = "BELOW the bar" if aoi["below_warning_threshold"] else "PASS"
        lines.append(
            f"  provenance_id={case['provenance_id']} "
            f"limitations={case['n_limitations']} "
            f"coverage_aoi={_pct(aoi['fraction'])} of {aoi['cells']:,} AOI H3 res-"
            f"{coverage['h3_resolution']} cells ({aoi['covered']:,} covered; "
            f"authoritative denominator, {bar:.0%} bar: {verdict})"
        )
        lines.append(
            f"  coverage_bbox={_pct(bbox['fraction'])} of {bbox['cells']:,} bbox H3 res-"
            f"{coverage['h3_resolution']} cells ({bbox['covered']:,} covered; envelope only, "
            "NOT the threshold denominator)"
        )

    summary = document["coverage_summary"]
    if summary["cases"]:
        verdict = (
            f"all {summary['cases']} case(s) PASS on the AOI denominator"
            if not summary["below_warning_threshold"]
            else "BELOW the bar: " + ", ".join(summary["below_warning_threshold"])
        )
        lines.append(
            f"COVERAGE VERDICT — the {summary['threshold']:.0%} bar is measured on the AOI H3 "
            f"cells (pipeline/process_case.py COVERAGE_WARNING_FRACTION): {verdict}; the bbox "
            "figures above are an envelope diagnostic and are never the threshold denominator."
        )

    contrast = document["contrast"]
    if document["cases"]:
        spreads = " ".join(
            f"{case_id}={_mm(value)}"
            for case_id, value in contrast["spreads_mm_yr"].items()
        )
        lines.append(f"CONTRAST spreads (p95-p05): {spreads} mm/yr")
        if contrast["ratio"] is not None:
            lines.append(
                f"  ratio strongest/null = {_mm(contrast['strongest_spread_mm_yr'])} / "
                f"{_mm(contrast['null_spread_mm_yr'])} = {contrast['ratio']:.1f}x"
            )
        else:
            lines.append(
                "  ratio strongest/null = n/a (no case publishes stats.detection_limit_mm_yr)"
            )
        if contrast["shared_scale_mm_yr"] is not None:
            lines.append(
                f"  shared legend = ±{contrast['shared_scale_mm_yr']:g} mm/yr for ALL cases "
                f"= ceil(max|velocity| {_mm(contrast['max_abs_velocity_mm_yr'])} / "
                f"{contrast['legend_step_mm_yr']:g}) * "
                f"{contrast['legend_step_mm_yr']:g} — this is the SHARED scale, not a per-case "
                "scale."
            )
            lines.append(
                f"  do not clip at p05 ({_mm(contrast['p05_min_mm_yr'])}): the core reaches "
                f"{_mm(contrast['velocity_min_mm_yr'])} mm/yr, so clipping at p05 hides the core."
            )

    if document["scale_check"]:
        lines.append(
            "PHASE->MM SCALE CHECK (recomputed via "
            "pipeline.ingest.nisar_gunw.phase_to_los_displacement_mm + "
            "transforms.vertical_from_los)"
        )
        for block in document["scale_check"]:
            lines.append(f"  {block['formula_los']} ; {block['formula_vertical']}")
            lines.append(
                f"  inputs: lambda={block['wavelength_m']:g} m "
                f"({block['wavelength_source']}), theta={block['incidence_deg']:.2f} deg "
                f"({block['incidence_source']}), heading={block['heading_deg']:.2f} deg "
                f"({block['heading_source']})"
            )
            lines.append(
                f"  |1 rad| -> {block['one_radian_los_mm']:.2f} mm LOS -> "
                f"{block['one_radian_vertical_mm']:.2f} mm vertical ; "
                f"|1 fringe (2*pi)| -> {block['one_fringe_los_mm']:.2f} mm LOS -> "
                f"{block['one_fringe_vertical_mm']:.2f} mm vertical "
                f"[{', '.join(block['cases'])}]"
            )

    for null in document["null_result"]:
        lines.append(
            f"NULL RESULT {null['case_id']}: detection_limit="
            f"{_mm(null['detection_limit_mm_yr'])} mm/yr "
            f"median={_mm(null['median_velocity_mm_yr'])} mm/yr"
        )
        lines.append(f"  {null['sentence']}")

    if document["seasonal_split"]:
        blocks = document["seasonal_split"]
        answerable = all(block["seasonal_identifiable"] for block in blocks)
        gate = (
            f"{blocks[0]['window_days']} d = {blocks[0]['annual_coverage']:.2f} cycles clears the "
            f"{blocks[0]['coverage_gate']:.2f}-cycle gate, so the fitted amplitude is quotable"
            if answerable
            else "below the coverage gate, so the fitted amplitude is NOT quotable"
        )
        lines.append(
            f"SEASONAL SPLIT — {gate} (fitted per cell by transforms.harmonic_fit; regime = "
            "classify_regime on R = |trend|/A): "
            + "; ".join(
                f"{block['case_id']} A_med={_mm(block['median_seasonal_amp_mm'])} mm "
                f"R_med={_ratio(block['median_ratio_R'])} {block['dominant_regime']}"
                for block in blocks
            )
        )

    lines.append(
        "HONESTY — limitations[] verbatim, per case (the presenter must not contradict these)"
    )
    for case in document["cases"]:
        for limitation in case["limitations"]:
            lines.append(f"  [{case['case_id']}] {limitation}")

    do_not_claim = honesty["do_not_claim"]
    if do_not_claim:
        lines.append("DO NOT CLAIM (derived from those limitations and the artefacts)")
        for item in do_not_claim:
            lines.append(f"  - {item}")

    if document["skeleton"]:
        lines.append(
            "90-SECOND SKELETON (20 s problem / 90 s demo / 30 s architecture; numbers inlined)"
        )
        for sentence in document["skeleton"]:
            lines.append(f"  {sentence}")

    if document["skipped"]:
        lines.append("SKIPPED (artefacts missing or inconsistent; never fatal)")
        for skip in document["skipped"]:
            lines.append(f"  - {skip['case']}: {skip['reason']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _discover(processed_root: pathlib.Path) -> list[pathlib.Path]:
    if not processed_root.is_dir():
        return []
    return sorted(
        path
        for path in processed_root.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )


def _candidate_dirs(processed_root: pathlib.Path, selected: Sequence[str] | None) -> list[pathlib.Path]:
    """Every case to try: the requested slugs, else the contract's cases plus what is on disk."""
    if selected is not None:
        return [processed_root / slug for slug in selected]
    on_disk = {path.name for path in _discover(processed_root)}
    # A declared case whose directory vanished is still a *skipped* case with a note, not a
    # silently shorter card. Extra directories found on disk are read too (and validated).
    return [processed_root / slug for slug in sorted(set(C.CASE_IDS) | on_disk)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit the demo number card derived from data/processed/ (no clock, no network).",
    )
    parser.add_argument(
        "--processed",
        default="data/processed",
        help="artefact root to read (default: data/processed)",
    )
    parser.add_argument(
        "--case",
        action="append",
        metavar="SLUG",
        help="restrict to one case slug; repeatable (default: every case on disk)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the same content as structured JSON (stderr keeps skip notes)",
    )
    args = parser.parse_args(argv)

    processed_root = pathlib.Path(args.processed)
    selected = sorted(set(args.case)) if args.case else None
    requested = _candidate_dirs(processed_root, selected)

    cases: list[CaseNumbers] = []
    skipped: list[tuple[str, str]] = []
    for case_dir in requested:
        try:
            cases.append(load_case(case_dir))
        except CaseSkipped as exc:
            skipped.append((case_dir.name, str(exc)))
        except Exception as exc:  # noqa: BLE001 - one bad file must never kill the card
            skipped.append((case_dir.name, f"unexpected {exc.__class__.__name__}: {exc}"))

    document = build_document(processed_root, cases, skipped)

    if args.json:
        for case_id, reason in skipped:
            print(f"note: skipped {case_id}: {reason}", file=sys.stderr)
        print(json.dumps(document, indent=2, ensure_ascii=False))
        return 0

    text = render_text(document)
    print(text)
    line_count = text.count("\n") + 1
    if line_count > MAX_CARD_LINES:
        print(
            f"note: card is {line_count} lines, over the {MAX_CARD_LINES}-line terminal budget; "
            "shorten the skeleton or drop a case (nothing was truncated).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
