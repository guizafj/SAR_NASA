# Three Cities, One Pump

**An interactive SAR change & subsidence explorer — NISAR + Sentinel-1.**

NASA Space Apps Challenge 2026 — *Dancing with the SARs*
**Deliverable: a deployed web application**, not a report.

---

## 1. What it is

A browser app that lets anyone scrub through time, watch three cities sink, and see what else is
changing on that ground — then understand why one city barely moves at all.

| Capability | What the user does |
|---|---|
| **Time scrub** | Drag a slider across acquisition dates; backscatter and velocity layers animate |
| **Before/after** | Swipe-compare two dates in the same viewport |
| **Click to interrogate** | Click any pixel → displacement time series, regime label, quality |
| **Compare mode** | Three cities side-by-side on **one shared colour scale** |
| **Change pins** | Auto-detected, vectorized disturbance events — clickable evidence cards |

### The thesis the app must make visible

Subsidence is set by **interbed compressibility**, not by how much water is pumped.

| | **Mexico City** | **Jakarta** | **Málaga** |
|---|---|---|---|
| Aquifer | Lacustrine clay, closed basin | Deltaic soft clay, coastal | Thin detrital cover, confined karst |
| Regime | Mostly **inelastic** | Mixed | Mostly **elastic** |
| Subsidence | **−45 cm/yr** | ~5 cm/yr avg; >1 m cumulative | mm–cm/yr |
| Failure mode | Land loss, faulting, drainage inversion | Inundation, capital relocation | Salinization, reservoir depletion |

> **The product insight:** with a shared legend, Málaga renders nearly flat. **That blankness is
> the finding** — and it is a UI decision, not a paragraph.

---

## 2. App specification

### Views

| # | View | Content | Key interaction |
|---|---|---|---|
| 1 | **Compare** (hero) | 3 city maps, synchronized | Shared/independent legend toggle; one global date slider |
| 2 | **City** | Velocity map + sampled time series | Click-to-sample; layer toggles; opacity |
| 3 | **Flipbook** | Raw SAR backscatter (GCOV) per date | Date scrub, swipe divider, play/pause |
| 4 | **Trends** | Secular vs seasonal decomposition; reservoir + head charts | Linked brushing across charts and map |
| 5 | **Change** | Coherence-loss / amplitude-change **event pins** | Click pin → evidence card; filter by type; table view |

### Interaction contract

- **Every visual encodes a number with a stated unit and source.** No unlabelled colour.
- **Provenance badge on every layer** — dataset, date range, method, revisit interval.
- **Shareable state** via URL hash: `#city=jakarta&layer=velocity&t=2026-03-04&scale=shared`
- **Deterministic demo** — no live compute on the critical path (see §3).

---

## 3. Architecture — precompute everything, ship static

The highest-risk failure in a 48 h demo is *live InSAR processing*. So the pipeline runs
**offline**, emits static artefacts, and the app is a static site.

```
┌──────────────────────────┐        ┌────────────────────┐        ┌──────────────────┐
│  Offline pipeline (py)   │  ───▶  │  Static artefacts  │  ───▶  │  Browser (SPA)   │
│  ASF/HyP3 → ISCE2/MintPy │        │  COG · PMTiles     │        │  MapLibre+deck   │
│  → velocity, events, ts  │        │  JSON · PNG chips  │        │  no backend      │
└──────────────────────────┘        └────────────────────┘        └──────────────────┘
```

**Why:** no server, no CORS pain, no cold-start, no token expiry mid-judging. Deploys to
GitHub Pages / Netlify / S3.

### Artefacts emitted per city

