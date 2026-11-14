#!/usr/bin/env python
"""Pitch figures for "Three Cities, One Pump" — reproducible, labelled, no decoration.

Run through the repo wrapper (it fixes LD_LIBRARY_PATH for the Nix interpreter):

    ./scripts/python.sh scripts/make_figures.py
    ./scripts/python.sh scripts/make_figures.py --out /tmp/figures
    ./scripts/python.sh scripts/make_figures.py --processed data/processed

What it draws
-------------
1. ``velocity_shared_scale.png`` — one panel per case, ONE shared linear colour scale in mm/yr,
   symmetric about 0. This is the argument of README §1: on a common legend Málaga is blank next
   to Mexico City, and the blankness is the result, not a gap in the data.
2. ``timeseries_three_cities.png`` — the ``cell_id == "aoi"`` series of all cases on one axis,
   referenced to the first acquisition (which is why the first value is 0, not a level), plus the
   detection floor of the declared null result.
3. ``coherence.png`` — one panel per case on a shared 0–1 scale, with the nodata rule stated:
   cells below the frozen ``coherence_min`` are *absent*, never drawn as zero.

Honesty rules enforced here
---------------------------
* Only ``data/processed/`` is read. No network, no basemap, no synthetic smoothing, no resampling.
* Values are plotted exactly as stored; the only transformation is the local metric projection
  ``x = (lon - lon0) * cos(lat_mean)``, ``y = lat - lat0`` (see :func:`local_metres`), needed so
  equal-aspect panels are not distorted by the lon/lat scale difference.
* Units, thresholds, ``revisit_days`` and enum vocabularies come from ``src/domain/contracts.py``
  and ``meta.json``. Nothing scientific is re-declared here.
* Every figure names its artefact files and their ``provenance_id``; mock data says so.
* Deterministic: fixed layout, no random sampling, row order preserved. Re-running produces
  equal-size PNGs.
* A missing case directory or an incomplete artefact set skips that panel with a printed note
  instead of failing the run. If no case survives, the script exits non-zero (nothing produced is
  a failure, not a success).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------------------------
# Matplotlib must be importable without a writable ~/.config: set the cache dir before pyplot.
# (The dev box mounts $HOME read-only, which makes matplotlib warn on every import.)
# ---------------------------------------------------------------------------------------------
if not os.environ.get("MPLCONFIGDIR"):
    _cache = Path(tempfile.gettempdir()) / f"make-figures-mpl-{os.getuid()}"
    try:
        _cache.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(_cache)
    except OSError:  # pragma: no cover - a read-only /tmp is not worth failing the run over
        pass

import matplotlib

matplotlib.use("Agg")

import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain import contracts as C  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Fixed presentation constants. These are *styling*, not science: thresholds and units are read
# from the contract and from meta.json.
# ---------------------------------------------------------------------------------------------

DPI = 150
FIG_W_IN, FIG_H_IN = 16.0, 9.0  # 16:9 slide, 2400 x 1350 px at 150 dpi
PX_W, PX_H = int(FIG_W_IN * DPI), int(FIG_H_IN * DPI)

GRID_FILE = "nisar_grid.parquet"
SERIES_FILE = "timeseries.csv"
META_FILE = "meta.json"

#: H3 resolution 9 mean centre spacing (~0.35 km). Used only to size markers so one marker covers
#: roughly one cell footprint; it does not affect the data or the colour mapping.
H3_RES9_SPACING_KM = 0.35

#: Margin (km) added around each city so edge cells are not clipped by the axes frame.
MAP_PAD_KM = 0.7

#: Map panel geometry in figure fractions. Panels share width and top edge; height follows the
#: data aspect so metres are equal in x and y (an undistorted city shape, no stretched axes).
MAP_LEFT, MAP_RIGHT, MAP_TOP, MAP_GAP = 0.045, 0.862, 0.815, 0.024
COLOURBAR_RECT = (0.872, 0.400, 0.011, 0.380)

CASE_COLOURS = {
    "mexico-city": "#d73027",
    "jakarta": "#4575b4",
    "malaga": "#1a9850",
}
FALLBACK_COLOUR = "#666666"

VELOCITY_CMAP = "RdBu"  # diverging: red = subsidence (negative), white = 0, blue = uplift
COHERENCE_CMAP = "viridis"  # sequential, perceptually ordered; low coherence is dark, not "empty"

FOOTER_FONTSIZE = 6.4
FOOTER_TOP = 0.160
FOOTER_STEP = 0.028
FOOTER_MAX_LINES = 6
AXES_BOTTOM = 0.245


# ---------------------------------------------------------------------------------------------
# Loading — read-only, tolerant of a missing case, loud about anything skipped.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CasePanel:
    """One case, loaded from the frozen artefacts and ready to draw."""

    case_id: str
    name: str
    grid: pd.DataFrame  # finite lat/lon/velocity/coherence rows only, plus x_km/y_km
    series: pd.DataFrame  # cell_id == "aoi", sorted by date
    meta: dict
    files: tuple[Path, Path, Path]
    provenance_id: str
    source: str
    product: str
    sensor: str
    revisit_days: int
    coherence_min: float
    n_rows_in_file: int
    n_below_coherence: int

    @property
    def n_cells(self) -> int:
        return int(len(self.grid))

    @property
    def median_velocity(self) -> float:
        return float(self.grid["velocity_mm_yr"].median())

    def ranged_km(self) -> tuple[float, float, float, float]:
        """Padded local extents (x0, x1, y0, y1) in km."""
        return (
            float(self.grid["x_km"].min()) - MAP_PAD_KM,
            float(self.grid["x_km"].max()) + MAP_PAD_KM,
            float(self.grid["y_km"].min()) - MAP_PAD_KM,
            float(self.grid["y_km"].max()) + MAP_PAD_KM,
        )

    @property
    def extent_km(self) -> tuple[float, float]:
        x0, x1, y0, y1 = self.ranged_km()
        return (x1 - x0, y1 - y0)

    @property
    def colour(self) -> str:
        return CASE_COLOURS.get(self.case_id, FALLBACK_COLOUR)

    @property
    def detection_limit_mm_yr(self) -> float | None:
        value = (self.meta.get("stats") or {}).get("detection_limit_mm_yr")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def is_null_result(self) -> bool:
        """The contract's null-result rule: |median| below NULL_RESULT_MM_YR must publish a bound."""
        return abs(self.median_velocity) < C.NULL_RESULT_MM_YR

    def provenance_ok(self) -> bool:
        return bool(C.PROVENANCE_ID_RE.match(self.provenance_id))


