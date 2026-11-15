"""Type holes in `validate_meta` / `validate_timeseries`: a non-number must never pass as a statistic.

Why this file exists
--------------------
A validator-parity audit found that the *normative* validator was the laxer one for types.
`validate_meta` checked `stats.median_ratio_R` with

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ...

which silently **skips** every value that is not an `int`/`float`: the string `"2.1"`, a list, a
dict. The producer is then entitled to publish a statistic that `src/data/validation.py` rejects
when it recomputes it from the grid, so CI and the contract disagree about the same file. The
same escape existed in `validate_timeseries`, one line stronger: `astype("float64")` *coerces*
`"2.1"` to `2.1` and `True` to `1.0`, so a broken serialisation reached the UI looking exactly
like a measurement.

What is pinned here
-------------------
* `stats.median_ratio_R` is JSON `null` or a real *finite* number; string, bool (`isinstance(True,
  int)` is True in Python), list/dict, NaN and ±inf are rejected — through `validate_meta` alone
  and through a full `C.validate_all` bundle.
* The same type gate on the other numeric-or-null stats keys the validator carries
  (`median_seasonal_amp_mm`, `median_velocity_mm_yr`, `p05_velocity_mm_yr`, `p95_velocity_mm_yr`),
  and on the three numeric timeseries columns (`value`, `trend_mm_yr`, `seasonal_mm`).
* The legal not-fitted bundle (null median ratio + zero amplitudes + the "not fitted" limitation +
  `seasonal_n_harmonics = 0`) is still **accepted**: closing a type hole must not outlaw the one
  null the contract deliberately allows.
* The guard is not vacuous: `_pre_fix_numeric_gate` below is the verbatim predicate that shipped
  before the fix, and it accepts the very bundle that `validate_meta` now rejects. The pre-change
  verdicts are asserted, so this file fails loudly if the guard is ever reverted to "skip what I do
  not recognise".

The shipped artefacts are also read (never written): their numeric `median_ratio_R` values are what
the tightened rule must keep accepting.

Contract 1.1 follow-up pinned in the same file (same class of hole, same helpers)
------------------------------------------------------------------------------
* `stats.n_cells` / `stats.n_dates` were presence-only: the required-key pass was the whole rule, so
  the string `"2645"`, `true`, `2.5` and `-1` were all accepted as a count. They now have to be real
  integers (`int`, never `bool`, numpy integers allowed) greater than zero, and the shipped counts
  are read from `data/processed/*/meta.json` so the positive control is the real artefact.
* `stats.detection_limit_mm_yr` was presence-checked only inside the null-result branch, so a `NaN`,
  an `±inf` or a negative "limit" passed beside any ordinary case. It is now finite and
  non-negative wherever it appears; JSON null stays legal where no bound is mandatory.
* `validate_grid`'s numeric gate (`astype("float64", errors="ignore")`) already rejected a
  non-numeric column, but the message named the column and not the offending cell. The message now
  carries a sample value; the accept/reject verdicts are asserted **bit-identical** to the pre-fix
  table recorded below, including the values the `errors="ignore"` cast still coerces (`"2.1"`).
"""

from __future__ import annotations

import json
import math
import numbers
import pathlib
import re
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pytest

from src.domain import contracts as C

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data/processed"

CASE_ID = "mexico-city"
TREND = -128.0
AMPLITUDE = 9.4
RATIO = abs(TREND) / AMPLITUDE
DATES = ["2026-07-02"]
PROVENANCE = C.provenance_id(CASE_ID, "nisar-gunw", "2026-11-14", "abc12345")

#: The three published cases whose `stats.median_ratio_R` must stay accepted, unchanged.
SHIPPED_MEDIAN_RATIO = {
    "mexico-city": 43.120,
    "jakarta": 2.155,
    "malaga": 0.389,
}

#: The shipped `(stats.n_cells, stats.n_dates)` pairs, read from `data/processed/*/meta.json`.
#: `n_dates` is 62 in all three because they share one 6-day cadence stack; `n_cells` differs by AOI.
SHIPPED_COUNTS = {
    "mexico-city": (11600, 62),
    "jakarta": (4936, 62),
    "malaga": (2758, 62),
}


# ---------------------------------------------------------------------------------------------
# Fixtures: the smallest bundle that is fully contract-valid, so a mutation is the only difference.
# ---------------------------------------------------------------------------------------------


def _grid_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "cell_id": "8928308280fffff",
        "lat": 19.4326,
        "lon": -99.1332,
        "val_t1": 0.0,
        "val_t2": -7.4,
        "delta_change": -7.4,
        "velocity_mm_yr": TREND,
        "seasonal_amp_mm": AMPLITUDE,
        "ratio_R": RATIO,
        "regime": "inelastic",
        "coherence": 0.82,
        "land_use": "urban-dense",
        "risk_level": C.risk_level_from_velocity(TREND),
        "case_id": CASE_ID,
        "sensor": "nisar",
        "product": "nisar-gunw",
        "unit": "mm",
        "velocity_unit": "mm/year",
        "source": "ASF DAAC",
        "acquisition_date": "2026-07-02",
        "provenance_id": PROVENANCE,
    }
    row.update(overrides)
    return row


def _grid(**overrides: Any) -> pd.DataFrame:
    return pd.DataFrame([_grid_row(**overrides)])


