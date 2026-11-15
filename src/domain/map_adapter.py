"""Map-ready data adapter for PyDeck 2D/3D rendering (Rol 1 — pozo).

Responsibilities (flujo §3.1, §4):
- Convert filtered `nisar_grid.parquet` rows and `context_layers.geojson` features into
  JSON-serialisable records with pre-computed RGBA colours, 3D column heights (`elevation_m`),
  H3 polygon boundaries (`[lon, lat]`), formatted tooltip strings and camera `MapViewState`.
- Implement the **shared vs independent scale toggle** (README §1):
  - `scale_mode="shared"` pins all cities to `[-450, +50] mm/year` with a neutral grey band
    across `[-5, +5] mm/year`, making Málaga's stability visibly flat next to Mexico City.
  - `scale_mode="independent"` scales to each city's own distribution while keeping values
    within `±detection_limit_mm_yr` inside the neutral detection-floor band.
- Never import `streamlit` or `pydeck` so every colour, height and layer spec is unit-tested
  in milliseconds.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence

import h3
import numpy as np
import pandas as pd

from src.domain import contracts as C
from src.domain.filters import FilterCriteria, FilteredCaseView, apply_filters

if TYPE_CHECKING:
    from src.data.loaders import CaseBundle

ScaleMode = Literal["shared", "independent"]
MapMetric = Literal["velocity_mm_yr", "delta_change", "coherence", "risk_level"]

SCALE_MODES: tuple[str, ...] = ("shared", "independent")
MAP_METRICS: tuple[str, ...] = ("velocity_mm_yr", "delta_change", "coherence", "risk_level")

#: Shared symmetric cross-city velocity domain [mm/year], aligned with docs/handoff-mock.md §3b
#: and docs/figures/velocity_shared_scale.png (±425 mm/yr symmetric about 0).
GLOBAL_VELOCITY_RANGE_MM_YR: tuple[float, float] = (-425.0, 425.0)
GLOBAL_DELTA_RANGE_MM: tuple[float, float] = (-155.0, 155.0)
MAX_EXTRUDE_METERS: float = 3000.0

#: Zero-displacement baseline for 3-D columns (O-07). deck.gl extrudes polygons *from the ground
#: plane*, so a negative `get_elevation` drives subsidence columns below the basemap where they
#: render unreliably; the signed displacement is therefore mapped two-sided around this midpoint:
#: subsidence runs down toward 0 m, `delta_change == 0` sits at the baseline, uplift runs up toward
#: `MAX_EXTRUDE_METERS`. Amplitudes stay symmetric, so a (+x, -x) pair keeps equal magnitude.
COLUMN_BASELINE_M: float = MAX_EXTRUDE_METERS / 2.0
COLUMN_AMPLITUDE_M: float = MAX_EXTRUDE_METERS / 2.0

# Diverging RdBu-aligned ramp (red = subsidence < 0, neutral slate = [-5, +5] floor, blue = uplift > 0).
_VELOCITY_SHARED_ANCHORS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (-425.0, (103, 0, 31)),      # #67001f deep crimson (peak compaction)
    (-275.0, (178, 24, 43)),     # #b2182b strong red
    (-150.0, (214, 96, 77)),     # #d6604d coral-red
    (-60.0, (244, 165, 130)),    # #f4a582 warm salmon
    (-25.0, (253, 219, 199)),    # #fddbc7 pale rose (moderate onset)
    (-5.0, (148, 163, 184)),     # #94a3b8 neutral slate (detection/stability edge)
    (5.0, (148, 163, 184)),      # #94a3b8 neutral slate (estable band [-5, +5])
    (25.0, (209, 229, 240)),     # #d1e5f0 pale ice blue
    (150.0, (67, 147, 195)),     # #4393c3 medium blue
    (425.0, (5, 48, 97)),        # #053061 deep ocean blue (strong uplift)
)

_COHERENCE_ANCHORS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (30, 27, 75)),         # #1e1b4b very low coherence
    (0.35, (109, 40, 217)),      # #6d28d9 threshold region
    (0.65, (13, 148, 136)),      # #0d9488 good coherence
    (1.0, (132, 204, 22)),       # #84cc16 high coherence
)

RISK_COLORS_RGB: Mapping[str, tuple[int, int, int]] = {
    "estable": (148, 163, 184),   # #94a3b8 neutral slate
    "moderado": (245, 158, 11),   # #f59e0b amber
    "critico": (225, 29, 72),     # #e11d48 crimson
}

CONTEXT_LAYER_STYLES: Mapping[str, Mapping[str, Any]] = {
    "admin_boundary": {
        "fill_color": [0, 0, 0, 0],
        "line_color": [226, 232, 240, 200],
        "line_width_min_pixels": 2,
        "point_radius_min_pixels": 4,
    },
    "water": {
        "fill_color": [14, 165, 233, 75],
        "line_color": [56, 189, 248, 220],
        "line_width_min_pixels": 2,
        "point_radius_min_pixels": 5,
    },
    "infrastructure": {
        "fill_color": [250, 204, 21, 180],
        "line_color": [254, 240, 138, 240],
        "line_width_min_pixels": 2,
        "point_radius_min_pixels": 6,
    },
    "land_use": {
        "fill_color": [148, 163, 184, 40],
        "line_color": [148, 163, 184, 110],
        "line_width_min_pixels": 1,
        "point_radius_min_pixels": 4,
    },
}


@dataclass(frozen=True)
class LegendStop:
    """Single colour stop in a map legend."""

    value: float | None
    label: str
    color_rgba: tuple[int, int, int, int]
    color_hex: str


@dataclass(frozen=True)
class LegendSpec:
    """Complete legend specification passed straight to UI legend components."""

    metric: str
    unit: str
    scale_mode: str
    domain_min: float | None
    domain_max: float | None
    neutral_band_mm_yr: tuple[float, float] | None
    detection_limit_mm_yr: float | None
    revisit_days: int
    stops: tuple[LegendStop, ...]
    caption: str


@dataclass(frozen=True)
class MapViewState:
    """Initial camera state for `pydeck.ViewState`."""

    latitude: float
    longitude: float
    zoom: float
    pitch: float
    bearing: float = 0.0
    min_zoom: float = 7.0
    max_zoom: float = 15.0

    def to_dict(self) -> dict[str, float]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "zoom": self.zoom,
            "pitch": self.pitch,
            "bearing": self.bearing,
            "min_zoom": self.min_zoom,
            "max_zoom": self.max_zoom,
        }


@dataclass(frozen=True)
class MapPayload:
    """Complete PyDeck-ready map package for one filtered study case."""

    case_id: str
    case_name: str
    metric: str
    scale_mode: str
    extrude_3d: bool
    grid_frame: pd.DataFrame
    grid_records: list[dict[str, Any]]
    context_geojson: dict[str, Any]
    view_state: MapViewState
    legend: LegendSpec
    selected_cell_id: str | None
    is_mock: bool
    is_empty: bool


def _rgb_to_hex(rgb: Sequence[int]) -> str:
    return f"#{int(rgb[0]):02x}{int(rgb[1]):02x}{int(rgb[2]):02x}"


def _interpolate_rgb(
    values: np.ndarray,
    anchors: Sequence[tuple[float, tuple[int, int, int]]],
) -> np.ndarray:
    """Vectorised piecewise-linear RGB interpolation -> uint8 array of shape (N, 3)."""
    xp = np.array([pt[0] for pt in anchors], dtype=np.float64)
    r_fp = np.array([pt[1][0] for pt in anchors], dtype=np.float64)
    g_fp = np.array([pt[1][1] for pt in anchors], dtype=np.float64)
    b_fp = np.array([pt[1][2] for pt in anchors], dtype=np.float64)

    clamped = np.clip(values, xp[0], xp[-1])
    r = np.rint(np.interp(clamped, xp, r_fp)).astype(np.int64)
    g = np.rint(np.interp(clamped, xp, g_fp)).astype(np.int64)
    b = np.rint(np.interp(clamped, xp, b_fp)).astype(np.int64)
    return np.stack([r, g, b], axis=-1)


def column_elevation_from_delta(
    signed_delta: np.ndarray | float,
    denom: float,
) -> np.ndarray:
    """Map SIGNED `delta_change` [mm] to a non-negative 3-D column height in metres (O-07).

    `denom` is the one-sided normalisation ceiling (mm), so the signed fraction
    `delta / denom` lands in `[-1, +1]`. That fraction is mapped two-sided around
    `COLUMN_BASELINE_M` with symmetric amplitudes, which keeps the *physical ordering*
    (`elev(-x) < elev(0) < elev(+x)`), keeps equal-magnitude displacements at equal-magnitude
    offsets from the baseline, and never produces a negative height for pydeck/deck.gl to
    extrude below the ground plane.
    """
    delta = np.asarray(signed_delta, dtype=np.float64)
    safe_denom = float(denom) if math.isfinite(float(denom)) and float(denom) > 0.0 else 1.0
    fraction = np.clip(delta / safe_denom, -1.0, 1.0)
    return COLUMN_BASELINE_M + fraction * COLUMN_AMPLITUDE_M


def _independent_velocity_anchors(
    domain_min: float,
    domain_max: float,
    neutral_half_width: float,
) -> tuple[tuple[float, tuple[int, int, int]], ...]:
    """Build local-range velocity colour anchors while preserving a neutral detection band."""
    nb = max(0.5, float(neutral_half_width))
    lo = min(float(domain_min), -max(8.0, nb * 3.0))
    hi = max(float(domain_max), max(8.0, nb * 2.0))

    # Proportional negative stops between lo and -nb
    span_neg = abs(lo) - nb
    s1 = -nb - 0.15 * span_neg
    s2 = -nb - 0.40 * span_neg
    s3 = -nb - 0.70 * span_neg
    pos_mid = nb + 0.5 * max(1.0, hi - nb)

    return (
        (lo, (103, 0, 31)),
        (s3, (178, 24, 43)),
        (s2, (214, 96, 77)),
        (s1, (244, 165, 130)),
        (-nb, (148, 163, 184)),
        (nb, (148, 163, 184)),
        (pos_mid, (67, 147, 195)),
        (hi, (5, 48, 97)),
    )


def compute_view_state(
    meta: Mapping[str, Any],
    *,
    extrude_3d: bool = False,
) -> MapViewState:
    """Compute initial camera center and zoom from `meta.bbox` (`[W, S, E, N]`)."""
    bbox = meta.get("bbox")
    ref = meta.get("reference_point")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        w, s, e, n = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        lon = 0.5 * (w + e)
        lat = 0.5 * (s + n)
        span_deg = max(abs(e - w), abs(n - s), 0.02)
        # Fit ~360 / span_deg into ~1.4 viewport tiles cleanly: ~0.5 deg -> zoom ~10.3, ~0.3 -> ~11.0
        zoom = float(max(7.5, min(13.5, math.log2(360.0 / span_deg) - 0.9)))
    elif isinstance(ref, (list, tuple)) and len(ref) == 2:
        lon, lat = float(ref[0]), float(ref[1])
        zoom = 10.8
    else:
        lon, lat, zoom = 0.0, 20.0, 2.0

    pitch = 45.0 if extrude_3d else 0.0
    return MapViewState(
        latitude=round(lat, 5),
        longitude=round(lon, 5),
        zoom=round(zoom, 2),
        pitch=pitch,
        bearing=0.0,
    )


@lru_cache(maxsize=32768)
def _cached_h3_ring(cell_id: str) -> tuple[tuple[float, float], ...]:
    ring = [(round(float(lng), 6), round(float(lat), 6)) for lat, lng in h3.cell_to_boundary(cell_id)]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return tuple(ring)


def _cell_polygon_lonlat(cell_id: str, fallback_lon: float, fallback_lat: float) -> list[list[float]]:
    """Return closed GeoJSON-ordered `[[lon, lat], ...]` boundary for an H3 cell."""
    if h3.is_valid_cell(cell_id):
        # h3 v4 returns ((lat, lng), ...) -> swap to [lng, lat] for PyDeck / GeoJSON.
        return [[pt[0], pt[1]] for pt in _cached_h3_ring(cell_id)]
    # Small ~100 m square fallback only for non-H3 synthetic test strings
    d = 0.001
    return [
        [fallback_lon - d, fallback_lat - d],
        [fallback_lon + d, fallback_lat - d],
        [fallback_lon + d, fallback_lat + d],
        [fallback_lon - d, fallback_lat + d],
        [fallback_lon - d, fallback_lat - d],
    ]


def build_legend_spec(
    meta: Mapping[str, Any],
    *,
    metric: MapMetric = "velocity_mm_yr",
    scale_mode: ScaleMode = "shared",
    observed_min: float | None = None,
    observed_max: float | None = None,
) -> LegendSpec:
    """Build a complete `LegendSpec` for the requested map metric and scale mode."""
    if metric not in MAP_METRICS:
        raise C.ContractError(f"Unsupported map metric {metric!r}; allowed {list(MAP_METRICS)}")
    if scale_mode not in SCALE_MODES:
        raise C.ContractError(f"Unsupported scale_mode {scale_mode!r}; allowed {list(SCALE_MODES)}")

    revisit_days = int(meta.get("revisit_days", 12))
    stats = meta.get("stats") or {}
    det_raw = stats.get("detection_limit_mm_yr")
    det_limit = float(det_raw) if isinstance(det_raw, (int, float)) else None

    if metric == "risk_level":
        stops = tuple(
            LegendStop(
                value=None,
                label=f"{lvl} ({'< 5' if lvl == 'estable' else '5–25' if lvl == 'moderado' else '≥ 25'} mm/year)",
                color_rgba=(*RISK_COLORS_RGB[lvl], 215),
                color_hex=_rgb_to_hex(RISK_COLORS_RGB[lvl]),
            )
            for lvl in C.RISK_LEVELS
        )
        return LegendSpec(
            metric=metric,
            unit=C.VELOCITY_UNIT,
            scale_mode="shared",
            domain_min=None,
            domain_max=None,
            neutral_band_mm_yr=(-5.0, 5.0),
            detection_limit_mm_yr=det_limit,
            revisit_days=revisit_days,
            stops=stops,
            caption="Frozen risk bands from |velocity_mm_yr| (sign-agnostic).",
        )

    if metric == "coherence":
        stops = tuple(
            LegendStop(
                value=val,
                label=f"{val:.2f}",
                color_rgba=(*rgb, 215),
                color_hex=_rgb_to_hex(rgb),
            )
            for val, rgb in _COHERENCE_ANCHORS
        )
        return LegendSpec(
            metric=metric,
            unit="-",
            scale_mode="shared",
            domain_min=0.0,
            domain_max=1.0,
            neutral_band_mm_yr=None,
            detection_limit_mm_yr=det_limit,
            revisit_days=revisit_days,
            stops=stops,
            caption=(
                f"Temporal interferometric coherence ({revisit_days}-day revisit). "
                f"Never compare directly across different revisit intervals."
            ),
        )

    if metric == "delta_change":
        if scale_mode == "shared":
            d_min, d_max = GLOBAL_DELTA_RANGE_MM
            neutral = (-2.0, 2.0)
            anchors = tuple(
                (val * (GLOBAL_DELTA_RANGE_MM[1] / GLOBAL_VELOCITY_RANGE_MM_YR[1]), rgb)
                for val, rgb in _VELOCITY_SHARED_ANCHORS
            )
            caption = f"Shared cumulative displacement scale [{d_min:.0f}, +{d_max:.0f}] mm across cities."
        else:
            lo = float(observed_min) if observed_min is not None else -20.0
            hi = float(observed_max) if observed_max is not None else 5.0
            anchors = _independent_velocity_anchors(lo, hi, det_limit or 2.0)
            d_min, d_max = anchors[0][0], anchors[-1][0]
            nb = det_limit or 2.0
            neutral = (-nb, nb)
            caption = f"Independent city cumulative scale [{d_min:.1f}, {d_max:.1f}] mm."

        stops = tuple(
            LegendStop(
                value=round(val, 1),
                label=f"{val:+.1f} mm",
                color_rgba=(*rgb, 215),
                color_hex=_rgb_to_hex(rgb),
            )
            for val, rgb in anchors
        )
        return LegendSpec(
            metric=metric,
            unit=C.UNIT,
            scale_mode=scale_mode,
            domain_min=float(d_min),
            domain_max=float(d_max),
            neutral_band_mm_yr=neutral,
            detection_limit_mm_yr=det_limit,
            revisit_days=revisit_days,
            stops=stops,
            caption=caption,
        )

    # Default: velocity_mm_yr
    if scale_mode == "shared":
        anchors = _VELOCITY_SHARED_ANCHORS
        d_min, d_max = GLOBAL_VELOCITY_RANGE_MM_YR
        neutral = (-5.0, 5.0)
        det_note = (
            f" Detection floor ±{det_limit:.1f} mm/year lies inside the neutral grey band."
            if det_limit is not None
            else ""
        )
        caption = (
            f"Shared cross-city velocity scale [{d_min:.0f}, +{d_max:.0f}] mm/year.{det_note}"
        )
    else:
        # Per docs/handoff-mock.md §3b: read p05/p95 from meta.stats for independent per-case toggle.
        p05 = stats.get("p05_velocity_mm_yr")
        p95 = stats.get("p95_velocity_mm_yr")
        lo_seed = float(p05) if isinstance(p05, (int, float)) else float(observed_min or -8.0)
        hi_seed = float(p95) if isinstance(p95, (int, float)) else float(observed_max or 8.0)
        nb = det_limit if det_limit is not None else 2.0
        anchors = _independent_velocity_anchors(lo_seed, hi_seed, nb)
        d_min, d_max = anchors[0][0], anchors[-1][0]
        neutral = (-nb, nb)
        caption = (
            f"Independent local velocity scale [{d_min:.1f}, +{d_max:.1f}] mm/year "
            f"(seeded from meta.stats p05/p95; neutral band ±{nb:.1f} mm/year)."
        )

    stops = tuple(
        LegendStop(
            value=round(val, 1),
            label=f"{val:+.1f} mm/yr",
            color_rgba=(*rgb, 215),
            color_hex=_rgb_to_hex(rgb),
        )
        for val, rgb in anchors
    )
    return LegendSpec(
        metric=metric,
        unit=C.VELOCITY_UNIT,
        scale_mode=scale_mode,
        domain_min=float(d_min),
        domain_max=float(d_max),
        neutral_band_mm_yr=neutral,
        detection_limit_mm_yr=det_limit,
        revisit_days=revisit_days,
        stops=stops,
        caption=caption,
    )


def prepare_context_for_map(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep-copied GeoJSON FeatureCollection with PyDeck visual style properties."""
    raw_features = context.get("features") or []
    styled_features: list[dict[str, Any]] = []
    for feat in raw_features:
        if not isinstance(feat, Mapping):
            continue
        cloned = copy.deepcopy(dict(feat))
        props = dict(cloned.get("properties") or {})
        layer_name = str(props.get("layer", "land_use"))
        style = CONTEXT_LAYER_STYLES.get(layer_name, CONTEXT_LAYER_STYLES["land_use"])
        props["fill_color"] = list(style["fill_color"])
        props["line_color"] = list(style["line_color"])
        props["line_width_min_pixels"] = int(style["line_width_min_pixels"])
        props["point_radius_min_pixels"] = int(style["point_radius_min_pixels"])
        props["tooltip_title"] = str(props.get("name") or props.get("class") or layer_name)
        cloned["properties"] = props
        styled_features.append(cloned)

    return {
        "type": "FeatureCollection",
        "features": styled_features,
    }


