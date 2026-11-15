# Plan de rediseño del cockpit — pantalla 1 ("1. Map")

Fase 0 del pack `docs/opencode_pack/PROMPT_rediseno_cockpit.md`. **Sin código**: fija sujeto,
audiencia, trabajo de la pantalla, tokens existentes y los dos wireframes que la Fase 1 implementa.
Fuente de verdad de la presentación: `docs/presentation-integration.md`; este documento solo decide
jerarquía y geometría.

## 1. Sujeto y audiencia

- **Sujeto:** un cockpit de interferometría SAR que traduce observaciones NISAR en indicadores de
  subsidencia por extracción de agua subterránea, con Ciudad de México como caso primario, Yakarta como
  extensión costera y Málaga como control negativo declarado.
- **Audiencia:** jurado de la NASA Space Apps, no técnico, que decide en menos de 90 segundos si el
  proyecto mide algo real y si lo puede verificar. No lee código ni el README.
- **Consecuencia de diseño:** la pantalla debe responder, sin scroll: *¿dónde se hunde, cuánto, y qué
  tan confiable es ese número?* La respuesta visual es **el mapa** (dónde) y **la serie** (cuánto y con
  qué patrón). Todo lo demás —KPIs de caso, comparación, contexto— es soporte.

## 2. Trabajo principal de la pantalla 1

1. Ver el patrón espacial del hundimiento sobre la ciudad con una escala honesta y su leyenda.
2. Elegir una celda y leer su serie temporal como elemento grande, con su procedencia.
3. Poder verificar cualquier cifra: unidad y `provenance_id` a la vista.

El aviso **DEMONSTRATION DATA** queda permanente sobre todo lo anterior (regla dura del repo).

## 3. Tokens: los que ya existen (no se inventan)

| Token | Oscuro (instrumento) | Claro (papel) | Uso |
|---|---|---|---|
| `--bg` | `#0B0F19` | `#F1F5F9` | fondo de app |
| `--surface` | `#0F172A` | `#FFFFFF` | tarjetas y franjas |
| `--border` | `#1E293B` | `#94A3B8` | borde 1 px, radio 4 px |
| `--accent` | `#06B6D4` | `#0B647D` | dato principal, foco, botones |
| `--coherence` | `#10B981` | `#047857` | calidad y ajuste estacional |
| `--alert` | `#F59E0B` | `#9A3412` | banda moderada |
| `--coral` | `#F43F5E` | `#BE123C` | banda crítica / interferograma inválido |
| `--text` / `--muted` | `#F8FAFC` / `#CBD5E1` | `#0B1220` / `#334155` | texto y secundario |
| Tipografía | Space Grotesk (títulos) · Inter (texto) · JetBrains Mono (cifras, coords, IDs) | — | — |

**Adición propuesta (Fase 2, ya validada con `dataviz/scripts/validate_palette.py`):**

| Token nuevo | Claro `#FFFFFF` | Oscuro `#0F172A` | Serie |
|---|---|---|---|
| `case_colors["mexico-city"]` | `#2a78d6` | `#3987e5` | 1 |
| `case_colors["jakarta"]` | `#eb6834` | `#d95926` | 2 |
| `case_colors["malaga"]` | `#1baf7a` | `#199e70` | 3 |

Resultado del validador: **ALL CHECKS PASS** en ambos modos; único `WARN` es el contraste 2.82:1 de
Málaga en claro, que obliga **etiqueta directa** al final de su línea (no solo leyenda). No se toca la
rampa del mapa ni el resto de tokens.

## 4. Wireframes de "1. Map" a 1440×900

### 4.1 Actual (medido con navegador, antes de esta fase)

```
+-------------------------------------------------------------------------------------------+
| Dancing with the SARs: NISAR Interferometry Cockpit          sensor · product · unit · …  |  ~56 px
+-------------------------------------------------------------------------------------------+
| DEMONSTRATION DATA                                                                        |  ~36 px
+-------------------------------------------------------------------------------------------+
| v Filters (abierto)  [Case][Land use][Coherence][Risk][3D][Theme]                         |  ~140 px
+-------------------------------------------------------------------------------------------+
| (o) Per case   ( ) Shared across cases                                                    |  ~48 px
+--------------------------------------------------+----------------------------------------+
|                                                  |  Inspector                              |
|              MAPA H3 (hasta ~430 px)             |  [chip Critical]                        |
|                                                  |  Deformation / Quality / Provenance     |
|                                                  |  (lista de ~20 líneas)                  |
|--------------------------------------------------|                                         |
| Leyenda · trazos de contexto · Procedencia       |  (sin serie mientras no hay celda)      |
+--------------------------------------------------+----------------------------------------+
| Relative magnitude across cases (3 filas)                                                 |  ~120 px
+-------------------------------------------------------------------------------------------+
| [KPI][KPI][KPI][KPI][KPI]   ·  "11694 cells shown of 11694"                               |  ~120 px
+-------------------------------------------------------------------------------------------+
        borde inferior del mapa ≈ 922 px con --map-height de 380–430 px  → fuera de pantalla
```

Problema: el mapa termina por debajo de 900 px (no cabe), la serie no existe en el estado vacío, y la
mitad derecha es una lista de texto sin jerarquía. El jurado ve una tabla, no una medición.

### 4.2 Propuesto

