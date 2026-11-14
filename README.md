# Three Cities, One Pump

**Land subsidence, aquifer storage loss, and the geology in between — mapped with NISAR + Sentinel-1.**

NASA Space Apps Challenge 2026 — *Dancing with the SARs*

---

## 1. Thesis

Subsidence magnitude is governed by the **compressibility of aquifer interbeds**, not by how
much groundwater is pumped. Three cities, comparable overdraft, three different outcomes:

| | **Mexico City** | **Jakarta** | **Málaga** |
|---|---|---|---|
| Aquifer | Lacustrine volcaniclastic clay, closed basin | Deltaic soft clay, coastal | Thin detrital cover, confined karst |
| Compressibility | Extreme | High | Low |
| Regime | Mostly **inelastic** | Mixed | Mostly **elastic** |
| Subsidence | **−45 cm/yr** | ~5 cm/yr avg; >1 m cumulative | mm–cm/yr |
| Permanence | Permanent capacity loss (~60 hm³/yr) | Permanent | Largely recoverable |
| Failure mode | Land loss, faulting, drainage inversion | Inundation, capital relocation | Salinization, reservoir depletion |

**Claim:** InSAR is the correct instrument in Mexico City and Jakarta, and the **wrong**
instrument in Málaga — knowing which is which *is* the result.

---

## 2. Study areas

| City | AOI | Role | Primary hazard |
|---|---|---|---|
| Mexico City, MX | ~19.43 N, −99.13 W | Case A (extreme) | Differential subsidence → infrastructure, drainage inversion |
| Jakarta, ID | ~−6.21 S, 106.85 E | Case B (coastal) | Inundation; 64% piped-water coverage drives well pumping |
| Málaga, ES | ~36.72 N, −4.42 W | **Control / negative** | Seawater intrusion; reservoir depletion |

---

## 3. Data sources

`✓` = live-checked. Access is open/free unless noted.

### SAR / deformation
| Source | URL | Status |
|---|---|---|
| NISAR product summary (ASF) | `asf.alaska.edu/datasets/daac/nisar` | ✓ |
| NISAR Data User Guide | `nisar-docs.asf.alaska.edu` | ✓ |
| ASF Vertex (search/download) | `search.asf.alaska.edu` | ✓ |
| **HyP3** (on-demand cloud InSAR) | `hyp3-docs.asf.alaska.edu` | ✓ |
| OPERA DISP-S1 displacement | `displacement.asf.alaska.edu` | ✓ |
| **Copernicus EGMS** (EU, mm/yr) | `egms.land.copernicus.eu` | ✓ |
| COMET-LiCSAR (Sentinel-1 ifgs) | `comet.nerc.ac.uk/comet-lics-portal` | ✓ |

> **NISAR constraint.** Documented interferometry products (RIFG/RUNW/GOFF/ROFF, and **GUNW**,
> which derives from RUNW) are **cryosphere-generated**; SME2 excludes urban areas. Verify
> coverage in Vertex before depending on NISAR for city-wide displacement. Fallbacks: HyP3
> Sentinel-1, OPERA DISP-S1, EGMS, or local ISCE2+MintPy from GSLC.

### Hydrology / groundwater
| Source | URL | Status |
|---|---|---|
| GRACE-FO TELLUS mascons | `podaac.jpl.nasa.gov/dataset/TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4` | ✓ |
| GLDAS | `ldas.gsfc.nasa.gov/gldas` | ✓ |
| IGRAC GGIS | `ggis.un-igrac.org` | ✓ |
| USGS NWIS (API) | `api.waterdata.usgs.gov` | ✓ |
| Junta de Andalucía SAIH (ES) | reservoir/head telemetry | — |
| CONAGUA / SACMEX (MX) | aquifer + supply reports | — |

### Urban context
| Source | URL | Status |
|---|---|---|
| GHSL built-up + population | `ghsl.jrc.ec.europa.eu` | ✓ |
| WorldPop | `worldpop.org` | ✓ |
| DLR World Settlement Footprint | `geoservice.dlr.de/web/maps/eoc:wsf2019` | ✓ |
| Copernicus EMS (event maps) | `emergency.copernicus.eu` | ✓ |
| Building footprints | Overture / Google Open Buildings / OSM | ✓ |

---

## 4. Software stack

