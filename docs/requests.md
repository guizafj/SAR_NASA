# Peticiones entre roles

Índice:

| Rol | Petición | Estado |
|---|---|---|
| Rol 1 (pozo) | `get_map_layer_df`: extrusión por defecto en `False` | resuelta en `main` (`7f0b455`), integrada en `fradiaz` |
| Rol 1 (pozo) | `get_case_series`: `scaled` y `scale_factor`; nota de KPI en inglés | resuelta en `main` (`7f0b455`); la UI ya consume `scaled` |
| Rol 1 (pozo) | `load_documented_limits`: párrafo completo del caveat e ítems con sus líneas de continuación | resuelta en `main` (`ui_bridge` y `_stubs` unen el párrafo del caveat y todas las líneas de continuación de `section_13`) |
| Rol 3 (yuls) | Texto aprobado por caso, módulo educativo, glosario de contexto, campos contradichos y definiciones | resuelta (2026-11-15): puntos 4 y 5 cerrados en `3d6b858`; puntos 1–3 conectados en `load_documented_limits` y `src/ui/formatters.py` desde `config/study_cases.yaml`, `docs/science-guide.md` §1 y `docs/data-contract-v1.md` §6/§7.4 |
| Todos (dueño de `docs/verification.md`) | Registrar `tests/test_doc_provenance_ids.py` en la tabla de familias del registro | resuelta: la fila «document provenance ids» está en `docs/verification.md` desde `feat/contract-1.1` y sobrevive a la integración de `main` (lo comprueba `tests/test_verification_register.py`) |
| Rol 1 (pozo) | `validate_case_bundle`: el contexto no puede nombrar a su propio proveedor | **abierta** — pendiente de Rol 1 |
| Todos | Estrategia de mejora continua por rama (sección final) | vigente desde 2026-11-15 |
| Rol 1 (pozo) | `.gitignore` ignora `AGENTS.md` rastreado; elegir `git rm --cached` o quitar la línea | resuelta en esta rama: se mantiene la regla y `AGENTS.md` sale del índice; verificado con `tests/test_gitignore_policy.py` |
| Rol 3 (yuls) | Retirar la metáfora "one pump" de la narrativa y re-scopear la tesis a `docs/science-guide.md` §3.4 | abierta |
| Rol 3 (yuls) | Manifiesto de contexto sin sello OSM por tile ni rango, `load_extract` leyendo un tile marcado como fallido y sha256 calculado sobre el buffer y no sobre el archivo | resuelta en esta rama: las tres correcciones están en el código |
## Rol 1 (`src/data/ui_bridge.py`) — extrusión por defecto

**De:** Rol 2 (fradiaz)
**Para:** Rol 1 (pozo)
**Contexto:** cockpit mapa H3, rama `feat/ui-map-render`.

En `get_map_layer_df`, el default actual es:

```python
extrude = bool((filters or {}).get("extrude_3d", True))
```

El toggle de la UI arranca en `False`. Pedimos cambiar el default a `False` para que, si el dict de filtros omite la clave, el payload no llegue con `elevation_m` alto (hasta ~3000 m en el mock).

La UI ya aplana `height` en modo flat como red de seguridad; alinear el default evita trabajo duplicado y sorpresas si otro consumidor llama el hook sin `extrude_3d`.

**Impacto en contrato de datos:** ninguno (solo default de presentación del bridge).  
**Aprobación:** Rol 1; Rol 2 verifica el mapa tras el cambio.  
**Estado:** RESUELTO por Rol 1 (`pozo`) — default cambiado a `False` en `src/data/ui_bridge.py`.

## Rol 1 (`src/data/ui_bridge.py`) — serie filtrada y notas de KPI

**De:** Rol 2 (fradiaz)  
**Para:** Rol 1 (pozo)  
**Contexto:** Fase 2 de `docs/presentation-integration.md`. La UI no infiere el factor.

`get_case_series` reescala la serie `cell_id == "aoi"` por el cociente de medianas de `delta_change` cuando el filtro reduce la grilla, y no lo declara. Pedimos que cada fila, o el payload, incluya:

- `scaled: bool`
- `scale_factor: float` (1.0 cuando no hay reescala)

Resuelto en `main`: el hook devuelve `scaled` y `scale_factor` en cada fila. La pestaña 2 añade el aviso solo cuando `scaled` es verdadero.

En `get_series_kpis`, la nota de celdas críticas sigue citando `risk_level critico`. Pedimos la nota en inglés, sin traducir el valor de contrato. La unidad visible debe ser `cells`, no `celdas`.

No pedimos un hook nuevo de procedencia: la UI ya lee `load_meta`.

**Impacto en contrato de datos:** ninguno. Son campos aditivos del bridge.  
**Aprobación:** Rol 1. Rol 2 cambia el caption cuando `scaled` llegue.  
**Estado:** RESUELTO por Rol 1 (`pozo`) — `get_case_series` incluye `scaled` y `scale_factor`, y `get_series_kpis` entrega unidad `cells` y nota en inglés.

## Rol 1 (`src/data/ui_bridge.py`) — caveat del README cortado

**De:** Rol 2 (fradiaz)  
**Para:** Rol 1 (pozo)  
**Contexto:** pestaña 4, bloque "Known caveat".

`load_documented_limits` busca la primera línea que contiene `validate against GNSS` y devuelve solo esa línea. El resultado es una frase suelta ("SNAP — validate against GNSS before publishing numbers.") que se lee fuera de contexto y pierde la condición que la acompaña.

Pedimos que devuelva el párrafo completo: desde la línea que contiene la frase hasta la primera línea en blanco posterior, uniendo las líneas con un espacio. Sin recortar el sentido ni añadir texto.

El mismo parser corta los ítems numerados de `section_13`: hoy devuelve solo la primera línea de cada ítem, así que el punto 5 termina en "…use coherence-loss change". Pedimos que cada ítem incluya sus líneas de continuación hasta la siguiente entrada numerada o la línea en blanco, unidas con un espacio.

La UI no puede reconstruirlo: solo recibe el string ya extraído y no lee el README.

