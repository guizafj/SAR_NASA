# Submission video script — Three Cities, One Pump

**Runtime** 20 s problem · 90 s demo · 30 s architecture and impact = 2:20. One presenter, read aloud.
**Numbers** every spoken figure is copied from `./scripts/python.sh scripts/demo_numbers.py` (run on `data/processed/`, 2026-11-14); the last column names the card line. Re-run the card before recording: if a number moves, the script moves.

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
| 0:30–0:44 | Mexico City map, KPI card, date slider at 2026-07-02 (`nisar_grid.parquet`, `meta.json`) | Open on Mexico City: 13,379 cells, median minus 54.7 millimetres a year. Twelve dates over 132 days, on a twelve-day revisit. | `cells=13,379`, `median=-54.68`, `(132 d, 12 dates) revisit_days=12` |
| 0:44–1:00 | Slider dragged to 2026-11-11; map recolours, cumulative mm readout | Drag the slider: displacement accumulates from zero at the first acquisition. Zero starts the record; it is not proof the ground is still. | `window=2026-07-02..2026-11-11`; `val_t1 = 0` by construction (handoff §3) |
| 1:00–1:14 | Click one cell; side panel shows velocity, coherence, land use, risk, then the series panel (`nisar_grid.parquet`, `timeseries.csv`) | Click a cell: velocity, coherence, land use, risk. Open the series panel. One cell is the map unit. | `posting 80 m`, `~201 m across` — `DO NOT CLAIM` |
| 1:14–1:30 | Toggle "shared legend" ±425; Málaga panel goes pale, Mexico City saturates nowhere | Switch to one shared scale, plus or minus 425 millimetres a year. Mexico City's spread is 49 times Málaga's, so Málaga goes nearly blank. | `shared legend = ±425 mm/yr`; `206.36 / 4.20 = 49.1x` — `CONTRAST` |
| 1:30–1:40 | Málaga panel + null-result box; `timeseries_three_cities.png` inset | Blank is not missing data. Málaga's median is minus 1.4 millimetres a year, below its 1.5 detection bound. The bound is the result. | `detection_limit=1.50`, `median=-1.36` — `NULL RESULT malaga` |
| 1:40–1:50 | Evidence card for the selected cell, or for one detected change if the pin layer shipped: unit, dates, coherence, `provenance_id` (`meta.json`) | Open the evidence card: unit, date range, coherence, provenance ID. Source, method and ID make the number re-derivable. | `provenance_id=mexico-city+mock+20261114+cc912b7a` |

## 3 · Architecture and impact — 1:50–2:20

| Time | On screen / shot list (artefact) | Say aloud (≤ 25 words) | Number → source |
|---|---|---|---|
| 1:50–1:56 | README §3 two-layer diagram: offline pipeline → four frozen artefacts → Streamlit | Offline pipeline, frozen contract, Streamlit app. The app reads four artefacts and never touches raw radar. | four artefacts — README §4 |
| 1:56–2:02 | Provenance footer opening into `meta.provenance` and one layer's `source` | Provenance on every layer: source, method, unit, dates, revisit interval, and one ID you can rerun and diff. | `provenance_id`; `revisit_days=12` |
| 2:02–2:09 | Grid zoomed until single cells are visible; caption "one H3 res-9 cell" | The posting is 80 metres, so city scale, not building scale. We never say "this building is sinking". | `posting is 80 m`; `~201 m across` — `DO NOT CLAIM` |
| 2:09–2:15 | Coverage bars for the three cases, threshold at 50 % (`meta.json` coverage) | Coverage is 62 percent in Mexico City, 46 in Jakarta, 39 in Málaga: no city-wide claim where it is below half. | `61.98%`, `46.42%`, `39.31%` — `CASE` lines |
| 2:15–2:20 | Slow fade to closing card; no new claims on screen | What this cannot claim yet: GNSS validation, seasonal amplitude, or that this is real data. A bound is not a prediction. | 132 d < 274 d gate — `DO NOT CLAIM` (seasonal) |

## Number ledger — what may be said, and only this

| Spoken or shown | Card value | Card line |
|---|---|---|
| 13,379 cells | `cells=13,379` | `CASE mexico-city` |
| 54.7 mm/yr | `median=-54.68` (spoken to 1 dp) | `CASE mexico-city` |
| 12 dates, 132 days, 12-day revisit | `(132 d, 12 dates) revisit_days=12` | `CASE mexico-city` (identical for all three) |
| ±425 mm/yr | `shared legend = ±425 mm/yr` = `ceil(max abs velocity 410.75 / 25) * 25` | `CONTRAST` |
| 49× | `206.36 / 4.20 = 49.1x` (spread = p95 − p05) | `CONTRAST` |
| 62 / 46 / 39 percent | `coverage=61.98%`, `46.42%`, `39.31%` | `CASE` lines |
| −1.4 and 1.5 mm/yr | `median=-1.36`, `detection_limit=1.50` | `NULL RESULT malaga` |
| 80 m posting, 200 m cell | `posting is 80 m`, `one H3 res-9 cell is ~201 m across` | `DO NOT CLAIM` |
| −410.75 core, −223.32 p05 (screen only, never spoken) | `max abs velocity 410.75`, `p05 -223.32` | `CONTRAST` |

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
| "city-wide for all three" / "real NISAR results" | "MOCK badge; city-wide only above 50 % coverage" | card `DO NOT CLAIM`; Jakarta 46 %, Málaga 39 % |

## Closing card (one line, hold 4 s)

`Three Cities, One Pump · github.com/[owner to fill]/SAR_NASA · Data: NISAR GUNW and Sentinel-1 via ASF; EGMS for Málaga · Status: MOCK — synthetic numbers, frozen schema`

**Editor notes** (not spoken): never crop the MOCK badge or the provenance footer out of a demo shot. Never clip the shared legend at p05: the core reaches −410.75 mm/yr and would saturate while Málaga still looked flat. Repo URL and mock/real status are the only two things to re-check on the day.
