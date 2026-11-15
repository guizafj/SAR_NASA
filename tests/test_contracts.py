"""Regression suite for the FROZEN data contract v1.1 (`src/domain/contracts.py`).

These tests are deliberately brittle. If one fails, either the change was agreed by all three
roles and the golden snapshot must move with a `SCHEMA_VERSION` bump, or someone drifted the
contract silently — which is exactly what the freeze exists to prevent.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math

import pandas as pd
import pytest

from src.domain import contracts as C


# ---------------------------------------------------------------------------------------------
# Fixtures: the smallest artefact set that is fully contract-conformant.
# ---------------------------------------------------------------------------------------------

PROV = C.provenance_id("mexico-city", "nisar-gunw", dt.date(2026, 11, 14), "abc12345")


def grid_row(**overrides):
    row = {
        "cell_id": "8928308280fffff",
        "lat": 19.4326,
        "lon": -99.1332,
        "val_t1": 0.0,
        "val_t2": -7.4,
        "delta_change": -7.4,
        "velocity_mm_yr": -128.0,
        # Contract 1.1: |trend| / amplitude, with a positive amplitude, and the regime the
        # pipeline's rule (R = |trend| / A: >2 inelastic, 1..2 transitional, <1 elastic) gives.
        "seasonal_amp_mm": 9.4,
        "ratio_R": 128.0 / 9.4,
        "regime": "inelastic",
        "coherence": 0.82,
        "land_use": "urban-dense",
        "risk_level": "critico",
        "case_id": "mexico-city",
        "sensor": "nisar",
        "product": "nisar-gunw",
        "unit": "mm",
        "velocity_unit": "mm/year",
        "source": "ASF DAAC",
        "acquisition_date": "2026-07-02",
        "provenance_id": PROV,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def grid():
    return pd.DataFrame([grid_row()])


@pytest.fixture()
def timeseries():
    return pd.DataFrame(
        [
            {
                "case_id": "mexico-city",
                "cell_id": "aoi",
                "date": "2026-07-02",
                "value": 0.0,
                "trend_mm_yr": -128.0,
                "seasonal_mm": 0.0,
                "unit": "mm",
                "sensor": "nisar",
                "product": "nisar-gunw",
                "quality": "good",
                "provenance_id": PROV,
            },
            {
                "case_id": "mexico-city",
                "cell_id": "aoi",
                "date": "2026-07-14",
                "value": -4.2,
                # value - trend_mm_yr*t - seasonal_mm == 0 on both rows: the published trend and
                # seasonal really are the decomposition of the published cumulative displacement.
                "trend_mm_yr": -128.0,
                "seasonal_mm": 0.0,
                "unit": "mm",
                "sensor": "nisar",
                "product": "nisar-gunw",
                "quality": "good",
                "provenance_id": PROV,
            },
        ]
    )


@pytest.fixture()
def meta():
    return {
        "schema_version": "1.1",
        "case_id": "mexico-city",
        "name": "Mexico City, MX",
        "bbox": [-99.35, 19.20, -98.85, 19.65],
        "crs": "EPSG:4326",
        "reference_point": [-99.13, 19.43],
        "dates": ["2026-07-02", "2026-07-14"],
        "revisit_days": 12,
        "sensor": "nisar",
        "product": "nisar-gunw",
        "thresholds": {
            "coherence_min": 0.35,
            "k_sigma": 2.5,
            "min_ddB": 3.0,
            "persistence": "2of3",
            "min_area_m2": 500,
        },
        "provenance": {
            "source": "ASF DAAC",
            "product_id": "NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH",
            "processing_date": "2026-11-14",
            "method": "LOS to vertical (v_north=0), mean per H3 res 9 cell",
            "retrieved_utc": "2026-11-14T16:02:00Z",
            "reference": "Herrera-Garcia et al. 2021, doi:10.1126/science.abb8549",
            "license": "NASA open data",
        },
        "stats": {
            "n_cells": 15482,
            "n_dates": 12,
            "median_velocity_mm_yr": -128.0,
            "median_seasonal_amp_mm": 9.4,
            "median_ratio_R": 128.0 / 9.4,
            "dominant_regime": "inelastic",
            "p05_velocity_mm_yr": -410.0,
            "p95_velocity_mm_yr": 12.0,
        },
        "limitations": ["GUNW posting is 80 m: city scale, not building scale."],
        "null_policy": "Cells without data are absent; never written as 0.",
    }


@pytest.fixture()
def context():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-99.13, 19.43]},
                "properties": {
                    "layer": "infrastructure",
                    "class": "water-well",
                    "name": "Pozo Roma",
                    "case_id": "mexico-city",
                    "crs": "EPSG:4326",
                    "source": "SACMEX",
                    "provenance_id": PROV,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------------------------
# Golden snapshots — the freeze itself.
# ---------------------------------------------------------------------------------------------


def test_schema_version_and_revision_are_frozen():
    assert C.SCHEMA_VERSION == "1.1"
    assert C.CONTRACT_REVISION == 2
    assert C.CHANGELOG[0][0] == "1.1"
    # The previous freeze is still recorded: a bump appends, it never rewrites history.
    assert [version for version, _ in C.CHANGELOG] == ["1.1", "1.0"]


def test_grid_field_order_is_frozen():
    assert [f.name for f in C.GRID_FIELDS] == [
        "cell_id", "lat", "lon", "val_t1", "val_t2", "delta_change", "velocity_mm_yr",
        "seasonal_amp_mm", "ratio_R", "regime",
        "coherence", "land_use", "risk_level", "case_id", "sensor", "product", "unit",
        "velocity_unit", "source", "acquisition_date", "provenance_id",
    ]


def test_timeseries_field_order_is_frozen():
    assert [f.name for f in C.TIMESERIES_FIELDS] == [
        "case_id", "cell_id", "date", "value", "trend_mm_yr", "seasonal_mm", "unit",
        "sensor", "product", "quality", "provenance_id",
    ]


def test_context_field_order_is_frozen():
    assert [f.name for f in C.CONTEXT_FIELDS] == [
        "layer", "class", "name", "case_id", "crs", "source", "provenance_id",
    ]


def test_required_key_lists_are_frozen():
    assert C.META_REQUIRED_KEYS == (
        "schema_version", "case_id", "name", "bbox", "crs", "reference_point", "dates",
        "revisit_days", "sensor", "product", "thresholds", "provenance", "stats",
        "limitations", "null_policy",
    )
    assert C.STATS_REQUIRED_KEYS == (
        "n_cells", "n_dates", "median_velocity_mm_yr", "median_seasonal_amp_mm",
        "median_ratio_R", "dominant_regime", "p05_velocity_mm_yr", "p95_velocity_mm_yr",
    )
    assert C.PROVENANCE_REQUIRED_KEYS == (
        "source", "product_id", "processing_date", "method", "retrieved_utc", "reference",
        "license",
    )


def test_enums_are_frozen():
    assert C.CASE_IDS == ("mexico-city", "jakarta", "malaga")
    assert C.SENSORS == ("nisar", "sentinel-1")
    assert C.PRODUCTS == (
        "nisar-gunw", "nisar-gcov", "opera-disp-s1", "hyp3-s1", "egms", "mock",
    )
    assert C.LAND_USE == (
        "urban-dense", "urban-residential", "industrial", "infrastructure", "water", "bare",
        "vegetation", "unknown",
    )
    assert C.RISK_LEVELS == ("estable", "moderado", "critico")
    assert C.QUALITY == ("good", "fair", "poor")
    # 1.1 enums: exactly three regimes, and the transition is spelled "transitional".
    assert C.REGIMES == ("inelastic", "elastic", "transitional")
    assert C.CONTEXT_LAYERS == ("land_use", "infrastructure", "water", "admin_boundary")
    assert C.UNIT == "mm"
    assert C.VELOCITY_UNIT == "mm/year"
    assert C.CRS_OUTPUT == "EPSG:4326"


def test_every_product_has_a_profile_and_profiles_are_unique_per_sensor():
    assert set(C.SENSOR_PROFILES) == set(C.PRODUCTS)
    for product, profile in C.SENSOR_PROFILES.items():
        assert profile.product == product
        assert profile.sensor in C.SENSORS
        assert profile.persistence == "2of3"
        assert profile.min_area_m2 == 500


# ---------------------------------------------------------------------------------------------
# Contract 1.1 additions: declared dtype/unit/enum, required (not optional) so a 1.0-only
# generator fails closed instead of silently omitting the thesis metrics.
# ---------------------------------------------------------------------------------------------

V11_FIELD_DECLARATIONS = [
    pytest.param(
        C.GRID_FIELDS, "seasonal_amp_mm", "float64", C.UNIT, None,
        id="grid.seasonal_amp_mm",
    ),
    pytest.param(C.GRID_FIELDS, "ratio_R", "float64", "-", None, id="grid.ratio_R"),
    pytest.param(C.GRID_FIELDS, "regime", "string", None, C.REGIMES, id="grid.regime"),
    pytest.param(
        C.TIMESERIES_FIELDS, "trend_mm_yr", "float64", C.VELOCITY_UNIT, None,
        id="timeseries.trend_mm_yr",
    ),
    pytest.param(
        C.TIMESERIES_FIELDS, "seasonal_mm", "float64", C.UNIT, None,
        id="timeseries.seasonal_mm",
    ),
]


@pytest.mark.parametrize(("fields", "name", "dtype", "unit", "enum"), V11_FIELD_DECLARATIONS)
def test_contract_11_fields_are_required_with_the_frozen_dtype_unit_and_enum(
    fields, name, dtype, unit, enum
):
    declared = {field.name: field for field in fields}
    assert name in declared, f"contract 1.1 declares {name!r}"
    field = declared[name]
    assert field.required is True
    assert field.dtype == dtype
    assert field.unit == unit
    assert field.enum == enum


def test_contract_11_stats_keys_are_required():
    for key in ("median_seasonal_amp_mm", "median_ratio_R", "dominant_regime"):
        assert key in C.STATS_REQUIRED_KEYS


# ---------------------------------------------------------------------------------------------
# Positive path.
# ---------------------------------------------------------------------------------------------


def test_minimal_valid_artefact_set_has_no_problems(grid, timeseries, meta, context):
    report = C.validate_all(
        grid=grid, timeseries=timeseries, meta=meta, context=context, case_id="mexico-city"
    )
    assert report == {"grid": [], "timeseries": [], "meta": [], "context": []}


def test_validate_all_only_reports_provided_artefacts(meta):
    assert list(C.validate_all(meta=meta)) == ["meta"]


def test_extra_optional_field_acquisition_date_may_be_absent(grid):
    assert C.validate_grid(grid.drop(columns=["acquisition_date"])) == []


# ---------------------------------------------------------------------------------------------
# Grid negatives.
# ---------------------------------------------------------------------------------------------


def test_grid_delta_change_arithmetic(grid):
    problems = C.validate_grid(pd.DataFrame([grid_row(delta_change=1.0)]))
    assert any("delta_change != val_t2 - val_t1" in p for p in problems)


def test_grid_rejects_undeclared_column(grid):
    problems = C.validate_grid(grid.assign(temperature=21.0))
    assert any("undeclared column 'temperature'" in p for p in problems)


def test_grid_rejects_missing_required_column(grid):
    problems = C.validate_grid(grid.drop(columns=["velocity_mm_yr"]))
    assert any("missing required column 'velocity_mm_yr'" in p for p in problems)


def test_grid_rejects_null_in_required_numeric():
    problems = C.validate_grid(pd.DataFrame([grid_row(val_t1=float("nan"), val_t2=float("nan"))]))
    assert any("val_t1 has 1 null(s)" in p for p in problems)


def test_grid_rejects_coherence_out_of_range():
    problems = C.validate_grid(pd.DataFrame([grid_row(coherence=1.4)]))
    assert any("coherence outside [0, 1]" in p for p in problems)


def test_grid_rejects_duplicate_cell_id():
    frame = pd.DataFrame([grid_row(), grid_row(delta_change=-7.4)])
    assert any("cell_id is not unique" in p for p in C.validate_grid(frame))


def test_grid_rejects_mixed_case_ids():
    frame = pd.DataFrame([grid_row(), grid_row(cell_id="8928308280ffffe", case_id="jakarta")])
    assert any("mixed case_id values" in p for p in C.validate_grid(frame))


def test_grid_rejects_undeclared_case_id():
    problems = C.validate_grid(pd.DataFrame([grid_row(case_id="lima")]))
    assert any("undeclared case_id" in p for p in problems)


def test_grid_rejects_bad_provenance_id():
    problems = C.validate_grid(pd.DataFrame([grid_row(provenance_id="nope")]))
    assert any("does not match" in p for p in problems)


def test_grid_rejects_undeclared_enum_value():
    problems = C.validate_grid(pd.DataFrame([grid_row(land_use="farmland")]))
    assert any("land_use: undeclared value" in p for p in problems)


def test_grid_rejects_product_sensor_mismatch():
    problems = C.validate_grid(pd.DataFrame([grid_row(product="opera-disp-s1")]))
    assert any("is produced from sensor 'sentinel-1'" in p for p in problems)


@pytest.mark.parametrize("column", ["seasonal_amp_mm", "ratio_R", "regime"])
def test_grid_rejects_missing_contract_11_column(grid, column):
    """A 1.0-only generator omits these, so the omission itself must fail closed."""
    problems = C.validate_grid(grid.drop(columns=[column]))
    assert any(f"missing required column {column!r}" in p for p in problems)


def test_grid_rejects_negative_seasonal_amplitude():
    problems = C.validate_grid(pd.DataFrame([grid_row(seasonal_amp_mm=-9.4)]))
    assert any("seasonal_amp_mm must be non-negative" in p for p in problems)


def test_grid_rejects_negative_ratio():
    problems = C.validate_grid(pd.DataFrame([grid_row(ratio_R=-13.6)]))
    assert any("ratio_R must be non-negative" in p for p in problems)


def test_grid_rejects_undeclared_regime():
    problems = C.validate_grid(pd.DataFrame([grid_row(regime="recoverable")]))
    assert any("regime: undeclared value" in p for p in problems)


# ---------------------------------------------------------------------------------------------
# timeseries negatives.
# ---------------------------------------------------------------------------------------------


def test_timeseries_rejects_date_outside_meta(meta):
    frame = pd.DataFrame(
        [
            {
                "case_id": "mexico-city",
                "cell_id": "aoi",
                "date": "2026-08-01",
                "value": 0.0,
                "unit": "mm",
                "sensor": "nisar",
                "product": "nisar-gunw",
                "quality": "good",
                "provenance_id": PROV,
            }
        ]
    )
    problems = C.validate_timeseries(frame, dates=meta["dates"])
    assert any("not in meta.dates" in p for p in problems)


def test_timeseries_rejects_non_iso_date(timeseries):
    problems = C.validate_timeseries(timeseries.assign(date="02/07/2026"))
    assert any("ISO YYYY-MM-DD" in p for p in problems)


def test_timeseries_rejects_bad_quality(timeseries):
    problems = C.validate_timeseries(timeseries.assign(quality="excellent"))
    assert any("quality: undeclared value" in p for p in problems)


@pytest.mark.parametrize("column", ["trend_mm_yr", "seasonal_mm"])
def test_timeseries_rejects_missing_contract_11_column(timeseries, column):
    problems = C.validate_timeseries(timeseries.drop(columns=[column]))
    assert any(f"missing required column {column!r}" in p for p in problems)


# ---------------------------------------------------------------------------------------------
# meta.json negatives.
# ---------------------------------------------------------------------------------------------


def test_meta_rejects_pooled_threshold(meta):
    broken = {**meta, "thresholds": {**meta["thresholds"], "coherence_min": 0.5}}
    assert any("coherence_min" in p for p in C.validate_meta(broken))


def test_meta_rejects_revisit_mismatch(meta):
    assert any("revisit_days" in p for p in C.validate_meta({**meta, "revisit_days": 6}))


def test_meta_rejects_product_sensor_mismatch(meta):
    broken = {**meta, "sensor": "sentinel-1"}
    assert any("is produced from sensor 'nisar'" in p for p in C.validate_meta(broken))


def test_meta_rejects_missing_stats_key(meta):
    stats = {k: v for k, v in meta["stats"].items() if k != "n_cells"}
    assert any("stats missing 'n_cells'" in p for p in C.validate_meta({**meta, "stats": stats}))


@pytest.mark.parametrize("key", ["median_seasonal_amp_mm", "median_ratio_R", "dominant_regime"])
def test_meta_rejects_missing_contract_11_stat(meta, key):
    stats = {k: v for k, v in meta["stats"].items() if k != key}
    problems = C.validate_meta({**meta, "stats": stats})
    assert any(f"stats missing {key!r}" in p for p in problems)


def test_meta_rejects_undeclared_dominant_regime(meta):
    stats = {**meta["stats"], "dominant_regime": "recoverable"}
    problems = C.validate_meta({**meta, "stats": stats})
    assert any("dominant_regime" in p and "is not one of" in p for p in problems)


@pytest.mark.parametrize("key", ["median_seasonal_amp_mm", "median_ratio_R"])
def test_meta_rejects_negative_seasonal_stat(meta, key):
    stats = {**meta["stats"], key: -1.0}
    problems = C.validate_meta({**meta, "stats": stats})
    assert any(f"stats.{key} must be non-negative" in p for p in problems)


def test_meta_rejects_null_result_without_detection_limit(meta):
    stats = {**meta["stats"], "median_velocity_mm_yr": -1.2}
    problems = C.validate_meta({**meta, "stats": stats})
    assert any("null result" in p for p in problems)


def test_meta_accepts_null_result_with_detection_limit(meta):
    stats = {**meta["stats"], "median_velocity_mm_yr": -1.2, "detection_limit_mm_yr": 1.5}
    assert C.validate_meta({**meta, "stats": stats}) == []


def test_meta_rejects_unsorted_or_duplicated_dates(meta):
    unsorted = {**meta, "dates": ["2026-07-14", "2026-07-02"]}
    assert any("sorted ascending" in p for p in C.validate_meta(unsorted))
    duplicated = {**meta, "dates": ["2026-07-02", "2026-07-02"]}
    assert any("duplicates" in p for p in C.validate_meta(duplicated))


def test_meta_rejects_non_iso_date(meta):
    assert any("ISO YYYY-MM-DD" in p for p in C.validate_meta({**meta, "dates": ["07/02/2026"]}))


def test_meta_rejects_empty_limitations(meta):
    assert any("limitations" in p for p in C.validate_meta({**meta, "limitations": []}))


def test_meta_rejects_wrong_schema_version(meta):
    assert any("schema_version" in p for p in C.validate_meta({**meta, "schema_version": "2.0"}))


def test_meta_rejects_mock_without_mock_provenance(meta):
    broken = {
        **meta,
        "product": "mock",
        "provenance": {**meta["provenance"], "source": "ASF DAAC"},
    }
    assert any("does not say so" in p for p in C.validate_meta(broken))


def test_meta_accepts_mock_with_mock_provenance(meta):
    ok = {
        **meta,
        "product": "mock",
        "provenance": {**meta["provenance"], "source": "mock (synthetic stub)"},
    }
    assert C.validate_meta(ok) == []


def test_meta_rejects_bad_bbox_and_crs(meta):
    assert any("bbox" in p for p in C.validate_meta({**meta, "bbox": [-99.35, 19.20]}))
    assert any("bbox" in p for p in C.validate_meta({**meta, "bbox": [-98.0, 19.2, -99.0, 19.6]}))
    assert any("crs" in p for p in C.validate_meta({**meta, "crs": "EPSG:3857"}))


# ---------------------------------------------------------------------------------------------
# context_layers.geojson negatives.
# ---------------------------------------------------------------------------------------------


def _with_props(context, **props):
    feature = context["features"][0]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                **feature,
                "properties": {**feature["properties"], **props},
            }
        ],
    }


def test_context_accepts_valid_collection(context):
    assert C.validate_context_layers(context) == []


def test_context_rejects_undeclared_layer(context):
    problems = C.validate_context_layers(_with_props(context, layer="roads"))
    assert any("undeclared layer" in p for p in problems)


def test_context_rejects_class_outside_layer_vocabulary(context):
    problems = C.validate_context_layers(_with_props(context, **{"class": "reservoir"}))
    assert any("not in" in p for p in problems)


def test_context_rejects_wrong_crs(context):
    problems = C.validate_context_layers(_with_props(context, crs="EPSG:3857"))
    assert any("crs must be EPSG:4326" in p for p in problems)


def test_context_rejects_bad_provenance(context):
    problems = C.validate_context_layers(_with_props(context, provenance_id="42"))
    assert any("provenance_id" in p for p in problems)


def test_context_rejects_empty_or_non_collection(context):
    assert any("no features" in p for p in C.validate_context_layers({"type": "FeatureCollection"}))
    assert any("not a GeoJSON" in p for p in C.validate_context_layers({"type": "Feature"}))


# ---------------------------------------------------------------------------------------------
# Behaviour: risk bands, profiles, provenance ids, fingerprint.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("velocity", "expected"),
    [
        (-420.0, "critico"),
        (420.0, "critico"),
        (-1.2, "estable"),
        (0.0, "estable"),
        (4.99, "estable"),
        (5.0, "moderado"),
        (-24.99, "moderado"),
        (-25.0, "critico"),
        (math.nan, "estable"),
        (math.inf, "estable"),
    ],
)
def test_risk_level_bands(velocity, expected):
    assert C.risk_level_from_velocity(velocity) == expected


@pytest.mark.parametrize(
    ("product", "revisit"),
    [("nisar-gunw", 12), ("nisar-gcov", 12), ("hyp3-s1", 6), ("egms", 6), ("mock", 12)],
)
def test_sensor_profile_revisit_days(product, revisit):
    profile = C.sensor_profile(C.SENSOR_PROFILES[product].sensor, product)
    assert profile.revisit_days == revisit


def test_sensor_profile_rejects_mismatch_and_unknowns():
    with pytest.raises(C.ContractError):
        C.sensor_profile("sentinel-1", "nisar-gunw")
    with pytest.raises(C.ContractError):
        C.sensor_profile("nisar", "sentinel-3")
    with pytest.raises(C.ContractError):
        C.sensor_profile("landsat", "nisar-gunw")


def test_provenance_id_accepts_date_and_string():
    from_date = C.provenance_id("jakarta", "nisar-gunw", dt.date(2026, 11, 14), "deadbeef1234")
    from_str = C.provenance_id("jakarta", "nisar-gunw", "2026-11-14", "deadbeef1234")
    assert from_date == from_str == "jakarta+nisar-gunw+20261114+deadbeef"
    assert C.PROVENANCE_ID_RE.match(from_date)


def test_fingerprint_is_stable_and_sensitive(monkeypatch):
    first = C.contract_fingerprint()
    assert first == C.contract_fingerprint()

    original = C.SENSOR_PROFILES["nisar-gunw"]
    monkeypatch.setitem(
        C.SENSOR_PROFILES, "nisar-gunw", dataclasses.replace(original, revisit_days=99)
    )
    assert C.contract_fingerprint() != first
