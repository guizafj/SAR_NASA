#!/usr/bin/env python3
"""Read a NISAR L2 GUNW granule, and fail loudly when the file is not what we expect.

Normative reference: ``docs/nisar-gunw-layout.md``. Every path below is taken from §2/§3 of that
page, which is itself verified against the NISAR Data User Guide and the ASF sample-data tutorial —
**but not yet against a real 2026 granule**. Treat the first real file as the correction event:
run ``pipeline/ingest/inspect_hdf5.py`` on it, diff the tree against the doc, and edit the single
``DATASETS`` table here if the layout differs.

Rules this module enforces (they matter more than any feature):

* **Fail closed.** A required dataset whose path is absent raises ``GranuleError`` naming the exact
  path. No silent zeros, no ``np.nan_to_num``.
* **Nodata stays absent.** Invalid pixels become NaN in float outputs and are excluded from
  summaries. They are never converted to 0 — in Málaga, 0 is a real measurement.
* **An empty granule is loud.** Zero valid pixels is a legal read, but it is always recorded as a
  warning so downstream aggregation cannot mistake an all-NaN / fully padded pair for a clean one.
* **Unknown quality flags are recorded, never reinterpreted.** :func:`valid_mask` drops padding
  (127) and non-finite floats only; every undocumented mask value is named with its pixel count in
  the warnings and exposed by :func:`summary` (``mask_values``/``mask_unknown_values``).
* **No date is fabricated.** A metadata time is only used when it has a real ISO-8601
  ``YYYY-MM-DD`` shape; a present-but-unusable value is warned about and the filename tokens are
  tried instead.
* **No geospatial invention.** If x/y coordinates or the CRS cannot be read, the reader says so and
  leaves them ``None``. It never synthesises a transform from the bounding box or guesses an EPSG.
* **No silent physical assumptions.** Wavelength is read from metadata or reported as ``None`` with
  a warning. If a real metadata wavelength is absent but ``centerFrequency`` is present, the
  wavelength is derived as ``c/f`` and the derivation is recorded — never assumed.

The module imports h5py lazily inside the reading functions, so the pure functions
(:func:`valid_mask`, :func:`phase_to_los_displacement_mm`) are importable and testable without HDF5.

CLI
---
    ./scripts/python.sh pipeline/ingest/nisar_gunw.py GRANULE [--json] [--polarisation HH|VV]

Exit status is 0 on a successful structural read, non-zero (2) on any structural failure.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

__all__ = [
    "DATASETS",
    "GranuleError",
    "GunwGranule",
    "discover_polarisation",
    "main",
    "phase_to_los_displacement_mm",
    "read_granule",
    "summary",
    "valid_mask",
]

#: Human-facing source of truth for the mapping. Quoted in every structural error so the operator
#: knows which page to correct.
DOC_REFERENCE = "docs/nisar-gunw-layout.md"

#: Only co-polarised products exist; `{POL}` is HH or VV and is read from the file, never assumed.
POLARISATIONS: tuple[str, ...] = ("HH", "VV")

#: Mask value 127 is padding around valid data. See `docs/nisar-gunw-layout.md` §2.
MASK_PADDING = 127

#: Documented mask values that are *not* padding and must be carried through: 0 = no quality
#: flags, 2 = some flag set. We keep 2 because the flag is a filter decision for a later stage,
#: not proof that the pixel is unusable; dropping it here would silently hide quality information.
OBSERVED_MASK_VALUES: tuple[int, ...] = (0, 2, MASK_PADDING)

# ---------------------------------------------------------------------------------------------
# ONE table of required dataset paths. `docs/nisar-gunw-layout.md` §2.
# `{pol}` is substituted from the granule. If a real granule disagrees, this is the one-line edit.
# ---------------------------------------------------------------------------------------------
_PRODUCT_ROOT = "/science/LSAR/GUNW"  # L-band instrument root; nothing lives at "/"
_GRID_ROOT = _PRODUCT_ROOT + "/grids/frequencyA"  # frequency A only for GUNW
_UNWRAPPED_ROOT = _GRID_ROOT + "/unwrappedInterferogram"

DATASETS: dict[str, str] = {
    "unwrapped_group": _UNWRAPPED_ROOT,
    "phase": _UNWRAPPED_ROOT + "/{pol}/unwrappedPhase",  # float32, radians
    "coherence": _UNWRAPPED_ROOT + "/{pol}/coherenceMagnitude",  # float32, 0..1
    "connected_components": _UNWRAPPED_ROOT + "/{pol}/connectedComponents",  # uint32
    "mask": _UNWRAPPED_ROOT + "/mask",  # int8, quality flags (NOT under {pol})
}

# ---------------------------------------------------------------------------------------------
# Optional metadata paths. `docs/nisar-gunw-layout.md` §3 and §5.1 say the exact names/locations of
# the coordinates, CRS, times and wavelength are NOT verified yet. These candidate lists are the
# best-effort probe; the first hit is recorded in the summary/warnings so the guess is visible.
# A candidate is either a dataset path or ``(group_path, attribute_name)``.
# ---------------------------------------------------------------------------------------------
Candidate = str | tuple[str, str]

#: CF-style coordinate arrays for the projected grid. §3: exact names unverified.
X_COORDINATE_CANDIDATES: tuple[Candidate, ...] = (
    _GRID_ROOT + "/xCoordinates",
    _GRID_ROOT + "/x",
    _GRID_ROOT + "/projection/xCoordinates",
)
Y_COORDINATE_CANDIDATES: tuple[Candidate, ...] = (
    _GRID_ROOT + "/yCoordinates",
    _GRID_ROOT + "/y",
    _GRID_ROOT + "/projection/yCoordinates",
)

#: EPSG code / CRS description. §3: whether it is a `projection` dataset, a `spatial_ref` variable
#: or a CF attribute is unresolved; probe them all, parse the WKT/PROJ string if necessary.
EPSG_CANDIDATES: tuple[Candidate, ...] = (
    _GRID_ROOT + "/projection",
    _GRID_ROOT + "/spatial_ref",
    _GRID_ROOT + "/crs",
    _GRID_ROOT + "/gridMapping",
    _GRID_ROOT + "/projection/spatial_ref",
    _GRID_ROOT + "/projection/gridMapping",
    _PRODUCT_ROOT + "/identification/epsg",
)
EPSG_ATTRIBUTE_NAMES: tuple[str, ...] = (
    "epsg_code",
    "epsg",
    "EPSG",
    "spatial_epsg",
    "crs_wkt",
    "spatial_ref",
    "proj4",
    "grid_mapping",
)

#: Acquisition times. NISAR `identification` normally carries zero-Doppler start/end times; keep
#: mid-orbit as a fallback because the exact field set is not verified for GUNW.
REFERENCE_TIME_CANDIDATES: tuple[Candidate, ...] = (
    _PRODUCT_ROOT + "/identification/referenceZeroDopplerStartTime",
    _PRODUCT_ROOT + "/identification/referenceMidOrbitDateTime",
    _PRODUCT_ROOT + "/identification/referenceZeroDopplerEndTime",
    (_PRODUCT_ROOT + "/identification", "referenceZeroDopplerStartTime"),
)
SECONDARY_TIME_CANDIDATES: tuple[Candidate, ...] = (
    _PRODUCT_ROOT + "/identification/secondaryZeroDopplerStartTime",
    _PRODUCT_ROOT + "/identification/secondaryMidOrbitDateTime",
    _PRODUCT_ROOT + "/identification/secondaryZeroDopplerEndTime",
    (_PRODUCT_ROOT + "/identification", "secondaryZeroDopplerStartTime"),
)

#: Radar wavelength / center frequency. §4 quotes λ_L ≈ 0.2384 m but says "confirm in metadata".
WAVELENGTH_CANDIDATES: tuple[Candidate, ...] = (
    _PRODUCT_ROOT + "/metadata/radarGrid/wavelength",
    _PRODUCT_ROOT + "/metadata/processingInformation/parameters/frequencyA/wavelength",
    _GRID_ROOT + "/wavelength",
    (_PRODUCT_ROOT + "/metadata/radarGrid", "wavelength"),
    (_PRODUCT_ROOT + "/metadata/processingInformation/parameters/frequencyA", "wavelength"),
)
CENTER_FREQUENCY_CANDIDATES: tuple[Candidate, ...] = (
    _PRODUCT_ROOT + "/metadata/processingInformation/parameters/frequencyA/centerFrequency",
    _PRODUCT_ROOT + "/metadata/radarGrid/centerFrequency",
    (_PRODUCT_ROOT + "/metadata/processingInformation/parameters/frequencyA", "centerFrequency"),
    (_PRODUCT_ROOT + "/metadata/radarGrid", "centerFrequency"),
)

#: Speed of light in vacuum, m/s. Used only to derive λ = c/f when metadata gives a frequency and
#: no wavelength; the derivation is always recorded as a warning.
SPEED_OF_LIGHT_M_S = 299_792_458.0

#: A plausible EPSG code is an integer in this range. We do not map codes to CRS names, and we do
#: not compare against `config/study_cases.yaml` (the granule decides; the config is a check later).
_EPSG_MIN, _EPSG_MAX = 1024, 999_999

_ISO_TOKEN_RE = re.compile(r"(?<!\d)(\d{8})T(\d{6})(?!\d)")
_NUMBER_RE = re.compile(r"\d{4,6}")


class GranuleError(RuntimeError):
    """A structural problem that makes the granule unreadable or ambiguous.

    Raised instead of returning partial/zero-filled arrays. The message always contains the
    offending dataset path and points at :data:`DOC_REFERENCE`.
    """


@dataclass
class GunwGranule:
    """Data and provenance from one NISAR L2 GUNW granule.

    All four arrays share one shape. ``phase_rad`` is float32 radians, ``coherence`` float32 in
    [0, 1] when the file obeys its spec, ``connected_components`` uint32 labels and ``mask`` int8
    quality flags. ``x``/``y`` are the CF coordinate arrays when present; ``epsg`` is ``None``
    rather than guessed when the CRS cannot be read. ``warnings`` records every absent optional
    field and every assumption made, so a human can decide whether to trust the numbers.
    """

    phase_rad: np.ndarray
    coherence: np.ndarray
    connected_components: np.ndarray
    mask: np.ndarray
    polarisation: str
    x: np.ndarray | None = None
    y: np.ndarray | None = None
    epsg: int | None = None
    reference_time: str | None = None
    secondary_time: str | None = None
    wavelength_m: float | None = None
    #: Where `wavelength_m` came from: ``metadata:<path>`` or ``derived:centerFrequency:<path>``.
    wavelength_source: str | None = None
    #: Absolute path of the granule, carried through for provenance.
    source_path: str | None = None
    #: Non-fatal findings. Never empty-by-omission: absence is always recorded here.
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# Lazy HDF5 access and small generic readers
# ---------------------------------------------------------------------------------------------


def _import_h5py() -> Any:
    """Import h5py at call time so the pure functions do not depend on HDF5 at import time."""
    try:
        import h5py  # noqa: PLC0415 - deliberately lazy
    except Exception as exc:  # ImportError, or None left in sys.modules by a test
        raise GranuleError(
            "h5py is required to read a GUNW granule; it could not be imported. The pure "
            "functions valid_mask() and phase_to_los_displacement_mm() do not need it. "
            f"Environment reference: {DOC_REFERENCE}"
        ) from exc
    return h5py


def _decode_bytes(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, np.bytes_)):
        return bytes(value).decode("utf-8", "replace").rstrip("\x00")
    return value


def _as_scalar(value: Any) -> Any:
    """Reduce an HDF5 scalar/dataset value to a Python scalar or string, else return None."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()  # numpy scalar -> Python scalar (repr and JSON stay clean)
    if isinstance(value, (str, bytes, bytearray, int, float)):
        if isinstance(value, float) and np.isnan(value):
            return None
        return _decode_bytes(value)
    try:
        arr = np.asarray(value)
    except Exception:  # pragma: no cover - exotic HDF5 object
        return None
    if arr.size == 0:
        return None
    if arr.dtype.kind in "SUO":
        parts = [_decode_bytes(x) for x in arr.reshape(-1)]
        if not all(isinstance(part, str) for part in parts):
            return None
        return "".join(parts).rstrip("\x00").strip()
    if arr.size != 1:
        return None
    item = arr.reshape(-1)[0].item()
    if isinstance(item, float) and np.isnan(item):
        return None
    return _decode_bytes(item)