**InSAR**
- `isce2` — SLC → interferogram (`conda install -c conda-forge isce2`)
- `snaphu` — phase unwrapping (`web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/`)
- **`MintPy`** — time-series inversion, DEM error, velocity (`mintpy.readthedocs.io`)
- `hyp3_sdk` — submit Sentinel-1 jobs to ASF HyP3, no local install
- `opera-utils` — DISP-S1 readers (`github.com/opera-adt/opera-utils`)
- `RAiDER` — tropospheric delay (`github.com/dbekaert/RAiDER`); `gacos.net` as alternative

**Geospatial Python**
`xarray` · `dask` · `rioxarray` · `rasterio` · `geopandas` · `shapely` · `pyproj` ·
`rasterstats` · `scipy.ndimage` (gradients) · `GDAL` CLI (warp/resample)

**Data access**
`asf_search` · `earthaccess` · `pystac-client` · `odc-stac` · `dataretrieval`

**Viz / app**
`maplibre` or `deck.gl` (frontend) · `kepler.gl` (exploration) · `panel`/`holoviews` or
`streamlit` (dashboard) · `QGIS` (QC and figure production)

---

## 5. Methodology skeleton

```
P0  Scope        AOI polygons, EPSG:326xx UTM, reference point per city
P1  Ingest       search → download → STAC/COG catalog
P2  Interfero    HyP3 or ISCE2+SNAPHU → GUNW/RUNW per pair
P3  Correct      troposphere (RAiDER/GACOS), DEM error (MintPy), orbital ramps
P4  Time series  network inversion → cumulative displacement cube
P5  Decompose    LOS → vertical (asc+desc, v_north≈0) or single-track w/ stated assumption
P6  Separate     linear trend + seasonal harmonics + step terms
                 → secular (inelastic) vs seasonal (elastic)
P7  Metrics      velocity, |∇v| gradient, compaction volume, S* = Δb/Δh
P8  Join         groundwater head, population, buildings, reservoirs
P9  Deliver      app: map + time series + same-scale comparison toggle
```

### Key equations

**Line of sight** (unit vector `u`, incidence `θ`, heading `α`):

```
d_LOS = u · [v_up, v_east, v_north]ᵀ ,   u = [cosθ, −sinθ·cosα, −sinθ·sinα]
```
Two geometries (asc + desc) → 2 equations, 3 unknowns → assume `v_north ≈ 0` or constrain
with GNSS. **Axis/sign conventions differ between ISCE2, MintPy and SNAP — validate against
GNSS or levelling before publishing numbers.**

**Time-series model:**

```
v(t) = v₀ + ṫ·t + Σₖ [Aₖcos(2πkt/T) + Bₖsin(2πkt/T)] + Σⱼ Δᵥⱼ·H(t−tⱼ)
```
`ṫ` → inelastic/permanent (storage loss); seasonal amplitude → elastic/recoverable.
Report `R = |ṫ| / A_seasonal`.

**Storage loss:**

```
S_skv = Δb / (b₀·Δh)            skeletal specific storage
dV/dt = Σ_px v_up · A_px        compaction-driven storage loss [m³/yr]
```

---

## 6. Pipeline pseudocode

