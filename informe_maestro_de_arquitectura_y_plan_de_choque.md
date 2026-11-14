# Documento Maestro de Arquitectura, Análisis Geoespacial y Plan de Ejecución MVP

**Desafío:** NASA Space Apps Challenge 2026 — *"Dancing with the SARs"*  
**Misión:** NASA-ISRO Synthetic Aperture Radar (NISAR)  
**Equipo:** 3 Integrantes | **Modalidad:** Dedicación Completa (Sábado a Domingo)  
**Objetivo del Sistema:** Traducir observaciones complejas de radar de apertura sintética (SAR) en banda L en una aplicación interactiva de código abierto para rastrear, cuantificar y visualizar cambios críticos en la superficie terrestre.

---

## 1. Contexto de la Misión y Justificación Científica

### 1.1. El Diferencial de la Misión NISAR (Banda L vs. Radares Tradicionales)
La misión conjunta entre la NASA y la ISRO proporciona datos calibrados de radar de apertura sintética (disponibles públicamente desde julio de 2026 a través de ASF DAAC y NASA Earthdata). Su ventaja competitiva fundamental reside en operar en **Banda L ($\lambda \approx 24\text{ cm}$, frecuencia de $1.25\text{ GHz}$)**:

* **Penetración del dosel vegetal:** A diferencia de los radares en Banda C (como Sentinel-1, con $\lambda \approx 5.6\text{ cm}$) que rebotan en las hojas superiores de los árboles o cultivos, la onda de $24\text{ cm}$ de NISAR penetra la vegetación hasta llegar al suelo o a la lámina de agua subyacente.
* **Alta coherencia interferométrica temporal:** En zonas agrícolas o boscosas, los radares tradicionales sufren "decorrelación temporal" en pocos días porque el viento o el crecimiento de las plantas alteran la fase. La Banda L mantiene la coherencia entre pasadas orbitales (ciclos de 12 días), permitiendo medir deformaciones milimétricas del terreno donde antes había puro ruido.
* **Independencia meteorológica y solar:** Opera día y noche atravesando nubes densas, humo de incendios y tormentas, garantizando series temporales sin huecos.

### 1.2. Público Objetivo de la Solución
La plataforma está diseñada bajo un enfoque de **traducción de complejidad**:
1. **Gestores de recursos hídricos y protección civil:** Necesitan alertas tempranas expresadas en hectáreas afectadas o milímetros de hundimiento sobre infraestructuras, no en decibelios ni radianes.
2. **Comunidad científica y técnica:** Requieren trazabilidad sobre qué producto NISAR se utilizó (`GCOV` o `GUNW`), el umbral de coherencia aplicado y acceso al código reproducible.
3. **Educadores y público general:** Necesitan entender visualmente mediante comparadores "antes/después" y gráficos claros cómo el radar ve lo que el ojo humano y los satélites ópticos no pueden ver.

---

## 2. Selección del Estudio de Caso (Opciones Más Viables)

Para maximizar la puntuación en la rúbrica del jurado de la NASA y asegurar la viabilidad técnica en un fin de semana, el equipo debe elegir **un único caso de estudio principal** entre las tres opciones de mayor retorno visual y científico:

| Criterio | Opción A: Subsidencia por Acuíferos e Infraestructura (Altamente Recomendada) | Opción B: Inundaciones Ocultas bajo Vegetación y Pérdida de Humedales | Opción C: Dinámica Agrícola y Humedad del Suelo |
| :--- | :--- | :--- | :--- |
| **Ubicación Geográfica Sugerida** | Cuenca del Guadalquivir / Entorno de Doñana o Alto Guadalentín (Murcia, España) | Humedales de Doñana (España) o Pantanal / Cuenca Amazónica | Valle del Guadalquivir (España) o Llanura Padana (Italia) |
| **Producto NISAR Principal** | **L2 GUNW** (*Geocoded Unwrapped Interferogram*) | **L2 GCOV** (*Geocoded Polarimetric Covariance*) | **L2 GCOV** (Términos diagonales `HHHH` y `HVHV`) |
| **Justificación Física con NISAR** | La extracción intensiva de agua subterránea hunde el terreno. La Banda L mide esta subsidencia incluso debajo de campos agrícolas en crecimiento. | Detecta el efecto de "doble rebote" (*double-bounce*) cuando el agua inunda zonas arboladas o marismas cubiertas de nubes y vegetación. | La polarización cruzada ($HV$) responde al volumen del cultivo, mientras que $HH$ responde a la rugosidad y humedad dieléctrica del suelo. |
| **Fórmula / Algoritmo de Cambio** | Conversión directa de fase desenrollada ($\Delta \phi$) a desplazamiento en milímetros. | Diferencial de retrodispersión ($\Delta \sigma^0$ en dB) entre fecha seca ($T_1$) y fecha húmeda ($T_2$). | Coeficiente de Variación temporal ($CV = \frac{\sigma}{\mu}$) o Índice de Vegetación Radar ($RVI$). |
| **Dificultad de Implementación** | **Baja/Media:** El producto L2 GUNW ya entrega la fase desenrollada y geocodificada; ahorra el paso más complejo de InSAR. | **Baja:** Álgebra de matrices directa sobre las bandas de potencia geocodificadas. | **Media:** Requiere procesar múltiples fechas entre julio y noviembre de 2026 para extraer tendencias. |
| **Impacto Visual en la Demo** | **Muy Alto:** Malla 3D mostrando hundimientos críticos sobre carreteras, canales, vías férreas o pozos. | **Alto:** Mapa comparador de cortina revelando láminas de agua invisibles en imágenes ópticas Sentinel-2. | **Medio/Alto:** Dashboard analítico de estrés hídrico por parcela agrícola. |

---

## 3. Arquitectura de la Aplicación

El mayor riesgo arquitectónico en este desafío es intentar descargar y procesar archivos HDF5 de NISAR (que pesan entre $1\text{ GB}$ y $6\text{ GB}$ por escena) en tiempo real dentro de la aplicación web. Por ello, se define una **Arquitectura Desacoplada de Pre-procesamiento Híbrido (90% ETL Offline / 10% Inferencia Ligera en Memoria)**.

### 3.1. Opciones de Arquitectura Técnica para el MVP

* **Opción 1 (RECOMENDADA): Pre-cálculo Híbrido + Malla Vectorial/Hexagonal H3 (`GeoParquet`) + PyDeck 3D**
  * **Funcionamiento:** Un script offline recorta el HDF5 de NISAR sobre el área de estudio (AOI), calcula el cambio físico y agrega los píxeles válidos en una malla hexagonal (Uber H3) o cuadrícula de puntos exportada a un archivo `.parquet` (< $10\text{ MB}$). Streamlit carga este archivo en milisegundos y **PyDeck** lo renderiza en 3D acelerado por GPU.
  * **Ventaja decisiva:** Rendimiento instantáneo, cero caídas de memoria en servidores gratuitos y facilidad total para cruzar cada hexágono con datos de carreteras, edificios o usos del suelo.
* **Opción 2 (COMPLEMENTARIA): Visor Raster de Cortina (*Split-Map*) con Recortes GeoTIFF / PNG**
  * **Funcionamiento:** Exportar los recortes del área de estudio como **Cloud Optimized GeoTIFF (COG)** o imágenes PNG georreferenciadas para $T_1$ y $T_2$, visualizándolas con **Leafmap / Folium** mediante una cortina deslizante sobre un mapa base óptico.
  * **Recomendación de diseño:** Usar la **Opción 1** como motor interactivo principal (para filtrar por umbrales en tiempo real y ver gráficos) e incluir una vista o pestaña con la **Opción 2** para mostrar la textura original del radar.

### 3.2. Diagrama del Flujo de Datos (Pipeline End-to-End)

