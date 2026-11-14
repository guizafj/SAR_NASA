#!/usr/bin/env python3
"""inspect_hdf5.py — read a satellite-SAR product's layout from evidence, not memory.

The first real NISAR GUNW (or OPERA DISP-S1) granule is the authority on its own HDF5
layout. This command walks the file lazily and prints groups, dataset metadata, attributes
and a short list of plausible phase / coherence / connected-component / geolocation
candidates. It never reads dataset contents unless `--sample-rows` asks for a bounded
sample.

Usage
-----
    ./scripts/python.sh pipeline/ingest/inspect_hdf5.py GRANULE.h5
    ./scripts/python.sh pipeline/ingest/inspect_hdf5.py data/raw/malaga/nisar_gunw/
    ./scripts/python.sh pipeline/ingest/inspect_hdf5.py GRANULE.h5 --json
    ./scripts/python.sh pipeline/ingest/inspect_hdf5.py GRANULE.h5 \
        --product nisar-gunw --max-depth 6 --sample-rows 2

What it will not do
-------------------
* Guess that a dataset is the one we need and silently invent a path.
* Read a full array, or any array when `--sample-rows 0` (the default).
* Fail just because a product's real layout differs from the provisional expectations below.

Exit status is 0 for a successful inspection (even when expectations are absent), 1 when a
file is not HDF5, is truncated/corrupt, has no `/science` group, or the product layout is
unknown under the default discovery mode, and 2 for a bad path/usage.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import h5py
    import numpy as np
except ImportError as exc:  # pragma: no cover - exercised only on a broken environment
    raise SystemExit(
        "error: inspect_hdf5.py needs h5py and numpy; install requirements.txt through "
        "./scripts/python.sh -m pip install -r requirements.txt"
    ) from exc


# =============================================================================================
# PROVISIONAL EXPECTED LAYOUTS — THESE ARE GUESSES, NOT A CONTRACT
# =============================================================================================
# Derived from public NISAR/OPERA product descriptions and file-name conventions, NOT from a
# real granule. The first real GUNW or DISP-S1 file wins. When it lands:
#
#   1. Run `--product nisar-gunw` (or opera-disp-s1) and read the PASS/absent lines.
#   2. Move this block to match what the granule actually contains.
#   3. Correct CANDIDATE_RULES below at the same time.
#
# Specifically re-check against the first real granule:
#   * NISAR GUNW: does phase live at `.../frequencyA/unwrappedInterferogram/<POL>/...` or
#     directly at `.../frequencyA/...`? Is the geolocation container `geolocationGrid`,
#     `radarGrid`, or something else? Are frequencyA/B and HH/VV the only choices?
#   * OPERA DISP-S1: is it HDF5/NetCDF with root `/displacement`, `/coherence`,
#     `/connected_components`, `/unwrapped_phase`, and `/x`, `/y`, `/spatial_ref`, or are the
#     products inside a `/science` or `/data` group? What is the CRS variable really called?
#   * Sentinel-1 SLC: the expected layout below is only a placeholder. Real ESA SLC has
#     annotation/measurement groups with product-specific names; do not treat it as verified.
#
# `groups` are checked with `h5py.File.get(path)` and must be groups. `datasets` entries are
# alternatives: PASS if any listed path is a dataset. Missing expectations are reported as
# `[absent]`, never as a run failure.
# Paths for phase / coherence / connected components / mask were verified on 2026-11-14 against the
# NISAR Data User Guide and the ASF sample-data tutorial — see docs/nisar-gunw-layout.md. The
# geolocation container name (`geolocationGrid` below) is still unconfirmed: for L2 products the
# spatial reference is carried by CF metadata, so the check may only be satisfied via attributes.
# Expectations are advisory: a missing group prints `[absent]`, never a failure.
PRODUCT_LAYOUTS: dict[str, dict[str, Any]] = {
    "nisar-gunw": {
        "title": "NISAR L2 GUNW (provisional)",
        "requires_science": True,
        "groups": [
            "/science",
            "/science/LSAR",
            "/science/LSAR/GUNW",
            "/science/LSAR/GUNW/grids",
            "/science/LSAR/GUNW/grids/frequencyA",
            "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram",
            "/science/LSAR/GUNW/grids/frequencyA/geolocationGrid",
            "/science/LSAR/GUNW/metadata",
            "/science/LSAR/GUNW/metadata/processingInformation",
            "/science/LSAR/GUNW/metadata/orbit",
            "/science/LSAR/GUNW/metadata/attitude",
        ],
        "datasets": [
            {
                "role": "phase",
                "paths": [
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase",
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/VV/unwrappedPhase",
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedPhase",
                ],
            },
            {
                "role": "coherence",
                "paths": [
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/coherenceMagnitude",
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/VV/coherenceMagnitude",
                    "/science/LSAR/GUNW/grids/frequencyA/coherence",
                ],
            },
            {
                "role": "connected-components",
                "paths": [
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/connectedComponents",
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/VV/connectedComponents",
                    "/science/LSAR/GUNW/grids/frequencyA/connectedComponents",
                ],
            },
            {
                "role": "mask",
                "paths": [
                    # Verified: int8 quality flags, 127 = padding (docs/nisar-gunw-layout.md §2)
                    "/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/mask",
                ],
            },
            {
                "role": "pixel-offsets",
                "paths": [
                    # Verified layer; not needed for the displacement path, but its absence is a
                    # signal that the product variant differs from what we documented
                    "/science/LSAR/GUNW/grids/frequencyA/pixelOffsets/HH/alongTrackOffset",
                    "/science/LSAR/GUNW/grids/frequencyA/pixelOffsets/VV/alongTrackOffset",
                    "/science/LSAR/GUNW/grids/frequencyA/pixelOffsets/HH/slantRangeOffset",
                ],
            },
            {
                "role": "wrapped-interferogram",
                "paths": [
                    "/science/LSAR/GUNW/grids/frequencyA/wrappedInterferogram/HH/wrappedInterferogram",
                    "/science/LSAR/GUNW/grids/frequencyA/wrappedInterferogram/VV/wrappedInterferogram",
                ],
            },
            {
                "role": "geolocation",
                "paths": [
                    "/science/LSAR/GUNW/grids/frequencyA/geolocationGrid/xCoordinates",
                    "/science/LSAR/GUNW/grids/frequencyA/geolocationGrid/yCoordinates",
                    "/science/LSAR/GUNW/grids/frequencyA/geolocationGrid/projection",
                    "/science/LSAR/GUNW/grids/frequencyA/radarGrid/xCoordinates",
                    "/science/LSAR/GUNW/grids/frequencyA/radarGrid/yCoordinates",
                ],
            },
        ],
    },
    "opera-disp-s1": {
        "title": "OPERA DISP-S1 (provisional; root-level NetCDF-like layout)",
        "requires_science": False,  # DISP-S1 may be root-level rather than NISAR's /science
        "groups": [
            "/displacement",
            "/coherence",
            "/connected_components",
            "/unwrapped_phase",
            "/identification",
            "/orbit",
        ],
        "datasets": [
            {"role": "phase", "paths": ["/unwrapped_phase/unwrapped_phase", "/unwrapped_phase"]},
            {"role": "coherence", "paths": ["/coherence/coherence", "/coherence"]},
            {
                "role": "connected-components",
                "paths": ["/connected_components/connected_components", "/connected_components"],
            },
            {
                "role": "geolocation",
                "paths": ["/x/x", "/y/y", "/x", "/y", "/spatial_ref", "/crs"],
            },
        ],
    },
    "s1-slc": {
        "title": "Sentinel-1 SLC (placeholder only; not verified)",
        "requires_science": True,
        "groups": [
            "/science",
            "/science/SENTINEL1",
            "/science/SENTINEL1/identification",
            "/science/SENTINEL1/annotation",
            "/science/SENTINEL1/measurement",
        ],
        "datasets": [
            {
                "role": "metadata",
                "paths": [
                    "/science/SENTINEL1/identification/productType",
                    "/science/SENTINEL1/identification/missionId",
                ],
            }
        ],
    },
    "unknown": {
        "title": "unknown product (discovery only; no expectations)",
        "requires_science": True,
        "groups": [],
        "datasets": [],
    },
}


# =============================================================================================
# CANDIDATE HEURISTICS — EDIT THIS TABLE FIRST WHEN A REAL GRANULE DISAGREES
# =============================================================================================
# Match the dataset's leaf name first. `path_components` is a fallback for products that put
# phase/coherence in a container group (e.g. `/coherence/data`). We deliberately do NOT match
# ancestor names such as `unwrappedInterferogram` for every child: an `amplitude` dataset in
# that group is not phase.
#
# The compiled patterns are case-insensitive. `reason` is what the human report prints.
CANDIDATE_RULES: tuple[dict[str, Any], ...] = (
    {
        "category": "phase",
        "reason": "unwrapped phase",
        "leaf": (r"unwrapped[\s_\-]?phase", r"^phase$", r"^unwrapped$"),
        "path_components": (),
    },
    {
        "category": "coherence",
        "reason": "coherence",
        "leaf": (r"coherence", r"^coh$"),
        "path_components": (r"^coherence$",),
    },
    {
        "category": "connected-components",
        "reason": "connected components",
        "leaf": (r"connected[\s_\-]?components?", r"conn[\s_\-]?comp", r"^components?$"),
        "path_components": (r"connected[\s_\-]?components?",),
    },
    {
        # Explicit, because without it `mask` matches none of the leaf patterns and falls through to
        # a path-component rule for `grids`, which is how it was being reported as "geolocation".
        "category": "quality-mask",
        "reason": "quality/padding mask (0 = no flags, 2 = flag set, 127 = padding)",
        "leaf": (r"^mask$", r"quality[\s_\-]?mask", r"^validity$"),
        "path_components": (),
    },
    {
        "category": "geolocation",
        "reason": "geolocation",
        "leaf": (
            r"^x$",
            r"^y$",
            r"x[\s_\-]?coordinates?",
            r"y[\s_\-]?coordinates?",
            r"coordinate[\s_\-]?[xy]",
            r"^projection$",
            r"^spatial[\s_\-]?ref(?:erence)?$",
            r"^crs$",
            r"^longitude$",
            r"^latitude$",
            r"^height$",
            r"^incidence[\s_\-]?angle$",
        ),
        "path_components": (
            r"geolocation(?:grid)?",
            r"radar[\s_\-]?grid",
        ),
    },
    {
        "category": "metadata",
        "reason": "metadata (identification/orbit/attitude)",
        "leaf": (),
        "path_components": (r"identification", r"orbit", r"attitude", r"metadata"),
    },
)


# Attributes we surface in both human and JSON reports. Name matching is case-insensitive so
# both `_FillValue` and HDF5's lowercase `fillvalue` are found.
REPORTED_ATTRIBUTES = (
    "units",
    "_FillValue",
    "fillvalue",
    "long_name",
    "description",
    "scale_factor",
    "add_offset",
)

HDF5_SUFFIXES = {".h5", ".hdf5", ".hdf"}
HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"

#: Cap for a single `--sample-rows` read, so `--sample-rows 1000` on a wide array stays bounded.
SAMPLE_MAX_ELEMENTS = 10_000


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Convert numpy/HDF5 attribute values into strict-JSON-safe Python values."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _attrs_selected(attrs: Any) -> dict[str, Any]:
    """Return only the attributes the report promises, preserving their on-disk names."""
    selected: dict[str, Any] = {}
    lowered = {str(key).lower(): key for key in attrs.keys()}
    for wanted in REPORTED_ATTRIBUTES:
        key = lowered.get(wanted.lower())
        if key is not None:
            selected[str(key)] = _jsonable(attrs[key])
    # Prefer the canonical spelling in output when both `_FillValue` and `fillvalue` exist.
    return selected


def _first_attr(selected: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in selected.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _format_shape(shape: Iterable[int] | None) -> str:
    if shape is None:
        return "unknown"
    dims = tuple(int(dim) for dim in shape)
    return "(" + ",".join(str(dim) for dim in dims) + ("," if len(dims) == 1 else "") + ")"


def _format_bytes(count: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(count)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{count} B"


def _human_value(value: Any) -> str:
    value = _jsonable(value)
    if isinstance(value, list):
        return "[" + ", ".join(_human_value(item) for item in value) + "]"
    return str(value)


def _logical_bytes(shape: tuple[int, ...], itemsize: int) -> int:
    return math.prod(shape) * int(itemsize) if shape else int(itemsize)


def _matches_any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_dataset(hdf5_path: str) -> tuple[str | None, str | None]:
    """Return `(candidate_category, reason)` for a dataset path, or `(None, None)`.

    Leaf-name rules are applied first. Container-group rules are only a fallback, so an
    unrelated dataset under `unwrappedInterferogram` is not mislabelled as phase.
    """
    leaf = hdf5_path.rsplit("/", 1)[-1].lower()
    components = [part for part in hdf5_path.lower().split("/") if part]

    for rule in CANDIDATE_RULES:
        if rule["leaf"] and _matches_any(rule["leaf"], leaf):
            return rule["category"], rule["reason"]
    for rule in CANDIDATE_RULES:
        patterns = rule.get("path_components", ())
        if patterns and any(_matches_any(patterns, component) for component in components):
            return rule["category"], rule["reason"]
    return None, None


def _bounded_sample(dataset: Any, rows: int) -> dict[str, Any] | None:
    """Read at most `rows` along axis 0, capped to SAMPLE_MAX_ELEMENTS in total.

    Only called when `rows > 0`. Every other code path uses `shape`, `dtype`, `chunks` and
    attributes, which HDF5 serves from metadata without touching the data chunks.
    """
    if rows <= 0:
        return None

    shape = tuple(int(dim) for dim in dataset.shape)
    if not shape:
        data = dataset[()]
    else:
        budget = SAMPLE_MAX_ELEMENTS
        slices: list[slice] = []
        for axis, dim in enumerate(shape):
            if axis == 0:
                take = min(rows, dim)
            else:
                take = min(dim, max(1, budget))
                if budget <= 1:
                    take = min(dim, 1)
            slices.append(slice(0, take))
            budget = max(1, budget // max(1, take))
        data = dataset[tuple(slices)]

    array = np.asarray(data)
    return {
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "values": _jsonable(array),
        "note": f"first {rows} row(s), capped at {SAMPLE_MAX_ELEMENTS} values",
    }


def _open_error(path: Path, exc: BaseException) -> str:
    """Turn h5py's generic OSError into an actionable message."""
    try:
        signature = path.read_bytes()[: len(HDF5_SIGNATURE)]
    except OSError:
        signature = b""
    if signature != HDF5_SIGNATURE:
        return f"not an HDF5 file (missing HDF5 signature): {path}"
    return f"truncated or corrupt HDF5 file: {path} ({exc})"


