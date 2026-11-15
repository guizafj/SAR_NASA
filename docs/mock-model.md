# Mock model — what the demo numbers actually are

This guide documents the synthetic generator behind `data/processed/`. It is not a scientific
measurement record. Case parameters live in `config/study_cases.yaml`; generated values and headline
statistics belong to the artefacts and `docs/handoff-mock.md`; consumer bindings stay with the
loader. Shared processing and parity details are in [`pipeline-internals.md`](pipeline-internals.md).

## Forward model


```text
# pipeline/mock/model.py, docstring (equations verbatim; velocity line reworded; reflowed)
v(x)   = peak_velocity_mm_yr * exp(-d(peak_lonlat)/falloff_km) + N(0, noise_mm_yr)
d(t)   = v * (t - t_first)/yr + A_seasonal * (cos(2*pi*(t-c)/yr) - cos(2*pi*c/yr))
val_t1 = d(first date);  val_t2 = d(last date);  delta_change = val_t2 - val_t1
velocity = trend coefficient of the joint [1, t, cos, sin] fit
```

The seasonal columns the mock publishes are the **output of `harmonic_fit`**, run per cell on the
synthetic `cumulative_series` with the order `harmonic_order` derives from the window — the same
function the real chain calls, not the injected `seasonal_amplitude_mm` copied into the artefacts.
Because the mock window is two years (2.004 annual cycles), the fit is identifiable at order 2 and
recovers the injected amplitude (1.5 / 12.0 / 3.5 mm for Mexico City, Jakarta and Málaga); that
agreement is a check on the generator and the fit, not a shortcut around either. The not-fitted path
is only the short-stack contrast here: on a stack too short to carry an annual harmonic the fit has
no seasonal column, so `seasonal_amp_mm = 0` means "not fitted", `ratio_R` is `null`, and `regime`
is `"transitional"`. The 2-year mock never reaches that corner.

## Parameters and interpretation

All case parameters come from `config/study_cases.yaml`, never from constants hidden in the model.
Read that file for current peaks, falloffs, noise, ellipses, seasonal amplitudes, and literature
references.

A configured peak is a **chosen model parameter**, not a fitted literature rate or a case median;
most cells sit away from the peak. Literature values are context, and order-of-magnitude targets
must not be promoted into citations.

## Determinism

The generator uses `seed = 7`, and each case draws from
`np.random.default_rng(SeedSequence([seed, sha256(case_id)[:4]]))`, so one case's cell set can never
perturb another's numbers. Draw order is fixed (noise → the bare patches of the synthetic
fallback, when that is the active branch → coherence jitter → water coherence). Regenerating into an empty directory reproduces all twelve files
**byte-identically** (verified with `cmp`), because no wall clock enters the output
(`PROCESSING_DATE` and `RETRIEVED_UTC` are constants), JSON is written with `sort_keys=True`, rows
are sorted, floats are formatted `%.9f`, and every write is a temp file plus `os.replace`.

## Spatial layers and coherence

`rho` sigue siendo el **radio elíptico normalizado desde el centro de la elipse de cobertura** (0 en
el centro, 1 en el borde) y *no* se mide desde `peak_lonlat` (el pico de Yakarta cae justo fuera del
dominio nodata). Lo que cambió es qué decide: la elipse decide **qué celdas existen**, nunca qué son.
La antigua cascada de anillos concéntricos sobrevive sólo dentro del fallback sintético declarado.

