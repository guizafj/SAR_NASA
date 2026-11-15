# Fuentes de la capa de contexto — OSM, cobertura real y licencia

**NASA Space Apps Challenge 2026 · Dancing with the SARs · rol Ciencia SAR / GIS (Rol 3).**

| | |
|---|---|
| Artefacto afectado | `context_layers.geojson` (contrato §6) y la columna `land_use` de `nisar_grid.parquet` |
| Productor del extracto | `pipeline/ingest/fetch_context.py` (CLI, §2) |
| Lector del extracto | `pipeline/ingest/osm_context.py` |
| Tabla de tags y unión por celda | `pipeline/enrich_context.py` |
| Proveedor que llega al artefacto | `OpenStreetMap contributors (ODbL 1.0), Overpass API extract` (`osm_context.SOURCE`) |
| Licencia de los polígonos publicados | ODbL 1.0 (§5) |
| Contexto fijado por caso | escritor `pipeline/ingest/pin_context.py`, lector `pipeline/context.py`, fichero `config/context/<case>.json` (§7) |

Este documento contesta, en este orden, las preguntas que un revisor hace: qué capa se publica y
de dónde sale, cuánta cobertura tiene de verdad, qué fuentes alternativas se descartaron y por qué,
y qué obliga la licencia. Cada cifra lleva su fecha de medida y el sitio donde se midió; lo que no
está medido se declara no medido.

---

## 1. Qué se publica y de dónde sale

`context_layers.geojson` lleva cuatro capas. `water`, `infrastructure` y `admin_boundary` son
geometría del extracto de OpenStreetMap; `land_use` es la disolución de las celdas del propio grid
por clase, y `admin_boundary` añade además el anillo del AOI, que es configuración y no fuente
externa.

| Capa | Geometría publicada | Origen |
|---|---|---|
| `land_use` | polígonos: una geometría disuelta por clase presente en el grid (`mock/artifacts.build_context` → `mock/model.land_use_geometries`) | la clase de cada celda viene del extracto OSM (`landuse`, `natural`, `leisure`, `amenity`, `place`, `building` mapeados por `enrich_context.TAG_RULES`), pero lo que se publica son las celdas H3, no los polígonos de OSM: publicarlos crudos no cabe en el presupuesto de 15 MB y dejaría que la capa y la columna divergieran |
| `water` | polígonos y líneas | extracto OSM: `natural=coastline/water/wetland/spring`, `waterway`, `landuse=reservoir/basin` |
| `infrastructure` | puntos y líneas | extracto OSM: `man_made`, `amenity`, `power`, `railway`, `public_transport` |
| `admin_boundary` | polígonos | `place=city/town/…` y `boundary=administrative` del extracto; más la feature `admin_boundary/aoi`, que sale de `config/aoi/<case>.geojson` (`pipeline/aoi.py`) y se publica aparte del extracto |

La etiqueta de cada celda se calcula una vez. `enrich_context.join_land_use` hace un
`geopandas.sjoin` de los centroides H3 contra las features **areales** del extracto
(`predicate="intersects"`), con la precedencia `LAND_USE_PRECEDENCE`:

```
water > infrastructure > industrial > urban-dense > urban-residential > vegetation > bare > unknown
```

y desempata por área menor y después por digest de geometría, nunca por orden de filas. Si el caso
tiene pin, la etiqueta sale del pin (§7) en lugar del sjoin, pero el pin se construyó con esa misma
operación sobre el extracto. La capa `land_use` del GeoJSON no publica esos polígonos: disuelve las
celdas del grid por clase, así que capa y columna son el mismo array y no pueden divergir.

Tres límites del join, explícitos en el código:

- Puntos y líneas se publican al mapa pero **no asignan** land use: un punto es 0-D y una línea 1-D,
  no contienen centroides (`enrich_context._areal_context`).
- `admin_boundary` tampoco asigna: una frontera cubre todos los usos y ganaría cualquier desempate.
- Un tag que ninguna regla mapea se clasifica como `land_use/unknown`, y la celda que lo contiene
  cae en `unknown`; **no se descarta**. Nada se borra por no estar mapeado.

El extracto se lee completo y deduplicado por `(type, id)`, porque un way que toca dos tiles vuelve
en los dos. `osm_context.TAG_KEYS` fija las claves que sobreviven al paso por el frame (`landuse`,
`natural`, `waterway`, `leisure`, `man_made`, `amenity`, `power`, `railway`, `public_transport`,
`place`, `boundary`, `building`, `name`); el resto de tags se descarta al leer, no al clasificar.

---

## 2. Cómo se obtiene el extracto (offline, una vez)

`pipeline/ingest/osm_context.py` sólo lee lo que ya está en disco (sin `manifest.json` levanta
`FileNotFoundError`) y no importa nada de red. El productor es `pipeline/ingest/fetch_context.py`,
que sí está en el árbol: parte el bbox del caso en tiles, pide un tile cada vez a Overpass y escribe
cada respuesta 200 literal. Es la receta de abajo en forma ejecutable, y `pipeline/ingest/README.md`
todavía la describe como si la descarga no estuviera en el repositorio.