# ---------------------------------------------------------------------------------------------
# Traversal and per-file report
# ---------------------------------------------------------------------------------------------


def _group_identity(group: Any) -> Any:
    """Stable HDF5 object address, so hard links/cycles cannot recurse forever."""
    try:
        return int(h5py.h5o.get_info(group.id).addr)
    except Exception:
        return (group.file.filename, group.name)


def _walk_group(
    group: Any,
    path: str,
    depth: int,
    *,
    max_depth: int | None,
    max_datasets: int | None,
    sample_rows: int,
    groups: list[str],
    datasets: list[dict[str, Any]],
    visited: set[Any],
    state: dict[str, bool],
) -> None:
    """Depth-first metadata walk. No dataset contents are touched here."""
    group_id = _group_identity(group)
    if group_id in visited:
        return
    visited.add(group_id)

    groups.append(path)
    if max_depth is not None and depth >= max_depth:
        return

    for name in sorted(group.keys()):
        child_path = f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
        try:
            child = group[name]
        except (KeyError, OSError):
            # A broken link should not hide the rest of the file.
            continue

        if isinstance(child, h5py.Group):
            _walk_group(
                child,
                child_path,
                depth + 1,
                max_depth=max_depth,
                max_datasets=max_datasets,
                sample_rows=sample_rows,
                groups=groups,
                datasets=datasets,
                visited=visited,
                state=state,
            )
            if state.get("dataset_limit_reached"):
                return
        elif isinstance(child, h5py.Dataset):
            if max_datasets is not None and len(datasets) >= max_datasets:
                state["dataset_limit_reached"] = True
                return
            datasets.append(_describe_dataset(child, child_path, sample_rows))


