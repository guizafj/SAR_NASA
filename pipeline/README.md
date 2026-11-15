# Offline pipeline — what runs before the app

Status: 2026-11-15, contract v1.1, fingerprint `ed92be80…` (bumped 2026-11-15 from contract v1.0 — O-05 in `docs/decisions.md`).
The bump moved the contract fingerprint: re-derive it with
`./scripts/python.sh -m src.domain.contracts` instead of trusting a remembered hash (the abbreviated
prefix above is a convenience; the command is the authority).
Scope: `pipeline/` only. Facts come from `README.md`
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

Per case under `data/processed/<case>/` (contract §1 names `data/processed/{case_id}/…`; `pipeline/validate_artifacts.py` accepts both the per-case and flat layouts). Field detail lives in `docs/data-contract-v1.md`; validity is decided by `src/domain/contracts.py`, which prevails when the prose disagrees.

| File | Contents, one line | Governed by |
|---|---|---|
| `nisar_grid.parquet` | one row per H3 res-9 cell (~200 m): lat/lon, `val_t1`/`val_t2`/`delta_change` (cumulative mm), `velocity_mm_yr`, coherence, land use, risk band, provenance; contract v1.1 adds the fitted `seasonal_amp_mm`, `ratio_R` and `regime` | contract §3 |
| `timeseries.csv` | cumulative mm per date; `cell_id == "aoi"` is the case aggregate, H3 rows are opt-in drill-down | contract §4 |
| `context_layers.geojson` | FeatureCollection, EPSG:4326 declared per feature: `land_use`, `infrastructure`, `water`, `admin_boundary` | contract §6 |
| `meta.json` | dates, `revisit_days`, `thresholds`, `provenance`, `stats`, `limitations`, `null_policy` | contract §5 |

Las capas de contexto siguen la misma regla que el grid al que anotan. `land_use` ya no sale de una
regla elíptica: el centroide de cada celda se une a los polígonos reales de OpenStreetMap del caso
(`pipeline/enrich_context.py::join_land_use`, precedencia
`water > infrastructure > industrial > urban-dense > urban-residential > vegetation > bare >
unknown`, desempate por área menor y después por digest de geometría) y las etiquetas por celda se
leen del pin commiteado `config/context/<case>.json`, de modo que regenerar no necesita `data/raw/`.
`water` es la única clase derivada de una geometría que no contiene centroides — una costa es una
línea —: `pipeline/context.py::sea_polygons` la poligoniza contra el anillo del bbox y se queda el
candidato que los polígonos de land use mapeados cubren en ≤ 5 % de su superficie, así que una celda
dentro de ese mar derivado se etiqueta `water`; el agua mapeada entra por el join normal (México no
tiene costa en el extracto y su pin etiqueta 422 celdas `water` desde cuerpos de agua). `urban-dense` es el tejido de
servicio, comercial e institucional por semántica del tag de OSM, no un umbral de densidad: un corte
por fracción construida por celda se midió y se descartó porque la distribución es un continuo (las
cifras están en `docs/context-sources.md` §4). Una celda que ningún polígono mapeado contiene —o que
está dentro de uno cuyo tag no mapea a ninguna clase— se
publica `unknown`, sin extrapolar, y su recuento y su proporción viajan en `meta.limitations`.

Los polígonos `land_use` del artefacto son la unión de las mismas celdas que publica el grid
(`pipeline/mock/artifacts.py::build_context`), así que la capa y la columna no pueden discrepar; los
polígonos crudos de OSM no se publican porque no caben en el presupuesto de tamaño del artefacto (la
medida está en el docstring de `build_context`), y las features mapeadas se publican con tope por tipo
de geometría (`FEATURE_CAP_PER_KIND = 150`; `ADMIN_FEATURE_CAP = 50` por tipo en `admin_boundary`),
aplicado al escribir el pin. La feature `admin_boundary/aoi` es el anillo real de
`config/aoi/<case>.geojson`, no la elipse de cobertura: la elipse sigue decidiendo qué celdas existen.
Primero se prueba el pin commiteado de cada caso (`config/context/<case>.json`, los tres en
`git ls-files`), así que un clon sin `data/raw/` regenera desde él. Sin pin y sin extracto se activa
el fallback sintético declarado, y entonces cada feature de contexto dice
`mock (synthetic …)` y `meta.limitations` dice que la geografía es inventada. Las dos geografías son
mutuamente excluyentes en el escritor — un único booleano (`context.synthetic`) elige una y
`meta.limitations` publica una sola de las dos frases —, y la compuerta posterior a la escritura
reconstruye la compuerta de calidad desde `meta.provenance.mock_seed` (celdas y coherencia por celda);
la frase de `meta.limitations` no se re-deriva de esa reconstrucción, así que sustituirla a mano no la
pone roja.

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
| Contexto OSM: extracto → pin | `pipeline/ingest/fetch_context.py` → `pipeline/ingest/pin_context.py` → `pipeline/context.py` | done: el extracto se cachea en crudo (gitignored) y se fija en el pin commiteado de cada caso; `load_context` lo prefiere al extracto y `label_cells` deja en `unknown` lo que el pin no nombra. Hermetic (`test_fetch_context.py`, `test_context_pin.py`, `test_context_pin_reproducibility.py`, `test_context_order_reproducibility.py`, `test_sea_and_coverage_gate.py`) | `docs/context-sources.md` §7 |
| Context join (CSV path) | `pipeline/enrich_context.py` | **partially wired**: writes a `land_use.csv` sidecar, never rewrites the grid (docstring says why); fold in via `process_case --land-use-csv` | CLI only |
| Artefact emission | `mock/artifacts.py` writers; `generate_mock.py` (mock), `process_case.py` (real) | done (`test_mock_expectations.py`, `test_process_case.py`) | §4 |
| Validation gate | `validate_artifacts.py` + `src/domain/contracts.py` | done (`test_validate_artifacts.py`, `test_contracts.py`); fail-closed | §4 |

