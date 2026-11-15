"""Guard: the two producers of `meta.stats` must not drift apart again.

Why this file exists
--------------------
`meta.stats` has *two independent producers* — `pipeline/mock/artifacts.py::build_meta` and
`pipeline/process_case.py::_stats` — and nothing compared them, so they diverged unnoticed until a
scoping study tripped over it: the real chain published `n_harmonics` where the mock published
`seasonal_n_harmonics`, and the real path published no `design_condition` at all, while
`docs/data-contract-v1.md` claimed the extra stats keys are written by *both* producers. The repair
(`fix(realpath): publish the seasonal evidence the mock already publishes`) aligned the names by
hand. This file is the durable half of that repair: a hand fix without a guard drifts again.

What is asserted
----------------
1. **The key sets agree, apart from a named list of asymmetries.** `MOCK_ONLY` / `REAL_ONLY` below
   are the asymmetries that exist today, each with its reason. The guard is an *equality* with that
   baseline, in both directions: a key the mock writes and the real path does not **and** a key the
   real path writes and the mock does not both fail, and so does a pinned asymmetry that disappears.
   The failure message quotes the full symmetric difference, so the next reader sees exactly what
   drifted instead of a bare `assert {...} == {...}`.
2. **The two keys that were just aligned stay aligned and stay meaningful.** `seasonal_n_harmonics`
   is the order actually fitted (2 on the shipped 2-year window, 0 in the not-fitted state) and
   `design_condition` is the fit's own column-normalised conditioning, finite and
   `<= transforms.MAX_DESIGN_CONDITION` — re-derived here from the *published* date axis, not
   restated from prose.
3. **`_stats` stays the only writer of a real `stats` key**, which is the premise that makes the
   measurement below complete.

How the two key sets are measured — and what that cannot cover
--------------------------------------------------------------
**Mock: measured end to end.** `pipeline.generate_mock.generate_case` writes all four artefacts for
a real (patched) study config under `tmp_path` — including the post-write gate `checks.assert_case`
— and the key set is read from the generated `meta.json`. Two states are measured because the key
set is state-dependent: the shipped 2-year window (`fitted`) and the same case with
`mock.seasonal_amplitude_mm: 0` (`not_fitted`, the path that also carries the null-result
`detection_limit_mm_yr`). The union over the two states is compared.

**Real path: `_stats` is called directly, not through `process_case`.** Running the real chain end
to end needs synthetic HDF5 granules (see `tests/test_pipeline_integration.py`) — reusable, but not
cheap enough for a guard meant to run on every edit. Instead `_stats` is invoked with the same
signature its only call site in `process_case.py` uses, in the three states that reach different
branches of the literal: `fitted` with no declared detection bound, `fitted` on a case that declares
one (`detection_limit_config_mm_yr`), and `not_fitted` (`harmonics=0`, 0.5-year span, undefined
scatter). The union over the three is compared. This is a *measurement* of the producer's emitted
dict — not a regex or AST reading of its source — so it cannot be fooled by a key built at runtime.

What this method **cannot** cover, stated plainly:

* it does not exercise the real chain end to end, so a stats key added by `_meta` or by any caller
  *after* `_stats` returns would not be seen. `test_stats_has_no_second_writer_in_the_real_chain`
  re-checks the premise (no `stats[...] = ...` / `stats.setdefault(...)` outside `_stats`), so a
  second writer fails this file instead of silently widening the blind spot;
* `velocity_metrics` is called with a 1-D per-cell vector, exactly like the chain, so the
  `gradient_*` keys it emits only for a 2-D field are never produced — they cannot appear in a real
  `meta.json` either;
* only top-level keys are compared. Nested objects (`per_cell_series`, `n_epochs_dropped`) are
  allowed to differ in shape; their contents are the contract's business, not this file's.

Not asserted here, on purpose: `n_context_features` is **not** drift — the mock counts the context
features it invents, the real path's context comes from `pipeline/enrich_context.py`.
`docs/data-contract-v1.md` names it the one cited extra key only the mock writes, and the test
asserts its *presence* on the mock side so a deletion cannot pass as "no asymmetry". `n_series_rows`
is the second mock-only key and is measured, not assumed (the doc's sentence is about the *cited*
keys). Nothing here may add a key to `contracts.STATS_REQUIRED_KEYS`: that tuple moves
`contract_fingerprint()`, and the extra keys this file compares are deliberately outside it.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys

import numpy as np
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts the cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

# Both producers are hard requirements of this chain, not of its pure helpers.
pytest.importorskip("h5py")
h3 = pytest.importorskip("h3")

from pipeline import process_case as P  # noqa: E402
from pipeline.generate_mock import DEFAULT_SEED, generate_case  # noqa: E402
from pipeline.mock.cases import load_study_cases  # noqa: E402
from pipeline.science import transforms as T  # noqa: E402
from src.domain import contracts as C  # noqa: E402

CONFIG = ROOT / "config" / "study_cases.yaml"
#: The shipped 2-year window with a *declared* null result (`detection_limit_mm_yr: 1.5`), so one
#: generated case reaches both conditional mock keys.
MOCK_CASE = "malaga"
#: A case with no declared detection bound: the absent branch of `detection_limit_config_mm_yr`.
REAL_CASE_WITHOUT_BOUND = "mexico-city"
#: What `mock.seasonal_amplitude_mm: 0` means for a case: no annual term is in the model.
NO_SEASONALITY = 0.0
#: The order the shipped 2-year window can identify (2 * 0.75 yr <= 2.004 cycles, 62 >= 6 epochs).
SHIPPED_ORDER = 2

#: Keys the mock writes and the real path does not. Presence is asserted, not merely tolerated:
#: a deletion from the mock is drift too, and the contract doc names the first one explicitly.
MOCK_ONLY: dict[str, str] = {
    "n_context_features": (
        "docs/data-contract-v1.md: the mock counts the context features it invents; the real path's "
        "context comes from pipeline/enrich_context.py and is not a meta.stats key there"
    ),
    "n_series_rows": (
        "the mock writes timeseries.csv itself and publishes its row count; the real path reports "
        "the series through n_dates (per-cell rows are opt-in in both producers)"
    ),
}

#: The physical diagnostics only the real chain computes, via `transforms.velocity_metrics` and its
#: own coverage bookkeeping. The mock has no storage-loss, risk-band or epoch-dropping model, so it
#: cannot publish them; they are named here so that an *undeclared* new key on either side fails.
_VELOCITY_DIAGNOSTICS = (
    "velocity_max_mm_yr",
    "velocity_mean_mm_yr",
    "velocity_min_mm_yr",
    "velocity_std_mm_yr",
    "velocity_n_valid",
    "velocity_n_missing",
    "risk_level_median",
    "risk_level_worst",
    "compaction_area_m2",
    "compaction_volume_m3_yr",
    "storage_loss_m3_yr",
)
_COVERAGE_DIAGNOSTICS = ("aoi_cells", "covered_aoi_cells", "coverage_fraction", "span_years")

REAL_ONLY: dict[str, str] = {
    **dict.fromkeys(
        _VELOCITY_DIAGNOSTICS,
        "from transforms.velocity_metrics: the real chain's storage-loss / risk / spread "
        "diagnostics; the mock's synthetic model has no such quantities to report",
    ),
    **dict.fromkeys(
        _COVERAGE_DIAGNOSTICS,
        "AOI coverage bookkeeping of real granules; the mock's grid IS its AOI, so the fraction is "
        "1 by construction and there is nothing to report",
    ),
    "n_epochs_dropped": (
        "the real chain drops epochs (coherence, NaN phase) and reports why per reason; the mock's "
        "stack is complete by construction"
    ),
    "detection_limit_config_mm_yr": (
        "the real path republishes the declared config bound beside the computed "
        "detection_limit_mm_yr; the mock publishes the declared bound as detection_limit_mm_yr"
    ),
}

#: `stats` key writes in `process_case.py`, used to prove `_stats` is the only writer.
_STATS_WRITE_RE = re.compile(r"\bstats\s*\[[^\]]*\]\s*=|stats\.setdefault\(")

#: Two real H3 res-9 cells, enough for `_stats` to ask `h3.cell_area` for an area.
_SAMPLE_CELLS = (
    h3.latlng_to_cell(19.4326, -99.1332, 9),
    h3.latlng_to_cell(19.4000, -99.1500, 9),
)
_VELOCITY = np.array([-10.0, -20.0])


# ---------------------------------------------------------------------------------------------
# Measurement: the mock producer, end to end
# ---------------------------------------------------------------------------------------------


def study_variant(root: pathlib.Path, case_id: str, *, seasonal_amplitude_mm: float | None = None):
    """The shipped study config with at most one knob changed, written under `root` and loaded.

    A real config file (not a hand-built `CaseSpec`) goes through `load_study_cases`, so the case
    keeps its bbox, AOI, seed and dates: the only difference from the shipped case is the knob under
    test. `config/aoi/<case>.geojson` is copied next to the patched YAML because
    `load_study_cases` resolves the AOI relative to the config's own directory.
    """
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if seasonal_amplitude_mm is not None:
        raw["cases"][case_id]["mock"]["seasonal_amplitude_mm"] = float(seasonal_amplitude_mm)
    config_dir = pathlib.Path(root) / f"{case_id}__amp-{seasonal_amplitude_mm}"
    config_dir.mkdir(parents=True, exist_ok=False)
    path = config_dir / "study_cases.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    shutil.copytree(CONFIG.parent / "aoi", config_dir / "aoi")
    return load_study_cases(path), config_dir


def mock_meta_by_state(root: pathlib.Path) -> dict[str, dict]:
    """Generate the mock case in both seasonal states; return `{state: meta.json payload}`.

    `generate_case` runs the post-write gate (`checks.assert_case`) on the files it just wrote, so
    an `AssertionError` raised here is the producer refusing its own artefacts — which is the right
    way for this fixture to fail.
    """
    measured: dict[str, dict] = {}
    for label, amplitude in (("fitted", None), ("not_fitted", NO_SEASONALITY)):
        study, config_dir = study_variant(root, MOCK_CASE, seasonal_amplitude_mm=amplitude)
        out_dir = config_dir / "out"
        generate_case(study.cases[MOCK_CASE], study, out_dir, DEFAULT_SEED, per_cell_series=False)
        meta_path = out_dir / MOCK_CASE / "meta.json"
        assert meta_path.is_file(), f"{meta_path} was not written: the mock case did not publish meta"
        measured[label] = json.loads(meta_path.read_text(encoding="utf-8"))
    return measured


@pytest.fixture(scope="module")
def mock_meta(tmp_path_factory) -> dict[str, dict]:
    """One generation per seasonal state, shared by every test in this file (~1 s each)."""
    return mock_meta_by_state(tmp_path_factory.mktemp("producer-parity"))


# ---------------------------------------------------------------------------------------------
# Measurement: the real producer's `_stats`, called the way the chain calls it
# ---------------------------------------------------------------------------------------------


def synthetic_axis(span_years: float, n_epochs: int) -> tuple[np.ndarray, np.ndarray]:
    """A month-free synthetic stack with an injected trend + annual pair, and its time axis.

    The values only have to be well conditioned: this test asks `_stats` for its *keys* and for the
    order/conditioning it publishes, never for the physics of a synthetic series.
    """
    t_years = np.linspace(0.0, span_years, n_epochs)
    series = (
        3.0 * np.cos(2.0 * np.pi * t_years)
        + 1.0 * np.sin(2.0 * np.pi * t_years)
        - 5.0 * t_years
    )
    return t_years, series


def real_stats(
    *,
    harmonics: int,
    design_condition: float,
    span_years: float,
    n_epochs: int,
    sigma_median: float,
    case_id: str,
) -> dict:
    """One `_stats` call with the signature its only call site in `process_case.py` uses.

    The per-cell arrays follow the order: with `harmonics == 0` nothing seasonal was fitted, so the
    amplitudes are exactly 0.0, every `ratio_R` is undefined (NaN/None, never a sentinel) and every
    regime is `transitional` — the state the contract calls "not fitted".
    """
    fitted = int(harmonics) > 0
    return P._stats(
        velocity=_VELOCITY,
        seasonal_amp=np.array([2.0, 3.0]) if fitted else np.zeros(2),
        ratio_R=np.array([0.2, 0.15]) if fitted else np.full(2, np.nan),
        regime=["elastic", "elastic"] if fitted else ["transitional", "transitional"],
        cells=_SAMPLE_CELLS,
        n_dates=int(n_epochs),
        dropped={"coherence": 3},
        coverage=P.Coverage(
            aoi_cells=2, covered_cells=2, covered_in_aoi=2, covered_outside_aoi=0, fraction=1.0
        ),
        span_years=float(span_years),
        n_epochs=int(n_epochs),
        harmonics=int(harmonics),
        design_condition=float(design_condition),
        sigma_median=float(sigma_median),
        case=P.load_case_config(case_id),
    )


def real_fit(*, fitted: bool) -> tuple[np.ndarray, dict]:
    """A real `harmonic_fit` on a synthetic axis; return its time axis and `_stats` for it.

    Nothing is typed twice: the `design_condition` and the order handed to `_stats` are the fit's
    own outputs, so the assertions below can prove the published keys carry *fitted* values.
    """
    span_years, n_epochs = (2.0, 62) if fitted else (0.5, 4)
    t_years, series = synthetic_axis(span_years, n_epochs)
    order = T.harmonic_order(n_epochs, span_years)
    fit = T.harmonic_fit(t_years, series, n_harmonics=order)
    stats = real_stats(
        harmonics=order,
        design_condition=fit.design_condition,
        span_years=span_years,
        n_epochs=n_epochs,
        # A fitted 2-year stack has a defined residual scatter; the 4-epoch one is given an
        # undefined scatter so the conditional `detection_limit_mm_yr` branch is not taken.
        sigma_median=8.0 if fitted else float("nan"),
        case_id=REAL_CASE_WITHOUT_BOUND,
    )
    return t_years, {"order": order, "fit": fit, "stats": stats}


def real_stats_by_state() -> dict[str, dict]:
    """The three reachable branches of `_stats`'s literal, keyed by the state they reach."""
    _, fitted = real_fit(fitted=True)
    _, not_fitted = real_fit(fitted=False)
    # `detection_limit_config_mm_yr` needs a case with a declared bound AND a defined scatter.
    without_declared_bound = fitted["stats"]
    span_years, n_epochs = 2.0, 62
    t_years, series = synthetic_axis(span_years, n_epochs)
    order = T.harmonic_order(n_epochs, span_years)
    fit = T.harmonic_fit(t_years, series, n_harmonics=order)
    with_declared_bound = real_stats(
        harmonics=order,
        design_condition=fit.design_condition,
        span_years=span_years,
        n_epochs=n_epochs,
        sigma_median=8.0,
        case_id=MOCK_CASE,  # the shipped case that declares detection_limit_mm_yr: 1.5
    )
    return {
        "fitted": without_declared_bound,
        "fitted_with_declared_bound": with_declared_bound,
        "not_fitted": not_fitted["stats"],
    }


