# Necesidades de datos de la UI

Contrato de integración entre el shell de Rol 2 y los loaders/pipeline de Rol 1 y Rol 3.

## Estado actual (post-reconciliación)

- `src/data/loaders.py` + `src/data/ui_bridge.py` publican los 18 hooks del cockpit (Rol 1).
- `config/study_cases.yaml` y artefactos en `data/processed/{case_id}/` están disponibles.
- `app.py` usa loaders reales cuando exportan las firmas requeridas; si no, cae a `src/ui/_stubs.py`.

## Stub temporal (fallback)

`src/ui/_stubs.py` solo para arranque sin artefactos.

Reglas de presentación del stub (no copiar a loaders reales):

- Coherencia baja **dispersa** (`h3.str_to_int(cell_id) % 5`), nunca por anillo: un filtro `coherence_min` no debe abrir huecos concéntricos que se lean como dos capas.
- `load_land_use_options` / `load_risk_levels`: solo valores presentes en el grid mock.
- KPIs del mapa usan `_display_unit(case_id)`, no `UNIT` fijo.

## Mapa (Rol 2)

- `src/ui/map_view.py` remonta el deck con `key=f"h3-map-{case_id}"` para aplicar `initial_view_state` al cambiar de caso.
- Con extrusión off, la UI fuerza `extrude_m=0` y `elevation_scale=0` sin alterar `height` (que conserva siempre `delta_change` con signo, O-07).
- Colores y dominio vienen de `get_color_scale` / fila `color`; la UI no recalcula magnitudes.

## Mock y umbral

- Aviso **DATOS DE DEMOSTRACIÓN** si `product == "mock"` o `provenance.source` contiene `mock`.
- Cabecera: `sensor`, `product`, `unit`, `dates`, `coherence_threshold` (omite ausentes).
- Slider: `coherence_threshold` o `thresholds.coherence_min`.

## Pestañas 2–4

- Series/KPIs/escala: hooks en `ui_bridge` (`get_series_kpis`, `get_case_series`, `get_land_use_bars`, `get_scale_comparison`). La UI solo pinta.
- Pestaña 3: `load_context_layers()`; sin dossier ni mitigaciones inventadas.
- Pestaña 4: sin copy científico de Rol 3; solo caveat README cuando el loader lo expone.
