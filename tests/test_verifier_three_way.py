"""Three-way verifier agreement: the mock's own gate vs the normative contract.

Why this file exists
--------------------
The repo has **three** independent verifiers of the same four artefacts:

1. ``src/domain/contracts.py`` — normative; ``validate_all`` / ``validate_grid`` / ``validate_meta``
   / ``validate_timeseries``. Its word is final.
2. ``src/data/validation.py`` — the CI/app gate. Already proven to agree with (1) rule by rule, and
   deliberately stricter only in cross-artefact checks (``tests/test_validator_parity.py``).
3. ``pipeline/mock/checks.py::assert_case`` — the mock's post-write gate. It gates artefact
   *generation* and carries its **own copies** of several contract rules (§4 nulls, §7
   ratio/regime, §9 stats, §9b decomposition) on top of calling (1).

(1) and (2) were reconciled; (3) never was. That asymmetry is the risk: if (3) is laxer than (1),
the generator can publish something the contract rejects and a green ``generate_mock.py`` run would
be a false comfort; if (3) is stricter than (1), a contract-legal artefact can be refused.

This file drives **both (1) and (3) on the same bundle**, produced by the real generator into a
temp tree (patched YAML, seed 7 — the pattern of ``tests/test_mock_not_fitted.py``), then mutates a
copy into each illegal state and asserts the two verdicts agree in the direction the contract
requires. Nothing here writes ``config/`` or ``data/processed/``.

Measured rule-by-rule verdicts (contract 1.1, seed 7, mutation applied to a copy; (2) columns from
``src/data/validation.py::validate_case_bundle`` on the same staged bundle)
---------------------------------------------------------------------------------------------
Rule (``assert_case`` guard)                                     | (1) contracts | (2) app | (3) mock
-----------------------------------------------------------------|---------------|---------|----------
required columns present, no null in a numeric column            | reject        | reject  | reject (2)
required columns present, no null in a *categorical* column      | accept **!**  | accept  | reject (2)
``delta_change == val_t2 - val_t1``                              | 1e-6 tol      | —       | 1e-6 tol
not-fitted state (amp 0, ratio null, transitional, limitation)   | accept        | accept  | accept
``ratio_R`` null exactly where ``seasonal_amp_mm == 0``           | reject        | reject  | reject (7)
``ratio_R`` = 1e9 / 1e15 where ``seasonal_amp_mm == 0``           | reject        | reject  | reject (7)
``ratio_R`` = ``inf`` where ``seasonal_amp_mm == 0``              | reject        | reject  | **only via (1)** *
``ratio_R`` == ``|velocity| / amp`` where fitted, 5 % off         | reject        | reject  | reject (7)
``ratio_R`` == ``|velocity| / amp``, rounded to 2 dp             | accept        | accept  | accept
``seasonal_amp_mm`` >= 0 and finite                              | reject        | reject  | reject (2/7)
``regime`` in ``REGIMES``                                        | reject        | reject  | reject (7)
``seasonal_n_harmonics == 0`` without a "not fitted" limitation   | reject        | reject  | reject (1/7)
non-finite ``median_ratio_R`` / non-finite ``meta.stats``        | reject        | reject  | reject (9)
``dominant_regime`` == grid regime mode                          | accept **!**  | reject  | reject (9) ++
``median_ratio_R`` null iff no harmonic was fitted               | one way only **!** | both | both (9) ++
mixture of fitted and 0-amplitude cells (per-cell legal)         | accept        | accept  | reject (7) +++
mock-only: nodata set == ellipse - coherence filter, no zero      | not checked   | partly  | reject (4)
  blobs, provenance resolves in meta, aoi endpoints, refit, etc.  |               |         | 

``!``  Gap on the *contract's* side, docs side with (3): ``validate_grid`` null-checks only the
       numeric columns (a null ``land_use``/``regime`` is accepted by (1) and (2)), ``validate_meta``
       only checks the ``dominant_regime`` enum and only the "null median beside a fitted amplitude"
       direction of ``median_ratio_R``. ``assert_case`` is stricter and right per
       ``docs/data-contract-v1.md`` §3/§5/§7.5. Reported, not asserted, so a contract fix cannot
       break this file. Consequence today: none for generation (the mock never emits these states);
       consequence for the contract: a hand-made bundle can carry them and pass CI.
``*``  Rule 7 of ``assert_case`` counts *finite* ratios on not-fitted cells, so ``inf`` slips past
       the copy (its docstring claims otherwise); rule 1 — the contract call that opens
       ``assert_case`` — still fails the gate, so the gate as a whole is fail-closed. The duplicate
       rule is buggy, the verdict is not.
``+``  Fixed (was: **genuine divergence, ``xfail(strict=True)`` twice**). Two duplicated
       tolerances in ``assert_case`` were tighter than the documented contract ones — rule 7's bare
       absolute ``1e-6`` for ``ratio_R`` (contract: ``|expected|*5e-3 + 5e-3``, "tolerancia
       relativa 5e-3 para absorber el redondeo", §3) and rule 3's ``1e-9`` for the ``delta_change``
       identity (contract: ``1e-6``). ``src/data/validation.py`` carried the same ratio ``1e-6`` and
       its comment records the fix ("CI failed while the frozen contract passed — the exact
       divergence this module must not have"); ``pipeline/mock/checks.py`` never received it. Both
       copies in the gate now read the contract's numbers (``DELTA_CHANGE_TOLERANCE_MM``,
       ``RATIO_RELATIVE_TOLERANCE`` / ``RATIO_ABSOLUTE_TOLERANCE`` + ``ratio_tolerance()``, and
       ``C.MAX_PUBLISHABLE_RATIO_R`` for the exported ceiling), so a rounded ratio and a 1e-7
       arithmetic residual are accepted by both verifiers while a value beyond the tolerance is
       still refused by both (the two negative controls below).
``++`` Cross-artefact invariants ``assert_case`` shares with (2), exactly the class of strictness
       ``tests/test_validator_parity.py`` already documents as deliberate.
``+++``Also deliberate: the seasonal term is fitted once per case (``transforms.harmonic_order``
       depends on the span, not the cell), so no producer in this repo can publish a mixture. Only a
       hand-built grid can, and refusing it cannot reject a real artefact.
"""

