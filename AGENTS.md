# AGENTS.md — Rol 2 (frontend)

Eres Rol 2 (fradiaz) en el NASA Space Apps 2026, "Dancing with the SARs". Imperativo: no escribas código de otros roles ni inventes lo que el repo no contiene. Lo ausente es "(pendiente)".

## Proyecto

Construye un cockpit web en Streamlit que traduzca observaciones SAR en indicadores de subsidencia por extracción de agua subterránea.

- Fuente: NISAR L2 (GUNW/GCOV) o respaldo declarado (OPERA DISP-S1, Sentinel-1/HyP3, EGMS). El `product` lo declara el artefacto.
- Casos: `mexico-city`, `jakarta`, `malaga` (control negativo). Slugs en `src/domain/contracts.py` (`CASE_IDS`).
- Nombres y parámetros: `config/study_cases.yaml`. No hardcodees nombres, cifras ni umbrales.
- El flujo trata Yakarta como extensión. Si no hay artefacto, muestra ausencia. No fabriques el caso.

## Arquitectura

Pipeline offline → artefactos → loaders (`src/data`) → UI (`src/ui`).

Artefactos de Rol 3, por caso, en `data/processed/{case_id}/`: `nisar_grid.parquet`, `timeseries.csv`, `context_layers.geojson`, `meta.json`. Publicados hoy (`product` `mock`) para `mexico-city`, `jakarta` y `malaga`. También está `data/processed/_availability/*.json`.

La app NUNCA descarga ni procesa HDF5 ni calcula magnitudes geoespaciales. `docs/route-contract.md` (PMTiles) es post-MVP: no lo implementes.

## Propiedad por rol

- Rol 1 (pozo): `src/data/`, `src/domain/`, CI, datos map-ready.
- Rol 2 (fradiaz): `app.py`, `src/ui/` (`charts.py`, `kpi_cards.py`, `educational.py`, `theme.py`, `context_view.py`, `formatters.py`, `color_scale.py`, `_stubs.py`), `.streamlit/config.toml`.
- `src/ui/map_view.py`: compartido Rol 1 + Rol 2.
- Rol 3 (yuls): `pipeline/`, `data/processed/`, capas GIS, narrativa y TODO texto científico.

`src/data/` y `src/ui/` existen. Si un cambio toca archivos de otro rol, no lo hagas: añade la petición a `docs/requests.md`.

## Reglas duras

- Usa solo los loaders de `src/data`. No leas Parquet, CSV, GeoJSON ni `meta.json` desde la UI. `app.py` solo compone: no lee artefactos ni hardcodea nombres.
- No modifiques unidades ni umbrales recibidos. No dupliques filtros ni agregaciones del backend (`src/domain/filters.py`, `src/domain/metrics.py`).
- No cambies el contrato. Proponlo en `docs/contract-change-requests.md`. Exige aprobación de los 3 roles. No edites `src/domain/contracts.py`.
- Mantén `main` ejecutable. No trabajes sobre `main`.

## Contrato

Fuente de verdad: `src/domain/contracts.py` (`SCHEMA_VERSION` **1.1**, revisión 2), descrito en `docs/data-contract-v1.md`. Si discrepan, manda el código. El borrador v1.0 (sin `velocity_mm_yr`, sin `cell_id` en la serie, `coherence_threshold` suelto) está superado: no lo reimplementes.

| Artefacto | Obligatorios |
|---|---|
| `nisar_grid.parquet` | `cell_id` (H3), `lat`, `lon`, `val_t1`, `val_t2`, `delta_change` (mm), `velocity_mm_yr`, `seasonal_amp_mm`, `ratio_R`, `regime`, `coherence` (0–1), `land_use`, `risk_level`, `case_id`, `sensor`, `product`, `unit` (`mm`), `velocity_unit` (`mm/year`), `source`, `provenance_id`. `acquisition_date` opcional. |
| `timeseries.csv` | `case_id`, `cell_id` (`aoi` = agregado), `date`, `value`, `trend_mm_yr`, `seasonal_mm`, `unit`, `sensor`, `product`, `quality`, `provenance_id`. |
| `meta.json` | `schema_version`, `case_id`, `name`, `bbox`, `crs`, `reference_point`, `dates`, `revisit_days`, `sensor`, `product`, `thresholds`, `provenance` {`source`, `product_id`, `processing_date`, `method`, `retrieved_utc`, `reference`, `license`}, `stats`, `limitations`, `null_policy`. |
| `context_layers.geojson` | `layer`, `class`, `case_id`, `crs` (`EPSG:4326`), `source`, `provenance_id`. `name` opcional. |

`provenance_id` = `{case_id}+{product}+{YYYYMMDD}+{sha256_8}`. Umbrales solo en `SENSOR_PROFILES`, por `(sensor, product)`. Positivo = hacia arriba. `product == "mock"` es de primera clase. No publiques `opera-disp-s1` para estos AOI: el contrato lo marca ausente.

## Contenido científico

