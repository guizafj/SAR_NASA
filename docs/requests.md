# Peticiones entre roles

Índice:

| Rol | Petición | Estado |
|---|---|---|
| Rol 1 (pozo) | `get_map_layer_df`: extrusión por defecto en `False` | resuelta en `main` (`7f0b455`), integrada en `fradiaz` |
| Rol 1 (pozo) | `get_case_series`: `scaled` y `scale_factor`; nota de KPI en inglés | resuelta en `main` (`7f0b455`); la UI ya consume `scaled` |
| Rol 1 (pozo) | `load_documented_limits`: párrafo completo del caveat e ítems con sus líneas de continuación | resuelta: caveat en `7f0b455` y continuación de ítems en el parser de `src/data/ui_bridge.py` |
| Rol 3 (yuls) | Texto aprobado por caso, módulo educativo, glosario de contexto, campos contradichos y definiciones | abierta |

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
**Estado:** RESUELTO por Rol 1 (`pozo`) — `load_documented_limits` devuelve el párrafo completo del README unido con espacios hasta la línea en blanco posterior.

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
