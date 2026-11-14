# First real NISAR L2 GUNW granule — operator runbook

Nothing here has run on a downloaded file. Tick top to bottom, stop at the first FAIL.
**L** = `docs/nisar-gunw-layout.md`, **D** = `docs/data-source-decision.md`, **C** = `config/study_cases.yaml`,
**K** = `src/domain/contracts.py`, **P** = `pipeline/process_case.py`, **T** = `pipeline/science/transforms.py`, **S** = `pipeline/ingest/02_search_download.py`.
Case = `mexico-city`. S's exit codes: 2 usage, 3 credentials, 4 product/geometry guard, 5 budget, 6 foreign partial, 7 HTTP 403.

## 1. Preconditions

- [ ] `export EARTHDATA_USER=... EARTHDATA_PASS=...`, or a `machine urs.earthdata.nasa.gov login ... password ...` line in `~/.netrc`.
- [ ] Grant the app: https://urs.earthdata.nasa.gov → **Applications → Authorized Apps → "Alaska Satellite Facility Data Access" = approved** (accepting the ASF EULA once in Vertex authorises the same app). Effective immediately — just rerun.
- [ ] Expect **exit 3** for either failure. The 401 text checks the password first because **a wrong password and a missing grant are indistinguishable** — both end at the EDL login form instead of the S3 redirect. Re-check password, then grant, then rerun; the `.part` is kept.

## 2. Step 1 — get the granule (one stack, one geometry)

- [ ] `./scripts/python.sh pipeline/ingest/02_search_download.py search --cities mexico-city`
      PASS: `[mexico-city] -> data/processed/_availability/mexico-city.json chosen=...` with non-empty `sources.nisar_gunw.stacks` and `recommended_stack.track != null`.
      FAIL `available:false` ⇒ no product for this AOI (D) — stop, take the fallback. A report without `stacks` predates stack derivation; `download` then plans the legacy 3-granule preview, which the geometry guard refuses whenever it spans tracks: re-run `search`.
- [ ] `./scripts/python.sh pipeline/ingest/02_search_download.py download --city mexico-city --source nisar_gunw --stack recommended --max-gb 6 --dry-run`
      PASS exit 0: every id `status: ACCEPT`, `level=L2 product=GUNW`, **exactly one** `track=/direction=` across the plan, and the size block is read for what it says (next bullet), not assumed.
      (The `parsed: ...` wording appears only inside refusal reasons, which print for `REJECT`.)
      FAIL exit 4 `REFUSED: this plan mixes acquisition geometries` ⇒ do **not** pass `--allow-mixed-geometry` (P still refuses to combine them); plan `--stack <track>`.
      FAIL exit 4 on one id ⇒ identity guard: read that `reason` before anything else.
      FAIL exit 5 `planned size ... exceeds --max-gb 6` ⇒ a plan over budget fetches **0 bytes**; raise `--max-gb` to the printed size deliberately (recorded in `provenance.json.max_gb`).