## 4. How to run it

Always through `./scripts/python.sh` (venv plus the Nix `libstdc++`/`libz` path), never `.venv/bin/python`.

```bash
# 1 regenerate the mock (seed 7, all cases, 4.74 MB total)
./scripts/python.sh pipeline/generate_mock.py
# 2 validate the artefacts on disk (fail-closed: zero cases is a FAIL)
./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed
# 3 ask ASF what exists (unauthenticated), then read it as a table
./scripts/python.sh pipeline/ingest/02_search_download.py search
./scripts/python.sh pipeline/ingest/02_search_download.py report
# 4 dry-run a download: identity/budget plan, no credentials, no writes
./scripts/python.sh pipeline/ingest/02_search_download.py download \
    --city malaga --source nisar_gunw --max-gb 2 --dry-run
# 4b one granule first: `--max-granules N` keeps the FIRST N in reference-date order (O-01),
#    which is cheaper than the whole stack when the reader is what you are verifying
./scripts/python.sh pipeline/ingest/02_search_download.py download \
    --city malaga --source nisar_gunw --max-granules 1 --dry-run
# 5 inspect a granule before trusting any path in it
./scripts/python.sh pipeline/ingest/inspect_hdf5.py data/raw/malaga/nisar_gunw/<granule>.h5 --json
./scripts/python.sh pipeline/ingest/nisar_gunw.py data/raw/malaga/nisar_gunw/<granule>.h5 --json
# 6 process a stack (one --granule per pair; --out required; --force overwrites a mock tree)
./scripts/python.sh pipeline/process_case.py --case mexico-city \
    --granule data/raw/mexico-city/nisar_gunw/<granule-a>.h5 \
    --granule data/raw/mexico-city/nisar_gunw/<granule-b>.h5 \
    --out data/processed/mexico-city --force
# 7 the gate: contract self-check → Phase A suites → artefact gate → handoff summary
./scripts/check_phase_a.sh
```

Step 6 refuses a directory whose `meta.json` says `product == "mock"` unless `--force` is given. Step 7 exits 0 only if all four sub-steps pass; on 2026-11-14 it printed `PHASE A: PASS`, 3 cases, 4.74 MB.

## 5. Provenance chain

`provenance_id = {case_id}+{product}+{YYYYMMDD}+{sha256_8}`, built by `contracts.provenance_id()`. The
same string must appear in every grid row, every `timeseries.csv` row, every context feature's
`properties` and `meta.provenance.provenance_id`; `PROVENANCE_ID_RE` rejects any other shape and the
gate checks the ids resolve across artefacts. `meta.provenance.method` may not be empty (contract §5)
— it is the physical claim a reviewer reads: the real chain names the LOS sign convention, the assumed
`v_north`, wavelength source, incidence/heading source, reference pixel and the H3 mean aggregation
(`process_case._method_text`), and the mock names its synthetic field model. Discrepancy: contract §8 calls
the digest "the hash of the input artefact", but the mock hashes `case|product|seed|window` (the
acquisition epochs, so two different stacks cannot share an id) and the real path
hashes sorted granule file stems — neither hashes file bytes.

## 6. Numbers a sceptic can reproduce

`docs/nisar-gunw-layout.md` §4, λ_L = 0.2384 m, θ = 39°, recomputed through `pipeline/science/transforms.py` on 2026-11-14:

| quantity | LOS | vertical |
|---|---:|---:|
| 1 radian of phase | 18.97 mm | **24.41 mm** |
| one fringe (2π) | 119.2 mm | **153.4 mm** |

Reproduce it: `./scripts/python.sh -c "import math; from pipeline.science import transforms as V; print(round(V.vertical_from_los(0.2384/(4*math.pi)*1e3,39,192),2))"` → `24.41`.

