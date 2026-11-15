"""Tests for `src.domain.map_adapter` shared/independent scales, 3D heights and JSON safety."""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from src.data import load_case_bundle
from src.domain import contracts as C
from src.domain.filters import FilterCriteria
from src.domain.map_adapter import (
    COLUMN_BASELINE_M,
    GLOBAL_VELOCITY_RANGE_MM_YR,
    MAX_EXTRUDE_METERS,
    RISK_COLORS_RGB,
    build_map_payload,
    build_pydeck_layer_specs,
    column_elevation_from_delta,
    prepare_context_for_map,
)
from tests.conftest import classify_regime, write_synthetic_case


def _case_with_signed_deltas(
    tmp_path: pathlib.Path,
    deltas_mm: tuple[float, ...] = (-9.0, 0.0, 9.0),
) -> pathlib.Path:
    """Synthetic Málaga case whose leading cells carry prescribed *signed* `delta_change` values.

    `write_synthetic_case` emits a strictly negative-to-positive ramp that never contains an exact
    zero, so the signed-height tests need one hermetic fixture with a known -x / 0 / +x triple.
    Only value columns change; row counts (the sole cross-artefact check) stay identical.

    Contract 1.1 ties three columns to `velocity_mm_yr`, so rewriting it obliges this helper to
    rewrite them too or the bundle fails closed on load: `ratio_R == |velocity| / amplitude`
    (`src/data/validation.py::_seasonal_column_problems`), `regime` by the pipeline's own rule,
    and `stats.median_ratio_R`, which must stay the grid's median.
    """
    write_synthetic_case(tmp_path, "malaga", n_rings=1)
    grid_path = tmp_path / "malaga" / "nisar_grid.parquet"
    grid = pd.read_parquet(grid_path)
    assert len(grid) >= len(deltas_mm)
    for idx, delta in enumerate(deltas_mm):
        amplitude_mm = float(grid.loc[idx, "seasonal_amp_mm"])
        grid.loc[idx, "val_t1"] = 0.0
        grid.loc[idx, "val_t2"] = float(delta)
        grid.loc[idx, "delta_change"] = float(delta)
        grid.loc[idx, "velocity_mm_yr"] = float(delta)
        grid.loc[idx, "ratio_R"] = abs(float(delta)) / amplitude_mm
        grid.loc[idx, "regime"] = classify_regime(float(delta), amplitude_mm)
        grid.loc[idx, "risk_level"] = C.risk_level_from_velocity(float(delta))
    grid.to_parquet(grid_path, index=False)

    meta_path = tmp_path / "malaga" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["stats"]["median_ratio_R"] = round(float(grid["ratio_R"].median()), 6)
    regime_counts = grid["regime"].astype(str).value_counts()
    # Same estimator and tie-break as `src/data/validation.py::_seasonal_stat_problems`.
    meta["stats"]["dominant_regime"] = sorted(
        regime_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))
    )[0][0]
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return grid_path


def _elevation_by_delta(payload) -> dict[float, float]:
    return {round(float(r["delta_change"]), 6): float(r["elevation_m"]) for r in payload.grid_records}


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

    # Every Málaga cell has |v| < 5 mm/yr -> neutral slate RGB (148, 163, 184) and, on the shared
    # scale, only a tiny signed offset from the zero-displacement column baseline (O-07).
    neutral_rgb = list(RISK_COLORS_RGB["estable"])
    for rec in p_malaga.grid_records:
        assert rec["fill_color"][:3] == neutral_rgb
        assert abs(rec["elevation_m"] - COLUMN_BASELINE_M) < 25.0

    # Peak CDMX cell (-380 mm/yr) is strongly coloured; as subsidence its column now renders
    # *below* the baseline by nearly the full half-span (the sign is preserved, not the |delta|).
    peak_cdmx = min(p_cdmx.grid_records, key=lambda r: r["velocity_mm_yr"])
    assert peak_cdmx["fill_color"][:3] != neutral_rgb
    assert peak_cdmx["elevation_m"] < COLUMN_BASELINE_M
    assert COLUMN_BASELINE_M - peak_cdmx["elevation_m"] > 1000.0
    assert peak_cdmx["elevation_m"] >= 0.0
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


def test_column_height_preserves_displacement_sign_around_zero_baseline(tmp_path: pathlib.Path):
    """O-07 parity: subsidence renders BELOW a zero-delta cell, uplift strictly ABOVE it."""
    _case_with_signed_deltas(tmp_path, (-9.0, 0.0, 9.0))
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    payload = build_map_payload(
        malaga, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=True
    )
    elev = _elevation_by_delta(payload)
    zero, sinking, rising = elev[0.0], elev[-9.0], elev[9.0]

    assert sinking < zero < rising
    assert zero == COLUMN_BASELINE_M == MAX_EXTRUDE_METERS / 2.0
    # Height binds to the SIGNED value, never to |delta_change|: the old bug made these equal.
    assert sinking != rising
    assert all(0.0 <= e <= MAX_EXTRUDE_METERS for e in elev.values())


def test_symmetric_delta_pair_maps_to_equal_magnitude_opposite_sides(tmp_path: pathlib.Path):
    """A (+x, -x) pair keeps equal magnitude on opposite sides of the baseline."""
    _case_with_signed_deltas(tmp_path, (-9.0, 0.0, 9.0))
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    payload = build_map_payload(
        malaga, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=True
    )
    elev = _elevation_by_delta(payload)
    zero, sinking, rising = elev[0.0], elev[-9.0], elev[9.0]

    assert (rising - zero) == pytest.approx(zero - sinking, abs=0.02)
    assert (rising + sinking) == pytest.approx(MAX_EXTRUDE_METERS, abs=0.05)