Flags reales (`fetch_context.build_parser`):

| Flag | Qué hace |
|---|---|
| `--case` | caso (repetible); por defecto, todos los de `config/study_cases.yaml` |
| `--root` | raíz del extracto (por defecto `data/raw`) |
| `--out` | escribe en `<out>` en lugar de `<root>/<case>/context` (un solo `--case`) |
| `--force` | vuelve a pedir los tiles que ya están en disco |
| `--include-buildings` | añade `building=*` a la consulta; opt-in porque es la capa más densa de OSM |
| `--dry-run` | imprime la rejilla y el destino de cada petición; no envía ni escribe nada |
| `--pause` | segundos entre tiles (8 por defecto) |
| `--backoff` | segundos antes de un reintento (30 por defecto) |
| `--attempts` | peticiones máximas por endpoint y tile (2 por defecto) |
| `--timeout` | segundos por petición (180 por defecto) |
| `--endpoint` | sustituye la escalera de endpoints (repetible, en orden) |
| `--config` | YAML de casos (`config/study_cases.yaml`) |

Reglas de la petición, leídas del código y no de la receta:

| Regla | Valor |
|---|---|
| Rejilla de tiles | como máximo 0.15° por lado (`MAX_TILE_DEG`), derivada del bbox del caso |
| Peticiones | una cada vez, sin concurrencia; la espera es entre peticiones, no antes de la primera |
| Identificación | `User-Agent` propio (`USER_AGENT`) en cada petición |
| Espera entre tiles | 8 s (`DEFAULT_PAUSE_SECONDS`) |
| Backoff | 30 s antes de reintentar un 429 / 502 / 504, una respuesta 200 ilegible o una caída de transporte |
| Escalera | `overpass-api.de` → `overpass.private.coffee` → `ovp.dresel.systems` (`ENDPOINTS`) |
| Reintentos | `--attempts` peticiones por endpoint y tile; un estado no reintentable (400, por ejemplo) no espera ni gasta los reintentos de ese endpoint, y pasa al siguiente |
| Respuesta pedida | `out geom` (el ensamblado de relaciones usa la geometría de cada miembro; no hay node store) |
| Código de salida | 0 si todos los tiles quedaron en 200 (pedidos u omitidos), 1 si alguno falló |

Salida, por caso, bajo `data/raw/<case_id>/context/` (gitignored):

```
manifest.json                        registro de la recuperación
tiles/<case>_r<r>_c<c>.json          respuestas de Overpass, literales, una por tile
```

`manifest.json` guarda, por tile: id, bbox, endpoint usado, estado HTTP, bytes, `sha256`, número de
elementos, segundos y si se omitió (`skipped`); y en el nivel superior `case_id`, `bbox`,
`retrieved_utc`, `timestamp_osm_base`, `source`, `totals` y el bloque `request` con la política
usada. El extracto que hay hoy en `data/raw/` se hizo antes de que el módulo existiera y usa otra
forma (`extraction_utc`, `http_status`/`ok`, `osm_timestamp` por tile, `osm_timestamp_base`, `bbox`
como diccionario de bordes); `osm_context.extract_record` acepta las dos y consume
`retrieved_utc`, `timestamp_osm_base`, `bbox`, el estado y `elements` de cada tile. Los demás campos
son el registro que permite auditar la petición, no algo que el pipeline necesite para producir el
artefacto.

`retrieved_utc` es un reloj de pared tomado una vez por ejecución, así que los tiles **no** son un
artefacto determinista: la misma petición mañana devuelve otros bytes. Lo determinista es el pin que
se construye con ellos (§7), que conserva las decisiones y descarta el cronómetro.

### Alternativas documentadas

| Alternativa | Qué es | Por qué no es el camino actual |
|---|---|---|
| Geofabrik, extractos subnacionales | un `.osm.pbf` de la región que contiene el AOI | necesita `osmium extract` + `osmium tags-filter`, y **osmium no está instalado en este entorno**; GDAL puede leer OSM, y aquí `ogr2ogr` y `pyogrio` están presentes, pero `rasterio` no (medido hoy) |
| BBBike | extractos personalizados por ciudad | mismo requisito de recorte/filtrado que el anterior |
| Overpass JSON | el que usa este pipeline | es el formato que el código lee hoy: no requiere osmium ni GDAL, y el manifest deja el registro por tile |

La conclusión operativa es estrecha y por eso se escribe: Overpass JSON es el camino **que el código
soporta**; un `.osm.pbf` de Geofabrik serviría como fuente, pero exigiría una cadena de herramientas
que esta máquina no tiene instalada.

---

## 3. Cobertura medida y su límite

