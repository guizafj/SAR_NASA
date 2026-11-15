"""Cross-artefact guard: what a judge READS on screen must equal what `data/processed/**` publishes.

`tests/test_ui_smoke.py` pins the tab labels and the demo banner; `tests/test_ui_bridge.py` pins the
18 hook signatures, the KPI card labels and a handful of their return values. Neither reads the DOM
the app actually produced, so a KPI card carrying a hardcoded, stale or invented figure passes the
whole suite. This file renders `app.py` with Streamlit's in-process `AppTest` and compares every
*displayed* number with the artefact behind it, for the three frozen cases.

Layers checked per case:

1. The formatted KPI cards (tab 1 and tab 2) against `meta.json::stats`, within the app's own
   display rounding. The cards are *computed from the grid parquet* by `get_series_kpis` /
   `get_map_kpis` (`src/data/ui_bridge.py`), so this is genuinely a cross-artefact parity check:
   the number on the card must agree with the number the case published.
2. The panels that echo an artefact: the tab-4 `meta.json` block (rendered by
   `src/ui/formatters.format_json_block`) and the tab-4 "case" stat cards, plus the tab-3 context
   cards and feature table against the selected case's `context_layers.geojson`.
3. The acquisition span `(62)` in the mission bar and the map footer against `meta.dates` /
   `meta.stats.n_dates`, and the cell-count caption against `meta.stats.n_cells`.
4. The null-result detection limit: shown only where `meta.stats` declares one.

Assertions never use a literal copied from the app for the *expected number*: every expected value
is read from `data/processed/<case>/meta.json`, `nisar_grid.parquet` or `context_layers.geojson`, or
from the frozen contract constant (`src.domain.contracts`) the loader formats with. Card labels are
DOM anchors, not expected values; `test_kpi_cards_are_labelled_as_the_bridge_publishes` re-derives
them from `src.data.ui_bridge` so a relabelled card cannot silently make the number checks vacuous.

Anti-vacuity
------------
`test_mutated_meta_median_velocity_is_caught` / `test_mutated_grid_velocity_is_caught` copy one
case into `tmp_path` (never the repo), corrupt the copy, and prove the parity helpers fail against
the app fed that copy. Technique: the app resolves its data through `src.data.loaders`, so the
loader is entered through a single seam — every hook in `src.data.ui_bridge.__all__` that accepts
`processed_dir=` is re-bound for the duration of the run to forward the temporary root (the hooks
are re-exported onto `src.data.loaders`, which is the module object `app.py::_loaders()` imports).
No `src/**` file is edited and `data/**` is only ever read.

Known gaps this file cannot close without touching `src/**` (reported, not fixed):

* `stats.annual_coverage`, `stats.n_dates`, `stats.n_context_features`, `stats.p05_velocity_mm_yr`,
  `stats.p95_velocity_mm_yr`, `stats.design_condition` and `stats.per_cell_series.*` are never
  formatted into a KPI card with a provenance badge; the only element that carries them is the
  whole-`meta.json` block in tab 4. A stale value would still be caught there, but no card-level
  provenance exists. `median_ratio_R` and `n_cells` *are* tab-4 stat cards now (checked here,
  without a provenance badge).
"""

from __future__ import annotations

import contextlib
import functools
import html
import inspect
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from src.data import loaders, ui_bridge
from src.domain import contracts as C
from src.ui import formatters

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
APP = ROOT / "app.py"

#: Display contract enforced on the cards. The loader rounds millimetre statistics to 2 decimals
#: and percentages to 1 (`src/data/ui_bridge.py`), so a card may show at most that many digits and
#: must agree with the published figure to within half of the last displayed digit.
_MM_DECIMALS = 2
_PCT_DECIMALS = 1

#: `src/ui/educational.py` prints its stat cards with `f"{value:g}"`, i.e. six significant digits.
_STAT_DIGITS = 6

#: Card labels published by `src/data/ui_bridge.py` (pinned there by `tests/test_ui_bridge.py`).
#: These are DOM anchors for the scraper, never an expected number.
_VELOCITY_LABEL = "Median velocity"
_SEASONAL_LABEL = "Median seasonal amplitude"
_COVERAGE_LABEL = "Coherent coverage"
_ABOVE_LABEL = "Cells above threshold"
_CRITICAL_LABEL = "Critical cells"

