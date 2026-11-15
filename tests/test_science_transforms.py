"""Unit tests for the pure numeric core: `pipeline/science/transforms.py`.

Hermetic on purpose: numpy/pandas/h3 only, no downloading, no fixtures on disk, no RNG without a
fixed seed, whole file well under 3 s. Every assertion is a statement about a formula in README §6
or about a frozen rule in `src/domain/contracts.py`, so a failure names the science that broke
rather than the plumbing.
"""

from __future__ import annotations

import inspect
import pathlib
import subprocess
import sys

import h3
import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts the cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

from pipeline.science import transforms as T  # noqa: E402
from src.domain import contracts as C  # noqa: E402

#: Sentinel-1 ascending-ish geometry used throughout: 39° incidence, right-looking.
INCIDENCE = 39.0
#: Platform heading α_f = −12° (northbound), converted to the README §6 azimuth α = 180° − α_f.
HEADING = 168.0


# ---------------------------------------------------------------------------------------------
# 1. LOS projection (README §6, step P5)
# ---------------------------------------------------------------------------------------------


def test_los_unit_vector_is_the_readme_formula_and_a_unit_vector() -> None:
    theta, alpha = np.radians(INCIDENCE), np.radians(HEADING)
    expected = np.array([np.cos(theta), -np.sin(theta) * np.cos(alpha), -np.sin(theta) * np.sin(alpha)])
    u = T.los_unit_vector(INCIDENCE, HEADING)
    assert u.shape == (3,)
    np.testing.assert_allclose(u, expected, atol=0, rtol=0)
    assert np.linalg.norm(u) == pytest.approx(1.0, abs=1e-15)
    # u_up = cos(theta) > 0, so positive LOS means uplift when the horizontals are zero.
    assert u[0] == pytest.approx(np.cos(theta))


def test_los_round_trip_recovers_vertical_when_horizontals_are_zero() -> None:
    vertical = np.array([-420.0, -45.0, 0.0, 12.5])
    los = T.project_to_los(vertical, INCIDENCE, HEADING)  # vertical -> LOS
    back = T.los_to_vertical(los, INCIDENCE, HEADING)  # LOS -> vertical
    np.testing.assert_allclose(back, vertical, atol=1e-9, rtol=0)


def test_los_round_trip_with_trusted_horizontals_is_exact() -> None:
    vertical = np.array([-120.0, 5.0])
    east = np.array([3.0, -7.5])
    north = np.array([1.5, 2.0])
    los = T.project_to_los(vertical, INCIDENCE, HEADING, v_east=east, v_north=north)
    back = T.los_to_vertical(los, INCIDENCE, HEADING, v_east=east, v_north=north)
    np.testing.assert_allclose(back, vertical, atol=1e-12, rtol=0)


def test_vertical_from_los_is_the_named_assume_north_entry_point() -> None:
    los = np.array([-100.0, 20.0])
    east = np.array([2.5, -1.0])
    explicit = T.los_to_vertical(los, INCIDENCE, HEADING, v_east=east, v_north=0.0)
    named = T.vertical_from_los(los, INCIDENCE, HEADING, assume_vnorth=0.0, v_east=east)
    np.testing.assert_allclose(named, explicit, atol=0, rtol=0)
    # ... and a non-zero v_north assumption is used with the sign the unit vector says.
    with_north = T.vertical_from_los(los, INCIDENCE, HEADING, assume_vnorth=1.0, v_east=east)
    u = T.los_unit_vector(INCIDENCE, HEADING)
    np.testing.assert_allclose(with_north, explicit - u[2] / u[0], atol=1e-12, rtol=0)


def test_los_ignoring_the_horizontal_term_is_a_measurable_error() -> None:
    """Why README §6 insists on v_north ≈ 0: a 10 mm/yr east motion is not negligible."""
    los = T.project_to_los(-100.0, INCIDENCE, HEADING, v_east=10.0)
    vertical = T.los_to_vertical(los, INCIDENCE, HEADING)  # horizontals treated as zero
    assert abs(vertical - (-100.0)) > 1.0


