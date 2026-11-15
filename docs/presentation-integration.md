# Registro de presentación e integración de capas

Estado de `fradiaz` tras integrar `main` en `feat/contract-1.1`. No es texto científico. No copia cifras para mostrarlas en la UI. Describe qué se pinta, de qué hook sale y cómo debe leerse.

La app no lee Parquet, CSV, GeoJSON ni `meta.json`. El único seam vivo es `src/data/ui_bridge.py`, reexportado por `src/data/loaders.py`. `src/ui/_stubs.py` solo entra si faltan esos hooks.

`docs/backend-ui-contract.md` cubre el bridge y la API del dominio; su composición directa desde `app.py` (slider de fechas, selector de métrica) no es la que el cockpit ejecuta. Nombra el contrato **v1.1** con el fingerprint, y el seam que la UI consume hoy son los 18 hooks del bridge. No reimplementar ese documento en la UI.

## Reglas de lectura

| Regla | Dónde está | Qué debe hacer la UI |
|---|---|---|
| Positivo = hacia arriba | `AGENTS.md`, `RISK_THRESHOLDS_MM_YR` | Decirlo junto a velocidad y desplazamiento. No recalcular el signo. |
| `product == "mock"` | artefacto | Aviso permanente **DEMONSTRATION DATA**. No es una observación. |
| Málaga es control | `meta.stats.detection_limit_mm_yr` solo en `malaga` | Un nulo es límite de detección, no un fallo. No calcular el límite. |
| `risk_level` | derivado de `\|velocity_mm_yr\|` | Banda, no una medición aparte. Umbrales del contrato: `< 5` estable, `< 25` moderado, si no critico. El filtro envía el valor del contrato; la etiqueta visible puede ser Stable / Moderate / Critical. |
| `ratio_R` | contrato: `abs(trend) / seasonal amplitude`, ≥ 0 | Mostrar el número con esa definición citada, no reinterpretarla. |
| `regime` | `inelastic` / `elastic` / `transitional` | Secular frente a estacional. No traducir el valor almacenado. |
| Serie por celda | `stats.per_cell_series.enabled` es `false` en los tres casos | La curva del inspector es el agregado `cell_id == "aoi"`. Hay que decirlo. No inventar una serie por celda. |
| Contexto | `context_layers.geojson`, 12 features por caso | Geografía de demostración. `source` lo declara. No son capas GIS verificadas. |
| Cada cifra | hook | Unidad y `provenance_id`. Si faltan, "not declared". No rellenar. |

Los tres casos publicados hoy son `mock`: `mexico-city` (primario), `jakarta` (extensión), `malaga` (control). Los nombres visibles salen de `config/study_cases.yaml` vía `load_study_cases`, no de un literal de la UI.

## Inventario: qué se ve, de dónde sale, qué se ignora

### Cabecera y filtros — `app.py`

| Visible | Hook | Campos usados | Hueco |
|---|---|---|---|
| Título fijo | ninguno | — | Correcto: no es un dato. |
| Telemetría | `load_meta` | `sensor`, `product`, `unit`, `dates`, `coherence_threshold`; si esa clave falta, `thresholds.coherence_min` | Correcto: v1.1 guarda el suelo en `thresholds.coherence_min`, la cabecera lo imprime como `coherence floor` y el slider lo usa como mínimo. |
| Aviso mock | `load_meta` | `product`, `provenance.source` | Correcto. |
| Caso | `load_study_cases` | `id`, `label` | Correcto. |
| Uso del suelo | `load_land_use_options(case_id)` | valores presentes en la grilla | No explica que es una clase del artefacto, no un mapa de uso oficial. |
| Coherencia | `load_meta` | `thresholds.coherence_min` | No modifica un valor fuera de `[0, 1]`. Correcto. |
| Riesgo | `load_risk_levels(case_id)` | enum presente | La etiqueta es inglesa; el valor enviado no cambia. El inspector ya dice que la banda sale de `\|velocity\|`; el filtro no lo repite. |
| Extrusión / tema / escala | estado de UI | `extrude_3d`, `scale_mode` = `independent` \| `shared` | La escala no es un dato. El bridge acepta también las frases españolas antiguas. |

