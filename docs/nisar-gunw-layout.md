# NISAR L2 GUNW: what is actually inside the file

**Status:** verified against published documentation, **not yet against a real 2026 granule**.
Compiled 2026-11-14. Read this before writing any reader; then run
`./scripts/python.sh pipeline/ingest/inspect_hdf5.py <granule>` on the first real file and correct
this page from evidence (§7).

Sources:
- NISAR Data User Guide — *Geocoded Unwrapped Interferogram*: <https://nisar-docs.asf.alaska.edu/gunw/>
- NASA Earthdata — *Work with NISAR Sample Data* (lists the sample granule's datasets verbatim):
  <https://www.earthdata.nasa.gov/learn/tutorials/work-nisar-sample-data>
- NISAR Data User Guide — *Data Format: HDF5*: <https://nisar-docs.asf.alaska.edu/data-format/>
- NISAR Data User Guide — *Product Specifications*: <https://nisar-docs.asf.alaska.edu/product-specification/>

---

## 1. What the product is

| Property | Value |
|---|---|
| Level / type | L2, geocoded, unwrapped interferogram (GUNW) |
| Derived from | RIFG + RUNW, geocoded with the reference RSLC orbit and a DEM |
| Posting | **80 m** for unwrapped phase and 80 m coherence (wrapped interferogram and a 20 m coherence layer also exist) |
| Frequency | **frequency A only** — `frequencyB` is not generated |
| Polarisation | **co-polarised only** — `HH` **or** `VV`, never both |
| Pairing | nearest-neighbour pairs at the **12-day** repeat (this is the revisit that governs coherence comparability) |
| Phase units | **radians**, `float32` |
| Coherence | `float32`, 0–1 |
| Connected components | `uint32` |
| Interpolation | bilinear for floats, Sinc for complex, nearest-neighbour for unsigned ints |

Two defaults that matter for `provenance`: the **ionospheric phase screen is included but not
applied**, and **external phase corrections are not removed**. Whatever we do about that must be
written into `meta.provenance.method`, or the number is unreproducible.

## 2. Paths inside the file

Root is `/science/LSAR/GUNW/`. `LSAR` is the L-band instrument; nothing lives at `/`.

```
/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/{POL}/unwrappedPhase                    float32  rad
/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/{POL}/coherenceMagnitude                float32  0..1
/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/{POL}/connectedComponents               uint32
/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/{POL}/ionospherePhaseScreen             float32
/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/{POL}/ionospherePhaseScreenUncertainty   float32
/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/mask                                    int8     quality flags
/science/LSAR/GUNW/grids/frequencyA/wrappedInterferogram/{POL}/wrappedInterferogram                complex64
/science/LSAR/GUNW/grids/frequencyA/wrappedInterferogram/{POL}/coherenceMagnitude                  float32
/science/LSAR/GUNW/grids/frequencyA/pixelOffsets/{POL}/alongTrackOffset                            float32
/science/LSAR/GUNW/grids/frequencyA/pixelOffsets/{POL}/slantRangeOffset                            float32
/science/LSAR/GUNW/grids/frequencyA/pixelOffsets/{POL}/correlationSurfacePeak                      float32
/science/LSAR/GUNW/metadata/radarGrid/...                                                          baselines, orbits
```

`{POL}` is `HH` or `VV` — read it from the file, never hardcode it.

### The mask

Observed values in the published sample: `0` (no quality flags), `2` (some flag set), `127`
(padding around valid data). Treat `127` as **nodata/padding** and drop it; keep `0` and `2` but
carry the mask value through so a filter can be applied later. Contract rule applies: nodata cells
are **absent** from the artefacts, never written as 0.

## 3. Geolocation

Spatial reference is encoded with **CF metadata conventions**, so GDAL/xarray/rioxarray can read
the grid once the extension is treated as NetCDF (the documented workaround is renaming `.h5` to
`.nc`). The projected CRS for an L2 geocoded product is UTM (or polar stereographic at high
latitudes) — for our three cases that is `EPSG:32614` (Mexico City), `EPSG:32748` (Jakarta),
`EPSG:32630` (Málaga), matching `config/study_cases.yaml: crs_analysis`.

**Not confirmed from the docs and must be read from the file:** the exact names of the x/y
coordinate datasets and where the EPSG code lives (a `projection`/`spatial_ref` dataset versus CF
attributes). Do not guess these — the inspector prints them.

## 4. Phase → displacement

```
d_LOS = -λ / (4π) · φ_unwrapped          (sign convention must be validated, see below)
λ_L  ≈ 0.2384 m                          (NISAR L-band, ~1.2575 GHz — confirm in metadata)
```

**Scale check** (computed through `pipeline/science/transforms.py`, θ = 39°):

| quantity | LOS | vertical |
|---|---:|---:|
| 1 radian of phase | 18.97 mm | **24.41 mm** |
| one fringe (2π) | 119.2 mm | **153.4 mm** |

The vertical scale depends only on `u_up = cosθ`, so ascending and descending geometries give the
same vertical conversion — they differ in the *east/north* terms (`u = [0.777, +0.616, +0.131]` vs
`[0.777, −0.616, −0.131]` at heading 192° vs 12°), which is why one pass cannot resolve the
horizontal ambiguity.

Sanity anchor for the demo: Mexico City at −420 mm/yr moves **−13.8 mm vertical in a 12-day pair**,
i.e. about **0.09 of a fringe**. A ninth of a fringe per pair is why unwrapping error, not noise,
dominates the budget (README §13) — and why a single bad cycle slip outranks every other term.

Then project LOS → vertical with `pipeline/science/transforms.py` (`v_north ≈ 0`), and reference to
the case's `reference_point` before aggregation. **Axis and sign conventions differ between
ISCE2, MintPy and SNAP** (README §6): validate against GNSS or a published rate before publishing a
velocity, and record which convention was used.

## 5. What we still do not know

1. Exact x/y coordinate dataset names, and the location of the CRS/EPSG.
2. Whether the 80 m or 20 m coherence layer is the better choice for a city at H3 res 9 (~200 m).
3. Whether real 2026 urban granules carry the same layer set as the 2008 sample (pre-launch test
   data). Urban coverage is the bigger question — see `docs/data-source-decision.md`.
4. Fill/no-data conventions for GUNW specifically: `-9999` is documented for SME2, **not** for
   GUNW, so do not assume it.

## 6. Third-party shortcut worth evaluating

`nisar-pytools` (PyPI) claims `open_nisar()`, `dataset_paths()`, `get_gunw()` and a `DataTree`
interface, i.e. exactly the reader described above. Evaluate it before hand-rolling more code —
but pin the version and record it in `provenance`, and do not let it become an undocumented
dependency in a 48 h project.

## 7. Correction protocol

When the first real granule lands:

```bash
./scripts/python.sh pipeline/ingest/inspect_hdf5.py data/raw/<case>/nisar_gunw/<granule>.h5 --json > /tmp/tree.json
```

Diff `/tmp/tree.json` against §2 and edit this page in the same commit that changes the reader.
This file is the evidence for `meta.provenance.method`; a stale version of it is a false provenance
claim.
