# Offline pipeline — what runs before the app

Status: 2026-11-14, contract v1.0 (`049376b0…`). Scope: `pipeline/` only. Facts come from `README.md`
§4–§6/§13, `docs/data-contract-v1.md`, `docs/data-source-decision.md`, `docs/nisar-gunw-layout.md`,
`docs/handoff-mock.md`, `config/study_cases.yaml` and the modules below.

## 1. What it does, and what it does not

**Does:** turn SAR data (or a synthetic stand-in) into the frozen artefacts — H3 res-9 cells, cumulative mm, mm/yr trend, coherence, land use, risk band, provenance — and refuse to publish a tree that fails the contract.

**Does not:** run Streamlit, process on request, or fetch anything while artefacts are written; `src/domain/contracts.py` imports no Streamlit/geopandas/pydeck (its hard rule 1) and the app never computes a geospatial quantity (README §3).

**The committed artefacts are mock:** `meta.product == "mock"` and `meta.provenance.source` says mock
in all three cases. The real path (`pipeline/process_case.py`) is implemented and tested against
synthetic HDF5 fixtures but **has never been run on a downloaded granule** — no Earthdata credentials
here (`docs/data-source-decision.md`, "Still open"); nothing in `data/processed/` claims a real product.
Product: NISAR L2 GUNW first; **OPERA DISP-S1 is excluded** — 0 granules for all three AOIs, probed
2026-11-14 (`docs/data-source-decision.md`), so no artefact may carry it even though the enum keeps the name.

## 2. The four artefacts

Per case under `data/processed/<case>/` (contract §1 lists the flat form; `pipeline/validate_artifacts.py` accepts both). Field detail lives in `docs/data-contract-v1.md`; validity is decided by `src/domain/contracts.py`, which prevails when the prose disagrees.

| File | Contents, one line | Governed by |
|---|---|---|
| `nisar_grid.parquet` | one row per H3 res-9 cell (~200 m): lat/lon, `val_t1`/`val_t2`/`delta_change` (cumulative mm), `velocity_mm_yr`, coherence, land use, risk band, provenance | contract §3 |
| `timeseries.csv` | cumulative mm per date; `cell_id == "aoi"` is the case aggregate, H3 rows are opt-in drill-down | contract §4 |
| `context_layers.geojson` | FeatureCollection, EPSG:4326 declared per feature: `land_use`, `infrastructure`, `water`, `admin_boundary` | contract §6 |
| `meta.json` | dates, `revisit_days`, `thresholds`, `provenance`, `stats`, `limitations`, `null_policy` | contract §5 |

## 3. Stages

`done` = the file exists **and** a test module exercises it. "Hermetic" = the test builds its own input (synthetic HDF5, in-memory frame): evidence the code does what it says, not that real data behaves that way.

| Stage | Module | Status | Command |
|---|---|---|---|
| Ingest: availability probe | `pipeline/ingest/02_search_download.py search\|report` | done (live run 2026-11-14 wrote `_availability/*.json`; parsing/guards in `test_download_guard.py`) | §4 |
| Ingest: download guard | same, `download` | done, hermetic (`test_download_guard.py`, `test_download_auth.py`); never run against Earthdata | §4 |
| Granule inspection | `pipeline/ingest/inspect_hdf5.py` | done, hermetic (`test_inspect_hdf5.py`) | §4 |
| HDF5 read (layers, geometry, mask) | `pipeline/ingest/nisar_gunw.py` | done, hermetic (`test_nisar_gunw.py`); layout from docs, not a real 2026 file | §4 |
| Phase → LOS displacement | `process_case.py` + `science/transforms.py` | done, hermetic (`test_process_case.py`) | §4 |
| LOS → vertical | `transforms.los_to_vertical` | done (`test_science_transforms.py`); one geometry, `v_north = 0` | §4 |
| Asc/desc joint inversion | — | **planned**: mixed flight direction is refused (`process_case.py`), not solved | — |
| H3 res-9 aggregation | `transforms.h3_aggregate` | done (`test_science_transforms.py`) | §4 |
| Trend + seasonal fit | `transforms.harmonic_fit` | done, gated: below `MIN_SEASONAL_COVERAGE_PER_HARMONIC` it returns the trend but marks the amplitude unpublishable | §4 |
| Context join | `pipeline/enrich_context.py` | **partially wired**: writes a `land_use.csv` sidecar, never rewrites the grid (docstring says why); fold in via `process_case --land-use-csv` | CLI only |
| Artefact emission | `mock/artifacts.py` writers; `generate_mock.py` (mock), `process_case.py` (real) | done (`test_mock_expectations.py`, `test_process_case.py`) | §4 |
| Validation gate | `validate_artifacts.py` + `src/domain/contracts.py` | done (`test_validate_artifacts.py`, `test_contracts.py`); fail-closed | §4 |

## 4. How to run it

Always through `./scripts/python.sh` (venv plus the Nix `libstdc++`/`libz` path), never `.venv/bin/python`.

