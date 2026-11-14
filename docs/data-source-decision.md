# Data-source decision record

**Status:** decided for the MVP, revisable if NISAR urban coverage fails verification.
**Date:** 2026-11-14 · **Owner:** Rol 3 (yuls) · **Consumers:** `pipeline/`, `meta.provenance`.

This is the answer to Rol 3 task 2 in the flujo doc — *"elegir y documentar la fuente utilizada"*.
It exists so that no artefact can claim a product we did not verify, and so the provenance badge in
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
SearchAPI, AOI = the authoritative `config/aoi/{case}.geojson` polygon — 1,498 / 748 / 394 km²).

| Source | Mexico City | Jakarta | Málaga |
|---|---|---|---|
| NISAR GUNW | 69 granules · 2025-10-25 → 2026-10-08 | 25 granules · 2026-06-26 → 2026-10-01 | 26 granules · 2026-06-20 → 2026-10-06 |
| OPERA DISP-S1 | **0** | **0** | **0** |
| Sentinel-1 SLC (5 y) | 250+ granules · ~1.07 TB | 250+ granules · ~1.18 TB | 250+ granules · ~0.96 TB |
| EGMS | not eligible (non-EU) | not eligible (non-EU) | eligible, manual portal download |

Two facts drive the decision:

1. **OPERA DISP-S1 does not exist for any of our AOIs.** The older README example metadata and the
   flujo doc's example both said `opera-disp-s1`; that was wrong for these three cities and is now
   excluded by the contract's product enum.
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

- Earthdata credentials (`EARTHDATA_USER` / `EARTHDATA_PASS`) plus the ASF application approval are
  not configured on this machine; the download step exits 3 until they are.
- Per-case download budget: 6 GB default guard for a GUNW stack. Check `sizeMB` before pulling a
  whole stack; a plan over budget fetches nothing.
- Primary case stays `mexico-city` (strongest signal) with `malaga` as the negative control — flip
  the one line in `config/study_cases.yaml` if coverage fails.