- [ ] **Sizes: NISAR GUNW arrives sizeless, so `0.00 GB` is not a budget check.** ASF's SearchAPI carries `sizeMB` for Sentinel-1 but not for NISAR GUNW: all 69 `mexico-city` records are `size_mb: null`, so `total_gb` is `null` and the cumulative preflight sums 0 MB. The dry run must therefore print `known size: NONE — no granule in this plan carries a size`, `BUDGET CANNOT BE PREFLIGHTED … --max-gb 6 will NOT refuse before transfer`, and `Enforcement is mid-stream only … AFTER bytes have flowed … .part file behind`. That is **not** a failure (an unknown size never refuses, S `cmd_download`); it is a warning that this guard is not doing anything yet.
- [ ] Make the budget real before transferring: add `--probe-sizes` (one unauthenticated HEAD per planned granule, reading `Content-Length`; verified `HTTP 200`, `Content-Length 2344615936` ≈ 2.34 GB on one `mexico-city` granule — no credentials needed). The plan then prints `known size X GB` and exits 5 if X > `--max-gb`.
- [ ] Budget arithmetic to expect: a 12-id stack at ~2.34 GB per granule is ≈ **28 GB**, far above the 6 GB default. Measure with `--probe-sizes`, then raise `--max-gb` to the printed number **deliberately** (it is recorded in `provenance.json.max_gb`). Never raise it blind.
- [ ] Same command **without** `--dry-run`. PASS: `get <granule>.h5 (2.3 GB)` per file — or `(unknown size)` when the report has no size for it (nothing is guessed) — then `provenance -> data/raw/mexico-city/nisar_gunw/provenance.json` (sizes + sha256). Rerun is safe: complete files print `skip (complete)`.
- [ ] Know the trap this closes: a plan built from an unguarded granule list once picked an L0 `NISAR_L0_..._RRSD_...` file (**123 GB**) and began downloading it. `build_download_plan` now identity-checks every id before credentials or bytes (`PRODUCT_POLICY`: RRSD/RSLC/RIFG/RUNW reject; GCOV/GSLC/GOFF only via `--allow-product`), so that mistake exits 4 with nothing fetched, and `--max-gb` is a cumulative second stop.
- [ ] Accept the granularity: the smallest fetchable unit is a whole stack (≤12 ids, S `STACK_GRANULE_LIMIT = 12`); there is **no flag for exactly one file**. Read one file in Steps 2–4; Step 5 needs the chain anyway.

## 3. Step 2 — read the file before trusting it

```bash
G=$(ls -S data/raw/mexico-city/nisar_gunw/*.h5 | head -1); ./scripts/python.sh pipeline/ingest/inspect_hdf5.py "$G" --product nisar-gunw --json > /tmp/tree.json
jq -r '.expectations[]|select(.kind=="dataset")|"\(.role)\t\(.present)\t\(.found//"-")"' /tmp/tree.json
jq -r '.datasets[]|select(.path|test("unwrappedPhase|coherenceMagnitude|connectedComponents|/mask$|numberOfLooks|radarGrid|xCoord|spatial_ref|crs"))|"\(.path)\t\(.dtype)\t\(.units)\t\(.fill_value)"' /tmp/tree.json
./scripts/python.sh pipeline/ingest/nisar_gunw.py "$G" --json
```

Compare against **L §2/§3**. A `present:false` role or a wrong `dtype`/`units` is the correction event: fix `pipeline/ingest/nisar_gunw.py:DATASETS` (and the matching layout block in `inspect_hdf5.py`) from `/tmp/tree.json`, then **restart at Step 2**.

| # | Compare | Expected (L §2/§3) | If it differs |
|---|---|---|---|
| 1 | phase / coherence / components paths | `.../frequencyA/unwrappedInterferogram/{POL}/*`, `{POL}` = HH **or** VV, read not assumed | re-point `DATASETS` |
| 2 | dtypes | phase `float32`, coherence `float32`, components `uint32`, mask `int8` | edit `DATASETS`; P refuses a wrong-width cast |
| 3 | units | phase `rad`, coherence `0..1` | phase not in radians ⇒ the `λ/4π` scale is wrong: stop and record |
| 4 | mask | `int8`; `127` padding dropped, `0`/`2` kept and carried (L §2) | record every observed value + count here — unknown flags are recorded, not dropped: **decide from the file, then record it** |
| 5 | x/y names, CRS location | CF metadata; names/EPSG location unverified (L §3) | record both; the reader leaves them `None` rather than guessing |
| 6 | wavelength | `nisar_gunw.py --json` → `wavelength_m`, `wavelength_source` | `null` ⇒ P uses `0.2384 m` (L §4) **with warning + limitation**; record that it did |
| 7 | times, geometry, looks | dates non-null; incidence/heading/looks probed at `metadata/radarGrid`, `processingInformation/parameters/frequencyA` (P) | per-pixel grid ⇒ refused (Step 4); no looks ⇒ coherence published without the finite-look debias (P warning) |

