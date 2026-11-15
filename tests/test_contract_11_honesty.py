"""Acceptance guard for contract 1.1: the seasonal term must be *measured*, not asserted.

Why this file exists
--------------------
`origin/main` ships contract 1.1 and publishes, for each case, a `seasonal_amp_mm`, a `ratio_R`
and a `dominant_regime` — while that same artefact's own `meta.limitations` says a ~4.5-month
window "cannot separate seasonal from secular motion: do not publish seasonal_amp_mm from this
stack". The repo already knows the term is unidentifiable on that window: `harmonic_fit` gates the
amplitude on `MIN_SEASONAL_COVERAGE_PER_HARMONIC = 0.75` annual cycles and on a design condition
number below `MAX_DESIGN_CONDITION = 30`, and a 132-day / 12-date stack fails both (coverage 0.36,
condition ~67). So 1.1 on the 132-day stack is a contract that requires publishing a number the
pipeline itself refuses to publish. That is the failure mode this file freezes out.

The migration fixes it by widening the window to ~2 years (so the annual term *is* identifiable)
and by publishing a **fit** instead of a typed constant. This module encodes what "1.1 is honest"
means so it cannot silently regress:

1. the contract constants are actually 1.1 / revision 2, and that contract *declares* the seasonal
   fields it now requires;
2. every published grid carries `seasonal_amp_mm`, `ratio_R`, `regime`, with real (non-null, in
   range, declared-vocabulary) values;
3. `meta.stats` carries the three new aggregate keys;
4. the aoi time series carries the `trend_mm_yr` / `seasonal_mm` split it is now required to carry;
5. **identifiability**: re-running the repo's own `harmonic_fit` on the published aoi series says
   the amplitude is publishable — coverage and conditioning pass, the exact gates `origin/main`
   violates;
6. **no contradiction**: a limitation that forbids publishing the amplitude and a published
   amplitude cannot coexist (the origin/main bug, verbatim);
7. **internal consistency**: the published medians/mode in `meta.stats` are the medians/mode of the
   grid the reader actually gets;
8. the README may keep the prohibition sentence, but only scoped to the short window it was true
   for, not as a live claim about the artefacts it now publishes.

Everything here is read-only and offline. Artefact tests skip cleanly (and say why) when
`data/processed` holds no case directories, so the file is useful before the first artefact run;
the contract and README tests run either way.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts the cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

from pipeline.science import transforms as T  # noqa: E402
from src.domain import contracts as C  # noqa: E402

PROCESSED = ROOT / "data/processed"

#: One directory per published case, the same discovery rule the other artefact tests use.
CASES = sorted(
    p.name
    for p in PROCESSED.iterdir()
    if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "meta.json").exists()
) if PROCESSED.exists() else []

needs_artefacts = pytest.mark.skipif(
    not CASES,
    reason=(
        "no case artefacts under data/processed — run pipeline/generate_mock.py first; "
        "this assertion is about published artefacts, not about the schema alone"
    ),
)

#: Fields contract 1.1 adds. Named here so a *missing* field is reported by name; the contract's
#: own declaration of them is checked in `test_the_contract_is_11_and_declares_the_seasonal_fields`
#: so the names cannot drift away from `src/domain/contracts.py` unnoticed.
SEASONAL_GRID_FIELDS = ("seasonal_amp_mm", "ratio_R", "regime")
SEASONAL_SERIES_FIELDS = ("trend_mm_yr", "seasonal_mm")
SEASONAL_STAT_KEYS = ("median_seasonal_amp_mm", "median_ratio_R", "dominant_regime")

#: A limitation phrased like one of these is a *prohibition on publishing the amplitude*. Matched
#: case-insensitively on whitespace-flattened, markdown-stripped text (the artefacts write plain
#: strings, but the README does not).
PROHIBITION_PHRASES = ("do not publish seasonal_amp_mm", "cannot separate seasonal")

#: A prohibition is only honest while it is scoped to the window that cannot support the term.
SHORT_WINDOW_RE = re.compile(r"\b132\s*[- ]?\s*(?:day|d)\b|\b4\.5\s*[- ]?month|\b4\s*[- ]?month")


def _rel(path: pathlib.Path) -> str:
    """Path relative to the repo root, so a failure message is copy-pasteable."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:  # pragma: no cover - every path here is inside the repo
        return str(path)


