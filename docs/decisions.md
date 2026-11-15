# Decision record

Why this file exists: the project carries a frozen contract (`docs/data-contract-v1.md`) and a
handful of non-obvious technical choices that a reader will otherwise re-litigate. Each entry below
records what was decided, the *reason*, and what evidence would justify reversing it. A decision
that is not written down here and not enforced by a test is a preference, not a decision.

Format: `D-nn` for decisions in force, `N-nn` for decisions discovered by audit (made implicitly
during the 2026-11-14 session and recorded retroactively), `O-nn` for open questions that still
need an owner's answer. Every entry names the file that enforces or implements it.

Changing a decision: `D-01` (the contract) requires a `SCHEMA_VERSION` bump, a `CHANGELOG` line and
all three roles' agreement. Every other entry needs the test that guards it to be updated in the
same commit — otherwise the change is invisible.

---

## A. Contract and domain model

### D-01 — Freeze the schema behind a fingerprint
**Decision.** `SCHEMA_VERSION = "1.1"`, `CONTRACT_REVISION = 2` (bumped from `"1.0"` / `1` on
2026-11-15 under the O-05 reversal; the bump moves the fingerprint with it). The contract's own hash
is printed by `./scripts/python.sh -m src.domain.contracts` and must be quoted by anyone claiming
conformance. Re-derive that value on the commit you are claiming and never quote a remembered one;
the retired v1.0 digest `049376b00f9f23906b1ccaa2b9555699e14a8d3bec9fcb6ba0fc0d336678ce1e` is kept as history <!-- fingerprint-historical: retired v1.0 digest, recorded so the 1.1 bump is visible; it is not a claim about this tree -->.
**Why.** Three people build against the artefacts in parallel; an unversioned schema means a silent
shape change reaches the UI as a mysterious `KeyError`. The fingerprint makes "did the contract
move?" a one-command question instead of a diff review.
**Evidence.** `src/domain/contracts.py`; `docs/data-contract-v1.md`; guarded by `tests/test_contracts.py`.
**Reverse if.** Never for this submission — bump instead.

### D-02 — `delta_change` is cumulative, `velocity_mm_yr` is a rate
**Decision.** `delta_change` = cumulative displacement over the window in mm (equals
`val_t2 − val_t1`); `velocity_mm_yr` = the fitted trend coefficient in mm/yr. Two units, two fields,
never one field with a unit flag.
**Why.** They answer different questions ("how much has it moved?" vs "how fast is it moving?"), and
conflating them produced the single worst bug of the session: a seasonal term anchored at the first
date made `delta/T` overstate the trend by 2.1–7.9× and disagreed in *sign* on up to 601 cells.
**Evidence.** `src/domain/contracts.py` (`GRID_FIELDS`), `tests/test_contracts.py`.
**Reverse if.** Never; add a field instead.

### D-03 — `product` is a closed enum, and `opera-disp-s1` is not reachable for these AOIs
**Decision.** Shipped products are `mock` or `nisar-gunw`. `opera-disp-s1` stays in the enum but is
not usable here: the availability probe returned **0 granules for all three AOIs**.
**Why.** OPERA DISP-S1 would have been the cheapest path (ready-made displacement, no phase
processing). It does not exist for our study areas, so adopting it would have meant an empty
product shipped under a confident badge.
**Evidence.** `data/processed/_availability/*.json`; `docs/data-source-decision.md`;
`src/domain/contracts.py` (`SENSOR_PROFILES`).
**Reverse if.** A later ASF/ASDA query returns DISP-S1 over any AOI.

### D-04 — Nodata is absent, never zero
**Decision.** Cells with padding mask 127, non-finite phase/coherence, coherence below the frozen
`coherence_min`, or an incomplete series are **dropped**. Water is not written as `0 mm/yr`; it
survives only as a `layer: "water"` context feature.
**Why.** Málaga's median is −1.4 mm/yr — a `0` there is indistinguishable from a real measurement.
Absent-versus-zero is the difference between "we have no data" and "we measured no motion", and a
low-signal negative control is exactly where that distinction gets destroyed.
**Evidence.** `pipeline/process_case.py` (`keep = valid & (coherence >= PROFILE.coherence_min)`;
`meta.null_policy`); `src/domain/contracts.py` (`META_REQUIRED_KEYS` demands that field).
**Reverse if.** Never.

### D-05 — Risk bands are sign-agnostic
**Decision.** `|v| < 5` → `estable`, `5 ≤ |v| < 25` → `moderado`, `|v| ≥ 25` → `critico`
(`RISK_THRESHOLDS_MM_YR`). Uplift is as alarming as subsidence.
**Why.** A single sign convention for "up positive" across three cities means a −80 mm/yr city and a
+80 mm/yr city are the same hazard. Thresholds are documented defaults, overridable only in config.
**Evidence.** `src/domain/contracts.py`; `tests/test_contracts.py`.
**Reverse if.** A case is added where uplift is benign and must be styled differently.

### D-06 — A detection limit is mandatory once the signal is small
**Decision.** When `|median velocity| < NULL_RESULT_MM_YR` (5.0 mm/yr), `stats.detection_limit_mm_yr`
must be present. Málaga ships `1.5` beside its `−1.358` mm/yr median.
**Why.** Without it, "−1.4 mm/yr" reads as a measured subsidence rate rather than as motion
indistinguishable from noise. The field makes the null result falsifiable instead of merely modest.
**Evidence.** `src/domain/contracts.py`; `pipeline/science/transforms.py` (`detection_limit_mm_yr`);
`tests/test_mock_expectations.py`.
**Reverse if.** Never; it is the honesty mechanism for the negative control.

