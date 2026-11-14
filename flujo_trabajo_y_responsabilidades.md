# Flujo de trabajo, arquitectura y responsabilidades

## NASA Space Apps Challenge 2026 — Dancing with the SARs

Documento operativo para iniciar el desarrollo de forma paralela, modular y escalable.

Fuentes de consolidación:

- [informe_readme.md](./informe_readme.md)
- [informe_maestro_de_arquitectura_y_plan_de_choque.md](./informe_maestro_de_arquitectura_y_plan_de_choque.md)

Este documento es la definición operativa vigente. Cuando exista una diferencia con la asignación
de responsabilidades de los informes fuente, prevalece esta versión.

---

## 1. Objetivo

Construir un MVP que traduzca observaciones SAR en indicadores comprensibles de subsidencia,
cambio territorial y riesgo, mediante:

- un pipeline científico ejecutado offline;
- artefactos de datos ligeros y versionados;
- una aplicación Streamlit modular;
- responsabilidades separadas por rol;
- ramas Git independientes y de corta duración;
- integración mediante Pull Requests.

La aplicación nunca debe descargar ni procesar archivos HDF5 pesados durante la interacción del
usuario.

---

## 2. Decisiones de alcance

### 2.1. Incluido en el MVP

- Ciudad de México como caso extremo y Málaga como control negativo.
- Yakarta queda como extensión priorizada, solo si hay cobertura NISAR y tiempo de integración.
- Pipeline Python documentado.
- Datos NISAR L2 o una fuente de respaldo declarada.
- Mapa 2D/3D interactivo.
- KPIs reactivos.
- Serie temporal.
- Filtros por umbral, coherencia, fecha y uso del suelo.
- Módulo educativo sobre NISAR y la metodología.
- Trazabilidad completa mediante `provenance`.

### 2.2. Fuera del MVP

- Descarga y procesamiento de HDF5 en tiempo real.
- Procesamiento crudo L1 si existe un producto L2 o una fuente procesada.
- PostgreSQL/PostGIS.
- Backend FastAPI separado.
- Autenticación de usuarios.
- PMTiles, CDN y MapLibre como requisito inicial.

La arquitectura estática con MapLibre/deck.gl puede implementarse posteriormente sin modificar el
contrato de datos.

---

## 3. Arquitectura objetivo

```text
┌─────────────────────────────────────────────────────────────┐
│ CAPA 1 — PIPELINE OFFLINE                                  │
│                                                             │
│ Fuentes SAR/GIS                                             │
│        ↓                                                    │
│ Descubrimiento y descarga local                             │
│        ↓                                                    │
│ Recorte, reproyección y cálculo físico                      │
│        ↓                                                    │
│ Filtro de calidad y enriquecimiento GIS                     │
│        ↓                                                    │
│ Backend geoespacial: contrato, loaders y datos para mapas    │
└──────────────────────────┬──────────────────────────────────┘
                           │ interfaz de datos estable
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ CAPA 2 — FRONTEND STREAMLIT                                │
│                                                             │
│ Consume backend → filtros → mapa → KPIs → gráficos           │
│                                                             │
│ Sin descargas NASA ni procesamiento pesado                  │
└─────────────────────────────────────────────────────────────┘
```

### 3.1. Estructura propuesta (Corregida sin colisión UI/Backend)

```
├── app.py
├── src/
│   ├── data/
│   │   ├── loaders.py          # Rol 1: Lectura con @st.cache_data
│   │   └── validation.py       # Rol 1: Validación de esquemas
│   ├── domain/
│   │   ├── contracts.py        # Rol 1 (conenso 3 roles): Esquemas y tipos
│   │   ├── filters.py          # Rol 1: Filtrado rápido de DataFrames
│   │   ├── metrics.py          # Rol 1: Cálculo de KPIs agregados
│   │   └── map_adapter.py      # Rol 1: Prepara columnas RGBA y elevación para PyDeck
│   └── ui/
│       ├── map_view.py         # Rol 2 (fradiaz): Instancia PyDeck, capas, cámara y tooltips
│       ├── charts.py           # Rol 2 (fradiaz): Gráficos Plotly de series temporales
│       ├── kpi_cards.py        # Rol 2 (fradiaz): Tarjetas de métricas e insignias de provenance
│       └── educational.py      # Rol 2 (fradiaz): Pestaña/drawer sobre banda L, rugosidad y constante dieléctrica
├── pipeline/                   # Rol 3 (yuls): Scripts offline de ingesta y cálculo SAR
├── config/
│   └── study_cases.yaml
├── data/
│   ├── raw/                    # IGNORADO EN GIT (.gitignore) - Archivos HDF5/SAFE pesados
│   └── processed/              # VERSIONADO EN GIT (< 15 MB total) - Artefactos listos para Cloud
│       ├── main_case/
│       └── malaga_control/
├── tests/
└── requirements.txt
```

