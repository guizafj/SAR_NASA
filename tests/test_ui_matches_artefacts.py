"""Cross-artefact guard: what a judge READS on screen must equal what `data/processed/**` publishes.

`tests/test_ui_smoke.py` pins the tab labels and the demo banner; `tests/test_ui_bridge.py` pins the
18 hook signatures and a handful of their return values. Neither reads the DOM the app actually
produced, so a KPI card carrying a hardcoded, stale or invented figure passes the whole suite. This
file renders `app.py` with Streamlit's in-process `AppTest` and compares every *displayed* number
with the artefact behind it, for the three frozen cases.

Three layers are checked per case:

1. The formatted KPI cards (tab 1 and tab 2) against `meta.json::stats`, within the app's own
   display rounding. The cards are *computed from the grid parquet* by `get_series_kpis` /
   `get_map_kpis` (`src/data/ui_bridge.py`), so this is genuinely a cross-artefact parity check:
   the number on the card must agree with the number the case published.
2. The panels that echo the artefact verbatim (tab 4 renders `meta.json`; tab 3 prints the
   context summary) against the file they claim to show.
3. The acquisition span `(62)` in the mission bar and the map footer against `meta.dates` /
   `meta.stats.n_dates`.

Assertions never use a literal copied from the app: every expected value is read from
`data/processed/<case>/meta.json`, `nisar_grid.parquet` or `context_layers.geojson`, or from the
frozen contract constant (`src.domain.contracts`) the loader formats with.

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

* `src/ui/formatters.py` has **no** `DEMO_BANNER` constant: the banner literal is duplicated in
  `app.py:116` and `src/ui/charts.py:76`. `_expected_banner()` prefers that constant as soon as it
  exists; until then it falls back to the banner text the UI spec declares
  (`docs/ui-data-needs.md`, "Aviso **…** si `product == \"mock\"`") and asserts both rendered
  banners match it and each other.
* `stats.median_ratio_R` and `stats.annual_coverage` are never formatted into a card; the only
  element that carries them is the whole-`meta.json` JSON panel in tab 4. A stale ratio would still
  be caught there, but no card-level provenance exists.
* `src/ui/context_view.py:9` drops the selected case (`del filters` plus `loader()` with no
  `case_id`), so tab 3 always renders the *primary* case's `context_layers.geojson`. Encoded as a
  strict `xfail` — it turns red when the panel starts following the selector.
"""

from __future__ import annotations

import contextlib
import functools
import html
import inspect
import json
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
UI_SPEC = ROOT / "docs" / "ui-data-needs.md"

#: Display contract enforced on the cards. The loader rounds millimetre statistics to 2 decimals
#: and percentages to 1 (`src/data/ui_bridge.py`), so a card may show at most that many digits and
#: must agree with the published figure to within half of the last displayed digit.
_MM_DECIMALS = 2
_PCT_DECIMALS = 1

_CARD_RE = re.compile(r'<div class="cockpit-card">(.*?)</div>', re.S)
_BANNER_RE = re.compile(r'<div class="demo-banner" role="status">(.*?)</div>', re.S)
_NUMBER_RE = re.compile(r"^(?P<num>[+-]?\d+(?:\.\d+)?)(?:\s+(?P<unit>.*))?$")
_SPAN_RE = re.compile(r"dates (?P<first>\d{4}-\d{2}-\d{2}) … (?P<last>\d{4}-\d{2}-\d{2}) \((?P<n>\d+)\)")
_CAPTION_TOTAL_RE = re.compile(r"^(?P<shown>\d+) celdas mostradas de (?P<total>\d+)$")
_CONTEXT_CAPTION = "Capas entregadas: "

CRITICAL_BAND = "critico"  # contracts.RISK_LEVELS critical member (frozen enum)


# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_loader_cache() -> Iterator[None]:
    loaders.clear_loader_cache()
    yield
    loaders.clear_loader_cache()


#: The case `load_context_layers(case_id=None)` falls back to (tab 3 ignores the selector).
_PRIMARY = loaders.load_study_cases_config(loaders.DEFAULT_STUDY_CASES_PATH).primary


