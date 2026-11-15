# Backend ↔ Pipeline ↔ UI Interface Contract (Rol 1 — `pozo`)

This document closes the concrete integration seams between **Rol 3 (`yuls`, `pipeline/`)**,
**Rol 1 (`pozo`, `src/data/*` + `src/domain/*`)** and **Rol 2 (`fradiaz`, `app.py` + `src/ui/*`)**
without modifying the frozen data contract (`src/domain/contracts.py`, v1.1, fingerprint
`ed92be80…`).

---

## 1. Disk layout under `data/processed/` (Rol 3 → Rol 1)

`src/domain/contracts.py` enforces **one `case_id` per file** (`CASE_IDS = ("mexico-city", "jakarta", "malaga")`).
Therefore, the canonical multi-city layout on disk is:

```text
data/processed/
├── _availability/                  # ASF probe evidence (ignored by size budget & loaders)
├── mexico-city/
│   ├── nisar_grid.parquet
│   ├── timeseries.csv
│   ├── context_layers.geojson
│   └── meta.json
├── jakarta/                        # optional 2nd case
│   └── ...
└── malaga/                         # negative control (null result + detection_limit_mm_yr)
    └── ...
```

- **Flat single-case fallback:** `src/data/loaders.py` also detects a flat `data/processed/{meta.json,...}` layout automatically if `yuls` emits a single case directly into `data/processed/` during H0–2.
- **Schema precedence:** `src/domain/contracts.py` prevails over the legacy illustrative table in `flujo_trabajo_y_responsabilidades.md` §5.1 (do **not** emit undeclared columns such as `polarization`, and keep `sensor`/`product`/`case_id` in lowercase enum form).

---

## 2. Live 18-Hook Cockpit Seam (`src/data/ui_bridge.py`, re-exported by `src.data.loaders`)

`app.py` and `src/ui/*` consume the backend exclusively through the 18 ready-made hooks in
`src/data/ui_bridge.py` (re-exported by `src.data.loaders`), falling back to `src/ui/_stubs.py`
only if artefacts are absent:

| Hook | Purpose & key payload fields |
|---|---|
| `load_study_cases()` | `[{'id': slug, 'label': name}, ...]` in frozen `CASE_IDS` order |
| `load_meta(case_id)` | Validated `meta.json` + additive `provenance_id`, `provenance_fields` (`source`, `method`, `license`, `reference`, `processing_date`, `product_id`, `retrieved_utc`) and top-level aliases |
| `load_land_use_options(case_id=None)` | Distinct `land_use` classes present in the grid |
| `load_risk_levels(case_id=None)` | Distinct `risk_level` bands present in the grid |
| `get_map_layer_df(case_id, filters)` | H3 DataFrame with `color`, `height` (`0.0` when `extrude_3d=False` default; two-sided around `COLUMN_BASELINE_M = 1500 m` when `True`), `signed_delta_mm`, `motion_direction`, `regime`, `ratio_R`, `seasonal_amp_mm` |
| `get_color_scale(case_id, filters)` | `min`, `max`, `unit`, `saturated`, `metric`, `caption`, `stops` |
| `get_layer_counts(case_id, filters)` | `{'shown', 'total'}` without UI-side aggregation |
| `get_view_state(case_id)` | `{'latitude', 'longitude', 'zoom'}` from `meta.bbox` |
| `get_signal_status(case_id)` | `{'above_limit', 'detection_limit', 'detection_limit_unit'}` |
| `get_map_kpis(case_id, filters)` | Median velocity, median displacement, minimum coherence, median seasonal amplitude |
| `get_cell_detail(case_id, cell_id)` | Cell attributes (`seasonal_amp_mm`, `ratio_R`, `regime`, `coherence`, …) + `series` (`series_scope`: `'cell'` \| `'aoi'` \| `'none'`) |
| `load_timeseries(case_id, cell_id=None)` | `timeseries.csv` DataFrame slice |
| `get_series_kpis(case_id, filters)` | Tab 2 KPI cards (English labels, `cells` unit, fitted seasonal amplitude) |
| `get_case_series(case_id, filters)` | AOI series rows with `value`, `trend_mm_yr`, `seasonal_mm`, `scaled`, `scale_factor` |
| `get_land_use_bars(case_id, filters)` | Median `velocity_mm_yr` per present `land_use` |
| `get_scale_comparison(scale_mode)` | Cross-case peak `|velocity_mm_yr|`, `share`, dynamic `mixed_units`, `mixed_sensors`, `mixed_products`, `sensors`, `products` |
| `load_context_layers(case_id=None)` | GeoJSON + feature/layer summary |
| `load_documented_limits()` | Full README GNSS caveat paragraph, multi-line §13 items, `science_section_1` (`docs/science-guide.md` §1) and `case_roles` (`config/study_cases.yaml`) |

---

## 2b. Underlying Domain API (`src/domain/*`)

`fradiaz` never reads files directly and never computes geophysical quantities, thresholds,
colours or H3 geometries in `src/ui/*`. Beneath `ui_bridge.py`, everything flows through four pure calls:

```python
from src.data import list_available_cases, load_case_bundle, load_study_cases_config
from src.domain.filters import (
    FilterCriteria,
    apply_filters,
    available_filter_options,
    default_filter_criteria,
)
from src.domain.metrics import (
    build_city_comparison,
    build_provenance_badge,
    compute_kpis,
    extract_series_view,
)
from src.domain.map_adapter import build_map_payload, build_pydeck_layer_specs
```