### 3.2. Reglas de modularidad

- `app.py` solo compone la aplicación.
- El pipeline no importa módulos de Streamlit.
- La UI no calcula magnitudes geoespaciales.
- Las métricas se calculan antes de llegar al cliente.
- Los parámetros científicos viven en configuración o metadatos.
- Cada módulo debe poder probarse sin ejecutar toda la aplicación.
- Los archivos de `data/raw/` nunca se versionan.

---

## 4. Responsabilidades por rol

La división se basa en el flujo real de dependencias:

```text
Rol 3 — Ciencia/GIS/producto
              ↓ datos científicos y contexto
Rol 1 — Backend geoespacial y mapas
              ↓ interfaz de datos estable y capas map-ready
Rol 2 — Frontend
```

El backend y el frontend colaboran directamente en la implementación del mapa. El backend define
qué datos necesita cada capa y el frontend decide cómo presentarlos e interactuarlos.

### Rol 1 — Backend geoespacial y mapas

**Responsable sugerido: pozo**

#### Responsabilidad principal

Convertir los artefactos científicos en una interfaz de datos preparada para mapas, métricas y
series temporales. Este rol no es un backend HTTP independiente: es la capa técnica que conecta
el pipeline con el frontend.

#### Tareas

1. Implementar `src/data/loaders.py` para cargar Parquet, CSV, GeoJSON y `meta.json`.
2. Implementar `src/data/validation.py` para validar esquema, unidades, CRS y valores nulos.
3. Definir los modelos de datos consumidos por la UI.
4. Generar los datasets map-ready:
   - coordenadas;
   - geometrías o índices H3;
   - valores coloreables;
   - altura de columnas;
   - categorías de riesgo;
   - capas contextuales.
5. Trabajar con el frontend en `src/ui/map_view.py`.
6. Definir cómo se filtran los datos sin recalcular el pipeline.
7. Preparar funciones para métricas, agregaciones y series.
8. Configurar caché de lectura.
9. Mantener el contrato de datos y su versión.
10. Configurar CI, pruebas de carga de datos y despliegue.

#### Entregables

```text
src/data/loaders.py
src/data/validation.py
src/domain/contracts.py
src/domain/filters.py
src/domain/metrics.py
src/ui/map_view.py
.github/workflows/
```

#### No debe hacer

- No modificar las ecuaciones científicas sin el Rol 3.
- No descargar HDF5 desde la aplicación.
- No introducir lógica de presentación duplicada en `app.py`.
- No crear una API independiente para el MVP.

### Rol 2 — Frontend y experiencia de usuario

**Responsable sugerido: fradiaz**

#### Responsabilidad principal

Consumir la interfaz de datos del backend y convertirla en una experiencia clara e interactiva.

#### Tareas

1. Implementar el esqueleto y la composición de `app.py`.
2. Crear los controles de usuario:
   - caso;
   - fecha;
   - umbral;
   - coherencia;
   - uso del suelo.
3. Consumir exclusivamente loaders y servicios del backend.
4. Implementar la presentación del mapa con PyDeck.
5. Implementar KPIs reactivos.
6. Implementar series temporales con Plotly.
7. Mostrar unidades, fuente y fecha de los datos.
8. Implementar el módulo educativo usando contenido aprobado.
9. Gestionar estado de interacción y accesibilidad.
10. Probar la aplicación contra el mock y contra datos reales.

#### Entregables

```text
app.py
src/ui/charts.py
src/ui/kpi_cards.py
src/ui/educational.py
.streamlit/config.toml
```

#### No debe hacer

- No leer archivos directamente desde múltiples componentes.
- No procesar HDF5.
- No calcular desplazamientos, velocidades o riesgos científicos.
- No modificar unidades ni umbrales recibidos del backend.
- No duplicar filtros o agregaciones del backend.