def _describe_dataset(dataset: Any, path: str, sample_rows: int) -> dict[str, Any]:
    selected = _attrs_selected(dataset.attrs)
    candidate, reason = classify_dataset(path)

    try:
        shape = tuple(int(dim) for dim in dataset.shape)
    except (TypeError, ValueError):
        shape = ()
    try:
        dtype = str(dataset.dtype)
        itemsize = int(dataset.dtype.itemsize)
    except (TypeError, ValueError):
        dtype, itemsize = "unknown", 0
    try:
        chunks = list(dataset.chunks) if dataset.chunks is not None else None
    except (TypeError, ValueError, RuntimeError):
        chunks = None
    try:
        compression = dataset.compression
    except (TypeError, ValueError, RuntimeError):
        compression = None

    record: dict[str, Any] = {
        "path": path,
        "shape": list(shape),
        "dtype": dtype,
        "chunks": chunks,
        "compression": compression,
        "logical_bytes": _logical_bytes(shape, itemsize),
        "attributes": selected,
        "units": _first_attr(selected, "units"),
        "fill_value": _first_attr(selected, "_FillValue", "fillvalue"),
        "long_name": _first_attr(selected, "long_name"),
        "description": _first_attr(selected, "description"),
        "scale_factor": _first_attr(selected, "scale_factor"),
        "add_offset": _first_attr(selected, "add_offset"),
        "candidate": candidate,
        "reason": reason,
    }
    if sample_rows > 0:
        try:
            record["sample"] = _bounded_sample(dataset, sample_rows)
        except Exception as exc:  # a corrupt chunk may only fail when actually read
            record["sample"] = {"error": f"{type(exc).__name__}: {exc}"}
    return record