def _timeseries(**overrides: Any) -> pd.DataFrame:
    row: dict[str, Any] = {
        "case_id": CASE_ID,
        "cell_id": "aoi",
        "date": DATES[0],
        "value": 0.0,
        "trend_mm_yr": TREND,
        "seasonal_mm": 0.0,
        "unit": "mm",
        "sensor": "nisar",
        "product": "nisar-gunw",
        "quality": "good",
        "provenance_id": PROVENANCE,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _meta(**stats_overrides: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "n_cells": 1,
        "n_dates": len(DATES),
        "median_velocity_mm_yr": TREND,
        "median_seasonal_amp_mm": AMPLITUDE,
        "median_ratio_R": RATIO,
        "dominant_regime": "inelastic",
        "p05_velocity_mm_yr": -410.0,
        "p95_velocity_mm_yr": 12.0,
    }
    stats.update(stats_overrides)
    return {
        "schema_version": C.SCHEMA_VERSION,
        "case_id": CASE_ID,
        "name": "Mexico City, MX",
        "bbox": [-99.35, 19.20, -98.85, 19.65],
        "crs": C.CRS_OUTPUT,
        "reference_point": [-99.13, 19.43],
        "dates": list(DATES),
        "revisit_days": 12,
        "sensor": "nisar",
        "product": "nisar-gunw",
        "thresholds": {
            "coherence_min": 0.35,
            "k_sigma": 2.5,
            "min_ddB": 3.0,
            "persistence": "2of3",
            "min_area_m2": 500,
        },
        "provenance": {
            "source": "ASF DAAC",
            "product_id": "NISAR_L2_PR_GUNW",
            "processing_date": "2026-11-14",
            "method": "LOS to vertical (v_north=0), H3 res 9 mean",
            "retrieved_utc": C.CONTRACT_FROZEN_UTC,
            "reference": "Herrera-García et al. 2021",
            "license": "NASA open data",
        },
        "stats": stats,
        "limitations": ["GUNW posting 80 m: city scale, not building scale."],
        "null_policy": "Cells without data are absent from the grid; never written as 0.",
    }


def _not_fitted_meta() -> dict[str, Any]:
    """The one legal null: no harmonic was fitted, so the aggregate ratio is undefined."""
    meta = _meta(
        median_seasonal_amp_mm=0.0,
        median_ratio_R=None,
        dominant_regime="transitional",
        seasonal_n_harmonics=0,
    )
    meta["limitations"] = [
        "GUNW posting 80 m: city scale, not building scale.",
        "Seasonal term not fitted: the stack is shorter than the identifiability gate.",
    ]
    return meta


def _context() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-99.13, 19.43]},
                "properties": {
                    "layer": "infrastructure",
                    "class": "water-well",
                    "name": "Pozo Roma",
                    "case_id": CASE_ID,
                    "crs": C.CRS_OUTPUT,
                    "source": "SACMEX",
                    "provenance_id": PROVENANCE,
                },
            }
        ],
    }


def _report(
    meta: Mapping[str, Any] | None = None,
    *,
    grid: pd.DataFrame | None = None,
    timeseries: pd.DataFrame | None = None,
) -> dict[str, list[str]]:
    """Full `validate_all` report for the fixture bundle with `meta` (or its overrides) applied."""
    return C.validate_all(
        grid=_grid() if grid is None else grid,
        timeseries=_timeseries() if timeseries is None else timeseries,
        meta=_meta() if meta is None else meta,
        context=_context(),
        case_id=CASE_ID,
    )


def _problems(**kwargs: Any) -> list[str]:
    """Flattened problem list of the whole bundle: empty means contract-conformant."""
    return [problem for problems in _report(**kwargs).values() for problem in problems]


def _all_problems(report: Mapping[str, list[str]]) -> list[str]:
    return [problem for problems in report.values() for problem in problems]


def _meta_problems(meta: Mapping[str, Any]) -> list[str]:
    return C.validate_meta(meta)


# ---------------------------------------------------------------------------------------------
# Baseline. Each rejection below is only meaningful if the untouched bundle is accepted.
# ---------------------------------------------------------------------------------------------


def test_the_fixture_bundle_is_accepted_before_any_mutation() -> None:
    """Negative-control anchor: the exact bundle shape the mutations below are derived from."""
    report = _report()
    assert report == {"grid": [], "timeseries": [], "meta": [], "context": []}, report
    assert _all_problems(report) == []


def test_the_not_fitted_fixture_is_accepted_before_any_mutation() -> None:
    """The legal-null bundle must be clean too, or "still accepted" proves nothing."""
    report = _report(
        _not_fitted_meta(),
        grid=_grid(seasonal_amp_mm=0.0, ratio_R=None, regime="transitional"),
    )
    assert _all_problems(report) == [], _all_problems(report)


# ---------------------------------------------------------------------------------------------
# 1. `stats.median_ratio_R`: JSON null or a real finite number, nothing else.
# ---------------------------------------------------------------------------------------------

OFFENDING_VALUES = (
    pytest.param("2.1", id="string-decimal"),
    pytest.param("41.37669", id="string-that-looks-exact"),
    pytest.param("", id="empty-string"),
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="plus-inf"),
    pytest.param(float("-inf"), id="minus-inf"),
    pytest.param([41.4], id="list"),
    pytest.param({"value": 41.4}, id="dict"),
)