### Rol 3 — Ciencia SAR, GIS y producto

**Responsable sugerido: yuls**

#### Responsabilidad principal

Generar y validar la información científica y contextual que consume el backend, además de
garantizar que el producto comunica correctamente sus resultados.

#### Tareas

1. Verificar la cobertura real de NISAR para el caso elegido.
2. Elegir y documentar la fuente utilizada:
   - NISAR L2 GUNW;
   - NISAR L2 GCOV;
   - OPERA DISP-S1;
   - HyP3;
   - EGMS.
3. Generar el mock inicial.
4. Descargar y procesar los productos localmente.
5. Recortar el AOI y reproyectar los datos.
6. Aplicar filtros de calidad y cálculos físicos.
7. Preparar capas GIS de contexto e infraestructura.
8. Generar `provenance`, unidades, fechas y limitaciones.
9. Revisar con el backend que los artefactos sean adecuados para mapas.
10. Preparar narrativa, README, pitch y vídeo.

#### Entregables

```text
pipeline/
data/processed/nisar_grid.parquet
data/processed/timeseries.csv
data/processed/context_layers.geojson
data/processed/meta.json
pipeline/README.md
README.md
material de presentación
```

#### No debe hacer

- No escribir componentes de frontend.
- No introducir lógica de cálculo científico en la aplicación.
- No cambiar el contrato unilateralmente.
- No definir estilos visuales finales sin coordinar con backend y frontend.

## Responsabilidades compartidas

### Backend + ciencia/GIS

- Definir el esquema de datos.
- Validar unidades, CRS y provenance.
- Confirmar que las capas sirven para PyDeck.
- Revisar métricas y umbrales.

### Backend + frontend

- Definir la interfaz de datos que consumirá la UI.
- Acordar nombres y tipos de campos.
- Integrar el mapa.
- Revisar filtros y rendimiento.
- Resolver incompatibilidades sin trasladar cálculos al frontend.

### Ciencia/GIS + frontend

- Validar textos, leyendas y colores.
- Revisar que los resultados no se sobreinterpreten.
- Comprobar que Málaga se muestre como control negativo.

### Todos

- Revisar cambios al contrato.
- Probar la aplicación antes de integrar.
- Mantener `main` ejecutable.

---

## 5. Contrato de datos

El contrato se congela antes de comenzar el desarrollo paralelo.

### 5.1. Contrato actualizado: `nisar_grid.parquet` (Optimizado para PyDeck)

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---:|---|
| `cell_id` | string | Sí | Identificador de celda o índice H3 |
| `lat` | float | Sí | Latitud del centroide en EPSG:4326 |
| `lon` | float | Sí | Longitud del centroide en EPSG:4326 |
| `val_t1` | float | Sí | Valor en fecha inicial ($T_1$) |
| `val_t2` | float | Sí | Valor en fecha final ($T_2$) |
| `delta_change` | float | Sí | Cambio neto ($T_2 - T_1$) |
| `velocity_mm_yr` | float | Sí | Tendencia secular ajustada en mm/año |
| `seasonal_amp_mm` | float | Sí | Amplitud estacional ajustada en mm |
| `ratio_R` | float | Sí | `abs(velocity_mm_yr) / seasonal_amp_mm` |
| `regime` | string | Sí | `elastic`, `transitional` o `inelastic` |
| `coherence` | float | Sí | Coherencia interferométrica / calidad entre 0.0 y 1.0 |
| `land_use` | string | Sí | Categoría territorial (urbano, agrícola, humedal, infraestructura) |
| `risk_level` | string | Sí | `estable`, `moderado` o `critico` |
| `case_id` | string | Sí | Identificador del caso (`mexico-city`, `jakarta`, `malaga`) |
| `sensor` | string | Sí | Sensor origen (`nisar`, `sentinel-1`) |
| `product` | string | Sí | Producto declarado por el contrato |
| `unit` | string | Sí | Unidad física declarada (`mm`, `mm/year`, `dB`) |
| `source` | string | Sí | Repositorio o agencia (`ASF DAAC`, `Copernicus`) |
| `provenance_id` | string | Sí | Hash o ID trazable al bloque `provenance` de `meta.json` |

### 5.2. `timeseries.csv`

