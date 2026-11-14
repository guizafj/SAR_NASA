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
    # min/max reference dates, not the response order (the epoch window: each granule's secondary
    # is the partner of an earlier epoch, so it does not extend the range)
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


def test_recommended_stack_is_the_largest_and_a_contiguous_connected_chain():
    payload = series("079", "D", 34) + series("078", "D", 5) + series("012", "A", 2)

    report = M.summarise(payload, 250)
    recommended = report["recommended_stack"]

    assert recommended["track"] == "079"
    assert recommended["n_granules"] == 34
    assert recommended["sample_purpose"] == "download"
    assert recommended["granule_ids_truncated"] is True
    assert len(recommended["granule_ids"]) == M.STACK_GRANULE_LIMIT == 12
    assert "largest single-geometry stack" in recommended["why"]
    assert "12 of 34" in recommended["why"]
    assert "13 connected epoch(s)" in recommended["why"]
    assert "2026-07" in recommended["why"]  # window of the *sample*, not of the stack
    assert "2026-11" in recommended["why"]

    # contiguous: the most recent 12 granules of the 34-granule stack, no holes
    stack_ids = ids(series("079", "D", 34))
    positions = [stack_ids.index(granule_id_) for granule_id_ in recommended["granule_ids"]]
    assert positions == list(range(22, 34))
    assert recommended["granule_ids"] == stack_ids[22:]

    # chronological, and the advertised window is exactly what the sample covers
    sampled = [M.parse_granule(granule_id_) for granule_id_ in recommended["granule_ids"]]
    references = [parsed.reference_start for parsed in sampled]
    assert references == sorted(references)
    assert recommended["date_range"] == [references[0], references[-1]]
    # ... while the whole stack keeps its own truth alongside it
    full_references = [M.parse_granule(granule_id_).reference_start for granule_id_ in stack_ids]
    assert recommended["full_date_range"] == [full_references[0], full_references[-1]]
    assert recommended["n_epochs"] == 35  # 34 chained pairs -> 35 acquisition days
    assert recommended["connected_epochs"] == 35
    assert recommended["download_epochs"] == 13
    assert recommended["epoch_sufficient"] is True
    assert recommended["warning"] is None


def test_preview_stacks_keep_the_even_sample_and_say_so():
    """Only the recommended stack is narrowed to a chain; the rest still preview the window."""
    payload = series("079", "D", 34) + series("078", "D", 20)

    report = M.summarise(payload, 250)
    by_track = {stack["track"]: stack for stack in report["stacks"]}
    preview = by_track["078"]

    ordered = ids(series("078", "D", 20))
    assert preview["sample_purpose"] == "preview"
    assert preview["granule_ids"] == M._even_sample(ordered, M.STACK_GRANULE_LIMIT)
    positions = [ordered.index(granule_id_) for granule_id_ in preview["granule_ids"]]
    assert positions[0] == 0
    assert positions[-1] == len(ordered) - 1
    references = [M.parse_granule(granule_id_).reference_start for granule_id_ in ordered]
    assert preview["date_range"] == preview["full_date_range"] == [references[0], references[-1]]
    assert "download_epochs" not in preview  # a preview is not a download verdict
    assert by_track["079"]["sample_purpose"] == "download"


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
# The connected run: a download must be a chain, not an even spread
# ---------------------------------------------------------------------------------------------
#
# A granule is a *pair*: its name carries a reference and a secondary acquisition. A stack only
# chains when granule[i].secondary == granule[i+1].reference, which is exactly the invariant
# `process_case._chain` enforces (it refuses a network gap) and what turns `k` pairs into `k + 1`
# epochs. The old even sample spread 12 ids across the whole window, so adjacent ids rarely shared
# an acquisition and `process_case` saw 3 disconnected epochs (below MIN_FIT_POINTS = 4).

TRANSFORMS = ROOT / "pipeline" / "science" / "transforms.py"


def local_chain_epochs(granule_ids: list[str]) -> int:
    """Independent epoch count of the longest adjacent chain (no module-private helpers)."""
    pairs = [
        (M.parse_granule(granule_id).reference_start, M.parse_granule(granule_id).secondary_start)
        for granule_id in granule_ids
    ]
    runs: list[list[tuple[str | None, str | None]]] = []
    current: list[tuple[str | None, str | None]] = []
    for reference, secondary in pairs:
        chained = (
            bool(current)
            and bool(reference)
            and bool(current[-1][1])
            and reference[:10] == current[-1][1][:10]  # type: ignore[index]
        )
        if chained:
            current.append((reference, secondary))
        else:
            if current:
                runs.append(current)
            current = [(reference, secondary)]
    if current:
        runs.append(current)

    def epochs(run: list[tuple[str | None, str | None]]) -> int:
        days = {
            stamp[:10]
            for pair in run
            for stamp in pair
            if stamp
        }
        return len(days)

    return max((epochs(run) for run in runs), default=0)


