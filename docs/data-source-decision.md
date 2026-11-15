# Data-source decision record

**Status:** decided for the MVP, revisable if NISAR urban coverage fails verification.
**Date:** 2026-11-14 · **Owner:** Rol 3 (yuls) · **Consumers:** `pipeline/`, `meta.provenance`.

This is the answer to Rol 3 task 2 in the flujo doc — *"elegir y documentar la fuente utilizada"*.
It exists so that no artefact can claim a product we did not verify, and so the provenance line in
the app has something to point at.

---

## Question

Which SAR product do we actually process for Mexico City, Jakarta and Málaga, given that we have
~48 hours and no guarantee of NISAR urban coverage?

## Evidence

Reproduce with:

```bash
./scripts/python.sh pipeline/ingest/02_search_download.py search
```

Raw JSON: `data/processed/_availability/{case}.json` (probe run 2026-11-14, unauthenticated ASF
SearchAPI). The query footprint is each case's declared **bbox envelope** — `asf_search(aoi["bbox"], …)`
over `pipeline/ingest/aois.json`, mirrored from `config/study_cases.yaml` and recorded as `aoi_bbox`
in every report — not the measurement-domain polygon in `config/aoi/{case}.geojson`
(1,498 / 748 / 394 km²). Every count below is therefore *granules intersecting the envelope*: see
fact 2 below.

| Source | Mexico City | Jakarta | Málaga |
|---|---|---|---|
| NISAR GUNW | 69 granules · 2025-10-25 → 2026-10-08 | 25 granules · 2026-06-26 → 2026-10-01 | 26 granules · 2026-06-20 → 2026-10-06 |
| OPERA DISP-S1 | **0** | **0** | **0** |
| Sentinel-1 SLC (5 y query window) | 250 granules, truncated · size in `sources.sentinel1_slc.total_gb` | 250 granules, truncated · size in `sources.sentinel1_slc.total_gb` | 250 granules, truncated · size in `sources.sentinel1_slc.total_gb` |
| EGMS | not eligible (non-EU) | not eligible (non-EU) | eligible, manual portal download |

Read the Sentinel-1 row from the artefact, not from a remembered terabyte figure: `n_granules` is
capped at 250 per query (`n_granules_truncated: true`), so neither the count nor `total_gb` is a
full 5-year archive total, and `total_gb` is summed decimal MB divided by 1024
(`pipeline/ingest/02_search_download.py`), not bytes/1e9. Per-case granule counts are restated in
the contract's product section, `docs/data-contract-v1.md` §7.3.

Two facts drive the decision:

1. **OPERA DISP-S1 does not exist for any of our AOIs.** The older README example metadata and the
   flujo doc's example both said `opera-disp-s1`; that was wrong for these three cities. The enum
   keeps the value (`contracts.PRODUCTS`), its `SENSOR_PROFILES` entry records it as absent here,
   and no artefact for these AOIs may ship it.
2. **NISAR GUNW exists, but intersecting is not covering.** GUNW is provisional and
   cryosphere-derived (SME2 excludes urban areas). A granule whose footprint touches our bbox can
   still be useless over the city we want to show.

## Options considered

| Option | Verdict |
|---|---|
| **NISAR GUNW** | **First choice**, conditional on the verification below. One product, one processing path, and it is the product the challenge is about. |
| **OPERA DISP-S1** | Rejected — zero granules for all three AOIs. Not a fallback, just absent. |
| **HyP3 on-demand Sentinel-1** | **Fallback.** Always feasible (deep archive), already-inverted products, but 6-day revisit and C-band, so its coherence thresholds must not be pooled with NISAR's. Costs Earthdata credentials and processing time. |
| **EGMS** | **Málaga only**, by hand. Gives a ready velocity field, no amplitude/coherence. Good for cross-checking the negative control. |
| **Mock** | Explicitly allowed, must be labelled (`provenance.source` contains "mock"). Ships first so the app is never blocked. |

## Decision

1. `meta.product = "nisar-gunw"` for a case **only after** the verification below passes for that
   case. Otherwise `"hyp3-s1"`, otherwise `"mock"`. Never `"opera-disp-s1"`.
2. `meta.sensor = "nisar"` for GUNW, `"sentinel-1"` for HyP3/EGMS.
3. `meta.revisit_days` comes from `contracts.SENSOR_PROFILES` — 12 for NISAR, 6 for Sentinel-1 —
   and is never edited to make two layers look comparable.
4. Mock artefacts are produced first and replaced case by case; each replacement is a PR that also
   updates `provenance`, never a silent swap.

### AOI scope: the measurement domain is one city-scale polygon

The AOI is a scope decision, not a formatting detail, so it is recorded here.

