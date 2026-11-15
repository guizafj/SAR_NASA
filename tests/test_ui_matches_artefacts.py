"""Cross-artefact guard: what a judge READS on screen must equal what `data/processed/**` publishes.

`tests/test_ui_smoke.py` pins the tab labels and the demo banner; `tests/test_ui_bridge.py` pins the
18 hook signatures, the KPI card labels and a handful of their return values. Neither reads the DOM
the app actually produced, so a KPI card carrying a hardcoded, stale or invented figure passes the
whole suite. This file renders `app.py` with Streamlit's in-process `AppTest` and compares every
*displayed* number with the artefact behind it, for the three frozen cases.

Layers checked per case:

1. The formatted KPI figures against `meta.json::stats`, within the app's own display rounding.
   The redesign publishes the same datum through three components: the tab-1 `reading-stat`
   strip (`map_view._stats_strip`), the tab-2 `kpi_card` row (`kpi_cards.render_kpi_cards`) and
   the tab-4 stat cards (`educational._case_block`). All three are scraped. The numbers are
   *computed from the grid parquet* by `get_series_kpis` / `get_map_kpis` (`src/data/ui_bridge.py`),
   so this is genuinely a cross-artefact parity check: every visible card must agree with the
   number the case published.
2. The panels that echo an artefact: the tab-4 `meta.json` block (rendered by
   `src/ui/formatters.format_json_block`) and the tab-4 "case" stat cards, plus the tab-3 context
   cards and feature table against the selected case's `context_layers.geojson`. Tab 4 labels a
   stat card with `formatters.humanize(key)` and the table echoes the app's display translation
   (`humanize`, `translate_feature_name`, `risk_label`), so the expected text is derived from those
   formatters instead of copied out of the app. The feature count on the tab-3 card is also
   cross-checked against `meta.stats.n_context_features`, so the GeoJSON and `meta.json` must agree.
3. The acquisition span `(62)` in the mission bar (the redesign prints it once; the retired map
   footer duplicate is no longer required) against `meta.dates` / `meta.stats.n_dates`, and the
   `N of M cells` count in the map footer against `meta.stats.n_cells`.
4. The null-result detection limit: shown only where `meta.stats` declares one.

Assertions never use a literal copied from the app for the *expected number*: every expected value
is read from `data/processed/<case>/meta.json`, `nisar_grid.parquet` or `context_layers.geojson`, or
from the frozen contract constant (`src.domain.contracts`) the loader formats with. Card labels are
DOM anchors, not expected values; `test_kpi_cards_are_labelled_as_the_bridge_publishes` re-derives
them from `src.data.ui_bridge` so a relabelled card cannot silently make the number checks vacuous.

Provenance is credited once per card group, and the guard follows the render order rather than
assuming a widget: `src/ui/kpi_cards.py::render_kpi_cards` (tab-2 row), the tab-1
`Source … · Provenance ID …` caption that closes the `reading-stat` strip,
`src/ui/context_view.py::_render_table` (one note above the table, or a per-row column when the
rows disagree) and `src/ui/educational.py::_case_block` (tab-4 stat cards) each print one
`Provenance ID …` line for the group. The guard pairs every KPI figure group with the provenance
line that follows it, and requires that line to name exactly the case's published `provenance_id`.
The tab-1 comparison strip no longer credits an id (its note sends the reader to the provenance
tab), so that strip is checked on the peaks it still publishes.

Anti-vacuity
------------
`test_mutated_meta_median_velocity_is_caught` / `test_mutated_grid_velocity_is_caught` copy the
published cases into `tmp_path` (never the repo), corrupt one, and prove the parity helpers fail
against the app fed that copy. Technique: the app resolves its data through `src.data.loaders`, so
the loader is entered through a single seam — every hook in `src.data.ui_bridge.__all__` that
accepts `processed_dir=` is re-bound for the duration of the run to forward the temporary root (the
hooks are re-exported onto `src.data.loaders`, which is the module object `app.py::_loaders()`
imports). No `src/**` file is edited and `data/**` is only ever read.

Known gaps this file cannot close without touching `src/**` (reported, not fixed):

* `stats.annual_coverage`, `stats.n_dates`, `stats.p05_velocity_mm_yr`,
  `stats.p95_velocity_mm_yr`, `stats.design_condition` and `stats.per_cell_series.*` are never
  formatted into a KPI card with a provenance badge; the only element that carries them is the
  whole-`meta.json` block in tab 4. A stale value would still be caught there, but no card-level
  provenance exists. `stats.n_context_features` is cross-checked against the GeoJSON count (tab 3)
  and `median_ratio_R` / `n_cells` *are* tab-4 stat cards now (checked here); their credit is the
  one `Provenance ID …` caption printed for the whole tab-4 stat-card group.
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

#: Card labels published by `src/data/ui_bridge.py` (pinned there by `tests/test_ui_bridge.py`).
#: These are DOM anchors for the scraper, never an expected number.
_VELOCITY_LABEL = "Median velocity"
_SEASONAL_LABEL = "Median seasonal amplitude"
_COVERAGE_LABEL = "Coherent coverage"
_ABOVE_LABEL = "Cells above threshold"
_CRITICAL_LABEL = "Critical cells"

# The redesigned card (`src/ui/components.py::kpi_card`) is `cockpit-card kpi-card` and prints
# the unit inside `<span class="kpi-unit">`, so the class list must be matched, not the old
# bare `cockpit-card`, and the value must be flattened before it is parsed as a number.
_CARD_RE = re.compile(r'<div class="cockpit-card[^"]*"[^>]*>(.*?)</div>', re.S)
# Tab 1 renders its three grid KPIs as a `reading-stat` strip (`map_view._stats_strip`), not
# through `kpi_card`. One match per figure: value (with its own unit span) then label.
_READING_STAT_RE = re.compile(
    r'<span class="stat-value">(?P<value>.*?)</span><span class="stat-label">'
    r"(?P<label>.*?)</span>",
    re.S,
)
_UNIT_SPAN_RE = re.compile(r'<span class="(?:kpi|stat)-unit">\s*')
_BANNER_RE = re.compile(r'<div class="demo-banner" role="status">(.*?)</div>', re.S)
_NUMBER_RE = re.compile(r"^(?P<num>[+-]?\d+(?:\.\d+)?)(?:\s+(?P<unit>.*))?$")
# `formatters.format_dates_span` output, capitalised at the start of the mission-bar run.
_SPAN_RE = re.compile(
    r"dates (?P<first>\d{4}-\d{2}-\d{2}) … (?P<last>\d{4}-\d{2}-\d{2}) \((?P<n>\d+)\)",
    re.IGNORECASE,
)
# The map footer prints the cell count as `<shown> of <total> cells` (`map_view._legend_details`).
_CELL_COUNT_RE = re.compile(r"(?P<shown>\d+) of (?P<total>\d+) cells")
# Tab 3 prints `<shown> features shown of <total>` (`context_view.render_hazard_tab`).
_CONTEXT_SUMMARY_RE = re.compile(
    r"<p>(?P<shown>\d+) features shown of (?P<total>\d+)</p>"
)
# One credit per group: a bare caption, or the note line inside the group's own markup.
_CAPTION_CREDIT_RE = re.compile(r"Provenance ID (?P<ids>\S+(?: · \S+)*)\s*$")
_NOTE_CREDIT_RE = re.compile(r'<p class="kpi-note">Provenance ID (?P<ids>[^<]+?)</p>')
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
#: The columns `src/ui/context_view.py` renders, in order before `_visible_frame` drops `name`.
_CONTEXT_TABLE_FIELDS = (
    "layer",
    "class",
    "name",
    "source",
    "nearest_cell_id",
    "risk_level",
    "distance_m",
    "provenance_id",
)
# Rows may carry a status class (`context_view._render_table` marks critical/moderate features).
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[hd]>(.*?)</t[hd]>", re.S)
_COMPARE_ROW = '<div class="compare-row">'
_COMPARE_CASE_RE = re.compile(r'<span class="compare-case">(?P<label>.*?)</span>', re.S)
_COMPARE_META_RE = re.compile(r'<span class="compare-meta">(?P<tail>.*?)</span>', re.S)

CRITICAL_BAND = "critico"  # contracts.RISK_LEVELS critical member (frozen enum)

#: The additive keys `src/data/ui_bridge.load_meta` is documented to surface from
#: `meta.json::provenance` (plus the contract unit). Anything else at tab 4's top level is invented.
_PROVENANCE_FIELD_KEYS = frozenset(
    {
        "provenance_id",
        "source",
        "method",
        "license",
        "reference",
        "processing_date",
        "product_id",
        "retrieved_utc",
    }
)
_ADDITIVE_META_KEYS = _PROVENANCE_FIELD_KEYS | {"unit", "provenance_fields"}


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


def _plain_value(raw: str) -> str:
    """Card text as a reader sees it: the unit span becomes `value unit`, tags are dropped.

    `components.kpi_card` emits `<p class="kpi-value">value<span class="kpi-unit">unit</span></p>`
    with no separating space (the gap is CSS), so the span is replaced by a space *before* stripping
    tags; otherwise `-64.68` and `mm/year` would fuse into one unparseable token.
    """
    spaced = _UNIT_SPAN_RE.sub(" ", raw)
    return " ".join(html.unescape(_TAG_RE.sub("", spaced)).split())


def _cards(at: AppTest) -> list[dict[str, str]]:
    """Every rendered KPI figure, in DOM order (tab 1, tab 2, tab 3, tab 4).

    Two components publish the same kind of figure: `kpi_card` (tab 2, tab 3, tab 4) and the
    tab-1 `reading-stat` strip. Both are scraped so a value cannot hide on the tab the guard
    does not read.
    """
    text = _markdown(at)
    cards = []
    for block in _CARD_RE.findall(text):
        if "kpi-value" not in block:
            continue
        cards.append(
            {
                "label": _field(block, "kicker"),
                "value": _plain_value(_field(block, "kpi-value")),
                "source": _field(block, "kpi-source"),
                "note": _field(block, "kpi-note"),
            }
        )
    for match in _READING_STAT_RE.finditer(text):
        cards.append(
            {
                "label": _plain_value(match["label"]),
                "value": _plain_value(match["value"]),
                "source": "",
                "note": "",
            }
        )
    return cards


def _cards_labelled(at: AppTest, label: str) -> list[dict[str, str]]:
    cards = [card for card in _cards(at) if card["label"] == label]
    assert cards, f"the app renders no KPI card labelled {label!r}"
    return cards


def _tab(at: AppTest, index: int) -> AppTest:
    """One tab, so a check cannot be satisfied by a card rendered on another tab."""
    tabs = at.tabs
    assert len(tabs) > index, f"the app renders {len(tabs)} tabs, expected at least {index + 1}"
    return tabs[index]


def _ordered_elements(at: AppTest) -> list[tuple[str, str]]:
    """Every Markdown/Caption element in render order.

    `at.markdown` and `at.caption` are flat per-widget lists, so they cannot say which caption
    follows which card group; the widget tree can.
    """
    elements: list[tuple[str, str]] = []

    def walk(block: Any) -> None:
        for child in getattr(block, "children", {}).values():
            name = type(child).__name__
            if name in ("Markdown", "Caption"):
                elements.append((name.lower(), str(getattr(child, "value", ""))))
            walk(child)

    walk(at.main)
    return elements


def _card_labels(markup: str) -> list[str]:
    labels = [
        _field(block, "kicker")
        for block in _CARD_RE.findall(markup)
        if "kpi-value" in block
    ]
    labels.extend(_plain_value(match["label"]) for match in _READING_STAT_RE.finditer(markup))
    return labels


def _credit_ids(kind: str, value: str) -> list[str]:
    """The `provenance_id`s a caption or a `<p class="kpi-note">` line credits."""
    if kind == "caption":
        match = _CAPTION_CREDIT_RE.search(value)
        return _split_ids(match) if match else []
    match = _NOTE_CREDIT_RE.search(value)
    if match:
        return _split_ids(match)
    return _table_credit_ids(value)


def _split_ids(match: re.Match[str]) -> list[str]:
    return [part.strip() for part in match["ids"].split(" · ") if part.strip()]


def _table_credit_ids(markup: str) -> list[str]:
    """Credits carried per row by the context table's own `Provenance ID` column."""
    if '<table class="cockpit-table"' not in markup:
        return []
    head = re.search(r"<thead><tr>(.*?)</tr></thead>", markup, re.S)
    body = re.search(r"<tbody>(.*?)</tbody>", markup, re.S)
    if not head or not body:
        return []
    header = [html.unescape(_TAG_RE.sub("", cell)) for cell in _CELL_RE.findall(head.group(1))]
    column = formatters.humanize("provenance_id")
    if column not in header:
        return []
    index = header.index(column)
    ids = []
    for raw in _ROW_RE.findall(body.group(1)):
        cells = [html.unescape(_TAG_RE.sub("", cell)) for cell in _CELL_RE.findall(raw)]
        if index < len(cells):
            ids.append(cells[index])
    return list(dict.fromkeys(ids))


