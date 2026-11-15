# Ingest — `02_search_download.py`

P1 ingest answers two questions in that order: **what real SAR data exists for these AOIs**
(no credentials), and **what may I download** (fail-closed guards, then a resumable transfer).

```bash
./scripts/python.sh pipeline/ingest/02_search_download.py search      # probe ASF, derive stacks
./scripts/python.sh pipeline/ingest/02_search_download.py report      # markdown table of the reports
./scripts/python.sh pipeline/ingest/02_search_download.py download --city malaga \
    --source nisar_gunw --stack recommended --max-gb 6 --dry-run       # plan only: no creds, no writes
./scripts/python.sh pipeline/ingest/02_search_download.py download --city malaga \
    --source nisar_gunw --stack recommended --max-gb 6 --probe-sizes --dry-run  # real sizes
```

Exit codes are a contract (see `docs/first-granule-runbook.md`): **2** usage, **3** credentials,
**4** product/geometry guard, **5** budget guard, **6** foreign partial, **7** HTTP 403.

## Stacks: one geometry per fetchable unit

A displacement series may only combine acquisitions from the same orbit **track** and **flight
direction**; the science layer refuses anything else. `search` therefore groups every result by
`(track, direction)` parsed from the granule name, using the module's single grammar
(`parse_granule`) — the same parser the download guard uses, so a stack can never disagree with
the identity check.

Per stack the report persists:

| field | meaning |
|---|---|
| `track`, `direction` | `"020"` / `"ASCENDING"`; `null` when the name carried no geometry |
| `n_granules` | size of the full group (not just the ids kept) |
| `date_range` | `[min, max]` reference (first acquisition) timestamps |
| `mean_interval_days` | **median** of consecutive reference-date gaps; `null` for one date. Repeated dates are de-duplicated so they cannot inject a 0-day gap |
| `granule_ids` | up to `STACK_GRANULE_LIMIT = 12` ids, **evenly sampled** across the stack (`round(i·(n−1)/11)`), so the first and last acquisitions are always included and the series spans the window |
| `granule_ids_truncated` | `true` when ids were dropped |

Stacks are sorted by `n_granules` descending, then most recent first. `recommended_stack` is the
first **identified** stack (a group of unparseable names never wins) plus a one-line `why`:

```json
{
  "track": "020",
  "direction": "ASCENDING",
  "n_granules": 17,
  "date_range": ["2026-06-20T05:22:52Z", "2026-10-06T05:22:49Z"],
  "mean_interval_days": 6.0,
  "granule_ids": ["NISAR_L2_PR_GUNW_023_087_A_020_...", "… 12 ids, first+last kept …"],
  "granule_ids_truncated": true,
  "why": "largest single-geometry stack (track 020/ASCENDING); 12 of 17 granules spread across 2026-06 .. 2026-10"
}
```

Each source entry also gains `granule_ids` (all ids in acquisition-date order, capped at
`GRANULE_ID_CAP = 120` with `granule_ids_truncated`), the matching `granules` records
(`id`/`url`/`size_mb`, the same slice as `granule_ids`) and at most `MAX_STACKS = 10` stacks.
`sample_granules` (the old first-three preview) is kept unchanged for backward compatibility.

### JSON growth

The id lists are the only unbounded part of a report, so they are capped: `granule_ids` ≤ 120
ids, `stacks` ≤ 10 × 12 ids, and `granules` mirrors `granule_ids`. `search` prints the size
delta per case (`json X KB (+Y B vs previous)`) so the growth stays visible.

All three committed reports now carry `stacks` (for `nisar_gunw` and `sentinel1_slc` alike), the
per-source `granule_ids` list and the mirrored `granules` records (`url`/`size_mb`), so each file
is roughly an order of magnitude larger than the first probe output that held only
`sample_granules`. Measured from the committed artefacts: `malaga.json` 79,668 B (7,683 B before
stack derivation), `mexico-city.json` 112,285 B and `jakarta.json` 82,220 B (both ~7.4 KB before).
The size tracks how many granules a probe returned, so read those byte counts as a snapshot of
these artefacts; `search` reprints the per-case delta every time it rewrites a file.

## `download --stack`

| value | plan |
|---|---|
| `recommended` (default) | exactly `recommended_stack.granule_ids` |
| `<track>` (e.g. `020`, `20`) | exactly that stack's `granule_ids`; zero-padded comparison |
| `all` | every persisted granule — mixes tracks, so the geometry guard refuses it (exit 4) and points at `--stack recommended` |