### Step-by-step composition in `app.py`

```python
# 1. Discover published cases (`primary` from config/study_cases.yaml comes first)
cases = list_available_cases()                       # e.g. ["mexico-city", "malaga"]
bundle = load_case_bundle(selected_case_id)          # validated + mtime/fingerprint cached
options = available_filter_options(bundle)           # bounds, dates, sensor coherence_min

# 2. Build FilterCriteria from sidebar controls (seeded per sensor/product profile)
criteria = FilterCriteria(
    coherence_min=coh_slider,                        # defaults to options.sensor_coherence_min
    land_uses=tuple(selected_land_uses) or None,
    risk_levels=tuple(selected_risks) or None,
    min_abs_velocity_mm_yr=vel_threshold_slider,
    date_range=(start_date, end_date),
    selected_date=scrub_date,
    selected_cell_id=clicked_cell_id,                # H3 index from map click, or None
    context_layers=tuple(selected_ctx_layers) or None,
)
view = apply_filters(bundle, criteria)

# 3. Reactive KPIs, provenance badge & Plotly time-series view
badge = build_provenance_badge(bundle)               # sensor, product, revisit_days, is_mock, limitations
kpis = compute_kpis(view)                            # velocities, H3 km², m³/yr loss, null-result bound
series = extract_series_view(view)                   # AOI or cell curve + OLS trend + fallback flag

# 4. PyDeck 2D/3D map payload + shared/independent scale legend
map_payload = build_map_payload(
    view,
    metric="velocity_mm_yr",                         # "velocity_mm_yr" | "delta_change" | "coherence" | "risk_level"
    scale_mode="shared" if shared_toggle else "independent",
    extrude_3d=enable_3d,
)
deck_spec = build_pydeck_layer_specs(map_payload)
```

### Minimal `src/ui/map_view.py` wiring (no collision between Rol 1 and Rol 2)

`src/domain/map_adapter.py` owns all RGBA, H3 boundary, elevation and camera maths;
`src/ui/map_view.py` (Rol 2) only instantiates `pydeck.Deck`:

```python
import pydeck as pdk
import streamlit as st
from src.domain.map_adapter import MapPayload, build_pydeck_layer_specs

def render_map(payload: MapPayload) -> None:
    spec = build_pydeck_layer_specs(payload, use_polygon_layer=True, show_context=True)
    pdk_layers = [pdk.Layer(item["type"], **item["kwargs"]) for item in spec["layers"]]
    deck = pdk.Deck(
        layers=pdk_layers,
        initial_view_state=pdk.ViewState(**spec["view_state"]),
        tooltip=spec["tooltip"],
        map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    )
    st.pydeck_chart(deck, width="stretch")
```

---

## 3. Shared vs Independent Scale & Null-Result Rendering

| Mode | Domain (`velocity_mm_yr`) | Neutral grey floor | 3D Extrusion (`extrude_3d=True`) | Visual thesis |
|---|---|---|---|---|
| `"shared"` (default) | `[-425, +425] mm/year` (symmetric `RdBu` aligned with `docs/handoff-mock.md` §3b & `velocity_shared_scale.png`) | `[-5, +5] mm/year` (`estable` / `NULL_RESULT_MM_YR`) | **Signed** `\|delta_change\|` mapped two-sided around `COLUMN_BASELINE_M = MAX_EXTRUDE_METERS / 2 = 1500 m`, normalised against `425 mm/year * T` when shared: subsidence extrudes **downward**, uplift upward | CDMX sinks in deep crimson towers below the baseline; **Málaga's cells sit within metres of it** |
| `"independent"` | Seeded from `meta.stats.p05_velocity_mm_yr` / `p95_velocity_mm_yr` (at least `[-8, +8] mm/year`) | `[-detection_limit, +detection_limit]` (e.g. `±1.5 mm/year` in Málaga) | Normalised to city peak `\|delta_change\|`, two-sided around the same `COLUMN_BASELINE_M` | Reveals local spatial structure while keeping sub-detection noise grey |

When `kpis.is_null_result` is `True`, the UI **must** display `kpis.detection_limit_mm_yr`
(e.g. *"Málaga median velocity −1.4 mm/year — within detection limit ±1.5 mm/year"*).

---

## 4. Time Slider & Click-to-Interrogate Semantics

To stay strictly honest with what the frozen contract stores:

1. **Date range / scrub slider (`date_range`, `selected_date`):**
   - Slices `timeseries.csv` (`SeriesView.data`), recomputes linear trend `SeriesView.fitted_trend_mm_yr` over the selected window, and updates `CaseKPIs.aoi_cumulative_mm_at_date` + `CaseKPIs.aoi_window_delta_mm`.
   - `nisar_grid.parquet` stores full-period `velocity_mm_yr` and `delta_change = val_t2 - val_t1`; it is never synthetically interpolated per date in the UI.
2. **Cell click (`selected_cell_id`):**
   - Highlights the clicked H3 cell on the map (`is_selected=True`, white border).
   - If `timeseries.csv` contains rows for `cell_id == selected_cell_id`, `SeriesView` returns that cell's exact curve (`is_fallback_to_aoi = False`).
   - If per-cell rows were omitted to stay under the 15 MB budget (contract §4), `SeriesView` returns the `"aoi"` curve with `is_fallback_to_aoi = True` alongside the clicked cell's static `cell_static_velocity_mm_yr`, `cell_coherence` and `cell_risk_level` from `nisar_grid.parquet` so the UI labels both honestly.
