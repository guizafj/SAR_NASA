#!/usr/bin/env python3
"""Real-data processing chain: NISAR L2 GUNW granule(s) -> LOS -> vertical -> H3 -> artefacts.

This is the missing link between the reader (`pipeline/ingest/nisar_gunw.py`), the numeric core
(`pipeline/science/transforms.py`) and the four frozen artefacts of
`docs/data-contract-v1.md` v1.0. It runs the chain the mock has always pretended to run:

    granule.h5 -> phase (rad) -> d_LOS (mm) -> v_up (mm) -> cumulative stack -> H3 res 9
        -> harmonic trend per cell -> nisar_grid.parquet / timeseries.csv /
           context_layers.geojson / meta.json

Normative inputs, all read and none re-declared here
----------------------------------------------------
* `docs/nisar-gunw-layout.md` — dataset paths, the phase→mm scale, what is still unverified.
* `docs/data-source-decision.md` — the coverage caveat this module reports instead of hiding.
* `src/domain/contracts.py` — schema, enums, thresholds, `risk_level_from_velocity`.
* `config/study_cases.yaml` — per-case bbox/AOI, CRS, reference point, `processing:` constants.

Rules this module enforces (they matter more than any feature)
--------------------------------------------------------------
* **Fail closed.** A missing geometry (incidence/heading), a missing geolocation, a mixed track or
  flight direction, a polarisation that changes mid-stack, a missing required layer, or a stack too
  short to fit a trend is a refusal with an actionable message — never a quiet default.
* **Nodata stays absent.** Mask 127 padding, non-finite phase/coherence, pixels below the frozen
  `coherence_min` and cells without a complete series are dropped. They are never written as 0
  (in Málaga, 0 is a real measurement).
* **The mock tree is protected.** Writing into a directory whose `meta.json` says
  `product == "mock"` requires `--force`; the committed demo is never silently replaced.
* **Provenance is the physical claim.** `meta.provenance.method` names the LOS sign convention, the
  `v_north = 0` assumption, the wavelength source, the geometry source and the aggregation. The
  ionospheric screen that GUNW delivers but does not apply is stated as a limitation.
  `meta.provenance.geometry_convention` carries the same choice machine-readably (`los_sign`,
  `heading_convention`, `validated_against`) so a reviewer can grep it instead of parsing prose.

Conventions are inputs, not decoration
--------------------------------------
`--los-sign`, `--heading-convention`, `--method-note` and `--validated-against` exist because
`docs/first-granule-runbook.md` §7 gates publication on one sentence: "sign convention of
docs/nisar-gunw-layout.md §4, NOT yet validated against GNSS" must be **gone** from
`meta.provenance.method` before any number is shown. The two convention flags change arithmetic as
well as prose; the two prose flags only record what the operator checked.

* `--los-sign away-from-sensor` (**the physical default**, and today's behaviour) keeps
  `d_LOS = -lambda/(4*pi)*phi`: positive LOS means the target moved away from the sensor, so
  subsidence — downward motion, up positive in this pipeline — is **negative**.
  `--los-sign toward-sensor` uses `+lambda/(4*pi)*phi`, which flips every vertical sign: subsidence
  then comes out **positive**. A 2π phase slip and a flipped sign both invert subsidence, so the
  operator must be able to state which of the two they chose.
* `--heading-convention platform` (today's behaviour) passes the granule heading through verbatim as
  the azimuth `alpha` of the frozen unit vector
  `u = [cos(theta), -sin(theta)*cos(alpha), -sin(theta)*sin(alpha)]`.
  `--heading-convention look` maps it to the look azimuth `alpha = 180 deg - alpha_platform` before
  `los_to_vertical`, because the horizontal part of that frozen `u` points at azimuth
  `alpha + 180`.
  **Where that is observable.** `u_up = cos(theta)` is heading-independent, so the mapping is felt
  only through the trusted horizontals `v_east`/`v_north` that `los_to_vertical` subtracts:
  `u_east` flips sign under the mapping, `u_north` does not (`sin(180 - alpha) == sin(alpha)`).
  With no horizontals supplied, the two conventions therefore produce **identical numbers** in this
  chain — which is exactly the single-geometry ambiguity the runbook cannot resolve. The trusted
  horizontals are `process_case(assumed_v_east_mm_yr=..., assumed_v_north_mm_yr=...)`, API-only
  because they are evidence (a GNSS model), not a knob; their defaults (0.0) are today's
  `v_north = 0` and no east assumption.

**Determinism.** The convention is an input, so `provenance_id` includes it: the same granules run
with an effective `los_sign`/`heading_convention` other than the default are two different physical
claims and legitimately carry two different ids (see `_provenance_id`). Stating the default
explicitly, adding `--method-note`/`--validated-against`, or passing all-zero horizontals changes
no number, so it keeps the historical id — which is bit-for-byte the one this module has always
produced when nothing is supplied. Non-zero `assumed_v_east_mm_yr`/`assumed_v_north_mm_yr` do enter
the id, because they move the numbers. Reruns with identical input **and identical arguments** are
byte-identical.

Writers are reused from `pipeline/mock/artifacts.py` (`write_parquet`, `write_csv`,
`write_geojson`, `write_json`) so the real and mock trees are byte-structured identically; the
frames and `meta.json` are built here because the mock's builders are bound to its synthetic
`CaseModel`.

CLI
---
    ./scripts/python.sh pipeline/process_case.py --case mexico-city \
        --granule a.h5 --granule b.h5 --out /tmp/processed/mexico-city \
        [--incidence 39 --heading 192] [--land-use-csv cells.csv] [--force] [--json] \
        [--los-sign away-from-sensor|toward-sensor] [--heading-convention platform|look] \
        [--method-note TEXT]... [--validated-against TEXT]

Exit status: 0 only when the written tree passes `contracts.validate_all` and the file-level gate
(`pipeline/validate_artifacts.py`); 2 on a refusal; 3 on a failed contract check.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

import h3
import numpy as np
import pandas as pd
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python pipeline/process_case.py` puts pipeline/, not the repo
    sys.path.insert(0, str(ROOT))

from pipeline.aoi import AOIError, load_aoi  # noqa: E402
from pipeline.ingest import nisar_gunw as G  # noqa: E402
from pipeline.science import transforms as V  # noqa: E402
from src.domain import contracts as C  # noqa: E402

__all__ = [
    "ConventionRecord",
    "Coverage",
    "DEFAULT_HEADING_CONVENTION",
    "DEFAULT_LOS_SIGN",
    "GeometryRequiredError",
    "GranuleIdentity",
    "HEADING_CONVENTIONS",
    "LOS_SIGN_FACTORS",
    "LandUseError",
    "MissingLayerError",
    "MockOverwriteError",
    "PolarisationMismatchError",
    "ProcessCaseError",
    "ProcessedCase",
    "TrackMismatchError",
    "load_case_config",
    "parse_granule_identity",
    "process_case",
    "write_artefacts",
]

# ---------------------------------------------------------------------------------------------
# Constants. Everything contract-shaped comes from `contracts`; this block is literature/format.
# ---------------------------------------------------------------------------------------------

PRODUCT = "nisar-gunw"
SENSOR = C.SENSOR_PROFILES[PRODUCT].sensor  # never re-declared: nisar, 12-day revisit
PROFILE = C.SENSOR_PROFILES[PRODUCT]

#: Human-readable provider, published in every grid row (`source`, contract §3).
SOURCE_PROVIDER = "ASF DAAC"
#: The product this chain actually consumes. Named in `meta.provenance.source`.
SOURCE_PRODUCT = (
    "NASA NISAR L2 GUNW (Geocoded Unwrapped Interferogram), frequency A, co-polarised"
)
LICENSE = "NASA open data (Earthdata / ASF DAAC terms of use)"

DOC_REFERENCE = "docs/nisar-gunw-layout.md"
DATA_SOURCE_DECISION = "docs/data-source-decision.md"
CONFIG_PATH = ROOT / "config" / "study_cases.yaml"

#: Documented L-band wavelength from `docs/nisar-gunw-layout.md` §4. Used ONLY when the granule
#: carries neither `wavelength` nor `centerFrequency`, and always with a warning + provenance note.
DEFAULT_WAVELENGTH_M = 0.2384

#: `v_north` is an assumption, not a measurement (README §6): recorded in `provenance.method`.
ASSUMED_V_NORTH_MM = 0.0

#: LOS sign conventions accepted by `--los-sign`, mapped to the `sign` factor of
#: `nisar_gunw.phase_to_los_displacement_mm` (`d_LOS = sign*lambda/(4*pi)*phi`). The first entry is
#: the physical default **and** the arithmetic this module has always applied: the LOS axis points
#: from the target toward the satellite, so positive means "the ground moved away from the sensor"
#: and subsidence (downward, up positive here) is negative.
LOS_SIGN_FACTORS: dict[str, float] = {"away-from-sensor": -1.0, "toward-sensor": 1.0}
DEFAULT_LOS_SIGN = "away-from-sensor"

#: What each sign means in words, reused by the flag help and by `meta.provenance.method` so the
#: operator and the artefact cannot drift apart.
LOS_SIGN_MEANINGS: dict[str, str] = {
    "away-from-sensor": (
        "positive d_LOS means the ground moved away from the sensor along the line of sight, so "
        "subsidence (downward motion; up is positive in this pipeline) is negative"
    ),
    "toward-sensor": (
        "positive d_LOS means the ground moved toward the sensor along the line of sight, so "
        "subsidence is positive and uplift negative — the opposite reading to away-from-sensor"
    ),
}

#: Heading conventions accepted by `--heading-convention`. `platform` passes the granule heading
#: through verbatim (today's behaviour); `look` maps it to `180 deg - heading` before the
#: LOS->vertical inversion. See the module docstring for the geometry.
HEADING_CONVENTIONS: tuple[str, ...] = ("platform", "look")
DEFAULT_HEADING_CONVENTION = "platform"

#: Coverage below this fraction of the case AOI is reported as a prominent warning, per the
#: verification checklist in `docs/data-source-decision.md`.
COVERAGE_WARNING_FRACTION = 0.5

#: Size guard for the land_use polygons of `context_layers.geojson`; the whole case must stay
#: inside the 15 MB contract budget (`validate_artifacts.DEFAULT_BUDGET_MB`).
CONTEXT_LAYOUT_BUDGET_BYTES = 4_000_000
CONTEXT_SIMPLIFY_DEG = 0.0005
CONTEXT_PRECISION_DEG = 1e-5

# ---------------------------------------------------------------------------------------------
# HDF5 metadata this reader does not expose: incidence, heading, effective looks.
# `docs/nisar-gunw-layout.md` §3/§5.1 say the exact names are NOT verified for GUNW, so every hit
# is recorded and reported; an array where a scalar is expected is a refusal, never a scene mean
# (averaging incidence would silently change the vertical scale).
# ---------------------------------------------------------------------------------------------

_BASE = "/science/LSAR/GUNW"
_GRIDS = f"{_BASE}/grids/frequencyA"
_RADAR = f"{_BASE}/metadata/radarGrid"
_PARAMS = f"{_BASE}/metadata/processingInformation/parameters/frequencyA"

Candidate = str | tuple[str, str]

INCIDENCE_CANDIDATES: tuple[Candidate, ...] = (
    f"{_RADAR}/incidenceAngleCenter",
    f"{_RADAR}/incidenceAngle",
    f"{_PARAMS}/incidenceAngle",
    (_RADAR, "incidenceAngle"),
    (_RADAR, "incidenceAngleCenter"),
    (_PARAMS, "incidenceAngle"),
)
HEADING_CANDIDATES: tuple[Candidate, ...] = (
    f"{_RADAR}/heading",
    f"{_RADAR}/platformHeading",
    (_RADAR, "heading"),
    (_RADAR, "platformHeading"),
)
LOOKS_CANDIDATES: tuple[Candidate, ...] = (
    f"{_RADAR}/numberOfLooks",
    f"{_PARAMS}/numberOfLooks",
    f"{_GRIDS}/numberOfLooks",
    (_RADAR, "numberOfLooks"),
)

#: NISAR granule names: NISAR_<L>_<PR>_<GUNW>_<track>_<frame>_<A|D>_… . Only the fields used for the
#: safety checks are parsed; anything unparseable is reported, never guessed.
_GRANULE_RE = re.compile(
    r"^(?P<mission>[A-Z]+)_(?P<level>L\d[A-Z]?)_(?P<ptype>[A-Z0-9]+)_(?P<product>GUNW[A-Z0-9]*)_"
    r"(?P<track>\d{3})_(?P<frame>\d{3})_(?P<direction>[AD])_"
)
_DIRECTIONS = {"A": "ascending", "D": "descending"}
_ISO_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})")

# ---------------------------------------------------------------------------------------------
# Errors. Every message names what is missing and what to do about it.
# ---------------------------------------------------------------------------------------------


class ProcessCaseError(RuntimeError):
    """A refusal: this stack cannot produce a contract-valid artefact set.

    ``warnings`` carries everything the run had already learned before it refused, so the CLI can
    print the diagnostic (e.g. "single granule") together with the reason. A refusal that hides
    its own context is a worse refusal.
    """

    def __init__(self, message: str, warnings: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.warnings: list[str] = list(warnings)


class MissingLayerError(ProcessCaseError):
    """A required GUNW layer (phase, coherence, mask, components) is absent or unusable."""


class GeometryRequiredError(ProcessCaseError):
    """Incidence/heading cannot be resolved: no metadata and no CLI flag. Never invented."""


class TrackMismatchError(ProcessCaseError):
    """Granules come from different tracks or flight directions (not a coherent time series)."""


class PolarisationMismatchError(ProcessCaseError):
    """The co-pol group changes mid-stack, so the phase scale would change silently."""


class MockOverwriteError(ProcessCaseError):
    """`out_dir` holds a committed mock artefact set and `--force` was not passed."""


class LandUseError(ProcessCaseError):
    """The `--land-use-csv` sidecar is malformed or uses a label outside the frozen enum."""


# ---------------------------------------------------------------------------------------------
# Small value objects
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseConfig:
    """One entry of `config/study_cases.yaml` plus the shared `processing:` constants."""

    case_id: str
    name: str
    bbox: tuple[float, float, float, float]
    aoi_ring: tuple[tuple[float, float], ...]  # (lon, lat), closed
    reference_lonlat: tuple[float, float]
    crs_publish: str
    crs_analysis: str
    revisit_days: int
    h3_resolution: int
    limitations: tuple[str, ...]
    null_result: bool
    detection_limit_mm_yr: float | None
    aoi_source: str = "config/study_cases.yaml:aoi_polygon"


@dataclass(frozen=True)
class GranuleIdentity:
    """What the filename says about a granule. `None` means "could not parse", not "guess"."""

    name: str
    track: int | None
    frame: int | None
    direction: str | None  # "ascending" | "descending"
    detail: str


@dataclass(frozen=True)
class Coverage:
    """AOI coverage diagnostic — the CDMX-survival question of `docs/data-source-decision.md`."""

    aoi_cells: int
    covered_cells: int
    covered_in_aoi: int
    covered_outside_aoi: int  # cells with data outside the AOI: dropped, never published
    fraction: float  # covered_in_aoi / aoi_cells

    @property
    def below_threshold(self) -> bool:
        return self.fraction < COVERAGE_WARNING_FRACTION


@dataclass(frozen=True)
class ConventionRecord:
    """The LOS sign / heading convention a run used, plus the validation statement behind it.

    Built by :func:`_resolve_convention` from the four CLI flags, so the arithmetic
    (:attr:`sign`), the prose (`meta.provenance.method`) and the machine-readable entry
    (`meta.provenance.geometry_convention`) are all derived from **one** value. ``requested`` is
    ``False`` only when the operator passed none of the four flags: that is the historical default
    run, whose method text keeps the "NOT yet validated against GNSS" hedge.
    """

    los_sign: str = DEFAULT_LOS_SIGN
    heading_convention: str = DEFAULT_HEADING_CONVENTION
    validated_against: str | None = None
    method_notes: tuple[str, ...] = ()
    requested: bool = False

    @property
    def sign(self) -> float:
        """The ``sign`` factor for `nisar_gunw.phase_to_los_displacement_mm` (-1.0 or +1.0)."""
        return LOS_SIGN_FACTORS[self.los_sign]

    @property
    def phase_factor(self) -> str:
        """`-lambda/(4*pi)*phi` for the default, `+lambda/(4*pi)*phi` for ``toward-sensor``."""
        prefix = "-" if self.sign < 0.0 else "+"
        return f"{prefix}lambda/(4*pi)*phi"

    @property
    def formula(self) -> str:
        """The whole conversion, as published in `provenance.geometry_convention`."""
        return f"d_LOS = {self.phase_factor}"

    def id_token(self) -> str:
        """The part of the provenance digest that the convention contributes.

        Empty when the **effective** convention is the historical default, so a flag-less run and a
        run that restates the default keep bit-for-bit the id this module has always produced. Any
        other effective convention is a different physical claim about the same granules, so it
        changes the id. Prose flags contribute nothing: they name no number.
        """
        if (
            self.los_sign == DEFAULT_LOS_SIGN
            and self.heading_convention == DEFAULT_HEADING_CONVENTION
        ):
            return ""
        return f"los_sign={self.los_sign}|heading_convention={self.heading_convention}"


def _look_azimuth(heading_deg: float) -> float:
    """Azimuth of the frozen unit vector's horizontal part, from the platform heading.

    `u = [cos(theta), -sin(theta)*cos(alpha), -sin(theta)*sin(alpha)]` puts the horizontal part at
    azimuth ``alpha + 180``. For a right-looking acquisition whose platform heading is
    ``alpha_platform``, that is reproduced by ``alpha = 180 - alpha_platform`` (see
    `transforms.los_to_vertical` and `docs/nisar-gunw-layout.md` §4). Normalised into [0, 360).
    """
    return (180.0 - float(heading_deg)) % 360.0


def _normalise_choice(value: str, allowed: tuple[str, ...], flag: str, *, what: str) -> str:
    """Fold a convention string onto one of ``allowed``; anything else is a refusal, not a guess."""
    text = str(value).strip().lower().replace("_", "-")
    if text in allowed:
        return text
    raise ProcessCaseError(
        f"{flag} {value!r} is not one of {list(allowed)}: {what}. The convention is recorded in "
        "`meta.provenance.method` and is a physical claim, so it is never inferred from the text "
        f"({DOC_REFERENCE} §4)."
    )


def _sentence(text: str) -> str:
    """Terminate an operator-supplied sentence so `provenance.method` stays readable prose."""
    stripped = " ".join(str(text).split())
    return stripped if stripped.endswith((".", "!", "?")) else f"{stripped}."


def _resolve_convention(
    *,
    los_sign: str | None,
    heading_convention: str | None,
    method_notes: Sequence[str] | None,
    validated_against: str | Sequence[str] | None,
    warnings: list[str],
) -> ConventionRecord:
    """Validate the four convention flags and fold them into one :class:`ConventionRecord`.

    Refusals (all ``ProcessCaseError``, so the CLI exits 2 with the reason):

    * an unknown ``los_sign`` / ``heading_convention`` value — the strings are the record, so a
      typo must not become a convention;
    * a blank ``--method-note`` / ``--validated-against`` — an empty sentence records nothing (drop
      the flag instead, and the run honestly keeps the "not yet validated" hedge).

    Both text flags are repeatable (runbook §7 records every check), so their sentences accumulate:
    notes become one sentence each, validations are joined with ``"; "`` into one statement.

    Warnings (allowed, but said out loud):

    * ``--validated-against`` **without** a sign or heading flag: legal, because validating the
      default convention is exactly what the runbook asks for, but the operator is told the default
      is what got recorded so they can name it explicitly next time;
    * a sign/heading flag **without** ``--validated-against``: the arithmetic is recorded, the
      external check is not.
    """
    notes: list[str] = []
    for note in method_notes or ():
        text = " ".join(str(note).split())
        if not text:
            raise ProcessCaseError(
                "--method-note was given an empty string: a blank note records nothing. Either "
                "state the sentence the artefact should carry, or drop the flag (the run then keeps "
                f"the \"NOT yet validated against GNSS\" hedge, {DOC_REFERENCE} §4)."
            )
        notes.append(text)
    validated_parts: list[str] = []
    raw_validations = (
        []
        if validated_against is None
        else ([validated_against] if isinstance(validated_against, str) else list(validated_against))
    )
    for part in raw_validations:
        text = " ".join(str(part).split())
        if not text:
            raise ProcessCaseError(
                "--validated-against was given an empty string: name the GNSS/levelling series, "
                "station or published rate the convention was checked against, or drop the flag so "
                "the run keeps the \"NOT yet validated against GNSS\" hedge instead of claiming a "
                "check it cannot show."
            )
        validated_parts.append(text)
    # Repeatable (runbook §7 tells the operator to record every check): one sentence, semicolons.
    validated = "; ".join(_dedupe(validated_parts)) if validated_parts else None

    record = ConventionRecord(
        los_sign=(
            DEFAULT_LOS_SIGN
            if los_sign is None
            else _normalise_choice(
                los_sign,
                tuple(LOS_SIGN_FACTORS),
                "--los-sign",
                what=(
                    "away-from-sensor keeps d_LOS = -lambda/(4*pi)*phi (positive = motion away "
                    "from the satellite, subsidence negative); toward-sensor flips it"
                ),
            )
        ),
        heading_convention=(
            DEFAULT_HEADING_CONVENTION
            if heading_convention is None
            else _normalise_choice(
                heading_convention,
                HEADING_CONVENTIONS,
                "--heading-convention",
                what=(
                    "platform passes the granule heading through verbatim; look maps it to "
                    "alpha = 180 deg - alpha_platform before los_to_vertical"
                ),
            )
        ),
        validated_against=validated,
        method_notes=tuple(_dedupe(notes)),
        requested=bool(
            los_sign is not None
            or heading_convention is not None
            or validated_against is not None
            or notes
        ),
    )

    if validated is not None and los_sign is None and heading_convention is None:
        warnings.append(
            "--validated-against was given without --los-sign or --heading-convention: allowed, "
            "but what got recorded as validated is the default convention "
            f"(los_sign={record.los_sign}, heading_convention={record.heading_convention}). Pass "
            "the flag explicitly next time so the record names the convention it tested."
        )
    elif record.requested and validated is None:
        warnings.append(
            f"the convention ({record.los_sign} / {record.heading_convention}) was changed or "
            "recorded without --validated-against: meta.provenance.method states the convention "
            "but claims no external check of it. Validate against GNSS/levelling or a published "
            "rate before publishing a velocity (README §13, target < 5 mm/yr RMSE)."
        )
    return record


def _resolve_horizontal_assumptions(
    assumed_v_east_mm_yr: float | np.ndarray,
    assumed_v_north_mm_yr: float | np.ndarray,
    shape: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray]:
    """Broadcast the trusted horizontal velocity assumptions onto the phase grid.

    Units are **mm/yr**, because the assumption is a statement about ground motion; the caller
    scales it by each pair's span, since `transforms.vertical_from_los` takes that pair's
    displacement.

    A scalar is one scene-wide assumption (the normal case: ``v_north = 0``); an array is the grid
    of a GNSS model, which `vertical_from_los` already accepts. Non-finite input is refused rather
    than propagated: a NaN "assumption" is not an assumption, it is a hole in the correction, and
    it would silently NaN the whole vertical grid.
    """
    grids: list[np.ndarray] = []
    for value, name in (
        (assumed_v_east_mm_yr, "assumed_v_east_mm_yr"),
        (assumed_v_north_mm_yr, "assumed_v_north_mm_yr"),
    ):
        array = np.asarray(value, dtype=float)
        if not np.all(np.isfinite(array)):
            raise ProcessCaseError(
                f"{name} must be finite everywhere: a NaN/inf horizontal assumption would NaN the "
                f"whole vertical grid instead of admitting that the horizontal motion is unknown "
                f"(pass 0.0 and say so, or pass a real model). Got {value!r}"
            )
        try:
            grids.append(np.asarray(np.broadcast_to(array, shape), dtype=float))
        except ValueError as exc:
            raise ProcessCaseError(
                f"{name} of shape {np.shape(value)} does not broadcast onto the {shape} phase "
                "grid; pass a scalar (one assumption for the scene) or one value per pixel"
            ) from exc
    return grids[0], grids[1]


def _horizontals_token(east: np.ndarray, north: np.ndarray) -> str:
    """Digest of the horizontal assumptions, ``""`` when both are exactly zero.

    They change the numbers, so they belong in the provenance id (`_provenance_id`); rounding to
    nm/yr keeps a rerun from the same model byte-identical despite float noise, and the empty token
    keeps the historical id for the zero-assumption run.
    """
    if not np.any(east) and not np.any(north):
        return ""
    digest = hashlib.sha256(
        np.round(east, 6).tobytes() + b"|" + np.round(north, 6).tobytes()
    ).hexdigest()
    return f"horizontals={digest[:8]}"


def _assumption_summary(grid: np.ndarray) -> float | dict[str, Any]:
    """JSON-safe summary of an assumption grid for `meta.provenance.geometry_convention`."""
    if not np.any(grid):
        return 0.0
    if float(np.ptp(grid)) == 0.0:
        return round(float(grid.flat[0]), 6)
    return {
        "grid": True,
        "mean": round(float(np.mean(grid)), 6),
        "min": round(float(np.min(grid)), 6),
        "max": round(float(np.max(grid)), 6),
    }


def _format_assumption(summary: float | dict[str, Any]) -> str:
    """`0` for the zero assumption, `20` for a constant one, `a mean 20 grid (0…32)` for a grid."""
    if isinstance(summary, dict):
        return (
            f"a grid (mean {summary['mean']:g}, {summary['min']:g}…{summary['max']:g})"
        )
    return f"{summary:g}"


@dataclass
class ProcessedCase:
    """Everything `write_artefacts` needs, plus the diagnostics a human must read."""

    case_id: str
    grid: pd.DataFrame
    timeseries: pd.DataFrame
    context: dict[str, Any]
    meta: dict[str, Any]
    coverage: Coverage
    epochs: tuple[str, ...]
    provenance_id: str
    warnings: list[str] = field(default_factory=list)

    @property
    def n_cells(self) -> int:
        return int(len(self.grid))


# ---------------------------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _raw_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise ProcessCaseError(
            f"case configuration {CONFIG_PATH} is missing; the pipeline cannot invent a bbox, a "
            "reference point or an H3 resolution"
        )
    try:
        loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ProcessCaseError(f"cannot parse {CONFIG_PATH}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise ProcessCaseError(f"{CONFIG_PATH} is not a mapping")
    return dict(loaded)


def load_case_config(case_id: str) -> CaseConfig:
    """Read one case from `config/study_cases.yaml`; unknown slugs are refused, not defaulted."""
    raw = _raw_config()
    cases = raw.get("cases") or {}
    if case_id not in cases:
        raise ProcessCaseError(
            f"unknown case_id {case_id!r}: {CONFIG_PATH} declares {sorted(cases)} "
            "(a case is never invented)"
        )
    entry = cases[case_id]
    processing = dict(raw.get("processing") or {})
    configured_bbox = tuple(float(v) for v in entry["bbox"])
    try:
        aoi = load_aoi(case_id, entry, aoi_dir=CONFIG_PATH.parent / "aoi")
    except AOIError as exc:
        raise ProcessCaseError(str(exc)) from exc
    bbox = aoi.bbox
    ring = list(aoi.ring)
    if ring and ring[0] == ring[-1]:
        ring = ring[:-1]
    lon, lat = (float(v) for v in entry["reference_point"])
    if not (
        configured_bbox[0] <= lon <= configured_bbox[2]
        and configured_bbox[1] <= lat <= configured_bbox[3]
    ):
        raise ProcessCaseError(
            f"{case_id}: reference_point {(lon, lat)} lies outside bbox {configured_bbox}; a reference "
            "pixel outside the AOI would silently zero the whole map"
        )
    return CaseConfig(
        case_id=case_id,
        name=str(entry["name"]),
        bbox=bbox,  # type: ignore[arg-type]
        aoi_ring=tuple(ring),
        reference_lonlat=(lon, lat),
        crs_publish=str(entry.get("crs_publish", C.CRS_OUTPUT)),
        crs_analysis=str(entry.get("crs_analysis", "EPSG:4326")),
        revisit_days=int(entry.get("revisit_days", PROFILE.revisit_days or 12)),
        h3_resolution=int(processing.get("h3_resolution", 9)),
        limitations=tuple(str(x) for x in entry.get("limitations", ())),
        null_result=bool(entry.get("null_result", False)),
        detection_limit_mm_yr=(
            float(entry["detection_limit_mm_yr"])
            if entry.get("detection_limit_mm_yr") is not None
            else None
        ),
        aoi_source=aoi.source,
    )


@lru_cache(maxsize=8)
def aoi_cells(case_id: str) -> frozenset[str]:
    """H3 cells of the case AOI polygon (`aoi_polygon`, else the bbox) at `processing.h3_resolution`.

    This is the denominator of the coverage diagnostic: "how much of the city did the granule
    actually give us". Cached because the CDMX AOI is ~25k cells and every run asks the same thing.
    """
    cfg = load_case_config(case_id)
    ring = h3.LatLngPoly([(lat, lon) for lon, lat in cfg.aoi_ring])
    return frozenset(h3.polygon_to_cells(ring, cfg.h3_resolution))


# ---------------------------------------------------------------------------------------------
# Granule identity (track / frame / flight direction) from the filename
# ---------------------------------------------------------------------------------------------


def parse_granule_identity(granule: str | pathlib.Path) -> GranuleIdentity:
    """Parse `NISAR_L2_PR_GUNW_<track>_<frame>_<A|D>_…` from a granule name.

    Only the three fields the safety checks need are taken. A name that does not match the
    documented pattern yields ``None`` fields and a human-readable ``detail`` — the caller warns
    rather than inventing an identity. Track and flight direction cannot be read from the HDF5
    contents with the current reader, so the filename is the only evidence available; if the first
    real granule disagrees, correct `_GRANULE_RE` here and `docs/nisar-gunw-layout.md` together.
    """
    path = pathlib.Path(granule)
    match = _GRANULE_RE.match(path.stem)
    if match is None:
        return GranuleIdentity(
            name=path.name,
            track=None,
            frame=None,
            direction=None,
            detail=(
                "filename does not match NISAR_<L>_<TYPE>_<track>_<frame>_<A|D>_…; track and "
                "flight direction cannot be verified for this granule"
            ),
        )
    code = match.group("direction")
    return GranuleIdentity(
        name=path.name,
        track=int(match.group("track")),
        frame=int(match.group("frame")),
        direction=_DIRECTIONS[code],
        detail=f"track {int(match.group('track'))}, frame {int(match.group('frame'))}, {_DIRECTIONS[code]}",
    )


# ---------------------------------------------------------------------------------------------
# HDF5 metadata probe (incidence, heading, looks)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _GranuleProbe:
    incidence_deg: float | None
    incidence_source: str | None
    incidence_is_grid: bool
    heading_deg: float | None
    heading_source: str | None
    heading_is_grid: bool
    looks: float | None
    looks_source: str | None


def _open_hdf5(path: pathlib.Path) -> Any:
    try:
        import h5py  # noqa: PLC0415 - lazy, mirroring the reader's import discipline
    except Exception as exc:  # noqa: BLE001
        raise ProcessCaseError(
            "h5py is required to read granule metadata; it could not be imported "
            f"({exc}). Reference: {DOC_REFERENCE}"
        ) from exc
    try:
        return h5py.File(path, "r")
    except OSError as exc:
        raise ProcessCaseError(
            f"cannot reopen {str(path)!r} as HDF5: {exc} ({DOC_REFERENCE} §7)"
        ) from exc


def _probe_scalar(handle: Any, candidates: Sequence[Candidate]) -> tuple[float | None, str | None, bool]:
    """Return ``(value, source, is_grid)`` for the first readable candidate.

    ``is_grid`` is True when the dataset holds more than one value: a per-pixel incidence grid is
    NOT averaged into a scene scalar (that would silently redefine the vertical scale); the caller
    refuses and asks for the flag instead.
    """
    for candidate in candidates:
        if isinstance(candidate, tuple):
            group_path, attribute = candidate
            obj = handle.get(group_path)
            if obj is None or not hasattr(obj, "attrs") or attribute not in obj.attrs:
                continue
            raw, source = obj.attrs[attribute], f"{group_path}@{attribute}"
        else:
            obj = handle.get(candidate)
            if obj is None or not hasattr(obj, "shape"):
                continue
            try:
                raw = obj[()]
            except Exception:  # noqa: BLE001 - unreadable optional metadata is absent metadata
                continue
            source = candidate
        try:
            numbers = np.asarray(raw, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            continue
        if numbers.size == 0:
            continue
        if numbers.size > 1:
            return None, source, True
        if not np.isfinite(numbers[0]):
            continue
        return float(numbers[0]), source, False
    return None, None, False


def _probe_granule_metadata(path: pathlib.Path) -> _GranuleProbe:
    """One open, three probes. Absent metadata stays ``None``; nothing is synthesised."""
    with _open_hdf5(path) as handle:
        incidence, incidence_source, incidence_grid = _probe_scalar(handle, INCIDENCE_CANDIDATES)
        heading, heading_source, heading_grid = _probe_scalar(handle, HEADING_CANDIDATES)
        looks, looks_source, looks_grid = _probe_scalar(handle, LOOKS_CANDIDATES)
    if looks_grid:  # a per-pixel looks grid cannot debias a single coherence value
        looks, looks_source = None, None
    return _GranuleProbe(
        incidence_deg=incidence,
        incidence_source=incidence_source,
        incidence_is_grid=incidence_grid,
        heading_deg=heading,
        heading_source=heading_source,
        heading_is_grid=heading_grid,
        looks=looks,
        looks_source=looks_source,
    )


def _geometry_missing_message(name: str, angle: str, probe_grid: str | None) -> str:
    candidates = INCIDENCE_CANDIDATES if angle == "incidence" else HEADING_CANDIDATES
    found = f" A multi-pixel grid was found at {probe_grid!r}." if probe_grid else ""
    return (
        f"{angle} angle missing for {name}: no scalar {angle} metadata at "
        f"{', '.join(str(c) for c in candidates)}.{found} Pass --{angle} (degrees) explicitly — "
        f"a geometry is never invented, because cos(incidence) scales every millimetre "
        f"({DOC_REFERENCE} §3/§5)."
    )


def _resolve_geometry(
    name: str,
    probe: _GranuleProbe,
    incidence_flag: float | None,
    heading_flag: float | None,
    warnings: list[str],
) -> tuple[float, float, str]:
    """Metadata first, CLI flag second, refusal third. Returns ``(incidence, heading, source)``."""
    incidence = probe.incidence_deg
    incident_source = probe.incidence_source
    if probe.incidence_is_grid:
        raise GeometryRequiredError(
            _geometry_missing_message(name, "incidence", probe.incidence_source)
        )
    if incidence is None:
        if incidence_flag is None:
            raise GeometryRequiredError(_geometry_missing_message(name, "incidence", None))
        incidence, incident_source = float(incidence_flag), "CLI --incidence"
    elif incidence_flag is not None and abs(float(incidence) - float(incidence_flag)) > 0.5:
        warnings.append(
            f"{name}: incidence {float(incidence):.3f}° from {incident_source} differs from "
            f"--incidence {float(incidence_flag):.3f}°; the granule metadata wins"
        )

    heading = probe.heading_deg
    heading_source = probe.heading_source
    if probe.heading_is_grid:
        raise GeometryRequiredError(_geometry_missing_message(name, "heading", probe.heading_source))
    if heading is None:
        if heading_flag is None:
            raise GeometryRequiredError(_geometry_missing_message(name, "heading", None))
        heading, heading_source = float(heading_flag), "CLI --heading"
    elif heading_flag is not None and abs(
        ((float(heading) - float(heading_flag) + 180.0) % 360.0) - 180.0
    ) > 0.5:
        warnings.append(
            f"{name}: heading {float(heading):.3f}° from {heading_source} differs from "
            f"--heading {float(heading_flag):.3f}°; the granule metadata wins"
        )

    if not np.isfinite(incidence) or not (0.0 <= incidence < 90.0):
        raise GeometryRequiredError(
            f"{name}: incidence {incidence!r} is outside [0, 90); at 90° cos(θ) vanishes and the "
            "LOS→vertical inversion is undefined. Correct the granule metadata or --incidence."
        )
    if not np.isfinite(heading):
        raise GeometryRequiredError(f"{name}: heading is not finite ({heading!r})")
    if not (0.0 <= heading < 360.0):
        warnings.append(
            f"{name}: heading {heading:.3f}° normalized into [0, 360) as {heading % 360.0:.3f}°"
        )
        heading = heading % 360.0
    return float(incidence), float(heading), f"incidence:{incident_source}; heading:{heading_source}"


# ---------------------------------------------------------------------------------------------
# Stack assembly
# ---------------------------------------------------------------------------------------------


def _iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    match = _ISO_DATE_RE.match(str(value).strip())
    if match is None:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _days_between(first: str, second: str) -> int:
    return (dt.date.fromisoformat(second) - dt.date.fromisoformat(first)).days


@dataclass
class _StackGranule:
    path: pathlib.Path
    granule: G.GunwGranule
    identity: GranuleIdentity
    reference_date: str
    secondary_date: str
    incidence_deg: float
    heading_deg: float
    look_azimuth_deg: float  # 180 deg - heading_deg, normalised: what "look" convention uses
    geometry_source: str
    wavelength_m: float
    wavelength_source: str
    looks: float | None
    vertical_mm: np.ndarray  # vertical displacement of this pair, NaN where invalid
    coherence: np.ndarray  # bias-corrected (when possible) coherence, NaN where invalid


def _require_geolocation(records: Sequence[G.GunwGranule], warnings: list[str]) -> tuple[np.ndarray, np.ndarray, int]:
    """One common x/y/CRS for the whole stack, or a refusal. Resampling is out of scope."""
    first = records[0]
    for record in records[1:]:
        if record.phase_rad.shape != first.phase_rad.shape:
            raise ProcessCaseError(
                "stack does not share one pixel grid: "
                f"{first.source_path} is {first.phase_rad.shape}, {record.source_path} is "
                f"{record.phase_rad.shape}. Geocode the stack to a common grid first; this "
                "pipeline never resamples a phase grid implicitly."
            )
        if record.epsg != first.epsg:
            raise ProcessCaseError(
                "stack mixes CRSs: "
                f"{first.source_path} epsg={first.epsg}, {record.source_path} epsg={record.epsg}. "
                f"One CRS per stack ({DOC_REFERENCE} §3)."
            )
        for axis, a, b in (("x", first.x, record.x), ("y", first.y, record.y)):
            if (a is None) != (b is None):
                raise ProcessCaseError(
                    f"stack geolocation is inconsistent on the {axis} axis: one granule has it and "
                    "another does not; cannot place the H3 grid"
                )
            if a is not None and not np.allclose(a, b, rtol=0.0, atol=1e-6):
                raise ProcessCaseError(
                    f"stack {axis} coordinates differ between granules; geocode once, then process"
                )
    if first.x is None or first.y is None:
        raise ProcessCaseError(
            "granule geolocation is missing (x/y coordinate arrays absent), so pixels cannot be "
            "placed on the H3 grid or referenced to config/study_cases.yaml:reference_point. Run "
            f"pipeline/ingest/inspect_hdf5.py on the granule and correct {DOC_REFERENCE} §3."
        )
    if first.epsg is None:
        raise ProcessCaseError(
            "granule CRS is missing (no parseable EPSG), so lon/lat cannot be derived for H3. Run "
            f"pipeline/ingest/inspect_hdf5.py and correct {DOC_REFERENCE} §3; do not assume a zone."
        )
    x = np.asarray(first.x, dtype=float)
    y = np.asarray(first.y, dtype=float)
    if x.size != first.phase_rad.shape[1] or y.size != first.phase_rad.shape[0]:
        raise ProcessCaseError(
            f"x/y lengths ({x.size}, {y.size}) do not match the phase grid "
            f"{first.phase_rad.shape}; the pyramid is misaligned"
        )
    warnings.extend(_dedupe(message for record in records for message in record.warnings))
    return x, y, int(first.epsg)


def _dedupe(messages: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for message in messages:
        seen.setdefault(str(message), None)
    return list(seen)


def _check_stack_identity(identities: Sequence[GranuleIdentity], warnings: list[str]) -> None:
    """Refuse a stack that mixes tracks or flight directions; warn when identity is unparseable."""
    parsed = [identity for identity in identities if identity.track is not None]
    unparsed = [identity for identity in identities if identity.track is None]
    if unparsed:
        warnings.append(
            "granule identity unparseable for "
            + ", ".join(identity.name for identity in unparsed)
            + ": track/frame/flight-direction consistency could not be verified from the filename "
            f"(see {DOC_REFERENCE}; correct _GRANULE_RE in pipeline/process_case.py on the first "
            "real granule if the naming differs)"
        )
    tracks = {identity.track for identity in parsed}
    directions = {identity.direction for identity in parsed}
    if len(tracks) > 1:
        detail = ", ".join(f"{identity.name} -> {identity.detail}" for identity in parsed)
        raise TrackMismatchError(
            f"stack mixes relative orbits (tracks) {sorted(tracks)}: {detail}. A time series must "
            "stay on one track; process each track into its own case directory."
        )
    if len(directions) > 1:
        detail = ", ".join(f"{identity.name} -> {identity.detail}" for identity in parsed)
        raise TrackMismatchError(
            f"stack mixes flight directions {sorted(directions)}: {detail}. Ascending and "
            "descending look vectors are not interchangeable (docs/nisar-gunw-layout.md §4); "
            "process each direction separately."
        )
    frames = {identity.frame for identity in parsed}
    if len(frames) > 1:
        warnings.append(
            f"granules span frames {sorted(frames)} on one track; the pixel grid must still match "
            "(it is checked explicitly) but the AOI may be clipped"
        )


def _read_land_use(path: pathlib.Path | str | None, cell_ids: Sequence[str], warnings: list[str]) -> list[str]:
    """Join `cell_id,land_use` from a sidecar; everything absent is `unknown`, never invented."""
    if path is None:
        return ["unknown"] * len(cell_ids)
    sidecar = pathlib.Path(path)
    if not sidecar.is_file():
        raise LandUseError(f"--land-use-csv {str(sidecar)!r} does not exist")
    try:
        frame = pd.read_csv(sidecar, dtype={"cell_id": str, "land_use": str})
    except Exception as exc:  # noqa: BLE001
        raise LandUseError(f"cannot read --land-use-csv {str(sidecar)!r}: {exc}") from exc
    missing = {"cell_id", "land_use"} - set(frame.columns)
    if missing:
        raise LandUseError(
            f"--land-use-csv {str(sidecar)!r} is missing column(s) {sorted(missing)}; expected the "
            "sidecar `cell_id,land_use` (contract §6)"
        )
    if frame["cell_id"].duplicated().any():
        duplicates = sorted(frame.loc[frame["cell_id"].duplicated(), "cell_id"].astype(str))[:3]
        raise LandUseError(
            f"--land-use-csv {str(sidecar)!r} repeats cell_id(s) {duplicates}; one label per cell"
        )
    labels = set(frame["land_use"].dropna().astype(str))
    undeclared = labels - set(C.LAND_USE)
    if undeclared:
        raise LandUseError(
            f"--land-use-csv uses undeclared land_use value(s) {sorted(undeclared)}; allowed "
            f"{list(C.LAND_USE)} (contract §7.1)"
        )
    mapping = dict(zip(frame["cell_id"].astype(str), frame["land_use"].astype(str), strict=True))
    mapped = [mapping.get(str(cell), "unknown") for cell in cell_ids]
    unused = sorted(set(mapping) - {str(cell) for cell in cell_ids})
    if unused:
        warnings.append(
            f"--land-use-csv has {len(unused)} cell_id(s) that are not cells of this grid "
            f"(e.g. {unused[:3]}); ignored"
        )
    unknown = sum(1 for label in mapped if label == "unknown")
    if unknown:
        warnings.append(
            f"{unknown} of {len(mapped)} H3 cells have no land_use label in the sidecar; they are "
            "published as 'unknown' rather than guessed"
        )
    return mapped


def _harmonic_order(n_epochs: int, span_years: float) -> int:
    """Number of annual harmonics the fit can honestly identify on this span (README §6).

    Fewer epochs than columns would make `numpy.linalg.lstsq` return a minimum-norm solution and a
    meaningless ``r2``; `transforms.harmonic_fit` refuses that itself. Here the *choice* is made
    explicit: two harmonics only from a one-year span with ≥6 epochs, one harmonic from a six-month
    span, and a pure linear model below that. A short stack must not pretend to resolve an annual
    cycle (the docstring of `harmonic_fit` makes the same argument).
    """
    if span_years >= 1.0 and n_epochs >= 6:
        return 2
    if span_years >= 0.5 and n_epochs >= 4:
        return 1
    return 0


def _residual_sigma(series: np.ndarray, fit: V.HarmonicFit) -> float:
    """Per-cell displacement noise (mm) from the fitted residual sum of squares.

    ``r2 = 1 - SS_res/SS_tot`` is computed by `harmonic_fit`, so ``SS_res = (1 - r2)·SS_tot`` and
    ``sigma = sqrt(SS_res / (n - p))``. A perfect fit gives 0 (noiseless fixture), a constant or
    undefined ``r2`` gives NaN and is excluded from the median rather than counted as 0 noise.
    """
    finite = np.isfinite(series)
    n = int(fit.n_points)
    p = int(np.asarray(fit.coefficients).size)
    if n <= p or not np.isfinite(fit.r2):
        return float("nan")
    values = np.asarray(series, dtype=float)[finite]
    centred = values - values.mean()
    ss_total = float(centred @ centred)
    ss_res = max(0.0, (1.0 - float(fit.r2)) * ss_total)
    return float(math.sqrt(ss_res / (n - p)))


# ---------------------------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------------------------


def _guard_existing_tree(out_dir: pathlib.Path, *, force: bool) -> None:
    """Never silently replace the committed mock: `product == "mock"` needs an explicit --force."""
    meta_path = out_dir / "meta.json"
    if force or not meta_path.is_file():
        return
    try:
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable existing meta is reported, not assumed
        raise ProcessCaseError(
            f"{meta_path} exists but cannot be parsed ({exc}); refusing to overwrite an unknown "
            "artefact set — move it aside, or pass --force"
        ) from exc
    if isinstance(existing, Mapping) and existing.get("product") == "mock":
        raise MockOverwriteError(
            f"{out_dir} already holds a committed mock artefact set (meta.json product='mock'). "
            "The demo is never silently replaced: archive it first, choose a different --out, or "
            "pass --force to overwrite deliberately."
        )


def process_case(
    granules: Sequence[str | pathlib.Path] | str | pathlib.Path,
    case_id: str,
    *,
    out_dir: str | pathlib.Path,
    incidence_deg: float | None = None,
    heading_deg: float | None = None,
    polarisation: str | None = None,
    land_use_csv: str | pathlib.Path | None = None,
    provenance_overrides: Mapping[str, Any] | None = None,
    force: bool = False,
    los_sign: str | None = None,
    heading_convention: str | None = None,
    method_notes: Sequence[str] | None = None,
    validated_against: str | Sequence[str] | None = None,
    assumed_v_east_mm_yr: float | np.ndarray = 0.0,
    assumed_v_north_mm_yr: float | np.ndarray = 0.0,
) -> ProcessedCase:
    """Run granule → H3 → artefacts for one case, in memory (writing is `write_artefacts`).

    ``force`` (an extra keyword beyond the agreed signature) is what the CLI's ``--force`` gates:
    it permits replacing a directory whose ``meta.json`` says ``product == "mock"``.

    ``los_sign`` / ``heading_convention`` / ``method_notes`` / ``validated_against`` are the four
    convention inputs, the API side of ``--los-sign``, ``--heading-convention``, ``--method-note``
    and ``--validated-against``; :func:`_resolve_convention` validates them and the module
    docstring explains the arithmetic. Both text inputs are repeatable (runbook §7 records every
    check); with all four left as ``None``/empty the run behaves exactly
    as before: ``d_LOS = -lambda/(4*pi)*phi``, the platform heading passed through verbatim, and the
    "NOT yet validated against GNSS" hedge kept in ``meta.provenance.method``.

    **These flags are inputs, so they reach the provenance id.** A run with a different
    ``los_sign``/``heading_convention`` is a different physical claim about the same bytes and gets
    a different ``provenance_id``; that is deliberate, not drift (see `_provenance_id`). Prose-only
    flags (``--method-note``, ``--validated-against``) leave the id alone.

    ``assumed_v_east_mm_yr`` / ``assumed_v_north_mm_yr`` are the **trusted horizontal velocity**
    that `transforms.vertical_from_los` needs (scalar for one scene-wide assumption, or a grid from
    a GNSS model), in mm/yr; each pair's span scales them into that pair's displacement. They are
    API-only on purpose: the runbook has no GNSS horizontal model yet, and a horizontal assumption
    is evidence, not a knob to tune until the number looks right. Defaults 0.0 are today's
    ``v_north = 0`` and no east assumption, which leave the arithmetic bit-for-bit unchanged — and
    which also mean ``--heading-convention`` cannot move a published number, because ``u_up =
    cos(theta)`` is heading-independent and the correction terms are exactly zero. Supply a
    horizontals grid and the convention becomes visible; it is recorded in
    ``meta.provenance.geometry_convention`` and enters the provenance id either way.

    Thin wrapper: it owns the warning buffer and attaches it to a refusal, so nothing learned
    before the failure is lost.
    """
    warnings: list[str] = []
    try:
        return _process_case_rows(
            granules,
            case_id,
            out_dir=out_dir,
            incidence_deg=incidence_deg,
            heading_deg=heading_deg,
            polarisation=polarisation,
            land_use_csv=land_use_csv,
            provenance_overrides=provenance_overrides,
            force=force,
            los_sign=los_sign,
            heading_convention=heading_convention,
            method_notes=method_notes,
            validated_against=validated_against,
            assumed_v_east_mm_yr=assumed_v_east_mm_yr,
            assumed_v_north_mm_yr=assumed_v_north_mm_yr,
            warnings=warnings,
        )
    except ProcessCaseError as exc:
        exc.warnings = list(warnings)
        raise


def _process_case_rows(
    granules: Sequence[str | pathlib.Path] | str | pathlib.Path,
    case_id: str,
    *,
    out_dir: str | pathlib.Path,
    incidence_deg: float | None,
    heading_deg: float | None,
    polarisation: str | None,
    land_use_csv: str | pathlib.Path | None,
    provenance_overrides: Mapping[str, Any] | None,
    force: bool,
    los_sign: str | None,
    heading_convention: str | None,
    method_notes: Sequence[str] | None,
    validated_against: str | Sequence[str] | None,
    assumed_v_east_mm_yr: float | np.ndarray,
    assumed_v_north_mm_yr: float | np.ndarray,
    warnings: list[str],
) -> ProcessedCase:
    """Implementation of :func:`process_case`; `warnings` is owned by the caller."""
    if isinstance(granules, (str, pathlib.Path)):
        granules = [granules]
    paths = [pathlib.Path(granule) for granule in granules]
    if not paths:
        raise ProcessCaseError("no granules given: pass at least one --granule")

    # Convention flags first: an unparseable convention is a refusal before anything is read, and
    # its warnings must reach the caller through the same buffer as everything else.
    convention = _resolve_convention(
        los_sign=los_sign,
        heading_convention=heading_convention,
        method_notes=method_notes,
        validated_against=validated_against,
        warnings=warnings,
    )

    out_path = pathlib.Path(out_dir)
    _guard_existing_tree(out_path, force=force)

    cfg = load_case_config(case_id)
    if len(paths) == 1:
        warnings.append(
            "single granule: the stack is one interferometric pair (2 epochs) — it produces one "
            "pair and no time series. Chaining needs at least 4 epochs to fit velocity_mm_yr "
            f"(transforms.MIN_FIT_POINTS={V.MIN_FIT_POINTS}); download more consecutive pairs."
        )

    identities = [parse_granule_identity(path) for path in paths]
    _check_stack_identity(identities, warnings)

    # ---- 1. read every granule and drop invalid pixels --------------------------------------
    probes: dict[pathlib.Path, _GranuleProbe] = {}
    loaded: list[G.GunwGranule] = []
    for path in paths:
        try:
            granule = G.read_granule(path, polarisation=polarisation)
        except G.GranuleError as exc:
            message = str(exc)
            if "required GUNW dataset not found" in message or "required GUNW group" in message:
                raise MissingLayerError(
                    f"missing required layer in {path.name}: {message.rstrip('.')}. "
                    "A GUNW without the phase "
                    "or mask layer cannot produce displacement; re-download the granule or correct "
                    "the DATASETS table from evidence (pipeline/ingest/inspect_hdf5.py)."
                ) from exc
            raise ProcessCaseError(f"{path.name}: {message}") from exc
        loaded.append(granule)
        probes[path] = _probe_granule_metadata(path)

    polarisations = {granule.polarisation for granule in loaded}
    if len(polarisations) > 1:
        detail = ", ".join(
            f"{path.name} -> {granule.polarisation}" for path, granule in zip(paths, loaded, strict=True)
        )
        raise PolarisationMismatchError(
            f"polarisation changes mid-stack ({sorted(polarisations)}): {detail}. HH and VV carry "
            "different phase statistics and cannot be chained; process each polarisation "
            "separately (docs/nisar-gunw-layout.md §1)."
        )
    polarisation_used = next(iter(polarisations))

    # Trusted horizontals: the input that makes `--heading-convention` observable (see the module
    # docstring). Validated here, once, against the actual phase grid; defaults stay exactly 0.0.
    assumed_east_grid, assumed_north_grid = _resolve_horizontal_assumptions(
        assumed_v_east_mm_yr, assumed_v_north_mm_yr, loaded[0].phase_rad.shape
    )

    # ---- 2. dates, geometry, wavelength, LOS→vertical ---------------------------------------
    records: list[_StackGranule] = []
    wavelength_notes: list[str] = []
    geometry_notes: list[str] = []
    wavelength_missing: list[str] = []
    looks_missing: list[str] = []
    for path, granule, identity in zip(paths, loaded, identities, strict=True):
        reference_date = _iso_date(granule.reference_time)
        secondary_date = _iso_date(granule.secondary_time)
        if reference_date is None or secondary_date is None:
            raise ProcessCaseError(
                f"{path.name}: acquisition dates incomplete (reference={granule.reference_time!r}, "
                f"secondary={granule.secondary_time!r}); an un-dated pair cannot be ordered into a "
                f"time series ({DOC_REFERENCE} §5.1)"
            )
        if secondary_date <= reference_date:
            raise ProcessCaseError(
                f"{path.name}: secondary acquisition {secondary_date} is not after the reference "
                f"{reference_date}; the interferogram sign would be inverted"
            )

        incidence, heading, geometry_source = _resolve_geometry(
            path.name, probes[path], incidence_deg, heading_deg, warnings
        )

        wavelength = granule.wavelength_m
        if wavelength is None:
            wavelength = DEFAULT_WAVELENGTH_M
            wavelength_missing.append(path.name)
            source = f"documented-constant λ={DEFAULT_WAVELENGTH_M} m ({DOC_REFERENCE} §4)"
        else:
            source = granule.wavelength_source or "metadata"
        wavelength_notes.append(source)
        geometry_notes.append(geometry_source)

        valid = G.valid_mask(granule)
        if not valid.any():
            raise MissingLayerError(
                f"{path.name}: no valid pixel (mask != 127 and finite phase/coherence). The layers "
                "exist but carry no data — the granule cannot support this AOI."
            )

        # ---- the two convention decisions this run makes, both recorded in provenance ----------
        # (a) LOS sign: reaches nisar_gunw.phase_to_los_displacement_mm(sign=...). The default
        #     -1.0 is bit-for-bit the historical arithmetic; +1.0 flips subsidence to positive.
        # (b) heading: `platform` passes the granule heading through verbatim (historical
        #     behaviour); `look` feeds the effective look azimuth 180 - heading to los_to_vertical.
        #     u_up = cos(theta) is heading-independent, so the mapping is felt **only** through the
        #     trusted horizontals (u_east flips sign, u_north does not): with the default zero
        #     assumptions below, the two conventions produce identical numbers in this chain — that
        #     identity is the single-geometry ambiguity, and it is asserted in the tests rather than
        #     papered over.
        effective_heading = (
            _look_azimuth(heading)
            if convention.heading_convention == "look"
            else heading
        )
        los = G.phase_to_los_displacement_mm(
            granule.phase_rad, wavelength, sign=convention.sign
        )
        pair_span_years = _days_between(reference_date, secondary_date) / V.DAYS_PER_YEAR
        vertical = V.vertical_from_los(
            los,
            incidence,
            effective_heading,
            # `vertical_from_los` takes the *pair's* displacement, so a mm/yr assumption is scaled
            # by this pair's span; a 0.0 assumption stays exactly 0.0 either way.
            assume_vnorth=assumed_north_grid * pair_span_years,
            v_east=assumed_east_grid * pair_span_years,
        )

        coherence = np.asarray(granule.coherence, dtype=float)
        looks = probes[path].looks
        if looks is not None and looks > 1.0:
            coherence = V.coherence_bias_correction(coherence, looks)
        else:
            looks_missing.append(path.name)

        keep = valid & (coherence >= PROFILE.coherence_min)
        if not keep.any():
            raise MissingLayerError(
                f"{path.name}: no pixel passes valid_mask and the frozen coherence_min="
                f"{PROFILE.coherence_min} for {PRODUCT}; the interferogram is not usable here"
            )

        vertical = np.where(keep, vertical, np.nan)
        coherence = np.where(keep, coherence, np.nan)

        records.append(
            _StackGranule(
                path=path,
                granule=granule,
                identity=identity,
                reference_date=reference_date,
                secondary_date=secondary_date,
                incidence_deg=incidence,
                heading_deg=heading,
                look_azimuth_deg=_look_azimuth(heading),
                geometry_source=geometry_source,
                wavelength_m=float(wavelength),
                wavelength_source=source,
                looks=float(looks) if looks is not None else None,
                vertical_mm=vertical,
                coherence=coherence,
            )
        )

    if wavelength_missing:
        warnings.append(
            f"no wavelength metadata in {len(wavelength_missing)} of {len(records)} granule(s) "
            f"({', '.join(wavelength_missing[:3])}{', …' if len(wavelength_missing) > 3 else ''}): "
            f"using the documented L-band constant λ = {DEFAULT_WAVELENGTH_M} m from "
            f"{DOC_REFERENCE} §4. The assumption is recorded in meta.provenance and must be "
            "replaced by metadata on the first real granule."
        )
    if looks_missing:
        warnings.append(
            f"{len(looks_missing)} of {len(records)} granule(s) declare no effective number of "
            "looks; coherence is published without the finite-look bias correction (contract §3 "
            "asks for a bias-corrected mean — record the correction on the first real granule)"
        )

    # ---- 3. geolocation + stack order -------------------------------------------------------
    x, y, epsg = _require_geolocation(loaded, warnings)
    records.sort(key=lambda record: (record.secondary_date, record.reference_date, record.path.name))

    epochs: list[str] = [records[0].reference_date]
    epoch_index: dict[str, int] = {epochs[0]: 0}
    cubes: list[np.ndarray] = [np.zeros(loaded[0].phase_rad.shape, dtype=float)]
    previous_secondary: str | None = None
    seen_pairs: dict[tuple[str, str], str] = {}
    for record in records:
        pair = (record.reference_date, record.secondary_date)
        if pair in seen_pairs:
            warnings.append(
                f"{record.path.name}: duplicate pair {pair[0]}→{pair[1]} already provided by "
                f"{seen_pairs[pair]}; keeping the first occurrence"
            )
            continue
        seen_pairs[pair] = record.path.name
        if previous_secondary is not None and record.reference_date != previous_secondary:
            warnings.append(
                f"granule network gap: {previous_secondary} → {record.reference_date} is not "
                f"covered by any pair (previous pair ended {previous_secondary}, next starts "
                f"{record.reference_date}); consecutive granules must share a date to chain"
            )
        if record.reference_date not in epoch_index:
            raise ProcessCaseError(
                f"stack is not a connected network at {record.path.name}: its reference date "
                f"{record.reference_date} is not an epoch of the chain {epochs}. Cumulative "
                "displacement cannot be summed across a broken network; download the missing pair "
                "or process the runs as separate cases."
            )
        base = cubes[epoch_index[record.reference_date]]
        total = base + record.vertical_mm
        if record.secondary_date in epoch_index:
            warnings.append(
                f"{record.path.name}: secondary date {record.secondary_date} already exists in the "
                "chain; the duplicate increment is ignored"
            )
        else:
            epoch_index[record.secondary_date] = len(epochs)
            epochs.append(record.secondary_date)
            cubes.append(total)
        previous_secondary = record.secondary_date

    if len(epochs) < V.MIN_FIT_POINTS:
        span_days = _days_between(epochs[0], epochs[-1])
        raise ProcessCaseError(
            f"stack has {len(epochs)} epoch(s) spanning {span_days} day(s): a trend needs at least "
            f"{V.MIN_FIT_POINTS} (transforms.MIN_FIT_POINTS) so the least-squares fit is not "
            "underdetermined. Add consecutive granules (12-day pairs) until the chain covers ≥4 "
            "epochs; a single pair can only publish one displacement, not a velocity."
        )
    order = np.argsort(epochs)
    epochs = [epochs[i] for i in order]
    cube = np.stack([cubes[i] for i in order])
    t_years = np.array([_days_between(epochs[0], date) for date in epochs], dtype=float) / V.DAYS_PER_YEAR
    for index in range(1, len(epochs)):
        step = _days_between(epochs[index - 1], epochs[index])
        if step != cfg.revisit_days:
            warnings.append(
                f"acquisition gap: {epochs[index - 1]} → {epochs[index]} is {step} days, not the "
                f"{cfg.revisit_days}-day revisit; the cumulative displacement spans the gap and no "
                "pair exists inside it"
            )

    # ---- 4. spatial referencing (v_north = 0) ------------------------------------------------
    try:
        from pyproj import Transformer  # noqa: PLC0415 - geopandas dependency, keep import local
    except Exception as exc:  # noqa: BLE001
        raise ProcessCaseError(
            f"pyproj is required to place the AOI and the reference pixel ({exc})"
        ) from exc
    forward = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    inverse = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)

    # Pixels -> H3 first: it is geometry only, and it tells us whether these granules belong to
    # this case before we spend the run referencing a stack that covers the wrong continent.
    lon2d, lat2d = inverse.transform(*np.meshgrid(x, y))
    flat_cells = [
        h3.latlng_to_cell(float(lat), float(lon), cfg.h3_resolution)
        for lat, lon in zip(lat2d.ravel(), lon2d.ravel(), strict=True)
    ]
    cell_ids = sorted(set(flat_cells))
    aoi = aoi_cells(cfg.case_id)
    outside = [cell for cell in cell_ids if cell not in aoi]
    if not (set(cell_ids) & aoi):
        raise ProcessCaseError(
            f"the granule footprint does not intersect the {cfg.case_id} AOI at all (0 of "
            f"{len(cell_ids)} H3 cells are inside the configured polygon). The case_id and the "
            "granules disagree; pass the slug the footprint actually belongs to."
        )
    if outside:
        warnings.append(
            f"{len(outside)} H3 cell(s) with data lie outside the {cfg.case_id} AOI polygon and are "
            "not published (the case extent is config/study_cases.yaml, not the granule footprint)"
        )

    ref_lon, ref_lat = cfg.reference_lonlat
    ref_x, ref_y = forward.transform(ref_lon, ref_lat)
    if not (np.isfinite(ref_x) and np.isfinite(ref_y)):
        raise ProcessCaseError(
            f"reference_point {(ref_lon, ref_lat)} does not project into EPSG:{epsg} "
            f"({cfg.crs_analysis} expected); check config/study_cases.yaml"
        )
    ref_ix = int(np.argmin(np.abs(x - ref_x)))
    ref_iy = int(np.argmin(np.abs(y - ref_y)))
    offset_m = float(math.hypot(x[ref_ix] - ref_x, y[ref_iy] - ref_y))
    if offset_m > 1000.0:
        warnings.append(
            f"reference pixel is {offset_m:.0f} m from reference_point {(ref_lon, ref_lat)}; the "
            "granule footprint does not reach the configured reference location"
        )
    reference_series = cube[:, ref_iy, ref_ix]
    if not np.all(np.isfinite(reference_series)):
        raise ProcessCaseError(
            f"reference pixel (row {ref_iy}, col {ref_ix}, nearest to {(ref_lon, ref_lat)}) has no "
            "valid data in at least one granule; the series cannot be referenced. Choose a "
            "reference_point inside the granule footprint (config/study_cases.yaml)."
        )
    cube = cube - reference_series[:, None, None]
    first_epoch_values = cube[0][np.isfinite(cube[0])]
    if first_epoch_values.size and float(np.max(np.abs(first_epoch_values))) > 1e-9:
        raise ProcessCaseError(
            "internal invariant violated: the first epoch is not referenced to 0 mm after spatial "
            f"referencing (max |value| {float(np.max(np.abs(first_epoch_values))):.3e} mm)"
        )

    # ---- 5. H3 aggregation (mean; nodata absent) --------------------------------------------
    position = {cell: index for index, cell in enumerate(cell_ids)}
    values = np.full((len(cell_ids), len(epochs)), np.nan, dtype=float)
    coherence_sum = np.zeros(len(cell_ids), dtype=float)
    coherence_n = np.zeros(len(cell_ids), dtype=int)

    for epoch in range(len(epochs)):
        frame = V.h3_aggregate(
            flat_cells, cube[epoch].ravel(), cfg.h3_resolution, mean_column="value"
        )
        for cell, value in zip(frame["cell_id"], frame["value"], strict=True):
            values[position[str(cell)], epoch] = float(value)

    for record in records:  # coherence: bias-corrected per granule, mean over epochs and pixels
        frame = V.h3_aggregate(
            flat_cells, record.coherence.ravel(), cfg.h3_resolution, mean_column="coh"
        )
        for cell, value in zip(frame["cell_id"], frame["coh"], strict=True):
            coherence_sum[position[str(cell)]] += float(value)
            coherence_n[position[str(cell)]] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        coherence = np.where(coherence_n > 0, coherence_sum / np.maximum(coherence_n, 1), np.nan)

    # ---- 6. drop cells outside the case AOI, then per-cell fit -------------------------------
    # The case is the AOI, not the granule footprint: publishing cells outside the configured
    # polygon would contradict meta.bbox and inflate every statistic with foreign ground.
    inside_indices = [
        index for index, cell in enumerate(cell_ids) if cell in aoi
    ]
    cell_ids = [cell_ids[index] for index in inside_indices]
    values = values[inside_indices]
    coherence = coherence[inside_indices]

    harmonics = _harmonic_order(len(epochs), float(t_years[-1]))
    dropped = {"incomplete_endpoints": 0, "too_few_epochs": 0, "rank_deficient": 0, "no_coherence": 0}
    kept: list[int] = []
    velocities: list[float] = []
    sigmas: list[float] = []
    for index, cell in enumerate(cell_ids):
        series = values[index]
        if not np.isfinite(coherence[index]):
            dropped["no_coherence"] += 1
            continue
        if not (np.isfinite(series[0]) and np.isfinite(series[-1])):
            dropped["incomplete_endpoints"] += 1
            continue
        if int(np.isfinite(series).sum()) < V.MIN_FIT_POINTS:
            dropped["too_few_epochs"] += 1
            continue
        try:
            fit = V.harmonic_fit(t_years, series, n_harmonics=harmonics)
        except ValueError:
            dropped["rank_deficient"] += 1
            continue
        kept.append(index)
        velocities.append(float(fit.trend_mm_yr))
        sigmas.append(_residual_sigma(series, fit))

    if not kept:
        raise ProcessCaseError(
            "no H3 cell has a complete series with at least "
            f"{V.MIN_FIT_POINTS} epochs (dropped: {dropped}). The stack cannot publish a grid; "
            "check the mask/coherence over the AOI before retrying."
        )

    kept_values = values[kept]
    kept_cells = [cell_ids[index] for index in kept]
    kept_coherence = np.clip(coherence[kept], 0.0, 1.0)
    velocity = np.asarray(velocities, dtype=float)
    land_use = _read_land_use(land_use_csv, kept_cells, warnings)

    centroids = [h3.cell_to_latlng(cell) for cell in kept_cells]
    lats = np.array([lat for lat, _ in centroids], dtype=float)
    lons = np.array([lon for _, lon in centroids], dtype=float)
    val_t1 = kept_values[:, 0].copy()
    val_t2 = kept_values[:, -1].copy()
    delta = val_t2 - val_t1
    risk = [C.risk_level_from_velocity(float(v)) for v in velocity]
    provenance = _provenance_id(
        cfg.case_id, paths, convention, _horizontals_token(assumed_east_grid, assumed_north_grid)
    )

    grid = _ordered_grid(
        kept_cells,
        lats,
        lons,
        val_t1,
        val_t2,
        delta,
        velocity,
        kept_coherence,
        land_use,
        risk,
        cfg.case_id,
        epochs[-1],
        provenance,
    ).sort_values("cell_id", kind="stable", ignore_index=True)

    # ---- 7. timeseries (AOI aggregate) ------------------------------------------------------
    aoi_series = kept_values.mean(axis=0)
    if abs(float(aoi_series[0])) > 1e-12:  # guaranteed by the referencing invariant
        raise ProcessCaseError(
            f"internal invariant violated: the AOI series starts at {float(aoi_series[0])!r} mm, "
            "not 0 (the series must be referenced to the first epoch)"
        )
    quality = _quality_from_coherence(float(kept_coherence.mean()))
    timeseries = pd.DataFrame(
        {
            "case_id": np.full(len(epochs), cfg.case_id, dtype=object),
            "cell_id": np.full(len(epochs), "aoi", dtype=object),
            "date": np.array(epochs, dtype=object),
            "value": aoi_series,
            "unit": np.full(len(epochs), C.UNIT, dtype=object),
            "sensor": np.full(len(epochs), SENSOR, dtype=object),
            "product": np.full(len(epochs), PRODUCT, dtype=object),
            "quality": np.full(len(epochs), quality, dtype=object),
            "provenance_id": np.full(len(epochs), provenance, dtype=object),
        },
        columns=[f.name for f in C.TIMESERIES_FIELDS],
    )

    # ---- 8. coverage diagnostic -------------------------------------------------------------
    covered = set(kept_cells)
    coverage = Coverage(
        aoi_cells=len(aoi),
        covered_cells=len(covered),
        covered_in_aoi=len(aoi & covered),
        covered_outside_aoi=len(outside),  # dropped above: data outside the declared AOI
        fraction=(len(aoi & covered) / len(aoi)) if aoi else 0.0,
    )
    if coverage.below_threshold:
        warnings.append(
            f"COVERAGE: only {coverage.fraction:.1%} of the {cfg.case_id} AOI's {coverage.aoi_cells} "
            f"H3 cells received data ({coverage.covered_in_aoi} cells, threshold "
            f"{COVERAGE_WARNING_FRACTION:.0%}). Do NOT claim city-wide displacement for this case; "
            f"see {DATA_SOURCE_DECISION} (GUNW is provisional and cryosphere-derived) and consider "
            "the declared fallback."
        )
    # ---- 9. meta ----------------------------------------------------------------------------
    span_years = float(t_years[-1])
    sigma_median = float(np.nanmedian(np.asarray(sigmas, dtype=float)))
    stats = _stats(
        velocity=velocity,
        cells=kept_cells,
        n_dates=len(epochs),
        dropped=dropped,
        coverage=coverage,
        span_years=span_years,
        n_epochs=len(epochs),
        sigma_median=sigma_median,
        case=cfg,
    )
    meta = _meta(
        cfg=cfg,
        epochs=epochs,
        records=records,
        paths=paths,
        polarisation=polarisation_used,
        provenance_id=provenance,
        stats=stats,
        coverage=coverage,
        harmonics=harmonics,
        geometry_notes=geometry_notes,
        wavelength_notes=wavelength_notes,
        warnings=warnings,
        reference_offset_m=offset_m,
        provenance_overrides=provenance_overrides,
        convention=convention,
        assumed_v_east_mm_yr=_assumption_summary(assumed_east_grid),
        assumed_v_north_mm_yr=_assumption_summary(assumed_north_grid),
    )

    context = _context(cfg, kept_cells, land_use, provenance, warnings)

    return ProcessedCase(
        case_id=cfg.case_id,
        grid=grid,
        timeseries=timeseries,
        context=context,
        meta=meta,
        coverage=coverage,
        epochs=tuple(epochs),
        provenance_id=provenance,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------------------------
# Frames, stats, meta, context
# ---------------------------------------------------------------------------------------------


def _ordered_grid(
    cells: Sequence[str],
    lats: np.ndarray,
    lons: np.ndarray,
    val_t1: np.ndarray,
    val_t2: np.ndarray,
    delta: np.ndarray,
    velocity: np.ndarray,
    coherence: np.ndarray,
    land_use: Sequence[str],
    risk: Sequence[str],
    case_id: str,
    acquisition_date: str,
    provenance: str,
) -> pd.DataFrame:
    """The contract's column order, exactly (`contracts.GRID_FIELDS`)."""
    columns = {
        "cell_id": np.array(cells, dtype=object),
        "lat": lats.astype("float64"),
        "lon": lons.astype("float64"),
        "val_t1": val_t1.astype("float64"),
        "val_t2": val_t2.astype("float64"),
        "delta_change": delta.astype("float64"),
        "velocity_mm_yr": velocity.astype("float64"),
        "coherence": coherence.astype("float64"),
        "land_use": np.array(land_use, dtype=object),
        "risk_level": np.array(risk, dtype=object),
        "case_id": np.full(len(cells), case_id, dtype=object),
        "sensor": np.full(len(cells), SENSOR, dtype=object),
        "product": np.full(len(cells), PRODUCT, dtype=object),
        "unit": np.full(len(cells), C.UNIT, dtype=object),
        "velocity_unit": np.full(len(cells), C.VELOCITY_UNIT, dtype=object),
        "source": np.full(len(cells), SOURCE_PROVIDER, dtype=object),
        "acquisition_date": np.full(len(cells), acquisition_date, dtype=object),
        "provenance_id": np.full(len(cells), provenance, dtype=object),
    }
    return pd.DataFrame(columns, columns=[f.name for f in C.GRID_FIELDS])