from __future__ import annotations

import copy
import json
import math
import pathlib
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Any

import pandas as pd
import numpy as np
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
#: The largest shipped case: the per-cell loop notices a wrong rule first here.
CASE = "mexico-city"
ARTEFACTS = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")
#: `contracts.validate_grid` accepts a published ratio within `|expected|*5e-3 + 5e-3`; a 5 %
#: deviation is safely outside it for every ratio this case publishes (0.39 … 277).
BEYOND_CONTRACT_TOLERANCE = 1.05
#: The same tolerance, re-derived from what the contract documents (§3) instead of imported from
#: the gate under test: ``|expected| * RATIO_RELATIVE_TOLERANCE + RATIO_ABSOLUTE_TOLERANCE``. The
#: relative term is the one that exists "para absorber el redondeo".
RATIO_RELATIVE_TOLERANCE = 5e-3
RATIO_ABSOLUTE_TOLERANCE = 5e-3
#: `contracts.validate_grid` rejects a `delta_change` identity residual only above this.
DELTA_CHANGE_TOLERANCE_MM = 1e-6
#: Multiples of a tolerance that must be accepted / refused, with enough margin (1e-2 … 1e-3 of the
#: tolerance itself) that float noise cannot decide a verdict. A boundary probe *at* the tolerance
#: would be a coin flip on the last bit; these remain unambiguous either side of it.
INSIDE_TOLERANCE = 0.5
OUTSIDE_TOLERANCE = 1.5


# ---------------------------------------------------------------------------------------------
# Real artefacts, once per module: one fitted case and one not-fitted case, seed 7, in tmp.
# ---------------------------------------------------------------------------------------------


def _patched_study(directory: pathlib.Path, *, seasonal_amplitude_mm: float | None):
    """The shipped config with at most ONE knob changed, loaded through the real loader.

    A real YAML file (not a hand-built ``CaseSpec``) keeps the comparison about the shipped
    dataset: bbox, AOI ring, ellipse, dates and geography stay exactly as shipped, and
    ``config/aoi/`` is copied next to the patched file because ``load_study_cases`` resolves the AOI
    relative to the config's own directory.
    """
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if seasonal_amplitude_mm is not None:
        raw["cases"][CASE]["mock"]["seasonal_amplitude_mm"] = float(seasonal_amplitude_mm)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "study_cases.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    shutil.copytree(CONFIG.parent / "aoi", directory / "aoi", dirs_exist_ok=True)
    return load_study_cases(path)


