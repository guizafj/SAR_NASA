# Pipeline internals — acquisition, processing, and parity

This is the detailed engineering companion to [`CONTEXT.md`](../CONTEXT.md). It preserves the
granule, stack, processing-stage, refusal, and mock-parity details that are too specific for the
project overview. Use [`../pipeline/README.md`](../pipeline/README.md) for commands and
[`../pipeline/ingest/README.md`](../pipeline/ingest/README.md) for the download CLI.

> **Current evidence.** The real path is implemented and tested with synthetic HDF5 fixtures, but no
> downloaded granule has produced a published artefact. All committed case artefacts are mock data.

---

## 1. Granule identity and reader behaviour

*The [science guide](science-guide.md) explains how a number is produced. For the product layout,
availability evidence, and download commands, use the canonical documents linked below rather than
another set of copied tables.*

### 1.1 What a granule is

A **granule** is one product file covering one footprint at one time — the smallest thing a data
archive (a DAAC, such as ASF) will hand you. Naming is a grammar, and the grammar is load-bearing: it
carries the level, the product type, the repeat-cycle bin, the track, the flight direction and the
frame. **Both parsers in the tree implement that order and agree on it** (O-06, resolved
2026-11-14 — the census table below is what they were reconciled against). The science layer's parse
is `parse_granule_identity` / `_check_stack_identity`; the ingest layer's is `parse_granule`, which
`derive_stacks` groups with.

Token indices are 0-based after splitting an ASF name on `_` (20 tokens), e.g.
`NISAR_L2_PR_GUNW_025_010_A_174_026_4000_SH_20260720T223920_…`:

| Field | Token | Evidence (census of the 120 committed ASF names) |
|---|---|---|
| level | 1 | `L2` in every name |
| product | 3 | `GUNW` in every name (token 2 is the class, `PR`) |
| repeat-cycle bin of the reference acquisition | 4 | 29 values, `003`–`032`; a bin, not a date |
| track | 5 | varies where token 7 does not: Málaga ascending carries `015` and `087` on frame `020` |
| direction | 6 | `A` ascending, `D` descending |
| frame | 7 | 9 values, constant per (case, direction) except three adjacent-frame pairs |
| secondary cycle bin | 8 | `token[8] − token[4] = ⌈Δdays/12⌉` in 120/120 names |
| reference start timestamp | 11 | `YYYYMMDDThhmmss`; the 72 reference dates are read from it |

Both parsers implement that table and agree on it. `pipeline/process_case.py:_GRANULE_RE` captures
`repeat_cycle`/`track`/`direction`/`frame` in exactly that order, so `parse_granule_identity` returns
the real track (token 5) and frame (token 7); `pipeline/ingest/02_search_download.py:parse_granule`
reads `track = tokens[5]` and `frame = tokens[7]`, and `derive_stacks` groups by
`(track, frame, direction)`. Until 2026-11-14 they did not: the science parser read `track = tokens[4]`
and `frame = tokens[5]`, one token left, so its "track" was the cycle bin, and the ingest parser read
`track = tokens[7]` — the frame — with no frame field at all. O-06 in `docs/decisions.md` records the
defect and its fix. The history is kept because it explains the evidence: stacks were once labelled by
frame, so two orbits could be merged under one label. Nothing published ever depended on the parse —
the mock path never parses a granule name, and its provenance digest hashes `case|product|seed`.

Anything unparseable is reported with what was parsed, never guessed. A name that parses but is
**not** `L2/GUNW` is refused by the download guard, not silently reinterpreted.

> **Why token 4 is a cycle bin and not a track (census, 2026-11-14).** This is the evidence the fix
> rests on, kept because it is the reference, not because anything is still in dispute. Token 4 is
> the 12-day repeat-cycle bin of the reference acquisition, not a date: 72 distinct reference dates
> map to exactly one token-4 value each, with zero conflicts across three cities and both directions
> — yet `025` alone covers `20260708`, `20260709`, `20260714` and `20260716` (Málaga, Jakarta,
> Mexico City; ascending and descending). 120 granules collapse to 29 distinct bins (`003`–`032`,
> `027` absent), up to 9 sharing one (`024`, `025`). Token 8 is its companion:
> `token[8] − token[4] = ⌈Δdays/12⌉` in 120/120 ids, so the pair reads "reference cycle, secondary
> cycle". Token 7 has 9 values and is constant per *(case, direction)* except three pairs 32–34 s
> apart on the same pass — **adjacent frames, not parallel orbits**: Jakarta ascending `174`/`175` on
> `20260720` (22:39:20 → 22:39:53), Jakarta descending `091`/`092` on every dated pass (+33 s),
> Mexico City descending `078`/`079` on `20260411`, `20260704` and `20260716` (+34 s).
> **Consequence while the defect was live:** any multi-date real stack spanning more than one cycle
> bin would have been refused by `_check_stack_identity` as "stack mixes relative orbits" — including
> the runbook's Step 2→Step 5 path — because the parser counted each token-4 bin as a separate track,
> and the derived stacks were labelled by frame, which merged orbits. Both tables were fixed together;
> the census stays here as the reference they are now checked against.