@pytest.mark.parametrize("scale_mode", ["shared", "independent"])
def test_shared_and_independent_scales_preserve_signed_height_ordering(
    tmp_path: pathlib.Path, scale_mode: str
):
    """Both scale modes keep elev(-x) < elev(0) < elev(+x) and monotonic magnitude in |delta|."""
    _case_with_signed_deltas(tmp_path, (-9.0, -4.5, 0.0, 4.5, 9.0))
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    payload = build_map_payload(
        malaga,
        FilterCriteria(coherence_min=0.0),
        scale_mode=scale_mode,  # type: ignore[arg-type]
        extrude_3d=True,
    )
    ordered = sorted(payload.grid_records, key=lambda r: float(r["delta_change"]))
    elevs = [float(r["elevation_m"]) for r in ordered]
    deltas = [float(r["delta_change"]) for r in ordered]

    # Strictly increasing in signed delta => sign ordering holds for every pair of cells.
    assert all(b > a for a, b in zip(elevs, elevs[1:]))

    zero_elev = next(
        float(r["elevation_m"]) for r in payload.grid_records if float(r["delta_change"]) == 0.0
    )
    assert zero_elev == COLUMN_BASELINE_M

    # Magnitudes monotonic in |delta|, separately for each sign.
    for sign in (-1.0, 1.0):
        side = sorted(
            (
                (abs(float(r["delta_change"])), abs(float(r["elevation_m"]) - COLUMN_BASELINE_M))
                for r in ordered
                if (float(r["delta_change"]) > 0) == (sign > 0) and float(r["delta_change"]) != 0.0
            )
        )
        magnitudes = [mag for _, mag in side]
        abs_deltas = [ad for ad, _ in side]
        assert abs_deltas == sorted(abs_deltas)
        assert all(b > a for a, b in zip(magnitudes, magnitudes[1:]))
        assert all(m <= MAX_EXTRUDE_METERS / 2.0 + 1e-9 for m in magnitudes)
    assert deltas[0] < 0.0 < deltas[-1]


def test_column_elevation_formula_is_monotonic_and_bounded():
    """Direct formula parity: strict ordering and 1:1 magnitude over the whole normalised range."""
    fractions = np.linspace(-1.0, 1.0, 41)
    elevs = column_elevation_from_delta(fractions, 1.0)

    assert elevs[20] == pytest.approx(COLUMN_BASELINE_M)
    assert all(b > a for a, b in zip(elevs, elevs[1:]))
    assert float(elevs[0]) == pytest.approx(0.0, abs=1e-9)
    assert float(elevs[-1]) == pytest.approx(MAX_EXTRUDE_METERS, abs=1e-9)
    # Symmetric around the baseline, and clamped (no runaway columns beyond the ceiling).
    assert elevs + elevs[::-1] == pytest.approx(np.full(fractions.shape, MAX_EXTRUDE_METERS))
    clamped = column_elevation_from_delta(np.array([-99.0, 99.0]), 1.0)
    assert clamped.tolist() == pytest.approx([0.0, MAX_EXTRUDE_METERS])


def test_flat_mode_keeps_elevation_at_zero(tmp_path: pathlib.Path):
    """Flat (non-extruded) mode must keep zeroing elevation exactly as before O-07."""
    _case_with_signed_deltas(tmp_path, (-9.0, 0.0, 9.0))
    malaga = load_case_bundle("malaga", processed_dir=tmp_path)

    payload = build_map_payload(
        malaga, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=False
    )
    assert payload.extrude_3d is False
    assert all(float(r["elevation_m"]) == 0.0 for r in payload.grid_records)
    assert payload.grid_frame["elevation_m"].abs().max() == 0.0

    spec = build_pydeck_layer_specs(payload)
    cell_layer = next(
        lyr for lyr in spec["layers"] if lyr["type"] in ("PolygonLayer", "H3HexagonLayer")
    )
    assert cell_layer["kwargs"]["get_elevation"] == "elevation_m"


def test_context_styling_never_mutates_the_cached_feature_collection() -> None:
    """Styling copies `properties` only; `geometry` is shared read-only, and nothing is written back.

    A case can now carry ~700 OSM features instead of 12, and deep-copying each feature's
    coordinates on every rerun was measured at roughly 130 ms of a one-second rerun. Sharing the
    geometry is only safe because nothing in this path writes to it, so that is what this pins: the
    input collection is unchanged, the style properties are a copy, and mutating what we hand back
    cannot reach a caller's cached bundle.
    """
    context = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                "properties": {"layer": "water", "class": "river", "name": "Río"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [3.0, 4.0]},
                "properties": {"class": "bare"},
            },
        ],
    }
    original_properties = dict(context["features"][0]["properties"])
    before = json.dumps(context, sort_keys=True)

    styled = prepare_context_for_map(context)

    assert json.dumps(context, sort_keys=True) == before, "the input collection was mutated"
    first = styled["features"][0]
    assert first["properties"]["fill_color"], "style properties were not added to the copy"
    assert first["properties"]["tooltip_title"] == "Río"
    assert context["features"][0]["properties"] == original_properties, "properties leaked back"
    assert first["geometry"] is context["features"][0]["geometry"], "geometry should be shared"

    first["properties"]["fill_color"].append(999)
    assert json.dumps(context, sort_keys=True) == before, "mutating the output reached the input"