def _note(message: str) -> None:
    print(f"note: {message}")


def local_metres(lon: pd.Series, lat: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Local equirectangular metres about the data centroid.

    ``x = (lon - lon0) * cos(lat_mean)``, ``y = lat - lat0``, scaled by 111.32 / 110.57 km per
    degree. The cosine term is the point: without it a 1° longitude step at 36°N or 19°N would be
    drawn as long as a 1° latitude step and the city shape would be wrong. Equal aspect then means
    equal metres.
    """
    lon0 = float(lon.mean())
    lat0 = float(lat.mean())
    cos_lat = math.cos(math.radians(lat0))
    x_km = (lon - lon0) * cos_lat * 111.32
    y_km = (lat - lat0) * 110.57
    return x_km, y_km


def load_case(processed: Path, case_id: str) -> CasePanel | None:
    """Load one case, or return None with a printed reason. Never raises for absent files."""
    case_dir = processed / case_id
    grid_path = case_dir / GRID_FILE
    series_path = case_dir / SERIES_FILE
    meta_path = case_dir / META_FILE

    if not case_dir.is_dir():
        _note(f"case '{case_id}' skipped — directory not found: {case_dir}")
        return None
    missing = [p.name for p in (grid_path, series_path, meta_path) if not p.is_file()]
    if missing:
        _note(f"case '{case_id}' skipped — missing artefact(s) in {case_dir}: {', '.join(missing)}")
        return None

    try:
        with meta_path.open(encoding="utf-8") as handle:
            meta = json.load(handle)
        grid = pd.read_parquet(grid_path)
        series = pd.read_csv(series_path)
    except Exception as exc:  # noqa: BLE001 - a corrupt case must not kill the other two
        _note(f"case '{case_id}' skipped — unreadable artefact: {exc}")
        return None

    declared_case = str(meta.get("case_id") or case_id)
    if declared_case != case_id:
        _note(
            f"case '{case_id}' skipped — meta.case_id is '{declared_case}', "
            f"directory says '{case_id}'"
        )
        return None

    required_grid = {"cell_id", "lat", "lon", "velocity_mm_yr", "coherence", "provenance_id"}
    required_series = {"case_id", "cell_id", "date", "value", "unit"}
    lacking = sorted(required_grid - set(grid.columns))
    if lacking:
        _note(f"case '{case_id}' skipped — grid lacks column(s): {', '.join(lacking)}")
        return None
    lacking = sorted(required_series - set(series.columns))
    if lacking:
        _note(f"case '{case_id}' skipped — series lacks column(s): {', '.join(lacking)}")
        return None

    # Units are part of the contract; a figure that silently rescales would be a lie.
    velocity_units: set[str] = set()
    if "velocity_unit" in grid.columns:
        velocity_units |= set(map(str, grid["velocity_unit"].dropna().unique()))
    if velocity_units - {C.VELOCITY_UNIT}:
        _note(
            f"case '{case_id}' skipped — velocity unit {sorted(velocity_units)} is not "
            f"{C.VELOCITY_UNIT!r}; refusing to mislabel the axis"
        )
        return None
    series_units = set(map(str, series["unit"].dropna().unique()))
    if series_units - {C.UNIT}:
        _note(
            f"case '{case_id}' skipped — timeseries unit {sorted(series_units)} is not "
            f"{C.UNIT!r}; refusing to mislabel the axis"
        )
        return None

    provenance_id = str((meta.get("provenance") or {}).get("provenance_id") or "")
    if not provenance_id:
        ids = grid["provenance_id"].dropna().astype(str).unique()
        if len(ids) != 1:
            _note(
                f"case '{case_id}' skipped — no meta.provenance.provenance_id and the grid has "
                f"{len(ids)} distinct provenance ids; the footer could not name one"
            )
            return None
        provenance_id = str(ids[0])

    product = str(meta.get("product") or "")
    sensor = str(meta.get("sensor") or "")
    try:
        profile = C.sensor_profile(sensor, product)
    except C.ContractError as exc:
        _note(f"case '{case_id}' skipped — {exc}")
        return None

    revisit_days = meta.get("revisit_days", profile.revisit_days)
    if not isinstance(revisit_days, int):
        _note(f"case '{case_id}' skipped — meta.revisit_days is not an integer")
        return None
    if revisit_days != profile.revisit_days:
        _note(
            f"case '{case_id}': meta.revisit_days={revisit_days} disagrees with the frozen "
            f"profile ({profile.revisit_days}); printed as stored"
        )

    meta_coherence = (meta.get("thresholds") or {}).get("coherence_min")
    if isinstance(meta_coherence, (int, float)) and float(meta_coherence) != profile.coherence_min:
        _note(
            f"case '{case_id}': meta.thresholds.coherence_min={meta_coherence} disagrees with the "
            f"frozen profile ({profile.coherence_min}); the profile wins (never pool products)"
        )
    coherence_min = profile.coherence_min

    n_rows_in_file = int(len(grid))
    numeric = grid.copy()
    for column in ("lat", "lon", "velocity_mm_yr", "coherence"):
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    keep = numeric[["lat", "lon", "velocity_mm_yr", "coherence"]].notna().all(axis=1)
    keep &= np.isfinite(numeric[["lat", "lon", "velocity_mm_yr", "coherence"]]).all(axis=1)
    dropped = int((~keep).sum())
    if dropped:
        _note(
            f"case '{case_id}': {dropped} of {n_rows_in_file} rows have non-finite lat/lon/"
            "velocity/coherence and are not drawn (absent, not zero)"
        )
    numeric = numeric.loc[keep].copy()
    if numeric.empty:
        _note(f"case '{case_id}' skipped — no cell has a finite velocity and coherence")
        return None

    # Deterministic order: sort by H3 id, so reruns are stable regardless of parquet row order.
    numeric = numeric.sort_values("cell_id", kind="stable").reset_index(drop=True)
    numeric["x_km"], numeric["y_km"] = local_metres(numeric["lon"], numeric["lat"])

    series = series.loc[series["cell_id"].astype(str) == "aoi"].copy()
    if series.empty:
        _note(f"case '{case_id}': no cell_id == 'aoi' rows — omitted from the timeseries figure")
    else:
        series["date"] = pd.to_datetime(series["date"], errors="coerce")
        series = series.loc[series["date"].notna()].sort_values("date", kind="stable")
        series = series.reset_index(drop=True)

    n_below = int((numeric["coherence"] < coherence_min).sum())

    return CasePanel(
        case_id=case_id,
        name=str(meta.get("name") or case_id),
        grid=numeric,
        series=series,
        meta=meta,
        files=(grid_path, series_path, meta_path),
        provenance_id=provenance_id,
        source=str((meta.get("provenance") or {}).get("source") or "undeclared"),
        product=product,
        sensor=sensor,
        revisit_days=int(revisit_days),
        coherence_min=float(coherence_min),
        n_rows_in_file=n_rows_in_file,
        n_below_coherence=n_below,
    )


def load_cases(processed: Path) -> list[CasePanel]:
    """Load cases in the frozen contract order, skipping (with a note) whatever is absent."""
    if not processed.is_dir():
        print(f"error: processed directory not found: {processed}", file=sys.stderr)
        return []
    panels: list[CasePanel] = []
    for case_id in C.CASE_IDS:
        panel = load_case(processed, case_id)
        if panel is not None:
            panels.append(panel)
    return panels


# ---------------------------------------------------------------------------------------------
# Shared drawing helpers
# ---------------------------------------------------------------------------------------------


def nice_symmetric_limit(max_abs: float) -> float:
    """Round a magnitude up to a readable symmetric colour-scale bound (multiple of 25 mm/yr)."""
    if not math.isfinite(max_abs) or max_abs <= 0:
        return 25.0
    return float(math.ceil(max_abs / 25.0) * 25.0)


def marker_area(km_per_inch: float) -> float:
    """Marker area (pt²) covering roughly one H3 res-9 cell at a given map scale.

    Sizing by scale rather than a constant keeps a cell the same physical size in every panel,
    even though the panels have different extents.
    """
    diameter_pt = (H3_RES9_SPACING_KM / max(km_per_inch, 1e-6)) * 72.0
    diameter_pt = float(np.clip(diameter_pt, 1.2, 12.0))
    return math.pi * (diameter_pt / 2.0) ** 2


def map_axes(
    fig: plt.Figure, panels: Sequence[CasePanel], top: float = MAP_TOP
) -> list[plt.Axes]:
    """Lay out one undistorted, equal-aspect axes per case, sharing width and top edge."""
    n = len(panels)
    width = (MAP_RIGHT - MAP_LEFT - MAP_GAP * (n - 1)) / n
    axes: list[plt.Axes] = []
    for index, panel in enumerate(panels):
        x0, x1, y0, y1 = panel.ranged_km()
        height = width * ((y1 - y0) / (x1 - x0)) * (FIG_W_IN / FIG_H_IN)
        ax = fig.add_axes(
            [MAP_LEFT + index * (width + MAP_GAP), top - height, width, height]
        )
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal", adjustable="box")
        for spine in ax.spines.values():
            spine.set_linewidth(0.6)
            spine.set_color("0.55")
        ax.tick_params(labelsize=7.5, length=2.5, width=0.6)
        ax.set_xlabel("local easting [km]  (x = lon·cos lat_mean)", fontsize=8.5)
        if index == 0:
            ax.set_ylabel("local northing [km]  (y = lat)", fontsize=8.5)
        axes.append(ax)
    return axes


def panel_header(ax: plt.Axes, panel: CasePanel, subtitle: str) -> None:
    """Case name as the title, the numbers as a smaller subtitle, the slug in the corner.

    Two text objects instead of one two-line title, so a long subtitle can never run into the
    neighbouring panel's title on a 16:9 slide.
    """
    ax.set_title(panel.name, fontsize=11.5, loc="left", pad=20)
    ax.text(
        0.0,
        1.012,
        subtitle,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=8.3,
        color="0.30",
    )
    ax.text(
        1.0,
        1.012,
        panel.case_id,
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=7.4,
        color="0.45",
    )


def panel_area(panel: CasePanel, panel_width_frac: float) -> float:
    """Marker area for a panel, from its km/inch scale."""
    x0, x1, _y0, _y1 = panel.ranged_km()
    return marker_area((x1 - x0) / (panel_width_frac * FIG_W_IN))


def _common_or_list(panels: Sequence[CasePanel], attr: str) -> str:
    values = [getattr(p, attr) for p in panels]
    if len(set(values)) == 1:
        return f"{attr}={values[0]}"
    return ", ".join(f"{p.case_id}: {getattr(p, attr)}" for p in panels)


def artefact_root(panels: Sequence[CasePanel]) -> str:
    """The directory the figures actually came from, so ``--processed`` cannot mislead a reader."""
    parents = {panel.files[0].parent for panel in panels}
    if len(parents) != 1:
        return "data/processed/<case>"
    root = parents.pop()
    try:
        return str(root.relative_to(ROOT))
    except ValueError:  # outside the repo: print the absolute path, not a guess
        return str(root)


def artefact_footer(panels: Sequence[CasePanel], extra: Iterable[str]) -> list[str]:
    """The mandatory traceability block: files, provenance ids, product/revisit, then extras."""
    cases = ", ".join(p.case_id for p in panels)
    root = artefact_root(panels)
    lines = [
        f"Artefacts read: {root}/<case>/{{{GRID_FILE}, {SERIES_FILE}, {META_FILE}}} "
        f"— drawn from: {cases}.",
        "provenance_id: " + " | ".join(f"{p.case_id} = {p.provenance_id}" for p in panels),
        "meta.provenance.source: "
        + "; ".join(sorted({p.source for p in panels}))
        + " — "
        + _common_or_list(panels, "product")
        + ", "
        + _common_or_list(panels, "sensor")
        + ", "
        + _common_or_list(panels, "revisit_days")
        + ".",
    ]
    lines.extend(extra)
    return lines


def add_footer(fig: plt.Figure, lines: Sequence[str]) -> None:
    """Bottom-of-figure provenance block, one line per entry. Fixed positions, never clipped."""
    if len(lines) > FOOTER_MAX_LINES:
        raise ValueError(f"footer takes at most {FOOTER_MAX_LINES} lines, got {len(lines)}")
    for index, line in enumerate(lines):
        fig.text(
            0.012,
            FOOTER_TOP - index * FOOTER_STEP,
            line,
            ha="left",
            va="bottom",
            fontsize=FOOTER_FONTSIZE,
            color="0.22",
        )


def save_figure(fig: plt.Figure, path: Path) -> None:
    """Save at a guaranteed pixel size (no bbox_inches='tight', which would resize the PNG)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, facecolor="white", edgecolor="white", format="png")
    plt.close(fig)