### D-07 — Processing parameters are per `(sensor, product)`
**Decision.** `coherence_min`, `k_sigma`, `min_ddB`, `persistence`, `min_area_m2`, `posting_m` and
`revisit_days` live in `SENSOR_PROFILES`, keyed by product. Coherence is never pooled across sensors.
**Why.** NISAR's 12-day revisit and Sentinel-1's 6-day revisit produce different coherence
distributions from identical ground; a shared threshold would silently favour whichever sensor
pairs more often, and a shared coherence statistic would be meaningless.
**Evidence.** `src/domain/contracts.py` (`SensorProfile`); `README.md` §13.
**Reverse if.** Never; add a profile instead.

---

## B. Science

### D-08 — The seasonal harmonic is centred on the observation window
**Decision.** The mock's cumulative displacement is `A·(cos(2π(t−c)/T) − cos(2πc/T))` with `c` the
window centre, so both endpoints carry the same seasonal offset. Not anchored at the first date.
(`harmonic_fit` fits the plain `[1, t, cos, sin]` basis; the centring belongs to how the mock
synthesises `d(t)`, which is what keeps the ramp out of `delta_change`.)
**Why.** Anchoring at the first date puts the seasonal ramp inside `delta_change`, where it is
indistinguishable from secular motion over a 132-day window. The invariant that proves the fix is
`delta_change / T ÷ velocity_mm_yr = 1.000` (it was 2.1–7.9× off before, with sign disagreements).
**Evidence.** `pipeline/science/transforms.py`; `pipeline/mock/model.py`;
`tests/test_science_transforms.py`.
**Reverse if.** The window ever covers ≥ 1 full year at ≥ monthly sampling, where anchoring stops
mattering.

### D-09 — One harmonic by default, with an identifiability guard
**Decision.** `harmonic_fit(..., n_harmonics=1)`. A second harmonic requires
`MIN_SEASONAL_COVERAGE_PER_HARMONIC` (0.75) of the period sampled.
**Why.** Two harmonics over 132 days is 2.7 parameters per 0.36 of a year: the fit has enough
freedom to absorb the trend and produced ±533 mm/yr scatter in testing. The guard converts "looks
noisy" into a refusal.
**Evidence.** `pipeline/science/transforms.py` (`MIN_FIT_POINTS = 4`,
`MIN_SEASONAL_COVERAGE_PER_HARMONIC = 0.75`).
**Reverse if.** A multi-year stack arrives. **Reversed 2026-11-15 by its own condition:** the
two-year mock window (62 dates, 732 d) *is* a multi-year stack, so `transforms.harmonic_order` now
asks for **two** harmonics (every shipped `meta.stats.seasonal_n_harmonics` is 2). The guard
survives as the coverage/epoch floor rather than as a fixed order of one; `harmonic_fit`'s own
default is still 1 for callers that do not compute an order, which is why the decision text above
is kept rather than rewritten.

### D-10 — Vertical conversion uses ONE geometry; the LOS sign is not validated
**Decision.** `v_up = d_LOS/cosθ + tanθ·(cosα·v_east + sinα·v_north)` from a single flight direction
(θ = 39°), with `v_north = 0` unless a GNSS model supplies it. Ascending + descending decomposition
is **not implemented**; the pipeline *refuses* stacks that mix flight directions rather than
pretending to invert them.
**Why.** Asc/desc inversion needs two independent look vectors; with one geometry the vertical
component is only as good as the assumed incidence angle, the assumed horizontals and the sign
convention. Refusing a mixed stack is honest; silently averaging two directions would be a
fabrication.
**Evidence.** `pipeline/process_case.py` (`TrackMismatchError`); `README.md` §6 marks it
*designed, not implemented*. The "not yet validated against GNSS" hedge is deliberately retained in
provenance.
**Reverse if.** GNSS validation is run, or two flight directions are downloaded.

### D-11 — Model coherence down to 0.35 so the quality filter bites
**Decision.** The mock's coherence spans 0.35–0.95 (Mexico City 0.44–0.95); `coherence_min = 0.35`
for `nisar-gunw`/`mock`.
**Why.** Clipping the mock at 0.45 made the quality slider a no-op: every cell passed, so the UI
control implied a filtering capability the data never exercised. Lowering the floor gives the
filter something to remove.
**Evidence.** `config/study_cases.yaml` (mock block); `src/domain/contracts.py` (`SENSOR_PROFILES`).
**Reverse if.** Real data shows an urban coherence floor above 0.35.

### D-12 — Do not publish a seasonal amplitude from a 4.5-month window *(superseded 2026-11-15)*
**Decision.** `seasonal_amp_mm` was computed internally for regime classification but never published
from the 132-day stack.
**Why.** 132 days is 0.36 of a year: a "seasonal" amplitude fitted to less than one cycle is
aliased with the trend. The elastic/inelastic classification (Málaga 3.5 mm seasonal > trend;
Mexico City 1.5 mm seasonal < trend) was a model statement, not a measured seasonality claim.
**Evidence.** `config/study_cases.yaml` (`limitations`, per case); `pipeline/process_case.py`.
**Reverse if.** ≥ 1 year of epochs. **Reversed 2026-11-15 by its own condition:** the window is now
2 years (62 dates, 732 days), so `seasonal_amp_mm`, `ratio_R` and `regime` are published on contract
1.1 as values fitted by `harmonic_fit`, not typed constants (O-05). The rule the entry encodes does
not change: an amplitude is published only where the identifiability gates pass, never derived from a
window that cannot support it.

---

## C. Acquisition and planning

### D-13 — Product identity is checked before any socket opens
**Decision.** A download plan validates every granule id against the product policy: L0 `RRSD`,
L1 `RSLC`/`RIFG`/`RUNW` are rejected; GCOV/GSLC/GOFF only via an explicit `--allow-product`.
**Why.** A plan built from an unguarded granule list once began fetching a 123 GB L0 file. Identity
checking is cheap, local, and catches the class of error that costs hours of hackathon time.
**Evidence.** `pipeline/ingest/02_search_download.py`; `tests/test_download_guard.py`.
**Reverse if.** Never.