```jsonc
// meta.json
{
  "city": "mexico-city",
  "bbox": [-99.35, 19.20, -98.85, 19.65],
  "crs": "EPSG:32614",
  "dates": ["2026-01-12", "2026-01-24", "..."],
  "reference_point": [-99.13, 19.43],
  "revisit_days": 12,                 // 12 = NISAR, 6 = Sentinel-1
  "layers": {
    "velocity":  { "src": "pmtiles://vel.pmtiles", "unit": "mm/yr", "range": [-450, 50],
                   "provenance": "Sentinel-1 PS-InSAR 2015-2020, 6-day" },
    "coherence": { "src": "pmtiles://coh.pmtiles", "unit": "-", "range": [0, 1] },
    "backscatter": { "src": "pmtiles://bs/{date}.pmtiles", "unit": "dB", "range": [-25, 5] },
    "change":    { "src": "pmtiles://chg.pmtiles", "unit": "-", "range": [0, 1] },
    "events":    { "src": "pmtiles://events.pmtiles", "geom": "polygon",
                   "thresholds": { "k_gamma": 2.5, "min_ddB": 3.0, "mmu_m2": 500 } }
  },
  "series": "series/{z}/{x}/{y}.json",
  "stats": { "median_vel": -128, "p95_vel": -450, "compaction_m3_yr": 6.0e7,
             "n_events": 412 }
}
```

**Tiling rule of thumb.** A 50 × 50 km city at 10 m ≈ 5000² px. Do **not** ship one raster:
pre-generate a tile pyramid at **z8–z14**, and encode velocity as quantised int16 with the
`range` from `meta.json` so tiles stay small and the legend stays honest.

---

## 4. Change detection & event pins

Coherence tells you *something changed*; amplitude change tells you *what kind*. Together they
form a small, physically-defensible classifier — no unexplainable model.

### The two observables

```
coherence   γ = |Σ s₁s₂*| / sqrt(Σ|s₁|² · Σ|s₂|²)        (L looks)
amplitude   ΔdB = 10·log₁₀(A₂/A₁)
```

### Joint classification

| Δγ | ΔdB | Type | In a city, means |
|---|---|---|---|
| ↓↓ | ↑↑ | `construction` | new buildings, extensions, corner reflectors |
| ↓↓ | ↓↓ | `demolition` / `flood` | cleared plots, excavation, inundation |
| ↓↓ | ↔ | `tillage` / `veg_change` | surface reorganisation, same roughness |
| ↔ | ↑↑ | `densification` | gradual infill, geometry stable |
| ↑ | ↑ | `stabilisation` | freshly built → settled |

### Detection pipeline

```
1. Co-register; common-band filter if mixing sensors
2. Coherence, bias-corrected:  γ_corr = sqrt((L·γ̂² − 1)/(L − 1)),  valid for γ̂ > 1/√L
3. Robust per-pixel z-score vs own temporal distribution:
       z = (γ_t − median_t γ) / (1.4826 · MAD_t γ)
4. Flag:   (z_γ < −k)  AND  (|ΔdB| > a)
5. Persistence: flagged in ≥ m of n consecutive pairs      ← main false-positive killer
6. Morph open/close → connected components → drop < MMU (~500 m²)
7. Polygonize → classify → emit events.pmtiles + chips
```

**Never threshold absolute γ.** Urban γ varies with perpendicular baseline, season, humidity and
land cover. Per-pixel robust z-scores self-calibrate.

### Event record (per pin)

```jsonc
{
  "id": "mex-00312",
  "type": "construction",
  "area_m2": 1840,
  "centroid": [-99.0412, 19.5127],
  "first_date": "2026-02-05", "last_date": "2026-03-17", "n_dates": 3,
  "d_gamma": -0.42, "d_amp_db": 5.8, "confidence": 0.81,
  "landcover": "urban", "buildings_overlap": 2,
  "chips": ["chips/mex-00312_pre.png", "chips/mex-00312_post.png"],
  "series_cell": [12, 1145, 1650]
}
```

**Chips are pre-rendered PNG crops** — the popup loads instantly with zero client-side raster work.

### The click → evidence card

