"""Deterministic synthetic SAR displacement field for one study case.

Model (all parameters come from `config/study_cases.yaml`, never hard-coded here):

    v(x)      = peak_velocity_mm_yr * exp(-d(peak_lonlat)/falloff_km) + N(0, noise_mm_yr)
    d(t)      = v * (t - t_first)/yr + A_seasonal * (cos(2*pi*(t-c)/yr) - cos(2*pi*c/yr))
    val_t1    = d(first date), val_t2 = d(last date), delta_change = val_t2 - val_t1
    velocity  = trend coefficient of the joint fit [1, t, cos, sin, ...] (README §6, step P6)

The harmonic is centred on the window (``c`` = its midpoint) rather than anchored at the first
date. That keeps both endpoints' seasonal offset equal, so ``delta_change`` tracks the secular rate
while the seasonal swing stays visible mid-window; the constant ``-A·cos(2πc/T)`` is absorbed by the
fit's intercept, so the injected amplitude is recovered exactly. (The centring was introduced when
the window was 0.36 yr and an anchored harmonic was a monotone slice of the annual cycle; it is kept
on the 2-year window because it still makes ``val_t1 = 0`` and ``delta_change = trend·span`` exact.)

**The published seasonal fields are fitted, not typed.** `contracts` 1.1 requires
``seasonal_amp_mm`` / ``ratio_R`` / ``regime`` and the timeseries ``trend_mm_yr`` / ``seasonal_mm``.
They all come out of :func:`fit_series`, which calls the pipeline's own
``pipeline.science.transforms.harmonic_fit`` per cell — the same function the real chain uses — so
the mock proves the contract can be filled from a fit. A config value copied into a column would
prove nothing, and this module no longer does that.

**A case with no seasonal term publishes the "not fitted" state, not NaN.** :func:`seasonal_order`
returns 0 when the case injects no seasonality (``mock.seasonal_amplitude_mm: 0``) or when the
window cannot identify a harmonic at all, and then every cell is fitted with the plain linear model
``[1, t]``: ``seasonal_amp_mm`` is exactly ``0.0``, ``ratio_R`` is NaN (which
``artifacts.build_meta`` publishes as JSON ``null``), ``regime`` is ``"transitional"`` and
``meta.limitations`` names the reason. That is the same state, decided the same way, that
``pipeline/process_case.py`` publishes for a too-short real stack — the mock has no second opinion
about contract 1.1.

The annual harmonic is inside the fit basis, so the published `velocity_mm_yr` is the *secular*
trend and not a seasonal artefact — which is what "fitted trend" means in the contract.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

import h3
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Polygon

from pipeline.science import transforms as V
from src.domain import contracts as C

from . import geography as G
from .cases import CaseSpec, StudyCases

SECONDS_PER_YEAR = 365.25
SEASONAL_PERIOD_DAYS = 365.25
#: H3 res-9 hexagons get simplified before they reach the GeoJSON (≈90 m, < half a cell edge).
CONTEXT_SIMPLIFY_DEG = 0.0008
CONTEXT_PRECISION_DEG = 1e-5


@dataclass
class CaseModel:
    case: CaseSpec
    seed: int  # noise seed; published in meta.provenance so the cell set is auditable
    cell_id: np.ndarray  # H3 res-9 index, sorted (post quality filter: what gets published)
    lat: np.ndarray
    lon: np.ndarray
    rho: np.ndarray  # normalised elliptical radius (1.0 == coverage edge)
    prefilter_cell_id: np.ndarray  # every cell inside bbox + coverage ellipse, before the filter
    prefilter_coherence: np.ndarray  # modelled coherence of those cells, before the filter
    secular_velocity: np.ndarray  # what the mock *generated*
    velocity_mm_yr: np.ndarray  # what the contract publishes (joint trend fit)
    coherence: np.ndarray
    land_use: np.ndarray
    risk_level: np.ndarray
    val_t1: np.ndarray
    val_t2: np.ndarray
    delta_change: np.ndarray
    series: np.ndarray  # (n_cells, n_dates) cumulative displacement, mm
    seasonal_mm: np.ndarray  # (n_cells, n_dates) FITTED seasonal component, mm (zeros if none)
    seasonal_amp_mm: np.ndarray  # FITTED amplitude of the first harmonic, mm; 0.0 == not fitted
    ratio_R: np.ndarray  # |fitted trend| / fitted amplitude; NaN where nothing was fitted
    regime: np.ndarray  # from transforms.classify_regime, the pipeline's own rule
    n_harmonics: int  # order asked of harmonic_fit; 0 == no seasonal term fitted
    annual_coverage: float  # span in annual cycles, from the fit's own diagnostic
    design_condition: float  # column-normalised design condition number, from the fit
    dates: tuple[str, ...]

    @property
    def n_cells(self) -> int:
        return int(self.cell_id.size)

    @property
    def seasonal_fitted(self) -> bool:
        """True when a seasonal term was fitted; False is the contract's "not fitted" state."""
        return int(self.n_harmonics) > 0


