# Submission video script — Three Cities, One Pump

**Runtime** 20 s problem · 90 s demo · 30 s architecture and impact = 2:20. One presenter, read aloud.
**Numbers** every spoken figure is copied from `./scripts/python.sh scripts/demo_numbers.py` (run on `data/processed/`, 2026-11-14); the last column names the card line. Re-run the card before recording: if a number moves, the script moves. **Coverage is always the AOI figure** (`coverage_aoi`, the denominator of the 50 % bar in `docs/first-granule-runbook.md` §4 and the one `pipeline/process_case.py` warns on); the card also prints `coverage_bbox`, the AOI's envelope, which is never the threshold denominator and is never spoken as coverage.

## 1 · Problem — 0:00–0:20

| Time | On screen / shot list (artefact) | Say aloud (≤ 25 words) | Number → source in the card |
|---|---|---|---|
| 0:00–0:05 | `docs/figures/velocity_shared_scale.png`, three panels, slow push-in on the legend | Three cities. One question. Why does the ground fall in one place and barely move in another? | no number |
| 0:05–0:10 | Same figure, cut to Mexico City's bowl | Because subsidence is set by interbed compressibility — how much the clay itself squeezes — not by how much water is pumped. | no number |
| 0:10–0:15 | Three-panel split: clay bowl / delta edge / karst, labelled from README §1 | Mexico City sits on lacustrine clay. Jakarta, deltaic clay. Málaga, karst. Same pump, different ground. | no number |
| 0:15–0:20 | Málaga panel, deliberately almost blank next to Mexico City | One of them never leaves the detection floor. That blankness is the finding, not missing data. | no number |

## 2 · Demo — 0:20–1:50

The MOCK badge stays visible for every second of this section: all three cases carry `product=mock`, and the footer shows `provenance.source = mock (synthetic stub, pipeline/generate_mock.py)`.

| Time | On screen / shot list (artefact) | Say aloud (≤ 25 words) | Number → source in the card |
|---|---|---|---|
| 0:20–0:30 | App header, MOCK badge, provenance footer (`meta.json`) | This demo runs on mock artefacts. The MOCK badge is on screen; every number is synthetic. Validate the map and the schema, never the science. | mock for 3/3 cases — `!! product == 'mock'` |
| 0:30–0:44 | Mexico City map, KPI card, date slider at 2024-11-10 (`nisar_grid.parquet`, `meta.json`) | Open on Mexico City: 11,694 cells, median minus 62.1 millimetres a year. 62 dates over 732 days, on a 12-day revisit. | `cells=11,694`, `median=-62.07`, `(732 d, 62 dates) revisit_days=12` |
| 0:44–1:00 | Slider dragged to 2026-11-12; map recolours, cumulative mm readout | Drag the slider: displacement accumulates from zero at the first acquisition. Zero starts the record; it is not proof the ground is still. | `window=2024-11-10..2026-11-12`; `val_t1 = 0` by construction (handoff §3) |
| 1:00–1:14 | Click one cell; side panel shows velocity, coherence, land use, risk, then the series panel (`nisar_grid.parquet`, `timeseries.csv`) | Click a cell: velocity, coherence, land use, risk. Open the series panel. One cell is the map unit. | `posting 80 m`, `~201 m across` — `DO NOT CLAIM` |
| 1:14–1:30 | Toggle "shared legend" ±425; Málaga panel goes pale, Mexico City saturates nowhere | Switch to one shared scale, plus or minus 425 millimetres a year. Mexico City's spread is 50 times Málaga's, so Málaga goes nearly blank. | `shared legend = ±425 mm/yr`; `209.23 / 4.16 = 50.3x` — `CONTRAST` |
| 1:30–1:40 | Málaga panel + null-result box; `timeseries_three_cities.png` inset | Blank is not missing data. Málaga's median is minus 1.4 millimetres a year, below its 1.5 detection bound. The bound is the result. | `detection_limit=1.50`, `median=-1.36` — `NULL RESULT malaga` |
| 1:40–1:50 | Evidence card for the selected cell, or for one detected change if the pin layer shipped: unit, dates, coherence, `provenance_id` (`meta.json`) | Open the evidence card: unit, date range, coherence, provenance ID. Source, method and ID make the number re-derivable. | `provenance_id=mexico-city+mock+20261114+cc912b7a` |

## 3 · Architecture and impact — 1:50–2:20

| Time | On screen / shot list (artefact) | Say aloud (≤ 25 words) | Number → source |
|---|---|---|---|
| 1:50–1:56 | README §3 two-layer diagram: offline pipeline → four frozen artefacts → Streamlit | Offline pipeline, frozen contract, Streamlit app. The app reads four artefacts and never touches raw radar. | four artefacts — README §4 |
| 1:56–2:02 | Provenance footer opening into `meta.provenance` and one layer's `source` | Provenance on every layer: source, method, unit, dates, revisit interval, and one ID you can rerun and diff. | `provenance_id`; `revisit_days=12` |
| 2:02–2:09 | Grid zoomed until single cells are visible; caption "one H3 res-9 cell" | The posting is 80 metres, so city scale, not building scale. We never say "this building is sinking". | `posting is 80 m`; `~201 m across` — `DO NOT CLAIM` |
| 2:09–2:15 | Coverage bars for the three cases, threshold at 50 % **of the AOI H3 cells** (`coverage_aoi` card lines) | Coverage is 53.7 percent of the AOI in Mexico City, 55.4 in Jakarta, 57.3 in Málaga: each clears the 50 percent bar. | `coverage_aoi=53.69%`, `55.41%`, `57.26%` — `CASE` lines; `coverage_bbox=52.73%`, `47.61%`, `34.83%` is the envelope, never the bar |
| 2:15–2:20 | Slow fade to closing card; no new claims on screen | The seasonal amplitude is fitted, not measured. No GNSS validation yet, and every number is mock, not NISAR. A bound is not a prediction. | `annual_coverage=2.004` clears the per-harmonic gate; `median_seasonal_amp_mm=1.50` fitted — `SEASONAL` (claimable, not measured) |

