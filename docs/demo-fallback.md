# Demo fallback — record the 90 seconds without the app

Use only if the Streamlit app is not runnable at recording time: everything below needs nothing but this repository. All three cases carry `product == "mock"`, so §5 applies.

## 1. Three assets that already exist

- `docs/figures/velocity_shared_scale.png` — regenerate with `./scripts/python.sh scripts/make_figures.py`; three panels on one symmetric ±425 mm/yr legend, Málaga almost blank beside Mexico City (README §1).
- `docs/figures/timeseries_three_cities.png` — same command; the 62 acquisitions (2024-11-10 → 2026-11-12, AOI aggregate) with Málaga's ±1.5 mm/yr detection floor (±3.0 mm over this 2-year window) as the green band.
- `scripts/demo_numbers.py` — `./scripts/python.sh scripts/demo_numbers.py`; the card with every spoken number, `provenance_id`, limitations, DO NOT CLAIM and the 90-second skeleton.

## 2. Interactive alternative: the GeoJSON, no app

One H3 cell polygon per row, EPSG:4326, properties `velocity_mm_yr`, `coherence`, `land_use`, `risk_level`:

```
./scripts/python.sh scripts/export_grid_geojson.py --out data/staging/demo
```

**kepler.gl** (browser, network, no install): Add Data ▸ File ▸ drop a `.geojson`; Point layer ▸ Color Based On `velocity_mm_yr` ▸ Color Scale **Quantize** ▸ domain **−425 … 425** ▸ 9-step diverging ramp, white at 0 ▸ radius 10 px fixed.
Type both bounds; never accept the auto domain.

**QGIS** (offline, needs QGIS): Add Vector Layer ▸ Symbology ▸ Graduated ▸ Value `velocity_mm_yr` ▸ Mode **Equal Interval** ▸ Min **−425**, Max **425** ▸ 10 classes ▸ RdBu reversed ▸ 1.2 mm markers;
copy/paste the style onto the other two layers so all three share one legend.

**Why fixed:** ±425 = `ceil(max|velocity| 415.14 / 25) * 25` for every case (`CONTRAST`). Auto stretches each layer to its own range, so Málaga's 4.16 mm/yr spread fills the ramp like Mexico City's 209.23 and the 50.3× contrast vanishes;
clipping at p05 (−231.89) hides the −415.14 core; and auto colours move on zoom while the data does not.

## 3. Beat → asset → sentence

Say the script's words (`docs/video-script.md`); the handles only identify each beat.

| Beat | On screen | Say |
|---|---|---|
| 0:00–0:20 | velocity figure, cropping to Mexico City, then Málaga | script verbatim: "One question", "Same pump, different ground." |
| 0:20–0:30 | card line `!! product == 'mock'` | verbatim: "every number is synthetic" — this line is the badge |
| 0:30–0:44 | Mexico City panel + `CASE mexico-city` | verbatim: "Open on Mexico City" |
| 0:44–1:00 | timeseries figure, all 12 markers | **replace**: "Twelve acquisitions, aggregate; the map carries first and last snapshot only." |
| 1:00–1:14 | kepler tooltip / QGIS Identify on one point | **replace**: "Velocity, coherence, land use, risk for one cell — no series." |
| 1:14–1:30 | shared legend of the velocity figure | **replace**: "This is already the shared scale, plus or minus 425." |
| 1:30–1:40 | timeseries figure, green detection band | verbatim: "Blank is not missing data." |
| 1:40–1:50 | card line `provenance_id=mexico-city+mock+…` | **replace**: "No card; this provenance line is the evidence." |
| 1:50–2:20 | README §3, then meta.json limitations | script verbatim; needs no app |

## 4. What this cannot show — and what to say instead

- **Click-to-interrogate, per-cell series**: `stats.per_cell_series.enabled` is false; say "the series is the city aggregate today; per-cell is opt-in".
- **The date slider**: the contract carries `val_t1`/`val_t2`, not a per-date grid; say "the map is first and last date; the series carries all twelve".
- **Anything needing the deployed URL**: say "not deployed; the recording is the frozen artefacts".

## 5. Honesty line

`docs/handoff-mock.md` §3 item 3: "`product` is `mock` and `provenance.source` says so. Render the badge."
Say the `limitations[]` sentence it makes mandatory, verbatim (handoff §4): "**MOCK: every number is synthesised by pipeline/generate_mock.py; validate the map and the schema, never the science.**"
