# Submission video script — Three Cities, One Pump

**Runtime** 20 s problem · 90 s demo · 30 s architecture and impact = 2:20. One presenter, read aloud.
**Numbers** every spoken figure is copied from `./scripts/python.sh scripts/demo_numbers.py` (run on `data/processed/`, 2026-11-14); the last column names the card line. Re-run the card before recording: if a number moves, the script moves. **Coverage is always the AOI figure** (`coverage_aoi`, the denominator of the 50 % bar in `docs/first-granule-runbook.md` §4 and the one `pipeline/process_case.py` warns on); the card also prints `coverage_bbox`, the AOI's envelope, which is never the threshold denominator and is never spoken as coverage.

**Written against the redesigned cockpit** (`main`; the design phase table is in `docs/requests.md`). Every stage direction below was checked against `app.py` and `src/ui/*` at that commit; where a fact can only be settled by running the app it is marked **[needs a live run]**. The previous revision described the cockpit before the redesign and told the presenter to drag a slider that no longer exists.

## Where the cockpit puts things now (read this before the take)

| Tab strip | Label | What is actually on it | Honesty beat it carries |
|---|---|---|---|
| first tab | `Map` | the deck on the left, the reading panel on the right (the map is twice as wide); the velocity legend row sits **under the deck**, before `Export filtered cells (CSV)`; panel order: `H3 cell` → `Whole area` or the cell id → **the series, as the hero** → a strip with velocity, coherence, ratio R → `Deformation` / `Quality` / `Provenance` groups. Under the same row — and only for a case whose velocity sits below its declared detection limit, i.e. Málaga — the null-result note. At the bottom: `Case comparison and context` (collapsed) | `DEMONSTRATION DATA` banner above everything; `Case comparison and context` → `Scale and provenance` |
| second tab | `Series and KPIs` | KPI cards, the case series with the dashed `Seasonal fit`, `Three cities, shared axis`, land-use bars, with ±detection-limit reference lines only for Málaga (the case whose `stats` declares a limit) | the seasonal term shown as a fit |
| third tab | `Hazard and infrastructure` | the delivered context features, per-layer counts and the layer glossary | `Delivered context only, not a hazard assessment` |
| fourth tab | `Provenance and limits` | the `Case` stat cards and their `Provenance ID`, the `Real NISAR GUNW availability` card, `Known caveat`, `Known limitations`, `Full meta.json record` | the limitations, and the in-app `not GNSS-validated` |

Tab labels are constants in `app.py` (`TAB_MAP`, `TAB_SERIES`, `TAB_HAZARD`, `TAB_PHYSICS`) and each is prefixed with its position number on the strip. Everything the old script pointed at has moved:

- **No date slider.** The map is coloured by velocity; the time axis lives in the series. `val_t1`/`val_t2` are the only epochs carried on a grid cell (`docs/demo-fallback.md` §4). Nothing on screen animates.
- **The series now leads the panel.** Clicking a cell renders the hero series *first*, then the figures, then the grouped read-out. The old order (numbers, then series) is inverted.
- **The scale control is an unlabelled radio above the map**, options `Per case` and `Shared across cases`. There is no "shared legend" toggle. The full legend text, the saturation note, the cell counts and the provenance line moved into `Case comparison and context`, which is **collapsed**.
- **No MOCK badge, no provenance footer.** The banner reads `DEMONSTRATION DATA` (`DEMO_BANNER`, `role="status"`); the provenance line lives in `Case comparison and context` → `Scale and provenance` and in the fourth tab.
- **`Filters` is an expander**, open on desktop, collapsed only in compact mode. The `Case` selectbox inside it is how you change city — a single deck at a time, no side-by-side panels.

## 1 · Problem — 0:00–0:20

