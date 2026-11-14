# Three Cities, One Pump

**An interactive SAR change & subsidence explorer — NISAR + Sentinel-1.**

NASA Space Apps Challenge 2026 — *Dancing with the SARs*
**Deliverable: a deployed web application**, not a report.

---

## 1. What it is

A browser app that lets anyone scrub through time and watch three cities sink — then see why
one of them barely moves at all.

| Capability | What the user does |
|---|---|
| **Time scrub** | Drag a slider across acquisition dates; SAR backscatter and velocity layers animate |
| **Before/after** | Swipe-compare two dates on the same viewport |
| **Click to interrogate** | Click any pixel → displacement time series, regime label, nearby infrastructure |
| **Compare mode** | Three cities side-by-side on **one shared colour scale** |
| **Change pins** | Auto-detected coherence-loss / amplitude-change events, clickable |

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
| 5 | **Change** | Coherence-loss / amplitude-change mask | Two-date picker; clickable change pins |

### Interaction contract

- **Every visual encodes a number with a stated unit and source.** No unlabelled colour.
- **Provenance badge on every layer** — dataset, date range, method (e.g. `Sentinel-1 PS-InSAR,
  2015–2020`). If a layer fell back from NISAR, the badge says so.
- **Shareable state** via URL hash: `#city=jakarta&layer=velocity&t=2026-03-04&scale=shared`
- **Deterministic demo** — no live compute on the critical path (see §3).

---

## 3. Architecture — precompute everything, ship static

The single highest-risk failure in a 48 h demo is *live InSAR processing*. So: the pipeline runs
**offline**, emits static artefacts, and the app is a static site.

```
┌──────────────────────────┐        ┌────────────────────┐        ┌──────────────────┐
│  Offline pipeline (py)   │  ───▶  │  Static artefacts  │  ───▶  │  Browser (SPA)   │
│  ASF/HyP3 → ISCE2/MintPy │        │  COG · PMTiles     │        │  MapLibre+deck   │
│  → velocity, change, ts  │        │  JSON series       │        │  no backend      │
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
  "layers": {
    "velocity":  { "src": "pmtiles://vel.pmtiles", "unit": "mm/yr", "range": [-450, 50],
                   "provenance": "Sentinel-1 PS-InSAR 2015-2020" },
    "coherence": { "src": "pmtiles://coh.pmtiles", "unit": "-", "range": [0, 1] },
    "backscatter": { "src": "pmtiles://bs/{date}.pmtiles", "unit": "dB", "range": [-25, 5] },
    "change":    { "src": "pmtiles://chg.pmtiles", "unit": "-", "range": [0, 1] }
  },
  "series": "series/{z}/{x}/{y}.json",
  "stats": { "median_vel": -128, "p95_vel": -450, "compaction_m3_yr": 6.0e7 }
}
```

**Tiling rule of thumb.** A 50 × 50 km city at 10 m ≈ 5000² px. Do **not** ship one raster:
pre-generate a tile pyramid at **z8–z14**, and encode velocity as quantised int16 with the
`range` from `meta.json` so tiles stay small and the legend stays honest.

### Client query path (no server)

```python
on_click(lon, lat):
    city   = city_at(lon, lat)
    tile   = xyz_of(lon, lat, z=12)
    series = fetch(f"{BASE}/{city}/series/12/{tile.x}/{tile.y}.json")
    chart.render(series)          # trend, seasonal amplitude, regime, quality flags
```

---

## 4. Study areas

| City | AOI | Role | Primary hazard |
|---|---|---|---|
| Mexico City, MX | 19.43 N, −99.13 W | Case A (extreme) | Differential subsidence → infrastructure, drainage inversion |
| Jakarta, ID | −6.21 S, 106.85 E | Case B (coastal) | Inundation; 64% piped-water coverage drives well pumping |
| Málaga, ES | 36.72 N, −4.42 W | **Control / negative** | Seawater intrusion; reservoir depletion |

---

## 5. Data sources

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
> Sentinel-1 → OPERA DISP-S1 → EGMS → local ISCE2+MintPy from GSLC. Record which one won in
> `provenance`.

### Hydrology
`GRACE-FO TELLUS mascons` (PO.DAAC ✓) · `GLDAS` (✓) · `IGRAC GGIS` (✓) · `USGS NWIS API` (✓) ·
Junta de Andalucía SAIH · CONAGUA/SACMEX

### Urban context
`GHSL` (✓) · `WorldPop` (✓) · `DLR WSF 2019` (✓) · `Copernicus EMS` (✓) · Overture / Google
Open Buildings / OSM

---

## 6. Science pipeline (offline build)

