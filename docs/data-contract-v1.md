# Contrato de datos v1.1 — CONGELADO

**NASA Space Apps Challenge 2026 · Dancing with the SARs · rol Ciencia SAR / GIS (Rol 3).**

| | |
|---|---|
| Versión de esquema | `1.1` |
| Revisión | `2` |
| Congelado | 2026-11-14 |
| Huella (`contract_fingerprint()`) | `9a749ceab7387326b0ec023a1b3e467bec86957a4a55c280f2df912e36762770` |
| Fuente de verdad | `src/domain/contracts.py` (código) — este documento lo describe |
| Prevalencia | si el código y este documento discrepan, **manda el código** y el documento se corrige |

Este documento desarrolla y cierra el §5 de
[`flujo_trabajo_y_responsabilidades.md`](../flujo_trabajo_y_responsabilidades.md), que sigue siendo
la definición operativa. No sustituye a ese documento: lo vuelve ejecutable.

**Regla de congelación.** A partir de ahora ningún rol cambia nombres de campo, tipos, enums,
unidades ni umbrales por su cuenta. Un cambio exige: (1) subir `SCHEMA_VERSION`/`CONTRACT_REVISION`
en `src/domain/contracts.py`, (2) una línea en `CHANGELOG` dentro del mismo módulo, (3) una entrada
en el PR con `Impacto sobre el contrato:`, (4) revisión de los tres roles (§8 del flujo). El mock y
los datos reales cumplen **exactamente** el mismo esquema.

---

## 1. Artefactos cubiertos

| Fichero | Productor | Consumidor | Contrato |
|---|---|---|---|
| `data/processed/{case_id}/nisar_grid.parquet` | pipeline (Rol 3) | loaders (Rol 1) → mapa (Rol 2) | §3 |
| `data/processed/{case_id}/timeseries.csv` | pipeline | loaders → gráficos | §4 |
| `data/processed/{case_id}/meta.json` | pipeline | loaders → cabecera, unidades, badges | §5 |
| `data/processed/{case_id}/context_layers.geojson` | pipeline | loaders → capas de contexto | §6 |

Nada de `PMTiles`, servidor HTTP ni backend: eso es la migración post-MVP (§2.2 del flujo). Los
cuatro ficheros son la interfaz completa entre pipeline y aplicación.

Vocabulario mínimo de tipos: `string` → texto UTF-8; `float64` → coma flotante IEEE-754;
`nullable` → puede faltar el valor, pero el campo existe.

---

## 2. Ambigüedades del §5 del flujo y cómo se resolvieron

| # | Ambigüedad en el flujo | Decisión congelada | Motivo |
|---|---|---|---|
| 1 | `delta_change` sin unidad clara frente a `unit: mm/year` | `val_t1`, `val_t2`, `delta_change` son **mm acumulados** (`unit = "mm"`); la tasa vive en el campo nuevo `velocity_mm_yr` (`velocity_unit = "mm/year"`) | Un solo campo no puede ser cambio y velocidad. El mapa colorea la tasa; el slider compara fechas. Ambos son necesarios y con unidades distintas |
| 2 | Ejemplo de `meta.json` con `product: "opera-disp-s1"` | `product` es enum cerrado y debe ser el producto **realmente usado**; hoy es `nisar-gunw` o `mock` | Comprobado el 2026-11-14 contra ASF: OPERA DISP-S1 devuelve **0** granulos en los tres AOI (ver `data/processed/_availability/*.json`). Publicar `opera-disp-s1` sería falso |
| 3 | `sensor` como "NISAR o Sentinel-1" en texto libre | Enum `nisar` \| `sentinel-1` | La coherencia no es comparable entre sensores; el enum impide escribir `S1A` o `NISAR L2` y que la UI los trate como distintos |
| 4 | `timeseries.csv` sin clave de agregación | Columna obligatoria `cell_id`: `"aoi"` para la serie agregada del caso, índice H3 para el detalle por celda | El gráfico necesita una serie por caso; el popup necesita detalle. Ambos viven en el mismo fichero, distinguidos por una clave, sin cambiar el esquema |
| 5 | `coherence_threshold: 0.35` global | Umbrales por `(sensor, product)` en `SENSOR_PROFILES`, con `revisit_days` obligatorio en `meta.json` | NISAR 12 días ≠ Sentinel-1 6 días (informe §13.6). Un umbral global invita a mezclar y a sobreinterpretar |
| 6 | `stats: {}` sin campos definidos | `stats` con velocidad, amplitud estacional, razón `R`, régimen dominante, percentiles y `detection_limit_mm_yr` **obligatorio** cuando \|mediana\| < 5 mm/año | La interfaz debe mostrar la separación entre subsidencia secular y componente estacional; Málaga es un resultado nulo declarado |

