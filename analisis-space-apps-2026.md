# NASA Space Apps 2026: análisis comparativo de dos challenges

**Challenges analizados:**
1. *Dancing with the SARs* (radar NISAR, cambios en la superficie terrestre)
2. *Field Shift: Adapting Farms with NASA Data* (rotación de cultivos y resiliencia agrícola)

**Fecha del análisis:** sábado 14 de noviembre de 2026

> **Nota sobre las fuentes.** Los enunciados y las listas de recursos de ambos challenges proceden del texto oficial que pegó el equipo. Lo marcado como *(inferencia)* o *(conocimiento general, verificar)* es valoración propia y no figura en los enunciados. El cierre de entregas (domingo 15 de noviembre, 23:59 hora local del evento) procede del repositorio de un equipo participante, no de la web oficial: confirmarlo allí.

---

## 1. Resumen ejecutivo

| | Dancing with the SARs | Field Shift |
|---|---|---|
| Qué se pide | Aplicación interactiva que rastree y visualice cambios de la superficie con datos de radar NISAR | Herramienta de apoyo a decisiones que compare al menos tres rotaciones de cultivo (una, la actual) |
| Dificultad oficial | Intermedia / Avanzada | Intermedia / Avanzada |
| Temas oficiales | Earth Science | Arts & Multimedia, Earth Science, Software |
| Naturaleza | Observación y visualización científica | Modelado, puntuación y producto de decisión |
| Riesgo principal | Disponibilidad de datos NISAR y rigor al interpretar el radar | Credibilidad agronómica y datos de suelo fuera de EE. UU. |
| Recomendación condicional | Elegirlo si el equipo tiene perfil de teledetección y busca profundidad científica | Elegirlo si el equipo es más de datos, software y producto, y busca utilidad directa |

**Conclusión provisional *(inferencia)*:** Field Shift es más seguro en 48 horas para un equipo sin experiencia en radar, porque el trabajo es sobre todo de datos tabulares, APIs y diseño de producto. Dancing with the SARs ofrece más profundidad científica pero concentra más riesgo técnico.

---

## 2. Challenge 1: Dancing with the SARs

### 2.1 Qué pide el enunciado
- Construir una aplicación interactiva con datos de radar de la misión NASA-ISRO NISAR para **rastrear y visualizar uno o más tipos de cambio** en la superficie, en ubicaciones de todo el mundo.
- Resultado deseado: solución **de código abierto y fácil de usar** que traduzca observaciones complejas de radar en información accionable para científicos, gestores de recursos, educadores, decisores y público general.
- Pasos propuestos: elegir una zona de estudio, descargar datos NISAR, añadir datos auxiliares (óptico, GIS) y presentar los hallazgos en el medio digital preferido (informe, vídeo, presentación, story map).
- **Formular hipótesis sobre los factores físicos** detrás de lo observado, y responder qué polarizaciones resaltan los cambios y qué aplicaciones futuras tendría NISAR.
- Opcional: distintas polarizaciones o frecuencias, series temporales, datos de población o vegetación, storytelling, interactividad (sliders, mapas web).

### 2.2 Tipos de cambio que menciona
Humedales e inundaciones, deforestación e incendios, cultivos y humedad del suelo, glaciares y hielo marino, terremotos y subsidencia costera.

### 2.3 Recursos oficiales
- NASA Earthdata Search y **ASF Vertex** (archivo NASA de datos SAR)
- **NISAR Data User Guide** (ASF)
- Introducciones a teledetección y a SAR de Earthdata; formaciones NASA ARSET (con material en español)
- NISAR StoryMaps (inundaciones, incendios, subsidencia, vertidos, hielo) y white papers de aplicaciones
- UAVSAR Postcards
- Socios: RADARSAT Constellation Mission y tutorial de SAR en Python (CSA); catálogo SAOCOM (CONAE)

