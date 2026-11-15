"""Parity guard for the unified O-07 decision (`COLUMN_BASELINE_M`, `docs/requests.md`).

Resolves the two-branch O-07 split in favour of `COLUMN_BASELINE_M = MAX_EXTRUDE_METERS / 2`:
1. `signed_delta_mm` is bit-for-bit identical to stored signed `delta_change` [mm] in both flat
   and 3-D modes across all three published cities, and `motion_direction` records the physical
   direction (`subsidence` | `uplift` | `zero`) from the unrounded value.
2. When `extrude_3d=True`, PyDeck `height` (`elevation_m`) maps signed `delta_change` two-sided
   around `COLUMN_BASELINE_M` (`1500 m`): subsiding cells sit strictly below `1500 m`, uplift cells
   sit strictly above `1500 m`, and symmetric `±50 mm` displacements never produce identical
   column heights.
3. When `extrude_3d=False` (default), `height == 0.0` everywhere to prevent hex z-fighting while
   `signed_delta_mm` and `delta_change` retain their sign.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from pipeline.science.transforms import classify_regime
from src.data import loaders
from src.data.loaders import load_all_available_bundles, load_case_bundle
from src.domain import contracts as C
from src.domain.filters import FilterCriteria
from src.domain.map_adapter import COLUMN_BASELINE_M, build_map_payload
from tests.conftest import write_synthetic_case


@pytest.mark.parametrize("case_id", ("mexico-city", "jakarta", "malaga"))
def test_shipped_cases_preserve_signed_delta_and_two_sided_baseline(case_id: str) -> None:
    bundles = load_all_available_bundles()
    bundle = bundles[case_id]
    raw_delta = bundle.grid["delta_change"].to_numpy(dtype=np.float64)

    df_flat = loaders.get_map_layer_df(
        case_id, {"case_id": case_id, "coherence_min": 0.0, "extrude_3d": False}
    )
    assert len(df_flat) == len(bundle.grid)
    np.testing.assert_array_equal(df_flat["signed_delta_mm"].to_numpy(dtype=np.float64), raw_delta)
    assert (df_flat["height"] == 0.0).all()

    df_3d = loaders.get_map_layer_df(
        case_id, {"case_id": case_id, "coherence_min": 0.0, "extrude_3d": True}
    )
    np.testing.assert_array_equal(df_3d["signed_delta_mm"].to_numpy(dtype=np.float64), raw_delta)
    heights = df_3d["height"].to_numpy(dtype=np.float64)
    sub_mask = raw_delta < -0.05
    up_mask = raw_delta > 0.05
    if sub_mask.any():
        assert (heights[sub_mask] < COLUMN_BASELINE_M).all()
        assert (df_3d.loc[sub_mask, "motion_direction"] == "subsidence").all()
    if up_mask.any():
        assert (heights[up_mask] > COLUMN_BASELINE_M).all()
        assert (df_3d.loc[up_mask, "motion_direction"] == "uplift").all()


def test_symmetric_opposite_displacements_straddle_column_baseline(tmp_path: pathlib.Path) -> None:
    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    grid = pd.read_parquet(case_dir / "nisar_grid.parquet")

    amp0 = float(grid.loc[0, "seasonal_amp_mm"])
    amp1 = float(grid.loc[1, "seasonal_amp_mm"])
    grid.loc[0, "val_t1"] = 0.0
    grid.loc[0, "val_t2"] = -50.0
    grid.loc[0, "delta_change"] = -50.0
    grid.loc[0, "velocity_mm_yr"] = -140.0
    grid.loc[0, "ratio_R"] = 140.0 / amp0
    grid.loc[0, "risk_level"] = C.risk_level_from_velocity(-140.0)
    grid.loc[0, "regime"] = classify_regime(-140.0, amp0)

    grid.loc[1, "val_t1"] = 0.0
    grid.loc[1, "val_t2"] = 50.0
    grid.loc[1, "delta_change"] = 50.0
    grid.loc[1, "velocity_mm_yr"] = 140.0
    grid.loc[1, "ratio_R"] = 140.0 / amp1
    grid.loc[1, "risk_level"] = C.risk_level_from_velocity(140.0)
    grid.loc[1, "regime"] = classify_regime(140.0, amp1)
    grid.to_parquet(case_dir / "nisar_grid.parquet", index=False)

    meta_path = case_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    vels = grid["velocity_mm_yr"].to_numpy(dtype=np.float64)
    meta["stats"]["median_velocity_mm_yr"] = round(float(np.median(vels)), 6)
    meta["stats"]["p05_velocity_mm_yr"] = round(float(np.percentile(vels, 5)), 6)
    meta["stats"]["p95_velocity_mm_yr"] = round(float(np.percentile(vels, 95)), 6)
    meta["stats"]["median_ratio_R"] = round(float(grid["ratio_R"].median()), 6)
    counts = grid["regime"].astype(str).value_counts()
    meta["stats"]["dominant_regime"] = sorted(
        counts.items(), key=lambda item: (-int(item[1]), str(item[0]))
    )[0][0]
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    bundle = load_case_bundle("mexico-city", processed_dir=tmp_path)
    payload = build_map_payload(
        bundle, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=True
    )
    sub_rec = payload.grid_records[0]
    up_rec = payload.grid_records[1]

    assert sub_rec["motion_direction"] == "subsidence"
    assert up_rec["motion_direction"] == "uplift"
    assert sub_rec["elevation_m"] < COLUMN_BASELINE_M < up_rec["elevation_m"]
    assert pytest.approx(COLUMN_BASELINE_M - sub_rec["elevation_m"], abs=0.02) == (
        up_rec["elevation_m"] - COLUMN_BASELINE_M
    )
    assert sub_rec["fill_color"][0] > sub_rec["fill_color"][2]
    assert up_rec["fill_color"][2] > up_rec["fill_color"][0]