def keys_union(state_mapping: dict[str, dict]) -> set[str]:
    """Union of the `stats` keys across the measured states (the key set is state-dependent)."""
    measured = set()
    for state, payload in state_mapping.items():
        stats = payload.get("stats", payload) if isinstance(payload, dict) else payload
        assert isinstance(stats, dict) and stats, f"no stats dict was measured for state {state!r}"
        measured |= set(stats)
    return measured


def mock_stats_keys(mock_meta: dict[str, dict]) -> set[str]:
    """Union of the mock's `meta.stats` keys over the generated states."""
    return keys_union({state: meta["stats"] for state, meta in mock_meta.items()})


# ---------------------------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------------------------


def parity_problems(mock_keys, real_keys) -> list[str]:
    """Every way the two key sets have drifted, as human-readable problems (empty == aligned).

    Both directions are computed: a key only the mock writes and a key only the real path writes are
    each reported against their own named baseline. A pinned asymmetry that *disappeared* is a
    problem too — otherwise this file would quietly lose the evidence it exists to keep.
    """
    mock_only = set(mock_keys) - set(real_keys)
    real_only = set(real_keys) - set(mock_keys)
    unexpected_mock = sorted(mock_only - set(MOCK_ONLY))
    unexpected_real = sorted(real_only - set(REAL_ONLY))
    missing_mock = sorted(set(MOCK_ONLY) - mock_only)
    missing_real = sorted(set(REAL_ONLY) - real_only)
    if not (unexpected_mock or unexpected_real or missing_mock or missing_real):
        return []
    return [
        "meta.stats drifted between the producers.\n"
        f"  mock-only keys (mock writes, real path does not): {sorted(mock_only)}\n"
        f"  real-only keys (real path writes, mock does not): {sorted(real_only)}\n"
        f"  NEW mock-only key(s), not in MOCK_ONLY: {unexpected_mock}\n"
        f"  NEW real-only key(s), not in REAL_ONLY: {unexpected_real}\n"
        f"  pinned mock-only key(s) the mock no longer writes: {missing_mock}\n"
        f"  pinned real-only key(s) the real path no longer writes: {missing_real}\n"
        "Repair the producer (preferred — the two paths must publish the same seasonal evidence; "
        "that is exactly the `n_harmonics` vs `seasonal_n_harmonics` / missing `design_condition` "
        "drift this file exists for), or add the key to MOCK_ONLY/REAL_ONLY with the reason it can "
        "only come from one producer. Do NOT add it to contracts.STATS_REQUIRED_KEYS: that tuple is "
        "part of contract_fingerprint()."
    ]