@dataclass(frozen=True)
class Bundle:
    """One generated artefact set plus the study/case spec needed to re-run the mock gate."""

    label: str
    study: Any
    directory: pathlib.Path
    grid: pd.DataFrame
    series: pd.DataFrame
    meta: dict[str, Any]
    context: dict[str, Any]


def _bundle(label: str, study: Any, directory: pathlib.Path) -> Bundle:
    missing = [name for name in ARTEFACTS if not (directory / name).is_file()]
    assert not missing, f"{directory} is missing {missing}: both verifiers need all four artefacts"
    loaded = K.load_case(directory)
    return Bundle(
        label=label,
        study=study,
        directory=directory,
        grid=loaded.grid,
        series=loaded.series,
        meta=loaded.meta,
        context=loaded.context,
    )


@pytest.fixture(scope="module")
def bundles(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Bundle]:
    """Two real cases: the shipped fitted state, and the amplitude-0 "not fitted" state.

    ``generate_case`` writes the four artefacts and then runs ``assert_case`` on the files it just
    wrote, so a bug in the *producer* surfaces here as an error rather than as a mutated-bundle
    verdict.
    """
    root = tmp_path_factory.mktemp("verifier_three_way")
    produced: dict[str, Bundle] = {}
    for label, amplitude in (("fitted", None), ("not_fitted", 0.0)):
        study = _patched_study(root / f"cfg-{label}", seasonal_amplitude_mm=amplitude)
        generate_case(study.cases[CASE], study, root / label, DEFAULT_SEED, per_cell_series=False)
        produced[label] = _bundle(label, study, root / label / CASE)

    # The fixtures must actually BE the two states the tests claim to compare.
    fitted = produced["fitted"]
    assert (fitted.grid["seasonal_amp_mm"] > 0.0).all(), "the 'fitted' fixture fitted no harmonic"
    assert fitted.grid["ratio_R"].notna().all(), "the 'fitted' fixture published a null ratio"
    assert fitted.meta["stats"]["seasonal_n_harmonics"] > 0

    not_fitted = produced["not_fitted"]
    assert (not_fitted.grid["seasonal_amp_mm"] == 0.0).all()
    assert not_fitted.grid["ratio_R"].isna().all()
    assert set(not_fitted.grid["regime"].astype(str)) == {"transitional"}
    assert not_fitted.meta["stats"]["median_ratio_R"] is None
    assert not_fitted.meta["stats"]["seasonal_n_harmonics"] == 0
    assert "not fitted" in " ".join(not_fitted.meta["limitations"]).lower()
    return produced


# ---------------------------------------------------------------------------------------------
# Driving both verifiers on the very same directory
# ---------------------------------------------------------------------------------------------


@dataclass
class Verdicts:
    """Both verdicts on one staged bundle. Empty list == "this verifier accepted"."""

    contract: list[str]
    mock: list[str]

    @property
    def contract_accepts(self) -> bool:
        return not self.contract

    @property
    def mock_accepts(self) -> bool:
        return not self.mock

    @property
    def mock_own(self) -> list[str]:
        """Failure lines raised by ``assert_case``'s OWN rules, not echoed from the contract.

        ``assert_case`` prefixes contract problems with ``- contract.<artefact>:`` and opens with a
        count header; the rest comes from rule 2 onwards. The distinction matters: it is how a lax
        duplicate rule can be seen even when the gate as a whole is fail-closed.
        """
        return [
            line
            for line in self.mock
            if not line.strip().startswith("- contract.")
            and not re.fullmatch(r"\S+: \d+ gate\(s\) failed", line.strip())
        ]

    def describe(self) -> str:
        return (
            f"contracts.accepts={self.contract_accepts} ({len(self.contract)} problem(s))\n"
            f"assert_case.accepts={self.mock_accepts} ({len(self.mock)} line(s))\n"
            "  contract: " + json.dumps(self.contract, ensure_ascii=False) + "\n"
            "  mock    : " + json.dumps([line.strip() for line in self.mock], ensure_ascii=False)
        )


