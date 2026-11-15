# Verification — what green means

This guide explains the gates behind the commands in [`CONTEXT.md`](../CONTEXT.md). It intentionally
omits captured test counts, artefact sizes, and medians: those are regenerated facts, not durable
documentation. The scripts and their tests are authoritative.


```bash
./scripts/check_phase_a.sh      # ~15 s: contract self-check, suites, artefact gate, handoff table
./scripts/preflight.sh          # ~5 min: seven hard gates, per-gate table, VERDICT: READY|NOT READY
./scripts/preflight.sh --fast   # skips gate 2 only (the clone + doc guards still run)
./scripts/python.sh -m pytest tests        # full suite (~4.5 min)
./scripts/python.sh -m src.domain.contracts    # positive + negative self-check, prints fingerprint
./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed   # fail-closed file gate
```

`process_case` has a known fail-open boundary: if it cannot run the external file gate, it can still
exit 0 after in-memory checks. Its exit status alone is therefore insufficient; run
`pipeline/validate_artifacts.py` or one of the gates below independently.

Always run through `./scripts/python.sh` (venv plus Nix `libstdc++`/`libz` paths), never
`.venv/bin/python`: the Nix interpreter does not put those libs on a pip wheel's search path, and
only ELFCLASS64 store builds are injected. `pytest.ini` is `testpaths = tests` plus `addopts = -q`,
and because the gates pass a second `-q`, **pytest prints no test count** — the exit code is the
evidence, not a remembered number.

What each gate actually asserts:

| Gate | Pass condition |
|---|---|
| `check_phase_a.sh` 1/4 | contract self-check exits 0 (the `contract fingerprint:` line is asserted by `preflight.sh` gate 1, not here) |
| `check_phase_a.sh` 2/4 | the **artefact-facing suites** pass (contracts, contract docs, config consistency, mock expectations, artefact capabilities, honesty claims, repo manifest, readme availability, grid export, docs links, artefact validation) — deliberately scoped so a module mid-development elsewhere cannot fail the handoff |
| `check_phase_a.sh` 3/4 | `data/processed/*/nisar_grid.parquet` exists, **then** the file gate + 15 MB budget; an empty tree gets its own failure message |
| `check_phase_a.sh` 4/4 | handoff table prints, and the **symmetric** provenance rule holds: mock says mock, real does not |
| `preflight.sh` 1–7 | the script's own labels: contract self-check · pytest suite (slow) · artefact gate (size) · phase A/B handoff · clone reproduction · figures regenerate · demo numbers. Gate 5 is launched in the background and its row is sorted back to position 5, so the table reads 1–7 while execution interleaves |
| `preflight.sh` semantics | keeps going after a failure; `READY` only with zero FAILs; SKIPs are reported, not failed; two NOTES warn on a dirty tree and on clone-vs-tree size divergence |

Gate 5 is the one that catches "works only on my machine": it clones the committed state read-only,
symlinks `.venv`, and re-runs the contract self-check, the artefact gate **and the four filesystem-only
doc guards** (`test_docs_commands`, `test_docs_links`, `test_readme_numbers`,
`test_pipeline_readme_numbers`) inside the clone; a failure there is reported as "committed doc breaks
a guard". It runs in the background in both modes and is sorted back into table position 5. Gate 6
writes the three figures to a temp `--out`, never to `docs/figures/`.

The **size budget is a feature**: compact Parquet artefacts let the demo ship in the repository
and load without a geospatial service. Re-run the gate for current sizes rather than copying them
into this guide.

What the suite pins, in families (the table names the main ones; the handoff gate runs only the
artefact-facing subset):

| Family | Files | Invariant |
|---|---|---|
| contract | `test_contracts.py`, `test_contract_docs.py` | field order, enums, profiles and risk bands; the code half and the prose half agree; the four validator negatives |
| contract 1.1 honesty | `test_contract_11_honesty.py` | the seasonal grid/series/stats fields exist and carry values, re-running `harmonic_fit` on the published series passes the coverage and conditioning gates, the published medians/mode match the grid, and no limitation forbids what the artefacts publish |
| seasonal not-fitted | `test_seasonal_not_fitted.py` | a null `ratio_R` is accepted exactly where `seasonal_amp_mm == 0` (no harmonic fitted) and rejected where an amplitude was fitted, `ratio_R` remains a required column even when every value is undefined, the ordinary numeric columns (`velocity_mm_yr`, `coherence`) still reject nulls and an amplitude is still non-negative, `stats.median_ratio_R` may be JSON `null` when no cell fitted a harmonic (and is still non-negative and still a required key), and no published grid hides a defined ratio behind a null |
| artefact gate | `test_validate_artifacts.py` | injected defects are caught, orphan provenance and size overruns fail, an empty tree fails closed |
| science transforms | `test_science_transforms.py`, `test_science_constants_consistency.py` | every formula matches an independent computation — **and constants quoted in the guarded docs still match the code** |
| real path | `test_process_case.py`, `test_pipeline_integration.py`, `test_process_case_convention.py` | synthetic HDF5 fixtures → a contract-valid tree; refusals fire; the four convention flags behave predictably |
| mock | `test_mock_expectations.py`, `test_mock_reproducibility.py` | seed 7 reproduces the artefacts byte-identically, and seed 8 differs (so the check is not vacuous) |
| documented numbers | `test_readme_numbers.py`, `test_pipeline_readme_numbers.py`, `test_handoff_numbers.py`, `test_handoff_snippets.py`, `test_demo_numbers.py`, `test_video_script_numbers.py`, `test_readme_availability.py` | anything quoted in a document is re-derived from the artefacts |
| honesty | `test_honesty_claims.py` (+ the convention and doc-number suites) | mock/synthetic labels, the window caveat (a short window cannot separate seasonal from secular motion, so the amplitude is only published where the gates pass), **the not-fitted complement of that caveat** — a stack below the seasonal gate publishes `seasonal_amp_mm` as exactly `0.0` meaning "not fitted", a null `ratio_R` and the cannot-classify `regime`, with `meta.limitations` saying so, instead of inventing a fit — 80 m posting, the published detection bound, ≥ 3 substantive limitations |
| config | `test_config_consistency.py`, `test_aoi_geometry.py` | `study_cases.yaml` ↔ `ingest/aois.json` ↔ `config/aoi/*.geojson` agree; rings validate |
| docs & manifest | `test_docs_links.py`, `test_repo_manifest.py`, `test_gitignore_policy.py` | links and anchors resolve, critical files exist and are executable, nothing required is git-ignored |
| ingest guards | `test_download_guard.py`, `test_download_auth.py`, `test_download_stack.py` | product identity, geometry, budget and partial-file guards; exit codes |
| granule reader | `test_nisar_gunw.py`, `test_nisar_gunw_robustness.py`, `test_inspect_hdf5.py` | HDF5 paths, dtypes, mask semantics; absence fails closed |
| gates & runbook | `test_preflight.py`, `test_runbook_constants.py`, `test_rehearse_first_granule.py` | gate semantics, the runbook's constants, the first-granule rehearsal |

---