`--dry-run` prints the chosen stack in the header (`Stack selection: recommended: track 020
ASCENDING (12 of 17 granule(s))`), then one block per granule (`level/product/track/direction`,
`reference`/`secondary`, size, `status: ACCEPT|REJECT`), and exits 0 **before** credentials are
read and before any byte is written. A granule the report has no size for prints
`size=UNKNOWN (no size for this granule in the availability report)` — never a `0.00 GB` that
looks like a passed check.

A report without `stacks` (legacy schema) falls back to `sample_granules`, prints
`Stack selection: legacy preview (N granule(s))`, derives a stack from the ids it has, and tells
the operator to re-run `search`. It never silently plans a mixed preview: that would just hit
the geometry guard.

## Sizes: what the report knows, and `--probe-sizes`

ASF's SearchAPI carries `sizeMB` for the archive missions — every Sentinel-1 record in
`data/processed/_availability/*.json` has a size and `total_gb` is populated — but **not for
NISAR GUNW**: all 69 `mexico-city` NISAR records are `size_mb: null`, so `total_gb`/`total_bytes`
are `null` too. This is a payload gap, not a parser bug: both sources go through the same
`g.get("sizeMB")` read in `summarise`.

The size is still obtainable without credentials. One unauthenticated `HEAD` on a planned
granule URL returns `Content-Length` — verified on one `mexico-city` granule:
`HTTP 200`, `Content-Length: 2344615936` (≈ 2.34 GB). `--probe-sizes` therefore spends one HEAD
per planned granule (`probe_plan_sizes`, accepted items only, ≤ `PROBE_LIMIT = 12`, 0.2 s apart,
no `Range`, no credentials) and fills `size_mb` before the budget verdict. A 401/403 stops the
probe instead of retrying: the plan then stays honest about the sizes it could not learn.

Consequences, in the order they matter:

- An **unknown** size is never a refusal. NISAR GUNW always starts sizeless, so refusing on
  "unknown" would refuse every first run. Only a **known** size over `--max-gb` is refused.
- Without `--probe-sizes`, `--dry-run` prints `BUDGET CANNOT BE PREFLIGHTED: … --max-gb N will
  NOT refuse before transfer` and says that enforcement is mid-stream only (after bytes have
  flowed, leaving a `.part`).
- With `--probe-sizes`, the plan prints `known size X GB` and the guard refuses (exit 5, zero
  bytes) when X exceeds the budget. A 12-id stack at ~2.34 GB/granule is ≈ 28 GB, so the
  `--max-gb` default (2 GB) cannot cover a full NISAR stack: measure first, then raise `--max-gb`
  deliberately.

### Guards, in the order they fire

1. **Product identity** (`evaluate_granule`): only `L2/GUNW` is accepted by default. `L0/RRSD`
   (raw, ~100+ GB to focus) and `L1/RSLC|RIFG|RUNW` (radar coordinates) are refused; `GSLC`,
   `GCOV`, `GOFF` only with an explicit `--allow-product <PRODUCT>` and a warning. Unparseable
   names fail closed with what was parsed. → exit 4.
2. **Mixed geometry** (`plan_geometries`): more than one `(track, direction)` in the accepted
   plan → `REFUSED: this plan mixes acquisition geometries`, exit 4. `--allow-mixed-geometry` is
   deliberately not a fix (the science layer refuses the mix too).
3. **Budget** (cumulative over **known** sizes, `--probe-sizes` to make them known): a known
   total over `--max-gb` → exit 5, zero bytes. A plan with no known size cannot fire it (see
   above) — that is stated in the plan output rather than hidden behind `0.00 GB`.
4. **Foreign partials**: a `.crdownload`/foreign `.part` beside the destination → exit 6 (our own
   `<file>.h5.part` is the resume mechanism and is allowed).
5. **Credentials**: no `EARTHDATA_USER`/`EARTHDATA_PASS` or `~/.netrc` entry → exit 3.
6. **Transfer**: streamed in 1 MiB chunks, `Range` resume, credentials sent to
   `urs.earthdata.nasa.gov` only (never to the presigned S3 redirect); a mid-stream budget
   breach keeps the `.part`. 401 / EDL login form → exit 3, 403 → exit 7 with the URL redacted.
   Note the mid-stream limit is **per granule** (`download_one`), not cumulative across the plan —
   that is why the preflight (guard 3) is the one that protects the whole job.

## Tests

`tests/test_download_stack.py` is hermetic: canned ASF payloads (`granuleName`, `startTime`,
`sizeMB`, `downloadUrl`), synthetic reports under `tmp_path`, and a read-only replay of the
committed `data/processed/_availability/*.json`. Cases whose committed report predates stack
derivation are skipped with a note (refreshing them needs the network).

## Contexto OSM (offline)

