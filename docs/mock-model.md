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
perturb another's numbers. Draw order is fixed (noise → land-use bare patches → coherence jitter →
water coherence). Regenerating into an empty directory reproduces all twelve files
**byte-identically** (verified with `cmp`), because no wall clock enters the output
(`PROCESSING_DATE` and `RETRIEVED_UTC` are constants), JSON is written with `sort_keys=True`, rows
are sorted, floats are formatted `%.9f`, and every write is a temp file plus `os.replace`.

## Spatial layers and coherence

The model's other layers, in precedence order. `rho` is the normalised **elliptical radius from the
coverage-ellipse centre** (0 at the centre, 1 at the rim) — it is *not* measured from `peak_lonlat`
(Jakarta's peak sits at `rho ≈ 1.02`, just outside the nodata domain). The arrows are a
**first-match-wins** cascade, and the
"corridor" is the synthetic industrial strip declared in `config/study_cases.yaml`:

- **land use**: fringe `bare` (`rho ≥ 0.80` and a draw < 0.35) → `urban-residential` (`rho < 0.80`)
  → `urban-dense` (`rho < 0.38`) → `industrial` (within 3 km of the corridor axis **and**
  `rho < 0.95`) → `infrastructure` (within 0.55 km) → and **water wins over everything** (lake
  ellipse, coast side, river/canal within 0.30 km).
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
