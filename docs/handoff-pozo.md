# Handoff — `pozo` (Rol 1: Geospatial Backend, Map Adapter, CI & Deploy)

1. **What I own:** `src/data/{validation,loaders}.py`, `src/domain/{filters,metrics,map_adapter}.py`,
   `.github/workflows/ci.yml`, `deploy/`, `requirements.txt` / `requirements-pipeline.txt`, and
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
4. **How artefact hot-swap (mock → real) works:** Drop the four files into
   `data/processed/{case_id}/`. `loaders.py` keys its cache by file `mtime_ns` +
   `contract_fingerprint()`, validates every rule before returning, and needs zero UI changes.
5. **The one thing that will break if ignored:** Emitting an undeclared column (e.g. `polarization`),
   mismatching `meta.stats.n_cells != len(grid)`, or omitting `detection_limit_mm_yr` when
   `|median_velocity_mm_yr| < 5` (Málaga) causes `ArtifactValidationError` by design — fix the
   pipeline output, never silence `validation.py`.
