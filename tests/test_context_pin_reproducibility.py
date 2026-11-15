"""Guard: an artefact that publishes an ODbL/OSM context layer must ship the pin that reproduces it.

Why this module exists (and why it is not in `test_mock_reproducibility.py`)
----------------------------------------------------------------------------
`pipeline/generate_mock.py` resolves the context in three steps: committed **pin**
(`config/context/<case>.json`) -> `data/raw/<case>/context/` extract -> declared **synthetic
fallback**. The raw extract is gitignored, so a clone has only the first and the last option. Once
the artefacts are derived from OpenStreetMap (which they are: `n_cells` and the `land_use` column
changed with the pin), the pin stops being an optimisation and becomes a *reproduction input*: drop
it from the commit and a clean clone silently regenerates the synthetic layer, so
`test_mock_reproducibility.py` fails in CI — while every local gate keeps passing, because the pin
is on the author's disk. That asymmetry is the risk this file closes.

`test_mock_reproducibility.py` asks a different question with a different mechanism (regenerate every
case in a subprocess and compare SHA-256). Mixing the two would make that module's premise muddy and
force a set of pins to be regenerated to answer a question reading them already answers, so this is a
separate module: it reads the published `meta.json` / `context_layers.geojson`, the pin, and — for
the one branch the generator would take — calls `pipeline.context.load_context` with synthetic
fallback disabled. No subprocess regeneration, no network, `data/**`, `config/**` and the index are
only ever read.

The state is not assumed, it is read off the published text: a synthetic artefact legitimately needs
no pin, so the guard must not fire on the pre-OSM artefacts (it would then demand a pin for a layer
that was never OSM). `_origin` below derives the state from the strings `pipeline/mock/artifacts.py`
writes for each branch, and every test reports the text it matched.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pytest from the repo root, like `python pipeline/generate_mock.py`
    sys.path.insert(0, str(ROOT))

try:  # the OSM context layer is unreleased work; a clone without it has nothing to guard
    from pipeline import context as CTX  # noqa: E402
except ImportError:  # pragma: no cover - only taken in a checkout that lacks the context layer
    pytest.skip(
        "pipeline/context.py is not in this checkout, so there is no context layer to guard; "
        "this module starts checking as soon as that layer is committed alongside its pins",
        allow_module_level=True,
    )

COMMITTED = ROOT / "data" / "processed"
ARTEFACTS = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")

OSM = "osm"
SYNTHETIC = "synthetic"
UNKNOWN = "unknown"

#: Signals read out of the PUBLISHED text, one per writer branch in `pipeline/mock/artifacts.py`:
#: `provenance.license`, `provenance.method`, a `limitations` entry, and the context features'
#: `source`. Every signal of a state is emitted by the same `if context.synthetic` branch, so they
#: co-vary; requiring two of them keeps a reworded sentence from flipping the verdict while still
#: failing loudly (instead of guessing) if the writer's wording changes wholesale.
_OSM_SIGNALS = (
    ("provenance.license", "context layers © OpenStreetMap contributors, ODbL"),
    ("provenance.method", "joined from the OpenStreetMap extract"),
    ("limitations", "is derived from an OpenStreetMap extract"),
    ("context_layers.source", "OpenStreetMap contributors (ODbL 1.0)"),
)
_SYNTHETIC_SIGNALS = (
    ("provenance.license", "Synthetic mock — no third-party data"),
    ("provenance.method", "land use is a deterministic spatial rule"),
    ("limitations", "invented by the synthetic fallback in pipeline/context.py"),
    ("context_layers.source", "mock (synthetic"),
)

SKIP_REASON = (
    "no mock artefacts in data/processed — run ./scripts/python.sh pipeline/generate_mock.py"
)


def _published_cases() -> list[str]:
    """Case directories holding the full frozen artefact set (same rule as the reproducibility test)."""
    if not COMMITTED.is_dir():
        return []
    return sorted(
        path.name
        for path in COMMITTED.iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and all((path / name).is_file() for name in ARTEFACTS)
    )


CASES = _published_cases()
pytestmark = pytest.mark.skipif(not CASES, reason=SKIP_REASON)


# ---------------------------------------------------------------------------------------------
# Reading the published state (never assumed, always matched)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Origin:
    """What the published artefact says about its own context layer, and the text that says it."""

    kind: str
    matches: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()

    @property
    def is_osm(self) -> bool:
        return self.kind == OSM

    def describe(self) -> str:
        if not self.matches:
            return "no recognised disclosure text"
        return "; ".join(self.matches)


def _meta(case: str) -> dict:
    return json.loads((COMMITTED / case / "meta.json").read_text(encoding="utf-8"))


def _context_sources(case: str) -> list[str]:
    """Every distinct `source` string published on a context feature."""
    payload = json.loads(
        (COMMITTED / case / "context_layers.geojson").read_text(encoding="utf-8")
    )
    return sorted(
        {
            str((feature.get("properties") or {}).get("source") or "")
            for feature in payload.get("features") or ()
        }
    )


def _harvest(meta: dict, sources: list[str]) -> dict[str, str]:
    """Flatten the four published places the writer discloses context origin into -> text."""
    provenance = meta.get("provenance") or {}
    return {
        "provenance.license": str(provenance.get("license") or ""),
        "provenance.method": str(provenance.get("method") or ""),
        "limitations": "\n".join(str(entry) for entry in meta.get("limitations") or ()),
        "context_layers.source": "\n".join(sources),
    }


def classify(texts: dict[str, str]) -> Origin:
    """OSM-derived or synthetic, from the published text alone; `unknown` when the text is silent.

    A state needs at least two matching signals and no signal of the other state: OSM and synthetic
    are mutually exclusive per artefact ("Two states, two sentences, because only one of them can be
    true per artefact" — `pipeline/mock/artifacts.py`), so text claiming both is a defect of its own.
    """
    matched: dict[str, list[str]] = {OSM: [], SYNTHETIC: []}
    for kind, signals in ((OSM, _OSM_SIGNALS), (SYNTHETIC, _SYNTHETIC_SIGNALS)):
        for where, needle in signals:
            if needle in texts.get(where, ""):
                matched[kind].append(f"{where} ~ {needle!r}")
    osm_hits, synthetic_hits = matched[OSM], matched[SYNTHETIC]
    if osm_hits and synthetic_hits:
        return Origin(UNKNOWN, contradictions=tuple(osm_hits + synthetic_hits))
    if len(osm_hits) >= 2:
        return Origin(OSM, matches=tuple(osm_hits))
    if len(synthetic_hits) >= 2:
        return Origin(SYNTHETIC, matches=tuple(synthetic_hits))
    return Origin(UNKNOWN, matches=tuple(osm_hits + synthetic_hits))


def origin_of(case: str) -> Origin:
    return classify(_harvest(_meta(case), _context_sources(case)))


def _provenance_id(meta: dict) -> str:
    value = (meta.get("provenance") or {}).get("provenance_id")
    assert isinstance(value, str) and value, (
        f"data/processed/{meta.get('case_id')}/meta.json has no provenance.provenance_id: the guard "
        "cannot rebuild the model's context with the case's own id."
    )
    return value


# ---------------------------------------------------------------------------------------------
# Git: only a tracked pin survives a clone
# ---------------------------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture(scope="module")
def git_worktree() -> bool:
    """False when this tree is not a git worktree (an sdist/tarball: tracking is then unanswerable)."""
    try:
        probe = _git("rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:  # pytest.skip is not allowed in a fixture returning a value
        return False
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def _tracked_relative(relative: str) -> tuple[bool, str]:
    """(is the path tracked, diagnostic for the not-tracked case)."""
    probe = _git("ls-files", "--error-unmatch", "--", relative)
    if probe.returncode == 0:
        return True, ""
    ignored = _git("check-ignore", "-v", "--", relative)
    if ignored.returncode == 0:
        return False, f"git reports it as IGNORED: {ignored.stdout.strip()}"
    if probe.stderr.strip():
        return False, probe.stderr.strip()
    return False, "git knows no such path"


def _requires_osm(case: str) -> Origin:
    origin = origin_of(case)
    if origin.kind == UNKNOWN:
        pytest.fail(
            f"data/processed/{case}/meta.json does not say plainly whether its context layer is "
            f"OpenStreetMap-derived or the declared synthetic fallback, so this guard cannot decide "
            f"whether a pin is required.\n  matched: {origin.describe() or '(nothing)'}\n"
            f"  contradictions: {list(origin.contradictions) or '(none)'}\n"
            "If pipeline/mock/artifacts.py was reworded, update _OSM_SIGNALS/_SYNTHETIC_SIGNALS here "
            "in the same commit — do not make this guard guess.",
            pytrace=False,
        )
    if origin.kind == SYNTHETIC:
        pytest.skip(
            f"data/processed/{case} publishes the declared synthetic layer "
            f"({origin.describe()}), which needs no pin; the OSM-only assertions do not apply"
        )
    return origin


# ---------------------------------------------------------------------------------------------
# 1. The published artefact declares its context origin, unambiguously
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_published_context_origin_is_declared(case: str) -> None:
    """Every artefact must say where its geography came from, in text a reader can check.

    This is the input of the rest of the module: without it the OSM/synthetic distinction would be
    assumed from the tree (a pin lying in `config/context/`) instead of read from the artefact.
    """
    origin = origin_of(case)
    print(f"{case}: context origin = {origin.kind.upper()} — matched {origin.describe()}")
    if origin.contradictions:
        pytest.fail(
            f"data/processed/{case}/meta.json claims BOTH an OSM-derived and a synthetic context "
            f"layer — one artefact can only have published one of them:\n  "
            + "\n  ".join(origin.contradictions),
            pytrace=False,
        )
    assert origin.kind in (OSM, SYNTHETIC), (
        f"data/processed/{case}/meta.json does not disclose whether its context layer is "
        f"OpenStreetMap-derived or synthetic (matched: {origin.describe() or '(nothing)'}).\n"
        "Every artefact must publish that in provenance.license, provenance.method and a "
        "limitations entry, because whether the geography is evidence about the city is exactly "
        "what a reader has to know."
    )


# ---------------------------------------------------------------------------------------------
# 2. An OSM-derived artefact must ship the tracked pin that reproduces it
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_osm_context_case_ships_a_tracked_pin(case: str, git_worktree: bool) -> None:
    """The pin is a reproduction input: untracked == absent for everyone but the author.

    A clone resolves only tracked files. `data/raw/` is gitignored, so once the pin is missing the
    generator falls through to the synthetic layer: the artefact cannot be rebuilt from the repo and
    `test_mock_reproducibility.py` goes red in CI, while `check_phase_a.sh` stays green locally
    because the pin is on the author's disk.
    """
    origin = _requires_osm(case)
    pin = CTX.pin_path(case)
    relative = pin.relative_to(ROOT).as_posix()

    assert pin.is_file(), (
        f"data/processed/{case}/meta.json publishes an ODbL/OSM context layer "
        f"({origin.describe()}), but {relative} does not exist — a clone falls back to the "
        "synthetic layer, so this artefact cannot be reproduced and test_mock_reproducibility will "
        f"fail in CI; commit the pin in the same commit as the artefacts (git add {relative})"
    )

    if not git_worktree:  # tarball/sdist: "tracked" has no answer, and pretending would be worse
        pytest.skip(f"{relative} exists, but this tree is not a git worktree so tracking is moot")

    tracked, diagnostic = _tracked_relative(relative)
    assert tracked, (
        f"data/processed/{case}/meta.json publishes an ODbL/OSM context layer but {relative} is "
        "not tracked — a clone falls back to the synthetic layer, so this artefact cannot be "
        "reproduced and test_mock_reproducibility will fail in CI; commit the pin in the same "
        f"commit as the artefacts (git add {relative}).\n"
        f"  published: provenance.license = {_meta(case)['provenance']['license']!r}\n"
        f"  git: {diagnostic}\n"
        "Locally every gate passes anyway, because the untracked pin is on this disk."
    )


@pytest.mark.parametrize("case", CASES)
def test_osm_context_case_generator_takes_the_pinned_branch(case: str) -> None:
    """A synthetic fallback under an ODbL-licensed artefact is the bug; require the pinned branch.

    `load_context` is the function `pipeline/generate_mock.py` uses, called here with
    `allow_synthetic=False` so the two silent branches (live extract, synthetic) raise or return a
    non-pinned context instead of quietly producing bytes that differ from `data/processed/`.
    """
    origin = _requires_osm(case)
    provenance = _provenance_id(_meta(case))
    try:
        context = CTX.load_context(case, provenance, allow_synthetic=False)
    except FileNotFoundError:
        pytest.fail(
            f"{case}: with `allow_synthetic=False` the generator has nowhere to load the context "
            f"from — no pin at {CTX.pin_path(case).relative_to(ROOT).as_posix()} and no "
            f"data/raw/{case}/context/ extract — although data/processed/{case}/meta.json "
            f"publishes an ODbL/OSM layer ({origin.describe()}). A clone has neither of the two, "
            "so this artefact is reproducible only where the untracked pin currently sits.",
            pytrace=False,
        )

    assert not context.synthetic, (
        f"{case}: the generator resolves a SYNTHETIC context although data/processed/{case}/meta.json "
        f"publishes an ODbL/OSM layer ({origin.describe()}) — the artefact and the code that writes "
        "it disagree; re-pin the case (pipeline/ingest/pin_context.py) or restore the artefact"
    )
    assert context.pinned, (
        f"{case}: the generator did not take the pinned branch — it resolved the live "
        f"data/raw/{case}/context/ extract instead (source {context.source!r}), which is gitignored "
        "and absent on a clone, so this artefact is reproducible only on this machine. "
        f"Commit {CTX.pin_path(case).relative_to(ROOT).as_posix()} and regenerate."
    )
    assert len(context.labels or {}) > 0, (
        f"{case}: the pin carries no cell_land_use, so the published land_use column cannot come "
        "from it; rebuild the pin with pipeline/ingest/pin_context.py"
    )
    assert case in CTX.summarise_context(context), f"{case}: run log does not name the pin"


# ---------------------------------------------------------------------------------------------
# 3. The published text names THIS pin, not some other extract
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_osm_context_case_discloses_the_timestamp_this_pin_records(case: str) -> None:
    """The OSM timestamp in `limitations` must be the one `config/context/<case>.json` stores.

    Otherwise the pin present in the tree is not the one the artefact was built from, and the bytes
    would be irreproducible even with the pin committed — a second, quieter version of the same bug.
    """
    _requires_osm(case)
    meta = _meta(case)
    payload = json.loads(CTX.pin_path(case).read_text(encoding="utf-8"))
    stamp = str((payload.get("record") or {}).get("timestamp_osm_base") or "")
    assert stamp, (
        f"config/context/{case}.json records no record.timestamp_osm_base, so the artefact cannot "
        "disclose which OSM snapshot it is derived from"
    )
    disclosed = [entry for entry in meta.get("limitations") or () if stamp in str(entry)]
    assert disclosed, (
        f"data/processed/{case}/meta.json does not disclose the OSM timestamp "
        f"{stamp!r} that config/context/{case}.json records: the pin in the tree is not the one "
        "this artefact was generated from."
    )

    # The pin must also cover the published cells: `label_cells` falls back to `unknown` for a cell
    # the pin does not name, so a partial pin would quietly change the published land_use column.
    import pandas as pd  # noqa: PLC0415 - only this test needs it

    published = set(
        pd.read_parquet(COMMITTED / case / "nisar_grid.parquet", columns=["cell_id"])["cell_id"]
    )
    labels = {str(cell) for cell in (payload.get("cell_land_use") or {})}
    uncovered = published - labels
    assert not uncovered, (
        f"config/context/{case}.json labels {len(labels)} cells but does not cover "
        f"{len(uncovered)} cell(s) published in data/processed/{case}/nisar_grid.parquet "
        f"(e.g. {sorted(uncovered)[:3]}): those cells publish as `unknown` from this pin and with "
        "their mapped class from the extract, so the pin and the artefact disagree."
    )


# ---------------------------------------------------------------------------------------------
# 4. The pre-OSM state must not be forced into a pin it never needed
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_synthetic_context_case_declares_the_fallback_and_needs_no_pin(case: str) -> None:
    """A synthetic artefact is legitimate: it must say so, and it must NOT be asked for a pin."""
    origin = origin_of(case)
    if origin.kind != SYNTHETIC:
        pytest.skip(
            f"data/processed/{case} publishes an OSM-derived layer ({origin.describe() or origin.kind})"
        )
    meta = _meta(case)
    licence = str((meta.get("provenance") or {}).get("license") or "")
    assert licence, f"{case}: synthetic artefact publishes no provenance.license"
    geography = [
        entry for entry in meta.get("limitations") or () if "invented by the synthetic fallback" in str(entry)
    ]
    assert geography, (
        f"data/processed/{case} names the synthetic fallback in provenance.license but discloses no "
        "limitation saying the geography is invented; a reader must not mistake it for a mapped layer"
    )
    # And it must not borrow the ODbL claim it is not entitled to.
    assert "ODbL" not in licence, (
        f"data/processed/{case} publishes the synthetic layer but its licence claims ODbL/OSM: "
        "that would send a reader to OpenStreetMap for geography that does not come from there"
    )
    print(f"{case}: declared synthetic fallback — no pin required ({origin.describe()})")


# ---------------------------------------------------------------------------------------------
# 5. Non-vacuity of the classifier and of the pin assertion itself
# ---------------------------------------------------------------------------------------------


def test_classifier_separates_the_two_published_states() -> None:
    """The classifier must be able to return both answers, from the writer's own two branches."""
    osm_meta = {
        "provenance": {
            "license": "Synthetic displacement field (mock) — no third-party data for the SAR "
            "values; context layers © OpenStreetMap contributors, ODbL 1.0",
            "method": "…; land use is joined from the OpenStreetMap extract and its polygons are "
            "the very cells this grid publishes, so the layer and the column share one assignment.",
        },
        "limitations": [
            "Context geography (land use, water, infrastructure) is derived from an OpenStreetMap "
            "extract (data through 2026-11-15T09:47:19Z) under the ODbL 1.0: …"
        ],
    }
    synthetic_meta = {
        "provenance": {
            "license": "Synthetic mock — no third-party data (repo-internal artefact)",
            "method": "…; land use is a deterministic spatial rule and its polygons are the very "
            "cells this grid publishes, so the layer and the column share one assignment.",
        },
        "limitations": [
            "Mock geography (coastline, lake, river, corridor, POIs) is invented by the synthetic "
            "fallback in pipeline/context.py and labelled as mock in every context feature."
        ],
    }
    osm_sources = ["OpenStreetMap contributors (ODbL 1.0), Overpass API extract"]
    synthetic_sources = ["mock (synthetic land-cover rule)"]

    osm = classify(_harvest(osm_meta, osm_sources))
    synthetic = classify(_harvest(synthetic_meta, synthetic_sources))
    assert osm.kind == OSM, f"OSM branch misclassified as {osm.kind}: {osm.describe()}"
    assert synthetic.kind == SYNTHETIC, (
        f"synthetic branch misclassified as {synthetic.kind}: {synthetic.describe()}"
    )
    assert classify(_harvest(osm_meta, synthetic_sources)).kind == UNKNOWN, (
        "an artefact whose licence claims ODbL but whose features are mock must be reported as "
        "contradictory, not silently accepted"
    )
    assert classify(_harvest({}, [])).kind == UNKNOWN, "silent text must classify as unknown, not OSM"


@pytest.mark.parametrize("case", CASES)
def test_the_pinned_assertion_is_not_vacuous(case: str, tmp_path: pathlib.Path) -> None:
    """Without the pin, `load_context` really does stop being pinned — so the guard can fail.

    Reads no extract: both the pin directory and the (absent on a clone) raw root point into
    `tmp_path`, so this hits the declared fallback exactly like a fresh clone would.
    """
    _requires_osm(case)
    meta = _meta(case)
    unpinned = CTX.load_context(
        case,
        _provenance_id(meta),
        root=tmp_path / "no-raw-extract",
        allow_synthetic=True,
        pin_directory=tmp_path / "no-pins",
    )
    assert not unpinned.pinned, (
        f"{case}: load_context returned a pinned context from {tmp_path / 'no-pins'} — the pin "
        "assertion in test_osm_context_case_generator_takes_the_pinned_branch could never fail"
    )
    assert unpinned.synthetic, (
        f"{case}: with no pin and no extract the generator did not fall back to the synthetic "
        f"layer (source {unpinned.source!r}); the 'a clone silently regenerates synthetic' premise "
        "of this module no longer holds — re-read pipeline/context.py::load_context"
    )