**Impacto en contrato de datos:** ninguno (solo el valor de un hook de presentación).  
**Aprobación:** Rol 1. Rol 2 comprueba que el bloque "Known caveat" se lee completo.  
**Estado:** RESUELTO por Rol 1 (`pozo`) — `load_documented_limits` devuelve el párrafo completo del caveat del README y une las líneas de continuación de cada ítem numerado de `section_13` hasta la siguiente entrada o línea en blanco.

## Rol 3 — texto y contexto que el cockpit no puede inventar

**De:** Rol 2 (fradiaz)  
**Para:** Rol 3 (yuls)  
**Contexto:** el bridge de Rol 1 ya entrega grilla, serie `aoi`, `meta.json`, GeoJSON y límites del README. Lo que falta para leer el cockpit es texto aprobado, no otro cálculo.

Hoy la pestaña **4. Provenance and limits** separa tres bloques: caso (`meta`), *Known caveat* / *Known limitations* (texto extraído del README por Rol 1) y registro (`meta.json`). No hay módulo educativo. La frase "Educational notes are not available for this case yet." solo aparece si el loader no devuelve nada.

Pedimos, por caso y sin cifras nuevas, lo siguiente. Si un punto no está listo, la respuesta es "no declarado". La UI lo mostrará así.

### 1. Lectura de cada caso

Un párrafo aprobado por `case_id` (`mexico-city`, `jakarta`, `malaga`) que diga qué demuestra el artefacto y qué no se puede afirmar. Málaga debe leerse como control: el nulo es el límite de detección, no un fallo. No calcular el límite; si falta `stats.detection_limit_mm_yr`, no lo pedimos inventado.

Confirmar si `meta.limitations` y `meta.null_policy` son el texto público del caso o solo notas de pipeline. Si hay una versión más corta para la cabecera, entregarla como campo o como archivo citado por el loader. La UI no parafrasea.

### 2. Módulo educativo

Texto aprobado, en inglés, para la pestaña **4. Provenance and limits**: qué es la observación SAR que este cockpit enseña, qué hace el producto declarado y qué no hace. Si el texto incluye física básica (InSAR, LOS frente a vertical, coherencia), la pestaña puede volver a llamarse "SAR physics"; hoy no la contiene y el nombre se ajustó a lo que se muestra. Sin estación de ejemplo, sin "deriva cero", sin SHA256/"verified", sin índices estructurales, sin mitigaciones y sin "L-Band & S-Band". Hasta que llegue, no hay bloque de educación.

### 3. Glosario de contexto

Los GeoJSON traen `layer` (`admin_boundary`, `land_use`, `water`, `infrastructure`) y `class` (`aoi`, `urban-dense`, `metro-station`, `water-well`, `coastline`, `canal`, `reservoir`, y otras). No traen `name`.

Pedimos:

- Una frase por `layer`: qué es y qué no es.
- Una frase que fije que `class` de contexto no es el filtro `land_use` de la grilla.
- Confirmación de que la geografía mock no es una capa de peligro verificada ni un inventario de infraestructura. Si alguna clase sí debe leerse como infraestructura de demostración, listarla. Si no, la UI sigue mostrándola como feature entregada, sin interpretarla.
- Los `name` de las features llegan en español ("Estación Metro Zócalo (sintética)", "Pozo Roma (sintético)"). Para un evento internacional, pedimos nombres en inglés o confirmación de que son cadenas de demostración que la UI debe citar tal cual. La UI no traduce valores del artefacto.

### 4. Campos que el artefacto contradice

`mexico-city` limita: no publicar `seasonal_amp_mm` de este stack. La grilla igual trae el campo y el inspector lo muestra con unidad y fuente. Pedimos una decisión: suprimirlo en la presentación, o mostrarlo marcado como campo mock que la limitación desaconseja. La UI no elige eso sola.

Lo mismo para cualquier otro campo que el `meta` pida no publicar y el grid sí traiga.

### 5. Definiciones que la UI ya cita del contrato

Confirmación, o sustitución aprobada, de estas lecturas. No son cifras:

- positivo = hacia arriba
- `risk_level` es banda de `|velocity_mm_yr|` (`< 5` estable, `< 25` moderado, resto critico), no una medición distinta
- `ratio_R` = `abs(trend) / seasonal amplitude`
- `regime` = secular frente a estacional (`inelastic`, `elastic`, `transitional`)

Si el signo del mock no es el del producto real, decirlo en una frase visible. El README marca la convención real como no validada. No la damos por cerrada.

### 6. Qué no hace falta reenviar

No pedimos otro `meta.json`, otra grilla ni otro GeoJSON si los publicados siguen siendo la fuente. No pedimos volumen `m³/year`, área crítica, slider de fechas ni cambio de métrica del mapa: el dominio los prevé y el bridge no los expone. Si más adelante hay texto aprobado para volumen o área, tiene que llegar con el campo ya calculado por Rol 1.

**Impacto en contrato de datos:** ninguno si el texto viaja por un hook o por `meta` ya previsto. Un campo nuevo de copy educativo se propone en `docs/contract-change-requests.md` y exige a los tres roles.  
**Aprobación:** Rol 3 entrega el texto. Rol 2 solo lo cita.

### Respuesta de Rol 3 (`yuls`) — 2026-11-15

Medido sobre `main`. Cada punto dice qué está publicado y qué no; donde no hay texto aprobado la
respuesta es **no declarado**, como pedía la petición.

**1. Lectura de cada caso.** `meta.limitations` (6 entradas en `mexico-city` y `jakarta`, 7 en
`malaga`) y `meta.null_policy` son **texto público publicado**, no notas de pipeline: son campos
obligatorios del contrato y viajan en `data/processed/<caso>/meta.json`. No existe además un
párrafo aprobado por `case_id`: **no declarado**. La lectura de Málaga como control ya está
publicada — su cota de detección es `stats.detection_limit_mm_yr = 1.5` y una de sus limitaciones
dice «Rates are at or below the detection floor: the bound IS the result» — así que no hay nada
que inventar.

**2. Módulo educativo.** `src/ui/educational.py` existe y la pestaña 4 se llama
**«4. Provenance and limits»** (`app.py`): el nombre ya se ajustó a lo que se muestra y no
reclama física que no exista. No hay texto docente aprobado: **no declarado**. La UI ya renderiza
`NOT_DECLARED` desde `src/ui/formatters.py` cuando el dato falta, de modo que la ausencia se lee
como ausencia.