_CARD_RE = re.compile(r'<div class="cockpit-card">(.*?)</div>', re.S)
_BANNER_RE = re.compile(r'<div class="demo-banner" role="status">(.*?)</div>', re.S)
_NUMBER_RE = re.compile(r"^(?P<num>[+-]?\d+(?:\.\d+)?)(?:\s+(?P<unit>.*))?$")
_SPAN_RE = re.compile(r"dates (?P<first>\d{4}-\d{2}-\d{2}) … (?P<last>\d{4}-\d{2}-\d{2}) \((?P<n>\d+)\)")
_CAPTION_TOTAL_RE = re.compile(r"^(?P<shown>\d+) cells shown of (?P<total>\d+)$")
_CONTEXT_SUMMARY_RE = re.compile(r"<p>(?P<n>\d+) features · (?P<layers>[^<]*)</p>")
#: DOM anchor: the phrase `src/ui/map_view.py::detection_caption` emits around the artefact value.
_DETECTION_RE = re.compile(
    r"detection limit of (?P<num>[+-]?\d+(?:\.\d+)?)(?: (?P<unit>[^<\s]+))?"
)
#: Tab-4 renders `meta.json` through `formatters.format_json_block`, one `<div class="json-line">`
#: per JSON line; stripping the spans restores the exact JSON text.
_JSON_LINE_RE = re.compile(r'<div class="json-line"[^>]*>(.*?)</div>', re.S)
_JSON_BLOCK_ANCHOR = 'aria-label="meta.json contents"'
_TAG_RE = re.compile(r"<[^>]+>")
_CONTEXT_TABLE_RE = re.compile(
    r'<table class="cockpit-table"[^>]*><thead><tr>(?P<head>.*?)</tr></thead>'
    r"<tbody>(?P<body>.*?)</tbody></table>",
    re.S,
)
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[hd]>(.*?)</t[hd]>", re.S)
_COMPARE_ROW = '<div class="compare-row">'
_COMPARE_CASE_RE = re.compile(r'<span class="compare-case">(?P<label>.*?)</span>', re.S)
_COMPARE_META_RE = re.compile(r'<span class="compare-meta">(?P<tail>.*?)</span>', re.S)

CRITICAL_BAND = "critico"  # contracts.RISK_LEVELS critical member (frozen enum)


# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_loader_cache() -> Iterator[None]:
    loaders.clear_loader_cache()
    yield
    loaders.clear_loader_cache()


@pytest.fixture(scope="module", params=C.CASE_IDS, ids=list(C.CASE_IDS))
def rendered(request: pytest.FixtureRequest) -> dict[str, Any]:
    """One in-process render per frozen case, shared by the read-only assertions below."""
    case_id = str(request.param)
    meta = json.loads((PROCESSED / case_id / "meta.json").read_text(encoding="utf-8"))
    grid = pd.read_parquet(PROCESSED / case_id / "nisar_grid.parquet")
    context = json.loads(
        (PROCESSED / case_id / "context_layers.geojson").read_text(encoding="utf-8")
    )
    return {
        "case": case_id,
        "meta": meta,
        "grid": grid,
        "context": context,
        "at": _run_app(case_id),
    }