| Element | Source |
|---|---|
| Before/after SAR chips | `chips` |
| Δγ, ΔdB with units + thresholds | `d_gamma`, `d_amp_db`, `meta.thresholds` |
| Type + confidence | classifier |
| Date range + persistence | `first_date`, `n_dates` |
| Mini displacement series for that cell | `series_cell` → §3 query path |
| "2 buildings inside" | `buildings_overlap` |
| Provenance + revisit interval | `meta.json` |

Pin colour = `type`, size = `area_m2`, clustered at low zoom. Click → fly-to + side panel, and the
URL becomes `#event=mex-00312`. Keyboard-focusable pins with `aria-label`; **table fallback view**
so the dataset is queryable and accessible.

### False positives to suppress

| Cause | Mitigation |
|---|---|
| Moisture / rainfall | persistence + amplitude sign |
| Crop phenology | mask by land cover (exclude cropland) |
| Baseline decorrelation | keep B⊥ < critical/2 |
| Layover / shadow | mask by common layover-shadow layer |
| Thermal dilation | require ΔdB; low confidence for single-date |

Validate ~50 events against OSM edits / historical imagery / Copernicus EMS and report precision.

> **⚠️ NISAR recalibration.** NISAR's **12-day** revisit yields systematically lower coherence
> than Sentinel-1's 6-day. **Every threshold above must be re-tuned per sensor** — a
> `k=2.5, γ=0.4` setting derived from Sentinel-1 will behave differently on NISAR. L-band's win
> is penetration: 24 cm detects change *under* canopy (Axarquía groves, CDMX conservation land)
> where C-band decorrelates entirely.

---

## 5. Team & labour division

Three people, three verticals, one shared contract. The architecture (§3) is what makes the split
clean: **yuls produces artefacts, pozo serves them, fradiaz renders them.**

| Member | Role | Owns | Ships |
|---|---|---|---|
| **yuls** | Geospatial & visualization lead | SAR ingest, InSAR, metrics, tiles, **change detection**, cartography, colour scales | `pipeline/`, all artefacts, layer styling + legend |
| **pozo** | Backend & infrastructure | Serving, build orchestration, CI/CD, caching, deploy, route contract | `serve/`, `deploy/`, CI, publish step |
| **fradiaz** | Frontend & interaction | SPA shell, map wiring, slider, charts, **pins + evidence card**, URL state, a11y | `app/src/{ui,state,charts}`, the 5 views |

### Ownership detail

**yuls — geospatial**
- `asf_search` → HyP3/OPERA/EGMS ingest; ISCE2+MintPy where needed
- LOS→vertical, harmonic decomposition, velocity/gradient/volume metrics
- Coherence/amplitude z-scores, persistence, MMU, event polygons, chips
- COG + PMTiles pyramids at z8–z14; series JSON tiling
- Colour scale design — incl. the **global shared scale** that makes Málaga look flat

**pozo — backend**
- Static artefact hosting + CDN/cache headers
- Build orchestration: run `pipeline/` on CI, publish on success
- The **route contract** and its validation
- Deployment, uptime, "does the demo URL work at 4 a.m."

**fradiaz — frontend**
- MapLibre GL JS + deck.gl layer hierarchy; raster re-tiling on zoom
- Date slider, swipe divider, play/pause; charts (uPlot/Observable Plot)
- Event pins, popup/evidence card, type filters, table view
- URL-hash state; linked brushing; the **shared/independent legend toggle**
- Keyboard access, mobile layout, loading/empty/error states

### Interface contracts — freeze on Day 0

**1. `meta.json`** — yuls defines → pozo serves → fradiaz consumes (§3)

**2. `series/{z}/{x}/{y}.json`** — yuls produces → fradiaz renders

```jsonc
{
  "city": "mexico-city", "cell": [12, 1145, 1650], "n_pixels": 842,
  "dates": ["2026-01-12", "2026-01-24", "2026-02-05"],
  "displacement_mm": [0, -3.1, -7.4],
  "coherence": [0.82, 0.79, 0.81],
  "fit": { "trend_mm_yr": -128, "seasonal_amp_mm": 9.4, "r2": 0.91 },
  "regime": "inelastic", "quality": "good"
}
```