def _quality_from_coherence(coherence: float) -> str:
    """Reuse the mock's single mapping of coherence → the frozen `quality` enum (read-only import).

    Imported lazily so this module's import does not depend on the synthetic-model package.
    """
    from pipeline.mock.model import quality_from_coherence  # noqa: PLC0415

    return quality_from_coherence(coherence)


def _stats(
    *,
    velocity: np.ndarray,
    cells: Sequence[str],
    n_dates: int,
    dropped: Mapping[str, int],
    coverage: Coverage,
    span_years: float,
    n_epochs: int,
    sigma_median: float,
    case: CaseConfig,
) -> dict[str, Any]:
    """`meta.stats`: contract keys from `velocity_metrics` + the diagnostics a judge will ask for."""
    areas = np.array([h3.cell_area(cell, unit="m^2") for cell in cells], dtype=float)
    try:
        metrics = V.velocity_metrics(velocity, areas)
    except ValueError as exc:
        raise ProcessCaseError(f"cannot compute velocity statistics: {exc}") from exc
    stats: dict[str, Any] = {
        "n_cells": int(len(cells)),
        "n_dates": int(n_dates),
        **{key: _round(value) for key, value in metrics.items()},
        "n_epochs_dropped": dict(dropped),
        "coverage_fraction": round(float(coverage.fraction), 6),
        "aoi_cells": int(coverage.aoi_cells),
        "covered_aoi_cells": int(coverage.covered_in_aoi),
        "span_years": round(float(span_years), 6),
        "per_cell_series": {
            "enabled": False,
            "criterion": (
                "aoi aggregate only: per-cell rows are optional (contract §4) and the 15 MB budget "
                "is spent on the grid instead of 12 dates x n_cells of CSV"
            ),
        },
    }
    if np.isfinite(sigma_median) and span_years > 0.0:
        stats["detection_limit_mm_yr"] = _round(
            V.detection_limit_mm_yr(sigma_median, span_years, n_epochs=n_epochs)
        )
    elif case.null_result:
        raise ProcessCaseError(
            f"{case.case_id} is a declared null result but the residual scatter is undefined, so "
            "detection_limit_mm_yr cannot be published; the bound IS the result (contract §5)"
        )
    if case.detection_limit_mm_yr is not None:
        stats.setdefault("detection_limit_config_mm_yr", float(case.detection_limit_mm_yr))
    for key, value in stats.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ProcessCaseError(f"meta.stats.{key} is not finite ({value!r}); refusing to publish")
    return stats


