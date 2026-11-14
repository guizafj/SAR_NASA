"""Hermetic tests for stack-aware search/download in `pipeline/ingest/02_search_download.py`.

No network, no ASF, no Earthdata: every test builds canned ASF-shaped payloads (the fields the
SearchAPI returns: ``granuleName``, ``startTime``, ``sizeMB``, ``downloadUrl``) and writes a
synthetic availability report under ``tmp_path``.

The regression these tests lock down: ``search`` used to persist only ``sample_granules`` (the
first three results), which for Mexico City spans three different tracks. ``download`` planned
from that preview, so the mixed-geometry guard refused every download with no way forward. The
fix is in the data model — persist the granules and derive one stack per acquisition geometry.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # running pytest from another cwd
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "search_download_stack", ROOT / "pipeline" / "ingest" / "02_search_download.py"
)
assert _SPEC is not None and _SPEC.loader is not None
M = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = M
_SPEC.loader.exec_module(M)

BASE = dt.date(2025, 10, 25)
CITY = "test-city"
SOURCE = "nisar_gunw"
PLAN_LINE = re.compile(r"^\s*\[\d+/\d+\]\s+(\S+)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------------------------
# Canned ASF payloads
# ---------------------------------------------------------------------------------------------


def gunw(track: str, code: str, reference: dt.date, *, size_mb: float = 250.0) -> dict:
    """One ASF-shaped NISAR GUNW response entry (real grammar, synthetic dates)."""
    secondary = reference + dt.timedelta(days=12)
    name = (
        f"NISAR_L2_PR_GUNW_032_113_{code}_{track}_033_4000_SH_"
        f"{reference:%Y%m%d}T011329_{reference:%Y%m%d}T011403_"
        f"{secondary:%Y%m%d}T011329_{secondary:%Y%m%d}T011403_P05023_N_F_J_001"
    )
    return {
        "granuleName": name,
        "startTime": f"{reference:%Y-%m-%d}T01:13:29Z",
        "stopTime": f"{reference:%Y-%m-%d}T01:14:03Z",
        "sizeMB": size_mb,
        "downloadUrl": f"https://datapool.asf.alaska.edu/GUNW/NISAR/{name}.h5",
        "flightDirection": "DESCENDING" if code == "D" else "ASCENDING",
        "polarization": "HH",
        "platform": "NISAR",
    }


def series(
    track: str,
    code: str,
    count: int,
    *,
    cadence: int = 12,
    start: dt.date = BASE,
    size_mb: float = 250.0,
) -> list[dict]:
    """``count`` granules on one geometry, ``cadence`` days apart, oldest first."""
    return [
        gunw(track, code, start + dt.timedelta(days=cadence * index), size_mb=size_mb)
        for index in range(count)
    ]


def at_dates(dates: list[dt.date], track: str = "079", code: str = "D") -> list[dict]:
    return [gunw(track, code, day) for day in dates]


def granule_id(granule: dict) -> str:
    return granule["granuleName"]


def ids(granules: list[dict]) -> list[str]:
    return [granule_id(granule) for granule in granules]


def sample_shape(granules: list[dict]) -> list[dict]:
    """The legacy ``sample_granules`` shape (id/url/size_mb/start/stop), no stacks."""
    return [
        {
            "id": granule_id(granule),
            "url": granule["downloadUrl"],
            "size_mb": granule["sizeMB"],
            "start": granule["startTime"],
            "stop": granule["stopTime"],
        }
        for granule in granules
    ]


# ---------------------------------------------------------------------------------------------
# Availability reports (the artefact search writes, the artefact download reads)
# ---------------------------------------------------------------------------------------------


def write_report(
    tmp_path: pathlib.Path,
    monkeypatch,
    payload: list[dict],
    *,
    legacy: bool = False,
    summary: dict | None = None,
) -> pathlib.Path:
    aois_path = tmp_path / "aois.json"
    aois_path.write_text(
        json.dumps(
            {
                "cities": {
                    CITY: {
                        "name": "Test City",
                        "bbox": [-1.0, 0.0, 1.0, 2.0],
                        "crs": "EPSG:32630",
                        "eu_member_state": False,
                    }
                }
            }
        )
    )
    monkeypatch.setattr(M, "AOIS", aois_path)

    if legacy:
        entry: dict = {
            "attempted": True,
            "available": True,
            "n_granules": len(payload),
            "sample_granules": payload,
            "date_range": [payload[0]["start"], payload[-1]["stop"]] if payload else None,
        }
    else:
        entry = {"attempted": True, "available": True, **(summary or M.summarise(payload, 250))}

    availability = tmp_path / "availability"
    availability.mkdir(exist_ok=True)
    report = {
        "contract_version": "1.0",
        "city": CITY,
        "sources": {SOURCE: entry},
        "chosen_source": "NISAR_L2_GUNW",
    }
    (availability / f"{CITY}.json").write_text(json.dumps(report))
    return availability


def load_source(availability: pathlib.Path) -> dict:
    report = json.loads((availability / f"{CITY}.json").read_text())
    return report["sources"][SOURCE]


def download_argv(availability: pathlib.Path, raw: pathlib.Path, *extra: str) -> list[str]:
    return [
        "download",
        "--city",
        CITY,
        "--source",
        SOURCE,
        "--availability",
        str(availability),
        "--raw",
        str(raw),
        "--dry-run",
        *extra,
    ]


def planned_ids(output: str) -> set[str]:
    return set(PLAN_LINE.findall(output))


# ---------------------------------------------------------------------------------------------
# derive_stacks: grouping and ordering
# ---------------------------------------------------------------------------------------------


def test_derive_stacks_groups_by_track_and_flight_direction():
    payload = series("079", "D", 6) + series("078", "D", 3) + series("012", "A", 2)

    stacks = M.derive_stacks(payload)

    assert [
        (stack["track"], stack["direction"], stack["n_granules"]) for stack in stacks
    ] == [
        ("079", "DESCENDING", 6),
        ("078", "DESCENDING", 3),
        ("012", "ASCENDING", 2),
    ]
    # min/max reference dates, not the response order
    oldest = f"{BASE:%Y-%m-%d}T01:13:29Z"
    assert stacks[0]["date_range"] == [oldest, "2025-12-24T01:13:29Z"]
    assert stacks[2]["date_range"] == [oldest, "2025-11-06T01:13:29Z"]
    assert stacks[0]["mean_interval_days"] == 12.0
    for stack in stacks:
        assert len(stack["granule_ids"]) == stack["n_granules"]
        assert stack["granule_ids_truncated"] is False


def test_derive_stacks_sorts_equal_sizes_by_most_recent_acquisition():
    older = series("079", "D", 3, start=dt.date(2024, 1, 1))
    newer = series("078", "D", 3, start=dt.date(2026, 1, 1))

    # input order must not matter
    stacks = M.derive_stacks(newer + older)

    assert [stack["track"] for stack in stacks] == ["078", "079"]
    assert [stack["n_granules"] for stack in stacks] == [3, 3]
    assert stacks[0]["date_range"][1] > stacks[1]["date_range"][1]


def test_single_track_payload_yields_exactly_one_stack():
    stacks = M.derive_stacks(series("079", "D", 5))

    assert len(stacks) == 1
    assert stacks[0]["track"] == "079"
    assert stacks[0]["n_granules"] == 5
    assert stacks[0]["mean_interval_days"] == 12.0


def test_mean_interval_days_is_the_median_gap_of_consecutive_dates():
    days = [
        dt.date(2025, 1, 1),
        dt.date(2025, 1, 13),  # +12
        dt.date(2025, 1, 25),  # +12
        dt.date(2025, 3, 1),  # +35
    ]

    stacks = M.derive_stacks(at_dates(days))

    assert stacks[0]["mean_interval_days"] == 12.0
    # one acquisition has no interval to measure
    assert M.derive_stacks(at_dates([dt.date(2025, 1, 1)]))[0]["mean_interval_days"] is None
    # a repeated date must not inject a 0-day gap into the median
    duplicated = at_dates([dt.date(2025, 1, 1), dt.date(2025, 1, 1), dt.date(2025, 1, 13)])
    assert M.derive_stacks(duplicated)[0]["mean_interval_days"] == 12.0


# ---------------------------------------------------------------------------------------------
# summarise: persistence and the recommendation
# ---------------------------------------------------------------------------------------------


def test_recommended_stack_is_the_largest_and_its_ids_span_the_window():
    payload = series("079", "D", 34) + series("078", "D", 5) + series("012", "A", 2)

    report = M.summarise(payload, 250)
    recommended = report["recommended_stack"]

    assert recommended["track"] == "079"
    assert recommended["n_granules"] == 34
    assert recommended["granule_ids_truncated"] is True
    assert len(recommended["granule_ids"]) == M.STACK_GRANULE_LIMIT == 12
    assert "largest single-geometry stack" in recommended["why"]
    assert "12 of 34" in recommended["why"]
    assert "2025-10" in recommended["why"]  # window start
    assert "2026-11" in recommended["why"]  # window end (33 x 12 d after 2025-10-25)

    # evenly spread: first and last acquisition included, and every third of the window hit
    stack_ids = ids(series("079", "D", 34))
    positions = [stack_ids.index(granule_id_) for granule_id_ in recommended["granule_ids"]]
    assert positions[0] == 0
    assert positions[-1] == len(stack_ids) - 1
    assert any(position < len(stack_ids) / 3 for position in positions)
    assert any(len(stack_ids) / 3 <= position < 2 * len(stack_ids) / 3 for position in positions)
    assert any(position >= 2 * len(stack_ids) / 3 for position in positions)
    assert positions == sorted(positions)


def test_summarise_persists_granule_ids_capped_with_the_truncation_flag():
    payload = series("079", "D", 150)

    report = M.summarise(payload, 250)

    assert report["n_granules"] == 150
    assert len(report["granule_ids"]) == M.GRANULE_ID_CAP == 120
    assert report["granule_ids_truncated"] is True
    # the downloader needs url+size, so the same slice is kept as records
    assert [record["id"] for record in report["granules"]] == report["granule_ids"]
    assert all(record["url"] and record["size_mb"] for record in report["granules"])
    # acquisition-date order, oldest first, both endpoints of the window retained
    all_ids = ids(payload)
    assert report["granule_ids"][0] == all_ids[0]
    assert report["granule_ids"][-1] == all_ids[-1]
    references = [
        M.parse_granule(granule_id_).reference_start for granule_id_ in report["granule_ids"]
    ]
    assert references == sorted(references)
    # backlog compatibility: the old preview is still there, still three granules
    assert len(report["sample_granules"]) == 3

    small = M.summarise(series("079", "D", 5), 250)
    assert small["granule_ids_truncated"] is False
    assert len(small["granule_ids"]) == 5


def test_stacks_are_bounded_and_the_recommendation_is_among_them():
    payload = []
    for index in range(14):
        payload += series(
            f"{100 + index:03d}", "D", 3, start=dt.date(2025, 1, 1) + dt.timedelta(days=index)
        )

    report = M.summarise(payload, 250)

    assert len(report["stacks"]) == M.MAX_STACKS == 10
    assert report["recommended_stack"]["track"] in {stack["track"] for stack in report["stacks"]}
    recommended = dict(report["recommended_stack"])
    assert recommended.pop("why")
    assert recommended == report["stacks"][0]


def test_unparseable_names_are_grouped_apart_and_never_recommended():
    malformed = [
        {
            "granuleName": f"not-a-granule-name-{index}",
            "startTime": f"2025-10-{index + 1:02d}T00:00:00Z",
            "sizeMB": 1.0,
            "downloadUrl": f"https://example.invalid/mystery-{index}.h5",
        }
        for index in range(7)
    ]
    payload = series("079", "D", 5) + malformed

    stacks = M.derive_stacks(payload)

    assert stacks[0]["track"] is None
    assert stacks[0]["n_granules"] == 7  # the biggest group is the unidentified one
    identified = next(stack for stack in stacks if stack["track"] == "079")
    assert identified["n_granules"] == 5

    recommended = M.recommend_stack(stacks)
    assert recommended["track"] == "079"  # a guessed geometry never wins
    assert "largest single-geometry stack" in recommended["why"]

    # if nothing identifies its geometry, say so instead of pretending
    unidentified = M.recommend_stack(M.derive_stacks(malformed))
    assert unidentified["track"] is None
    assert "no granule name carried a track/direction" in unidentified["why"]


# ---------------------------------------------------------------------------------------------
# download --stack
# ---------------------------------------------------------------------------------------------


def test_download_recommended_plans_only_that_stack_and_passes_the_guard(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    payload = series("079", "D", 8) + series("078", "D", 4) + series("012", "A", 3)
    availability = write_report(tmp_path, monkeypatch, payload)
    src = load_source(availability)
    raw = tmp_path / "raw"

    records, label, notes = M.select_stack_granules(src, "recommended")
    plan = M.build_download_plan(records, SOURCE, [], raw)

    assert set(plan_ids := [item["granule_id"] for item in plan]) == set(
        src["recommended_stack"]["granule_ids"]
    )
    assert len(plan_geometries := M.plan_geometries(plan)) == 1
    assert ("079", "DESCENDING") in plan_geometries
    assert "recommended" in label and "079" in label
    assert notes == []
    assert all(item["decision"].accepted for item in plan)

    rc = M.main(download_argv(availability, raw))
    out = capsys.readouterr().out

    assert rc == M.RC_OK
    assert "Stack selection: recommended" in out
    assert "REFUSED" not in out
    assert planned_ids(out) == set(plan_ids)
    assert raw.exists() is False  # --dry-run still writes nothing


def test_download_all_trips_the_mixed_geometry_guard(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    payload = series("079", "D", 8) + series("078", "D", 4) + series("012", "A", 3)
    availability = write_report(tmp_path, monkeypatch, payload)
    raw = tmp_path / "raw"

    rc = M.main(download_argv(availability, raw, "--stack", "all"))
    captured = capsys.readouterr()

    assert rc == M.RC_PRODUCT_GUARD
    assert "REFUSED" in captured.out
    assert "track 079" in captured.out
    assert "track 078" in captured.out
    assert "track 012" in captured.out
    assert "--stack recommended" in captured.out  # the way forward the old message lacked
    assert not raw.exists()
    assert planned_ids(captured.out) == set(ids(payload))  # "all" really plans everything


def test_download_stack_track_selects_exactly_that_stack(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    payload = series("079", "D", 8) + series("078", "D", 4) + series("012", "A", 3)
    availability = write_report(tmp_path, monkeypatch, payload)
    src = load_source(availability)
    by_track = {stack["track"]: stack for stack in src["stacks"]}
    raw = tmp_path / "raw"

    rc = M.main(download_argv(availability, raw, "--stack", "078"))
    out = capsys.readouterr().out

    assert rc == M.RC_OK
    assert planned_ids(out) == set(by_track["078"]["granule_ids"])
    assert "track 078" in out

    # "78" and "078" name the same track
    rc = M.main(download_argv(availability, raw, "--stack", "78"))
    assert rc == M.RC_OK
    assert planned_ids(capsys.readouterr().out) == set(by_track["078"]["granule_ids"])


def test_download_unknown_track_is_a_usage_error_that_lists_the_tracks(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    availability = write_report(
        tmp_path, monkeypatch, series("079", "D", 3) + series("078", "D", 2)
    )
    raw = tmp_path / "raw"

    rc = M.main(download_argv(availability, raw, "--stack", "999"))
    captured = capsys.readouterr()

    assert rc == M.RC_USAGE
    assert "--stack 999" in captured.err
    assert "079" in captured.err and "078" in captured.err
    assert "search" in captured.err
    assert not raw.exists()


def test_old_report_without_stacks_falls_back_to_sample_granules_with_a_note(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    # one geometry, so the legacy fallback still passes the mixed-geometry guard
    legacy_samples = sample_shape(series("079", "D", 3))
    availability = write_report(tmp_path, monkeypatch, legacy_samples, legacy=True)
    raw = tmp_path / "raw"

    rc = M.main(download_argv(availability, raw))  # default --stack recommended
    captured = capsys.readouterr()

    assert rc == M.RC_OK
    assert "Stack selection: legacy preview (3 granule(s))" in captured.out
    assert "no `stacks`/`recommended_stack`" in captured.out
    assert "Re-run `search`" in captured.out
    assert planned_ids(captured.out) == {sample["id"] for sample in legacy_samples}
    assert not raw.exists()


def test_download_reports_a_source_without_any_granule_records(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    availability = write_report(
        tmp_path, monkeypatch, [], summary={"n_granules": 0, "granules": []}
    )
    raw = tmp_path / "raw"

    rc = M.main(download_argv(availability, raw))
    captured = capsys.readouterr()

    assert rc == M.RC_USAGE
    assert "re-run `search`" in captured.err
    assert not raw.exists()


# ---------------------------------------------------------------------------------------------
# End to end: search writes the report, download follows the recommendation
# ---------------------------------------------------------------------------------------------


def test_search_persists_stacks_and_download_follows_them(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    payload = series("079", "D", 6) + series("078", "D", 3) + series("012", "A", 2)

    def fake_asf_search(bbox, params, max_results=250):
        # no sockets: NISAR gets the canned payload, everything else is empty
        return (200, payload) if str(params.get("platform", "")).startswith("NISAR") else (200, [])

    monkeypatch.setattr(M, "asf_search", fake_asf_search)
    monkeypatch.setattr(M.time, "sleep", lambda _seconds: None)

    write_report(tmp_path, monkeypatch, payload)  # only to install a temporary aois.json
    out = tmp_path / "availability-search"
    rc = M.main(["search", "--cities", CITY, "--out", str(out)])
    assert rc == M.RC_OK

    entry = json.loads((out / f"{CITY}.json").read_text())["sources"][SOURCE]
    assert [stack["track"] for stack in entry["stacks"]] == ["079", "078", "012"]
    assert entry["recommended_stack"]["track"] == "079"
    assert set(entry["granule_ids"]) == set(ids(payload))
    references = [M.parse_granule(gid).reference_start for gid in entry["granule_ids"]]
    assert references == sorted(references)  # date order, not response order
    assert len(entry["granules"]) == len(payload)
    assert entry["sample_granules"][0]["id"] == ids(payload)[0]  # legacy preview untouched

    capsys.readouterr()  # discard search chatter
    raw = tmp_path / "raw"
    rc = M.main(download_argv(out, raw))
    printed = capsys.readouterr().out

    assert rc == M.RC_OK
    assert "Stack selection: recommended" in printed
    assert planned_ids(printed) == set(entry["recommended_stack"]["granule_ids"])


# ---------------------------------------------------------------------------------------------
# The committed availability evidence: the documented dry-run must work against it
# ---------------------------------------------------------------------------------------------

COMMITTED = ROOT / "data" / "processed" / "_availability"
COMMITTED_CASES = sorted(path.stem for path in COMMITTED.glob("*.json"))


@pytest.mark.parametrize("city", COMMITTED_CASES)
def test_committed_report_recommended_dry_run_is_single_geometry(
    city: str, tmp_path: pathlib.Path, capsys
) -> None:
    """For every committed report that already has stacks, the runbook command exits 0.

    Stale reports are skipped with a note instead of failing: regenerating them needs the
    network, which this suite must not touch.
    """
    report_path = COMMITTED / f"{city}.json"
    if not report_path.exists():
        pytest.skip(f"no committed availability report for {city!r}")
    source = json.loads(report_path.read_text())["sources"].get(SOURCE) or {}
    if not source.get("stacks") or not source.get("recommended_stack"):
        pytest.skip(
            f"{city}: committed report predates stack derivation (no `stacks`); "
            "re-run `search` to refresh it (not done here: the suite is offline)"
        )

    raw = tmp_path / "raw"
    rc = M.main(
        [
            "download",
            "--city",
            city,
            "--source",
            SOURCE,
            "--availability",
            str(COMMITTED),
            "--raw",
            str(raw),
            "--stack",
            "recommended",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out

    assert rc == M.RC_OK
    assert "Stack selection: recommended" in out
    assert "REFUSED" not in out
    planned = planned_ids(out)
    assert planned, "the recommended stack must plan at least one granule"
    assert planned <= set(source["recommended_stack"]["granule_ids"])
    geometries = {
        (parsed.track, parsed.direction)
        for parsed in (M.parse_granule(granule_id) for granule_id in planned)
    }
    assert len(geometries) == 1, f"{city}: mixed geometries in the recommended plan"
    assert "1 geometry/geometries" in out
    assert not raw.exists()  # dry run writes nothing