def build_map_payload(
    bundle_or_view: CaseBundle | FilteredCaseView,
    criteria: FilterCriteria | None = None,
    *,
    metric: MapMetric = "velocity_mm_yr",
    scale_mode: ScaleMode = "shared",
    extrude_3d: bool = False,
    include_h3_polygons: bool = True,
    alpha: int = 210,
) -> MapPayload:
    """Build a complete `MapPayload` (colours, 3D heights, H3 polygons, context, legend, camera)."""
    if isinstance(bundle_or_view, FilteredCaseView):
        view = bundle_or_view
    else:
        view = apply_filters(bundle_or_view, criteria)

    meta = view.meta
    grid = view.grid
    selected_cell = view.criteria.selected_cell_id
    revisit_days = int(meta.get("revisit_days", 12))
    alpha_clamped = max(30, min(255, int(alpha)))

    obs_min: float | None = None
    obs_max: float | None = None
    if not grid.empty and metric in ("velocity_mm_yr", "delta_change"):
        obs_min = float(grid[metric].min())
        obs_max = float(grid[metric].max())

    legend = build_legend_spec(
        meta,
        metric=metric,
        scale_mode=scale_mode,
        observed_min=obs_min,
        observed_max=obs_max,
    )
    view_state = compute_view_state(meta, extrude_3d=extrude_3d)
    styled_context = prepare_context_for_map(view.context)

    if grid.empty:
        empty_df = grid.copy()
        for extra_col in (
            "fill_color",
            "line_color",
            "motion_direction",
            "elevation_m",
            "is_selected",
            "tooltip_velocity",
            "tooltip_delta",
            "tooltip_coherence",
        ):
            empty_df[extra_col] = pd.Series(dtype="object")
        return MapPayload(
            case_id=view.case_id,
            case_name=str(meta.get("name", view.case_id)),
            metric=metric,
            scale_mode=scale_mode,
            extrude_3d=extrude_3d,
            grid_frame=empty_df,
            grid_records=[],
            context_geojson=styled_context,
            view_state=view_state,
            legend=legend,
            selected_cell_id=selected_cell,
            is_mock=(str(meta.get("product")) == "mock"),
            is_empty=True,
        )

    n_rows = len(grid)
    vel_arr = grid["velocity_mm_yr"].to_numpy(dtype=np.float64)
    delta_arr = grid["delta_change"].to_numpy(dtype=np.float64)
    coh_arr = grid["coherence"].to_numpy(dtype=np.float64)
    risk_list = grid["risk_level"].astype(str).tolist()

    if metric == "risk_level":
        rgb_arr = np.array(
            [RISK_COLORS_RGB.get(r, (148, 163, 184)) for r in risk_list],
            dtype=np.int64,
        )
    elif metric == "coherence":
        rgb_arr = _interpolate_rgb(coh_arr, _COHERENCE_ANCHORS)
    elif metric == "delta_change":
        anchors = tuple(
            (float(s.value), s.color_rgba[:3])
            for s in legend.stops
            if s.value is not None
        )
        rgb_arr = _interpolate_rgb(delta_arr, anchors)
    else:
        anchors = tuple(
            (float(s.value), s.color_rgba[:3])
            for s in legend.stops
            if s.value is not None
        )
        rgb_arr = _interpolate_rgb(vel_arr, anchors)

    if extrude_3d:
        # O-07: bind 3D column height to SIGNED delta_change [mm] — never to |delta_change| — while
        # keeping the shared physical rate ceiling. The normalised signed fraction is mapped
        # two-sided around COLUMN_BASELINE_M (see the constants' docstring for why the baseline is
        # the midpoint rather than 0 m):
        #   elevation = MAX/2 + clip(delta_change / denom, -1, +1) * MAX/2
        # so elev(-x) < elev(0) = MAX/2 < elev(+x) and |elev - baseline| grows with |delta|.
        if scale_mode == "shared":
            # "shared" normalisation: one physical rate ceiling for every city.
            dates_raw = meta.get("dates") or ()
            span_years = 0.3614
            if len(dates_raw) >= 2:
                try:
                    d0 = dt.date.fromisoformat(str(dates_raw[0]))
                    d1 = dt.date.fromisoformat(str(dates_raw[-1]))
                    span_years = max((d1 - d0).days / 365.25, 12.0 / 365.25)
                except ValueError:
                    span_years = 0.3614
            denom = abs(GLOBAL_VELOCITY_RANGE_MM_YR[0]) * span_years
        else:
            # "independent": the city's own maximum, symmetrically — both signs share one scale.
            denom = max(3.5, float(np.max(np.abs(delta_arr))))
        elev_arr = column_elevation_from_delta(delta_arr, denom)
    else:
        # Flat mode keeps zeroing elevation: non-zero heights cause z-fighting on hexes.
        elev_arr = np.zeros(n_rows, dtype=np.float64)

    cell_ids = grid["cell_id"].astype(str).tolist()
    lats = grid["lat"].to_numpy(dtype=np.float64)
    lons = grid["lon"].to_numpy(dtype=np.float64)
    val_t1s = grid["val_t1"].to_numpy(dtype=np.float64)
    val_t2s = grid["val_t2"].to_numpy(dtype=np.float64)
    land_uses = grid["land_use"].astype(str).tolist()
    sensors = grid["sensor"].astype(str).tolist()
    products = grid["product"].astype(str).tolist()
    sources = grid["source"].astype(str).tolist()
    prov_ids = grid["provenance_id"].astype(str).tolist()

    records: list[dict[str, Any]] = []
    fill_colors_col: list[list[int]] = []
    line_colors_col: list[list[int]] = []
    directions_col: list[str] = []
    is_selected_col: list[bool] = []
    tooltip_vel_col: list[str] = []
    tooltip_delta_col: list[str] = []
    tooltip_coh_col: list[str] = []
    polygons_col: list[list[list[float]]] = []

    for idx in range(n_rows):
        cid = cell_ids[idx]
        is_sel = bool(selected_cell and cid == selected_cell)
        r_i, g_i, b_i = int(rgb_arr[idx, 0]), int(rgb_arr[idx, 1]), int(rgb_arr[idx, 2])
        fill_rgba = [r_i, g_i, b_i, 245 if is_sel else alpha_clamped]
        line_rgba = [255, 255, 255, 255] if is_sel else [r_i, g_i, b_i, 235]

        v_val = float(vel_arr[idx])
        d_val = float(delta_arr[idx])
        direction = "subsidence" if d_val < 0.0 else ("uplift" if d_val > 0.0 else "zero")
        c_val = float(coh_arr[idx])
        e_val = round(float(elev_arr[idx]), 2)
        lat_val = float(lats[idx])
        lon_val = float(lons[idx])

        t_vel = f"{v_val:+.1f} {C.VELOCITY_UNIT}"
        t_delta = f"{d_val:+.1f} {C.UNIT}"
        t_coh = f"{c_val:.2f} ({revisit_days}d revisit)"

        rec: dict[str, Any] = {
            "cell_id": cid,
            "lat": lat_val,
            "lon": lon_val,
            "val_t1": float(val_t1s[idx]),
            "val_t2": float(val_t2s[idx]),
            "delta_change": d_val,
            "motion_direction": direction,
            "velocity_mm_yr": v_val,
            "coherence": c_val,
            "land_use": land_uses[idx],
            "risk_level": risk_list[idx],
            "sensor": sensors[idx],
            "product": products[idx],
            "unit": C.UNIT,
            "velocity_unit": C.VELOCITY_UNIT,
            "source": sources[idx],
            "provenance_id": prov_ids[idx],
            "fill_color": fill_rgba,
            "line_color": line_rgba,
            "elevation_m": e_val,
            "is_selected": is_sel,
            "tooltip_velocity": t_vel,
            "tooltip_delta": t_delta,
            "tooltip_coherence": t_coh,
        }
        if include_h3_polygons:
            poly = _cell_polygon_lonlat(cid, lon_val, lat_val)
            rec["polygon"] = poly
            polygons_col.append(poly)

        records.append(rec)
        fill_colors_col.append(fill_rgba)
        line_colors_col.append(line_rgba)
        directions_col.append(direction)
        is_selected_col.append(is_sel)
        tooltip_vel_col.append(t_vel)
        tooltip_delta_col.append(t_delta)
        tooltip_coh_col.append(t_coh)

    map_df = grid.copy()
    map_df["fill_color"] = fill_colors_col
    map_df["line_color"] = line_colors_col
    map_df["motion_direction"] = directions_col
    map_df["elevation_m"] = np.round(elev_arr, 2)
    map_df["is_selected"] = is_selected_col
    map_df["tooltip_velocity"] = tooltip_vel_col
    map_df["tooltip_delta"] = tooltip_delta_col
    map_df["tooltip_coherence"] = tooltip_coh_col
    if include_h3_polygons:
        map_df["polygon"] = polygons_col

    return MapPayload(
        case_id=view.case_id,
        case_name=str(meta.get("name", view.case_id)),
        metric=metric,
        scale_mode=scale_mode,
        extrude_3d=extrude_3d,
        grid_frame=map_df,
        grid_records=records,
        context_geojson=styled_context,
        view_state=view_state,
        legend=legend,
        selected_cell_id=selected_cell,
        is_mock=(str(meta.get("product")) == "mock"),
        is_empty=False,
    )