```python
# ---------- P1: ingest ----------
aoi = load_aoi(city)                     # GeoJSON, EPSG:4326 -> reproject to UTM
pairs = asf_search.search(
    dataset="NISAR", processingLevel="GUNW",
    intersects=aoi, start=START, end=END, beamMode="IW")
if not pairs:
    pairs = asf_search.search(dataset="SENTINEL-1", processingLevel="SLC", intersects=aoi)
    jobs  = [hyp3.submit_insar(slc_pair) for slc_pair in make_pairs(pairs, dt_max=48)]
# EU fallback: EGMS ortho product, already velocity (no processing)

# ---------- P2-P3: interferometry ----------
for pair in pairs:
    ifg   = isce2.run_stripmap(slc1, slc2)          # or download HyP3 GUNW
    unw   = snaphu.unwrap(ifg, coh, mask=coh < 0.3)
    unw   = raider.correct_troposphere(unw, weather_model="ERA5")
    save(unw)

# ---------- P4: time series ----------
ts = mintpy.load(unw_list, geometry=dem, reference=ref_pixel)
ts = mintpy.correct_DEM_error(ts); ts = mintpy.deramp(ts)
disp = mintpy.network_inversion(ts)                    # (t, y, x) cumulative LOS

# ---------- P5: LOS -> vertical ----------
if asc and desc:
    v_up, v_east = solve_two_track(disp_asc, disp_desc, theta, alpha, assume_vnorth=0)
else:
    v_up = disp / cos(theta);  warn("single-track: assuming purely vertical")

# ---------- P6: decompose ----------
fit = fit_harmonics(v_up, period=365.25, n=2)          # returns trend, amps, steps
secular, seasonal = fit.trend, fit.seasonal_amplitude
regime = "inelastic" if abs(secular) > 2*seasonal else "elastic"

# ---------- P7: metrics ----------
vel     = np.median(secular) * 365.25                  # m/yr
mask    = (coh > 0.5) & ghsl_urban & ~water
grad    = np.hypot(*sobel(secular))                    # differential subsidence
volume  = np.nansum(secular[mask] * pixel_area)        # m^3/yr compaction
S_star  = regress(delta_b = secular, delta_h = well_head_change)

# ---------- P8: join ----------
exposure = zonal_stats(vel, buildings)                 # buildings in sinking zones
flood    = dem + cumulative_subsidence + slr_projection

# ---------- P9: deliver ----------
compare(mexico_city, jakarta, malaga, shared_color_scale=True)
```

---

## 7. Validation

| Check | Method |
|---|---|
| Velocity accuracy | GNSS / levelling; target < 5 mm/yr RMSE |
| Unwrapping errors | closure phase on redundant triplets |
| Atmosphere | compare with GACOS/ERA5-corrected run |
| Urban flood mask | Copernicus EMS activation maps |
| Null result (Málaga) | detection-limit analysis — report upper bound, don't force a signal |

**Stacking error budget:** unwrapping ≫ troposphere ≫ orbital ≫ DEM ≫ noise. Report it.

---

## 8. Known limitations

1. **Málaga may be at the detection floor.** Present the bound as the finding.
2. **CDMX is not coastal** — do not claim a coastal framing across all three.
3. **NISAR urban coverage is not guaranteed** (see §3). Always have a Sentinel-1 path.
4. GUNW posting is 80 m — city-scale, not building-scale.
5. Urban SAR flood mapping is confounded by double-bounce and radar shadow; use change
   detection + coherence loss, not simple thresholding.

---

## 9. Repo layout

```
data/raw/{city}/        downloads (not committed)
data/processed/{city}/  velocity, masks, cubes (GeoTIFF/NetCDF)
notebooks/              01_ingest, 02_insar, 03_timeseries, 04_metrics, 05_app
src/sinkcity/           pipeline library
app/                    map + dashboard
docs/                   methods, error budget, figures
```

---

## 10. Key references

| Topic | Cite | DOI |
|---|---|---|
| Global subsidence threat | Herrera-García et al. 2021, *Science* | 10.1126/science.abb8549 |
| Global subsidence → storage loss | Hasan et al. 2023, *Nat Commun* | 10.1038/s41467-023-41933-z |
| Mexico City −45 cm/yr, compaction volume | Cigna & Tapete 2022, *GRL* | 10.1029/2022gl098923 |
| 99 coastal cities, uniform PS-InSAR | Wu et al. 2022, *GRL* | 10.1029/2022gl098477 |
| Subsidence + groundwater, method review | Galloway & Burbey 2011, *Hydrogeol J* | 10.1007/s10040-011-0775-5 |
| Jakarta sinking cities | Chaussard et al. 2013, *RSE* | 10.1016/j.rse.2012.10.015 |
| Jakarta 2017–22 multi-track SBAS | 2024, *Geocarto Int* | 10.1080/10106049.2024.2364726 |
| Jakarta piped water → abstraction | 2022, *Water* | 10.3390/w14203197 |
| Málaga/Torremolinos subsidence | Ruiz-Constán et al. 2018, *STOTEN* | 10.1016/j.scitotenv.2018.04.280 |
| Axarquía water crisis 2019–24 | Junquera et al. 2025, *PNAS* | 10.1073/pnas.2508055122 |
| NISAR Data User Guide | Kristenson et al. 2026 | 10.5281/ZENODO.20075813 |

---

*Draft. Rates for Jakarta vary by method and period — see §10 and quote the source you used.*