def assert_parity(mock_keys, real_keys) -> None:
    """The guard entry point. Public so the anti-vacuity check can drive it with mutated sets."""
    problems = parity_problems(mock_keys, real_keys)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------------------------
# 1. The key sets agree, apart from the named asymmetries.
# ---------------------------------------------------------------------------------------------


def test_stats_key_sets_agree_apart_from_the_named_asymmetries(mock_meta):
    """Equality of the two key sets, with `MOCK_ONLY`/`REAL_ONLY` as the only allowed difference.

    This is the assertion the whole file exists for. It is an equality, not a subset: the vacuous
    version (`mock_keys <= real_keys`) passes as soon as the mock drops a key, and it cannot see a
    key the real path invents, which is precisely how `n_harmonics` and the missing
    `design_condition` survived review.
    """
    mock_keys = mock_stats_keys(mock_meta)
    real_keys = keys_union(real_stats_by_state())

    assert_parity(mock_keys, real_keys)

    # The documented exception is present, not merely tolerated: a mock that quietly stopped
    # publishing it would make the parity trivially true and lose a documented stat.
    mock_only = mock_keys - real_keys
    assert "n_context_features" in mock_only, (
        "the mock no longer publishes n_context_features, the one extra key "
        "docs/data-contract-v1.md says only the mock writes. mock-only keys: "
        f"{sorted(mock_only)}"
    )
    assert mock_only == set(MOCK_ONLY), (
        f"the mock-only key set is {sorted(mock_only)}, not the pinned {sorted(MOCK_ONLY)}"
    )
    assert real_keys - mock_keys == set(REAL_ONLY), (
        f"the real-only key set is {sorted(real_keys - mock_keys)}, not the pinned "
        f"{sorted(REAL_ONLY)}"
    )

    # The contract's required subset must be published by both, and the compared extras must stay
    # outside it (nothing here may move contract_fingerprint()).
    required = set(C.STATS_REQUIRED_KEYS)
    assert required <= mock_keys, f"the mock omits required stats keys {sorted(required - mock_keys)}"
    assert required <= real_keys, f"the real path omits required stats keys {sorted(required - real_keys)}"
    assert not required & (set(MOCK_ONLY) | set(REAL_ONLY)), (
        "a named producer asymmetry is also a STATS_REQUIRED_KEYS member: "
        f"{sorted(required & (set(MOCK_ONLY) | set(REAL_ONLY)))}"
    )
    for aligned in ("seasonal_n_harmonics", "design_condition"):
        assert aligned in mock_keys and aligned in real_keys, (
            f"stats.{aligned} is published by only one producer "
            f"(mock: {aligned in mock_keys}, real: {aligned in real_keys}); that is the drift the "
            "seasonal-evidence repair fixed"
        )