### D-14 — One geometry per stack; mixed stacks are refused, not truncated
**Decision.** `--stack recommended` selects a single `(track, direction)`; `--stack all` and any
mixed plan exit 4 with a refusal.
**Why.** See D-10. A truncation would silently produce a stack whose geometry differs from what the
operator believed, which is worse than a stop.
**Evidence.** `pipeline/ingest/02_search_download.py`; `tests/test_download_stack.py`.
**Reverse if.** Asc/desc inversion is implemented.

### D-15 — `date_range` is the epoch window (reference acquisitions)
**Decision.** `date_range = [first reference_start, last reference_start]` over the sampled granules;
`full_date_range` describes the whole stack. A granule id carries **four** timestamps (reference
start/end, secondary start/end); the secondary is the partner of an *earlier* epoch and does not
extend the window. Separately, `n_epochs` inventories every distinct acquisition day carried by the
stack, reference **and secondary**; `connected_epochs` describes the longest chained run.
**Why.** This cost real debugging time twice: first because a chain check compared ref-end to
ref-start and "proved" the sampler was broken, then because using both stamps made the advertised
window disagree with the sampled ids. Reference-only is the coherent definition for the advertised
window; the epoch inventory is a different quantity and therefore uses both stamps.
**Evidence.** `pipeline/ingest/02_search_download.py`; `tests/test_availability_evidence.py`
(asserts the advertised window equals the sampled ids' span).
**Reverse if.** The report ever describes "acquisitions contained" rather than "epochs covered" —
then both stamps belong in the window.

### D-16 — The recommended stack is a contiguous, connected run
**Decision.** Among the stacks, the recommendation is the largest single geometry, then the longest
connected run of pairs (`_best_download_run`), evenly sampled to `STACK_GRANULE_LIMIT` (12). If the
run yields fewer than `MIN_FIT_POINTS` (4) connected epochs, the stack is still reported but flagged
`epoch_sufficient: false` with a warning.
**Why.** A stack that cannot be fitted must not be advertised as downloadable. The flag is what
makes an insufficient archive visible instead of a false plan, and it is still armed and still
tested: under the corrected grammar (O-06) no committed case trips it — the recommended stacks carry
22, 9 and 10 connected epochs — but a stack under 4 is reported with `epoch_sufficient: false` and a
warning rather than silently planned.
**Evidence.** `pipeline/ingest/02_search_download.py`; `tests/test_download_stack.py`.
**Reverse if.** `MIN_FIT_POINTS` changes or a longer archive arrives.

### D-17 — Budget: probe sizes, refuse on known, never refuse on unknown
**Decision.** ASF's SearchAPI carries `sizeMB` for Sentinel-1 but **not** for NISAR GUNW, so a
`--max-gb` preflight over NISAR sums 0 MB and cannot trip. `--probe-sizes` issues one unauthenticated
`HEAD` per planned granule (`PROBE_LIMIT = 12`, 0.2 s apart, stops on 401/403) and reads
`Content-Length`. Known over-budget ⇒ exit 5 *before* transfer. Unknown size ⇒ a loud warning that
the cap cannot be preflighted, and enforcement is mid-stream.
**Why.** The pre-existing guard was dead code for the exact product being downloaded: `--max-gb 6`
passed a 28.23 GB plan. Measured: ~2.34 GB per GUNW, so a 12-granule CDMX stack is **28.23 GB**.
Refusing on an unknown size would block the only path that matters, so unknown warns loudly instead.
**Evidence.** `pipeline/ingest/02_search_download.py`; `provenance.json.max_gb` + `size_probe`.
**Reverse if.** ASF starts returning NISAR sizes, or a manifest with sizes is published.

### D-18 — Authentication fails closed with a checklist
**Decision.** Missing credentials stop the download (exit 3) with an actionable message naming the
Earthdata registration, the ASF EULA and the "Authorized Apps" approval. A 401 prints the same
checklist.
**Why.** Earthdata 401s are almost never a wrong password; they are an unapproved application. A
generic "auth failed" wastes the operator's afternoon.
**Evidence.** `pipeline/ingest/02_search_download.py`; `tests/test_download_auth.py`.
**Reverse if.** Never.

---

## D. Product and presentation

### D-19 — Mexico City is primary; Málaga is the declared negative control
**Decision.** `primary: mexico-city` in `config/study_cases.yaml`; Málaga ships as the explicit
negative control. One-line flip to `malaga` if CDMX's GUNW coverage fails the ≥ 50 % check.
**Why.** The strongest signal (median −62.1 vs −1.4 mm/yr) is the clearest demo; a negative control
is required by the brief, and admitting one is more credible than three cities that all look
alarming. Keeping the flip as a single line means the decision cannot rot into scattered edits.
**Evidence.** `config/study_cases.yaml`; `docs/data-source-decision.md`.
**Reverse if.** CDMX coverage < 50 % on real data.

### D-20 — Coverage is measured against AOI cells, not the bounding box
**Decision.** The ≥ 50 % bar (`COVERAGE_WARNING_FRACTION`) is `|AOI cells ∩ cells with data| /
|AOI cells|`, with h3 centre containment (`polygon_to_cells`), matching what `process_case` warns
on. The bbox figure is reported as `coverage_bbox` and must never be spoken as "coverage".
**Why.** Two denominators had produced two contradictory stories: bbox said Jakarta 47.6 % and
Málaga 34.8 % ("below half, no city-wide claim"), the AOI said **53.69 / 55.41 / 57.26 %** (all
clear it). The bbox is ~45 % larger than the Jakarta polygon, so it understated coverage enough to
flip the conclusion. An envelope is not a denominator.
**Evidence.** `pipeline/process_case.py`; `docs/first-granule-runbook.md` §4;
`scripts/demo_numbers.py` (`coverage_aoi` / `coverage_bbox`); `docs/video-script.md`.
**Reverse if.** Never; report both, label both.

### D-21 — AOI scope: city-scale single polygon is the measurement domain
**Decision.** Each case's measurement area is one city-scale `Polygon` in `config/aoi/{case}.geojson`.
Metro/multipart exports are context only and are not adopted. Málaga's detached 1.33 km² fragment is
a **declared exclusion**, never merged in with a fabricated corridor.
**Why.** The pipeline derives its cell set from a single closed ring (`h3.LatLngPoly(cfg.aoi_ring)`),
so multipart needs support end-to-end; the metro exports are 5.3× (CDMX) and 7.2× (Jakarta) larger
and would triple the cell count while diluting GUNW overlap, making the 50 % bar unreachable exactly
where the honesty claim depends on it. A MultiPolygon was once committed by mistake, which is why
the geometry is now guarded.
**Evidence.** `docs/data-source-decision.md` ("AOI scope"); `tests/test_aoi_geometry.py`
(rejects MultiPolygon, holes, lat/lon swaps); `tests/test_aoi_loader.py`.
**Reverse if.** Multipart support is implemented and coverage is re-measured.

### D-22 — The mock is deterministic and byte-identical on rerun
**Decision.** Fixed seed 7, no wall clock, sorted rows, sorted JSON keys, atomic writes.
**Why.** Reviewers and the UI need to know that a changed artefact means changed *code*, not a
changed random draw. Byte-identical reruns also make "is this tree stale?" answerable with a hash.
**Evidence.** `pipeline/generate_mock.py`; `pipeline/mock/artifacts.py` (`_atomic_write`);
`tests/test_mock_expectations.py`; `tests/test_mock_reproducibility.py` (rerun from the default
seed reproduces the committed bytes).
**Reverse if.** Never for the demo.

### D-23 — The mock badge is part of the product, not a caveat
**Decision.** Every mock artefact carries `product: "mock"`, `provenance.source` says mock, and the
UI must badge MOCK. `check_phase_a.sh` is symmetric: a mock product must say mock, a real product
must not.
**Why.** The whole submission's credibility rests on not laundering synthetic numbers as measured
ones. A symmetric test means neither direction can drift unnoticed.
**Evidence.** `scripts/check_phase_a.sh` (step 4); `tests/test_honesty_claims.py`;
`scripts/demo_numbers.py` prints one mock banner and repeats the MOCK caveat in every case's
limitations.
**Reverse if.** Never.

---

## E. Process and verification

### D-24 — The artefact gate fails closed
**Decision.** `pipeline/validate_artifacts.py` exits nonzero on zero cases; `--allow-empty` prints a
vacuous-run warning; a `--case` miss is a failure.
**Why.** "0 cases, 0 problems" is the classic false green: a pipeline that processed nothing looks
identical to one that processed everything correctly.
**Evidence.** `pipeline/validate_artifacts.py`; `tests/test_validate_artifacts.py`.
**Reverse if.** Never.

### D-25 — Documented commands must work offline
**Decision.** Every `download ... --dry-run` printed in the docs is executed by
`tests/test_docs_commands.py` against a fixture built inside `tmp_path`, with no credentials and no
socket. Lines that genuinely need the network (`--probe-sizes`, a non-dry-run `download`, `search`)
are never executed: they are proven to sit outside the execution allowlist and checked statically
against the module's own `--help`. A documented offline line that breaks is a test failure, not a
doc preference.
**Why.** Docs rot silently. When HEAD documented `--probe-sizes --dry-run`, the suite went red for
anyone who cloned — while our own gate still said PASS, because the clone gate did not run the doc
guards. Both halves are now guarded.
**Evidence.** `tests/test_docs_commands.py`; `scripts/preflight.sh` (clone gate runs the doc guards).
**Reverse if.** A documented command is deliberately network-dependent — then it must not be
presented as the offline verification path.

### D-26 — MIT for the code, with an explicit data carve-out
**Decision.** `LICENSE` is MIT for code/docs/figures, and states plainly that it does not relicense
the data: NISAR (NASA/JPL-Caltech, ASF DAAC, Earthdata terms), Sentinel-1 (ESA Copernicus), map
context (OpenStreetMap, ODbL).
**Why.** A public repo with no licence is "all rights reserved" by default, which blocks reuse by
judges and future teams; and an MIT header alone would imply the data is MIT too, which is false.
**Evidence.** `LICENSE`; `README.md` §17.
**Reverse if.** The team chooses a different licence — the data carve-out stays regardless.

### D-27 — Ignore backstops for granules and credentials
**Decision.** `.gitignore` covers `data/raw/`, `data/staging/`, and additionally `*.h5`, `*.hdf5`,
`*.nc`, `*.zip`, `*.tif*`, `*.part`, `cookies.txt`.
**Why.** `data/raw/` is the documented landing zone, but a resumed or moved file lands beside its
destination. One GUNW is ~2.3 GB and a cookie jar is a credential: either one committed is a
disaster that history rewriting cannot cleanly undo.
**Evidence.** `.gitignore`; `tests/test_gitignore_policy.py` (`test_no_tracked_file_is_ignored`,
`test_secrets_and_heavy_data_are_never_committable`).
**Reverse if.** Never.

### D-28 — `yuls` is the integration branch; `main` absorbs it by fast-forward
**Decision.** The integrated MVP lives on `yuls`: it fast-forwarded `origin/pozo` (a strict superset —
`contracts.py` and all 12 mock artefacts byte-identical, plus the backend, CI and deploy), then merged
`origin/main` while keeping the contract at **1.0**. `origin/main` is consequently an *ancestor* of
`yuls`, so landing the work on the default branch is a zero-conflict fast-forward rather than a merge
with a reversion commit.
**Why.** Three branches each held a third of the submission: the artefacts here, the app on `main` (on
a contract 1.1 that the pipeline itself rejects), the backend on `pozo`. Integrating on the artefact
branch keeps the diff reviewable and means the default branch never carries a schema the pipeline
cannot satisfy.
**Evidence.** `./scripts/preflight.sh` → `VERDICT: READY` 7/7 on `f2dd5a8`; `tests/test_contracts.py`
pins 1.0 and the field order; contract fingerprint `049376b0…` after every merge. <!-- fingerprint-historical: the v1.0 digest this integration re-verified while the tree was pinned to 1.0; superseded by the 2026-11-15 bump -->
**Correction (2026-11-14, same day).** The first version of this entry claimed `origin/main` was an
ancestor of `yuls` and that landing the work was "a zero-conflict fast-forward". That was true for
about an hour: `origin/main` then advanced to `5c9a59c` with the cockpit UI, a docker fix and a
1.1 regeneration, so the second merge **did** diverge and did have conflicts. The premise is
recorded here rather than silently overwritten — D-29 covers what the divergence required.
**Reverse if.** The team moves to short-lived branches off `main` per flujo §6; then `yuls` stops being
the integration point and this entry is deleted rather than edited.

### N-10 — Two merge regressions, and why "no conflict" is not "correct"
| # | What actually happened | Guard that caught it |
|---|---|---|
| **N-10a** | `data/processed/_availability/*.json` from `origin/main` were **pre-AOI-refresh** (generated 14:36, old bbox `-99.35..-98.85`, granule sizes stripped) and the merge removed 3,775 lines of this branch's refreshed 22:28 evidence | `tests/test_docs_commands.py` — the documented dry-run's exit code flipped from 0 to 5, which is how it surfaced. The artefact gate does **not** cover `_availability` |
| **N-10b** | `src/domain/contracts.py` auto-merged **with no conflict** and kept `origin/main`'s 1.1 body (`SCHEMA_VERSION "1.1"`, three new required grid fields, three stats keys) while `tests/test_contracts.py` kept matching 1.1 assertions — a clean merge that would have shipped the wrong schema | the contract self-check + the frozen-field-order tests; **nothing in the merge itself** |

Both sides were restored from `HEAD` and re-verified (fingerprint `049376b0…`, `PHASE A/B: PASS`). <!-- fingerprint-historical: the v1.0 digest of that 14 Nov restoration; superseded by the 2026-11-15 bump --> The
lesson recorded here: on a file both sides rewrote, a clean auto-merge is the *dangerous* outcome — it
has to be read, not trusted.

### D-29 — A second merge of a moved `main`: 1.0 kept, their cockpit adopted, agent files excluded
**Decision.** When `origin/main` advanced mid-PR (`c4f0fb7` → `5c9a59c`), the merge was resolved by
keeping contract **1.0**, taking `origin/main`'s **`app.py` + `src/ui/*`** (a 246-line four-tab
cockpit that binds through `ui_bridge` and never reads the 1.1-only fields), restoring our artefacts
and regenerating them, and **excluding** four files that are agent/team process rather than product:
*AGENTS.md*, *skills-lock.json*, *docs/requests.md*, *docs/contract-change-requests.md*.
**Why.** (1) The 1.0 argument is unchanged (see O-05): 1.1 makes `seasonal_amp_mm` required and this
window cannot identify it. (2) Their shell is strictly richer than our rewrite — four tabs, filters,
telemetry, mock detection against our three-layer map — so adopting it loses nothing and avoids two
competing frontends. (3) Those four files assert a contract version we do not ship and name
individuals; *docs/ui-data-needs.md* was kept because `src/data/ui_bridge.py` and
`tests/test_ui_bridge.py` reference it, so deleting it would leave production references dangling.
**Evidence.** `src/domain/contracts.py` restored from HEAD (fingerprint `049376b0…`) <!-- fingerprint-historical: the v1.0 digest of that 14 Nov merge; superseded by the 2026-11-15 bump -->;
`pipeline/generate_mock.py --check` passes and a full regeneration is byte-identical to the committed
artefacts (1.39 MiB / 15 MB); `app.py` is `origin/main`'s 246-line file by hash.
**Reverse if.** The team adopts 1.1 or 1.2 (O-05 option B/C), in which case the excluded request docs
become current again and should be restored from `origin/main` rather than rewritten.
*(O-05 was reversed on 2026-11-15 in favour of 1.1 with a 2-year window; the exclusion above is
still recorded here as what the 14 Nov merge did, not as a claim about the contract that ships now.)*