# --------------------------------------------------------------------------------------
# rendering + DOM scraping (AppTest renders in-process; no server, no network, no sleeps)
# --------------------------------------------------------------------------------------
def _run_app(case_id: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    # Select the case the way a judge does, without relying on format_func labels.
    at.session_state["filter_case_id"] = case_id
    at.run()
    assert not at.exception, f"{case_id}: the app raised {at.exception!r}"
    return at


def _markdown(at: AppTest) -> str:
    return "\n".join(str(node.value) for node in at.markdown)


def _field(block: str, css_class: str) -> str:
    match = re.search(rf'<p class="{css_class}"[^>]*>(.*?)</p>', block, re.S)
    return html.unescape(match.group(1)) if match else ""


def _cards(at: AppTest) -> list[dict[str, str]]:
    """Every rendered KPI card, in DOM order (tab 1, tab 2, tab 3, tab 4)."""
    cards = []
    for block in _CARD_RE.findall(_markdown(at)):
        if "kpi-value" not in block:
            continue
        cards.append(
            {
                "label": _field(block, "kicker"),
                "value": _field(block, "kpi-value"),
                "source": _field(block, "kpi-source"),
                "note": _field(block, "kpi-note"),
            }
        )
    return cards


def _cards_labelled(at: AppTest, label: str) -> list[dict[str, str]]:
    cards = [card for card in _cards(at) if card["label"] == label]
    assert cards, f"the app renders no KPI card labelled {label!r}"
    return cards


def _span_occurrences(at: AppTest) -> list[re.Match[str]]:
    return list(_SPAN_RE.finditer(html.unescape(_markdown(at))))


def _meta_panel(at: AppTest) -> dict[str, Any]:
    """The tab-4 `meta.json` block, restored to the JSON text `format_json_block` received."""
    text = _markdown(at)
    assert text.count(_JSON_BLOCK_ANCHOR) == 1, (
        "tab 4 no longer renders exactly one meta.json block, so no panel can be cross-checked"
    )
    raw = "\n".join(
        html.unescape(_TAG_RE.sub("", chunk)) for chunk in _JSON_LINE_RE.findall(text)
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:  # pragma: no cover - markup change, not data
        raise AssertionError(
            f"the tab-4 meta.json block is no longer parseable ({error}); first 400 chars: "
            f"{raw[:400]!r}"
        ) from error


def _compare_rows(at: AppTest) -> list[tuple[str, str]]:
    """`(case label, 'peak unit · provenance_id')` per row of the tab-1 comparison strip."""
    text = _markdown(at)
    starts = [match.start() for match in re.finditer(re.escape(_COMPARE_ROW), text)]
    rows: list[tuple[str, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        block = text[start:end]
        label = _COMPARE_CASE_RE.search(block)
        tail = _COMPARE_META_RE.search(block)
        assert label and tail, f"the comparison row markup changed: {block[:200]!r}"
        rows.append((html.unescape(label["label"]), html.unescape(tail["tail"])))
    return rows


def _context_table(at: AppTest) -> tuple[list[str], list[dict[str, str]]]:
    """Header names and cell values of the tab-3 context table."""
    match = _CONTEXT_TABLE_RE.search(_markdown(at))
    assert match, "tab 3 no longer renders the context feature table"
    header = [html.unescape(_TAG_RE.sub("", cell)) for cell in _CELL_RE.findall(match["head"])]
    rows = []
    for raw in _ROW_RE.findall(match["body"]):
        cells = [html.unescape(_TAG_RE.sub("", cell)) for cell in _CELL_RE.findall(raw)]
        rows.append(dict(zip(header, cells)))
    return header, rows


# --------------------------------------------------------------------------------------
# assertion helpers (also exercised directly by the mutation tests)
# --------------------------------------------------------------------------------------
def _assert_shows(shows: str, artefact: float, *, decimals: int, what: str) -> None:
    match = _NUMBER_RE.match(shows)
    assert match, f"{what}: {shows!r} is not a formatted number"
    number = match["num"]
    shown_decimals = len(number.partition(".")[2])
    assert shown_decimals <= decimals, (
        f"{what}: the app shows {number} ({shown_decimals} decimals) but the artefact is published "
        f"to {decimals} display decimals — fake precision or a stale digit"
    )
    tolerance = 0.5 * 10**-decimals + 1e-9
    assert abs(float(number) - float(artefact)) <= tolerance, (
        f"{what}: the app shows {number} but the artefact publishes {artefact!r} "
        f"(display rounding allows ±{tolerance:.6f})"
    )


def _assert_shows_significant(shows: str, artefact: float, *, what: str) -> None:
    """`f"{value:g}"` display: the artefact rounded to `_STAT_DIGITS` significant digits."""
    match = _NUMBER_RE.match(shows)
    assert match, f"{what}: {shows!r} is not a formatted number"
    number = float(match["num"])
    if artefact == 0:
        tolerance = 10 ** (1 - _STAT_DIGITS) + 1e-12
    else:
        exponent = math.floor(math.log10(abs(float(artefact))))
        tolerance = 0.5 * 10 ** (exponent - (_STAT_DIGITS - 1)) + 1e-12
    assert abs(number - float(artefact)) <= tolerance, (
        f"{what}: the app shows {match['num']} but the artefact publishes {artefact!r} "
        f"(a {_STAT_DIGITS}-significant-digit display allows ±{tolerance:.3g})"
    )


def assert_velocity_cards_match_meta(
    at: AppTest, meta: dict[str, Any], case_id: str
) -> None:
    """Tab 1 + tab 2 `Median velocity` vs `meta.stats.median_velocity_mm_yr`."""
    published = meta["stats"]["median_velocity_mm_yr"]
    cards = _cards_labelled(at, _VELOCITY_LABEL)
    assert len(cards) >= 2, f"{case_id}: Median velocity must appear on tab 1 and tab 2"
    for card in cards:
        _assert_shows(
            card["value"],
            published,
            decimals=_MM_DECIMALS,
            what=f"{case_id}: {_VELOCITY_LABEL} card",
        )


def assert_seasonal_cards_match_meta(at: AppTest, meta: dict[str, Any], case_id: str) -> None:
    """`Median seasonal amplitude` card, its unit, and the regime the note prints."""
    stats = meta["stats"]
    cards = _cards_labelled(at, _SEASONAL_LABEL)
    assert len(cards) >= 2, (
        f"{case_id}: the seasonal amplitude must appear on tab 1 and tab 2"
    )
    for card in cards:
        _assert_shows(
            card["value"],
            stats["median_seasonal_amp_mm"],
            decimals=_MM_DECIMALS,
            what=f"{case_id}: {_SEASONAL_LABEL} card",
        )
    notes = [card["note"] for card in cards if card["note"]]
    assert notes, f"{case_id}: the seasonal card lost its provenance note"
    assert any(
        f"dominant regime {stats['dominant_regime']}" in note for note in notes
    ), (
        f"{case_id}: no card note names the published dominant regime "
        f"{stats['dominant_regime']!r}; notes were {notes!r}"
    )


def assert_grid_derived_cards_match_grid(
    at: AppTest, meta: dict[str, Any], grid: pd.DataFrame, case_id: str
) -> None:
    """Cards computed from the grid (`ui_bridge`) checked against the grid artefact itself."""
    floor = float(meta["thresholds"]["coherence_min"])
    coherence = grid["coherence"].to_numpy(dtype=float)
    # The card is computed on the *filtered* view, whose default floor is this same threshold, so
    # the expectation is only honest because the artefact obeys its own null policy.
    assert (coherence >= floor).all(), (
        f"{case_id}: the grid carries cells below thresholds.coherence_min ({floor}), "
        "so the published coverage percentage and the app's filtered card disagree by design"
    )
    coverage = round(float((coherence >= floor).mean() * 100.0), _PCT_DECIMALS)
    (coverage_card,) = _cards_labelled(at, _COVERAGE_LABEL)
    _assert_shows(
        coverage_card["value"],
        coverage,
        decimals=_PCT_DECIMALS,
        what=f"{case_id}: {_COVERAGE_LABEL} card",
    )
    assert f"coherence >= {floor:g}" in coverage_card["note"], (
        f"{case_id}: the coverage note does not quote the published floor {floor}"
    )

    velocities = grid["velocity_mm_yr"].to_numpy(dtype=float)
    above = round(float((abs(velocities) >= C.NULL_RESULT_MM_YR).mean() * 100.0), _PCT_DECIMALS)
    (above_card,) = _cards_labelled(at, _ABOVE_LABEL)
    _assert_shows(
        above_card["value"],
        above,
        decimals=_PCT_DECIMALS,
        what=f"{case_id}: {_ABOVE_LABEL} card",
    )
    assert f"threshold {C.NULL_RESULT_MM_YR:g} mm/year" in above_card["note"], (
        f"{case_id}: the card does not name the frozen null-result threshold "
        f"{C.NULL_RESULT_MM_YR} mm/year it counted against"
    )

    critical = int((grid["risk_level"].astype(str) == CRITICAL_BAND).sum())
    (critical_card,) = _cards_labelled(at, _CRITICAL_LABEL)
    _assert_shows(
        critical_card["value"],
        critical,
        decimals=0,
        what=f"{case_id}: {_CRITICAL_LABEL} card",
    )


def _bridge_kpi_labels(case_id: str) -> set[str]:
    """The KPI card labels `src/data/ui_bridge.py` publishes for one case (tabs 1 and 2)."""
    labels = {str(item["label"]) for item in loaders.get_map_kpis(case_id, {})}
    labels |= {str(item["label"]) for item in loaders.get_series_kpis(case_id, {})}
    return labels


def assert_provenance_badge_is_the_artefact(at: AppTest, meta: dict[str, Any], case_id: str) -> None:
    """Every KPI card credits the `provenance_id` its own `meta.json` publishes."""
    published = meta["provenance"]["provenance_id"]
    cards = _cards(at)
    assert cards
    credited = [card for card in cards if card["source"]]
    stale = {card["label"]: card["source"] for card in credited if card["source"] != published}
    assert not stale, f"{case_id}: cards credit {stale} but meta.json publishes {published}"
    # Anti-vacuity: dropping the badge from every card must not turn this into a no-op.
    assert {card["label"] for card in credited} >= _bridge_kpi_labels(case_id), (
        f"{case_id}: only {sorted({card['label'] for card in credited})} carry a provenance "
        f"badge, but the bridge publishes {sorted(_bridge_kpi_labels(case_id))}"
    )


# --------------------------------------------------------------------------------------
# 1 + 2. displayed statistics vs meta.json
# --------------------------------------------------------------------------------------
def test_median_velocity_cards_match_published_stat(rendered: dict[str, Any]) -> None:
    assert_velocity_cards_match_meta(rendered["at"], rendered["meta"], rendered["case"])


def test_seasonal_amplitude_and_regime_match_published_stats(rendered: dict[str, Any]) -> None:
    assert_seasonal_cards_match_meta(rendered["at"], rendered["meta"], rendered["case"])


def test_kpi_units_come_from_the_contract(rendered: dict[str, Any]) -> None:
    at = rendered["at"]
    for card in _cards_labelled(at, _VELOCITY_LABEL):
        assert card["value"].endswith(f" {C.VELOCITY_UNIT}"), (
            f"{rendered['case']}: velocity card {card['value']!r} is not in {C.VELOCITY_UNIT}"
        )
    for card in _cards_labelled(at, _SEASONAL_LABEL):
        assert card["value"].endswith(f" {C.UNIT}"), (
            f"{rendered['case']}: seasonal card {card['value']!r} is not in {C.UNIT}"
        )


def test_grid_derived_cards_match_the_published_grid(rendered: dict[str, Any]) -> None:
    assert_grid_derived_cards_match_grid(
        rendered["at"], rendered["meta"], rendered["grid"], rendered["case"]
    )


def test_every_card_credits_the_published_provenance_id(rendered: dict[str, Any]) -> None:
    assert_provenance_badge_is_the_artefact(
        rendered["at"], rendered["meta"], rendered["case"]
    )


def test_kpi_cards_are_labelled_as_the_bridge_publishes(rendered: dict[str, Any]) -> None:
    """The DOM anchors above are the bridge's labels; drift here means update the anchors."""
    at, case_id = rendered["at"], rendered["case"]
    shown = {card["label"] for card in _cards(at)}
    missing = _bridge_kpi_labels(case_id) - shown
    assert not missing, (
        f"{case_id}: the app renders no card labelled {sorted(missing)} even though "
        "src/data/ui_bridge.py publishes those labels"
    )


def test_case_stat_cards_echo_the_published_stats(rendered: dict[str, Any]) -> None:
    """Tab 4 prints the v1.1 stats no chart formats (`median_ratio_R`, `n_cells`, ...)."""
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    stats = meta["stats"]
    cards = {card["label"]: card for card in _cards(at) if card["label"] in stats}
    assert {"median_ratio_R", "n_cells"} <= set(cards), (
        f"{case_id}: tab 4 no longer prints the published stats as cards; it rendered "
        f"{sorted(cards)}"
    )
    for label, card in cards.items():
        artefact = stats[label]
        if isinstance(artefact, str):
            assert card["value"] == artefact, (
                f"{case_id}: the {label} card shows {card['value']!r} but meta.json publishes "
                f"{artefact!r}"
            )
        else:
            # The card routes every value through `formatters.format_number` (2 dp, half-up) via
            # `_shown` in `src/ui/educational.py`. Compare against that same documented rule
            # exactly rather than assuming a 6-significant-digit display: a wrong number still
            # fails, and so does a silent change of the rounding rule.
            expected = formatters.format_number(artefact)
            match = _NUMBER_RE.match(card["value"])
            assert match and match["num"] == expected, (
                f"{case_id}: the {label} stat card shows {card['value']!r} but the artefact "
                f"publishes {artefact!r}, which `format_number` displays as {expected!r}"
            )
        # The unit follows from the published key, not from the card's copy.
        if label.endswith("_mm_yr"):
            assert card["value"].endswith(f" {C.VELOCITY_UNIT}"), (
                f"{case_id}: the {label} card {card['value']!r} is not in {C.VELOCITY_UNIT}"
            )
        elif label.endswith("_mm"):
            assert card["value"].endswith(f" {C.UNIT}"), (
                f"{case_id}: the {label} card {card['value']!r} is not in {C.UNIT}"
            )


# --------------------------------------------------------------------------------------
# 3. counts: dates and cells
# --------------------------------------------------------------------------------------
def test_acquisition_span_matches_meta_dates(rendered: dict[str, Any]) -> None:
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    dates = [str(item) for item in meta["dates"]]
    occurrences = _span_occurrences(at)
    assert len(occurrences) >= 2, (
        f"{case_id}: the acquisition span must show in the mission bar and the map footer, "
        f"found {len(occurrences)}"
    )
    for match in occurrences:
        assert match["first"] == dates[0] and match["last"] == dates[-1], (
            f"{case_id}: the app spans {match['first']} … {match['last']} but meta.json publishes "
            f"{dates[0]} … {dates[-1]}"
        )
        assert int(match["n"]) == len(dates) == meta["stats"]["n_dates"], (
            f"{case_id}: the app shows ({match['n']}) acquisitions, meta.dates has {len(dates)} "
            f"and meta.stats.n_dates is {meta['stats']['n_dates']}"
        )


def test_cell_total_matches_meta_n_cells(rendered: dict[str, Any]) -> None:
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    captions = [str(node.value) for node in at.caption]
    totals = [
        (int(m["shown"]), int(m["total"]))
        for m in (_CAPTION_TOTAL_RE.match(value) for value in captions)
        if m
    ]
    assert len(totals) == 1, f"{case_id}: expected one cell-count caption, found {totals}"
    shown, total = totals[0]
    assert total == meta["stats"]["n_cells"] == len(rendered["grid"]), (
        f"{case_id}: the app totals {total} cells, meta.stats.n_cells is "
        f"{meta['stats']['n_cells']} and the parquet has {len(rendered['grid'])}"
    )
    assert shown == total, (
        f"{case_id}: the default filter strip hides cells ({shown} of {total}), so the KPI cards "
        "would describe a subset the artefacts never publish"
    )


def test_cross_case_bar_peak_matches_the_grid(rendered: dict[str, Any]) -> None:
    """The tab-1 comparison strip prints one peak per case; each must be that case's grid peak."""
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    rows = _compare_rows(at)
    assert rows, f"{case_id}: the comparison strip renders no rows"
    # The strip lists cases by their published display name, not by slug.
    labels = {str(case["id"]): str(case["label"]) for case in loaders.load_study_cases()}
    mine = [tail for label, tail in rows if label == labels[case_id]]
    assert len(mine) == 1, (
        f"{case_id}: the comparison strip does not list {labels[case_id]!r} exactly once; "
        f"rows were {[label for label, _tail in rows]}"
    )
    peak = round(float(rendered["grid"]["velocity_mm_yr"].abs().max()), _MM_DECIMALS)
    _assert_shows(
        mine[0].split(" · ")[0],
        peak,
        decimals=_MM_DECIMALS,
        what=f"{case_id}: comparison-strip peak",
    )
    assert meta["provenance"]["provenance_id"] in mine[0], (
        f"{case_id}: the comparison row credits {mine[0]!r}, not the published provenance_id"
    )


# --------------------------------------------------------------------------------------
# 4. detection limit: shown only where the artefact declares one
# --------------------------------------------------------------------------------------
def test_detection_limit_caption_follows_the_artefact(rendered: dict[str, Any]) -> None:
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    stats = meta["stats"]
    text = html.unescape(_markdown(at))
    found = list(_DETECTION_RE.finditer(text))
    # The unit follows from the published key; a `_mm_yr` statistic is a rate.
    limit = stats.get("detection_limit_mm_yr")
    if limit is None:
        assert not found, (
            f"{case_id}: the app shows a detection limit "
            f"{[match.group(0) for match in found]} but meta.stats has no "
            "detection_limit_mm_yr — that number has no artefact behind it"
        )
        return
    assert found, f"{case_id}: meta.stats publishes a detection limit the app never shows"
    for match in found:
        _assert_shows_significant(
            f"{match['num']} {match['unit'] or ''}".strip(),
            limit,
            what=f"{case_id}: detection-limit caption",
        )
        assert match["unit"] == C.VELOCITY_UNIT, (
            f"{case_id}: the caption prints the detection limit in {match['unit']!r}, but "
            f"meta.stats.detection_limit_mm_yr is a {C.VELOCITY_UNIT} rate"
        )


# --------------------------------------------------------------------------------------
# 5. the demo banner equals the declared banner text
# --------------------------------------------------------------------------------------
def _expected_banner() -> str:
    """The banner the code is supposed to render, from the constant both sites share.

    `src/ui/formatters.DEMO_BANNER` is what `app.py::render_header` and
    `src/ui/educational.py` render, so pinning the DOM to it keeps the banner and the
    `product == "mock"` disclosure in one place.
    """
    constant = getattr(formatters, "DEMO_BANNER", None)
    assert isinstance(constant, str) and constant.strip(), (
        "src/ui/formatters.py no longer declares DEMO_BANNER, so the rendered banner has no "
        "single source of truth"
    )
    return constant


def test_demo_banner_is_the_declared_banner(rendered: dict[str, Any]) -> None:
    expected = _expected_banner()
    shown = [html.unescape(text) for text in _BANNER_RE.findall(_markdown(rendered["at"]))]
    assert shown, f"{rendered['case']}: the mock case renders no demo banner"
    assert set(shown) == {expected}, (
        f"{rendered['case']}: the app renders {sorted(set(shown))} but the declared banner is "
        f"{expected!r}"
    )


# --------------------------------------------------------------------------------------
# 6. panels that echo an artefact verbatim
# --------------------------------------------------------------------------------------
def test_meta_panel_echoes_the_published_artefact(rendered: dict[str, Any]) -> None:
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    panel = _meta_panel(at)

    # Keys `load_meta` injects for display; both are the contract constant or a published value.
    loaded = loaders.load_meta(case_id)
    injected = set(loaded) - set(meta)
    assert injected <= {"unit", "provenance_id"}, (
        f"{case_id}: tab 4 invents keys {sorted(injected)}"
    )
    if "unit" in injected:
        assert loaded["unit"] == C.UNIT
    if "provenance_id" in injected:
        assert loaded["provenance_id"] == meta["provenance"]["provenance_id"]

    expected = {**meta, **{key: loaded[key] for key in injected}}
    stale = {
        key: (value, panel.get(key))
        for key, value in expected.items()
        if panel.get(key) != value
    }
    assert not stale, f"{case_id}: tab 4 shows {stale} but meta.json says otherwise"
    assert set(panel) == set(expected), (
        f"{case_id}: tab 4 renders keys {sorted(set(panel) - set(expected))} and drops "
        f"{sorted(set(expected) - set(panel))}"
    )


def test_context_panel_matches_the_selected_cases_artefact(rendered: dict[str, Any]) -> None:
    """Tab 3 must summarise the `context_layers.geojson` of the case the selector names.

    The merged panel prints cards and a feature table instead of the artefact path, so the claim
    is checked where it lives: the counts, the layer breakdown, the rows and the credits it
    renders all come from the selected case's file — and, because the three cases publish
    different `provenance_id`s, from *that* case only.
    """
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    features = [
        dict(feature.get("properties") or {}) for feature in rendered["context"]["features"]
    ]
    assert features, f"{case_id}: context_layers.geojson carries no features to compare against"

    counts: dict[str, int] = {}
    for props in features:
        layer = str(props.get("layer") or "")
        if layer:
            counts[layer] = counts.get(layer, 0) + 1
    breakdown = ", ".join(f"{layer} {count}" for layer, count in sorted(counts.items()))

    (delivered,) = _cards_labelled(at, "Delivered features")
    _assert_shows(
        delivered["value"], len(features), decimals=0, what=f"{case_id}: Delivered features"
    )
    (layers_card,) = _cards_labelled(at, "Delivered layers")
    _assert_shows(
        layers_card["value"], len(counts), decimals=0, what=f"{case_id}: Delivered layers"
    )
    (breakdown_card,) = _cards_labelled(at, "Layer counts")
    assert breakdown_card["value"] == breakdown, (
        f"{case_id}: the layer breakdown reads {breakdown_card['value']!r} but "
        f"context_layers.geojson yields {breakdown!r}"
    )

    summary = _CONTEXT_SUMMARY_RE.search(_markdown(at))
    assert summary, f"{case_id}: tab 3 no longer prints the feature/layer summary line"
    assert int(summary["n"]) == len(features), (
        f"{case_id}: tab 3 claims {summary['n']} features but the artefact has {len(features)}"
    )
    assert summary["layers"] == ", ".join(sorted(counts)), (
        f"{case_id}: tab 3 lists layers {summary['layers']!r} but the artefact has "
        f"{sorted(counts)}"
    )

    header, rows = _context_table(at)
    expected_rows = [
        {
            field: ("" if props.get(field) is None else str(props.get(field)).replace("\n", " "))
            for field in header
        }
        for props in features
    ]
    assert len(rows) == len(expected_rows), (
        f"{case_id}: tab 3 renders {len(rows)} rows but the artefact has {len(expected_rows)} "
        "features"
    )
    assert rows == expected_rows, (
        f"{case_id}: the context table does not echo the artefact; first difference: "
        f"{next((pair for pair in zip(rows, expected_rows) if pair[0] != pair[1]), None)}"
    )
    # Selector-following: every credit is this case's, not the primary case's fallback.
    published = meta["provenance"]["provenance_id"]
    credited = {row["provenance_id"] for row in rows if "provenance_id" in row}
    assert credited == {published}, (
        f"{case_id}: tab 3 credits {sorted(credited)} but this case publishes {published!r} "
        "(the panel is not following the case selector)"
    )


# --------------------------------------------------------------------------------------
# 7. anti-vacuity: the guards fail against a corrupted COPY under /tmp
# --------------------------------------------------------------------------------------
def _loader_hooks_accepting_processed_dir() -> dict[str, Any]:
    hooks = {}
    for name in ui_bridge.__all__:
        function = getattr(loaders, name, None)
        if function is None:
            continue
        try:
            parameters = inspect.signature(function).parameters
        except (TypeError, ValueError):  # pragma: no cover - builtins only
            continue
        if "processed_dir" in parameters:
            hooks[name] = function
    return hooks


@contextlib.contextmanager
def _app_reading(processed_dir: Path) -> Iterator[None]:
    """Point every artefact-reading loader hook at another root for the duration of a render.

    `app.py::_loaders()` resolves `src.data.loaders` at run time, so re-binding the module
    attributes it re-exports from `ui_bridge` is enough: no `src/**` edit, no environment
    variable, and the repository tree is never written.
    """
    saved = _loader_hooks_accepting_processed_dir()
    assert "load_meta" in saved and "get_series_kpis" in saved, (
        "the loader seam changed; the mutation harness can no longer redirect the app"
    )

    def redirected(function: Any) -> Any:
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            kwargs["processed_dir"] = processed_dir
            return function(*args, **kwargs)

        return wrapper

    try:
        for name, function in saved.items():
            setattr(loaders, name, redirected(function))
        loaders.clear_loader_cache()
        yield
    finally:
        for name, function in saved.items():
            setattr(loaders, name, function)
        loaders.clear_loader_cache()


def _corrupted_copy(
    root: Path,
    case_id: str,
    *,
    corrupt_meta: Any = None,
    corrupt_grid: Any = None,
) -> Path:
    """Copy one case into `root/<case>` (never the repo) and apply the corruption there."""
    target = root / case_id
    shutil.copytree(PROCESSED / case_id, target)
    if corrupt_grid is not None:
        grid = pd.read_parquet(target / "nisar_grid.parquet")
        corrupt_grid(grid)
        grid.to_parquet(target / "nisar_grid.parquet", index=False)
    if corrupt_meta is not None:
        meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
        corrupt_meta(meta)
        (target / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return root


def test_mutated_meta_median_velocity_is_caught(tmp_path: Path) -> None:
    """A stale `stats.median_velocity_mm_yr` must not survive this file.

    If the app echoed meta.json, corrupting it would agree with the card; if the test only
    recomputed the grid, the mutated artefact would never be compared. The guard fails.
    """
    case_id = "malaga"
    published = json.loads((PROCESSED / case_id / "meta.json").read_text())["stats"][
        "median_velocity_mm_yr"
    ]
    corrupted = 12.5
    assert corrupted != published

    root = _corrupted_copy(
        tmp_path / "processed",
        case_id,
        corrupt_meta=lambda meta: meta["stats"].__setitem__("median_velocity_mm_yr", corrupted),
    )
    with _app_reading(root):
        at = _run_app(case_id)
        shown = [card["value"] for card in _cards_labelled(at, _VELOCITY_LABEL)]
        mutated_meta = json.loads((root / case_id / "meta.json").read_text(encoding="utf-8"))
        mutated_grid = pd.read_parquet(root / case_id / "nisar_grid.parquet")
        # The card still renders the grid's own number, not the corrupted stat.
        assert shown and shown[0].split()[0] != f"{corrupted:g}"
        with pytest.raises(AssertionError, match=_VELOCITY_LABEL):
            assert_velocity_cards_match_meta(at, mutated_meta, case_id)
        # Every other guard stays green: the failure is the mutation, not collateral damage.
        assert_seasonal_cards_match_meta(at, mutated_meta, case_id)
        assert_grid_derived_cards_match_grid(at, mutated_meta, mutated_grid, case_id)
        assert_provenance_badge_is_the_artefact(at, mutated_meta, case_id)


def test_mutated_grid_velocity_is_caught(tmp_path: Path) -> None:
    """The rendered value is live, not an echo of `meta.stats`.

    The copy's parquet is rewritten *consistently* — `risk_level` follows the new velocity and
    `ratio_R` follows `|velocity| / seasonal_amp_mm`, and the copy's `stats.median_ratio_R` is
    re-published from the new column — so `src/data/validation.py` accepts the bundle and the app
    renders. Only the cross-artefact parity guard can notice that the card and
    `stats.median_velocity_mm_yr` no longer describe the same case. A test that hardcoded the card
    text, or compared `meta.stats` with itself, would stay green here.
    """
    case_id = "malaga"

    def corrupt(grid: pd.DataFrame) -> None:
        velocity = grid["velocity_mm_yr"].to_numpy(dtype=float) * 2.0
        amplitude = grid["seasonal_amp_mm"].to_numpy(dtype=float)
        ratio = [abs(v) / a if a else None for v, a in zip(velocity, amplitude)]
        grid["velocity_mm_yr"] = velocity
        grid["risk_level"] = [C.risk_level_from_velocity(value) for value in velocity]
        grid["ratio_R"] = ratio

    def republish(meta: dict[str, Any]) -> None:
        grid = pd.read_parquet(target / "nisar_grid.parquet")
        meta["stats"]["median_ratio_R"] = float(grid["ratio_R"].median())

    target = tmp_path / "processed" / case_id
    root = _corrupted_copy(
        tmp_path / "processed", case_id, corrupt_grid=corrupt, corrupt_meta=republish
    )
    assert target.is_dir()
    with _app_reading(root):
        at = _run_app(case_id)
        mutated_meta = json.loads((root / case_id / "meta.json").read_text(encoding="utf-8"))
        published = mutated_meta["stats"]["median_velocity_mm_yr"]
        shown = [card["value"] for card in _cards_labelled(at, _VELOCITY_LABEL)]
        assert shown and abs(float(shown[0].split()[0]) - published) > 0.005, (
            "the corrupted grid did not move the card, so this fixture proves nothing"
        )
        with pytest.raises(AssertionError, match=_VELOCITY_LABEL):
            assert_velocity_cards_match_meta(at, mutated_meta, case_id)
        # The card agrees with the corrupted parquet, so the guard that reads the grid directly
        # stays green: the failure is the meta/parquet disagreement, not a broken render.
        assert_grid_derived_cards_match_grid(
            at, mutated_meta, pd.read_parquet(root / case_id / "nisar_grid.parquet"), case_id
        )