def inspect_file(
    path: Path | str,
    *,
    product: str = "unknown",
    max_depth: int | None = None,
    max_datasets: int | None = None,
    sample_rows: int = 0,
) -> dict[str, Any]:
    """Inspect one HDF5 file and return a JSON-ready report object."""
    path = Path(path)
    report: dict[str, Any] = {
        "path": str(path),
        "ok": False,
        "product": product,
        "max_depth": max_depth,
        "max_datasets": max_datasets,
        "sample_rows": sample_rows,
        "groups": [],
        "datasets": [],
        "expectations": [],
        "summary": {},
        "error": None,
    }

    if not path.exists():
        report["error"] = f"path does not exist: {path}"
        return report
    if not path.is_file():
        report["error"] = f"not a regular file: {path}"
        return report

    try:
        handle = h5py.File(path, "r")
    except OSError as exc:
        report["error"] = _open_error(path, exc)
        return report

    try:
        groups: list[str] = []
        datasets: list[dict[str, Any]] = []
        state = {"dataset_limit_reached": False}
        _walk_group(
            handle,
            "/",
            0,
            max_depth=max_depth,
            max_datasets=max_datasets,
            sample_rows=sample_rows,
            groups=groups,
            datasets=datasets,
            visited=set(),
            state=state,
        )

        layout = PRODUCT_LAYOUTS.get(product, PRODUCT_LAYOUTS["unknown"])
        has_science = "/science" in groups or handle.get("science") is not None
        if not has_science and layout.get("requires_science", True):
            report["groups"] = groups
            report["datasets"] = datasets
            report["error"] = (
                f"no /science group in {path}; found root entries: "
                + (", ".join(groups[1:]) if len(groups) > 1 else "<none>")
                + " (use --product opera-disp-s1 for a root-level DISP-S1 layout if that is what this is)"
            )
            return report

        expectations = _check_expectations(handle, product) if product != "unknown" else []
        report.update(
            {
                "ok": True,
                "groups": groups,
                "datasets": datasets,
                "expectations": expectations,
                "summary": _summarise(groups, datasets, state["dataset_limit_reached"]),
            }
        )
        return report
    except OSError as exc:
        report["error"] = f"cannot traverse HDF5 metadata in {path}: {exc}"
        return report
    finally:
        handle.close()