---

## F. Discovered by audit (decided implicitly, now recorded)

| # | Decision actually taken | Where it lives | Risk if undocumented |
|---|---|---|---|
| **N-01** | `date_range` semantics fixed as reference-start window (D-15) | `02_search_download.py` | a reviewer "fixes" it back to both stamps and the evidence guard fails confusingly |
| **N-02** | A single-pair stack may report `mean_interval_days: null` (no gap to measure); the guard allows `null` only when `n_granules < 2` and still rejects `0` | `tests/test_availability_evidence.py` | reads as a weakened guard; it is an edge case exemption |
| **N-03** | Size probing is **opt-in** (`--probe-sizes`), default path unchanged | `02_search_download.py` | a future change makes the default path network-dependent and breaks offline dry runs |
| **N-04** | Numbers quoted in docs are only allowed if computed by a guard or listed with a reason (the "allowlist") — wall-clock claims are allowlisted, measurements are not | `tests/test_readme_numbers.py`, `tests/test_pipeline_readme_numbers.py` | someone adds a plausible-looking number that nothing checks |
| **N-05** | The committed figures are asserted byte-identical when regenerated from current artefacts | `scripts/make_figures.py` + manual check | a figure outlives the data it depicts |
| **N-06** | An unauthenticated `HEAD` is acceptable for size discovery (public CloudFront/S3), and must stop on 401/403 | `02_search_download.py` | someone "optimises" it into a crawl |
| **N-07** | Area is quoted from the loader's equirectangular estimate (1,498 / 748 / 394 km²); geodesic measurement differs by < 0.5 % | `pipeline/aoi.py`, `README.md` §14 | two areas in two docs look like a contradiction |
| **N-08** | The reference loader resolves `data/processed` against the **cwd**, not the repo root | `examples/reference_loader.py` | consumers run it from elsewhere and get a "missing data" error that blames the data |
| **N-09** | `demo_numbers` publishes **both** denominators — `coverage.aoi` (authoritative, the one the 50 % bar is measured on) and `coverage.bbox` (envelope only) — each labelled in the text card and the JSON; the bbox figure is never the threshold denominator (D-20) | `scripts/demo_numbers.py`; `tests/test_demo_numbers.py` | the two figures are compared as if interchangeable (this already happened) |