def _card_groups_with_credit(at: AppTest) -> list[tuple[list[str], list[str]]]:
    """`(card labels, credited ids)` for every KPI card group, in render order.

    A group is the run of KPI cards between two provenance lines; the app prints one line per
    group instead of repeating a badge under every card.
    """
    groups: list[tuple[list[str], list[str]]] = []
    labels: list[str] = []
    for kind, value in _ordered_elements(at):
        if kind == "markdown":
            labels.extend(_card_labels(value))
        ids = _credit_ids(kind, value)
        if ids and labels:
            groups.append((labels, ids))
            labels = []
    return groups


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


def _feature_point(geometry: Any) -> tuple[float, float] | None:
    """Representative `(lat, lon)` of a GeoJSON geometry.

    Same documented rule as `src/data/ui_bridge.py::_feature_point`: the point itself, else the
    mean of its vertices. Re-derived here so the proximity columns are checked against the
    artefact, not against the app's own output.
    """
    if not isinstance(geometry, dict):
        return None
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
        return float(coords[1]), float(coords[0])
    pairs: list[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and all(isinstance(value, (int, float)) for value in node[:2])
        ):
            pairs.append((float(node[0]), float(node[1])))
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(coords)
    if not pairs:
        return None
    lon = sum(pair[0] for pair in pairs) / len(pairs)
    lat = sum(pair[1] for pair in pairs) / len(pairs)
    return lat, lon


