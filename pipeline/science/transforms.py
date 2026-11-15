"""Pure numeric transforms for the offline InSAR pipeline.

Every formula here is traceable to a section of ``README.md`` (the science pipeline, §6) or to the
frozen contract ``src/domain/contracts.py`` / ``docs/data-contract-v1.md``. Nothing in this module
touches the filesystem, the network, a credential or a clock: it is the part of Phase B that can be
written and tested before a single granule exists.

Conventions shared by the whole module
--------------------------------------
* Displacement is **mm**, velocity **mm/yr**, volume **m³/yr** (``contracts.UNIT`` /
  ``contracts.VELOCITY_UNIT``). Angles are degrees at the API boundary, radians internally.
* Time is years on a 365.25-day year, the same constant ``pipeline/mock`` uses.
* ``np.nan`` means *no data*. Functions that cannot produce a number return NaN rather than
  clamping to a plausible-looking value; the caller drops those cells (contract §8: a cell without
  data is absent from the grid, never written as 0).
* Up, east and north are positive. Subsidence is therefore negative.

Identifiability rule for the seasonal term (the one judgement call in this module)
--------------------------------------------------------------------------------
A **seasonal amplitude is only publishable when the dates span a substantial fraction of the
annual cycle.** In this module harmonic ``k`` of period ``T`` needs an observed span of at least
``MIN_SEASONAL_COVERAGE_PER_HARMONIC × k × T`` — 0.75 yr for the annual term, 1.5 yr for the
second harmonic — *and* the column-normalised design matrix must condition below
``MAX_DESIGN_CONDITION``. Below those gates the annual pair is nearly collinear with ``[1, t]``:
the amplitude is then a fitted parameter, not a measurement, and ``r2 ≈ 0.95`` (a good-looking
fit) says nothing about it. The trend survives the same window far better, so
:func:`harmonic_fit` **warns instead of raising**: it returns ``trend_mm_yr`` as usual and sets
``seasonal_identifiable=False`` with the reason in ``HarmonicFit.warnings``.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Sequence

import h3
import numpy as np

from src.domain import contracts as C

if TYPE_CHECKING:  # pandas is imported lazily: see h3_aggregate
    import pandas as pd

# ---------------------------------------------------------------------------------------------
# Constants. They are named and documented because the rest of the module quotes them.
# ---------------------------------------------------------------------------------------------

#: Days per Julian year. Same value as ``pipeline/mock/model.py`` so both sides of the pipeline
#: convert dates to years identically.
DAYS_PER_YEAR = 365.25

#: Period of the annual harmonic in years: ``365.25 d / 365.25 d·yr⁻¹``. README §6 writes the
#: seasonal term as ``cos(2πkt/T)``; T = 1 yr is the only period the three study cases justify
#: (12 acquisitions over ~4 months cannot resolve a 6-month cycle — do not add one).
SEASONAL_PERIOD_YEARS = 1.0

#: Median absolute deviation → Gaussian sigma. 1/Φ⁻¹(0.75) = 1.482602218505602.
MAD_TO_SIGMA = 1.4826

#: Regime split of README §6, ``R = |ṫ| / A_seasonal``: above 2 the secular term dominates
#: (permanent, non-recoverable), below 1 the seasonal term dominates (recoverable).
REGIME_INELASTIC_RATIO = 2.0
REGIME_ELASTIC_RATIO = 1.0

#: Minimum number of finite samples any harmonic fit needs. See :func:`harmonic_fit`.
MIN_FIT_POINTS = 4

#: Fraction of its own cycle that harmonic ``k`` must be observed over before its amplitude is
#: publishable: the annual term needs 0.75 yr of data, the second harmonic 1.5 yr. See
#: :func:`harmonic_fit` for the ``132 d / 12 dates`` stack that motivates it.
MIN_SEASONAL_COVERAGE_PER_HARMONIC = 0.75

#: Ceiling on the condition number of the **column-normalised** design matrix. Well-identified
#: designs measure ~4–12 at the coverage gates above; the degenerate cases this module refuses to
#: publish measure 67 (132 d, one harmonic), 1.0e3 (132 d, two harmonics) and 5.5e4 (dates
#: clustered into five weeks). 30 sits in that empty band with a ≥2.5× margin over every blessed
#: design, and it is the backstop for irregular sampling that passes the coverage gate but is still
#: degenerate. See :func:`harmonic_fit`.
MAX_DESIGN_CONDITION = 30.0

# ---------------------------------------------------------------------------------------------
# P5 — LOS projection (README §6, "LOS projection")
# ---------------------------------------------------------------------------------------------


def _geometry_radians(incidence_deg: object, heading_deg: object) -> tuple[np.ndarray, np.ndarray]:
    """Validate an incidence/heading pair and return it in radians, broadcast together."""
    theta = np.asarray(incidence_deg, dtype=float)
    alpha = np.asarray(heading_deg, dtype=float)
    if not np.all(np.isfinite(theta)) or not np.all(np.isfinite(alpha)):
        raise ValueError("incidence_deg and heading_deg must be finite (NaN cannot define a look)")
    if np.any(theta < 0.0) or np.any(theta >= 90.0):
        raise ValueError(
            "incidence_deg must be in [0, 90): 0 is nadir and 90 is the tangent plane, where the "
            f"vertical component cos(theta) vanishes and the inversion is undefined; got {theta}"
        )
    theta, alpha = np.broadcast_arrays(theta, alpha)
    return np.radians(theta), np.radians(alpha)


def _unit_vectors(theta_rad: np.ndarray, alpha_rad: np.ndarray) -> np.ndarray:
    """Stack ``[cosθ, −sinθ·cosα, −sinθ·sinα]`` on the last axis (README §6, frozen formula)."""
    return np.stack(
        [
            np.cos(theta_rad),
            -np.sin(theta_rad) * np.cos(alpha_rad),
            -np.sin(theta_rad) * np.sin(alpha_rad),
        ],
        axis=-1,
    )


def los_unit_vector(incidence_deg: float, heading_deg: float) -> np.ndarray:
    """LOS projection unit vector ``u = [u_up, u_east, u_north]`` (README §6).

    Formula::

        u = [cosθ, −sinθ·cosα, −sinθ·sinα],      |u| = 1

    ``θ`` is the incidence angle at the target and ``α`` the heading, in degrees clockwise from
    north. The vector is returned on the last axis, so scalars give ``shape (3,)`` and an array of
    ``n`` geometries gives ``shape (n, 3)``.

    Sign convention chosen by this module (see :func:`los_to_vertical` for why it must be validated
    against GNSS): up/east/north are positive, and ``u_up = cosθ > 0``, so a positive LOS value is
    positive vertical motion when the horizontal components are zero.
    """
    theta, alpha = _geometry_radians(incidence_deg, heading_deg)
    return _unit_vectors(theta, alpha)


def project_to_los(
    vertical_mm: object,
    incidence_deg: object,
    heading_deg: object,
    v_east: object = 0.0,
    v_north: object = 0.0,
) -> np.ndarray:
    """Forward projection: ground motion → ``d_LOS`` (README §6).

    Formula::

        d_LOS = u · [v_up, v_east, v_north]ᵀ
              = cosθ·v_up − sinθ·cosα·v_east − sinθ·sinα·v_north

    Inputs broadcast against each other; units are mm (or mm/yr: the projection is linear, so a
    velocity projects exactly like a displacement). See :func:`los_to_vertical` for the sign
    convention and the inverse.

    **Sign of the result: toward-sensor-positive.** ``u_up = cosθ > 0``, so with no horizontal
    motion a positive (upward) vertical motion gives a positive ``d_LOS``: the axis ``u`` points
    from the target toward the satellite, uplift is positive and subsidence negative. That is the
    convention `pipeline/process_case.py` labels ``toward-sensor``, and it is the default there
    (O-08: the label was renamed to match this arithmetic, the arithmetic was not flipped). The
    ``away-from-sensor`` label is the same numbers negated.
    """
    theta, alpha = _geometry_radians(incidence_deg, heading_deg)
    u = _unit_vectors(theta, alpha)
    up, east, north = np.broadcast_arrays(
        np.asarray(vertical_mm, dtype=float),
        np.asarray(v_east, dtype=float),
        np.asarray(v_north, dtype=float),
    )
    return u[..., 0] * up + u[..., 1] * east + u[..., 2] * north


def los_to_vertical(
    los_mm: object,
    incidence_deg: object,
    heading_deg: object,
    v_east: object = 0.0,
    v_north: object = 0.0,
) -> np.ndarray:
    """Invert the LOS projection: solve README §6 for ``v_up`` given trusted horizontals.

    Formula (inverse of :func:`project_to_los`, README §6)::

        v_up = (d_LOS − u_east·v_east − u_north·v_north) / u_up
             = (d_LOS + sinθ·cosα·v_east + sinθ·sinα·v_north) / cosθ
             = d_LOS / cosθ + tanθ·(cosα·v_east + sinα·v_north)

    ``v_east``/``v_north`` are *inputs*, not estimates: a single LOS geometry gives one equation
    for three unknowns, so a vertical estimate is only as good as the horizontals you believe.
    The sensitivity is ``sinθ·cosα`` on east and ``sinθ·sinα`` on north, so the omitted-north error
    is exactly ``tanθ·sinα·v_north`` — for θ = 39°, α = 192° that is ``−0.17·v_north`` while the
    east term reaches ``−0.79·v_east``; neither is negligible, which is why README §6 requires
    ``v_north ≈ 0`` to be justified (or the north component to come from GNSS), and why a
    two-geometry asc/desc stack is preferred over a single pass.

    **Sign convention.** Units mm, up/east/north positive, ``u = [cosθ, −sinθ·cosα, −sinθ·sinα]``
    from README §6 with ``heading_deg`` passed through verbatim: this module applies **no**
    sensor-specific remapping. Positive ``d_LOS`` under this frozen ``u`` is motion **toward** the
    sensor (``u_up = cosθ > 0``), which is why `pipeline/process_case.py` labels the phase
    conversion that reproduces it ``toward-sensor`` and makes that the default. The products still
    disagree, which is why the label is an input there, not a constant:

    * ISCE2/GUNW ``heading`` is the platform heading; MintPy's ``incidenceAngle``/``heading``
      grids and SNAP's metadata use different azimuth origins, and the sign of "LOS positive" is
      toward-the-sensor in some products and away-from-it in others (README §6 says so explicitly).
    * Under the frozen formula the horizontal part of ``u`` has magnitude ``sinθ`` and points at
      azimuth ``α + 180°``. For a right-looking acquisition with platform heading ``α_f`` the
      target→satellite unit vector is reproduced by passing ``α = 180° − α_f`` instead of ``α_f``;
      the two differ only through the horizontal terms (``u_up = cosθ`` either way), which is
      exactly the ambiguity that a single geometry cannot reveal.

    **Validation before publishing** (README §13, "Velocity accuracy: GNSS / levelling; target
    < 5 mm/yr RMSE"): pick a station with a known vertical rate, run the same
    product → LOS → vertical chain, flip the LOS sign and/or the heading convention until the
    residual is unbiased, and record the chosen convention in ``meta.provenance.method``. Never
    publish a number from this function whose convention has not been checked that way.
    """
    theta, alpha = _geometry_radians(incidence_deg, heading_deg)
    u = _unit_vectors(theta, alpha)
    east = np.asarray(v_east, dtype=float)
    north = np.asarray(v_north, dtype=float)
    los = np.asarray(los_mm, dtype=float)
    return (los - u[..., 1] * east - u[..., 2] * north) / u[..., 0]


def vertical_from_los(
    los_mm: object,
    incidence_deg: object,
    heading_deg: object,
    assume_vnorth: object = 0.0,
    v_east: object = 0.0,
) -> np.ndarray:
    """P5 entry point: LOS → vertical under the README §6 ``v_north ≈ 0`` assumption.

    Same algebra as :func:`los_to_vertical` (``= d_LOS/cosθ + tanθ·(cosα·v_east + sinα·v_north)``),
    but the north component is named ``assume_vnorth`` on purpose: it is an **assumption**, not a
    measurement, and the parameter name is the last place to notice that before the number ships.
    ``assume_vnorth`` may be a scalar (one assumption for the whole scene, the normal case) or an
    array from a GNSS model. ``v_east`` is kept as a free input because the east sensitivity
    ``sinθ·cosα`` reaches ±sinθ (±0.63 at θ = 39°), i.e. it does **not** cancel.

    The residual error of the assumption is ``tanθ·sinα·(v_north − assume_vnorth)``; publish it in
    ``meta.limitations`` when the assumed value is not backed by GNSS (README §13).
    """
    return los_to_vertical(
        los_mm, incidence_deg, heading_deg, v_east=v_east, v_north=assume_vnorth
    )


# ---------------------------------------------------------------------------------------------
# P6 — trend + seasonal split (README §6, "Time-series model")
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class HarmonicFit:
    """Result of :func:`harmonic_fit` (README §6, "Time-series model").

    ``eq=False``: the coefficient arrays make field-wise equality ambiguous, so instances compare
    by identity. Compare fields, e.g. ``fit.trend_mm_yr == pytest.approx(expected)``.
    """

    #: Secular trend ``ṫ`` — the number that becomes ``velocity_mm_yr`` in the contract.
    trend_mm_yr: float
    #: Amplitude of the **first** harmonic, ``A_seasonal = hypot(A₁, B₁)``, mm.
    seasonal_amp_mm: float
    #: Coefficient of determination of the joint fit; 1.0 for a noise-free series.
    r2: float
    #: One of ``contracts.REGIMES``: ``"inelastic"`` | ``"transitional"`` | ``"elastic"``. It is
    #: forced to ``"transitional"`` when ``seasonal_identifiable`` is ``False``: the
    #: ``R = |ṫ| / A_seasonal`` split is meaningless without a measured amplitude.
    regime: str
    #: Raw least-squares coefficients ``[v₀, ṫ, A₁, B₁, A₂, B₂, ...]``, shape ``(2 + 2K,)``.
    coefficients: np.ndarray
    #: ``coefficients[0]``, the displacement at ``t = 0`` (the reference epoch), mm.
    intercept_mm: float
    #: Amplitude of every harmonic, shape ``(K,)``; ``[0]`` is ``seasonal_amp_mm``.
    seasonal_amplitudes_mm: np.ndarray = field(default_factory=lambda: np.empty(0))
    #: Number of finite samples actually used (NaNs dropped before the fit).
    n_points: int = 0
    #: Period of the first harmonic in years (1.0 = annual).
    period_years: float = SEASONAL_PERIOD_YEARS
    #: Number of harmonics in the model.
    n_harmonics: int = 0
    #: Condition number of the column-normalised design matrix (diagnostic, not a gate by itself).
    design_condition: float = float("nan")
    #: Fraction of the annual cycle the dates span, ``(t_max − t_min)/period_years``.
    annual_coverage: float = float("nan")
    #: ``False`` when the seasonal term is not identifiable over this span: ``seasonal_amp_mm``
    #: then exists but **must not be published** (``trend_mm_yr`` is unaffected). See
    #: :func:`harmonic_fit` and ``MIN_SEASONAL_COVERAGE_PER_HARMONIC``.
    seasonal_identifiable: bool = True
    #: Reasons ``seasonal_identifiable`` is ``False``; empty when the seasonal term is publishable.
    warnings: tuple[str, ...] = ()

    @property
    def regime_ratio(self) -> float:
        """``R = |ṫ| / A_seasonal`` from README §6; ``inf`` when there is no seasonal signal."""
        if self.seasonal_amp_mm <= 0.0:
            return float("inf") if self.trend_mm_yr != 0.0 else 0.0
        return abs(self.trend_mm_yr) / self.seasonal_amp_mm


def _harmonic_design(t_years: np.ndarray, period_years: float, n_harmonics: int) -> np.ndarray:
    """Design matrix ``[1, t, cos(2πt/T), sin(2πt/T), ..., cos(2πKt/T), sin(2πKt/T)]``."""
    columns = [np.ones_like(t_years), t_years]
    for k in range(1, n_harmonics + 1):
        omega = 2.0 * np.pi * k / period_years
        columns.append(np.cos(omega * t_years))
        columns.append(np.sin(omega * t_years))
    return np.column_stack(columns)


def _design_condition(design: np.ndarray) -> float:
    """Condition number of the **column-normalised** design matrix.

    Without normalisation the number would mostly measure the unit mismatch between the ``1`` and
    ``t`` columns (mm vs mm/yr) instead of the collinearity that matters. Normalising each column
    to unit 2-norm makes the condition number the ratio of the extreme singular values of the
    correlation-like matrix, i.e. how badly the worst-identified linear combination of the basis
    amplifies noise relative to an orthogonal design. A zero column can only come from a
    rank-deficient design, which :func:`harmonic_fit` rejects before calling this.
    """
    norms = np.linalg.norm(design, axis=0)
    scaled = design / np.where(norms > 0.0, norms, 1.0)
    return float(np.linalg.cond(scaled))


def _seasonal_identifiability(
    t_years: np.ndarray, period_years: float, n_harmonics: int, condition: float
) -> tuple[float, bool, tuple[str, ...]]:
    """Apply the publication gate: ``(annual_coverage, identifiable, warnings)``.

    Two independent tests, both documented in the module docstring:

    * **coverage** — harmonic ``k`` needs ``MIN_SEASONAL_COVERAGE_PER_HARMONIC·k`` cycles of data
      (0.75 yr for the annual term, 1.5 yr for the second). This is the primary gate: an annual
      sinusoid observed over a third of its period is not separable from ``[1, t]``, whatever the
      number of dates and whatever ``r2`` says.
    * **conditioning** — the column-normalised design must stay below ``MAX_DESIGN_CONDITION``.
      This catches irregular sampling that spans enough time but clusters the dates (a 2-year span
      with 11 of 12 dates inside five weeks conditions at 5.5e4).

    The trend is not gated: it is identified by the span itself (its standard error on 132 d of
    12 dates is ~37 mm/yr per mm of noise with one harmonic versus ~518 mm/yr with two), so a
    caller may always publish ``trend_mm_yr`` and must only withhold the amplitude.
    """
    coverage = float(np.ptp(t_years)) / float(period_years)
    if n_harmonics == 0:  # nothing seasonal is fitted, so nothing seasonal can be over-claimed
        return coverage, True, ()

    messages: list[str] = []
    for k in range(1, n_harmonics + 1):
        needed = MIN_SEASONAL_COVERAGE_PER_HARMONIC * k
        if coverage < needed:
            messages.append(
                f"seasonal harmonic {k} (period {float(period_years) / k:g} yr) is not "
                f"identifiable: the dates span {coverage:.2f} annual cycles but harmonic {k} "
                f"needs at least {needed:.2f}; do not publish seasonal_amp_mm "
                "(trend_mm_yr remains usable)"
            )
    if condition > MAX_DESIGN_CONDITION:
        messages.append(
            f"design condition number {condition:.3g} exceeds the limit "
            f"{MAX_DESIGN_CONDITION:g}: the harmonic columns are nearly collinear with [1, t] "
            f"over these {coverage:.2f} cycles, so do not publish seasonal_amp_mm "
            "(trend_mm_yr remains usable)"
        )
    return coverage, not messages, tuple(messages)


def classify_regime(trend_mm_yr: float, seasonal_amp_mm: float) -> str:
    """Map ``(ṫ, A_seasonal)`` onto ``contracts.REGIMES`` using ``R = |ṫ| / A_seasonal``.

    README §6: ``ṫ`` → inelastic/permanent, ``A_seasonal`` → elastic/recoverable. The bands are::

        R > 2                    → "inelastic"    (secular dominates: permanent, non-recoverable)
        1 ≤ R ≤ 2                → "transitional" (neither term dominates: mixed regime)
        R < 1                    → "elastic"      (seasonal dominates: recoverable)

    The ``R > 2`` cut is the one fixed by the task and README §6; the ``R < 1`` cut is this
    module's documented choice for the third enum member, which the contract requires to exist but
    README §6 does not define. Strict inequalities: ``R == 2`` is *not* inelastic, ``R == 1`` is
    *not* elastic. A fit with no seasonal signal (``A_seasonal == 0``) is inelastic when the trend
    is non-zero (``R = ∞``) and ``"transitional"`` when the series is flat, because "nothing moves"
    carries no regime information and inventing ``"elastic"`` there would assert recoverability.
    """
    trend = float(trend_mm_yr)
    amp = float(seasonal_amp_mm)
    if not np.isfinite(trend) or not np.isfinite(amp):
        return "transitional"
    if amp <= 0.0:
        return "inelastic" if trend != 0.0 else "transitional"
    ratio = abs(trend) / amp
    if ratio > REGIME_INELASTIC_RATIO:
        return "inelastic"
    if ratio >= REGIME_ELASTIC_RATIO:
        return "transitional"
    return "elastic"


def harmonic_order(n_epochs: int, span_years: float) -> int:
    """How many annual harmonics a stack of this span can honestly carry (README §6).

    The order is chosen so that **whatever it asks for is publishable**: harmonic ``k`` needs
    ``MIN_SEASONAL_COVERAGE_PER_HARMONIC * k`` cycles of data *and* ``2 + 2k`` epochs, because
    fewer rows than ``[1, t, cos, sin, ...]`` columns would make ``numpy.linalg.lstsq`` return a
    minimum-norm solution (:func:`harmonic_fit` refuses that itself). That gives::

        span >= 1.50 yr and n >= 6  ->  2 harmonics
        span >= 0.75 yr and n >= 4  ->  1 harmonic
        otherwise                   ->  0 (pure linear model: no seasonal term is fitted)

    The two spans are the coverage gates of :func:`_seasonal_identifiability` written as
    ``MIN_SEASONAL_COVERAGE_PER_HARMONIC * k``, not as magic numbers. Asking for a harmonic the
    span cannot identify would set ``seasonal_identifiable=False`` on every fit, and since
    ``contracts`` 1.1 makes ``seasonal_amp_mm`` a required column there is no honest value to put
    in it — so the order is capped at what the span supports.

    ``0`` is a statement about the **model**, not about the ground: the series then has no seasonal
    basis at all and the fitted ``seasonal_amp_mm`` is exactly ``0.0``. Since ``contracts`` 1.1 makes
    that column required, a *publisher* must not ship order ``0`` as "no seasonality": both
    ``pipeline/process_case.py`` and ``pipeline/mock/model.py`` treat the case as unpublishable
    (they refuse or raise) rather than publish an amplitude the gate cannot certify.
    """
    for k in (2, 1):
        if span_years >= MIN_SEASONAL_COVERAGE_PER_HARMONIC * k and n_epochs >= 2 + 2 * k:
            return k
    return 0


def seasonal_component_mm(t_years: object, fit: HarmonicFit) -> np.ndarray:
    """The fitted seasonal part of ``fit`` evaluated at ``t_years``, in mm.

    Formula (the seasonal half of the :func:`harmonic_fit` model, README §6)::

        s(t) = Σ_k [A_k·cos(2πkt/T) + B_k·sin(2πkt/T)],      k = 1..n_harmonics

    i.e. everything except the intercept ``v₀`` and the secular trend ``ṫ·t``. It is the column
    ``contracts.TIMESERIES_FIELDS`` calls ``seasonal_mm``, and it is computed from the fit's own
    coefficients so the published trend and seasonal components cannot drift apart from the fit
    that produced them (``value - trend_mm_yr·t - seasonal_mm`` is then constant, the intercept).

    An ``n_harmonics == 0`` fit has no seasonal basis, so the result is an array of zeros: the
    model contains no seasonal term, which is a different statement from "the seasonal signal is
    zero" — say which one is meant where the number is published.
    """
    t = np.asarray(t_years, dtype=float)
    component = np.zeros_like(t)
    for k in range(1, int(fit.n_harmonics) + 1):
        omega = 2.0 * np.pi * k / float(fit.period_years)
        component = component + fit.coefficients[2 + 2 * (k - 1)] * np.cos(
            omega * t
        ) + fit.coefficients[3 + 2 * (k - 1)] * np.sin(omega * t)
    return component


def harmonic_fit(
    t_years: object,
    displacement_mm: object,
    period_years: float = SEASONAL_PERIOD_YEARS,
    n_harmonics: int = 1,
) -> HarmonicFit:
    """Least-squares fit of the README §6 time-series model to one pixel.

    Model (README §6; the step terms ``Σ Δᵥⱼ·H(t−tⱼ)`` are *not* fitted — see the note below)::

        v(t) = v₀ + ṫ·t + Σₖ [Aₖ·cos(2πkt/T) + Bₖ·sin(2πkt/T)]

    solved as one linear least-squares problem on
    ``[1, t, cos(2πt/T), sin(2πt/T), cos(4πt/T), sin(4πt/T), ...]``. ``ṫ`` is the secular trend
    that enters the contract as ``velocity_mm_yr``; ``A_seasonal = hypot(A₁, B₁)`` is the
    recoverable part. Because the annual harmonic is *inside* the basis, a 12-day sampling of a
    subsiding city cannot label its seasonal swing as a trend — that collinearity is the whole
    point of step P6 (README §6).

    ``t_years`` and ``displacement_mm`` are 1-D and equally long; non-finite samples in either
    array are dropped (a missing date is not a zero displacement). A rank-deficient fit is refused
    rather than reported: with fewer rows than columns ``numpy.linalg.lstsq`` would return the
    minimum-norm solution and ``r2`` would be a meaningless 1.0. The minimum is therefore
    ``max(MIN_FIT_POINTS, n_columns)`` = 4 points for the default one-harmonic model (and 6 for
    ``n_harmonics=2``).

    Identifiability gate — when is ``seasonal_amp_mm`` publishable?
    --------------------------------------------------------------
    **Rule: a seasonal amplitude is only publishable when the data span a substantial fraction of
    the annual cycle.** Enforced thresholds (module constants, see
    :func:`_seasonal_identifiability`):

    * harmonic ``k`` requires ``span ≥ MIN_SEASONAL_COVERAGE_PER_HARMONIC · k · T``, i.e. **0.75 yr
      (274 d) for the annual term and 1.5 yr (548 d) for the second harmonic**;
    * the column-normalised design matrix must condition at or below ``MAX_DESIGN_CONDITION = 30``.

    **The default is ``n_harmonics=1``** for exactly this reason. Two annual harmonics span at most
    two cycles *if* the stack is two years long; over the 132-day / 12-date windows of the three
    study cases the 6-column basis conditions at ~1.0e3 and the trend standard error reaches
    ~518 mm/yr per mm of noise (measured: ±509 mm/yr scatter for a true −20 mm/yr with 1 mm/epoch
    noise), against ~37 mm/yr for one harmonic. A multi-year stack may ask for more harmonics; the
    gate below will still check that the span can carry them.

    **Behaviour chosen: warn, not raise.** The fit is returned with
    ``seasonal_identifiable=False`` and a human-readable entry in ``HarmonicFit.warnings`` naming
    the term that is not identifiable and stating that ``seasonal_amp_mm`` must not be published.
    ``trend_mm_yr`` stays usable, because the trend *is* identified by a 132-day span: raising
    would throw away the one number the contract actually needs (``velocity_mm_yr``) and would make
    the whole real stack abort per cell — ``pipeline/process_case.py`` catches ``ValueError`` and
    drops the cell, so an error here would silently empty the grid. Warning keeps the number and
    makes the unidentifiable part impossible to publish by accident. Per-check diagnostics are also
    exposed as ``design_condition`` and ``annual_coverage``.

    Consistent with that, ``regime`` is reported as ``"transitional"`` whenever
    ``seasonal_identifiable`` is ``False`` **or** ``n_harmonics == 0``. The README §6 rule
    ``R = |ṫ| / A_seasonal`` is **meaningless** when the denominator is not measurable: it would
    divide by a fitted artefact and could claim ``"elastic"`` ("recoverable movement") on a
    seasonal swing the data never resolved, and with ``n_harmonics == 0`` it would claim
    ``"inelastic"`` on the strength of a seasonal term the model does not even contain.
    ``classify_regime`` is still called (its output is what a caller gets whenever the amplitude is
    publishable), and ``regime_ratio`` still returns the raw ratio so a diagnostic can see what the
    rule *would* have said. ``"transitional"`` is this module's existing spelling for "the ratio
    carries no regime information" (see :func:`classify_regime`).

    The gate is per-term: a 132-day stack fitted with ``n_harmonics=2`` names *both* harmonics as
    not identifiable, and the fitted values are still returned so a diagnostic can compare them
    with truth — the flag, not a NaN, is what stops publication.

    ``r2`` is ``1 − SS_res/SS_tot`` on the retained samples; a constant series (``SS_tot == 0``)
    reports 1.0 when the fit is exact and NaN when it is not, because "fraction of variance
    explained" has no meaning when there is no variance. Note that ``r2`` is **not** an
    identifiability test: the 132-day two-harmonic fit above reports ``r2 ≈ 0.95`` and rank 6.

    Step detection (``Σ Δᵥⱼ·H(t−tⱼ)``) is deliberately out of scope: it needs a step-date search and
    the 12-date mock window cannot support one without overfitting. Add it with a real stack.
    """
    t = np.asarray(t_years, dtype=float)
    y = np.asarray(displacement_mm, dtype=float)
    if t.ndim != 1 or y.ndim != 1:
        raise ValueError(
            f"harmonic_fit takes 1-D series (one pixel); got t.ndim={t.ndim}, y.ndim={y.ndim}. "
            "Loop or np.apply_along_axis over cells — do not guess an axis."
        )
    if t.shape != y.shape:
        raise ValueError(f"t_years and displacement_mm differ in length: {t.shape} vs {y.shape}")
    if not np.isfinite(period_years) or period_years <= 0.0:
        raise ValueError(f"period_years must be finite and > 0; got {period_years!r}")
    if n_harmonics < 0:
        raise ValueError(f"n_harmonics must be >= 0; got {n_harmonics!r}")

    usable = np.isfinite(t) & np.isfinite(y)
    t_ok, y_ok = t[usable], y[usable]
    n_columns = 2 + 2 * int(n_harmonics)
    minimum = max(MIN_FIT_POINTS, n_columns)
    if t_ok.size < minimum:
        raise ValueError(
            f"harmonic_fit needs at least {minimum} finite samples for a "
            f"{n_columns}-column model (4 is the absolute floor, one per parameter otherwise); "
            f"got {t_ok.size} after dropping NaNs from {t.size}"
        )

    design = _harmonic_design(t_ok, float(period_years), int(n_harmonics))
    coefficients, _residuals, rank, _singular = np.linalg.lstsq(design, y_ok, rcond=None)
    if rank < n_columns:  # pragma: no cover - prevented by the size check above
        raise ValueError(
            f"harmonic_fit design matrix is rank deficient (rank {rank} < {n_columns}): the dates "
            "do not separate trend from seasonal terms"
        )

    residual = y_ok - design @ coefficients
    ss_res = float(residual @ residual)
    centred = y_ok - y_ok.mean()
    ss_tot = float(centred @ centred)
    if ss_tot > 0.0:
        r2 = 1.0 - ss_res / ss_tot
    else:
        r2 = 1.0 if ss_res <= 1e-12 else float("nan")

    harmonics = np.asarray(
        [float(np.hypot(coefficients[2 + 2 * k], coefficients[3 + 2 * k])) for k in range(n_harmonics)],
        dtype=float,
    )
    seasonal_amp = float(harmonics[0]) if harmonics.size else 0.0
    trend = float(coefficients[1])
    condition = _design_condition(design)
    coverage, identifiable, identifiability_warnings = _seasonal_identifiability(
        t_ok, float(period_years), int(n_harmonics), condition
    )
    regime = classify_regime(trend, seasonal_amp)
    if not identifiable or int(n_harmonics) == 0:
        # The |trend| > 2·amplitude rule needs a measured amplitude; without one, "elastic"
        # (recoverable) or "inelastic" (permanent) would be a claim about a number this span cannot
        # produce. That covers both cases: the design is unidentifiable, *and* a pure linear model
        # (n_harmonics = 0) where no seasonal term was fitted at all — the same R rule with an
        # amplitude of exactly 0 would otherwise report "inelastic" on the strength of a term that
        # does not exist. "transitional" is this module's documented spelling for "no regime
        # information".
        regime = "transitional"
    return HarmonicFit(
        trend_mm_yr=trend,
        seasonal_amp_mm=seasonal_amp,
        r2=float(r2),
        regime=regime,
        coefficients=coefficients,
        intercept_mm=float(coefficients[0]),
        seasonal_amplitudes_mm=harmonics,
        n_points=int(t_ok.size),
        period_years=float(period_years),
        n_harmonics=int(n_harmonics),
        design_condition=condition,
        annual_coverage=coverage,
        seasonal_identifiable=bool(identifiable),
        warnings=identifiability_warnings,
    )


# ---------------------------------------------------------------------------------------------
# P8 — coherence quality (README §6 step P8 filter; §13 limitation 6)
# ---------------------------------------------------------------------------------------------


def coherence_bias_correction(gamma_hat: object, looks: object) -> np.ndarray:
    """Debias a multi-look coherence estimate.

    Formula (inverse of the standard finite-look bias ``E[γ̂²] = γ² + (1−γ²)/L``)::

        γ_corr = sqrt((L·γ̂² − 1) / (L − 1)),      defined only for 1/√L < γ̂ ≤ 1

    ``γ̂`` is the sample coherence and ``L`` the number of looks used to form it (for a boxcar of
    ``L`` independent looks; for a GUNW product take the effective looks implied by the posting,
    not the raw window size). The correction is unbiased to first order in ``1/L``
    (``E[γ_corr²] ≈ γ²``); the residual bias is second order, so a near-perfect pixel can be
    slightly overcorrected — it is a point estimator, not a confidence interval. It always *lowers*
    the number, because ``γ̂`` is positively biased (``γ_corr < γ̂`` for every ``γ̂ < 1``): a
    coherence that went up after "correction" is a bug, not a feature.

    **Why NaN outside the domain instead of a clamp.** For ``γ̂ ≤ 1/√L`` the radicand is ≤ 0: the
    observed coherence is consistent with pure noise given ``L`` looks, so no real-valued unbiased
    estimate exists. Clamping to 0 would invent a *precise* "totally decorrelated" value where the
    data only says "below the resolution of this estimator", and clamping to ``1/√L`` would
    fabricate a measurement. Returning NaN keeps the distinction and lets the ordinary
    ``coherence_min`` filter (0.35 for NISAR GUNW, ``contracts.SENSOR_PROFILES``) drop the cell, so
    the cell is *absent* from the grid rather than written as a confident zero (contract §8).
    ``γ̂ > 1`` is likewise NaN: a coherence above 1 is a corrupt input, not a very good pixel.
    ``L ≤ 1`` is NaN throughout, since one look carries no coherence information.

    Never compare corrected values across sensors/products: the same number means different things
    for NISAR's 12-day and Sentinel-1's 6-day revisit (README §13, limitation 6).
    """
    coherent = np.asarray(gamma_hat, dtype=float)
    looks_array = np.asarray(looks, dtype=float)
    coherent, looks_array = np.broadcast_arrays(coherent, looks_array)
    result = np.full(coherent.shape, np.nan, dtype=float)

    usable = np.isfinite(coherent) & np.isfinite(looks_array) & (looks_array > 1.0)
    if not usable.any():
        return result
    with np.errstate(invalid="ignore", divide="ignore"):
        threshold = 1.0 / np.sqrt(looks_array)
        domain = usable & (coherent > threshold) & (coherent <= 1.0)
        radicand = (looks_array * coherent**2 - 1.0) / (looks_array - 1.0)
        result[domain] = np.sqrt(radicand[domain])
    return result


# ---------------------------------------------------------------------------------------------
# Change detection helpers (README §6 step P8; §13 limitation 5: coherence-loss, not thresholding)
# ---------------------------------------------------------------------------------------------


def robust_zscore(series: object, axis: int = 0) -> np.ndarray:
    """Per-pixel temporal z-score, robust to outliers: ``z = (x − median) / (1.4826·MAD)``.

    ``MAD = median(|x − median|)`` along ``axis``, default 0 = **time**, i.e. a ``(n_dates, ...)``
    cube. (``pipeline/mock``'s ``CaseModel.series`` is ``(n_cells, n_dates)``: pass ``axis=1`` for
    it.) ``1.4826`` makes the scale comparable with a Gaussian sigma (``MAD_TO_SIGMA``), so a
    ``|z| > 2.5`` gate is a statement about the pixel's own history rather than about the
    scene-wide variance — that is what makes it usable for change detection in a field whose
    amplitude varies by orders of magnitude between cities.

    A pixel whose ``MAD`` is 0 (constant history, or a single date) gets **NaN**, not ``inf``:
    dividing by an estimated scale of exactly zero is a statement that the scale is unidentifiable,
    and an infinite z-score would pass every threshold downstream. NaN inputs are ignored by the
    median (so one dropped date does not poison a whole series) and stay NaN in the output.
    """
    array = np.asarray(series, dtype=float)
    if array.size == 0:
        return np.full(array.shape, np.nan, dtype=float)
    with warnings.catch_warnings():
        # An all-NaN pixel legitimately has no median; numpy warns, we return NaN on purpose.
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(array, axis=axis, keepdims=True)
        mad = np.nanmedian(np.abs(array - median), axis=axis, keepdims=True)
    scale = MAD_TO_SIGMA * mad
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (array - median) / scale
    z[~np.isfinite(z)] = np.nan
    return z


_PERSISTENCE_RE = re.compile(r"^\s*(\d+)\s*of\s*(\d+)\s*$")


def _parse_persistence(persistence: tuple[int, int] | Sequence[int] | str) -> tuple[int, int]:
    """Accept ``(2, 3)`` or the contract's spelling ``"2of3"`` (``SensorProfile.persistence``)."""
    if isinstance(persistence, str):
        match = _PERSISTENCE_RE.match(persistence)
        if match is None:
            raise ValueError(
                f"persistence {persistence!r} is not a 'mofn' string such as '2of3' "
                "(that is the spelling used by contracts.SENSOR_PROFILES)"
            )
        rise, window = int(match.group(1)), int(match.group(2))
    else:
        try:
            rise, window = (int(v) for v in persistence)
        except (TypeError, ValueError):
            raise ValueError(
                f"persistence must be a (m, n) pair or 'mofn'; got {persistence!r}"
            ) from None
    if window < 1 or rise < 1 or rise > window:
        raise ValueError(f"persistence must satisfy 1 <= m <= n; got m={rise}, n={window}")
    return rise, window


def detect_change(
    flag: object,
    persistence: tuple[int, int] | Sequence[int] | str = (2, 3),
    axis: int = 0,
) -> np.ndarray:
    """Persistence filter: keep a date flagged only if it fires in ``m`` of ``n`` consecutive dates.

    Formula (README §6 step P8, "the main false-positive killer"): with ``f(t)`` the raw boolean
    detections along ``axis`` and ``P(s) = Σ_{k=s}^{s+n-1} f(k) ≥ m`` over window starts ``s``::

        keep(t) = f(t) ∧ ∃ s ∈ [t − n + 1, t] : P(s)

    i.e. a *detection* survives if it sits inside some persistent window. A single-date blip is
    rejected (no window that covers it reaches ``m``), while two hits two dates apart — ``{3, 5}``
    with 2-of-3 — both survive, because each is corroborated by the window ``[3, 4, 5]``. The
    result is always a **subset of ``flag``**: a filter may only remove detections, never invent
    one for a date where the raw detector saw nothing (dilating the passing windows instead would
    add quiet dates to the event and inflate any "number of change dates" metric). The frozen
    contract spells this ``persistence: "2of3"`` in every ``SENSOR_PROFILES`` entry, so
    ``persistence="2of3"`` is accepted alongside ``(2, 3)``.

    ``flag`` is boolean (non-zero = fired) with **time on ``axis``** (default 0, the same default as
    :func:`robust_zscore`). If fewer than ``n`` dates exist, no window can be persistent and the
    result is all-False — never an exception, because a short stack is a data condition, not a bug.
    """
    array = np.asarray(flag, dtype=bool)
    rise, window = _parse_persistence(persistence)
    if array.ndim == 0:
        raise ValueError("detect_change needs at least one axis (a time series), got a scalar")
    if not -array.ndim <= axis < array.ndim:
        raise ValueError(f"axis {axis} is out of range for a {array.ndim}-D array")
    axis = axis % array.ndim

    moved = np.moveaxis(array, axis, -1)
    n_dates = moved.shape[-1]
    if n_dates == 0 or window > n_dates:
        return np.zeros_like(array, dtype=bool)

    counts = np.lib.stride_tricks.sliding_window_view(moved, window, axis=-1).sum(axis=-1)
    passing = counts >= rise  # (..., n_dates - window + 1)
    n_windows = passing.shape[-1]

    # Sliding "any passing window covers t": prefix sums over window starts, O(n_dates) memory.
    cumulative = np.cumsum(passing.astype(np.int32), axis=-1)
    cumulative = np.concatenate(
        [np.zeros(passing.shape[:-1] + (1,), dtype=np.int32), cumulative], axis=-1
    )
    t = np.arange(n_dates)
    low = np.clip(t - window + 1, 0, n_windows)
    high = np.clip(t + 1, 0, n_windows)
    corroborated = (cumulative[..., high] - cumulative[..., low]) > 0
    kept = corroborated & moved  # a filter removes detections; it never invents them
    return np.moveaxis(kept, -1, axis)


# ---------------------------------------------------------------------------------------------
# P8 — H3 aggregation (README §6 step P8)
# ---------------------------------------------------------------------------------------------


def h3_aggregate(
    cell_ids: Iterable[str],
    values: object,
    resolution: int,
    *,
    mean_column: str = "mean",
) -> pd.DataFrame:
    """Aggregate per-cell values to a coarser H3 parent resolution.

    Formula (README §6 step P8, "H3 res 9 mean")::

        parent(c) = h3.cell_to_parent(c, resolution)
        mean_p    = mean{ v_c : c ∈ children(p) },      count_p = #{ finite v_c }

    Returns one row per parent that **has data**, columns ``["cell_id", mean_column, "count"]``
    (``mean_column`` lets the caller name the statistic after the contract field it fills, e.g.
    ``mean_column="velocity_mm_yr"``), sorted by ``cell_id`` for byte-stable output. ``count``
    counts only finite values, so a parent whose children all carry NaN is dropped instead of
    published as ``0`` — nodata stays **absent**, never zero (contract §8 null policy). A child
    with NaN is skipped by the mean; it never drags the parent toward zero.

    ``resolution`` must be the input resolution or coarser (aggregating is what this is for);
    ``h3.cell_to_parent`` on a finer target would silently invent children, so it is rejected.

    pandas is imported inside this function on purpose: it is the only helper here that returns a
    frame, and keeping it out of the module import keeps the numeric core cheap to import for the
    LOS/fit maths (pandas costs ~1 s of interpreter start-up in this project's venv).
    """
    import pandas as pd  # local on purpose: keep the numeric core's import surface small

    if isinstance(cell_ids, str):
        raise TypeError(
            "h3_aggregate takes a sequence of cell ids, not a single id (a str would be iterated "
            "character by character and produce nonsense parents)"
        )
    cells = [str(cell) for cell in cell_ids]
    flat_values = np.asarray(values, dtype=float).ravel()
    if len(cells) != flat_values.size:
        raise ValueError(
            f"h3_aggregate: {len(cells)} cell_ids but {flat_values.size} values"
        )
    target = int(resolution)
    if not 0 <= target <= 15:
        raise ValueError(f"h3 resolution must be 0..15; got {resolution!r}")
    if not cells:
        return pd.DataFrame(
            {
                "cell_id": pd.Series([], dtype=object),
                mean_column: pd.Series([], dtype="float64"),
                "count": pd.Series([], dtype="int64"),
            }
        )

    source_resolutions = {h3.get_resolution(cell) for cell in cells}
    if len(source_resolutions) > 1:
        raise ValueError(
            f"h3_aggregate: mixed source resolutions {sorted(source_resolutions)}; aggregate one "
            "resolution at a time (the pipeline uses res 9 everywhere)"
        )
    source = source_resolutions.pop()
    if target > source:
        raise ValueError(
            f"h3_aggregate: target resolution {target} is finer than the source resolution {source}; "
            "aggregation can only go up"
        )

    parents = [h3.cell_to_parent(cell, target) for cell in cells]
    frame = pd.DataFrame({"cell_id": parents, "value": flat_values})
    grouped = (
        frame.groupby("cell_id", sort=True)["value"]
        .agg(**{mean_column: "mean", "count": "count"})  # pandas' "mean"/"count" skip NaN
        .reset_index()
    )
    grouped = grouped[grouped["count"] > 0].reset_index(drop=True)
    grouped["count"] = grouped["count"].astype("int64")
    return grouped[["cell_id", mean_column, "count"]]


# ---------------------------------------------------------------------------------------------
# P7 — metrics for `meta.stats` (README §6 step P7 + storage loss)
# ---------------------------------------------------------------------------------------------


def velocity_metrics(
    velocity_mm_yr: object,
    cell_area_m2: object,
    *,
    pixel_size_m: float | None = None,
) -> dict[str, float | int | str]:
    """Statistics that ``meta.stats`` publishes, computed once so the app never guesses.

    Contract names (``contracts.STATS_REQUIRED_KEYS``) are reproduced verbatim and with the same
    estimator pandas uses in ``pipeline/mock/artifacts.py`` (linear interpolation):
    ``median_velocity_mm_yr``, ``p05_velocity_mm_yr``, ``p95_velocity_mm_yr``. Everything else is
    prefixed (``velocity_*``, ``compaction_*``, ``storage_*``, ``gradient_*``) so it cannot collide
    with a frozen key; ``n_cells``/``n_dates`` stay the caller's job because this function never
    sees a date (``velocity_n_valid`` gives the count it can justify).

    Storage loss (README §6, "Storage loss")::

        dV/dt = Σ_px v_up · A_px      [m³/yr]      (v_up in mm/yr, A in m², so /1000)

    reported as the signed ``compaction_volume_m3_yr`` and as ``storage_loss_m3_yr = −dV/dt``
    (positive = volume lost, which is what a subsiding basin does). Cells with NaN velocity or NaN
    area are excluded from the sum instead of contributing zero; ``compaction_area_m2`` says how
    much area the published volume actually covers.

    ``|∇v|`` (README §6 step P7) is computed only for a 2-D field: forward differences in both
    directions on the interior, ``hypot`` of the two, per-pixel spacing from ``pixel_size_m`` or —
    if that is not supplied — ``sqrt(median(cell_area_m2))``, the equivalent square-cell size. That
    fallback is exact for a square raster and approximate for H3 hexagons (a res-9 hexagon has
    ~0.105 km² and a 200 m edge but a 324 m "equivalent square"), so pass ``pixel_size_m``
    explicitly when the gradient is a published number. A 1-D input omits the gradient keys
    entirely, because there is no neighbourhood to difference.

    ``risk_level_median`` / ``risk_level_worst`` come from ``contracts.risk_level_from_velocity``
    (frozen bands: < 5 ``estable``, 5–25 ``moderado``, ≥ 25 ``critico``, sign ignored), so the
    pipeline and the app can never disagree about a colour. ``velocity_std_mm_yr`` is the
    population standard deviation (``ddof=0``), i.e. it describes this grid, not a sample of one.
    """
    velocity = np.asarray(velocity_mm_yr, dtype=float)
    if velocity.size == 0:
        raise ValueError("velocity_metrics: empty velocity array")
    area = np.broadcast_to(np.asarray(cell_area_m2, dtype=float), velocity.shape)
    finite_velocity = np.isfinite(velocity)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median, p05, p95 = (float(v) for v in np.nanpercentile(velocity, [50.0, 5.0, 95.0]))
        metrics: dict[str, float | int | str] = {
            "median_velocity_mm_yr": median,
            "p05_velocity_mm_yr": p05,
            "p95_velocity_mm_yr": p95,
            "velocity_mean_mm_yr": float(np.nanmean(velocity)),
            "velocity_std_mm_yr": float(np.nanstd(velocity)),
            "velocity_min_mm_yr": float(np.nanmin(velocity)),
            "velocity_max_mm_yr": float(np.nanmax(velocity)),
            "velocity_n_valid": int(finite_velocity.sum()),
            "velocity_n_missing": int(velocity.size - finite_velocity.sum()),
        }
        # Frozen bands, sign ignored; the median band is the colour the app shows, the worst
        # band is the one the legend must still cover.
        metrics["risk_level_median"] = C.risk_level_from_velocity(median)
        metrics["risk_level_worst"] = C.risk_level_from_velocity(
            float(np.nanmax(np.abs(velocity)))
        )

    usable = finite_velocity & np.isfinite(area)
    if not usable.any():
        raise ValueError("velocity_metrics: no (velocity, area) pair with both values finite")
    metric_area = area[usable]
    volume_m3_yr = float(np.sum(velocity[usable] * metric_area) / 1000.0)
    metrics["compaction_volume_m3_yr"] = volume_m3_yr
    metrics["storage_loss_m3_yr"] = -volume_m3_yr
    metrics["compaction_area_m2"] = float(np.sum(metric_area))

    if velocity.ndim == 2 and min(velocity.shape) >= 2:
        spacing = _pixel_spacing(area, pixel_size_m)
        dv_drow = np.diff(velocity, axis=0)  # (rows-1, cols)
        dv_dcol = np.diff(velocity, axis=1)  # (rows, cols-1)
        gradient = np.hypot(dv_drow[:, :-1], dv_dcol[:-1, :]) / spacing  # mm/yr per m
        gradient_per_km = gradient * 1000.0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            metrics["gradient_median_mm_yr_per_km"] = float(np.nanmedian(gradient_per_km))
            metrics["gradient_p95_mm_yr_per_km"] = float(
                np.nanpercentile(gradient_per_km, 95.0)
            )
    return metrics


def _pixel_spacing(area: np.ndarray, pixel_size_m: float | None) -> float:
    """Pixel size in metres: explicit argument, else ``sqrt(median equivalent cell area)``."""
    if pixel_size_m is not None:
        spacing = float(pixel_size_m)
        if not np.isfinite(spacing) or spacing <= 0.0:
            raise ValueError(f"pixel_size_m must be finite and > 0; got {pixel_size_m!r}")
        return spacing
    finite_area = area[np.isfinite(area) & (area > 0.0)]
    if finite_area.size == 0:
        raise ValueError(
            "velocity_metrics: cannot derive a pixel spacing — pass pixel_size_m, every "
            "cell_area_m2 is NaN/zero"
        )
    return float(np.sqrt(float(np.median(finite_area))))


# ---------------------------------------------------------------------------------------------
# Null results — the bound Málaga must publish (README §13, limitation 1; contract §5 stats)
# ---------------------------------------------------------------------------------------------


def detection_limit_mm_yr(
    velocity_noise_mm: object,
    span_years: object,
    *,
    n_epochs: int | None = None,
    n_sigma: float = 2.0,
) -> float:
    """Detection limit of a linear trend: the smallest ``|v|`` this stack could have resolved.

    Formula. For ordinary least squares on ``n`` equally spaced epochs spanning ``T`` years, with
    white noise ``σ`` per epoch (the residual scatter of the displacement series, mm), the slope
    standard error is::

        σ_ṫ = σ / sqrt(Σ (t_i − t̄)²)
            = (σ / T) · sqrt(12·(n−1) / (n·(n+1)))        ≈  3.46·σ/(T·√n) for large n

        detection_limit = n_sigma · σ_ṫ

    ``n_sigma = 2`` (≈ 95 % two-sided). ``n_epochs`` defaults to ``None``, which uses ``n = 2``,
    i.e. ``σ·√2/T``: the *worst case* of a two-date difference divided by the span. That is the
    honest default, because the number of usable epochs of a real NISAR stack is itself an
    assumption. More epochs shrink the bound only as ``1/√n``: doubling the dates buys ``1/√2``,
    never more — which is why Málaga's bound is driven by the *span*, not by the sample count.

    ``velocity_noise_mm`` keeps the name from the agreed signature, but it is a **displacement**
    noise in mm per epoch, not a mm/yr rate: dividing by the span is exactly what turns one into
    the other, so feeding it a rate would divide by the span twice.

    Assumptions, stated because the published number depends on them:

    1. white Gaussian noise; **no** temporal correlation;
    2. evenly spaced epochs (12 days for NISAR GUNW, 6 for Sentinel-1 — never mix them);
    3. the trend is a straight line: no seasonal term, no steps. If the seasonal harmonic is
       fitted, use its residual scatter as ``σ``, otherwise the bound is inflated by the annual
       swing.

    Because atmosphere and orbit errors are strongly *correlated* in time, real error is larger
    than this idealisation (README §13 error budget: unwrapping ≫ troposphere ≫ orbital ≫ DEM ≫
    noise). So the returned value is a lower bound and must be published as a **null-result
    statement** — "we detect no motion above X mm/yr" — never as a measurement precision; the bound
    *is* the result for Málaga (README §13, limitation 1; ``config/study_cases.yaml``
    ``null_result: true``).
    """
    sigma = float(np.asarray(velocity_noise_mm, dtype=float))
    span = float(np.asarray(span_years, dtype=float))
    if not np.isfinite(sigma) or sigma < 0.0:
        raise ValueError(
            f"velocity_noise_mm must be a finite, non-negative 1-σ scatter in mm; got {velocity_noise_mm!r}"
        )
    if not np.isfinite(span) or span <= 0.0:
        raise ValueError(f"span_years must be finite and > 0; got {span_years!r}")
    if not np.isfinite(n_sigma) or n_sigma <= 0.0:
        raise ValueError(f"n_sigma must be finite and > 0; got {n_sigma!r}")
    if n_epochs is None:
        n = 2
    else:
        n = int(n_epochs)
        if n < 2:
            raise ValueError(
                f"n_epochs must be >= 2 (a single epoch has no trend); got {n_epochs!r}"
            )
    if sigma == 0.0:
        return 0.0
    slope_stderr = sigma * np.sqrt(12.0 * (n - 1) / (n * (n + 1))) / span
    return float(n_sigma * slope_stderr)