def test_stats_required_keys_unchanged():
    """The guard must not have been made true by moving the contract.

    `STATS_REQUIRED_KEYS` feeds `contract_fingerprint()`, so growing it would change every
    provenance record to make this file pass. The 8 members are pinned here by value.
    """
    assert C.STATS_REQUIRED_KEYS == (
        "n_cells",
        "n_dates",
        "median_velocity_mm_yr",
        "median_seasonal_amp_mm",
        "median_ratio_R",
        "dominant_regime",
        "p05_velocity_mm_yr",
        "p95_velocity_mm_yr",
    ), f"STATS_REQUIRED_KEYS changed to {C.STATS_REQUIRED_KEYS}; it is part of the fingerprint"
    for extra in ("seasonal_n_harmonics", "design_condition", "annual_coverage", "n_context_features"):
        assert extra not in C.STATS_REQUIRED_KEYS, (
            f"{extra} was moved into STATS_REQUIRED_KEYS; the extra stats keys this file compares "
            "are contract-optional and must stay so"
        )


def test_the_guard_reports_drift_in_both_directions():
    """Prove the guard is not one-sided: a mock-only AND a real-only new key each fail it.

    Driving `parity_problems` with the measured baseline plus one invented key on each side shows
    that neither direction can pass silently — the reverse case is the one a `<=` assertion misses.
    """
    shared = {"n_cells", "seasonal_n_harmonics", "design_condition"}
    baseline_mock = shared | set(MOCK_ONLY)
    baseline_real = shared | set(REAL_ONLY)
    assert parity_problems(baseline_mock, baseline_real) == [], (
        "the pinned baseline does not describe itself as aligned; the guard's own bookkeeping is "
        f"broken: {parity_problems(baseline_mock, baseline_real)}"
    )

    mock_extra = parity_problems(baseline_mock | {"n_harmonics"}, baseline_real)
    assert mock_extra, "a key the mock writes and the real path does not was accepted"
    assert "n_harmonics" in mock_extra[0] and "NEW mock-only" in mock_extra[0], (
        f"the mock-only direction is not named in the failure message:\n{mock_extra[0]}"
    )

    real_extra = parity_problems(baseline_mock, baseline_real | {"design_condition_typed"})
    assert real_extra, "a key the real path writes and the mock does not was accepted"
    assert "design_condition_typed" in real_extra[0] and "NEW real-only" in real_extra[0], (
        f"the real-only direction is not named in the failure message:\n{real_extra[0]}"
    )

    # Dropping a shared key from one side makes it an undeclared asymmetry in the other direction.
    dropped = parity_problems(baseline_mock - {"seasonal_n_harmonics"}, baseline_real)
    assert dropped and "seasonal_n_harmonics" in dropped[0], (
        "deleting a shared key from the mock was accepted; the guard must notice a key that "
        f"vanished:\n{dropped}"
    )
    # And losing a pinned asymmetry is reported, so this file cannot silently forget it.
    forgotten = parity_problems(baseline_mock - {"n_context_features"}, baseline_real)
    assert forgotten and "pinned mock-only" in forgotten[0], (
        f"a pinned asymmetry that disappeared was accepted:\n{forgotten}"
    )