### 2.4 Magnitud del trabajo
Tres capas encadenadas:
1. **Datos:** cuenta Earthdata, búsqueda en Vertex, descarga y lectura de productos (pueden ser de varios GB).
2. **Interpretación:** vincular retrodispersión o fase con la física (rugosidad y constante dieléctrica; el agua destaca por su alta constante dieléctrica).
3. **Entrega:** visualización, relato y repositorio abierto.

### 2.5 Enfoques viables

| Tipo de cambio | Técnica | Dificultad *(inferencia)* | Observación |
|---|---|---|---|
| Inundaciones y humedales | Cambio de retrodispersión entre fechas | Media-baja | Historia clara; slider antes/después |
| Incendios y deforestación | Retrodispersión por polarización | Media | Responde a la pregunta sobre polarizaciones |
| Agricultura | Series temporales | Media | Exige varias fechas |
| Terremotos y subsidencia | InSAR (fase) | Alta | Ruido atmosférico y procesado delicado |
| Glaciares y hielo | InSAR o seguimiento de desplazamiento | Alta | Muy vistoso y el más arriesgado |

### 2.6 Riesgos
- Puede no haber suficientes adquisiciones NISAR en la zona y fechas elegidas. **Comprobarlo en Vertex antes de decidir.**
- La fase InSAR sufre ruido atmosférico, ionosférico y decorrelación; interpretarla mal da resultados bonitos pero incorrectos.
- Mezclar sensores de socios (RCM, SAOCOM) añade trabajo de armonización.

### 2.7 Resultados que se pueden obtener
- Mapa web con **slider antes/después** de un evento (inundación, incendio).
- **Serie temporal animada** de retrodispersión sobre una zona.
- Panel con la **interpretación física** y la comparación de polarizaciones.
- Story map o vídeo corto con el relato de la zona.
- Repositorio abierto con pipeline reproducible.

---

## 3. Challenge 2: Field Shift

### 3.1 Qué pide el enunciado
- Crear una **herramienta de apoyo a decisiones** (app, modelo, mapa interactivo u otra) que combine observaciones de la Tierra de NASA con información local de suelo, características de cultivos y **prioridades del agricultor**, para explorar estrategias de rotación que refuercen la salud del suelo y la adaptación al cambio climático.
- **Comparar al menos tres estrategias de rotación**, incluida la actual. Para cada una: por qué se sugiere, qué evidencia la respalda, qué beneficios ofrece, dónde hay lagunas de información y qué riesgos requieren revisión local.
- Mostrar periodos de campo desnudo, estaciones de mayor vulnerabilidad de los cultivos actuales y oportunidades (romper ciclos de plagas, reducir demanda de agua, mejorar infiltración, ingresos complementarios).
- Explicar cómo interpreta el modelo la **incertidumbre** y dónde se necesita validación de expertos locales.
- Principio rector: una herramienta de **apoyo que no sustituye** el criterio del agricultor ni la experiencia agronómica local.

### 3.2 Datos NASA sugeridos
SMAP (humedad del suelo), GPM/IMERG (precipitación), Landsat y MODIS/VIIRS (NDVI, estrés vegetal, suelo desnudo), NASA POWER (agroclimatología), SRTM (elevación y pendiente), ECOSTRESS (temperatura superficial y evapotranspiración), GRACE/GRACE-FO (agua), GEDI (estructura de vegetación), NLDAS/GLDAS (variables de superficie).

### 3.3 Datos complementarios en los recursos

| Ámbito | Fuentes |
|---|---|
| Suelo | Web Soil Survey, SSURGO/STATSGO2 (EE. UU.) |
| Cultivos y mercado | Cropland Data Layer, NASS QuickStats, AMS Market News (EE. UU.) |
| Clima | PRISM (EE. UU.), ERA5 (global), CoCoRaHS (Norteamérica) |
| Agua | OpenET (oeste de EE. UU.), USGS NWIS (EE. UU.) |
| Imagen y cobertura | NAIP (EE. UU.), NLCD (EE. UU.), LANDFIRE (EE. UU.), Sentinel-2 (global) |
| Aptitud de cultivos | FAO GAEZ (global) |
| Datos agrícolas generales | USDA Ag Data Commons |