def _stage(
    bundle: Bundle,
    directory: pathlib.Path,
    *,
    grid: pd.DataFrame | None = None,
    meta: dict[str, Any] | None = None,
) -> pathlib.Path:
    """Copy the four artefacts to `directory`, then write the mutated grid/meta over them."""
    shutil.copytree(bundle.directory, directory)
    if grid is not None:
        grid.to_parquet(
            directory / "nisar_grid.parquet", engine="pyarrow", compression="zstd", index=False
        )
    if meta is not None:
        # ``allow_nan=True`` on purpose: NaN/inf cannot come from the producer's writer
        # (`allow_nan=False`), but a verifier must still be driven on a payload that holds one, and
        # `json.loads` reads them. The file is the mutation vehicle, not a published artefact.
        (directory / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True, allow_nan=True) + "\n", encoding="utf-8"
        )
    return directory


def _verdicts(bundle: Bundle, directory: pathlib.Path) -> Verdicts:
    """Run (1) ``contracts.validate_all`` and (3) ``checks.assert_case`` on one directory.

    Both read through the mock's own loader, so the two validators see byte-identical frames: a
    verdict difference can only come from a rule difference, never from a parsing difference.
    """
    loaded = K.load_case(directory)
    report = C.validate_all(
        grid=loaded.grid,
        timeseries=loaded.series,
        meta=loaded.meta,
        context=loaded.context,
        case_id=CASE,
    )
    contract = [problem for problems in report.values() for problem in problems]
    try:
        K.assert_case(bundle.study.cases[CASE], bundle.study, directory)
        mock: list[str] = []
    except AssertionError as exc:  # the gate's contract-style failure: one line per broken rule
        mock = str(exc).splitlines()
    return Verdicts(contract=contract, mock=mock)


def _run(
    bundle: Bundle,
    tmp_path: pathlib.Path,
    name: str,
    *,
    grid: pd.DataFrame | None = None,
    meta: dict[str, Any] | None = None,
) -> Verdicts:
    return _verdicts(bundle, _stage(bundle, tmp_path / name, grid=grid, meta=meta))


def _meta_with_recomputed_ratio_median(grid: pd.DataFrame, meta: dict[str, Any]) -> dict[str, Any]:
    """`meta` as ``artifacts.build_meta`` would write it for this grid.

    The producer publishes ``round(median(finite ratios), 6)`` (JSON null when none is finite). A
    grid mutation that changes the ratio column must carry that recomputation, otherwise the
    unrelated ``stats.median_ratio_R`` rule would be the reason the gate fails and the case would
    prove nothing about the ratio rule itself.
    """
    updated = copy.deepcopy(meta)
    ratio = pd.to_numeric(grid["ratio_R"], errors="coerce").dropna()
    finite = [float(value) for value in ratio if math.isfinite(float(value))]
    updated["stats"]["median_ratio_R"] = (
        round(float(pd.Series(finite).median()), 6) if finite else None
    )
    return updated


def _assert_both_reject(verdicts: Verdicts, needle: str, *, mock_own_rule: bool = True) -> None:
    """Both verifiers reject, each naming the rule under test.

    ``mock_own_rule=False`` is used where the mock's *duplicate* rule is known to be blind and the
    rejection comes from the contract call inside ``assert_case`` — asserted explicitly so the
    documented laxness cannot silently become something else.
    """
    assert not verdicts.contract_accepts, (
        f"(1) contracts accepted an illegal bundle ({needle})\n" + verdicts.describe()
    )
    assert not verdicts.mock_accepts, (
        f"(3) assert_case accepted an illegal bundle ({needle})\n" + verdicts.describe()
    )
    assert any(needle in problem for problem in verdicts.contract), (
        f"(1) rejected, but not for {needle!r}\n" + verdicts.describe()
    )
    if mock_own_rule:
        assert any(needle in line for line in verdicts.mock_own), (
            f"(3) rejected, but its own rules never mention {needle!r}: the gate is only failing "
            f"because of the contract call, so its duplicate rule is blind here\n" + verdicts.describe()
        )
    else:
        assert verdicts.mock_own == [], (
            f"(3)'s duplicate rule was expected to be blind to {needle!r}; it is not any more — "
            "promote this case to mock_own_rule=True (the duplication gap closed)\n" + verdicts.describe()
        )


# ---------------------------------------------------------------------------------------------
# The two legal states: both verifiers must accept what the generator publishes.
# ---------------------------------------------------------------------------------------------


def test_shipped_fitted_artefacts_are_accepted_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """Baseline: the generator's fitted state is accepted by (1) and by (3) unchanged."""
    verdicts = _run(bundles["fitted"], tmp_path, "fitted-pristine")
    assert verdicts.contract_accepts and verdicts.mock_accepts, verdicts.describe()