**3. HTTP routes** — pozo defines → fradiaz consumes

```
GET /{city}/meta.json
GET /{city}/vel.pmtiles
GET /{city}/coh.pmtiles
GET /{city}/bs/{date}.pmtiles
GET /{city}/events.pmtiles          # vector, polygon geom
GET /{city}/series/{z}/{x}/{y}.json
GET /{city}/chips/{event_id}_{pre|post}.png
GET /global-scale.json
```

**4. Event attributes** — the property table baked into `events.pmtiles` (§4)

### Build order

| Window | yuls | pozo | fradiaz |
|---|---|---|---|
| **H0–2** | Write `meta.json` + `series` + event schemas; commit **stubs** | Static host + routes | Scaffold SPA against stubs |
| **H2–20** | Ingest → velocity → tiles | CI auto-publish | Map + slider + charts on stubs |
| **H20–30** | Metrics, shared scale, **event detection + chips** | Cache headers, size audit | Wire real artefacts; pins + popup |
| **H30–40** | QC, validation table, figures | Deploy hardening | Polish, a11y, table view |
| **H40–48** | Provenance badges, docs | Final deploy | Demo script, rehearsal |

**The stub rule:** fradiaz never blocks on yuls. Ship three fake `series` files, one fake
`meta.json`, and a hand-authored `events.geojson` in H0 — build the whole UI against them.

### Explicit non-overlaps
- yuls writes no app UI code (layer styles as JSON config only).
- pozo touches no scientific parameters — no thresholds, no colour ranges.
- fradiaz computes no geospatial quantity client-side; if a number isn't in an artefact, it
  doesn't exist.

### Definition of done
- **yuls:** `meta.json` validates for CDMX y Málaga; Yakarta se incorpora solo como extensión; cada capa lleva
  `provenance` + `revisit_days`; events pass MMU; precision reported on a 50-event sample.
- **pozo:** all 8 routes serve with correct MIME + `pmtiles` range-request support; TTFB < 200 ms;
  CI reproduces artefacts from clean checkout.
- **fradiaz:** las vistas de mapa, KPIs, serie temporal, comparación y educación son navegables;
  la comparación muestra escala compartida/independiente y Málaga conserva su cota de detección.

### Bus factor
Each member writes `docs/handoff-<name>.md` — what they own, how to run it, the one thing that
will break.

---

## 6. Study areas

| City | AOI | Role | Primary hazard |
|---|---|---|---|
| Mexico City, MX | 19.43 N, −99.13 W | Case A (extreme) | Differential subsidence → infrastructure, drainage inversion |
| Jakarta, ID | −6.21 S, 106.85 E | Case B (coastal) | Inundation; 64% piped-water coverage drives well pumping |
| Málaga, ES | 36.72 N, −4.42 W | **Control / negative** | Seawater intrusion; reservoir depletion |

---

## 7. Data sources

`✓` = live-checked. Open/free unless noted.

### SAR / deformation
| Source | URL | Status |
|---|---|---|
| NISAR product summary (ASF) | `asf.alaska.edu/datasets/daac/nisar` | ✓ |
| NISAR Data User Guide | `nisar-docs.asf.alaska.edu` | ✓ |
| ASF Vertex | `search.asf.alaska.edu` | ✓ |
| **HyP3** (on-demand cloud InSAR) | `hyp3-docs.asf.alaska.edu` | ✓ |
| OPERA DISP-S1 | `displacement.asf.alaska.edu` | ✓ |
| **Copernicus EGMS** (EU, mm/yr) | `egms.land.copernicus.eu` | ✓ |
| COMET-LiCSAR | `comet.nerc.ac.uk/comet-lics-portal` | ✓ |

