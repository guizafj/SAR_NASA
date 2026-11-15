# Registro de presentación e integración de capas

Estado de `fradiaz` tras `91a1846`. No es texto científico. No copia cifras para mostrarlas en la UI. Describe qué se pinta, de qué hook sale y cómo debe leerse.

La app no lee Parquet, CSV, GeoJSON ni `meta.json`. El único seam vivo es `src/data/ui_bridge.py`, reexportado por `src/data/loaders.py`. `src/ui/_stubs.py` solo entra si faltan esos hooks.

`docs/backend-ui-contract.md` **cubre el bridge y la API del dominio**: nombra el contrato **v1.1** con su huella, y el seam que la UI consume hoy son los 18 hooks del bridge (verificado: los 18 nombres siguen existiendo; los re-exports crecieron de 19 a 22). No reimplementar ese documento en la UI.

## Reglas de lectura

| Regla | Dónde está | Qué debe hacer la UI |
|---|---|---|
| Positivo = hacia el sensor | `docs/requests.md` (respuesta Rol 3, punto 5), `README.md`, `pipeline` `--los-sign` | Decirlo junto a velocidad y desplazamiento, con la salvedad de que es convención registrada y no validada contra GNSS. En la geometría del mock coincide con uplift porque `u_up = cos θ > 0`. No recalcular el signo. |
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
| Telemetría | `load_meta` | `sensor`, `product`, `unit`, `dates`, `coherence_threshold` | v1.1 guarda el suelo en `thresholds.coherence_min`; si el artefacto no trae la clave superior `coherence_threshold`, `app.py` toma `thresholds.coherence_min` y la cabecera lo imprime como `Coherence floor`. El slider lee el mismo valor. |
| Aviso mock | `load_meta` | `product`, `provenance.source` | Correcto. |
| Caso | `load_study_cases` | `id`, `label` | Correcto. |
| Uso del suelo | `load_land_use_options(case_id)` | valores presentes en la grilla | No explica que es una clase del artefacto, no un mapa de uso oficial. |
| Coherencia | `load_meta` | `thresholds.coherence_min` | No modifica un valor fuera de `[0, 1]`. Correcto. |
| Riesgo | `load_risk_levels(case_id)` | enum presente | La etiqueta es inglesa; el valor enviado no cambia. El inspector ya lo dice: `Risk band: … (from |velocity|, not a separate measurement)` (`src/ui/map_view.py`). |
| Extrusión / tema / escala | estado de UI | `extrude_3d`, `scale_mode` = `independent` \| `shared` | La escala no es un dato. El bridge acepta también las frases españolas antiguas. |
| Estado en la URL | `st.query_params` (solo UI) | `case`, `land`, `risk`, `coh`, `extrude`, `theme`, `scale`, `cell`, `tab`, `ramp` | La primera carga valida y restaura caso, filtros, pestaña, rampa y celda, y cada interacción reescribe la URL (salvo el estado abierto/cerrado de los expanders, aparte de `?compact=1`). `land`/`risk` viajan con el valor del contrato, no con la etiqueta visible. El popover Share junto al selector H3 muestra la URL con el control de copia nativo de `st.code`. |

### 1. Map — `src/ui/map_view.py`

