# Contrato de datos v1.1 — CONGELADO

**NASA Space Apps Challenge 2026 · Dancing with the SARs · rol Ciencia SAR / GIS (Rol 3).**

| | |
|---|---|
| Versión de esquema | `1.1` |
| Revisión | `2` |
| Congelado | 2026-11-14 (`CONTRACT_FROZEN_UTC` no cambia con la revisión 2) |
| Huella (`contract_fingerprint()`) | `ed92be802ff8846fe369058d9a80c9545c2a694a17896de6a5f1844146e302ae` |
| Fuente de verdad | `src/domain/contracts.py` (código) — este documento lo describe |
| Prevalencia | si el código y este documento discrepan, **manda el código** y el documento se corrige |
| Qué cambió en 1.1 | §12 «Qué cambia en 1.1»: campos añadidos, puerta de identificabilidad e historial 1.0 |

Este documento desarrolla y cierra el §5 de
[`flujo_trabajo_y_responsabilidades.md`](../flujo_trabajo_y_responsabilidades.md), que sigue siendo
la definición operativa. No sustituye a ese documento: lo vuelve ejecutable.

El fichero conserva el nombre `data-contract-v1.md` para no romper enlaces, aunque su contenido sea
la revisión 1.1: las secciones §1–§11 son el texto de 1.0 ampliado en su sitio y §12 registra el
diff, de modo que el documento sirve de pista de auditoría y no de foto de la última versión.

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
| 5 | `coherence_threshold: 0.35` global | Umbrales por `(sensor, product)` en `SENSOR_PROFILES`, con `revisit_days` obligatorio en `meta.json` | NISAR 12 días ≠ Sentinel-1 6 días (perfiles en §7.3). Un umbral global invita a mezclar y a sobreinterpretar |
| 6 | `stats: {}` sin campos definidos | `stats` con `n_cells`, `n_dates`, `median_velocity_mm_yr`, `p05_velocity_mm_yr`, `p95_velocity_mm_yr`, y `detection_limit_mm_yr` **obligatorio** cuando \|mediana\| < 5 mm/año; 1.1 **añade** `median_seasonal_amp_mm`, `median_ratio_R` y `dominant_regime` (§12.1) | Málaga es un resultado nulo declarado: la cota de detección **es** el resultado |

---

## 3. `nisar_grid.parquet`

Una fila por celda H3 resolución 9 (~200 m de arista) y caso. `cell_id` es único en el fichero.
Desde 1.1 el fichero tiene **21 columnas**: las 18 de 1.0 más `seasonal_amp_mm`, `ratio_R` y
`regime` (§12.1), todas obligatorias.

| Campo | Tipo | Obligatorio | Unidad | Descripción |
|---|---|---:|---|---|
| `cell_id` | string | sí | — | Índice H3 res 9, único |
| `lat` | float64 | sí | grados | Centroide de la celda, EPSG:4326 |
| `lon` | float64 | sí | grados | Centroide de la celda, EPSG:4326 |
| `val_t1` | float64 | sí | mm | Desplazamiento modelado en la primera fecha (positivo = hacia arriba) |
| `val_t2` | float64 | sí | mm | Desplazamiento modelado en la última fecha |
| `delta_change` | float64 | sí | mm | **`val_t2 - val_t1`** exacto (negativo = subsidencia) |
| `velocity_mm_yr` | float64 | sí | mm/year | Tendencia lineal ajustada (campo congelado nuevo) |
| `seasonal_amp_mm` | float64 | sí | mm | Amplitud estacional **ajustada**, no tecleada: semiamplitud del primer armónico del mismo `harmonic_fit` de la ruta real. Debe ser ≥ 0 |
| `ratio_R` | float64 | sí | — | Índice de régimen **`ratio_R = \|velocity_mm_yr\| / seasonal_amp_mm`** (§7.5). La **columna** es obligatoria siempre; el **valor** es `null` exactamente donde no se ajustó armónico (`seasonal_amp_mm == 0`) y, donde sí se ajustó, obligatorio, finito y ≥ 0 |
| `regime` | string | sí | — | Enum §7.5: `inelastic` \| `elastic` \| `transitional`, derivado por `classify_regime` sobre la pareja anterior |
| `coherence` | float64 | sí | — | Ruta real: media temporal por pares, corregida por sesgo cuando hay looks; mock: campo espacial modelado. En [0, 1] |
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
- única excepción, y es semántica: `ratio_R` es `null` **exactamente** donde `seasonal_amp_mm == 0`,
  porque `\|tendencia\| / 0` no es un número;