def test_not_fitted_artefacts_are_accepted_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """The 1.1 not-fitted state: amplitude 0.0, null ratio, transitional, "not fitted" limitation.

    This is the relaxation both verifiers had to learn. If either rejects it, a legal short-window
    publication is refused and the mock cannot ship a two-date demo.
    """
    bundle = bundles["not_fitted"]
    verdicts = _run(bundle, tmp_path, "not-fitted-pristine")
    assert verdicts.contract_accepts and verdicts.mock_accepts, verdicts.describe()

    # The state is not merely tolerated: it is exactly what the contract describes (§3/§5).
    assert (bundle.grid["seasonal_amp_mm"] == 0.0).all()
    assert bundle.grid["ratio_R"].isna().all()
    assert set(bundle.grid["regime"].astype(str)) == {"transitional"}
    assert bundle.meta["stats"]["median_ratio_R"] is None
    assert bundle.meta["stats"]["seasonal_n_harmonics"] == 0
    assert "not fitted" in " ".join(bundle.meta["limitations"]).lower()


# ---------------------------------------------------------------------------------------------
# Grid rule §7: `ratio_R` is null exactly where nothing was fitted, and the quotient where fitted.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentinel",
    [1e9, 1e15],
    ids=["sentinel-1e9", "sentinel-1e15"],
)
def test_ratio_published_where_no_harmonic_was_fitted_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path, sentinel: float
) -> None:
    """``|trend| / 0`` is undefined: a number there is fabricated, sentinel or not.

    The not-fitted grid is mutated so every cell publishes the sentinel — the shape a producer with
    a "large number means very inelastic" short-cut would emit — and the metadata carries the median
    such a producer would compute, so the ratio rule itself is the reason both verifiers fail.
    """
    bundle = bundles["not_fitted"]
    grid = bundle.grid.copy()
    grid["ratio_R"] = sentinel
    meta = _meta_with_recomputed_ratio_median(grid, bundle.meta)
    assert meta["stats"]["median_ratio_R"] == sentinel  # the mutation is coherent, not self-defeating

    verdicts = _run(bundle, tmp_path, f"sentinel-{sentinel:g}", grid=grid, meta=meta)
    _assert_both_reject(verdicts, "ratio_R")


def test_infinite_ratio_where_no_harmonic_was_fitted_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """``inf`` is not JSON and not a ratio; the contract rejects it on the finiteness rule.

    The mock's own duplicate rule counts *finite* ratios on not-fitted cells, so it is blind to
    ``inf`` (``assert_case``'s docstring claims both are rejected — they are, but not by that
    rule). The gate stays fail-closed because rule 1 is the contract call; this test pins that the
    verdict is a rejection and that the blindness, if repaired, is noticed here.
    """
    bundle = bundles["not_fitted"]
    grid = bundle.grid.copy()
    grid["ratio_R"] = float("inf")
    meta = _meta_with_recomputed_ratio_median(grid, bundle.meta)
    assert meta["stats"]["median_ratio_R"] is None

    verdicts = _run(bundle, tmp_path, "ratio-inf", grid=grid, meta=meta)
    _assert_both_reject(verdicts, "ratio_R", mock_own_rule=False)


def test_ratio_withheld_where_a_harmonic_was_fitted_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """A null where ``seasonal_amp_mm > 0`` hides a computed number, so it is not the legal null."""
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid.loc[grid.index[0], "ratio_R"] = None

    verdicts = _run(bundle, tmp_path, "ratio-null-fitted", grid=grid)
    _assert_both_reject(verdicts, "ratio_R")


def test_ratio_beyond_the_contract_tolerance_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """A ratio 5 % off ``|velocity| / amp`` is neither rounding nor a computed quotient.

    Beyond ``|expected|*5e-3 + 5e-3`` the contract calls it typed/sentinel, and the mock's copy of
    the rule now uses the contract's own tolerance, so both reject and both name the quotient.
    """
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid["ratio_R"] = grid["ratio_R"] * BEYOND_CONTRACT_TOLERANCE
    meta = _meta_with_recomputed_ratio_median(grid, bundle.meta)

    verdicts = _run(bundle, tmp_path, "ratio-5pct", grid=grid, meta=meta)
    _assert_both_reject(verdicts, "ratio_R")