def _flatten(text: str) -> str:
    """Lowercase, drop markdown emphasis/backticks, collapse whitespace.

    The phrases live inside `code spans` and **bold** in the README, and artefacts wrap sentences
    across list items; normalising both sides keeps the match about content, not formatting.
    """
    return re.sub(r"\s+", " ", text.replace("`", "").replace("*", "").replace("_", "")).strip().lower()


class Case:
    """One published case directory. Loads lazily so a missing file names itself in the failure."""

    def __init__(self, case_id: str) -> None:
        self.id = case_id
        self.directory = PROCESSED / case_id

    @property
    def meta_path(self) -> pathlib.Path:
        return self.directory / "meta.json"

    @property
    def grid_path(self) -> pathlib.Path:
        return self.directory / "nisar_grid.parquet"

    @property
    def series_path(self) -> pathlib.Path:
        return self.directory / "timeseries.csv"

    @property
    def meta(self) -> dict:
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    @property
    def meta_text(self) -> str:
        """Raw text, used to recover the *printed* precision of a published statistic."""
        return self.meta_path.read_text(encoding="utf-8")

    def grid(self) -> pd.DataFrame:
        assert self.grid_path.exists(), (
            f"{self.id}: {_rel(self.grid_path)} is missing — a published case must ship its grid"
        )
        return pd.read_parquet(self.grid_path)

    def timeseries(self) -> pd.DataFrame:
        assert self.series_path.exists(), (
            f"{self.id}: {_rel(self.series_path)} is missing — a published case must ship its "
            "time series; regenerate the case rather than dropping the file"
        )
        return pd.read_csv(self.series_path, dtype={"cell_id": str})


@pytest.fixture(params=CASES, ids=CASES)
def case(request) -> Case:
    return Case(request.param)


# ---------------------------------------------------------------------------------------------
# 1. The contract itself: 1.1 / revision 2, and it declares the fields it now requires
# ---------------------------------------------------------------------------------------------


def test_the_contract_is_11_and_declares_the_seasonal_fields() -> None:
    """A 1.1 artefact published under a 1.0 contract has no normative source for its new fields."""
    assert C.SCHEMA_VERSION == "1.1", (
        f"src/domain/contracts.py still declares SCHEMA_VERSION={C.SCHEMA_VERSION!r}; the "
        "migration is to contract 1.1, and a 1.0 contract cannot validate a grid that carries "
        f"{SEASONAL_GRID_FIELDS}"
    )
    assert C.CONTRACT_REVISION == 2, (
        f"src/domain/contracts.py declares CONTRACT_REVISION={C.CONTRACT_REVISION!r}; contract 1.1 "
        "is revision 2 (a version bump without the revision bump is the drift the freeze record "
        "forbids)"
    )

    declared = {field.name for field in C.GRID_FIELDS}
    missing = [name for name in SEASONAL_GRID_FIELDS if name not in declared]
    assert not missing, (
        f"contract 1.1 requires grid field(s) {missing} but GRID_FIELDS does not declare them; the "
        "grid schema in src/domain/contracts.py must be the one place the new fields are defined"
    )

    declared_series = {field.name for field in C.TIMESERIES_FIELDS}
    missing_series = [name for name in SEASONAL_SERIES_FIELDS if name not in declared_series]
    assert not missing_series, (
        f"contract 1.1 requires timeseries field(s) {missing_series} but TIMESERIES_FIELDS does not "
        "declare them; add them to src/domain/contracts.py, not only to the generator"
    )
    assert set(SEASONAL_STAT_KEYS) <= set(C.STATS_REQUIRED_KEYS), (
        f"contract 1.1 requires meta.stats keys {list(SEASONAL_STAT_KEYS)} but "
        f"STATS_REQUIRED_KEYS is {list(C.STATS_REQUIRED_KEYS)}; the validator cannot enforce a key "
        "it does not declare"
    )


# ---------------------------------------------------------------------------------------------
# 2. The grid publishes real seasonal values, in the contract's own vocabulary
# ---------------------------------------------------------------------------------------------