Las capas `land_use`, `water` e `infrastructure` de `context_layers.geojson` salen de un extracto de
OpenStreetMap cacheado en disco. Lector y productor están separados y commiteados: el lector
`pipeline/ingest/osm_context.py` sólo lee (`load_extract` levanta `FileNotFoundError` si no hay
`manifest.json`) y no importa nada de red, y el productor del extracto es
`pipeline/ingest/fetch_context.py` (`./scripts/python.sh pipeline/ingest/fetch_context.py --dry-run`;
`--case` repetible, `--force`, `--include-buildings` opt-in, y `--pause`/`--attempts`/`--backoff`/
`--timeout`/`--endpoint` para la política de red). La extracción se ejecuta una vez, a mano; a partir
de ahí el pipeline es offline y determinista. Qué fuente es, qué cobertura tiene y con qué licencia, en
[`docs/context-sources.md`](../../docs/context-sources.md).

### Disposición en disco

```
data/raw/<case_id>/context/manifest.json                  registro de la recuperación
data/raw/<case_id>/context/tiles/<case>_r<r>_c<c>.json   respuestas de Overpass, literales, una por tile
```

`data/raw/` está en `.gitignore`: un dump de Overpass son decenas de MB. Lo que se commitea es el
artefacto, y el registro de la recuperación viaja en `manifest.json` y en el `source` de cada
feature.

### Cómo se recupera (una vez)

| Regla | Valor |
|---|---|
| Rejilla de tiles | como máximo 0.15° por lado, por AOI |
| Peticiones | una cada vez, con `User-Agent` propio |
| Espera entre tiles | 8 s |
| Backoff | 30 s ante 429 / 502 / 504 |
| Escalera de reintentos | `overpass-api.de` → `overpass.private.coffee` → `ovp.dresel.systems` |
| Respuesta | geometría incluida: `_relation_geometry` ensambla los multipolígonos desde la geometría de cada miembro, sin node store |

Los tiles se piden por separado y un way que toca dos tiles vuelve en los dos: `load_extract`
deduplica por `(type, id)` y ordena, así que el frame no depende del orden de los tiles ni del orden
en que Overpass devolvió los elementos.

### Cómo comprobar qué está cacheado

```bash
./scripts/python.sh -c "from pipeline.ingest import osm_context as O; import json; print(json.dumps(O.summarise('malaga'), indent=2, ensure_ascii=False))"
```

`osm_context.summarise(case_id)` no construye geometría: cuenta elementos, devuelve el `record`
(`retrieved_utc`, `timestamp_osm_base`, `tiles_ok`/`tiles_failed`, `elements`, `bbox`) y un censo de
tags `landuse`/`natural`/`waterway`/`leisure`. Sin manifest devuelve `{"cached": false}`.

### Contexto fijado: `pin_context`

El extracto está gitignored y el artefacto tiene que regenerarse idéntico en una máquina que nunca
lo descargó (CI incluida). La pieza que se commitea para eso es el **pin** del caso: las decisiones
que el extracto implica (una clase por celda H3 r9 más las features simplificadas de agua,
infraestructura y límite administrativo), no los tiles.

```bash
./scripts/python.sh -m pipeline.ingest.pin_context --case malaga   # reconstruye un caso
./scripts/python.sh -m pipeline.ingest.pin_context                 # por defecto: los casos declarados
```

| | |
|---|---|
| Entrada | el extracto cacheado del caso, `data/raw/<case_id>/context/` (gitignored) |
| Salida | `config/context/<case_id>.json`, commiteado |
| Escritor | `pipeline/ingest/pin_context.py` (offline; el generador no lo importa) |
| Lector | `pipeline/context.py`: `read_pin` → `pinned_context` → `label_cells` |

Sin extracto el comando falla nombrando el `manifest.json` que falta en vez de escribir un pin
vacío. El pin es dato derivado y no se edita a mano: se reconstruye, y su `pin_sha256` permite
comparar la reconstrucción con el fichero commiteado. Qué contiene cada clave, por qué el tope de
features es por tipo de geometría y qué pasa con una celda que el pin no nombra están en
[`docs/context-sources.md`](../../docs/context-sources.md) §7.

El pin es lo que hace reproducible `pipeline/generate_mock.py` sin el extracto: con él, un clon sin
`data/raw/` produce el mismo `context_layers.geojson` y la misma columna `land_use`.

### Licencia

Los polígonos publicados van bajo **ODbL 1.0** y cada feature lleva el proveedor y la licencia en su
`source` (`osm_context.SOURCE`). El texto de atribución, la distinción entre base de datos derivada
y produced work, y la cuestión de licencia mixta que sigue abierta están en
[`docs/context-sources.md`](../../docs/context-sources.md) §5; la atribución visible del repositorio
está en `README.md` §17.
