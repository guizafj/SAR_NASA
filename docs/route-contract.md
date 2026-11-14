> **SUPERSEDED for the MVP — this page describes the post-MVP static/PMTiles migration and is NOT
> the artefact contract. The authoritative schema is `docs/data-contract-v1.md` +
> `src/domain/contracts.py`.**

# Route contract v1 — Option B (static site + PMTiles)

**Status: FROZEN as of Sat 14 Nov 2026, hackathon H0.**
Owner: pozo (serving) · Producer: yuls (`pipeline/`) · Consumer: fradiaz (`app/src/`).

This document is normative. If an artefact or a route disagrees with it, the artefact is wrong.
Changes require a `contract_version` bump **and** a message in the team channel; no silent edits.

```
contract_version = "1.0"
```

Everything is static. There is no backend, no database, no CORS preflight, no auth.
A build step turns `data/processed/` into the contract layout below; a dumb file server (or
GitHub Pages / Netlify / S3) serves it.

---

## 1. Routes

All artefacts for one city live under `/{city}/`. `city` matches `^[a-z0-9]+(-[a-z0-9]+)*$`.

| # | Route | Type | Range | Cacheable |
|---|---|---|---|---|
| 1 | `GET /{city}/meta.json` | `application/json` | no | `max-age=60` (mutable per build) |
| 2 | `GET /{city}/vel.pmtiles` | `application/vnd.pmtiles` | **yes** | `immutable` |
| 3 | `GET /{city}/coh.pmtiles` | `application/vnd.pmtiles` | **yes** | `immutable` |
| 4 | `GET /{city}/bs/{date}.pmtiles` | `application/vnd.pmtiles` | **yes** | `immutable` |
| 5 | `GET /{city}/events.pmtiles` | `application/vnd.pmtiles` | **yes** | `immutable` |
| 6 | `GET /{city}/series/{z}/{x}/{y}.json` | `application/json` | no | `max-age=300` |
| 7 | `GET /{city}/chips/{event_id}_{pre\|post}.png` | `image/png` | no | `immutable` |
| 8 | `GET /global-scale.json` | `application/json` | no | `max-age=60` |

`{date}` is `YYYY-MM-DD` and **must** appear in `meta.json.dates`. `{z}` ∈ 8…14 (see §3).
Nothing else is served; anything else 404s.

### Response headers (all routes)

```
Access-Control-Allow-Origin: *
X-Contract-Version: 1.0
```

PMTiles **must** answer `Accept-Ranges: bytes` and honour `Range: bytes=a-b` with
`206 Partial Content` + `Content-Range`. PMTiles clients fetch the 16 KiB header, then byte ranges;
a 200-with-full-body server still works but blows the mobile payload budget. `HEAD` is supported
on every route. `Content-Encoding` is not negotiated — artefacts are pre-compressed where useful
(PMTiles internal gzip).

### Errors

Non-existent artefact → `404` with a JSON body (not an HTML error page):

```json
{ "error": { "code": "not_found", "detail": "malaga/vel.pmtiles" } }
```

`code` ∈ `not_found` | `bad_range` | `city_not_found`. Dates outside `meta.dates` are a client
error: the slider is built from `meta.dates`, so it cannot request one.

---

## 2. `meta.json` (route 1)

```jsonc
{
  "contract_version": "1.0",
  "city": "mexico-city",
  "name": "Mexico City, MX",
  "bbox": [-99.35, 19.20, -98.85, 19.65],      // WGS84 lon/lat, [W,S,E,N]
  "crs": "EPSG:32614",                          // projected CRS of the source rasters
  "reference_point": [-99.13, 19.43],           // lon/lat of the InSAR reference pixel
  "dates": ["2026-01-12", "2026-01-24"],         // ASC, YYYY-MM-DD, no gaps guarantee
  "revisit_days": 12,                            // 12 = NISAR, 6 = Sentinel-1: NEVER pool
  "provenance": {
    "source": "OPERA DISP-S1",                   // or "NISAR GUNW" | "HyP3 S1" | "EGMS" | "MOCK"
    "processing": "ISCE2+SNAPHU > MintPy",
    "atmosphere": "none | GACOS | ERA5",
    "retrieved_utc": "2026-11-14T16:02:00Z",
    "reference": "Herrera-García et al. 2021, doi:10.1126/science.abb8549"
  },
  "layers": {
    "velocity":    { "src": "vel.pmtiles",  "unit": "mm/yr", "range": [-450, 50],
                     "quant": { "type": "int16", "scale": 0.0305, "offset": -450 },
                     "provenance": "OPERA DISP-S1 2015-2020, 12-day" },
    "coherence":   { "src": "coh.pmtiles",  "unit": "-", "range": [0, 1],
                     "quant": { "type": "uint8", "scale": 0.00392, "offset": 0 } },
    "backscatter": { "src": "bs/{date}.pmtiles", "unit": "dB", "range": [-25, 5],
                     "quant": { "type": "int16", "scale": 0.000458, "offset": -25 } },
    "change":      { "src": "chg.pmtiles", "unit": "-", "range": [0, 1],
                     "quant": { "type": "uint8", "scale": 0.00392, "offset": 0 } },
    "events":      { "src": "events.pmtiles", "geom": "polygon",
                     "thresholds": { "k_gamma": 2.5, "min_ddB": 3.0, "mmu_m2": 500 } }
  },
  "series": "series/{z}/{x}/{y}.json",
  "cell": { "scheme": "h3", "resolution": 9, "approx_edge_m": 200 },
  "stats": {
    "median_vel": -128.0, "p05_vel": -410.0, "p95_vel": 12.0,
    "compaction_m3_yr": 6.0e7, "n_events": 412, "n_cells": 18240,
    "detection_limit_mm_yr": 1.5          // required when the city is a null result
  }
}
```