def _haversine_m(lat0: float, lon0: float, lat: float, lon: float) -> float:
    phi0, phi = math.radians(lat0), math.radians(lat)
    dphi, dlam = phi - phi0, math.radians(lon - lon0)
    hav = math.sin(dphi / 2.0) ** 2 + math.cos(phi0) * math.cos(phi) * math.sin(dlam / 2.0) ** 2
    return 2.0 * 6_371_000.0 * math.asin(math.sqrt(min(1.0, max(0.0, hav))))


def _nearest_cell(
    geometry: Any, grid: pd.DataFrame
) -> tuple[str, str, float] | tuple[None, None, None]:
    """`(cell_id, risk_level, distance_m)` of the nearest published grid cell."""
    point = _feature_point(geometry)
    if point is None:
        return None, None, None
    lat0, lon0 = point
    best: tuple[float, str, str] | None = None
    for cell, risk, lat, lon in zip(
        grid["cell_id"], grid["risk_level"], grid["lat"], grid["lon"]
    ):
        distance = _haversine_m(lat0, lon0, float(lat), float(lon))
        if best is None or distance < best[0]:
            best = (distance, str(cell), str(risk))
    assert best is not None, "the published grid is empty"
    return best[1], best[2], round(best[0], 1)


def _displayed_context_value(value: Any, *, field: str) -> str:
    """One table cell exactly as `src/ui/context_view.py::_render_table` prints it."""
    if field in ("layer", "class"):
        text = formatters.humanize(value) if value not in (None, "") else ""
    elif field == "name":
        text = formatters.translate_feature_name(value)
    elif field == "risk_level":
        text = formatters.risk_label(value) if value not in (None, "") else ""
    else:
        text = "" if value is None else str(value).replace("\n", " ")
    if text in ("", "None", "nan"):
        return "—"
    return text