Mexico City's mock `peak_velocity_mm_yr` (config/study_cases.yaml — the model's peak parameter and not a measured cell) at −420 mm/yr is −13.8 mm vertical in a 12-day pair, ≈ 0.09 fringe: unwrapping error, not noise, dominates (`docs/nisar-gunw-layout.md` §4, README §13). The strongest grid cell measures −415.14 mm/yr — −13.6 mm vertical, ≈ 0.09 fringe over the same 12-day pair.

Mock headline numbers (`docs/handoff-mock.md` §1, re-read from disk and reprinted by `./scripts/check_phase_a.sh`; seed 7, 62 dates 2024-11-10 → 2026-11-12, product `mock`):

| case | cells | median mm/yr | p05 | size |
|---|---:|---:|---:|---:|
| `mexico-city` | 11,600 | −64.7 | −232.2 | 2.15 MB |
| `jakarta` | 4,936 | −25.9 | −144.0 | 1.51 MB |
| `malaga` | 2,758 | −1.3 | −3.4 | 1.08 MB |

Coherence 0.37–0.95 (0.47–0.95 in Mexico City) and 7 `land_use` classes in each of the three cases — Málaga spans 0.37–0.94 and Jakarta 0.45–0.95. That count is what survives the coherence filter, not the class list the extract carries: the pin assigns eight classes, water included (422 / 221 / 890 cells labelled `water`, in the table order), and no water cell is published — inside the cell set they are all below `coherence_min` 0.35 — so water cells are absent, not zero; `unknown` is published where no mapped polygon contains a cell or where the containing polygon's tag maps to no class. Regenerating into an empty directory from `config/context/` alone (the pin path, `data/raw/` never read) reproduces all twelve files byte-identically (`cmp`, 2026-11-15). Málaga's `|median| < 5 mm/yr` forces `stats.detection_limit_mm_yr = 1.5`: the bound, not "no data", is the result.

## 7. Known limits and unverified parts

1. **NISAR urban coverage unverified**: GUNW is provisional and cryosphere-derived, and intersecting is not covering (`docs/data-source-decision.md`; its checklist has not been run).
2. **Dataset names confirmed only against published documentation**, not a real 2026 granule: x/y dataset names, CRS location, fill convention and 80 m vs 20 m coherence stay open (`docs/nisar-gunw-layout.md` status line, §5). The first granule must correct that page.
3. **80 m posting**: city scale, not building scale (README §13.3, contract §7.3).
4. **Seasonality is identified on the current window; the old one is history**: the current 732-day stack spans annual coverage 2.004 yr (2.00 cycles against the 0.75 yr-per-harmonic gate) at design condition 4.12 (limit `MAX_DESIGN_CONDITION = 30`), so the annual term *is* separable from `[1, t]` and `seasonal_amp_mm`, `ratio_R` and `regime` are published as FIT results, not typed constants (`science/transforms.py`; the per-case medians are `stats.median_seasonal_amp_mm` 1.5 / 12.0 / 3.5 mm, `stats.median_ratio_R` 43.12 / 2.16 / 0.39 and `stats.dominant_regime` inelastic / inelastic / elastic, in the order of the table above). The gates were calibrated against the old 132-day window (0.36 annual cycles, design condition 66.8), where `harmonic_fit` set `seasonal_identifiable=False` and withheld the amplitude: that is why the gates exist, not a statement about the current tree (O-05, 2026-11-15). One caveat is unchanged: the mock is noise-free in time, so the fit recovers the injected amplitude exactly; a real 2-year stack would carry an uncertainty this artefact does not.
5. **Sign and heading conventions need GNSS validation**: ISCE2, MintPy and SNAP disagree on azimuth origin and on which LOS direction is positive (README §6, `transforms.los_to_vertical`); target < 5 mm/yr RMSE (README §13). `process_case` writes "NOT yet validated against GNSS" into `provenance.method` on purpose.
6. **One geometry only**: `v_north ≈ 0` is an assumption, residual `tanθ·sinα·v_north`, east unconstrained, no asc/desc pairing (`transforms.los_to_vertical`).
7. **Nothing has run on real data**: no download (no credentials), no real granule read, no coverage number; real-path evidence is synthetic fixtures only.
8. **Sentinel-1 counts are page caps**: the probe requests 2019→2035 with `maxResults=250`, so "250+ granules" is a floor: every case's result set truncates at that page, and the span the 250 records cover is whatever the date-sorted page returns — ~12 months (Málaga 2025-11-18 → 2026-11-13) where the archive samples the AOI sparsely, years where it samples densely (Jakarta 2023-05-10 → 2026-06-23, ~3.1 years). Neither is the ~16-year history the query window suggests, so this is not 5 years of coverage. The window slides with every probe, so treat those endpoints as an example rather than a fact about the archive.

## 8. Where to look next

- `src/domain/contracts.py` — schema, enums, per-product thresholds, validators; wins over prose.
- `docs/data-contract-v1.md` — normative description of the four artefacts and the null policy.
- `pipeline/science/transforms.py` — every formula, each tied to a README §6 step, plus the identifiability gates.
- `docs/data-source-decision.md` — why this product, what would drop it, the untested checklist.
