"""Hermetic tests for the Overpass extract producer (`pipeline/ingest/fetch_context.py`).

Why this module exists
----------------------
The reader (`osm_context.py`) and the pin writer (`pin_context.py`) are useless without tiles, and
until now nothing in the tree could produce them: the retrieval lived in prose. A producer that is
only reviewed by eye breaks in the silent ways this suite pins down:

* a tile grid that misses (or double-covers) a sliver of the AOI bbox, so part of the city is
  unmapped and the map simply looks emptier;
* the `S,W,N,E` argument printed in the wrong order — a valid query for a different box, which
  Overpass answers happily, so nothing fails and the polygons describe the wrong place;
* a retry loop that hammers one endpoint instead of descending the ladder, or one that never
  stops;
* a manifest whose `sha256` is computed from something other than the bytes on disk, which makes
  the audit trail decorative;
* a dry run that quietly sends a request, or a rerun that silently overwrites evidence.

Everything here is offline: the HTTP call is an injectable `transport(url, data, timeout) ->
(status, body_bytes)`, so no test opens a socket. That is mandatory, not convenient — the public
Overpass instances are shared and the suite runs in CI. The 200-byte fixture built inline below is
a real Overpass JSON shape (one way with `geometry`, one multipolygon relation with `members`), so
the bytes written verbatim are the bytes `load_extract` would parse.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pytest from the repo root, without an installed package
    sys.path.insert(0, str(ROOT))

from pipeline.ingest import fetch_context as FC  # noqa: E402
from pipeline.ingest import osm_context as OSM  # noqa: E402

#: A 0.3 x 0.3 deg bbox: exactly two 0.15 deg cells per side, so the grid is 2 x 2 and the tile
#: edges are exact decimal values that can be asserted as strings.
KNOWN_BBOX = (0.0, 10.0, 0.3, 10.3)
#: Two tiles (0.2 / 0.15 -> 2 columns, 0.1 -> 1 row): the smallest grid that can mix an OK tile with
#: a failed one.
TWO_TILE_BBOX = (0.0, 10.0, 0.2, 10.1)
STAMP = "2026-11-15T09:00:00Z"


# ---------------------------------------------------------------------------------------------
# Fixtures / helpers (in memory or under tmp_path; nothing is fetched)
# ---------------------------------------------------------------------------------------------


def overpass_body(*, way_id: int = 101, relation_id: int = 202, stamp: str = STAMP) -> bytes:
    """A tiny but structurally real Overpass response: one way, one multipolygon relation."""
    payload = {
        "version": 0.6,
        "generator": "Overpass API 0.7.62.8 1e9f4a4f",
        "osm3s": {
            "timestamp_osm_base": stamp,
            "copyright": "The data included in this document is from www.openstreetmap.org. "
            "The data is made available under ODbL.",
        },
        "elements": [
            {
                "type": "way",
                "id": way_id,
                "bounds": {"minlat": 10.04, "minlon": 0.04, "maxlat": 10.06, "maxlon": 0.06},
                "geometry": [
                    {"lat": 10.04, "lon": 0.04},
                    {"lat": 10.04, "lon": 0.06},
                    {"lat": 10.06, "lon": 0.06},
                    {"lat": 10.06, "lon": 0.04},
                    {"lat": 10.04, "lon": 0.04},
                ],
                "tags": {"landuse": "residential", "name": "Test Block"},
            },
            {
                "type": "relation",
                "id": relation_id,
                "members": [
                    {
                        "type": "way",
                        "ref": 303,
                        "role": "outer",
                        "geometry": [
                            {"lat": 10.10, "lon": 0.10},
                            {"lat": 10.10, "lon": 0.12},
                            {"lat": 10.12, "lon": 0.12},
                            {"lat": 10.12, "lon": 0.10},
                            {"lat": 10.10, "lon": 0.10},
                        ],
                    }
                ],
                "tags": {"type": "multipolygon", "natural": "water"},
            },
        ],
    }
    return json.dumps(payload, indent=1).encode("utf-8")


class StubTransport:
    """A scripted transport: each call pops the next `(status, body)` and records the request."""

    def __init__(self, script: list[tuple[int, bytes]]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, bytes, float]] = []

    def __call__(self, url: str, data: bytes, timeout: float) -> tuple[int, bytes]:
        self.calls.append((url, data, timeout))
        if not self.script:
            raise AssertionError(f"unexpected request to {url}: the script is spent")
        return self.script.pop(0)

    @property
    def urls(self) -> list[str]:
        return [url for url, _, _ in self.calls]


def refusing_transport(url: str, data: bytes, timeout: float) -> tuple[int, bytes]:
    """A transport that must never be reached (dry run, skipped tiles)."""
    raise AssertionError(f"a request was sent to {url}: this path must not touch the network")


class RecordingSleeper:
    """Replace `time.sleep` so backoff and pause are observable and cost no wall clock."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(float(seconds))