- [ ] `inspect_hdf5.py` exits 0 **and** `nisar_gunw.py` exits 0 (structural read).

## 4. Step 3 — does the case survive?

The coverage number exists only after the aggregate, and P refuses < 4 epochs (T `MIN_FIT_POINTS = 4`), so probe with the minimum connected chain (≥3 chained 12-day pairs) into `/tmp`, never into `data/processed`:

```bash
./scripts/python.sh pipeline/process_case.py --case mexico-city --out /tmp/gunw-probe/mexico-city \
    --granule <g1>.h5 --granule <g2>.h5 --granule <g3>.h5 --incidence <θ> --heading <α>
./scripts/python.sh -c "import pandas as pd;print(pd.read_parquet('/tmp/gunw-probe/mexico-city/nisar_grid.parquet').coherence.median())"
```

- [ ] Coverage PASS bar: ≥ **50 %** of the AOI's H3 cells must receive data (P `COVERAGE_WARNING_FRACTION = 0.5`; D checklist). The aggregate prints `WARNING: COVERAGE: only X% of the <case> AOI's N H3 cells received data (M cells, threshold 50%). Do NOT claim city-wide displacement…` — any such line is a FAIL **by reading**, not by exit code: `process_case.py` still exits 0 on low coverage.
- [ ] Coherence median ≥ **0.35** = `K SENSOR_PROFILES["nisar-gunw"].coherence_min`; below it the interferogram is unusable at this posting (D) ⇒ FAIL.
- [ ] `dates` inside `2026-07-02` … `2026-11-11` (C `processing.dates_start`, `dates_step_days: 12`, `dates_count: 12`). Real granules may fall outside: **decide from the file, then record it**, and note it in `meta.limitations`.
- [ ] Decision rule: **coverage < 50 % ⇒ do not claim city-wide displacement.** Flip `primary: mexico-city` → `primary: malaga` in `config/study_cases.yaml` (one line; C header, D "Still open") and keep CDMX as a coverage-limited appendix, never the headline.
- [ ] Why `/tmp`: only `_availability` may sit undeclared under `data/processed` (K gate file check); any other stray directory fails the gate.

## 5. Step 4 — supply the geometry only if the file does not

`--incidence DEG` = incidence angle at the target, `[0, 90)`; `--heading DEG` = platform heading, degrees clockwise from north (P `--help`). Metadata wins; the flag fills an absence only.

- [ ] Run P **without** the flags: `refused: incidence angle missing ...` / `heading angle missing ...` lists every path it probed — record those paths in L §5, then choose one defensible scalar: the granule's `metadata/radarGrid` (`incidenceAngleCenter`, `heading`), the product's published acquisition geometry (L §1), or the published geometry for that track/direction. No default is invented.
- [ ] A **per-pixel** incidence grid is a refusal, not an average (P `_probe_scalar`: `is_grid` ⇒ `GeometryRequiredError`): averaging θ silently redefines `cos θ`, and `cos θ` scales every millimetre of vertical displacement (L §4). Take the scalar at the reference pixel's geometry and record which.
- [ ] Whatever you pass is recorded: `meta.provenance.geometry` = `incidence:<source>; heading:<source>` (`CLI --incidence` when you supplied it).
- [ ] For Step 6: with `v_north = 0`, `v_east = 0` the vertical rate depends only on `cos θ` (T `los_to_vertical`), so no vertical rate validates the heading convention — record it, do not tune it.

## 6. Step 5 — process and gate

```bash
./scripts/python.sh pipeline/process_case.py --case mexico-city --out data/processed/mexico-city \
    --granule data/raw/mexico-city/nisar_gunw/<g1>.h5 --granule ... --granule ... --incidence <θ> --heading <α> --force
