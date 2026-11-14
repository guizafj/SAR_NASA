# Necesidades de datos de la UI

Contrato de integración entre el shell de Rol 2 y los loaders/pipeline de Rol 1 y Rol 3.

## Ausente

- `src/data/loaders.py` implementa la lectura cacheada de los artefactos.
- `config/study_cases.yaml` define los casos y sus nombres.
- `pipeline/generate_mock.py` genera artefactos reproducibles para el shell.
- Los artefactos viven en `data/processed/mexico-city/` (y la misma estructura para cada caso):
  `nisar_grid.parquet`, `timeseries.csv`,
  `context_layers.geojson` y `meta.json`.

## Stub temporal

`src/ui/_stubs.py` (`# TODO(rol1)`) sirve como fallback visual cuando no hay artefactos.

Funciones que debe publicar Rol 1, con la misma firma:

- `load_study_cases() -> list[{id, label}]` desde `config/study_cases.yaml`.
- `load_meta(case_id) -> dict` del `data/processed/mexico-city/meta.json` o del directorio del caso.
- `load_land_use_options()` valores presentes en el grid, no el enum completo si el caso no los tiene.
- `load_risk_levels()` igual, desde el artefacto.

Los filtros viven en `st.session_state` y se pasan a las pestañas; `src/domain/filters.py` los aplica
antes de construir el payload del mapa.

## Mock y umbral

El contrato v1.1 incluye los campos de la tesis secular/estacional.

- Aviso **DATOS DE DEMOSTRACIÓN** si `product == "mock"` o `provenance.source` contiene `mock`. El contrato v1.1 ya define `mock`.
- La cabecera solo pinta `sensor`, `product`, `unit`, `dates`, `coherence_threshold`, y omite la clave que no esté.
- El slider usa `coherence_threshold` si existe; si no, `thresholds.coherence_min`. No se reintroduce `coherence_threshold`.
- El stub copia umbrales de `sensor_profile("nisar", "mock")`. No inventa fechas, stats, hashes ni `provenance_id`.

## Pestaña 2 y escala común

El stub calcula, la UI solo pinta. Faltan en Rol 1:

- `get_series_kpis(case_id, filters)` — velocidad mediana, % sobre umbral, celdas críticas, cobertura coherente, cada una con unidad y `provenance_id`.
- `get_case_series(case_id, filters)` y `get_land_use_bars(case_id, filters)`.
- `get_scale_comparison(scale_mode)` — picos ya convertidos a un eje de referencia. La UI no convierte unidades.
- El mock declara `cm/year` fuera del control y `mm` en el control para que la escala común deje el control en el extremo. No es una medición. El contrato v1.1 fija `unit = mm`; no se cambia aquí.

## Pestaña 3

`data/processed/mexico-city/context_layers.geojson` aporta las capas de contexto. La pestaña no
inventa dossier, mitigaciones ni índices.

## Pestaña 4

No hay texto científico aprobado por el Rol 3 en un módulo de UI. El README es borrador con cifras: no se muestra. Marcador hasta que el Rol 3 entregue el copy.