def build_pydeck_layer_specs(
    payload: MapPayload,
    *,
    use_polygon_layer: bool = True,
    show_context: bool = True,
) -> dict[str, Any]:
    """Return declarative PyDeck layer & tooltip configurations for `src/ui/map_view.py`.

    `PolygonLayer` is the default because its `[lon, lat]` rings are pre-computed in Python with
    `h3`, avoiding any browser-side `h3-js` CDN fetch failure during offline/restricted demos.
    Set `use_polygon_layer=False` to emit an `H3HexagonLayer` spec instead.
    """
    layers: list[dict[str, Any]] = []

    if show_context and payload.context_geojson.get("features"):
        layers.append(
            {
                "type": "GeoJsonLayer",
                "kwargs": {
                    "id": f"context-{payload.case_id}",
                    "data": payload.context_geojson,
                    "pickable": True,
                    "stroked": True,
                    "filled": True,
                    "point_type": "circle",
                    "get_fill_color": "properties.fill_color",
                    "get_line_color": "properties.line_color",
                    "get_line_width": 2,
                    "line_width_min_pixels": 1,
                    "point_radius_min_pixels": 5,
                },
            }
        )

    if not payload.is_empty:
        if use_polygon_layer:
            layers.append(
                {
                    "type": "PolygonLayer",
                    "kwargs": {
                        "id": f"sar-cells-{payload.case_id}",
                        "data": payload.grid_records,
                        "pickable": True,
                        "stroked": True,
                        "filled": True,
                        "extruded": payload.extrude_3d,
                        "wireframe": payload.extrude_3d,
                        "get_polygon": "polygon",
                        "get_fill_color": "fill_color",
                        "get_line_color": "line_color",
                        "get_elevation": "elevation_m",
                        "elevation_scale": 1.0,
                        "line_width_min_pixels": 1,
                        "auto_highlight": True,
                    },
                }
            )
        else:
            layers.append(
                {
                    "type": "H3HexagonLayer",
                    "kwargs": {
                        "id": f"sar-h3-{payload.case_id}",
                        "data": payload.grid_records,
                        "pickable": True,
                        "stroked": True,
                        "filled": True,
                        "extruded": payload.extrude_3d,
                        "get_hexagon": "cell_id",
                        "get_fill_color": "fill_color",
                        "get_line_color": "line_color",
                        "get_elevation": "elevation_m",
                        "elevation_scale": 1.0,
                        "auto_highlight": True,
                    },
                }
            )

    tooltip = {
        "html": (
            "<b>Cell:</b> {cell_id}<br/>"
            "<b>Velocity:</b> {tooltip_velocity}<br/>"
            "<b>Cumulative Δ:</b> {tooltip_delta}<br/>"
            "<b>Risk:</b> {risk_level} · <b>Land use:</b> {land_use}<br/>"
            "<b>Coherence:</b> {tooltip_coherence}<br/>"
            "<b>Source:</b> {source} ({sensor} / {product})"
        ),
        "style": {
            "backgroundColor": "#0f172a",
            "color": "#f8fafc",
            "fontSize": "12px",
            "borderRadius": "6px",
            "padding": "8px 10px",
        },
    }

    return {
        "view_state": payload.view_state.to_dict(),
        "layers": layers,
        "tooltip": tooltip,
    }


__all__ = [
    "COLUMN_AMPLITUDE_M",
    "COLUMN_BASELINE_M",
    "GLOBAL_DELTA_RANGE_MM",
    "GLOBAL_VELOCITY_RANGE_MM_YR",
    "LegendSpec",
    "LegendStop",
    "MAP_METRICS",
    "MAX_EXTRUDE_METERS",
    "MapMetric",
    "MapPayload",
    "MapViewState",
    "RISK_COLORS_RGB",
    "SCALE_MODES",
    "ScaleMode",
    "build_legend_spec",
    "build_map_payload",
    "build_pydeck_layer_specs",
    "column_elevation_from_delta",
    "compute_view_state",
    "prepare_context_for_map",
]