def no_log(_message: str) -> None:
    """Fetch logs go to `print` in production; the assertions here are on the manifest."""


def fetch(tmp_path: pathlib.Path, transport, **kwargs):
    """`fetch_case` with the boilerplate every test here shares."""
    kwargs.setdefault("retrieved_utc", STAMP)
    kwargs.setdefault("transport", transport)
    kwargs.setdefault("sleep", RecordingSleeper())
    kwargs.setdefault("log", no_log)
    return FC.fetch_case(
        kwargs.pop("case_id", "malaga"),
        kwargs.pop("bbox", KNOWN_BBOX),
        out=kwargs.pop("out", tmp_path),
        **kwargs,
    )


def read_manifest(tmp_path: pathlib.Path) -> dict:
    return json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------
# 1. The grid covers the AOI bbox exactly once, and no tile is too big
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ["mexico-city", "jakarta", "malaga"])
def test_tile_grid_covers_the_aoi_bbox_exactly_once(case_id: str) -> None:
    """The planned grid must partition the AOI bbox: no gap (unmapped land) and no overlap (a
    feature fetched twice, i.e. paid for twice and deduplicated downstream)."""
    aoi = FC.load_case_aois([case_id])[case_id]
    bbox = aoi.bbox
    grid = FC.tile_grid(case_id, bbox)

    west, south, east, north = bbox
    total_area = (east - west) * (north - south)
    covered = sum(tile.area_deg2 for tile in grid)
    assert covered == pytest.approx(total_area, rel=1e-9), (
        f"{case_id}: tiles cover {covered!r} deg^2, the AOI bbox is {total_area!r} deg^2"
    )

    for tile in grid:
        width, height = tile.size
        assert width > 0.0 and height > 0.0, f"{tile.id}: degenerate tile {tile.bbox!r}"
        assert width <= FC.MAX_TILE_DEG + 1e-12, f"{tile.id} is {width} deg wide"
        assert height <= FC.MAX_TILE_DEG + 1e-12, f"{tile.id} is {height} deg tall"

    # Outer edges are the bbox edges, and neighbouring tiles share an edge exactly (touching, not
    # overlapping): that is what makes the area sum above meaningful rather than accidental.
    rows = sorted({tile.row for tile in grid})
    cols = sorted({tile.col for tile in grid})
    assert len(grid) == len(rows) * len(cols), f"{case_id}: the grid is not rectangular"
    assert min(tile.west for tile in grid) == west
    assert max(tile.east for tile in grid) == east
    assert min(tile.south for tile in grid) == south
    assert max(tile.north for tile in grid) == north
    for row in rows:
        band = sorted((tile for tile in grid if tile.row == row), key=lambda tile: tile.col)
        for left, right in zip(band, band[1:]):
            assert left.east == right.west, f"{case_id} row {row}: gap/overlap at {left.id}"
    for col in cols:
        column = sorted((tile for tile in grid if tile.col == col), key=lambda tile: tile.row)
        for lower, upper in zip(column, column[1:]):
            assert lower.north == upper.south, f"{case_id} col {col}: gap/overlap at {lower.id}"

    # "Exactly once" for a point: every sample of the bbox falls in exactly one tile.
    for south_step in range(12):
        for east_step in range(12):
            lon = west + (east - west) * (east_step + 0.5) / 12.0
            lat = south + (north - south) * (south_step + 0.5) / 12.0
            hits = [
                tile.id
                for tile in grid
                if tile.west <= lon < tile.east and tile.south <= lat < tile.north
            ]
            assert len(hits) == 1, f"{case_id}: ({lon}, {lat}) is in {hits}"