```
P0  Scope      AOI, EPSG:326xx, reference pixel
P1  Ingest     asf_search → HyP3/DISP/EGMS (record provenance)
P2  Interfero  ISCE2 + SNAPHU → GUNW/RUNW
P3  Correct    RAiDER/GACOS troposphere; MintPy DEM error + ramps
P4  Series     network inversion → cumulative displacement cube
P5  Decompose  LOS → vertical (asc+desc, v_north≈0; else assume purely vertical)
P6  Separate   trend + seasonal harmonics + steps → secular vs seasonal
P7  Metrics    velocity, |∇v|, compaction volume, S* = Δb/Δh
P8  Join       buildings, population, reservoirs, head
P9  Emit       COG/PMTiles + series JSON + meta.json  ← app contract
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

## 7. Pseudocode

### A — build (offline, produces app artefacts)

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

    # P9 emit artefacts
    write_cog(vel,        f"{city}/velocity.tif", quantise=(-450, 50))
    write_pmtiles(vel,    f"{city}/vel.pmtiles",   zooms=(8, 14))
    write_series_json(fit, f"{city}/series/")
    write_meta(city, provenance=provenance[city], stats=dict(median=..., volume=volume))

# app-global legend: ONE shared scale, or Málaga's flatness is invisible
write_global_scale([c.velocity_range for c in cities], mode="shared")
```

### B — app runtime (browser, no compute)

```python
state = parse_url_hash()                       # city, date, layer, scale mode

map.on("load",  lambda: add_layers(state))
map.on("click", lambda p: chart.render(fetch_series(state.city, p)))
slider.on("input", lambda d: set_date(d))      # swap pre-tiled backscatter layer
toggle.on("change", lambda: set_scale(shared if on else independent))

function set_scale(shared):
    legend = global_range if shared else city_range
    map.layers.forEach(l -> l.colorScale = legend)
    legend.render(legend)
```

---

## 8. Software stack

**Processing (offline)**
`isce2` (`conda install -c conda-forge isce2`) · `snaphu` ·
**`MintPy`** (`mintpy.readthedocs.io`) · `hyp3_sdk` · `opera-utils` ·
`RAiDER` (`github.com/dbekaert/RAiDER`) · `asf_search` · `earthaccess`

**Geospatial py**
`xarray` · `dask` · `rioxarray` · `rasterio` · `geopandas` · `shapely` · `pyproj` ·
`rasterstats` · `scipy.ndimage` · `GDAL` CLI · `pmtiles`

**App**
- **Recommended:** `MapLibre GL JS` + `deck.gl` raster/point layers, `uPlot`/`Observable Plot`
  charts, Vite + TypeScript, **PMTiles** for single-file tile delivery
- **Fastest to build:** Streamlit/Panel + `folium`/`pydeck` — trade polish and slider
  performance for speed
- **QC / figures:** QGIS

---

## 9. Risk & demo strategy

| Risk | Mitigation |
|---|---|
| NISAR urban coverage absent | Build-time fallback chain (§6 P1); badge shows the source used |
| Live InSAR too slow | Never on the critical path; artefacts are pre-built |
| Málaga signal ≤ noise | Render it flat **deliberately**; show the detection-limit band in the legend |
| Slide-like demo (static maps) | Time scrub + click-to-query are the required interactions, not optional |
| Judge asks "is this just pretty maps?" | Every pixel answers with a trend, a regime label, and a provenance badge |

---

## 10. Validation

| Check | Method |
|---|---|
| Velocity accuracy | GNSS / levelling; target < 5 mm/yr RMSE |
| Unwrapping | closure phase on redundant triplets |
| Atmosphere | diff against GACOS/ERA5-corrected run |
| Flood / change masks | Copernicus EMS activation maps |
| Málaga null | detection-limit analysis — publish the upper bound |

Error budget, largest first: unwrapping ≫ troposphere ≫ orbital ≫ DEM ≫ noise.

---

## 11. Limitations

1. Málaga may sit at the detection floor — the bound *is* the result.
2. CDMX is not coastal; don't claim a coastal framing across all three.
3. GUNW posting is 80 m — city scale, not building scale.
4. Urban SAR flood mapping is confounded by double-bounce and shadow; use coherence-loss change
   detection, not thresholding.
5. "Changes in imagery" ≠ "changes on the ground" — geometric effects (layover, shadow) move
   with acquisition geometry. Label the Flipbook accordingly.

---

## 12. Repo layout

```
pipeline/            offline build (produces artefacts)
  ingest/  insar/  metrics/  emit/
data/raw/{city}/     downloads (gitignored)
data/processed/{city}/
app/
  src/map/  src/charts/  src/state/
  public/tiles/{city}/   PMTiles + series JSON
deploy/              static host config
docs/                methods, error budget, figures
```

---

## 13. Key references

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

*Draft. Jakarta rates vary by method and period — quote the source you used in the provenance badge.*
