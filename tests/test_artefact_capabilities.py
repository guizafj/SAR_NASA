"""App-facing capability tests for the frozen artefacts in ``data/processed/{case}/``.

These tests do not validate the pipeline's code or the contract module in isolation: they load the
four published files and ask whether the Streamlit app the README promises can actually be built
from them. One test per capability in README §1:

1. The date slider can scrub the window
2. The coherence filter does something, and does not erase the map
3. Land-use filtering gives a usable map
4. Risk bands partition the grid
5. The shared legend makes the contrast visible
6. Clicking a cell gives a coherent number
7. Every cell can be drawn
8. Units and provenance are always visible
9. The context layer can be drawn without inventing anything

A failure here means "the app cannot do X with the artefacts it is being handed", and the message
names both the capability and the measured number that broke it. The whole module skips when
``data/processed`` contains no complete case directory.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import pathlib

import pandas as pd
import pytest
import yaml

from src.domain import contracts as C

ROOT = pathlib.Path("data/processed")
CONFIG_PATH = pathlib.Path("config/study_cases.yaml")
REQUIRED_FILES = (
    "nisar_grid.parquet",
    "timeseries.csv",
    "context_layers.geojson",
    "meta.json",
)


def _case_dirs() -> list[pathlib.Path]:
    if not ROOT.is_dir():
        return []
    return sorted(
        path
        for path in ROOT.iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and all((path / name).exists() for name in REQUIRED_FILES)
    )


CASE_DIRS = _case_dirs()

pytestmark = pytest.mark.skipif(
    not CASE_DIRS,
    reason=(
        "data/processed has no complete case directory (nisar_grid.parquet, timeseries.csv, "
        "context_layers.geojson, meta.json) — there is nothing for the app to render"
    ),
)


@pytest.fixture(scope="module")
def artefacts() -> dict[str, dict[str, object]]:
    """Read every published case once; the tests assert on these objects, not on the pipeline."""
    loaded: dict[str, dict[str, object]] = {}
    for directory in CASE_DIRS:
        loaded[directory.name] = {
            "dir": directory,
            "grid": pd.read_parquet(directory / "nisar_grid.parquet"),
            "series": pd.read_csv(directory / "timeseries.csv"),
            "meta": json.loads((directory / "meta.json").read_text()),
            "context": json.loads((directory / "context_layers.geojson").read_text()),
        }
    return loaded


@pytest.fixture(scope="module")
def study_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _distinct(frame: pd.DataFrame, column: str) -> list[str]:
    return sorted({str(value) for value in frame[column].dropna().unique()})


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _iso_or_none(value: object) -> str | None:
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# 1. Time scrub
# ---------------------------------------------------------------------------------------------


def test_date_slider_can_scrub_the_window(artefacts):
    for case, art in artefacts.items():
        meta, series = art["meta"], art["series"]
        dates = list(meta.get("dates") or [])

        assert dates, f"date slider cannot scrub the window ({case}): meta.dates is empty"
        assert dates == sorted(dates), (
            f"date slider cannot scrub the window ({case}): meta.dates is not ascending "
            f"(first {dates[0]!r}, last {dates[-1]!r})"
        )
        assert len(set(dates)) == len(dates), (
            f"date slider cannot scrub the window ({case}): meta.dates has "
            f"{len(dates) - len(set(dates))} duplicate date(s) out of {len(dates)}"
        )
        bad_iso = [value for value in dates if _iso_or_none(value) != value]
        assert not bad_iso, (
            f"date slider cannot scrub the window ({case}): non-ISO date(s) {bad_iso[:3]!r}; "
            "the slider needs YYYY-MM-DD to sort and label"
        )

        aoi = series[series["cell_id"] == "aoi"]
        duplicates = int(aoi["date"].duplicated().sum())
        assert duplicates == 0, (
            f"date slider cannot scrub the window ({case}): {duplicates} duplicated 'aoi' "
            f"date(s) across {len(aoi)} rows"
        )
        assert len(aoi) == len(dates), (
            f"date slider cannot scrub the window ({case}): {len(aoi)} 'aoi' rows for "
            f"{len(dates)} dates (exactly one per date is required)"
        )
        assert list(aoi["date"]) == dates, (
            f"date slider cannot scrub the window ({case}): timeseries 'aoi' order "
            f"{list(aoi['date'])[:3]}... does not match meta.dates {dates[:3]}..."
        )


# ---------------------------------------------------------------------------------------------
# 2. Coherence filter
# ---------------------------------------------------------------------------------------------


def test_coherence_filter_does_something_and_does_not_erase_the_map(artefacts):
    for case, art in artefacts.items():
        grid, meta = art["grid"], art["meta"]
        product = str(meta.get("product"))
        assert product in C.SENSOR_PROFILES, (
            f"coherence filter does something ({case}): product {product!r} has no frozen "
            f"sensor profile (declared: {sorted(C.SENSOR_PROFILES)})"
        )
        profile = C.SENSOR_PROFILES[product]
        coherence = grid["coherence"]
        low, high = float(coherence.min()), float(coherence.max())
        span = high - low

        assert span >= 0.20, (
            f"coherence filter does nothing ({case}): coherence spans only {span:.3f} "
            f"({low:.3f}-{high:.3f}); a filter needs at least 0.20 of range to move"
        )

        threshold = profile.coherence_min
        kept = int((coherence >= threshold).sum())
        share = kept / len(grid)
        assert 0.50 <= share <= 1.0, (
            f"coherence filter erases the map ({case}): only {share:.1%} of {len(grid)} cells "
            f"pass the frozen {profile.sensor}/{product} coherence_min {threshold:.2f} "
            f"({kept} cells); the map must keep 50-100%"
        )

        # Scan the filter's own range. If no threshold changes the count, the control is inert.
        steps = 25
        scan = [threshold + (high - threshold) * index / (steps - 1) for index in range(steps)]
        counts = [int((coherence >= value).sum()) for value in scan]
        distinct_counts = len(set(counts))
        assert distinct_counts > 1, (
            f"the coherence filter is inert ({case}): {distinct_counts} distinct cell counts "
            f"across the threshold range {threshold:.2f}-{high:.2f} "
            f"(always {counts[0]} of {len(grid)} cells)"
        )


# ---------------------------------------------------------------------------------------------
# 3. Land use
# ---------------------------------------------------------------------------------------------


def test_land_use_filtering_gives_a_usable_map(artefacts):
    for case, art in artefacts.items():
        grid = art["grid"]
        counts = {str(key): int(value) for key, value in grid["land_use"].value_counts().items()}
        unknown = sorted(set(counts) - set(C.LAND_USE))
        assert not unknown, (
            f"land-use filtering gives a usable map ({case}): undeclared class(es) {unknown}; "
            f"frozen vocabulary is {list(C.LAND_USE)}"
        )
        usable = {name: count for name, count in counts.items() if count >= 50}
        assert len(usable) >= 3, (
            f"land-use filtering gives a usable map ({case}): only {len(usable)} class(es) have "
            f"at least 50 cells ({usable}); counts are {counts}"
        )


# ---------------------------------------------------------------------------------------------
# 4. Risk bands
# ---------------------------------------------------------------------------------------------


def test_risk_bands_partition_the_grid(artefacts):
    populated: set[str] = set()
    per_case: dict[str, dict[str, int]] = {}
    for case, art in artefacts.items():
        grid = art["grid"]
        counts = {
            str(key): int(value) for key, value in grid["risk_level"].value_counts().items()
        }
        per_case[case] = counts
        populated |= set(counts)

    unknown = sorted(populated - set(C.RISK_LEVELS))
    assert not unknown, (
        f"risk bands do not partition the grid: undeclared band(s) {unknown}; "
        f"frozen bands are {list(C.RISK_LEVELS)} (observed {per_case})"
    )
    assert len(populated) >= 2, (
        f"risk bands do not partition the grid: only {len(populated)} band(s) populated "
        f"({sorted(populated)}); counts are {per_case}"
    )

    if "malaga" not in artefacts:
        pytest.skip("malaga case absent, so the declared null-result share cannot be measured")
    malaga_counts = per_case["malaga"]
    malaga_total = sum(malaga_counts.values())
    estable = malaga_counts.get("estable", 0)
    share = estable / malaga_total if malaga_total else 0.0
    assert share >= 0.90, (
        f"the null result is not legible (malaga): only {share:.1%} of {malaga_total} cells are "
        f"'estable' ({estable}); README §1 promises a blank control at >= 90%, counts {malaga_counts}"
    )


# ---------------------------------------------------------------------------------------------
# 5. Shared legend
# ---------------------------------------------------------------------------------------------


def test_shared_legend_makes_the_contrast_visible(artefacts):
    if "mexico-city" not in artefacts or "malaga" not in artefacts:
        pytest.skip("both mexico-city and malaga are needed to measure the shared-legend contrast")

    def spread(case: str) -> tuple[float, float, float]:
        velocity = artefacts[case]["grid"]["velocity_mm_yr"]
        p05 = float(velocity.quantile(0.05))
        p95 = float(velocity.quantile(0.95))
        return p95 - p05, p05, p95

    mexico_spread, mexico_p05, mexico_p95 = spread("mexico-city")
    malaga_spread, malaga_p05, malaga_p95 = spread("malaga")
    assert malaga_spread > 0, (
        f"shared legend makes the contrast visible: malaga velocity spread is {malaga_spread:.4f} "
        f"mm/yr (p05 {malaga_p05:.3f}, p95 {malaga_p95:.3f}); a zero spread is not a measurable blank"
    )
    ratio = mexico_spread / malaga_spread
    assert ratio >= 5.0, (
        f"shared legend hides the contrast: mexico-city p95-p05 is {mexico_spread:.2f} mm/yr "
        f"(p05 {mexico_p05:.2f}, p95 {mexico_p95:.2f}) vs malaga {malaga_spread:.2f} mm/yr "
        f"(p05 {malaga_p05:.2f}, p95 {malaga_p95:.2f}); ratio {ratio:.2f}x, needs >= 5x"
    )


# ---------------------------------------------------------------------------------------------
# 6. Click to interrogate
# ---------------------------------------------------------------------------------------------


def test_clicking_a_cell_gives_a_coherent_number(artefacts):
    for case, art in artefacts.items():
        grid, series, meta = art["grid"], art["series"], art["meta"]
        assert len(grid) >= 25, (
            f"clicking a cell gives a coherent number ({case}): only {len(grid)} cells exist, "
            "cannot sample 25"
        )
        sample = grid.sample(n=25, random_state=20261114)

        residual = (sample["delta_change"] - (sample["val_t2"] - sample["val_t1"])).abs()
        worst = float(residual.max())
        assert worst <= 1e-6, (
            f"clicking a cell gives an incoherent number ({case}): delta_change != val_t2 - val_t1 "
            f"for {int((residual > 1e-6).sum())}/25 sampled cells, max residual {worst:.3e}"
        )

        offset = float(sample["val_t1"].abs().max())
        assert offset <= 1e-9, (
            f"clicking a cell gives a displaced series ({case}): val_t1 is not 0 (max |val_t1| "
            f"{offset:.3e}); cumulative displacement must be referenced to the first date"
        )

        aoi = series[series["cell_id"] == "aoi"].sort_values("date")
        duplicate_dates = int(aoi["date"].duplicated().sum())
        assert duplicate_dates == 0, (
            f"clicking a cell gives a coherent number ({case}): {duplicate_dates} duplicated "
            "'aoi' date(s), so the last value is ambiguous"
        )
        assert list(aoi["date"]) == list(meta.get("dates") or []), (
            f"clicking a cell gives a coherent number ({case}): the 'aoi' dates do not match "
            "meta.dates, so the popup and the slider would plot different windows"
        )
        last_value = float(aoi["value"].iloc[-1])
        mean_delta = float(grid["delta_change"].mean())
        error = abs(last_value - mean_delta)
        assert error <= 1e-6, (
            f"clicking a cell gives an incoherent number ({case}): the AOI series ends at "
            f"{last_value:.6f} mm but the grid mean delta_change is {mean_delta:.6f} mm "
            f"(difference {error:.3e}; the KPI card and the series would disagree)"
        )

        first_value = float(aoi["value"].iloc[0])
        assert abs(first_value) <= 1e-9, (
            f"clicking a cell gives a displaced series ({case}): the AOI series starts at "
            f"{first_value:.6f} mm, not 0"
        )


# ---------------------------------------------------------------------------------------------
# 7. Map rendering
# ---------------------------------------------------------------------------------------------


def test_every_cell_can_be_drawn(artefacts, study_config):
    tolerance = 1e-6  # degrees; positions are cell centroids, not edge crossings
    for case, art in artefacts.items():
        grid = art["grid"]
        for column in ("lat", "lon", "velocity_mm_yr", "coherence"):
            nulls = int(grid[column].isna().sum())
            assert nulls == 0, (
                f"every cell can be drawn ({case}): {nulls} null/NaN value(s) in {column!r} "
                f"across {len(grid)} cells; a marker cannot be placed or coloured"
            )
            non_finite = sum(
                1 for value in grid[column] if not math.isfinite(float(value))
            )
            assert non_finite == 0, (
                f"every cell can be drawn ({case}): {non_finite} non-finite value(s) in "
                f"{column!r} across {len(grid)} cells"
            )

        assert case in study_config["cases"], (
            f"every cell can be drawn ({case}): no bbox in config/study_cases.yaml for this case"
        )
        west, south, east, north = study_config["cases"][case]["bbox"]
        outside = grid[
            (grid["lon"] < west - tolerance)
            | (grid["lon"] > east + tolerance)
            | (grid["lat"] < south - tolerance)
            | (grid["lat"] > north + tolerance)
        ]
        assert outside.empty, (
            f"every cell can be drawn ({case}): {len(outside)}/{len(grid)} cells fall outside the "
            f"configured bbox [{west}, {south}, {east}, {north}] (tolerance {tolerance}); "
            f"first offender is lat {float(outside['lat'].iloc[0]):.6f}, "
            f"lon {float(outside['lon'].iloc[0]):.6f}"
        )

        duplicates = int(grid["cell_id"].duplicated().sum())
        assert duplicates == 0, (
            f"every cell can be drawn ({case}): {duplicates} duplicate cell_id value(s) across "
            f"{len(grid)} rows; two markers would land on one cell"
        )


# ---------------------------------------------------------------------------------------------
# 8. Evidence: units and provenance
# ---------------------------------------------------------------------------------------------


def test_units_and_provenance_are_always_visible(artefacts):
    for case, art in artefacts.items():
        grid, series, meta = art["grid"], art["series"], art["meta"]

        grid_units = _distinct(grid, "unit")
        series_units = _distinct(series, "unit")
        assert grid_units == [C.UNIT], (
            f"units are not visible ({case}): grid.unit is {grid_units}, expected ['{C.UNIT}']"
        )
        assert series_units == [C.UNIT], (
            f"units are not visible ({case}): timeseries.unit is {series_units}, "
            f"expected ['{C.UNIT}']"
        )
        velocity_units = _distinct(grid, "velocity_unit")
        assert velocity_units == [C.VELOCITY_UNIT], (
            f"units are not visible ({case}): grid.velocity_unit is {velocity_units}, "
            f"expected ['{C.VELOCITY_UNIT}']"
        )

        grid_sensor = _distinct(grid, "sensor")
        series_sensor = _distinct(series, "sensor")
        grid_product = _distinct(grid, "product")
        series_product = _distinct(series, "product")
        meta_sensor, meta_product = str(meta.get("sensor")), str(meta.get("product"))
        assert len(grid_sensor) == len(series_sensor) == 1, (
            f"provenance is ambiguous ({case}): grid.sensor {grid_sensor}, "
            f"timeseries.sensor {series_sensor}"
        )
        assert len(grid_product) == len(series_product) == 1, (
            f"provenance is ambiguous ({case}): grid.product {grid_product}, "
            f"timeseries.product {series_product}"
        )
        assert grid_sensor[0] == series_sensor[0] == meta_sensor, (
            f"provenance disagrees ({case}): sensor grid={grid_sensor[0]!r}, "
            f"timeseries={series_sensor[0]!r}, meta={meta_sensor!r}"
        )
        assert grid_product[0] == series_product[0] == meta_product, (
            f"provenance disagrees ({case}): product grid={grid_product[0]!r}, "
            f"timeseries={series_product[0]!r}, meta={meta_product!r}"
        )

        grid_source = _distinct(grid, "source")
        provenance = meta.get("provenance") or {}
        meta_source = provenance.get("source")
        assert len(grid_source) == 1 and _non_empty(grid_source[0]), (
            f"provenance is not visible ({case}): grid.source is {grid_source}; every row needs "
            "one non-empty provider badge"
        )
        assert _non_empty(meta_source), (
            f"provenance is not visible ({case}): meta.provenance.source is {meta_source!r}"
        )
        assert grid_source[0].strip().lower() == str(meta_source).strip().lower(), (
            f"provenance disagrees ({case}): grid.source {grid_source[0]!r} != "
            f"meta.provenance.source {meta_source!r}; the badge and the grid would tell two stories"
        )
        if meta_product == "mock":
            assert "mock" in grid_source[0].lower() and "mock" in str(meta_source).lower(), (
                f"provenance is not visible ({case}): product is 'mock' but grid.source "
                f"{grid_source[0]!r} / meta.provenance.source {meta_source!r} do not say so"
            )

        for key in ("source", "method", "reference", "license"):
            assert _non_empty(provenance.get(key)), (
                f"provenance is not visible ({case}): meta.provenance.{key} is "
                f"{provenance.get(key)!r}; the evidence badge would be blank"
            )

        graph_ids = set(_distinct(grid, "provenance_id"))
        series_ids = set(_distinct(series, "provenance_id"))
        meta_provenance_id = provenance.get("provenance_id")
        meta_ids = {str(meta_provenance_id)} if meta_provenance_id is not None else set()
        all_ids = graph_ids | series_ids | meta_ids
        assert graph_ids and series_ids and meta_ids, (
            f"provenance is not resolvable ({case}): grid={sorted(graph_ids)}, "
            f"timeseries={sorted(series_ids)}, meta={sorted(meta_ids)}"
        )
        assert len(all_ids) == 1, (
            f"provenance is not resolvable ({case}): {len(all_ids)} distinct provenance ids "
            f"across grid, timeseries and meta: {sorted(all_ids)}"
        )
        provenance_id = next(iter(all_ids))
        assert C.PROVENANCE_ID_RE.match(provenance_id), (
            f"provenance is not visible ({case}): provenance_id {provenance_id!r} does not match "
            f"the frozen format {C.PROVENANCE_ID_RE.pattern}"
        )

        limitations = meta.get("limitations")
        assert isinstance(limitations, list) and limitations, (
            f"provenance is not visible ({case}): meta.limitations is {limitations!r}; the honest "
            "caveats are part of the evidence"
        )
        blank_limitations = [item for item in limitations if not _non_empty(item)]
        assert not blank_limitations, (
            f"provenance is not visible ({case}): {len(blank_limitations)} empty limitation(s) "
            f"in {limitations!r}"
        )
        assert _non_empty(meta.get("null_policy")), (
            f"provenance is not visible ({case}): meta.null_policy is {meta.get('null_policy')!r}; "
            "the app must explain why nodata is absent rather than zero"
        )


# ---------------------------------------------------------------------------------------------
# 9. Context layer
# ---------------------------------------------------------------------------------------------

_ALLOWED_GEOMETRIES = {"Point", "LineString", "Polygon", "MultiPolygon"}


def test_context_layer_can_be_drawn_without_inventing_anything(artefacts):
    for case, art in artefacts.items():
        context = art["context"]
        assert context.get("type") == "FeatureCollection", (
            f"context layer cannot be drawn ({case}): GeoJSON type is {context.get('type')!r}, "
            "expected 'FeatureCollection'"
        )
        features = context.get("features") or []
        assert features, (
            f"context layer cannot be drawn ({case}): FeatureCollection has 0 features; the app "
            "has no coast, infrastructure or boundary to show"
        )

        required = [field.name for field in C.CONTEXT_FIELDS if field.required]
        for index, feature in enumerate(features):
            properties = feature.get("properties") or {}
            missing = [name for name in required if name not in properties]
            assert not missing, (
                f"context layer cannot be drawn ({case}): feature {index} is missing frozen "
                f"propertie(s) {missing}"
            )

            layer = str(properties["layer"])
            assert layer in C.CONTEXT_LAYERS, (
                f"context layer cannot be drawn ({case}): feature {index} has layer {layer!r}; "
                f"frozen layers are {list(C.CONTEXT_LAYERS)}"
            )

            feature_class = str(properties["class"])
            allowed_classes = C.CONTEXT_CLASSES[layer]
            assert feature_class in allowed_classes, (
                f"context layer cannot be drawn ({case}): feature {index} has class "
                f"{feature_class!r} in layer {layer!r}; frozen vocabulary is {list(allowed_classes)}"
            )

            assert str(properties["crs"]) == C.CRS_OUTPUT, (
                f"context layer cannot be drawn ({case}): feature {index} declares crs "
                f"{properties['crs']!r}, expected {C.CRS_OUTPUT!r}"
            )
            assert str(properties["case_id"]) == case, (
                f"context layer cannot be drawn ({case}): feature {index} carries case_id "
                f"{properties['case_id']!r}; a layer from another city would be silently overlaid"
            )
            assert _non_empty(properties["source"]), (
                f"context layer cannot be drawn ({case}): feature {index} has blank source"
            )
            provenance_id = str(properties["provenance_id"])
            assert C.PROVENANCE_ID_RE.match(provenance_id), (
                f"context layer cannot be drawn ({case}): feature {index} provenance_id "
                f"{provenance_id!r} does not match {C.PROVENANCE_ID_RE.pattern}"
            )

            geometry = feature.get("geometry") or {}
            geometry_type = geometry.get("type")
            assert geometry_type in _ALLOWED_GEOMETRIES, (
                f"context layer cannot be drawn ({case}): feature {index} has geometry type "
                f"{geometry_type!r}; allowed {sorted(_ALLOWED_GEOMETRIES)}"
            )
            coordinates = geometry.get("coordinates")
            assert coordinates, (
                f"context layer cannot be drawn ({case}): feature {index} ({layer}/"
                f"{feature_class}) has empty coordinates"
            )