def _candidate_value(h5: Any, candidate: Candidate) -> tuple[Any, str | None]:
    """Resolve one candidate entry to ``(scalar, source_label)``; ``(None, None)`` when absent."""
    if isinstance(candidate, tuple):
        group_path, attr = candidate
        obj = h5.get(group_path)
        if obj is None or not hasattr(obj, "attrs") or attr not in obj.attrs:
            return None, None
        return _as_scalar(obj.attrs[attr]), f"{group_path}@{attr}"

    obj = h5.get(candidate)
    if obj is None or not hasattr(obj, "shape"):
        return None, None  # absent, or a group where a dataset was expected
    try:
        raw = obj[()]
    except Exception:  # noqa: BLE001 - an unreadable dataset is "absent" for optional metadata
        return None, None
    return _as_scalar(raw), candidate


def _first_candidate(h5: Any, candidates: Sequence[Candidate]) -> tuple[Any, str | None]:
    for candidate in candidates:
        value, source = _candidate_value(h5, candidate)
        if value not in (None, ""):
            return value, source
    return None, None


def _parse_epsg(value: Any) -> int | None:
    """Extract an EPSG code from an int or from WKT/PROJ/CF text; None when not parseable."""
    if value is None:
        return None
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        code = int(value)
        return code if _EPSG_MIN <= code <= _EPSG_MAX else None
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value) or not float(value).is_integer():
            return None
        code = int(value)
        return code if _EPSG_MIN <= code <= _EPSG_MAX else None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        code = int(text)
        return code if _EPSG_MIN <= code <= _EPSG_MAX else None

    # Explicit `EPSG:32614` / `+init=epsg:32614` wins.
    for pattern in (r"EPSG\s*:\s*(\d{4,6})", r"init=epsg:(\d{4,6})", r"[+\s]epsg=(\d{4,6})"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            code = int(match.group(1))
            if _EPSG_MIN <= code <= _EPSG_MAX:
                return code

    # WKT: take the *last* AUTHORITY/ID, which is the top-level CRS (earlier ones are datum/unit).
    for pattern in (
        r"ID\s*\[\s*[\"']EPSG[\"']\s*,\s*[\"']?(\d{4,6})",
        r"AUTHORITY\s*\[\s*[\"']EPSG[\"']\s*,\s*[\"']?(\d{4,6})",
    ):
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        for raw in reversed(matches):
            code = int(raw)
            if _EPSG_MIN <= code <= _EPSG_MAX:
                return code
    return None


def _epsg_from_object(obj: Any, source: str) -> tuple[int | None, str | None]:
    """Read EPSG from a dataset value or any declared CF attribute on an HDF5 object."""
    if obj is None:
        return None, None
    if hasattr(obj, "shape"):
        try:
            code = _parse_epsg(_as_scalar(obj[()]))
        except Exception:  # noqa: BLE001
            code = None
        if code is not None:
            return code, source
    if hasattr(obj, "attrs"):
        for attr in EPSG_ATTRIBUTE_NAMES:
            if attr not in obj.attrs:
                continue
            code = _parse_epsg(_as_scalar(obj.attrs[attr]))
            if code is not None:
                return code, f"{source}@{attr}"
    return None, None


def _read_epsg(h5: Any, x_path: str | None, y_path: str | None, warnings: list[str]) -> int | None:
    for candidate in EPSG_CANDIDATES:
        value, source = _candidate_value(h5, candidate)
        if value is None or source is None:
            continue
        code = _parse_epsg(value)
        if code is not None:
            if not isinstance(value, (int, np.integer)) and not str(value).strip().isdigit():
                warnings.append(
                    f"epsg={code} parsed from CRS description at {source}; verify it against "
                    f"config/study_cases.yaml crs_analysis before publication"
                )
            return code

    # CF puts the CRS in a `grid_mapping` variable referenced by the coordinates, or in attributes
    # on the grid/coordinate objects. Probe all three without inventing anything.
    for path in (_GRID_ROOT, _GRID_ROOT + "/projection", x_path, y_path):
        if not path:
            continue
        obj = h5.get(path)
        code, source = _epsg_from_object(obj, path)
        if code is not None:
            warnings.append(
                f"epsg={code} read from CF attribute at {source}; verify against "
                f"config/study_cases.yaml crs_analysis"
            )
            return code

        # Resolve the name in a `grid_mapping` attribute (CF: a variable name, not an EPSG code).
        try:
            mapping_name = (
                _as_scalar(obj.attrs.get("grid_mapping")) if hasattr(obj, "attrs") else None
            )
        except Exception:  # noqa: BLE001
            mapping_name = None
        if isinstance(mapping_name, str) and mapping_name:
            for base in (_GRID_ROOT, _GRID_ROOT + "/projection"):
                target = f"{base}/{mapping_name}"
                code, source = _epsg_from_object(h5.get(target), target)
                if code is not None:
                    warnings.append(
                        f"epsg={code} read from CF grid_mapping {source}; verify against "
                        f"config/study_cases.yaml crs_analysis"
                    )
                    return code

    warnings.append(
        f"epsg not found: no EPSG code or parseable CRS metadata at the candidate paths "
        f"({', '.join(str(c) for c in EPSG_CANDIDATES)}); leaving it None rather than guessing "
        f"from the bbox — {DOC_REFERENCE} §3"
    )
    return None


def _read_coordinate(
    h5: Any,
    candidates: Sequence[Candidate],
    expected_length: int,
    axis: str,
    warnings: list[str],
) -> tuple[np.ndarray | None, str | None]:
    for candidate in candidates:
        if isinstance(candidate, tuple):  # coordinate candidates are dataset paths only
            continue
        obj = h5.get(candidate)
        if obj is None or not hasattr(obj, "shape"):
            continue
        try:
            raw = obj[()]
        except Exception:  # noqa: BLE001 - an unreadable coordinate is an absent coordinate
            continue
        arr = np.asarray(raw, dtype=float)
        # A 1-D axis is what the documented CF layout provides. A 2-D grid must never be
        # flattened: (1, ncols) for x or (nrows, 1) for y flattens to exactly the axis length, so
        # a size check alone would accept it and a downstream affine transform would be built from
        # the wrong numbers. Reject on rank *before* any reshape.
        if arr.ndim != 1:
            warnings.append(
                f"{axis} coordinate at {candidate} is {arr.ndim}-D (shape {arr.shape}): a 1-D "
                f"axis is expected and no flattening/resampling is done (length {arr.size}, "
                f"expected {expected_length}) — {DOC_REFERENCE} §3"
            )
            continue
        arr = arr.reshape(-1)
        if arr.size != expected_length:
            warnings.append(
                f"{axis} coordinate at {candidate} has length {arr.size}, expected "
                f"{expected_length}; ignored (no resampling/transform is invented)"
            )
            continue
        if not np.all(np.isfinite(arr)):
            warnings.append(
                f"{axis} coordinate at {candidate} contains non-finite values; ignored "
                f"(no geospatial transform is invented)"
            )
            continue
        return arr, candidate
    warnings.append(
        f"{axis} coordinate array not found at the candidate paths "
        f"({', '.join(str(c) for c in candidates)}); x/y left None — {DOC_REFERENCE} §3"
    )
    return None, None


def _tokens_from_filename(path: pathlib.Path) -> list[tuple[str, str]]:
    """Extract (YYYYMMDD, HHMMSS) tokens from a NISAR granule filename, in file order."""
    return _ISO_TOKEN_RE.findall(path.name)


def _iso_token(token: tuple[str, str]) -> str:
    date, clock = token
    return (
        f"{date[0:4]}-{date[4:6]}-{date[6:8]}T"
        f"{clock[0:2]}:{clock[2:4]}:{clock[4:6]}Z"
    )


def _read_times(
    path: pathlib.Path, h5: Any, warnings: list[str]
) -> tuple[str | None, str | None]:
    reference, ref_source = _first_candidate(h5, REFERENCE_TIME_CANDIDATES)
    secondary, sec_source = _first_candidate(h5, SECONDARY_TIME_CANDIDATES)

    # A *present* metadata time that is not an ISO-8601 timestamp must not become a fabricated
    # date (any 8 leading digits, e.g. epoch seconds -> month 00) nor a silent None that also
    # suppresses the filename fallback. Record it, drop it, and let the filename tokens have a turn.
    if reference is not None and _date_part(str(reference)) is None:
        warnings.append(
            f"reference acquisition time {str(reference).strip()!r} at {ref_source} is not a "
            f"usable ISO-8601 timestamp (YYYY-MM-DD[THH:MM:SS]); no date is derived from it and "
            f"the filename tokens are tried instead — {DOC_REFERENCE} §5.1"
        )
        reference, ref_source = None, None
    if secondary is not None and _date_part(str(secondary)) is None:
        warnings.append(
            f"secondary acquisition time {str(secondary).strip()!r} at {sec_source} is not a "
            f"usable ISO-8601 timestamp (YYYY-MM-DD[THH:MM:SS]); no date is derived from it and "
            f"the filename tokens are tried instead — {DOC_REFERENCE} §5.1"
        )
        secondary, sec_source = None, None

    if reference is not None and secondary is not None:
        return str(reference).strip(), str(secondary).strip()

    tokens = _tokens_from_filename(path)
    used_filename = False
    if reference is None and tokens:
        reference = _iso_token(tokens[0])
        ref_source = "filename"
        used_filename = True
    if secondary is None and len(tokens) >= 2:
        secondary = _iso_token(tokens[1])
        sec_source = "filename"
        used_filename = True

    if used_filename:
        warnings.append(
            "acquisition time(s) parsed from the filename because the corresponding "
            "metadata/identification entries were absent or unusable: "
            f"reference={reference!r} secondary={secondary!r} — verify on the first real granule "
            f"({DOC_REFERENCE} §5.1)"
        )
    if reference is None or secondary is None:
        warnings.append(
            "acquisition date pair incomplete: "
            f"reference={reference!r} (from {ref_source}), secondary={secondary!r} "
            f"(from {sec_source}); metadata/identification paths and the filename were checked"
        )
    return (
        None if reference is None else str(reference).strip(),
        None if secondary is None else str(secondary).strip(),
    )


def _read_wavelength(h5: Any, warnings: list[str]) -> tuple[float | None, str | None]:
    for candidate in WAVELENGTH_CANDIDATES:
        value, source = _candidate_value(h5, candidate)
        if value is None or source is None:
            continue
        try:
            wavelength = float(value)
        except (TypeError, ValueError):
            warnings.append(f"ignored non-numeric wavelength {value!r} at {source}")
            continue
        if np.isfinite(wavelength) and wavelength > 0.0:
            return wavelength, f"metadata:{source}"
        warnings.append(f"ignored implausible wavelength {wavelength!r} at {source}")

    for candidate in CENTER_FREQUENCY_CANDIDATES:
        value, source = _candidate_value(h5, candidate)
        if value is None or source is None:
            continue
        try:
            frequency = float(value)
        except (TypeError, ValueError):
            warnings.append(f"ignored non-numeric centerFrequency {value!r} at {source}")
            continue
        if np.isfinite(frequency) and frequency > 0.0:
            wavelength = SPEED_OF_LIGHT_M_S / frequency
            warnings.append(
                f"wavelength_m derived as c/f from centerFrequency={frequency!r} Hz at {source}; "
                "no explicit wavelength dataset was present — verify against the first real "
                f"granule ({DOC_REFERENCE} §4)"
            )
            return wavelength, f"derived:centerFrequency:{source}"
        warnings.append(f"ignored implausible centerFrequency {frequency!r} at {source}")

    warnings.append(
        "wavelength_m absent: no explicit wavelength or centerFrequency metadata found at the "
        "candidate paths; leaving it None rather than assuming the L-band 0.2384 m — "
        f"phase→mm conversion is blocked until this is corrected ({DOC_REFERENCE} §4/§5.1)"
    )
    return None, None


# ---------------------------------------------------------------------------------------------
# Required datasets
# ---------------------------------------------------------------------------------------------


def _required_path(key: str, polarisation: str | None = None) -> str:
    template = DATASETS[key]
    return template.format(pol=polarisation) if "{pol}" in template else template


def _missing_dataset_error(h5: Any, path: str) -> GranuleError:
    parent = path.rsplit("/", 1)[0]
    available: list[str] = []
    obj = h5.get(parent)
    if obj is not None and hasattr(obj, "keys"):
        available = sorted(obj.keys())
    return GranuleError(
        f"required GUNW dataset not found: {path!r} (mapping from {DOC_REFERENCE} §2). "
        f"Contents of {parent!r}: {available}. If the real granule differs, correct the "
        "DATASETS table in pipeline/ingest/nisar_gunw.py."
    )


def _warn_declared_fill_value(obj: Any, path: str, warnings: list[str]) -> None:
    """Surface a declared fill value instead of guessing what it means.

    `docs/nisar-gunw-layout.md` §5.4 says GUNW fill conventions are unverified, so this reader does
    not reinterpret a `_FillValue`/`missing_value`. It only warns: the mask (127 = padding) and
    non-finite checks decide validity. If the first real granule uses a fill convention, that is a
    deliberate code change, not a silent one.
    """
    attrs = getattr(obj, "attrs", None)
    if attrs is None:
        return
    for attr in ("_FillValue", "missing_value"):
        if attr in attrs:
            value = _as_scalar(attrs[attr])
            warnings.append(
                f"{path}: declares {attr}={value!r}; it is NOT interpreted as nodata here "
                f"(mask 127 and non-finite floats do that). Verify the fill convention on the "
                f"first real granule — {DOC_REFERENCE} §5.1"
            )


def _read_required(
    h5: Any,
    key: str,
    polarisation: str,
    dtype: np.dtype,
    warnings: list[str],
    *,
    integer: bool,
) -> np.ndarray:
    path = _required_path(key, polarisation)
    if path not in h5:
        raise _missing_dataset_error(h5, path)
    obj = h5[path]
    if not hasattr(obj, "shape"):
        raise GranuleError(
            f"required GUNW dataset path is a group, not a dataset: {path!r} "
            f"(mapping from {DOC_REFERENCE} §2)"
        )
    try:
        data = np.asarray(obj[()])
    except Exception as exc:  # noqa: BLE001
        raise GranuleError(
            f"cannot read required GUNW dataset {path!r}: {exc}; the file may be truncated or "
            f"corrupt (see {DOC_REFERENCE} §7)"
        ) from exc

    if data.ndim != 2:
        raise GranuleError(
            f"required GUNW dataset {path!r} is {data.ndim}-D with shape {data.shape}; "
            f"expected a 2-D grid ({DOC_REFERENCE} §2)"
        )
    if data.size == 0:
        raise GranuleError(
            f"required GUNW dataset {path!r} is empty (shape {data.shape}); refusing to treat an "
            f"empty grid as data ({DOC_REFERENCE} §2)"
        )

    _warn_declared_fill_value(obj, path, warnings)

    if integer:
        if data.dtype.kind not in "iu":
            raise GranuleError(
                f"required GUNW dataset {path!r} has dtype {data.dtype}, expected an integer "
                f"dtype; refusing to cast ({DOC_REFERENCE} §2)"
            )
        minimum = int(data.min())
        maximum = int(data.max())
        if minimum < 0:
            raise GranuleError(
                f"required GUNW dataset {path!r} contains negative integer value {minimum}; "
                f"that is not a valid label/flag ({DOC_REFERENCE} §2)"
            )
        if dtype == np.dtype(np.int8) and maximum > 127:
            raise GranuleError(
                f"required GUNW dataset {path!r} contains mask value {maximum}, which does not "
                f"fit int8; refusing to wrap it ({DOC_REFERENCE} §2)"
            )
        if dtype == np.dtype(np.uint32) and maximum > np.iinfo(np.uint32).max:
            raise GranuleError(
                f"required GUNW dataset {path!r} contains label {maximum}, which does not fit "
                f"uint32; refusing to wrap it ({DOC_REFERENCE} §2)"
            )
    else:
        if data.dtype.kind != "f":
            raise GranuleError(
                f"required GUNW dataset {path!r} has dtype {data.dtype}, expected a float dtype; "
                f"refusing to reinterpret it ({DOC_REFERENCE} §2)"
            )

    if data.dtype != dtype:
        warnings.append(
            f"{path}: dtype {data.dtype} cast to {np.dtype(dtype)} (the granule declares a "
            f"different dtype than {DOC_REFERENCE} §2)"
        )
        data = data.astype(dtype, copy=False)
    return data


def _select_polarisation(
    h5: Any, requested: str | None, warnings: list[str]
) -> str:
    """Pick the co-pol group, failing closed when it is missing or ambiguous."""
    group_path = DATASETS["unwrapped_group"]
    if group_path not in h5:
        raise GranuleError(
            f"required GUNW group not found: {group_path!r} (mapping from {DOC_REFERENCE} §2). "
            "Check that this is an L2 GUNW granule and not a different NISAR product."
        )
    group = h5[group_path]
    if not hasattr(group, "keys"):
        raise GranuleError(
            f"expected a group at {group_path!r}, found a dataset "
            f"({DOC_REFERENCE} §2)"
        )
    children = sorted(group.keys())
    found = [name for name in children if name in POLARISATIONS]

    if requested is not None:
        requested = requested.upper()
        if requested not in POLARISATIONS:
            raise GranuleError(
                f"invalid polarisation {requested!r}: expected one of {list(POLARISATIONS)} "
                f"({DOC_REFERENCE} §1)"
            )
        if requested not in children:
            raise GranuleError(
                f"requested polarisation {requested!r} not present under {group_path!r}; "
                f"found {children} ({DOC_REFERENCE} §2)"
            )
        if len(found) == 2:
            warnings.append(
                f"both HH and VV groups are present under {group_path!r}; using the explicitly "
                f"requested {requested!r}. GUNW should be co-pol only, so inspect this granule."
            )
        return requested

    if len(found) == 2:
        raise GranuleError(
            f"ambiguous polarisation: found both HH and VV under {group_path!r} "
            f"(contents: {children}). GUNW is co-polarised only ({DOC_REFERENCE} §1); "
            "pass --polarisation to choose explicitly if the product is malformed."
        )
    if not found:
        raise GranuleError(
            f"no polarisation found under {group_path!r}: expected exactly one of HH or VV, "
            f"found {children}. The granule layout does not match {DOC_REFERENCE} §2."
        )
    return found[0]


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------


def discover_polarisation(path: str | pathlib.Path) -> str:
    """Return ``"HH"`` or ``"VV"`` by listing ``unwrappedInterferogram/`` children.

    Never hardcodes the polarisation. Raises :class:`GranuleError` when both or neither exist,
    naming what was actually found. See ``docs/nisar-gunw-layout.md`` §1–§2.
    """
    h5py = _import_h5py()
    granule_path = pathlib.Path(path)
    if not granule_path.exists():
        raise GranuleError(
            f"granule not found: {str(granule_path)!r}. Check the path; "
            f"layout reference: {DOC_REFERENCE}"
        )
    try:
        with h5py.File(granule_path, "r") as handle:
            return _select_polarisation(handle, None, [])
    except OSError as exc:
        raise GranuleError(
            f"cannot open {str(granule_path)!r} as HDF5: {exc}. A failed download often leaves "
            f"an HTML/JSON error page with an .h5 name; check the bytes ({DOC_REFERENCE} §7)."
        ) from exc


def read_granule(
    path: str | pathlib.Path, polarisation: str | None = None
) -> GunwGranule:
    """Read one GUNW granule. Structural problems raise :class:`GranuleError`; optional metadata
    absence is recorded in ``GunwGranule.warnings`` and left ``None`` — never guessed."""
    h5py = _import_h5py()
    granule_path = pathlib.Path(path)
    if not granule_path.exists():
        raise GranuleError(
            f"granule not found: {str(granule_path)!r}. Check the path; "
            f"layout reference: {DOC_REFERENCE}"
        )
    if granule_path.is_dir():
        raise GranuleError(
            f"granule path is a directory, not an HDF5 file: {str(granule_path)!r} "
            f"({DOC_REFERENCE} §7)"
        )

    warnings: list[str] = []
    try:
        handle_context = h5py.File(granule_path, "r")
    except OSError as exc:
        raise GranuleError(
            f"cannot open {str(granule_path)!r} as HDF5: {exc}. A failed download often leaves "
            f"an HTML/JSON error page with an .h5 name; check the bytes ({DOC_REFERENCE} §7)."
        ) from exc

    with handle_context as h5:
        pol = _select_polarisation(h5, polarisation, warnings)

        phase = _read_required(
            h5, "phase", pol, np.dtype(np.float32), warnings, integer=False
        )
        coherence = _read_required(
            h5, "coherence", pol, np.dtype(np.float32), warnings, integer=False
        )
        connected = _read_required(
            h5, "connected_components", pol, np.dtype(np.uint32), warnings, integer=True
        )
        mask = _read_required(
            h5, "mask", pol, np.dtype(np.int8), warnings, integer=True
        )

        # Documented mask semantics are 0/2/127 (§2). Any other value is *kept* by valid_mask()
        # but must not be swallowed: name it with its pixel count so a human can decide.
        _, unknown_mask_values = _mask_value_summary(mask)
        if unknown_mask_values:
            unknown_pixels = int(np.isin(mask, unknown_mask_values).sum())
            warnings.append(
                f"{_required_path('mask', pol)}: undocumented mask value(s) "
                f"{unknown_mask_values} at {unknown_pixels} pixel(s); valid_mask() carries them "
                f"through unchanged and does not reinterpret them (documented values are "
                f"{list(OBSERVED_MASK_VALUES)}) — see {DOC_REFERENCE} §2 for the documented "
                f"semantics and §5 for the unknowns"
            )

        shapes = {
            _required_path("phase", pol): phase.shape,
            _required_path("coherence", pol): coherence.shape,
            _required_path("connected_components", pol): connected.shape,
            _required_path("mask", pol): mask.shape,
        }
        if len(set(shapes.values())) != 1:
            detail = ", ".join(f"{name} {shape}" for name, shape in shapes.items())
            raise GranuleError(
                f"required GUNW datasets have inconsistent shapes: {detail}. Refusing to read a "
                f"misaligned grid ({DOC_REFERENCE} §2)."
            )

        x, x_path = _read_coordinate(
            h5, X_COORDINATE_CANDIDATES, phase.shape[1], "x", warnings
        )
        y, y_path = _read_coordinate(
            h5, Y_COORDINATE_CANDIDATES, phase.shape[0], "y", warnings
        )
        epsg = _read_epsg(h5, x_path, y_path, warnings)
        reference_time, secondary_time = _read_times(granule_path, h5, warnings)
        wavelength_m, wavelength_source = _read_wavelength(h5, warnings)

    granule = GunwGranule(
        phase_rad=phase,
        coherence=coherence,
        connected_components=connected,
        mask=mask,
        polarisation=pol,
        x=x,
        y=y,
        epsg=epsg,
        reference_time=reference_time,
        secondary_time=secondary_time,
        wavelength_m=wavelength_m,
        wavelength_source=wavelength_source,
        source_path=str(granule_path.resolve()),
        warnings=warnings,
    )
    if not valid_mask(granule).any():
        # An all-NaN / fully padded granule is a legal read but a useless pair. The counts stay
        # empty (nothing is imputed); the warning is what stops downstream aggregation from
        # treating "0 valid pixels" as a clean granule.
        warnings.append(
            "granule has 0 valid pixels: every phase/coherence value is non-finite or padding "
            "(mask 127); downstream aggregation must skip this pair"
        )
    return granule


def valid_mask(granule: GunwGranule) -> np.ndarray:
    """Boolean pixels that carry data: mask != 127 and finite phase and coherence.

    Observed mask values in the published sample are 0 (no quality flags), 2 (some flag set) and
    127 (padding). 127 is dropped as nodata/padding. **0 and 2 are kept**: 0 is a legitimate
    measurement (in Málaga, zero displacement is the expected value), and 2 means "a quality flag
    was set", which is a later filtering decision — dropping it here would discard data and hide
    the flag from the caller. Any other mask value is carried through untouched; this function
    only removes padding and non-finite floats, it does not reinterpret unknown flags.

    Non-finite phase or coherence is also excluded. The caller still receives the raw mask array,
    so an additional filter can be applied downstream.
    """
    mask = np.asarray(granule.mask)
    phase = np.asarray(granule.phase_rad)
    coherence = np.asarray(granule.coherence)
    if not (mask.shape == phase.shape == coherence.shape):
        raise GranuleError(
            "valid_mask: mask/phase/coherence shapes differ "
            f"({mask.shape}, {phase.shape}, {coherence.shape}); the granule is misaligned "
            f"({DOC_REFERENCE} §2)"
        )
    return (mask != MASK_PADDING) & np.isfinite(phase) & np.isfinite(coherence)


def phase_to_los_displacement_mm(
    phase_rad: Any, wavelength_m: float | None, sign: float = -1.0
) -> np.ndarray:
    """Convert unwrapped phase to line-of-sight displacement in millimetres::

        d_LOS = sign * λ / (4π) * φ        [metres]  →  × 1000 for mm

    Non-finite phase (NaN, ±inf) yields **NaN**, never 0: an invalid pixel has no displacement,
    and in Málaga 0 is a real measurement. Masking is the caller's job via :func:`valid_mask`;
    for convenience a ``numpy.ma`` input is honoured by filling masked entries with NaN.

    The default ``sign=-1.0`` is the ``-λ/4π·φ`` convention quoted in ``docs/nisar-gunw-layout.md``
    §4. **That sign must be validated against GNSS or a published rate before publication**;
    ISCE2, MintPy and SNAP differ. The caller records which convention it used (e.g. in
    ``meta.provenance.method``), because the sign flips uplift into subsidence.

    ``wavelength_m`` is required: this function refuses to assume L-band's ~0.2384 m. A missing or
    implausible wavelength raises ``ValueError`` rather than producing a plausible-looking number.
    """
    if wavelength_m is None:
        raise ValueError(
            "phase_to_los_displacement_mm: wavelength_m is None; refusing to assume a wavelength. "
            f"Read it from metadata or record the assumption explicitly ({DOC_REFERENCE} §4)."
        )
    wavelength = float(wavelength_m)
    if not np.isfinite(wavelength) or wavelength <= 0.0:
        raise ValueError(
            f"phase_to_los_displacement_mm: wavelength_m must be finite and positive, "
            f"got {wavelength_m!r} ({DOC_REFERENCE} §4)"
        )
    if not np.isfinite(sign) or float(sign) not in (-1.0, 1.0):
        raise ValueError(
            f"phase_to_los_displacement_mm: sign must be -1 or +1, got {sign!r}; the convention "
            f"is not a tunable scale factor ({DOC_REFERENCE} §4)"
        )

    if isinstance(phase_rad, np.ma.MaskedArray):
        phase = np.asarray(np.ma.filled(phase_rad, np.nan), dtype=float)
    else:
        phase = np.asarray(phase_rad, dtype=float)

    output = (float(sign) * wavelength * 1000.0 / (4.0 * np.pi)) * phase
    # NaN and ±inf are invalid input, never physical displacement. `np.where` would also map
    # inf*0 to NaN inconsistently; mask explicitly after the arithmetic.
    output = np.asarray(output, dtype=float)
    output[~np.isfinite(phase)] = np.nan
    return output


def _date_part(value: str | None) -> str | None:
    """Return YYYY-MM-DD from an ISO-8601 timestamp, or None rather than inventing a date.

    Only a real ``YYYY-MM-DD`` shape followed by ``T`` or end-of-string is accepted, with
    ``1 <= month <= 12`` and ``1 <= day <= 31``. A bare digit run is **not** a date: epoch
    seconds ``1783000000.5`` or epoch nanoseconds ``1783000000123456789`` must not be sliced into
    ``'1783-00-00'``. ``None`` means "not a date", and the caller decides what to warn about.
    """
    if not value:
        return None
    text = str(value).strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:T|$)", text)
    if match is None:
        return None
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year}-{month:02d}-{day:02d}"


