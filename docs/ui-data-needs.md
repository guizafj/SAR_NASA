# Necesidades de datos de la UI

Shell de Rol 2. No se tocó `src/data/` ni `pipeline/`.

## Ausente

- `src/data/loaders.py` (pendiente, Rol 1).
- `config/study_cases.yaml` (pendiente). El selectbox usa los slugs de `CASE_IDS`, sin nombres traducidos.
- `pipeline/generate_mock.py` no está. No se ejecutó ningún generador.
- Artefactos `nisar_grid.parquet`, `timeseries.csv`, `context_layers.geojson`, `meta.json`: no están.

## Stub temporal

`src/ui/_stubs.py` (`# TODO(rol1)`). La app lo usa solo si `src.data.loaders` no exporta las cuatro funciones de abajo. No lee artefactos.

Funciones que debe publicar Rol 1, con la misma firma:

- `load_study_cases() -> list[{id, label}]` desde `config/study_cases.yaml`.
- `load_meta(case_id) -> dict` del `meta.json` real. Confirmar ruta: `data/processed/meta.json` o `data/processed/{case_id}/meta.json` (`app.py` antiguo usaba la segunda; el contrato cita la primera).
- `load_land_use_options()` valores presentes en el grid, no el enum completo si el caso no los tiene.
- `load_risk_levels()` igual, desde el artefacto.

Los filtros viven en `st.session_state` y se pasan a las pestañas. No se aplican aquí. Hace falta `src/domain/filters.py` (pendiente, Rol 1).

## Mock y umbral

No hace falta un campo nuevo.

- Aviso **DATOS DE DEMOSTRACIÓN** si `product == "mock"` o `provenance.source` contiene `mock`. El contrato v1.1 ya define `mock`.
- La cabecera solo pinta `sensor`, `product`, `unit`, `dates`, `coherence_threshold`, y omite la clave que no esté.
- El slider usa `coherence_threshold` si existe; si no, `thresholds.coherence_min`. No se reintroduce `coherence_threshold`.
- El stub copia umbrales de `sensor_profile("nisar", "mock")`. No inventa fechas, stats, hashes ni `provenance_id`.

## Pestaña 4

No hay texto científico aprobado por el Rol 3 en un módulo de UI. El README es borrador con cifras: no se muestra. Marcador hasta que el Rol 3 entregue el copy.