> **NISAR constraint.** Documented interferometry products (RIFG/RUNW/GOFF/ROFF, and **GUNW**,
> derived from RUNW) are **cryosphere-generated**; SME2 excludes urban areas. Verify coverage in
> Vertex before relying on NISAR for city-wide displacement. Build-time fallbacks: HyP3
> Sentinel-1 → OPERA DISP-S1 → EGMS → local ISCE2+MintPy from GSLC. Record which won in
> `provenance`.

### Hydrology
`GRACE-FO TELLUS mascons` (PO.DAAC ✓) · `GLDAS` (✓) · `IGRAC GGIS` (✓) · `USGS NWIS API` (✓) ·
Junta de Andalucía SAIH · CONAGUA/SACMEX

### Urban context
`GHSL` (✓) · `WorldPop` (✓) · `DLR WSF 2019` (✓) · `Copernicus EMS` (✓) · Overture / Google
Open Buildings / OSM *(also the validation source for events)*

---

## 8. Science pipeline (offline build)

```
P0  Scope      AOI, EPSG:326xx, reference pixel
P1  Ingest     asf_search → HyP3/DISP/EGMS (record provenance + revisit)
P2  Interfero  ISCE2 + SNAPHU → GUNW/RUNW
P3  Correct    RAiDER/GACOS troposphere; MintPy DEM error + ramps
P4  Series     network inversion → cumulative displacement cube
P5  Decompose  LOS → vertical (asc+desc, v_north≈0; else assume purely vertical)
P6  Separate   trend + seasonal harmonics + steps → secular vs seasonal
P7  Metrics    velocity, |∇v|, compaction volume, S* = Δb/Δh
P8  Events     coherence/amplitude z-scores → persistence → polygons (§4)
P9  Join       buildings, population, reservoirs, head
P10 Emit       COG/PMTiles + series JSON + events + chips + meta.json  ← app contract
```

### Equations

**LOS projection** (incidence `θ`, heading `α`):

```
d_LOS = u · [v_up, v_east, v_north]ᵀ ,   u = [cosθ, −sinθ·cosα, −sinθ·sinα]
```
Two geometries → 2 equations, 3 unknowns → assume `v_north ≈ 0` or constrain with GNSS.
**Axis/sign conventions differ between ISCE2, MintPy and SNAP — validate against GNSS before
publishing numbers.**

**Time-series model** (drives the Trends view):

```
v(t) = v₀ + ṫ·t + Σₖ [Aₖcos(2πkt/T) + Bₖsin(2πkt/T)] + Σⱼ Δᵥⱼ·H(t−tⱼ)
```
`ṫ` → inelastic/permanent; `A_seasonal` → elastic/recoverable. Regime index `R = |ṫ|/A_seasonal`.

**Storage loss:**

```
S_skv = Δb / (b₀·Δh)            skeletal specific storage
dV/dt = Σ_px v_up · A_px        compaction-driven storage loss [m³/yr]
```

---

## 9. Pseudocode

### A — build (offline, produces app artefacts) · *yuls*