def png_dimensions(path: Path) -> tuple[int, int]:
    """Read width/height from the PNG IHDR chunk — no PIL dependency."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG")
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def _colourbar(fig: plt.Figure, norm, cmap, label: str, note: str) -> None:
    cax = fig.add_axes(list(COLOURBAR_RECT))
    scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    scalar.set_array([])
    colourbar = fig.colorbar(scalar, cax=cax)
    colourbar.set_label(label, fontsize=8.5)
    colourbar.ax.tick_params(labelsize=7.5, length=2.5, width=0.6)
    colourbar.outline.set_linewidth(0.6)
    cax.text(
        0.0,
        -0.045,
        note,
        transform=cax.transAxes,
        ha="left",
        va="top",
        fontsize=7.0,
        color="0.25",
    )


# ---------------------------------------------------------------------------------------------
# Figure 1 — one shared velocity scale
# ---------------------------------------------------------------------------------------------


def build_velocity_figure(panels: Sequence[CasePanel], path: Path) -> dict:
    observed = max(float(np.abs(p.grid["velocity_mm_yr"]).max()) for p in panels)
    vmax = nice_symmetric_limit(observed)
    norm = mcolors.Normalize(vmin=-vmax, vmax=vmax)
    cmap = plt.get_cmap(VELOCITY_CMAP)

    fig = plt.figure(figsize=(FIG_W_IN, FIG_H_IN), facecolor="white")
    axes = map_axes(fig, panels)
    width_frac = (MAP_RIGHT - MAP_LEFT - MAP_GAP * (len(panels) - 1)) / len(panels)

    drawn = []
    for ax, panel in zip(axes, panels):
        ax.scatter(
            panel.grid["x_km"].to_numpy(),
            panel.grid["y_km"].to_numpy(),
            c=panel.grid["velocity_mm_yr"].to_numpy(),
            cmap=cmap,
            norm=norm,
            s=panel_area(panel, width_frac),
            marker="o",
            linewidths=0,
            edgecolors="none",
        )
        dx, dy = panel.extent_km
        panel_header(
            ax,
            panel,
            f"n = {panel.n_cells:,} cells · median {panel.median_velocity:+.1f} mm/yr",
        )
        ax.text(
            0.985,
            0.015,
            f"{dx:.0f} × {dy:.0f} km",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=7.2,
            color="0.35",
        )
        drawn.append({"case_id": panel.case_id, "cells": panel.n_cells})

        if panel.is_null_result:
            bound = panel.detection_limit_mm_yr
            bound_line = (
                f"detection limit {bound:.1f} mm/yr — the bound\nis the result, not a failure"
                if bound is not None
                else "bound undeclared — the contract requires one"
            )
            position = ax.get_position()
            fig.text(
                position.x0,
                position.y0 - 0.085,
                "DECLARED NULL RESULT\n"
                f"median {panel.median_velocity:+.1f} mm/yr  (|median| < {C.NULL_RESULT_MM_YR:.0f} mm/yr)\n"
                f"{bound_line}",
                ha="left",
                va="top",
                fontsize=8.0,
                color=panel.colour,
                linespacing=1.45,
                bbox={
                    "boxstyle": "round,pad=0.35",
                    "facecolor": "white",
                    "edgecolor": panel.colour,
                    "alpha": 0.95,
                    "linewidth": 0.8,
                },
            )

    _colourbar(
        fig,
        norm,
        cmap,
        "vertical velocity [mm/yr]",
        "ONE shared linear scale\n"
        f"−{vmax:.0f} … +{vmax:.0f} mm/yr\n"
        "symmetric about 0\n"
        "no clipping",
    )

    fig.text(
        0.012,
        0.935,
        "Velocity on one shared scale: Málaga is blank next to Mexico City — that blankness is the "
        "result, not missing data.",
        ha="left",
        va="center",
        fontsize=11.5,
        color="0.10",
    )
    fig.text(
        0.012,
        0.898,
        "Colour = stored velocity_mm_yr per H3 res-9 cell, plotted at its centroid in a local metric "
        "frame (equal aspect). No basemap, no interpolation, no smoothing. White = absent cell.",
        ha="left",
        va="center",
        fontsize=7.8,
        color="0.38",
    )

    add_footer(
        fig,
        artefact_footer(
            panels,
            [
                f"Shared scale: linear, symmetric, ±{vmax:.0f} mm/yr for every panel; max "
                f"|velocity_mm_yr| drawn = {observed:.1f} mm/yr, so no cell saturates. "
                f"Marker area ≈ one H3 res-9 footprint (~{H3_RES9_SPACING_KM:.2f} km spacing).",
                "Absent cells (outside coverage, or removed as water by the quality filter) are "
                f"absent here — nodata is never drawn as 0. Units: {C.UNIT} / {C.VELOCITY_UNIT} "
                f"from the artefacts. Generated by scripts/make_figures.py · {DPI} dpi · "
                f"{PX_W}×{PX_H} px",
            ],
        ),
    )
    save_figure(fig, path)
    width_px, height_px = png_dimensions(path)
    return {
        "path": path,
        "width": width_px,
        "height": height_px,
        "cells": int(sum(entry["cells"] for entry in drawn)),
        "detail": ", ".join(f"{entry['case_id']}={entry['cells']:,}" for entry in drawn),
        "vmax": vmax,
    }


# ---------------------------------------------------------------------------------------------
# Figure 2 — the three AOI series, referenced to the first acquisition
# ---------------------------------------------------------------------------------------------


def build_timeseries_figure(panels: Sequence[CasePanel], path: Path) -> dict:
    with_series = [p for p in panels if not p.series.empty]
    if not with_series:
        raise RuntimeError("no case has cell_id == 'aoi' rows")

    all_values = pd.concat([p.series["value"] for p in with_series], ignore_index=True)
    vmin, vmax_data = float(all_values.min()), float(all_values.max())
    span = max(vmax_data - vmin, 1.0)
    y_top = vmax_data + span * 0.27
    y_bottom = vmin - span * 0.10

    fig = plt.figure(figsize=(FIG_W_IN, FIG_H_IN), facecolor="white")
    ax = fig.add_axes([0.078, AXES_BOTTOM, 0.892, 0.845 - AXES_BOTTOM])

    # The floor is a *rate* (mm/yr). On a displacement axis it becomes a displacement over the
    # window: mm/yr × years. Shading the rate directly would mislabel the y axis.
    band_mm = 0.0
    band_case: CasePanel | None = None
    window_days = 0

    for panel in with_series:
        limit = panel.detection_limit_mm_yr
        if band_case is None and panel.is_null_result and limit is not None:
            band_case = panel
            window_days = int(
                (panel.series["date"].iloc[-1] - panel.series["date"].iloc[0]).days
            )
            band_mm = limit * window_days / 365.25
            ax.axhspan(-band_mm, band_mm, facecolor=panel.colour, alpha=0.20, edgecolor="none", zorder=0)
            ax.axhline(band_mm, color=panel.colour, linewidth=0.7, zorder=1)

    ax.axhline(0.0, color="0.35", linewidth=0.8, zorder=1)

    for panel in panels:
        if panel.series.empty:
            _note(f"case '{panel.case_id}' has no 'aoi' series — omitted from the timeseries figure")
            continue
        ax.plot(
            panel.series["date"],
            panel.series["value"],
            color=panel.colour,
            linewidth=1.8,
            marker="o",
            markersize=3.6,
            markeredgewidth=0,
            label=(
                f"{panel.name} — median cell velocity {panel.median_velocity:+.1f} mm/yr "
                f"(n = {panel.n_cells:,} cells)"
            ),
            zorder=4,
        )
        last_date = panel.series["date"].iloc[-1]
        last_value = float(panel.series["value"].iloc[-1])
        ax.annotate(
            f"{last_value:+.1f} mm",
            xy=(last_date, last_value),
            xytext=(6, 0),
            textcoords="offset points",
            fontsize=7.4,
            color=panel.colour,
            va="center",
            ha="left",
            zorder=6,
        )

    if band_case is not None:
        last_date = band_case.series["date"].iloc[-1]
        ax.annotate(
            f"below detection floor: ±{band_case.detection_limit_mm_yr:.1f} mm/yr → "
            f"±{band_mm:.2f} mm over {window_days} d",
            xy=(last_date - pd.Timedelta(days=4), band_mm),
            xytext=(last_date, band_mm + span * 0.075),
            textcoords="data",
            ha="right",
            va="bottom",
            fontsize=7.6,
            color=band_case.colour,
            arrowprops={"arrowstyle": "-", "color": band_case.colour, "linewidth": 0.7, "shrinkA": 1, "shrinkB": 1},
            zorder=7,
        )

    first_date = min(p.series["date"].iloc[0] for p in with_series)
    last_date_all = max(p.series["date"].iloc[-1] for p in with_series)
    ax.annotate(
        f"Every series is referenced to the first acquisition: {first_date:%Y-%m-%d} ≡ 0 mm\n"
        "(cumulative change since that date — the product has no absolute displacement datum)",
        xy=(first_date, 0.0),
        xytext=(first_date + pd.Timedelta(days=10), vmax_data + span * 0.21),
        textcoords="data",
        fontsize=8.2,
        color="0.20",
        ha="left",
        va="center",
        arrowprops={"arrowstyle": "-|>", "color": "0.45", "linewidth": 0.8, "shrinkA": 2, "shrinkB": 3},
        zorder=8,
    )

    ax.set_xlim(first_date - pd.Timedelta(days=5), last_date_all + pd.Timedelta(days=20))
    ax.set_ylim(y_bottom, y_top)
    ax.set_xlabel("date (acquisition day)", fontsize=9)
    ax.set_ylabel(
        f"cumulative displacement since first acquisition [{C.UNIT}]\n"
        "(up positive; negative = subsidence)",
        fontsize=9,
    )
    ax.grid(True, axis="y", color="0.88", linewidth=0.6, zorder=0)
    ax.tick_params(labelsize=8)
    same_year = {d.year for d in pd.concat([p.series["date"] for p in with_series])} == {
        first_date.year
    }
    if same_year:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.set_xlabel(f"date — acquisition day in {first_date.year}", fontsize=9)
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        for label in ax.get_xticklabels():
            label.set_rotation(30)
            label.set_ha("right")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.get_offset_text().set_visible(False)
    for spine in ax.spines.values():
        spine.set_linewidth(0.6)
        spine.set_color("0.55")
    ax.legend(loc="lower left", fontsize=8.2, framealpha=0.95, borderpad=0.5)

    fig.text(
        0.012,
        0.935,
        "Three cities, one clock: the AOI aggregate since the first acquisition",
        ha="left",
        va="center",
        fontsize=12.5,
        color="0.10",
    )
    fig.text(
        0.012,
        0.898,
        "Each line is the artefact's cell_id == 'aoi' series, plotted as stored (no smoothing, no "
        "resampling). The legend quotes the median grid velocity, which is not the mean of this line.",
        ha="left",
        va="center",
        fontsize=7.8,
        color="0.38",
    )

    endpoints = ", ".join(
        f"{p.case_id} {float(p.series['value'].iloc[-1]):+.1f} mm" for p in with_series
    )
    extra = [
        f"AOI series at last acquisition ({last_date_all:%Y-%m-%d}): {endpoints}. "
        "Zero line = first acquisition; above 0 means net uplift since then."
    ]
    if band_case is not None:
        observed = band_case.series["value"]
        extra.append(
            f"Detection floor: {band_case.case_id} stats.detection_limit_mm_yr = "
            f"{band_case.detection_limit_mm_yr:.1f} mm/yr; band = ±"
            f"{band_case.detection_limit_mm_yr:.1f} × {window_days}/365.25 = ±{band_mm:.2f} mm "
            f"(rate converted to a window bound). The seasonal bulge (range {observed.min():+.2f}…"
            f"{observed.max():+.2f} mm) briefly leaves the band: it is not secular subsidence."
        )
    else:
        extra.append("No case declares a null result, so no detection-floor band is drawn.")
    extra.append(
        f"Units from the artefacts: series value in {C.UNIT}; grid velocity in {C.VELOCITY_UNIT}. "
        f"Generated by scripts/make_figures.py · {DPI} dpi · {PX_W}×{PX_H} px"
    )

    add_footer(fig, artefact_footer(panels, extra))
    save_figure(fig, path)
    width_px, height_px = png_dimensions(path)
    return {
        "path": path,
        "width": width_px,
        "height": height_px,
        "cells": int(sum(len(p.series) for p in with_series)),
        "detail": ", ".join(f"{p.case_id}={len(p.series)} rows" for p in with_series),
    }


# ---------------------------------------------------------------------------------------------
# Figure 3 — coherence, with the nodata rule on the page
# ---------------------------------------------------------------------------------------------


def build_coherence_figure(panels: Sequence[CasePanel], path: Path) -> dict:
    norm = mcolors.Normalize(vmin=0.0, vmax=1.0)
    cmap = plt.get_cmap(COHERENCE_CMAP)

    fig = plt.figure(figsize=(FIG_W_IN, FIG_H_IN), facecolor="white")
    axes = map_axes(fig, panels)
    width_frac = (MAP_RIGHT - MAP_LEFT - MAP_GAP * (len(panels) - 1)) / len(panels)

    for ax, panel in zip(axes, panels):
        ax.scatter(
            panel.grid["x_km"].to_numpy(),
            panel.grid["y_km"].to_numpy(),
            c=panel.grid["coherence"].to_numpy(),
            cmap=cmap,
            norm=norm,
            s=panel_area(panel, width_frac),
            marker="o",
            linewidths=0,
            edgecolors="none",
        )
        median = float(panel.grid["coherence"].median())
        minimum = float(panel.grid["coherence"].min())
        panel_header(
            ax,
            panel,
            f"n = {panel.n_cells:,} cells · median γ {median:.2f} · min {minimum:.2f}",
        )

    _colourbar(
        fig,
        norm,
        cmap,
        "temporal coherence γ [0–1]",
        "ONE shared scale 0 … 1\nacross all panels\nno gamma stretch",
    )

    fig.text(
        0.012,
        0.935,
        "Quality behind the velocity: coherence is per cell, and low-coherence cells are not in the "
        "artefact",
        ha="left",
        va="center",
        fontsize=11.5,
        color="0.10",
    )
    fig.text(
        0.012,
        0.898,
        "White background is absent data, not low coherence. The grid only contains cells that "
        "passed the quality filter; nothing is filled, interpolated or clipped.",
        ha="left",
        va="center",
        fontsize=7.8,
        color="0.38",
    )

    thresholds = sorted({p.coherence_min for p in panels})
    total = sum(p.n_cells for p in panels)
    below = sum(p.n_below_coherence for p in panels)
    mins = ", ".join(f"{p.case_id} {float(p.grid['coherence'].min()):.3f}" for p in panels)
    thresh_txt = f"{thresholds[0]:.2f}" if len(thresholds) == 1 else "/".join(f"{t:.2f}" for t in thresholds)
    note = (
        f"Cells with coherence below coherence_min = {thresh_txt} (frozen per sensor+product: "
        f"{panels[0].sensor}/{panels[0].product}, revisit {panels[0].revisit_days} d) are ABSENT "
        "from nisar_grid.parquet — nodata is never drawn as zero, and nothing is filled, "
        "interpolated or smoothed.\n"
        f"Present with γ < {thresh_txt}: {below} of {total:,} cells drawn "
        f"(min γ observed: {mins}). The blank margin outside each city is the quality filter, "
        "not a rendering choice."
    )
    if below:
        _note(
            f"{below} cell(s) are present with coherence below the frozen floor; they are drawn, "
            "because the artefact says they exist — the figure does not silently drop rows"
        )
    fig.text(
        0.012,
        0.315,
        note,
        ha="left",
        va="top",
        fontsize=8.2,
        color="0.12",
        linespacing=1.5,
        bbox={
            "boxstyle": "round,pad=0.45",
            "facecolor": "#f4f6f8",
            "edgecolor": "0.65",
            "linewidth": 0.7,
        },
    )

    add_footer(
        fig,
        artefact_footer(
            panels,
            [
                "Nodata rule from meta.null_policy: a cell without data is absent, never 0. "
                "The blank margin outside each city is that rule, not a rendering choice. "
                f"Marker area ≈ one H3 res-9 footprint (~{H3_RES9_SPACING_KM:.2f} km spacing).",
                "Units from the artefacts: coherence is dimensionless [0–1] (unit '-' in the "
                f"contract); grid velocity uses {C.VELOCITY_UNIT}. "
                f"Generated by scripts/make_figures.py · {DPI} dpi · {PX_W}×{PX_H} px",
            ],
        ),
    )
    save_figure(fig, path)
    width_px, height_px = png_dimensions(path)
    return {
        "path": path,
        "width": width_px,
        "height": height_px,
        "cells": int(total),
        "detail": ", ".join(f"{p.case_id}={p.n_cells:,}" for p in panels),
    }


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _print_summary(results: Sequence[dict]) -> None:
    path_width = max(len(str(result["path"])) for result in results)
    header = f"{'figure':<{path_width}}  {'dimensions':>11}  {'size':>10}  {'cells':>9}"
    print()
    print(header)
    print("-" * len(header))
    for result in results:
        path: Path = result["path"]
        size_kb = path.stat().st_size / 1024.0
        print(
            f"{str(path):<{path_width}}  {result['width']}x{result['height']}  "
            f"{size_kb:>7.1f} KB  {result['cells']:>9,}"
        )
        print(f"  drawn: {result['detail']}")
    print("-" * len(header))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the pitch figures from data/processed (read-only, deterministic)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs" / "figures",
        help="output directory for the PNGs (default: docs/figures)",
    )
    parser.add_argument(
        "--processed",
        type=Path,
        default=ROOT / "data" / "processed",
        help="artefact root to read (default: data/processed); read-only",
    )
    args = parser.parse_args(argv)

    out_dir: Path = args.out
    panels = load_cases(args.processed)
    if not panels:
        print(
            f"error: no usable case under {args.processed} — nothing to draw. "
            "Generate the artefacts first (pipeline/generate_mock.py).",
            file=sys.stderr,
        )
        return 1

    print(f"reading artefacts: {args.processed}")
    print(f"writing figures:   {out_dir}")
    for panel in panels:
        print(
            f"  {panel.case_id:<12} {panel.name:<18} n={panel.n_cells:>6,} cells "
            f"median={panel.median_velocity:+8.1f} mm/yr  product={panel.product} "
            f"revisit={panel.revisit_days} d  provenance={panel.provenance_id}"
        )
        if not panel.provenance_ok():
            _note(
                f"provenance_id '{panel.provenance_id}' does not match the frozen format "
                f"{C.PROVENANCE_ID_RE.pattern}; printed verbatim in the footer"
            )

    results = [
        build_velocity_figure(panels, out_dir / "velocity_shared_scale.png"),
        build_timeseries_figure(panels, out_dir / "timeseries_three_cities.png"),
        build_coherence_figure(panels, out_dir / "coherence.png"),
    ]
    _print_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
