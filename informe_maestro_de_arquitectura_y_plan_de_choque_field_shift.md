# 🛰️ DOCUMENTO TÉCNICO MAESTRO DE ARQUITECTURA Y PLAN DE CHOQUE (30 HORAS)
**Desafío:** NASA Space Apps Challenge 2026 — *"Field Shift: Adapting Farms with NASA Data"*  
**Equipo:** 3 Integrantes (Dedicación Full-Time)  
**Contexto Operativo:** Sábado Mediodía — Ejecución Intensiva hacia el MVP del Domingo  
**Región Recomendada para el Caso de Estudio:** Cuenca del Guadalquivir / Campiña Andaluza (España) o Valle Central / Kansas (EE. UU.)

---

## 0. Principios Tácticos de Victoria para este Hackathon

1. **Regla de Oro Geoespacial:** **Nunca proceses rásteres satelitales crudos (HDF5/NetCDF/GeoTIFF pesados) en tiempo real durante la demostración en vivo.** Descargar, reproyectar y recortar gigabytes de imágenes de SMAP o Landsat localmente consumiría el 80% del tiempo del fin de semana. El éxito en 30 horas depende de delegar el cómputo pesado a plataformas geoespaciales en la nube (Google Earth Engine / NASA POWER API) y pre-calcular estadísticas zonales sobre una región de demostración ("Golden Cache").
2. **Arquitectura de Cero Bloqueos (Zero-Blocking):** Con 3 personas y medio sábado consumido, el mayor riesgo no es la complejidad científica, sino la fricción de integración (CORS, despliegues dobles, esperas entre compañeros). Los 3 integrantes trabajarán en paralelo sobre un **Contrato JSON Único (`schema_contrato.json`)** congelado en la primera hora.
3. **Motor Determinista Primero, Traductor IA Después:** Los jueces de la NASA penalizan las "cajas negras" o las alucinaciones de IA cuando se trata de datos científicos. Un LLM nunca debe decidir qué cultivo sembrar por su cuenta; el cálculo de viabilidad de las 3 estrategias de rotación se ejecuta mediante un algoritmo multicriterio transparente y trazable (MCDA), mientras que el LLM actúa exclusivamente como traductor agrotécnico para redactar el "porqué", las brechas de incertidumbre y las alertas de validación local.

---

## 1. Resumen Ejecutivo de la Arquitectura

Para cumplir con la ventana de tiempo restante (~30 horas efectivas) y maximizar el impacto ante los jueces con un equipo de 3 personas, se establece una arquitectura **Monolítica Modular con Backend BFF (Backend-for-Frontend) Ultraligero y Procesamiento Geoespacial Delegado**. Construir microservicios distribuiría innecesariamente el esfuerzo del equipo en configuración de infraestructura, redes y despliegues (DevOps), restando tiempo al núcleo del reto: la lógica de rotación agronómica y la interfaz de usuario. Un backend unificado en Python permite que el código de ciencia de datos, el motor de reglas y la API convivan en el mismo repositorio sin fricción de serialización.

El procesamiento geoespacial se resuelve mediante un enfoque híbrido de doble vía: **APIs punto-a-serie-temporal en tiempo real** (como NASA POWER API) combinadas con **cómputo delegado en la nube** (Google Earth Engine o Microsoft Planetary Computer) y **pre-cálculo zonal sobre una región de demostración** con 5 parcelas reales contrastantes (ej. secano degradado, monocultivo agotado, ladera con erosión y regadío en estrés hídrico). Esto garantiza respuestas en menos de 50 milisegundos durante el pitch frente al jurado y soporte en vivo (<4 segundos) si un juez solicita probar una coordenada adyacente.

Finalmente, el sistema desacopla el **Motor Determinista de Decisión Agronómica (MCDA)** de la **Capa de Explicabilidad (Inteligencia de Sistemas - SI)**. El motor cruza matemáticamente las anomalías satelitales de la NASA con las fichas técnicas de los cultivos y los pesos de prioridad del agricultor para puntuar 3 estrategias de rotación a 3 años. Posteriormente, esos resultados estructurados alimentan un modelo de lenguaje (LLM) con salidas forzadas en JSON para explicar con lenguaje accesible para el productor la evidencia utilizada, las limitaciones de resolución espacial de los sensores y las verificaciones en tierra obligatorias antes de tomar una decisión.

---

## 2. Requisitos Funcionales y No Funcionales (Priorizados)

### 2.1. Requisitos Funcionales (Método MoSCoW)

* **Must-Have (Innegociables para la demo del domingo a las 14:00h):**
  * **Ingesta y Selección de Parcela:** Selección rápida de entre 4-5 parcelas reales pre-diagnosticadas con problemáticas distintas + capacidad de hacer clic en el mapa o dibujar un polígono para consultar coordenadas en vivo.
  * **Configuración del Productor:** Selector del cultivo actual/histórico y 4 *sliders* interactivos (escala 1 a 5) para ponderar las prioridades del agricultor: *Ahorro de Agua*, *Salud/Retención del Suelo*, *Estabilidad de Ingresos Inmediatos* y *Resiliencia Térmica/Climática*.
  * **Tablero de Diagnóstico Satelital NASA:** Visualización clara de 3 indicadores históricos (últimos 5 años) extraídos para esa ubicación: anomalía de humedad radicular (SMAP), tendencia de vigor vegetal y meses de suelo desnudo (MODIS/Landsat NDVI), y balance hidroclimático/estrés térmico (NASA POWER).
  * **Comparador Lado a Lado de 3 Estrategias de Rotación (Horizonte 3 años):**
    1. *Estrategia 1 (Línea Base / Actual):* Monocultivo o rotación tradicional actual del productor (sirve como referencia de desgaste).
    2. *Estrategia 2 (Optimización Hídrica y Resiliencia Climática):* Introducción de cultivos de baja demanda transpirativa y raíz profunda (ej. sorgo, cártamo, girasol o garbanzo).
    3. *Estrategia 3 (Regeneración Intensiva de Suelo y Nitrógeno):* Inclusión de leguminosas fijadoras de nitrógeno y cultivos de cobertura invernal (*cover crops* como centeno o veza) para eliminar el barbecho desnudo.
  * **Módulo de Explicabilidad, Brechas y Riesgos ("El Porqué y Lo Que Falta"):** Desglose explícito por cada estrategia indicando: (a) Por qué encaja con las prioridades marcadas, (b) Evidencia exacta de los sensores NASA, (c) Brechas de información y resolución espacial, y (d) Checklist de riesgos que exigen validación local en campo (ej. análisis NPK, pH, compactación o maquinaria disponible).