```bash
# 1 regenerate the mock (seed 7, all cases, 1.61 MB total)
./scripts/python.sh pipeline/generate_mock.py
# 2 validate the artefacts on disk (fail-closed: zero cases is a FAIL)
./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed
# 3 ask ASF what exists (unauthenticated), then read it as a table
./scripts/python.sh pipeline/ingest/02_search_download.py search
./scripts/python.sh pipeline/ingest/02_search_download.py report
# 4 dry-run a download: identity/budget plan, no credentials, no writes
./scripts/python.sh pipeline/ingest/02_search_download.py download \
    --city malaga --source nisar_gunw --max-gb 2 --dry-run
# 5 inspect a granule before trusting any path in it
./scripts/python.sh pipeline/ingest/inspect_hdf5.py data/raw/malaga/nisar_gunw/<granule>.h5 --json
./scripts/python.sh pipeline/ingest/nisar_gunw.py data/raw/malaga/nisar_gunw/<granule>.h5 --json
# 6 process a stack (one --granule per pair; --out required; --force overwrites a mock tree)
./scripts/python.sh pipeline/process_case.py --case mexico-city \
    --granule data/raw/mexico-city/nisar_gunw/<granule-a>.h5 \
    --granule data/raw/mexico-city/nisar_gunw/<granule-b>.h5 \
    --out data/processed/mexico-city
# 7 the gate: contract self-check → Phase A suites → artefact gate → handoff summary
./scripts/check_phase_a.sh
```

Step 6 refuses a directory whose `meta.json` says `product == "mock"` unless `--force` is given. Step 7 exits 0 only if all four sub-steps pass; on 2026-11-14 it printed `PHASE A: PASS`, 3 cases, 1.610 MB.

## 5. Provenance chain

`provenance_id = {case_id}+{product}+{YYYYMMDD}+{sha256_8}`, built by `contracts.provenance_id()`. The
same string must appear in every grid row, every `timeseries.csv` row, every context feature's
`properties` and `meta.provenance.provenance_id`; `PROVENANCE_ID_RE` rejects any other shape and the
gate checks the ids resolve across artefacts. `meta.provenance.method` may not be empty (contract §5)
— it is the physical claim a reviewer reads: the real chain names the LOS sign convention, the assumed
`v_north`, wavelength source, incidence/heading source, reference pixel and the H3 mean aggregation
(`process_case._method_text`), and the mock names its synthetic field model. Discrepancy: contract §8 calls
the digest "the hash of the input artefact", but the mock hashes `case|product|seed` and the real path
hashes sorted granule file stems — neither hashes file bytes.

## 6. Numbers a sceptic can reproduce

`docs/nisar-gunw-layout.md` §4, λ_L = 0.2384 m, θ = 39°, recomputed through `pipeline/science/transforms.py` on 2026-11-14:

| quantity | LOS | vertical |
|---|---:|---:|
| 1 radian of phase | 18.97 mm | **24.41 mm** |
| one fringe (2π) | 119.2 mm | **153.4 mm** |

Reproduce it: `./scripts/python.sh -c "import math; from pipeline.science import transforms as V; print(round(V.vertical_from_los(0.2384/(4*math.pi)*1e3,39,192),2))"` → `24.41`.

Mexico City at −420 mm/yr is −13.8 mm vertical in a 12-day pair, ≈ 0.09 fringe: unwrapping error, not noise, dominates (`docs/nisar-gunw-layout.md` §4, README §13).

Mock headline numbers (`docs/handoff-mock.md` §1, re-read from disk and reprinted by `./scripts/check_phase_a.sh`; seed 7, 12 dates 2026-07-02 → 2026-11-11, product `mock`):

| case | cells | median mm/yr | p05 | size |
|---|---:|---:|---:|---:|
| `mexico-city` | 13,379 | −54.7 | −223.3 | 1.01 MB |
| `jakarta` | 4,855 | −25.8 | −143.9 | 0.38 MB |
| `malaga` | 2,677 | −1.4 | −3.5 | 0.22 MB |

Coherence 0.45–0.95 and 6 `land_use` classes in every case; water cells are absent, not zero. Regenerating into an empty directory reproduces all twelve files byte-identically (`cmp`, 2026-11-14). Málaga's `|median| < 5 mm/yr` forces `stats.detection_limit_mm_yr = 1.5`: the bound, not "no data", is the result.

## 7. Known limits and unverified parts

1. **NISAR urban coverage unverified**: GUNW is provisional and cryosphere-derived, and intersecting is not covering (`docs/data-source-decision.md`; its checklist has not been run).
2. **Dataset names confirmed only against published documentation**, not a real 2026 granule: x/y dataset names, CRS location, fill convention and 80 m vs 20 m coherence stay open (`docs/nisar-gunw-layout.md` status line, §5). The first granule must correct that page.
3. **80 m posting**: city scale, not building scale (README §13.3, contract §7.3).
4. **A 132-day stack cannot separate seasonality from trend**: `harmonic_fit` needs 0.75 yr of coverage for the annual term and sets `seasonal_identifiable=False` below it, so `seasonal_amp_mm` must not be published from a window like ours (`science/transforms.py`).
5. **Sign and heading conventions need GNSS validation**: ISCE2, MintPy and SNAP disagree on azimuth origin and on which LOS direction is positive (README §6, `transforms.los_to_vertical`); target < 5 mm/yr RMSE (README §13). `process_case` writes "NOT yet validated against GNSS" into `provenance.method` on purpose.
6. **One geometry only**: `v_north ≈ 0` is an assumption, residual `tanθ·sinα·v_north`, east unconstrained, no asc/desc pairing (`transforms.los_to_vertical`).
7. **Nothing has run on real data**: no download (no credentials), no real granule read, no coverage number; real-path evidence is synthetic fixtures only.
8. **Sentinel-1 counts are page caps**: the probe requests 2019→2035 with `maxResults=250`, so "250+ granules" is a floor and returned spans are ~12 months (Málaga 2025-10-18 → 2026-11-08), not 5 years.

## 8. Where to look next

- `src/domain/contracts.py` — schema, enums, per-product thresholds, validators; wins over prose.
- `docs/data-contract-v1.md` — normative description of the four artefacts and the null policy.
- `pipeline/science/transforms.py` — every formula, each tied to a README §6 step, plus the identifiability gates.
- `docs/data-source-decision.md` — why this product, what would drop it, the untested checklist.