```python
for city in [MEXICO_CITY, JAKARTA, MALAGA]:
    aoi = load_aoi(city)

    # P1 ingest, with provenance fallback chain
    src = first_available([
        lambda: asf_search.search(dataset="NISAR", processingLevel="GUNW", intersects=aoi),
        lambda: hyp3_s1_stack(aoi),                 # Sentinel-1 on-demand
        lambda: opera_disp_s1(aoi),
        lambda: egms_ortho(aoi),                    # EU only, already velocity
    ])
    provenance[city] = src.name

    # P2–P4
    ifg  = src.interferograms()                     # or isce2 + snaphu locally
    ts   = mintpy.network_inversion(raider.correct(ifg), reference=ref_pixel(city))

    # P5–P6
    v_up   = to_vertical(ts, assume_vnorth=0)
    fit    = fit_harmonics(v_up, period=365.25, n=2)
    regime = where(abs(fit.trend) > 2*fit.seasonal_amp, "inelastic", "elastic")

    # P7 metrics
    vel    = fit.trend * 365.25
    grad   = np.hypot(*sobel(vel))
    volume = np.nansum(fit.trend[mask] * pixel_area)

    # P8 change events
    gamma  = coherence(ifg, looks=L)                    # bias-corrected
    z      = (gamma - gamma.median("time")) / (1.4826 * mad(gamma, "time"))
    ddb    = 10*log10(amp_t / amp_t0)
    flagged = (z < -K) & (abs(ddb) > MIN_DDB)
    keep   = persistence(flagged, m=2, n=3)
    events = classify(connected_components(morphology(keep)),
                      min_area_m2=500, rules=DELTA_GAMMA_DB_TABLE)
    chips  = render_chips(events, pre=first_date, post=last_date)

    # P10 emit artefacts
    write_cog(vel,          f"{city}/velocity.tif", quantise=(-450, 50))
    write_pmtiles(vel,      f"{city}/vel.pmtiles",   zooms=(8, 14))
    write_pmtiles(events,   f"{city}/events.pmtiles")
    write_series_json(fit,  f"{city}/series/")
    write_meta(city, provenance=provenance[city], revisit=src.revisit_days,
               thresholds=dict(k_gamma=K, min_ddB=MIN_DDB, mmu_m2=500),
               stats=dict(median=..., volume=volume, n_events=len(events)))

# app-global legend: ONE shared scale, or Málaga's flatness is invisible
write_global_scale([c.velocity_range for c in cities], mode="shared")
```

### B — app runtime (browser, no compute) · *fradiaz*

```python
state = parse_url_hash()                       # city, date, layer, scale, event

map.on("load",  lambda: add_layers(state))
map.on("click", lambda p: chart.render(fetch_series(state.city, p)))
slider.on("input", lambda d: set_date(d))      # swap pre-tiled backscatter layer
toggle.on("change", lambda: set_scale(shared if on else independent))

pin.on("click", lambda e: open_card({
    "chips":   load_chips(e.id),               # pre-rendered PNGs
    "deltas":  (e.d_gamma, e.d_amp_db),
    "series":  fetch_series_cell(e.series_cell),
    "thresh":  meta["layers"]["events"]["thresholds"],
}))

function set_scale(shared):
    legend = global_range if shared else city_range
    map.layers.forEach(l -> l.colorScale = legend)
    legend.render(legend)
```

---

## 10. Software stack

**Processing (offline)** · *yuls*
`isce2` (`conda install -c conda-forge isce2`) · `snaphu` ·
**`MintPy`** (`mintpy.readthedocs.io`) · `hyp3_sdk` · `opera-utils` ·
`RAiDER` (`github.com/dbekaert/RAiDER`) · `asf_search` · `earthaccess`

**Geospatial py** · *yuls*
`xarray` · `dask` · `rioxarray` · `rasterio` · `geopandas` · `shapely` · `pyproj` ·
`rasterstats` · `scipy.ndimage` (z-scores, morphology, Sobel) · `skimage.measure`
(connected components) · `GDAL` CLI · `pmtiles`

**App** · *fradiaz* (UI) · *pozo* (serving)
- **MVP actual:** `Streamlit` + `pydeck` + `Plotly`, consumiendo artefactos offline y loaders
cacheados. No descarga ni procesa HDF5 durante la interacción.
- **Evolución:** `MapLibre GL JS` + `deck.gl`, `uPlot`/`Observable Plot`, Vite + TypeScript y
  PMTiles para una entrega estática posterior, sin cambiar el contrato.
- **Serving:** static host + CDN (Netlify/Pages/S3), GitHub Actions for publish
- **QC / figures:** QGIS

---

## 11. Risk & demo strategy