- **land use**: el centroide de cada celda se une a los polígonos reales del extracto de
  OpenStreetMap con `pipeline/enrich_context.py::join_land_use` — la misma función que usa la CLI —
  y la precedencia `water > infrastructure > industrial > urban-dense > urban-residential >
  vegetation > bare > unknown`, desempatando por área menor y después por digest de geometría, nunca
  por orden de filas. Las etiquetas por celda se leen del pin commiteado
  `config/context/<case>.json`, así que regenerar no necesita `data/raw/`. `water` es la única clase
  que se *deriva*: una costa es una línea y una línea no contiene centroides, de modo que las celdas
  del lado del mar de la costa real se etiquetan `water` a partir de esa geometría. Nada más se
  extrapola: una celda cuyo centroide no cae en ningún polígono mapeado se publica `unknown`, y ese
  recuento y su proporción viajan en `meta.limitations`. `urban-dense` no es un umbral de densidad
  sino el tejido de servicio, comercial e institucional según la semántica del tag de OSM: se midió
  un corte por fracción construida y se descartó porque la distribución es un continuo. Los
  polígonos `land_use` del artefacto son la unión de las mismas
  celdas que publica el grid (`pipeline/mock/artifacts.py::build_context`), así que la capa y la
  columna no pueden discrepar; los polígonos crudos de OSM no se publican porque no caben en el
  presupuesto de tamaño del artefacto (los tamaños medidos están en el docstring de esa función).
- **water e infrastructure**: se publican desde las features reales del extracto, mapeadas a la
  taxonomía congelada por `pipeline/enrich_context.py` y simplificadas antes de escribir
  (`pipeline/mock/artifacts.py::_published_context_features`). La costa real es una de esas features,
  y el agua sigue siendo contexto aunque nunca sobreviva como celda de desplazamiento.
- **admin_boundary**: la feature `admin_boundary/aoi` es el anillo real de
  `config/aoi/<case>.geojson`, no la elipse de cobertura. La elipse sigue decidiendo qué celdas
  existen; ya no se publica como extensión del análisis.
- **geografía sintética (fallback)**: sin extracto, la geografía vuelve a ser la inventada (lago
  elíptico, costa en zigzag, río y corredor industrial, definidos por la capa de contexto;
  `pipeline/mock/geography.py` sólo reexporta esa API). Mientras esta rama está activa, *todas* las
  features de contexto dicen `mock (synthetic …)` en su `source` y `meta.limitations` declara que la
  geografía es inventada. Las dos ramas son mutuamente excluyentes — un único booleano en la capa de
  contexto elige una, y `meta.limitations` publica una sola de las dos frases — y quien reconstruye
  el caso para comprobarlo es `pipeline/generate_mock.py --check`, que llama a
  `pipeline/mock/checks.py::assert_case`: esa rutina relee `meta.provenance.mock_seed`, vuelve a
  construir el modelo con el contexto que ve en disco y exige que el conjunto de celdas publicado
  coincida celda a celda con el reconstruido (la elipse de cobertura menos las celdas por debajo de
  `coherence_min`, y la coherencia depende del `land_use`), así que una geografía y un grid de ramas
  distintas no pasan juntos. La **compuerta de artefactos no hace eso**:
  `pipeline/validate_artifacts.py` y `src/data/validation.py` comprueban forma de contrato,
  vocabulario y unidades, presupuesto de tamaño y concordancia entre los cuatro ficheros, y nunca
  leen `mock_seed`; por sí solas no reconstruyen la geografía ni distinguen una rama de la otra.
  Su único motivo de existir es que un clon sin `data/raw/` pueda regenerar.
- **coherence**: `0.80` base ± a land-use bonus (urban-dense +0.10, water −0.30, …), a −0.20 edge
  penalty for `rho → 1`, a small coastal penalty, and `N(0, 0.025)` jitter, clipped to [0.05, 0.95].
  Water is then forced to `0.35·(0.35 + 0.55·rand)`, i.e. **strictly below** `coherence_min`, so it
  is dropped downstream rather than published as a water cell with subsidence.
- **mid-window bulge**: the centred harmonic returns to the same endpoint offset while producing
  a visible mid-window excursion. Its amplitude follows the configured seasonal term; use the
  regenerated series for current values.

## Output checks

`pipeline/mock/checks.py` validates the generated set on the way out. `meta.json` declares
`product: "mock"`, its method names the synthetic model, and its limitations explicitly warn that
every number is synthesised: the artefacts validate the map and schema, never the science.
