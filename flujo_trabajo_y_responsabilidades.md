# Flujo de trabajo, arquitectura y responsabilidades

## NASA Space Apps Challenge 2026 — Dancing with the SARs

Documento operativo para iniciar el desarrollo de forma paralela, modular y escalable.

Fuentes de consolidación:

- [informe_readme.md](./informe_readme.md)
- [informe_maestro_de_arquitectura_y_plan_de_choque.md](./informe_maestro_de_arquitectura_y_plan_de_choque.md)

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

- Un caso de estudio principal ejecutado correctamente.
- Una segunda ubicación precargada si el tiempo lo permite.
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
│ Artefactos ligeros en data/processed/                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ contrato de datos
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ CAPA 2 — APLICACIÓN STREAMLIT                              │
│                                                             │
│ Carga de datos → filtros → métricas → mapa → gráficos       │
│                                                             │
│ Sin descargas NASA ni procesamiento pesado                  │
└─────────────────────────────────────────────────────────────┘
```

### 3.1. Estructura propuesta

```text
.
├── app.py
├── src/
│   ├── data/
│   │   ├── loaders.py
│   │   └── validation.py
│   ├── domain/
│   │   ├── contracts.py
│   │   ├── filters.py
│   │   └── metrics.py
│   └── ui/
│       ├── map_view.py
│       ├── charts.py
│       ├── kpi_cards.py
│       └── educational.py
├── pipeline/
│   ├── generate_mock.py
│   ├── discover_sources.py
│   ├── process_product.py
│   ├── enrich_context.py
│   └── export_artifacts.py
├── config/
│   └── study_cases.yaml
├── data/
│   ├── raw/
│   └── processed/
├── tests/
├── .github/
│   └── workflows/
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

## Rol 1 — Datos SAR y pipeline científico

### Responsabilidad principal

Convertir productos SAR y datos auxiliares en artefactos ligeros, reproducibles y científicamente
trazables.

### Tareas

1. Verificar la cobertura real de NISAR para el área elegida.
2. Elegir y documentar la fuente utilizada:
   - NISAR L2 GUNW;
   - NISAR L2 GCOV;
   - OPERA DISP-S1;
   - HyP3;
   - EGMS;
   - otra fuente aprobada y documentada.
3. Generar el mock inicial durante el primer bloque de trabajo.
4. Consultar y descargar los productos localmente.
5. Inspeccionar HDF5 y extraer únicamente los grupos necesarios.
6. Recortar el área de interés.
7. Reproyectar y normalizar coordenadas.
8. Aplicar filtros de calidad, incluido el umbral de coherencia.
9. Calcular desplazamiento, cambio de retrodispersión o velocidad.
10. Generar los artefactos procesados.
11. Añadir provenance, unidades, fechas y versión del procesamiento.
12. Documentar las limitaciones científicas.

### Entregables

```text
pipeline/
data/processed/nisar_grid.parquet
data/processed/timeseries.csv
data/processed/meta.json
pipeline/README.md
```

### No debe hacer

- No modificar componentes de UI.
- No introducir cálculos científicos dentro de `app.py`.
- No cambiar el contrato sin Pull Request.
- No subir HDF5 ni archivos pesados.

---

## Rol 2 — Frontend y visualización interactiva

### Responsabilidad principal

Construir una experiencia rápida, clara y explicativa usando exclusivamente los artefactos
producidos por el pipeline.

### Tareas

1. Crear el esqueleto de `app.py`.
2. Configurar el tema visual de Streamlit.
3. Implementar la carga validada de datos.
4. Implementar el selector de caso.
5. Implementar filtros de:
   - fecha;
   - umbral;
   - coherencia;
   - uso del suelo.
6. Crear tarjetas KPI reactivas.
7. Crear el mapa PyDeck 2D/3D.
8. Crear la serie temporal con Plotly.
9. Mostrar unidades y fuente de cada variable.
10. Gestionar estados de interacción sin recalcular datos pesados.
11. Aplicar caché de lectura.
12. Verificar la experiencia en la URL de producción.

### Entregables

```text
app.py
src/ui/map_view.py
src/ui/charts.py
src/ui/kpi_cards.py
src/ui/educational.py
.streamlit/config.toml
```

### No debe hacer

- No descargar datos de NASA.
- No procesar HDF5.
- No calcular desplazamientos o velocidades.
- No modificar umbrales científicos de forma arbitraria.
- No asumir unidades que no estén en `meta.json`.

---

## Rol 3 — GIS, producto y storytelling

### Responsabilidad principal

Conectar los resultados SAR con el territorio, el impacto social y la comunicación para el jurado.

### Tareas

1. Delimitar el área de interés.
2. Preparar capas de infraestructura y uso del suelo.
3. Verificar CRS y geometrías.
4. Preparar `context_layers.geojson`.
5. Definir categorías territoriales.
6. Revisar la asociación entre anomalías y contexto.
7. Redactar el módulo educativo.
8. Revisar que las afirmaciones distingan:
   - observación;
   - interpretación;
   - limitación.