**3. Glosario de contexto.** Corrección sobre la petición: los GeoJSON **sí** traen `name`, en 6 de
las 12 features (nombres sintéticos, etiquetados como mock). `layer` toma cuatro valores —
`admin_boundary`, `infrastructure`, `land_use`, `water`— y `class` doce: `aoi`, `bare`,
`industrial`, `infrastructure`, `metro-station`, `reservoir`, `river`, `urban-dense`,
`urban-residential`, `vegetation`, `water-treatment`, `water-well`. Las frases por `layer` y la que
fija que `class` de contexto no es el filtro `land_use` de la grilla: **no declarado**. Lo que sí
se confirma: la geografía la inventa `pipeline/mock/geography.py` y cada feature se etiqueta como
mock, así que no es capa de peligro verificada ni inventario de infraestructura.

**4. Campos que el artefacto contradice.** Resuelto, y ya no hay decisión que tomar: `mexico-city`
publica `seasonal_amp_mm` y `ratio_R` en las 11.694 celdas, y su limitación 3 dice que la ventana
de dos años **sí** identifica el término estacional. La limitación del stack de 132 días que
desaconsejaba publicar el campo quedó superseded al cambiar la ventana; ninguna limitación
publicada prohíbe ya el campo, así que no hace falta suprimirlo ni marcarlo.

**5. Definiciones que la UI cita.** Confirmadas contra el código, con una corrección de lectura:

- `risk_level` es banda de `|velocity_mm_yr|`, no una medición distinta: en
  `src/domain/contracts.py` los cortes son `< 5` estable, `< 25` moderado, resto `critico`, y la
  función ignora el signo. Enum `RISK_LEVELS` = estable, moderado, critico.
- `ratio_R = |velocity_mm_yr| / seasonal_amp_mm` (contrato §7.5).
- `regime` ∈ `inelastic`, `elastic`, `transitional`.
- El signo no es «positivo = hacia arriba» sin más: positivo significa **hacia el sensor**
  (`--los-sign` por defecto `toward-sensor`, `d_LOS = -λ/4π·φ`), que en la geometría del mock
  coincide con el uplift porque `u_up = cos θ > 0`. El README lo marca como **registrado, no
  validado**, y la UI lo dice en una frase visible; no se da por cerrado.

**6. Qué no hace falta reenviar.** Confirmado: no se pide otro `meta.json`, grilla ni GeoJSON
mientras los publicados sigan siendo la fuente. Tampoco volumen `m³/year`, área crítica, slider de
fechas ni cambio de métrica del mapa.

**Estado:** RESUELTA (2026-11-15) — los puntos 4 y 5 quedaron confirmados en `3d6b858`; los puntos 1, 2 y 3 quedan cubiertos sin tocar artefactos ni añadir llamadas externas mediante `load_documented_limits` (`config/study_cases.yaml` + `docs/science-guide.md` §1) y el glosario/traductor determinista de `src/ui/formatters.py` (`docs/data-contract-v1.md` §6 y §7.4).

## Estrategia de mejora continua por rama (2026-11-15)

Protocolo común para los tres roles:

1. **Una rama por miembro.** Cada rol trabaja en su rama (`pozo`, `yuls`, `fradiaz`). Un objetivo por commit o rama corta derivada; sin acumular objetivos ajenos a la propiedad de archivos (el AGENTS.md del rol, §Propiedad).
2. **Fila por hallazgo.** Todo defecto, deuda u optimización detectada se registra aquí como fila: `Qué / Por qué / Cómo se valida / Estado`. Una fila sin `Cómo se valida` no es accionable.
3. **Estados.** `abierto` (sin trabajo) → `en-rama` (commit en la rama del rol) → `en-main` (merge con preflight verde) → `publicado` (push). Cambiar de estado exige la validación de su columna.
4. **Puerta de merge a `main`.** Solo con `./scripts/preflight.sh` completo → `VERDICT: READY` sobre árbol limpio. Rol 2 revisa todo lo que toca presentación; Rol 1 todo lo que toca contrato o bridge; Rol 3 todo texto científico. Un cambio que cruce propiedad se pide aquí antes de escribirlo.
5. **Reglas duras vigentes** (no negociables en ninguna rama): ningún campo nuevo de contrato sin propuesta aprobada por los tres en `docs/contract-change-requests.md`; la UI no lee artefactos ni calcula ciencia; ninguna cifra sin unidad y `provenance_id`; `product: mock` lleva aviso permanente.

### Rol 1 (pozo) — backend, bridge, CI y deploy