./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed
./scripts/check_phase_a.sh
```

- [ ] `--force` is mandatory: the committed `meta.json` says `product == "mock"` and P refuses to overwrite it otherwise (`MockOverwriteError`, exit 2, tree untouched).
- [ ] PASS: exit 0 with `contract PASS` and `gate PASS`; `provenance.source` = `ASF DAAC — NASA NISAR L2 GUNW ...` and no longer contains "mock" (check `jq -r '.provenance.source' data/processed/mexico-city/meta.json`).
- [ ] PASS: `validate_artifacts.py --processed data/processed` exits 0.
- [ ] `check_phase_a.sh` must PASS end to end, steps 1–4 included. Its provenance rule is now
      **symmetric**: a `mock` product must declare itself mock, and a real product must **not**
      contain "mock". If step 4 complains that `provenance.source` still says mock, the artefacts are
      stale — rerun Step 5 rather than editing the gate.

## 7. Step 6 — sign convention: hard gate before any number is published

- [ ] Compare `meta.stats.median_velocity_mm_yr` and its `p05_velocity_mm_yr` sibling with the published **−450 mm/yr** (C `published_rate_mm_yr`; Cigna & Tapete 2022, doi:10.1029/2022gl098923). PASS: median **negative** (subsidence under `d_LOS = -λ/4π·φ`, L §4), `|median| ≥ 5 mm/yr` so the city is not `estable` (K `RISK_THRESHOLDS_MM_YR`, `NULL_RESULT_MM_YR = 5.0`), and `p05` within **−900 … −225 mm/yr** (factor-2 band on −450).
- [ ] FAIL on sign ⇒ the LOS/heading convention is inverted. There **is** a CLI switch now: `--los-sign {away-from-sensor,toward-sensor}` and `--heading-convention {platform,look}` (the latter applies `α = 180° − α_platform`), both passed to Step 5's command. Flipping the sign inside `nisar_gunw.phase_to_los_displacement_mm` remains the code-level alternative; prefer the flag and record whichever you used.
- [ ] With a GNSS/levelling series at the reference pixel, the residual target is **< 5 mm/yr RMSE** (README §13 "Velocity accuracy"); otherwise use the published rate and say so.
- [ ] Record the winning combination in `meta.provenance.method` with `--method-note "TEXT"` and `--validated-against "TEXT"` (both on P's CLI, both repeatable). Supplying any convention flag **replaces** the hedge sentence "sign convention of docs/nisar-gunw-layout.md §4, NOT yet validated against GNSS"; leaving the defaults keeps it, which is correct — no GNSS check has happened yet. The choice is also stored machine-readably in `meta.provenance.geometry` (`los_sign`, `heading_convention`, `validated_against`). Gate: that sentence must be gone from `meta.provenance.method` before any number is shown — make it the first line of the Step 7 commit.

## 8. Step 7 — record what you learned (same commit as the code change)

- [ ] Dataset names / `dtype` / units → `nisar_gunw.DATASETS` + L §2 (and `PRODUCT_LAYOUTS` in `inspect_hdf5.py`).
- [ ] Mask semantics: observed values and counts (`127` padding vs `0`/`2` data, any unknown flag) → L §2. Counts: **decide from the file, then record it here.**
- [ ] Geometry: values, units, real path under `metadata/radarGrid`, scalar vs grid, CLI value used → L §3/§5, `meta.provenance.geometry`.
- [ ] Heading azimuth convention + LOS sign (Step 6 outcome) → L §4 and `meta.provenance.method`, permanently.
- [ ] Wavelength source (metadata, `c/f`, or the `0.2384 m` fallback) → L §4.
- [ ] x/y dataset names and where EPSG lives (dataset vs CF attribute) → L §3; close L §5.1.
- [ ] Effective looks `L`: present, scalar, or absent (debias applied or not) → L §5 and `meta.limitations`.
- [ ] Acquisition-time fields that actually resolved the pair → L §5.
- [ ] Filename grammar: does the real id match `GRANULE_GRAMMAR["nisar-l1-l2"]` (S) **and** P's `_GRANULE_RE` (track/frame/direction)? A near-miss ⇒ fix both tables together.
- [ ] Real AOI coverage: the Step 3 fraction and N cells per case → D "Still open", and delete the unknown from L §5. Anything still undecided: **decide from the file, then record it here.**