def _stats_span(source: list[str]) -> tuple[int, int]:
    """1-indexed line span of the module-level `def _stats(` body, read from the file on disk.

    Located by scanning the source rather than by `inspect.getsourcelines`, so a test that wraps
    `_stats` (the anti-vacuity harness in `/tmp` does exactly that) cannot make this check report a
    false second writer.
    """
    start = next(
        (
            number
            for number, line in enumerate(source, start=1)
            if line.startswith("def _stats(") or line.startswith("def _stats ")
        ),
        None,
    )
    assert start is not None, f"{P.__file__} no longer defines a module-level `_stats`"
    end = len(source)
    for number in range(start + 1, len(source) + 1):
        line = source[number - 1]
        if line.startswith("def ") or line.startswith("class "):
            end = number - 1
            break
    return start, end


def test_stats_has_no_second_writer_in_the_real_chain():
    """`_stats` is the only place a real `stats` key is written — the premise of the measurement.

    The key set above is measured by calling `_stats` directly instead of running the chain end to
    end; that is only complete while nothing else assigns into the dict. A second writer fails here
    rather than quietly releasing an artefact-only key from the guard.
    """
    source = pathlib.Path(P.__file__).read_text(encoding="utf-8").splitlines()
    first, last = _stats_span(source)
    offenders = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(source, start=1)
        if _STATS_WRITE_RE.search(line) and not first <= number <= last
    ]
    assert not offenders, (
        "a stats key is written outside `_stats` "
        f"({P.__file__}, `_stats` spans lines {first}-{last}):\n  " + "\n  ".join(offenders)
        + "\n`_stats` is the only writer this file's key-set measurement assumes; either fold the "
        "extra key into `_stats` or measure the real chain end to end instead."
    )