def test_ratio_rounded_to_two_decimals_is_contract_legal_and_must_be_accepted_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """A ratio rounded to two decimals is legal by design: the contract's tolerance absorbs it.

    This is the case that found the divergence. ``contracts.validate_grid`` accepts a published
    ratio within ``|expected| * 5e-3 + 5e-3`` ("tolerancia relativa 5e-3 para absorber el
    redondeo", §3); the gate's copy of the same rule gated at a bare absolute ``1e-6``, so the
    producer could never publish the rounded ratio the contract allows. Both verifiers must accept
    it, and they must accept it for the same reason — the residual here is ~1e-3, a thousand times
    the old epsilon, so this cannot pass by luck.
    """
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    expected = grid["velocity_mm_yr"].abs() / grid["seasonal_amp_mm"]
    grid["ratio_R"] = expected.round(2)
    meta = _meta_with_recomputed_ratio_median(grid, bundle.meta)

    # The mutation must actually exceed the tolerance the gate used to apply, otherwise the case
    # would prove nothing about the fix.
    residual = (grid["ratio_R"] - expected).abs().max()
    assert float(residual) > 1e-6, (
        f"the two-decimal rounding leaves a residual of only {float(residual):.3e}, so this bundle "
        "would not distinguish the contract tolerance from the old bare 1e-6"
    )

    verdicts = _run(bundle, tmp_path, "ratio-2dp", grid=grid, meta=meta)
    # The contract's tolerance is what makes the rounding legal by design, not by accident.
    assert verdicts.contract_accepts, verdicts.describe()
    assert verdicts.mock_accepts, (
        f"(3) assert_case refused a grid the contract accepts — the tolerance drift is back:\n"
        f"{verdicts.describe()}"
    )


def test_ratio_just_outside_the_contract_tolerance_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """Negative control for the relaxed rule: relaxing it must not have made it vacuous.

    Every published ratio is pushed ``1.5 x`` the contract tolerance away from ``|velocity|/amp``.
    That is close enough to be rounding-shaped (a 1 % error, not the 5 % of the case above) and
    still outside the gate both verifiers are supposed to apply, so both must refuse it, each
    naming the quotient. Without this test the previous case could be "passed" by deleting the
    rule.
    """
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    expected = grid["velocity_mm_yr"].abs() / grid["seasonal_amp_mm"]
    tolerance = expected.abs() * RATIO_RELATIVE_TOLERANCE + RATIO_ABSOLUTE_TOLERANCE
    grid["ratio_R"] = expected + OUTSIDE_TOLERANCE * tolerance
    meta = _meta_with_recomputed_ratio_median(grid, bundle.meta)

    verdicts = _run(bundle, tmp_path, "ratio-just-outside", grid=grid, meta=meta)
    _assert_both_reject(verdicts, "ratio_R")