```
+-----------------------------------------------------------------------------------+
| CAPA 1: INGESTA Y PROCESAMIENTO OFFLINE (Ejecutado por Rol 1 en máquina local)    |
|                                                                                   |
|  [Catálogo ASF DAAC / NASA Earthdata]                                             |
|         | 1. Búsqueda espacial/temporal vía API (asf_search + earthaccess)        |
|         v                                                                         |
|  [Archivos NISAR L2 HDF5 (GCOV / GUNW)] + [Capas GIS Auxiliares OSM (Rol 3)]      |
|         | 2. Apertura selectiva del árbol HDF5 (h5py + xarray + rioxarray)        |
|         | 3. Recorte espacial por Bounding Box (AOI) y reproyección a EPSG:4326   |
|         v                                                                         |
|  [Motor de Cálculo Físico y Limpieza]                                             |
|         | 4. Enmascaramiento de ruido (Coherencia < 0.35 o bordes sin datos)      |
|         | 5. Cálculo de cambio (mm de subsidencia en GUNW o delta dB en GCOV)     |
|         | 6. Cruce espacial (geopandas.sjoin) con infraestructuras/usos de suelo  |
|         v                                                                         |
|  ==================== CONTRATO DE DATOS (/data/processed/) =====================  |
|   - nisar_grid.parquet (Malla con coordenadas, valores SAR, coherencia y uso)     |
|   - timeseries.csv (Serie temporal agregada julio-noviembre 2026)                 |
|   - context_layers.geojson (Polígonos de referencia e infraestructuras críticas)  |
+-----------------------------------------------------------------------------------+
                                          |
                                          v (Carga en caché RAM con @st.cache_data)
+-----------------------------------------------------------------------------------+
| CAPA 2: APLICACIÓN WEB INTERACTIVA (Construida por Rol 2 en Streamlit)            |
|                                                                                   |
|  +-----------------------+  +--------------------------------------------------+  |
|  | Barra Lateral (UI)    |  | Fila Superior: 4 Tarjetas KPI Dinámicas          |  |
|  | - Selector de Caso    |  | (Área afectada, Cambio máx., Activos en riesgo)  |  |
|  | - Slider de Fechas    |  +-------------------------------+------------------+  |
|  | - Slider de Umbral    |  | Visor Cartográfico 2D/3D      | Serie Temporal   |  |
|  |   Crítico (dB o mm)   |  | (PyDeck H3 / Folium Split)    | Interactiva      |  |
|  | - Filtro de Coherencia|  | Elevación y color dinámicos   | (Plotly Express) |  |
|  | - Filtro Uso de Suelo |  +-------------------------------+------------------+  |
|  |                       |  | Módulo Educativo: Física de NISAR y Metodología  |  |
|  +-----------------------+  +--------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

### 3.3. Especificación de Algoritmos del Backend (Física del Radar)

1. **Para el producto NISAR L2 GCOV (Inundaciones, Humedad, Vegetación):**
   * **Ruta HDF5:** `/science/LSAR/GCOV/grids/frequencyA/HHHH` y `/science/LSAR/GCOV/grids/frequencyA/HVHV`.
   * **Conversión a Decibelios (dB):**
     $$\sigma^0_{\text{dB}} = 10 \cdot \log_{10}(\text{Covarianza Lineal})$$
   * **Detección de Cambio Dual ($T_1 \rightarrow T_2$):**
     $$\Delta \sigma^0 = \sigma^0_{\text{dB}}(T_2) - \sigma^0_{\text{dB}}(T_1)$$
   * **Detección Multitemporal (Serie de $N$ imágenes desde julio 2026):** Coeficiente de Variación ($CV = \frac{\sigma}{\mu}$) o Diferencia Interpercentil ($P_{90} - P_{10}$) para resaltar píxeles que han sufrido alteraciones drásticas ignorando el ruido *speckle*.

2. **Para el producto NISAR L2 GUNW (Subsidencia y Deformación del Terreno):**
   * **Ruta HDF5:** `/science/LSAR/GUNW/grids/frequencyA/interferogram/unwrappedPhase` y `coherenceMagnitude`.
   * **Filtro de Calidad:** Descartar todo píxel donde $\text{coherenceMagnitude} < 0.35$.
   * **Conversión de Fase a Desplazamiento en Línea de Visión (LOS):**
     $$d_{\text{LOS}}\text{ (mm)} = -\frac{\lambda}{4\pi} \cdot \Delta \phi$$
     Donde $\lambda = 240\text{ mm}$ para la Banda L de NISAR y $\Delta \phi$ está expresado en radianes.

---

## 4. Tecnologías, Recursos y Prerrequisitos

### 4.1. Matriz Relacional de Funcionalidades, Datos y Tecnologías

| Funcionalidad del Sistema | Dataset / Recurso Exacto | Librerías y Herramientas | Valor Aportado al Usuario Final |
| :--- | :--- | :--- | :--- |
| **Descubrimiento y Descarga de Escenas** | Catálogo NISAR L2 (`GCOV` / `GUNW`) en ASF DAAC | `asf_search`, `earthaccess` | Acceso programático oficial y reproducible a los datos abiertos de la NASA. |
| **Extracción Jerárquica y Recorte (Clip)** | Grupos internos HDF5 (`/science/LSAR/...`) | `h5py`, `xarray`, `rioxarray`, `rasterio` | Reduce archivos de varios gigabytes a tensores ligeros del área de estudio. |
| **Cálculo de Anomalías y Filtrado** | Matrices de fase desenrollada, coherencia y retrodispersión | `numpy`, `pandas`, `h3` | Elimina ruido radar y convierte señales físicas complejas en indicadores claros. |
| **Enriquecimiento Contextual (GIS)** | Infraestructuras de OpenStreetMap, uso de suelo (CORINE / WorldCover) | `geopandas`, `shapely`, QGIS / Overpass Turbo | Cruza la anomalía satelital con carreteras, cultivos o zonas urbanas reales. |
| **Visualización Geoespacial 2D y 3D** | Archivo consolidado `nisar_grid.parquet` y capas `.geojson` | `streamlit`, `pydeck`, `folium`, `streamlit-folium` | Permite explorar interactivamente dónde y cuánto está cambiando el terreno. |
| **Análisis de Series Temporales** | Tabla agregada `timeseries.csv` (julio - noviembre 2026) | `plotly` (`plotly.express` y `graph_objects`) | Muestra la velocidad del cambio y permite correlacionarlo con eventos climáticos. |

### 4.2. Conocimientos e Infraestructura Requerida

* **Habilidades distribuidas en el equipo:**
  1. Manipulación de arrays multidimensionales y datos raster en Python (`xarray`/`numpy`).
  2. Desarrollo de interfaces de datos con `Streamlit` y gráficas interactivas (`PyDeck`/`Plotly`).
  3. Sistemas de Información Geográfica (GIS básicos: proyecciones `EPSG:4326`, archivos GeoJSON) y comunicación científica/storytelling.
* **Infraestructura:**
  * **Procesamiento:** **100% Local** (o Google Colab si la conexión a internet local es lenta para descargar el HDF5 inicial). Al recortar el *Bounding Box*, el consumo de RAM es mínimo.
  * **Alojamiento Web (Deployment):** **Streamlit Community Cloud** (gratuito, conectado directamente a la rama `main` de GitHub) o contenedor en **Hugging Face Spaces / Google Cloud Run**.

### 4.3. Prerrequisitos de Software por Integrante

| Herramienta / Cuenta | Rol 1 (Datos SAR) | Rol 2 (Frontend Web) | Rol 3 (GIS & Producto) | Propósito |
| :--- | :--- | :--- | :--- | :--- |
| **Cuenta NASA Earthdata** | Obligatorio | Opcional | Opcional | Autenticación con `earthaccess` para leer datos de ASF DAAC. |
| **Python 3.11 + Git + VS Code** | Obligatorio | Obligatorio | Recomendado | Desarrollo colaborativo y control de versiones en GitHub. |
| **QGIS (Escritorio)** | Recomendado | No necesario | Obligatorio | Inspeccionar recortes GeoTIFF y preparar capas vectoriales GeoJSON. |
| **Cuenta Streamlit Cloud** | No necesario | Obligatorio | No necesario | Publicación de la app con URL accesible para los jueces. |

### 4.4. Archivo `requirements.txt` Unificado (Listo para Producción)

```text
# Core & Web Framework
streamlit>=1.38.0
pandas>=2.2.0
numpy>=1.26.0
pyarrow>=16.0.0