def _round(value: Any) -> Any:
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return round(number, 6) if math.isfinite(number) else number
    return value


def _provenance_id(
    case_id: str,
    paths: Sequence[pathlib.Path],
    convention: ConventionRecord | None = None,
    horizontals_token: str = "",
) -> str:
    """Content-derived provenance id.

    The date is the **latest acquisition date in the stack**, parsed from the granule names — never
    the wall clock. ``provenance_id`` is a column in the grid and in the series, so a rerun after
    UTC midnight used to produce different bytes for identical input (flagged by both the pipeline
    review and the end-to-end test author).

    **The convention is part of the content.** `--los-sign` and `--heading-convention` change the
    numbers, so the same granules processed under two conventions are two different physical claims
    and must not share an id: :meth:`ConventionRecord.id_token` is appended to the digested string
    when (and only when) the effective convention differs from the default one. A differently
    stated *default* (``--los-sign away-from-sensor``) is the same claim and keeps the same id, and
    so do the prose flags (``--method-note``, ``--validated-against``), because they name no number.
    ``horizontals_token`` behaves the same way for the trusted horizontal assumptions: empty when
    they are exactly zero (the historical run), a digest of the grids otherwise.

    That makes an id differ legitimately between conventions and between horizontal models — it is
    not a reproducibility bug. Reruns with identical input **and identical arguments** still digest
    identical bytes.
    """
    token = "" if convention is None else convention.id_token()
    material = case_id + "|" + PRODUCT + "|" + "|".join(sorted(path.stem for path in paths))
    for extra in (token, horizontals_token):
        if extra:
            material = f"{material}|{extra}"
    digest = hashlib.sha256(material.encode()).hexdigest()
    stamps = sorted(re.findall(r"(20\d{6})T\d{6}", " ".join(path.stem for path in paths)))
    if stamps:
        token = stamps[-1]
        when = dt.date(int(token[:4]), int(token[4:6]), int(token[6:]))
    else:
        # No date token in the names: a fixed epoch keeps the id reproducible rather than
        # silently making every rerun unique.
        when = dt.date(1970, 1, 1)
    return C.provenance_id(case_id, PRODUCT, when, digest)