@needs_artefacts
def test_the_grid_publishes_the_seasonal_fields(case: Case) -> None:
    """`seasonal_amp_mm`, `ratio_R`, `regime` must be present, populated, in range and declared."""
    grid = case.grid()
    path = _rel(case.grid_path)

    missing = [name for name in SEASONAL_GRID_FIELDS if name not in grid.columns]
    assert not missing, (
        f"{case.id}: {path} is missing contract-1.1 column(s) {missing} (columns present: "
        f"{list(grid.columns)}). Regenerate the case under contract 1.1 — do not patch the parquet."
    )

    for column in SEASONAL_GRID_FIELDS:
        values = grid[column]
        assert values.notna().any(), (
            f"{case.id}: {path} column {column!r} is present but entirely null. A required 1.1 "
            "field that is null for every cell is a schema placeholder, not a published fit."
        )

    amplitude = pd.to_numeric(grid["seasonal_amp_mm"], errors="coerce").dropna()
    negative_amplitude = amplitude[amplitude < 0.0]
    assert negative_amplitude.empty, (
        f"{case.id}: {path} publishes {len(negative_amplitude)} negative seasonal_amp_mm value(s), "
        f"first {float(negative_amplitude.iloc[0])!r}: the contract's amplitude is a "
        "non-negative magnitude (hypot(A, B)), never a signed coefficient — a sign here usually "
        "means a raw cosine coefficient was published instead of the amplitude."
    )

    ratio = pd.to_numeric(grid["ratio_R"], errors="coerce").dropna()
    negative_ratio = ratio[ratio < 0.0]
    assert negative_ratio.empty, (
        f"{case.id}: {path} publishes {len(negative_ratio)} negative ratio_R value(s), first "
        f"{float(negative_ratio.iloc[0])!r}. README §6 defines R = |trend| / A_seasonal: the "
        "absolute value is part of the definition, so publish the magnitude."
    )

    observed = {str(value) for value in grid["regime"].dropna().unique()}
    undeclared = sorted(observed - set(C.REGIMES))
    assert not undeclared, (
        f"{case.id}: {path} publishes regime value(s) {undeclared}, which contract "
        f"contracts.REGIMES does not declare {list(C.REGIMES)}. Use the contract's vocabulary "
        "(and bump the contract first if a fourth regime is real)."
    )


# ---------------------------------------------------------------------------------------------
# 3. meta.stats carries the three 1.1 aggregates
# ---------------------------------------------------------------------------------------------


@needs_artefacts
def test_meta_stats_carries_the_seasonal_aggregates(case: Case) -> None:
    stats = case.meta.get("stats")
    assert isinstance(stats, dict), (
        f"{case.id}: {_rel(case.meta_path)} has no `stats` object, so it cannot carry "
        f"{list(SEASONAL_STAT_KEYS)}"
    )
    missing = [key for key in SEASONAL_STAT_KEYS if key not in stats]
    assert not missing, (
        f"{case.id}: {_rel(case.meta_path)} stats is missing contract-1.1 key(s) {missing} "
        f"(keys present: {sorted(stats)}). Regenerate the case; meta.stats is what the app reads."
    )

    for key in ("median_seasonal_amp_mm", "median_ratio_R"):
        assert isinstance(stats[key], (int, float)) and not isinstance(stats[key], bool), (
            f"{case.id}: stats.{key} is {stats[key]!r}, not a number. Contract 1.1 publishes a "
            "fitted statistic here; null means the case has nothing to publish and must say so "
            "with the identifiability gate instead (see test 5)."
        )
    assert stats["dominant_regime"] in C.REGIMES, (
        f"{case.id}: stats.dominant_regime is {stats['dominant_regime']!r}, which is not one of "
        f"{list(C.REGIMES)}; it must be the modal grid regime, so it uses the same vocabulary."
    )


# ---------------------------------------------------------------------------------------------
# 4. The aoi series carries its own trend/seasonal decomposition
# ---------------------------------------------------------------------------------------------


@needs_artefacts
def test_the_aoi_series_carries_the_trend_and_seasonal_split(case: Case) -> None:
    """1.1 makes `trend_mm_yr` / `seasonal_mm` required series columns — for the aoi rows too."""
    series = case.timeseries()
    path = _rel(case.series_path)
    assert "cell_id" in series.columns, (
        f"{case.id}: {path} has no `cell_id` column (columns present: {list(series.columns)}); "
        "the aoi rows cannot be selected without it"
    )

    aoi = series[series["cell_id"].astype(str) == "aoi"]
    assert not aoi.empty, (
        f"{case.id}: {path} has no rows with cell_id == 'aoi'; contract §5.2 requires the case "
        "aggregate, and it is the series this guard re-fits in test 5"
    )

    for column in SEASONAL_SERIES_FIELDS:
        assert column in series.columns, (
            f"{case.id}: {path} is missing contract-1.1 column {column!r} (columns present: "
            f"{list(series.columns)}). Regenerate the case under contract 1.1."
        )
        values = pd.to_numeric(aoi[column], errors="coerce")
        assert values.notna().any(), (
            f"{case.id}: {path} column {column!r} is present but null for every aoi row, so the "
            "decomposition was declared and never populated"
        )


