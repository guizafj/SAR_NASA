# Handoff — `pozo` (Rol 1: Geospatial Backend, Map Adapter, CI & Deploy)

1. **What I own:** `src/data/{validation,loaders}.py`, `src/domain/{filters,metrics,map_adapter}.py`,
   `.github/workflows/ci.yml`, `deploy/`, `requirements.txt` / `requirements-app.txt`, and
   `docs/backend-ui-contract.md`.
2. **How to run tests & checks locally:**
   ```bash
   ./scripts/python.sh -m src.domain.contracts
   ./scripts/python.sh -m pytest tests -q
   ./scripts/python.sh -m src.data.validation --processed data/processed
   ```
3. **How the UI consumes my layer:** Call `load_case_bundle(case_id)` → `apply_filters(bundle, criteria)`
   → `compute_kpis(view)`, `build_provenance_badge(bundle)`, `extract_series_view(view)`, and
   `build_map_payload(view, scale_mode="shared"|"independent", extrude_3d=bool)`. Full signatures
   and PyDeck snippet are in `docs/backend-ui-contract.md`.
   The cockpit's 18 ready-made hooks live in `src/data/ui_bridge.py` and are re-exported by
   `src.data.loaders` (`load_study_cases`, `get_map_layer_df`, `get_map_kpis`, `get_cell_detail`,
   `load_timeseries`, `get_scale_comparison`, …): `app.py` imports only from `src.data.loaders` and
   stays unchanged if the artefacts are swapped.
4. **How artefact hot-swap (mock → real) works:** Drop the four files into
   `data/processed/{case_id}/`. `loaders.py` keys its cache by file `mtime_ns` +
   `contract_fingerprint()`, validates every rule before returning, and needs zero UI changes.
5. **The one thing that will break if ignored:** Emitting an undeclared column (e.g. `polarization`),
   mismatching `meta.stats.n_cells != len(grid)`, or omitting `detection_limit_mm_yr` when
   `|median_velocity_mm_yr| < 5` (Málaga) causes `ArtifactValidationError` by design — fix the
   pipeline output, never silence `validation.py`.
6. **How to prove CI will be green before pushing (Python 3.12 parity):** GitHub Actions runs
   `3.12`, this WSL only ships `3.14`, so the suite can pass locally and still fail upstream
   (different `repr(float)`, different BLAS paths). Build a throwaway parity venv — it is ignored by
   `.gitignore` (`.*`), so nothing lands in the tree:
   ```bash
   uv python install 3.12
   uv venv --python 3.12 --seed .venv312
   .venv312/bin/python -m pip install -r requirements.txt
   SAR_PYTHON=.venv312/bin/python ./scripts/python.sh -m pytest tests -q
   ```
   `SAR_PYTHON` is the wrapper's interpreter override (`scripts/python.sh` pins it to an absolute
   path, and `scripts/preflight.sh` gives the clone gate a symlinked `.venv` so the override cannot
   leak in as a relative path). Verified on `3.12.16` / `pandas 3.1.1` / `pyarrow 26.0.1`:
   contract self-check, the full `pytest tests -q` suite, the artefact gate, `check_phase_a.sh` and
   `preflight.sh --fast` are all green.
7. **O-07 3D height sign decision (final, unified on `COLUMN_BASELINE_M`):** The two-branch split
   (`extrude_m` vs `COLUMN_BASELINE_M`) is retired in favour of `COLUMN_BASELINE_M =
   MAX_EXTRUDE_METERS / 2 = 1500 m` in `src/domain/map_adapter.py`: when `extrude_3d=True`,
   `height` (`elevation_m`) maps signed `delta_change` two-sided around `1500 m` (subsidence `<
   1500 m`, uplift `> 1500 m`, zero `== 1500 m`); in flat mode (`extrude_3d=False`, the default),
   `height == 0.0` to prevent WebGL z-fighting while `signed_delta_mm` and `motion_direction`
   preserve exact unrounded displacement sign and direction. Pinned by
   `tests/test_height_sign_parity.py`.