Debe incluir como mínimo:

```text
case_id
cell_id
date
value
trend_mm_yr
seasonal_mm
unit
sensor
product
quality
provenance_id
```

### 5.3. `meta.json`

```json
{
  "schema_version": "1.1",
  "case_id": "mexico-city",
  "sensor": "sentinel-1",
  "product": "nisar-gunw",
  "unit": "mm/year",
  "crs": "EPSG:4326",
  "dates": [
    "2026-01-12",
    "2026-01-24"
  ],
  "revisit_days": 12,
  "stats": {
    "median_velocity_mm_yr": -128.0,
    "median_seasonal_amp_mm": 9.4,
    "median_ratio_R": 13.617,
    "dominant_regime": "inelastic"
  },
  "provenance": {
    "source": "ASF DAAC",
    "product_id": "",
    "processing_date": "",
    "method": ""
  }
}
```

### 5.4. Reglas del contrato

- `delta_change` siempre debe tener una unidad declarada.
- No se pueden mezclar sensores sin identificarlos.
- Los umbrales deben depender del producto y del sensor.
- El mock debe respetar exactamente el mismo esquema.
- Todo artefacto real debe incluir `provenance`.
- Los valores nulos y no válidos deben documentarse.
- Una modificación requiere revisión de los tres roles.

---

## 6. Estrategia de ramas Git

Se utilizará un modelo de ramas cortas basado en `main`.

```text
main
├── feat/data-contract
├── feat/pipeline-mock
├── feat/pipeline-real-source
├── feat/gis-context
├── feat/backend-map-data
├── feat/ui-shell
├── feat/ui-map
├── feat/ui-charts
├── chore/ci-validation
└── release/mvp
```

### 6.1. Reglas

1. `main` siempre debe poder ejecutarse.
2. Cada rama debe tener un objetivo único.
3. No se trabaja directamente sobre `main`.
4. Cada rama se integra mediante Pull Request.
5. Las ramas deben durar horas, no días.
6. Los artefactos pesados no se suben al repositorio.
7. Los cambios al contrato requieren aprobación de los tres roles.
8. Los conflictos se resuelven en la rama que los introduce.
9. No se mezclan cambios de UI, pipeline y documentación en un mismo PR salvo que sean
   inseparables.

### 6.2. Orden recomendado de integración

```text
1. feat/data-contract
2. feat/pipeline-mock
3. feat/gis-context
4. feat/backend-map-data
5. feat/ui-shell
6. feat/ui-map y feat/ui-charts
7. feat/pipeline-real-source
8. chore/ci-validation
9. release/mvp
```

El mock permite que frontend y producto trabajen sin esperar a los datos reales.

### 6.3. Convención de commits

```text
feat: add mock data generator
feat: add interactive map
fix: handle missing coherence values
docs: describe data provenance
test: validate processed schema
chore: configure deployment
```

---

## 7. Flujo de trabajo por fases

### Fase 0 — Sincronización

#### Objetivo

Eliminar decisiones ambiguas antes de escribir código.

#### Acciones

- Elegir el caso principal.
- Confirmar cobertura de NISAR.
- Definir fuente de respaldo.
- Congelar el contrato.
- Confirmar unidades.
- Crear ramas.
- Crear issues o tareas por entregable.

#### Criterio de salida

Los tres integrantes pueden ejecutar y entender el mock sin depender de datos externos.

---

### Fase 1 — Mock funcional

#### Datos

El Rol 3 genera:

```text
nisar_grid.parquet
timeseries.csv
context_layers.geojson
meta.json
```

#### Aplicación

El Rol 2 muestra:

- mapa;
- KPIs;
- gráfico;
- filtros básicos.

#### Producto

El Rol 3 revisa:

- categorías;
- textos;
- AOI;
- coherencia visual.

#### Criterio de salida

La aplicación arranca localmente usando solo datos mock.

---

### Fase 2 — Desarrollo paralelo

| Rol | Trabajo principal |
|---|---|
| Backend geoespacial y mapas | Loaders, validación, filtros, métricas y capas map-ready |
| Frontend | Consumo de datos, controles, mapa, KPIs y gráficos |
| Ciencia SAR, GIS y producto | Fuente real, cálculo físico, contexto y narrativa |

Cada rol debe trabajar contra la versión congelada del contrato.