## Number ledger — what may be said, and only this

| Spoken or shown | Card value | Card line |
|---|---|---|
| 11,694 cells | `cells=11,694` | `CASE mexico-city` |
| 62.1 mm/yr | `median=-62.07` (spoken to 1 dp) | `CASE mexico-city` |
| 62 dates, 732 days, 12-day revisit | `(732 d, 62 dates) revisit_days=12` | `CASE mexico-city` (identical for all three) |
| ±425 mm/yr | `shared legend = ±425 mm/yr` = `ceil(max abs velocity 415.14 / 25) * 25` | `CONTRAST` |
| 50.3× | `209.23 / 4.16 = 50.3x` (spread = p95 − p05) | `CONTRAST` |
| 53.69 / 55.41 / 57.26 percent of the AOI (the bar's denominator) | `coverage_aoi=53.69%`, `55.41%`, `57.26%` | `CASE` lines |
| bbox envelope only: 52.73 / 47.61 / 34.83 percent (`coverage_bbox`, quote it only with the word bbox, never against the 50 % bar) | `coverage_bbox=52.73%`, `47.61%`, `34.83%` | `CASE` lines — envelope, not coverage of the AOI |
| −1.4 and 1.5 mm/yr | `median=-1.36`, `detection_limit=1.50` | `NULL RESULT malaga` |
| 80 m posting, 200 m cell | `posting is 80 m`, `one H3 res-9 cell is ~201 m across` | `DO NOT CLAIM` |
| fitted seasonal amplitude — may be spoken, but only as a fit, never as a measured cycle | `median_seasonal_amp_mm=1.50` (Mexico City), `median_seasonal_amp_mm=12.00` (Jakarta), `median_seasonal_amp_mm=3.50` (Málaga) | `SEASONAL` lines |
| −415.14 core, −231.89 p05 (screen only, never spoken) | `max abs velocity 415.14`, `p05 -231.89` | `CONTRAST` |

## Bracketed swaps — say the brackets, not the original, when the condition holds

- [if the real granule landed: 0:20–0:30 becomes "This is one real NISAR GUNW granule, provisional. The badge says nisar-gunw; coverage is what it is, not what we hoped." Swap 2:09–2:15 to the measured coverage, and drop MOCK from the closing card.]
- [if change-event pins ship: 1:40–1:50 opens a pin for one detected change — Δγ, ΔdB, first and last date, confidence — and says "a change in imagery, not yet a change on the ground." Contract v1.0 has no events artefact; if the pin is absent, keep the provenance card and say "no change layer today".]
- [if per-cell series is generated (`--per-cell-series`): 1:00–1:14 says "the cell's own series". Today `meta.stats.per_cell_series.enabled` is false for all three cases, so the panel can only show the case aggregate.]

## Do not say (five phrases)

| Never say | Say instead | Why |
|---|---|---|
| "we predict earthquakes" (or collapse, or flooding) | "we measure how fast the ground has already moved" | nothing in the artefacts forecasts anything; README §13 |
| "building-level" / "this building is sinking" | "one cell; posting 80 m, city scale" | card `DO NOT CLAIM`; README §13.3 |
| "validated against GNSS" | "GNSS validation is post-MVP; velocity is the calibrated claim" | README §13 is a target check, §15.4 is post-MVP |
| "Málaga is stable" / "Málaga has no data" | "Málaga is below its 1.5 mm/yr detection bound" | card `NULL RESULT` + `DO NOT CLAIM` |
| "city-wide for all three" / "real NISAR results" | "AOI coverage clears the 50 % bar in all three cities, but every cell is MOCK" | card `COVERAGE VERDICT` + `!! product == 'mock'`; the envelope figures (bbox Jakarta 47.61 %, Málaga 34.83 %) are not coverage and settle nothing |

## Closing card (one line, hold 4 s)

`Three Cities, One Pump · github.com/[owner to fill]/SAR_NASA · Targets: NISAR GUNW, Sentinel-1 (ASF/NASA), EGMS (Málaga) · MOCK — no granules fetched, synthetic numbers`

**Why this wording.** The earlier card read "Data: NISAR GUNW and Sentinel-1 via ASF; EGMS for Málaga", which reads as sources *used*. Nothing was fetched for this submission: `data/raw/` holds no granule, the only EGMS artefact is the availability probe in `data/processed/_availability/*.json`, and every artefact file says `product=mock`. A judge who asks "where is the EGMS data?" must not be able to catch the closing card in a false claim, so the line names sources as *targets* and states the mock status in the same breath.

**Editor notes** (not spoken): never crop the MOCK badge or the provenance footer out of a demo shot. Never clip the shared legend at p05: the core reaches −415.14 mm/yr and would saturate while Málaga still looked flat. Repo URL and mock/real status are the only two things to re-check on the day.