| Risk | Mitigation | Owner |
|---|---|---|
| NISAR urban coverage absent | Build-time fallback chain (§8 P1); badge shows source used | yuls |
| Live InSAR too slow | Never on the critical path; artefacts pre-built | yuls / pozo |
| Málaga signal ≤ noise | Render flat **deliberately**; detection-limit band in legend | yuls / fradiaz |
| Change pins noisy | Persistence m-of-n + MMU + land-cover mask; report precision | yuls |
| Thresholds wrong after NISAR swap | `revisit_days` in `meta.json`; re-tune per sensor (§4) | yuls |
| Frontend blocked on data | Stub artefacts frozen at H0; contract never changes | pozo / fradiaz |
| Demo URL dies | Static host + CI publish; rehearsal at H44 | pozo |
| Judge: "just pretty maps?" | Every pixel answers with trend + regime + provenance; pins give evidence | fradiaz |

---

## 12. Validation

| Check | Method |
|---|---|
| Velocity accuracy | GNSS / levelling; target < 5 mm/yr RMSE |
| Unwrapping | closure phase on redundant triplets |
| Atmosphere | diff against GACOS/ERA5-corrected run |
| Event precision | 50-event sample vs OSM edits / historical imagery / Copernicus EMS |
| Change masks | Copernicus EMS activation maps |
| Málaga null | detection-limit analysis — publish the upper bound |

Error budget, largest first: unwrapping ≫ troposphere ≫ orbital ≫ DEM ≫ noise.

---

## 13. Limitations

1. Málaga may sit at the detection floor — the bound *is* the result.
2. CDMX is not coastal; don't claim a coastal framing across all cases.
3. GUNW posting is 80 m — city scale, not building scale.
4. Urban SAR flood mapping is confounded by double-bounce and shadow; use coherence-loss change
   detection, not thresholding.
5. "Changes in imagery" ≠ "changes on the ground" — layover/shadow move with acquisition geometry.
   Label the Flipbook accordingly.
6. Coherence depends on revisit interval; **NISAR 12-day ≠ Sentinel-1 6-day**. Never pool the two
   in one threshold.
7. Event detection is a **supporting layer**, not the headline metric — velocity is the
   calibrated, GNSS-validated claim.

---

## 14. Repo layout

```
pipeline/            offline build — yuls
  ingest/  insar/  metrics/  events/  emit/
serve/               serving + build orchestration — pozo
deploy/              static host config, CI — pozo
data/raw/{city}/     downloads (gitignored)
data/processed/{city}/
app/
  src/map/           layer styling — yuls
  src/ui/  src/state/  src/charts/  src/events/   — fradiaz
  public/tiles/{city}/   PMTiles + series JSON + chips (generated)
docs/                methods, error budget, handoff-<name>.md
```

---

## 15. Key references

| Topic | Cite | DOI |
|---|---|---|
| Global subsidence threat | Herrera-García et al. 2021, *Science* | 10.1126/science.abb8549 |
| Global subsidence → storage loss | Hasan et al. 2023, *Nat Commun* | 10.1038/s41467-023-41933-z |
| Mexico City −45 cm/yr | Cigna & Tapete 2022, *GRL* | 10.1029/2022gl098923 |
| 99 coastal cities, uniform PS-InSAR | Wu et al. 2022, *GRL* | 10.1029/2022gl098477 |
| Method review | Galloway & Burbey 2011, *Hydrogeol J* | 10.1007/s10040-011-0775-5 |
| Indonesia sinking cities | Chaussard et al. 2013, *RSE* | 10.1016/j.rse.2012.10.015 |
| Jakarta 2017–22 multi-track SBAS | 2024, *Geocarto Int* | 10.1080/10106049.2024.2364726 |
| Jakarta piped water → abstraction | 2022, *Water* | 10.3390/w14203197 |
| Málaga/Torremolinos | Ruiz-Constán et al. 2018, *STOTEN* | 10.1016/j.scitotenv.2018.04.280 |
| Axarquía water crisis | Junquera et al. 2025, *PNAS* | 10.1073/pnas.2508055122 |
| NISAR Data User Guide | Kristenson et al. 2026 | 10.5281/ZENODO.20075813 |

---

*Draft. Jakarta rates vary by method and period — quote the source used in the provenance badge.*