| Qué | Por qué | Cómo se valida | Estado |
|---|---|---|---|
| **Sign-off de `science_section_1` y `case_roles`** que Rol 2 añadió a `load_documented_limits` (aditivo, sin contrato) | El hook es de Rol 1 y el cambio entró desde `fradiaz` (`cfcc14e`) sin firma de Rol 1, aunque la pestaña 4 ya lo consume | `tests/test_ui_bridge.py` (ambos campos presentes y no vacíos) + `tests/test_ui_smoke.py` (la pestaña 4 los pinta); impacto en contrato: **ninguno** | **RESUELTO — firmado por Rol 1 (`pozo`)** |
| **Sign-off de `load_availability(case_id)`** (hook aditivo: registro de búsqueda ASF de `data/processed/_availability/{case}.json`) | La pestaña 4 prueba que el catálogo real existe sin presentar sus cifras como medición, y la UI no puede leer `_availability` por sí sola | `tests/test_ui_bridge.py::test_load_availability_cites_the_asf_record_without_downloading` (endurecido: ningún stack declara `downloaded`; `atlantis` → `None`) + `tests/test_ui_smoke.py::test_tab4_cites_real_nisar_availability` | **RESUELTO — firmado por Rol 1 (`pozo`)** |
| Exponer `provenance.method`, `license`, `reference` y `processing_date` por `load_meta` (aditivo, sin contrato) | La pestaña 4 los ignora hoy porque viajan dentro de `meta` pero la UI solo pinta lo declarado por hooks (registro en `presentation-integration.md`) | `test_ui_bridge.py` (`meta["provenance_fields"]` + claves top-level); falta que Rol 2 pinte el bloque en pestaña 4 | en-rama (`pozo`) |
| Decidir O-07 final: variante firmada de `origin/pozo` (`extrude_m`, `motion_direction`) contra `COLUMN_BASELINE_M` vigente en `main` | Dos soluciones conviven en ramas; una debe morir. Rol 2 verifica el mapa tras la decisión | unificado sobre `COLUMN_BASELINE_M = 1500 m` + `signed_delta_mm` + `motion_direction` + `tests/test_height_sign_parity.py` + `./scripts/preflight.sh` | en-rama (`pozo`) |
| Job CI con paridad 3.12 usando `SAR_PYTHON` (`aeabcf8`) | La receta existe (`docs/handoff-pozo.md` §6) pero `ci.yml` no la ejecutaba | `.github/workflows/ci.yml` fija `SAR_PYTHON=.venv312/bin/python` y añade el job `app-runtime-smoke` (`requirements-app.txt` en 3.12) — **run verde en GitHub Actions** | en-rama (`pozo`, CI verde) |
| URL pública de demo | Checklist §1 pide URL; el Docker local ya sirve | `curl -fsS <url>/_stcore/health` → 200 | abierto (requiere despliegue en host con credenciales) |
| `mixed_sensors` explícito en `get_scale_comparison` (antes `mixed_units` era siempre `false`) | Si algún caso cambia de sensor, la UI necesita declararlo, no deducirlo | `mixed_sensors`, `mixed_products`, `sensors`/`products` y `mixed_units` dinámico + `test_ui_bridge.py::test_get_scale_comparison_flags_mixed_sensors_across_cases` | en-rama (`pozo`) |
| Preservar `docs/science-guide.md` en `.dockerignore` + `deploy/Dockerfile`, fijar `streamlit>=1.67` y test hermético de contenedor | `.dockerignore` excluía todo `docs/`, dejando `science_section_1` vacío en producción Docker (`cfcc14e`), y `streamlit>=1.38` no exigía soporte de `[theme.dark]`/`[theme.light]` | `tests/test_deploy_context.py` + job `app-runtime-smoke` | en-rama (`pozo`) |
| Actualizar `docs/backend-ui-contract.md` con la tabla viva de los hooks de `src/data/ui_bridge.py` | `presentation-integration.md` señalaba que el documento solo describía la API interna de dominio | `test_docs_links.py` + `test_fingerprint_consistency.py` | en-rama (`pozo`) |

### Rol 2 (fradiaz) — cockpit y presentación

