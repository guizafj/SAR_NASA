# Demo fallback — grabar los 90 segundos sin la app

Solo si la app Streamlit no es ejecutable a la hora de grabar: nada de lo que sigue necesita más que este repositorio. Los tres casos llevan `product == "mock"`, así que §7 aplica siempre.

La geografía de contexto ya no se inventa. `land_use`, `water` e `infrastructure` salen de un extracto real de OpenStreetMap, y lo que viaja en el repositorio es el **pin** de cada caso (`config/context/<case>.json`): las decisiones que el extracto implica, en ~1 MiB de JSON legible. El extracto crudo está en `data/raw/`, gitignored. Un clon sin red y sin extracto sigue produciendo los mismos bytes que el pin.

## 1. Qué hay commiteado, y qué no

Por caso, los cuatro artefactos de `data/processed/<case>/` (`nisar_grid.parquet`, `timeseries.csv`, `context_layers.geojson`, `meta.json`) y un pin. Los números de esta tabla se leen de ahí, no se retienen de memoria:

| caso | celdas publicadas | celdas etiquetadas en el pin | pin | tiles 200 / elementos | celdas `unknown` publicadas |
|---|---:|---:|---:|---:|---:|
| `mexico-city` | 11 600 | 17 618 | 1.4 MiB | 12 / 49 784 | 7 897 (68.1 %) |
| `jakarta` | 4 936 | 7 935 | 1.1 MiB | 6 / 72 461 | 2 434 (49.3 %) |
| `malaga` | 2 758 | 4 825 | 0.8 MiB | 10 / 76 422 | 1 080 (39.2 %) |

`tiles_failed` es 0 en los tres casos: la recuperación está completa según su propio registro (`record.tiles_ok`, `record.tiles_failed`, `record.timestamp_osm_base` en el pin). El `source` de cada feature nombra OpenStreetMap y la ODbL 1.0.

Sin la app quedan además tres activos que ya existen:

- `docs/figures/velocity_shared_scale.png` — regenerar con `./scripts/python.sh scripts/make_figures.py`; tres paneles en una leyenda simétrica compartida de ±425 mm/yr, Málaga casi en blanco al lado de Mexico City (README §1).
- `docs/figures/timeseries_three_cities.png` — misma orden; las 62 adquisiciones (2024-11-10 → 2026-11-12, agregado `aoi`) con el suelo de detección de Málaga (±1.5 mm/yr, ±3.0 mm en esta ventana de 2 años) como banda.
- `scripts/demo_numbers.py` — `./scripts/python.sh scripts/demo_numbers.py`; la tarjeta con cada número que se dice en voz alta, su `provenance_id`, las limitaciones, el DO NOT CLAIM y el esqueleto de 90 segundos.

## 2. Qué rama produjo lo que estás enseñando

El contexto de cada caso lo decide un solo lector, en este orden: **pin commiteado** → **extracto recién mapeado** (`data/raw/<case>/context/`) → **fallback sintético declarado**. `allow_synthetic=False` convierte el último paso en un error en vez de un fallback silencioso, que es lo que se quiere en un gate y no en una demo.

Dos comprobaciones, una al regenerar y otra sobre el artefacto que ya está en disco:

- **La línea de log del generador** nombra la rama entre corchetes. Así se ve con el pin de Málaga:

  ```
  [malaga] 2758 cells, 62 dates (2024-11-10..2026-11-12), provenance malaga+mock+20261114+5cabe2c2
      land_use: 41x bare, 94x industrial, 13x infrastructure, 1080x unknown, 125x urban-dense, 222x urban-residential, 1183x vegetation [OpenStreetMap via malaga.json, OSM data 2026-11-15T09:47:19Z, 300 water, 252 infrastructure feature(s)]
  ```

  Las tres variantes del corchete son `OpenStreetMap via <case>.json` (pin), `OpenStreetMap extract, OSM data …` (extracto fresco) y `declared synthetic fallback (no cached extract and no pinned context)`.
- **El `source` de una feature de contexto**, que es lo que viaja en el artefacto que estás enseñando. Con pin o extracto: `OpenStreetMap contributors (ODbL 1.0), Overpass API extract`. Con fallback: `mock (synthetic land-cover rule)`, `mock (synthetic hydrology)` o `mock (synthetic utility POI)` en todas las features salvo la del AOI.

Excepción a tener presente, porque no es ni extracto ni invención: la feature `admin_boundary / aoi` sale de `config/aoi/<case>.geojson` y su `source` dice que es el límite de ciudad de OSM usado como extensión de análisis, no una frontera legal. En el fallback también sigue siendo real, así que una feature con `source` no sintético no demuestra por sí sola que el resto lo sea.

