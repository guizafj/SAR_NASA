# Backend ↔ Pipeline ↔ UI Interface Contract (Rol 1 — `pozo`)

This document closes the concrete integration seams between **Rol 3 (`yuls`, `pipeline/`)**,
**Rol 1 (`pozo`, `src/data/*` + `src/domain/*`)** and **Rol 2 (`fradiaz`, `app.py` + `src/ui/*`)**
without modifying the frozen data contract (`src/domain/contracts.py`: `SCHEMA_VERSION = "1.1"`,
`CONTRACT_REVISION = 2`, fingerprint
`ed92be802ff8846fe369058d9a80c9545c2a694a17896de6a5f1844146e302ae` — re-derive it with
`./scripts/python.sh -m src.domain.contracts` on the commit you are claiming, never from memory).

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
├── jakarta/                        # published extension case
│   └── ...
└── malaga/                         # negative control (null result + detection_limit_mm_yr)
    └── ...
```

- **Flat single-case fallback:** `src/data/loaders.py` also detects a flat `data/processed/{meta.json,...}` layout automatically if `yuls` emits a single case directly into `data/processed/` during H0–2.
- **Schema precedence:** `src/domain/contracts.py` prevails over the legacy illustrative table in `flujo_trabajo_y_responsabilidades.md` §5.1 (do **not** emit undeclared columns such as `polarization`, and keep `sensor`/`product`/`case_id` in lowercase enum form).

---

## 2. Python API for `app.py` & `src/ui/*` (Rol 1 → Rol 2)

`fradiaz` never reads Parquet/CSV/GeoJSON/`meta.json` and never computes geophysical quantities,
thresholds, colour **domains** or H3 geometries in `src/ui/*` (the one presentation fallback is
`FALLBACK_DOMAIN` in `src/ui/color_scale.py`, used only when `get_color_scale` hands back no
domain). Everything reaches the UI through the hooks in **`src/data/ui_bridge.py`** — the 18 names
in its `__all__`. `src/data/loaders.py` re-exports all of them, `app.py._loaders()` resolves them
from there, and it drops to `src/ui/_stubs.py` only if `src.data.loaders` cannot be imported or
is missing one of the eight hooks `app.py` requires.
Every hook takes `case_id` first and `processed_dir=` as a **keyword-only** path (default
`loaders.DEFAULT_PROCESSED_DIR`), so the same call can be pointed at a fixture tree without
touching the published artefacts.

| Hook | Signature | Returns |
|---|---|---|
| `load_study_cases` | `(config_path=DEFAULT_STUDY_CASES_PATH)` | `[{"id", "label"}]` in frozen `CASE_IDS` order |
| `load_meta` | `(case_id, *, processed_dir=…)` | validated `meta.json` `dict` + `unit` and `provenance_id` |
| `load_land_use_options` | `(case_id=None, *, processed_dir=…)` | `tuple[str, ...]` of the `land_use` classes actually present |
| `load_risk_levels` | `(case_id=None, *, processed_dir=…)` | `tuple[str, ...]` of the present `RISK_LEVELS` bands |
| `get_map_layer_df` | `(case_id, filters=None, *, processed_dir=…)` | `DataFrame` with pre-computed `color` (RGBA) and `height` (m), plus `regime`, `ratio_R`, `seasonal_amp_mm` |
| `get_color_scale` | `(case_id, filters=None, *, processed_dir=…)` | `{min, max, unit, saturated, fallback, mixed_units, metric, caption, stops}` |
| `get_layer_counts` | `(case_id, filters=None, *, processed_dir=…)` | `{"shown", "total"}` |
| `get_view_state` | `(case_id, *, processed_dir=…)` | `{"latitude", "longitude", "zoom"}` computed from `meta.bbox` |
| `get_signal_status` | `(case_id, *, processed_dir=…)` | `{above_limit, detection_limit, detection_limit_unit}` |
| `get_map_kpis` | `(case_id, filters=None, *, processed_dir=…)` | `[{label, value, unit, provenance_id}]` |
| `get_series_kpis` | `(case_id, filters=None, *, processed_dir=…)` | `[{label, value, unit, provenance_id, note}]` |
| `get_case_series` | `(case_id, filters=None, *, processed_dir=…)` | `[{date, value, trend_mm_yr, seasonal_mm, unit, provenance_id, scaled, scale_factor}]` |
| `get_land_use_bars` | `(case_id, filters=None, *, processed_dir=…)` | `[{land_use, value, unit, provenance_id}]` |
| `get_scale_comparison` | `(scale_mode="shared", *, processed_dir=…)` | `{reference_unit, mixed_units, cases: [{id, unit, peak, share, provenance_id}]}` |
| `get_cell_detail` | `(case_id, cell_id, *, processed_dir=…)` | cell attributes + `series` and `series_scope` (`"cell"` \| `"aoi"` \| `"none"`), or `None` |
| `load_timeseries` | `(case_id, cell_id=None, *, processed_dir=…)` | `DataFrame` of `timeseries.csv` (the whole case when `cell_id is None`) |
| `load_context_layers` | `(case_id=None, *, processed_dir=…)` | `{path, geojson, summary: {case_id, n_features, layers}}`, or `None` |
| `load_documented_limits` | `(readme_path=ROOT_DIR/"README.md")` | `{section_8_caveat, section_13}` read verbatim from `README.md` |

The `filters` mapping is a plain `dict` (`case_id`, `land_use`, `coherence_min`, `risk_level`,
`extrude_3d`, `scale_mode`). `_dict_to_criteria` maps only `land_use`, `risk_level` and
`coherence_min` onto `FilterCriteria`; `_resolve_scale_mode` maps `scale_mode` onto `"shared"`
for the exact shared labels and onto `"independent"` for everything else, `None` included.

### Domain layer underneath the bridge (Rol 1) — available, not imported by the UI

`src/data/ui_bridge.py` wraps `src.domain.filters.apply_filters`,
`src.domain.map_adapter.build_map_payload` / `compute_view_state` and
`src.domain.metrics.compute_kpis`. `app.py` and `src/ui/*` as merged import **none** of the
`src.domain.*` modules directly: a consumer that does takes over caching and validation itself.

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

### Step-by-step composition (target, not current behaviour)

The calls below exist with these signatures and are what the bridge composes internally, but no
hook publishes `FilteredCaseView`, `CaseKPIs`, `SeriesView`, `ProvenanceBadge` or
`CityComparisonResult`, and `build_provenance_badge`, `build_city_comparison`,
`extract_series_view`, `available_filter_options`, `default_filter_criteria` and
`build_pydeck_layer_specs` are not wired into the merged UI.

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

### `src/ui/map_view.py` as merged (no collision between Rol 1 and Rol 2)

`src/domain/map_adapter.py` owns all RGBA, H3 boundary, elevation and camera maths;
`src/ui/map_view.py` (Rol 2) only draws what the hooks hand it. It calls `get_map_layer_df` (fill
`color`, `height`, tooltip columns), `get_layer_counts`, `get_view_state`, `get_color_scale`,
`get_scale_comparison`, `get_map_kpis`, `get_signal_status`, `load_context_layers` and
`get_cell_detail`, then instantiates `pdk.Deck` itself with an `H3HexagonLayer`
(`get_fill_color="color"`, `get_elevation="height"`, `extruded=extrude_3d`, `coverage=0.9`),
`map_provider="carto"`, `map_style` `"light"`/`"dark"` from `reported_scheme()`, and
`st.pydeck_chart(deck, height=MAP_HEIGHT, width="stretch", on_select="rerun",
selection_mode="single-object")`.

**Target, not current behaviour:** the `MapPayload` → `build_pydeck_layer_specs` path
(`PolygonLayer` over pre-computed `[lon, lat]` rings, so no browser-side `h3-js` fetch). No hook
returns a `MapPayload` and no UI module imports it today:

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
| `"shared"` (the `build_map_payload` default) | `[-425, +425] mm/year` (symmetric `RdBu` aligned with `docs/handoff-mock.md` §3b & `velocity_shared_scale.png`) | `[-5, +5] mm/year` (`estable` / `NULL_RESULT_MM_YR`) | **Signed** `\|delta_change\|` mapped two-sided around `COLUMN_BASELINE_M = MAX_EXTRUDE_METERS / 2 = 1500 m`, normalised against `425 mm/year * T` when shared: subsidence extrudes **downward**, uplift upward | CDMX sinks in deep crimson towers below the baseline; **Málaga's cells sit within metres of it** |
| `"independent"` | Seeded from `meta.stats.p05_velocity_mm_yr` / `p95_velocity_mm_yr` (at least `[-8, +8] mm/year`) | `[-detection_limit, +detection_limit]` (e.g. `±1.5 mm/year` in Málaga) | Normalised to city peak `\|delta_change\|`, two-sided around the same `COLUMN_BASELINE_M` | Reveals local spatial structure while keeping sub-detection noise grey |

The table's `"shared"` default is the domain call's, not the app's: `render_map_tab` seeds
`st.session_state.scale_mode = "independent"` and `ui_bridge._resolve_scale_mode` answers
`"independent"` for anything that is not an explicit shared label, so the merged cockpit opens on
the independent scale.

When the case is a declared null result (`|median velocity| < NULL_RESULT_MM_YR`, which is what
`CaseKPIs.is_null_result` tests) the UI **must** display the published bound. It does, but through
`get_signal_status`, not through `kpis`: the hook returns `above_limit=False`, `detection_limit`
and `detection_limit_unit` from `meta.stats.detection_limit_mm_yr`, `detection_caption` in
`src/ui/map_view.py` renders *"No detectable signal above the detection limit of …"*, and
`src/ui/educational.py` lists the same field. The bound is quoted from the artefact, never
recomputed.

---

## 4. Time Slider & Click-to-Interrogate Semantics

To stay strictly honest with what the frozen contract stores:

1. **Date range / scrub slider (`date_range`, `selected_date`):**
   - Slices `timeseries.csv` (`SeriesView.data`), recomputes linear trend `SeriesView.fitted_trend_mm_yr` over the selected window, and updates `CaseKPIs.aoi_cumulative_mm_at_date` + `CaseKPIs.aoi_window_delta_mm` (`src/domain/filters.py`, `src/domain/metrics.py`).
   - `nisar_grid.parquet` stores full-period `velocity_mm_yr` and `delta_change = val_t2 - val_t1`; it is never synthetically interpolated per date in the UI.
2. **Cell click (`selected_cell_id`):**
   - Payload support: whenever `criteria.selected_cell_id` is set, `build_map_payload` marks that cell (`is_selected=True`, alpha 245, white `line_color`) in `src/domain/map_adapter.py`.
   - If `timeseries.csv` contains rows for `cell_id == selected_cell_id`, `SeriesView` returns that cell's exact curve (`is_fallback_to_aoi = False`).
   - If per-cell rows were omitted to stay under the 15 MB budget (contract §4), `SeriesView` returns the `"aoi"` curve with `is_fallback_to_aoi = True` alongside the clicked cell's static `cell_static_velocity_mm_yr`, `cell_coherence` and `cell_risk_level` from `nisar_grid.parquet` so the UI labels both honestly.

**Status as merged (target vs current behaviour).** The semantics above hold in
`src/domain/filters.py` and `src/domain/metrics.py`, but the merged cockpit does not drive all of
them yet:

- No date control exists in `app.py`, and `ui_bridge._dict_to_criteria` carries only `land_use`,
  `risk_level` and `coherence_min`, so `date_range` / `selected_date` are a target, not current
  behaviour.
- The click stores `st.session_state.selected_cell_id` and calls `get_cell_detail`, which reports
  `series_scope` (`"cell"` / `"aoi"` / `"none"`) rather than a `SeriesView`. `selected_cell_id` is
  never added to the `filters` mapping, so the payload's `is_selected` / white `line_color` are
  computed and then unused, and the visible highlight is deck.gl's `auto_highlight`.