| Time | On screen / shot list (artefact) | Say aloud (≤ 25 words) | Number → source in the card |
|---|---|---|---|
| 0:00–0:05 | `docs/figures/velocity_shared_scale.png`, three panels, slow push-in on the legend | Three cities. One question. Why does the ground fall in one place and barely move in another? | no number |
| 0:05–0:10 | Same figure, cut to Mexico City's bowl | Because subsidence is set by interbed compressibility — how much the clay itself squeezes — not by how much water is pumped. | no number |
| 0:10–0:15 | Three-panel split: clay bowl / delta edge / karst, labelled from README §1 | Mexico City sits on lacustrine clay. Jakarta, deltaic clay. Málaga, karst. Same pump, different ground. | no number |
| 0:15–0:20 | Málaga panel, deliberately almost blank next to Mexico City | One of them never leaves the detection floor. That blankness is the finding, not missing data. | no number |

## 2 · Demo — 0:20–1:50

The app is not a still: the `DEMONSTRATION DATA` banner sits above the tab strip for every second of this section, and all three cases carry `product=mock`. The old "provenance footer" is gone — the same sentence (`provenance.source = mock (synthetic stub, pipeline/generate_mock.py)`) is now inside `Case comparison and context` → `Scale and provenance` on the first tab, and inside `Full meta.json record` on the fourth.

| Time | On screen / shot list (artefact) | Say aloud (≤ 25 words) | Number → source in the card |
|---|---|---|---|
| 0:20–0:30 | App header: title bar, its telemetry line (`Product mock`, `Unit mm`, the acquisition span), then the `DEMONSTRATION DATA` banner; the `Filters` expander just below is open on desktop | This demo runs on mock artefacts. The banner says DEMONSTRATION DATA; every number is synthetic. Validate the map and the schema, never the science. | mock for 3/3 cases — `!! product == 'mock'` |
| 0:30–0:44 | First tab (`Map`). `Filters` → `Case` sits on `Mexico City, MX` already. Left: the deck. Right, top to bottom: the legend row, `Whole area`, the AOI aggregate series, then the figure strip (`nisar_grid.parquet`, `meta.json`). **The cell count is not on this tab** — it is the `Cells` card on the fourth tab, or the printed count inside `Case comparison and context` | Open on Mexico City: 11,600 cells, median minus 64.7 millimetres a year. 62 dates over 732 days, on a 12-day revisit. | `cells=11,600`, `median=-64.68`, `(732 d, 62 dates) revisit_days=12` |
| 0:44–1:00 | Stay on the first tab: the hero series in the right column, whole-area aggregate, all acquisitions on the x-axis. **Do not reach for a date slider** — the deck carries velocity, the series carries time | The map is a velocity per cell; the series carries time. Zero starts the record; it is not proof the ground is still. | `window=2024-11-10..2026-11-12`; `val_t1 = 0` by construction (handoff §3) |
| 1:00–1:14 | Click a hexagon on the deck, or use the `H3 cell` selectbox under the legend (`Clear selection` undoes it). The panel swaps `Whole area` for the cell id plus a risk chip and reads, in order: the series, the velocity/coherence/ratio-R strip, then `Deformation`, `Quality`, `Provenance` — land use and risk sit in the later groups, not beside velocity (`nisar_grid.parquet`, `timeseries.csv`) | Click a cell: velocity, coherence, ratio R, then regime, land use and risk underneath. One cell is the map unit. | `posting 80 m`, `~201 m across` — `DO NOT CLAIM` |
| 1:14–1:30 | The unlabelled `Scale` radio above the map: `Per case` → `Shared across cases`. The legend row now reads a symmetric 425 mm/yr either side of zero, for every city. The cross-case bars are not on the panel — they are inside `Case comparison and context` (collapsed) | Switch to one shared scale, plus or minus 425 millimetres a year. Mexico City's spread is 50 times Málaga's, so Málaga goes nearly blank. | `shared legend = ±425 mm/yr`; `208.62 / 4.17 = 50.09x` — `CONTRAST` |
| 1:30–1:40 | `Filters` → `Case` → `Málaga, ES`. A single deck, not a panel: the hero series redraws flat over all acquisitions, and a full-width note sits **under** the map row — `No detectable signal above the detection limit` — with the bound itself, 1.5 mm/yr, in the same sentence (`detection_caption`). For the cross-city cut use the second tab (`Series and KPIs` → `Three cities, shared axis`); `docs/figures/timeseries_three_cities.png` is the same picture | Blank is not missing data. Málaga's median is minus 1.3 millimetres a year, below its 1.5 detection bound. The bound is the result. | `detection_limit=1.50`, `median=-1.31` — `NULL RESULT malaga` |
| 1:40–1:50 | Re-select the Mexico City cell: the `Provenance` group at the foot of the reading panel — coordinates · land use, product · sensor · case, and `Provenance ID`. The caption under the hero restates the unit and the same ID. There is no separate evidence card; this group is it (`meta.json`) | Open the evidence read-out: coordinates, unit, coherence, provenance ID. Source, method and ID make the number re-derivable. | `provenance_id=mexico-city+mock+20261114+14fc3e1f` |