# ---------------------------------------------------------------------------------------------
# 2. `seasonal_n_harmonics` is the order actually fitted.
# ---------------------------------------------------------------------------------------------


def test_seasonal_n_harmonics_is_the_order_actually_fitted(mock_meta):
    """Both producers publish the fitted order: 2 on the shipped 2-year window, 0 when not fitted."""
    # -- the mock, from the generated artefacts --------------------------------------------------
    fitted = mock_meta["fitted"]["stats"]
    dates = sorted(mock_meta["fitted"]["dates"])
    span_years = (
        np.datetime64(dates[-1]) - np.datetime64(dates[0])
    ) / np.timedelta64(1, "D") / 365.25
    assert fitted["seasonal_n_harmonics"] == SHIPPED_ORDER, (
        f"the shipped {span_years:.2f}-year window publishes seasonal_n_harmonics = "
        f"{fitted['seasonal_n_harmonics']!r}, not {SHIPPED_ORDER}"
    )
    # The published order is the one a stack of THIS span and THIS many dates can identify.
    assert fitted["seasonal_n_harmonics"] == T.harmonic_order(fitted["n_dates"], span_years), (
        f"seasonal_n_harmonics {fitted['seasonal_n_harmonics']} is not the order "
        f"harmonic_order({fitted['n_dates']} epochs, {span_years:.3f} yr) = "
        f"{T.harmonic_order(fitted['n_dates'], span_years)} returns for the published span: the "
        "declared order must be the order the fit could actually use"
    )
    # A refit on the published date axis at that order is what produced the seasonal columns.
    t_years, series = synthetic_axis(span_years, fitted["n_dates"])
    refit = T.harmonic_fit(t_years, series, n_harmonics=fitted["seasonal_n_harmonics"])
    assert refit.n_harmonics == fitted["seasonal_n_harmonics"], (
        f"harmonic_fit reports {refit.n_harmonics} harmonic(s) but the order asked for was "
        f"{fitted['seasonal_n_harmonics']}"
    )

    not_fitted = mock_meta["not_fitted"]["stats"]
    assert not_fitted["seasonal_n_harmonics"] == 0, (
        f"a case with mock.seasonal_amplitude_mm: 0 publishes seasonal_n_harmonics = "
        f"{not_fitted['seasonal_n_harmonics']!r}; nothing was fitted, so the declared order is 0"
    )
    assert not_fitted["median_seasonal_amp_mm"] == 0.0, (
        "the not-fitted mock case published a non-zero median amplitude "
        f"({not_fitted['median_seasonal_amp_mm']!r})"
    )

    # -- the real path, with the order its own gate derives --------------------------------------
    _, real_fitted = real_fit(fitted=True)
    assert real_fitted["order"] == SHIPPED_ORDER, (
        f"transforms.harmonic_order(62, 2.0) = {real_fitted['order']}, not {SHIPPED_ORDER}"
    )
    assert real_fitted["stats"]["seasonal_n_harmonics"] == SHIPPED_ORDER, (
        "the real path publishes seasonal_n_harmonics = "
        f"{real_fitted['stats']['seasonal_n_harmonics']!r} for a fit that used order "
        f"{real_fitted['order']}"
    )
    _, real_not_fitted = real_fit(fitted=False)
    assert real_not_fitted["order"] == 0, "the 0.5-year / 4-epoch axis must not identify a harmonic"
    assert real_not_fitted["stats"]["seasonal_n_harmonics"] == 0, (
        "the not-fitted real state publishes seasonal_n_harmonics = "
        f"{real_not_fitted['stats']['seasonal_n_harmonics']!r}, not 0"
    )