### 3.4 Punto crítico: región piloto
Casi todas las fuentes de suelo, cultivos, clima local y mercado son **exclusivas de EE. UU.** Las de cobertura global son: NASA POWER, IMERG, SMAP, MODIS/VIIRS, Landsat, SRTM, GLDAS, ERA5, Sentinel-2 y GAEZ. Para suelos fuera de EE. UU. falta fuente en la lista; una alternativa es SoilGrids de ISRIC *(conocimiento general, verificar)*.

Hay que decidir pronto entre:
- **Piloto en EE. UU.:** más datos y precisión, menos transferible.
- **Piloto fuera de EE. UU.:** más cercano al equipo, pero con datos de suelo y cultivos más pobres.

### 3.5 Límites de escala *(conocimiento general, verificar)*
SMAP y GRACE tienen resolución gruesa; ECOSTRESS no cubre todos los días; GEDI sirve sobre todo para vegetación alta. Por ello el piloto realista es **a escala comarcal**, no parcela a parcela.

### 3.6 Enfoque viable
- **Capas mínimas:** NASA POWER, NDVI (MODIS, Landsat o Sentinel-2), SMAP o GLDAS, IMERG, SRTM, un dato de suelo y la rotación actual.
- **Modelo transparente basado en reglas y puntuación**, no una caja negra. Una tabla curada de unos 8-10 atributos por cultivo (necesidad de agua, fijación de nitrógeno, profundidad de raíz, ciclo, riesgo de plaga), ponderada con las prioridades del agricultor.
- El enunciado usa el término "SI" sin definirlo; conviene aclarar su alcance (por ejemplo, si incluye explicaciones generadas por IA).
- **Extras:** ECOSTRESS, GRACE, GEDI, escenarios de lluvia y temperatura.

### 3.7 Resultados que se pueden obtener
- **Mapa interactivo** con capas conmutables: NDVI estacional, calendario de campos desnudos, pendiente y riesgo de erosión, anomalías de humedad.
- **Panel comparador de tres rotaciones** con evidencia, beneficios, lagunas y riesgos por cada una.
- **Deslizadores de prioridades** (agua, ingresos, estructura del suelo, erosión) que reordenan las recomendaciones.
- **Escenarios** (más seco, más cálido) para ver la sensibilidad de cada rotación.
- Avisos claros de incertidumbre y de puntos donde hace falta criterio local.
- Repositorio abierto con tabla de cultivos y metodología documentada.

### 3.8 Riesgos
- **Credibilidad agronómica:** una recomendación errónea tiene coste real. Se mitiga con transparencia, avisos y revisión de expertos.
- La tabla de cultivos exige curación cuidadosa y es la parte más difícil de acotar.
- Fuentes muchas y heterogéneas: riesgo de dispersión.

---

## 4. Comparativa de resultados

| Dimensión | Dancing with the SARs | Field Shift |
|---|---|---|
| Producto central | Visor de cambio con interpretación física | Herramienta de decisión con comparación de rotaciones |
| Quién lo usa | Científicos, gestores de recursos, educadores, público | Agricultores, cooperativas, servicios de extensión |
| Decisión que mejora | Entender y comunicar un cambio ya ocurrido | Elegir qué cultivar y cuándo |
| Complejidad del dato | Alta (productos pesados, física del radar) | Baja-media (APIs y series), pero muchas fuentes |
| Elemento diferencial *(inferencia)* | Rigor científico y claridad al explicar el radar | Utilidad práctica, incertidumbre explícita y diseño centrado en el agricultor |
| Sensibilidad a la región | Depende de la cobertura de NISAR | Depende de la disponibilidad de datos de suelo y cultivos |
| Riesgo de entrega en 48 h | Alto si el equipo no domina radar | Medio, si se acota a un piloto |
| Extras visuales | Slider antes/después, series animadas | Mapas de vulnerabilidad, escenarios, comparador |
| Temas oficiales | Earth Science | Earth Science, Software, Arts & Multimedia |

