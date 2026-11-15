# Peticiones entre roles

Índice:

| Rol | Petición | Estado |
|---|---|---|
| Rol 1 (pozo) | `get_map_layer_df`: extrusión por defecto en `False` | resuelta en `main` (`7f0b455`), integrada en `fradiaz` |
| Rol 1 (pozo) | `get_case_series`: `scaled` y `scale_factor`; nota de KPI en inglés | resuelta en `main` (`7f0b455`); la UI ya consume `scaled` |
| Rol 1 (pozo) | `load_documented_limits`: párrafo completo del caveat e ítems con sus líneas de continuación | resuelta en `main` (`ui_bridge` y `_stubs` unen el párrafo del caveat y todas las líneas de continuación de `section_13`) |
| Rol 3 (yuls) | Texto aprobado por caso, módulo educativo, glosario de contexto, campos contradichos y definiciones | abierta |
| Todos (dueño de `docs/verification.md`) | Registrar `tests/test_doc_provenance_ids.py` en la tabla de familias del registro | abierta |
| Rol 1 (pozo) | `validate_case_bundle`: el contexto no puede nombrar a su propio proveedor | abierta |
| Rol 1 (pozo) | `.gitignore` ignora `AGENTS.md` rastreado; elegir `git rm --cached` o quitar la línea | abierta |

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
**Estado:** ABIERTA — pendiente de Rol 1.