# ---------------------------------------------------------------------------------------------
# 3. `design_condition` is the fit's own conditioning, finite and inside the gate.
# ---------------------------------------------------------------------------------------------


def _published_condition(mock_meta: dict, state: str) -> tuple[float, float, int, str]:
    """Re-derive the design condition from the published date axis at the published order."""
    meta = mock_meta[state]
    stats = meta["stats"]
    dates = np.array(sorted(meta["dates"]), dtype="datetime64[D]")
    t_years = (dates - dates.min()) / np.timedelta64(1, "D") / 365.25
    order = stats["seasonal_n_harmonics"]
    _, series = synthetic_axis(float(t_years[-1]), t_years.size)
    refit = T.harmonic_fit(t_years, series, n_harmonics=order)
    return float(stats["design_condition"]), float(refit.design_condition), int(order), state


def test_design_condition_is_the_fits_finite_gated_diagnostic(mock_meta):
    """`design_condition` is the fit's conditioning on the published axis — finite and <= 30."""
    conditions = {}
    for state in ("fitted", "not_fitted"):
        published, rederived, order, _ = _published_condition(mock_meta, state)
        assert np.isfinite(published), (
            f"[mock/{state}] stats.design_condition is {published!r}; a published JSON number must "
            "be finite (NaN is not valid JSON and 30 is the gate)"
        )
        assert 0.0 < published <= T.MAX_DESIGN_CONDITION, (
            f"[mock/{state}] stats.design_condition {published} is outside "
            f"(0, {T.MAX_DESIGN_CONDITION}]: a stack that designs above the gate fits no harmonic, "
            "so publishing a larger number means the gate was bypassed"
        )
        assert abs(published - rederived) <= 1e-6, (
            f"[mock/{state}] stats.design_condition {published} is not the fit's own conditioning "
            f"on the published date axis ({rederived} for order {order}); the key must carry the "
            "fitted diagnostic, not a typed constant"
        )
        conditions[state] = published
    assert conditions["fitted"] != conditions["not_fitted"], (
        "both seasonal states publish the same design_condition "
        f"({conditions['fitted']}); the number depends on the order, so a constant is being "
        "published instead of the diagnostic"
    )

    # -- the real path publishes the fit's value, rounded, and refuses a non-finite one -----------
    for fitted in (True, False):
        _, payload = real_fit(fitted=fitted)
        label = "fitted" if fitted else "not_fitted"
        published = payload["stats"].get("design_condition")
        assert published is not None, (
            f"[real/{label}] the real path publishes no stats.design_condition; the fit's "
            f"conditioning ({payload['fit'].design_condition}) is exactly the evidence the "
            "seasonal trio is gated on, so a reader cannot re-run the gate without it"
        )
        expected = round(float(payload["fit"].design_condition), 6)
        assert published == expected, (
            f"[real/{label}] stats.design_condition {published} is not the fit's "
            f"{payload['fit'].design_condition} (order {payload['order']}) rounded to 6 dp; the "
            "real path must republish aoi_fit.design_condition, not a constant"
        )
        assert np.isfinite(published) and published <= T.MAX_DESIGN_CONDITION, (
            f"[real/{label}] stats.design_condition {published} is not finite or exceeds "
            f"{T.MAX_DESIGN_CONDITION}"
        )

    with pytest.raises(P.ProcessCaseError, match="not finite"):
        real_stats(
            harmonics=SHIPPED_ORDER,
            design_condition=float("nan"),
            span_years=2.0,
            n_epochs=62,
            sigma_median=8.0,
            case_id=REAL_CASE_WITHOUT_BOUND,
        )