---

## 3. `nisar_grid.parquet`

Una fila por celda H3 resolución 9 (~200 m de arista) y caso. `cell_id` es único en el fichero.

| Campo | Tipo | Obligatorio | Unidad | Descripción |
|---|---|---:|---|---|
| `cell_id` | string | sí | — | Índice H3 res 9, único |
| `lat` | float64 | sí | grados | Centroide de la celda, EPSG:4326 |
| `lon` | float64 | sí | grados | Centroide de la celda, EPSG:4326 |
| `val_t1` | float64 | sí | mm | Desplazamiento modelado en la primera fecha (positivo = hacia arriba) |
| `val_t2` | float64 | sí | mm | Desplazamiento modelado en la última fecha |
| `delta_change` | float64 | sí | mm | **`val_t2 - val_t1`** exacto (negativo = subsidencia) |
| `velocity_mm_yr` | float64 | sí | mm/year | Tendencia lineal ajustada (campo congelado nuevo) |
| `seasonal_amp_mm` | float64 | sí | mm | Amplitud estacional ajustada |
| `ratio_R` | float64 | sí | — | `abs(velocity_mm_yr) / seasonal_amp_mm` |
| `regime` | string | sí | — | `inelastic`, `elastic` o `transitional` |
| `coherence` | float64 | sí | — | Media temporal corregida por sesgo, en [0, 1] |
| `land_use` | string | sí | — | Enum §7.1 |
| `risk_level` | string | sí | — | Enum §7.2, derivado por `risk_level_from_velocity` |
| `case_id` | string | sí | — | Enum `mexico-city` \| `jakarta` \| `malaga`; **un solo valor por fichero** |
| `sensor` | string | sí | — | `nisar` \| `sentinel-1` |
| `product` | string | sí | — | Enum §7.3 |
| `unit` | string | sí | — | Siempre `"mm"` (unidad de las tres columnas acumuladas) |
| `velocity_unit` | string | sí | — | Siempre `"mm/year"` |
| `source` | string | sí | — | Proveedor legible, p. ej. `"ASF DAAC"` |
| `acquisition_date` | string | no | — | ISO `YYYY-MM-DD` de la última adquisición cuando aporte información |
| `provenance_id` | string | sí | — | Formato `{case_id}+{product}+{YYYYMMDD}+{sha256_8}`; debe resolver en `meta.provenance` |

Reglas validadas por `validate_grid()`:

- no se admiten columnas no declaradas (si hacen falta, se sube la versión del esquema);
- nulos prohibidos en las columnas obligatorias: una celda sin dato **no se escribe**, no se
  escribe como 0 (política de nulos, §8);
- `coherence` fuera de [0, 1] es error;
- `delta_change != val_t2 - val_t1` (tolerancia 1e-6) es error;
- `cell_id` duplicado es error.

Ejemplo de fila:

```json
{
  "cell_id": "8928308280fffff", "lat": 19.4326, "lon": -99.1332,
  "val_t1": 0.0, "val_t2": -7.4, "delta_change": -7.4, "velocity_mm_yr": -128.0,
  "seasonal_amp_mm": 9.4, "ratio_R": 13.617, "regime": "inelastic",
  "coherence": 0.82, "land_use": "urban-dense", "risk_level": "critico",
  "case_id": "mexico-city", "sensor": "nisar", "product": "nisar-gunw",
  "unit": "mm", "velocity_unit": "mm/year", "source": "ASF DAAC",
  "acquisition_date": "2026-07-02",
  "provenance_id": "mexico-city+nisar-gunw+20261114+abc12345"
}
```

Presupuesto: una ciudad de 50 × 50 km con ~15 000 celdas ocupa < 2 MB en Parquet (compresión
zstd). `data/processed/` completo debe quedar por debajo de 15 MB (presupuesto de tamaño, `README.md` §8).

---