# ---------------------------------------------------------------------------------------------
# 5. THE CORE INVARIANT: the repo's own identifiability gate must bless the published window
# ---------------------------------------------------------------------------------------------


@needs_artefacts
def test_the_aoi_seasonal_term_is_identifiable_by_the_repo_s_own_gate(case: Case) -> None:
    """Re-fit the published aoi series with `transforms.harmonic_fit` and obey its verdict.

    This is the assertion `origin/main` cannot pass: on a 132-day / 12-date stack the same function
    returns `seasonal_identifiable=False`, `annual_coverage≈0.36` and `design_condition≈67`, so the
    amplitude it publishes is a fitted artefact. Here the artefacts are the ones under test; the
    gate and its two constants are read from `transforms` so the artefact cannot drift from them.

    `n_harmonics=1` on purpose: it is `harmonic_fit`'s default and the term the pipeline publishes
    (`seasonal_amp_mm` is the *first* harmonic). Asking for a second harmonic would test a
    different claim — a 1-year window could never carry it.
    """
    series = case.timeseries()
    path = _rel(case.series_path)
    aoi = series[series["cell_id"].astype(str) == "aoi"]
    assert not aoi.empty, f"{case.id}: no aoi rows in {path}; nothing to re-fit"

    frame = (
        pd.DataFrame(
            {
                "date": pd.to_datetime(aoi["date"], format="%Y-%m-%d", errors="coerce"),
                "value": pd.to_numeric(aoi["value"], errors="coerce"),
            }
        )
        .dropna()
        .sort_values("date")
    )
    assert len(frame) >= T.MIN_FIT_POINTS, (
        f"{case.id}: {path} has only {len(frame)} usable aoi sample(s); harmonic_fit needs at "
        f"least {T.MIN_FIT_POINTS}. The window must be wide enough to identify the annual term, "
        "not merely wide enough to load."
    )

    # Same time axis as pipeline/mock/model.py: years since the first acquisition, 365.25 d/yr.
    days = (frame["date"] - frame["date"].min()).dt.days.to_numpy(dtype=float)
    fit = T.harmonic_fit(days / T.DAYS_PER_YEAR, frame["value"].to_numpy(dtype=float))

    span_days = float(days.max())
    context = (
        f"{case.id}: harmonic_fit on the aoi series in {path} "
        f"(span {span_days:.0f} d / {fit.annual_coverage:.2f} annual cycles, condition "
        f"{fit.design_condition:.3g})"
    )
    assert fit.seasonal_identifiable is True, (
        f"{context} reports seasonal_identifiable=False, so the pipeline itself forbids publishing "
        f"seasonal_amp_mm while the artefact publishes it. Warnings: {fit.warnings}. Widen the "
        "window until the annual term is identifiable (≥ "
        f"{T.MIN_SEASONAL_COVERAGE_PER_HARMONIC:g} annual cycles) — do not publish the fitted "
        "amplitude anyway."
    )
    assert fit.annual_coverage >= T.MIN_SEASONAL_COVERAGE_PER_HARMONIC, (
        f"{context} spans {fit.annual_coverage:.4f} annual cycles; "
        f"MIN_SEASONAL_COVERAGE_PER_HARMONIC requires ≥ "
        f"{T.MIN_SEASONAL_COVERAGE_PER_HARMONIC:g}. The seasonal amplitude is not separable from "
        "[1, t] on this window, so publishing it in the grid/stats is the origin/main defect."
    )
    assert fit.design_condition <= T.MAX_DESIGN_CONDITION, (
        f"{context} has a column-normalised design condition number "
        f"{fit.design_condition:.3g} > MAX_DESIGN_CONDITION "
        f"{T.MAX_DESIGN_CONDITION:g}: the harmonic columns are nearly collinear with [1, t] over "
        "these dates (clustered or irregular sampling), so the amplitude is a fitted parameter. "
        "Spread the acquisitions over the window, then regenerate."
    )


# ---------------------------------------------------------------------------------------------
# 6. THE NO-CONTRADICTION INVARIANT: the origin/main bug, verbatim
# ---------------------------------------------------------------------------------------------