def _expected_context_rows(
    raw_features: list[dict[str, Any]], grid: pd.DataFrame, header: list[str]
) -> list[dict[str, str]]:
    """What the table must read, from the artefact props and the published grid."""
    field_for_header = {formatters.humanize(field): field for field in _CONTEXT_TABLE_FIELDS}
    unknown = set(header) - set(field_for_header)
    assert not unknown, f"tab 3 renders columns with no artefact field: {sorted(unknown)}"
    rows = []
    for feature in raw_features:
        props = dict(feature.get("properties") or {})
        cell, risk, distance = _nearest_cell(feature.get("geometry"), grid)
        values = {
            "layer": props.get("layer"),
            "class": props.get("class"),
            "name": props.get("name"),
            "source": props.get("source"),
            "provenance_id": props.get("provenance_id"),
            "nearest_cell_id": cell,
            "risk_level": risk,
            "distance_m": distance,
        }
        rows.append(
            {
                head: _displayed_context_value(
                    values[field_for_header[head]], field=field_for_header[head]
                )
                for head in header
            }
        )
    return rows


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


def assert_velocity_cards_match_meta(
    at: AppTest, meta: dict[str, Any], case_id: str
) -> None:
    """Every visible `Median velocity` (tab 1 strip, tab 2 card, tab 4 stat) vs the artefact."""
    published = meta["stats"]["median_velocity_mm_yr"]
    cards = _cards_labelled(at, _VELOCITY_LABEL)
    assert len(cards) >= 2, (
        f"{case_id}: Median velocity must appear at least on the tab-2 card and one other "
        f"visible source; found {len(cards)}"
    )
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
        f"{case_id}: the seasonal amplitude must appear under more than one visible source "
        f"(tab 2 card + tab 4 stat); found {len(cards)}"
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
    # The bridge ships `coherence >= <floor>` and `kpi_cards.render_kpi_cards` runs it through
    # `formatters.format_note`, so the expected sentence is the formatter's output, not a copy.
    expected_note = formatters.format_note(f"coherence >= {floor:g}")
    assert expected_note in coverage_card["note"], (
        f"{case_id}: the coverage note {coverage_card['note']!r} does not quote the published "
        f"floor {floor} as {expected_note!r}"
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
    """Every KPI card group credits the `provenance_id` its own `meta.json` publishes.

    The merged cockpit prints one `Provenance ID …` line per group (map KPIs, grid KPIs, context
    table, tab-4 stat cards) instead of repeating a badge under every card, so the check follows
    the render order and pairs each group with the credit line that closes it.
    """
    published = meta["provenance"]["provenance_id"]
    groups = _card_groups_with_credit(at)
    assert groups, f"{case_id}: no KPI card group is followed by a provenance line at all"
    stale = {tuple(labels): ids for labels, ids in groups if ids != [published]}
    assert not stale, (
        f"{case_id}: card groups credit {stale} but meta.json publishes {published}"
    )
    # Anti-vacuity: dropping the badge from every card must not turn this into a no-op.
    credited = {label for labels, _ids in groups for label in labels}
    assert credited >= _bridge_kpi_labels(case_id), (
        f"{case_id}: only {sorted(credited)} are credited by a provenance line, but the bridge "
        f"publishes {sorted(_bridge_kpi_labels(case_id))}"
    )


def _stat_value(stats: dict[str, Any], key: str) -> Any:
    """The published stat behind a card key, resolving the nested `per_cell_series.enabled`."""
    if "." in key:
        outer, inner = key.split(".", 1)
        series = stats.get(outer)
        return series.get(inner) if isinstance(series, dict) else None
    return stats.get(key)


def _case_stat_cards(at: AppTest, stats: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Tab-4 stat cards keyed by the published stat they render.

    `src/ui/educational.py::_stat_card` labels every card with `formatters.humanize(key)`, so the
    key is recovered with the same documented formatter rather than a copied label literal.
    """
    key_for_label = {formatters.humanize(key): key for key in stats}
    if isinstance(stats.get("per_cell_series"), dict) and "enabled" in stats["per_cell_series"]:
        key_for_label[formatters.humanize("per_cell_series.enabled")] = "per_cell_series.enabled"
    cards: dict[str, dict[str, str]] = {}
    for card in _cards(at):
        key = key_for_label.get(card["label"])
        if key is not None:
            cards[key] = card
    return cards


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
    cards = _case_stat_cards(_tab(at, 3), stats)
    assert {"median_ratio_R", "n_cells"} <= set(cards), (
        f"{case_id}: tab 4 no longer prints the published stats as cards; it rendered "
        f"{sorted(cards)}"
    )
    for label, card in cards.items():
        artefact = _stat_value(stats, label)
        if isinstance(artefact, bool):
            # `educational._shown` prints a boolean as a word, not as `True`/`False`.
            expected_word = "emitted" if artefact else "not emitted"
            assert card["value"] == expected_word, (
                f"{case_id}: the {label} card shows {card['value']!r} but meta.json publishes "
                f"{artefact!r} ({expected_word!r})"
            )
        elif isinstance(artefact, str):
            # `educational._case_block` prints `formatters.humanize` of the published value.
            expected = formatters.humanize(artefact)
            assert card["value"] == expected, (
                f"{case_id}: the {label} card shows {card['value']!r} but meta.json publishes "
                f"{artefact!r}, which displays as {expected!r}"
            )
        elif artefact is None:
            assert card["value"] == formatters.NOT_DECLARED, (
                f"{case_id}: the {label} card shows {card['value']!r} but meta.json publishes "
                "no value"
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
        if artefact in (None, ""):
            # No published value, so no unit is printed either.
            continue
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
    """The mission bar prints the acquisition span once; every occurrence must match meta.dates.

    The redesign's one-visible-source-per-datum rule retired the duplicate span in the map
    footer, so the count is no longer asserted (a layout choice). What is asserted is that each
    span the reader can see carries the artefact's own first/last date and `n_dates`.
    """
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    dates = [str(item) for item in meta["dates"]]
    occurrences = _span_occurrences(at)
    assert occurrences, (
        f"{case_id}: the acquisition span is not rendered anywhere; the mission bar lost it"
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
    # `map_view._legend_details` prints `<shown> of <total> cells` in the map footer.
    totals = [
        (int(match["shown"]), int(match["total"]))
        for match in _CELL_COUNT_RE.finditer(html.unescape(_markdown(at)))
    ]
    assert len(totals) == 1, f"{case_id}: expected one cell-count line, found {totals}"
    shown, total = totals[0]
    assert total == meta["stats"]["n_cells"] == len(rendered["grid"]), (
        f"{case_id}: the app totals {total} cells, meta.stats.n_cells is "
        f"{meta['stats']['n_cells']} and the parquet has {len(rendered['grid'])}"
    )
    assert shown == total, (
        f"{case_id}: the default filter hides cells ({shown} of {total}), so the KPI cards "
        "would describe a subset the artefacts never publish"
    )


def test_cross_case_bar_peak_matches_the_grid(rendered: dict[str, Any]) -> None:
    """The tab-1 comparison strip prints one peak per published case, from that case's grid.

    The redesign moved provenance out of the strip (its own note says the ids live in the
    "Provenance and limits" tab), so this no longer claims the strip credits an id. It checks
    the datum the strip still carries: every row's peak is the `|velocity|` maximum of that
    case's own parquet, in that parquet's own unit — not a copied constant, not a shared figure.
    """
    case_id, at = rendered["case"], rendered["at"]
    rows = dict(_compare_rows(at))
    assert rows, f"{case_id}: the comparison strip renders no rows"
    # The strip lists cases by their published display name, not by slug.
    labels = {str(case["id"]): str(case["label"]) for case in loaders.load_study_cases()}
    assert set(rows) == set(labels.values()), (
        f"{case_id}: the strip lists {sorted(rows)} but the published cases are "
        f"{sorted(labels.values())}"
    )
    for slug, label in labels.items():
        grid = (
            rendered["grid"]
            if slug == case_id
            else pd.read_parquet(PROCESSED / slug / "nisar_grid.parquet")
        )
        peak = float(grid["velocity_mm_yr"].abs().max())
        unit = str(grid["velocity_unit"].iloc[0])
        _assert_shows(
            rows[label], peak, decimals=_MM_DECIMALS, what=f"{case_id}: {label} peak"
        )
        assert rows[label].endswith(f" {unit}"), (
            f"{case_id}: the {label} row reads {rows[label]!r}, not the grid's unit {unit!r}"
        )


# --------------------------------------------------------------------------------------
# 4. detection limit: shown only where the artefact declares one
# --------------------------------------------------------------------------------------
def test_detection_limit_caption_follows_the_artefact(rendered: dict[str, Any]) -> None:
    """The detection limit appears iff the artefact declares one, and equals it.

    The redesign moved the number out of the map caption (which now only says "above the
    detection limit") into the tab-4 stat card labelled with `formatters.humanize` of the
    published key. The card is the visible source, so it is checked against
    `meta.stats.detection_limit_mm_yr`; a case that declares no limit must show no such card.
    """
    case_id, meta, at = rendered["case"], rendered["meta"], rendered["at"]
    label = formatters.humanize("detection_limit_mm_yr")
    cards = [card for card in _cards(_tab(at, 3)) if card["label"] == label]
    # The unit follows from the published key; a `_mm_yr` statistic is a rate.
    limit = meta["stats"].get("detection_limit_mm_yr")
    if limit is None:
        assert not cards, (
            f"{case_id}: the app shows a {label!r} card {[card['value'] for card in cards]} "
            "but meta.stats has no detection_limit_mm_yr — that number has no artefact"
        )
        return
    assert cards, f"{case_id}: meta.stats publishes a detection limit the app never shows"
    expected = formatters.format_number(limit)
    for card in cards:
        match = _NUMBER_RE.match(card["value"])
        assert match and match["num"] == expected, (
            f"{case_id}: the {label} card shows {card['value']!r} but the artefact publishes "
            f"{limit!r}, which `format_number` displays as {expected!r}"
        )
        assert match["unit"] == C.VELOCITY_UNIT, (
            f"{case_id}: the card prints the detection limit in {match['unit']!r}, but "
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

    # Keys `load_meta` injects for display. They are exactly the additive telemetry set its
    # docstring declares, and every one of them must carry the value meta.json publishes under
    # `provenance` — the panel is allowed to surface those fields, not to invent any.
    loaded = loaders.load_meta(case_id)
    injected = set(loaded) - set(meta)
    assert injected <= _ADDITIVE_META_KEYS, (
        f"{case_id}: tab 4 invents keys {sorted(injected - _ADDITIVE_META_KEYS)}"
    )
    provenance = meta.get("provenance") or {}
    for key in injected - {"unit", "provenance_fields"}:
        assert loaded[key] == provenance.get(key), (
            f"{case_id}: tab 4 shows provenance.{key} as {loaded[key]!r} but meta.json "
            f"publishes {provenance.get(key)!r}"
        )
    if "unit" in injected:
        assert loaded["unit"] == C.UNIT
    if "provenance_fields" in injected:
        fields = loaded["provenance_fields"]
        assert set(fields) == _PROVENANCE_FIELD_KEYS, (
            f"{case_id}: provenance_fields carries {sorted(fields)}, expected "
            f"{sorted(_PROVENANCE_FIELD_KEYS)}"
        )
        assert fields["provenance_id"] == provenance.get("provenance_id") == (
            loaded.get("provenance_id")
        )
        for key in _PROVENANCE_FIELD_KEYS - {"provenance_id"}:
            assert fields[key] == provenance.get(key), (
                f"{case_id}: provenance_fields[{key}] is {fields[key]!r} but meta.json publishes "
                f"{provenance.get(key)!r}"
            )

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
    # `context_view.render_hazard_tab` prints `formatters.humanize(layer)` next to the count.
    breakdown = ", ".join(
        f"{formatters.humanize(layer)} {count}" for layer, count in sorted(counts.items())
    )

    tab = _tab(at, 2)
    (delivered,) = _cards_labelled(tab, "Delivered features")
    _assert_shows(
        delivered["value"], len(features), decimals=0, what=f"{case_id}: Delivered features"
    )
    # Two artefacts, one number: the GeoJSON must also agree with the count meta.json publishes.
    assert len(features) == meta["stats"]["n_context_features"], (
        f"{case_id}: context_layers.geojson has {len(features)} features but "
        f"meta.stats.n_context_features is {meta['stats']['n_context_features']}"
    )
    (layers_card,) = _cards_labelled(tab, "Delivered layers")
    _assert_shows(
        layers_card["value"], len(counts), decimals=0, what=f"{case_id}: Delivered layers"
    )
    (breakdown_card,) = _cards_labelled(tab, "Layer counts")
    assert breakdown_card["value"] == breakdown, (
        f"{case_id}: the layer breakdown reads {breakdown_card['value']!r} but "
        f"context_layers.geojson yields {breakdown!r}"
    )

    summary = _CONTEXT_SUMMARY_RE.search(_markdown(tab))
    assert summary, f"{case_id}: tab 3 no longer prints the feature summary line"
    assert int(summary["shown"]) == int(summary["total"]) == len(features), (
        f"{case_id}: tab 3 claims {summary['shown']} of {summary['total']} features but the "
        f"artefact has {len(features)}"
    )

    header, rows = _context_table(tab)
    expected_rows = _expected_context_rows(
        rendered["context"]["features"], rendered["grid"], header
    )
    assert len(rows) == len(expected_rows), (
        f"{case_id}: tab 3 renders {len(rows)} rows but the artefact has {len(expected_rows)} "
        "features"
    )
    assert rows == expected_rows, (
        f"{case_id}: the context table does not echo the artefact; first difference: "
        f"{next((pair for pair in zip(rows, expected_rows) if pair[0] != pair[1]), None)}"
    )
    # Selector-following: every credit is this case's, not the primary case's fallback. The
    # merged table prints one `Provenance ID …` note above it and drops the per-row column when
    # every feature shares the id; either shape is accepted, but the id set must be exact.
    published = meta["provenance"]["provenance_id"]
    credit_header = formatters.humanize("provenance_id")
    if credit_header in header:
        credited = {row[credit_header] for row in rows if row.get(credit_header)}
    else:
        table_markup = next(
            (
                value
                for value in (str(node.value) for node in tab.markdown)
                if '<table class="cockpit-table"' in value
            ),
            "",
        )
        assert table_markup, f"{case_id}: tab 3 no longer renders the context table"
        credited = set(_credit_ids("markdown", table_markup))
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
    """Copy the published cases into `root` (never the repo) and corrupt `root/<case>`.

    Every case is copied because the merged cockpit renders a three-case chart on tab 2; a root
    holding only the corrupted case would fail to render for a reason unrelated to the mutation.
    """
    for source in sorted(PROCESSED.iterdir()):
        if source.is_dir():
            shutil.copytree(source, root / source.name)
    target = root / case_id
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