# Visualización Interactiva y Mapas
pydeck>=0.9.1
plotly>=5.22.0
folium>=0.17.0
streamlit-folium>=0.22.0

# Geoespacial Ligero (Vectorial y Mallas)
geopandas>=1.0.0
shapely>=2.0.0
h3>=3.7.7

# Ingesta y Procesamiento SAR (Usado en scripts offline)
asf_search>=7.1.0
earthaccess>=0.9.0
h5py>=3.11.0
xarray>=2024.6.0
rioxarray>=0.17.0
rasterio>=1.3.10
```

---

## 5. Alcance del MVP y Separación de Responsabilidades

### 5.1. Qué Entra y Qué Queda Fuera del MVP (Gestión del Tiempo en 48h)

* **IMPRESCINDIBLE (In-Scope):**
  * **1 caso de estudio principal perfectamente ejecutado** (y opcionalmente una segunda ubicación pre-cargada para demostrar escalabilidad global).
  * Pipeline en Python documentado dentro de `/pipeline/` que demuestre el uso real de productos NISAR L2 (`GCOV` o `GUNW`).
  * Dashboard interactivo en Streamlit con: tarjetas KPI superiores que responden a los filtros, mapa 3D/2D interactivo, gráfico de serie temporal en Plotly y pestaña divulgativa sobre la misión NISAR.
* **DESCARTADO (Out-of-Scope — Prohibido invertir tiempo aquí):**
  * Descarga y procesamiento en vivo de archivos HDF5 cuando el usuario hace clic en cualquier coordenada arbitraria del mundo (demasiado lento; rompería la aplicación en la demo).
  * Procesamiento interferométrico crudo desde nivel L1 (`RSLC`) usando `isce3` (complejidad innecesaria existiendo los productos L2 `GCOV` y `GUNW` ya geocodificados).
  * Autenticación de usuarios, bases de datos PostgreSQL/PostGIS dedicadas o separar el backend en FastAPI/React.

### 5.2. Estructura del Repositorio GitHub (Cero Conflictos de Merge)

Cada integrante trabaja en su propio directorio para no sobrescribir el código de los demás:

```text
nisar-spaceapps-2026/
├── README.md                  <-- PROPIEDAD DEL ROL 3 (Documentación, Arquitectura y Pitch)
├── requirements.txt           <-- Compartido (Configurado en la hora 1)
├── app.py                     <-- PROPIEDAD DEL ROL 2 (Aplicación principal Streamlit)
├── ui_components/             <-- PROPIEDAD DEL ROL 2 (Módulos visuales)
│   ├── map_view.py
│   ├── charts.py
│   └── kpi_cards.py
├── pipeline/                  <-- PROPIEDAD DEL ROL 1 (Scripts de ciencia de datos SAR)
│   ├── 01_generate_mock.py    <-- Prioridad minuto 30: genera datos simulados para desbloquear al equipo
│   ├── 02_search_download.py
│   └── 03_process_nisar_hdf5.py
├── gis_context/               <-- PROPIEDAD DEL ROL 3 (Capas auxiliares y narrativa)
│   ├── enrich_spatial.py
│   └── content_texts.py
└── data/
    ├── raw/                   <-- (Añadir a .gitignore - Archivos HDF5 pesados locales)
    └── processed/             <-- PUNTO DE ENCUENTRO (Archivos ligeros < 15 MB en GitHub)
        ├── nisar_grid.parquet
        ├── timeseries.csv
        └── context_layers.geojson