Rules:
- `layers.*.src` is **relative to `/{city}/`**; the client joins, it never hardcodes paths.
  The one exception is `bs/{date}.pmtiles`, whose `{date}` comes from `dates`.
- `range` is authoritative for both the legend and de-quantisation. A `null` range means the
  layer is not published for this city — the client hides it. `detection_limit_mm_yr` is required
  whenever `|median_vel| < 5` (Málaga is a declared null result: its `|median| < 5 mm/yr` makes the
  detection bound the result — `docs/data-contract-v1.md` §5).
- Every published layer carries `provenance`. Judges ask; the badge reads it.
- `revisit_days` propagates to the coherence legend; coherence is **not** comparable across sensors.

### De-quantisation

```
value = q * quant.scale + quant.offset        # q is the integer tile property
```

`scale = (range[1] - range[0]) / ((1 << bits) - 1)`, `offset = range[0]`.
Tiles carry **integers only** for raster-backed fields; this keeps the legend honest and the
payload small. Vector float properties (event attributes) are exempt.

---

## 3. PMTiles artefact spec (routes 2–5)

PMTiles v3, Web Mercator XYZ (EPSG:3857), gzip-compressed MVT, zoom range **8–14**
(`z8` overview, `z14` ≈ 2.4 m/px at the equator, i.e. finer than any source posting).

| File | Layer name | Geometry | Properties |
|---|---|---|---|
| `vel.pmtiles` | `cells` | Polygon (H3 cell) | `cell` (string), `v` (int16, quantised velocity) |
| `coh.pmtiles` | `cells` | Polygon (H3 cell) | `cell`, `c` (uint8, quantised coherence) |
| `bs/{date}.pmtiles` | `cells` | Polygon (H3 cell) | `cell`, `db` (int16, quantised backscatter) |
| `events.pmtiles` | `events` | Polygon (event footprint) | see §4 |

`cells` uses the geometry from `meta.json.cell` (h3 res 9 default). One feature per cell; a cell
absent from the tile has no data (nodata), and the client renders nodata as transparent — **never
as zero**, because zero is a legitimate velocity in Málaga and that distinction is the point.

`events.pmtiles` is vector-only and small; it is loaded once and filtered client-side.

### `bs/{date}.pmtiles` is a stack, not a single file

One file per date in `meta.dates`, same tiling, same layer name. The slider swaps the source
(`pmtiles://{city}/bs/2026-02-05.pmtiles`). Pre-tiled = no client-side raster work mid-demo.

---

## 4. `events.pmtiles` property table (route 5)

One feature per detected change polygon.

```jsonc
{
  "id": "mex-00312",              // string, unique, ⇒ chips/{id}_pre.png
  "type": "construction",         // construction|demolition|flood|tillage|veg_change|densification|stabilisation
  "area_m2": 1840,                // number
  "centroid": [-99.0412, 19.5127],// lon/lat, duplicated for popup + table view
  "first_date": "2026-02-05",
  "last_date": "2026-03-17",
  "n_dates": 3,
  "d_gamma": -0.42,               // Δγ, negative = coherence loss
  "d_amp_db": 5.8,                // Δ amplitude in dB
  "confidence": 0.81,             // 0..1
  "landcover": "urban",
  "buildings_overlap": 2,
  "series_cell": [12, 1145, 1650] // [z,x,y] → feed route 6 verbatim
}
```

`type` is generated from the Δγ/ΔdB table in README §4 — the client never re-derives it.
`series_cell` makes the popup's mini-chart a single fetch of an existing route; no new maths in
the browser.

---