@needs_artefacts
def test_a_limitation_forbidding_publication_is_not_contradicted_by_the_artefact(case: Case) -> None:
    """An artefact may not forbid publishing the amplitude and publish one at the same time.

    `origin/main` does exactly that: `meta.limitations` says "cannot separate seasonal from secular
    motion: do not publish seasonal_amp_mm from this stack" while `meta.stats` and the grid publish
    `median_seasonal_amp_mm=1.5`, `ratio_R=41.377` and `dominant_regime="inelastic"`. A reader who
    trusts the caveat and a reader who plots the number reach opposite conclusions from one file.
    """
    meta = case.meta
    prohibitions = [
        item
        for item in meta.get("limitations") or []
        if any(phrase in _flatten(str(item)) for phrase in PROHIBITION_PHRASES)
    ]
    if not prohibitions:
        return  # no prohibition: the artefact is free to publish a fitted amplitude

    grid = case.grid()
    stats = meta.get("stats") or {}
    published: list[str] = []
    if "seasonal_amp_mm" in grid.columns and grid["seasonal_amp_mm"].notna().any():
        published.append(
            f"{_rel(case.grid_path)} publishes {int(grid['seasonal_amp_mm'].notna().sum())} "
            f"non-null seasonal_amp_mm row(s), median "
            f"{float(pd.to_numeric(grid['seasonal_amp_mm'], errors='coerce').median())!r}"
        )
    if stats.get("median_seasonal_amp_mm") is not None:
        published.append(
            f"{_rel(case.meta_path)} stats.median_seasonal_amp_mm = "
            f"{stats['median_seasonal_amp_mm']!r}"
        )

    quote = prohibitions[0]
    assert not published, (
        f"{case.id}: {_rel(case.meta_path)} limitations say {quote!r} — and the same case DOES "
        f"publish a seasonal amplitude: {'; '.join(published)}. One of the two must go: either the "
        "window is wide enough that the term is identifiable (then rescope or delete the "
        "prohibition) or it is not (then stop publishing the amplitude, in the grid, in stats and "
        "in the series). Publishing both is the origin/main defect this migration exists to fix."
    )


# ---------------------------------------------------------------------------------------------
# 7. Internal consistency: meta.stats must describe the grid the reader receives
# ---------------------------------------------------------------------------------------------


def _printed_tolerance(meta_text: str, key: str) -> tuple[float, int]:
    """Tolerance = one unit in the last digit the artefact actually *printed* for `key`.

    Why this choice: the pipeline rounds the published median to a few decimals (the 1.0 artefacts
    print `median_velocity_mm_yr: -62.065`, i.e. 3 decimals), so the published number and the
    median we recompute from the parquet differ in the last printed digit — rounding, not a
    disagreement. A tolerance any wider would stop distinguishing "median of the right column"
    from "median of a different estimator". A default of 3 decimals applies if the literal cannot
    be located, matching the pipeline's rounding convention.
    """
    literal = re.search(
        rf'"{re.escape(key)}"\s*:\s*(-?\d+(?:\.(\d+))?(?:[eE][+-]?\d+)?)', meta_text
    )
    decimals = len(literal.group(2) or "") if literal else 3
    return max(10.0**-decimals, 1e-9), decimals