```

### 5.3. El "Contrato de Datos" (Coordinación sin Bloqueos)

Para que el **Rol 2** y el **Rol 3** trabajen desde el primer minuto sin esperar a que el **Rol 1** descargue y procese los datos de la NASA, los tres acuerdan que el archivo `data/processed/nisar_grid.parquet` tendrá exactamente este esquema (generado primero con datos simulados en el minuto 30 y reemplazado el domingo por datos reales):

| Columna | Tipo | Descripción Técnica | Responsable |
| :--- | :--- | :--- | :--- |
| `cell_id` | `string` | Índice hexagonal H3 (resolución 8 o 9) o ID único de celda | Rol 1 |
| `lat` | `float` | Latitud del centroide en `EPSG:4326` | Rol 1 |
| `lon` | `float` | Longitud del centroide en `EPSG:4326` | Rol 1 |
| `val_t1` | `float` | Valor base en Fecha 1 ($\sigma^0$ en dB o referencia $0\text{ mm}$) | Rol 1 |
| `val_t2` | `float` | Valor observado en Fecha 2 ($\sigma^0$ en dB o desplazamiento en $\text{mm}$) | Rol 1 |
| `delta_change` | `float` | Magnitud del cambio neto ($T_2 - T_1$) | Rol 1 |
| `coherence` | `float` | Coherencia interferométrica o índice de calidad ($0.0$ a $1.0$) | Rol 1 |
| `land_use` | `string` | Categoría territorial ("Regadío", "Vía Férrea", "Carretera", "Humedal", "Urbano") | Rol 3 |
| `risk_level` | `string` | Nivel de alerta pre-clasificado ("Estable", "Moderado", "Crítico") | Rol 1 + Rol 3 |

### 5.4. Asignación Detallada por Rol

#### 👤 ROL 1: Ingeniero de Datos SAR & Backend Geoespacial
* **Responsabilidad Principal:** Domar los productos HDF5 de NISAR, aplicar las ecuaciones físicas de radar y generar los datasets ligeros optimizados.
* **Lista de Tareas:**
  1. Ejecutar `01_generate_mock.py` en la primera media hora para crear un `nisar_grid.parquet` y `timeseries.csv` sintéticos sobre las coordenadas elegidas.
  2. Consultar el catálogo con `asf_search` y descargar escenas NISAR L2 (`GCOV` o `GUNW`) con `earthaccess`.
  3. Inspeccionar el árbol HDF5 con `h5py`, extraer las matrices con `xarray`/`rioxarray` y recortar el *Bounding Box*.
  4. Calcular la conversión física (dB o milímetros de desplazamiento LOS), filtrar por coherencia y muestrear a tabla de coordenadas/hexágonos H3.
  5. Documentar el código en `/pipeline/` para que el jurado verifique la validez científica del procesamiento.

#### 👤 ROL 2: Desarrollador Frontend & Visualización Interactiva
* **Responsabilidad Principal:** Construir una experiencia de usuario impecable, rápida y autoexplicativa en Streamlit.
* **Lista de Tareas:**
  1. Inicializar el repositorio, configurar el tema visual (`.streamlit/config.toml` en modo oscuro espacial) y desplegar el esqueleto en *Streamlit Community Cloud*.
  2. Construir la barra lateral (`st.sidebar`) con controles interactivos: selector de variable, *slider* de umbral crítico de cambio y filtro por tipo de uso de suelo (`land_use`).
  3. Implementar el mapa 3D con **PyDeck** (`H3HexagonLayer` o `ColumnLayer`) donde la altura de cada columna represente `abs(delta_change)` y el color cambie dinámicamente según el umbral elegido por el usuario.
  4. Construir la gráfica de serie temporal en **Plotly** mostrando la tendencia entre julio y noviembre de 2026 junto con bandas de alerta.

#### 👤 ROL 3: Arquitecto de Producto, GIS Contextual & Storytelling
* **Responsabilidad Principal:** Conectar las anomalías del radar con el impacto real en la sociedad/ecosistema y liderar la presentación ante el jurado.
* **Lista de Tareas:**
  1. Delimitar el *Bounding Box* exacto del caso de estudio y extraer de **OpenStreetMap (Overpass Turbo)** o fuentes oficiales las capas vectoriales de infraestructuras, parcelas o humedales (`.geojson`).
  2. Programar el cruce espacial (`geopandas.sjoin`) para etiquetar cada punto de NISAR con su `land_use` correspondiente.
  3. Redactar los textos divulgativos integrados en la app (explicando de forma visual cómo la Banda L de $24\text{ cm}$ penetra la vegetación) y diseñar los diagramas del proyecto.
  4. Redactar el `README.md` siguiendo los criterios de evaluación de NASA Space Apps y producir el vídeo/presentación final de 3 minutos.

---

## 6. Cronograma Táctico de Ejecución (Sábado Tarde a Domingo Noche)

### Bloque 1: Sincronización y Desbloqueo (Sábado 15:00 - 17:30)
* **[Todo el Equipo - 20 min]** Decisión final del Caso de Estudio (Opción A: Subsidencia con `GUNW` u Opción B: Inundaciones con `GCOV`) y fijación de las 4 coordenadas del *Bounding Box*.
* **[Rol 1]** Genera y sube a GitHub el archivo `nisar_grid.parquet` simulado (*mock*). Configura credenciales de NASA Earthdata y lanza la búsqueda de archivos HDF5 reales.
* **[Rol 2]** Levanta el esqueleto de `app.py` leyendo el archivo *mock* y verifica que el mapa PyDeck y Plotly se renderizan en local.
* **[Rol 3]** Extrae los polígonos `.geojson` de carreteras, cultivos o humedales del área de estudio mediante Overpass Turbo / QGIS.

### Bloque 2: Construcción del Núcleo en Paralelo (Sábado 17:30 - 22:30)
* **[Rol 1]** Abre el primer archivo HDF5 real de NISAR L2, recorta el área de estudio, aplica la fórmula de conversión física y verifica la distribución de los datos (máximos, mínimos, valores nulos).
* **[Rol 2]** Programa la reactividad completa del dashboard: al mover el *slider* de umbral, se actualizan en vivo las 4 tarjetas KPI superiores (`st.metric`), se filtran los hexágonos en el mapa 3D y se ajusta la línea de umbral en Plotly.
* **[Rol 3]** Desarrolla el script `enrich_spatial.py` para cruzar la malla de puntos con los polígonos GIS y redacta el contenido del módulo educativo dentro de la aplicación.

### Bloque 3: Integración de Datos Reales y Calibración (Domingo 09:00 - 13:30)
* **[Rol 1 + Rol 3]** Ejecutan el pipeline unificado generando el `nisar_grid.parquet` y `timeseries.csv` definitivos con datos reales de NISAR cruzados con las capas de infraestructura/suelo.
* **[Rol 2]** Conecta los archivos definitivos, ajusta el ángulo de cámara (`pitch=45`) y el factor de escala vertical en PyDeck para que las anomalías reales resalten visualmente, y realiza el despliegue en *Streamlit Community Cloud*.

### Bloque 4: Congelación de Código, Documentación y Pitch (Domingo 14:30 - 19:00)
* **16:00h — CONGELACIÓN DE CÓDIGO (*Code Freeze*):** A partir de esta hora solo se corrigen errores visuales menores.
* **[Rol 2]** Comprueba el rendimiento en producción usando `@st.cache_data` y asegura que la URL pública carga en menos de 3 segundos.
* **[Rol 1]** Documenta los scripts de la carpeta `/pipeline/` e incluye una captura del árbol HDF5 procesado para demostrar rigor técnico ante los jueces.
* **[Rol 3 (con apoyo de 1 y 2)]** Publica el `README.md` final y graba el vídeo de demostración (20s planteando el problema real, 90s operando la aplicación interactiva con datos NISAR, 30s destacando la arquitectura open-source y su impacto).