Two concepts that are constantly confused:

| Term | Meaning | Consequence |
|---|---|---|
| **granule** | one file: one footprint, one pair, one geometry | the unit you download |
| **stack** | granules with the same `(track, frame, flight direction)` | the only legal unit you may *combine* into a series |

### 1.2 Reader ownership and fallbacks

`docs/nisar-gunw-layout.md` owns the HDF5 paths, dtypes, geolocation questions, and correction
protocol. The implementation-specific fallback behaviour is:

Ownership of the pieces is worth knowing before editing the reader:

| Piece | Owner | Fallback behaviour |
|---|---|---|
| phase, coherence, components, mask | `nisar_gunw.py` (`DATASETS`) | fail closed on missing/wrong dtype/shape |
| polarisation | `nisar_gunw._select_polarisation` | both `HH` and `VV` present ⇒ `GranuleError` unless `--polarisation` given |
| x/y coordinates, EPSG, times | `nisar_gunw.read_granule` | `None` + warning, never synthesised |
| wavelength | `nisar_gunw` | metadata → `c/centerFrequency` → `None`; `process_case` then uses 0.2384 m **with a warning + limitation** |
| incidence, heading | `process_case._probe_granule_metadata` | `--incidence` / `--heading` fills an absence; a per-pixel grid is a `GeometryRequiredError` |
| looks | same probe | **no** CLI flag; a per-pixel looks grid is dropped to `None` with a warning, and coherence is then published without the finite-look debias |

### 1.3 Mask semantics beyond the basics

The [GUNW layout](nisar-gunw-layout.md) owns the observed `0`/`2`/`127` mask values and the rule
that padding is absent, never zero. Two reader behaviours are easy to miss:

- unknown flag values are carried unchanged, counted in `warnings`, and listed distinctly in
  `summary()["mask_unknown_values"]`; they are not silently dropped;
- declared `_FillValue`/`missing_value` attributes are recorded but not interpreted, because the
  GUNW fill convention is not yet confirmed from a real granule.

### 1.4 Acquisition evidence and guards

Availability counts and source selection belong to `docs/data-source-decision.md`; the guarded CLI,
exit codes, unknown-size behaviour, and stack-selection rules belong to
`pipeline/ingest/README.md`. The operator sequence is `docs/first-granule-runbook.md`. This guide
keeps only the processing consequences of those contracts.

## 2. Actual processing order

`pipeline/README.md` owns the stage inventory and commands. The important detail here is that the
logical P0–P10 names are **not** the call order:

1. Read each granule and apply its mask and coherence filter.
2. Convert phase to LOS displacement and then to vertical **per pair** (logical P5).
3. Chain the pairs into a cumulative cube (P4), subtract the configured reference pixel, and assign
   pixels to H3 cells.
4. Aggregate to one H3 series per cell (P8), then fit trend and any permitted harmonic (P6).
5. Derive cumulative change, rate, risk, metrics, context, and the four artefacts (P7/P9/P10).

Thus P5 precedes P4, and aggregation precedes fitting. The path refuses mixed track/direction or
polarisation, missing required layers or scalar geometry, disconnected/short stacks, invalid land
use, and unsafe output overwrites. Atmospheric/orbit/DEM corrections and richer context joins remain
partial or unimplemented; a logical stage name is not evidence that a correction ran.

End-to-end evidence currently comes only from in-repo synthetic HDF5 fixtures. No real granule has
produced a published artefact.

## 3. Real-path refusals and writer parity

The deterministic synthetic model is documented separately in [`mock-model.md`](mock-model.md).
Both paths call the same writers and validators, so a valid real tree replaces a mock tree without
an application rewrite.

### 3.1 Real-path refusal taxonomy

`process_case.py` fails closed. Every one of these is an actionable message, not a default:

| Error | Trigger | Why refusing is right |
|---|---|---|
| `MockOverwriteError` | `--out` holds `meta.json` with `product == "mock"` and no `--force` | the committed demo is never silently replaced |
| `TrackMismatchError` | stack mixes flight direction / track | one geometry cannot be inverted jointly |
| `PolarisationMismatchError` | `HH` in one granule, `VV` in another | different scattering, not a series |
| `GeometryRequiredError` | incidence/heading missing, or a **per-pixel grid** where a scalar is needed | averaging θ silently redefines `cos θ`, which scales every millimetre |
| `MissingLayerError` | required HDF5 dataset absent | fail before producing a plausible-looking map |
| `LandUseError` | bad `--land-use-csv` | never guess a land-use class |