| Visible | Hook | Lectura correcta | Hueco |
|---|---|---|---|
| Hexágonos | `get_map_layer_df` | La rampa original y la altura vienen en `color` y `height`. La rampa accesible opcional usa solo para presentación `velocity_mm_yr` y el dominio entregado por `get_color_scale`. La leyenda cambia junto al mapa. | La UI no calcula tasas ni magnitudes geoespaciales, ni cambia la métrica o el dominio. |
| Selección H3 | `pydeck_chart(on_select="rerun", selection_mode="single-object")` + `get_cell_detail` | El click y el selector H3 alimentan el mismo inspector; `cell` se escribe en la URL. | Los artefactos actuales declaran `series_scope="aoi"`: el panel indica que muestra el agregado de área; no se inventa una serie por celda. |
| Recuento | `get_layer_counts` | `shown` de `total` tras el filtro. | No dice que el total es la grilla publicada, no el AOI administrativo. |
| KPIs del mapa | `get_map_kpis` | Mediana de velocidad (`mm/year`), mediana de `delta_change` (`mm`), coherencia mínima del subconjunto. | No dice que la coherencia mínima es la del subconjunto filtrado, no el umbral. |
| Nulo | `get_signal_status` + `meta.stats` | Si `above_limit` es falso, mostrar el límite recibido. | Solo Málaga trae `detection_limit_mm_yr`. |
| Leyenda | `get_color_scale` | `min`, `max`, `unit`, `metric`, `caption`, `stops`, `saturated`. | El caption del adapter ya explica escala compartida o local. Hay que tratarlo como texto del backend, no reescribirlo. |
| Comparación | `get_scale_comparison` | Pico de `\|velocity_mm_yr\|` y `share`. | Muestra el slug, no el `label` del caso. No avisa de sensores distintos salvo `mixed_units`. |
| Mapa base + contexto | `get_view_state`, `load_context_layers` | Cámara desde `bbox`. El GeoJSON se dibuja entero, no seleccionable, un solo color. | No distingue `layer` (`admin_boundary`, `land_use`, `water`, `infrastructure`). |
| Inspector | `get_cell_detail` | Atributos de la celda. Serie `cell` o, si no hay filas, agregado `aoi`. | No pinta `lat`, `lon`, `val_t1`, `val_t2` aunque el hook los entrega. No explica `risk_level`, `regime` ni `ratio_R`. |
| Procedencia | `load_meta` | `source`, `unit`, `sensor`, `product`, `provenance_id`, `dates`. | Ignora `provenance.method`, `license`, `reference`, `processing_date`. |

`build_provenance_badge`, `extract_series_view` y el slider de fechas existen en `src/domain/` y no están cableados al cockpit. No añadirlos en la UI hasta que el bridge los publique.

### 2. Series and KPIs — `src/ui/charts.py`, `src/ui/kpi_cards.py`

| Visible | Hook | Lectura correcta | Hueco |
|---|---|---|---|
| Cinco tarjetas | `get_series_kpis` | Mediana de velocidad; % de celdas con `\|v\| ≥ 5 mm/year`; celdas con `risk_level == critico`; % con coherencia ≥ suelo del caso; mediana de amplitud estacional. | El 5 mm/year es `NULL_RESULT_MM_YR`, no un umbral de la UI. |
| Línea | `get_case_series` | Serie `aoi` en `mm`. | `get_case_series` fija `scaled: false` y `scale_factor: 1.0` aunque el filtro reduzca la grilla: la serie `aoi` se pinta tal cual viene del artefacto, sin reescalar. |
| Barras | `get_land_use_bars` | Mediana de `velocity_mm_yr` por `land_use` presente. | El eje se titula `Velocity (mm/year)` (`src/ui/charts.py`), así que la unidad y la magnitud son visibles. |

`compute_kpis` también calcula área crítica, volumen y tendencia. Esos campos no salen del bridge. No mostrarlos: serían una cifra nueva sin aprobación de Rol 3.

### 3. Hazard and infrastructure — `src/ui/context_view.py`

| Visible | Hook | Lectura |
|---|---|---|
| Resumen | `load_context_layers` | `path`, `summary.n_features`, `summary.layers`. |
| Tabla | mismo payload | `layer`, `class`, `name`, `source`, `provenance_id`. |

Corrección de Rol 3 (`docs/requests.md`, 2026-11-15): el GeoJSON **sí** trae `name`, en 6 de las 12 features; son nombres sintéticos etiquetados como mock y llegan en español. La UI los **traduce de forma determinista** (`translate_feature_name`, `src/ui/context_view.py`) sin tocar el GeoJSON —los hashes del artefacto no cambian— y pinta `—` donde la feature no tiene nombre. Las `class` incluyen valores que no están en `LAND_USE` (`metro-station`, `water-well`, `coastline`, `canal`, `reservoir`, `aoi`). Son clases de contexto, no el `land_use` de la grilla. Hay que separar esas dos listas en la lectura, no mezclarlas en un filtro.

### 4. Provenance and limits — `src/ui/educational.py`