def _method_text(
    *,
    records: Sequence[_StackGranule],
    polarisation: str,
    n_epochs: int,
    harmonics: int,
    reference_lonlat: tuple[float, float],
    h3_resolution: int,
    wavelength_summary: str,
    geometry_summary: str,
    convention: ConventionRecord,
    assumed_v_east_mm_yr: float | dict[str, Any] = 0.0,
    assumed_v_north_mm_yr: float | dict[str, Any] = 0.0,
) -> str:
    """The physical claim, in prose, including the LOS sign and heading conventions actually used.

    Two shapes, and they must not blur into each other:

    * **No convention flags** (``convention.requested is False``): the historical text, whose step
      (2) carries the "sign convention of docs/nisar-gunw-layout.md §4, NOT yet validated against
      GNSS" hedge. That hedge is what `docs/first-granule-runbook.md` §7 gates publication on; it
      stays while the operator has recorded nothing.
    * **Any of the four flags** (``requested``): the hedge is gone, replaced by an explicit statement
      of the sign convention and the heading convention in the operator's words, plus appended
      sentences for ``--validated-against`` and each ``--method-note``. If no
      ``--validated-against`` was passed the text says so rather than implying a validation that
      did not happen.
    """
    incidence = _format_angle({round(record.incidence_deg, 3) for record in records})
    heading = _format_angle({round(record.heading_deg, 3) for record in records})
    look_azimuth = _format_angle({round(record.look_azimuth_deg, 3) for record in records})

    if convention.requested:
        sign_clause = (
            f"{convention.phase_factor} with los_sign = {convention.los_sign} "
            f"(sign {convention.sign:+.0f} in front of lambda/(4*pi)): "
            f"{LOS_SIGN_MEANINGS[convention.los_sign]}"
        )
        if convention.heading_convention == "look":
            heading_clause = (
                f"incidence {incidence} and platform heading {heading}, source: {geometry_summary}; "
                "heading_convention = look, so the platform heading was mapped to the look azimuth "
                f"alpha = 180 deg - alpha_platform = {look_azimuth} before the LOS->vertical "
                "inversion, because the horizontal part of the frozen unit vector "
                "u = [cos(theta), -sin(theta)*cos(alpha), -sin(theta)*sin(alpha)] points at "
                "azimuth alpha + 180 and u_up = cos(theta) is the same under either reading"
            )
        else:
            heading_clause = (
                f"incidence {incidence} and platform heading {heading}, source: {geometry_summary}; "
                "heading_convention = platform, so the platform heading was passed through "
                "verbatim as alpha in the frozen unit vector "
                "u = [cos(theta), -sin(theta)*cos(alpha), -sin(theta)*sin(alpha)] "
                f"(the look azimuth would be {look_azimuth})"
            )
    else:
        sign_clause = (
            f"-lambda/(4*pi)*phi (sign convention of {DOC_REFERENCE} §4, NOT yet validated "
            "against GNSS)"
        )
        heading_clause = f"incidence {incidence} and heading {heading}, source: {geometry_summary}"

    zero_assumptions = assumed_v_east_mm_yr == 0.0 and assumed_v_north_mm_yr == 0.0
    if zero_assumptions:
        assumption_clause = (
            f"with v_north = {ASSUMED_V_NORTH_MM:g} mm assumed, so the residual error is "
            "tan(theta)*sin(alpha)*(v_north - assumed) and the east term is not constrained "
            "(README §6)"
        )
    else:
        assumption_clause = (
            "with the caller-supplied horizontal velocity "
            f"v_east = {_format_assumption(assumed_v_east_mm_yr)} mm/yr and "
            f"v_north = {_format_assumption(assumed_v_north_mm_yr)} mm/yr (each scaled by the "
            "pair's span), so the residual error is tan(theta)*(cos(alpha)*dv_east + "
            "sin(alpha)*dv_north) with dv = assumed - true (README §6)"
        )

    method = (
        f"Real NISAR L2 GUNW stack: {len(records)} granule(s), frequency A, co-pol {polarisation}. "
        f"(1) phase (rad) read from /unwrappedInterferogram/{polarisation}/unwrappedPhase; "
        f"(2) d_LOS = {sign_clause}, wavelength {wavelength_summary}; "
        "(3) pixels with mask==127, non-finite phase/coherence, or coherence below the frozen "
        f"{PRODUCT} coherence_min={PROFILE.coherence_min} are dropped (nodata stays absent, never 0); "
        f"(4) LOS->vertical {assumption_clause}; {heading_clause}; "
        f"(5) pairs ordered by acquisition date and chained into cumulative displacement, "
        f"referenced spatially to the pixel nearest reference_point {reference_lonlat} (a spatial "
        "(InSAR) reference; the first epoch is 0 mm by construction); "
        f"(6) H3 resolution {h3_resolution} mean per epoch (science.h3_aggregate, mean; all-NaN "
        "cells are absent); "
        f"(7) per cell science.harmonic_fit over {n_epochs} epochs with {harmonics} annual "
        "harmonic(s) gives velocity_mm_yr; val_t1/val_t2 are the referenced cumulative values at "
        "the first/last epoch and delta_change = val_t2 - val_t1; "
        "(8) meta.stats from science.velocity_metrics. "
        "The ionospheric phase screen delivered by GUNW is included but NOT applied, and external "
        "phase corrections are NOT removed (docs/nisar-gunw-layout.md §1). Coherence is the "
        "temporal mean of bias-corrected per-pair coherence when effective looks are declared. "
        "Land use comes from the --land-use-csv sidecar when given, else 'unknown'."
    )

    if convention.requested:
        if convention.validated_against:
            extras = [
                "Convention validated against: "
                f"{_sentence(convention.validated_against)}"
            ]
        else:
            extras = [
                "Convention validation: no --validated-against was supplied, so this run records "
                "the convention it used but no external check of it"
            ]
        extras.extend(
            f"Convention note (--method-note): {_sentence(note)}"
            for note in convention.method_notes
        )
        method = method + " " + " ".join(extras)
    return method


