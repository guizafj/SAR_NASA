"""Hermetic tests for the product-and-budget guard in `pipeline/ingest/02_search_download.py`.

No network, no ASF calls, no real granules: every test uses a synthetic availability report
under `tmp_path` and synthetic granule ids shaped like the real ones.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # running pytest from another cwd
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "search_download_guard", ROOT / "pipeline" / "ingest" / "02_search_download.py"
)
assert _SPEC is not None and _SPEC.loader is not None
M = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = M
_SPEC.loader.exec_module(M)

GUNW_ID = (
    "NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH_"
    "20261008T011329_20261008T011403_20261020T011329_20261020T011403_P05023_N_F_J_001"
)
L0_RRSD_ID = (
    "NISAR_L0_PR_RRSD_035_113_D_156S_"
    "20261113T010735_20261113T01425_P05036_F_J_001"
)


def _product_id(product: str, level: str = "L2", track: str = "079") -> str:
    return (
        f"NISAR_{level}_PR_{product}_032_113_D_{track}_033_4000_SH_"
        "20261008T011329_20261008T011403_20261020T011329_20261020T011403_"
        "P05023_N_F_J_001"
    )


def _sample(granule_id: str, size_mb: float | None = None) -> dict:
    return {
        "id": granule_id,
        "url": f"https://example.invalid/{granule_id}/{granule_id}.h5",
        "size_mb": size_mb,
        "start": "2026-10-08T01:13:29Z",
        "stop": "2026-10-20T01:14:03Z",
    }


def _write_availability(
    tmp_path: pathlib.Path,
    monkeypatch,
    samples: list[dict],
    city: str = "test-city",
    source: str = "nisar_gunw",
) -> pathlib.Path:
    aois = {
        "cities": {
            city: {
                "name": "Test City",
                "bbox": [-1.0, 0.0, 1.0, 2.0],
                "crs": "EPSG:32630",
                "eu_member_state": False,
            }
        }
    }
    aois_path = tmp_path / "aois.json"
    aois_path.write_text(json.dumps(aois))
    monkeypatch.setattr(M, "AOIS", aois_path)

    availability = tmp_path / "availability"
    availability.mkdir()
    report = {
        "contract_version": "1.0",
        "city": city,
        "name": "Test City",
        "aoi_bbox": [-1.0, 0.0, 1.0, 2.0],
        "crs": "EPSG:32630",
        "sources": {
            source: {
                "attempted": True,
                "available": True,
                "n_granules": len(samples),
                "sample_granules": samples,
                "date_range": ["2026-10-08T01:13:29Z", "2026-10-20T01:14:03Z"],
                "total_gb": None,
            }
        },
        "chosen_source": "NISAR_L2_GUNW",
    }
    (availability / f"{city}.json").write_text(json.dumps(report))
    return availability


def _download_argv(
    availability: pathlib.Path,
    raw: pathlib.Path,
    *extra: str,
    city: str = "test-city",
    source: str = "nisar_gunw",
) -> list[str]:
    return [
        "download",
        "--city",
        city,
        "--source",
        source,
        "--availability",
        str(availability),
        "--raw",
        str(raw),
        *extra,
    ]


# ---------------------------------------------------------------------------------------------
# Product identity
# ---------------------------------------------------------------------------------------------


def test_level0_rrsd_is_rejected_and_explains_focusing() -> None:
    decision = M.evaluate_granule(L0_RRSD_ID, "nisar_gunw", [])

    assert decision.accepted is False
    assert "Level 0" in decision.reason
    assert "focus" in decision.reason.lower()
    assert "100" in decision.reason  # the ~100+ GB warning
    assert decision.parsed.level == "L0"
    assert decision.parsed.product == "RRSD"


def test_real_gunw_id_is_accepted_for_nisar_gunw_and_parses_fields() -> None:
    decision = M.evaluate_granule(GUNW_ID, "nisar_gunw", [])
    parsed = decision.parsed

    assert decision.accepted is True
    assert parsed.level == "L2"
    assert parsed.product == "GUNW"
    assert parsed.track == "079"
    assert parsed.direction == "DESCENDING"
    assert parsed.flight_direction == "DESCENDING"
    assert parsed.direction_code == "D"
    assert parsed.reference_start == "2026-10-08T01:13:29Z"
    assert parsed.reference_stop == "2026-10-08T01:14:03Z"
    assert parsed.secondary_start == "2026-10-20T01:13:29Z"
    assert parsed.secondary_stop == "2026-10-20T01:14:03Z"

    wrong_source = M.evaluate_granule(GUNW_ID, "sentinel1_slc", [])
    assert wrong_source.accepted is False
    assert "nisar_gunw" in wrong_source.reason


def test_level1_radar_products_are_rejected_with_explanation() -> None:
    for product in ("RSLC", "RIFG", "RUNW"):
        decision = M.evaluate_granule(_product_id(product, level="L1"), "nisar_gunw", [])
        assert decision.accepted is False, product
        assert "Level 1" in decision.reason, product
        assert "radar-coordinate" in decision.reason.lower(), product


def test_gslc_and_gcov_require_explicit_opt_in_and_gcov_warns_no_phase() -> None:
    for product, warning_fragment in (("GSLC", "interferometric"), ("GCOV", "no phase")):
        granule_id = _product_id(product)

        without = M.evaluate_granule(granule_id, "nisar_gunw", [])
        assert without.accepted is False, product
        assert f"--allow-product {product}" in without.reason, product

        with_opt_in = M.evaluate_granule(granule_id, "nisar_gunw", [product])
        assert with_opt_in.accepted is True, product
        assert with_opt_in.warning is not None, product
        assert warning_fragment in with_opt_in.warning.lower(), product
        assert with_opt_in.parsed.product == product


def test_unrecognised_name_is_rejected_and_shows_what_it_parsed() -> None:
    decision = M.evaluate_granule(_product_id("FOO", level="L3"), "nisar_gunw", [])

    assert decision.accepted is False
    assert "fail closed" in decision.reason
    assert "level=L3" in decision.reason
    assert "product=FOO" in decision.reason
    assert "grammar=no table entry matched" in decision.reason


def test_sentinel1_names_remain_usable_for_the_sentinel1_source() -> None:
    s1 = "S1A_IW_SLC__1SDV_20260623T111515_20260623T111542_065095_083488_D338"
    decision = M.evaluate_granule(s1, "sentinel1_slc", [])

    assert decision.accepted is True
    assert decision.parsed.level == "S1"
    assert decision.parsed.product == "SLC"
    assert M.evaluate_granule(s1, "nisar_gunw", []).accepted is False


# ---------------------------------------------------------------------------------------------
# Partial-download hygiene and budget
# ---------------------------------------------------------------------------------------------


def test_crdownload_sibling_blocks_download_with_actionable_message(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    samples = [_sample(GUNW_ID, size_mb=1)]
    availability = _write_availability(tmp_path, monkeypatch, samples)
    raw = tmp_path / "raw"
    dest = raw / "test-city" / "nisar_gunw" / f"{GUNW_ID}.h5"
    dest.parent.mkdir(parents=True)
    chrome_partial = dest.with_suffix(dest.suffix + ".crdownload")
    chrome_partial.write_bytes(b"browser bytes")

    rc = M.main(_download_argv(availability, raw))

    captured = capsys.readouterr()
    assert rc == M.RC_PARTIAL_GUARD
    assert rc != 0
    assert ".crdownload" in captured.err
    assert "browser" in captured.err.lower()
    assert "Cancel" in captured.err
    assert not dest.exists()
    assert not (dest.parent / "provenance.json").exists()


def test_our_own_part_file_stays_resumable(tmp_path: pathlib.Path) -> None:
    raw_dir = tmp_path / "raw"
    dest = raw_dir / f"{GUNW_ID}.h5"
    raw_dir.mkdir()
    ours = dest.with_suffix(dest.suffix + ".part")
    ours.write_bytes(b"partial from this script")

    assert M.find_foreign_partials(raw_dir, [dest]) == []


def test_dry_run_prints_plan_writes_nothing_and_exits_zero(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    samples = [_sample(GUNW_ID)]
    availability = _write_availability(tmp_path, monkeypatch, samples)
    raw = tmp_path / "raw"

    rc = M.main(_download_argv(availability, raw, "--dry-run"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "Download plan for test-city / nisar_gunw" in out
    assert GUNW_ID in out
    assert "level=L2 product=GUNW track=079 direction=DESCENDING" in out
    assert "reference=2026-10-08T01:13:29Z secondary=2026-10-20T01:13:29Z" in out
    assert "status: ACCEPT" in out
    assert "cumulative" in out
    assert "dry run" in out.lower()
    assert not raw.exists()
    assert not (tmp_path / "provenance.json").exists()


def test_over_budget_plan_exits_nonzero_before_any_transfer(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    # Single geometry on purpose: a mixed-track plan is refused earlier by the geometry guard
    # (RC_PRODUCT_GUARD), which would mask the budget path this test is about. Mixed-geometry
    # refusal is covered in tests/test_download_stack.py.
    samples = [
        _sample(_product_id("GUNW", track="079"), size_mb=800),
        _sample(_product_id("GUNW", track="079"), size_mb=800),
        _sample(_product_id("GUNW", track="079"), size_mb=800),
    ]
    availability = _write_availability(tmp_path, monkeypatch, samples)
    raw = tmp_path / "raw"

    rc = M.main(_download_argv(availability, raw, "--max-gb", "2"))

    captured = capsys.readouterr()
    assert rc == M.RC_BUDGET_GUARD
    assert rc != 0
    assert "2.40 GB" in captured.out  # cumulative plan arithmetic
    assert "exceeds" in captured.err.lower()
    assert "2" in captured.err
    assert not raw.exists()


# ---------------------------------------------------------------------------------------------
# Mixed-track parsing and unchanged search/report behaviour
# ---------------------------------------------------------------------------------------------


def test_mixed_track_list_reports_each_track_separately() -> None:
    ids = [
        _product_id("GUNW", track="079"),
        _product_id("GUNW", track="078"),
        _product_id("GUNW", track="012"),
    ]
    parsed = [M.parse_granule(granule_id) for granule_id in ids]

    assert [item.track for item in parsed] == ["079", "078", "012"]
    assert [item.flight_direction for item in parsed] == ["DESCENDING", "DESCENDING", "DESCENDING"]


def test_search_argument_parsing_and_report_still_work(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _write_availability(tmp_path, monkeypatch, [_sample(GUNW_ID, size_mb=250)])
    out = tmp_path / "availability-search"
    payload = [
        {
            "granuleName": GUNW_ID,
            "startTime": "2026-10-08T01:13:29Z",
            "stopTime": "2026-10-20T01:14:03Z",
            "sizeMB": 250,
            "downloadUrl": f"https://example.invalid/{GUNW_ID}.h5",
            "flightDirection": "DESCENDING",
            "polarization": "HH",
            "platform": "NISAR",
        }
    ]

    def fake_asf_search(bbox, params, max_results=250):
        return 200, payload

    monkeypatch.setattr(M, "asf_search", fake_asf_search)
    monkeypatch.setattr(M.time, "sleep", lambda _seconds: None)

    rc = M.main(["search", "--cities", "test-city", "--out", str(out)])
    assert rc == 0
    report = json.loads((out / "test-city.json").read_text())
    assert report["sources"]["nisar_gunw"]["n_granules"] == 1
    assert report["sources"]["nisar_gunw"]["sample_granules"][0]["id"] == GUNW_ID
    assert report["chosen_source"] == "NISAR_L2_GUNW"

    capsys.readouterr()  # discard search chatter
    rc = M.main(["report", "--out", str(out)])
    table = capsys.readouterr().out
    assert rc == 0
    assert "| city | source | available | n | date range | GB | chosen |" in table
    assert "test-city" in table
    assert "nisar_gunw" in table