## 4. `timeseries.csv`

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---:|---|
| `case_id` | string | sí | Igual que en el grid |
| `cell_id` | string | sí | `"aoi"` = agregado del caso; índice H3 = detalle por celda |
| `date` | string | sí | ISO `YYYY-MM-DD`, miembro de `meta.dates` |
| `value` | float64 | sí | Desplazamiento acumulado, mm, positivo hacia arriba |
| `trend_mm_yr` | float64 | sí | Tendencia secular calculada offline, mm/year |
| `seasonal_mm` | float64 | sí | Componente estacional calculada offline, mm |
| `unit` | string | sí | `"mm"` |
| `sensor` | string | sí | `nisar` \| `sentinel-1` |
| `product` | string | sí | Enum §7.3 |
| `quality` | string | sí | `good` \| `fair` \| `poor` |
| `provenance_id` | string | sí | Mismo formato que en el grid |

Reglas:

- las filas `cell_id == "aoi"` son **obligatorias** y alimentan el gráfico principal: una por fecha
  y caso (12 filas por ciudad);
- las filas por celda son **opcionales** y solo se generan si el fichero completo sigue dentro del
  presupuesto de 15 MB (12 fechas × ~15 000 celdas supera ese límite en CSV: por defecto **no** se
  emiten; si se emiten, se limitan a las celdas del top-N por \|velocidad\| o a una resolución H3
  menor, y se documenta el criterio en `meta.stats`);
- `date` nunca fuera de `meta.dates`; `value` nunca se interpola ni se rellena;
- huecos: si falta una fecha, falta la fila. No hay valor centinela.

---

## 5. `meta.json`

```jsonc
{
  "schema_version": "1.1",
  "case_id": "mexico-city",
  "name": "Mexico City, MX",
  "bbox": [-99.35, 19.20, -98.85, 19.65],       // [W, S, E, N] en EPSG:4326
  "crs": "EPSG:4326",                            // CRS de publicación de los artefactos
  "reference_point": [-99.13, 19.43],            // píxel de referencia del InSAR
  "dates": ["2026-07-02", "2026-07-14"],         // ISO, ascendente, sin duplicados
  "revisit_days": 12,                            // del perfil del producto; nunca se mezcla
  "sensor": "nisar",
  "product": "nisar-gunw",
  "thresholds": {
    "coherence_min": 0.35,
    "k_sigma": 2.5,
    "min_ddB": 3.0,
    "persistence": "2of3",
    "min_area_m2": 500
  },
  "provenance": {
    "source": "ASF DAAC",
    "product_id": "NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH_...",
    "processing_date": "2026-11-14",
    "method": "LOS→vertical (v_north≈0), media por celda H3 res 9, referencia en el píxel indicado",
    "retrieved_utc": "2026-11-14T16:02:00Z",
    "reference": "Herrera-García et al. 2021, doi:10.1126/science.abb8549",
    "license": "NASA open data"
  },
  "stats": {
    "n_cells": 15482,
    "n_dates": 12,
    "median_velocity_mm_yr": -128.0,
    "median_seasonal_amp_mm": 9.4,
    "median_ratio_R": 13.617,
    "dominant_regime": "inelastic",
    "p05_velocity_mm_yr": -410.0,
    "p95_velocity_mm_yr": 12.0,
    "detection_limit_mm_yr": 1.5
  },
  "limitations": [
    "GUNW tiene posting de 80 m: escala de ciudad, no de edificio.",
    "NISAR 12 días ≠ Sentinel-1 6 días: la coherencia no se compara entre sensores."
  ],
  "null_policy": "Las celdas sin dato no se escriben; nunca se representan como 0."
}
```

Claves obligatorias: `schema_version`, `case_id`, `name`, `bbox`, `crs`, `reference_point`, `dates`,
`revisit_days`, `sensor`, `product`, `thresholds`, `provenance`, `stats`, `limitations`,
`null_policy`.

Dentro de `provenance` son obligatorias: `source`, `product_id`, `processing_date`, `method`,
`retrieved_utc`, `reference`, `license`. `method` no puede estar vacío: debe describir la
conversión física aplicada (p. ej. «LOS→vertical con v_north≈0»), porque es lo que un evaluador
lee para decidir si el número es creíble.

Reglas validadas por `validate_meta()`:

- `schema_version` debe ser exactamente `"1.1"`, `case_id` debe estar en el enum;
- `crs` de publicación es `EPSG:4326` (la reproyección interna a `EPSG:326xx` es asunto del
  pipeline, no del contrato);
- `thresholds.coherence_min` y `revisit_days` deben coincidir con el perfil congelado de
  `(sensor, product)`: si no coinciden, el artefacto está mal, no el perfil;