- `seasonal_amp_mm == 0` significa **«no ajustado»**, nunca «no hay estacionalidad medida»:
  `transforms.harmonic_order` devuelve 0 por debajo de 0.75 año de span, el ajuste es entonces
  lineal, y el cociente es matemáticamente indefinido — se publica `null`, jamás `inf` ni un
  centinela (`1e9` se leería como «extremadamente inelástico», y `inf` rompe la regla de finitud);
- la **columna** `ratio_R` es obligatoria aunque *todos* sus valores sean indefinidos: la columna
  ausente es error, el `null` en una fila no ajustada no lo es;
- `coherence` fuera de [0, 1] es error;
- `delta_change != val_t2 - val_t1` (tolerancia 1e-6) es error;
- `cell_id` duplicado es error;
- `seasonal_amp_mm < 0` es error: una amplitud es no negativa;
- `ratio_R` con armónico ajustado (`seasonal_amp_mm > 0`) es obligatorio, finito y ≥ 0: `ratio_R < 0`
  es error (es un cociente de magnitudes) y un `null` ahí también, porque la relajación es
  estrictamente el caso «no ajustado» y no un permiso general para callar el cociente;
- `regime` fuera de `REGIMES` es error: la lista de valores permitidos no se amplía por comodidad
  (un `"recoverable"` inventado rompe la UI en silencio, así que se rechaza en el pipeline);
- `seasonal_amp_mm` no admite valores no finitos (`inf`/`NaN`), igual que `velocity_mm_yr`, y
  `ratio_R` solo admite el `null` de «no ajustado»: `inf` es error en ambos casos;
- `ratio_R` **debe coincidir** con `|velocity_mm_yr| / seasonal_amp_mm` donde la amplitud se ajustó
  (tolerancia relativa 5e-3 para absorber el redondeo): un cociente tecleado o un centinela se
  rechaza. En las tres ciudades publicadas la deriva medida es exactamente 0.0;
- `ratio_R > MAX_PUBLISHABLE_RATIO_R` (`1e9`) es error. Esa magnitud no describe un régimen: significa
  que la amplitud es indistinguible del cero numérico (una serie exactamente lineal ajusta ~1e-14 mm
  y publicaría ~1e15). Es una guarda contra el artefacto numérico, **no** un umbral científico de
  detectabilidad — el máximo real es 276.76, y una serie sin estacionalidad ajusta el suelo de ruido
  (~0.2 mm con 1 mm/época), no 0. En ese caso se publica amplitud 0 con `ratio_R` nulo.

Ejemplo de fila:

```json
{
  "cell_id": "8928308280fffff", "lat": 19.4326, "lon": -99.1332,
  "val_t1": 0.0, "val_t2": -7.4, "delta_change": -7.4, "velocity_mm_yr": -128.0,
  "seasonal_amp_mm": 9.4, "ratio_R": 13.62, "regime": "inelastic",
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
| `trend_mm_yr` | float64 | sí | Tendencia secular del ajuste armónico **offline** de esta misma serie (1.1). Constante dentro de la fila `cell_id == "aoi"`, porque es el parámetro de un ajuste, no un valor por fecha |
| `seasonal_mm` | float64 | sí | Componente estacional **ajustada offline** para esa fecha (1.1). Con `trend_mm_yr` deja un residuo constante: `value - trend_mm_yr·t - seasonal_mm` es la ordenada en el origen |
| `unit` | string | sí | `"mm"` |
| `sensor` | string | sí | `nisar` \| `sentinel-1` |
| `product` | string | sí | Enum §7.3 |
| `quality` | string | sí | `good` \| `fair` \| `poor` |
| `provenance_id` | string | sí | Mismo formato que en el grid |

Reglas:

- las filas `cell_id == "aoi"` son **obligatorias** y alimentan el gráfico principal: una por fecha
  y caso (62 filas por ciudad con la ventana actual de dos años);
- las filas por celda son **opcionales** y solo se generan si el fichero completo sigue dentro del
  presupuesto de 15 MB (62 fechas × ~15 000 celdas supera ese límite en CSV: por defecto **no** se
  emiten; si se emiten, se limitan a las celdas del top-N por \|velocidad\| o a una resolución H3
  menor, y se documenta el criterio en `meta.stats`);
- `date` nunca fuera de `meta.dates`; `value` nunca se interpola ni se rellena;
- `trend_mm_yr` y `seasonal_mm` (1.1) son el resultado del ajuste de la serie agregada del caso: se
  emiten, no se recalcula nada en el navegador, y su coherencia se comprueba con el residuo constante
  descrito arriba;
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
    "median_seasonal_amp_mm": 9.4,       // 1.1 · ejemplo, no medición
    "median_ratio_R": 13.62,             // 1.1 · ejemplo, no medición
    "dominant_regime": "inelastic",      // 1.1 · ejemplo, enum §7.5
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

**El bloque de arriba es ilustrativo, no una medición.** Las cifras de `stats` muestran la *forma* del
objeto (y las tres claves nuevas de 1.1 con valores plausibles de ejemplo); los números reales salen
de los artefactos y de `scripts/demo_numbers.py`, y ningún documento debe citarlos desde aquí.

Dentro de `stats` son obligatorias (`STATS_REQUIRED_KEYS`, 8 claves desde 1.1): `n_cells`, `n_dates`,
`median_velocity_mm_yr`, `median_seasonal_amp_mm`, `median_ratio_R`, `dominant_regime`,
`p05_velocity_mm_yr`, `p95_velocity_mm_yr`. Claves como `detection_limit_mm_yr` son condicionales
(ver reglas); las extra (`n_context_features`, `annual_coverage`, `design_condition`, …) están
permitidas y las escribe el pipeline.

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
- `stats` completo: las 8 claves de `STATS_REQUIRED_KEYS` (1.1); si `|median_velocity_mm_yr| < 5`,
  `detection_limit_mm_yr` es obligatorio;
- `stats.median_seasonal_amp_mm` y `stats.median_ratio_R`, cuando son numéricos, deben ser ≥ 0;
- `stats.median_ratio_R` es `null` (JSON `null`: ni `0.0` ni la clave ausente) cuando **ninguna**
  celda del caso ajustó armónico, es decir cuando `stats.median_seasonal_amp_mm == 0`: la mediana de
  un cociente indefinido no existe. La **clave** sigue siendo obligatoria (§4): `null` dice
  «indefinido» y la ausencia dice «generador 1.0»;
- `meta.limitations` debe llevar una frase explícita de «seasonal term not fitted» cuando el caso
  tomó la ruta de 0 armónicos: el `null` viaja con su explicación, para que nadie lo lea como
  «estacionalidad cero medida»;
- `stats.dominant_regime` debe estar en `REGIMES` (§7.5): es el mismo enum que `regime` del grid, no
  una segunda taxonomía;
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

### 7.5 `regime` y `dominant_regime` — `classify_regime(velocity_mm_yr, seasonal_amp_mm)`

Enum `REGIMES` = `inelastic`, `elastic`, `transitional`. Se deriva del índice `R` (§12.1) y no de
un umbral de velocidad: `risk_level` (§7.2) sigue siendo la banda de velocidad, y confundirlos
borra la tesis (una ciudad puede ser `critico` por velocidad y `elastic` por régimen).

| `R = \|velocity_mm_yr\| / seasonal_amp_mm` | `regime` | Lectura física |
|---|---|---|
| `R > 2` | `inelastic` | domina lo secular: permanente, no recuperable |
| `1 ≤ R ≤ 2` | `transitional` | ninguno domina: régimen mixto |
| `R < 1` | `elastic` | domina lo estacional: recuperable |

Las desigualdades son estrictas: `R == 2` **no** es `inelastic` y `R == 1` **no** es `elastic`.
`seasonal_amp_mm == 0` con tendencia no nula da `R = ∞` → `inelastic`; una serie totalmente plana da
`transitional`, porque «no se mueve» no lleva información de régimen e inventar `elastic` afirmaría
recuperabilidad. **Ese `R = ∞` exige que el armónico se haya ajustado.** Si el span no identifica
ningún armónico (`harmonic_order == 0`, por debajo de 0,75 año), `seasonal_amp_mm` es `0` por
construcción y no por medición: no hay pareja secular/estacional que comparar, así que `regime` es
`transitional` (no clasificable) y publicar `inelastic` afirmaría compactación permanente a partir de
un término que nunca se ajustó. Ese `∞` es el cociente **interno** de `classify_regime`: el `ratio_R` **publicado**
es `null` cuando no se ajustó armónico (§3), porque el contrato no fabrica un número que no existe.
Un ajuste no finito también cae a `transitional`.

`regime` es por celda (columna del grid); `dominant_regime` es la **moda** de `regime` en
`meta.stats`, con los empates resueltos por orden alfabético para que dos ejecuciones den el mismo
fichero. Ambos usan el mismo enum: `dominant_regime` no introduce vocabulario nuevo.

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

El primer comando imprime además `contract fingerprint: <64 hex>`. Esa huella es
`contract_fingerprint()`: el SHA-256 del payload congelado, que incluye los campos de `grid`,
`timeseries` y `context`, `meta_required`, **`stats_required`** (esta clave entra en el payload con
1.1, §12.3), `profiles` y `risk`. Dos consecuencias prácticas: (a) añadir una clave a
`STATS_REQUIRED_KEYS` mueve la huella aunque no cambie ningún nombre de campo; (b) los documentos
deben citar la huella **impresa**, no una recordada — `docs/*.md` publica el digest y
`tests/test_fingerprint_consistency.py` lo contrasta.

Las funciones viven en `src/domain/contracts.py` y **no** importan Streamlit, geopandas ni pydeck:
el pipeline, los loaders y CI comparten exactamente el mismo código de validación.

---

## 10. Compatibilidad con el flujo y el README

| Documento / flujo | Estado |
|---|---|
| `flujo §5.1` (`nisar_grid.parquet`) | se mantienen todos los campos; se **añaden** `velocity_mm_yr` y `velocity_unit` (§2.1) en 1.0, y `seasonal_amp_mm`, `ratio_R` y `regime` en 1.1 (§12.1) |
| `flujo §5.2` (`timeseries.csv`) | se mantienen los 8 campos; se **añade** `cell_id` (§2.4) en 1.0 y `trend_mm_yr` / `seasonal_mm` en 1.1 |
| contrato 1.0 | 1.1 es **aditiva y obligatoria**: no se retira nada de 1.0 y las ocho incorporaciones son `required`, así que un generador 1.0 falla cerrado en vez de publicar un fichero a medias. Historial en §12.4 |
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
3. **Ventana temporal.** `dates` sale de las adquisiciones realmente descargadas. Desde 1.1 la
   ventana deja de ser libre: es de **dos años** porque por debajo de 0.75 año por armónico la
   amplitud estacional no es publicable (§12.2). La duración concreta se cierra en
   `config/study_cases.yaml`, no aquí.
4. **Emisión de series por celda.** Depende del presupuesto de tamaño (§4).

---

## 12. Qué cambia en 1.1 («What 1.1 changes»)

1.0 no se borra: esta sección es el diff, no un reemplazo. `SCHEMA_VERSION = "1.1"`,
`CONTRACT_REVISION = 2`; `CONTRACT_FROZEN_UTC` sigue en `2026-11-14T00:00:00Z` porque 1.1 es aditiva
sobre el mismo esquema congelado, no una re-congelación.

### 12.1 Campos añadidos — todos **obligatorios**

| Artefacto | Campo | Tipo | Unidad | Enum / regla |
|---|---|---|---|---|
| `nisar_grid.parquet` | `seasonal_amp_mm` | float64 | mm | ≥ 0 |
| `nisar_grid.parquet` | `ratio_R` | float64 | — | `\|velocity_mm_yr\| / seasonal_amp_mm`; con armónico ajustado ≥ 0 y finito, `null` donde `seasonal_amp_mm == 0` (§3) |
| `nisar_grid.parquet` | `regime` | string | — | `inelastic` \| `elastic` \| `transitional` (§7.5) |
| `timeseries.csv` | `trend_mm_yr` | float64 | mm/year | tendencia del ajuste offline de la serie |
| `timeseries.csv` | `seasonal_mm` | float64 | mm | componente estacional de ese ajuste |
| `meta.json` → `stats` | `median_seasonal_amp_mm` | float64 | mm | ≥ 0, mediana del grid |
| `meta.json` → `stats` | `median_ratio_R` | float64 | — | ≥ 0 cuando es numérico; `null` si `median_seasonal_amp_mm == 0` (§5) |
| `meta.json` → `stats` | `dominant_regime` | string | — | mismo enum `REGIMES` (§7.5) |

Son `required`, no opcionales: un generador 1.0 falla cerrado en `validate_grid()` /
`validate_meta()` en lugar de producir un artefacto a medias. Hay una sola excepción de **valor**
—nunca de columna ni de clave—: `ratio_R` y `stats.median_ratio_R` se publican `null` donde no se
ajustó armónico, porque `\|tendencia\| / 0` es indefinido y cualquier número ahí sería inventado
(§3, §5). **Ningún** campo ni clave de 1.0 se retira ni se renombra, así que el diff es estrictamente
aditivo y el consumidor solo necesita mirar `meta.schema_version` para saber qué forma esperar.

### 12.2 Son valores **ajustados**, no tecleados — y por eso la ventana es de dos años

Los ocho campos salen de un ajuste, nunca de una constante escrita a mano:
`harmonic_fit` de `pipeline/science/transforms.py` produce `trend_mm_yr` y `seasonal_amp_mm`;
`classify_regime` convierte la pareja en `ratio_R` y `regime`; la serie publica el reparto tendencia /
estacional del ajuste de la **serie agregada del caso** (`trend_mm_yr` constante, `seasonal_mm` por
fecha); `meta.stats` agrega el grid (medianas y moda). Ni `seasonal_amp_mm` ni `ratio_R` ni `regime`
son umbrales congelados: los umbrales congelados son los de §7.2/§7.3, y estos campos se *miden*.

Que el mock inyecte una amplitud conocida en la serie sintética y el pipeline la recupere no
demuestra nada por sí solo: el número es honesto porque lo produce el mismo `harmonic_fit` que corre
sobre datos reales, y porque **pasa su propia puerta de identificabilidad**.

Esa puerta son dos condiciones, ambas en `pipeline/science/transforms.py`:

| Condición | Constante | Límite | Ventana ANTIGUA (132 d) | Ventana NUEVA (2 años) |
|---|---|---:|---|---|
| cobertura por armónico | `MIN_SEASONAL_COVERAGE_PER_HARMONIC` | ≥ 0.75 año por armónico | `annual_coverage` 0.3614 → no pasa | 2.0041 → pasa |
| condicionamiento | `MAX_DESIGN_CONDITION` | ≤ 30 | condición 66.84 → no pasa | 4.12 → pasa |
| veredicto | `HarmonicFit.seasonal_identifiable` | — | `False` → **no se publica nada**; `regime` forzado a `transitional` | `True` → publicable |

En la ventana antigua el propio pipeline decía «no publiques `seasonal_amp_mm`»: con 0.3614 de
cobertura anual y una condición de 66.84 frente al límite de 30, el par anual es casi colineal con
`[1, t]` y la amplitud es un artefacto del ajuste, no una medida. Publicar el campo de todas formas
—lo que hizo `origin/main` con su 1.1— habría emitido un número que la puerta del propio repo
rechaza. La decisión de 1.1 no fue retirar los campos, sino **mover la ventana**: dos años (732 días,
62 fechas a la cadencia de 12 días), donde ambas condiciones pasan con margen —incluido el orden de
dos armónicos, porque el segundo exige 1.5 años de cobertura— y el campo se puede publicar.

Los cuatro números de la tabla son **medidas registradas**, no cálculos de este documento: 0.3614 y
66.84 son lo que midió `harmonic_fit` en la ventana de 132 días (documentados en el docstring de
`src/domain/contracts.py` y en [`decisions.md`](decisions.md) O-05); 2.0041 y 4.12 son
`stats.annual_coverage` / `stats.design_condition` de los `meta.json` de la ventana de dos años
(`4.02` es el diseño de un solo armónico; el artefacto publica el de dos, que es el que se ajusta). Si
la ventana vuelve a cambiar, estos números cambian con ella: son la razón del diff, no parte del
esquema.

### 12.3 La huella se movió

El payload de `contract_fingerprint()` ganó la clave `stats_required` (además de `grid`,
`timeseries`, `context`, `meta_required`, `profiles` y `risk`), y los tres campos nuevos entran en
las listas de `grid` y `timeseries`: la huella cambia aunque `SCHEMA_VERSION` fuese la única
diferencia. La de 1.1 es la del encabezado de este documento y la imprime
`./scripts/python.sh -m src.domain.contracts`; la de 1.0 quedó retirada.

El digest retirado de 1.0 **no se repite aquí como literal**: está registrado, con su marcador de
historia, en [`decisions.md`](decisions.md) (D-01), y `tests/test_fingerprint_consistency.py`
rechaza cualquier copia completa del mismo que no lleve ese marcador. La consecuencia operativa sí
va aquí: un documento que aún publique el digest viejo está desactualizado, no «documentando
historia», y la huella se actualiza en el mismo cambio que el esquema (§9).

### 12.4 Historial (`CHANGELOG` en `src/domain/contracts.py`)

| Versión | Revisión | Entrada |
|---|---:|---|
| `1.1` | `2` | Añade las métricas de la tesis: `seasonal_amp_mm`, `ratio_R` y `regime` al grid; `trend_mm_yr` y `seasonal_mm` a la serie; y el agregado de tesis a `meta.stats`. Son valores **ajustados** (armónicos del reparto tendencia/estacional del README §6), nunca constantes tecleadas, y el trío estacional solo es publicable cuando pasa la puerta de span/condicionamiento de `pipeline/science/transforms.py`. |
| `1.0` | `1` | Congelación inicial. Resuelve seis ambigüedades del flujo §5: `delta_change` son mm acumulados y la tasa vive en `velocity_mm_yr`; `product` es un enum declarado (OPERA DISP-S1 **no** está disponible para estos AOI); `sensor` es enum; `timeseries` tiene clave de agregación `cell_id`; umbrales por sensor+producto con `revisit_days`; `stats` lleva `detection_limit_mm_yr` para los casos de resultado nulo. |

Las frases de §1–§11 que describen una decisión de 1.0 se mantienen en su sitio y en su tiempo
verbal: cuando 1.1 las amplía, la anotación remite a §12 en vez de sustituirlas. Eso es deliberado —
el documento es la pista de auditoría de la congelación, no solo la foto de la última revisión.