- No inventes cifras, estaciones, verificaciones, hashes ni "estado de calibración".
- Todo número se muestra con unidad y fuente (`provenance_id`). Distingue dato de interpretación.
- Málaga es control: un resultado nulo es límite de detección. Nunca fuerces una señal. Si el artefacto trae `detection_limit_mm_yr`, muéstralo; no lo calcules.
- No mezcles sensores ni escalas sin aviso visible.
- Si `product` es `mock`, aviso permanente: **DEMONSTRATION DATA**.
- Los textos científicos solo salen de contenido aprobado por el Rol 3. No copies cifras del README ni de Stitch.

## Diseño

`docs/desing/DESIGN.md` y `docs/desing/stitch/` no están en el árbol (ausente). Los tokens vigentes están en `src/ui/theme.py` (`DARK` / `LIGHT`), aquí, y espejados en `.streamlit/config.toml`.

- Oscuro, fijado: fondo `#0B0F19`, superficie `#0F172A`, borde 1px `#1E293B`, acento `#06B6D4`, coherencia `#10B981`, alerta `#F59E0B`, coral `#F43F5E` solo para alertas graves o interferogramas inválidos, texto `#F8FAFC` / `#CBD5E1`.
- Claro, papel de instrumento: fondo `#F1F5F9`, superficie `#FFFFFF`, borde `#94A3B8`, acento `#0B647D`, coherencia `#047857`, alerta `#9A3412`, coral `#BE123C`, texto `#0B1220` / `#334155`. No copies los hex oscuros sobre papel.
- Radio 4px. Sin sombras difusas. Botones con borde de acento, nunca relleno sólido a ancho completo.
- Space Grotesk (títulos), Inter (texto), JetBrains Mono (cifras, coordenadas, IDs), con fallback a fuentes del sistema.
- `.streamlit/config.toml` declara `[theme.dark]` y `[theme.light]`. Streamlit 1.67 sigue `prefers-color-scheme`. No vuelvas a fijar un solo tema.

## Referencia Stitch

`docs/desing/stitch/` no está en el árbol (ausente). Si reaparece, es guía de maquetación, no de datos. No copies: estación MALG-01 ni "deriva cero", SHA256/"verified", índices estructurales, mitigaciones, "L-Band & S-Band", orbit/B_PERP fijos, ni nombres de ejemplo (Guadalquivir, Doñana, Sector 4).

## Comandos

Python solo vía `./scripts/python.sh`. `.venv` existe.

| Acción | Comando |
|---|---|
| Arranque | `deploy/README.md`: `docker build -f deploy/Dockerfile -t sar-nasa:mvp .` y `docker run --rm -p 8501:8501 sar-nasa:mvp`. El contenedor ejecuta `streamlit run app.py`. No hay script de arranque local. |
| Tests | `./scripts/python.sh -m pytest tests -q` |
| Contrato | `./scripts/python.sh -m src.domain.contracts` |
| Artefactos | `./scripts/python.sh -m src.data.validation --processed data/processed --require-cases` |
| Gate de artefactos | `./scripts/check_phase_a.sh` — contrato → suites de artefactos → `/15 MB` → tabla de handoff. Un árbol sin `data/processed/*/nisar_grid.parquet` falla aquí. |
| Gate de entrega | `./scripts/preflight.sh` — 7 gates y `VERDICT: READY` solo con cero FAIL. `--fast` **omite el gate de pytest**: bajo `--fast` un árbol con la suite en rojo igual imprime `VERDICT: READY`, así que `--fast` no sirve como prueba de entrega. |
| Lint | (pendiente). No hay ruff, flake8 ni pre-commit. |

`pytest` está en `requirements.txt` (`pytest>=8.0`). CI: `.github/workflows/ci.yml`, que corre contrato, `pytest tests -q`, `src.data.validation --require-cases`, `check_phase_a.sh` y `preflight.sh --fast` como pasos separados — ahí `--fast` no sustituye la suite, solo evita repetirla. `pipeline/validate_artifacts.py` existe (`--processed`, `--strict-size-mb`); el gate de despliegue usa `src.data.validation`.

## Git

Ramas cortas de un solo objetivo (`feat/ui-shell`, `feat/ui-map`, `feat/ui-charts`, `docs/...`). No trabajes sobre `main`. No acumules objetivos en `fradiaz`.

PR con: Objetivo / Archivos / Dependencias nuevas / Impacto en contrato / Validaciones / Riesgos / Captura.

Commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`. No hagas commit ni push salvo que el usuario lo pida; propone el mensaje.

## Definición de hecho

- Arranca solo con datos locales y sin credenciales NASA.
- Los filtros actualizan mapa, KPIs y gráficos.
- Cada cifra muestra unidad y fuente.
- Sin valores ni nombres de caso hardcodeados.
- Tests pasan: `./scripts/python.sh -m pytest tests -q` en verde y `./scripts/check_phase_a.sh` en PASS. Para entrega, `./scripts/preflight.sh` completo (no `--fast`) con `VERDICT: READY`.