9. Preparar README, pitch y vídeo.
10. Revisar que Málaga se presente como control negativo y no como fallo.

### Entregables

```text
pipeline/enrich_context.py
data/processed/context_layers.geojson
src/ui/educational.py
README.md
material de presentación
```

### No debe hacer

- No modificar ecuaciones ni parámetros científicos sin coordinación con el Rol 1.
- No introducir cálculos de radar en el frontend.
- No cambiar el esquema del contrato unilateralmente.

---

## Responsabilidad compartida de infraestructura

Esta responsabilidad puede asumirla el integrante que gestione el repositorio, sin crear un
backend separado.

### Tareas

- Configurar GitHub Actions.
- Ejecutar validaciones automáticas.
- Comprobar el tamaño de los artefactos.
- Verificar que la aplicación arranca.
- Configurar Streamlit Community Cloud.
- Comprobar la URL pública.
- Documentar el proceso de despliegue.

### Fuera de alcance

- API independiente.
- Base de datos.
- Orquestador complejo.
- Infraestructura cloud avanzada.

---

## 5. Contrato de datos

El contrato se congela antes de comenzar el desarrollo paralelo.

### 5.1. `nisar_grid.parquet`

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---:|---|
| `cell_id` | string | Sí | Identificador de celda o índice H3 |
| `lat` | float | Sí | Latitud en EPSG:4326 |
| `lon` | float | Sí | Longitud en EPSG:4326 |
| `val_t1` | float | Sí | Valor inicial |
| `val_t2` | float | Sí | Valor final |
| `delta_change` | float | Sí | Cambio neto |
| `coherence` | float | Sí | Índice de calidad entre 0 y 1 |
| `land_use` | string | Sí | Categoría territorial |
| `risk_level` | string | Sí | Estable, moderado o crítico |
| `case_id` | string | Sí | Identificador del caso |
| `sensor` | string | Sí | NISAR o Sentinel-1 |
| `product` | string | Sí | GCOV, GUNW, DISP-S1, EGMS, etc. |
| `unit` | string | Sí | mm, mm/year, dB u otra unidad declarada |
| `source` | string | Sí | Fuente del producto |
| `acquisition_date` | string | No | Fecha de adquisición |
| `provenance_id` | string | Sí | Referencia trazable al procesamiento |

### 5.2. `timeseries.csv`

Debe incluir como mínimo:

```text
case_id
date
value
unit
sensor
product
quality
provenance_id
```

### 5.3. `meta.json`

```json
{
  "schema_version": "1.0",
  "case_id": "mexico-city",
  "sensor": "sentinel-1",
  "product": "opera-disp-s1",
  "unit": "mm/year",
  "crs": "EPSG:4326",
  "dates": [
    "2026-01-12",
    "2026-01-24"
  ],
  "coherence_threshold": 0.35,
  "provenance": {
    "source": "ASF DAAC",
    "product_id": "",
    "processing_date": "",
    "method": ""
  },
  "stats": {}
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
3. feat/ui-shell
4. feat/ui-map y feat/ui-charts
5. feat/gis-context
6. feat/pipeline-real-source
7. chore/ci-validation
8. release/mvp
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

## Fase 0 — Sincronización

### Objetivo

Eliminar decisiones ambiguas antes de escribir código.

### Acciones

- Elegir el caso principal.
- Confirmar cobertura de NISAR.
- Definir fuente de respaldo.
- Congelar el contrato.
- Confirmar unidades.
- Crear ramas.
- Crear issues o tareas por entregable.

### Criterio de salida

Los tres integrantes pueden ejecutar y entender el mock sin depender de datos externos.

---

## Fase 1 — Mock funcional

### Datos

El Rol 1 genera:

```text
nisar_grid.parquet
timeseries.csv
context_layers.geojson
meta.json
```

### Aplicación

El Rol 2 muestra:

- mapa;
- KPIs;
- gráfico;
- filtros básicos.

### Producto

El Rol 3 revisa:

- categorías;
- textos;
- AOI;
- coherencia visual.

### Criterio de salida

La aplicación arranca localmente usando solo datos mock.

---

## Fase 2 — Desarrollo paralelo

| Rol | Trabajo principal |
|---|---|
| Datos SAR | Fuente real, recorte, cálculo físico y artefactos |
| Frontend | Interacción completa, mapa, KPIs y gráficos |
| GIS/producto | Capas, narrativa y módulo educativo |
| Infraestructura | CI, validaciones y despliegue |

Cada rol debe trabajar contra la versión congelada del contrato.

---

## Fase 3 — Integración de datos reales

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

## Fase 4 — Validación y despliegue

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

## Fase 5 — Code freeze

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
| Pipeline científico | Rol 1 + Rol 3 |
| Frontend | Rol 2 + un segundo integrante |
| GIS y narrativa | Rol 3 + Rol 1 |
| CI/despliegue | Responsable de infraestructura + Rol 2 |

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