OSM `landuse` es **de facto**, no completo: depende de que alguien haya dibujado el polígono. La
cobertura no se supone, se mide sobre lo que el repositorio publica. Medido hoy sobre
`data/processed/<case>/nisar_grid.parquet` y `config/context/<case>.json`, y con
`enrich_context.join_land_use` sobre el extracto cacheado para separar los dos motivos de `unknown`:

| Caso | Celdas del pin | Celdas publicadas | `unknown` | % unknown | Celdas sin polígono | % sin polígono |
|---|---:|---:|---:|---:|---:|---:|
| Málaga | 4 825 | 2 758 | 1 080 | 39.2 % | 0 | 0.0 % |
| Yakarta | 7 935 | 4 936 | 2 434 | 49.3 % | 2 288 | 46.4 % |
| Ciudad de México | 17 618 | 11 600 | 7 897 | 68.1 % | 7 792 | 67.2 % |

«Sin polígono mapeado» es la celda cuyo centroide no cae en ninguna feature areal del extracto
(`context_id` vacío en `join_land_use`). El resto de las `unknown` son celdas dentro de un polígono
cuyo tag no tiene regla en `TAG_RULES`: 1 080 en Málaga (todas), 146 en Yakarta y 105 en Ciudad de
México. Son cifras de cobertura de OSM, no un defecto de la app: `unknown` es una categoría
publicable del vocabulario y el grid no la rellena.

Las cifras nacionales de taginfo y el recuento de features por ventana que este documento citaba
antes no se repiten aquí: no salen de `data/processed/` ni de `config/context/*.json`, y sin red no
se pueden volver a medir.

Una celda sin polígono mide cuánto hay dibujado, no cuánto hay en el suelo. De ahí la obligación, que
es una regla del pipeline y no una aspiración:

> Una celda se publica `unknown` por dos motivos, y sólo dos: su centroide no cae dentro de ningún
> polígono mapeado, o cae dentro de uno cuyos tags no mapean a ninguna clase (`land_use/unknown` de
> `_classify`). Nunca se extrapola del polígono vecino, ni se rellena por clase dominante, ni se
> interpola, ni se omite.

Cómo se cumple, en el código: `join_land_use` arranca todas las celdas en `unknown` y sólo
sobrescribe las que ganan el sjoin; `unknown` es el último de `LAND_USE_PRECEDENCE`, así que ninguna
feature puede "ganar" por defecto; `_classify` asigna `land_use/unknown` a cualquier tag sin regla.

Y la app tiene que poder **mostrar esa fracción**, porque es un resultado como cualquier otro:

- `unknown` está en el vocabulario congelado (`contracts.LAND_USE`), así que es una categoría
  publicable, no un nulo;
- el resumen del enriquecedor publica `cells_unknown` y el recuento por clase (`cells_by_land_use`),
  de modo que la pérdida se reporta en vez de esconderse;
- los loaders enumeran las categorías presentes en el grid (`load_land_use_options`) sin rellenar las
  ausentes, así que `unknown` es seleccionable y graficable como cualquier clase.

Una celda `unknown` es una respuesta —"no está mapeado"— y no un hueco que haya que tapar antes de
enseñar el mapa.

---

## 4. Qué NO se usa y por qué

| Fuente | Qué ofrece | Por qué queda fuera |
|---|---|---|
| Google Open Buildings V3 | huellas de edificios | **no cubre España** (lista de países comprobada); el control negativo del proyecto se quedaría sin fuente |
| Overture `base.land_use` | polígonos de uso del suelo | es un *pass-through* de `landuse` de OSM sin conflación: hereda exactamente la misma escasez que se documenta en §3 y añade una capa de indirección sin información nueva |
| Copernicus Urban Atlas | uso del suelo urbano | cubre Málaga, pero **explícitamente no** Ciudad de México ni Yakarta; dejaría dos de los tres casos sin fuente |
| GHSL R2023A (`GHS-BUILT-S`, `GHS-BUILT-C`, `GHS-SMOD`) | global; licencia CC BY 4.0, acceso anónimo | da fracción construida y morfología del asentamiento, no las clases congeladas del contrato; y `GHS-SMOD` tiene 1 km de celda, ~5× más gruesa que una celda H3 r9 — asignar clases con ella sería un remuestreo, no una observación |

Como fuente *de clases* GHSL exigiría además un paso raster → polígono y una tabla de decisión
documentada; eso vive en un ingestor raster que este pipeline no tiene, y el docstring de
`pipeline/enrich_context.py` lo deja dicho en vez de improvisarlo.

### Por qué no hay un umbral de densidad por celda

La tentación es partir `urban-dense` de `urban-residential` con un umbral de fracción construida por
celda. Se midió sobre dos ciudades y no existe tal umbral: la distribución es un continuo, el hueco
mayor de la curva ordenada no separa dos poblaciones —es sólo el hueco mayor de una curva suave— y
la mediana de una ciudad no sirve de corte en la otra, de modo que cualquier constante elegida en
una etiqueta mal la otra. Un corte así sería un artefacto de la constante, no una propiedad del
suelo.