---

## G. Open decisions (no owner answer yet)

Entries marked **Answered** or **Resolved** stay here on purpose: the question, the answer and the
date are the audit trail. What is left unmarked is still waiting on an owner.

### O-01 — Download budget for the real run
A 12-granule CDMX stack is **28.23 GB**; the documented cap was 6 GB. Options: raise to ~30 GB, or
take 8 granules (~19 GB) and stay above `MIN_FIT_POINTS = 4`. Note the recommended chain contains a
36-day gap (2026-07-16 → 2026-08-21), which weakens a seasonal fit further as epochs are dropped.
**Blocks.** The first real granule.
**Guard answered 2026-11-14 (D-17).** The cap is enforceable before any transfer now: `--probe-sizes`
reads the real `Content-Length` and an over-budget plan exits 5 with nothing fetched. What remains
open is the spend — ~30 GB for 12 epochs or ~19 GB for 8 — not the mechanism.
**Answered 2026-11-14 — spend the first ~2.34 GB, not 28.** `download --max-granules N` truncates the
plan `--stack` already selected to its **first N granules by reference date** (a contiguous prefix that
still starts where the chain starts — never an even sample, never a hole in the middle), so
`--stack recommended --max-granules 1 --probe-sizes --max-gb 3` fetches exactly one granule to verify
the HDF5 reader before the remaining 28 GB. The limit gates `--probe-sizes`/`--max-gb` on the *truncated*
plan (30 GB no longer refuses one granule), while the mixed-geometry verdict is still taken on the full
selection so truncation cannot smuggle a mixed plan through; below `MIN_FIT_POINTS = 4` epochs the plan
prints an INSPECTION-ONLY warning and is still allowed, and `provenance.json` records `max_granules`,
`n_selected`, `n_planned`, `granules_truncated` and `planned_granule_ids` so nobody reads it as 12
fetched. What stays open is only the spend for the *full* chain, decided after the one file is read.
Guarded by `tests/test_download_stack.py` (`test_max_granules_*`).

