# Backend ↔ Pipeline ↔ UI Interface Contract (Rol 1 — `pozo`)

This document closes the concrete integration seams between **Rol 3 (`yuls`, `pipeline/`)**,
**Rol 1 (`pozo`, `src/data/*` + `src/domain/*`)** and **Rol 2 (`fradiaz`, `app.py` + `src/ui/*`)**
without modifying the frozen data contract (`src/domain/contracts.py`, v1.0, fingerprint
`049376b0…`).

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

## 2. Python API for `app.py` & `src/ui/*` (Rol 1 → Rol 2)

`fradiaz` never reads files directly and never computes geophysical quantities, thresholds,
colours or H3 geometries in `src/ui/*`. Everything flows through four pure calls:

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
| `"shared"` (default) | `[-450, +50] mm/year` | `[-5, +5] mm/year` (`estable` / `NULL_RESULT_MM_YR`) | Scaled uniformly against `450 mm/year` (`MAX_EXTRUDE_METERS = 3000 m`) | CDMX rises in deep magenta towers; **Málaga renders visibly flat grey** |
| `"independent"` | Auto-fitted to city `p05..p95` / min..max (at least `[-8, +8] mm/year`) | `[-detection_limit, +detection_limit]` (e.g. `±1.5 mm/year` in Málaga) | Normalized to city peak | Reveals local spatial structure while keeping sub-detection noise grey |

When `kpis.is_null_result` is `True`, the UI **must** display `kpis.detection_limit_mm_yr`
(e.g. *"Málaga median velocity −1.2 mm/year — within detection limit ±1.5 mm/year"*).

---

## 4. Time Slider & Click-to-Interrogate Semantics

To stay strictly honest with what the frozen contract stores:

1. **Date range / scrub slider (`date_range`, `selected_date`):**
   - Slices `timeseries.csv` (`SeriesView.data`), recomputes linear trend `SeriesView.fitted_trend_mm_yr` over the selected window, and updates `CaseKPIs.aoi_cumulative_mm_at_date` + `CaseKPIs.aoi_window_delta_mm`.
   - `nisar_grid.parquet` stores full-period `velocity_mm_yr` and `delta_change = val_t2 - val_t1`; it is never synthetically interpolated per date in the UI.
2. **Cell click (`selected_cell_id`):**
   - Highlights the clicked H3 cell on the map (`is_selected=True`, white border).
   - If `timeseries.csv` contains rows for `cell_id == selected_cell_id`, `SeriesView` returns that cell's exact curve (`is_fallback_to_aoi = False`).
   - If per-cell rows were omitted to stay under the 15 MiB budget (contract §4), `SeriesView` returns the `"aoi"` curve with `is_fallback_to_aoi = True` alongside the clicked cell's static `cell_static_velocity_mm_yr`, `cell_coherence` and `cell_risk_level` from `nisar_grid.parquet` so the UI labels both honestly.
