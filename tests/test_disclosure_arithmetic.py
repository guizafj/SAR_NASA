"""Guard: the published geography disclosure must still *count* what the parquet contains.

Why this file exists
--------------------
`meta.json` carries one sentence — written by
`pipeline/mock/artifacts.py::_geography_limitation` — that names how much of the land-use layer is
unmapped: "… so N of M published cells (P %) are `unknown` — either no mapped polygon contains
their centroid, or the containing polygon's tags map to no class of the frozen land-use vocabulary.
Both causes stay `unknown` and neither is extrapolated from a neighbouring cell."
`tests/test_limitation_disclosures.py` asserts that *a* geography sentence is still present, that
the two branches are mutually exclusive and that both causes of `unknown` are named; nothing there
reads the three numbers back against the grid behind them.

So today an edit that changes the land-use assignment (say, a pin gets re-pinned or the H3 join
changes) can leave `meta.json` claiming one share while `nisar_grid.parquet` publishes another, and
every check stays green: the schema still validates, `stats.n_cells` still matches the parquet, and
the limitation count is unchanged. The same is true of the other direction — delete the sentence, or
let `provenance.method` drift back to calling the mapped land use a "deterministic spatial rule",
and no test reports it. A reader then sees a precise-looking percentage whose arithmetic is no
longer the artefact's: that is a false disclosure, not a typo, and it is the kind of claim a judge
does check by dividing two numbers.

What is pinned here
-------------------
For every case in `contracts.CASE_IDS` that has a directory under `data/processed/`:

1. the sentence's ``N``, ``M`` and ``P`` are recomputed from `nisar_grid.parquet` (``N`` rows with
   ``land_use == "unknown"``, ``M`` rows, ``P = 100*N/M``) and compared with the producer's own
   rounding (``f"{share:.1f}"``);
2. ``M`` is the parquet's roster and the sentence repeats it: the parquet row count (read back from
   disk) is compared with the sentence's ``M`` and with ``stats.n_cells``, and the drill-down rows in
   `timeseries.csv` must be a subset of that roster with the row count ``stats.n_series_rows``
   predicts. Note what is *not* evidence: ``stats.n_cells`` and the sentence's ``M`` are both
   ``model.n_cells`` evaluated inside one `build_meta` call, so that pair is tautological and is
   asserted only as a formatting invariant (see the docstring of the cell-count test);
3. the OSM sentence and the synthetic-geography sentence never coexist — the branch is decided by
   the artefact's own `context_layers.geojson` sources, never by a config flag;
4. an OSM-derived context names its licence (ODbL) and does not claim the context layers are the
   result of "deterministic spatial rules", while still stating that the published land_use
   polygons are the dissolved H3 cells of this grid;
5. ``water`` is a context class, never a grid class: no published row is labelled ``water``, yet the
   matching ``water`` layer is present when `config/context/<case>.json` pins one (skipped only
   when that pin is absent, because then there is nothing to compare);
6. the parser matched exactly one sentence per case, so tests 1-4 cannot pass vacuously.

Read-only, offline, filesystem-only. No `unittest.mock`, no producer import: the assertions are made
against the bytes the producer actually wrote, so a behavioural change in `pipeline/` cannot make
this file agree with itself.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pandas as pd
import pytest

from src.domain import contracts as C

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
CONTEXT_PINS = ROOT / "config" / "context"

#: Cases whose artefacts are actually on disk. Parametrising only over published cases keeps this
#: module usable on a tree where `data/processed/` has not been generated yet; if nothing is
#: published the module collects zero tests (pytest exits 5), which is louder than a green pass.
PUBLISHED: tuple[str, ...] = tuple(case for case in C.CASE_IDS if (PROCESSED / case).is_dir())

#: The OSM-branch sentence, anchored on stable phrases rather than the whole prose so a rewording
#: does not break the guard — but with the three numbers and the licence captured, because those are
#: what this file checks. `\s+` everywhere so a re-indented/re-wrapped string concat still matches.
#: Producer wording (`pipeline/mock/artifacts.py::_geography_limitation`):
#:   "Context geography (land use, water, infrastructure) is derived from an OpenStreetMap extract
#:    (data through <stamp>) under the ODbL 1.0: ... so <N> of <M> published cells (<P> %) are
#:    `unknown` — either no mapped polygon contains their centroid, or the containing polygon's tags
#:    map to no class of the frozen land-use vocabulary. Both causes stay `unknown` and neither is
#:    extrapolated from a neighbouring cell."
#: The regex stops at the percentage, so it never parses (or pins) the cause clause: the share is
#: measured here, and `tests/test_limitation_disclosures.py` owns the wording of the two causes.
DISCLOSURE = re.compile(
    r"Context\s+geography\b"
    r".*?\(data\s+through\s+(?P<stamp>[^)]+)\)"
    r"\s+under\s+the\s+ODbL\s+1\.0"
    r".*?(?P<unknown>\d+)\s+of\s+(?P<total>\d+)\s+published\s+cells"
    r"\s*\((?P<percent>\d+(?:\.\d+)?)\s*%\)",
    re.DOTALL,
)

#: The synthetic-branch sentence, quoted from the same producer function. It exists only so test 3
#: can assert its absence on a mapped artefact — never retyped from memory.
SYNTHETIC_SENTENCE = "Mock geography"

#: Producer phrases asserted by test 4 (both branches live in `build_meta` → `provenance.method`).
OSM_LAND_USE_PHRASE = "land use is joined from the OpenStreetMap extract"
DISSOLVED_CELLS_PHRASE = "the published land_use polygons are the dissolved H3 cells of this grid"
SYNTHETIC_LAND_USE_PHRASE = "land use is a deterministic spatial rule"


def _meta_path(case_id: str) -> pathlib.Path:
    return PROCESSED / case_id / "meta.json"


def _grid_path(case_id: str) -> pathlib.Path:
    return PROCESSED / case_id / "nisar_grid.parquet"


def _context_path(case_id: str) -> pathlib.Path:
    return PROCESSED / case_id / "context_layers.geojson"


def _meta(case_id: str) -> dict[str, Any]:
    return json.loads(_meta_path(case_id).read_text(encoding="utf-8"))


def _grid(case_id: str) -> pd.DataFrame:
    return pd.read_parquet(_grid_path(case_id))


def _series_path(case_id: str) -> pathlib.Path:
    return PROCESSED / case_id / "timeseries.csv"


def _series(case_id: str) -> pd.DataFrame:
    return pd.read_csv(_series_path(case_id), dtype={"cell_id": str})


def _features(case_id: str) -> list[dict[str, Any]]:
    return json.loads(_context_path(case_id).read_text(encoding="utf-8"))["features"]


def _limitations(meta: dict[str, Any]) -> list[str]:
    return [str(entry) for entry in meta["limitations"]]


def _disclosures(meta: dict[str, Any]) -> list[re.Match[str]]:
    return [match for entry in _limitations(meta) for match in DISCLOSURE.finditer(entry)]


def _one_disclosure(case_id: str) -> re.Match[str]:
    """The single OSM disclosure, or a failure that names the file it is missing from."""
    matches = _disclosures(_meta(case_id))
    assert len(matches) == 1, (
        f"{_meta_path(case_id)}: expected exactly one 'Context geography ... N of M (P %) ...' "
        f"limitation, found {len(matches)} (pattern {DISCLOSURE.pattern!r})"
    )
    return matches[0]


def _context_is_synthetic(case_id: str) -> bool:
    """True when the *artefact's* context sources mark the geography as invented.

    The branch is read from `context_layers.geojson` rather than from a config flag because the
    config says what the pipeline *intended* to publish; the artefacts say what a reader actually
    sees. `pipeline/context.py` writes ``mock (synthetic …)`` into the `source` of every feature on
    the synthetic fallback, so any such source is the published declaration.
    """
    sources = [str(feature.get("properties", {}).get("source") or "") for feature in _features(case_id)]
    return any("synthetic" in source.lower() for source in sources)


def _published_triple(case_id: str, grid: pd.DataFrame) -> tuple[int, int, float]:
    """``(unknown, total, share)`` exactly as the producer computes them from the model."""
    total = int(len(grid))
    unknown = int((grid["land_use"] == "unknown").sum())
    # Mirrors `_geography_limitation`: `100.0 * unknown / total if total else 0.0`, rendered `:.1f`.
    share = 100.0 * unknown / total if total else 0.0
    return unknown, total, share


def _fmt(unknown: int, total: int, share: str | float) -> str:
    return f"{unknown} of {total} ({share} %)"


# ---------------------------------------------------------------------------------------------
# 1. The sentence's arithmetic is the parquet's arithmetic
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", PUBLISHED)
def test_disclosure_numbers_match_the_published_grid(case_id: str) -> None:
    """The disclosed share must be recomputable from `nisar_grid.parquet`, digit for digit.

    A silent drift here publishes a precise-sounding percentage that the grid behind it contradicts:
    the reader cannot tell whether the layer or the sentence is stale, and both look authoritative.
    The failure message carries the sentence's triple and the grid's triple so the next person sees
    which side moved.
    """
    match = _one_disclosure(case_id)
    grid = _grid(case_id)
    unknown, total, share = _published_triple(case_id, grid)

    stated = (
        int(match.group("unknown")),
        int(match.group("total")),
        float(match.group("percent")),
    )
    measured = (unknown, total, float(f"{share:.1f}"))  # same rounding the producer applies

    assert stated == measured, (
        f"{_meta_path(case_id)}: sentence says {_fmt(*stated)} but "
        f"{_grid_path(case_id)} says {_fmt(measured[0], measured[1], f'{share:.1f}')} "
        f"(raw share {share!r})"
    )


# ---------------------------------------------------------------------------------------------
# 2. M is the grid's roster; the sentence repeats it and the drill-down lives inside it
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", PUBLISHED)
def test_published_cell_count_agrees_with_grid_and_disclosure(case_id: str) -> None:
    """``M`` is the parquet's roster and the sentence repeats it; the drill-down stays inside it.

    Only one *independent* source for ``M`` exists in this repo, and it is the grid:
    `pipeline/mock/model.py::grid_frame` fills every column from the model's arrays and
    `artifacts.write_parquet` writes them, so the bytes read back here are the only published roster
    of cells. So the pairs asserted as evidence are:

    * ``nisar_grid.parquet`` row count (also ``cell_id``-unique, so it is a cell count and not a row
      count inflated by a duplicated cell) ↔ the sentence's ``M``: an edit that re-pins cells and
      then hand-writes a stale denominator, or a truncated/rewritten parquet, is caught here;
    * ``timeseries.csv`` ↔ that roster: `pipeline/generate_mock.py::write_timeseries` builds the file
      from `select_per_cell_targets` plus the per-case byte budget — a different code path from
      `grid_frame` — so a drill-down ``cell_id`` the grid does not publish, a per-cell count that
      disagrees with ``stats.per_cell_series.n_cells`` (itself ``int(selected.size)``), or a row
      total that ``stats.n_series_rows`` (= ``len(frame)`` before the write) does not predict is a
      real inconsistency, not a restatement. On the default (``--per-cell-series`` off) artefacts
      this leg is degenerate but not vacuous: it forces the file to hold exactly the ``aoi``
      aggregate and ``per_cell_series.n_cells == 0``.

    The excluded pair, tautological by construction: ``meta.stats.n_cells`` is
    ``model.n_cells`` in `build_meta`'s ``stats`` dict, and the sentence's ``M`` is
    ``total = int(model.n_cells)`` inside `_geography_limitation(model)` — the same model object,
    the same attribute, both evaluated during a single `build_meta` call. Comparing them can never
    detect a wrong count. It is still asserted, but as a *formatting* invariant on the sentence
    (a hand-edited or reworded disclosure that dropped ``stats.n_cells``), and the assertion says so.
    """
    meta = _meta(case_id)
    grid = _grid(case_id)
    series = _series(case_id)
    match = _one_disclosure(case_id)

    cell_ids = grid["cell_id"].astype(str)
    rows = int(len(grid))
    stats_cells = int(meta["stats"]["n_cells"])
    stated = int(match.group("total"))

    # The roster is one row per H3 cell (contract invariant), so `rows` counts cells, not rows.
    assert int(cell_ids.nunique()) == rows, (
        f"{_grid_path(case_id)}: {rows} rows but {int(cell_ids.nunique())} distinct cell_id; the "
        f"published cell count cannot be read off a grid that repeats a cell"
    )

    assert rows == stated, (
        f"{case_id}: sentence says {stated} published cells, {_grid_path(case_id)} holds {rows} "
        f"rows ({_meta_path(case_id)})"
    )
    assert rows == stats_cells, (
        f"{case_id}: stats.n_cells {stats_cells} but {_grid_path(case_id)} holds {rows} rows "
        f"({_meta_path(case_id)})"
    )
    # Tautological by construction (see docstring): both are `int(model.n_cells)` in one
    # `build_meta` call. Kept only as a formatting invariant on the written sentence.
    assert stats_cells == stated, (
        f"{case_id}: sentence total {stated} != stats.n_cells {stats_cells} — formatting invariant "
        f"only, both come from model.n_cells in build_meta ({_meta_path(case_id)})"
    )

    # Third artefact, different producer path: the drill-down must be a subset of the grid roster,
    # with the size and row total the meta claims for it.
    per_cell_ids = set(series["cell_id"].astype(str)) - {"aoi"}
    per_cell_meta = int(meta["stats"]["per_cell_series"]["n_cells"])
    n_dates = len(meta["dates"])
    n_series_rows = int(meta["stats"]["n_series_rows"])

    assert per_cell_ids <= set(cell_ids), (
        f"{_series_path(case_id)}: {len(per_cell_ids - set(cell_ids))} drill-down cell_id(s) "
        f"absent from {_grid_path(case_id)} (e.g. {sorted(per_cell_ids - set(cell_ids))[:3]}): the "
        f"series and the grid were not built from the same cell set"
    )
    assert len(per_cell_ids) == per_cell_meta, (
        f"{_series_path(case_id)}: {len(per_cell_ids)} distinct drill-down cell_id(s) but "
        f"stats.per_cell_series.n_cells says {per_cell_meta} ({_meta_path(case_id)})"
    )
    assert len(series) == n_series_rows == n_dates * (1 + len(per_cell_ids)), (
        f"{_series_path(case_id)}: {len(series)} rows / stats.n_series_rows {n_series_rows} / "
        f"{n_dates} dates x (1 aoi + {len(per_cell_ids)} per-cell) disagree ({_meta_path(case_id)})"
    )


# ---------------------------------------------------------------------------------------------
# 3. One geography, one sentence
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", PUBLISHED)
def test_geography_disclosures_are_mutually_exclusive(case_id: str) -> None:
    """An artefact must not carry both geography disclosures, nor neither.

    Both sentences at once is a contradiction a reader cannot resolve ("mapped extract" and
    "invented by the fallback"); neither is a missing disclosure that the limitation-count guard
    cannot see because another entry can be swapped in. The branch is detected from the artefact's
    own `context_layers.geojson` `source` strings: the publisher writes ``mock (synthetic …)`` on the
    fallback, so the file itself says which geography was shipped, independent of the producer's
    intent (a config flag could disagree with what was actually written).
    """
    limitations = _limitations(_meta(case_id))
    synthetic_sentences = [entry for entry in limitations if SYNTHETIC_SENTENCE in entry]
    osm_sentences = _disclosures(_meta(case_id))

    if _context_is_synthetic(case_id):
        assert len(osm_sentences) == 0, (
            f"{_meta_path(case_id)}: context features declare synthetic sources but the artefact "
            f"also publishes the OSM sentence {osm_sentences[0].group(0)!r}"
        )
        assert len(synthetic_sentences) == 1, (
            f"{_meta_path(case_id)}: synthetic context must publish exactly one "
            f"{SYNTHETIC_SENTENCE!r} sentence, found {len(synthetic_sentences)}"
        )
    else:
        assert len(synthetic_sentences) == 0, (
            f"{_meta_path(case_id)}: context sources are mapped (no 'synthetic' source in "
            f"{_context_path(case_id)}) but the artefact still carries {synthetic_sentences!r}"
        )
        assert len(osm_sentences) == 1, (
            f"{_meta_path(case_id)}: mapped context must publish exactly one OSM sentence, "
            f"found {len(osm_sentences)}"
        )


# ---------------------------------------------------------------------------------------------
# 4. Licence and method say what the context actually is
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", PUBLISHED)
def test_licence_and_method_describe_the_context_that_was_published(case_id: str) -> None:
    """ODbL for mapped context; no "deterministic spatial rule" claim for land use.

    ``provenance.license`` is where a reuser looks for the terms they must honour, so an artefact
    built from an OSM extract that forgets ODbL is a licence problem, not a wording problem. The
    method sentence is the mirror: on the OSM branch ``build_meta`` writes "land use is joined from
    the OpenStreetMap extract (water, infrastructure and admin layers come from the same extract)
    and the published land_use polygons are the dissolved H3 cells of this grid, one per class, so
    the layer and the column share one assignment." If that collapses back to the synthetic wording
    ("land use is a deterministic spatial rule"), the artefact would be describing a rule it did not
    run while the geometry proves otherwise.

    The negative assertion is scoped to *land use* on purpose: both branches legitimately contain
    "coherence and risk bands are deterministic spatial rules", which is a different column, so an
    unscoped ``"deterministic spatial rules" not in method`` would be a permanent false alarm.
    """
    meta = _meta(case_id)
    provenance = meta["provenance"]
    license_text = str(provenance["license"])
    method = str(provenance["method"]).lower()

    if _context_is_synthetic(case_id):
        # Symmetric branch, so this test never silently skips: a synthetic artefact must not borrow
        # the OSM credit, and its method must keep the synthetic land-use wording.
        assert "odbl" not in license_text.lower(), (
            f"{_meta_path(case_id)}: synthetic context must not claim ODbL, got {license_text!r}"
        )
        assert SYNTHETIC_LAND_USE_PHRASE in method, (
            f"{_meta_path(case_id)}: synthetic context must state {SYNTHETIC_LAND_USE_PHRASE!r}, "
            f"method={method!r}"
        )
        assert OSM_LAND_USE_PHRASE.lower() not in method, (
            f"{_meta_path(case_id)}: synthetic context must not claim an OSM join, "
            f"method={method!r}"
        )
        return

    assert "ODbL" in license_text, (
        f"{_meta_path(case_id)}: OSM-derived context must name its licence (ODbL), "
        f"license={license_text!r}"
    )
    assert "OpenStreetMap contributors" in license_text, (
        f"{_meta_path(case_id)}: OSM-derived context must credit OpenStreetMap contributors, "
        f"license={license_text!r}"
    )
    assert SYNTHETIC_LAND_USE_PHRASE.lower() not in method, (
        f"{_meta_path(case_id)}: mapped context must not claim {SYNTHETIC_LAND_USE_PHRASE!r}, "
        f"method={method!r}"
    )
    assert OSM_LAND_USE_PHRASE.lower() in method, (
        f"{_meta_path(case_id)}: mapped context must state {OSM_LAND_USE_PHRASE!r}, "
        f"method={method!r}"
    )
    assert DISSOLVED_CELLS_PHRASE.lower() in method, (
        f"{_meta_path(case_id)}: mapped context must state {DISSOLVED_CELLS_PHRASE!r}, "
        f"method={method!r}"
    )


# ---------------------------------------------------------------------------------------------
# 5. Water is context, never a grid class
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", PUBLISHED)
def test_water_is_context_not_a_grid_class(case_id: str) -> None:
    """No published cell may be classed ``water`` while the water layer is published as context.

    A ``water`` row in the grid would mean a decorrelating surface survived the coherence gate and
    now carries a displacement — the opposite of what `null_policy` promises ("water and other
    decorrelating surfaces are absent rather than zero"). The second half keeps the complement
    honest: if the pin carries water features, the artefact's context must still publish them, or
    the map loses the water the grid deliberately excludes.
    """
    grid = _grid(case_id)
    classes = sorted(set(grid["land_use"].astype(str)))
    assert "water" not in classes, (
        f"{_grid_path(case_id)}: published rows are classed {classes}; "
        "'water' must stay a context class, not a grid class"
    )

    pin_path = CONTEXT_PINS / f"{case_id}.json"
    if not pin_path.is_file():
        pytest.skip(f"no context pin at {pin_path}: nothing to cross-check the water layer against")

    pinned_water = json.loads(pin_path.read_text(encoding="utf-8")).get("water") or []
    if not pinned_water:
        return

    layers = [str(feature.get("properties", {}).get("layer") or "") for feature in _features(case_id)]
    assert "water" in layers, (
        f"{_context_path(case_id)}: {pin_path} pins {len(pinned_water)} water feature(s) but the "
        f"artefact publishes layers {sorted(set(layers))} with no 'water' feature"
    )


# ---------------------------------------------------------------------------------------------
# 6. Non-vacuity: the parser must match, once, in every published case
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", PUBLISHED)
def test_the_disclosure_parser_matches_exactly_one_sentence(case_id: str) -> None:
    """A regex that matches nothing would make tests 1, 2 and 4 pass without checking anything.

    ``_one_disclosure`` already fails on zero matches; this test states the requirement in its own
    name so a future reader can see the file is guarded against turning green by matching nothing
    (for example if the sentence were reworded beyond the stable phrases). Two matches are also a
    failure: the same disclosure stated twice can disagree with itself.
    """
    matches = _disclosures(_meta(case_id))
    assert len(matches) == 1, (
        f"{_meta_path(case_id)}: pattern {DISCLOSURE.pattern!r} matched {len(matches)} sentence(s) "
        f"in limitations; expected exactly 1 so the arithmetic checks are not vacuous"
    )