* **Should-Have (Añaden alto impacto visual y técnico si se integran el domingo por la mañana):**
  * **Simulador de Escenario Climático Adverso:** Un interruptor (*toggle*) de *"Simular Sequía Severa (+2°C, -25% precipitación)"* que recalcula al instante las puntuaciones y demuestra cómo se desploma el monocultivo frente a la rotación diversificada.
  * **Detección Histórica de Cultivos:** Cruce con capas de uso de suelo (USDA CDL en EE. UU. o SIGPAC/CORINE en Europa) para pre-rellenar el historial del lote.
  * **Copiloto Conversacional Acotado ("Pregúntale a tus Datos"):** Pequeño chat contextual donde el agricultor puede hacer preguntas en lenguaje natural sobre las 3 estrategias generadas.

* **Could-Have (Solo si sobra tiempo tras el congelado de código):**
  * **Exportación de Informe de Campo (PDF/Impresión):** Resumen de una página para llevar al asesor agronómico o cooperativa.
  * **Indicador de Volatilidad de Mercado:** Cruce estático con precios históricos (USDA AMS / MAPA) para estimar el riesgo financiero de la transición.

* **Out-of-Scope (Prohibido tocar en el hackathon para no perder tiempo):**
  * Autenticación de usuarios, cuentas o pasarelas de pago.
  * Descarga y procesamiento local de archivos GeoTIFF/HDF5/NetCDF completos en el servidor web.
  * Entrenamiento de modelos de Machine Learning desde cero sin datos de verdad de campo (*ground-truth*) validados.

### 2.2. Requisitos No Funcionales

* **Latencia y Resiliencia (Modo Demo):** Tiempo de respuesta inferior a **1.5 segundos** para las parcelas de demostración pre-cacheadas e inferior a **6 segundos** para consultas en vivo usando llamadas asíncronas paralelas. Si una API externa de la NASA o del LLM supera los **4 segundos** de espera o devuelve un error HTTP, el backend debe activar silenciosamente un *fallback* automático al dataset cacheado regionalmente para evitar que la demo falle frente al jurado.
* **Usabilidad y Accesibilidad Cognitiva:** Cero jerga teledetectora cruda sin traducción agronómica. En lugar de mostrar `"SMAP L4 Root Zone Soil Moisture = 0.14 m³/m³"`, la interfaz debe traducirlo a `"Humedad radicular crítica (Percentil 18 histórico): Alto riesgo para cultivos de raíz superficial"`.
* **Transparencia de Incertidumbre (Data Honesty):** Cada métrica satelital mostrada debe ir acompañada de un **Badge de Confianza Espacial**. Si una parcela mide 12 hectáreas y el dato proviene de SMAP (resolución ~9 km), la UI debe advertir visualmente que ese indicador refleja la tendencia hidroclimática de la comarca y no la variabilidad interna del lote.

---

## 3. Arquitectura Propuesta y Flujo de Componentes

```text
+-----------------------------------------------------------------------------------+
|                        CAPA DE PRESENTACIÓN (FRONTEND UI)                         |
|  [Mapa Interactivo + Parcelas Demo]  [Sliders Prioridades]  [Matriz 3 Rotaciones] |
|  [Gráficos Radar / Series NASA]      [Toggle Sequía]        [Badges Incertidumbre]|
+-----------------------------------------+-----------------------------------------+
                                          | Contrato JSON Único (schema_contrato.json)
                                          v
+-----------------------------------------------------------------------------------+
|                      CAPA DE API Y SERVICIO BACKEND (Python)                      |
|  [Controlador Geoespacial] ---> [Orquestador Asíncrono] ---> [Golden Cache Local] |
+-----------------+-----------------------+-----------------------+-----------------+
                  |                       |                       |
                  v                       v                       v
+-------------------------+   +-----------------------+   +-------------------------+
| CAPA INGESTA / DATOS    |   | CAPA LÓGICA NEGOCIO   |   | CAPA INTELIGENCIA (SI)  |
| (Rol 1: Data Lead)      |   | (Rol 2: Engine Lead)  |   | (Rol 2: SI Lead)        |
| - NASA POWER API (REST) |   | - Motor MCDA (Reglas) |   | - Traductor Agrotécnico |
| - GEE / Planetary Comp. |   | - Catálogo 8 Cultivos |   |   (LLM con salida JSON  |
| - SRTM / Suelos SSURGO  |   | - Evaluador de Brechas|   |    estructurada y fija) |
+------------+------------+   +-----------+-----------+   +-------------------------+
             |                            |
             v                            v
+-----------------------------------------------------------------------------------+
|                           CAPA DE ALMACENAMIENTO LIGERO                           |
|  [golden_parcels.json: 5 Parcelas Reales Pre-calculadas] [crops_catalog.json]     |
+-----------------------------------------------------------------------------------+
```

### Detalle de las 5 Capas Arquitectónicas

