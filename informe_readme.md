# Three Cities, One Pump — Informe Maestro / Master Report

**NASA Space Apps Challenge 2026 — *Dancing with the SARs*** · Misión NISAR (NASA-ISRO)
Equipo: **yuls** · **pozo** · **fradiaz**

> **Idioma / Language:** [🇪🇸 Español](#es) · [🇬🇧 English](#en)
> Cada sección es autocontenida y puede presentarse por separado.
> *Each section is self-contained and can be shown independently.*

---

<a id="es"></a>

# 🇪🇸 Español

## Índice

1. [Resumen ejecutivo](#es-1) · 2. [Tesis científica](#es-2) · 3. [Casos de estudio](#es-3) ·
4. [Restricción crítica NISAR](#es-4) · 5. [Arquitectura](#es-5) · 6. [Contratos de datos](#es-6) ·
7. [Pipeline metodológico](#es-7) · 8. [Ecuaciones](#es-8) · 9. [Stack y requisitos](#es-9) ·
10. [Roles y reparto del trabajo](#es-10) · 11. [Alcance del MVP](#es-11) ·
12. [Cronograma 48 h](#es-12) · 13. [Validación y limitaciones](#es-13) ·
14. [Estructura del repositorio](#es-14) · 15. [Fuentes y referencias](#es-15)

<a id="es-1"></a>
## 1. Resumen ejecutivo

Aplicación web interactiva que traduce observaciones SAR de NISAR en **indicadores accionables de
subsidencia del terreno y pérdida de almacenamiento de acuíferos**. El entregable es una
aplicación desplegada, no un informe.

**Diferencial de NISAR (Banda L, λ ≈ 24 cm, 1.25 GHz):**

| Ventaja | Consecuencia práctica |
|---|---|
| Penetra el dosel vegetal | Detecta hundimiento y cambios **bajo** cultivos y arbolado, donde Sentinel-1 (Banda C, λ ≈ 5.6 cm) solo ve hojas |
| Mayor coherencia temporal | Mantiene fase entre pasadas (ciclo de 12 días) donde la Banda C decorrelaciona |
| Independiente de nubes y luz | Series temporales sin huecos, día y noche |

**Público objetivo:** (1) gestores hídricos y protección civil — necesitan hectáreas y milímetros,
no decibelios; (2) comunidad técnica — necesitan trazabilidad del producto y del umbral aplicado;
(3) público general y educadores — necesitan comparadores «antes/después» y gráficos claros.

> **Estado verificado de los datos (nov. 2026).** Lanzamiento **30 jul 2025**. Banda **L = 24 cm**
> y banda **S = 9.4 cm**. Productos L0–L2 **plenamente validados**; en feb. 2026 se publicaron
> >100 000 productos L1–L3 **pre-calibración** como muestreo global; la **liberación global
> calibrada estaba prevista para julio de 2026**. Datos S-band liberados por ISRO vía Bhoonidhi.
> Acceso: ASF Vertex, `asf_search`, Earthdata Search. Todo abierto y gratuito.

<a id="es-2"></a>
## 2. Tesis científica

La magnitud de la subsidencia la gobierna la **compresibilidad de las intercalaciones del
acuífero**, no el volumen de agua extraído. Tres ciudades, sobreexplotación comparable, tres
resultados distintos:

| | **Ciudad de México** | **Yakarta** | **Málaga** |
|---|---|---|---|
| Acuífero | Arcillas lacustres volcaniclásticas, cuenca cerrada | Arcillas blandas deltaicas, costero | Delgado recubrimiento detrítico sobre karst confinado |
| Compresibilidad | Extrema | Alta | Baja |
| Régimen | Mayormente **inelástico** | Mixto | Mayormente **elástico** |
| Subsidencia | **−45 cm/año** | ~5 cm/año medio; >1 m acumulado | mm–cm/año |
| Permanencia | Pérdida permanente (~60 hm³/año) | Permanente | En gran parte recuperable |
| Modo de fallo | Pérdida de suelo, fallas, inversión de drenaje | Inundación, traslado de capital | Intrusión salina, agotamiento de embalses |

**Afirmación central:** InSAR es el instrumento correcto en Ciudad de México y Yakarta, y el
instrumento **equivocado** en Málaga. *Saber cuál es cuál es el resultado.*

> **Idea de producto:** con una leyenda compartida, Málaga se renderiza casi plana. **Ese vacío es
> el hallazgo** — y es una decisión de interfaz, no un párrafo.

<a id="es-3"></a>
## 3. Casos de estudio

### 3.1. Núcleo decidido (urbano)

| Ciudad | AOI | Rol | Amenaza principal |
|---|---|---|---|
| Ciudad de México, MX | 19.43 N, −99.13 W | Caso A (extremo) | Subsidencia diferencial → infraestructura, inversión de drenaje |
| Yakarta, ID | −6.21 S, 106.85 E | Caso B (costero) | Inundación; cobertura de agua entubada del 64 % impulsa pozos |
| Málaga, ES | 36.72 N, −4.42 W | **Control / negativo** | Intrusión salina; agotamiento de embalses |

### 3.2. Candidatos alternativos evaluados

| Criterio | A: Subsidencia por acuíferos | B: Inundación bajo vegetación | C: Dinámica agrícola |
|---|---|---|---|
| Ubicación sugerida | Alto Guadalentín (Murcia), Guadalquivir | Doñana, Pantanal | Guadalquivir, Llanura Padana |
| Producto NISAR | L2 **GUNW** | L2 **GCOV** | L2 **GCOV** (`HHHH`, `HVHV`) |
| Algoritmo | Δφ → mm | Δσ⁰ (dB) T₁→T₂ | CV = σ/μ o RVI |
| Dificultad | Baja/Media | Baja | Media |
| Impacto visual | Muy alto | Alto | Medio |

**Decisión:** se mantiene el núcleo urbano (§3.1) porque la subsidencia es el fenómeno que el
enunciado **nombra explícitamente** («land subsidence», «coastal subsidence») y porque su impacto
es cuantificable en metros, euros y personas expuestas. Las opciones B y C quedan como reserva.
**Alto Guadalentín (Murcia)** es la alternativa española de subsidencia dramática (hasta ~dm/año,
Bonano et al. 2015) si se descarta el control de Málaga.

<a id="es-4"></a>
## 4. Restricción crítica NISAR — leer antes de elegir la fuente

> ⚠️ **Corrección importante respecto al supuesto habitual.** La documentación oficial describe
> los productos interferométricos de NISAR — RIFG, RUNW, GOFF, ROFF y **GUNW (que deriva de
> RUNW)** — como **generados para criosfera**: «Antarctica, Greenland, and selected mountain
> glaciers». Además, `SME2` (humedad del suelo) **excluye explícitamente las áreas urbanas**.
>
> **Consecuencia:** no se puede asumir que exista un GUNW listo sobre Doñana, Murcia, Ciudad de
> México o Yakarta. **Verificar cobertura en ASF Vertex antes de comprometer la arquitectura.**

**Cadena de respaldo obligatoria (registrar cuál se usó en `provenance`):**

1. NISAR L2 **GUNW** (si hay cobertura)
2. **OPERA DISP-S1** — serie de desplazamiento Sentinel-1, ya procesada (`displacement.asf.alaska.edu`)
3. **HyP3** — interferogramas Sentinel-1 bajo demanda (`hyp3-docs.asf.alaska.edu`)
4. **EGMS** — velocidad mm/año ya calculada, **solo Europa** (`egms.land.copernicus.eu`)
5. ISCE2 + MintPy local desde GSLC/SLC *(último recurso, no recomendado en 48 h)*

**Regla del MVP:** **no** procesar L1 (`RSLC`) crudo con `isce3`/ISCE2 existiendo L2/OPERA. Para
la demo, DISP-S1 y EGMS son rutas **preferentes**, no degradaciones.

<a id="es-5"></a>
## 5. Arquitectura

**Principio irrenunciable:** un script **offline** procesa y recorta; la aplicación web **nunca**
descarga ni procesa HDF5 (1–6 GB por escena). Arquitectura desacoplada ≈ 90 % ETL offline / 10 %
render ligero.

```
+-----------------------------------------------------------------------------------+
| CAPA 1: INGESTA Y PROCESAMIENTO OFFLINE (yuls)                                    |
|                                                                                   |
|  [ASF DAAC / NASA Earthdata]                                                      |
|         | 1. Búsqueda espacial/temporal (asf_search + earthaccess)                |
|         v                                                                         |
|  [NISAR L2 HDF5 (GCOV/GUNW)] + [OPERA DISP-S1] + [EGMS] + [GIS contextual]        |
|         | 2. Apertura selectiva del árbol HDF5 (h5py + xarray + rioxarray)        |
|         | 3. Recorte por AOI y reproyección a EPSG:4326 / EPSG:326xx              |
|         v                                                                         |
|  [Motor físico y limpieza]                                                        |
|         | 4. Enmascarado por coherencia (< 0.35) o sin dato                        |
|         | 5. Cálculo de cambio (mm LOS en GUNW · Δσ⁰ dB en GCOV)                   |
|         | 6. Cruce espacial (geopandas.sjoin) con infraestructura / uso de suelo   |
|         v                                                                         |
|  ============ CONTRATO DE DATOS (data/processed/, < 15 MB) ====================== |
|   nisar_grid.parquet · timeseries.csv · context_layers.geojson · meta.json         |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  (carga en RAM: @st.cache_data / CDN)
+-----------------------------------------------------------------------------------+
| CAPA 2: APLICACIÓN WEB (fradiaz UI · pozo servicio)                               |
|  +-------------------+  +----------------------------------------------------+    |
|  | Barra lateral     |  | KPI: área afectada · cambio máx · activos en riesgo |    |
|  | caso · fechas     |  +---------------------------+------------------------+    |
|  | umbral · coherencia| | Visor 2D/3D (PyDeck H3)   | Serie temporal (Plotly)|    |
|  | uso de suelo      |  +---------------------------+------------------------+    |
|  |                   |  | Módulo educativo: física de NISAR y metodología    |    |
|  +-------------------+  +----------------------------------------------------+    |
+-----------------------------------------------------------------------------------+
```

### 5.1. Dos implementaciones válidas

| | **Opción A — Streamlit + PyDeck + H3 + Parquet** | **Opción B — Sitio estático + MapLibre/deck.gl + PMTiles** |
|---|---|---|
| Velocidad de construcción | **Máxima** (ideal 48 h) | Media |
| Riesgo en demo | Bajo (Streamlit Cloud, `@st.cache_data`) | Muy bajo (sin servidor ni cold-start) |
| Rendimiento del slider | Limitado por Python | Fluido (WebGL) |
| Cruce con GIS | Trivial (`geopandas.sjoin` en el ETL) | Requiere pre-tileado vectorial |
| Interactividad rica | Buena | Excelente |
| **Recomendación** | **Elegir A para el MVP** | Migrar a B si sobra tiempo |

Ambas comparten el mismo **contrato de datos** (§6): el ETL no cambia al cambiar de visor.

<a id="es-6"></a>
## 6. Contratos de datos

### 6.1. `data/processed/nisar_grid.parquet` — contrato principal

| Columna | Tipo | Descripción | Responsable |
|---|---|---|---|
| `cell_id` | `string` | Índice H3 (res. 8–9) o ID de celda | yuls |
| `lat` / `lon` | `float` | Centroide en `EPSG:4326` | yuls |
| `val_t1` | `float` | Valor base T₁ (σ⁰ dB o 0 mm) | yuls |
| `val_t2` | `float` | Valor observado T₂ (σ⁰ dB o mm LOS) | yuls |
| `delta_change` | `float` | Cambio neto (T₂ − T₁) | yuls |
| `coherence` | `float` | Coherencia / índice de calidad (0–1) | yuls |
| `land_use` | `string` | Regadío · Vía férrea · Carretera · Humedal · Urbano | yuls |
| `risk_level` | `string` | Estable · Moderado · Crítico | yuls |

### 6.2. `meta.json` — metadatos y leyendas

```jsonc
{
  "city": "mexico-city", "crs": "EPSG:32614",
  "dates": ["2026-01-12", "2026-01-24"],
  "revisit_days": 12,                    // 12 = NISAR, 6 = Sentinel-1
  "layers": {
    "velocity": { "unit": "mm/yr", "range": [-450, 50],
                  "provenance": "Sentinel-1 PS-InSAR 2015-2020, 6-day" }
  },
  "stats": { "median_vel": -128, "compaction_m3_yr": 6.0e7 }
}
```

**Regla de tildado:** una ciudad de 50 × 50 km a 10 m ≈ 5000² px. **No** enviar un raster único:
pirámide de teselas **z8–z14** y velocidad cuantizada a `int16` usando `range` de `meta.json`.

<a id="es-7"></a>
## 7. Pipeline metodológico

```
P0  Alcance     AOI, EPSG:326xx, píxel de referencia
P1  Ingesta     asf_search → GUNW / OPERA DISP-S1 / HyP3 / EGMS (registrar provenance)
P2  Interfero   solo si es imprescindible: ISCE2 + SNAPHU
P3  Corrección  tropósfera (RAiDER/GACOS), error DEM (MintPy), rampas orbitales
P4  Serie       inversión de red → cubo de desplazamiento acumulado
P5  Descomponer LOS → vertical (asc+desc, v_north≈0; si no, asumir vertical puro)
P6  Separar     tendencia + armónicos estacionales + escalones → secular vs estacional
P7  Métricas    velocidad, |∇v|, volumen de compactación, S* = Δb/Δh
P8  Contexto    sjoin con edificios, población, embalses, niveles piezométricos
P9  Emitir      parquet/COG/PMTiles + series JSON + meta.json  ← contrato
```

**Los dos productos y sus rutas HDF5:**

- **GCOV** (inundación, humedad, vegetación):
  `/science/LSAR/GCOV/grids/frequencyA/HHHH` y `.../HVHV`
- **GUNW** (subsidencia, deformación):
  `/science/LSAR/GUNW/grids/frequencyA/interferogram/unwrappedPhase` y `coherenceMagnitude`

<a id="es-8"></a>
## 8. Ecuaciones

**Fase → desplazamiento LOS** (Banda L: λ = 240 mm, Δφ en radianes):

```
d_LOS (mm) = −(λ / 4π) · Δφ
```

**Proyección LOS** (θ incidencia, α rumbo):

```
d_LOS = u · [v_up, v_east, v_north]ᵀ ,   u = [cosθ, −sinθ·cosα, −sinθ·sinα]
```

Dos geometrías (asc + desc) → 2 ecuaciones, 3 incógnitas → `v_north ≈ 0` o restringir con GNSS.
**Las convenciones de signo difieren entre ISCE2, MintPy y SNAP: validar contra GNSS antes de
publicar cifras.**

**Retrodispersión y cambio:**

```
σ⁰_dB = 10 · log₁₀(Covarianza lineal)
Δσ⁰   = σ⁰_dB(T₂) − σ⁰_dB(T₁)
CV    = σ / μ            (multitemporal: resalta cambios ignorando el speckle)
```

**Filtro de calidad:** descartar píxeles con `coherenceMagnitude < 0.35`.

**Modelo de serie temporal:**

```
v(t) = v₀ + ṫ·t + Σₖ [Aₖcos(2πkt/T) + Bₖsin(2πkt/T)] + Σⱼ Δᵥⱼ·H(t−tⱼ)
```

`ṫ` → inelástico/permanente · `A_estacional` → elástico/recuperable · `R = |ṫ| / A_estacional`.

**Pérdida de almacenamiento:**

```
S_skv = Δb / (b₀·Δh)              almacenamiento específico esquelético
dV/dt = Σ_px v_up · A_px          pérdida de almacenamiento por compactación [m³/año]
```

<a id="es-9"></a>
## 9. Stack y requisitos

### 9.1. Matriz funcionalidad → dato → tecnología

| Funcionalidad | Dataset | Librerías | Valor para el usuario |
|---|---|---|---|
| Descubrir y descargar | NISAR L2 (`GCOV`/`GUNW`), DISP-S1, EGMS | `asf_search`, `earthaccess` | Acceso oficial reproducible |
| Extraer y recortar | Grupos HDF5 `/science/LSAR/...` | `h5py`, `xarray`, `rioxarray`, `rasterio` | Reduce GB a tensores ligeros |
| Calcular anomalías | Fase, coherencia, retrodispersión | `numpy`, `pandas`, `h3`, `scipy` | Elimina ruido, produce indicadores |
| Enriquecer contexto GIS | OSM, CORINE, WorldCover | `geopandas`, `shapely`, QGIS/Overpass | Cruza anomalía con territorio real |
| Visualizar 2D/3D | `nisar_grid.parquet`, `.geojson` | `streamlit`, `pydeck`, `folium` | Exploración interactiva |
| Series temporales | `timeseries.csv` | `plotly` | Velocidad del cambio y correlación |

### 9.2. `requirements.txt` (Opción A)

```text
# Core & Web Framework
streamlit>=1.38.0
pandas>=2.2.0
numpy>=1.26.0
pyarrow>=16.0.0

# Visualización interactiva y mapas
pydeck>=0.9.1
plotly>=5.22.0
folium>=0.17.0
streamlit-folium>=0.22.0

# Geoespacial ligero (vectorial y mallas)
geopandas>=1.0.0
shapely>=2.0.0
h3>=3.7.7

# Ingesta y procesamiento SAR (scripts offline)
asf_search>=7.1.0
earthaccess>=0.9.0
h5py>=3.11.0
xarray>=2024.6.0
rioxarray>=0.17.0
rasterio>=1.3.10
```

**Si se migra a la Opción B**, añadir: `pmtiles`, `maplibre`/`deck.gl` (JS), `Vite` + TypeScript.
**Procesamiento opcional:** `isce2` (`conda install -c conda-forge isce2`), `snaphu`, `MintPy`,
`hyp3_sdk`, `opera-utils`, `RAiDER`.

### 9.3. Prerrequisitos por integrante

| Herramienta | yuls | pozo | fradiaz |
|---|---|---|---|
| Cuenta NASA Earthdata | Obligatorio | Opcional | Opcional |
| Python 3.11 + Git + VS Code | Obligatorio | Obligatorio | Obligatorio |
| QGIS escritorio | Obligatorio | No | Recomendado |
| Streamlit Cloud / hosting | No | Obligatorio | Recomendado |

<a id="es-10"></a>
## 10. Roles y reparto del trabajo

| Integrante | Rol | Posee | Entrega |
|---|---|---|---|
| **yuls** | Datos SAR y visualización geoespacial | Ingesta, InSAR, métricas, contexto GIS, cartografía, escalas de color, narrative | `pipeline/`, todos los artefactos, estilos de capa |
| **pozo** | Backend e infraestructura | Servicio de artefactos, orquestación, CI/CD, caché, despliegue, contrato de rutas | `serve/`, `deploy/`, CI, publicación |
| **fradiaz** | Frontend e interacción | Esqueleto de app, mapa, slider, gráficos, estado en URL, accesibilidad | `app.py`, `ui_components/`, las 5 vistas |

### 10.1. Tareas detalladas

**yuls — datos SAR y geovisualización**
1. `01_generate_mock.py` en los primeros 30 min: `nisar_grid.parquet` y `timeseries.csv` sintéticos.
2. Catálogo con `asf_search`; descarga NISAR L2 / DISP-S1 / EGMS con `earthaccess`.
3. Árbol HDF5 con `h5py`; extracción con `xarray`/`rioxarray`; recorte AOI.
4. Conversión física (dB o mm LOS), filtro de coherencia, muestreo a H3.
5. Cruce espacial `geopandas.sjoin` → `land_use` y `risk_level`.
6. Escalas de color, incluida la **leyenda global compartida** que aplana Málaga.
7. Documentar `/pipeline/` para que el jurado verifique el rigor científico.

**pozo — backend e infraestructura**
1. Hosting estático/CDN y cabeceras de caché; soporte de *range requests* si hay PMTiles.
2. Orquestación: ejecutar `pipeline/` en CI y publicar artefactos al terminar.
3. Definir y validar el **contrato de rutas**.
4. Despliegue y uptime: «¿la URL de la demo funciona a las 4 a.m.?».

**fradiaz — frontend e interacción**
1. Inicializar repo, tema visual (`.streamlit/config.toml`, modo oscuro espacial), desplegar esqueleto.
2. Barra lateral: selector de caso, slider de fechas, umbral crítico, filtro de coherencia y uso de suelo.
3. Mapa 3D en **PyDeck** (`H3HexagonLayer`/`ColumnLayer`): altura = `abs(delta_change)`, color dinámico por umbral.
4. Serie temporal en **Plotly** (jul–nov 2026) con bandas de alerta.
5. KPI reactivos (`st.metric`), estado en URL, accesibilidad.

### 10.2. Contrato de rutas (Opción B)

```
GET /{city}/meta.json            GET /{city}/vel.pmtiles
GET /{city}/bs/{date}.pmtiles    GET /{city}/events.pmtiles
GET /{city}/series/{z}/{x}/{y}.json
```

### 10.3. Reparto sin solapes
- **yuls** no escribe código de UI (los estilos de capa son configuración JSON).
- **pozo** no toca parámetros científicos (umbrales, rangos de color).
- **fradiaz** no calcula magnitudes geoespaciales en cliente: si un número no está en un artefacto, no existe.

<a id="es-11"></a>
## 11. Alcance del MVP

**IMPRESCINDIBLE**
- Un caso de estudio principal perfectamente ejecutado (+ una segunda ubicación precargada).
- Pipeline Python documentado en `/pipeline/` que demuestre uso real de productos NISAR L2
  (o de las fuentes de respaldo declaradas en §4).
- Dashboard interactivo: KPI reactivos, mapa 3D/2D, serie temporal, pestaña divulgativa.

**DESCARTADO — prohibido invertir tiempo**
- Descarga y procesamiento **en vivo** de HDF5 al hacer clic en una coordenada.
- InSAR crudo desde L1 (`RSLC`) con `isce3`/ISCE2, existiendo L2/OPERA.
- Autenticación de usuarios, PostgreSQL/PostGIS, backend separado en FastAPI/React.

**Líneas de corte** (si falta tiempo, en este orden): eventos de cambio → tercera ciudad → todo
salvo Málaga vía EGMS. **Nunca recortar:** trazabilidad (`provenance`).

<a id="es-12"></a>
## 12. Cronograma 48 h

**Bloque 1 — Sincronización y desbloqueo (Sáb 15:00–17:30)**
- *Todos, 20 min:* decisión final del caso y las 4 coordenadas del AOI.
- **yuls:** genera y sube el parquet simulado; configura credenciales Earthdata; lanza la búsqueda.
- **fradiaz:** esqueleto de `app.py` leyendo el mock; verifica PyDeck + Plotly en local.
- **pozo:** hosting, CI mínimo, contrato de rutas congelado.

**Bloque 2 — Núcleo en paralelo (Sáb 17:30–22:30)**
- **yuls:** primer HDF5 real, recorte, conversión física, distribución de valores.
- **fradiaz:** reactividad completa (slider → KPI, hexágonos, umbral en Plotly).
- **pozo:** publicación automática de artefactos; auditoría de tamaño.

**Bloque 3 — Datos reales y calibración (Dom 09:00–13:30)**
- **yuls + fradiaz:** integración de parquet y series definitivos.
- **fradiaz:** cámara (`pitch=45`), escala vertical, despliegue público.
- **pozo:** cabeceras de caché, prueba de carga < 3 s.

**Bloque 4 — Congelación, documentación y pitch (Dom 14:30–19:00)**
- **16:00 — CONGELACIÓN DE CÓDIGO.** Solo correcciones visuales menores.
- **yuls:** documentar `/pipeline/` + captura del árbol HDF5 procesado.
- **pozo:** verificación final en producción.
- **Todos:** README final y vídeo (20 s problema · 90 s demo · 30 s arquitectura e impacto).

<a id="es-13"></a>
## 13. Validación y limitaciones

| Verificación | Método |
|---|---|
| Exactitud de velocidad | GNSS / nivelación; objetivo < 5 mm/año RMSE |
| Errores de desenvolvimiento | fase de cierre con tripletes redundantes |
| Atmósfera | comparar con corrida corregida GACOS/ERA5 |
| Máscaras de inundación | mapas de activación de Copernicus EMS |
| Resultado nulo (Málaga) | análisis de límite de detección — publicar la cota superior |

**Presupuesto de error (mayor a menor):** desenvolvimiento ≫ tropósfera ≫ orbital ≫ DEM ≫ ruido.

**Limitaciones**
1. Málaga puede estar en el umbral de detección: la cota **es** el resultado.
2. Ciudad de México no es costera: no sostener un marco costero para las tres.
3. Cobertura urbana de NISAR **no garantizada** (§4).
4. GUNW tiene posting de 80 m: escala de ciudad, no de edificio.
5. Inundación urbana en SAR se confunde por doble rebote y sombra: usar detección de cambio y
   pérdida de coherencia, no umbral simple.
6. La coherencia depende del intervalo de revisita: **NISAR 12 días ≠ Sentinel-1 6 días**. No
   mezclar umbrales entre sensores.

<a id="es-14"></a>
## 14. Estructura del repositorio

```text
nisar-spaceapps-2026/
├── README.md                  ← documentación y pitch
├── informe_readme.md          ← este documento
├── requirements.txt           ← compartido (configurado en la hora 1)
├── app.py                     ← yuls/fradiaz: aplicación principal
├── ui_components/             ← fradiaz
│   ├── map_view.py
│   ├── charts.py
│   └── kpi_cards.py
├── pipeline/                  ← yuls
│   ├── 01_generate_mock.py    ← prioridad minuto 30: desbloquea al equipo
│   ├── 02_search_download.py
│   ├── 03_process_nisar_hdf5.py
│   └── events/                ← detección de cambios
├── serve/  deploy/            ← pozo
├── gis_context/               ← yuls
│   ├── enrich_spatial.py
│   └── content_texts.py
└── data/
    ├── raw/                   ← .gitignore (HDF5 pesados)
    └── processed/             ← punto de encuentro (< 15 MB en Git)
        ├── nisar_grid.parquet
        ├── timeseries.csv
        ├── context_layers.geojson
        └── meta.json
```

<a id="es-15"></a>
## 15. Fuentes y referencias

**Fuentes de datos** (`✓` = verificado activo): ASF NISAR (`asf.alaska.edu/datasets/daac/nisar` ✓) ·
NISAR DUG (`nisar-docs.asf.alaska.edu` ✓) · ASF Vertex (`search.asf.alaska.edu` ✓) ·
HyP3 (`hyp3-docs.asf.alaska.edu` ✓) · OPERA DISP-S1 (`displacement.asf.alaska.edu` ✓) ·
EGMS (`egms.land.copernicus.eu` ✓) · GRACE-FO en PO.DAAC ✓ · GLDAS ✓ · IGRAC GGIS ✓ ·
USGS NWIS API ✓ · GHSL ✓ · WorldPop ✓ · DLR WSF 2019 ✓ · Copernicus EMS ✓

| Tema | Cita | DOI |
|---|---|---|
| Amenaza global de subsidencia | Herrera-García et al. 2021, *Science* | 10.1126/science.abb8549 |
| Subsidencia → pérdida de almacenamiento | Hasan et al. 2023, *Nat Commun* | 10.1038/s41467-023-41933-z |
| Ciudad de México −45 cm/año | Cigna & Tapete 2022, *GRL* | 10.1029/2022gl098923 |
| 99 ciudades costeras, PS-InSAR uniforme | Wu et al. 2022, *GRL* | 10.1029/2022gl098477 |
| Revisión metodológica | Galloway & Burbey 2011, *Hydrogeol J* | 10.1007/s10040-011-0775-5 |
| Ciudades que se hunden (Indonesia) | Chaussard et al. 2013, *RSE* | 10.1016/j.rse.2012.10.015 |
| Yakarta 2017–22, SBAS multitrack | 2024, *Geocarto Int* | 10.1080/10106049.2024.2364726 |
| Yakarta: agua entubada → abstracción | 2022, *Water* | 10.3390/w14203197 |
| Málaga/Torremolinos | Ruiz-Constán et al. 2018, *STOTEN* | 10.1016/j.scitotenv.2018.04.280 |
| Crisis hídrica de la Axarquía | Junquera et al. 2025, *PNAS* | 10.1073/pnas.2508055122 |
| Guía de datos NISAR | Kristenson et al. 2026 | 10.5281/ZENODO.20075813 |

*Las tasas de Yakarta varían según método y periodo: citar la fuente usada en `provenance`.*

---

<a id="en"></a>

# 🇬🇧 English

## Contents

1. [Executive summary](#en-1) · 2. [Scientific thesis](#en-2) · 3. [Study areas](#en-3) ·
4. [Critical NISAR constraint](#en-4) · 5. [Architecture](#en-5) · 6. [Data contracts](#en-6) ·
7. [Methodology pipeline](#en-7) · 8. [Equations](#en-8) · 9. [Stack & requirements](#en-9) ·
10. [Roles & labour split](#en-10) · 11. [MVP scope](#en-11) · 12. [48 h schedule](#en-12) ·
13. [Validation & limitations](#en-13) · 14. [Repository layout](#en-14) ·
15. [Sources & references](#en-15)

<a id="en-1"></a>
## 1. Executive summary

An interactive web application that translates NISAR SAR observations into **actionable
indicators of land subsidence and aquifer storage loss**. The deliverable is a deployed
application, not a report.

**Why NISAR matters (L-band, λ ≈ 24 cm, 1.25 GHz):**

| Advantage | Practical consequence |
|---|---|
| Penetrates vegetation canopy | Detects subsidence and change **beneath** crops and woodland, where Sentinel-1 (C-band, λ ≈ 5.6 cm) only sees leaves |
| Higher temporal coherence | Holds phase across passes (12-day cycle) where C-band decorrelates |
| Cloud- and light-independent | Gap-free time series, day and night |

**Audiences:** (1) water managers and civil protection — they need hectares and millimetres, not
decibels; (2) technical community — they need product traceability and the thresholds applied;
(3) public and educators — they need before/after comparators and clear charts.

> **Verified data status (Nov 2026).** Launched **30 Jul 2025**. **L-band = 24 cm**, **S-band =
> 9.4 cm**. L0–L2 products **fully validated**; Feb 2026 released >100,000 **pre-calibration**
> L1–L3 products as a global sampling; the **full calibrated global release was scheduled for
> July 2026**. S-band released by ISRO via Bhoonidhi. Access: ASF Vertex, `asf_search`, Earthdata
> Search. All open and free.

<a id="en-2"></a>
## 2. Scientific thesis

Subsidence magnitude is governed by the **compressibility of aquifer interbeds**, not by how much
water is pumped. Three cities, comparable overdraft, three different outcomes:

| | **Mexico City** | **Jakarta** | **Málaga** |
|---|---|---|---|
| Aquifer | Lacustrine volcaniclastic clay, closed basin | Deltaic soft clay, coastal | Thin detrital cover over confined karst |
| Compressibility | Extreme | High | Low |
| Regime | Mostly **inelastic** | Mixed | Mostly **elastic** |
| Subsidence | **−45 cm/yr** | ~5 cm/yr avg; >1 m cumulative | mm–cm/yr |
| Permanence | Permanent loss (~60 hm³/yr) | Permanent | Largely recoverable |
| Failure mode | Land loss, faulting, drainage inversion | Inundation, capital relocation | Salinization, reservoir depletion |

**Core claim:** InSAR is the correct instrument in Mexico City and Jakarta, and the **wrong**
instrument in Málaga. *Knowing which is which is the result.*

> **Product insight:** with a shared legend, Málaga renders nearly flat. **That blankness is the
> finding** — and it is a UI decision, not a paragraph.

<a id="en-3"></a>
## 3. Study areas

### 3.1. Decided core (urban)

| City | AOI | Role | Primary hazard |
|---|---|---|---|
| Mexico City, MX | 19.43 N, −99.13 W | Case A (extreme) | Differential subsidence → infrastructure, drainage inversion |
| Jakarta, ID | −6.21 S, 106.85 E | Case B (coastal) | Inundation; 64% piped-water coverage drives well pumping |
| Málaga, ES | 36.72 N, −4.42 W | **Control / negative** | Seawater intrusion; reservoir depletion |

### 3.2. Alternative candidates evaluated

| Criterion | A: Aquifer subsidence | B: Flooding under vegetation | C: Agricultural dynamics |
|---|---|---|---|
| Suggested location | Alto Guadalentín (Murcia), Guadalquivir | Doñana, Pantanal | Guadalquivir, Po Plain |
| NISAR product | L2 **GUNW** | L2 **GCOV** | L2 **GCOV** (`HHHH`, `HVHV`) |
| Algorithm | Δφ → mm | Δσ⁰ (dB) T₁→T₂ | CV = σ/μ or RVI |
| Difficulty | Low/Medium | Low | Medium |
| Visual impact | Very high | High | Medium |

**Decision:** the urban core (§3.1) is retained because subsidence is the phenomenon the brief
**explicitly names** ("land subsidence", "coastal subsidence") and because its impact is
quantifiable in metres, money and people exposed. Options B and C stay in reserve.
**Alto Guadalentín (Murcia)** is the dramatic Spanish alternative (up to ~dm/yr, Bonano et al.
2015) if the Málaga control is dropped.

<a id="en-4"></a>
## 4. Critical NISAR constraint — read before choosing a source

> ⚠️ **Important correction to the usual assumption.** Official documentation describes NISAR's
> interferometry products — RIFG, RUNW, GOFF, ROFF and **GUNW (which derives from RUNW)** — as
> **cryosphere-generated**: "Antarctica, Greenland, and selected mountain glaciers". In addition,
> `SME2` (soil moisture) **explicitly excludes urban areas**.
>
> **Consequence:** you cannot assume a ready-made GUNW exists over Doñana, Murcia, Mexico City or
> Jakarta. **Check coverage in ASF Vertex before committing the architecture.**

**Mandatory fallback chain (record which one won in `provenance`):**

1. NISAR L2 **GUNW** (if coverage exists)
2. **OPERA DISP-S1** — pre-processed Sentinel-1 displacement series (`displacement.asf.alaska.edu`)
3. **HyP3** — on-demand Sentinel-1 interferograms (`hyp3-docs.asf.alaska.edu`)
4. **EGMS** — already-computed mm/yr velocity, **Europe only** (`egms.land.copernicus.eu`)
5. Local ISCE2 + MintPy from GSLC/SLC *(last resort, not recommended in 48 h)*

**MVP rule:** do **not** process raw L1 (`RSLC`) with `isce3`/ISCE2 when L2/OPERA exist. For the
demo, DISP-S1 and EGMS are **preferred** routes, not degradations.

<a id="en-5"></a>
## 5. Architecture

**Non-negotiable principle:** an **offline** script processes and crops; the web app **never**
downloads or processes HDF5 (1–6 GB per scene). Decoupled architecture ≈ 90% offline ETL / 10%
light rendering.

```
+-----------------------------------------------------------------------------------+
| LAYER 1: OFFLINE INGEST & PROCESSING (yuls)                                       |
|                                                                                   |
|  [ASF DAAC / NASA Earthdata]                                                      |
|         | 1. Spatial/temporal search (asf_search + earthaccess)                   |
|         v                                                                         |
|  [NISAR L2 HDF5 (GCOV/GUNW)] + [OPERA DISP-S1] + [EGMS] + [contextual GIS]        |
|         | 2. Selective HDF5 tree access (h5py + xarray + rioxarray)               |
|         | 3. AOI clip and reprojection to EPSG:4326 / EPSG:326xx                 |
|         v                                                                         |
|  [Physics & cleaning engine]                                                      |
|         | 4. Mask by coherence (< 0.35) or no-data                                |
|         | 5. Change computation (mm LOS for GUNW · Δσ⁰ dB for GCOV)                |
|         | 6. Spatial join (geopandas.sjoin) with infrastructure / land use         |
|         v                                                                         |
|  ============ DATA CONTRACT (data/processed/, < 15 MB) ========================== |
|   nisar_grid.parquet · timeseries.csv · context_layers.geojson · meta.json         |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  (RAM cache: @st.cache_data / CDN)
+-----------------------------------------------------------------------------------+
| LAYER 2: WEB APPLICATION (fradiaz UI · pozo serving)                              |
|  +-------------------+  +----------------------------------------------------+    |
|  | Sidebar           |  | KPI: affected area · max change · assets at risk     |    |
|  | case · dates      |  +---------------------------+------------------------+    |
|  | threshold · coh.  | | 2D/3D view (PyDeck H3)    | Time series (Plotly)   |    |
|  | land use          |  +---------------------------+------------------------+    |
|  |                   |  | Educational module: NISAR physics & methodology    |    |
|  +-------------------+  +----------------------------------------------------+    |
+-----------------------------------------------------------------------------------+
```

### 5.1. Two valid implementations

| | **Option A — Streamlit + PyDeck + H3 + Parquet** | **Option B — Static site + MapLibre/deck.gl + PMTiles** |
|---|---|---|
| Build speed | **Highest** (ideal for 48 h) | Medium |
| Demo risk | Low (Streamlit Cloud, `@st.cache_data`) | Very low (no server, no cold start) |
| Slider performance | Python-bound | Smooth (WebGL) |
| GIS join | Trivial (`geopandas.sjoin` in ETL) | Requires vector pre-tiling |
| Rich interactivity | Good | Excellent |
| **Recommendation** | **Choose A for the MVP** | Migrate to B if time allows |

Both share the same **data contract** (§6): the ETL is unchanged when the viewer changes.

<a id="en-6"></a>
## 6. Data contracts

### 6.1. `data/processed/nisar_grid.parquet` — primary contract

| Column | Type | Description | Owner |
|---|---|---|---|
| `cell_id` | `string` | H3 index (res. 8–9) or cell ID | yuls |
| `lat` / `lon` | `float` | Centroid in `EPSG:4326` | yuls |
| `val_t1` | `float` | Baseline value T₁ (σ⁰ dB or 0 mm) | yuls |
| `val_t2` | `float` | Observed value T₂ (σ⁰ dB or mm LOS) | yuls |
| `delta_change` | `float` | Net change (T₂ − T₁) | yuls |
| `coherence` | `float` | Coherence / quality index (0–1) | yuls |
| `land_use` | `string` | Irrigated · Railway · Road · Wetland · Urban | yuls |
| `risk_level` | `string` | Stable · Moderate · Critical | yuls |

### 6.2. `meta.json` — metadata and legends

```jsonc
{
  "city": "mexico-city", "crs": "EPSG:32614",
  "dates": ["2026-01-12", "2026-01-24"],
  "revisit_days": 12,                    // 12 = NISAR, 6 = Sentinel-1
  "layers": {
    "velocity": { "unit": "mm/yr", "range": [-450, 50],
                  "provenance": "Sentinel-1 PS-InSAR 2015-2020, 6-day" }
  },
  "stats": { "median_vel": -128, "compaction_m3_yr": 6.0e7 }
}
```

**Tiling rule of thumb:** a 50 × 50 km city at 10 m ≈ 5000² px. Do **not** ship a single raster:
a **z8–z14** tile pyramid, with velocity quantised to `int16` using `range` from `meta.json`.

<a id="en-7"></a>
## 7. Methodology pipeline

```
P0  Scope       AOI, EPSG:326xx, reference pixel
P1  Ingest      asf_search → GUNW / OPERA DISP-S1 / HyP3 / EGMS (record provenance)
P2  Interfero   only if unavoidable: ISCE2 + SNAPHU
P3  Correct     troposphere (RAiDER/GACOS), DEM error (MintPy), orbital ramps
P4  Series      network inversion → cumulative displacement cube
P5  Decompose   LOS → vertical (asc+desc, v_north≈0; else assume pure vertical)
P6  Separate    trend + seasonal harmonics + step terms → secular vs seasonal
P7  Metrics     velocity, |∇v|, compaction volume, S* = Δb/Δh
P8  Context     sjoin with buildings, population, reservoirs, groundwater head
P9  Emit        parquet/COG/PMTiles + series JSON + meta.json  ← contract
```

**The two products and their HDF5 paths:**

- **GCOV** (flooding, moisture, vegetation):
  `/science/LSAR/GCOV/grids/frequencyA/HHHH` and `.../HVHV`
- **GUNW** (subsidence, deformation):
  `/science/LSAR/GUNW/grids/frequencyA/interferogram/unwrappedPhase` and `coherenceMagnitude`

<a id="en-8"></a>
## 8. Equations

**Phase → LOS displacement** (L-band: λ = 240 mm, Δφ in radians):

```
d_LOS (mm) = −(λ / 4π) · Δφ
```

**LOS projection** (θ incidence, α heading):

```
d_LOS = u · [v_up, v_east, v_north]ᵀ ,   u = [cosθ, −sinθ·cosα, −sinθ·sinα]
```

Two geometries (asc + desc) → 2 equations, 3 unknowns → assume `v_north ≈ 0` or constrain with
GNSS. **Sign conventions differ between ISCE2, MintPy and SNAP — validate against GNSS before
publishing numbers.**

**Backscatter and change:**

```
σ⁰_dB = 10 · log₁₀(linear covariance)
Δσ⁰   = σ⁰_dB(T₂) − σ⁰_dB(T₁)
CV    = σ / μ            (multitemporal: highlights change while ignoring speckle)
```

**Quality filter:** discard pixels with `coherenceMagnitude < 0.35`.

**Time-series model:**

```
v(t) = v₀ + ṫ·t + Σₖ [Aₖcos(2πkt/T) + Bₖsin(2πkt/T)] + Σⱼ Δᵥⱼ·H(t−tⱼ)
```

`ṫ` → inelastic/permanent · `A_seasonal` → elastic/recoverable · `R = |ṫ| / A_seasonal`.

**Storage loss:**

```
S_skv = Δb / (b₀·Δh)              skeletal specific storage
dV/dt = Σ_px v_up · A_px          compaction-driven storage loss [m³/yr]
```

<a id="en-9"></a>
## 9. Stack & requirements

### 9.1. Feature → data → technology matrix

| Feature | Dataset | Libraries | User value |
|---|---|---|---|
| Discover & download | NISAR L2 (`GCOV`/`GUNW`), DISP-S1, EGMS | `asf_search`, `earthaccess` | Reproducible official access |
| Extract & clip | HDF5 groups `/science/LSAR/...` | `h5py`, `xarray`, `rioxarray`, `rasterio` | Reduces GB to light tensors |
| Compute anomalies | Phase, coherence, backscatter | `numpy`, `pandas`, `h3`, `scipy` | Removes noise, yields indicators |
| GIS enrichment | OSM, CORINE, WorldCover | `geopandas`, `shapely`, QGIS/Overpass | Joins anomaly to real territory |
| 2D/3D visualization | `nisar_grid.parquet`, `.geojson` | `streamlit`, `pydeck`, `folium` | Interactive exploration |
| Time series | `timeseries.csv` | `plotly` | Rate of change and correlation |

### 9.2. `requirements.txt` (Option A)

```text
# Core & web framework
streamlit>=1.38.0
pandas>=2.2.0
numpy>=1.26.0
pyarrow>=16.0.0

# Interactive visualization and maps
pydeck>=0.9.1
plotly>=5.22.0
folium>=0.17.0
streamlit-folium>=0.22.0

# Lightweight geospatial (vector and grids)
geopandas>=1.0.0
shapely>=2.0.0
h3>=3.7.7

# SAR ingest and processing (offline scripts)
asf_search>=7.1.0
earthaccess>=0.9.0
h5py>=3.11.0
xarray>=2024.6.0
rioxarray>=0.17.0
rasterio>=1.3.10
```

**If migrating to Option B**, add: `pmtiles`, `maplibre`/`deck.gl` (JS), `Vite` + TypeScript.
**Optional processing:** `isce2` (`conda install -c conda-forge isce2`), `snaphu`, `MintPy`,
`hyp3_sdk`, `opera-utils`, `RAiDER`.

### 9.3. Per-member prerequisites

| Tool | yuls | pozo | fradiaz |
|---|---|---|---|
| NASA Earthdata account | Required | Optional | Optional |
| Python 3.11 + Git + VS Code | Required | Required | Required |
| Desktop QGIS | Required | No | Recommended |
| Streamlit Cloud / hosting | No | Required | Recommended |

<a id="en-10"></a>
## 10. Roles & labour split

| Member | Role | Owns | Ships |
|---|---|---|---|
| **yuls** | SAR data & geospatial visualization | Ingest, InSAR, metrics, GIS context, cartography, colour scales, narrative | `pipeline/`, all artefacts, layer styles |
| **pozo** | Backend & infrastructure | Artefact serving, orchestration, CI/CD, caching, deployment, route contract | `serve/`, `deploy/`, CI, publishing |
| **fradiaz** | Frontend & interaction | App shell, map, slider, charts, URL state, accessibility | `app.py`, `ui_components/`, the 5 views |

### 10.1. Detailed tasks

**yuls — SAR data & geovisualization**
1. `01_generate_mock.py` in the first 30 min: synthetic `nisar_grid.parquet` and `timeseries.csv`.
2. Catalogue via `asf_search`; download NISAR L2 / DISP-S1 / EGMS with `earthaccess`.
3. HDF5 tree via `h5py`; extraction with `xarray`/`rioxarray`; AOI clip.
4. Physical conversion (dB or mm LOS), coherence filter, resample to H3.
5. Spatial join `geopandas.sjoin` → `land_use` and `risk_level`.
6. Colour scales, including the **global shared legend** that flattens Málaga.
7. Document `/pipeline/` so judges can verify scientific rigour.

**pozo — backend & infrastructure**
1. Static hosting/CDN and cache headers; *range request* support if PMTiles are used.
2. Orchestration: run `pipeline/` in CI and publish artefacts on success.
3. Define and validate the **route contract**.
4. Deployment and uptime: "does the demo URL work at 4 a.m.?"

**fradiaz — frontend & interaction**
1. Init repo, visual theme (`.streamlit/config.toml`, space-dark), deploy the skeleton.
2. Sidebar: case selector, date slider, critical threshold, coherence and land-use filters.
3. 3D map in **PyDeck** (`H3HexagonLayer`/`ColumnLayer`): height = `abs(delta_change)`, colour dynamic by threshold.
4. **Plotly** time series (Jul–Nov 2026) with alert bands.
5. Reactive KPI (`st.metric`), URL state, accessibility.

### 10.2. Route contract (Option B)

```
GET /{city}/meta.json            GET /{city}/vel.pmtiles
GET /{city}/bs/{date}.pmtiles    GET /{city}/events.pmtiles
GET /{city}/series/{z}/{x}/{y}.json
```

### 10.3. Non-overlaps
- **yuls** writes no UI code (layer styles are JSON config).
- **pozo** touches no scientific parameters (thresholds, colour ranges).
- **fradiaz** computes no geospatial quantity client-side: if a number isn't in an artefact, it
  doesn't exist.

<a id="en-11"></a>
## 11. MVP scope

**IN SCOPE**
- One primary study case executed perfectly (+ one pre-loaded second site for scalability).
- Documented Python pipeline in `/pipeline/` proving real use of NISAR L2 products (or the
  fallback sources declared in §4).
- Interactive dashboard: reactive KPI, 3D/2D map, time series, educational tab.

**OUT OF SCOPE — do not spend time here**
- **Live** HDF5 download and processing on an arbitrary coordinate click.
- Raw InSAR from L1 (`RSLC`) with `isce3`/ISCE2 when L2/OPERA exist.
- User authentication, PostgreSQL/PostGIS, a separate FastAPI/React backend.

**Cut lines** (if short on time, in order): change events → third city → everything except Málaga
via EGMS. **Never cut:** traceability (`provenance`).

<a id="en-12"></a>
## 12. 48 h schedule

**Block 1 — Sync & unblock (Sat 15:00–17:30)**
- *All, 20 min:* final case decision and the 4 AOI coordinates.
- **yuls:** generate and push the mock parquet; configure Earthdata credentials; start the search.
- **fradiaz:** `app.py` skeleton reading the mock; verify PyDeck + Plotly locally.
- **pozo:** hosting, minimal CI, route contract frozen.

**Block 2 — Parallel core build (Sat 17:30–22:30)**
- **yuls:** first real HDF5, clip, physical conversion, value distribution.
- **fradiaz:** full reactivity (slider → KPI, hexagons, Plotly threshold line).
- **pozo:** automatic artefact publishing; size audit.

**Block 3 — Real data & calibration (Sun 09:00–13:30)**
- **yuls + fradiaz:** integrate final parquet and series.
- **fradiaz:** camera (`pitch=45`), vertical scale, public deployment.
- **pozo:** cache headers, load test < 3 s.

**Block 4 — Freeze, docs & pitch (Sun 14:30–19:00)**
- **16:00 — CODE FREEZE.** Minor visual fixes only.
- **yuls:** document `/pipeline/` + screenshot of the processed HDF5 tree.
- **pozo:** final production verification.
- **All:** final README and video (20 s problem · 90 s demo · 30 s architecture and impact).

<a id="en-13"></a>
## 13. Validation & limitations

| Check | Method |
|---|---|
| Velocity accuracy | GNSS / levelling; target < 5 mm/yr RMSE |
| Unwrapping errors | closure phase on redundant triplets |
| Atmosphere | compare against GACOS/ERA5-corrected run |
| Flood masks | Copernicus EMS activation maps |
| Null result (Málaga) | detection-limit analysis — publish the upper bound |

**Error budget (largest to smallest):** unwrapping ≫ troposphere ≫ orbital ≫ DEM ≫ noise.

**Limitations**
1. Málaga may be at the detection floor: the bound **is** the result.
2. Mexico City is not coastal: don't claim a coastal framing across all three.
3. NISAR urban coverage **not guaranteed** (§4).
4. GUNW posting is 80 m: city scale, not building scale.
5. Urban flood mapping in SAR is confounded by double-bounce and shadow: use change detection and
   coherence loss, not simple thresholding.
6. Coherence depends on revisit interval: **NISAR 12-day ≠ Sentinel-1 6-day**. Never pool
   thresholds across sensors.

<a id="en-14"></a>
## 14. Repository layout

```text
nisar-spaceapps-2026/
├── README.md                  ← documentation and pitch
├── informe_readme.md          ← this document
├── requirements.txt           ← shared (configured in hour 1)
├── app.py                     ← yuls/fradiaz: main application
├── ui_components/             ← fradiaz
│   ├── map_view.py
│   ├── charts.py
│   └── kpi_cards.py
├── pipeline/                  ← yuls
│   ├── 01_generate_mock.py    ← minute-30 priority: unblocks the team
│   ├── 02_search_download.py
│   ├── 03_process_nisar_hdf5.py
│   └── events/                ← change detection
├── serve/  deploy/            ← pozo
├── gis_context/               ← yuls
│   ├── enrich_spatial.py
│   └── content_texts.py
└── data/
    ├── raw/                   ← .gitignore (heavy HDF5)
    └── processed/             ← meeting point (< 15 MB in Git)
        ├── nisar_grid.parquet
        ├── timeseries.csv
        ├── context_layers.geojson
        └── meta.json
```

<a id="en-15"></a>
## 15. Sources & references

**Data sources** (`✓` = verified live): ASF NISAR (`asf.alaska.edu/datasets/daac/nisar` ✓) ·
NISAR DUG (`nisar-docs.asf.alaska.edu` ✓) · ASF Vertex (`search.asf.alaska.edu` ✓) ·
HyP3 (`hyp3-docs.asf.alaska.edu` ✓) · OPERA DISP-S1 (`displacement.asf.alaska.edu` ✓) ·
EGMS (`egms.land.copernicus.eu` ✓) · GRACE-FO at PO.DAAC ✓ · GLDAS ✓ · IGRAC GGIS ✓ ·
USGS NWIS API ✓ · GHSL ✓ · WorldPop ✓ · DLR WSF 2019 ✓ · Copernicus EMS ✓

| Topic | Cite | DOI |
|---|---|---|
| Global subsidence threat | Herrera-García et al. 2021, *Science* | 10.1126/science.abb8549 |
| Subsidence → storage loss | Hasan et al. 2023, *Nat Commun* | 10.1038/s41467-023-41933-z |
| Mexico City −45 cm/yr | Cigna & Tapete 2022, *GRL* | 10.1029/2022gl098923 |
| 99 coastal cities, uniform PS-InSAR | Wu et al. 2022, *GRL* | 10.1029/2022gl098477 |
| Methodological review | Galloway & Burbey 2011, *Hydrogeol J* | 10.1007/s10040-011-0775-5 |
| Sinking cities (Indonesia) | Chaussard et al. 2013, *RSE* | 10.1016/j.rse.2012.10.015 |
| Jakarta 2017–22, multi-track SBAS | 2024, *Geocarto Int* | 10.1080/10106049.2024.2364726 |
| Jakarta: piped water → abstraction | 2022, *Water* | 10.3390/w14203197 |
| Málaga/Torremolinos | Ruiz-Constán et al. 2018, *STOTEN* | 10.1016/j.scitotenv.2018.04.280 |
| Axarquía water crisis | Junquera et al. 2025, *PNAS* | 10.1073/pnas.2508055122 |
| NISAR Data User Guide | Kristenson et al. 2026 | 10.5281/ZENODO.20075813 |

*Jakarta rates vary by method and period: quote the source used in `provenance`.*