### 1. Map — `src/ui/map_view.py`

| Visible | Hook | Lectura correcta | Hueco |
|---|---|---|---|
| Hexágonos | `get_map_layer_df` | Color y altura ya vienen en `color` y `height`. Métrica fija del bridge: `velocity_mm_yr`. | La UI no elige métrica. No debe calcular color. |
| Recuento | `get_layer_counts` | `shown` de `total` tras el filtro. | No dice que el total es la grilla publicada, no el AOI administrativo. |
| KPIs del mapa | `get_map_kpis` | Cuatro tarjetas: mediana de velocidad (`mm/year`), mediana de `delta_change` (`mm`), coherencia mínima del subconjunto y mediana de `seasonal_amp_mm` (`mm`). | No dice que la coherencia mínima es la del subconjunto filtrado, no el umbral. |
| Nulo | `get_signal_status` + `meta.stats` | Si `above_limit` es falso, mostrar el límite recibido. | Solo Málaga trae `detection_limit_mm_yr`. |
| Leyenda | `get_color_scale` | `min`, `max`, `unit`, `metric`, `caption`, `stops`, `saturated`. | El caption del adapter ya explica escala compartida o local. Hay que tratarlo como texto del backend, no reescribirlo. |
| Comparación | `get_scale_comparison` | Pico de `\|velocity_mm_yr\|` y `share`. La UI resuelve el slug con `case_labels` y cae al `id` solo si falta la etiqueta. | El bridge declara `mixed_units` en `false`, así que el aviso de sensores distintos no llega a mostrarse. |
| Mapa base + contexto | `get_view_state`, `load_context_layers` | Cámara desde `bbox`. El GeoJSON se dibuja entero y no seleccionable; `_context_groups` lo agrupa por `layer` (`admin_boundary`, `land_use`, `water`, `infrastructure`) y pinta un `GeoJsonLayer` por grupo con el rol de color de `_CONTEXT_ROLE`, más la leyenda `Context strokes`. | Correcto: el color es presentación y no clasifica riesgo; la leyenda lo dice. |
| Inspector | `get_cell_detail` | Atributos de la celda en tres grupos: deformación (`val_t1`, `val_t2`, `delta_change`, `velocity_mm_yr`), calidad (`coherence`, `regime`, `ratio_R`, `seasonal_amp_mm`) y procedencia (`lat`, `lon`, `source`, `provenance_id`). Serie `cell` o, si no hay filas, agregado `aoi`. | Correcto: `risk_level` se anuncia como banda derivada de `\|velocity\|`, `regime` como secular frente a estacional y `ratio_R` como `abs(trend) / seasonal amplitude`; con armónico no ajustado dice `not fitted`. |
| Procedencia | `load_meta` | `source`, `unit`, `sensor`, `product`, `provenance_id`, `dates`. | La tarjeta ignora `provenance.method`, `license`, `reference` y `processing_date`; la pestaña 4 los muestra tal cual en el bloque `meta.json`. |

`build_provenance_badge`, `extract_series_view` y el slider de fechas existen en `src/domain/` y no están cableados al cockpit. No añadirlos en la UI hasta que el bridge los publique.

### 2. Series and KPIs — `src/ui/charts.py`, `src/ui/kpi_cards.py`

| Visible | Hook | Lectura correcta | Hueco |
|---|---|---|---|
| Cinco tarjetas | `get_series_kpis` | Mediana de velocidad; % de celdas con `\|v\| ≥ 5 mm/year`; celdas en banda `critico` (unidad `cells`); % con coherencia ≥ suelo del caso; mediana de `seasonal_amp_mm` (`mm`). | Correcto: la nota de celdas críticas va en inglés y la unidad es `cells`. El 5 mm/year es `NULL_RESULT_MM_YR`, no un umbral de la UI. |
| Línea | `get_case_series` | Serie `aoi` publicada en `mm`. El hook ignora el filtro y devuelve `scaled: false` con `scale_factor: 1.0`. | Correcto: al no reescalar, la serie no se lee como el subconjunto filtrado; el caption añade el factor solo si `scaled` fuera verdadero. |
| Barras | `get_land_use_bars` | Mediana de `velocity_mm_yr` por `land_use` presente. | Correcto: el eje se titula `velocity (mm/year)` y cae a `value (<unit>)` para otra unidad. |