def _check_expectations(handle: Any, product: str) -> list[dict[str, Any]]:
    """Compare the file with a provisional layout. Missing paths never fail the run."""
    layout = PRODUCT_LAYOUTS[product]
    results: list[dict[str, Any]] = []
    for group_path in layout.get("groups", []):
        try:
            obj = handle.get(group_path)
            present = isinstance(obj, h5py.Group)
        except (KeyError, OSError):
            present = False
        results.append({"kind": "group", "path": group_path, "present": present})

    for entry in layout.get("datasets", []):
        found: str | None = None
        for candidate_path in entry["paths"]:
            try:
                obj = handle.get(candidate_path)
                if isinstance(obj, h5py.Dataset):
                    found = candidate_path
                    break
            except (KeyError, OSError):
                continue
        results.append(
            {
                "kind": "dataset",
                "role": entry["role"],
                "paths": list(entry["paths"]),
                "present": found is not None,
                "found": found,
            }
        )

    return results


def _summarise(
    groups: list[str], datasets: list[dict[str, Any]], truncated: bool
) -> dict[str, Any]:
    total_bytes = sum(int(dataset["logical_bytes"]) for dataset in datasets)
    tops: dict[str, list[str]] = {}
    for category in ("phase", "coherence", "connected-components", "geolocation", "metadata"):
        paths = [dataset["path"] for dataset in datasets if dataset["candidate"] == category]
        if paths:
            tops[category] = paths
    return {
        "group_count": len(groups),
        "dataset_count": len(datasets),
        "logical_bytes": total_bytes,
        "logical_size": _format_bytes(total_bytes),
        "dataset_limit_reached": truncated,
        "top_candidates": tops,
    }