| Qué | Por qué | Cómo se valida | Estado |
|---|---|---|---|
| **Pintar `meta["provenance_fields"]`** (`method`, `license`, `reference`, `processing_date`) en pestaña 4 (`src/ui/educational.py`) y **avisar si `mixed_sensors` es `True`** en la comparación (`src/ui/map_view.py`, hoy solo mira `mixed_units`) | Rol 1 ya expone ambos en `pozo` sin tocar archivos de propiedad de Rol 2; sin esto el hook existe pero nadie lo lee | `test_ui_smoke.py` + bloque visible en pestaña 4 / nota en la franja de comparación | abierto (hook listo en `pozo`) |
| P0-1 · mapa sobre el pliegue: sin contenedor interno en el expander de filtros y `--map-height` recalculado | A 1440×900 el mapa empezaba en y≈492 y acababa en y≈922 (fuera de pantalla) | Playwright 1440×900: borde inferior del mapa ≤ 880 px y alto ≥ 360 px | en-rama (`798c62d`, superado por la Fase 1) |
| P0-2 · pestaña 4 legible: límites del README divididos en visibles (≤220 caracteres, sin backticks ni ids `O-NN`) y `st.expander("Technical notes (N)")`, caveat con sus dos primeras frases | Volcaba 6–10 líneas técnicas con rutas e ids internos a un jurado no técnico | `tests/test_ui_smoke.py::test_tab4_reader_plus_technical_equals_loader_total`: visibles + técnicos == total del loader y N del expander | en-rama (`4de06f9`) |
| P0-3 · una sola cifra para la mediana de velocidad: `format_number` con `Decimal` `ROUND_HALF_UP` y la tarjeta de la pestaña 4 leyendo la misma clave del bridge que la pestaña 1 | El mismo valor (−62.065) se leía −62.07 en una pestaña y −62.06 en otra | `test_number_format_is_half_up_and_trimmed` + `test_median_velocity_is_one_figure_across_tabs` | en-rama (`b0dfd7a`) |
| Fase 0 · plan de diseño (`docs/ui-redesign-plan.md`): sujeto/audiencia, trabajo de la pantalla 1, tokens existentes + `case_colors`, dos wireframes ASCII 1440×900 | El pack exigía fijar jerarquía antes de tocar código | revisión del documento; sin cambios de código; documentado que el criterio "alto ≥ 420 px y borde ≤ 880 px" exigía compactar la cabecera | en-rama (`6705f92`) |
| Fase 1 · mapa 2/3 + panel de lectura 1/3 con la serie como hero, franja compacta de leyenda/procedencia/celdas y `st.expander("Case comparison and context")`; estado vacío con la serie del AOI ("Whole area") | El mapa quedaba fuera del pliegue y la mitad derecha era una lista de 20 líneas sin jerarquía | Playwright 1440×900: mapa top 448, bottom 868, alto 420, columnas 910/455 (2:1), sin scroll horizontal, en claro y oscuro; 390×844 apilado con el mapa primero | en-rama (`65e5bce`) |
| Fase 2 · gráficas `@dataviz`: constructores puros con unidades en el eje, `Seasonal fit` discontinuo, hover con valor+unidad+`provenance_id`, gráfica "Three cities, shared axis" con `case_colors` y etiquetas directas, barras finas con línea de detección | Las gráficas no comunicaban jerarquía ni permitían verificar contra la fuente | `dataviz/scripts/validate_palette.py` en claro (PASS, WARN 2.82:1 en Málaga → etiqueta directa) y oscuro (PASS, ≥3:1); `test_every_figure_declares_a_unit_on_the_y_axis`; capturas de la pestaña 2 en `/tmp/opencode/caps/` | en-rama (`7583a4d`) |
| Fase 3 · interacción: botón *Clear selection* (borra `?cell=`), contorno de acento en la celda elegida, `?compact=1` cierra filtros, foco visible, `prefers-reduced-motion`, una animación de 180 ms y contraste real del carril del toggle 3D | Faltaba salida del estado seleccionado, el foco de teclado no se veía y el toggle se perdía sobre el fondo oscuro | `test_compact_query_param_closes_the_filter_strip` + `test_clear_selection_resets_cell_and_url` + capturas 390×844; capa `h3-selected` presente en el payload del deck | en-rama (`52d23b9`) |
| Fase 4 · verificación y cierre: suite completa, medidas y 24 capturas (4 pestañas × 2 temas × 3 casos) | El pack exige evidencia medida, no estimada | `pytest tests -q` exit 0; Playwright 1440×900 sin tarjetas recortadas (`clippedCards: 0`) ni scroll horizontal; pestaña 4 = 1345 px (1.49 pantallas); capturas en `/tmp/opencode/caps/matrix/` | en-rama (`3a8d6ca`) |
| Panel de lectura: separar ámbitos (serie del AOI vs KPIs de rejilla) y quitar la tarjeta de procedencia duplicada | En estado vacío la serie y las cifras se leían como un mismo grupo; la procedencia aparecía dos veces | `test_reading_panel_separates_series_scope_from_grid_statistics` + captura del panel vacío y con celda | en-rama (`1e60688`) |
| Dedupe de Tab 1: fuera las tarjetas KPI inferiores (la tira del panel derecho es la única), Provenance ID solo dentro de "Scale and provenance", metadatos reducidos a Source + Provenance ID, caption de escala sin rango y aviso de detección sin repetir el número; Tab 3 pasa de 12 a 1 id por tabla | El mismo dato aparecía hasta 4 veces en Tab 1 al abrir el bloque, y 12 ids idénticos en la tabla de contexto | `test_map_tab_shows_provenance_and_kpis_exactly_once` (AppTest.from_function sobre la Tab 1 sola) + recuento Playwright antes/después en las 4 pestañas | en-rama |
| Pase de CSS: filas de alerta en Tab 3, purga del CSS muerto, reconexión de la tira de filtros (`.st-key-filters`), chrome sin solape, header móvil 114 px, leyenda bajo el mapa, hero 225 px, inspector en grid de 2 columnas y tarjetas KPI de altura única (124/96) | El resaltado de filas no tenía CSS; quedaban 6 clases y 4 contenedores muertos; el header solapaba 4 px con el banner y consumía 177 px en móvil; la serie se cortaba 43 px a 1440×900; el inspector tenía 11 filas monótonas de 475 px; las tarjetas de una fila medían 95/112/121 | `tests/test_ui_smoke.py` + `test_ui_bridge.py` (1131 passed); Playwright: gap 8 px, mapa 434–849 y serie 626–851 a 1440×900 con filtros abiertos, leyenda 860–899, header móvil 114 px, tarjetas 124/96 uniformes, `overflowX` = 0 en 390/1024/1440/1920 | en-rama |
| Exportación: CSV del subconjunto filtrado (columnas de contrato, con `provenance_id`), JSON de la celda seleccionada, CSV de contexto filtrado; indicador `saturated` de vuelta en la fila compacta | El jurado debe poder descargar lo que ve, no solo mirarlo; la saturación no puede quedar escondida en el expander | `tests/test_ui_bridge.py::test_filtered_export_csv_drops_visual_columns`, `test_cell_export_carries_attributes_and_series`; `test_ui_smoke.py::test_export_buttons_exist_for_grid_context_and_cell` | en-rama (`e8c3517`) |
| Pestaña 3 interactiva: `get_context_exposure` une cada feature con su celda H3 más cercana (riesgo y `land_use` informativos), filtros Layer/Class/riesgo, filas críticas resaltadas, *Jump to cell on Map* y export CSV | La tabla era estática y no cruzaba contexto con la rejilla | `test_context_exposure_joins_nearest_cell_without_claiming_risk` + `test_context_tab_cross_references_nearest_cell_risk` + `test_physics_topics_*` | en-rama (mismo commit) |
| Pestaña 4: tres viñetas de física en `README.md` §6 (roughness, dielectric constant, L-band vs C-band) expuestas por `load_documented_limits()["physics"]` y explorables con un selector dentro de *SAR physics and regime classification* | Faltaba la profundidad física pedida; la UI no puede inventarla | `test_documented_limits_expose_three_physics_bullets` + `test_physics_topics_cover_roughness_dielectric_and_bands`; §13 sigue con 13 ítems | en-rama (mismo commit) |
| Refinamiento visual: rampa de 30 px en el panel y radio de escala sin etiqueta visible, para que el hero (serie) quede sobre el pliegue | La leyenda ocupaba ~190 px y la serie empezaba en y≈860 (40 px visibles) | Playwright 1440×900: mapa top 420 / bottom 840, serie en 654..934 (246 de 280 px visibles), claro y oscuro; 390×844 sin scroll horizontal | en-rama (`08e3997`; capturas en `/tmp/opencode/caps/refine-*.png`) |

### Rol 3 (yuls) — artefactos, ciencia, narrativa y video

| Qué | Por qué | Cómo se valida | Estado |
|---|---|---|---|
| Aprobar o sustituir el copy que la UI cita automáticamente: `science_section_1` (science-guide §1), `case_roles` (`study_cases.yaml`) y el glosario de capas (contrato §6/§7.4) | Desde `cfcc14e` la pestaña 4 muestra párrafo §1 literal, rol/régimen/tasa de referencia por caso, y la pestaña 3 glosario; son texto del repo, no aún aprobado como guion | revisión de Rol 3; si cambia, `test_honesty_claims` + `test_ui_smoke` | en-rama (pendiente aprobación) |
| Nombres GeoJSON en inglés en `pipeline/mock/geography.py` (próxima regeneración) | La UI los traduce determinista para no tocar hashes, pero un artefacto autoconsistente en inglés es mejor para jurado internacional | regen + `check_phase_a.sh` + pestaña 3 sin traductor activo | abierto |
| Saturación de bandas en el mock CDMX (~93 % de celdas `critico`) | La banda `risk_level` no discrimina en el caso primario; es decisión de datos (ajustar peak/falloff o declararlo en `limitations`), no de la UI | `demo_numbers.py` + limitación visible en pestaña 4 | abierto |
| Video 2:20 con números re-derivados de `demo_numbers.py` tras el último regen | Checklist §1; los números del guion deben coincidir con los artefactos congelados | `test_video_script_numbers.py` | abierto |