---

## 5. Aplicaciones y beneficios posibles

### 5.1 Dancing with the SARs
- **Respuesta a desastres:** mapeo de inundaciones y daños con radar, que funciona de día y de noche y con nubes.
- **Gestión del territorio:** vigilancia de deforestación, incendios y humedales.
- **Infraestructuras y riesgo:** detección de subsidencia y deformación.
- **Criosfera:** seguimiento de glaciares y hielo.
- **Educación y comunicación:** traducir datos complejos a historias accesibles para público no técnico.
- **Beneficio de fondo:** democratizar el acceso a datos de radar abiertos.

### 5.2 Field Shift
- **Adaptación climática:** rotaciones más resistentes a sequía, calor y lluvias extremas.
- **Salud del suelo:** más materia orgánica, menos erosión, mejor infiltración.
- **Gestión del agua:** reducir la demanda hídrica y planificar el riego.
- **Economía del agricultor:** diversificar ingresos y encajar con el mercado local.
- **Extensión agraria:** herramienta de apoyo para técnicos y cooperativas.
- **Beneficio de fondo:** convertir décadas de datos satelitales abiertos en decisiones de campo.

### 5.3 Viabilidad futura *(inferencia)*

| Criterio | Dancing with the SARs | Field Shift |
|---|---|---|
| Datos a largo plazo | Misión nueva, archivo creciente | Series largas y abiertas |
| Validación | Contraste con eventos documentados y otros sensores | Contraste con agrónomos y pilotos reales |
| Escalado | Global, limitado por cobertura de adquisiciones | Global con datos abiertos y tabla de cultivos ampliable |
| Mantenimiento | Código abierto y comunidad científica | Código abierto con una entidad agraria como aliada |
| Barrera principal | Conocimiento especializado de radar | Confianza del agricultor y rigor agronómico |

---

## 6. Recomendación y decisión

- **Elegir Field Shift** si el equipo es más de datos, software y producto, quiere una utilidad real directa y acepta construir una tabla de cultivos y validar con criterios locales.
- **Elegir Dancing with the SARs** si hay alguien con experiencia en teledetección y se prefiere profundidad científica a costa de más riesgo técnico.
- **No combinar ambos:** ampliaría el alcance sin necesidad en 48 horas.

### Decisiones abiertas para cerrar hoy
1. **Región piloto** (define qué datos de suelo y cultivos se pueden usar).
2. **Composición y perfiles del equipo.**
3. **Tres rotaciones concretas a comparar** (la actual más dos alternativas).
4. **Confirmar el cierre de entregas** en la web oficial.

---

## 7. Plan de trabajo orientativo (hasta el cierre)

**Primeras horas**
- Fijar región y tres rotaciones.
- Comprobar acceso a datos (cuenta Earthdata, primeras descargas o llamadas a API).

**Mediodía a tarde del sábado**
- Pipeline de datos mínimo y primera capa visible en el mapa.
- Tabla de atributos de cultivos y esquema de puntuación.

**Noche del sábado**
- Primer prototipo de punta a punta, aunque sea tosco.

**Domingo**
- Pulir comparador, avisos de incertidumbre y claridad de interfaz.
- README, vídeo o presentación y repositorio abierto.
- Envío con margen antes del cierre.

---

## 8. Fuentes

- Enunciados y recursos oficiales de ambos challenges, copiados por el equipo desde spaceappschallenge.org.
- Listado de challenges de HackAthlone 2026: https://hackathlone.com/challenges
- Repositorios públicos de equipos (para enfoques y fecha de cierre no oficial):
  - https://github.com/iams1am/TerraCascade-Dancing-with-the-SARs-NASA
  - https://github.com/CodexUjayer/dancing-with-sars
  - https://github.com/dmanpark/dancing-with-the-sars
  - https://github.com/iamtanim0195/nasa1