`meta.limitations` cierra la misma pregunta con dos frases mutuamente excluyentes: la de OSM da el total de celdas publicadas que salen `unknown` y nombra los **dos** motivos —ningún polígono mapeado contiene el centroide, o el que lo contiene tiene tags que no mapean a ninguna clase del vocabulario—, sin atribuir todas a uno solo; la sintética nombra qué geometría está inventada. El reparto entre los dos motivos no está en `meta.json`: se mide sobre el extracto gitignored y vive en `docs/context-sources.md` §3. Y el `provenance_id` **no** distingue la rama: sale del caso, del producto, de la ventana y de la semilla, no de la geografía.

## 3. Si la rama es el fallback sintético

Entonces hay que decirlo, y decirlo primero. El aviso permanente **DEMONSTRATION DATA** no es opcional con `product == "mock"`, y a él se suma la frase de `meta.limitations` que nombra la geografía inventada (línea de costa, lago, río, POIs). Se lee del fichero, verbatim, no de memoria.

No es cosmético: cambia lo que se publica. Con pin, el generador etiqueta desde OSM y filtra por coherencia modelada; sin pin ni extracto vuelve la regla elíptica del mock antiguo — anillos alrededor de la elipse de cobertura, corredor recto y agua inventada — que asigna una clase a toda celda y por eso no deja ninguna `unknown`. Eso mueve la coherencia modelada, que es la que decide qué celdas sobreviven al filtro.

| caso | celdas publicadas (pin → fallback) | `unknown` (pin → fallback) | mediana de coherencia (pin → fallback) |
|---|---|---|---|
| `mexico-city` | 11 600 → 11 694 | 7 897 → 0 | 0.786 → 0.826 |
| `jakarta` | 4 936 → 4 846 | 2 434 → 0 | 0.794 → 0.800 |
| `malaga` | 2 758 → 2 675 | 1 080 → 0 | 0.738 → 0.772 |

En esta rama, ninguna frase de esa pantalla dice "OpenStreetMap", y el mapa no se presenta como geografía de la ciudad.

## 4. Alternativa interactiva: el GeoJSON, sin app

Un polígono de celda H3 por fila, EPSG:4326, con `velocity_mm_yr`, `coherence`, `land_use` y `risk_level`. `land_use` es la clase mapeada desde OSM; `unknown` es una clase publicada por dos motivos —ningún polígono mapeado contiene el centroide de esa celda, o sí lo contiene pero sus tags no mapean a ninguna clase del vocabulario (en Málaga las 1 080 publicadas son de este segundo tipo y ninguna está fuera de polígono)—, no un valor que falte.

```
./scripts/python.sh scripts/export_grid_geojson.py --out data/staging/demo
```

**kepler.gl** (navegador, red, sin instalar): Add Data ▸ File ▸ soltar un `.geojson`; Polygon layer ▸ Color Based On `velocity_mm_yr` ▸ Color Scale **Quantize** ▸ dominio **−425 … 425** ▸ rampa divergente de 9 pasos, blanco en 0 ▸ celdas rellenas (sin radio: la geometría es el anillo H3).
Escribir los dos límites a mano; nunca aceptar el dominio automático.

**QGIS** (offline, necesita QGIS): Add Vector Layer ▸ Symbology ▸ Graduated ▸ Value `velocity_mm_yr` ▸ Mode **Equal Interval** ▸ Min **−425**, Max **425** ▸ 10 clases ▸ RdBu invertida ▸ **relleno** de símbolo único sobre el polígono de la celda (sin tamaño de marcador);
copiar/pegar el estilo sobre las otras dos capas para que las tres compartan una leyenda.

**Por qué fijo:** ±425 = `ceil(max|velocity| 415.14 / 25) * 25` para todos los casos (`CONTRAST`). Auto estira cada capa a su propio rango, así que el spread de Málaga (4.17 mm/yr) llena la rampa igual que el de Mexico City (208.62) y el contraste de 50.1× desaparece;
recortar en p05 (−232.16) esconde el núcleo de −415.14; y los colores automáticos se mueven al hacer zoom mientras el dato no.

## 5. Beat → activo → frase

Decir las palabras del guion (`docs/video-script.md`); los manejadores solo identifican cada beat.