### Plantilla para filas nuevas

```
| <acción concreta> | <defecto, deuda o riesgo> | <comando o vista que lo prueba> | abierto |
```

Nada se declara hecho fuera de esta tabla sin su `Cómo se valida` ejecutado en el commit integrado.

## Todos (`docs/verification.md`) — registrar el guard de ids citados en prosa

**De:** Rol 2 (fradiaz)
**Para:** quien mantenga `docs/verification.md` y `tests/test_verification_register.py` (ninguno de los dos es de Rol 2).

`tests/test_doc_provenance_ids.py` (nuevo) falla cuando un `provenance_id` completo citado en `README.md`, `CONTEXT.md`, `docs/**/*.md` o `pipeline/README.md` ya no es el `meta.provenance.provenance_id` de ningún caso publicado. Hasta que alguien lo registre, `tests/test_verification_register.py` lo cuenta como no registrado y falla (el otro archivo de la lista no es de Rol 2):

```
64 test files, 62 registered, unregistered: test_doc_provenance_ids.py, test_requirements_coverage.py
```

Pedimos una fila en la tabla de familias, en la línea de la fila «provenance id | `test_provenance_id.py` | …», que diga qué asegura el archivo: los ids citados en prosa siguen vivos; un id retirado exige `<!-- fingerprint-historical: reason -->` o `<!-- provenance-example: reason -->` junto a la cita; una marca sobre un id vivo también falla; y el guard se prueba sobre una copia mutada de `docs/video-script.md`. El registro completo debe volver a verde con `./scripts/python.sh -m pytest tests/test_verification_register.py -q`.

**Impacto en contrato de datos:** ninguno.
**Aprobación:** quien mantenga `docs/verification.md`.
**Estado:** RESUELTO — el archivo ya está registrado en la tabla de familias de `docs/verification.md` (fila «document provenance ids», línea 94), con el alcance pedido: los ids citados en prosa siguen vivos, un id retirado exige marca histórica y el guard se prueba sobre una copia mutada de `docs/video-script.md`. `./scripts/python.sh -m pytest tests/test_verification_register.py -q` vuelve a verde con ese archivo registrado.

## Rol 1 (`src/data/validation.py`) — el contexto no puede nombrar a su propio proveedor

**De:** Rol 2 (fradiaz)
**Para:** Rol 1 (pozo), dueño de `src/data/validation.py`
**Contexto:** capas de contexto de esta sesión. Medido corriendo el gate, no leído de la prosa.

`validate_case_bundle` arma `expected_prov_prefix = f"{case_id}+{meta_product}+"` y exige que el `provenance_id` de cada feature de contexto empiece por ese prefijo (bloque `cross_check` que reporta "context has N feature(s) whose provenance_id does not start with ...").

Re-etiquetando las 12 features de contexto publicadas de `malaga` a `malaga+osm+20260115+deadbeef` y corriendo el gate, la salida es: <!-- provenance-example: id inventado para el experimento de re-etiquetado; no nombra ningún artefacto publicado ni existió nunca -->

```
cross_check: context has 12 feature(s) whose provenance_id does not start with 'malaga+mock+'
```

Consecuencia hoy: una capa de contexto derivada de una fuente real (OpenStreetMap, ODbL) tiene que llevar el id `mock` del bundle del caso, así que el proveedor solo se ve en el `source` de cada feature, nunca en el id. `pipeline/mock/checks.py` exige además exactamente un `provenance_id` entre grilla, serie y contexto.

Pedimos una decisión: si el contrato 1.1 debe permitir procedencia por capa (p. ej. `{case_id}+{provider}+{date}+{sha8}` para contexto, con grilla y serie en el id del producto), entra como cambio de contrato en `docs/contract-change-requests.md` con aprobación de los tres roles. Si no, decirlo explícitamente para que el rodeo actual (id del bundle + string `source`) quede como regla documentada.

**Impacto en contrato de datos:** posiblemente §6 (propiedades de contexto) y el gate; no se añade ningún campo al esquema en ningún caso.  
**Aprobación:** Rol 1 (dueño de `src/data/validation.py`); Rol 3 documenta el resultado.  
**Estado:** ABIERTA — pendiente de Rol 1.
`.gitignore` y `AGENTS.md` sale del índice (`git rm --cached AGENTS.md`, sin commit). No se reescribe la regla; el archivo sigue en el árbol de trabajo para quien lo lea en local. `tests/test_gitignore_policy.py` vuelve a verde. Si `main` prefiere el otro camino, basta con revertir la línea del índice y quitar la regla; el resto del cambio no depende de esta decisión.

## Rol 1 (`.gitignore`, CI) — un `AGENTS.md` rastreado también cae en una regla de ignore

**De:** Rol 2 (fradiaz)
**Para:** Rol 1 (pozo), dueño de CI
**Contexto:** higiene del repo; deja la suite en rojo.

El árbol de trabajo trae un cambio sin commitear en `.gitignore` que añade la línea `AGENTS.md` (última, después de `*~`), mientras `git ls-files AGENTS.md` informa el archivo como rastreado. Por eso falla `tests/test_gitignore_policy.py::test_no_tracked_file_is_ignored` con "tracked files are also matched by an ignore rule ... AGENTS.md". Verificado en esta sesión corriendo ese archivo: un solo FAIL, `AGENTS.md`.

Pedimos elegir una, no las dos:

- `git rm --cached AGENTS.md`: se mantiene la regla, el archivo sale del repo publicado.
- Quitar la línea de `.gitignore`: el archivo sigue rastreado.

La decisión no es nuestra: el archivo es el contrato de roles del proyecto y el único sitio donde están escritos los límites entre roles.

**Impacto en contrato de datos:** ninguno.  
**Aprobación:** Rol 1 (CI) y el dueño del repo.  
**Estado:** RESUELTA en `feat/contract-1.1` — decisión del dueño del repo: se mantiene la regla de `.gitignore` y `AGENTS.md` sale del índice (`git rm --cached AGENTS.md`, sin commit). No se reescribe la regla; el archivo sigue en el árbol de trabajo para quien lo lea en local. `tests/test_gitignore_policy.py` vuelve a verde. Si `main` prefiere el otro camino, basta con revertir la línea del índice y quitar la regla; el resto del cambio no depende de esta decisión.