`compute_kpis` también calcula área crítica, volumen y tendencia. Esos campos no salen del bridge. No mostrarlos: serían una cifra nueva sin aprobación de Rol 3.

### 3. Hazard and infrastructure — `src/ui/context_view.py`

| Visible | Hook | Lectura |
|---|---|---|
| Resumen | `load_context_layers` | `path`, `summary.n_features`, `summary.layers`. |
| Tabla | mismo payload | `layer`, `class`, `name`, `source`, `provenance_id`. |

En los tres artefactos actuales ninguna feature de `land_use` trae `name`, y todas las de `infrastructure`, `water` y `admin_boundary` sí. La tabla los muestra tal cual los entrega el artefacto y solo quita la columna si todas las filas vienen vacías. Las `class` incluyen valores que no están en `LAND_USE` (`metro-station`, `water-well`, `coastline`, `canal`, `reservoir`, `aoi`). Son clases de contexto, no el `land_use` de la grilla. Hay que separar esas dos listas en la lectura, no mezclarlas en un filtro.

### 4. Provenance and limits — `src/ui/educational.py`

| Visible | Origen | Lectura | Hueco |
|---|---|---|---|
| Módulo educativo | — | Ausente: no hay texto científico aprobado por Rol 3, y la pestaña no finge uno. | Correcto mientras el pedido siga abierto en `docs/requests.md`; la frase "Educational notes are not available for this case yet." solo aparece si el loader no devuelve meta ni README. |
| Tarjetas del caso | `meta.stats` | Una tarjeta por campo presente: `dominant_regime`, `median_ratio_R`, `median_velocity_mm_yr`, `median_seasonal_amp_mm`, `detection_limit_mm_yr` (solo Málaga), `n_cells` y `per_cell_series.enabled`. | Ignora `n_dates`, `p05`/`p95` y `annual_coverage`. |
| `null_policy`, `limitations` | `meta` | Texto del artefacto. Citarlo, no parafrasearlo. | No se distingue "dato" de "limitación del generador mock". |
| README §8 y §13 | `load_documented_limits` | Cita literal del README. | No es el `meta.json` del caso. Hay que etiquetarlo como documentación del repo, no como observación. |
| Bloque `meta.json` | `load_meta` | JSON devuelto, sin claves que contengan `verif`. | Útil como registro. No sustituye una lectura guiada. |

## Qué no integrar todavía

- Slider de fechas y cambio de métrica del mapa. El dominio los prevé; el bridge no los expone al cockpit.
- Volumen `m3/year` y área crítica. Existen en `compute_kpis` y no tienen texto aprobado.
- PMTiles y `docs/route-contract.md`. Post-MVP.
- Traducir valores de contrato más allá de la etiqueta visible (`RISK_LABELS` para `estable`, `moderado` y `critico`, y los roles de cromática). El valor enviado no cambia.

## Estado

Fase 1 aplicada en la UI. Tras integrar `main`, Rol 1 ya declara `scaled` / `scale_factor`, extrusión `False` por defecto y el caveat completo; la pestaña 2 consume el aviso solo cuando `scaled` es verdadero. El contrato v1.1 añade la descomposición ajustada (`trend_mm_yr`, `seasonal_mm`, `median_seasonal_amp_mm`) y `ratio_R` nulo cuando no se ajustó armónico: el inspector muestra "not fitted" en ese caso, nunca un número inventado. El pedido de texto a Rol 3 sigue abierto en `docs/requests.md`.

## Plan

Objetivo: que cada bloque diga qué es, de qué hook sale y cómo se lee, usando solo campos que el bridge ya entrega. Sin nuevas cifras y sin leer artefactos desde la UI.

