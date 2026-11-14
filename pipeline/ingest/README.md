# Ingest — `02_search_download.py`

P1 ingest answers two questions in that order: **what real SAR data exists for these AOIs**
(no credentials), and **what may I download** (fail-closed guards, then a resumable transfer).

```bash
./scripts/python.sh pipeline/ingest/02_search_download.py search      # probe ASF, derive stacks
./scripts/python.sh pipeline/ingest/02_search_download.py report      # markdown table of the reports
./scripts/python.sh pipeline/ingest/02_search_download.py download --city malaga \
    --source nisar_gunw --stack recommended --max-gb 6 --dry-run       # plan only: no creds, no writes
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
delta per case (`json X KB (+Y B vs previous)`) so the growth stays visible. On the current
evidence: `malaga.json` 7,683 B → 76,953 B (+69,270 B, real 26-granule NISAR payload);
`mexico-city.json` and `jakarta.json` are unchanged until `search` is re-run (their committed
copies predate stack derivation).

## `download --stack`

| value | plan |
|---|---|
| `recommended` (default) | exactly `recommended_stack.granule_ids` |
| `<track>` (e.g. `020`, `20`) | exactly that stack's `granule_ids`; zero-padded comparison |
| `all` | every persisted granule — mixes tracks, so the geometry guard refuses it (exit 4) and points at `--stack recommended` |

`--dry-run` prints the chosen stack in the header (`Stack selection: recommended: track 020
ASCENDING (12 of 17 granule(s))`), then one block per granule (`level/product/track/direction`,
`reference`/`secondary`, size + cumulative GB, `status: ACCEPT|REJECT`), and exits 0 **before**
credentials are read and before any byte is written.

A report without `stacks` (legacy schema) falls back to `sample_granules`, prints
`Stack selection: legacy preview (N granule(s))`, derives a stack from the ids it has, and tells
the operator to re-run `search`. It never silently plans a mixed preview: that would just hit
the geometry guard.

### Guards, in the order they fire

1. **Product identity** (`evaluate_granule`): only `L2/GUNW` is accepted by default. `L0/RRSD`
   (raw, ~100+ GB to focus) and `L1/RSLC|RIFG|RUNW` (radar coordinates) are refused; `GSLC`,
   `GCOV`, `GOFF` only with an explicit `--allow-product <PRODUCT>` and a warning. Unparseable
   names fail closed with what was parsed. → exit 4.
2. **Mixed geometry** (`plan_geometries`): more than one `(track, direction)` in the accepted
   plan → `REFUSED: this plan mixes acquisition geometries`, exit 4. `--allow-mixed-geometry` is
   deliberately not a fix (the science layer refuses the mix too).
3. **Budget** (cumulative, `sizeMB` known sizes only): over `--max-gb` → exit 5, zero bytes.
4. **Foreign partials**: a `.crdownload`/foreign `.part` beside the destination → exit 6 (our own
   `<file>.h5.part` is the resume mechanism and is allowed).
5. **Credentials**: no `EARTHDATA_USER`/`EARTHDATA_PASS` or `~/.netrc` entry → exit 3.
6. **Transfer**: streamed in 1 MiB chunks, `Range` resume, credentials sent to
   `urs.earthdata.nasa.gov` only (never to the presigned S3 redirect); a mid-stream budget
   breach keeps the `.part`. 401 / EDL login form → exit 3, 403 → exit 7 with the URL redacted.

## Tests

`tests/test_download_stack.py` is hermetic: canned ASF payloads (`granuleName`, `startTime`,
`sizeMB`, `downloadUrl`), synthetic reports under `tmp_path`, and a read-only replay of the
committed `data/processed/_availability/*.json`. Cases whose committed report predates stack
derivation are skipped with a note (refreshing them needs the network).