1. **Capa de Ingesta y Procesamiento de Datos:** Se descarta la descarga de archivos pesados desde NASA Earthdata durante la ejecución de la app. Se utiliza un pipeline bajo demanda vía **NASA POWER REST API** (que devuelve JSON climático directo por coordenadas sin necesidad de API Key) combinado con **Google Earth Engine (GEE) Python API** (o STAC vía Microsoft Planetary Computer) para extraer reducciones espaciales (`reduceRegion`) de SMAP, MODIS/Landsat y SRTM. Además, incluye un script generador que guarda en disco las estadísticas reales de las 5 parcelas de demostración (`golden_parcels.json`).
2. **Capa de Almacenamiento:** Sin bases de datos pesadas que requieran mantenimiento en el hackathon. Se utilizan archivos estructurados en disco/memoria (`golden_parcels.json` para las geometrías GeoJSON y series históricas pre-calculadas, y `crops_catalog.json` con los parámetros fenológicos y agronómicos de 8-10 cultivos clave), complementados opcionalmente con SQLite/DuckDB local si se requiere cachear consultas nuevas realizadas en vivo.
3. **Capa de Lógica de Negocio (Modelo de Rotación MCDA):** Se implementa un **Sistema Experto Basado en Reglas y Análisis de Decisión Multicriterio (MCDA - Suma Ponderada Normalizada / TOPSIS)** en lugar de un modelo de Machine Learning opaco. **Justificación técnica:** No existe un dataset etiquetado de "rotación exitosa vs. fallida" específico para cada finca sin caer en datos sintéticos falsos. Un motor determinista basado en coeficientes agronómicos reales (FAO/USDA: coeficiente de cultivo $K_c$, tolerancia a sequía, fijación biológica de nitrógeno, aporte de biomasa radicular y factor de cobertura contra erosión) es 100% auditable, rápido de programar en `numpy`/`pandas` y permite explicar matemáticamente qué variable empujó cada decisión.
4. **Capa de API y Servicio Backend:** Escrito en Python. Dependiendo de la destreza del Integrante 3 (Frontend), puede exponerse mediante **FastAPI** (con validación `Pydantic` y llamadas asíncronas `httpx`) o integrarse directamente como módulos nativos dentro de una aplicación monolítica en **Streamlit**.
5. **Capa de Presentación (Frontend):** Interfaz de pantalla única (*Single-View Dashboard*) centrada en el mapa interactivo a la izquierda y el panel de decisión a la derecha, mostrando tarjetas comparativas con semáforos de viabilidad, gráficos de radar para comparar las 3 estrategias y desgloses claros de incertidumbre satelital.

---

## 4. Stack Tecnológico Recomendado y Elección Táctica para 3 Personas

| Capa | Opción A: "Velocidad Extrema" (100% Python) | Opción B: "Acabado Visual Superior" (Híbrido) | Opción "Robusta/Escalable" (Post-Hackathon) |
| :--- | :--- | :--- | :--- |
| **Frontend & UI** | **Streamlit** + Custom CSS + Plotly | **React (Vite)** + TailwindCSS + Recharts | Next.js + TypeScript + Deck.gl |
| **Visualización Mapa** | **Folium** (`streamlit-folium`) o PyDeck | **MapLibre GL JS** / Leaflet (`react-leaflet`) | Mapbox GL + Martin Vector Tile Server |
| **Backend API** | Módulos Python directos (Cero latencia REST) | **FastAPI** + Pydantic + Uvicorn | FastAPI + Celery + Redis (Colas asíncronas) |
| **Motor Geoespacial** | `requests` (NASA POWER) + `earthengine-api` | `httpx` (NASA POWER) + `earthengine-api` | Pipeline STAC + Xarray + Dask sobre Cloud GeoTIFF |
| **Almacenamiento** | Archivos JSON estáticos + Caché en memoria | JSON + SQLite / Supabase (PostGIS) | PostgreSQL + PostGIS + AWS S3 |
| **Explicabilidad (SI)** | SDK de Google GenAI (`gemini-2.5-flash`) | SDK de Google GenAI / OpenAI Structured Outputs | RAG con boletines de extensión agraria locales |
| **Despliegue** | Streamlit Community Cloud / Railway | Vercel (Front) + Render/Railway (Back) | Contenedores Docker en AWS ECS / Cloud Run |

> **Recomendación Táctica Inmediata (Sábado 13:30h):** Si la **Persona 3** ya tiene una plantilla lista en React + Tailwind + Mapa y la domina con los ojos cerrados, usad la **Opción B (FastAPI + React)**. Si no tenéis plantilla de React lista ahora mismo, elegid la **Opción A (Streamlit + Folium + Plotly)**: los 3 programaréis en Python en el mismo repositorio, cada uno en su propio archivo (`data_nasa.py`, `rotation_engine.py`, `app.py`), ahorrando 6 horas de peleas con CORS, estados de React y CSS.

---

## 5. Prerrequisitos, Accesos y Datasets Priorizados

### 5.1. Cuentas y Accesos Necesarios
1. **NASA POWER API:** **Acceso inmediato sin registro ni API Key.** Permite extraer series históricas de agroclimatología mediante una simple petición HTTP `GET`.
2. **Google Earth Engine (GEE) o Microsoft Planetary Computer:** Para consultar SMAP, MODIS, Landsat y SRTM en la nube sin descargar gigabytes. *(Truco de emergencia para el sábado: si la cuenta de GEE tarda en aprobarse, Microsoft Planetary Computer permite consultar colecciones STAC de MODIS, Landsat y DEM de forma abierta sin aprobación previa, o bien se puede usar la API REST de Open-Meteo / SoilGrids / AppEEARS combinada con NASA POWER).*
3. **API Key de LLM (Google AI Studio / OpenAI / Anthropic):** Para el módulo de traducción agrotécnica y explicabilidad estructurada.

### 5.2. Los 4 Datasets Imprescindibles para el MVP (Por qué estos y no otros)
Intentar integrar los 10 datasets mencionados en las bases del desafío en un solo fin de semana bloqueará al equipo. Seleccionamos **4 datasets complementarios** que cubren las 4 dimensiones agronómicas (Clima, Agua Subsuperficial, Planta y Topografía/Suelo) con mínima fricción técnica:

1. **NASA POWER (Agroclimatology API - `power.larc.nasa.gov`):**
   * *Variables:* `PRECTOTCORR` (Precipitación corregida), `T2M_MAX` (Temperatura máxima a 2m), `EVPTRNS` (Evapotranspiración).
   * *Justificación:* Es la API más estable y rápida de la NASA. Acepta latitud y longitud directamente en la URL y devuelve un JSON limpio en 1.5 segundos con 10 años de datos diarios o mensuales. Cero procesamiento de imágenes.
