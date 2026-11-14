"""Numeric core of the offline InSAR pipeline (README §6, steps P5–P8).

The functions live in :mod:`pipeline.science.transforms` and are deliberately boring:

* **pure** — no I/O, no network, no credentials, no environment reads, no module state;
* **deterministic** — no RNG and no wall clock, so the same inputs always give the same bytes;
* **numpy in / numpy out** — NaN is the only "no data" value, and it is never silently replaced
  by 0 (contract §8 null policy).

This is the slice of Phase B that can be finished and reviewed *before* anyone has a granule:
the LOS projection, the trend/seasonal split, the coherence bias correction, the persistence
filter, H3 aggregation, the contract-facing statistics and the null-result bound. Real data
changes the inputs of these functions, never their maths.

Typical use::

    from pipeline.science import harmonic_fit, los_to_vertical

    v_up = los_to_vertical(los_mm, 39.0, 192.0, v_east=0.0, v_north=0.0)
    fit = harmonic_fit(t_years, displacement_mm)
    fit.trend_mm_yr, fit.seasonal_amp_mm, fit.regime

Not imported by the app: the Streamlit side reads artefacts, not pipeline internals
(`src/domain/contracts.py`, hard rule 1).
"""

from __future__ import annotations

from .transforms import (
    DAYS_PER_YEAR,
    MAD_TO_SIGMA,
    MIN_FIT_POINTS,
    REGIME_ELASTIC_RATIO,
    REGIME_INELASTIC_RATIO,
    SEASONAL_PERIOD_YEARS,
    HarmonicFit,
    classify_regime,
    coherence_bias_correction,
    detect_change,
    detection_limit_mm_yr,
    h3_aggregate,
    harmonic_fit,
    los_to_vertical,
    los_unit_vector,
    project_to_los,
    robust_zscore,
    velocity_metrics,
    vertical_from_los,
)

__all__ = [
    "DAYS_PER_YEAR",
    "MAD_TO_SIGMA",
    "MIN_FIT_POINTS",
    "REGIME_ELASTIC_RATIO",
    "REGIME_INELASTIC_RATIO",
    "SEASONAL_PERIOD_YEARS",
    "HarmonicFit",
    "classify_regime",
    "coherence_bias_correction",
    "detect_change",
    "detection_limit_mm_yr",
    "h3_aggregate",
    "harmonic_fit",
    "los_to_vertical",
    "los_unit_vector",
    "project_to_los",
    "robust_zscore",
    "velocity_metrics",
    "vertical_from_los",
]