def test_min_fit_points_is_the_science_threshold_not_a_copy():
    text = TRANSFORMS.read_text()
    match = re.search(r"^MIN_FIT_POINTS\s*=\s*(\d+)", text, re.MULTILINE)
    assert match is not None, f"no MIN_FIT_POINTS in {TRANSFORMS}"
    assert M.MIN_FIT_POINTS == int(match.group(1))


def test_33_granule_stack_recommends_a_contiguous_chain():
    payload = series("079", "D", 33)

    recommended = M.summarise(payload, 250)["recommended_stack"]
    sampled = [M.parse_granule(granule_id_) for granule_id_ in recommended["granule_ids"]]

    # every consecutive pair shares an acquisition: the chain `process_case` needs
    for earlier, later in zip(sampled, sampled[1:]):
        assert earlier.secondary_start == later.reference_start, (
            f"granule {earlier.raw!r} secondary {earlier.secondary_start!r} does not meet "
            f"{later.raw!r} reference {later.reference_start!r}"
        )

    epochs = len(recommended["granule_ids"]) + 1
    assert epochs >= M.MIN_FIT_POINTS, (
        f"the recommendation yields {epochs} epochs < MIN_FIT_POINTS={M.MIN_FIT_POINTS}; "
        "`process_case` would refuse it"
    )
    assert recommended["download_epochs"] == epochs
    assert recommended["epoch_sufficient"] is True
    assert recommended["warning"] is None
    assert recommended["sample_purpose"] == "download"

    # contiguous in the ordered stack: the capped, most recent run of one unbroken chain
    ordered = ids(payload)
    positions = [ordered.index(granule_id_) for granule_id_ in recommended["granule_ids"]]
    assert positions == list(range(len(ordered) - M.STACK_GRANULE_LIMIT, len(ordered)))
    assert recommended["granule_ids"] == ordered[-M.STACK_GRANULE_LIMIT:]
    assert recommended["granule_ids_truncated"] is True
    references = [parsed.reference_start for parsed in sampled]
    assert references == sorted(references)
    assert recommended["date_range"] == [references[0], references[-1]]
    assert recommended["full_date_range"] == [
        ordered_reference(ordered, 0),
        ordered_reference(ordered, -1),
    ]
    assert local_chain_epochs(recommended["granule_ids"]) == epochs


def ordered_reference(granule_ids: list[str], index: int) -> str | None:
    return M.parse_granule(granule_ids[index]).reference_start


def test_a_stack_too_short_to_fit_says_so_and_names_a_usable_stack():
    # 8 interleaved pairs on the biggest track: no two adjacent granules chain (each granule is a
    # lone pair -> 2 epochs), which is Málaga track 020's real shape. A smaller track chains fully.
    payload = series("020", "A", 8, cadence=6) + series("070", "D", 6)

    report = M.summarise(payload, 250)
    recommended = report["recommended_stack"]

    assert recommended["track"] == "020"  # the largest identified stack still wins
    epochs = len(recommended["granule_ids"]) + 1
    assert epochs < M.MIN_FIT_POINTS
    assert recommended["download_epochs"] < M.MIN_FIT_POINTS
    assert recommended["epoch_sufficient"] is False
    assert recommended["min_fit_points"] == M.MIN_FIT_POINTS
    assert recommended["warning"], "an unusable recommendation must never be silent"
    assert f"MIN_FIT_POINTS={M.MIN_FIT_POINTS}" in recommended["warning"]
    assert "process_case" in recommended["warning"]
    assert "--stack 070" in recommended["warning"]  # the way forward, not just a complaint
    # the warning is persisted in stacks[] too, next to the recommendation it describes
    published = next(stack for stack in report["stacks"] if stack["track"] == "020")
    assert published["warning"] == recommended["warning"]
    assert published["epoch_sufficient"] is False

    # and the CLI says it out loud before planning anything
    records, label, notes = M.select_stack_granules(report, "recommended")
    assert records  # the plan still names what would be fetched
    assert "020" in label
    assert any("MIN_FIT_POINTS" in note and "--stack 070" in note for note in notes)


def test_a_stack_too_short_with_no_alternative_says_re_run_search():
    payload = series("020", "A", 8, cadence=6)

    recommended = M.summarise(payload, 250)["recommended_stack"]

    assert recommended["epoch_sufficient"] is False
    assert "No stack in this report reaches the threshold" in recommended["warning"]