def _format_angle(values: set[float]) -> str:
    """`39.0 deg` for a constant geometry, `[39.0, 40.2] deg` when it varies across the stack."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return f"{ordered[0]:g} deg"
    return "[" + ", ".join(f"{value:g}" for value in ordered) + "] deg"


def _geometry_convention(
    convention: ConventionRecord,
    records: Sequence[_StackGranule],
    assumed_v_east_mm_yr: float | dict[str, Any],
    assumed_v_north_mm_yr: float | dict[str, Any],
) -> dict[str, Any]:
    """`meta.provenance.geometry_convention`: the convention as data, next to the prose.

    The three keys a reviewer greps are ``los_sign``, ``heading_convention`` and
    ``validated_against``. The neighbours exist so the record is self-contained: the numeric factor
    actually applied to the phase, the formula it belongs to, the platform heading and look azimuth
    in degrees, the operator's own sentences, and the trusted horizontals the inversion used (0.0
    when the chain applied today's ``v_north = 0`` / no-east assumption). It is written on
    **every** run, defaults included — "the default was used, unvalidated" is a fact the artefact
    must carry, not an absence.
    """
    return {
        "los_sign": convention.los_sign,
        "los_sign_factor": convention.sign,
        "los_sign_formula": convention.formula,
        "los_sign_meaning": LOS_SIGN_MEANINGS[convention.los_sign],
        "heading_convention": convention.heading_convention,
        "heading_platform_deg": sorted({round(record.heading_deg, 3) for record in records}),
        "heading_look_azimuth_deg": sorted(
            {round(record.look_azimuth_deg, 3) for record in records}
        ),
        "assumed_v_east_mm_yr": assumed_v_east_mm_yr,
        "assumed_v_north_mm_yr": assumed_v_north_mm_yr,
        "validated_against": convention.validated_against,
        "method_notes": list(convention.method_notes),
        "recorded_from_flags": convention.requested,
    }


def _meta(
    *,
    cfg: CaseConfig,
    epochs: Sequence[str],
    records: Sequence[_StackGranule],
    paths: Sequence[pathlib.Path],
    polarisation: str,
    provenance_id: str,
    stats: Mapping[str, Any],
    coverage: Coverage,
    harmonics: int,
    geometry_notes: Sequence[str],
    wavelength_notes: Sequence[str],
    warnings: Sequence[str],
    reference_offset_m: float,
    provenance_overrides: Mapping[str, Any] | None,
    convention: ConventionRecord,
    assumed_v_east_mm_yr: float | dict[str, Any],
    assumed_v_north_mm_yr: float | dict[str, Any],
) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    wavelength_summary = "; ".join(sorted(set(wavelength_notes)))
    geometry_summary = "; ".join(sorted(set(geometry_notes)))
    limitations = list(cfg.limitations)
    limitations.extend(
        [
            "LOS->vertical assumes v_north = 0 mm; the omitted horizontal motion biases the "
            "vertical rate by tan(incidence)*sin(heading)*v_north (README §6). Validate against "
            "GNSS or a published rate before quoting a velocity.",
            "The GUNW ionospheric phase screen is included but not applied, and external phase "
            "corrections are not removed (docs/nisar-gunw-layout.md §1); the product is provisional.",
            "NISAR GUNW posting is 80 m: city scale, not building scale.",
            f"Coverage diagnostic: {coverage.covered_in_aoi} of {coverage.aoi_cells} AOI H3 cells "
            f"received data ({coverage.fraction:.1%}). "
            + (
                f"Below the {COVERAGE_WARNING_FRACTION:.0%} threshold of {DATA_SOURCE_DECISION}: do "
                "not claim city-wide displacement for this case."
                if coverage.below_threshold
                else f"Above the {COVERAGE_WARNING_FRACTION:.0%} threshold of {DATA_SOURCE_DECISION}."
            ),
        ]
    )
    for note in warnings:
        lowered = note.lower()
        if "coherence" in lowered and "bias" in lowered:
            limitations.append(
                "Coherence is not bias-corrected: the granules do not declare an effective number "
                "of looks, so the published mean is the raw finite-look estimate (contract §3)."
            )
            break
    if any("documented L-band constant" in note for note in warnings):
        limitations.append(
            f"Wavelength fell back to the documented L-band constant "
            f"{DEFAULT_WAVELENGTH_M} m ({DOC_REFERENCE} §4) because the granule has neither "
            "wavelength nor centerFrequency metadata; every millimetre scales with it."
        )
    limitations = _dedupe(limitations)  # config and this module may state the same caveat

    provenance: dict[str, Any] = {
        "source": f"{SOURCE_PROVIDER} — {SOURCE_PRODUCT}",
        "product_id": "; ".join(path.stem for path in sorted(paths, key=lambda p: p.name)),
        "processing_date": now.date().isoformat(),
        "method": _method_text(
            records=records,
            polarisation=polarisation,
            n_epochs=len(epochs),
            harmonics=harmonics,
            reference_lonlat=cfg.reference_lonlat,
            h3_resolution=cfg.h3_resolution,
            wavelength_summary=wavelength_summary,
            geometry_summary=geometry_summary,
            convention=convention,
            assumed_v_east_mm_yr=assumed_v_east_mm_yr,
            assumed_v_north_mm_yr=assumed_v_north_mm_yr,
        ),
        "retrieved_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference": (
            "NISAR L2 GUNW product definition: NISAR Data User Guide, Geocoded Unwrapped "
            "Interferogram (nisar-docs.asf.alaska.edu/gunw); dataset map and the phase->mm scale in "
            f"{DOC_REFERENCE} §2/§4; contract docs/data-contract-v1.md v1.0"
        ),
        "license": LICENSE,
        "provenance_id": provenance_id,
        "geometry": geometry_summary,
        # Machine-readable twin of the prose above: a reviewer greps these three keys instead of
        # parsing (2)/(4) of `method`. Recorded on every run, defaults included, because "the
        # default was used" is itself the convention a reader needs to know.
        "geometry_convention": _geometry_convention(
            convention, records, assumed_v_east_mm_yr, assumed_v_north_mm_yr
        ),
        "wavelength": wavelength_summary,
        "reference_offset_m": round(float(reference_offset_m), 3),
        "polarisation": polarisation,
    }
    if provenance_overrides:
        provenance.update(dict(provenance_overrides))

    return {
        "schema_version": C.SCHEMA_VERSION,
        "case_id": cfg.case_id,
        "name": cfg.name,
        "bbox": list(cfg.bbox),
        "crs": C.CRS_OUTPUT,
        "reference_point": list(cfg.reference_lonlat),
        "dates": list(epochs),
        "revisit_days": cfg.revisit_days,
        "sensor": SENSOR,
        "product": PRODUCT,
        "thresholds": {
            "coherence_min": PROFILE.coherence_min,
            "k_sigma": PROFILE.k_sigma,
            "min_ddB": PROFILE.min_ddB,
            "persistence": PROFILE.persistence,
            "min_area_m2": PROFILE.min_area_m2,
        },
        "provenance": provenance,
        "stats": dict(stats),
        "limitations": limitations,
        "null_policy": (
            "Cells without data (mask==127, non-finite phase/coherence, coherence below "
            f"{PROFILE.coherence_min}, or fewer than {V.MIN_FIT_POINTS} fitted epochs) are absent "
            "from nisar_grid.parquet, and a missing date is a missing timeseries row: nodata is "
            "never written as 0. In a null-result city, 0 mm is a legitimate measurement."
        ),
    }


def _context(
    cfg: CaseConfig,
    cells: Sequence[str],
    land_use: Sequence[str],
    provenance: str,
    warnings: list[str],
) -> dict[str, Any]:
    """The minimal honest `context_layers.geojson`: the AOI polygon + the grid's own land_use.

    No invented infrastructure, coastline or wells: a real-data artefact says only what the
    pipeline actually derived. `land_use` polygons are the union of the very same H3 cells the grid
    publishes, so the layer and the column cannot disagree (contract §6).
    """
    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[round(lon, 6), round(lat, 6)] for lon, lat in cfg.aoi_ring]
                    + [[round(cfg.aoi_ring[0][0], 6), round(cfg.aoi_ring[0][1], 6)]]
                ],
            },
            "properties": {
                "layer": "admin_boundary",
                "class": "aoi",
                "name": f"{cfg.name} — analysis AOI",
                "case_id": cfg.case_id,
                "crs": C.CRS_OUTPUT,
                "source": f"{cfg.aoi_source} (analysis extent, not a legal boundary)",
                "provenance_id": provenance,
            },
        }
    ]

    try:
        from shapely.geometry import Polygon, mapping  # noqa: PLC0415
        import shapely  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - guarded optional: the AOI feature always ships
        warnings.append(
            f"shapely is unavailable ({exc}); context_layers.geojson carries the AOI polygon only, "
            "so the land_use layer is absent"
        )
        return {"type": "FeatureCollection", "features": features}

    by_class: dict[str, list[str]] = {}
    for cell, label in zip(cells, land_use, strict=True):
        by_class.setdefault(str(label), []).append(cell)

    land_use_features: list[dict[str, Any]] = []
    for label in sorted(by_class):
        polygons = [
            Polygon([(lon, lat) for lat, lon in h3.cell_to_boundary(cell)])
            for cell in by_class[label]
        ]
        merged = shapely.union_all(polygons)
        merged = shapely.set_precision(merged, CONTEXT_PRECISION_DEG, mode="valid_output")
        merged = merged.simplify(CONTEXT_SIMPLIFY_DEG, preserve_topology=True)
        merged = shapely.set_precision(merged, CONTEXT_PRECISION_DEG, mode="valid_output")
        if merged.is_empty:
            continue
        land_use_features.append(
            {
                "type": "Feature",
                "geometry": mapping(merged),
                "properties": {
                    "layer": "land_use",
                    "class": label,
                    "case_id": cfg.case_id,
                    "crs": C.CRS_OUTPUT,
                    "source": "H3 land_use assignment of nisar_grid.parquet (one array for both)",
                    "provenance_id": provenance,
                },
            }
        )

    size = len(json.dumps(land_use_features, ensure_ascii=False, default=str).encode("utf-8"))
    if size > CONTEXT_LAYOUT_BUDGET_BYTES:
        warnings.append(
            f"land_use polygons would weigh {size / 1e6:.2f} MB (> {CONTEXT_LAYOUT_BUDGET_BYTES / 1e6:.1f} "
            "MB); they are omitted from context_layers.geojson to keep the case inside the 15 MB "
            "contract budget — the land_use column of the parquet remains authoritative"
        )
    else:
        features.extend(land_use_features)
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------------------------
# Writer (reuses the mock's atomic, deterministic writers; builds no mock values)
# ---------------------------------------------------------------------------------------------


def write_artefacts(
    processed: ProcessedCase,
    out_dir: str | pathlib.Path,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Write the four frozen artefacts; returns ``{filename: bytes}``.

    Choice of reuse: `pipeline/mock/artifacts.py` already owns the atomic-rename + fixed-format
    writers (parquet zstd, LF CSV with %.9f, sorted-keys JSON). Those are parameterised by
    frame/collection, so they are imported unchanged; the *content* builders are not reusable
    because they are typed against the synthetic `CaseModel`, so the frames and meta are built in
    this module. Output layout is therefore identical to the mock's down to the byte conventions.
    """
    from pipeline.mock import artifacts as A  # noqa: PLC0415 - read-only reuse, lazy import

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = dict(processed.meta if meta is None else meta)
    if payload.get("product") != PRODUCT:
        raise ProcessCaseError(
            f"refusing to write meta.product={payload.get('product')!r}; this chain only produces "
            f"{PRODUCT!r} (a mock tree is written by pipeline/generate_mock.py)"
        )
    sizes = {
        "nisar_grid.parquet": A.write_parquet(processed.grid, out / "nisar_grid.parquet"),
        "timeseries.csv": A.write_csv(processed.timeseries, out / "timeseries.csv"),
        "context_layers.geojson": A.write_geojson(processed.context, out / "context_layers.geojson"),
        "meta.json": A.write_json(payload, out / "meta.json"),
    }
    return sizes


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="process_case.py",
        description=(
            "Real NISAR L2 GUNW -> LOS -> vertical -> H3 -> the four frozen artefacts. Fails closed "
            "on a missing geometry, a mixed track/direction, a mid-stack polarisation change, a "
            "missing layer, or a stack too short to fit a trend."
        ),
        epilog=f"Layout and unknowns: {DOC_REFERENCE}. Coverage caveat: {DATA_SOURCE_DECISION}.",
    )
    parser.add_argument("--case", required=True, help="case slug from config/study_cases.yaml")
    parser.add_argument(
        "--granule",
        action="append",
        required=True,
        metavar="PATH",
        help="NISAR L2 GUNW .h5 granule; repeat once per pair, in any order",
    )
    parser.add_argument("--out", required=True, metavar="DIR", help="case directory to write")
    parser.add_argument(
        "--incidence",
        type=float,
        default=None,
        metavar="DEG",
        help="incidence angle at the target; required only when the granule carries none",
    )
    parser.add_argument(
        "--heading",
        type=float,
        default=None,
        metavar="DEG",
        help="platform heading, degrees clockwise from north; required only when absent",
    )
    parser.add_argument(
        "--polarisation",
        type=str.upper,
        choices=G.POLARISATIONS,
        default=None,
        help="force a co-pol group when a granule is malformed (normally auto-detected)",
    )
    parser.add_argument(
        "--land-use-csv",
        default=None,
        metavar="PATH",
        help="optional sidecar `cell_id,land_use`; cells absent from it become 'unknown'",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing directory even if its meta.json says product == 'mock'",
    )
    parser.add_argument(
        "--los-sign",
        choices=list(LOS_SIGN_FACTORS),
        default=None,
        metavar="{away-from-sensor,toward-sensor}",
        help=(
            "LOS sign convention passed to phase_to_los_displacement_mm(sign=...). "
            "away-from-sensor (the physical default, and what every earlier run used) is "
            "d_LOS = -lambda/(4*pi)*phi: positive means the ground moved AWAY from the sensor along "
            "the line of sight, so subsidence is negative (up is positive here). toward-sensor uses "
            "+lambda/(4*pi)*phi: positive means motion TOWARD the sensor, so subsidence becomes "
            "positive and uplift negative. A 2*pi phase slip and a flipped sign both invert "
            "subsidence, so state which you chose; the choice is written to provenance.method and "
            "provenance.geometry_convention.los_sign. Omitting the flag keeps today's text, including "
            "the 'NOT yet validated against GNSS' hedge"
        ),
    )
    parser.add_argument(
        "--heading-convention",
        choices=list(HEADING_CONVENTIONS),
        default=None,
        metavar="{platform,look}",
        help=(
            "how the granule heading is fed to los_to_vertical. platform (today's behaviour) passes "
            "the platform heading through verbatim as alpha in the frozen unit vector "
            "u = [cos(theta), -sin(theta)*cos(alpha), -sin(theta)*sin(alpha)]. look maps it to the "
            "look azimuth alpha = 180 - alpha_platform first, because the horizontal part of that "
            "vector points at azimuth alpha + 180. u_up = cos(theta) is identical either way, so a "
            "stack with no horizontal motion is unaffected: the two conventions differ only through "
            "sin(theta)*(v_east, v_north), which one geometry cannot resolve"
        ),
    )
    parser.add_argument(
        "--method-note",
        action="append",
        default=None,
        dest="method_notes",
        metavar="TEXT",
        help=(
            "sentence appended verbatim to meta.provenance.method (repeatable). Supplying any of "
            "the four convention flags removes the 'NOT yet validated against GNSS' hedge and "
            "states the convention instead, which is the gate of "
            "docs/first-granule-runbook.md §7"
        ),
    )
    parser.add_argument(
        "--validated-against",
        action="append",
        default=None,
        dest="validated_against",
        metavar="TEXT",
        help=(
            "what the convention was checked against, e.g. 'GNSS station MEX1, 2019-2024, residual "
            "1.8 mm/yr RMSE' or 'Cigna & Tapete 2022 published -450 mm/yr'. Appended to "
            "meta.provenance.method and recorded in provenance.geometry_convention.validated_against "
            "(repeatable: every check is joined with '; '). Allowed without a sign/heading flag "
            "(validating the default is legitimate) but warned, because the record then names the "
            "default rather than your choice"
        ),
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable summary")
    return parser


def _gate(out_dir: pathlib.Path) -> dict[str, Any]:
    """Run the file-level gate when importable; never pretend a skipped gate passed."""
    try:
        from pipeline import validate_artifacts as gate  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"file-level gate not importable: {exc}"}
    report = gate.validate_tree(out_dir, config=_raw_config())
    return {
        "available": True,
        "failed": bool(report.failed),
        "cases_validated": int(report.cases_validated),
        "rendered": gate.render_human(report),
        "report": gate.report_to_dict(report),
    }