2. **NASA SMAP L4 (Root Zone Soil Moisture - Resolución ~9 km):**
   * *Variables:* `sm_rootzone` (Humedad en la zona radicular de 0 a 100 cm de profundidad) y su anomalía interanual.
   * *Justificación:* La humedad superficial se evapora en horas, pero la humedad en la zona radicular determina si un cultivo de verano sobrevivirá o si conviene rotar hacia especies de raíz profunda (cártamo, girasol, sorgo). Además, su resolución gruesa de 9 km nos brinda el ejemplo perfecto para brillar ante los jueces explicando las **brechas de escala espacial**.
3. **MODIS / Landsat NDVI (Vigor Vegetal y Cobertura Estacional - Resolución 250m / 30m):**
   * *Variables:* Índice de Vegetación de Diferencia Normalizada (`NDVI`) estacional de los últimos 5 años.
   * *Justificación:* Permite diagnosticar dos problemas críticos en la parcela: (1) caída progresiva de los picos de biomasa por fatiga de monocultivo, y (2) detección de meses con `NDVI < 0.22` en otoño/invierno, lo que demuestra que el agricultor deja el suelo desnudo y justifica científicamente recomendar un cultivo de cobertura en la Estrategia 3.
4. **SRTM Digital Elevation (30m) + Suelos (USDA SSURGO en EE. UU. / SoilGrids 250m global):**
   * *Variables:* Pendiente media de la parcela (`slope` en %) y fracción de arcilla/arena/materia orgánica.
   * *Justificación:* Una pendiente superior al 4-6% combinada con suelo desnudo en invierno y lluvias torrenciales (NASA POWER) dispara el riesgo de pérdida de suelo fértil por escorrentía, activando automáticamente en el motor la recomendación de gramíneas/leguminosas de cobertura.

---

## 6. Flujo de Datos Detallado y Modelo Matemático (Paso a Paso)

### Paso 1: Selección y Entrada del Agricultor
El usuario selecciona una parcela en el mapa (ej. *"Finca El Olivar / Campiña - 24 ha"*, cultivo actual: *Trigo continuo*) y ajusta sus 4 prioridades mediante *sliders* de 1 a 5:
$$\mathbf{W} = [w_{\text{agua}}, w_{\text{suelo}}, w_{\text{ingreso}}, w_{\text{clima}}]$$
El sistema normaliza el vector de pesos para que sume 1: $\hat{w}_i = \frac{w_i}{\sum w_j}$.

### Paso 2: Extracción Satelital (Caché Instantánea o Asíncrona)
El backend verifica si el ID de la parcela o las coordenadas $(lat, lon)$ están dentro del archivo `golden_parcels.json`. Si están, carga la serie temporal en `<20 ms`. Si es un punto nuevo en el mapa, lanza una consulta a `NASA POWER API` y a `GEE/SoilGrids` con un *timeout* estricto de 3.5 segundos.

### Paso 3: Cálculo de los 3 Índices de Estrés Ambiental de la Parcela (Escala 0 a 100)
Los datos crudos de la NASA se transforman mediante fórmulas deterministas en tres índices comprensibles:
1. **Índice de Déficit Hídrico ($I_{\text{DH}}$):** Combina la anomalía negativa de humedad radicular de SMAP de los últimos 3 años con el déficit de Aridez (Precipitación menos Evapotranspiración de NASA POWER) y la frecuencia de días con $T_{\max} > 35^\circ\text{C}$.
2. **Índice de Vulnerabilidad a la Erosión ($I_{\text{VE}}$):** Función directa de la pendiente topográfica (SRTM) multiplicada por la proporción de meses del año en que la parcela presenta suelo desnudo ($\text{NDVI} < 0.22$ en MODIS/Landsat).
3. **Índice de Fatiga Biológica y Nutricional ($I_{\text{FB}}$):** Penalización basada en la reiteración de la misma familia botánica (monocultivo de gramíneas) y el estancamiento o pendiente negativa de los máximos anuales de NDVI.

### Paso 4: Evaluación de las 3 Estrategias en el Motor de Rotación (MCDA)
El motor construye y evalúa siempre 3 secuencias de rotación a 3 años contrastadas:
* **Estrategia A (Continuista / Testigo):** Mantener el esquema actual del productor (ej. *Cereal → Cereal → Barbecho desnudo*).
* **Estrategia B (Ahorro Hídrico y Resiliencia Térmica):** Secuencia adaptada a baja disponibilidad de agua (ej. *Girasol/Sorgo → Garbanzo (Leguminosa de secano) → Trigo*).
* **Estrategia C (Regeneración de Suelo y Captura de Nitrógeno):** Secuencia intensiva en cobertura y biología de suelo (ej. *Cereal + Veza/Avena de cobertura invernal → Habas/Soja → Colza/Girasol*).

Para cada Estrategia $k \in \{A, B, C\}$, el motor calcula 4 sub-scores ($0-100$) cruzando los atributos del catálogo `crops_catalog.json` con los índices de estrés de la parcela:
* $S_{\text{agua}}(k) = 100 - \text{DemandaHídrica}(k) \times (0.5 + 0.5 \cdot \frac{I_{\text{DH}}}{100})$
* $S_{\text{suelo}}(k) = 0.4 \cdot \text{FijaciónNitrógeno}(k) + 0.4 \cdot \text{CoberturaInvernal}(k) \cdot (1 + \frac{I_{\text{VE}}}{100}) + 0.2 \cdot \text{DiversidadRaíz}(k)$
* $S_{\text{ingreso}}(k) = \text{MargenEstabilidad}(k) - \text{PenalizaciónFatiga}(k, I_{\text{FB}})$
* $S_{\text{clima}}(k) = \text{ToleranciaCalorSequía}(k) \times (1 - 0.3 \cdot \text{RiesgoHelada}(k))$