### Fase 0 — Este registro

Hecho en este archivo. Es la lista de huecos. No cambia el contrato.

### Fase 1 — Lectura de lo que ya llega (Rol 2)

Archivos: `app.py`, `src/ui/map_view.py`, `src/ui/charts.py`, `src/ui/educational.py`, `src/ui/context_view.py`.

Aplicado: los diez puntos están en el código de esta rama; la lista se conserva como registro de lo que había que leer.

1. Cabecera: si no hay `coherence_threshold`, mostrar `thresholds.coherence_min` con la etiqueta "coherence floor". No inventar el número.
2. Inspector: pintar `lat`, `lon`, `val_t1`, `val_t2` con unidad. Una línea fija: positive = up. Una línea: `risk_level` es banda derivada de `|velocity|`, no una medición distinta.
3. Junto a `ratio_R` y `regime`, la definición corta del contrato (`abs(trend) / seasonal amplitude`; secular vs seasonal). Si el campo falta, "not declared".
4. Si `series_scope == "aoi"`, mantener el aviso y añadir, cuando `meta.stats.per_cell_series.enabled` sea falso, que el artefacto no emitió serie por celda.
5. Pestaña 4: quitar la tarjeta que dice que no hay texto científico cuando debajo se cita el artefacto. Separar tres bloques: caso (`meta`), documentación del repo (README), registro (`meta.json`).
6. Pestaña 4: mostrar `detection_limit_mm_yr` si el `meta` lo trae; si no, no calcularlo. Mostrar `per_cell_series.enabled` y `n_cells` como conteos del artefacto, con unidad "cells" solo si el valor es un conteo.
7. Pestaña 3: no renderizar la columna `name` si todas las filas vienen vacías. Encima de la tabla, una frase: context class is not the grid `land_use` filter.
8. Comparación: resolver el slug con las etiquetas de `load_study_cases` que `app.py` ya tiene. Hay que pasarlas a `render_map_tab`; no leer el YAML en la UI.
9. Eje de barras: "velocity" cuando la unidad sea `mm/year`, no "value".
10. Leyenda de contexto en el mapa: cuatro `layer` del contrato, un trazo cada una. El color es presentación. No clasificar riesgo con ese color.

Validación: `pytest` de UI y bridge. Los tres casos en local. Málaga sigue mostrando el límite recibido, no una señal forzada.

### Fase 2 — Peticiones al Rol 1, antes de pintar más

Añadir a `docs/requests.md` solo esto:

Los tres puntos están resueltos y copiados a `docs/requests.md`; se conservan como registro.

1. `get_case_series` debe devolver `scaled: bool` y `scale_factor` cuando reescala la serie `aoi`. La UI no debe inferir el factor.
2. `get_series_kpis`: la nota de celdas críticas en inglés, citando el valor `critico` tal cual. Unidad `cells`, no `celdas`.
3. No hace falta un hook nuevo de procedencia si Fase 1 cubre los campos de `load_meta`. Si Rol 1 prefiere exponer `build_provenance_badge`, la UI lo pintará y dejará de armar la línea a mano.

Hasta esa respuesta, la serie filtrada se captiona como "AOI series" y no se afirma que sea el subconjunto filtrado.

### Fase 3 — Lo que queda fuera de este ciclo

- Selector de métrica y slider de fechas: solo si Rol 1 los publica en el bridge.
- Volumen y área: solo con texto de Rol 3 y campos del bridge.
- Distinguir sensores entre casos: el bridge ya puede marcar `mixed_units`. Un aviso de sensor distinto espera un campo explícito; no compararlo en la UI.

## Hecho cuando

- Un jurado puede señalar cualquier cifra y ver unidad, fuente y si es dato, banda derivada, cita o ausencia.
- El aviso de demostración no convive con una tarjeta que niega el texto que hay debajo.
- La serie del inspector no se lee como medición de esa celda.
- Ningún número nuevo aparece sin un campo del hook.