## 3 · Architecture and impact — 1:50–2:20

| Time | On screen / shot list (artefact) | Say aloud (≤ 25 words) | Number → source |
|---|---|---|---|
| 1:50–1:56 | README §3 two-layer diagram: offline pipeline → four frozen artefacts → Streamlit | Offline pipeline, frozen contract, Streamlit app. The app reads four artefacts and never touches raw radar. | four artefacts — README §4 |
| 1:56–2:02 | Fourth tab (`Provenance and limits`): the `Case` cards and their `Provenance ID`, then `Known caveat`, then `Full meta.json record` — open it for `provenance.source` and `revisit_days`. The same line on the first tab is `Case comparison and context` → `Scale and provenance` | Provenance on every layer: source, method, unit, dates, revisit interval, and one ID pinning the case, product and seed. | `provenance_id`; `revisit_days=12` |
| 2:02–2:09 | First tab again, deck zoomed with the wheel until single hexagons fill the frame (the camera already sits a step closer than the loader's view state). No caption on screen — the number is spoken | The posting is 80 metres, so city scale, not building scale. We never say "this building is sinking". | `posting is 80 m`; `~201 m across` — `DO NOT CLAIM` |
| 2:09–2:12 | **No coverage bar exists in the app.** Put the card on screen instead: `./scripts/python.sh scripts/demo_numbers.py`, its `COVERAGE VERDICT` and the per-case coverage lines | Coverage is 54.0 percent of the AOI in Mexico City, 57.2 in Jakarta, 66.0 in Málaga: each clears the 50 percent bar. | `coverage_aoi=54.01%`, `57.18%`, `66.00%` — `CASE` lines; `coverage_bbox=52.30%`, `48.85%`, `36.32%` is the envelope, never the bar |
| 2:12–2:15 | Cut to the third tab (`Hazard and infrastructure`): its own card states the caveat, so the audience reads it while you say it | Context layers are delivered, not a hazard assessment — this is the tab that says so itself. | no number |
| 2:15–2:20 | Slow fade to closing card; cut through the fourth tab's `Known limitations` on the way out. No new claims on screen | The seasonal amplitude is fitted, not measured. No GNSS validation yet, and every number is mock, not NISAR. A bound is not a prediction. | `annual_coverage=2.004` clears the per-harmonic gate; `median_seasonal_amp_mm=1.50` fitted — `SEASONAL SPLIT` (claimable, not measured) |

## Number ledger — what may be said, and only this

| Spoken or shown | Card value | Card line |
|---|---|---|
| 11,600 cells | `cells=11,600` | `CASE mexico-city` |
| 64.7 mm/yr | `median=-64.68` (spoken to 1 dp) | `CASE mexico-city` |
| 62 dates, 732 days, 12-day revisit | `(732 d, 62 dates) revisit_days=12` | `CASE mexico-city` (identical for all three) |
| ±425 mm/yr | `shared legend = ±425 mm/yr` = `ceil(max abs velocity 415.14 / 25) * 25` | `CONTRAST` |
| 50× | `208.62 / 4.17 = 50.09x` (spread = p95 − p05) | `CONTRAST` |
| 54.01 / 57.18 / 66.00 percent of the AOI (the bar's denominator) | `coverage_aoi=54.01%`, `57.18%`, `66.00%` | `CASE` lines |
| bbox envelope only: 52.30 / 48.85 / 36.32 percent (`coverage_bbox`, quote it only with the word bbox, never against the 50 % bar) | `coverage_bbox=52.30%`, `48.85%`, `36.32%` | `CASE` lines — envelope, not coverage of the AOI |
| −1.3 and 1.5 mm/yr | `median=-1.31`, `detection_limit=1.50` | `NULL RESULT malaga` |
| 80 m posting, 200 m cell | `posting is 80 m`, `one H3 res-9 cell is ~201 m across` | `DO NOT CLAIM` |
| fitted seasonal amplitude — may be spoken, but only as a fit, never as a measured cycle | `median_seasonal_amp_mm=1.50` (Mexico City), `median_seasonal_amp_mm=12.00` (Jakarta), `median_seasonal_amp_mm=3.50` (Málaga) | `SEASONAL SPLIT` lines |
| −415.14 core, −232.16 p05 (screen only, never spoken) | `max abs velocity 415.14`, `p05 -232.16` | `CONTRAST` |

**Never quote a number the panel shows but the card does not.** The reading panel prints the filtered minimum coherence and the median displacement; the second tab prints cells above threshold and critical-cell counts. None of those is in the card, so none of them is spoken — if a judge asks, say "read it off the artefact, the `Provenance ID` is on screen".

## What is on screen, and where — the presenter's grep

Exact handles at this commit, by symbol rather than by line number (line numbers rot; the guard that binds this document to the numbers also refuses non-derived digits, so they live in the review, not here).

| Beat | Element | Handle |
|---|---|---|
| 0:20 | demo banner | `DEMO_BANNER` (`"DEMONSTRATION DATA"`, `src/ui/formatters.py`), rendered by `render_header` in `app.py` with `role="status"` |
| 0:20 | case switcher | `render_filters` in `app.py`: `st.expander("Filters", expanded=not compact)` + `st.selectbox("Case")` |
| 0:30 | cell count | `_case_block` in `src/ui/educational.py` (`Cells` card); `_legend_details` count line inside `Case comparison and context` |
| 0:30 | median velocity | `_stats_strip`, fed by `get_map_kpis` (`src/data/ui_bridge.py`), label `Median velocity` |
| 0:44 | time axis | `_reading_panel` → `_render_series` (the hero); the only `st.slider` in `app.py` is `Minimum coherence` |
| 1:00 | cell picker | `_render_deck` (`on_select`), the `H3 cell` selectbox, the `Clear selection` button — all in `src/ui/map_view.py` |
| 1:14 | scale control | `render_map_tab` → `st.radio("Scale", label_visibility="collapsed")`, option labels in `SCALE_LABELS` |
| 1:30 | null-result note | `detection_caption` → the `null-note` div under the map row (`src/ui/map_view.py`) |
| 1:40 | provenance group | `_inspector_groups` → `Provenance` (`src/ui/map_view.py`) |
| 1:56 | provenance line | `_legend_details` (`Scale and provenance`) inside `Case comparison and context`; `render_physics_tab` → `Full meta.json record` |
| 2:12 | context caveat | `render_hazard_tab` (`src/ui/context_view.py`) |

## Bracketed swaps — say the brackets, not the original, when the condition holds

- [if the real granule landed: 0:20–0:30 becomes "This is one real NISAR GUNW granule, provisional. The badge says nisar-gunw; coverage is what it is, not what we hoped." The `DEMONSTRATION DATA` banner removes itself once `product` is no longer `mock` — `is_demonstration()` reads the artefact, not a flag. Swap 2:09–2:12 to the measured coverage and drop MOCK from the closing card.]
- [if change-event pins ship: 1:40–1:50 opens a pin for one detected change — Δγ, ΔdB, first and last date, confidence — and says "a change in imagery, not yet a change on the ground." Contract v1.0 has no events artefact; if the pin is absent, keep the provenance group and say "no change layer today".]
- [if per-cell series is generated (`--per-cell-series`): 1:00–1:14 says "the cell's own series". Today `meta.stats.per_cell_series.enabled` is false for all three cases, so clicking a cell draws the **area aggregate** and the caption says so out loud: "This H3 cell has no series of its own; showing the area aggregate (the artefact does not emit per-cell series)". Read that caption rather than talking over it.]

## Do not say (five phrases)

| Never say | Say instead | Why |
|---|---|---|
| "we predict earthquakes" (or collapse, or flooding) | "we measure how fast the ground has already moved" | nothing in the artefacts forecasts anything; README §13 |
| "building-level" / "this building is sinking" | "one cell; posting 80 m, city scale" | card `DO NOT CLAIM`; README §13.3 |
| "validated against GNSS" | "sign and heading conventions are recorded, not validated" | these are the app's own words: `Known caveat` on the fourth tab, and `not GNSS-validated` in the reading panel's `Deformation` group (`_inspector_groups`) |
| "Málaga is stable" / "Málaga has no data" | "Málaga is below its 1.5 mm/yr detection bound" | card `NULL RESULT` + `DO NOT CLAIM` |
| "city-wide for all three" / "real NISAR results" | "AOI coverage clears the 50 % bar in all three cities, but every cell is MOCK" | card `COVERAGE VERDICT` + `!! product == 'mock'`; the envelope figures (bbox Jakarta 48.85 %, Málaga 36.32 %) are not coverage and settle nothing |

**On the third-city asymmetry.** Do not build a beat on a per-case "GNSS reference": no artefact on this commit carries one — `meta.json` has `reference_point` (a lon/lat pair) and nothing else, and no string anywhere reads "no declarado". The asymmetry that *is* documented, and that the fourth tab prints, is EGMS eligibility: Málaga's `Case` card renders `Declared role: control — declared null result (EU, EGMS-eligible)`, while Jakarta and Mexico City render roles naming no external reference product, and the availability card says of all of them that nothing was downloaded. If pressed: "Málaga is the only city with an eligible external reference product; for none of them did we fetch it." **[needs a live run]** to confirm the role line renders at your screen width — the string comes from `config/study_cases.yaml` `role:` via `load_documented_limits()`.

## Closing card (one line, hold 4 s)

`Three Cities, One Pump · github.com/[owner to fill]/SAR_NASA · Targets: NISAR GUNW, Sentinel-1 (ASF/NASA), EGMS (Málaga) · MOCK — no granules fetched, synthetic numbers`

**Why this wording.** The earlier card read "Data: NISAR GUNW and Sentinel-1 via ASF; EGMS for Málaga", which reads as sources *used*. Nothing was fetched for this submission: `data/raw/` holds no granule, the only EGMS artefact is the availability probe in `data/processed/_availability/*.json`, and every artefact file says `product=mock`. A judge who asks "where is the EGMS data?" must not be able to catch the closing card in a false claim, so the line names sources as *targets* and states the mock status in the same breath.

**Editor notes** (not spoken): never crop the `DEMONSTRATION DATA` banner or the tab strip out of a demo shot. The banner is rendered above the tabs and never inside one, so any crop that keeps the tab strip keeps the banner — check it on the first frame of each demo shot. Never clip the shared legend at p05: the core reaches −415.14 mm/yr and would saturate while Málaga still looked flat. Do not open `Filters` or `Case comparison and context` mid-sentence: both are collapsed/expanded by state and re-opening costs seconds. Repo URL and mock/real status are the only two things to re-check on the day; if `product` changed, `is_demonstration()` silently removes the banner and the 0:20 line becomes false.