def test_the_mock_gates_the_ratio_with_the_contract_tolerance(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """A ratio *inside* the tolerance but far outside the old 1e-6 is accepted by both.

    The expression is the documented one (``|expected| * 5e-3 + 5e-3``) re-derived here rather than
    imported from the gate: a boundary probe that read the gate's own constant could not detect a
    wrong constant. The gate's helper must evaluate to the same tolerance, which is what makes the
    two verdicts agree for the same reason instead of by accident.
    """
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    expected = grid["velocity_mm_yr"].abs() / grid["seasonal_amp_mm"]
    tolerance = expected.abs() * RATIO_RELATIVE_TOLERANCE + RATIO_ABSOLUTE_TOLERANCE
    grid["ratio_R"] = expected + INSIDE_TOLERANCE * tolerance
    meta = _meta_with_recomputed_ratio_median(grid, bundle.meta)

    # The gate's own helper, on the same inputs, is the expression above — not a tighter epsilon.
    np.testing.assert_allclose(
        K.ratio_tolerance(expected.to_numpy(dtype=float)),
        tolerance.to_numpy(dtype=float),
        rtol=0.0,
        atol=0.0,
    )
    assert float((grid["ratio_R"] - expected).abs().min()) > 1e-6

    verdicts = _run(bundle, tmp_path, "ratio-inside", grid=grid, meta=meta)
    assert verdicts.contract_accepts and verdicts.mock_accepts, verdicts.describe()


def test_delta_change_within_the_contract_tolerance_is_accepted_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """A 1e-7 arithmetic residual is contract-legal, so the gate must accept it too.

    ``contracts.validate_grid`` rejects the identity only above ``1e-6`` (``delta_change`` is
    cumulative mm). The gate's copy gated at ``1e-9``, i.e. a hundredth of the contract's
    tolerance, so a producer writing ``delta_change`` with 7-decimal rounding would have been
    refused by the generator's own gate while the frozen contract called the artefact valid.
    """
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid["delta_change"] = grid["val_t2"] - grid["val_t1"] + 1e-7

    verdicts = _run(bundle, tmp_path, "delta-1e-7", grid=grid)
    assert verdicts.contract_accepts, verdicts.describe()
    assert verdicts.mock_accepts, (
        f"(3) assert_case refused a 1e-7 arithmetic residual the contract tolerates:\n"
        f"{verdicts.describe()}"
    )


def test_delta_change_just_outside_the_contract_tolerance_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """Negative control for the relaxed identity rule: 2e-6 is outside 1e-6, so both refuse.

    The delta is applied to every row, so the gate's own rule 3 (not the echoed contract problem)
    must be the one that fires — asserted through ``mock_own``.
    """
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid["delta_change"] = grid["val_t2"] - grid["val_t1"] + OUTSIDE_TOLERANCE * DELTA_CHANGE_TOLERANCE_MM

    verdicts = _run(bundle, tmp_path, "delta-just-outside", grid=grid)
    _assert_both_reject(verdicts, "delta_change")


# ---------------------------------------------------------------------------------------------
# §4/§5: nulls, amplitudes, and the statistics derived from them.
# ---------------------------------------------------------------------------------------------


def test_not_fitted_without_its_limitation_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """``seasonal_n_harmonics == 0`` must travel with the phrase that explains the null ratio."""
    bundle = bundles["not_fitted"]
    meta = copy.deepcopy(bundle.meta)
    meta["limitations"] = ["GUNW posting 80 m: city scale, not building scale."]  # non-empty, wrong

    verdicts = _run(bundle, tmp_path, "nf-no-limitation", meta=meta)
    _assert_both_reject(verdicts, "limitation")


@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_non_finite_median_ratio_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path, value: float
) -> None:
    """A statistic is finite or JSON null: NaN/inf cannot survive a strict JSON reader."""
    bundle = bundles["fitted"]
    meta = copy.deepcopy(bundle.meta)
    meta["stats"]["median_ratio_R"] = value

    verdicts = _run(bundle, tmp_path, f"median-ratio-{value}", meta=meta)
    _assert_both_reject(verdicts, "median_ratio_R")


def test_negative_amplitude_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """An amplitude is a magnitude; a negative one is also "not fitted" for the ratio rule."""
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid.loc[grid.index[0], "seasonal_amp_mm"] = -1.5

    verdicts = _run(bundle, tmp_path, "negative-amplitude", grid=grid)
    assert not verdicts.contract_accepts and not verdicts.mock_accepts, verdicts.describe()
    assert any("seasonal_amp_mm" in problem for problem in verdicts.contract), verdicts.describe()
    assert any("amplitude" in line or "seasonal_amp_mm" in line for line in verdicts.mock_own), (
        verdicts.describe()
    )


@pytest.mark.parametrize(
    "column",
    ["velocity_mm_yr", "lat", "coherence", "delta_change"],
    ids=["velocity_mm_yr", "lat", "coherence", "delta_change"],
)
def test_null_in_a_required_non_seasonal_numeric_column_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path, column: str
) -> None:
    """Only ``ratio_R`` may be null; every other required numeric column is a value or the row is
    absent from the grid (docs §3: "nulos prohibidos en las columnas obligatorias")."""
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid[column] = grid[column].astype(object)
    grid.loc[grid.index[0], column] = None

    verdicts = _run(bundle, tmp_path, f"null-{column}", grid=grid)
    assert not verdicts.contract_accepts and not verdicts.mock_accepts, verdicts.describe()
    assert any(column in problem for problem in verdicts.contract), verdicts.describe()
    assert any(column in line for line in verdicts.mock_own), (
        f"(3) rejected, but not for the null in {column!r}\n" + verdicts.describe()
    )