def _mask_value_summary(mask: np.ndarray) -> tuple[list[int], list[int]]:
    """Return ``(values, unknown)``: every mask value present, and those §2 does not document."""
    values = sorted(int(value) for value in np.unique(np.asarray(mask)))
    unknown = [value for value in values if value not in OBSERVED_MASK_VALUES]
    return values, unknown


def summary(granule: GunwGranule) -> dict[str, Any]:
    """Human-readable facts about the granule — what to read before trusting the file.

    Every statistic is computed over :func:`valid_mask` only. If no pixel is valid, percentiles
    and the component maximum are ``None`` rather than 0; nothing is imputed. ``wavelength_source``
    says whether λ came from metadata, from a recorded ``c/f`` derivation, or is absent.
    """
    phase = np.asarray(granule.phase_rad)
    valid = valid_mask(granule)
    total_pixels = int(valid.size)
    valid_pixels = int(valid.sum())
    fraction = (valid_pixels / total_pixels) if total_pixels else 0.0

    coherence_values = np.asarray(granule.coherence, dtype=float)[valid]
    if coherence_values.size:
        p05, p50, p95 = (float(value) for value in np.percentile(coherence_values, [5, 50, 95]))
        percentiles: dict[str, float | None] = {"p05": p05, "p50": p50, "p95": p95}
        coherence_mean: float | None = float(coherence_values.mean())
    else:
        percentiles = {"p05": None, "p50": None, "p95": None}
        coherence_mean = None

    component_values = np.asarray(granule.connected_components)[valid]
    if component_values.size:
        labels = np.unique(component_values)
        n_labels_including_zero = int(labels.size)
        n_labels_nonzero = int(np.count_nonzero(labels != 0))
        max_label = int(labels.max())
        zero_share = float(np.count_nonzero(component_values == 0) / component_values.size)
    else:
        n_labels_including_zero = 0
        n_labels_nonzero = 0
        max_label = None
        zero_share = None

    reference_date = _date_part(granule.reference_time)
    secondary_date = _date_part(granule.secondary_time)

    mask_values, mask_unknown_values = _mask_value_summary(granule.mask)

    # Defensive: a manually built GunwGranule can carry an unparseable time without read_granule
    # having warned about it. Never let that become a silent None.
    report_warnings = list(granule.warnings)
    for label, raw_value, date in (
        ("reference", granule.reference_time, reference_date),
        ("secondary", granule.secondary_time, secondary_date),
    ):
        if raw_value is None or date is not None:
            continue
        message = (
            f"{label} acquisition time {str(raw_value).strip()!r} is not a usable ISO-8601 "
            f"timestamp (YYYY-MM-DD[THH:MM:SS]); no date is derived from it — {DOC_REFERENCE} §5.1"
        )
        if message not in report_warnings:
            report_warnings.append(message)

    return {
        "path": granule.source_path,
        "shape": [int(phase.shape[0]), int(phase.shape[1])],
        "polarisation": granule.polarisation,
        "epsg": None if granule.epsg is None else int(granule.epsg),
        "dates": {"reference": reference_date, "secondary": secondary_date},
        "date_pair": [reference_date, secondary_date],
        "valid_fraction": float(fraction),
        "valid_pixels": valid_pixels,
        "total_pixels": total_pixels,
        "coherence_percentiles": percentiles,
        "coherence_mean": coherence_mean,
        # 0 means "not in a connected component" and is not counted as one; the including-zero
        # count is exposed explicitly so a human can see the convention rather than infer it.
        "connected_component_count": n_labels_nonzero,
        "connected_component_max": max_label,
        "connected_components": {
            "n_labels_including_zero": n_labels_including_zero,
            "n_labels_nonzero": n_labels_nonzero,
            "max_label": max_label,
            "zero_share": zero_share,
        },
        # Mask semantics are §2: 0/2/127 are documented, anything else is recorded and carried
        # through unchanged so a caller can decide instead of inheriting a silent filter.
        "mask_values": mask_values,
        "mask_unknown_values": mask_unknown_values,
        "wavelength_m": None if granule.wavelength_m is None else float(granule.wavelength_m),
        "wavelength_source": granule.wavelength_source or "absent",
        "warnings": report_warnings,
    }


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _format_summary(report: dict[str, Any]) -> str:
    shape = report["shape"]
    dates = report["dates"]
    percentiles = report["coherence_percentiles"]
    components = report["connected_components"]
    wavelength = report["wavelength_m"]

    def pct(key: str) -> str:
        value = percentiles[key]
        return "n/a" if value is None else f"{value:.3f}"

    epsg = "unknown (see warnings)" if report["epsg"] is None else f"EPSG:{report['epsg']}"
    wavelength_text = (
        "absent (see warnings)"
        if wavelength is None
        else f"{wavelength:.6f} m ({report['wavelength_source']})"
    )
    max_label = "n/a" if report["connected_component_max"] is None else str(report["connected_component_max"])
    zero_share = components["zero_share"]
    zero_text = "n/a" if zero_share is None else f"{zero_share:.1%}"

    lines = [
        f"NISAR L2 GUNW: {report['path']}",
        f"  shape                  {shape[0]} rows x {shape[1]} columns",
        f"  polarisation           {report['polarisation']}",
        f"  epsg                   {epsg}",
        f"  reference date         {dates['reference'] or 'unknown'}",
        f"  secondary date         {dates['secondary'] or 'unknown'}",
        (
            f"  valid fraction         {report['valid_fraction']:.2%} "
            f"({report['valid_pixels']} / {report['total_pixels']} px, mask!=127 and finite)"
        ),
        f"  coherence p05/p50/p95  {pct('p05')} / {pct('p50')} / {pct('p95')}",
        (
            f"  connected components   {report['connected_component_count']} non-zero labels, "
            f"max {max_label} (zero-label share {zero_text})"
        ),
        f"  wavelength             {wavelength_text}",
        f"  warnings               {len(report['warnings'])}",
    ]
    lines.extend(f"    ! {warning}" for warning in report["warnings"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nisar_gunw.py",
        description=(
            "Read a NISAR L2 GUNW granule and print a provenance summary. Fails closed: a missing "
            "required dataset, an unreadable file or an ambiguous polarisation exits non-zero."
        ),
        epilog=f"Dataset mapping and unknowns: {DOC_REFERENCE}",
    )
    parser.add_argument("granule", help="path to the NISAR L2 GUNW .h5 granule")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the summary as JSON instead of human-readable text",
    )
    parser.add_argument(
        "--polarisation",
        type=str.upper,
        choices=POLARISATIONS,
        default=None,
        help="read this co-pol group when the granule is ambiguous (normally auto-detected)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        granule = read_granule(args.granule, polarisation=args.polarisation)
        report = summary(granule)
    except GranuleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_format_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