- `provenance.method` no puede estar vacío y `product == "mock"` exige que `source` diga `mock`;
- `stats` completo; si `|median_velocity_mm_yr| < 5`, `detection_limit_mm_yr` es obligatorio;
- `limitations` no puede estar vacío: es un requisito de honestidad, no decoración.

`detection_limit_mm_yr` es la cota superior de detección estimada para ese caso y sensor. En Málaga
el resultado nulo **se publica con su cota**; no se omite el campo.

---

## 6. `context_layers.geojson`

`FeatureCollection` en **EPSG:4326**, con el CRS declarado **en cada feature** (el consumidor no
adivina). Geometrías admitidas: `Point`, `LineString`, `Polygon`, `MultiPolygon`.

Propiedades obligatorias por feature:

| Propiedad | Tipo | Descripción |
|---|---|---|
| `layer` | string | Enum: `land_use`, `infrastructure`, `water`, `admin_boundary` |
| `class` | string | Subcategoría dentro de la capa, vocabulario cerrado §7.4 |
| `name` | string (opcional) | Nombre legible para tooltips |
| `case_id` | string | Caso al que pertenece |
| `crs` | string | Siempre `"EPSG:4326"` |
| `source` | string | Proveedor (GHSL, OSM, SACMEX, …) |
| `provenance_id` | string | Mismo formato que en el grid |

Ejemplo:

```json
{
  "type": "Feature",
  "geometry": { "type": "Point", "coordinates": [-99.13, 19.43] },
  "properties": {
    "layer": "infrastructure", "class": "water-well", "name": "Pozo Roma",
    "case_id": "mexico-city", "crs": "EPSG:4326", "source": "SACMEX",
    "provenance_id": "mexico-city+nisar-gunw+20261114+abc12345"
  }
}
```

La capa `land_use` debe ser coherente con la columna `land_use` del grid: ambas salen del mismo
`geopandas.sjoin` sobre los centroides H3, no de dos procesos distintos.

---

## 7. Enumeraciones y umbrales congelados

### 7.1 `land_use`
`urban-dense`, `urban-residential`, `industrial`, `infrastructure`, `water`, `bare`, `vegetation`,
`unknown`.

### 7.2 `risk_level` — `risk_level_from_velocity(velocity_mm_yr)`

| \|velocidad\| (mm/año) | `risk_level` |
|---|---|
| < 5 | `estable` |
| 5 – 25 | `moderado` |
| ≥ 25 | `critico` |

El signo se ignora: la subsidencia y la elevación de la misma magnitud son igual de relevantes para
la infraestructura. `NaN` se mapea a `estable` solo para no romper la UI; un `NaN` publicable es un
error del pipeline, no un caso válido.

### 7.3 `product` y perfiles por sensor

| `product` | `sensor` | `revisit_days` | `coherence_min` | `k_sigma` | `min_ddB` | `persistence` | `min_area_m2` | `posting_m` |
|---|---|---:|---:|---:|---:|---|---:|---:|
| `nisar-gunw` | `nisar` | 12 | 0.35 | 2.5 | 3.0 | 2of3 | 500 | 80 |
| `nisar-gcov` | `nisar` | 12 | 0.25 | 3.0 | 2.0 | 2of3 | 500 | 30 |
| `opera-disp-s1` | `sentinel-1` | 12 | 0.30 | 2.5 | 3.0 | 2of3 | 500 | 30 |
| `hyp3-s1` | `sentinel-1` | 6 | 0.40 | 2.5 | 3.0 | 2of3 | 500 | 20 |
| `egms` | `sentinel-1` | 6 | 0.30 | 2.5 | 3.0 | 2of3 | 500 | 50 |
| `mock` | `nisar` | 12 | 0.35 | 2.5 | 3.0 | 2of3 | 500 | 80 |

`sensor_profile(sensor, product)` lanza `ContractError` si la pareja no existe o no coincide. No hay
manera de escribir "umbral único": el perfil es la única puerta.

**Estado real de las fuentes** (sondas en `data/processed/_availability/*.json`, 2026-11-14):

| Fuente | CDMX | Jakarta | Málaga |
|---|---|---|---|
| NISAR GUNW (ASF) | 69 gránulos, 2025-10 → 2026-10 | 25 gránulos, 2026-06 → 2026-10 | 26 gránulos, 2026-06 → 2026-10 |
| OPERA DISP-S1 (ASF) | **0** | **0** | **0** |
| Sentinel-1 SLC (5 años) | 250+ gránulos, ~1.07 TB | 250+ gránulos, ~1.18 TB | 250+ gránulos, ~0.96 TB |
| EGMS | no aplica | no aplica | descarga manual en portal |

