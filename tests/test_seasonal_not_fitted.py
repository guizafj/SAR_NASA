"""Contract 1.1 decision: a *not-fitted* harmonic is published as `ratio_R = null`, never as a number.

Why the null is the honest value
--------------------------------
1.1 keeps `seasonal_amp_mm` **required**, so a stack whose span cannot identify an annual harmonic
still has to write that column. `transforms.harmonic_order` returns 0 below 0.75 yr of span (the
coverage gate `MIN_SEASONAL_COVERAGE_PER_HARMONIC * k`, up to the epoch floor `2 + 2k`), the fit is
then a plain linear model, and the published amplitude is exactly `0.0`. That `0.0` means "no
seasonal term was fitted" — it does **not** mean "we measured no seasonality", which would be a
claim about the ground rather than about the model.

Given an amplitude of zero, `ratio_R = |velocity_mm_yr| / seasonal_amp_mm` is undefined — a division
by zero, not a small ratio and not an infinite one. Any number written there is fabricated: `inf`
breaks the `>= 0` / finite rule the column carries everywhere else, and a sentinel like `1e9` would
be read as "extremely inelastic" by a human and by `classify_regime`. So the value is published as
NULL/NaN, and `meta.stats.median_ratio_R` is JSON `null` when no cell in the case fitted a harmonic.
The corresponding per-case caveat ("seasonal term not fitted") belongs in `meta.limitations`, so the
null travels with an explanation instead of being read as a measured absence.

What this file pins, and how
----------------------------
The relaxation must be *narrow*. A null `ratio_R` is legal exactly where `seasonal_amp_mm == 0`;
everywhere else the column is still required, finite and non-negative, and nulls in the other
numeric columns stay forbidden.

`validate_grid` and `validate_meta` accept the null (the relaxation landed with contract 1.1), and
the assertions below pin that it stays legal *and* narrow: the key must still be present (JSON null
is a value, a missing key is a 1.0 generator), a numeric median must stay non-negative, a null is
rejected where an amplitude WAS fitted, and `median_ratio_R` may be null but never NaN/inf. The
remaining assertions guard the rest of the rule.

Grids are real published artefacts (`data/processed/*/nisar_grid.parquet`, already contract-valid)
copied and mutated in-process; `meta.json` is built as a small in-process dict. Nothing here writes
HDF5, imports the heavy `pipeline/process_case.py` fixtures, or touches the network.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts the cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

from src.domain import contracts as C  # noqa: E402

PROCESSED = ROOT / "data/processed"

#: The reference artefact for mutating a copy. It is contract-valid today, which is what makes the
#: mutations below the *only* difference between "accepted" and "rejected".
REFERENCE_CASE = "mexico-city"
REFERENCE_GRID = PROCESSED / REFERENCE_CASE / "nisar_grid.parquet"

#: Every published grid, for the case-level guard (assertion 5).
PUBLISHED_GRIDS = sorted(PROCESSED.glob("*/nisar_grid.parquet")) if PROCESSED.exists() else []

needs_reference_grid = pytest.mark.skipif(
    not REFERENCE_GRID.exists(),
    reason=(
        f"missing {REFERENCE_GRID.relative_to(ROOT)} — run pipeline/generate_mock.py first; these "
        "tests mutate a contract-valid published grid instead of re-implementing one"
    ),
)

needs_published_grids = pytest.mark.skipif(
    not PUBLISHED_GRIDS,
    reason=(
        "no case artefacts under data/processed — run pipeline/generate_mock.py first; this guard "
        "is about the shipped artefacts, not about the schema alone"
    ),
)

def describe(problems: list[str]) -> str:
    """Render a problem list the way a failing assertion should read: one problem per line."""
    return "\n".join(f"  - {p}" for p in problems) or "  (none)"


def has_problem(problems: list[str], needle: str) -> bool:
    return any(needle in problem for problem in problems)


def has_null_problem(problems: list[str], column: str) -> bool:
    """True when the validators reported nulls in `column`, whatever count they printed."""
    return any(
        column in problem and "null(s)" in problem and "has" in problem for problem in problems
    )


@pytest.fixture()
def reference_grid() -> pd.DataFrame:
    """A fresh copy of the reference grid, proven contract-valid so a mutation is the only change."""
    grid = pd.read_parquet(REFERENCE_GRID)
    pristine = C.validate_grid(grid, case_id=REFERENCE_CASE)
    assert not pristine, (
        f"fixture is unusable: {REFERENCE_GRID.relative_to(ROOT)} does not pass validate_grid today, "
        "so no mutation below could be attributed to this decision. Regenerate the artefacts "
        "(pipeline/generate_mock.py) or fix the contract first:\n" + describe(pristine)
    )
    assert "ratio_R" in grid.columns, (
        "reference grid has no ratio_R column: contract 1.1 requires it even when "
        "every value would be undefined"
    )
    return grid


def not_fitted_copy(grid: pd.DataFrame, rows: int = 3) -> tuple[pd.DataFrame, list]:
    """Copy where `rows` cells fitted no harmonic: amplitude 0.0 and an undefined ratio (NaN)."""
    copy = grid.copy()
    index = list(copy.index[:rows])
    copy.loc[index, "seasonal_amp_mm"] = 0.0
    copy.loc[index, "ratio_R"] = float("nan")
    return copy, index


# ---------------------------------------------------------------------------------------------
# 1. The decision itself: a null ratio_R where nothing was fitted is accepted, column present.
# ---------------------------------------------------------------------------------------------


@needs_reference_grid
def test_grid_accepts_null_ratio_r_where_no_harmonic_was_fitted(reference_grid):
    """`ratio_R` NaN exactly where `seasonal_amp_mm == 0` is the *correct* publication, not an error.

    An undefined ratio has no number: `|trend| / 0`. Rejecting the null forces the producer to
    invent one, which is the failure this decision removes.
    """
    grid, rows = not_fitted_copy(reference_grid)

    # The frame really is "not fitted" on those rows: the amplitude is exactly 0 and the ratio is
    # undefined. Guard the mutation itself so a wrong column name cannot masquerade as a bug here.
    assert (grid.loc[rows, "seasonal_amp_mm"] == 0.0).all(), "mutation did not set the amplitude to 0"
    assert grid.loc[rows, "ratio_R"].isna().all(), "mutation did not null the ratio"

    problems = C.validate_grid(grid, case_id=REFERENCE_CASE)

    assert not problems, (
        f"validate_grid rejected {REFERENCE_CASE}/nisar_grid.parquet with ratio_R = null on rows "
        "where seasonal_amp_mm == 0. A not-fitted harmonic makes |velocity_mm_yr| / "
        "seasonal_amp_mm undefined, so NULL is the only honest value and the null check must skip "
        "ratio_R exactly there. Reported:\n" + describe(problems)
    )


@needs_reference_grid
def test_grid_still_requires_the_ratio_r_column_when_every_value_is_undefined(reference_grid):
    """Nullable *values* do not make the column optional: 1.1 still requires `ratio_R` to exist."""
    grid, _rows = not_fitted_copy(reference_grid)
    grid = grid.drop(columns=["ratio_R"])

    problems = C.validate_grid(grid, case_id=REFERENCE_CASE)

    assert has_problem(problems, "missing required column 'ratio_R'"), (
        "a grid without the ratio_R column must still be rejected: relaxing the null rule for "
        "undefined ratios does not make the column optional. Reported:\n" + describe(problems)
    )


# ---------------------------------------------------------------------------------------------
# 2. The relaxation is narrow: a fitted harmonic must carry a number.
# ---------------------------------------------------------------------------------------------


@needs_reference_grid
def test_grid_still_rejects_null_ratio_r_where_the_harmonic_was_fitted(reference_grid):
    """If an amplitude was measured, the ratio is defined and must be published as a finite >= 0."""
    fitted = list(reference_grid.index[reference_grid["seasonal_amp_mm"] > 0.0][:1])
    assert fitted, (
        f"fixture is unusable: {REFERENCE_CASE}/nisar_grid.parquet has no cell with "
        "seasonal_amp_mm > 0, so it cannot show that a fitted harmonic must keep its ratio"
    )

    grid = reference_grid.copy()
    grid.loc[fitted, "ratio_R"] = float("nan")

    problems = C.validate_grid(grid, case_id=REFERENCE_CASE)

    assert has_null_problem(problems, "ratio_R"), (
        f"validate_grid accepted a null ratio_R in {REFERENCE_CASE}/nisar_grid.parquet on row "
        f"{fitted[0]} where seasonal_amp_mm > 0. There the ratio is defined: publish the number, or "
        "set the amplitude to 0 only if no harmonic was actually fitted. Reported:\n"
        + describe(problems)
    )


# ---------------------------------------------------------------------------------------------
# 3. ... and nulls elsewhere stay forbidden, amplitudes stay non-negative.
# ---------------------------------------------------------------------------------------------


@needs_reference_grid
def test_grid_still_rejects_a_negative_seasonal_amplitude(reference_grid):
    """An amplitude is a non-negative magnitude; a negative one would also flip the regime rule."""
    fitted = list(reference_grid.index[reference_grid["seasonal_amp_mm"] > 0.0][:1])
    assert fitted, f"fixture is unusable: {REFERENCE_CASE} has no fitted amplitude to negate"

    grid = reference_grid.copy()
    grid.loc[fitted, "seasonal_amp_mm"] = -1.0

    problems = C.validate_grid(grid, case_id=REFERENCE_CASE)

    assert has_problem(problems, "seasonal_amp_mm must be non-negative"), (
        f"validate_grid accepted seasonal_amp_mm = -1 in {REFERENCE_CASE}/nisar_grid.parquet. The "
        "null relaxation for undefined ratios must not weaken the non-negativity rule. Reported:\n"
        + describe(problems)
    )


@needs_reference_grid
@pytest.mark.parametrize("column", ["velocity_mm_yr", "coherence"])
def test_grid_still_rejects_null_in_a_defined_numeric_column(reference_grid, column):
    """Only `ratio_R` gained a null window: an ordinary numeric column still forbids nulls.

    `velocity_mm_yr` is the column the ratio divides, and `coherence` is an unrelated measurement;
    both are always defined by contract, so a null is a missing row, not an undefined statistic.
    """
    row = list(reference_grid.index[:1])

    grid = reference_grid.copy()
    grid.loc[row, column] = float("nan")

    problems = C.validate_grid(grid, case_id=REFERENCE_CASE)

    assert has_null_problem(problems, column), (
        f"validate_grid accepted a null {column} in {REFERENCE_CASE}/nisar_grid.parquet. The "
        "ratio_R relaxation is specific to undefined ratios: a cell with no number must be omitted "
        "from the grid, never written as null (null_policy, contract §8). Reported:\n"
        + describe(problems)
    )


# ---------------------------------------------------------------------------------------------
# 4. `meta.stats.median_ratio_R` is JSON null when nothing was fitted, and still checks its value.
# ---------------------------------------------------------------------------------------------


def meta_dict(**stats_overrides) -> dict:
    """Smallest fully contract-valid `meta.json`, with the 0-harmonic limitation already stated.

    Built by hand rather than read from disk: assertion 4 is about the validator's rule, so it must
    not skip (or drift) with the artefacts. Values are illustrative, not measurements.
    """
    stats = {
        "n_cells": 2,
        "n_dates": 62,
        "median_velocity_mm_yr": -62.065,
        "median_seasonal_amp_mm": 1.5,
        "median_ratio_R": 41.37669,
        "dominant_regime": "inelastic",
        "p05_velocity_mm_yr": -231.892,
        "p95_velocity_mm_yr": -22.658,
    }
    stats.update(stats_overrides)
    return {
        "schema_version": C.SCHEMA_VERSION,
        "case_id": REFERENCE_CASE,
        "name": "Mexico City (fixture)",
        "bbox": [-99.37, 19.19, -98.94, 19.60],
        "crs": C.CRS_OUTPUT,
        "reference_point": {"lat": 19.4326, "lon": -99.1332},
        "dates": ["2024-11-14", "2026-11-15"],
        "revisit_days": 12,
        "sensor": "nisar",
        "product": "mock",
        "thresholds": {
            "coherence_min": 0.35,
            "k_sigma": 2.5,
            "min_ddB": 3.0,
            "persistence": "2of3",
            "min_area_m2": 500,
        },
        "provenance": {
            "source": "mock (synthetic stub, fixture)",
            "product_id": "FIXTURE",
            "processing_date": "2026-11-14",
            "method": "LOS->vertical (v_north=0), mean per H3 res 9 cell",
            "retrieved_utc": "2026-11-14T16:02:00Z",
            "reference": "fixture, not a measurement",
            "license": "NASA open data",
        },
        "stats": stats,
        "limitations": [
            "MOCK: every number is synthesised; validate the map and the schema, never the science.",
            "Seasonal term not fitted on this stack: harmonic_order returned 0 because the span is "
            "below the 0.75 yr annual gate, so seasonal_amp_mm is 0.0 and ratio_R is null.",
        ],
        "null_policy": "Cells without data are omitted from the grid; they are never written as 0.",
    }


def test_meta_fixture_is_valid():
    """The fixture must be valid *before* each mutation, or assertion 4 proves nothing."""
    problems = C.validate_meta(meta_dict())
    assert not problems, (
        "meta fixture is not contract-valid, so its mutations are meaningless:\n"
        + describe(problems)
    )


def test_meta_accepts_null_median_ratio_r_when_nothing_was_fitted():
    """No fitted harmonic anywhere in the case ⇒ the median of an empty/undefined set is `null`.

    `0.0` would claim "every cell divides evenly", and any positive number would be invented: the
    honest aggregate of undefined ratios is JSON `null`, and the key still has to be there so a
    consumer can distinguish "undefined" from "publisher forgot the field".
    """
    meta = meta_dict(median_seasonal_amp_mm=0.0, median_ratio_R=None)

    # JSON null, not NaN and not 0.0: `NaN` is not valid JSON, which is why the contract says null.
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped["stats"]["median_ratio_R"] is None, (
        "stats.median_ratio_R must serialise as JSON null when no harmonic was fitted"
    )

    problems = C.validate_meta(meta)

    assert not problems, (
        "validate_meta rejected stats.median_ratio_R = null with median_seasonal_amp_mm = 0. With "
        "no fitted harmonic the median ratio is undefined, so null is the correct aggregate; the "
        "non-negativity rule only applies to a numeric value. Reported:\n" + describe(problems)
    )


def test_meta_still_rejects_a_negative_median_ratio_r():
    """The ratio is a quotient of magnitudes: when it *is* a number it cannot be negative."""
    problems = C.validate_meta(meta_dict(median_ratio_R=-3.0))

    assert has_problem(problems, "stats.median_ratio_R must be non-negative"), (
        "validate_meta accepted stats.median_ratio_R = -3.0. Accepting null where the ratio is "
        "undefined must not weaken the >= 0 rule where it is defined. Reported:\n"
        + describe(problems)
    )


def test_meta_still_requires_the_median_ratio_r_key():
    """JSON null is a value; a missing key is a broken artefact. Only the first is legal here."""
    meta = meta_dict(median_seasonal_amp_mm=0.0)
    del meta["stats"]["median_ratio_R"]

    problems = C.validate_meta(meta)

    assert has_problem(problems, "'median_ratio_R'"), (
        "validate_meta accepted a meta.json whose stats block lacks the median_ratio_R key. "
        "Contract 1.1 requires the key always; null is how an undefined median is published, and "
        "an absent key is indistinguishable from a 1.0 generator. Reported:\n" + describe(problems)
    )


# ---------------------------------------------------------------------------------------------
# 5. Case-level guard: shipped artefacts never hide a defined ratio behind a null.
# ---------------------------------------------------------------------------------------------


@needs_published_grids
def test_published_grids_never_null_a_defined_ratio():
    """A not-fitted cell and a withheld ratio must stay distinguishable in data/processed.

    A null where `seasonal_amp_mm > 0` would be a ratio the pipeline computed and then hid, and no
    consumer could tell it from an honest not-fitted cell. (The converse — amplitude 0 ⇒ null —
    is what assertion 1 pins on a mutated copy; today every shipped cell has a fitted amplitude, so
    asserting it here would be vacuous.)
    """
    offenders: list[str] = []
    for path in PUBLISHED_GRIDS:
        case_id = path.parent.name
        grid = pd.read_parquet(path)
        if not {"seasonal_amp_mm", "ratio_R"} <= set(grid.columns):
            offenders.append(
                f"{case_id}: grid lacks seasonal_amp_mm/ratio_R — contract 1.1 requires both"
            )
            continue
        fitted = grid["seasonal_amp_mm"] > 0.0
        hidden = fitted & grid["ratio_R"].isna()
        if hidden.any():
            offenders.append(
                f"{case_id}: {int(hidden.sum())} row(s) with seasonal_amp_mm > 0 have a null "
                "ratio_R — publish the finite >= 0 ratio, or set the amplitude to 0 if no "
                "harmonic was fitted"
            )
        infinite = fitted & ~grid["ratio_R"].isna() & ~grid["ratio_R"].map(
            lambda v: isinstance(v, (int, float)) and math.isfinite(float(v))
        )
        if infinite.any():
            offenders.append(
                f"{case_id}: {int(infinite.sum())} row(s) with seasonal_amp_mm > 0 carry a "
                "non-finite ratio_R — a defined ratio must be finite (inf belongs to the "
                "not-fitted case, and there it is published as null)"
            )

    assert not offenders, (
        "published grids violate the not-fitted rule:\n  - " + "\n  - ".join(offenders)
    )


def test_a_zero_harmonic_fit_does_not_claim_a_regime() -> None:
    """No seasonal basis means no secular-vs-seasonal verdict: `regime` must be `"transitional"`.

    `classify_regime` maps `A == 0` with a non-zero trend to `"inelastic"` (R = |trend|/0 = inf).
    That rule is right for a fit that *resolved* a near-zero amplitude, but a zero-harmonic fit never
    had a seasonal column at all, so the comparison cannot be made. Publishing `"inelastic"` from it
    would assert permanent, non-recoverable compaction on the strength of a term that was never
    fitted — the same dishonesty as inventing a `ratio_R`. The contract's cannot-classify value is
    `"transitional"`, so `harmonic_fit` must return that when `n_harmonics == 0`.

    Acceptance test: it must fail with a regime mismatch (not an import or an API error) until the
    fit stops classifying from a term it never fitted.
    """
    import numpy as np

    from pipeline.science import transforms as V

    # Four epochs over 36 days: below the 0.75 yr coverage gate, so no harmonic is fitted.
    t_years = np.array([0.0, 12.0, 24.0, 36.0]) / V.DAYS_PER_YEAR
    order = V.harmonic_order(len(t_years), float(t_years[-1]))
    assert order == 0, (
        "this fixture is meant to be too short for any harmonic; if harmonic_order now returns "
        f"{order}, pick a shorter span so the not-fitted path is what is exercised"
    )

    fit = V.harmonic_fit(t_years, -62.0 * t_years, n_harmonics=order)

    assert fit.seasonal_amp_mm == 0.0
    assert fit.regime == "transitional", (
        "a zero-harmonic fit has no seasonal basis, so it cannot classify the regime; got "
        f"{fit.regime!r}. 'inelastic' here is R = |trend|/0 computed from a term that was never "
        "fitted, which claims permanent compaction the stack cannot support."
    )