## 5. `series/{z}/{x}/{y}.json` (route 6)

Aggregate of every data cell inside the XYZ tile `z/x/y` (Web Mercator, same grid as §3).
This is the click → time-series path: the browser converts a click to `z/x/y`, fetches once.

```jsonc
{
  "city": "mexico-city",
  "tile": [12, 1145, 1650],
  "cell": [12, 1145, 1650],       // = tile; kept for symmetry with events.series_cell
  "n_cells": 42,                  // how many source cells contributed
  "n_pixels": 842,                // how many source pixels (≈ cells × pixels-per-cell)
  "dates": ["2026-01-12", "2026-01-24", "2026-02-05"],
  "displacement_mm": [0, -3.1, -7.4],   // cumulative, mm, LOS→vertical, referenced
  "coherence": [0.82, 0.79, 0.81],      // same length as dates
  "velocity_mm_yr": -128.0,
  "fit": { "trend_mm_yr": -128.0, "seasonal_amp_mm": 9.4, "r2": 0.91 },
  "regime": "inelastic",          // inelastic | elastic | transitional
  "quality": "good"               // good | fair | poor   (poor ⇒ render dashed + tooltip)
}
```

`dates` **must** be a subset of `meta.dates` and `displacement_mm`/`coherence` must be exactly
`len(dates)` long. Empty tiles (ocean, no data) return `404` — the client shows the
"no coverage here" state, which is honest and demo-safe.

Only tiles containing data are emitted. The build writes a `series/index.json` (non-contract,
debug only) listing emitted `z/x/y`, so pozo can assert "every `series_cell` in events.pmtiles
resolves".

---

## 6. `global-scale.json` (route 8)

One shared legend across all cities, so Málaga's flatness is visibly flat next to CDMX.

```jsonc
{
  "contract_version": "1.0",
  "mode": "shared",
  "velocity": { "unit": "mm/yr", "range": [-450, 50],
                "breaks": [-300, -150, -75, -25, -5, 5],
                "colors": ["#3b0f70", "#8c2981", "#de4968", "#fe9f6d", "#fde725", "#7f7f7f"],
                "reverse_for_subsidence": true },
  "coherence": { "unit": "-", "range": [0, 1],
                 "breaks": [0.2, 0.4, 0.6, 0.8],
                 "colors": ["#0b0b0b", "#3b0f70", "#28738a", "#7ad151"] },
  "cities": {
    "mexico-city": { "median_vel": -128.0, "range": [-450, 50] },
    "jakarta":     { "median_vel": -75.0,  "range": [-320, 40] },
    "malaga":      { "median_vel": -1.2,   "range": [-8, 8] }
  },
  "note": "Coherence thresholds are per-sensor (revisit_days); never pool NISAR 12-day with S1 6-day."
}
```

`cities.*.range` is what the *independent* legend toggle uses. Colours come from yuls (layer
styles are JSON config, never UI code, per the non-overlap rule).

---

## 7. Build & verify

**Target layout only — none of these paths exist in the MVP tree except the generator.** The real
generator today is `pipeline/generate_mock.py`; `pipeline/emit/*`, `serve/build.py`, `serve/dev.py`
and `serve/check_contract.py` belong to the post-MVP static/PMTiles migration and must be built
before the routes in §1 can be served.

```
pipeline/generate_mock.py        → data/processed/{case}/…            (current mock generator)
pipeline/emit/*                  → same layout, real data             (target, H2+)
serve/build.py                   → app/public/tiles/                   (target, copy + validate)
serve/dev.py                     → http://localhost:8000 with Range   (target, local demo)
serve/check_contract.py          → asserts every route in §1            (target, CI gate)
```

Once built, `serve/check_contract.py` is the definition of done for pozo: it walks §1 for all
cities, checks MIME, `Accept-Ranges`, a 206 on `bytes=0-99`, JSON schema keys, `dates` ⊆ `meta.dates`,
every `events.series_cell` resolving, and PMTiles magic `PMTiles\x03`. Non-zero exit fails CI.

---

## 8. Conformance checklist

- [ ] every route in §1 answers with the MIME in §1 and `X-Contract-Version: 1.0`
- [ ] PMTiles answers `Accept-Ranges: bytes` and a valid `206`/`Content-Range`
- [ ] `meta.json` validates against §2, incl. `contract_version`, `provenance`, `revisit_days`
- [ ] every `layers.*.src` resolves; `{date}` placeholders match `meta.dates` exactly
- [ ] nodata cells are absent, not zero
- [ ] `series` responses satisfy the length invariant in §5
- [ ] `events.pmtiles` features carry every property in §4
- [ ] `global-scale.json` ranges cover every city's published range
- [ ] `serve/check_contract.py` exits 0