Exit statuses (these are `process_case.py`'s codes — the download CLI in `pipeline/ingest/README.md` uses a different
set): `0` only when the written tree passes both the in-code contract and the file gate
(`0` also if the gate could not be run at all); `2` on a refusal; `3` on a failed contract check
**or** a failed file gate.

Other refusals worth knowing before running it: a footprint sharing **zero** H3 cells with the AOI;
a granule whose reference/secondary times are incomplete; a chained stack with fewer than 4 epochs; a
cell without finite first *and* last epoch (it is dropped, not filled); and any output directory whose
existing `meta.json` says a product other than `nisar-gunw` (`write_artefacts` refuses). Two warnings
that are not refusals: coverage below `COVERAGE_WARNING_FRACTION = 0.5`, and context geometry above
`CONTEXT_LAYOUT_BUDGET_BYTES = 4_000_000` (the **land_use polygon features** are omitted; the single
`admin_boundary`/`aoi` feature always ships).

**No parallelism.** There is no `multiprocessing`, `concurrent.futures` or `joblib` anywhere in
`pipeline/` or `src/`. It is a serial loop that opens each granule twice (data + metadata), maps
every pixel to an H3 cell in Python, and runs one pandas `groupby` per epoch; the only caching is
`@lru_cache` (a memoisation decorator, so the YAML and the AOI are parsed once per process) on the raw
config and on the AOI cell set. For three cities × ~12 granules that is
adequate — and it means a failure is reproducible, not scheduling-dependent.

### 3.2 Parity by construction

Both paths call the same writers in `pipeline/mock/artifacts.py` (`write_parquet`, `write_csv`,
`write_geojson`, `write_json`), so the real and mock trees are structured identically. Only the
frames differ. That is what makes the UI swap a file replacement.

The mock adds a second conformance layer (`pipeline/mock/checks.py`) that re-reads the files **from
disk** rather than trusting the in-memory frames. It checks delta arithmetic, the predicted nodata
set, disguised zero sentinels, frozen risk bands, absence of water grid cells while water remains in
context, and agreement between AOI-series endpoints and grid means.

---

## 4. Contract implementation notes

The normative schema is [`data-contract-v1.md`](data-contract-v1.md) and
`src/domain/contracts.py`; this section records implementation details that are easy to lose behind
the field list. It deliberately does not repeat the schema or profile tables.

### 4.1 Artefact semantics beyond the field list

- On the real path, grid `coherence` is the temporal mean of per-pair, bias-corrected coherence,
  corrected only when a granule declares effective looks. The per-pair `coherence_min` filter runs
  **before** that mean. In the mock, coherence is a modelled spatial field, not a pair average.
- `timeseries.csv` always carries the case aggregate as `cell_id == "aoi"`. Per-cell rows are opt-in
  through `--per-cell-series`, capped at 3 MiB per case and ranked by `|velocity|` then `cell_id`.
- Every context feature carries its own `crs`, `source` and `provenance_id`. The mock simplifies
  geometry at `0.0008°`; the real path uses `0.0005°`. The mock's single `admin_boundary` is its
  synthetic coverage ellipse, whereas the real path publishes the AOI ring as an analysis extent,
  not a legal boundary.
- Parquet uses zstd compression. The size budget is a product constraint: small, local artefacts
  are what let the renderer avoid a geospatial service.

### 4.2 Known prose-versus-code deltas

These are recorded so a maintainer does not "fix" the wrong side blindly. Re-check the code before
acting on any row.

| Prose claim | Current enforcement |
|---|---|
| all `meta.thresholds` values come from the product profile | only `coherence_min` and top-level `revisit_days` are compared; `k_sigma`, `min_ddB`, `persistence`, and `min_area_m2` are presence-checked |
| `meta.provenance` must carry `provenance_id` | `PROVENANCE_REQUIRED_KEYS` omits it; the cross-artefact orphan check lives in `pipeline/validate_artifacts.py` |
| `timeseries.csv` must carry `cell_id == "aoi"` rows | documented, but not checked by `validate_timeseries` |
| `REGIMES` is part of the published schema | the tuple exists, but no field or validator publishes it in contract v1.0 |
| the provenance digest hashes input artefact bytes | the mock hashes `case\|product\|seed`; the real path hashes sorted granule stems and the convention token |

### 4.3 Validation caveats

Validators return problem lists rather than raising, and the file gate fails closed on an empty case
set or a `--case` filter that matches nothing. `validate_grid` rejects undeclared columns as well as
missing ones, so adding a field is a schema-version change.

Several enum, arithmetic, and duplicate-ID checks catch broad exceptions and skip that particular
check for frame-like objects that are not quite DataFrames. This does not make an empty artefact set
valid, but it is a reason to keep testing through the real file gate rather than treating the
in-memory validators as sufficient by themselves.