| Beat | En pantalla | Decir |
|---|---|---|
| 0:00–0:20 | figura de velocidad, recortando a Mexico City y luego Málaga | guion verbatim: "One question", "Same pump, different ground." |
| 0:20–0:30 | línea de tarjeta `!! product == 'mock'` | verbatim: "every number is synthetic" — esta línea es el badge |
| 0:30–0:44 | panel de Mexico City + `CASE mexico-city` | verbatim: "Open on Mexico City" |
| 0:44–1:00 | figura de series, los 62 marcadores | **sustituir**: "62 acquisitions, aggregate; the map carries first and last snapshot only." |
| 1:00–1:14 | tooltip de kepler / Identify de QGIS sobre un punto | **sustituir**: "Velocity, coherence, land use, risk for one cell — no series." |
| 1:14–1:30 | leyenda compartida de la figura de velocidad | **sustituir**: "This is already the shared scale, plus or minus 425." |
| 1:30–1:40 | figura de series, banda verde de detección | verbatim: "Blank is not missing data." |
| 1:40–1:50 | línea de tarjeta `provenance_id=mexico-city+mock+…` | **sustituir**: "No card; this provenance line is the evidence." |
| 1:50–2:20 | README §3, luego `meta.limitations` | guion verbatim; no necesita app |

## 6. Lo que esto no puede mostrar — y qué decir en su lugar

- **Interrogar una celda, serie por celda**: `stats.per_cell_series.enabled` es false; decir "the series is the city aggregate today; per-cell is opt-in".
- **El slider de fechas**: el contrato lleva `val_t1`/`val_t2`, no una rejilla por fecha; decir "the map is first and last date; the series carries all 62".
- **Cualquier cosa que necesite la URL desplegada**: decir "not deployed; the recording is the frozen artefacts".

## 7. Línea de honestidad

`docs/handoff-mock.md` §3 punto 3: "`product` is `mock` and `provenance.source` says so. Render the badge."
Y §4: las `limitations[]` se dicen verbatim. Ahora son **dos** frases obligatorias, no una: la de MOCK ("**MOCK: every number is synthesised by pipeline/generate_mock.py; validate the map and the schema, never the science.**") y la de contexto, que nombra la rama — cuántas celdas quedan `unknown` y sus dos motivos, o qué geografía es inventada. Las dos están en `meta.limitations` de cada caso: se leen, no se recuerdan.

## 8. Regenerar o re-pinear, en orden

El orden no es negociable: el pin se construye desde el extracto, y los artefactos se construyen desde el pin.

1. **Tiles** (una vez, con red; el único paso que la necesita):

   ```
   ./scripts/python.sh pipeline/ingest/fetch_context.py --dry-run
   ./scripts/python.sh pipeline/ingest/fetch_context.py --case malaga
   ```

   Un tile por petición, como máximo 0.15° por lado, 8 s entre tiles y 30 s de backoff ante 429/502/504 en una escalera de tres endpoints. `--dry-run` imprime la rejilla de tiles y no envía nada. `data/raw/` no se commitea.

2. **Pin**, sin red y sin reloj:

   ```
   ./scripts/python.sh -m pipeline.ingest.pin_context --case malaga
   ```

   Falla nombrando el `manifest.json` que falta en vez de escribir un pin vacío. Con el pin commiteado, regenerar es byte-idéntico; reconstruir el pin desde un extracto propio da las mismas features y geometría, pero `context_layers.geojson` puede listarlas en otro orden — solo la primera garantía se promete a quien consume.

3. **Artefactos**:

   ```
   ./scripts/python.sh pipeline/generate_mock.py
   ```

4. **Gate de artefactos**: `./scripts/check_phase_a.sh` (contrato → suites → gate sobre `data/processed/` → tabla de handoff). Verde o no hay entrega.

**Qué se rompe si falta el pin:** nada falla, pero la rama cambia: `load_context` prueba pin → extracto recién mapeado (`data/raw/<case>/context/`) → fallback sintético, así que un clon con el extracto presente publica la unión fresca contra OSM, con las etiquetas recalculadas desde el extracto y no las del pin, y sólo sin extracto se cae al fallback sintético de §3, con las cifras de esa tabla. En las dos ramas cambian las etiquetas de `land_use`, cambia con ellas la coherencia modelada y cambia el número de celdas publicadas, en silencio salvo por la línea de log y el `source`; el artefacto deja de ser el que el pin commiteado describe. Por eso la rama se confirma antes de enseñar la primera cifra (§2) y no después.

**Por qué un pin no se edita a mano:** al leerlo se verifica su `pin_sha256`; un fichero tocado a mano lo falla y el lector lanza `ValueError` nombrando `python -m pipeline.ingest.pin_context` como el único productor válido. Un pin retocado publicaría etiquetas que nadie derivó, y el sitio de descubrirlo no es el gate.
