"""Parity guard for O-07 (`docs/decisions.md`): signed `height` vs visual 3D subsidence extrusion.

Verifies two invariants across the integrated backend + UI bridge:
1. `height` is bit-for-bit identical to stored signed `delta_change` [mm] across all published
   cities (never `abs(delta_change)`, never clipped, never zeroed in flat mode).
2. Purely visual 3D extrusion (`extrude_m` / `elevation_m`) extrudes downward subsidence
   (`delta_change < 0`) only, so equal-magnitude uplift (`+50 mm`) and subsidence (`-50 mm`)
   NEVER rise identically and maintain opposite physical signs and diverging `RdBu` colours.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest

from src.data import loaders
from src.data.loaders import load_all_available_bundles, load_case_bundle
from src.domain.filters import FilterCriteria
from src.domain.map_adapter import build_map_payload
from tests.conftest import write_synthetic_case


@pytest.mark.parametrize("case_id", ("mexico-city", "jakarta", "malaga"))
def test_shipped_cases_preserve_exact_signed_height_parity(case_id: str):
    bundles = load_all_available_bundles()
    bundle = bundles[case_id]

    for extrude in (False, True):
        df = loaders.get_map_layer_df(
            case_id,
            {"case_id": case_id, "coherence_min": 0.0, "extrude_3d": extrude},
        )
        assert len(df) == len(bundle.grid)
        np.testing.assert_array_equal(
            df["height"].to_numpy(dtype=np.float64),
            bundle.grid["delta_change"].to_numpy(dtype=np.float64),
        )
        if not extrude:
            assert (df["extrude_m"] == 0.0).all()


def test_equal_magnitude_uplift_and_subsidence_do_not_extrude_identically(tmp_path: pathlib.Path):
    case_dir = write_synthetic_case(tmp_path, "mexico-city")
    grid = pd.read_parquet(case_dir / "nisar_grid.parquet")

    # Set cell 0 to -50 mm subsidence and cell 1 to +50 mm uplift with equal magnitude
    grid.loc[0, "val_t1"] = 0.0
    grid.loc[0, "val_t2"] = -50.0
    grid.loc[0, "delta_change"] = -50.0
    grid.loc[0, "velocity_mm_yr"] = -140.0

    grid.loc[1, "val_t1"] = 0.0
    grid.loc[1, "val_t2"] = 50.0
    grid.loc[1, "delta_change"] = 50.0
    grid.loc[1, "velocity_mm_yr"] = 140.0
    grid.to_parquet(case_dir / "nisar_grid.parquet", index=False)

    bundle = load_case_bundle("mexico-city", processed_dir=tmp_path)
    payload = build_map_payload(
        bundle, FilterCriteria(coherence_min=0.0), scale_mode="shared", extrude_3d=True
    )
    sub_rec = payload.grid_records[0]
    up_rec = payload.grid_records[1]

    # Signed physical parity preserved
    assert sub_rec["height"] == -50.0
    assert up_rec["height"] == +50.0
    assert sub_rec["motion_direction"] == "subsidence"
    assert up_rec["motion_direction"] == "uplift"

    # Visual 3D column rises ONLY for subsidence, never identically for uplift
    assert sub_rec["elevation_m"] > 0.0
    assert up_rec["elevation_m"] == 0.0

    # Diverging RdBu palette separates red subsidence (R > B) from blue uplift (B > R)
    assert sub_rec["fill_color"][0] > sub_rec["fill_color"][2]
    assert up_rec["fill_color"][2] > up_rec["fill_color"][0]