def _report_failures(title: str, problems: Mapping[str, Sequence[str]]) -> bool:
    failed = False
    for artefact, messages in problems.items():
        if not messages:
            continue
        failed = True
        print(f"{title}: FAIL {artefact}", file=sys.stderr)
        for message in messages:
            print(f"  - {message}", file=sys.stderr)
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = pathlib.Path(args.out)

    try:
        processed = process_case(
            args.granule,
            args.case,
            out_dir=out_dir,
            incidence_deg=args.incidence,
            heading_deg=args.heading,
            polarisation=args.polarisation,
            land_use_csv=args.land_use_csv,
            force=args.force,
            los_sign=args.los_sign,
            heading_convention=args.heading_convention,
            method_notes=args.method_notes,
            validated_against=args.validated_against,
        )
    except ProcessCaseError as exc:
        for warning in exc.warnings:
            prefix = "WARNING" if warning.startswith("COVERAGE") else "warning"
            print(f"{prefix}: {warning}", file=sys.stderr)
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    for warning in processed.warnings:
        prefix = "WARNING" if warning.startswith("COVERAGE") else "warning"
        print(f"{prefix}: {warning}", file=sys.stderr)

    try:
        sizes = write_artefacts(processed, out_dir)
    except ProcessCaseError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    contract = C.validate_all(
        grid=processed.grid,
        timeseries=processed.timeseries,
        meta=processed.meta,
        context=processed.context,
        case_id=processed.case_id,
    )
    contract_failed = _report_failures("contract", contract)

    gate = _gate(out_dir)
    if gate.get("available") and gate.get("failed"):
        print(gate.get("rendered", ""), file=sys.stderr)

    summary = {
        "case_id": processed.case_id,
        "product": processed.meta["product"],
        "out": str(out_dir),
        "artefacts": sizes,
        "cells": processed.n_cells,
        "dates": list(processed.epochs),
        "coverage": {
            "aoi_cells": processed.coverage.aoi_cells,
            "covered_in_aoi": processed.coverage.covered_in_aoi,
            "fraction": round(processed.coverage.fraction, 6),
            "warning": processed.coverage.below_threshold,
        },
        "stats": processed.meta["stats"],
        "provenance": processed.meta["provenance"],
        "warnings": list(processed.warnings),
        "contract": {name: list(problems) for name, problems in contract.items()},
        "gate": {
            "available": bool(gate.get("available")),
            "status": (
                "not-run"
                if not gate.get("available")
                else ("pass" if not gate.get("failed") else "fail")
            ),
            "cases_validated": gate.get("cases_validated", 0),
            "reason": gate.get("reason"),
        },
    }

    if args.as_json:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        print(
            f"{processed.case_id}: {processed.n_cells} H3 cells, {len(processed.epochs)} dates "
            f"({processed.epochs[0]} … {processed.epochs[-1]}), product {processed.meta['product']}"
        )
        print(
            f"  coverage  {processed.coverage.fraction:.2%} of {processed.coverage.aoi_cells} AOI "
            f"cells ({processed.coverage.covered_in_aoi} covered)"
        )
        geometry_convention = processed.meta.get("provenance", {}).get("geometry_convention") or {}
        if geometry_convention.get("recorded_from_flags"):
            # Only when the operator supplied a convention flag: the default run's stdout stays
            # exactly what it was.
            print(
                f"  convention los_sign={geometry_convention.get('los_sign')} "
                f"heading_convention={geometry_convention.get('heading_convention')} "
                f"validated_against={geometry_convention.get('validated_against') or '(not stated)'}"
            )
        print(f"  out       {out_dir}")
        for name, size in sizes.items():
            print(f"            {name} ({size} bytes)")
        contract_status = "FAIL" if contract_failed else "PASS"
        print(f"  contract  {contract_status} (grid/timeseries/meta/context)")
        if gate.get("available"):
            gate_status = "FAIL" if gate.get("failed") else "PASS"
            print(f"  gate      {gate_status} ({gate.get('cases_validated', 0)} case validated)")
        else:
            print(f"  gate      not run — {gate.get('reason')}", file=sys.stderr)

    if contract_failed or (gate.get("available") and gate.get("failed")):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