El backend y el frontend deben integrar el mapa juntos: el backend expone los datos y atributos
necesarios, mientras el frontend implementa la interacción y la presentación visual.

---

### Fase 3 — Integración de datos reales

1. Ejecutar el pipeline desde cero.
2. Comparar el esquema real con el mock.
3. Validar unidades y fechas.
4. Verificar `provenance`.
5. Sustituir los artefactos mock.
6. Ejecutar la aplicación sin cambiar la UI.
7. Revisar las métricas y leyendas.

Si los datos reales no son compatibles, se adapta el pipeline, no se modifica silenciosamente la
aplicación.

---

### Fase 4 — Validación y despliegue

Validar:

- instalación de dependencias;
- ejecución del pipeline;
- esquema de datos;
- ausencia de valores inválidos;
- carga de la aplicación;
- comportamiento de filtros;
- tiempo de carga;
- funcionamiento de la URL pública;
- visualización en un entorno limpio.

---

### Fase 5 — Code freeze

Después del freeze solo se aceptan:

- correcciones críticas;
- errores de despliegue;
- problemas de datos;
- ajustes visuales pequeños.

No se aceptan nuevas funcionalidades ni cambios de arquitectura.

---

## 8. Pull Requests

Cada Pull Request debe incluir:

```text
Objetivo:
Archivos modificados:
Dependencias nuevas:
Impacto sobre el contrato:
Validaciones ejecutadas:
Riesgos o limitaciones:
Captura o resultado visual, si aplica:
```

### Revisiones mínimas

| Tipo de cambio | Revisores |
|---|---|
| Contrato de datos | Los tres roles |
| Backend y datos para mapas | Rol 1 + Rol 3 |
| Frontend y mapa | Rol 2 + Rol 1 |
| Ciencia, GIS y narrativa | Rol 3 + Rol 1 |
| CI/despliegue | Rol 1 + Rol 2 |

---

## 9. Criterios de aceptación

### Pipeline

- Es reproducible.
- Funciona sin componentes de Streamlit.
- No versiona datos pesados.
- Incluye fuente, sensor, unidad y fechas.
- Aplica filtro de calidad.
- Produce métricas verificables.
- Registra `provenance`.

### Aplicación

- Arranca sin credenciales NASA.
- Consume artefactos locales.
- Actualiza mapa, KPIs y gráficos al cambiar filtros.
- No ejecuta procesamiento pesado.
- Muestra unidades y fuente.
- No mezcla sensores o escalas sin advertencia.

### GIS y producto

- Las capas tienen CRS definido.
- Las categorías territoriales están documentadas.
- Las afirmaciones diferencian datos de interpretación.
- Las limitaciones aparecen en la interfaz o documentación.

### Integración

- PR revisado.
- Tests ejecutados.
- Contrato validado.
- Sin archivos pesados.
- `main` sigue siendo ejecutable.

---

## 10. Checklist inicial

### Antes de empezar

- [ ] Elegir caso principal.
- [ ] Confirmar cobertura y fuente.
- [ ] Crear ramas.
- [ ] Congelar contrato.
- [ ] Definir unidades y umbrales.
- [ ] Crear estructura de carpetas.

### Primer bloque

- [ ] Generar mock.
- [ ] Levantar `app.py`.
- [ ] Preparar AOI.
- [ ] Configurar CI mínimo.
- [ ] Verificar instalación local.

### Antes de integrar datos reales

- [ ] Validar esquema.
- [ ] Confirmar `provenance`.
- [ ] Revisar CRS.
- [ ] Revisar nulos.
- [ ] Revisar unidades.

### Antes del despliegue

- [ ] Ejecutar pipeline desde cero.
- [ ] Probar aplicación con artefactos definitivos.
- [ ] Ejecutar validaciones.
- [ ] Comprobar URL pública.
- [ ] Revisar narrativa y limitaciones.

---

## 11. Decisión final de arquitectura

La solución recomendada es:

> **Pipeline científico independiente + contrato de datos versionado + aplicación Streamlit
> modular + ramas cortas por responsabilidad + integración mediante Pull Requests.**

Esta decisión permite que los integrantes trabajen en paralelo, evita conflictos y mantiene
abierta la migración futura a una arquitectura estática con teselas sin rehacer el pipeline ni el
contrato de datos.