# ---------------------------------------------------------------------------------------------
# Human rendering
# ---------------------------------------------------------------------------------------------


def format_report(report: dict[str, Any]) -> str:
    lines: list[str] = [f"=== FILE {report['path']} ==="]
    if report.get("error"):
        lines.append(f"  ERROR: {report['error']}")
        return "\n".join(lines)

    summary = report["summary"]
    for group_path in report["groups"]:
        lines.append(f"  GROUP    {group_path}")

    for dataset in report["datasets"]:
        chunks = "None" if dataset["chunks"] is None else tuple(dataset["chunks"])
        compression = "None" if dataset["compression"] is None else dataset["compression"]
        lines.append(
            "  DATASET  "
            f"{dataset['path']} shape={_format_shape(dataset['shape'])} "
            f"dtype={dataset['dtype']} chunks={chunks} compression={compression}"
        )
        attrs = dataset["attributes"]
        if attrs:
            lines.append("           attrs: " + "  ".join(
                f"{key}={_human_value(value)}" for key, value in attrs.items()
            ))
        if dataset["candidate"]:
            lines.append(
                f"           CANDIDATE {dataset['candidate']} — {dataset['reason']}"
            )
        sample = dataset.get("sample")
        if sample is not None:
            if "error" in sample:
                lines.append(f"           sample error: {sample['error']}")
            else:
                preview = sample["values"]
                if isinstance(preview, list) and len(preview) > 8:
                    preview = preview[:8] + ["…"]
                lines.append(
                    f"           sample shape={_format_shape(sample['shape'])} "
                    f"values={_human_value(preview)}"
                )

    if report["product"] != "unknown":
        layout = PRODUCT_LAYOUTS[report["product"]]
        lines.append(
            f"  EXPECTED LAYOUT {report['product']} — {layout['title']} "
            "(provisional; absent is discovery, not failure)"
        )
        for item in report["expectations"]:
            if item["kind"] == "group":
                state = "PASS" if item["present"] else "absent"
                lines.append(f"    [{state}] group {item['path']}")
            else:
                state = "PASS" if item["present"] else "absent"
                found = f" -> {item['found']}" if item.get("found") else ""
                alternatives = " | ".join(item["paths"])
                lines.append(f"    [{state}] dataset {item['role']}{found} (expected one of: {alternatives})")
    else:
        lines.append("  EXPECTED LAYOUT unknown — discovery only; pass --product to check a layout.")

    tops = summary["top_candidates"]
    top_line = " | ".join(
        f"{category}={_short_list(tops.get(category, []))}"
        for category in ("phase", "coherence", "geolocation")
    )
    limited = " (dataset limit reached)" if summary["dataset_limit_reached"] else ""
    lines.append(
        "  SUMMARY "
        f"groups={summary['group_count']} datasets={summary['dataset_count']} "
        f"logical_bytes={summary['logical_bytes']} ({summary['logical_size']}, logical: "
        f"sum of shape×itemsize; not file size){limited}"
    )
    lines.append(f"  TOP CANDIDATES {top_line}")
    return "\n".join(lines)