Las cifras de esa medida no se repiten aquí, por la misma razón que las de taginfo en §3: el
protocolo que las produjo no está en el árbol y la fracción construida no sale de `data/processed/`
ni de `config/context/*.json`, así que no se puede volver a medir con lo que el repositorio publica.
Queda la conclusión, que no depende de los decimales.

Por eso la clase viene de la **semántica del tag** de OSM y no de una densidad calculada:
`landuse=residential` es tejido residencial; `landuse=commercial|retail`, un campus
(`landuse=education|religious`), un equipamiento (`building=commercial|office|…`) o una plaza
(`place=square`) son tejido denso/de servicio. El razonamiento está también en el docstring de
`pipeline/context.py`, que es donde se tomó la decisión.

---

## 5. Licencia y atribución (ODbL 1.0)

Los datos de OpenStreetMap están bajo la **Open Database License (ODbL) 1.0**. Lo que eso obliga
depende de qué se publique:

| Qué se publica | Qué es bajo ODbL | Obligación |
|---|---|---|
| `context_layers.geojson` commiteado (polígonos extraídos y filtrados) | **Derivative Database** | el fichero se ofrece bajo ODbL 1.0, no bajo la licencia MIT del código |
| mapa renderizado, captura de pantalla, figura del README | **Produced Work** | sin share-alike sobre la imagen, pero **con** el aviso de atribución |

Texto de atribución a usar, literal:

```
Contains information from OpenStreetMap, which is made available here under the Open Database License (ODbL). © OpenStreetMap contributors — https://www.openstreetmap.org/copyright
```

Ese aviso es el que va en la app y en las figuras. La entrada del `README.md` §17 lo enlaza desde la
perspectiva del repositorio.

Sobre la frontera Produced Work / Derivative Database: la guía de Produced Work refrendada por el
consejo de la OSM Foundation no se decide por el formato del fichero sino por si el resultado
publicado está pensado para permitir extraer los datos originales. Un GeoJSON de polígonos lo
permite —de ahí que se trate como base de datos derivada y se ofrezca bajo ODbL—; un PNG del mapa no.

**Cuestión abierta: licencia mixta.** Combinar polígonos de OSM (ODbL 1.0) con huellas de edificios
bajo CDLA-Permissive-2.0 plantea una pregunta de compatibilidad que este documento **no resuelve**:
queda marcada como **ABIERTO** y no se afirma ninguna conclusión. Hoy no hay ninguna capa publicada
que mezcle las dos; si se mezcla, la pregunta se contesta antes de publicar, no después.

El `LICENSE` del repositorio es MIT para código, documentación y figuras, y dice explícitamente que
no relicencia los datos (decisión D-26 en `docs/decisions.md`, `README.md` §17).

---

## 6. Reproducibilidad

- **El extracto crudo está gitignored** (`data/raw/`): son decenas de MB y no pertenecen al
  repositorio. Lo que se commitea es el artefacto (`context_layers.geojson`) y el pin de cada caso
  (`config/context/<case>.json`), no la entrada.
- **`provenance_id` es uno por caso**, el mismo en grid, series y contexto: la puerta de artefactos
  exige un único id por caso, así que el contexto no puede tener un id propio que lo separe del
  resto. Consecuencia práctica: el *proveedor* viaja en el `source` de cada feature
  (`osm_context.SOURCE`), y el *registro de recuperación* en el manifest y en el `record` del pin
  (§7). Un polígono publicado sigue siendo atribuible aunque los tiles se hayan borrado.
- **Sin extracto, el orden es pin → extracto → fallback declarado.** `load_context` consulta
  `config/context/<case>.json` antes que el extracto, para que un clon reproduzca el artefacto byte a
  byte sin haber descargado nada (§7); si no hay pin, lee el extracto; y si tampoco hay extracto, cae
  a la geografía sintética y cada feature lo dice en su `source` —
  `mock (synthetic land-cover rule)`, `mock (synthetic hydrology)`, `mock (synthetic utility POI)` o
  `mock (synthetic AOI)`. Es la única forma de que el repositorio arranque sin red; y es visible, no
  silenciosa: si el `source` de un fichero dice `mock (synthetic …)`, ese fichero es el fallback y no
  una extracción de OSM. `load_context(..., allow_synthetic=False)` convierte el fallback en error
  para quien necesite probar que el extracto existe.
- **Cómo se comprueba qué hay cacheado**: `osm_context.summarise(case_id)` devuelve
  `{"cached": false}` cuando no hay manifest, y si lo hay, el recuento de elementos, el `record` y un
  censo de tags (`landuse`, `natural`, `waterway`, `leisure`). La receta y el comando están en
  `pipeline/ingest/README.md`.