## Rol 3 (narrativa) — retirar la metáfora "one pump" y re-scopear la tesis

**De:** Rol 3 (yuls), dueño de la narrativa — anotado a partir del trabajo de presentación y de la decisión del dueño del repo
**Para:** Rol 1 (pozo), por el literal de guarda en `tests/test_video_script_numbers.py` y el nombre del proyecto en `deploy/Dockerfile`
**Contexto:** presentación del proyecto (7 diapositivas), decisión del dueño del repo: la metáfora "one pump" no se sostiene y sale del material de presentación. La portada del deck pasa a llevar el nombre del reto, `Dancing with the SARs`, con el subtítulo "See how cities sink, from orbit. A NISAR risk assessment tool for researchers and planners." — el deck ya no depende de la metáfora.

Dos cosas distintas, que conviene decidir por separado:

**1. La tesis del README contradice a la guía científica.** `README.md` (línea 53) dice:

```
Subsidence is set by **interbed compressibility**, not by how much water is pumped.
```

`docs/science-guide.md` §3.4 dice algo más ajustado:

> That is the thesis: compressibility helps control the *rate and recoverability*, while pumping-driven head change supplies the forcing.

El README, tal como está, afirma que el bombeo no importa; la guía dice que sí aporta el forzamiento y que la compresibilidad fija la tasa y la recuperabilidad. No es una preferencia de estilo: son dos afirmaciones distintas. Pedimos re-escribir la línea 53 de `README.md` con la versión de la guía (o la que Rol 3 apruebe), porque hoy el README es lo que un juez lee primero.

**2. Dónde vive la metáfora (ninguno de estos archivos es de Rol 2):**

| Archivo | Qué contiene |
|---|---|
| `README.md` (línea 1) | `# Three Cities, One Pump` |
| `README.md` (línea 53) | "not by how much water is pumped" |
| `docs/video-script.md` (línea 11) | "not by how much water is pumped" |
| `docs/video-script.md` (línea 12) | "Same pump, different ground." |
| `docs/demo-fallback.md` (línea 82) | cita literal de "Same pump, different ground." |
| `tests/test_video_script_numbers.py` (línea 125) | **fija el literal**: `("one", "pump"): "video title ('Three Cities, One Pump')"` |
| `CONTEXT.md`, `docs/submission-checklist.md`, `scripts/make_figures.py`, `deploy/Dockerfile` | el nombre del proyecto, una ocurrencia cada uno |

El orden importa: el literal de `tests/test_video_script_numbers.py` (línea 125) fija el nombre del proyecto *dentro* del guion de vídeo, así que cambiar el guion sin tocar el literal deja la suite en rojo, y cambiar el literal sin cambiar el guion deja el guard sin sentido. Si el nombre cambia, guion y test se mueven en el mismo commit; si el nombre se queda, basta con retirar "Same pump, different ground." (línea 12 de `docs/video-script.md`, citada en la línea 82 de `docs/demo-fallback.md`) y re-escribir la línea 53 de `README.md`.

**Impacto en contrato de datos:** ninguno. Es prosa narrativa más un literal de test; no hay campo de esquema ni umbral en juego.
**Aprobación:** Rol 3 (narrativa y nombre del proyecto, es suyo); Rol 1 (pozo) por `tests/test_video_script_numbers.py` y el nombre en `deploy/Dockerfile`.
**Estado:** ABIERTA solo en la parte de Rol 1 (el literal del nombre); el deck de presentación ya no usa la metáfora y la narrativa la conserva hasta que el dueño del repo decida el nombre.

## Rol 3 (`pipeline/ingest/fetch_context.py`, `pipeline/ingest/osm_context.py`) — manifiesto de contexto: sello por tile, tiles fallidos y sha256 del archivo

**De:** Rol 3 (yuls)
**Para:** Rol 3 (yuls). Es un defecto interno, y se registra aquí porque el manifiesto es la evidencia de recuperación que `pipeline/context.py` y el pin committed citan como procedencia.

**Contexto:** el fetcher por tiles de esta rama tenía tres agujeros que ningún test veía, porque cada uno deja un artefacto legal: el manifiesto no sellaba el tile, `load_extract` confiaba en el directorio y el hash no era el del archivo. Corregidos en esta rama:

1. **El manifiesto omitía el sello OSM por tile y el rango de la corrida.** Un lote de tiles no es un snapshot y los tiles se piden minutos aparte: ahora `_tile_record` trae `osm_timestamp` (el `osm3s.timestamp_osm_base` con que se respondió ese tile, o `None`) y el manifiesto publica `timestamp_osm_base` como el **más reciente** de los observados, nunca el primero, más `osm_timestamp_base_range` (`[oldest]` cuando los tiles coinciden, `[oldest, newest]` cuando no), calculado por `_stamp_range`. Con más de un sello el CLI avisa; no se declara un snapshot compartido que la corrida no puede sostener.
2. **`load_extract` podía consumir un tile que el manifiesto marcaba fallido.** Un `--force` que falla conserva el archivo anterior como evidencia, así que el directorio `tiles/` no es la lista de lo recuperado. Ahora `_manifest_tile_states` devuelve el `ok` por tile y `load_extract` salta los stems marcados, deja de leer (no borra) el archivo, falla con `FileNotFoundError` si el manifiesto marca todos como no recuperados, y publica los saltados en `frame.attrs["skipped_tiles"]`. Un manifiesto que no nombra ningún tile (el que usan algunos fixtures) se comporta como antes: la ausencia de registro no es un fallo.
3. **El `sha256` se calculaba sobre el cuerpo en memoria, no sobre los bytes del disco.** La ruta de descarga hasheaba `outcome.body` mientras la ruta de *skip* hasheaba el archivo, así que dos ejecuciones sobre el mismo tile podían publicar hashes distintos y la auditoría del manifiesto no probaba nada: ahora se escribe con `write_bytes` y se hashea `path.read_bytes()` en ambas rutas, que es el archivo que un lector va a parsear.