```
+-------------------------------------------------------------------------------------------+
| Dancing with the SARs: NISAR Interferometry Cockpit          sensor · product · unit · …  |  compacto
+-------------------------------------------------------------------------------------------+
| DEMONSTRATION DATA                                                                        |
+-------------------------------------------------------------------------------------------+
| v Filters                                                                                 |
+-------------------------------------------------------------------------------------------+
| (o) Per case  ( ) Shared across cases                                                     |
+---------------------------------------------+---------------------------------------------+
|                                             | [H3 cell ▾]                                 |
|                                             | Mexico City · cell 892830…  [ Critical ]     |
|              MAPA H3                        |                                             |
|      (alto ≥ 420 px, borde inferior         |   SERIE DE LA CELDA (hero, alto ≥ 280 px)   |
|       ≤ 880 px a 1440×900)                  |   Cumulative displacement (mm)               |
|                                             |   ── celda   ┈┈ Seasonal fit                |
|                                             |                                             |
|                                             |   -415.14 mm/year   ·   0.94   ·   276.8    |
|                                             |   Velocity          ·   Coherence · Ratio R |
|                                             |                                             |
|                                             |   Deformation / Quality / Provenance        |
|                                             |   (3 bloques, 3–4 líneas cada uno)          |
+---------------------------------------------+---------------------------------------------+
| Leyenda · saturados · Procedencia (una sola fila)                                         |  ~56 px
+-------------------------------------------------------------------------------------------+
| ▸ Case comparison and context   (plegable: comparación, trazos de contexto, KPIs de caso)  |  ~48 px
+-------------------------------------------------------------------------------------------+
```

Regla de lectura en columnas: **2/3 mapa, 1/3 panel** (`st.columns([2, 1], gap="medium")`). El panel
derecho, de arriba abajo, es siempre el mismo orden: selector → título de celda con chip de riesgo →
serie → tres cifras clave → inspector agrupado.

**Estado vacío (sin celda elegida):** el panel derecho no muestra una caja de "Pick a cell": muestra la
**serie agregada del AOI** con título *Whole area*, las tres cifras clave del caso y una línea que
invita a elegir una celda en el mapa o en la lista. Así la pantalla comunica desde el primer segundo.

**Móvil (≤ 700 px):** una sola columna: mapa → panel (selector, serie, cifras, inspector). Filtros en
expander cerrado cuando la URL traiga `?compact=1`.

## 5. Qué cambia / qué se mantiene

**Cambia (solo presentación y composición):**

1. `render_map_tab`: orden de bloques y proporción de columnas (`[2, 1]`), serie en el panel derecho
   como elemento grande, franja compacta bajo el mapa, y comparación/contexto/KPIs dentro de
   `st.expander("Case comparison and context", expanded=False)`.
2. Estado vacío con la serie AOI (*Whole area*) en vez de una caja de 400 px.
3. Inspector: tres bloques con 3–4 líneas (las definiciones cortas se conservan como segunda línea).
4. `--map-height` y el alto de la cabecera para cumplir el criterio de pliegue.
5. Fase 2: tokens `case_colors`, gráfica de tres ciudades, barras y línea de límite de detección.
6. Fase 3: botón *Clear selection*, resaltado de la celda, focus visible, `?compact=1`.

**Se mantiene intacto:**

- Claves de estado y query params (`case`, `land`, `risk`, `coh`, `extrude`, `theme`, `scale`, `cell`).
- Selector H3 (alternativa accesible al clic) y el clic de pydeck con el mismo estado.
- Rampa del mapa, dominio de la leyenda, `color_scale.py`.
- Todas las cifras y textos de backend; la UI no calcula ni reescribe ciencia.
- Las cuatro pestañas y el aviso de demostración.

## 6. Riesgos y criterios de aceptación

| Riesgo | Mitigación |
|---|---|
| **El criterio "alto ≥ 420 px y borde ≤ 880 px" choca con la cabecera actual**: el mapa empieza ≈ 476–492 px, así que el alto máximo disponible es ≈ 388–404 px. | Compactar la franja de misión y el banner (~32 px) para que el mapa empiece ≤ 460 px; si no se consigue con CSS seguro, aceptar 388 px y registrar la desviación con medida. La cifra final la da Playwright, no la estimación. |
| pydeck no repinta al cambiar de columna | `key=f"h3-map-{case_id}"` ya remonta el deck; verificar tras el cambio de layout. |
| Serie AOI y serie de celda con unidades o escalas distintas | Ambas salen de los loaders con su `unit`; el eje toma la unidad declarada y la leyenda lo dice. Nada se convierte. |
| Gráfica de tres ciudades con sensores distintos | El eje es `mm` acumulado y los tres artefactos son `mock`/`nisar`; si algún caso cambiara de sensor, se muestran solo los presentes y se anota la ausencia. |
| El panel derecho apilado en móvil alarga el scroll | Es aceptable: la pantalla 1 no exige pliegue en móvil, solo en 1440×900. |

**Aceptación de la Fase 1:** a 1440×900, borde inferior del mapa ≤ 880 px, alto ≥ 420 px (o desviación
medida y documentada), columnas ≈ 2:1, sin solapes, sin scroll horizontal, leyenda y
`provenance_id` visibles, y las cuatro pestañas siguen renderizando en los tres casos en claro y oscuro.
