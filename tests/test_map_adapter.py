"""Tests for `src.domain.map_adapter` shared/independent scales, 3D heights and JSON safety."""

from __future__ import annotations

import json
import pathlib

from src.data import load_case_bundle
from src.domain.filters import FilterCriteria
from src.domain.map_adapter import (
    GLOBAL_VELOCITY_RANGE_MM_YR,
    RISK_COLORS_RGB,
    build_map_payload,
    build_pydeck_layer_specs,
)
from tests.conftest import write_synthetic_case


def test_shared_scale_renders_malaga_flat_grey_and_cdmx_extruded(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "mexico-city")
    write_synthetic_case(tmp_path, "malaga")

    cdmx = load_case_bundle("mexico-city", processed_dir=tmp_path)
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    p_cdmx = build_map_payload(
        cdmx, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=True
    )
    p_malaga = build_map_payload(
        malaga, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=True
    )

    assert p_cdmx.legend.domain_min == GLOBAL_VELOCITY_RANGE_MM_YR[0]
    assert p_malaga.legend.domain_min == GLOBAL_VELOCITY_RANGE_MM_YR[0]
    assert p_malaga.legend.detection_limit_mm_yr == 1.5

    # Every Málaga cell has |v| < 5 mm/yr -> neutral slate RGB (148, 163, 184) and tiny 3D height
    neutral_rgb = list(RISK_COLORS_RGB["estable"])
    for rec in p_malaga.grid_records:
        assert rec["fill_color"][:3] == neutral_rgb
        assert rec["elevation_m"] < 25.0

    # Peak CDMX cell (-380 mm/yr) is strongly coloured and extruded > 2000 m
    peak_cdmx = min(p_cdmx.grid_records, key=lambda r: r["velocity_mm_yr"])
    assert peak_cdmx["fill_color"][:3] != neutral_rgb
    assert peak_cdmx["elevation_m"] > 2000.0
    assert p_cdmx.view_state.pitch == 45.0


def test_independent_scale_and_h3_polygon_lonlat_orientation(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "malaga")
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)
    target_cell = str(malaga.grid.iloc[0]["cell_id"])

    payload = build_map_payload(
        malaga,
        FilterCriteria(coherence_min=0.0, selected_cell_id=target_cell),
        scale_mode="independent",
        extrude_3d=False,
    )
    assert payload.legend.scale_mode == "independent"
    assert payload.legend.neutral_band_mm_yr == (-1.5, 1.5)
    assert payload.view_state.pitch == 0.0

    selected = [r for r in payload.grid_records if r["is_selected"]]
    assert len(selected) == 1
    assert selected[0]["line_color"] == [255, 255, 255, 255]

    # Verify GeoJSON [lon, lat] order (Málaga lon ~ -4.4, lat ~ +36.7) and closed ring
    poly = selected[0]["polygon"]
    assert poly[0] == poly[-1]
    lon0, lat0 = poly[0]
    assert -5.0 < lon0 < -4.0
    assert 36.0 < lat0 < 37.5

    # Must serialise cleanly to JSON for PyDeck without numpy scalar TypeErrors
    spec = build_pydeck_layer_specs(payload)
    encoded = json.dumps(spec)
    assert "PolygonLayer" in encoded
    assert "GeoJsonLayer" in encoded


def test_all_map_metrics_and_empty_grid_are_supported(tmp_path: pathlib.Path):
    write_synthetic_case(tmp_path, "jakarta")
    jakarta = load_case_bundle("jakarta", processed_dir=tmp_path)

    for metric in ("velocity_mm_yr", "delta_change", "coherence", "risk_level"):
        payload = build_map_payload(jakarta, FilterCriteria(coherence_min=0.0), metric=metric)  # type: ignore[arg-type]
        assert not payload.is_empty
        assert payload.legend.metric == metric

    empty_payload = build_map_payload(
        jakarta, FilterCriteria(min_abs_velocity_mm_yr=9999.0)
    )
    assert empty_payload.is_empty is True
    spec = build_pydeck_layer_specs(empty_payload)
    assert len(spec["layers"]) == 1  # context layer only, no crash


def test_real_pydeck_deck_serialises_all_layer_modes(tmp_path: pathlib.Path):
    import pydeck as pdk

    write_synthetic_case(tmp_path, "mexico-city")
    cdmx = load_case_bundle("mexico-city", processed_dir=tmp_path)
    payload = build_map_payload(
        cdmx, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=True
    )

    for use_poly in (True, False):
        spec = build_pydeck_layer_specs(payload, use_polygon_layer=use_poly, show_context=True)
        layers = [pdk.Layer(item["type"], **item["kwargs"]) for item in spec["layers"]]
        deck = pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(**spec["view_state"]),
            tooltip=spec["tooltip"],
        )
        deck_json = json.loads(deck.to_json())
        layer_types = [lyr["@@type"] for lyr in deck_json["layers"]]
        assert "GeoJsonLayer" in layer_types
        expected_cell_layer = "PolygonLayer" if use_poly else "H3HexagonLayer"
        assert expected_cell_layer in layer_types