**Impacto en contrato de datos:** ninguno. `manifest.json` es el registro de recuperación (conserva su forma, `contract_version: "1.0"`, schema del informe de búsqueda y no `SCHEMA_VERSION`) y no cambia ningún campo de los cuatro artefactos ni la forma de `config/context/<case>.json`, cuyo bloque `record` ya llevaba `timestamp_osm_base` y los conteos de tiles. Lo que cambia es qué bytes entran al join cuando hay un tile fallido; eso se regenera con el escritor del pin, no a mano.
**Aprobación:** Rol 3, dueño de `pipeline/`. No hay cambio de contrato que aprobar y no toca archivos de Rol 1 ni de Rol 2.
**Estado:** RESUELTO en `feat/contract-1.1` — las tres correcciones están en el código y las cubren los tests del fetcher y del pin de esta rama.

## Todos (integración en `main`) — el dueño ordenó integrar sin la firma de O-05

**De:** Rol 3 (yuls, dueño del repositorio)
**Para:** Rol 1 (pozo), Rol 2 (fradiaz)
**Contexto:** rama de integración construida a partir de `feat/contract-1.1` (`78c3329`) + `main` (`dce1b9b3`) + `pozo` (`24a69ee`).

`docs/decisions.md` (O-05) pide el acuerdo de Rol 1 y Rol 2 antes de tocar `main`. Ese acuerdo **no se obtuvo**. El
dueño pidió explícitamente integrar todo en `main`, así que la fusión se hace por decisión del dueño y **queda
registrada aquí** en lugar de ocurrir en silencio — `main` ya lleva una integración de 1.1 sin firma, y una segunda
sin registro es cómo la gobernanza deja de significar algo.

Lo que sí está verificado antes de integrar, para que el acuerdo se pueda dar a posteriori con evidencia:

- contrato: `SCHEMA_VERSION` 1.1, huella `ed92be802ff8846fe369058d9a80c9545c2a694a17896de6a5f1844146e302ae`,
  idéntica en las cuatro ramas (base, `main`, `pozo`, `feat/contract-1.1`);
- datos: `main` y `pozo` **nunca** regeneraron `data/processed/` (0 de 12 ficheros difieren de la base), así que la
  fusión conserva los artefactos del contrato 1.1 y sus `provenance_id` con ventana;
- artefactos: `validate_artifacts --require-cases --strict-size-mb 15` → PASS, 1.687 MB; `src.data.validation` → PASS
  en los tres casos;
- despliegue: `deploy/Dockerfile`, `.dockerignore` y `streamlit>=1.67` tomados de `pozo`, porque `main` lee
  `docs/science-guide.md` en tiempo de ejecución y nuestra imagen no lo copiaba (la tarjeta de física de la pestaña
  4 desaparecía sin error);
- guardas: 1.610 pruebas verdes; las 26 restantes están todas en `tests/test_ui_matches_artefacts.py` y son
  expectativas desfasadas contra el rediseño del cockpit, no defectos de producto.

**Pendiente conocido y no resuelto aquí:** la capa de contexto OSM. Los pines (3.464.620 B) están versionados y su
consumidor no, de modo que la rama integrada los lleva inertes. Debe resolverse en un sentido o en otro antes de
publicar: o entra la capa completa en un solo commit, o se retiran los pines. **Resuelto en el commit siguiente:**
entra la capa completa en un único commit — consumidor, cableado (`pipeline/enrich_context.py`,
`pipeline/generate_mock.py`, `pipeline/mock/*`), los artefactos regenerados, los pines, los seis módulos de prueba
nuevos y sus filas en `docs/verification.md`; los números de esa entrada están en `docs/context-sources.md`.

**Aprobación:** pendiente de Rol 1 y Rol 2 (a posteriori). Dueño: Rol 3, que asume la decisión.
**Estado:** INTEGRADO POR DECISIÓN DEL DUEÑO — firma de O-05 pendiente.

## Rol 2 (`app.py`, `tests/test_ui_smoke.py`) — tres cambios de Rol 3 durante la integración

**De:** Rol 3 (yuls)
**Para:** Rol 2 (fradiaz), dueño de `app.py` y de las pruebas de la UI
**Contexto:** al integrar la capa de contexto OSM (62 ficheros, `902d0e1`) y el rediseño de `main` (`6d3e158`), tres
sitios de tu ámbito dejaron de ser ciertos o de cumplirse. Los toqué para no entregar un árbol en rojo, y los declaro
para que los revises: la redacción es tuya.

1. **`app.py` — falta la atribución ODbL en pantalla.** El árbol tenía la licencia en `LICENSE`,
   `docs/context-sources.md` §5 y `meta.provenance.license`, y **cero** menciones en `app.py` o `src/ui/`; con la capa
   OSM la aplicación pinta 708 polígonos reales por caso sin acreditar a nadie. Añadí `render_attribution()`, una
   `st.caption` a nivel de aplicación (las dos pestañas que muestran los polígonos), con el texto canónico de §5 más
   una frase que aclara que los valores de desplazamiento son sintéticos. Si prefieres otra colocación o redacción,
   cambia la función: la guarda `tests/test_attribution.py` sólo exige que la línea esté en pantalla, que siga
   diciendo `mock` y que los tres artefactos conserven la misma cadena.
2. **`tests/test_ui_smoke.py` — afirmaba un nombre sintético que ya no existe.** Asertaba
   `"Zócalo Metro Station (synthetic)"`, que sólo existía mientras la capa de contexto eran 12 elementos inventados.
   Ahora toma un nombre **del artefacto** (`data/processed/mexico-city/context_layers.geojson`) y comprueba que llega
   a pantalla; no se retipea ninguna constante.
3. **`tests/test_ui_unit_fallbacks.py` — seguía importando `_render_kpis`.** Tu rediseño lo movió a
   `src/ui/components.py::kpi_card` y quitó el límite numérico de `detection_caption`, así que el fichero no
   compilaba. Lo reorienté a los sitios nuevos y trasladé allí el fallback de unidad, porque tu versión imprimía la
   unidad sólo `if unit else ""` — el hueco se había mudado, no cerrado.

**Impacto en contrato de datos:** ninguno.
**Aprobación:** tuya, a posteriori. Dueño de la integración: Rol 3.
**Estado:** ABIERTA — cambios aplicados, redacción pendiente de tu revisión.