Consecuencia: el `product` real de la primera integración es `nisar-gunw` (con la advertencia de
cobertura urbana) o `mock`. **Nunca** `opera-disp-s1` para estos tres AOI.

### 7.4 `context_layers.geojson` → `class`

| `layer` | Valores permitidos |
|---|---|
| `land_use` | los mismos que §7.1 |
| `infrastructure` | `water-well`, `water-treatment`, `metro-station`, `pipeline`, `levee`, `power` |
| `water` | `reservoir`, `river`, `canal`, `aquifer`, `coastline` |
| `admin_boundary` | `municipality`, `district`, `aoi` |

---

## 8. Políticas transversales

**Nulos.** No hay valor centinela. Una celda sin dato no aparece en el grid; una fecha sin dato no
aparece en la serie. `meta.null_policy` lo declara por escrito para que nadie "arregle" el fichero
rellenando ceros (en Málaga el cero es un valor legítimo y confundirlo con nodata destruye el
mensaje científico).

**Trazabilidad.** `provenance_id = {case_id}+{product}+{YYYYMMDD}+{sha256_8}`, construido con
`provenance_id()`; el mismo identificador aparece en el grid, en la serie, en el contexto y en
`meta.provenance`. El pipeline lo deriva del hash del artefacto de entrada, no de un contador.

**Unidades.** Siempre declaradas en el propio fichero. Prohibido deducirlas del nombre de columna.

**Fechas.** ISO `YYYY-MM-DD` en UTC; `meta.dates` es la lista canónica y todo `date` de la serie
pertenece a ella.

**Sensores.** Se identifican siempre. Mezclar sensores exige ficheros separados (un `case_id` y un
`product` por fichero) y una advertencia visible en la UI.

---

## 9. Validación

El contrato se valida con código, no con revisión manual:

```bash
# 1. autocomprobación del contrato (positivos + negativos, sin ficheros)
./scripts/python.sh -m src.domain.contracts

# 2. suite de regresión que detecta cualquier deriva silenciosa del esquema
./scripts/python.sh -m pytest tests/test_contracts.py -q

# 3. validación de los artefactos reales (paso 2 del plan, `pipeline/validate_artifacts.py`)
./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed
```

`validate_all(grid=…, timeseries=…, meta=…, context=…)` devuelve un diccionario
`{artefacto: [problemas]}`; lista vacía = conforme. Los tres comandos deben salir con código 0 antes
de abrir un PR que toque artefactos o esquema.

Las funciones viven en `src/domain/contracts.py` y **no** importan Streamlit, geopandas ni pydeck:
el pipeline, los loaders y CI comparten exactamente el mismo código de validación.

---

## 10. Compatibilidad con el flujo y el README

| Documento / flujo | Estado |
|---|---|
| `flujo §5.1` (`nisar_grid.parquet`) | se mantienen todos los campos; se **añaden** `velocity_mm_yr` y `velocity_unit` (§2.1) |
| `flujo §5.2` (`timeseries.csv`) | se mantienen los 8 campos; se **añade** `cell_id` (§2.4) |
| `flujo §5.3` (`meta.json`) | se mantienen las claves del ejemplo; se **añaden** `name`, `bbox`, `reference_point`, `revisit_days`, `thresholds{}`, `limitations`, `null_policy` y tres claves de `provenance` |
| `flujo §3.1` (rutas) | `src/domain/contracts.py` es el fichero previsto; no se crean rutas nuevas |
| `README.md` §8 (`data/processed/`) | mismos cuatro ficheros, mismo sitio, < 15 MB |
| `docs/route-contract.md` (PMTiles, Opción B) | fuera del MVP (§2.2 del flujo); queda como ruta de migración post-MVP |

---

## 11. Decisiones abiertas (no son contrato)

Estas no bloquean la congelación y se cierran en `config/study_cases.yaml`, no aquí:

1. **Caso principal.** El flujo §2.1 deja la elección abierta; `config/study_cases.yaml` la resuelve:
   `mexico-city` como principal (señal fuerte, mejor demo) y `malaga` como control negativo explícito.
2. **AOI exacto.** El contrato fija el *esquema*, no el polígono: `bbox` es la envolvente del AOI
   real, que puede ser un polígono en config.
3. **Ventana temporal.** `dates` sale de las adquisiciones realmente descargadas.
4. **Emisión de series por celda.** Depende del presupuesto de tamaño (§4).