@pytest.fixture(scope="module", params=C.CASE_IDS, ids=list(C.CASE_IDS))
def rendered(request: pytest.FixtureRequest) -> dict[str, Any]:
    """One in-process render per frozen case, shared by the read-only assertions below."""
    case_id = str(request.param)
    meta = json.loads((PROCESSED / case_id / "meta.json").read_text(encoding="utf-8"))
    grid = pd.read_parquet(PROCESSED / case_id / "nisar_grid.parquet")
    return {"case": case_id, "meta": meta, "grid": grid, "at": _run_app(case_id)}


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
    """Every rendered KPI card, in DOM order (tab 1 then tab 2)."""
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


def _json_payloads(at: AppTest) -> list[dict[str, Any]]:
    payloads = []
    for node in at.json:
        value = node.value
        payloads.append(value if isinstance(value, dict) else json.loads(value))
    return payloads


def _meta_panel(at: AppTest) -> dict[str, Any]:
    """The tab-4 `st.json(meta.json)` panel (the context summary has no `stats`/`dates`)."""
    for payload in _json_payloads(at):
        if isinstance(payload, dict) and "stats" in payload and "dates" in payload:
            return payload
    raise AssertionError("tab 4 no longer renders meta.json, so no panel can be cross-checked")


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
    """Tab 1 + tab 2 `velocidad mediana` vs `meta.stats.median_velocity_mm_yr`."""
    published = meta["stats"]["median_velocity_mm_yr"]
    cards = _cards_labelled(at, "velocidad mediana")
    assert len(cards) >= 2, f"{case_id}: velocidad mediana must appear on tab 1 and tab 2"
    for card in cards:
        _assert_shows(
            card["value"],
            published,
            decimals=_MM_DECIMALS,
            what=f"{case_id}: velocidad mediana card",
        )