@pytest.mark.parametrize("value", OFFENDING_VALUES)
def test_meta_rejects_a_non_number_median_ratio(value: Any) -> None:
    problems = _meta_problems(_meta(median_ratio_R=value))
    assert any("stats.median_ratio_R" in problem for problem in problems), (
        f"validate_meta accepted stats.median_ratio_R = {value!r} ({type(value).__name__}). "
        "Anything but a real finite number (or JSON null) is a published statistic the app "
        f"cannot use. Reported: {problems}"
    )


@pytest.mark.parametrize("value", OFFENDING_VALUES)
def test_bundle_rejects_a_non_number_median_ratio(value: Any) -> None:
    """The same value inside a complete artefact set: `validate_all` must flag the meta section."""
    report = _report(_meta(median_ratio_R=value))
    assert report["grid"] == [] and report["timeseries"] == [] and report["context"] == [], report
    assert report["meta"], (
        f"the full bundle accepted stats.median_ratio_R = {value!r}; validate_all reported "
        f"{_all_problems(report)}"
    )
    assert any("stats.median_ratio_R" in problem for problem in report["meta"]), report


@pytest.mark.parametrize("value", [41.37669, RATIO, 12, 0.0])
def test_meta_accepts_a_real_number_median_ratio(value: Any) -> None:
    problems = _meta_problems(_meta(median_ratio_R=value))
    assert problems == [], f"a real number ({value!r}) must stay publishable: {problems}"


@pytest.mark.parametrize("value", [41.37669, RATIO, 12, 0.0])
def test_bundle_accepts_a_real_number_median_ratio(value: Any) -> None:
    assert _problems(meta=_meta(median_ratio_R=value)) == []


def test_median_ratio_may_be_null_exactly_where_nothing_was_fitted() -> None:
    """The one legal null: amplitude 0 everywhere, `seasonal_n_harmonics = 0`, limitation says so."""
    meta = _not_fitted_meta()
    grid = _grid(seasonal_amp_mm=0.0, ratio_R=None, regime="transitional")

    # The fixture really is the not-fitted state, not a fitted case with a hidden statistic.
    assert meta["stats"]["median_ratio_R"] is None
    assert meta["stats"]["median_seasonal_amp_mm"] == 0.0
    assert meta["stats"]["seasonal_n_harmonics"] == 0
    assert any("not fitted" in item.lower() for item in meta["limitations"])

    assert _meta_problems(meta) == [], _meta_problems(meta)
    assert _problems(meta=meta, grid=grid) == []

    # ...and the null is only legal there: beside a fitted amplitude it hides a computed number.
    fitted = _meta(median_ratio_R=None)
    assert any("median_ratio_R" in problem for problem in _meta_problems(fitted)), (
        "null median_ratio_R beside median_seasonal_amp_mm > 0 must stay rejected"
    )


# ---------------------------------------------------------------------------------------------
# 2. Negative control: the shipped guard skipped the string, so this hole was reachable.
# ---------------------------------------------------------------------------------------------