def test_incidence_outside_the_valid_domain_is_rejected() -> None:
    for bad in (90.0, 91.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            T.los_unit_vector(bad, HEADING)
    with pytest.raises(ValueError):
        T.los_to_vertical(1.0, INCIDENCE, float("inf"))


# -- O-08: the projection's sign is a physical statement, not a bookkeeping choice ---------------


def test_uplift_projects_to_positive_los_and_subsidence_to_negative() -> None:
    """O-08's close criterion at the arithmetic layer: uplift ⇒ positive LOS.

    `u_up = cos(theta) = 0.777 > 0`, so the frozen projection is **toward-sensor-positive**: a pure
    upward motion (up is positive in this project) gives a positive `d_LOS`, and subsidence
    (downward) a negative one. `pipeline/process_case.py` labels the phase conversion that
    reproduces this `--los-sign toward-sensor`, and that label is the default. This is what the
    previous `away-from-sensor` label contradicted.
    """
    u_up = float(T.los_unit_vector(INCIDENCE, HEADING)[0])
    assert u_up == pytest.approx(np.cos(np.radians(INCIDENCE)))
    vertical = np.array([+10.0, +2.5, -10.0])
    np.testing.assert_allclose(
        T.project_to_los(vertical, INCIDENCE, HEADING), vertical * u_up, rtol=0.0, atol=0.0
    )
    assert T.project_to_los(+10.0, INCIDENCE, HEADING) > 0.0  # uplift is positive LOS
    assert T.project_to_los(-10.0, INCIDENCE, HEADING) < 0.0  # subsidence is negative
    assert T.project_to_los(0.0, INCIDENCE, HEADING) == 0.0


def test_known_horizontal_motion_keeps_the_sign_the_unit_vector_declares() -> None:
    """A known horizontal motion must project with the sign of its own unit-vector component.

    At theta = 39 deg, alpha = 168 deg: `u = [0.777, +0.616, -0.131]`, so pure +east points partly
    *toward* the satellite (positive LOS) and pure +north partly *away* (negative LOS). Asserting
    both directions matters: "horizontals move LOS" alone would pass under either sign convention.
    """
    u = T.los_unit_vector(INCIDENCE, HEADING)
    east_los = float(T.project_to_los(0.0, INCIDENCE, HEADING, v_east=10.0))
    north_los = float(T.project_to_los(0.0, INCIDENCE, HEADING, v_north=10.0))
    assert u[1] > 0.0 > u[2]  # the declared signs at this azimuth
    assert east_los == pytest.approx(10.0 * u[1], abs=1e-12)
    assert north_los == pytest.approx(10.0 * u[2], abs=1e-12)
    assert east_los > 0.0 > north_los
    # Motion straight along +u is the extreme case: the ground moves exactly toward the sensor.
    along = 10.0 * np.asarray(u)
    assert float(
        T.project_to_los(along[0], INCIDENCE, HEADING, v_east=along[1], v_north=along[2])
    ) == pytest.approx(10.0, abs=1e-12)
    # ... and the same motion with every component negated (along -u) is away from it.
    assert float(
        T.project_to_los(-along[0], INCIDENCE, HEADING, v_east=-along[1], v_north=-along[2])
    ) == pytest.approx(-10.0, abs=1e-12)


# ---------------------------------------------------------------------------------------------
# 2. Harmonic fit (README §6, step P6)
# ---------------------------------------------------------------------------------------------


def _synthetic_series(trend: float, amplitude: float = 5.0, noise: float = 0.0, n: int = 37):
    """One pixel: linear trend + annual harmonic (split 3/4 so hypot(A, B) == amplitude)."""
    t = np.linspace(0.0, 3.0, n)
    phase = 2.0 * np.pi * t
    a = 0.6 * amplitude  # 3 of 5
    b = 0.8 * amplitude  # 4 of 5
    y = 1.25 + trend * t + a * np.cos(phase) + b * np.sin(phase)
    return t, y, a, b


def test_harmonic_fit_recovers_a_known_trend_and_amplitude() -> None:
    t, y, _, _ = _synthetic_series(trend=-37.0, amplitude=5.0)
    fit = T.harmonic_fit(t, y)
    assert isinstance(fit, T.HarmonicFit)
    assert fit.trend_mm_yr == pytest.approx(-37.0, abs=1e-6)
    assert fit.seasonal_amp_mm == pytest.approx(5.0, abs=1e-6)
    assert fit.intercept_mm == pytest.approx(1.25, abs=1e-6)
    # The default is one annual harmonic: 2 + 2*1 columns. Two harmonics over a 3-year span are
    # identifiable too, but the default must never be the 6-column basis (see the guard tests).
    assert fit.n_harmonics == 1
    assert fit.coefficients.shape == (2 + 2 * 1,)
    assert fit.seasonal_amplitudes_mm.shape == (1,)
    assert fit.n_points == 37
    assert fit.r2 == pytest.approx(1.0, abs=1e-12)
    assert fit.seasonal_identifiable is True
    assert fit.warnings == ()


def test_r2_is_one_only_for_a_noise_free_series() -> None:
    t, clean, _, _ = _synthetic_series(trend=-37.0)
    rng = np.random.default_rng(11)
    noisy = clean + rng.normal(0.0, 3.0, size=clean.size)
    assert T.harmonic_fit(t, clean).r2 == pytest.approx(1.0, abs=1e-12)
    r2 = T.harmonic_fit(t, noisy).r2
    assert r2 < 1.0
    assert r2 > 0.9  # the trend still dominates 3 mm of noise
    # A pure ramp is perfectly explained by the linear term alone.
    assert T.harmonic_fit(t, -2.0 * t).r2 == pytest.approx(1.0, abs=1e-12)


def test_nans_are_dropped_not_treated_as_zero() -> None:
    t, y, _, _ = _synthetic_series(trend=-12.0)
    holed = y.copy()
    holed[[0, 5, 17, 30]] = np.nan
    clean_fit = T.harmonic_fit(t, y)
    holed_fit = T.harmonic_fit(t, holed)
    assert holed_fit.n_points == y.size - 4
    assert holed_fit.trend_mm_yr == pytest.approx(clean_fit.trend_mm_yr, abs=1e-9)
    assert holed_fit.seasonal_amp_mm == pytest.approx(clean_fit.seasonal_amp_mm, abs=1e-9)
    # Treating NaN as 0 would move the trend by a lot; assert we are nowhere near that.
    zero_filled = T.harmonic_fit(t, np.nan_to_num(holed, nan=0.0))
    assert abs(zero_filled.trend_mm_yr - clean_fit.trend_mm_yr) > 0.5


def test_too_few_points_raise_a_clear_error() -> None:
    t = np.arange(3, dtype=float)
    with pytest.raises(ValueError, match="at least 4"):
        T.harmonic_fit(t, np.array([0.0, 1.0, 2.0]), n_harmonics=0)
    # A second harmonic (6 columns) needs more than the absolute floor of 4; the default
    # (one harmonic, 4 columns) does not, so 5 points fit fine implicitly.
    with pytest.raises(ValueError, match="at least 6"):
        T.harmonic_fit(np.arange(5, dtype=float), -np.arange(5, dtype=float) * 1.0, n_harmonics=2)
    assert T.harmonic_fit(np.linspace(0.0, 0.3, 5), -np.arange(5, dtype=float)).n_points == 5
    with pytest.raises(ValueError, match="1-D"):
        T.harmonic_fit(np.zeros((3, 4)), np.zeros((3, 4)))
    with pytest.raises(ValueError, match="differ in length"):
        T.harmonic_fit(np.arange(8, dtype=float), np.zeros(7))
    with pytest.raises(ValueError, match="period_years"):
        T.harmonic_fit(np.linspace(0, 2, 12), np.zeros(12), period_years=0.0)


# ---------------------------------------------------------------------------------------------
# 2b. Identifiability of the seasonal term (the 132-day stack of config/study_cases.yaml)
# ---------------------------------------------------------------------------------------------

#: The real study-case window: 12 dates over ~132 days = 0.36 of an annual cycle.
SHORT_SPAN_YEARS = 132.0 / T.DAYS_PER_YEAR
#: True secular rate of the short-window synthetic series, mm/yr.
SHORT_TREND = -20.0


def _short_window_series(noise: float = 1.0, seed: int = 7, n: int = 12):
    """132 days, 12 dates: a −20 mm/yr trend plus an annual swing and 1 mm/epoch noise."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, SHORT_SPAN_YEARS, n)
    truth = SHORT_TREND * t + 5.0 * np.cos(2.0 * np.pi * t + 0.7)
    return t, truth + rng.normal(0.0, noise, size=n)


def _slope_stderr(t: np.ndarray, n_harmonics: int, sigma: float) -> float:
    """Exact OLS standard error of ``ṫ`` for this sampling: ``σ·sqrt((DᵀD)⁻¹[1,1])``."""
    design = np.column_stack(
        [np.ones_like(t), t]
        + [f(2.0 * np.pi * k * t) for k in range(1, n_harmonics + 1) for f in (np.cos, np.sin)]
    )
    return float(sigma * np.sqrt(np.linalg.inv(design.T @ design)[1, 1]))


def test_default_harmonic_order_is_one_not_two() -> None:
    """The headline velocity must not silently come from the 6-column basis."""
    assert inspect.signature(T.harmonic_fit).parameters["n_harmonics"].default == 1
    t, y = _short_window_series()
    implicit = T.harmonic_fit(t, y)
    assert implicit.n_harmonics == 1
    assert implicit.coefficients.shape == (4,)
    # ... and a caller who asks for two harmonics still gets two, so the knob is live.
    assert T.harmonic_fit(t, y, n_harmonics=2).coefficients.shape == (6,)


def test_short_window_trend_is_recovered_within_its_standard_error() -> None:
    """132 days is enough for the trend — if the basis is one harmonic, not two.

    Tolerance: ``2·σ_ṫ`` with ``σ_ṫ = σ/sqrt(Σ(t−t̄)²)`` computed from the *actual* 4-column
    design = 2 × 37.1 mm/yr ≈ 74 mm/yr for 1 mm/epoch noise. That is the honest precision of a
    0.36-yr stack (which is why Málaga publishes a null result, not a velocity); the defect being
    fixed is the ×14 inflation a second harmonic adds, and the ×10³ blow-ups it produced.
    """
    t, y = _short_window_series()
    fit = T.harmonic_fit(t, y, n_harmonics=1)
    sigma = _slope_stderr(t, 1, 1.0)
    assert sigma == pytest.approx(37.1, abs=0.5)  # the number the docstring quotes
    assert abs(fit.trend_mm_yr - SHORT_TREND) < 2.0 * sigma, (
        f"trend {fit.trend_mm_yr:.2f} is further than 2σ_ṫ={2 * sigma:.1f} from {SHORT_TREND}"
    )


def test_two_harmonics_on_132_days_scatter_the_trend_by_orders_of_magnitude() -> None:
    """The measured pathology, so the fix is demonstrably better and not merely different.

    Same 1 mm/epoch noise, 300 draws each: one harmonic recovers a −20 mm/yr trend with a scatter
    near its analytic 37 mm/yr standard error; two harmonics scatters by ~500 mm/yr (and both
    models report r2 > 0.9, which is why r2 cannot be the guard).
    """
    def scatter(n_harmonics: int, draws: int = 300) -> tuple[float, float]:
        rng = np.random.default_rng(2026)
        t = np.linspace(0.0, SHORT_SPAN_YEARS, 12)
        truth = SHORT_TREND * t + 5.0 * np.cos(2.0 * np.pi * t + 0.7)
        estimates = np.array(
            [
                T.harmonic_fit(
                    t, truth + rng.normal(0.0, 1.0, t.size), n_harmonics=n_harmonics
                ).trend_mm_yr
                for _ in range(draws)
            ]
        )
        return float(np.median(estimates)), float(np.std(estimates, ddof=1))

    one_median, one_std = scatter(1)
    two_median, two_std = scatter(2)
    analytic = _slope_stderr(np.linspace(0.0, SHORT_SPAN_YEARS, 12), 1, 1.0)
    assert abs(one_median - SHORT_TREND) < 5.0
    assert one_std == pytest.approx(analytic, rel=0.15)
    assert two_std > 5.0 * one_std, (
        f"two harmonics should be far worse: 1-harmonic σ={one_std:.1f} mm/yr, "
        f"2-harmonic σ={two_std:.1f} mm/yr (medians {one_median:.1f} / {two_median:.1f})"
    )


def test_guard_flags_two_harmonics_over_132_days() -> None:
    t, y = _short_window_series()
    fit = T.harmonic_fit(t, y, n_harmonics=2)
    assert fit.seasonal_identifiable is False
    assert len(fit.warnings) >= 2  # one per unidentifiable harmonic, plus the conditioning note
    joined = " ".join(fit.warnings)
    assert "harmonic 1" in joined and "harmonic 2" in joined
    assert "seasonal_amp_mm" in joined  # the message must say what not to publish
    assert "trend_mm_yr remains usable" in joined
    # The gate does not raise: a trend is still returned (that is the whole contract value). On
    # this seed the 2-harmonic value is ~467 mm/yr for a true −20 mm/yr — exactly the silent
    # failure the flag now names — and r2 still looks excellent, so r2 is not the identifiability
    # test. The 1-harmonic recovery of the same sampling is asserted in the previous test.
    assert np.isfinite(fit.trend_mm_yr)
    assert fit.r2 > 0.95
    assert fit.design_condition > T.MAX_DESIGN_CONDITION
    assert fit.annual_coverage == pytest.approx(SHORT_SPAN_YEARS, abs=1e-12)


def test_guard_flags_one_harmonic_over_132_days_but_keeps_the_trend() -> None:
    """One annual harmonic on 0.36 of a cycle is marginal: fit it, never publish its amplitude."""
    t, y = _short_window_series()
    fit = T.harmonic_fit(t, y)  # default: n_harmonics=1
    assert fit.seasonal_identifiable is False
    assert any("harmonic 1" in message for message in fit.warnings)
    assert np.isfinite(fit.trend_mm_yr)
    # The regime rule has no measurable denominator here, so it must not claim "elastic"
    # (recoverable) on an amplitude the span cannot resolve.
    assert fit.regime == "transitional"
    assert fit.regime in C.REGIMES
    assert fit.regime_ratio == pytest.approx(abs(fit.trend_mm_yr) / fit.seasonal_amp_mm)


def test_guard_is_silent_over_two_years() -> None:
    t = np.linspace(0.0, 2.0, 12)
    y = -20.0 * t + 5.0 * np.cos(2.0 * np.pi * t + 0.7)
    for n_harmonics in (1, 2):
        fit = T.harmonic_fit(t, y, n_harmonics=n_harmonics)
        assert fit.seasonal_identifiable is True, fit.warnings
        assert fit.warnings == ()
        assert fit.design_condition <= T.MAX_DESIGN_CONDITION
    # Two full cycles is the minimum for the second harmonic, not a threshold effect.
    assert T.harmonic_fit(t, y, n_harmonics=2).annual_coverage == pytest.approx(2.0, abs=1e-12)


def test_two_year_series_recovers_trend_and_amplitude() -> None:
    """The guard does not forbid harmonics: with 2 years of data both terms are measurable."""
    rng = np.random.default_rng(3)
    t = np.linspace(0.0, 2.0, 24)
    truth = -20.0 * t + 9.0 * np.cos(2.0 * np.pi * t + 0.7)
    y = truth + rng.normal(0.0, 1.0, t.size)
    fit = T.harmonic_fit(t, y, n_harmonics=2)
    assert fit.seasonal_identifiable is True
    assert fit.warnings == ()
    assert fit.trend_mm_yr == pytest.approx(-20.0, abs=2.0 * _slope_stderr(t, 2, 1.0))
    assert fit.seasonal_amp_mm == pytest.approx(9.0, abs=1.0)
    # The amplitude is the point: published only because two full cycles are observed. And the
    # regime is the plain |ṫ|/A rule here, no downgrade.
    assert fit.regime == T.classify_regime(fit.trend_mm_yr, fit.seasonal_amp_mm)
    assert fit.regime == "inelastic"  # |−20| > 2·9


def test_coverage_thresholds_are_exact_and_documented() -> None:
    """0.75 yr for harmonic 1, 1.5 yr for harmonic 2: assert the rule, not just its spirit."""
    assert T.MIN_SEASONAL_COVERAGE_PER_HARMONIC == 0.75
    assert T.MAX_DESIGN_CONDITION == 30.0
    below_gate = T.harmonic_fit(np.linspace(0.0, 0.75 - 1e-3, 12), np.zeros(12), n_harmonics=1)
    assert below_gate.seasonal_identifiable is False
    at_gate = T.harmonic_fit(np.linspace(0.0, 0.75, 12), np.zeros(12), n_harmonics=1)
    assert at_gate.seasonal_identifiable is True, at_gate.warnings
    for span, n_harmonics, identified in ((1.5 - 1e-3, 2, False), (1.5, 2, True)):
        fit = T.harmonic_fit(np.linspace(0.0, span, 12), np.zeros(12), n_harmonics=n_harmonics)
        assert fit.seasonal_identifiable is identified, (span, fit.warnings)
    # A linear model has no seasonal term to gate, however short the window.
    short_linear = T.harmonic_fit(np.linspace(0.0, 0.05, 6), np.zeros(6), n_harmonics=0)
    assert short_linear.seasonal_identifiable is True
    assert short_linear.warnings == ()
    assert short_linear.seasonal_amp_mm == 0.0


@pytest.mark.parametrize(
    ("n_epochs", "span_years", "expected"),
    [
        # 4 epochs are enough for one harmonic (2 + 2*1), so the span gate is the one that binds.
        (4, 0.749, 0),
        (4, 0.750, 1),
        # 1.50 yr with 6 epochs carries two harmonics; at 1.49 yr only one is publishable.
        (6, 1.490, 1),
        (6, 1.500, 2),
        # ... and 1.50 yr with 5 epochs is one harmonic: the epoch floor 2 + 2*2 = 6 binds first.
        (5, 1.500, 1),
        # 3 epochs are below even the one-harmonic floor of 4, however long the span claims to be.
        (3, 0.750, 0),
        (12, 2.000, 2),
    ],
)
def test_harmonic_order_is_exact_at_both_gates(
    n_epochs: int, span_years: float, expected: int
) -> None:
    """The order ladder is ``span >= 0.75·k yr AND n_epochs >= 2 + 2k``, with no slack either way.

    ``harmonic_order`` decides whether the published ``seasonal_amp_mm`` is a *fit* or a typed
    ``0.0``, and ``contracts`` 1.1 makes that column required, so a boundary that drifts by one
    day or one epoch silently converts a not-fitted stack into a claimed amplitude (or withholds
    one the span supports). Both gates are therefore pinned *at* the threshold: the cases below
    bracket 0.75 yr and 1.50 yr by 1 day-ish, and each span is paired with an epoch count that
    makes one of the two gates the binding one.
    """
    # The ladder is written in units of the coverage constant, not in magic years, so restate the
    # constant here: if MIN_SEASONAL_COVERAGE_PER_HARMONIC moves, these pins must move with it
    # instead of quietly testing a ladder the module no longer has.
    assert T.MIN_SEASONAL_COVERAGE_PER_HARMONIC == 0.75
    assert T.harmonic_order(4, T.MIN_SEASONAL_COVERAGE_PER_HARMONIC) == 1
    assert T.harmonic_order(6, 2.0 * T.MIN_SEASONAL_COVERAGE_PER_HARMONIC) == 2

    got = T.harmonic_order(n_epochs, span_years)
    assert got == expected, (
        f"harmonic_order(n_epochs={n_epochs}, span_years={span_years:g}) = {got}, expected "
        f"{expected}: 2 harmonics need span >= "
        f"{2 * T.MIN_SEASONAL_COVERAGE_PER_HARMONIC:g} yr AND n_epochs >= 6, 1 harmonic needs "
        f"span >= {T.MIN_SEASONAL_COVERAGE_PER_HARMONIC:g} yr AND n_epochs >= 4, otherwise 0."
    )


def test_guard_also_catches_dates_clustered_inside_a_long_span() -> None:
    """Coverage alone is not enough: a 2-year span sampled in five weeks is still degenerate."""
    t = np.concatenate([np.linspace(0.0, 0.08, 11), [2.0]])
    fit = T.harmonic_fit(t, np.zeros(t.size), n_harmonics=2)
    assert fit.annual_coverage >= 1.5  # the span test would wave this through
    assert fit.seasonal_identifiable is False
    assert any("condition number" in message for message in fit.warnings)


def test_harmonic_fit_is_still_constructible_with_its_original_fields() -> None:
    """New fields are defaults, so a caller/hand-built instance keeps working."""
    fit = T.HarmonicFit(
        trend_mm_yr=-20.0,
        seasonal_amp_mm=5.0,
        r2=0.99,
        regime="inelastic",
        coefficients=np.array([0.0, -20.0, 5.0, 0.0]),
        intercept_mm=0.0,
    )
    assert fit.n_points == 0
    assert fit.n_harmonics == 0
    assert fit.seasonal_amplitudes_mm.size == 0
    assert fit.period_years == T.SEASONAL_PERIOD_YEARS
    assert fit.seasonal_identifiable is True  # nothing was flagged
    assert fit.warnings == ()  # default: a field a caller may ignore
    assert np.isnan(fit.design_condition) and np.isnan(fit.annual_coverage)
    assert fit.regime_ratio == pytest.approx(4.0)


def test_regime_uses_the_two_sigma_amplitude_rule_on_both_sides() -> None:
    seasonal = 5.0
    expected = {
        -30.0: "inelastic",  # |ṫ|/A = 6.0  -> secular dominates
        -8.0: "transitional",  # |ṫ|/A = 1.6  -> exactly the boundary band
        -2.0: "elastic",  # |ṫ|/A = 0.4  -> seasonal dominates
    }
    for trend, regime in expected.items():
        t, y, _, _ = _synthetic_series(trend=trend, amplitude=seasonal)
        fit = T.harmonic_fit(t, y)
        assert fit.seasonal_amp_mm == pytest.approx(seasonal, abs=1e-6)
        assert fit.regime == regime, f"trend={trend} ratio={fit.regime_ratio}"
        assert fit.regime in C.REGIMES


def test_regime_boundary_is_strict_and_documented() -> None:
    # |trend| == 2*amplitude is NOT inelastic: README §6 says ">", not ">=".
    assert T.classify_regime(10.0, 5.0) == "transitional"
    assert T.classify_regime(10.001, 5.0) == "inelastic"
    assert T.classify_regime(-10.001, 5.0) == "inelastic"  # sign ignored, like the risk bands
    # |trend| == amplitude is transitional, below it is elastic.
    assert T.classify_regime(5.0, 5.0) == "transitional"
    assert T.classify_regime(4.999, 5.0) == "elastic"
    # No seasonal signal at all.
    assert T.classify_regime(-3.0, 0.0) == "inelastic"
    assert T.classify_regime(0.0, 0.0) == "transitional"
    assert T.classify_regime(float("nan"), 1.0) == "transitional"


def test_regime_ratio_property_matches_the_classifier() -> None:
    t, y, _, _ = _synthetic_series(trend=-40.0, amplitude=10.0)
    fit = T.harmonic_fit(t, y)
    assert fit.regime_ratio == pytest.approx(4.0, abs=1e-9)


# ---------------------------------------------------------------------------------------------
# 3. Coherence bias correction (README §6 step P8; §13 limitation 6)
# ---------------------------------------------------------------------------------------------


def test_coherence_is_nan_outside_the_unbiased_domain() -> None:
    looks = 16.0
    threshold = 1.0 / np.sqrt(looks)  # 0.25
    for gamma_hat in (0.0, 0.1, threshold - 1e-12, threshold):  # <= 1/sqrt(L)
        assert np.isnan(T.coherence_bias_correction(gamma_hat, looks))
    # and it is defined just above the threshold
    just_above = T.coherence_bias_correction(np.nextafter(threshold, 1.0), looks)
    assert np.isfinite(just_above)
    assert just_above < 1e-6


def test_coherence_correction_lowers_the_estimate_and_removes_the_bias() -> None:
    """The correction is a *debiasing*, so it must move gamma down, never up.

    Note: the task brief asked for "the corrected value exceeding gamma_hat". That is impossible
    for this estimator: gamma_corr**2 - gamma_hat**2 = (gamma_hat**2 - 1)/(L - 1) <= 0 for every
    gamma_hat <= 1, i.e. the finite-look bias of the sample coherence is *positive*. The test
    therefore asserts the true property (strictly decreasing, equality only at 1), and the Monte
    Carlo test below proves the movement is the right one.
    """
    for looks in (4.0, 16.0, 64.0):
        gammas = np.linspace(1.0 / np.sqrt(looks), 1.0, 25)[1:-1]
        corrected = T.coherence_bias_correction(gammas, looks)
        assert np.all(np.isfinite(corrected))
        assert np.all(corrected < gammas)
        assert np.all(corrected > 0.0)
        assert np.all(np.diff(corrected) > 0.0)  # monotone in the input
    assert T.coherence_bias_correction(1.0, 16.0) == pytest.approx(1.0, abs=1e-15)


def test_coherence_correction_inverts_exactly() -> None:
    looks, gamma_hat = 16.0, 0.62
    corrected = T.coherence_bias_correction(gamma_hat, looks)
    recovered = np.sqrt(((looks - 1.0) * corrected**2 + 1.0) / looks)
    assert recovered == pytest.approx(gamma_hat, abs=1e-12)


def test_coherence_rejects_corrupt_inputs_as_nan() -> None:
    assert np.isnan(T.coherence_bias_correction(1.05, 16.0))  # coherence > 1 is a bug
    assert np.isnan(T.coherence_bias_correction(0.9, 1.0))  # one look carries no coherence
    assert np.isnan(T.coherence_bias_correction(0.9, 0.0))
    assert np.isnan(T.coherence_bias_correction(np.nan, 16.0))
    out = T.coherence_bias_correction(np.array([0.1, 0.7, 0.95]), 16.0)
    assert out.shape == (3,)
    assert np.isnan(out[0]) and np.isfinite(out[1]) and np.isfinite(out[2])


def test_coherence_correction_removes_the_positive_finite_look_bias() -> None:
    """Monte Carlo on the estimator the formula assumes: L independent circular-Gaussian looks.

    At γ = 0.4 and L = 16 the raw sample coherence is biased high in γ² by ~0.06 and a tenth of
    the draws fall under 1/√L (no estimate exists there — hence NaN, not a clamp). The correction
    takes the bias down by roughly an order of magnitude. It is a first-order correction, so it
    can overshoot slightly near γ → 1; that is stated in the docstring and not hidden here.
    """
    rng = np.random.default_rng(2026)
    looks, gamma_true, draws = 16, 0.4, 2000
    estimates = np.empty(draws)
    for i in range(draws):
        first = (rng.normal(size=looks) + 1j * rng.normal(size=looks)) / np.sqrt(2.0)
        second = gamma_true * first + np.sqrt(1.0 - gamma_true**2) * (
            rng.normal(size=looks) + 1j * rng.normal(size=looks)
        ) / np.sqrt(2.0)
        estimates[i] = abs(np.vdot(first, second)) / np.sqrt(
            np.vdot(first, first).real * np.vdot(second, second).real
        )
    corrected = T.coherence_bias_correction(estimates, looks)
    finite = np.isfinite(corrected)
    assert 0.05 < 1.0 - finite.mean() < 0.15  # the domain floor is a real, frequent outcome

    raw_bias = float(np.mean(estimates[finite] ** 2) - gamma_true**2)
    corrected_bias = float(np.mean(corrected[finite] ** 2) - gamma_true**2)
    assert raw_bias > 0.03  # the sample coherence is biased high, as the formula assumes
    assert abs(corrected_bias) < abs(raw_bias) / 3.0


# ---------------------------------------------------------------------------------------------
# 4. Robust z-score (per-pixel temporal, outlier resistant)
# ---------------------------------------------------------------------------------------------


def test_robust_zscore_flags_an_outlier_and_centres_on_the_median() -> None:
    series = np.arange(10.0)  # 0..9 mm, time on axis 0
    series[7] = 100.0  # one gross unwrapping/atmosphere blunder
    z = T.robust_zscore(series)
    assert z[7] > 5.0
    assert np.median(z) == pytest.approx(0.0, abs=1e-9)
    assert np.all(np.abs(z[:7]) < 1.5)
    # 1.4826*MAD is the Gaussian-equivalent scale, so the outlier is not shrunk by itself.
    assert np.abs(z[7]) == pytest.approx(
        (100.0 - np.median(series)) / (T.MAD_TO_SIGMA * np.median(np.abs(series - np.median(series)))),
        rel=1e-12,
    )


def test_robust_zscore_returns_nan_when_mad_is_zero() -> None:
    constant = np.full((8, 3), -1.5)
    z = T.robust_zscore(constant)
    assert z.shape == constant.shape
    assert np.isnan(z).all()  # NaN, never +/-inf: a zero scale is unidentifiable, not infinite


def test_robust_zscore_matches_a_hand_computation_per_pixel() -> None:
    rng = np.random.default_rng(3)
    cube = rng.normal(0.0, 1.0, size=(12, 5))  # (n_dates, n_cells)
    cube[:, 2] = np.nan
    z = T.robust_zscore(cube, axis=0)
    assert z.shape == cube.shape
    assert np.isnan(z[:, 2]).all()  # a pixel with no data has no score
    median = np.median(cube[:, 0])
    mad = np.median(np.abs(cube[:, 0] - median))
    assert np.median(z[:, 0]) == pytest.approx(0.0, abs=1e-12)
    assert z[0, 0] == pytest.approx((cube[0, 0] - median) / (T.MAD_TO_SIGMA * mad), rel=1e-12)
    # one dropped date does not poison a pixel that has data
    cube[:, 1] = np.nan
    cube[:6, 1] = np.linspace(-2.0, 2.0, 6)
    z = T.robust_zscore(cube, axis=0)[:, 1]
    assert np.isfinite(z[:6]).all()
    assert np.isnan(z[6:]).all()  # the six missing dates stay missing, they are not zeros
    # ... but a pixel with a *single* valid date has no scale at all, so it gets NaN (MAD == 0).
    lonely = np.full((5, 1), np.nan)
    lonely[2, 0] = 7.0
    assert np.isnan(T.robust_zscore(lonely, axis=0)).all()


# ---------------------------------------------------------------------------------------------
# 5. Persistence filter (README §6 step P8, "the main false-positive killer")
# ---------------------------------------------------------------------------------------------


def test_persistence_rejects_a_single_date_blip() -> None:
    flag = np.zeros(12, dtype=bool)
    flag[5] = True
    assert not T.detect_change(flag, (2, 3)).any()
    assert not T.detect_change(flag, "2of3").any()


def test_persistence_accepts_two_of_three_consecutive_dates() -> None:
    flag = np.zeros(9, dtype=bool)
    flag[[3, 4]] = True
    kept = T.detect_change(flag, (2, 3))
    assert kept[[3, 4]].all()
    assert not kept[0] and not kept[8]
    np.testing.assert_array_equal(kept, T.detect_change(flag, "2of3"))


def test_persistence_keeps_a_two_date_pair_that_shares_one_window() -> None:
    """t=3 and t=5 are 2-of-3 inside the window [3, 4, 5], so both ends survive.

    The quiet date t=4 does *not* become a detection: the filter may only remove.
    """
    flag = np.zeros(9, dtype=bool)
    flag[[3, 5]] = True
    kept = T.detect_change(flag, (2, 3))
    np.testing.assert_array_equal(kept, np.array([0, 0, 0, 1, 0, 1, 0, 0, 0], dtype=bool))
    assert not (kept & ~flag).any()  # kept is always a subset of the raw detections


def test_persistence_needs_a_window_that_fits() -> None:
    flag = np.array([True, True])
    assert not T.detect_change(flag, (2, 3)).any()  # 2 dates cannot hold 2-of-3
    assert not T.detect_change(np.zeros(0, dtype=bool), (2, 3)).any()


def test_persistence_rate_is_tunable_and_validated() -> None:
    flag = np.zeros(6, dtype=bool)
    flag[[1, 2, 3]] = True
    assert T.detect_change(flag, (3, 3)).sum() == 3  # all three must fire
    assert not T.detect_change(np.array([1, 0, 1, 0, 1], dtype=bool), (3, 3)).any()
    for bad in ((0, 3), (4, 3), (2, 0), "2of", "2/3", (2,)):
        with pytest.raises(ValueError):
            T.detect_change(flag, bad)


def test_persistence_respects_the_time_axis() -> None:
    cube = np.zeros((4, 7), dtype=bool)  # (cells, dates)
    cube[:, 2] = True  # one date on every pixel -> never persistent
    assert not T.detect_change(cube, (2, 3), axis=1).any()
    cube[:, 2] = False
    cube[1, 4] = cube[1, 5] = True
    kept = T.detect_change(cube, (2, 3), axis=1)
    assert kept[1, 4] and kept[1, 5]
    assert kept.sum() == 2


# ---------------------------------------------------------------------------------------------
# 6. H3 aggregation (README §6 step P8)
# ---------------------------------------------------------------------------------------------


def _parent_and_children(resolution: int = 7, child: int = 9):
    parent = h3.latlng_to_cell(19.43, -99.13, resolution)
    children = sorted(h3.cell_to_children(parent, child))
    return parent, children


def test_h3_aggregate_sums_res9_cells_into_the_right_parent() -> None:
    first_parent, first_children = _parent_and_children()
    lat, lon = h3.cell_to_latlng(first_parent)
    second_parent = h3.latlng_to_cell(lat + 0.05, lon, 7)  # a genuinely different res-7 cell
    second_children = sorted(h3.cell_to_children(second_parent, 9))
    assert second_parent != first_parent

    children = first_children[:10] + second_children[:5]
    values = np.concatenate([np.full(10, 4.0), np.full(5, -20.0)])
    frame = T.h3_aggregate(children, values, 7)

    assert list(frame.columns) == ["cell_id", "mean", "count"]
    assert list(frame["cell_id"]) == sorted([first_parent, second_parent])  # deterministic order
    by_parent = frame.set_index("cell_id")
    assert by_parent.loc[first_parent, "count"] == 10
    assert by_parent.loc[first_parent, "mean"] == pytest.approx(4.0)
    assert by_parent.loc[second_parent, "count"] == 5
    assert by_parent.loc[second_parent, "mean"] == pytest.approx(-20.0)
    assert frame["count"].dtype == np.dtype("int64")


def test_h3_aggregate_keeps_nodata_as_absence_never_zero() -> None:
    parent, children = _parent_and_children()
    present = children[:19]  # 19 of the 49 children exist; the rest are nodata
    values = np.full(19, 2.0)
    values[:3] = np.nan  # three "present" cells whose value could not be estimated
    frame = T.h3_aggregate(present, values, 7)
    assert len(frame) == 1
    assert frame.loc[0, "cell_id"] == parent
    assert frame.loc[0, "count"] == 16  # only the children that actually carried a number
    assert frame.loc[0, "mean"] == pytest.approx(2.0)  # NaN children do not pull the mean down

    all_nan = T.h3_aggregate(present, np.full(19, np.nan), 7)
    assert all_nan.empty  # a parent with no data is absent, not 0.0
    assert list(all_nan.columns) == ["cell_id", "mean", "count"]


def test_h3_aggregate_empty_and_invalid_inputs() -> None:
    parent, children = _parent_and_children()
    empty = T.h3_aggregate([], np.array([]), 7)
    assert empty.empty and list(empty.columns) == ["cell_id", "mean", "count"]
    with pytest.raises(ValueError, match="values"):
        T.h3_aggregate(children[:3], np.zeros(4), 7)
    with pytest.raises(ValueError, match="finer"):
        T.h3_aggregate(children[:3], np.zeros(3), 10)
    with pytest.raises(TypeError, match="sequence"):
        T.h3_aggregate(children[0], 1.0, 7)
    with pytest.raises(ValueError, match="mixed source resolutions"):
        other = h3.cell_to_children(parent, 8)[0]
        T.h3_aggregate([children[0], other], np.zeros(2), 7)


def test_h3_aggregate_can_name_the_statistic_after_the_contract_field() -> None:
    parent, children = _parent_and_children()
    frame = T.h3_aggregate(children[:4], np.array([-10.0, -20.0, -30.0, -40.0]), 7, mean_column="velocity_mm_yr")
    assert list(frame.columns) == ["cell_id", "velocity_mm_yr", "count"]
    assert frame.loc[0, "velocity_mm_yr"] == pytest.approx(-25.0)


# ---------------------------------------------------------------------------------------------
# 7. Contract-facing metrics (README §6 step P7 + storage loss)
# ---------------------------------------------------------------------------------------------


def test_velocity_metrics_reproduces_the_contract_percentiles() -> None:
    rng = np.random.default_rng(42)
    velocity = rng.normal(-120.0, 90.0, size=500)
    velocity[::37] = np.nan
    metrics = T.velocity_metrics(velocity, 10_000.0)
    finite = pd.Series(velocity).dropna()

    # Same estimator pipeline/mock/artifacts.py uses to fill meta.stats (linear interpolation).
    assert metrics["median_velocity_mm_yr"] == pytest.approx(float(finite.median()), rel=1e-12)
    assert metrics["p05_velocity_mm_yr"] == pytest.approx(float(finite.quantile(0.05)), rel=1e-12)
    assert metrics["p95_velocity_mm_yr"] == pytest.approx(float(finite.quantile(0.95)), rel=1e-12)
    assert metrics["p05_velocity_mm_yr"] < metrics["median_velocity_mm_yr"] < metrics["p95_velocity_mm_yr"]
    assert metrics["velocity_n_valid"] == int(np.isfinite(velocity).sum())
    assert metrics["velocity_n_missing"] == int(np.isnan(velocity).sum())


def test_velocity_metrics_keys_are_contract_compatible_and_collision_free() -> None:
    velocity = np.array([-1.0, 6.0, 30.0])
    metrics = T.velocity_metrics(velocity, 1.0)
    frozen = set(C.STATS_REQUIRED_KEYS)
    keys = set(metrics)
    assert {"median_velocity_mm_yr", "p05_velocity_mm_yr", "p95_velocity_mm_yr"} <= keys
    # Everything else is prefixed, so it can never overwrite a frozen stats key silently.
    extras = keys - {"median_velocity_mm_yr", "p05_velocity_mm_yr", "p95_velocity_mm_yr"}
    assert all(
        key.startswith(("velocity_", "compaction_", "storage_", "gradient_", "risk_")) for key in extras
    ), sorted(extras)
    assert (keys & frozen) == {"median_velocity_mm_yr", "p05_velocity_mm_yr", "p95_velocity_mm_yr"}
    assert "n_cells" not in keys and "n_dates" not in keys  # deliberately the caller's job


def test_velocity_metrics_maps_risk_levels_through_the_frozen_bands() -> None:
    stable = T.velocity_metrics(np.array([-1.0, 2.0, 0.5]), 1.0)
    assert stable["risk_level_median"] == "estable"
    assert stable["risk_level_worst"] == "estable"
    critical = T.velocity_metrics(np.array([-30.0, 1.0, 1.0]), 1.0)
    assert critical["risk_level_worst"] == "critico"
    assert critical["risk_level_median"] == "estable"
    moderate = T.velocity_metrics(np.array([6.0, 6.0, 6.0]), 1.0)
    assert moderate["risk_level_median"] == "moderado"
    # Same rule as contracts.risk_level_from_velocity, sign ignored.
    for band in (stable, critical, moderate):
        assert band["risk_level_median"] == C.risk_level_from_velocity(float(band["median_velocity_mm_yr"]))


def test_velocity_metrics_computes_storage_loss_as_a_volume_per_year() -> None:
    velocity = np.array([-10.0, 10.0, np.nan])
    area = np.array([1000.0, 1000.0, 1000.0])
    metrics = T.velocity_metrics(velocity, area)
    # sum(v_up * A_px) in m3/yr: (-10 + 10) * 1000 / 1000 = 0, the NaN cell is not counted as 0
    assert metrics["compaction_volume_m3_yr"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["storage_loss_m3_yr"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["compaction_area_m2"] == pytest.approx(2000.0)  # only the usable cells

    sinking = T.velocity_metrics(np.array([-400.0, -200.0]), np.array([1000.0, 3000.0]))
    expected_volume = (-400.0 * 1000.0 + -200.0 * 3000.0) / 1000.0  # -1_000_000 m3/yr
    assert sinking["compaction_volume_m3_yr"] == pytest.approx(expected_volume)
    assert sinking["storage_loss_m3_yr"] == pytest.approx(-expected_volume)  # positive = lost


def test_velocity_metrics_gradient_only_for_a_2d_field() -> None:
    grid = np.array([[-10.0, -12.0, -14.0], [-11.0, -13.0, -15.0], [-12.0, -14.0, -16.0]])
    metrics = T.velocity_metrics(grid, 100.0, pixel_size_m=10.0)
    # forward differences on the interior: hypot(1, 2) mm/yr per 10 m -> 223.6 mm/yr per km
    expected = np.hypot(1.0, 2.0) / 10.0 * 1000.0
    assert metrics["gradient_median_mm_yr_per_km"] == pytest.approx(expected)
    assert metrics["gradient_p95_mm_yr_per_km"] == pytest.approx(expected)

    flat = T.velocity_metrics(np.array([1.0, 2.0, 3.0]), 100.0)
    assert not any(key.startswith("gradient_") for key in flat)
    assert "gradient_median_mm_yr_per_km" not in flat

    # The default spacing is sqrt(median area) = 10 m for 100 m2 cells, so it agrees here.
    assert T.velocity_metrics(grid, 100.0)["gradient_median_mm_yr_per_km"] == pytest.approx(expected)


def test_velocity_metrics_rejects_inputs_it_cannot_summarise() -> None:
    with pytest.raises(ValueError, match="empty"):
        T.velocity_metrics(np.array([]), 1.0)
    with pytest.raises(ValueError, match="finite"):
        T.velocity_metrics(np.array([np.nan, np.nan]), 1.0)
    with pytest.raises(ValueError, match="pixel_size_m"):
        T.velocity_metrics(np.ones((2, 2)), 4.0, pixel_size_m=0.0)


# ---------------------------------------------------------------------------------------------
# 8. Detection limit (README §13 limitation 1; contract §5 requires it for a null result)
# ---------------------------------------------------------------------------------------------


def test_detection_limit_matches_the_two_epoch_worst_case() -> None:
    sigma, span = 2.0, 0.5
    assert T.detection_limit_mm_yr(sigma, span) == pytest.approx(2.0 * np.sqrt(2.0) * sigma / span)
    assert T.detection_limit_mm_yr(0.0, span) == 0.0
    # The bound shrinks with the span: that is the whole point for Málaga.
    assert T.detection_limit_mm_yr(sigma, 4.0) < T.detection_limit_mm_yr(sigma, 1.0)


def test_detection_limit_is_the_ols_slope_standard_error() -> None:
    """Monte Carlo: the formula really is the 1-sigma scatter of a fitted trend."""
    rng = np.random.default_rng(2026)
    n_epochs, span, sigma = 12, 3.0, 1.0
    t = np.linspace(0.0, span, n_epochs)
    design = np.column_stack([np.ones_like(t), t])  # straight-line trend, no harmonics
    slopes = np.empty(400)
    for i in range(slopes.size):
        y = -5.0 * t + rng.normal(0.0, sigma, n_epochs)
        slopes[i] = np.linalg.lstsq(design, y, rcond=None)[0][1]
    predicted = T.detection_limit_mm_yr(sigma, span, n_epochs=n_epochs, n_sigma=1.0)
    assert np.std(slopes, ddof=1) == pytest.approx(predicted, rel=0.1)


def test_detection_limit_tightens_as_one_over_root_n() -> None:
    sigma, span = 1.5, 5.0
    four = T.detection_limit_mm_yr(sigma, span, n_epochs=4)
    sixteen = T.detection_limit_mm_yr(sigma, span, n_epochs=16)
    assert sixteen < four
    # Exact small-n ratio: sqrt(12(n-1)/(n(n+1))) is only ~1/sqrt(n) asymptotically.
    expected_ratio = np.sqrt((12 * 3 / (4 * 5)) / (12 * 15 / (16 * 17)))
    assert four / sixteen == pytest.approx(expected_ratio, rel=1e-12)
    assert T.detection_limit_mm_yr(sigma, span, n_epochs=100) / T.detection_limit_mm_yr(
        sigma, span, n_epochs=400
    ) == pytest.approx(1.97, abs=0.05)
    assert T.detection_limit_mm_yr(sigma, span, n_epochs=12) == pytest.approx(
        2.0 * sigma * np.sqrt(12 * 11 / (12 * 13)) / span
    )
    # n_sigma scales the published bound linearly (2 sigma ~ 95 % two-sided).
    tight = T.detection_limit_mm_yr(sigma, span, n_epochs=12, n_sigma=1.0)
    assert T.detection_limit_mm_yr(sigma, span, n_epochs=12) == pytest.approx(2.0 * tight)


def test_detection_limit_rejects_meaningless_inputs() -> None:
    with pytest.raises(ValueError, match="span_years"):
        T.detection_limit_mm_yr(1.0, 0.0)
    with pytest.raises(ValueError, match="velocity_noise_mm"):
        T.detection_limit_mm_yr(-1.0, 1.0)
    with pytest.raises(ValueError, match="n_epochs"):
        T.detection_limit_mm_yr(1.0, 1.0, n_epochs=1)
    with pytest.raises(ValueError, match="n_sigma"):
        T.detection_limit_mm_yr(1.0, 1.0, n_sigma=0.0)
    with pytest.raises(ValueError, match="velocity_noise_mm"):
        T.detection_limit_mm_yr(float("nan"), 1.0)


# ---------------------------------------------------------------------------------------------
# 9. Import hygiene: nobody may quietly start depending on scipy/scikit-image
# ---------------------------------------------------------------------------------------------


def test_module_imports_without_scipy_or_skimage() -> None:
    code = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import pipeline.science.transforms as t\n"
        "gained = sorted(m for m in set(sys.modules) - before "
        "if m.split('.')[0] in {'scipy', 'skimage', 'pandas'})\n"
        "print(','.join(gained))\n"
        "print(t.los_to_vertical.__name__)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    gained = completed.stdout.splitlines()[0]
    assert "scipy" not in gained and "skimage" not in gained, (
        f"pipeline.science.transforms pulled in scipy/skimage: {gained}"
    )
    # pandas is allowed only inside h3_aggregate: the numeric core must stay importable without
    # paying ~1 s of pandas start-up (see the docstring of h3_aggregate). If this fails, a new
    # top-level pandas import crept in — move it into the frame-returning helper.
    assert "pandas" not in gained, f"the numeric core now imports pandas at module level: {gained}"


def test_package_reexports_the_public_api() -> None:
    import pipeline.science as science

    assert set(science.__all__) <= set(dir(science))
    for name in science.__all__:
        assert getattr(science, name) is getattr(T, name) or isinstance(getattr(science, name), (int, float))