| Visible | Origen | Lectura | Hueco |
|---|---|---|---|
| **Real NISAR GUNW availability** | `load_availability` (lee `data/processed/_availability/{case}.json`) | Cita el registro de búsqueda de ASF: stacks reales catalogados (track, dirección, frame, nº de granulos, ventana), HTTP, fecha de la búsqueda y la advertencia de cobertura urbana. **No es una descarga**: el artefacto publicado sigue siendo `mock` y el bloque lo dice. | El hook es aditivo y lo añadió Rol 2 desde `fradiaz`; falta la firma de Rol 1 (fila en `docs/requests.md`) |
| "Educational notes are not available for this case yet." | literal de la UI | Cierto para un módulo educativo. | No contradice nada: `educational.py` pinta ese texto sólo cuando **no** hay `meta` ni documentos, y retorna ahí mismo, así que no hay tarjetas debajo que citar. |
| Cuatro tarjetas | `meta.stats` | `dominant_regime`, `median_ratio_R`, `median_velocity_mm_yr`, `median_seasonal_amp_mm`. | Ignora `n_cells`, `n_dates`, `p05`/`p95`, `per_cell_series`, y el límite de Málaga. |
| `null_policy`, `limitations` | `meta` | Texto del artefacto. Citarlo, no parafrasearlo. | No se distingue "dato" de "limitación del generador mock". |
| README §8 y §13 | `load_documented_limits` | Cita literal del README. | No es el `meta.json` del caso. Hay que etiquetarlo como documentación del repo, no como observación. |
| Bloque `meta.json` | `load_meta` | JSON devuelto, sin claves que contengan `verif`. | Útil como registro. No sustituye una lectura guiada. |

## Qué no integrar todavía

- Slider de fechas y cambio de métrica del mapa. El dominio los prevé; el bridge no los expone al cockpit.
- Volumen `m3/year` y área crítica. Existen en `compute_kpis` y no tienen texto aprobado.
- PMTiles y `docs/route-contract.md`. Post-MVP.
- Traducir valores de contrato (`critico`, `inelastic`, slugs de `land_use`). Solo etiquetas de cromática.

## Estado

Fase 1 aplicada en la UI. Tras integrar `main`, Rol 1 ya declara `scaled` / `scale_factor`, extrusión `False` por defecto y el caveat completo; la pestaña 2 consume el aviso solo cuando `scaled` es verdadero. El contrato v1.1 añade la descomposición ajustada (`trend_mm_yr`, `seasonal_mm`, `median_seasonal_amp_mm`) y `ratio_R` nulo cuando no se ajustó armónico: el inspector muestra "not fitted" en ese caso, nunca un número inventado. El pedido de texto a Rol 3 sigue abierto en `docs/requests.md`.

## Texto revisado (2026-11-15)

Cada bloque se contrastó contra el hook que lo alimenta. Cambios de presentación, sin tocar datos:

| Antes | Ahora | Origen |
|---|---|---|
| `provenance_id` repetido bajo cada tarjeta y en cada línea del inspector | una sola línea de fuente por grupo | el mismo id para todas las cifras del caso |
| `dominant_regime`, `median_ratio_R`, `n_cells`, `per_cell_series.enabled` | Dominant regime, Median ratio R, Cells, Per-cell series | claves de `meta.stats` |
| `per_cell_series.enabled: False` | `not emitted` | booleano de `meta.stats` |
| `ratio_R: 276.76` | `Ratio R: 276.76` + definición del contrato | `get_cell_detail` |
| `velocity_mm_yr`, `val_t1`, `val_t2` | Velocity, Displacement at first/last date | `get_cell_detail` |
| leyenda con línea de rango duplicada y `velocity_mm_yr` | caption del adapter + `Velocity · mm/year` | `get_color_scale` |
| barras con `provenance_id` por fila | una línea con los ids de los tres casos | `get_scale_comparison` |
| tabla `layer/class/name/source/provenance_id` | Layer, Class, Feature name, Source, Provenance ID; `—` para vacío; id una vez | `load_context_layers` |
| `meta.json` crudo siempre visible | dentro de "Full meta.json record" | `load_meta` |
| asteriscos y backticks visibles | énfasis y `code` convertidos; marcadores truncados eliminados | `load_documented_limits` |
| tarjetas KPI con `-62.065` y `41.3767` | dos decimales como máximo | todos los hooks numéricos |
| gráfica de serie AOI sin componente estacional ni pendiente | traza `Seasonal component` (`seasonal_mm`) + `Linear trend` (`trend_mm_yr`) en caption | `get_case_series` (contrato v1.1) |
| eje X de barras con `land_use` y slugs crudos | `Land use` y etiquetas legibles (`Urban dense`, …) | `get_land_use_bars` |
| tarjetas de pestañas 3 y 4 sin `Provenance ID` | `Provenance ID` bajo las tarjetas de resumen y `Dominant regime` humanizado | `load_context_layers`, `load_meta` |
| `?cell=` borrado en el primer arranque | `selected_cell_id` se conserva si pertenece al caso activo y se limpia si es inválida o cambia el caso | `st.query_params` + `get_cell_detail` |
| serie del AOI y KPIs de rejilla en el mismo grupo, y procedencia repetida en tarjeta aparte | serie rotulada "Whole-area aggregate series"; los KPIs llevan "Grid statistics for the current filter"; una sola línea de procedencia (la de la franja) | `get_case_series`, `get_map_kpis`, `load_meta` |
| Provenance ID repetido en serie, KPIs y comparación (4 veces en Tab 1) | una sola línea, como `st.caption`, dentro de "Scale and provenance"; los IDs por caso viven en la Tab 4 | `load_meta` |
| leyenda con caption que repetía el rango [-8.0, +8.0] | la rampa es la única representación del rango; el texto queda en origen p05/p95 y banda neutra | `get_color_scale` |
| aviso de detección y banda neutra con el mismo 1.5 mm/year dos veces | número una sola vez en la nota de escala; el aviso pasa a "(the neutral band above)" | `get_signal_status`, `get_color_scale` |
| tabla de contexto con el mismo `provenance_id` en 12 filas | columna omitida cuando todas las filas comparten artefacto; una línea de procedencia | `get_context_exposure` |
| inspector con 11 líneas apiladas a ancho completo | grid de 2 columnas (`.detail-head` a ancho completo) y mono de 0.62 rem: 475 → 288 px | `get_cell_detail` |
| leyenda dentro del panel derecho | rampa de una fila bajo el mapa (junto a la exportación), visible en móvil tras el lienzo | `get_color_scale` |
| leyenda de la pestaña 1 en tarjeta alta (caption, saturación, conteo y procedencia) empujando la serie fuera del pliegue | rampa de una fila (30 px) con métrica+unidad y rango; el resto del texto pasa a "Scale and provenance" dentro del expander inferior; el radio de escala pierde la etiqueta visible | `get_color_scale`, `load_meta` |

Los nombres de feature del GeoJSON llegan en español desde el artefacto. La UI los cita, no los traduce: petición abierta en `docs/requests.md`.

El parser de `load_documented_limits` une ahora las líneas de continuación de cada ítem numerado del README, así que el punto 5 y los ítems 9–13 se leen completos. La UI solo recibe el texto; no lo reconstruye.

## Rol 3 respondió (2026-11-15, `3d6b858`)

`docs/requests.md` trae la respuesta de Rol 3 medida sobre el árbol. Efectos en esta UI:

- **Punto 4 cerrado por el código:** `mexico-city` publica `seasonal_amp_mm` y `ratio_R` en las 11 694 celdas y su limitación afirma que la ventana de dos años sí identifica el término estacional. No hay campo que suprimir ni marcar: el inspector los muestra con unidad y fuente, como hace hoy.
- **Punto 5 con corrección de lectura:** positivo no es «hacia arriba» sin más, es **hacia el sensor** (por defecto `toward-sensor`), que coincide con uplift en la geometría del mock. La línea de signo del inspector cita ahora esa formulación y añade que la convención está registrada, no validada contra GNSS. No se da por cerrada.
- **Puntos 1–3 cubiertos desde las fuentes canónicas del repo:** `load_documented_limits` extrae `case_roles` (`role`, `expected_regime`, `published_rate_mm_yr` de `config/study_cases.yaml`) y `science_section_1` (el párrafo §1 de `docs/science-guide.md`), mientras `src/ui/formatters.py` aporta `CONTEXT_LAYER_GLOSSARY` (`docs/data-contract-v1.md` §6 y §7.4) y `translate_feature_name()` para mostrar en inglés los nombres sintéticos del GeoJSON sin alterar `data/processed/`.

## Plan

Objetivo: que cada bloque diga qué es, de qué hook sale y cómo se lee, usando solo campos que el bridge ya entrega. Sin nuevas cifras y sin leer artefactos desde la UI.

### Fase 0 — Este registro

Hecho en este archivo. Es la lista de huecos. No cambia el contrato.

### Fase 1 — Lectura de lo que ya llega (Rol 2)

Archivos: `app.py`, `src/ui/map_view.py`, `src/ui/charts.py`, `src/ui/educational.py`, `src/ui/context_view.py`.

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