def _case_rng(seed: int, case_id: str) -> np.random.Generator:
    """Per-case stream: one case's cell set never perturbs another case's numbers."""
    return np.random.default_rng(np.random.SeedSequence([int(seed), _case_key(case_id)]))


def _case_key(case_id: str) -> int:
    return int.from_bytes(hashlib.sha256(case_id.encode()).digest()[:4], "big")


def bbox_cells(bbox: tuple[float, float, float, float], resolution: int) -> list[str]:
    """Every H3 cell of `resolution` whose centroid follows the bbox quad (pre-filter)."""
    west, south, east, north = bbox
    ring = h3.LatLngPoly(
        [(south, west), (south, east), (north, east), (north, west)]
    )
    return sorted(h3.polygon_to_cells(ring, resolution))


def expected_cells(case: CaseSpec, resolution: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cell ids whose centroid is inside the bbox *and* the coverage ellipse.

    This is the nodata rule: anything outside is absent from the parquet, never written as 0.
    """
    hexes = bbox_cells(case.bbox, resolution)
    lat = np.empty(len(hexes))
    lon = np.empty(len(hexes))
    for i, cell in enumerate(hexes):
        lat[i], lon[i] = h3.cell_to_latlng(cell)
    west, south, east, north = case.bbox
    ell = case.field.coverage_ellipse
    _, _, rho = G.ellipse_rho(
        lon, lat, ell.center_lon, ell.center_lat, ell.semi_axes_km[0], ell.semi_axes_km[1], ell.rotation_deg
    )
    keep = (rho <= 1.0) & (lat >= south) & (lat <= north) & (lon >= west) & (lon <= east)
    return np.array(hexes, dtype=object)[keep], lat[keep], lon[keep]


def velocity_field(case: CaseSpec, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Secular velocity (mm/yr): exponential falloff from `peak_lonlat` + deterministic noise."""
    field = case.field
    peak_lon, peak_lat = field.peak_lonlat
    dx, dy = G.lonlat_to_local_km(lon, lat, peak_lon, peak_lat)
    distance_km = np.hypot(dx, dy)
    smooth = field.peak_velocity_mm_yr * np.exp(-distance_km / field.falloff_km)
    return smooth, distance_km


def elapsed_years(dates: tuple[str, ...]) -> np.ndarray:
    """Years since the first acquisition, the time axis every fit in this repo uses."""
    first = dt.date.fromisoformat(dates[0])
    return np.array(
        [(dt.date.fromisoformat(date) - first).days for date in dates], dtype=float
    ) / SECONDS_PER_YEAR


def cumulative_series(case: CaseSpec, dates: tuple[str, ...], velocity: np.ndarray) -> np.ndarray:
    """Generated cumulative displacement (mm, up positive) per cell per date.

    ``d(t) = v·t + A_seasonal·(cos(2π(t−c)/T) − cos(2πc/T))``, with ``c`` the centre of the window.
    The series is referenced to the first acquisition (``val_t1 == 0``, as InSAR cumulative
    displacement is referenced to an epoch, not to absolute ground motion).

    **Seasonal anchoring matters and was wrong before.** Centring the harmonic keeps both endpoints'
    seasonal offset identical, so ``delta_change`` tracks the secular rate and the map (coloured by
    ``velocity_mm_yr``) cannot disagree in sign with the date slider (driven by ``delta_change``).
    The subtracted constant is absorbed by the fit's intercept, so the injected amplitude survives
    the fit exactly — which is what :func:`fit_series` verifies below.
    """
    t_days = elapsed_years(dates) * SECONDS_PER_YEAR  # days since the first acquisition
    season_centre_days = float(t_days[-1]) / 2.0 if t_days.size else 0.0
    anchor = np.cos(2.0 * np.pi * season_centre_days / SEASONAL_PERIOD_DAYS)
    season_phase = 2.0 * np.pi * (t_days - season_centre_days) / SEASONAL_PERIOD_DAYS
    seasonal = case.field.seasonal_amplitude_mm * (np.cos(season_phase) - anchor)
    return velocity[:, None] * elapsed_years(dates)[None, :] + seasonal[None, :]


def seasonal_order(case: CaseSpec, dates: tuple[str, ...]) -> int:
    """How many annual harmonics this case may honestly fit (0 == "no seasonal term fitted").

    Two ways to land on 0, and each is a statement about the **model**, never about the ground:

    * the configured synthetic field injects no seasonality (``mock.seasonal_amplitude_mm: 0``), so
      there is no term to recover. Fitting one anyway would report the least-squares noise floor
      (~1e-13 mm) as an amplitude, and ``ratio_R = |trend| / 1e-13`` is the sentinel the contract
      rejects — exactly the NaN/crash this function exists to avoid;
    * the window cannot identify one (:func:`transforms.harmonic_order`: below 0.75 annual cycles
      of span, or too few epochs), whatever was injected.

    Either way the publication is the contract's "not fitted" state. A case that injects
    seasonality *and* spans enough time still fits it: the shipped cases take that branch, so the
    mock keeps proving the contract can be filled from a fit.
    """
    if float(case.field.seasonal_amplitude_mm) == 0.0:
        return 0
    t_years = elapsed_years(dates)
    return int(V.harmonic_order(len(dates), float(t_years[-1])))


def fit_series(
    dates: tuple[str, ...], series: np.ndarray, order: int
) -> tuple[list[V.HarmonicFit], np.ndarray]:
    """Fit the pipeline's harmonic model per cell and refuse an unpublishable seasonal term.

    One ``transforms.harmonic_fit`` call per cell — the function the real chain
    (`pipeline/process_case.py`) uses — with the order chosen by :func:`seasonal_order` (two annual
    harmonics on a ≥1.5 yr span, one on ≥0.75 yr, none below or when nothing was injected).
    Returns the fits and the time axis in years.

    With ``order > 0`` the mock injects a seasonal amplitude, so a fit that reports
    ``seasonal_identifiable=False`` (or an amplitude of zero) means the *window* or the *fit* is
    broken, not that the ground stopped moving seasonally. It raises instead of publishing a
    number the pipeline's own gate would reject: that is the whole point of the 1.1 change.

    With ``order == 0`` no seasonal term is fitted by construction, so the expected result is the
    "not fitted" state — an amplitude of exactly ``0.0`` on every cell (``harmonic_fit`` has no
    harmonic column to fill) and regime ``"transitional"``. A non-zero amplitude there would mean
    the fit invented a term, so it raises too.
    """
    t_years = elapsed_years(dates)
    fits = [V.harmonic_fit(t_years, row, n_harmonics=int(order)) for row in series]
    unidentifiable = [fit for fit in fits if not fit.seasonal_identifiable]
    if unidentifiable:
        raise ValueError(
            f"the mock window cannot publish a seasonal amplitude: {len(unidentifiable)} of "
            f"{len(fits)} cells are seasonal_identifiable=False "
            f"({unidentifiable[0].warnings[0] if unidentifiable[0].warnings else 'no reason given'}). "
            f":func:`seasonal_order` asked for {int(order)} harmonic(s) on a stack this function "
            "says cannot carry them; it must return 0 for this window instead (the publication is "
            "then the contract's 'not fitted' state, never a fitted-looking number)."
        )
    amplitudes = np.array([fit.seasonal_amp_mm for fit in fits], dtype=float)
    if int(order) == 0:
        invented = int(np.count_nonzero(amplitudes))
        if invented:
            raise ValueError(
                f"no annual harmonic was fitted (harmonic_order = 0) but {invented} of "
                f"{amplitudes.size} cells report a non-zero seasonal_amp_mm (max "
                f"{float(np.max(amplitudes)):.3e} mm): the published amplitude must be exactly "
                "0.0 in the not-fitted state"
            )
        return fits, t_years
    if not np.all(amplitudes > 0.0):
        raise ValueError(
            "the mock injected a non-zero seasonal amplitude but harmonic_fit recovered "
            f"{float(np.min(amplitudes)):.3e} mm for at least one cell: the centred harmonic is no "
            "longer representable in the fitted basis (see cumulative_series)"
        )
    return fits, t_years


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def assign_land_use(
    case: CaseSpec, geo: G.CaseGeography, lon: np.ndarray, lat: np.ndarray, rho: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Deterministic spatial rule over the whole frozen vocabulary (§7.1) except `unknown`.

    Order matters and is deliberate: water wins over everything (a cell whose centroid is at sea
    is water), then the corridor (infrastructure spine inside industrial), then the urban rings,
    then the fringe with hash-driven bare patches.
    """
    n = lon.size
    land_use = np.full(n, "vegetation", dtype=object)

    # --- fringe: vegetation with bare patches ------------------------------------------------
    bare_draw = rng.random(n)
    land_use = np.where((rho >= 0.80) & (bare_draw < 0.35), "bare", land_use)

    # --- urban rings -------------------------------------------------------------------------
    land_use = np.where(rho < 0.80, "urban-residential", land_use)
    land_use = np.where(rho < 0.38, "urban-dense", land_use)

    # --- industrial corridor (+ infrastructure spine) ---------------------------------------
    center = case.field.coverage_ellipse
    axis = G.corridor_segment(geo, center.center_lon, center.center_lat)
    corridor_km, _ = G.polyline_metrics(lon, lat, [axis[0], axis[1]])
    land_use = np.where((corridor_km <= 3.0) & (rho < 0.95), "industrial", land_use)
    land_use = np.where(corridor_km <= 0.55, "infrastructure", land_use)

    # --- water: lake, sea side of the coastline, river/canal --------------------------------
    water = np.zeros(n, dtype=bool)
    if geo.lake is not None:
        lake_lon, lake_lat, lake_a, lake_b, lake_rot = geo.lake
        _, _, lake_rho = G.ellipse_rho(lon, lat, lake_lon, lake_lat, lake_a, lake_b, lake_rot)
        water |= lake_rho <= 1.0
    if geo.coast is not None:
        water |= geo.coast.signed_distance(lon, lat) > 0.0
    if geo.waterway is not None:
        river_km, _ = G.polyline_metrics(lon, lat, geo.waterway[2])
        water |= river_km <= 0.30
    land_use = np.where(water, "water", land_use)
    return land_use


def assign_coherence(
    case: CaseSpec,
    geo: G.CaseGeography,
    land_use: np.ndarray,
    rho: np.ndarray,
    lon: np.ndarray,
    lat: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Modelled coherence in [0.05, 0.95] *before* the quality filter.

    High in the dense core, low over water, at the coverage edge and near the coast. The low clip
    is deliberately far below ``contracts.SENSOR_PROFILES["mock"].coherence_min``: clipping *up*
    to the threshold would make ``build_case``'s coherence filter a no-op (every cell would already
    pass it), which is exactly the defect this function used to have. Decorrelation is modelled
    first and filtered second, so the filter — a headline UI feature — actually removes cells.
    """
    base = np.full(lon.size, 0.80)
    land_use_bonus = {
        "urban-dense": 0.10,
        "urban-residential": 0.04,
        "infrastructure": 0.02,
        "industrial": -0.02,
        "bare": -0.10,
        "vegetation": -0.04,
        "water": -0.30,
        "unknown": 0.0,
    }
    for label, bonus in land_use_bonus.items():
        base = np.where(land_use == label, base + bonus, base)

    # Coverage edge: the outer 20 % of the ellipse loses up to 0.20 (fewer looks, more layover).
    base -= 0.20 * _smoothstep(0.80, 1.0, rho)
    # Dense core: extra looks and stable scatterers.
    base += 0.04 * (1.0 - _smoothstep(0.0, 0.38, rho))
    # Water/coast proximity: specular surfaces decorrelate.
    if geo.coast is not None:
        distance = geo.coast.signed_distance(lon, lat)
        base -= 0.18 * np.exp(-np.maximum(distance, 0.0) / 2.5) * (distance > -6.0)
    base += rng.normal(0.0, 0.025, lon.size)
    base = np.clip(base, 0.05, 0.95)  # low clip only: the filter below, not this clip, decides

    # Water must not survive the quality filter. A specular surface has no usable phase, and
    # publishing "ocean with subsidence" is exactly what a judge is entitled to disbelieve. Force
    # water below the frozen coherence_min so the grid drops those cells (they become absent, per
    # the contract's nodata rule) instead of carrying a displacement.
    water = land_use == "water"
    if water.any():
        threshold = C.SENSOR_PROFILES["mock"].coherence_min
        base = np.where(water, threshold * (0.35 + 0.55 * rng.random(lon.size)), base)
    return base


def build_case(case: CaseSpec, study: StudyCases, seed: int) -> CaseModel:
    """Synthesise one case: cells, fields, series and fitted trend (fully deterministic)."""
    rng = _case_rng(seed, case.case_id)
    cell_ids, lat, lon = expected_cells(case, study.h3_resolution)
    ellipse = case.field.coverage_ellipse
    _, _, rho = G.ellipse_rho(
        lon,
        lat,
        ellipse.center_lon,
        ellipse.center_lat,
        ellipse.semi_axes_km[0],
        ellipse.semi_axes_km[1],
        ellipse.rotation_deg,
    )
    geo = G.GEOGRAPHY[case.case_id]

    smooth, _ = velocity_field(case, lat, lon)
    secular = smooth + rng.normal(0.0, case.field.noise_mm_yr, lon.size)

    land_use = assign_land_use(case, geo, lon, lat, rho, rng)
    coherence = assign_coherence(case, geo, land_use, rho, lon, lat, rng)

    # The pre-filter cell set and its modelled coherence are kept on the model: the post-write gate
    # rebuilds the model from the published seed and must reproduce this cell set *exactly*. That is
    # what turns "the filter dropped some cells" into an equation instead of a tolerance.
    prefilter_cell_id = cell_ids.copy()
    prefilter_coherence = coherence.copy()

    # Mirror the pipeline's quality filter: cells below the frozen coherence_min are dropped, not
    # published with a displacement. This is what makes "water has no data" actually true in the
    # artefact, rather than true only in prose.
    keep = coherence >= C.SENSOR_PROFILES["mock"].coherence_min
    if not keep.all():
        cell_ids, lat, lon, rho = cell_ids[keep], lat[keep], lon[keep], rho[keep]
        land_use, coherence, secular = land_use[keep], coherence[keep], secular[keep]

    series = cumulative_series(case, study.dates, secular)
    order = seasonal_order(case, study.dates)
    fits, t_years = fit_series(study.dates, series, order)
    trend = np.array([fit.trend_mm_yr for fit in fits], dtype=float)
    amplitude = np.array([fit.seasonal_amp_mm for fit in fits], dtype=float)
    # `ratio_R` is undefined where nothing was fitted (amplitude exactly 0): publish NaN there,
    # never inf and never a sentinel. `artifacts.build_meta` turns it into JSON null and
    # `contracts.validate_grid` accepts the null exactly where the amplitude is 0 — the same
    # `np.divide(where=...)` the real chain uses in pipeline/process_case.py.
    ratio = np.full(trend.shape, np.nan, dtype=float)
    np.divide(np.abs(trend), amplitude, out=ratio, where=amplitude > 0.0)
    regime = np.array([fit.regime for fit in fits], dtype=object)
    seasonal = np.vstack([V.seasonal_component_mm(t_years, fit) for fit in fits])

    val_t1 = series[:, 0].copy()
    val_t2 = series[:, -1].copy()
    delta = val_t2 - val_t1
    risk = np.array([C.risk_level_from_velocity(v) for v in trend], dtype=object)

    return CaseModel(
        case=case,
        seed=int(seed),
        cell_id=cell_ids.astype(str),
        lat=lat,
        lon=lon,
        rho=rho,
        prefilter_cell_id=prefilter_cell_id.astype(str),
        prefilter_coherence=prefilter_coherence,
        secular_velocity=secular,
        velocity_mm_yr=trend,
        coherence=coherence,
        land_use=land_use,
        risk_level=risk,
        val_t1=val_t1,
        val_t2=val_t2,
        delta_change=delta,
        series=series,
        seasonal_mm=seasonal,
        seasonal_amp_mm=amplitude,
        ratio_R=ratio,
        regime=regime,
        n_harmonics=int(fits[0].n_harmonics),
        annual_coverage=float(fits[0].annual_coverage),
        design_condition=float(fits[0].design_condition),
        dates=tuple(study.dates),
    )


# ---------------------------------------------------------------------------------------------
# Frozen artefact frames. Column order == GRID_FIELDS order (the contract's own declaration).
# ---------------------------------------------------------------------------------------------


def grid_frame(model: CaseModel, sensor: str, product: str, source: str, provenance: str) -> pd.DataFrame:
    n = model.n_cells
    frame = pd.DataFrame(
        {
            "cell_id": model.cell_id.astype(str),
            "lat": model.lat.astype("float64"),
            "lon": model.lon.astype("float64"),
            "val_t1": model.val_t1.astype("float64"),
            "val_t2": model.val_t2.astype("float64"),
            "delta_change": model.delta_change.astype("float64"),
            "velocity_mm_yr": model.velocity_mm_yr.astype("float64"),
            "seasonal_amp_mm": model.seasonal_amp_mm.astype("float64"),
            "ratio_R": model.ratio_R.astype("float64"),
            "regime": model.regime.astype(str),
            "coherence": model.coherence.astype("float64"),
            "land_use": model.land_use.astype(str),
            "risk_level": model.risk_level.astype(str),
            "case_id": np.full(n, model.case.case_id, dtype=object),
            "sensor": np.full(n, sensor, dtype=object),
            "product": np.full(n, product, dtype=object),
            "unit": np.full(n, C.UNIT, dtype=object),
            "velocity_unit": np.full(n, C.VELOCITY_UNIT, dtype=object),
            "source": np.full(n, source, dtype=object),
            "acquisition_date": np.full(n, model.dates[-1], dtype=object),
            "provenance_id": np.full(n, provenance, dtype=object),
        }
    )
    return frame[[field.name for field in C.GRID_FIELDS]]


def aoi_series_frame(model: CaseModel, sensor: str, product: str, provenance: str) -> pd.DataFrame:
    """Mandatory aggregate rows: one per date, `cell_id == "aoi"`.

    `value` is the mean cumulative displacement of every cell present at that date, so the first
    and last AOI values equal `mean(val_t1)` / `mean(val_t2)` of the parquet exactly. `quality`
    comes from the mean coherence of those same cells.

    `trend_mm_yr` / `seasonal_mm` are the **fitted** secular trend and seasonal component of that
    same aggregate: least squares is linear in ``y`` and every mock cell shares one design
    (identical dates, no NaN), so the mean of the per-cell fits *is* the fit of the mean series —
    the AOI row and the drill-down rows can never disagree about the trend/seasonal split.
    """
    n_dates = len(model.dates)
    quality = quality_from_coherence(float(np.mean(model.coherence)))
    return pd.DataFrame(
        {
            "case_id": np.full(n_dates, model.case.case_id, dtype=object),
            "cell_id": np.full(n_dates, "aoi", dtype=object),
            "date": np.array(model.dates, dtype=object),
            "value": model.series.mean(axis=0).astype("float64"),
            "trend_mm_yr": np.full(n_dates, model.velocity_mm_yr.mean(), dtype="float64"),
            "seasonal_mm": model.seasonal_mm.mean(axis=0).astype("float64"),
            "unit": np.full(n_dates, C.UNIT, dtype=object),
            "sensor": np.full(n_dates, sensor, dtype=object),
            "product": np.full(n_dates, product, dtype=object),
            "quality": np.full(n_dates, quality, dtype=object),
            "provenance_id": np.full(n_dates, provenance, dtype=object),
        }
    )


def per_cell_series_frame(
    model: CaseModel,
    selected: np.ndarray,
    sensor: str,
    product: str,
    provenance: str,
    coherence_min: float,
) -> pd.DataFrame:
    """Optional drill-down rows: `n_selected x n_dates`, ordered by (cell_id, date)."""
    mean_coherence = float(np.mean(model.coherence[selected])) if selected.any() else 0.0
    quality = quality_from_coherence(mean_coherence)
    n_dates = len(model.dates)
    cell_rows = np.repeat(selected, n_dates)
    date_rows = np.tile(np.arange(n_dates), selected.size)
    return pd.DataFrame(
        {
            "case_id": np.full(cell_rows.size, model.case.case_id, dtype=object),
            "cell_id": model.cell_id[cell_rows].astype(str),
            "date": np.array(model.dates, dtype=object)[date_rows],
            "value": model.series[cell_rows, date_rows].astype("float64"),
            "trend_mm_yr": model.velocity_mm_yr[cell_rows].astype("float64"),
            "seasonal_mm": model.seasonal_mm[cell_rows, date_rows].astype("float64"),
            "unit": np.full(cell_rows.size, C.UNIT, dtype=object),
            "sensor": np.full(cell_rows.size, sensor, dtype=object),
            "product": np.full(cell_rows.size, product, dtype=object),
            "quality": np.full(cell_rows.size, quality, dtype=object),
            "provenance_id": np.full(cell_rows.size, provenance, dtype=object),
        }
    )


def quality_from_coherence(coherence: float) -> str:
    """Map a cell/AOI coherence to the frozen `quality` enum.

    The bands are the ones the code below cuts at: ``good >= 0.70``, ``fair >= 0.50``, otherwise
    ``poor``. They are *quality labels*, not the ``thresholds.coherence_min`` (0.35) gate — that
    one decides whether a cell is published at all, and it is applied before this function is used.
    """
    if coherence >= 0.70:
        return "good"
    if coherence >= 0.50:
        return "fair"
    return "poor"


def select_per_cell_targets(model: CaseModel, coherence_min: float, limit: int) -> np.ndarray:
    """Rank cells for drill-down: most negative/positive |velocity| first, deterministic ties."""
    eligible = np.flatnonzero(model.coherence >= coherence_min)
    order = np.lexsort((model.cell_id[eligible], -np.abs(model.velocity_mm_yr[eligible])))
    return eligible[order[:limit]]


def cell_polygon(cell_id: str) -> Polygon:
    boundary = h3.cell_to_boundary(cell_id)
    return Polygon([(lon, lat) for lat, lon in boundary])


def land_use_geometries(model: CaseModel) -> dict[str, shapely.Geometry]:
    """Dissolve the *same* per-cell land_use assignment into one geometry per class.

    Contract §6: the land_use layer and the grid column must come from one assignment, not two.
    Here there is literally one array: the union of the hexagons of each class.
    """
    by_class: dict[str, list[Polygon]] = {}
    for cell, label in zip(model.cell_id, model.land_use, strict=True):
        by_class.setdefault(str(label), []).append(cell_polygon(str(cell)))
    geometries: dict[str, shapely.Geometry] = {}
    for label, polygons in sorted(by_class.items()):
        merged = shapely.union_all(polygons)
        merged = shapely.set_precision(merged, CONTEXT_PRECISION_DEG, mode="valid_output")
        merged = merged.simplify(CONTEXT_SIMPLIFY_DEG, preserve_topology=True)
        merged = shapely.set_precision(merged, CONTEXT_PRECISION_DEG, mode="valid_output")
        if not merged.is_empty:
            geometries[label] = merged
    return geometries


def land_use_summary(model: CaseModel) -> str:
    """Human-readable class counts, for the run log (not an artefact field)."""
    labels, counts = np.unique(model.land_use, return_counts=True)
    return ", ".join(f"{int(c)}x {label}" for label, c in zip(labels, counts, strict=True))