- **Lo que no es reproducible sin red es la descarga**, no el artefacto: dado el mismo extracto (o el
  mismo pin), el artefacto es determinista — coordenadas redondeadas a
  `enrich_context.COORD_DECIMALS` (7 decimales), GeoJSON con separadores fijos, `meta.json` con
  `sort_keys` y escritura atómica (`.tmp` + `os.replace`). El orden de las features lo fija la
  construcción, no el orden de filas: las clases de `land_use` en orden
  alfabético y después `infrastructure`, `water`, el AOI y `admin_boundary`, cada capa ordenada por
  tamaño con desempate por clase, nombre y WKB (§7).

---

## 7. Contexto fijado (`config/context/<case>.json`)

El contexto de un caso tiene dos mitades, y sólo una está en el repositorio.

| Mitad | Ruta | ¿Commit? | Para qué sirve |
|---|---|---|---|
| Extracto crudo | `data/raw/<case_id>/context/` | no (gitignored, decenas de MB) | la evidencia: los cuerpos de Overpass literales y su manifest (§2) |
| Contexto fijado | `config/context/<case_id>.json` | sí (1 156 256 B en Yakarta, 845 707 B en Málaga y 1 462 719 B en Ciudad de México, medido con `ls -l`) | las decisiones que el extracto implica, en JSON legible |

El extracto no se commitea porque son decenas de MB. Pero el artefacto tiene que regenerarse
idéntico en una máquina que nunca descargó nada, incluida CI. Lo que falta ahí no son los tiles: son
las decisiones que los tiles implican — qué clase congelada tiene cada celda H3, qué polígono de mar
se deriva y qué geometría de agua, infraestructura y límite administrativo se publica. Eso es el pin.

Escritor y lector no se solapan, y la dependencia va en un solo sentido: `pipeline/ingest/pin_context.py`
escribe el pin, `pipeline/context.py` lo lee en cada generación. El escritor es una herramienta
offline y el generador no la importa, así que no hay ciclo de imports ni forma de que el pipeline
consulte la red sin querer.

### Cómo se reconstruye

```bash
./scripts/python.sh -m pipeline.ingest.pin_context --case malaga   # un caso
./scripts/python.sh -m pipeline.ingest.pin_context                 # los tres declarados
```

`--case` es repetible; `--out` cambia el directorio de salida (por defecto `config/context`). El
comando lee el extracto cacheado del caso y no inventa nada: sin `manifest.json` termina con un
error que nombra la ruta que falta, en vez de escribir un pin vacío.

**El pin es dato derivado y no se edita a mano.** Se escribe con claves ordenadas, separadores
mínimos y `pin_sha256` = sha256 del cuerpo sin ese campo, y se reemplaza de forma atómica
(`.tmp` + `replace`): la misma entrada da el mismo byte y el mismo hash, y una reconstrucción se
puede comparar con el fichero commiteado. Y se verifica al leer, pero **sólo si el campo está
presente**: `read_pin` saca `pin_sha256` del JSON, y si trae valor recalcula el sha256 del cuerpo y
lanza `ValueError` cuando no coinciden, con un mensaje que nombra `python -m pipeline.ingest.pin_context`
como el único productor válido; un pin sin el campo se devuelve tal cual, sin comprobar. Así que un
pin editado a mano se rechaza en la lectura sólo mientras conserve su hash — borrarlo también es
editarlo, y esa edición no se detecta. La regla sigue siendo no editarlo: el hash lo hace
detectable, no correcto.

### Qué contiene

| Clave | Contenido |
|---|---|
| `case_id`, `crs`, `source` | el caso, `EPSG:4326` y el proveedor (`osm_context.SOURCE`, ODbL 1.0) |
| `record` | `source`, `retrieved_utc`, `timestamp_osm_base`, `tiles_ok`, `tiles_failed`, `elements`, `bbox`: el mismo registro del manifest (§2) |
| `sea` | el polígono de mar derivado y simplificado, o `null` cuando no se pudo derivar (§8) |
| `cell_land_use` | `{cell_id H3 r9: clase congelada}` para la unión del anillo del AOI y las celdas del prefilter del modelo (`pin_context.cells_to_label`), que es exactamente el conjunto que el modelo puede publicar |
| `water`, `infrastructure`, `admin` | features GeoJSON simplificadas, con `class`, `source` y `name` opcional |
| `class_counts` | recuento por clase de `cell_land_use` |
| `coastline_lines` | cuántas polilíneas de costa traía el extracto completo, contadas por `build_pin` con `coast_lines` antes de recortar `water` (§8) |
| `pin_sha256` | hash del fichero sin este campo, añadido por `pin_context.write_pin` |

