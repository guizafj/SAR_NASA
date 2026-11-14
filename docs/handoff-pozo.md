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
4. **Map heights keep the physical sign (O-07, closed):** `get_map_layer_df()` returns `height`
   equal to the stored signed `delta_change` [mm] plus `motion_direction`
   (`subsidence`|`uplift`|`zero`). The PyDeck column height is a *separate* visual field,
   `extrude_m`: it extrudes downward subsidence only (`max(0, -delta_change)`) and stays `0.0` for
   uplift and in flat mode, so `+50 mm` never rises like `-50 mm`. Never reintroduce
   `abs(delta_change)` as a substitute for the sign — `tests/test_height_sign_parity.py` fails if
   you do.
4. **How artefact hot-swap (mock → real) works:** Drop the four files into
   `data/processed/{case_id}/`. `loaders.py` keys its cache by file `mtime_ns` +
   `contract_fingerprint()`, validates every rule before returning, and needs zero UI changes.
5. **How artefact hot-swap (mock → real) works:** Drop the four files into
   `data/processed/{case_id}/`. `loaders.py` keys its cache by file `mtime_ns` +
   `contract_fingerprint()`, validates every rule before returning, and needs zero UI changes.
6. **The one thing that will break if ignored:** Emitting an undeclared column (e.g. `polarization`),
   mismatching `meta.stats.n_cells != len(grid)`, or omitting `detection_limit_mm_yr` when
   `|median_velocity_mm_yr| < 5` (Málaga) causes `ArtifactValidationError` by design — fix the
   pipeline output, never silence `validation.py`.
7. **Which contract is frozen:** `v1.0`, fingerprint
   `049376b00f9f23906b1ccaa2b9555699e14a8d3bec9fcb6ba0fc0d336678ce1e`, revision `1`. Schema `1.1`
   (seasonal amplitude / `ratio_R` / regime) was **rejected** by the science owner
   (`docs/decisions.md` D-28 / O-05) and is never published; `tests/test_flujo_contract_section.py`
   keeps every prose copy of the contract honest, not just `docs/data-contract-v1.md`.