def test_null_in_a_categorical_required_column_is_refused_by_the_mock_alone(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """Measured contract gap: (1) and (2) accept a null ``land_use``, (3) refuses it.

    ``validate_grid`` null-checks only the numeric columns (plus ``provenance_id``), so a null in a
    required *categorical* column slips through the normative validator and through
    ``src/data/validation.py`` (measured: both accept). ``assert_case`` rule 2 checks every required
    column and rejects, which is what docs §3 says ("nulos prohibidos en las columnas
    obligatorias"). The mock is therefore the strictest-and-correct side and the gap is on the
    contract's side — reported, not asserted, so a contract-side fix cannot break this file.
    """
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid["land_use"] = grid["land_use"].astype(object)
    grid.loc[grid.index[0], "land_use"] = None

    verdicts = _run(bundle, tmp_path, "null-land_use", grid=grid)
    assert not verdicts.mock_accepts, (
        "(3) accepted a null in a required categorical column\n" + verdicts.describe()
    )
    assert any("land_use" in line for line in verdicts.mock_own), verdicts.describe()


# ---------------------------------------------------------------------------------------------
# §7.5: `regime` enum, and `dominant_regime` as the mode of the grid column.
# ---------------------------------------------------------------------------------------------


def test_undeclared_regime_is_rejected_by_both(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """``REGIMES`` is frozen: a convenience value breaks the UI silently, so both refuse it."""
    bundle = bundles["fitted"]
    grid = bundle.grid.copy()
    grid.loc[grid.index[0], "regime"] = "recoverable"

    verdicts = _run(bundle, tmp_path, "regime-undeclared", grid=grid)
    _assert_both_reject(verdicts, "regime")


def test_dominant_regime_disagreeing_with_the_grid_mode_is_refused_by_the_mock(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """``dominant_regime`` is the MODE of the grid column (docs §7.5), not any allowed label.

    ``contracts.validate_meta`` sees ``meta.json`` alone, so it can only check the enum; ``assert_case``
    and ``src/data/validation.py`` have the grid in hand and reject the disagreement. The mock is the
    stricter-and-correct side here (the docs say "moda"), which is why this is a *documented
    asymmetry* and not an xfail: a contract-legal-but-wrong artefact must not be publishable.

    The contract's verdict is deliberately not asserted: if ``validate_meta`` grows the
    cross-artefact rule this file must keep passing.
    """
    bundle = bundles["fitted"]
    meta = copy.deepcopy(bundle.meta)
    published = sorted(
        bundle.grid["regime"].astype(str).value_counts().items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    mode = published[0][0]
    wrong = next(value for value in C.REGIMES if value != mode)
    meta["stats"]["dominant_regime"] = wrong

    verdicts = _run(bundle, tmp_path, "dominant-regime-mismatch", meta=meta)
    assert not verdicts.mock_accepts, (
        f"(3) published dominant_regime={wrong!r} while the grid's mode is {mode!r}\n"
        + verdicts.describe()
    )
    assert any("dominant_regime" in line for line in verdicts.mock_own), verdicts.describe()
    # Measured: (1) accepts it (enum-only rule), (2) rejects it like (3). Recorded, not asserted.


def test_numeric_median_ratio_on_a_not_fitted_case_is_refused_by_the_mock(
    bundles: dict[str, Bundle], tmp_path: pathlib.Path
) -> None:
    """The mirror of the task's null-beside-fitted rule: a number beside an all-zero amplitude.

    ``contracts.validate_meta`` only walks the direction "null median + non-zero median amplitude";
    the reverse (``median_ratio_R = 0.0`` while no cell fitted a harmonic) passes it, although docs
    §5 requires null exactly then. ``assert_case`` (rule 9) and ``src/data/validation.py`` reject it,
    so the mock refuses to publish a legal-looking-but-wrong aggregate.

    The contract's verdict is deliberately not asserted, for the same reason as above.
    """
    bundle = bundles["not_fitted"]
    meta = copy.deepcopy(bundle.meta)
    meta["stats"]["median_ratio_R"] = 0.0

    verdicts = _run(bundle, tmp_path, "nf-numeric-median", meta=meta)
    assert not verdicts.mock_accepts, (
        "(3) published a numeric median ratio for a case that fitted no harmonic\n" + verdicts.describe()
    )
    assert any("median_ratio_R" in line for line in verdicts.mock_own), verdicts.describe()
    # Measured: (1) accepts it, (2) rejects it like (3). Recorded, not asserted.