El **Score Global de Adecuación** se obtiene mediante el producto escalar con las prioridades del agricultor:
$$\text{Score}(k) = \hat{w}_{\text{agua}} S_{\text{agua}}(k) + \hat{w}_{\text{suelo}} S_{\text{suelo}}(k) + \hat{w}_{\text{ingreso}} S_{\text{ingreso}}(k) + \hat{w}_{\text{clima}} S_{\text{clima}}(k)$$

### Paso 5: Generación de Explicabilidad e Incertidumbre y Renderizado
El vector numérico de resultados se inyecta en el módulo de Inteligencia de Sistemas (LLM), que genera la justificación narrativa, las alertas de resolución y el checklist local. El frontend recibe el JSON unificado y pinta el mapa, los gráficos comparativos y las tarjetas explicativas.

---

## 7. Estrategia de "Inteligencia de Sistemas" (SI) y Explicabilidad Responsable

Para cumplir rigurosamente con la exigencia del reto (*"explicar el porqué, la evidencia, los beneficios, las brechas de información y los riesgos que requieren revisión local sin reemplazar el juicio del agricultor"*), implementamos un patrón de **Inyección de Contexto Determinista con Salida Estructurada (Structured Outputs)**.

### Reglas de Diseño del Agente Explicador:
1. **Prohibición de Inventar Datos Satelitales:** El LLM recibe en su *prompt* los valores exactos ya calculados por Python (ej. `"SMAP_anomaly_pct": -18.4`, `"SRTM_slope_deg": 6.2`, `"bare_soil_months": 4.5`, `"parcel_area_ha": 14.0`). Tiene prohibido citar sensores o cifras que no estén en ese JSON de entrada.
2. **Cálculo Automático de Brechas de Escala (Data Gaps):** Antes de llamar al LLM, una regla determinista en Python compara el área de la parcela con la huella de los sensores:
   * Si `parcel_area_ha < 500`, añade el flag: `GAP_SMAP_RESOLUTION: Parcela menor a un píxel de SMAP (9 km x 9 km = 8,100 ha)`.
   * Añade siempre el flag: `GAP_SUBSURFACE_CHEMISTRY: Los satélites ópticos y de microondas no miden NPK, pH ni compactación mecánica profunda`.
3. **Estructura Obligatoria de Salida por cada Rotación:**
   * **`por_que_funciona`:** Explicación conectando los *sliders* del usuario con los datos de la NASA (ej. *"Como priorizaste Ahorro de Agua (5/5) y NASA POWER muestra una caída del 21% en lluvias de primavera junto a una anomalía negativa de humedad en SMAP, introducir garbanzo en el Año 2 reduce el consumo hídrico un 38% y deja nitrógeno residual para el cereal del Año 3"*).
   * **`evidencia_nasa_utilizada`:** Lista de viñetas citando sensor, variable y tendencia observada en la parcela.
   * **`brechas_de_informacion`:** Advertencia transparente sobre la resolución de SMAP/POWER frente al tamaño real del lote y la ausencia de datos químicos del suelo.
   * **`checklist_validacion_local`:** 3 comprobaciones físicas que el agricultor debe hacer en su tierra antes de decidir (ej. *"1. Medir pH del suelo en laboratorio (el garbanzo sufre en suelos ácidos < 6.0). 2. Comprobar con calicata o penetrómetro si existe suela de labor antes de sembrar raíz pivotante. 3. Verificar disponibilidad de cabezal de cosecha en la cooperativa local"*).

---

## 8. Matriz de Riesgos Técnicos y Plan de Mitigación (Estrategia 30h)

| Riesgo Técnico | Impacto | Probabilidad | Mitigación Concreta e Inmediata |
| :--- | :--- | :--- | :--- |
| **1. Caída o lentitud de APIs de la NASA / GEE durante la evaluación del jurado** | Crítico (Demo rota) | Alta | **Modo "Golden Cache":** Pre-descargar hoy sábado las series históricas reales de 5 parcelas representativas en `golden_parcels.json`. Configurar un `timeout=3.5s` en todas las llamadas externas; si falla o tarda más, servir el dato cacheado de la comarca al instante. |
| **2. Desajuste de resoluciones espaciales (Píxeles gigantes vs. parcelas pequeñas)** | Alto (Crítica científica de los jueces) | Segura (100%) | **Convertir la limitación en tu mejor argumento:** No intentar hacer *downscaling* complejo. Mostrar en la interfaz etiquetas de resolución (`Landsat 30m: Escala Parcela` vs `SMAP 9km: Contexto Comarcal`) y destacarlo en la caja de "Brechas de Información". A los jueces de la NASA les encanta esta honestidad técnica. |
| **3. Bloqueo entre compañeros esperando que otro termine su parte** | Crítico (No llegar al domingo) | Media-Alta | **Contrato JSON en la Hora 1:** Pactar el archivo `schema_contrato.json` hoy a las 14:00h. La Persona 3 maqueta toda la UI leyendo ese archivo estático mientras las Personas 1 y 2 programan el motor real. |
| **4. Alucinaciones o respuestas lentas del LLM generando explicaciones** | Medio-Alto | Media | **Doble seguro:** Usar un modelo rápido (`gemini-2.5-flash` o `gpt-4o-mini`) Y programar un **generador de plantillas en Python (`f-strings`)** como respaldo. Si la API del LLM falla o no hay créditos, la plantilla Python genera textos impecables con las variables en `0.01 segundos`. |
| **5. Complejidad excesiva al dibujar polígonos en el mapa** | Medio | Media | **Enfoque progresivo:** Empezar con 4 botones grandes de *"Cargar Parcela Real #1, #2, #3, #4"* que centran el mapa y pintan el polígono predefinido al hacer clic, más la opción de hacer un clic simple `(lat, lon)` en cualquier punto del mapa. |

---

## 9. División Exacta de Responsabilidades (Equilibrio para 3 Personas)

Para que nadie pise el código de otro ni existan cuellos de botella, el equipo se divide en **3 Carriles Paralelos con Responsabilidad End-to-End**:

### 🛰️ PERSONA 1: Arquitecto de Datos Geoespaciales (NASA Data & Geo-Pipeline Lead)
* **Misión:** Conseguir que el sistema entregue indicadores reales de la NASA para cualquier parcela seleccionada en menos de 2 segundos y documentar el rigor científico de los datos.
* **Archivos bajo su propiedad exclusiva:** `data_nasa.py` y `golden_parcels.json`.
* **Tareas concretas:**
  1. Seleccionar 5 coordenadas reales de parcelas agrícolas con problemáticas distintas (ej. 1: Monocultivo cereal en secano árido; 2: Parcela en ladera con pendiente >7%; 3: Regadío con caída de humedad; 4: Suelo arenoso de baja retención; 5: Vega fértil de control).
  2. Programar el conector en vivo a `NASA POWER API` (precipitación, temperaturas extremas y evapotranspiración de los últimos 5-10 años).
  3. Extraer (vía GEE, Planetary Computer o AppEEARS/SoilGrids) los valores históricos de SMAP (humedad radicular), MODIS/Landsat (NDVI mensual) y SRTM (pendiente) para esas 5 parcelas y guardarlos en `golden_parcels.json`.
  4. Programar la función `get_parcel_diagnosis(parcel_id, lat, lon)` que transforma los datos en los 3 Índices de Estrés ($0-100$) y devuelve las series temporales listas para graficar.

### 🧠 PERSONA 2: Ingeniero del Motor Agronómico & IA (Decision Engine & SI Lead)
* **Misión:** Construir el cerebro matemático que evalúa las 3 rotaciones según las prioridades del agricultor y el módulo de explicabilidad que redacta el "porqué", las brechas y los riesgos locales.
* **Archivos bajo su propiedad exclusiva:** `rotation_engine.py`, `explainer_ai.py` y `crops_catalog.json`.
* **Tareas concretas:**
  1. Construir `crops_catalog.json` con 8-10 cultivos bien parametrizados (Trigo, Maíz, Girasol, Sorgo, Garbanzo, Soja/Habas, Alfalfa, Centeno/Veza de cobertura) incluyendo: consumo hídrico, fijación de N, cobertura contra erosión, tolerancia térmica, familia botánica y margen relativo.
  2. Programar el **Motor Multicriterio (MCDA)** que recibe los 3 índices de estrés de la Persona 1 + los 4 pesos del agricultor y genera las 3 estrategias de rotación puntuadas (0-100) con desglose por categoría.
  3. Programar el **Módulo de Explicabilidad (`explainer_ai.py`)** conectando la API del LLM con salida JSON estructurada + el generador determinista de plantillas como *fallback* infalible.
  4. Exponer la función maestra (o endpoint FastAPI `POST /api/evaluate`) que une todo y devuelve el contrato JSON final a la Persona 3.

### 🗺️ PERSONA 3: Especialista en Interfaz Geoespacial, UX & Storytelling (Frontend & Pitch Lead)
* **Misión:** Crear una interfaz intuitiva para el agricultor e impactante para los jueces, liderando el diseño visual, el despliegue web y la narrativa del video de presentación.
* **Archivos bajo su propiedad exclusiva:** `app_ui.py` (si es Streamlit) o carpeta `/frontend` (si es React), más el mazo de diapositivas y el guion del video.
* **Tareas concretas:**
  1. Construir el **Dashboard de Pantalla Única**:
     * *Izquierda:* Mapa interactivo con vista satelital, polígonos de las 5 parcelas y selector de coordenadas.
     * *Arriba:* Selector de cultivo actual, los 4 *sliders* de prioridades del agricultor y el botón de *"Simular Escenario de Sequía"*.
     * *Centro/Abajo:* Panel de diagnóstico NASA (3 tarjetas de estrés + gráfico de serie temporal de lluvias/humedad/NDVI) y la **Matriz Comparativa de las 3 Rotaciones** (con gráfico de radar o barras comparativas).
  2. Diseñar las cajas visuales de **"Por qué se sugiere"**, **"Brechas Satelitales (Badges de resolución 30m vs 9km)"** y **"Checklist de Verificación en Campo"**.
  3. Preparar el storytelling del proyecto, grabar el video de demostración (máximo 2-3 minutos) mostrando el flujo real y liderar la maquetación de la página de entrega en NASA Space Apps.

---

## 10. Contrato de Datos Unificado (`schema_contrato.json`)

Este es el esquema exacto que conecta a los 3 miembros del equipo desde el minuto 1. Si respetáis estas llaves, la integración del sábado por la noche tardará menos de 15 minutos:

```json
{
  "request_example": {
    "parcel_id": "parcel_01_campina_secano",
    "lat": 37.4821,
    "lon": -5.6312,
    "area_ha": 18.5,
    "current_crop": "trigo_continuo",
    "drought_simulation": false,
    "farmer_priorities": {
      "water_saving": 5,
      "soil_health": 4,
      "short_term_income": 3,
      "climate_resilience": 5
    }
  },
  "response_example": {
    "parcel_metadata": {
      "name": "Parcela Demo 1 - Campiña Secano (Sevilla/Córdoba)",
      "area_ha": 18.5,
      "soil_texture": "Franco-arcilloso",
      "slope_percent": 5.8
    },
    "nasa_diagnostics": {
      "water_deficit_index": 78,
      "erosion_risk_index": 64,
      "biological_fatigue_index": 82,
      "summary_metrics": {
        "precip_trend_5yr_pct": -19.4,
        "smap_rootzone_anomaly_pct": -16.2,
        "extreme_heat_days_yr": 42,
        "bare_soil_months_yr": 4.5
      }
    },
    "strategies": [
      {
        "id": "strat_current",
        "name": "Estrategia 1: Continuista (Trigo -> Trigo -> Barbecho Desnudo)",
        "tag": "Línea Base Actual",
        "overall_score": 41,
        "dimension_scores": {
          "water": 38,
          "soil": 25,
          "income": 65,
          "resilience": 35
        },
        "crop_sequence": ["Trigo Blando", "Trigo Blando", "Barbecho Desnudo"],
        "explainability": {
          "why_this_score": "Mantener cereal tras cereal con barbecho desnudo penaliza severamente tu prioridad de Salud del Suelo (4/5) y Ahorro de Agua (5/5). Los 4.5 meses sin cobertura vegetal aumentan la evaporación directa y agravan la fatiga de nitrógeno.",
          "nasa_evidence": [
            "MODIS/Landsat NDVI confirma suelo totalmente desnudo (NDVI < 0.20) entre julio y noviembre.",
            "SRTM detecta una pendiente del 5.8%, generando alto riesgo de escorrentía durante las lluvias de otoño registradas por NASA POWER."
          ],
          "data_gaps": [
            "SMAP (resolución 9 km) indica caída regional de humedad del -16.2%, pero no distingue si las partes bajas de tus 18.5 ha retienen más agua.",
            "Los satélites no detectan carga de patógenos fúngicos de raíz acumulados por el monocultivo de cereal."
          ],
          "local_verification_checklist": [
            "Inspeccionar rastrojo en busca de enfermedades fúngicas (mal del pie / septoria).",
            "Evaluar aparición de cárcavas o arrastre de tierra tras las primeras lluvias de otoño."
          ]
        }
      },
      {
        "id": "strat_water_smart",
        "name": "Estrategia 2: Resiliencia Hídrica (Trigo -> Garbanzo -> Sorgo/Girasol)",
        "tag": "Óptima para Ahorro de Agua",
        "overall_score": 86,
        "dimension_scores": {
          "water": 91,
          "soil": 78,
          "income": 82,
          "resilience": 89
        },
        "crop_sequence": ["Trigo Blando", "Garbanzo (Leguminosa)", "Girasol / Sorgo"],
        "explainability": {
          "why_this_score": "Maximiza tus dos prioridades principales: Ahorro de Agua (5/5) y Resiliencia Climática (5/5). Sustituir el segundo año de cereal por garbanzo reduce la demanda hídrica un 35%, y el girasol/sorgo en el tercer año aprovecha su raíz profunda para extraer humedad por debajo de los 80 cm.",
          "nasa_evidence": [
            "NASA POWER registra una caída del 19.4% en lluvias primaverales y 42 días de calor extremo (>35°C), condiciones donde el sorgo y el garbanzo superan al cereal.",
            "SMAP muestra déficit persistente en los primeros 40 cm del perfil, favoreciendo raíces pivotantes."
          ],
          "data_gaps": [
            "La huella de NASA POWER (50 km) y SMAP (9 km) promedia el clima comarcal; microclimas de vaguada en tu parcela pueden alterar la humedad real.",
            "La teledetección no mide el pH ni la salinidad subsuperficial ni la presencia de rabia del garbanzo (Ascochyta)."
          ],
          "local_verification_checklist": [
            "Realizar análisis de pH y caliza activa antes de sembrar garbanzo.",
            "Comprobar con barrena o calicata que no exista suela de labor a 30 cm que impida profundizar a la raíz del girasol."
          ]
        }
      },
      {
        "id": "strat_soil_regen",
        "name": "Estrategia 3: Regenerativa (Trigo + Cubierta Veza/Avena -> Habas/Leguminosa -> Cereal)",
        "tag": "Óptima para Salud de Suelo y Erosión",
        "overall_score": 83,
        "dimension_scores": {
          "water": 76,
          "soil": 95,
          "income": 78,
          "resilience": 84
        },
        "crop_sequence": ["Trigo + Cubierta Invernal (Veza/Avena)", "Leguminosa de Grano", "Trigo / Cebada"],
        "explainability": {
          "why_this_score": "Resuelve directamente la vulnerabilidad a la erosión (índice 64/100) y la fatiga biológica (82/100). La cubierta vegetal protege la pendiente del 5.8% durante las lluvias torrenciales de otoño y las leguminosas fijan nitrógeno atmosférico, reduciendo el gasto en fertilizantes sintéticos.",
          "nasa_evidence": [
            "Cruza la pendiente SRTM (5.8%) con los picos de intensidad de lluvia otoñal de NASA POWER para eliminar los 4.5 meses de suelo desnudo detectados por Landsat.",
            "Corrige el estancamiento interanual de los picos de NDVI observado en los últimos 5 años."
          ],
          "data_gaps": [
            "El satélite no permite saber el banco de semillas de malas hierbas presente en tu lote ni la compactación superficial.",
            "La transpiración exacta de la cubierta vegetal en primavera depende de la fecha exacta en que el agricultor realice el segado/terminación."
          ],
          "local_verification_checklist": [
            "Confirmar disponibilidad de sembradora directa o mínimo laboreo para implantar la cubierta sin voltear el suelo.",
            "Planificar la fecha límite de terminación de la cubierta (finales de invierno) para evitar que compita por agua con el cultivo principal."
          ]
        }
      }
    ]
  }
}
```

---

## 11. Cronograma de Guerra Hora por Hora (Sábado 13:00h a Domingo 20:00h)

### ⏱️ FASE 1: Sábado Tarde (13:00h – 19:30h) — Cimientos y Trabajo en Paralelo
* **13:00 – 14:00 (Reunión de 60 min - Los 3 juntos):**
  * Crear repositorio en GitHub.
  * Elegir definitivamente entre **Opción A (Todo en Streamlit)** u **Opción B (FastAPI + React)**.
  * Guardar el JSON de la Sección 10 como `mock_response.json` en la raíz del proyecto.
* **14:00 – 19:30 (Ejecución en 3 carriles aislados):**
  * **Persona 1 (Datos NASA):** Programa el script de consulta a `NASA POWER API` (`https://power.larc.nasa.gov/api/temporal/monthly/point`). Extrae datos reales de 5 parcelas y genera la primera versión de `golden_parcels.json`.
  * **Persona 2 (Motor & IA):** Crea `crops_catalog.json` con los 8 cultivos y programa en `rotation_engine.py` la fórmula matemática que lee las prioridades del agricultor y recalcula en vivo los `overall_score` y `dimension_scores` de las 3 estrategias.
  * **Persona 3 (Frontend & UX):** Construye la pantalla completa leyendo `mock_response.json`. A las 19:00h ya se debe ver en pantalla el mapa, los 4 *sliders* y las 3 columnas de estrategias con sus colores y alertas.