def _pre_fix_numeric_gate(value: Any) -> bool:
    """Verbatim predicate from `validate_meta` before the fix (`median_seasonal_amp_mm`,
    `median_ratio_R`). Kept so the regression is demonstrated, not asserted from memory."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pre_fix_median_problems(stats: Mapping[str, Any]) -> list[str]:
    """The pre-fix loop body: a value the gate does not recognise is silently skipped."""
    problems: list[str] = []
    for key in ("median_seasonal_amp_mm", "median_ratio_R"):
        value = stats.get(key)
        if _pre_fix_numeric_gate(value):
            if not math.isfinite(float(value)):
                problems.append(f"meta: stats.{key} must be finite or null, got {value!r}")
            elif value < 0.0:
                problems.append(f"meta: stats.{key} must be non-negative, got {value!r}")
    return problems


def test_guard_is_not_vacuous_meta() -> None:
    """Non-vacuity: `validate_meta` now rejects the exact bundle the old loop *accepted*."""
    string_bundle = _meta(median_ratio_R="2.1")
    assert _pre_fix_median_problems(string_bundle["stats"]) == [], (
        "the pre-fix guard rejected the string, so this test would not demonstrate the hole"
    )
    assert _meta_problems(string_bundle), "validate_meta must reject the string median_ratio_R"
    assert _problems(meta=string_bundle), "validate_all must reject the string median_ratio_R"

    # The old guard *did* catch NaN; the finiteness rule is therefore unchanged, and only the
    # type escape is new. That is what keeps this a tightening rather than a redefinition.
    assert _pre_fix_median_problems(_meta(median_ratio_R=float("nan"))["stats"]), (
        "NaN was already rejected before this change; do not claim it as a new rule"
    )
    assert _meta_problems(_meta(median_ratio_R=float("nan")))


def test_guard_is_not_vacuous_timeseries() -> None:
    """Non-vacuity for the timeseries: the old code *coerced* the string, it did not reject it."""
    coerced = pd.Series(["2.1"]).astype("float64").tolist()
    assert coerced == [2.1], "the pre-fix cast no longer coerces a numeric string; revisit the fix"

    problems = C.validate_timeseries(_timeseries(value="2.1"))
    assert any("must hold real numbers" in problem for problem in problems), problems
    assert _problems(timeseries=_timeseries(value="2.1"))

    # A genuinely non-numeric cell is still rejected, so the guard did not replace that check.
    assert C.validate_timeseries(_timeseries(value="not-a-number")), (
        "a non-numeric 'value' must stay rejected"
    )


# ---------------------------------------------------------------------------------------------
# 3. The other numeric-or-null stats keys: same class of hole, same gate.
# ---------------------------------------------------------------------------------------------

NUMERIC_STATS_KEYS = (
    "median_seasonal_amp_mm",
    "median_velocity_mm_yr",
    "p05_velocity_mm_yr",
    "p95_velocity_mm_yr",
)


@pytest.mark.parametrize("key", NUMERIC_STATS_KEYS)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("2.1", id="string"),
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="plus-inf"),
        pytest.param([2.1], id="list"),
    ],
)
def test_other_numeric_stats_reject_type_escapes(key: str, value: Any) -> None:
    problems = _meta_problems(_meta(**{key: value}))
    assert any(f"stats.{key}" in problem for problem in problems), (
        f"validate_meta accepted stats.{key} = {value!r} ({type(value).__name__}); only a real "
        f"finite number or JSON null is a statistic. Reported: {problems}"
    )

    report = _report(_meta(**{key: value}))
    assert any(f"stats.{key}" in problem for problem in report["meta"]), report


@pytest.mark.parametrize(
    "key,value",
    [
        ("median_seasonal_amp_mm", 1.5),
        ("median_velocity_mm_yr", -128.0),
        ("p05_velocity_mm_yr", -410.0),
        ("p95_velocity_mm_yr", 12.0),
    ],
)
def test_other_numeric_stats_still_accept_numbers(key: str, value: Any) -> None:
    """No new required key and no new rule on real numbers: the legitimate value still passes."""
    assert _meta_problems(_meta(**{key: value})) == [], f"{key}={value!r} must be accepted"


@pytest.mark.parametrize("key", NUMERIC_STATS_KEYS)
def test_other_numeric_stats_still_accept_json_null(key: str) -> None:
    """The fix narrows the *type*, not the null: JSON null stays legal for every one of them."""
    assert _meta_problems(_meta(**{key: None})) == [], f"{key}=null must stay legal"


def test_negative_amplitude_is_still_rejected_negative_velocity_is_not() -> None:
    """The non-negativity rule is unchanged: an amplitude is a magnitude, a velocity has a sign."""
    amplitude = _meta_problems(_meta(median_seasonal_amp_mm=-1.5))
    assert any("median_seasonal_amp_mm must be non-negative" in p for p in amplitude), amplitude
    assert _meta_problems(_meta(p05_velocity_mm_yr=-410.0)) == []


def test_dominant_regime_enum_already_rejected_non_strings() -> None:
    """Audit note: `dominant_regime` compares `str(value)` to the enum, so it never had the hole."""
    for value in (3, True, ["inelastic"], {"regime": "inelastic"}):
        problems = _meta_problems(_meta(dominant_regime=value))
        assert any("dominant_regime" in problem for problem in problems), (value, problems)


def test_a_bool_is_still_not_a_detection_limit() -> None:
    """`isinstance(True, int)` let a JSON `true` satisfy the mandatory bound of a null result."""
    meta = _meta(median_velocity_mm_yr=-1.2, detection_limit_mm_yr=True)
    problems = _meta_problems(meta)
    assert any("detection_limit_mm_yr" in problem for problem in problems), problems

    # A real bound still satisfies it, so no legal null result was made unpublishable.
    assert _meta_problems(_meta(median_velocity_mm_yr=-1.2, detection_limit_mm_yr=1.5)) == []


# ---------------------------------------------------------------------------------------------
# 4. timeseries numeric columns: value / trend_mm_yr / seasonal_mm.
# ---------------------------------------------------------------------------------------------

TIMESERIES_NUMERIC_KEYS = ("value", "trend_mm_yr", "seasonal_mm")


@pytest.mark.parametrize("column", TIMESERIES_NUMERIC_KEYS)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("2.1", id="string"),
        pytest.param("0.0", id="string-zero"),
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="plus-inf"),
    ],
)
def test_timeseries_numeric_columns_reject_type_escapes(column: str, value: Any) -> None:
    series = _timeseries(**{column: value})
    problems = C.validate_timeseries(series)
    assert any(column in problem for problem in problems), (
        f"validate_timeseries accepted {column} = {value!r} ({type(value).__name__}). Reported: "
        f"{problems}"
    )
    assert _problems(timeseries=series), f"validate_all accepted {column} = {value!r}"


@pytest.mark.parametrize("column", TIMESERIES_NUMERIC_KEYS)
def test_timeseries_numeric_columns_still_accept_real_numbers(column: str) -> None:
    assert C.validate_timeseries(_timeseries(**{column: 1.25})) == []
    assert C.validate_timeseries(_timeseries(**{column: -1})) == []
    assert _problems(timeseries=_timeseries(**{column: 0})) == []


# ---------------------------------------------------------------------------------------------
# 5. The shipped artefacts: numeric medians the tightened rule must keep accepting.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(SHIPPED_MEDIAN_RATIO))
def test_shipped_meta_still_passes_with_its_numeric_median_ratio(case: str) -> None:
    meta_path = PROCESSED / case / "meta.json"
    if not meta_path.exists():
        pytest.skip(f"missing {meta_path.relative_to(ROOT)} — artefacts not generated here")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    published = meta["stats"]["median_ratio_R"]
    assert isinstance(published, float) and math.isfinite(published), (
        f"{case}: shipped median_ratio_R is {published!r}; the fix must not have changed the "
        "artefacts"
    )
    assert published == pytest.approx(SHIPPED_MEDIAN_RATIO[case], rel=5e-3), (
        f"{case}: shipped median_ratio_R moved ({published!r}); artefacts must not be regenerated"
    )
    assert _meta_problems(meta) == [], (
        f"{case}: the shipped meta.json no longer validates:\n" + "\n".join(_meta_problems(meta))
    )


@pytest.mark.parametrize("case", sorted(SHIPPED_MEDIAN_RATIO))
def test_shipped_timeseries_still_passes(case: str) -> None:
    path = PROCESSED / case / "timeseries.csv"
    if not path.exists():
        pytest.skip(f"missing {path.relative_to(ROOT)} — artefacts not generated here")

    frame = pd.read_csv(path)
    problems = C.validate_timeseries(frame)
    assert problems == [], (
        f"{case}: the shipped timeseries.csv no longer validates (the type gate must accept the "
        f"numbers pandas reads from it):\n" + "\n".join(problems)
    )


# ---------------------------------------------------------------------------------------------
# 6. `stats.n_cells` / `stats.n_dates`: a whole, positive number of rows — nothing else.
#
# These two keys were the last *presence-only* rule in `validate_meta`: the required-key pass was
# the entire check, so a producer could publish `"2645"`, `true`, `2.5` or `-1` as a cell count and
# the normative validator accepted the file. The rule is attached to that same loop below — one
# check, not a second parallel one — and the shipped counts are the positive control.
# ---------------------------------------------------------------------------------------------

COUNT_KEYS = ("n_cells", "n_dates")

BAD_COUNTS = (
    pytest.param("2645", id="string-digits"),
    pytest.param("62", id="string-short"),
    pytest.param("", id="empty-string"),
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param(2.5, id="float-fraction"),
    pytest.param(2645.0, id="float-integral"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="plus-inf"),
    pytest.param(0, id="zero"),
    pytest.param(-1, id="negative"),
    pytest.param(-2645, id="negative-large"),
    pytest.param(None, id="json-null"),
    pytest.param([2645], id="list"),
    pytest.param({"n": 2645}, id="dict"),
)


@pytest.mark.parametrize("key", COUNT_KEYS)
@pytest.mark.parametrize("value", BAD_COUNTS)
def test_counts_reject_anything_but_a_positive_integer(key: str, value: Any) -> None:
    problems = _meta_problems(_meta(**{key: value}))
    assert any(f"stats.{key}" in problem for problem in problems), (
        f"validate_meta accepted stats.{key} = {value!r} ({type(value).__name__}). A count of "
        f"cells/dates is a whole, positive number; only `src/data/validation.py` compared it to "
        f"the real row count. Reported: {problems}"
    )

    report = _report(_meta(**{key: value}))
    assert report["grid"] == [] and report["timeseries"] == [] and report["context"] == [], report
    assert any(f"stats.{key}" in problem for problem in report["meta"]), (
        f"the full bundle accepted stats.{key} = {value!r}: {_all_problems(report)}"
    )


@pytest.mark.parametrize("key", COUNT_KEYS)
def test_counts_still_accept_the_shipped_integers(key: str) -> None:
    """No new rejection for a real count: the shipped AoI sizes all pass, alone and in a bundle."""
    for value in (1, 62, 2758, 11600):
        assert _meta_problems(_meta(**{key: value})) == [], f"{key}={value!r} must be accepted"
        assert _problems(meta=_meta(**{key: value})) == []


@pytest.mark.parametrize("key", COUNT_KEYS)
def test_counts_still_accept_a_numpy_integer(key: str) -> None:
    """The pipeline derives the counts from numpy shapes, so `np.int64` has to keep passing."""
    value = np.int64(2645)
    assert not isinstance(value, int), "the reason this is pinned: np.int64 is not a builtin int"
    assert _meta_problems(_meta(**{key: value})) == [], f"{key}=np.int64(2645) must be accepted"


def test_counts_are_required_keys_but_json_null_is_not_a_count() -> None:
    """The type rule extends the existing required-key pass; it does not replace it."""
    for key in COUNT_KEYS:
        stats = {k: v for k, v in _meta()["stats"].items() if k != key}
        missing = _meta_problems({**_meta(), "stats": stats})
        assert any(f"stats missing {key!r}" in problem for problem in missing), (
            f"the required-key check is gone for {key}: {missing}"
        )
        nulled = _meta_problems(_meta(**{key: None}))
        assert any(f"stats.{key}" in problem for problem in nulled), (
            f"stats.{key} = null is not a count: {nulled}"
        )


def _pre_fix_count_problems(stats: Mapping[str, Any]) -> list[str]:
    """Verbatim pre-fix rule for `n_cells`/`n_dates`: presence in `STATS_REQUIRED_KEYS` and nothing
    else — no type, no sign. Kept so the hole is demonstrated, not asserted from memory."""
    return [f"meta: stats missing {key!r}" for key in C.STATS_REQUIRED_KEYS if key not in stats]


def test_count_guard_is_not_vacuous() -> None:
    """Non-vacuity: the pre-fix rule accepted every value `validate_meta` now rejects."""
    for key in COUNT_KEYS:
        for value in ("2645", True, 2.5, -1):
            stats = _meta(**{key: value})["stats"]
            assert _pre_fix_count_problems(stats) == [], (
                f"the pre-fix rule rejected {key}={value!r}, so this test would not demonstrate "
                "the hole"
            )
            assert _meta_problems(_meta(**{key: value})), (
                f"validate_meta must reject {key}={value!r}"
            )
            assert _problems(meta=_meta(**{key: value})), (
                f"validate_all must reject {key}={value!r}"
            )


@pytest.mark.parametrize("case", sorted(SHIPPED_COUNTS))
def test_shipped_meta_still_passes_with_its_counts(case: str) -> None:
    """Positive control on the real artefacts: the counts they publish are exactly what passes."""
    meta_path = PROCESSED / case / "meta.json"
    if not meta_path.exists():
        pytest.skip(f"missing {meta_path.relative_to(ROOT)} — artefacts not generated here")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    cells, dates = SHIPPED_COUNTS[case]

    assert isinstance(meta["stats"]["n_cells"], int) and not isinstance(meta["stats"]["n_cells"], bool)
    assert meta["stats"]["n_cells"] == cells, (
        f"{case}: shipped stats.n_cells is {meta['stats']['n_cells']!r}, expected {cells}"
    )
    assert meta["stats"]["n_dates"] == dates, (
        f"{case}: shipped stats.n_dates is {meta['stats']['n_dates']!r}, expected {dates}"
    )
    # ...and the published count agrees with the artefact's own date list, so the pair above is the
    # shipped value and not a transcription.
    assert meta["stats"]["n_dates"] == len(meta["dates"])
    assert _meta_problems(meta) == [], (
        f"{case}: the shipped meta.json no longer validates:\n" + "\n".join(_meta_problems(meta))
    )


# ---------------------------------------------------------------------------------------------
# 7. `stats.detection_limit_mm_yr`: finite and non-negative wherever it appears.
#
# The presence rule only fires for a null result (|median velocity| < 5 mm/yr), so a NaN, an ±inf
# or a negative bound passed beside any ordinary case. Finiteness is a property of the bound, not
# of that branch.
# ---------------------------------------------------------------------------------------------

#: A median far above the null-result threshold: the mandatory-bound rule cannot fire here, so a
#: rejection below can only come from the new type/finiteness rule.
ORDINARY_MEDIAN = -62.065

BAD_DETECTION_LIMITS = (
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="plus-inf"),
    pytest.param(float("-inf"), id="minus-inf"),
    pytest.param(-1.5, id="negative"),
    pytest.param(-0.0 - 1e-9, id="negative-tiny"),
    pytest.param("1.5", id="string"),
    pytest.param(True, id="bool-true"),
    pytest.param([1.5], id="list"),
)


@pytest.mark.parametrize("value", BAD_DETECTION_LIMITS)
def test_detection_limit_must_be_finite_even_when_it_is_not_mandatory(value: Any) -> None:
    meta = _meta(median_velocity_mm_yr=ORDINARY_MEDIAN, detection_limit_mm_yr=value)
    problems = _meta_problems(meta)

    assert any("detection_limit_mm_yr" in problem for problem in problems), (
        f"validate_meta accepted stats.detection_limit_mm_yr = {value!r} "
        f"({type(value).__name__}) beside an ordinary median. Reported: {problems}"
    )
    assert not any("is mandatory" in problem for problem in problems), (
        "this rejection must be the finiteness/non-negativity rule, not the null-result presence "
        f"rule: {problems}"
    )

    report = _report(meta)
    assert any("detection_limit_mm_yr" in problem for problem in report["meta"]), report


@pytest.mark.parametrize("value", [None, 0.0, 1.5, 5.0])
def test_detection_limit_still_accepts_a_real_bound_or_null(value: Any) -> None:
    """The fix narrows what a bound may be; it does not make the key mandatory."""
    meta = _meta(median_velocity_mm_yr=ORDINARY_MEDIAN, detection_limit_mm_yr=value)
    assert _meta_problems(meta) == [], f"detection_limit_mm_yr={value!r} must stay accepted: "
    "a real bound is publishable and JSON null is legal where nothing forces one"


def test_a_finite_bound_still_satisfies_the_null_result_rule() -> None:
    """No legal null result was made unpublishable: a real bound still discharges the obligation."""
    assert _meta_problems(
        _meta(median_velocity_mm_yr=-1.2, detection_limit_mm_yr=1.5)
    ) == []
    # ...and the obligation itself is unchanged: absent/null is still rejected for a null result.
    for absent in (None,):
        problems = _meta_problems(_meta(median_velocity_mm_yr=-1.2, detection_limit_mm_yr=absent))
        assert any("is mandatory" in problem for problem in problems), problems
    stats = {k: v for k, v in _meta()["stats"].items() if k != "detection_limit_mm_yr"}
    problems = _meta_problems({**_meta(), "stats": {**stats, "median_velocity_mm_yr": -1.2}})
    assert any("is mandatory" in problem for problem in problems), problems


def _pre_fix_is_real_number(value: Any) -> bool:
    """Verbatim pre-fix `_is_real_number` — the helper that shipped just before this change."""
    return not isinstance(value, bool) and isinstance(value, numbers.Real)


def _pre_fix_detection_limit_problems(stats: Mapping[str, Any]) -> list[str]:
    """Verbatim pre-fix detection-limit rule: presence inside the null-result branch, and no
    finiteness or sign check anywhere. Kept so the hole is demonstrated, not remembered."""
    problems: list[str] = []
    median = stats.get("median_velocity_mm_yr")
    if _pre_fix_is_real_number(median) and abs(float(median)) < C.NULL_RESULT_MM_YR:
        if not _pre_fix_is_real_number(stats.get("detection_limit_mm_yr")):
            problems.append(
                "meta: |median_velocity_mm_yr| < 5 mm/yr is a null result — "
                "stats.detection_limit_mm_yr is mandatory (README §13: a null result must "
                "publish its bound)"
            )
    return problems


def test_detection_limit_guard_is_not_vacuous() -> None:
    """Non-vacuity: the pre-fix rule accepted NaN/±inf/negative in both branches."""
    for value in (float("nan"), float("inf"), float("-inf"), -1.5):
        stats = _meta(median_velocity_mm_yr=ORDINARY_MEDIAN, detection_limit_mm_yr=value)["stats"]
        assert _pre_fix_detection_limit_problems(stats) == [], (
            f"the pre-fix rule rejected detection_limit_mm_yr={value!r}, so this would not "
            "demonstrate the hole"
        )
        assert _meta_problems(_meta(median_velocity_mm_yr=ORDINARY_MEDIAN, detection_limit_mm_yr=value))

    # A NaN bound was not caught by the *mandatory* branch either: `isinstance(nan, float)` is True,
    # so a null result counted as "bound published" while carrying no usable number.
    null_result_nan = _meta(median_velocity_mm_yr=-1.2, detection_limit_mm_yr=float("nan"))
    assert _pre_fix_detection_limit_problems(null_result_nan["stats"]) == [], (
        "the mandatory branch already rejected a NaN bound; do not claim it as a new rule"
    )
    assert _meta_problems(null_result_nan)


# ---------------------------------------------------------------------------------------------
# 8. `validate_grid`'s numeric gate: better message, *identical* verdicts.
#
# The gate is `astype("float64", errors="ignore")` followed by a `try` body that raises for a column
# it could not read, so the verdict already existed: the complaint is that the message named the
# column and not the offending cell. The sample below is computed after the verdict, from the raw
# column, so it cannot change an accept/reject decision.
# ---------------------------------------------------------------------------------------------

#: `(accepted, number of problems, columns named in a "is not numeric" message)` per mutation,
#: recorded from `validate_grid` *before* the message change (the pre-fix function body is otherwise
#: ~200 lines, so the verdicts themselves are the fixture). If any `(accepted, count)` moves, this
#: change was not message-only. `lat="2.1"` is the interesting row: `errors="ignore"` still *coerces*
#: a numeric string, so it must stay ACCEPTED — the improvement must not turn the gate into a type
#: check.
PRE_FIX_GRID_VERDICTS: tuple[tuple[str, Any, tuple[bool, int, tuple[str, ...]]], ...] = (
    ("lat", "abc", (False, 1, ("lat",))),
    ("lat", "2.1", (True, 0, ())),
    ("lat", "-inf", (False, 1, ())),
    ("lat", [1.0], (False, 1, ("lat",))),
    ("lat", {"a": 1}, (False, 1, ("lat",))),
    ("lat", float("nan"), (False, 2, ())),
    ("lon", "abc", (False, 1, ("lon",))),
    ("lon", "2.1", (True, 0, ())),
    ("lon", "-inf", (False, 1, ())),
    ("lon", [1.0], (False, 1, ("lon",))),
    ("lon", {"a": 1}, (False, 1, ("lon",))),
    ("lon", float("nan"), (False, 2, ())),
    ("val_t1", "abc", (False, 1, ("val_t1",))),
    ("val_t1", "2.1", (True, 0, ())),
    ("val_t1", "-inf", (False, 1, ())),
    ("val_t1", [1.0], (False, 1, ("val_t1",))),
    ("val_t1", {"a": 1}, (False, 1, ("val_t1",))),
    ("val_t1", float("nan"), (False, 2, ())),
    ("val_t2", "abc", (False, 1, ("val_t2",))),
    ("val_t2", "2.1", (True, 0, ())),
    ("val_t2", "-inf", (False, 1, ())),
    ("val_t2", [1.0], (False, 1, ("val_t2",))),
    ("val_t2", {"a": 1}, (False, 1, ("val_t2",))),
    ("val_t2", float("nan"), (False, 2, ())),
    ("delta_change", "abc", (False, 1, ("delta_change",))),
    ("delta_change", "2.1", (True, 0, ())),
    ("delta_change", "-inf", (False, 1, ())),
    ("delta_change", [1.0], (False, 1, ("delta_change",))),
    ("delta_change", {"a": 1}, (False, 1, ("delta_change",))),
    ("delta_change", float("nan"), (False, 2, ())),
    ("velocity_mm_yr", "abc", (False, 2, ("ratio_R", "velocity_mm_yr"))),
    ("velocity_mm_yr", "2.1", (False, 1, ())),
    ("velocity_mm_yr", "-inf", (False, 1, ())),
    ("velocity_mm_yr", [1.0], (False, 2, ("ratio_R", "velocity_mm_yr"))),
    ("velocity_mm_yr", {"a": 1}, (False, 2, ("ratio_R", "velocity_mm_yr"))),
    ("velocity_mm_yr", float("nan"), (False, 2, ())),
    ("seasonal_amp_mm", "abc", (False, 2, ("ratio_R", "seasonal_amp_mm"))),
    ("seasonal_amp_mm", "2.1", (False, 1, ())),
    ("seasonal_amp_mm", "-inf", (False, 3, ())),
    ("seasonal_amp_mm", [1.0], (False, 2, ("ratio_R", "seasonal_amp_mm"))),
    ("seasonal_amp_mm", {"a": 1}, (False, 2, ("ratio_R", "seasonal_amp_mm"))),
    ("seasonal_amp_mm", float("nan"), (False, 4, ())),
    ("ratio_R", "abc", (False, 1, ("ratio_R",))),
    ("ratio_R", "2.1", (False, 1, ())),
    ("ratio_R", "-inf", (False, 3, ())),
    ("ratio_R", [1.0], (False, 1, ("ratio_R",))),
    ("ratio_R", {"a": 1}, (False, 1, ("ratio_R",))),
    ("ratio_R", float("nan"), (False, 1, ())),
    ("coherence", "abc", (False, 1, ("coherence",))),
    ("coherence", "2.1", (False, 1, ())),
    ("coherence", "-inf", (False, 1, ())),
    ("coherence", [1.0], (False, 1, ("coherence",))),
    ("coherence", {"a": 1}, (False, 1, ("coherence",))),
    ("coherence", float("nan"), (False, 1, ())),
)

GRID_MUTATION_PARAMS = [
    pytest.param(column, value, expected, id=f"{column}-{value!r}")
    for column, value, expected in PRE_FIX_GRID_VERDICTS
]


def _numeric_flagged(problems: list[str]) -> tuple[str, ...]:
    """Columns named by a "is not numeric" message, independent of the new sample suffix."""
    return tuple(
        sorted(
            {
                match.group(1)
                for problem in problems
                for match in [re.match(r"grid: (\S+) is not numeric", problem)]
                if match
            }
        )
    )


@pytest.mark.parametrize("column,value,expected", GRID_MUTATION_PARAMS)
def test_grid_numeric_gate_verdict_is_unchanged(
    column: str, value: Any, expected: tuple[bool, int, tuple[str, ...]]
) -> None:
    accepted, count, flagged = expected
    problems = C.validate_grid(_grid(**{column: value}))
    assert (not problems, len(problems), _numeric_flagged(problems)) == (accepted, count, flagged), (
        f"grid {column}={value!r}: the accept/reject verdict (or which columns were flagged) "
        f"moved. This change was meant to be message-only.\nRecorded pre-fix: {expected}\nNow: "
        f"{problems}"
    )


def _pre_fix_grid_message(column: str) -> str:
    """Verbatim pre-fix message for a column the numeric gate could not read."""
    return f"grid: {column} is not numeric"


BAD_CELL = "not-a-number"
NUMERIC_GATE_COLUMNS = (
    "lat",
    "lon",
    "val_t1",
    "val_t2",
    "delta_change",
    "velocity_mm_yr",
    "seasonal_amp_mm",
    "ratio_R",
    "coherence",
)


@pytest.mark.parametrize("column", NUMERIC_GATE_COLUMNS)
def test_grid_not_numeric_message_names_the_offending_cell(column: str) -> None:
    problems = C.validate_grid(_grid(**{column: BAD_CELL}))
    matching = [p for p in problems if p.startswith(_pre_fix_grid_message(column))]
    assert matching, (
        f"{column}: the gate no longer reports the column at all; every grid check must keep its "
        f"message. Reported: {problems}"
    )
    message = matching[0]
    assert repr(BAD_CELL) in message and "str" in message, (
        f"the message must show a sample value and its type so a broken file can be opened at the "
        f"right row, got {message!r}"
    )
    # Non-vacuity: the pre-fix wording carried neither the value nor its type.
    assert repr(BAD_CELL) not in _pre_fix_grid_message(column)
    assert "(str)" not in _pre_fix_grid_message(column)


def test_grid_message_samples_a_container_cell_verbatim() -> None:
    problems = C.validate_grid(_grid(lat=[19.4]))
    assert len(problems) == 1, problems
    assert problems[0] == "grid: lat is not numeric, e.g. [19.4] (list)", problems


def test_grid_message_is_unchanged_when_the_column_is_readable() -> None:
    """No sample is appended where the gate succeeded: a clean grid still reports nothing."""
    assert C.validate_grid(_grid()) == []
    # ...and a numeric string is still coerced by `errors="ignore"`, i.e. still ACCEPTED. Turning
    # that into a rejection would be a new check, not a message improvement.
    assert C.validate_grid(_grid(lat="19.4326")) == [], (
        "the message change must not turn the coercion in the existing gate into a type check"
    )
    assert C.validate_grid(_grid(val_t1="0.0", val_t2="-7.4", delta_change="-7.4")) == []