def assert_seasonal_cards_match_meta(at: AppTest, meta: dict[str, Any], case_id: str) -> None:
    """`amplitud estacional mediana` card, its unit, and the regime the note prints."""
    stats = meta["stats"]
    cards = _cards_labelled(at, "amplitud estacional mediana")
    assert len(cards) >= 2, f"{case_id}: the seasonal amplitude must appear on tab 1 and tab 2"
    for card in cards:
        _assert_shows(
            card["value"],
            stats["median_seasonal_amp_mm"],
            decimals=_MM_DECIMALS,
            what=f"{case_id}: amplitud estacional mediana card",
        )
    notes = [card["note"] for card in cards if card["note"]]
    assert notes, f"{case_id}: the seasonal card lost its provenance note"
    assert any(
        f"régimen dominante {stats['dominant_regime']}" in note for note in notes
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
    (coverage_card,) = _cards_labelled(at, "cobertura coherente")
    _assert_shows(
        coverage_card["value"],
        coverage,
        decimals=_PCT_DECIMALS,
        what=f"{case_id}: cobertura coherente card",
    )
    assert f"coherence >= {floor:g}" in coverage_card["note"], (
        f"{case_id}: the coverage note does not quote the published floor {floor}"
    )

    velocities = grid["velocity_mm_yr"].to_numpy(dtype=float)
    above = round(float((abs(velocities) >= C.NULL_RESULT_MM_YR).mean() * 100.0), _PCT_DECIMALS)
    (above_card,) = _cards_labelled(at, "% de celdas por encima del umbral")
    _assert_shows(
        above_card["value"],
        above,
        decimals=_PCT_DECIMALS,
        what=f"{case_id}: '% de celdas por encima del umbral' card",
    )
    assert f"umbral {C.NULL_RESULT_MM_YR:g} mm/year" in above_card["note"], (
        f"{case_id}: the card does not name the frozen null-result threshold "
        f"{C.NULL_RESULT_MM_YR} mm/year it counted against"
    )

    critical = int((grid["risk_level"].astype(str) == CRITICAL_BAND).sum())
    (critical_card,) = _cards_labelled(at, "celdas críticas")
    _assert_shows(
        critical_card["value"],
        critical,
        decimals=0,
        what=f"{case_id}: celdas críticas card",
    )


def assert_provenance_badge_is_the_artefact(at: AppTest, meta: dict[str, Any], case_id: str) -> None:
    """Every card credits the `provenance_id` its own `meta.json` publishes."""
    published = meta["provenance"]["provenance_id"]
    cards = _cards(at)
    assert cards
    stale = {card["label"]: card["source"] for card in cards if card["source"] != published}
    assert not stale, f"{case_id}: cards credit {stale} but meta.json publishes {published}"


# --------------------------------------------------------------------------------------
# 1 + 2. displayed statistics vs meta.json
# --------------------------------------------------------------------------------------
def test_median_velocity_cards_match_published_stat(rendered: dict[str, Any]) -> None:
    assert_velocity_cards_match_meta(rendered["at"], rendered["meta"], rendered["case"])


def test_seasonal_amplitude_and_regime_match_published_stats(rendered: dict[str, Any]) -> None:
    assert_seasonal_cards_match_meta(rendered["at"], rendered["meta"], rendered["case"])


def test_kpi_units_come_from_the_contract(rendered: dict[str, Any]) -> None:
    at = rendered["at"]
    for card in _cards_labelled(at, "velocidad mediana"):
        assert card["value"].endswith(f" {C.VELOCITY_UNIT}"), (
            f"{rendered['case']}: velocity card {card['value']!r} is not in {C.VELOCITY_UNIT}"
        )
    for card in _cards_labelled(at, "amplitud estacional mediana"):
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
    rows = re.findall(
        r'<div class="compare-row"><span>(?P<slug>[^<]*)</span>.*?<span>(?P<tail>[^<]*)</span></div>',
        _markdown(at),
        re.S,
    )
    assert rows, f"{case_id}: the comparison strip renders no rows"
    mine = [tail for slug, tail in rows if slug == case_id]
    assert len(mine) == 1, f"{case_id}: the comparison strip does not list this case once"
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
    limit = meta["stats"].get("detection_limit_mm_yr")
    text = html.unescape(_markdown(at))
    mentions = re.findall(r"límite de detección de ([^<]*)", text)
    if limit is None:
        assert not mentions, (
            f"{case_id}: the app shows a detection limit {mentions} but meta.stats has no "
            "detection_limit_mm_yr — that number has no artefact behind it"
        )
        return
    assert mentions, f"{case_id}: meta.stats publishes a detection limit the app never shows"
    assert mentions == [f"{limit:g} mm/year"], (
        f"{case_id}: the app prints {mentions} but meta.stats.detection_limit_mm_yr is {limit!r}"
    )


# --------------------------------------------------------------------------------------
# 5. the demo banner equals the declared banner text
# --------------------------------------------------------------------------------------
def _expected_banner() -> str:
    """The banner the code is supposed to render.

    Preferred source is `src.ui.formatters.DEMO_BANNER` (the constant `app.py` and
    `src/ui/charts.py` should share). It does not exist on this branch, so the fallback is the
    banner string the UI spec declares in `docs/ui-data-needs.md`; both render sites are then
    pinned to that spec, so translating the banner without translating the spec fails.
    """
    constant = getattr(formatters, "DEMO_BANNER", None)
    if isinstance(constant, str) and constant.strip():
        return constant
    spec = UI_SPEC.read_text(encoding="utf-8")
    match = re.search(r"Aviso \*\*(?P<banner>.+?)\*\*", spec)
    assert match, "docs/ui-data-needs.md no longer declares a demo banner"
    return match.group("banner").strip()


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
    stale = {key: (value, panel.get(key)) for key, value in meta.items() if panel.get(key) != value}
    assert not stale, f"{case_id}: tab 4 shows {stale} but meta.json says otherwise"

    # Keys the loader injects for display; both come from the contract, not from thin air.
    injected = set(panel) - set(meta)
    assert injected <= {"unit", "provenance_id"}, f"{case_id}: tab 4 invents keys {sorted(injected)}"
    if "unit" in injected:
        assert panel["unit"] == C.UNIT

    # The v1.1 statistics that no card formats still reach the judge through this panel.
    for key in (
        "median_velocity_mm_yr",
        "median_seasonal_amp_mm",
        "median_ratio_R",
        "dominant_regime",
        "annual_coverage",
        "n_dates",
        "n_cells",
        "detection_limit_mm_yr",
    ):
        if key in meta["stats"]:
            assert panel["stats"][key] == meta["stats"][key], (
                f"{case_id}: tab 4 shows stats.{key} = {panel['stats'][key]!r} but meta.json "
                f"publishes {meta['stats'][key]!r}"
            )


def test_context_panel_matches_the_artefact_it_names(rendered: dict[str, Any]) -> None:
    """Tab 3 prints a path; the JSON beside it must be that same file's summary."""
    caption = next(
        (str(node.value) for node in rendered["at"].caption if str(node.value).startswith(_CONTEXT_CAPTION)),
        None,
    )
    assert caption, f"{rendered['case']}: tab 3 shows no context caption"
    named = Path(caption[len(_CONTEXT_CAPTION) :].strip())
    geojson_path = named if named.is_absolute() else ROOT / named
    assert geojson_path.is_file(), f"tab 3 names {named}, which does not exist"
    features = json.loads(geojson_path.read_text(encoding="utf-8"))["features"]
    summary = next(
        payload
        for payload in _json_payloads(rendered["at"])
        if isinstance(payload, dict) and "n_features" in payload
    )
    assert summary["n_features"] == len(features), (
        f"tab 3 claims {summary['n_features']} features but {named} has {len(features)}"
    )
    layers: dict[str, int] = {}
    for feature in features:
        layer = str((feature.get("properties") or {}).get("layer", "unknown"))
        layers[layer] = layers.get(layer, 0) + 1
    assert summary["layers"] == layers
    assert summary["case_id"] == geojson_path.parent.name


@pytest.mark.xfail(
    strict=True,
    reason=(
        "src/ui/context_view.py:9 drops the selected case (`del filters`, then `loader()` with no "
        "case_id`), so tab 3 always renders the primary case's context_layers.geojson"
    ),
)
@pytest.mark.parametrize(
    "case_id", [slug for slug in C.CASE_IDS if slug != _PRIMARY], ids=lambda slug: slug
)
def test_hazard_tab_shows_the_selected_cases_context(case_id: str) -> None:
    """FINDING, not a pass: tab 3 ignores the selector.

    `src/ui/context_view.py:9` calls `loader()` with no `case_id` after `del filters`, so the
    loader falls back to the primary case per `list_available_cases` ordering
    (`src/data/ui_bridge.py::load_context_layers`). Strict xfail: it fails loudly once the panel
    follows the case selector, so the marker has to be removed deliberately.
    """
    at = _run_app(case_id)
    caption = next(
        str(node.value) for node in at.caption if str(node.value).startswith(_CONTEXT_CAPTION)
    )
    named = caption[len(_CONTEXT_CAPTION) :].strip()
    assert Path(named).parent.name == case_id, (
        f"tab 3 shows {named} while {case_id} is selected"
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
        shown = [card["value"] for card in _cards_labelled(at, "velocidad mediana")]
        mutated_meta = json.loads((root / case_id / "meta.json").read_text(encoding="utf-8"))
        mutated_grid = pd.read_parquet(root / case_id / "nisar_grid.parquet")
        # The card still renders the grid's own number, not the corrupted stat.
        assert shown and shown[0].split()[0] != f"{corrupted:g}"
        with pytest.raises(AssertionError, match="velocidad mediana"):
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
        shown = [card["value"] for card in _cards_labelled(at, "velocidad mediana")]
        assert shown and abs(float(shown[0].split()[0]) - published) > 0.005, (
            "the corrupted grid did not move the card, so this fixture proves nothing"
        )
        with pytest.raises(AssertionError, match="velocidad mediana"):
            assert_velocity_cards_match_meta(at, mutated_meta, case_id)
        # The card agrees with the corrupted parquet, so the guard that reads the grid directly
        # stays green: the failure is the meta/parquet disagreement, not a broken render.
        assert_grid_derived_cards_match_grid(
            at, mutated_meta, pd.read_parquet(root / case_id / "nisar_grid.parquet"), case_id
        )