### ⏱️ FASE 2: Sábado Noche (20:30h – 01:30h) — Integración End-to-End e Inteligencia
* **20:30 – 22:30 (Hito Crítico de Integración):**
  * **Personas 1 y 2:** Conectan `data_nasa.py` con `rotation_engine.py`. Ahora, al pasar una coordenada o seleccionar una de las 5 parcelas reales, los índices de la NASA alimentan el cálculo de las rotaciones.
  * **Persona 3:** Conecta los *sliders* de la UI con la función/endpoint de la Persona 2. **Prueba de fuego:** Mover el *slider* de "Ahorro de Agua" de 1 a 5 debe cambiar instantáneamente los puntos y el ganador en pantalla.
* **22:30 – 01:30:**
  * **Persona 2:** Conecta la API del LLM para enriquecer dinámicamente los textos de `explainability` y deja activado el sistema de plantillas Python como *fallback* automático si falla internet o la API Key.
  * **Persona 1:** Incorpora el *toggle* de `"Simular Sequía Severa"` (que incrementa el índice de déficit hídrico en +25 puntos y recalcula todo al instante).
  * **Persona 3:** Añade los gráficos visuales (Radar Chart comparando las 4 dimensiones de las 3 estrategias y línea temporal de anomalía climática de la NASA).

### ⏱️ FASE 3: Domingo Mañana (09:00h – 14:00h) — Pulido Visual y Congelación de Código
* **09:00 – 11:30:**
  * **Persona 3:** Añade los **Badges de Escala Espacial** (`SMAP 9km - Comarcal`, `Landsat 30m - Parcela`, `SRTM 30m - Topografía`) y formatea la sección de *"Checklist de Revisión Local"* con iconos de alerta para que destaque a primera vista.
  * **Personas 1 y 2:** Prueban casos extremos (poner todos los *sliders* al mínimo o al máximo, hacer clic fuera de las parcelas predefinidas) y blindan el código con bloques `try/except` para que sea imposible que aparezca un error 500 en pantalla.
* **11:30 – 14:00 (FEATURE FREEZE & DESPLIEGUE):**
  * Se prohíbe añadir funcionalidades nuevas.
  * Despliegue de la aplicación en una URL pública (Streamlit Cloud, Render o Vercel) para que los jueces puedan probarla desde el enlace del proyecto.

### ⏱️ FASE 4: Domingo Tarde (15:00h – 19:30h) — Storytelling, Video y Entrega Ganadora
* **15:00 – 17:30:**
  * **Persona 3:** Graba el video de demostración (máximo 2 minutos y 30 segundos, subido a YouTube como público/oculto o en el formato exigido por vuestra sede local).
  * **Persona 1:** Redacta la documentación oficial de *NASA Data & Methodology* (listando con enlaces exactos SMAP, NASA POWER, MODIS/Landsat y SRTM).
  * **Persona 2:** Limpia el `README.md` de GitHub incluyendo el diagrama de arquitectura, la fórmula del motor multicriterio y la explicación de cómo se evita que la IA reemplace el juicio del agricultor.
* **17:30 – 19:00:** Revisión cruzada de todos los enlaces en ventana de incógnito y envío definitivo en la plataforma de NASA Space Apps.

---

## 12.Estructura Ganadora para el Video / Pitch de 3 Minutos (Guion para la Persona 3)

Los jueces evalúan decenas de proyectos el domingo por la noche; ganan aquellos que comunican claridad técnica y empatía con el usuario en los primeros 30 segundos:

* **0:00 – 0:30 | El Problema Real y la Paradoja Espacial:**
  *"Los agricultores enfrentan sequías más largas y suelos agotados, pero los datos satelitales de la NASA están en formatos científicos complejos que un productor no puede interpretar directamente. Además, recomendar un cultivo sin explicar la incertidumbre del satélite es peligroso. Presentamos **Field Shift**."*
* **0:30 – 1:30 | Demo en Vivo del Flujo de Decisión (El Corazón del Proyecto):**
  *Mostrar la pantalla funcionando:* Seleccionar una parcela real en el mapa. Mostrar cómo el sistema cruza automáticamente **NASA POWER**, **SMAP**, **MODIS NDVI** y **SRTM** para diagnosticar fatiga biológica y déficit hídrico. Mover en vivo los *sliders* de prioridad del agricultor y mostrar cómo la herramienta compara **3 estrategias de rotación a 3 años** (Actual vs. Resiliencia Hídrica vs. Regenerativa), activando el simulador de sequía.
* **1:30 – 2:15 | El Diferenciador Clave ("Por Qué", Brechas y Juicio Local):**
  *Hacer zoom en la tarjeta de explicabilidad:* Mostrar cómo nuestra arquitectura combina un motor multicriterio determinista con Inteligencia de Sistemas para explicar el **porqué** con datos reales, advierte honestamente sobre las **brechas de resolución espacial** (diferenciando los 9 km de SMAP de los 30 m de Landsat) y entrega al agricultor una **lista de verificación en campo** (pH, compactación, maquinaria) porque ninguna tecnología espacial reemplaza el conocimiento de quien pisa la tierra.
* **2:15 – 2:45 | Impacto y Escalabilidad:**
  Cerrar destacando la velocidad de respuesta, el bajo coste computacional gracias al uso inteligente de APIs agroclimáticas y el impacto directo en la resiliencia climática y la salud del suelo.
```

Ya tenéis el documento consolidado en el editor lateral con la arquitectura completa, el modelo matemático de puntuación de rotaciones, el contrato JSON exacto para conectar vuestros tres módulos y el cronograma por horas desde este sábado al mediodía hasta la entrega de mañana domingo. 

Si queréis acelerar aún más el arranque técnico ahora mismo, decidme si habéis elegido la **Opción A (Streamlit 100% Python)** o la **Opción B (FastAPI + React)** y os genero en el acto los archivos de código base (`data_nasa.py`, `rotation_engine.py` y `app.py`) listos para ejecutar.