### O-02 — Copyright holder for `LICENSE`
MIT needs a name; a collective handle grants a licence weakly.
**Answered 2026-11-14 — `fradiaz, jvernon and frpozo-l`.** The three team handles, named
individually so the grant is traceable to people rather than to a project label. Enforced in
`LICENSE` line 3; nothing else quotes the holder.

### O-03 — Does the loader keep cwd-relative resolution (N-08)?
Safer than a `__file__`-based guess (the previous version pointed one directory off once copied), but
it requires running from the repo root. Alternative: explicit `processed=` argument everywhere.
**Answered 2026-11-14 — keep it cwd-relative.** `examples/reference_loader.py` resolves against the
caller's cwd and says so in its own docstring (never `__file__`), with `processed=` offered for
callers that live elsewhere. Recorded as N-08; guarded by `tests/test_handoff_snippets.py`
(`test_the_default_root_follows_the_cwd`, `test_the_doc_tells_the_caller_to_run_from_the_repo_root`).

### O-04 — Should the artefact carry an AOI-based coverage field?
Currently coverage is computed by consumers; `meta.stats` has no `coverage_fraction`. Adding one is a
contract change (`SCHEMA_VERSION` bump) and would remove the ambiguity in N-09 at the source.
**Answered 2026-11-14 — no contract field.** The ambiguity is settled at the consumer instead: the
AOI denominator is the bar's (D-20) and `scripts/demo_numbers.py` publishes both figures labelled, so
nothing has to be inferred from `meta.stats` (N-09; `tests/test_demo_numbers.py`). Revisit only if a
consumer must report coverage without running the card.

### O-05 — Contract 1.0 vs 1.1 vs 1.2 (the merge blocker)
**Superseded 2026-11-15 — read the reversal at the end of this entry before acting on anything
below.** The 14 Nov record is kept verbatim as the audit trail; where it and the reversal disagree,
the reversal is current.
`origin/main` (`c4f0fb7`, the default branch) moved the contract to `SCHEMA_VERSION "1.1"` /
`CONTRACT_REVISION 2` (fingerprint `9a749cea…` <!-- fingerprint-historical: origin/main's 1.1 hash, quoted to show what the default branch moved to; it is not this tree's -->), adding **required** fields: grid
`seasonal_amp_mm`, `ratio_R`, `regime`; timeseries `trend_mm_yr`, `seasonal_mm`; stats
`median_seasonal_amp_mm`, `median_ratio_R`, `dominant_regime`. This branch (`yuls`) is at 1.0
(`049376b0…`), and all three cases fail 1.1 validation with 10 problems each <!-- fingerprint-historical: the digest this branch shipped while it was pinned to 1.0, before the 2026-11-15 reversal -->
(`grid: missing required column 'seasonal_amp_mm'`, and the same for `ratio_R` and `regime`).