`coastline_lines` es un contador, no geometría: `build_pin` lo escribe como
`len(CTX.coast_lines(mapped))` sobre el extracto entero, antes del tope por tipo, y vale 14 en
Yakarta, 34 en Málaga y 0 en Ciudad de México. Hace falta porque el pin sólo publica las features de
agua más grandes de cada tipo —la costa que sobrevive son 12 features en Yakarta, 6 en Málaga y 0 en
Ciudad de México—, así que sin el contador un caso costero que hubiera perdido *todas* sus líneas de
costa en el tope se leería igual que un AOI interior y el gate del mar pasaría de forma vacía: su
condición es «si hay costa, debe haber polígono de mar». `pinned_context` lo carga en
`counters["coastline_lines"]`, y `assert_case` en `pipeline/mock/checks.py` (línea 95, bloque 12)
acepta ese contador o una feature `coastline` en `water` como prueba de que la costa existía.

Las etiquetas se calculan sobre el extracto **sin simplificar**: `label_cells` corre antes de que la
geometría se simplifique, de modo que el redondeo afecta a lo que se dibuja y no a qué clase tiene
una celda. La geometría que sí se guarda en el pin ya viene simplificada (2e-4 °) y redondeada
(1e-5 °), y `mock/artifacts._published_context_features` no la vuelve a simplificar cuando el
contexto está fijado: hacerlo producía bytes distintos para el mismo seed según que el contexto
viniera del pin o del extracto. `admin` excluye la feature `aoi`, porque el límite del AOI es
configuración y no fuente externa (§1).

Una celda que el pin **no** nombra es `unknown`: `label_cells` toma el diccionario y devuelve
`unknown` cuando no hay entrada, que es el mismo valor honesto que el de una celda que ningún
polígono contiene (§3) — y que el de un polígono cuyo tag no tiene regla (§1). Desde que
`cells_to_label` toma la unión del anillo con el prefilter, toda celda publicada está nombrada por el
pin; la regla sigue existiendo para una celda fuera de ese conjunto, que el modelo no publica. Sobre
esas etiquetas se aplica después el override de mar si el pin trae polígono `sea`, igual que sobre un
extracto recién leído (§8).

### La regla que hace determinista el artefacto

`source` y `retrieved_utc` viajan con las features. Cada feature del pin guarda su
`properties.source`, y `pin_features` lo usa (si faltara, cae al `source` del pin); `pinned_context`
recupera `retrieved_utc` y `timestamp_osm_base` del `record`. Por eso un polígono fijado es tan
atribuible como uno recién mapeado: el proveedor y la fecha del corte no se pierden al dejar de
tener los tiles. Lo que se pierde es el byte del tile, que es un cronómetro y no una decisión.

### Tope por tipo de geometría (`FEATURE_CAP_PER_KIND`, `ADMIN_FEATURE_CAP`)

| Constante | Valor | Dónde | Qué limita |
|---|---:|---|---|
| `FEATURE_CAP_PER_KIND` | 150 | `pipeline/context.py` | features por capa y por tipo de geometría (`water`, `infrastructure`) |
| `ADMIN_FEATURE_CAP` | 50 | `pipeline/context.py` | features de `admin_boundary`, también por tipo |

El tope no es prudencia genérica: viene de un fallo concreto. Cuando `water` se recortaba
ordenando **todo** por área, la línea de costa desaparecía del artefacto — una costa es una
`LineString` y el área de una línea es cero, así que perdía contra cualquier polígono, por pequeño
que fuera. Con la costa fuera, el mapa dejaba de dibujar el litoral y el gate del mar (§8) dejaba de
ver una costa contra la que exigir el polígono, así que la regla se apagaba sin que ningún artefacto
cambiara de forma. El polígono de mar del pin sí se deriva del extracto completo, no de la capa
recortada.

Por eso el recorte es por tipo: los polígonos más grandes, las líneas más largas y los puntos. Se
ordena por área (polígonos) o longitud (líneas) medidas en EPSG:3857 —dentro de un AOI la distorsión
es monótona, y ordenar en grados pondría grados donde van metros— y los empates se resuelven por
clase, nombre y WKB de la geometría, nunca por orden de filas, de modo que una repetición no puede
reordenar el artefacto. `select_for_publication` es la única implementación y la usan tanto el
escritor del pin como el generador del artefacto, con el mismo número: pin y artefacto no pueden
discrepar sobre qué features existen.

Medido sobre `config/context/malaga.json` (2026-11-15): `water` lleva 150 polígonos y 150 líneas
(6 de esas líneas son de clase `coastline`), `infrastructure` 102 polígonos y 150 líneas, `admin`
50 y 50. El tope no es holgado: en las líneas de agua, en las de infraestructura y en ambas de
`admin` se alcanza exactamente.

### Cobertura del pin

El pin nombra 4 825 celdas en Málaga, 7 935 en Yakarta y 17 618 en Ciudad de México, y de ellas
quedan `unknown` 1 897, 3 914 y 10 713. Es más de lo que se publica porque el pin cubre el prefilter
entero, incluidas las celdas que el filtro de coherencia descarta; la cobertura del grid publicado,
que es la que ve el revisor, está en §3.