def test_the_run_with_more_epochs_wins_over_a_more_recent_shorter_run():
    # one geometry, two separated chains: 5 chained pairs early, 3 chained pairs late
    early = series("079", "D", 5)
    late = at_dates(
        [BASE + dt.timedelta(days=200 + 12 * index) for index in range(3)]
    )

    recommended = M.summarise(early + late, 250)["recommended_stack"]

    assert recommended["granule_ids"] == ids(early), "more epochs must beat recency"
    assert recommended["download_epochs"] == len(early) + 1 == 6
    assert recommended["granule_ids_truncated"] is True  # 5 of 8 granules


def test_equal_epoch_runs_pick_the_most_recent_one():
    early = series("079", "D", 3)
    late = at_dates([BASE + dt.timedelta(days=200 + 12 * index) for index in range(3)])

    recommended = M.summarise(early + late, 250)["recommended_stack"]

    assert recommended["granule_ids"] == ids(late), "equal epochs must break to the recent run"
    assert recommended["download_epochs"] == len(late) + 1 == 4
    assert recommended["granule_ids"] == sorted(
        recommended["granule_ids"],
        key=lambda granule_id_: M.parse_granule(granule_id_).reference_start or "",
    )


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


# ---------------------------------------------------------------------------------------------
# The committed evidence, measured: how many epochs does the recommendation actually give?
# ---------------------------------------------------------------------------------------------
#
# The committed reports were generated *before* this fix, so their `recommended_stack` still
# carries the old even sample (mexico-city 12 ids -> 3 connected epochs, jakarta 8 -> 9, malaga
# 12 -> 3). They cannot be regenerated offline, so this test measures and reports instead of
# asserting a stale number; once `search` is re-run it locks in the new verdict per case.

EVIDENCE_CASES = ("mexico-city", "jakarta", "malaga")


@pytest.mark.parametrize("city", EVIDENCE_CASES)
def test_committed_recommendation_epochs(city: str, record_property) -> None:
    """Report `len(granule_ids) + 1` and the measured chain for each committed recommendation."""
    report_path = COMMITTED / f"{city}.json"
    if not report_path.exists():
        pytest.skip(f"no committed availability report for {city!r}")
    source = json.loads(report_path.read_text())["sources"].get(SOURCE) or {}
    recommended = source.get("recommended_stack") or {}
    granule_ids = recommended.get("granule_ids") or []
    if not granule_ids:
        pytest.skip(f"{city}: committed report has no recommended_stack ids")

    sampled = [M.parse_granule(granule_id) for granule_id in granule_ids]
    references = [parsed.reference_start for parsed in sampled]
    assert references == sorted(references), f"{city}: recommended ids are not chronological"

    epochs = len(granule_ids) + 1
    connected = local_chain_epochs(granule_ids)
    clears = connected >= M.MIN_FIT_POINTS
    record_property("granule_ids", len(granule_ids))
    record_property("epochs_if_chained", epochs)
    record_property("connected_epochs", connected)
    record_property("clears_min_fit_points", clears)
    print(
        f"\n{city}: recommended_stack has {len(granule_ids)} id(s) -> {epochs} epoch(s) if they "
        f"chained; measured longest chain = {connected}; clears MIN_FIT_POINTS={M.MIN_FIT_POINTS}: "
        f"{'YES' if clears else 'NO'}"
    )

    if recommended.get("sample_purpose") == "download":
        # fresh evidence: the report itself must agree with the measurement, and never hide a
        # recommendation that cannot feed a fit
        assert recommended["download_epochs"] == connected, (
            f"{city}: report claims {recommended['download_epochs']} download epochs, "
            f"measurement says {connected}"
        )
        assert recommended["epoch_sufficient"] is clears
        if clears:
            assert connected == epochs, (
                f"{city}: a sufficient download must be a chain ({len(granule_ids)} pairs -> "
                f"{epochs} epochs), measured {connected}"
            )
        else:
            assert recommended.get("warning"), (
                f"{city}: {connected} epochs < MIN_FIT_POINTS={M.MIN_FIT_POINTS} but the report "
                "carries no `warning`"
            )
    elif not clears:
        pytest.skip(
            f"{city}: committed evidence predates the connected-run fix — {len(granule_ids)} "
            f"even-sampled id(s) chain into {connected} epoch(s), below MIN_FIT_POINTS="
            f"{M.MIN_FIT_POINTS}; re-run `search` (needs network) to persist the contiguous run"
        )