def test_tile_grid_does_not_add_a_sliver_for_an_exact_multiple() -> None:
    """A span that is an exact multiple of the limit must not produce a 1e-16 extra column."""
    grid = FC.tile_grid("synthetic", (0.0, 0.0, 0.45, 0.15))
    assert len(grid) == 3
    assert all(tile.size[0] == pytest.approx(0.15) for tile in grid)


# ---------------------------------------------------------------------------------------------
# 2. The Overpass argument is S,W,N,E
# ---------------------------------------------------------------------------------------------


def test_overpass_bbox_is_south_west_north_east() -> None:
    """`bbox` is `[W, S, E, N]`; Overpass wants `S,W,N,E`. The two orders differ, and getting it
    wrong yields a valid query for the wrong box — an error no HTTP status would report."""
    grid = FC.tile_grid("known", KNOWN_BBOX)
    first = grid[0]
    last = grid[-1]
    assert first.bbox == (0.0, 10.0, 0.15, 10.15)
    assert FC.overpass_bbox(first.bbox) == "10.0000000,0.0000000,10.1500000,0.1500000"
    assert FC.overpass_bbox(last.bbox) == "10.1500000,0.1500000,10.3000000,0.3000000"

    query = FC.build_query(first.bbox)
    assert "(10.0000000,0.0000000,10.1500000,0.1500000)" in query
    assert "out geom;" in query
    assert 'nwr["building"]' not in query, "building is opt-in (39 344 ways / 21.47 MB measured)"
    assert 'nwr["boundary"="administrative"]' in query
    for key in ("landuse", "natural", "waterway", "leisure", "man_made", "amenity", "power",
                "railway", "public_transport", "place"):
        assert f'nwr["{key}"](' in query, f"{key} is classified by enrich_context but not selected"

    with_buildings = FC.build_query(first.bbox, include_buildings=True)
    assert 'nwr["building"](10.0000000,0.0000000,10.1500000,0.1500000);' in with_buildings


# ---------------------------------------------------------------------------------------------
# 3. The ladder advances on failure and stops after --attempts per endpoint
# ---------------------------------------------------------------------------------------------


def test_endpoint_ladder_advances_on_failure_and_stops_after_attempts() -> None:
    endpoints = ("https://a.invalid/api", "https://b.invalid/api", "https://c.invalid/api")
    transport = StubTransport([(504, b"too busy")] * 6)
    sleeper = RecordingSleeper()

    outcome = FC.fetch_query(
        b"data=query",
        endpoints=endpoints,
        attempts=2,
        backoff=30.0,
        transport=transport,
        sleep=sleeper,
        log=no_log,
    )

    assert transport.urls == [endpoints[0]] * 2 + [endpoints[1]] * 2 + [endpoints[2]] * 2, (
        "one endpoint is retried in place, then the ladder moves down"
    )
    assert outcome.status == 504
    assert outcome.requests == 6
    assert outcome.endpoint == endpoints[2]
    assert outcome.ok is False
    # A backoff before each retry, but none after the last attempt of an endpoint (that would be
    # 30 s of waiting followed by a request to a different server).
    assert sleeper.slept == [30.0, 30.0, 30.0]