---

## 8. El mar como polígono derivado

`water` es la única clase del vocabulario congelado (`docs/data-contract-v1.md`, §7.1) que no puede
salir del `sjoin` de §1: OSM publica la costa como **línea** y una línea no contiene centroides, así
que ninguna celda de mar quedaría etiquetada por contención. El mar se **deriva** de la línea de
costa; no se declara en la configuración del caso.

### Por qué el mar no es un lado declarado

La tentación —y fue la primera versión— es declarar un entero por caso (`+1` / `-1`) que diga de qué
lado de la costa está el agua. Falla por geometría: en una bahía la línea de costa corre en las dos
direcciones, así que **un solo signo global no separa tierra de mar**; en cuanto la costa dobla, la
mitad equivocada se etiqueta `water` y las celdas de mar del lado contrario se quedan `unknown` y
**sí** publican desplazamiento. A eso se suma que un mar sin geometría no se puede excluir. Por eso
la etiqueta se decide contra un polígono y el gate hace lo mismo: probar un signo contra la costa
pasaría de forma vacía.

Del lado declarado no queda ruta de etiquetado: `Coast.sea_side` sigue en la dataclass porque el
fallback sintético lo rellena, `coast_from_features` lo deja en `0` para toda costa real y ninguna
función lo lee. La variante firmada de `Coast.distance` se retiró a propósito: hoy es **distancia sin
signo** en km a la polilínea más cercana, que es lo único que el modelo de coherencia necesita (el
gradiente de decorrelación costera).

### Cómo se decide: `sea_polygons`

`pipeline/context.py::sea_polygons(features, bbox, land_use_share_max=0.05)`:

1. `coast_lines` reúne las polilíneas `water/coastline` del extracto. Una costa que se cierra sobre
   sí misma (el contorno de una isla) llega como `Polygon` y se toma por su anillo exterior.
2. `shapely.ops.polygonize` sobre la unión de esas líneas **con el anillo del bbox del AOI** produce
   áreas cerradas candidatas a los dos lados de la costa. Ningún candidato recibe dirección.
3. Para cada candidato, `share` = área cubierta por los polígonos **areales** de `land_use` del
   extracto (`Polygon` / `MultiPolygon`; puntos y líneas no asignan, §1) dividida por el área del
   candidato. Es mar si `share <= land_use_share_max`.
4. Devuelve la unión de los candidatos que pasan, o `None` si no hay costa, no hay bbox o ningún
   candidato pasa.

Dos consecuencias del paso 3, escritas porque no son obvias: si el extracto no trae **ningún**
polígono de `land_use`, la cuota se toma como `0.0` y todos los candidatos pasan; y la cuota mide lo
que está mapeado, no lo que hay en el suelo —un candidato de tierra sin dibujar pasa por mar—, que es
el mismo límite de §3 aplicado al mar.

### De la costa a la celda: `label_cells`

Las etiquetas salen del pin si existe, y del `sjoin` de centroides contra el extracto si no (§7).
Después, y sólo después, toda celda cuyo centroide cae dentro del polígono de mar pasa a `water`,
**incluidas las que el pin no nombra** y que por defecto serían `unknown`: dentro del polígono, el
mar gana. El predicado es `contains` y no `covers`, así que un centroide exactamente sobre el borde
no cuenta como mar; el gate usa el mismo predicado, de modo que etiqueta y comprobación no pueden
discrepar sobre una celda.

### El mar no lleva desplazamiento

Una celda con `land_use` = `water` es una superficie especular sin fase utilizable, así que
`pipeline/mock/model.py`
lleva la coherencia de esas celdas por debajo de `SENSOR_PROFILES["mock"].coherence_min` (0.35: entre
el 35 % y el 90 % de ese umbral, según la celda) y el grid las descarta como datos ausentes.
Medido ahora sobre los tres artefactos publicados: **0 celdas `water`** en `nisar_grid.parquet` en los
tres casos, y **0 celdas publicadas dentro** del polígono derivado en los dos casos que lo tienen.
La costa sigue publicándose como capa, así que el mapa dibuja el litoral sin publicar «océano con
subsidencia»: 12 features `water/coastline` en Yakarta, 6 en Málaga, 0 en Ciudad de México. El
polígono de mar **no** se publica: no hay clase congelada para él (`docs/data-contract-v1.md`, §7.4)
y añadirla rompería el vocabulario.

### Dónde vive el polígono: en el pin