@needs_artefacts
def test_meta_stats_describe_the_published_grid(case: Case) -> None:
    """`stats.median_*` and `stats.dominant_regime` must be the grid's statistics, not typed values."""
    meta = case.meta
    stats = meta.get("stats") or {}
    grid = case.grid()
    path = _rel(case.grid_path)

    assert "seasonal_amp_mm" in grid.columns, (
        f"{case.id}: {path} has no seasonal_amp_mm column, so stats.median_seasonal_amp_mm cannot "
        "be the grid's median (columns present: " f"{list(grid.columns)})"
    )
    amplitude = pd.to_numeric(grid["seasonal_amp_mm"], errors="coerce").dropna()
    assert not amplitude.empty, (
        f"{case.id}: {path} seasonal_amp_mm is entirely null; there is no grid median to compare"
    )
    published_amplitude = stats.get("median_seasonal_amp_mm")
    assert isinstance(published_amplitude, (int, float)), (
        f"{case.id}: {_rel(case.meta_path)} stats.median_seasonal_amp_mm is "
        f"{published_amplitude!r}, not a number, while {path} carries "
        f"{len(amplitude)} seasonal_amp_mm value(s)"
    )
    tolerance, decimals = _printed_tolerance(case.meta_text, "median_seasonal_amp_mm")
    grid_amplitude = float(amplitude.median())
    assert abs(float(published_amplitude) - grid_amplitude) <= tolerance, (
        f"{case.id}: stats.median_seasonal_amp_mm = {published_amplitude!r} but the median of "
        f"{path} seasonal_amp_mm is {grid_amplitude!r} (tolerance {tolerance:g} = one unit in the "
        f"{decimals} printed decimal(s)). meta.stats is the app's headline number: it must be "
        "computed from the published grid, not typed in separately."
    )

    assert "ratio_R" in grid.columns, (
        f"{case.id}: {path} has no ratio_R column, so stats.median_ratio_R cannot be the grid's "
        "median (columns present: " f"{list(grid.columns)})"
    )
    ratio = pd.to_numeric(grid["ratio_R"], errors="coerce").dropna()
    finite_ratio = ratio[np.isfinite(ratio)]
    assert not finite_ratio.empty, (
        f"{case.id}: {path} ratio_R has no finite value; a published median_ratio_R would have "
        "nothing to describe (R = |trend| / A_seasonal is finite whenever the amplitude is > 0)"
    )
    published_ratio = stats.get("median_ratio_R")
    assert isinstance(published_ratio, (int, float)), (
        f"{case.id}: {_rel(case.meta_path)} stats.median_ratio_R is {published_ratio!r}, not a "
        f"number, while {path} carries {len(finite_ratio)} finite ratio_R value(s)"
    )
    ratio_tolerance, ratio_decimals = _printed_tolerance(case.meta_text, "median_ratio_R")
    grid_ratio = float(finite_ratio.median())
    assert abs(float(published_ratio) - grid_ratio) <= ratio_tolerance, (
        f"{case.id}: stats.median_ratio_R = {published_ratio!r} but the median of {path} ratio_R is "
        f"{grid_ratio!r} (tolerance {ratio_tolerance:g} = one unit in the {ratio_decimals} printed "
        "decimal(s)). Recompute stats from the grid the app renders."
    )

    regimes = grid["regime"].dropna().astype(str)
    assert not regimes.empty, (
        f"{case.id}: {path} regime is entirely null; stats.dominant_regime has no mode to report"
    )
    modes = {str(value) for value in regimes.mode()}
    published_regime = stats.get("dominant_regime")
    assert str(published_regime) in modes, (
        f"{case.id}: stats.dominant_regime = {published_regime!r} but the modal regime of {path} is "
        f"{sorted(modes)} (counts: {regimes.value_counts().to_dict()}). A tie may be broken either "
        "way, but a published mode must be one of the tied values."
    )


# ---------------------------------------------------------------------------------------------
# 8. Doc-level guard: the README may keep the prohibition only as a past-window statement
# ---------------------------------------------------------------------------------------------


def test_the_readme_does_not_publish_what_it_says_is_unpublishable() -> None:
    """A live "do not publish seasonal_amp_mm" contradicts a README that publishes one.

    README §13 currently says, next to the displacement figure, "Do not publish `seasonal_amp_mm`
    from a ~4.5-month stack". That is a true statement about the 132-day window the 1.0 stack used,
    and it may stay *as such*. What may not stay is the same sentence reading as a claim about the
    artefacts this repo now ships: the 1.1 grid and stats publish the amplitude, so a live
    prohibition would make the README and the data disagree in the same repository.

    Rule enforced: every paragraph carrying the prohibition must name the short window it is
    scoped to (132-day / 4.5-month / 4-month). Anything else reads as a live claim.
    """
    readme_path = ROOT / "README.md"
    text = readme_path.read_text(encoding="utf-8")
    paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]

    offending: list[tuple[int, str]] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        flat = _flatten(paragraph)
        if not any(phrase in flat for phrase in PROHIBITION_PHRASES):
            continue
        if not SHORT_WINDOW_RE.search(flat):
            offending.append((index, flat))

    assert not offending, (
        f"README.md paragraph(s) {[index for index, _ in offending]} forbid publishing "
        f"`seasonal_amp_mm` without scoping the prohibition to the short window that cannot support "
        f"the term. Offending text: {offending[0][1]!r}. The 1.1 artefacts publish the amplitude "
        "from a ~2-year window, so a live prohibition contradicts them. Either name the window the "
        "prohibition is about (e.g. 'the 132-day / ~4.5-month window could not separate seasonal "
        "from secular motion') or drop the prohibition — but do not leave one in the present tense "
        f"next to a published amplitude ({readme_path})."
    )