def test_no_endpoint_gets_more_than_attempts_requests() -> None:
    endpoints = ("https://a.invalid/api", "https://b.invalid/api")
    transport = StubTransport([(429, b"slow down")] * 6)
    outcome = FC.fetch_query(
        b"data=query",
        endpoints=endpoints,
        attempts=3,
        transport=transport,
        sleep=RecordingSleeper(),
        log=no_log,
    )
    assert outcome.requests == 6, "attempts=3 on two endpoints is six requests, not a loop"
    assert transport.urls == [endpoints[0]] * 3 + [endpoints[1]] * 3
    assert outcome.status == 429


def test_http_200_is_written_verbatim_and_its_sha256_matches_the_file(tmp_path) -> None:
    body = overpass_body()
    transport = StubTransport([(200, body)] * 4)
    manifest = fetch(tmp_path, transport)

    tiles = sorted((tmp_path / "tiles").glob("*.json"))
    assert len(tiles) == 4
    assert tiles[0].read_bytes() == body, "the response must be stored verbatim, not re-serialised"
    on_disk = tiles[0].read_bytes()
    assert len(on_disk) == len(body)

    record = manifest["tiles"][0]
    assert record["id"] == "malaga_r0_c0"
    assert record["status"] == 200
    assert record["sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert record["bytes"] == len(on_disk)
    assert record["elements"] == 2
    assert record["endpoint"] == FC.ENDPOINTS[0]
    assert record["attempts"] == 1
    assert record["skipped"] is False
    assert record["bbox"] == [0.0, 10.0, 0.15, 10.15]

    # The bbox is published in the repository order, the request used the Overpass order.
    assert manifest["bbox"] == [0.0, 10.0, 0.3, 10.3]
    assert manifest["case_id"] == "malaga"
    assert manifest["retrieved_utc"] == STAMP
    assert manifest["timestamp_osm_base"] == STAMP
    assert manifest["totals"]["tiles"] == 4
    assert manifest["totals"]["ok"] == 4
    assert manifest["totals"]["failed"] == 0
    assert manifest["totals"]["elements"] == 8
    assert manifest["request"]["include_buildings"] is False
    assert FC.BBOX_PLACEHOLDER in manifest["request"]["query_template"]


def test_manifest_is_written_atomically_with_sorted_keys(tmp_path) -> None:
    fetch(tmp_path, StubTransport([(200, overpass_body())] * 4))
    manifest = read_manifest(tmp_path)
    assert list(manifest) == sorted(manifest), "sort_keys=True keeps a two-run diff readable"
    assert list(manifest["tiles"][0]) == sorted(manifest["tiles"][0])
    assert not list(tmp_path.glob("*.tmp")), "the temp file must be renamed away, not left behind"


# ---------------------------------------------------------------------------------------------
# 4. A 504 is a recorded failure, not an exception and not a substitute
# ---------------------------------------------------------------------------------------------


def test_a_504_tile_is_recorded_as_failed_while_ok_tiles_are_counted(tmp_path) -> None:
    body = overpass_body()
    transport = StubTransport([(200, body), (504, b"server too busy")])
    manifest = fetch(
        tmp_path,
        transport,
        bbox=TWO_TILE_BBOX,
        endpoints=(FC.ENDPOINTS[0],),
        attempts=1,
    )

    first, second = manifest["tiles"]
    assert first["status"] == 200 and first["elements"] == 2
    assert second["status"] == 504
    assert second["sha256"] is None and second["bytes"] == 0 and second["elements"] == 0
    assert manifest["totals"] == {
        "tiles": 2,
        "ok": 1,
        "failed": 1,
        "skipped": 0,
        "bytes": len(body),
        "elements": 2,
        "requests": 2,
        "seconds": manifest["totals"]["seconds"],
    }
    # No file, no synthetic stand-in: absence is the honest representation of a failed tile.
    assert [path.name for path in sorted((tmp_path / "tiles").glob("*.json"))] == [
        "malaga_r0_c0.json"
    ]
    assert len(transport.calls) == 2


def test_a_200_with_an_unusable_body_is_not_published_as_a_tile(tmp_path) -> None:
    """A proxy can answer 200 with an HTML error page. Writing it would make `load_extract` skip a
    file the manifest calls ok — the tile would look fetched and carry nothing."""
    transport = StubTransport([(200, b"<html>gateway</html>")])
    manifest = fetch(
        tmp_path, transport, bbox=TWO_TILE_BBOX, endpoints=(FC.ENDPOINTS[0],), attempts=1
    )
    record = manifest["tiles"][0]
    assert record["status"] == FC.STATUS_NO_RESPONSE
    assert record["sha256"] is None
    assert manifest["totals"]["ok"] == 0
    assert not list((tmp_path / "tiles").glob("*.json"))


# ---------------------------------------------------------------------------------------------
# 5. --dry-run: one line per tile, zero requests, zero writes
# ---------------------------------------------------------------------------------------------


def test_dry_run_sends_nothing_and_prints_one_line_per_tile(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(FC, "http_transport", refusing_transport)
    root = tmp_path / "raw"
    code = FC.main(["--dry-run", "--case", "malaga", "--root", str(root)])
    out = capsys.readouterr().out

    assert code == 0
    assert not root.exists(), "a dry run must not create the tree it describes"
    aoi = FC.load_case_aois(["malaga"])["malaga"]
    grid = FC.tile_grid("malaga", aoi.bbox)
    plan = [line for line in out.splitlines() if " POST " in line]
    assert len(plan) == len(grid), "one line per tile"
    assert len(out.splitlines()) == len(grid) + 1, "and exactly one header line per case"
    assert out.splitlines()[0].startswith("[malaga] bbox [W,S,E,N]=")
    assert f"-> {len(grid)} tile(s)" in out.splitlines()[0]
    assert all(FC.ENDPOINTS[0] in line for line in plan)
    assert FC.overpass_bbox(grid[0].bbox) in plan[0]


def test_dry_run_defaults_to_every_declared_case(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(FC, "http_transport", refusing_transport)
    code = FC.main(["--dry-run", "--root", str(tmp_path / "raw")])
    out = capsys.readouterr().out
    assert code == 0
    declared = FC.load_case_aois(None)
    assert len(declared) == 3
    headers = [line for line in out.splitlines() if " tile(s)" in line]
    assert len(headers) == 3
    for case_id in declared:
        assert any(line.startswith(f"[{case_id}] ") for line in headers)
    tile_lines = [line for line in out.splitlines() if " POST " in line]
    assert len(tile_lines) == sum(len(FC.tile_grid(case_id, aoi.bbox)) for case_id, aoi in declared.items())
    assert not (tmp_path / "raw").exists()


# ---------------------------------------------------------------------------------------------
# 6. Skips: cheap rerun, and --force is the only way to overwrite
# ---------------------------------------------------------------------------------------------


def test_existing_tile_is_skipped_without_force_and_refetched_with_it(tmp_path) -> None:
    first = overpass_body(way_id=101)
    tiles_dir = tmp_path / "tiles"
    tiles_dir.mkdir(parents=True)
    existing = tiles_dir / "malaga_r0_c0.json"
    existing.write_bytes(first)

    skipped = fetch(tmp_path, refusing_transport, bbox=(0.0, 10.0, 0.1, 10.1))
    record = skipped["tiles"][0]
    assert record["skipped"] is True
    assert record["status"] == 200, "the file on disk is a 200 response; 'skip' is not a status"
    assert record["attempts"] == 0
    assert record["sha256"] == hashlib.sha256(first).hexdigest()
    assert record["elements"] == 2
    assert existing.read_bytes() == first
    assert skipped["totals"]["skipped"] == 1

    second = overpass_body(way_id=555)
    transport = StubTransport([(200, second)])
    refreshed = fetch(
        tmp_path, transport, force=True, bbox=(0.0, 10.0, 0.1, 10.1), endpoints=(FC.ENDPOINTS[0],)
    )
    record = refreshed["tiles"][0]
    assert len(transport.calls) == 1
    assert record["skipped"] is False
    assert record["attempts"] == 1
    assert existing.read_bytes() == second
    assert record["sha256"] == hashlib.sha256(second).hexdigest()


# ---------------------------------------------------------------------------------------------
# 7. The manifest records the planet state(s) the tiles were answered with
# ---------------------------------------------------------------------------------------------


def test_two_snapshots_publish_the_latest_stamp_and_record_the_range(tmp_path) -> None:
    """A tiled run has no single snapshot. The reader (`osm_context._osm_data_stamp`) prefers the
    global key, so it must never carry the oldest stamp, and the per-tile stamps must exist —
    otherwise the reader's honest path has nothing to read."""
    older = "2026-11-14T00:00:00Z"
    newer = "2026-11-15T00:00:00Z"
    transport = StubTransport(
        [
            (200, overpass_body(way_id=1, relation_id=2, stamp=newer)),
            (200, overpass_body(way_id=3, relation_id=4, stamp=older)),
        ]
    )
    extract_dir = OSM.context_dir("malaga", tmp_path)  # where load_extract looks for a case
    manifest = fetch(
        tmp_path,
        transport,
        bbox=TWO_TILE_BBOX,
        endpoints=(FC.ENDPOINTS[0],),
        attempts=1,
        pause=0.0,
        out=extract_dir,
    )

    assert [record["osm_timestamp"] for record in manifest["tiles"]] == [newer, older]
    assert manifest["osm_timestamp_base_range"] == [older, newer], "the range is [oldest, newest]"
    assert manifest["timestamp_osm_base"] == newer, (
        "the global key is the latest stamp, not the first: the reader prefers it"
    )

    record = OSM.extract_record("malaga", tmp_path)
    assert record is not None and record.timestamp_osm_base == newer

    # The range carries the truth on its own: with the global key absent the reader still resolves
    # the newest stamp it was given, which is the path this manifest exists to feed.
    without_global = dict(manifest)
    without_global.pop("timestamp_osm_base")
    FC.write_manifest(without_global, extract_dir / "manifest.json")
    record = OSM.extract_record("malaga", tmp_path)
    assert record is not None and record.timestamp_osm_base == newer


def test_load_extract_skips_a_tile_the_manifest_marks_failed(tmp_path) -> None:
    """A failed `--force` keeps the previous file as evidence; parsing that file as if this run had
    fetched it publishes data the manifest says was never retrieved."""
    fresh = overpass_body(way_id=11, relation_id=12, stamp="2026-11-15T00:00:00Z")
    stale = overpass_body(way_id=21, relation_id=22, stamp="2026-11-14T00:00:00Z")
    extract_dir = OSM.context_dir("malaga", tmp_path)
    common = {
        "bbox": TWO_TILE_BBOX,
        "endpoints": (FC.ENDPOINTS[0],),
        "attempts": 1,
        "pause": 0.0,
        "out": extract_dir,
    }
    fetch(tmp_path, StubTransport([(200, fresh), (200, stale)]), **common)
    manifest = fetch(
        tmp_path,
        StubTransport([(200, overpass_body(way_id=31, relation_id=32)), (504, b"too busy")]),
        force=True,
        **common,
    )

    by_id = {record["id"]: record for record in manifest["tiles"]}
    assert by_id["malaga_r0_c1"]["status"] == 504, "the second run must record the failure"
    stale_path = extract_dir / "tiles" / "malaga_r0_c1.json"
    assert stale_path.is_file(), "evidence is kept: a failure is not a reason to delete a file"
    assert json.loads(stale_path.read_text(encoding="utf-8"))["elements"][0]["id"] == 21

    frame = OSM.load_extract("malaga", tmp_path)
    assert 21 not in set(frame["osm_id"]), "the failed tile's stale elements were parsed anyway"
    assert set(frame["osm_id"]) >= {31, 32}, "the tile this run did fetch must still load"
    assert frame.attrs["skipped_tiles"] == ["malaga_r0_c1"]


def test_load_extract_still_loads_when_the_manifest_names_no_tiles(tmp_path) -> None:
    """The pinned/committed case: a manifest with no per-tile ids must not turn the directory into
    nothing. An absent record is not a failure, so every file is trusted exactly as before."""
    directory = OSM.context_dir("malaga", tmp_path)
    (directory / "tiles").mkdir(parents=True)
    (directory / "tiles" / "malaga_r0_c0.json").write_bytes(overpass_body())
    (directory / "manifest.json").write_text(
        json.dumps({"retrieved_utc": STAMP, "timestamp_osm_base": STAMP, "tiles": []}),
        encoding="utf-8",
    )

    frame = OSM.load_extract("malaga", tmp_path)
    assert set(frame["osm_id"]) == {101, 202}
    assert frame.attrs["skipped_tiles"] == []
    # And with no manifest at all the loader is still the hard stop it always was.
    (directory / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        OSM.load_extract("malaga", tmp_path)


# ---------------------------------------------------------------------------------------------
# 8. The recorded sha256 is the hash of the file, not of the socket buffer
# ---------------------------------------------------------------------------------------------


def test_the_recorded_sha256_is_the_sha256_of_the_bytes_on_disk(tmp_path, monkeypatch) -> None:
    """`bytes`/`sha256` describe the tile a reader will parse. Hashing the response bytes records a
    file that was never written whenever writer and buffer disagree (a newline, a re-encode), and
    the skip path — which hashes disk — would then disagree with it."""
    body = overpass_body()
    write_bytes = pathlib.Path.write_bytes

    def write_with_trailing_newline(self, data: bytes, *args, **kwargs) -> int:
        # Stands in for any writer that changes the bytes on their way to disk.
        return write_bytes(self, data + b"\n")

    monkeypatch.setattr(pathlib.Path, "write_bytes", write_with_trailing_newline)
    manifest = fetch(
        tmp_path,
        StubTransport([(200, body), (200, body)]),
        bbox=TWO_TILE_BBOX,
        endpoints=(FC.ENDPOINTS[0],),
        attempts=1,
        pause=0.0,
    )

    on_disk = (tmp_path / "tiles" / "malaga_r0_c0.json").read_bytes()
    assert on_disk == body + b"\n"
    record = manifest["tiles"][0]
    assert record["sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert record["sha256"] != hashlib.sha256(body).hexdigest(), (
        "the recorded hash is of the socket buffer, not of the file on disk"
    )
    assert record["bytes"] == len(on_disk)


# ---------------------------------------------------------------------------------------------
# 9. The politeness pause spans cases, not tiles-within-a-case
# ---------------------------------------------------------------------------------------------


def test_the_pause_between_tiles_spans_cases(tmp_path) -> None:
    """The pause is politeness between two consecutive requests to a shared server. Counting it per
    case issues the last tile of one case and the first of the next back to back — the burst the
    pause exists to prevent."""
    sleeper = RecordingSleeper()
    gate = FC.RequestGate()
    for case_id in ("malaga", "jakarta"):
        FC.fetch_case(
            case_id,
            (0.0, 10.0, 0.1, 10.1),  # one tile per case: nothing to pause for inside a case
            out=tmp_path / case_id,
            transport=StubTransport([(200, overpass_body())]),
            sleep=sleeper,
            pause=5.0,
            attempts=1,
            endpoints=(FC.ENDPOINTS[0],),
            retrieved_utc=STAMP,
            gate=gate,
            log=no_log,
        )

    assert sleeper.slept == [5.0], (
        "the second case starts with a request already sent, so it must pause before its tile"
    )