`pipeline/ingest/pin_context.py::build_pin` lo deriva al construir el pin y lo guarda bajo `sea` en
`config/context/<case_id>.json`, redondeado y simplificado con la misma tolerancia del pin (§7). No
se puede derivar al leer, y no es por el tope: el pin guarda las features de agua más grandes por
tipo de geometría (`FEATURE_CAP_PER_KIND` = 150) y, sobre todo, **no guarda los polígonos de
`land_use`**, sólo la clase por celda, así que la cuota del paso 3 no se puede recalcular. Por eso el
polígono viaja con el pin. `pinned_context` lo recupera con `_pinned_sea`; un pin escrito antes de
que la clave existiera, o con una geometría ilegible, devuelve `None` en lugar de abortar el caso.

### El gate

`pipeline/mock/checks.py` falla el caso en dos situaciones y sólo en dos:

| Condición | Por qué falla |
|---|---|
| el contexto trae features `water/coastline` y `context.sea` es `None` | la etiqueta de `water` no puede excluir el mar, así que el artefacto publicaría desplazamiento sobre él. Ocurrió de verdad: el tope por área borró la costa (§7) y la regla se apagó sin que ningún artefacto cambiara de forma |
| alguna celda publicada cae dentro del polígono derivado | superficie especular con desplazamiento: el error que el gate existe para impedir |

Cuando pasa, el informe lo deja escrito (`sea: the derived sea polygon excludes all N published
cells`), con N = celdas publicadas: el recuento se imprime, así que una comprobación sobre cero
celdas se ve como tal.

### Límites, dichos sin adornos

| Límite | Qué implica |
|---|---|
| `land_use_share_max = 0.05` | constante **documentada** en `pipeline/context.py`, no umbral de contrato: no vive en `SENSOR_PROFILES` ni en `contracts.py`. Subirla absorbe parques costeros; bajarla deja dársenas en `unknown` (comentario del código). Cambiarla cambia el artefacto y no exige cambio de contrato — por eso queda escrita aquí |
| caso sin línea de costa | **Ciudad de México**: 0 features `water/coastline` en su pin → `sea: null`. No hay nada que poligonizar |
| AOI cuyo bbox no incluye mar abierto | todos los candidatos quedan cubiertos por el `land_use` mapeado (cuota 1.0), así que la unión es vacía y el resultado es `None` |
| mar no derivable | las celdas conservan `unknown`; no se adivina el lado. `unknown` es una categoría publicable (§3), no un hueco, y la entrada de contexto OSM de `meta.limitations` publica esa fracción |

Y una limitación medida que conviene no descubrir por sorpresa: la derivación depende de que la costa
cierre contra el anillo del bbox. En Yakarta, medido ahora, las 14 polilíneas no cruzan el encuadre de
lado a lado: `polygonize` da 8 candidatos, el mayor cubre el 90 % del bbox y lleva un 56 % de su área
mapeada como `land_use` (tierra y mar juntos), así que se descarta por la cuota, y el polígono que
sobrevive mide `1.7e-05` °², tiene 22 vértices y contiene 2 de las 7 935 celdas del pin — las otras
219 `water` de Yakarta vienen de polígonos de agua mapeados, no del mar. En Málaga, las 34 polilíneas
del extracto dan 2 candidatos: el mayor cubre el 82 % del bbox y está cubierto al 100 % por `land_use`
mapeado, así que se descarta por la cuota; el que sobrevive contiene 842 de las 890 celdas `water`
del pin. Donde el polígono no cubre el mar, el gate sigue pasando (no hay celda publicada dentro de
él) pero la exclusión no está haciendo trabajo: la cobertura del mar es tan buena como la costa del
extracto.

El límite se declara en el artefacto, caso por caso, en `meta.limitations`: la entrada de contexto OSM
dice que el uso del suelo es «de facto and incomplete», nombra la fecha del extracto, da el recuento
total y el porcentaje de celdas publicadas que salen `unknown` y enumera los **dos** motivos —«either
no mapped polygon contains their centroid, or the containing polygon's tags map to no class of the
frozen land-use vocabulary»— rematando con «Both causes stay `unknown` and neither is extrapolated
from a neighbouring cell». La frase publica el total, no el reparto, y por eso ninguna de las tres
`meta.json` puede atribuir todas las `unknown` a una sola causa: una frase que sólo nombrase la falta
de polígono sería falsa en Málaga.

El reparto se mide sobre el extracto, que está gitignored (`data/raw/<case>/context/`), uniendo las
celdas publicadas con `osm_context.load_extract` + `enrich_context.join_land_use`; no es un campo
publicado y no se cita como si lo fuese. Medido así, motivo 1 (sin polígono) / motivo 2 (dentro de un
polígono cuyo tag no mapea): Málaga 0 / 1 080 de 1 080 `unknown`, Yakarta 2 288 / 146 de 2 434,
Ciudad de México 7 792 / 105 de 7 897. Málaga es el extremo del motivo 2 —las 1 080 están dentro de un
polígono cuyo tag no tiene regla (§3)—, y es justo el caso que una lectura de una sola causa
convertiría en cifra falsa. No hay una limitación aparte para el mar: lo que la app no debe prometer
es una cobertura que el extracto no tiene.