1. **`config/aoi/{case}.geojson` is the MEASUREMENT domain** — exactly one `Polygon`, one closed
   exterior ring, no holes. That ring is what `pipeline/process_case.py` turns into the H3 cell set,
   and its cells are the denominator of the coverage bar in the checklist below.
2. **The metro exports are context only and are NOT adopted.** They were once committed by mistake;
   that is a regression class, so `tests/test_aoi_geometry.py` now fails if a MultiPolygon appears
   in `config/aoi/` (including under the retired `{case}-aoi.geojson` name the loader also accepts).
3. **Málaga's detached ~1.3 km² fragment is a DECLARED EXCLUSION.** The committed
   `config/aoi/malaga.geojson` is exactly the main part of that metro export; the tighter polygon
   removes only the detached fragment. It must never be merged back in, and never joined to the city
   by a fabricated corridor — that would put ground we never measured inside the domain.

Measured today, equal-area on the WGS84 ellipsoid (`pyproj.Geod`), ring vertices only:

| Case | Measurement domain | Metro export (context only, not committed) | Size ratio |
|---|---|---|---|
| `mexico-city` | 1 Polygon · 1,494.6 km² | MultiPolygon, 1 part · 7,865.4 km² | 5.3× |
| `jakarta` | 1 Polygon · 744.9 km² | MultiPolygon, 10 parts · 5,337.4 km² | 7.2× |
| `malaga` | 1 Polygon · 394.2 km² | MultiPolygon, 2 parts · 395.5 km² = 394.2 + a detached 1.33 (0.34 %) | 1.003× |

Two reasons settle it:

1. **Single-ring pipeline constraint.** `process_case.py` builds the cell set from one ring
   (`h3.LatLngPoly(cfg.aoi_ring)`), so a MultiPolygon would need multipart support end-to-end before
   it could be an AOI at all.
2. **The coverage bar.** Measured on the AOI denominator, coverage is 54.01 % / 57.18 % / 66.00 %
   (Mexico City / Jakarta / Málaga) — all three clear the 50 % bar. A 5.3–7.2× larger envelope
   dilutes NISAR swath overlap and pushes that bar out of reach: the bbox envelope alone already
   gives 52.30 % / 48.85 % / 36.32 %, below the 50 % bar for Jakarta and Málaga. The bbox figure is
   the envelope of the AOI and is never coverage of the city.

Same polygons, different area formula: the loader's equirectangular estimate quoted in §Evidence
reads 1,498 / 748 / 394 km², under 0.5 % from the figures above. Neither is a survey measurement;
the guard polices a silent swap, not precision.

## Verification checklist for GUNW before it may be labelled `nisar-gunw`

Run per case, record the outcome in `meta.provenance.method` or `meta.limitations`:

- [ ] Open the granule with `h5py`; confirm the unwrapped interferogram, coherence and
      connected-components datasets are present and non-empty (paths in `docs/nisar-gunw-layout.md`).
- [ ] Compute the fraction of the AOI's H3 cells that receive a value. **< 50 % ⇒ do not claim
      city-wide displacement**; drop to the fallback for that case.
- [ ] Check the coherence distribution over the AOI: a median below the profile's
      `coherence_min` means the interferogram is not usable at this posting.
- [ ] Confirm the pair's acquisition dates land inside the case window in
      `config/study_cases.yaml`.
- [ ] Note whether the granule is flagged provisional (it will be) and surface that in
      `meta.limitations` — judges ask, and overclaiming is the one unrecoverable mistake.

## Consequences

- Coherence thresholds and change-detection parameters are **per sensor/product**; the contract
  refuses to store a pooled number.
- GUNW's 80 m posting means city scale, not building scale — this is a limitation, not a bug.
- Málaga's null result may come from NISAR *or* EGMS; whichever is used must be named, and the
  detection limit published either way.
- If GUNW verification fails for the primary case, the demo is HyP3-on-Sentinel-1 **or** mock with
  an honest badge. It is never a mislabelled artefact.

## Still open

- Earthdata credentials (`EARTHDATA_USER` / `EARTHDATA_PASS`, or a `machine urs.earthdata.nasa.gov`
  line in `~/.netrc`) plus the ASF application approval are not configured on this machine; the
  download step fails closed with exit `3` (`RC_CREDENTIALS`) until they are.
- Per-case download budget: the CLI default is **2 GB** (`--max-gb 2.0`); any larger cap is a
  deliberate operator choice. GUNW sizes are unknown until `--probe-sizes`, so probe before pulling
  a whole stack; a known over-budget plan fetches nothing and exits `5` (`RC_BUDGET_GUARD`).
- Primary case stays `mexico-city` (strongest signal) with `malaga` as the negative control — flip
  the one line in `config/study_cases.yaml` if coverage fails.
