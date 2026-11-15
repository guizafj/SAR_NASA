"""Regression: the MOCK producer publishes the "not fitted" seasonal state, never NaN.

Why this file exists
--------------------
Contract 1.1 makes ``seasonal_amp_mm`` a *required* grid column, but a stack whose span cannot
identify an annual harmonic still has to write it. The rule (already implemented by the real chain
in ``pipeline/process_case.py`` and enforced by ``contracts.validate_grid``/``validate_meta``) is
that such a stack publishes:

* ``seasonal_amp_mm = 0.0`` — "no seasonal term was fitted", *not* "we measured no seasonality";
* ``ratio_R = null`` — ``|trend| / 0`` is undefined, so neither ``inf`` nor a sentinel is a value;
* ``regime = "transitional"`` — "inelastic" is a verdict about a *fitted* amplitude this stack does
  not have (``classify_regime`` would happily derive it from ``R = inf``, which is the dishonesty);
* ``stats.median_ratio_R = null`` and ``stats.seasonal_n_harmonics = 0``;
* an explicit ``"seasonal term not fitted"`` limitation, so the null travels with its reason.

The mock used to publish NaN instead, which is not valid JSON: ``build_meta`` computed
``float(pd.Series(ratio).median())`` = NaN, ``write_json``'s ``allow_nan=False`` would refuse it, and
``checks.assert_case`` did ``float(None)``/NaN comparisons on the way there. So a case configured
with ``mock.seasonal_amplitude_mm: 0`` — or a window short enough for
``transforms.harmonic_order`` to return 0 — did not produce a wrong artefact, it crashed.

How the two states are reached, and what is pinned here
------------------------------------------------------
``pipeline/mock/model.py::seasonal_order`` returns 0 in exactly two situations, and both are
*statements about the model*: the case injects no seasonality at all, or the window is below the
0.75 annual-cycle gate. The tests below take one route each, generating a real case end to end
(``config`` -> seed-7 model -> four artefacts) into ``tmp_path``:

1. ``mock.seasonal_amplitude_mm: 0`` over the shipped 2-year window,
2. the shipped amplitude over a 4-date (36 d) window.

Each asserts the published state cell by cell and key by key, that ``contracts.validate_grid`` /
``validate_meta`` accept it, that the post-write gate ``checks.assert_case`` accepts it, and that
every JSON artefact parses with ``allow_nan=False`` (a NaN would have to survive the writer to get
here, and ``json.loads`` accepts NaN by default — hence the strict ``parse_constant``).

The last test is the mirror image, so the new branch cannot quietly silence the fitted guard: a
*fitted* case whose ``ratio_R`` was replaced by a sentinel or by ``inf`` must still fail
``checks.assert_case`` loudly, on a copy in ``tmp_path`` — never on ``data/processed/``.

Nothing here edits ``config/`` or ``data/processed/``: the case config is a patched copy of the
shipped YAML in ``tmp_path`` and every artefact is written under ``tmp_path``.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pandas as pd
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts the cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

from pipeline.generate_mock import DEFAULT_SEED, generate_case  # noqa: E402
from pipeline.mock import checks as K  # noqa: E402
from pipeline.mock.cases import load_study_cases  # noqa: E402
from src.domain import contracts as C  # noqa: E402

CONFIG = ROOT / "config" / "study_cases.yaml"
PROCESSED = ROOT / "data" / "processed"
#: The largest shipped case: the one whose per-cell loop would notice a wrong order first.
CASE = "mexico-city"
#: 4 epochs x 12 d = 36 d = 0.10 annual cycles, far below the 0.75-year annual gate.
SHORT_WINDOW_DATES = 4
ARTEFACTS = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def patched_study(
    tmp_path: pathlib.Path,
    *,
    seasonal_amplitude_mm: float | None = None,
    dates_count: int | None = None,
):
    """The shipped study config with ONE knob changed, written to `tmp_path` and loaded.

    A real config file (not a hand-built `CaseSpec`) is used on purpose: the reachability argument
    is about what `config/study_cases.yaml` may say, so the test has to go through
    `load_study_cases`. Everything else — bbox, AOI ring, ellipse, seed, geography, dates start and
    step — stays exactly as shipped, so the only difference from a shipped case is the seasonal
    knob under test.

    `config/aoi/<case>.geojson` is copied next to the patched YAML because `load_study_cases`
    resolves the AOI relative to the config's own directory: without it the case would silently
    fall back to the bbox envelope and the comparison would be about a different cell set rather
    than about the seasonal term.
    """
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if seasonal_amplitude_mm is not None:
        raw["cases"][CASE]["mock"]["seasonal_amplitude_mm"] = float(seasonal_amplitude_mm)
    if dates_count is not None:
        raw["processing"]["dates_count"] = int(dates_count)
    path = tmp_path / "study_cases.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    shutil.copytree(CONFIG.parent / "aoi", tmp_path / "aoi")
    return load_study_cases(path)


def generate(study, out_dir: pathlib.Path) -> pathlib.Path:
    """Generate one case with the shipped default seed; return its directory.

    `generate_case` writes the four artefacts and then runs the post-write gate
    (`checks.assert_case`) on the files it just wrote, so an AssertionError raised here is the gate
    refusing the published state — which is exactly what a failed regression must look like.
    """
    case = study.cases[CASE]
    generate_case(case, study, out_dir, DEFAULT_SEED, per_cell_series=False)
    directory = out_dir / CASE
    assert all((directory / name).is_file() for name in ARTEFACTS), (
        f"{directory} is missing one of {ARTEFACTS}: the not-fitted case must publish the same four "
        "artefacts as every other case"
    )
    return directory


def _years(dates: pd.Series) -> "pd.Series":
    """Years since the first date, the time axis every fit in this repo uses (365.25 d/yr)."""
    as_dates = pd.to_datetime(dates, format="%Y-%m-%d")
    return (as_dates - as_dates.min()).dt.days / 365.25


def _reject_json_constant(token: str):
    """`json.loads(..., parse_constant=...)` hook: NaN/Infinity are not JSON, so refuse them."""
    raise AssertionError(
        f"the artefact contains the non-JSON constant {token!r}. NaN is not valid JSON: a "
        "not-fitted harmonic publishes seasonal_amp_mm = 0.0 and ratio_R = null instead."
    )


def assert_not_fitted_state(directory: pathlib.Path, *, expected_span_reason: str) -> dict:
    """Assert the whole published "not fitted" state for one generated case; return its meta.

    Everything here is a *positive* claim about the artefact, so a producer that publishes neither
    the fitted nor the not-fitted state cannot pass by omission.
    """
    grid = pd.read_parquet(directory / "nisar_grid.parquet")
    series = pd.read_csv(directory / "timeseries.csv")
    meta_text = (directory / "meta.json").read_text(encoding="utf-8")
    context_text = (directory / "context_layers.geojson").read_text(encoding="utf-8")
    meta = json.loads(meta_text, parse_constant=_reject_json_constant)
    context = json.loads(context_text, parse_constant=_reject_json_constant)

    # -- the grid: column present, amplitude exactly 0.0, ratio null, regime unclassified -------
    assert list(grid.columns) == [field.name for field in C.GRID_FIELDS], (
        f"{directory.name}/nisar_grid.parquet has columns {list(grid.columns)}, not the contract's "
        f"GRID_FIELDS order {[field.name for field in C.GRID_FIELDS]}: the not-fitted state must "
        "not drop or reorder a column"
    )
    assert "ratio_R" in grid.columns, (
        "the not-fitted case dropped ratio_R. Nullable values do not make the column optional: "
        "contract 1.1 requires it even when every value is null."
    )
    amplitude = grid["seasonal_amp_mm"]
    assert (amplitude == 0.0).all(), (
        f"{directory.name}: seasonal_amp_mm must be exactly 0.0 on every cell when no harmonic is "
        f"fitted (the 'not fitted' value), but {int((amplitude != 0.0).sum())} of {len(grid)} "
        f"cell(s) carry something else (max {float(amplitude.max()):.3e})"
    )
    assert grid["ratio_R"].isna().all(), (
        f"{directory.name}: ratio_R must be null on every cell when nothing was fitted — "
        f"|trend| / 0 is undefined, so inf and 1e9 are equally fabricated — but "
        f"{int(grid['ratio_R'].notna().sum())} value(s) were published (first "
        f"{grid['ratio_R'].dropna().iloc[0]!r})"
    )
    assert set(grid["regime"].astype(str)) == {"transitional"}, (
        f"{directory.name}: regime(s) {sorted(set(grid['regime'].astype(str)))} were published. "
        "'inelastic' is a verdict about a FITTED amplitude; with no seasonal term there is no "
        "ratio to classify, so every cell is 'transitional'."
    )

    # -- stats: the aggregates follow the same rule --------------------------------------------
    stats = meta["stats"]
    assert stats["seasonal_n_harmonics"] == 0, (
        f"{directory.name}: stats.seasonal_n_harmonics is {stats['seasonal_n_harmonics']!r}; the "
        "grid published a 0.0 amplitude, so the declared order must say 0 (that key is what "
        "contracts.validate_meta uses to demand the 'not fitted' limitation)"
    )
    assert stats["median_seasonal_amp_mm"] == 0.0, (
        f"{directory.name}: stats.median_seasonal_amp_mm is {stats['median_seasonal_amp_mm']!r}, "
        "not 0.0; the median of an all-zero amplitude column is 0.0 — it must stay a number"
    )
    assert stats["median_ratio_R"] is None, (
        f"{directory.name}: stats.median_ratio_R is {stats['median_ratio_R']!r}. The median of an "
        "undefined ratio is JSON null, not NaN and not 0.0: NaN is not valid JSON and 0.0 would "
        "claim every cell divides evenly."
    )

    # -- the limitation: the null travels with its reason --------------------------------------
    limitations_text = " ".join(str(item) for item in meta["limitations"]).lower()
    assert "not fitted" in limitations_text, (
        f"{directory.name}: no limitation says the seasonal term was not fitted (limitations: "
        f"{meta['limitations']!r}). An amplitude of 0.0 and a null ratio without their reason read "
        "as 'no seasonality was measured', which is a claim about the ground instead of about the "
        "model."
    )
    assert expected_span_reason in limitations_text, (
        f"{directory.name}: the 'not fitted' limitation does not name why THIS case fitted nothing "
        f"(expected to mention {expected_span_reason!r}): {meta['limitations']!r}"
    )

    # -- the contract accepts the state, and every JSON artefact is strict-JSON -----------------
    problems = C.validate_grid(grid, case_id=CASE)
    assert not problems, (
        f"contracts.validate_grid rejected the not-fitted grid; the relaxation for ratio_R null "
        "where seasonal_amp_mm == 0 is contract 1.1 and must accept it:\n  - "
        + "\n  - ".join(problems)
    )
    problems = C.validate_meta(meta)
    assert not problems, (
        "contracts.validate_meta rejected the not-fitted meta:\n  - " + "\n  - ".join(problems)
    )
    problems = C.validate_all(
        grid=grid, timeseries=series, meta=meta, context=context, case_id=CASE
    )
    assert not {name: p for name, p in problems.items() if p}, (
        f"contracts.validate_all refused the not-fitted artefacts: {problems}"
    )
    # Re-serialise what was read from disk with allow_nan=False: the writer already does, so this
    # proves the payload itself carries no NaN/inf (a NaN would be a *value* the file cannot hold).
    json.dumps(meta, allow_nan=False, sort_keys=True)
    json.dumps(context, allow_nan=False)

    return meta


# ---------------------------------------------------------------------------------------------
# 1. A case configured with no seasonality: the reachable path, end to end.
# ---------------------------------------------------------------------------------------------


def test_zero_injected_amplitude_publishes_the_not_fitted_state(tmp_path):
    """`mock.seasonal_amplitude_mm: 0` must publish the not-fitted state, not NaN and not a crash.

    This is the case the mock could already be configured into. `fit_series` used to raise only
    when a NON-zero injected amplitude recovered 0, so a zero injected amplitude reached
    `build_meta` with `amplitude == 0`, where `float(pd.Series(ratio).median())` is NaN — refused
    by `allow_nan=False` — and `checks.assert_case` compared NaN. Both the JSON writer and the gate
    must accept the contract's not-fitted state instead.
    """
    study = patched_study(tmp_path, seasonal_amplitude_mm=0.0)
    case = study.cases[CASE]
    assert case.field.seasonal_amplitude_mm == 0.0, "the patched config did not reach CaseSpec"

    directory = generate(study, tmp_path / "out")
    meta = assert_not_fitted_state(directory, expected_span_reason="injects no seasonality")

    # The series is a pure linear model, so the published split is trend-only: the seasonal
    # component is zeros (the model HAS no seasonal term) and the trend still describes the series.
    series = pd.read_csv(directory / "timeseries.csv")
    aoi = series[series["cell_id"].astype(str) == "aoi"].sort_values("date")
    assert (aoi["seasonal_mm"] == 0.0).all(), (
        f"{directory.name}: timeseries.seasonal_mm must be 0.0 when no harmonic is fitted "
        f"(max |seasonal_mm| = {float(aoi['seasonal_mm'].abs().max()):.3e})"
    )
    residual = (aoi["value"] - aoi["trend_mm_yr"] * _years(aoi["date"])).to_numpy()
    assert float(residual.max() - residual.min()) <= 1e-6, (
        "value - trend_mm_yr * t is not constant: the published trend does not decompose the "
        f"published series (spread {float(residual.max() - residual.min()):.3e} mm)"
    )
    assert meta["provenance"]["product_id"].startswith(f"mock:{CASE}:"), (
        "the not-fitted case must keep its normal provenance: the state is published, not withheld"
    )


# ---------------------------------------------------------------------------------------------
# 2. A window too short for any harmonic: the other route into the same state.
# ---------------------------------------------------------------------------------------------


def test_window_below_the_annual_gate_publishes_the_not_fitted_state(tmp_path):
    """A span `harmonic_order` cannot fit is the same state, reached through the dates.

    The configured amplitude stays at its shipped non-zero value, so the case *does* inject
    seasonality: what is missing is the span to identify it. `transforms.harmonic_order` returns 0
    at 4 epochs / 36 d, and the publication must say "not fitted" rather than reporting the fit's
    noise floor as an amplitude with an infinite ratio.
    """
    study = patched_study(tmp_path, dates_count=SHORT_WINDOW_DATES)
    case = study.cases[CASE]
    assert case.field.seasonal_amplitude_mm > 0.0, (
        "this test is about the WINDOW, not the config: the case must still inject seasonality"
    )
    assert len(study.dates) == SHORT_WINDOW_DATES, "the patched dates_count did not reach the study"

    directory = generate(study, tmp_path / "out")
    assert_not_fitted_state(directory, expected_span_reason="annual cycles")

    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["dates"] == list(study.dates), (
        "meta.dates does not match the configured short window, so the 'not fitted' limitation is "
        "describing a different stack"
    )


# ---------------------------------------------------------------------------------------------
# 3. The fitted guard still bites: a sentinel or a non-finite ratio must fail the gate.
# ---------------------------------------------------------------------------------------------


needs_shipped_case = pytest.mark.skipif(
    not (PROCESSED / CASE / "nisar_grid.parquet").is_file(),
    reason=(
        f"missing data/processed/{CASE}/nisar_grid.parquet — run "
        "./scripts/python.sh pipeline/generate_mock.py; this test mutates a *copy* of a fitted "
        "case, so it needs one to copy"
    ),
)


@needs_shipped_case
@pytest.mark.parametrize(
    ("label", "mutation"),
    [
        ("sentinel", 1e9),
        ("infinite", float("inf")),
    ],
)
def test_a_fitted_case_with_a_sentinel_ratio_still_fails_the_gate(tmp_path, label, mutation):
    """The not-fitted branch must not make the fitted ratio check optional.

    A fitted amplitude has a defined ratio, so writing `1e9` (a sentinel read as "extremely
    inelastic") or `inf` is fabrication. The gate must reject it — this is the guard the new
    branch could have switched off by widening the "null is legal" test, so it is asserted on a
    copy of a shipped, currently-fitted case in `tmp_path`.
    """
    shipped = PROCESSED / CASE
    copy = tmp_path / CASE
    shutil.copytree(shipped, copy)

    grid = pd.read_parquet(copy / "nisar_grid.parquet")
    fitted = grid.index[grid["seasonal_amp_mm"] > 0.0][:1]
    assert len(fitted) == 1, (
        f"fixture is unusable: {CASE}/nisar_grid.parquet has no cell with a fitted amplitude, so "
        "there is no defined ratio to corrupt"
    )
    grid.loc[fitted, "ratio_R"] = mutation
    grid.to_parquet(copy / "nisar_grid.parquet", engine="pyarrow", compression="zstd", index=False)

    study = load_study_cases(CONFIG)
    with pytest.raises(AssertionError) as excinfo:
        K.assert_case(study.cases[CASE], study, copy)

    failures = str(excinfo.value)
    assert "ratio_R is not abs(velocity_mm_yr)/seasonal_amp_mm" in failures, (
        f"checks.assert_case did not flag a {label} ratio_R (value {mutation!r}) on a fitted "
        f"amplitude. The gate must fail loudly on a non-finite or sentinel ratio; reported:\n"
        f"{failures}"
    )