def _short_list(paths: list[str], limit: int = 3) -> str:
    if not paths:
        return "<none>"
    shown = ", ".join(paths[:limit])
    if len(paths) > limit:
        shown += f" (+{len(paths) - limit} more)"
    return shown


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _directory_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in HDF5_SUFFIXES
    )


def _combined_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    ok_reports = [report for report in reports if report.get("ok")]
    groups = sum(report["summary"]["group_count"] for report in ok_reports)
    datasets = sum(report["summary"]["dataset_count"] for report in ok_reports)
    total_bytes = sum(report["summary"]["logical_bytes"] for report in ok_reports)
    tops: dict[str, list[str]] = {}
    for report in ok_reports:
        for category, paths in report["summary"]["top_candidates"].items():
            tops.setdefault(category, []).extend(paths)
    return {
        "file_count": len(reports),
        "ok_count": len(ok_reports),
        "error_count": len(reports) - len(ok_reports),
        "group_count": groups,
        "dataset_count": datasets,
        "logical_bytes": total_bytes,
        "logical_size": _format_bytes(total_bytes),
        "top_candidates": tops,
    }


def _format_combined(summary: dict[str, Any]) -> str:
    tops = summary["top_candidates"]
    return "\n".join(
        [
            "=== COMBINED SUMMARY ===",
            (
                f"  files={summary['file_count']} ok={summary['ok_count']} "
                f"errors={summary['error_count']} groups={summary['group_count']} "
                f"datasets={summary['dataset_count']} logical_bytes={summary['logical_bytes']} "
                f"({summary['logical_size']}, logical: sum of shape×itemsize; not file size)"
            ),
            "  TOP CANDIDATES "
            + " | ".join(
                f"{category}={_short_list(tops.get(category, []))}"
                for category in ("phase", "coherence", "geolocation")
            ),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect an HDF5 SAR granule lazily: tree, shapes, dtypes, units, fill values, "
            "and plausible phase/coherence/connected-component/geolocation datasets."
        )
    )
    parser.add_argument("path", help="an .h5/.hdf5/.hdf file, or a directory to scan recursively")
    parser.add_argument("--json", action="store_true", help="emit structured JSON instead of text")
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        metavar="N",
        help="maximum HDF5 path depth (root=0); omit for unlimited",
    )
    parser.add_argument(
        "--product",
        choices=sorted(PRODUCT_LAYOUTS),
        default="unknown",
        help="expected layout to compare against; absence never fails the run",
    )
    parser.add_argument(
        "--max-datasets",
        type=int,
        default=None,
        metavar="N",
        help="stop collecting datasets after N (groups already visited are still shown)",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=0,
        metavar="N",
        help="read at most N rows per dataset (default 0: read no dataset contents)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.max_depth is not None and args.max_depth < 0:
        parser.error("--max-depth must be >= 0")
    if args.max_datasets is not None and args.max_datasets < 0:
        parser.error("--max-datasets must be >= 0")
    if args.sample_rows < 0:
        parser.error("--sample-rows must be >= 0")

    target = Path(args.path)
    if not target.exists():
        message = f"error: path does not exist: {target}"
        if args.json:
            print(json.dumps({"path": str(target), "ok": False, "error": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2

    if target.is_dir():
        files = _directory_files(target)
        if not files:
            message = (
                f"error: no HDF5 files ({', '.join(sorted(HDF5_SUFFIXES))}) found under: {target}"
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "path": str(target),
                            "kind": "directory",
                            "files": [],
                            "error": message,
                        },
                        indent=2,
                    )
                )
            else:
                print(message, file=sys.stderr)
            return 2

        reports = [
            inspect_file(
                path,
                product=args.product,
                max_depth=args.max_depth,
                max_datasets=args.max_datasets,
                sample_rows=args.sample_rows,
            )
            for path in files
        ]
        combined = _combined_summary(reports)
        if args.json:
            print(
                json.dumps(
                    {
                        "path": str(target),
                        "kind": "directory",
                        "files": reports,
                        "combined": combined,
                    },
                    indent=2,
                )
            )
        else:
            for report in reports:
                print(format_report(report))
                print()
            print(_format_combined(combined))
        return 0 if combined["error_count"] == 0 else 1

    report = inspect_file(
        target,
        product=args.product,
        max_depth=args.max_depth,
        max_datasets=args.max_datasets,
        sample_rows=args.sample_rows,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