**Why it is not a formality.** `harmonic_fit` on our 132-day stacks reports `annual_coverage 0.36`
against the 0.75 a harmonic needs, and a design condition number of 66.8 (limit 30), so
`seasonal_identifiable` is False, `regime` is forced to `"transitional"`, and the fit warns *do not
publish `seasonal_amp_mm`*. The mock's amplitude is recoverable (1.50 / 12.00 / 3.50 mm) only
because the series is noise-free; the **design** is what is ill-conditioned. A 1.1 artefact would
publish an amplitude the pipeline itself rejects.

**No consumer needs it.** `app.py` on `main` reads only `provenance.source`, `revisit_days`,
`len(dates)`, `crs` and grid `lat`/`lon`; the UI iterates `stats` generically. The requirement comes
from the validator alone, so 1.0 costs the app nothing.

**Options.** (A) revert `main` to 1.0 — one commit, no regeneration, no fingerprint move; (B) adopt
1.1 and populate — cheapest for the UI, weakest on honesty; (C) revise to 1.2 with nullable
`seasonal_amp_mm`/`ratio_R`/`seasonal_mm`/`median_*` plus a required boolean `seasonal_identifiable`,
where `false` forces the nulls and `"transitional"` — about 19 files, and the fingerprint moves.

**Blocks.** Merging to a green default branch. The 14 Nov premise — `main` shipping the 1.1 contract
with **zero artefacts** — is stale: `main` now ships **15 files** (it merged fradiaz's branch) on top
of that contract, so the divergence is wider than described here and a `--ff-only` merge is not
possible (the `3 behind / 4 ahead` count is the 14 Nov audit's, not a fresh measurement).

**Owner.** All three roles: the contract's own rule requires agreement for a version bump, and this
one was merged without Rol 3's sign-off.

**Do not decide by default.** Merge the file union first (`requirements.txt` union, `README.md`
union, keep both sides of `docs/route-contract.md`), then settle the contract, then regenerate — never
merge the bump and leave 1.0 artefacts in place.

**Answered 2026-11-14 — option A: revert `main` to 1.0.** The seasonal fields are not merely
unnecessary, they are unpublishable on this window: `harmonic_fit` reports `annual_coverage 0.36`
against the 0.75 a harmonic needs and a design condition number of 66.8 against a limit of 30, so
`seasonal_identifiable` is False and the pipeline says do not publish `seasonal_amp_mm`. No consumer
reads the 1.1 fields (`grep -rn` over `app.py` and `src/` returns nothing), so the reversion costs the
UI nothing. Executed as: `src/domain/contracts.py` and `tests/test_contracts.py` restored from this
branch, `docs/data-contract-v1.md` kept at 1.0, and the fingerprint re-verified as `049376b0…` <!-- fingerprint-historical: option A's v1.0 digest; superseded by the 2026-11-15 reversal --> (D-28,
PR #3). Option C (1.2 with nullable seasonal fields) stays the technically honest version if the
project outlives the hackathon. <!-- prohibition-historical: records the 2026-11-14 option-A decision to stay on 1.0, when the amplitude was unpublishable on that window; superseded 2026-11-15 by the 2-year window and O-05 -->

**Superseded 2026-11-15 — the reversal below replaces this answer.** The reversion described above is
undone: this branch adopts 1.1 and widens the mock window instead, which removes the reason option A
existed without publishing an amplitude the design cannot support.

**Owner reversed 2026-11-15 — adopt contract 1.1, and make it honest by widening the mock window.**
The reason option A existed was that 1.1 makes `seasonal_amp_mm`/`ratio_R`/`regime` (grid),
`trend_mm_yr`/`seasonal_mm` (timeseries) and `median_seasonal_amp_mm`/`median_ratio_R`/
`dominant_regime` (stats) REQUIRED, while on the 132-day window `harmonic_fit` measured
`annual_coverage 0.3614` against `MIN_SEASONAL_COVERAGE_PER_HARMONIC = 0.75` and a design condition
number of `66.84` against `MAX_DESIGN_CONDITION = 30` → `seasonal_identifiable False`, so publishing
the amplitude would be dishonest. That objection was right about the window, not about the field.

**Why the reversal is honest.** The window moves to **2 years — 62 dates at the 12-day cadence, 732
days** — where the same gates measure `annual_coverage 2.004` and condition `4.12` (two harmonics;
`4.02` is the one-harmonic design): identifiable with margin, including the 2-harmonic order
(`_harmonic_order` asks for two harmonics from a 1.5-yr span, which is why 1 yr was rejected; 0.75 yr
was a knife-edge at two days of slack).
So the published seasonal fields are a **fit result** from the same `harmonic_fit` the real path uses,
not a typed constant. Option B was rejected on 14 Nov for exactly the dishonesty this removes; option
C (nullable fields) is no longer needed, though it remains available if a future case cannot be
widened.

**`origin/main`'s 1.1 is not a model to copy.** It publishes `median_seasonal_amp_mm = 1.5`,
`median_ratio_R = 41.377` and `dominant_regime = "inelastic"` on a 132-day window while its own
`meta.limitations` says *"A ~4.5-month window cannot separate seasonal from secular motion: do not
publish seasonal_amp_mm from this stack"*. Both statements ship in the same artefact set; the
limitation is the one to believe. Copying the field list without widening the window would publish a
number the pipeline's own gate refuses. (Those three values happen to coincide with this branch's
Mexico City stats — the mock's amplitude is a config constant and the series is noise-free in time, so
the fitted number is the injected one — which is exactly why the window and the gates, not the digits,
are what make a published amplitude honest.)

**Status and owner.** Implemented on branch `feat/contract-1.1` (contract, mock generator, artefacts
and a new acceptance test). The bump's own fingerprint is
`ed92be802ff8846fe369058d9a80c9545c2a694a17896de6a5f1844146e302ae`, printed by
`./scripts/python.sh -m src.domain.contracts`; the retired 1.0 digest survives only in lines tagged
`fingerprint-historical` (a bare re-quote would fail `tests/test_fingerprint_consistency.py`).
`tests/test_contract_11_honesty.py` is the guard that keeps
this honest: it re-fits the published series with `harmonic_fit`, checks the coverage and
conditioning gates, checks the published medians/mode against the grid, and fails if a case's
`limitations` forbids publishing something the artefact publishes — the exact `origin/main` defect.
The contract's own rule requires all three roles' agreement for a `SCHEMA_VERSION` bump, so this
**needs Rol 1 and Rol 2 sign-off before it lands on a shared branch**. It is a proposal with its
evidence attached, not an approved change: nothing here implies the other roles have reviewed it.

### O-06 — Reconcile granule identity before the first real stack
`pipeline/ingest/02_search_download.py` and `pipeline/process_case.py` assigned different filename
tokens to `track`. The science parser could therefore reject a valid multi-date chain as a mix of
relative orbits. The 120-name census and its evidence are preserved in
`docs/pipeline-internals.md` §1.1, which stays the reference grammar; `docs/first-granule-runbook.md`
treated this as a known blocker rather than a hypothetical grammar mismatch.
**Blocks.** Processing the first downloaded multi-date stack.
**Close when.** Both parsers share or agree on the identity fields and a regression proves that a
valid connected multi-date chain passes `_check_stack_identity` while genuinely mixed geometry is
still refused.
**Resolved 2026-11-14 — fixed in both parsers, and in the evidence they produced.** The authoritative
order after the product token is `<repeat-cycle>_<track>_<A|D>_<frame>_<secondary-cycle>` (tokens
4–8). `pipeline/process_case.py`'s `_GRANULE_RE` now captures cycle, track, direction and frame in
that order, and `parse_granule_identity` returns the real track and frame;
`pipeline/ingest/02_search_download.py`'s `parse_granule` now reads `track = tokens[5]` and
`frame = tokens[7]`, and `derive_stacks` groups by `(track, frame, direction)`. Agreement is the
point: the fix is not one side patched to match a disagreement. The regressions that keep it closed
are `tests/test_process_case.py` (`test_mixed_tracks_are_refused` moves only the track token of an
otherwise connected chain; the connected-chain fixtures vary the cycle, not the track) and
`tests/test_availability_evidence.py` (`test_granule_identity`, `test_stack_consistency`, which make
every declared stack label equal the parser's reading of every id in it).
**Consequence, visible in the committed evidence.** `data/processed/_availability/{case}.json` was
re-derived offline from its own unchanged raw granule list — same ids, urls and sizes; only `stacks`
and `recommended_stack` were recomputed. The old stack labels were *frames*, so some "stacks" had
merged two distinct orbits: Málaga's label `020` pooled tracks `087` and `015`, which is where its
reported **2** connected epochs came from. Under the corrected grammar the recommended stacks are
Mexico City track 113 / frame 079 descending (33 granules, 22 epochs in one connected run), Jakarta
018 / 092 descending (8 granules, 9 epochs) and Málaga 095 / 070 descending (9 granules, 10 epochs)
— all `epoch_sufficient: true`. Málaga therefore flips from insufficient to sufficient. It remains the
negative control for *signal* magnitude (D-19), not for fittability, and no replacement "negative
control" is invented to keep the old story.

### O-07 — Preserve displacement sign in the frontend's 3-D height
A dated review of `origin/pozo` found elevation based on `|delta_change|`, clipped and scaled for
display. That makes equal-magnitude uplift and subsidence rise identically, while the artefact
interface requires height to bind to stored, signed `delta_change`. Re-check the actual merge
candidate rather than assuming that old branch snapshot is still current.
**Close when.** The integrated frontend has a parity test for height sign and magnitude, and any
purely visual exaggeration is applied without discarding the physical direction.

### O-08 — Reconcile the LOS phase sign with the projection convention
The default phase conversion is labelled `away-from-sensor` and uses
`d_LOS = -lambda/(4*pi)*phi`, with prose claiming that positive LOS is motion away and subsidence
is negative. But `project_to_los` computes `u · v` with `u_up = cos(theta) > 0`: with zero
horizontals it assigns positive LOS to positive vertical motion, and the worked example maps
downward motion to negative LOS. Those definitions cannot all describe one away-positive physical
LOS coordinate. Existing synthetic round trips prove algebraic consistency of the projection and
inverse, not the physical label.
**Naming closed 2026-11-15; validation still open.** The default label is now `toward-sensor` (the
factor is unchanged at −1.0), which is the direction `u_up = cos θ > 0` actually computes, and the
worked examples and the provenance string follow it. What the "Close when" below still requires is
the *external* GNSS/levelling validation, not a renaming.
**Blocks.** Physical interpretation or publication of any real-path velocity, independently of the
GNSS-validation requirement in D-10.
**Close when.** Phase conversion, forward/inverse projection, CLI labels, provenance strings, and
worked examples use one declared LOS direction; independent tests include the physical meaning of
purely vertical and known horizontal motion, not only round trips; and the external validation gate
is satisfied. Do not silently flip only one side: that could preserve the contradiction under a
different label.
**Deferred 2026-11-14 by owner decision — documented, not fixed, before the freeze.** Real-path only:
the mock synthesises millimetres and never converts phase, so every published number is unaffected.
It is named in `README.md` limitation 12, including which way the arithmetic actually points
(uplift ⇒ positive LOS). **The renaming this paragraph proposed shipped on 2026-11-15**: the default
label is `toward-sensor`, the provenance string records it, and neither arithmetic nor artefacts
changed. The remaining gap is validation, not naming.
