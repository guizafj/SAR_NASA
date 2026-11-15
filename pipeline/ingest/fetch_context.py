#!/usr/bin/env python3
"""Fetch the cached Overpass extract that `osm_context` reads (the missing producer).

Why this file exists
--------------------
`pipeline/ingest/osm_context.py` reads `data/raw/<case>/context/{manifest.json,tiles/*.json}` and
`pipeline/ingest/pin_context.py` turns that extract into `config/context/<case>.json`, but nothing
in the tree could *produce* the tiles: retrieval was prose only (`docs/context-sources.md` §2 and
`pipeline/ingest/README.md` "Contexto OSM"), retyped by hand for every case. A recipe that lives in
prose drifts from the reader that consumes its output — the three details this file pins are exactly
the ones a hurried operator gets wrong once and then trusts: the tile grid (at most 0.15 deg per
side), the argument order (`bbox` is `[W, S, E, N]`, Overpass wants `S,W,N,E`), and the manifest
fields `extract_record` counts (`status`, `elements`, `bbox`, `retrieved_utc`,
`timestamp_osm_base`, `osm_timestamp` per tile and `osm_timestamp_base_range` for the run).

A run of tiles has no single snapshot, so neither value is invented: each tile records the
`osm3s.timestamp_osm_base` it was answered with, the global key publishes the **latest** of them
(never the first) and `osm_timestamp_base_range` says how wide the disagreement was.

It is deliberately boring: tile the AOI bbox, ask Overpass one tile at a time, write every usable
200 body verbatim, record what happened in `manifest.json`. It never invents data. A failed tile
keeps its HTTP status in the manifest and no file is written, which is exactly how
`extract_record` already reports `tiles_failed`; a run that fetched nothing leaves no tile to be
mistaken for ground truth.

Output layout (gitignored, tens of MB — `data/raw/` is not committed)::

    <root>/<case>/context/manifest.json                  the retrieval record
    <root>/<case>/context/tiles/<case>_r<r>_c<c>.json    verbatim Overpass responses

Politeness, and where each number comes from
--------------------------------------------
The public Overpass instances are shared and reject load politely rather than silently:

* one request at a time (no concurrency anywhere in this module);
* a descriptive `User-Agent` on every request; no e-mail is embedded because the repository
  publishes no contact address and an invented one would be worse than none;
* `--pause` seconds between tiles, default 8, and 30 s of backoff before a retry — the values
  recorded in `docs/context-sources.md` §2 while extracting the first case;
* on 429/502/504, a transport-level failure or a timeout: sleep 30 s and retry once on the same
  endpoint, then move down the ladder
  `overpass-api.de` -> `overpass.private.coffee` -> `ovp.dresel.systems`;
* never more than `--attempts` (default 2) requests to one endpoint for one tile.

`building` is opt-in (`--include-buildings`)
--------------------------------------------
The classifier (`pipeline/enrich_context.py`) can use `building=*`, but building footprints are the
densest layer OSM has. Measured against the public server while extracting the first case: a
3.3 x 3.9 km box returned 39 344 building ways / 21.47 MB, which is one tile's whole budget spent on
one tag and takes minutes to transfer. The figure is not re-derivable from the repository because
`data/raw/` is gitignored; it is recorded here because it is the reason the flag defaults to off.
Without the flag the extract still classifies land use from `landuse`/`natural`/`amenity`/... — a
missing building only means `unknown` where no other polygon covers the cell, never a guess.

What `retrieved_utc` is, and is not
-----------------------------------
It is a wall clock stamped ONCE per CLI run, so all cases of one invocation agree. The tiles are
consequently *not* a deterministic artefact — the same request tomorrow returns different bytes.
What is deterministic is the pin built from them (`pipeline/ingest/pin_context.py`), which keeps the
decisions and drops the timing. Do not compare tile sha256 across runs and call a difference a bug.

Skips keep the reader honest
----------------------------
An existing tile is not overwritten without `--force`, and the manifest records it with
`"status": 200` plus `"skipped": true`: the file on disk *is* a 200 response and `extract_record`
counts `status == 200` as a fetched tile, so recording `"skip"` there would report a present tile as
a failure. `--force` re-fetches; if that fetch fails, the previous file is kept and the run says so
(one transient outage must not delete evidence).

Exit code: 0 when every tile is 200 (fetched or skipped), 1 when at least one tile failed — a
partial extract is usable but must not be mistaken for a complete one.

Usage::

    ./scripts/python.sh pipeline/ingest/fetch_context.py --dry-run
    ./scripts/python.sh pipeline/ingest/fetch_context.py --case malaga
    ./scripts/python.sh pipeline/ingest/fetch_context.py --case malaga --include-buildings --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `python pipeline/ingest/fetch_context.py` from the repo root
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from pipeline.aoi import AOI, AOIError, load_aoi  # noqa: E402
from pipeline.ingest import osm_context as OSM  # noqa: E402

CONFIG_PATH = ROOT / "config" / "study_cases.yaml"

#: Hard ceiling for one tile side. The tile count is derived per case from the AOI bbox, never
#: hand-written: 0.15 deg is ~16 km in latitude, small enough that Overpass answers a tile in
#: seconds and that a retry costs little.
MAX_TILE_DEG = 0.15
#: Seconds between tiles, and the backoff before a retry (recorded in `docs/context-sources.md` §2).
DEFAULT_PAUSE_SECONDS = 8.0
DEFAULT_BACKOFF_SECONDS = 30.0
#: Requests per endpoint per tile. Two covers a momentary 429/502/504; a third is abuse, not
#: persistence — the ladder exists for that.
DEFAULT_ATTEMPTS = 2
#: Overpass's own documented `[timeout:]` default, repeated so the query is explicit about it.
DEFAULT_TIMEOUT_SECONDS = 180.0

#: The ladder, in the order tried. Public instances answer the same QL; moving down it is a
#: politeness measure (a busy mirror) and not a claim that the mirrors hold identical planet states.
ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://ovp.dresel.systems/api/interpreter",
)
#: Statuses the public server uses to say "not now". Retried once on the same endpoint after the
#: backoff, then the next endpoint.
RETRY_STATUSES = frozenset({429, 502, 504})
#: No HTTP response at all (DNS failure, connect timeout, reset). Retryable for the same reason as
#: a 502: the server never judged the query.
STATUS_NO_RESPONSE = 0

#: Selector keys for the QL. They are the tag keys `enrich_context.TAG_RULES` classifies, minus
#: `boundary` (value-filtered below), `building` (opted into, see the module docstring) and `name`
#: (not a selector: it arrives as a tag of the elements these keys already match).
QUERY_KEYS: tuple[str, ...] = (
    "landuse",
    "natural",
    "waterway",
    "leisure",
    "man_made",
    "amenity",
    "power",
    "railway",
    "public_transport",
    "place",
)
#: `boundary` is only useful with its value; `boundary=*` would drag in unrelated boundaries.
ADMIN_QUERY = 'nwr["boundary"="administrative"]'
#: Stand-in for the `S,W,N,E` argument in the query recorded in the manifest.
BBOX_PLACEHOLDER = "S,W,N,E"

USER_AGENT = (
    "SAR_NASA-context-extract/1.0 "
    "(Dancing with the SARs, NASA Space Apps 2026; OpenStreetMap context pull, one tile at a time)"
)

#: The injectable HTTP call: `(url, body_bytes, timeout) -> (status, response_bytes)`.
#: Status 0 means "no response at all". Tests pass a stub; nothing else in this module opens a
#: socket, which is what makes the fetch logic testable offline.
Transport = Callable[[str, bytes, float], tuple[int, bytes]]
Sleeper = Callable[[float], None]
Logger = Callable[[str], None]


# ---------------------------------------------------------------------------------------------
# Tiling and the QL (pure functions)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    """One bbox tile: its grid position, its `[W, S, E, N]` box and its file stem.

    `bbox` is kept in the repository's own order because the manifest publishes it that way;
    `overpass_bbox` is the only place that reorders it, which is the point.
    """

    id: str
    row: int
    col: int
    bbox: tuple[float, float, float, float]

    @property
    def west(self) -> float:
        return self.bbox[0]

    @property
    def south(self) -> float:
        return self.bbox[1]

    @property
    def east(self) -> float:
        return self.bbox[2]

    @property
    def north(self) -> float:
        return self.bbox[3]

    @property
    def size(self) -> tuple[float, float]:
        return (self.east - self.west, self.north - self.south)

    @property
    def area_deg2(self) -> float:
        width, height = self.size
        return width * height


def _split(start: float, end: float, max_deg: float) -> list[float]:
    """Ascending cell edges from `start` to `end`, every cell at most `max_deg` wide.

    The last edge is `end` itself rather than a computed value, so the sum of the cells telescopes
    to exactly the bbox: a rounding gap between tiles would be a hole in the query, and a hole in
    the query is silently unmapped land. The `- 1e-12` in the ceiling guard is the other half of
    the same argument: a span that is an exact multiple of `max_deg` must not produce a sliver
    column from a 1e-16 remainder.
    """
    if not end > start:
        raise ValueError(f"need end > start, got start={start!r} end={end!r}")
    if max_deg <= 0:
        raise ValueError(f"max_deg must be positive, got {max_deg!r}")
    span = end - start
    count = max(1, int(math.ceil(span / max_deg - 1e-12)))
    return [start + span * index / count for index in range(count)] + [end]


def tile_grid(
    case_id: str, bbox: tuple[float, float, float, float], *, max_deg: float = MAX_TILE_DEG
) -> tuple[Tile, ...]:
    """The tile grid of a case: rows south-to-north, columns west-to-east, `r0_c0` at SW.

    `id` is `<case>_r<r>_c<c>` because `osm_context` globs `tiles/*.json` and does not parse the
    name; the name is for the operator reading a directory listing.
    """
    west, south, east, north = (float(value) for value in bbox)
    lon_edges = _split(west, east, max_deg)
    lat_edges = _split(south, north, max_deg)
    tiles: list[Tile] = []
    for row in range(len(lat_edges) - 1):
        for col in range(len(lon_edges) - 1):
            tiles.append(
                Tile(
                    id=f"{case_id}_r{row}_c{col}",
                    row=row,
                    col=col,
                    bbox=(lon_edges[col], lat_edges[row], lon_edges[col + 1], lat_edges[row + 1]),
                )
            )
    return tuple(tiles)


def overpass_bbox(bbox: tuple[float, float, float, float]) -> str:
    """`[W, S, E, N]` -> the `S,W,N,E` string Overpass expects.

    Seven decimals is ~1 cm in latitude: finer than any OSM edit and finer than the tile edge
    arithmetic, so a tile's recorded bbox and its queried box agree to the printed digit.
    """
    west, south, east, north = (float(value) for value in bbox)
    return f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"


def _query_lines(box: str, include_buildings: bool) -> str:
    statements = [f'nwr["{key}"]({box});' for key in QUERY_KEYS]
    statements.append(f"{ADMIN_QUERY}({box});")
    if include_buildings:
        statements.append(f'nwr["building"]({box});')
    lines = [f"[out:json][timeout:{int(DEFAULT_TIMEOUT_SECONDS)}];", "("]
    lines.extend(f"  {statement}" for statement in statements)
    lines.extend([");", "out geom;"])
    return "\n".join(lines) + "\n"


def build_query(
    bbox: tuple[float, float, float, float], *, include_buildings: bool = False
) -> str:
    """The Overpass QL for one tile.

    `out geom` is not optional: `osm_context._relation_geometry` assembles multipolygons from the
    geometry of each member way, so a response without geometry cannot be read at all (there is no
    node store in this pipeline).
    """
    return _query_lines(overpass_bbox(bbox), include_buildings=include_buildings)


# ---------------------------------------------------------------------------------------------
# Transport and the retry ladder
# ---------------------------------------------------------------------------------------------


def http_transport(url: str, data: bytes, timeout: float) -> tuple[int, bytes]:
    """POST the QL and return `(status, body)`; status 0 when there was no HTTP response.

    Error statuses are returned, not raised: the caller decides whether a 429 is a retry, and a
    transport that raises would turn a routine "server busy" into a crashed run. Only a *transport*
    failure (no response) becomes status 0; a body that came with a status is kept so the caller
    can log it (it is never written to a tile file).
    """
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 200)), response.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx: urllib raises, Overpass still answered
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 - a body we cannot read is not a reason to crash
            body = b""
        return int(exc.code), body
    except (urllib.error.URLError, TimeoutError, OSError):
        return STATUS_NO_RESPONSE, b""


def _tile_payload(body: bytes) -> Mapping[str, Any] | None:
    """The Overpass JSON object of a response body, or `None` when it is not one."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping) or not isinstance(payload.get("elements"), list):
        return None
    return payload


def _payload_stamp(payload: Mapping[str, Any] | None) -> str | None:
    """The `osm3s.timestamp_osm_base` a tile was answered with, or `None` when it has none.

    This is the planet state that tile describes, per tile, because tiles are not one snapshot.
    """
    if not isinstance(payload, Mapping):
        return None
    osm3s = payload.get("osm3s")
    if not isinstance(osm3s, Mapping):
        return None
    value = osm3s.get("timestamp_osm_base")
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class FetchOutcome:
    """What one tile's requests produced: the status a reviewer audits and the bytes to write."""

    status: int
    endpoint: str | None
    body: bytes
    requests: int
    seconds: float
    usable: bool

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.usable


def fetch_query(
    data: bytes,
    *,
    endpoints: Sequence[str] = ENDPOINTS,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Transport | None = None,
    sleep: Sleeper | None = None,
    accept: Callable[[bytes], bool] | None = None,
    log: Logger | None = None,
) -> FetchOutcome:
    """Run one query through the ladder: at most `attempts` requests per endpoint, in order.

    The order of operations matters and is asserted in `tests/test_fetch_context.py`: a retryable
    failure sleeps `backoff` and retries the *same* endpoint; only when that endpoint's attempts
    are spent does the next one get a turn. A non-retryable status (a 400 from a malformed query,
    say) does not sleep and does not spend the remaining attempts — every other endpoint would
    reject the same query for the same reason.

    `accept` lets the caller refuse a 200 whose body is unusable (an HTML error page from a proxy);
    a refused body counts as a retryable failure, so garbage is never written as a tile.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts!r}")
    sender: Transport = transport if transport is not None else http_transport
    sleeper: Sleeper = sleep if sleep is not None else time.sleep
    note: Logger = log if log is not None else (lambda message: None)

    started = time.monotonic()
    requests = 0
    last_status = STATUS_NO_RESPONSE
    last_endpoint: str | None = None

    for endpoint in endpoints:
        for attempt in range(1, attempts + 1):
            requests += 1
            try:
                status, body = sender(endpoint, data, timeout)
            except Exception as exc:  # noqa: BLE001 - a crashing transport is a failed attempt
                status, body = STATUS_NO_RESPONSE, b""
                note(f"transport error on {endpoint} (attempt {attempt}): {exc}")
            status = int(status)
            last_status, last_endpoint = status, endpoint
            if status == 200 and (accept is None or accept(body)):
                return FetchOutcome(
                    status=200,
                    endpoint=endpoint,
                    body=bytes(body),
                    requests=requests,
                    seconds=round(time.monotonic() - started, 3),
                    usable=True,
                )
            if status == 200:
                note(f"{endpoint}: HTTP 200 with an unusable body, treated as a retryable failure")
            elif status not in RETRY_STATUSES and status != STATUS_NO_RESPONSE:
                note(f"{endpoint}: HTTP {status}, not retried (the query itself was refused)")
                break
            if attempt < attempts:
                note(f"{endpoint}: HTTP {status}, sleeping {backoff:g} s before the retry")
                sleeper(backoff)

    return FetchOutcome(
        status=last_status,
        endpoint=last_endpoint,
        body=b"",
        requests=requests,
        seconds=round(time.monotonic() - started, 3),
        usable=False,
    )


# ---------------------------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------------------------


def _utc_now() -> str:
    """Wall clock, second resolution, `Z`-suffixed: the format `retrieved_utc` already uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _previous_tiles(path: Path) -> dict[str, Mapping[str, Any]]:
    """The `tiles[]` of an existing manifest, keyed by id (empty when absent or unreadable).

    Used for one thing: a skipped tile keeps the endpoint that actually fetched it, so re-running
    the fetch over a complete directory does not erase where the data came from.
    """
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    tiles = manifest.get("tiles") if isinstance(manifest, Mapping) else None
    if not isinstance(tiles, list):
        return {}
    return {
        str(tile.get("id")): tile
        for tile in tiles
        if isinstance(tile, Mapping) and tile.get("id") is not None
    }


def write_manifest(manifest: Mapping[str, Any], path: Path) -> int:
    """Write `manifest.json` atomically: temp file in the same directory, then `os.replace`.

    `sort_keys=True` is not cosmetic. The manifest is what an operator diffs between two runs, and
    a dict-order-dependent file would make every run look changed. The temp file lives beside the
    target so the replace is a rename on the same filesystem and a reader never sees half a file.
    """
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return len(text.encode("utf-8"))


def _tile_record(tile: Tile, **fields: Any) -> dict[str, Any]:
    """One `tiles[]` entry. `bbox` is rounded to the 7 decimals the query was printed with."""
    record: dict[str, Any] = {
        "id": tile.id,
        "bbox": [round(value, 7) for value in tile.bbox],
        "status": STATUS_NO_RESPONSE,
        "attempts": 0,
        "endpoint": None,
        "bytes": 0,
        "elements": 0,
        "sha256": None,
        "seconds": 0.0,
        "skipped": False,
        "osm_timestamp": None,
    }
    record.update(fields)
    return record


def _stamp_range(observed: Sequence[str]) -> list[str] | None:
    """The distinct tile stamps as `[oldest]` or `[oldest, newest]`; `None` when none was read.

    One element means the tiles agree, which is a fact worth stating and not a fabricated span.
    `osm_context._osm_data_stamp` reads the last element, so both shapes resolve to the newest
    stamp the run actually saw.
    """
    if not observed:
        return None
    return [observed[0], observed[-1]] if len(observed) > 1 else [observed[0]]


# ---------------------------------------------------------------------------------------------
# Politeness state, shared by every case of one run
# ---------------------------------------------------------------------------------------------


@dataclass
class RequestGate:
    """Whether this run has already issued a request, **shared across cases**.

    The pause is politeness between two consecutive requests to a shared server, so it cannot be
    counted per case: the last tile of one case and the first tile of the next are otherwise issued
    back to back, which is the burst the pause exists to prevent. `main` creates one gate for the
    whole invocation; a direct `fetch_case` call gets a fresh one, i.e. the single-case behaviour.
    """

    sent: bool = False

    def wait(self, pause: float, sleep: Sleeper) -> None:
        """Sleep before the next request when one has already gone out, then mark the request."""
        if self.sent:
            sleep(pause)
        self.sent = True


# ---------------------------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------------------------


def fetch_case(
    case_id: str,
    bbox: tuple[float, float, float, float],
    *,
    root: Path | None = None,
    out: Path | None = None,
    force: bool = False,
    include_buildings: bool = False,
    pause: float = DEFAULT_PAUSE_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    endpoints: Sequence[str] = ENDPOINTS,
    transport: Transport | None = None,
    sleep: Sleeper | None = None,
    retrieved_utc: str | None = None,
    max_tile_deg: float = MAX_TILE_DEG,
    gate: RequestGate | None = None,
    log: Logger | None = None,
) -> dict[str, Any]:
    """Fetch every tile of one case and write its manifest; return the manifest.

    The bbox is a plain tuple (not an `AOI`) so the grid, the queries and the manifest can be
    exercised without the AOI loader; `main` is what resolves the case configuration.
    """
    say: Logger = log if log is not None else print
    sleeper: Sleeper = sleep if sleep is not None else time.sleep
    directory = Path(out) if out is not None else OSM.context_dir(case_id, root)
    tiles_dir = directory / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    previous = _previous_tiles(directory / "manifest.json")
    grid = tile_grid(case_id, bbox, max_deg=max_tile_deg)
    stamp = retrieved_utc or _utc_now()

    pause_gate: RequestGate = gate if gate is not None else RequestGate()
    records: list[dict[str, Any]] = []
    stamps: list[str] = []
    requests_sent = 0

    for tile in grid:
        path = tiles_dir / f"{tile.id}.json"
        if path.is_file() and not force:
            body = path.read_bytes()
            payload = _tile_payload(body)
            tile_stamp = _payload_stamp(payload)
            record = _tile_record(
                tile,
                status=200 if payload is not None else STATUS_NO_RESPONSE,
                attempts=0,
                endpoint=(previous.get(tile.id) or {}).get("endpoint"),
                bytes=len(body),
                elements=len(payload.get("elements") or []) if payload is not None else 0,
                sha256=hashlib.sha256(body).hexdigest(),
                seconds=0.0,
                skipped=True,
                osm_timestamp=tile_stamp,
            )
            records.append(record)
            say(
                f"[{case_id}] {tile.id} skip ({len(body)} B already on disk; --force to refetch)"
            )
            if tile_stamp:
                stamps.append(tile_stamp)
            continue

        query = build_query(tile.bbox, include_buildings=include_buildings)
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        # One request at a time: the pause is between requests, not before the first, and the gate
        # carries that state across cases so two cases never burst either.
        pause_gate.wait(pause, sleeper)
        outcome = fetch_query(
            data,
            endpoints=endpoints,
            attempts=attempts,
            backoff=backoff,
            timeout=timeout,
            transport=transport,
            sleep=sleeper,
            accept=lambda body: _tile_payload(body) is not None,
            log=say,
        )
        requests_sent += outcome.requests
        payload = _tile_payload(outcome.body) if outcome.ok else None

        if payload is not None:
            path.write_bytes(outcome.body)  # verbatim: the manifest sha256 is this file's hash
            # Hash the file a reader will parse, not the socket buffer. The skip path hashes the
            # file for the same reason; the two must agree or the audit trail is decorative.
            on_disk = path.read_bytes()
            tile_stamp = _payload_stamp(payload)
            elements = len(payload.get("elements") or [])
            record = _tile_record(
                tile,
                status=200,
                attempts=outcome.requests,
                endpoint=outcome.endpoint,
                bytes=len(on_disk),
                elements=elements,
                sha256=hashlib.sha256(on_disk).hexdigest(),
                seconds=outcome.seconds,
                osm_timestamp=tile_stamp,
            )
            say(
                f"[{case_id}] {tile.id} fetch 200 {len(on_disk)} B, {elements} elements, "
                f"{outcome.seconds:.1f} s, {outcome.endpoint}"
            )
            if tile_stamp:
                stamps.append(tile_stamp)
        else:
            # A 200 we cannot parse is "no usable answer", not a 200 tile: the file is absent, so
            # the record must not claim 200 or `extract_record` would count a missing tile as ok.
            status = outcome.status if outcome.status != 200 else STATUS_NO_RESPONSE
            record = _tile_record(
                tile,
                status=status,
                attempts=outcome.requests,
                endpoint=outcome.endpoint,
                seconds=outcome.seconds,
            )
            detail = "HTTP 200 with an unusable body" if outcome.status == 200 else f"HTTP {outcome.status}"
            say(
                f"[{case_id}] {tile.id} FAIL {detail} after {outcome.requests} request(s) on "
                f"{outcome.endpoint}; no tile written"
            )
            if force and path.is_file():
                say(
                    f"[{case_id}] {tile.id} keeping the previous file from an earlier run: it is "
                    "not covered by this manifest's request and may be stale"
                )
        records.append(record)

    ok = [record for record in records if record["status"] == 200]
    totals = {
        "tiles": len(records),
        "ok": len(ok),
        "failed": len(records) - len(ok),
        "skipped": sum(1 for record in records if record["skipped"]),
        "bytes": sum(int(record["bytes"]) for record in ok),
        "elements": sum(int(record["elements"]) for record in ok),
        "requests": requests_sent,
        "seconds": round(sum(float(record["seconds"]) for record in records), 3),
    }

    observed = sorted(set(stamps))
    if len(observed) > 1:
        # Tiles queried minutes apart can be served from different planet states; publishing one
        # of them as "the" snapshot would be a claim the manifest cannot support.
        say(
            f"[{case_id}] warning: tiles carry {len(observed)} different "
            f"osm3s.timestamp_osm_base values ({observed[0]} .. {observed[-1]}); the manifest "
            f"publishes the latest ({observed[-1]}) as timestamp_osm_base and records the range, "
            "without claiming the tiles share one snapshot"
        )

    manifest: dict[str, Any] = {
        "case_id": case_id,
        "bbox": [float(value) for value in bbox],
        "retrieved_utc": stamp,
        # Latest, never first: `osm_context._osm_data_stamp` prefers this key, so filling it with
        # the oldest of several snapshots would publish a stale planet state as the snapshot.
        "timestamp_osm_base": observed[-1] if observed else None,
        "osm_timestamp_base_range": _stamp_range(observed),
        "source": OSM.SOURCE,
        "tiles": records,
        "totals": totals,
        "request": {
            "endpoints": list(endpoints),
            "query_template": _query_lines(BBOX_PLACEHOLDER, include_buildings=include_buildings),
            "selector_keys": list(QUERY_KEYS),
            "include_buildings": bool(include_buildings),
            "max_tile_deg": max_tile_deg,
            "pause_seconds": pause,
            "attempts": attempts,
            "backoff_seconds": backoff,
            "timeout_seconds": timeout,
            "user_agent": USER_AGENT,
        },
    }
    path = directory / "manifest.json"
    size = write_manifest(manifest, path)
    say(
        f"[{case_id}] manifest -> {path} ({size} B): {totals['ok']} ok, {totals['failed']} failed, "
        f"{totals['skipped']} skipped, {totals['elements']} elements"
    )
    return manifest


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def dry_run_lines(
    case_id: str,
    bbox: tuple[float, float, float, float],
    *,
    include_buildings: bool = False,
    endpoints: Sequence[str] = ENDPOINTS,
    max_tile_deg: float = MAX_TILE_DEG,
) -> list[str]:
    """The plan of one case: one header line plus one line per tile, no request, no write.

    The first endpoint of the ladder is what the line shows, because a dry run has no way to know
    which endpoint would answer; the ladder is printed in the header when there is more than one.
    """
    grid = tile_grid(case_id, bbox, max_deg=max_tile_deg)
    widest = max(tile.size[0] for tile in grid)
    tallest = max(tile.size[1] for tile in grid)
    header = (
        f"[{case_id}] bbox [W,S,E,N]={[float(value) for value in bbox]} -> {len(grid)} tile(s), "
        f"largest {widest:.6f} x {tallest:.6f} deg (limit {max_tile_deg:g} deg per side), "
        f"buildings={'yes' if include_buildings else 'no'}, ladder={' -> '.join(endpoints)}"
    )
    lines = [header]
    for tile in grid:
        lines.append(
            f"[{case_id}] {tile.id} {overpass_bbox(tile.bbox)} POST {endpoints[0]}"
        )
    return lines


def load_case_aois(
    case_ids: Sequence[str] | None, config_path: Path = CONFIG_PATH
) -> dict[str, AOI]:
    """Resolve the selected cases from `config/study_cases.yaml` through the validated loader.

    The config is the only place a case is declared, so the default (`case_ids=None`) is "every
    case in the file" and not a list pasted here that could outlive a change to the YAML.
    """
    if not config_path.is_file():
        raise AOIError(f"case config not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cases = (raw or {}).get("cases") if isinstance(raw, Mapping) else None
    if not isinstance(cases, Mapping) or not cases:
        raise AOIError(f"{config_path}: no 'cases' mapping found")
    wanted = list(case_ids) if case_ids else list(cases)
    unknown = [case_id for case_id in wanted if case_id not in cases]
    if unknown:
        raise AOIError(
            f"undeclared case(s) {unknown}; config declares {sorted(cases)}"
        )
    return {case_id: load_aoi(case_id, cases[case_id]) for case_id in wanted}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/ingest/fetch_context.py",
        description=(
            "Fetch the cached Overpass extract under data/raw/<case>/context/ that "
            "pipeline/ingest/osm_context.py reads: one tile at a time, at most 0.15 deg per side."
        ),
    )
    parser.add_argument(
        "--case",
        action="append",
        help="case id (repeatable); default: every case in config/study_cases.yaml",
    )
    parser.add_argument("--root", default=str(OSM.CASE_ROOT), help="data root (default data/raw)")
    parser.add_argument(
        "--out",
        help="write the extract here instead of <root>/<case>/context (single --case; for tests)",
    )
    parser.add_argument(
        "--force", action="store_true", help="re-fetch tiles that already exist on disk"
    )
    parser.add_argument(
        "--include-buildings",
        action="store_true",
        help="also select building=* (opt-in: measured 39 344 ways / 21.47 MB in one 3.3 x 3.9 km box)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the tile grid and the request target per tile, send nothing, write nothing",
    )
    parser.add_argument(
        "--pause", type=float, default=DEFAULT_PAUSE_SECONDS, help="seconds between tiles"
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=DEFAULT_ATTEMPTS,
        help="maximum requests per endpoint per tile",
    )
    parser.add_argument(
        "--backoff", type=float, default=DEFAULT_BACKOFF_SECONDS, help="seconds before a retry"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="seconds per request"
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        help="override the endpoint ladder (repeatable, tried in order)",
    )
    parser.add_argument("--config", default=str(CONFIG_PATH), help="study cases YAML")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        aois = load_case_aois(args.case, Path(args.config))
    except (AOIError, OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out and len(aois) > 1:
        parser.error("--out takes a single --case: one directory cannot hold several extracts")
    if args.pause < 0 or args.backoff < 0 or args.timeout <= 0:
        parser.error("--pause/--backoff must be >= 0 and --timeout > 0")
    if args.attempts < 1:
        parser.error("--attempts must be >= 1")
    endpoints = tuple(args.endpoint) if args.endpoint else ENDPOINTS

    if args.dry_run:
        for case_id, aoi in aois.items():
            for line in dry_run_lines(
                case_id, aoi.bbox, include_buildings=args.include_buildings, endpoints=endpoints
            ):
                print(line)
        return 0

    stamp = _utc_now()  # one wall clock per run: the tiles are not a deterministic artefact
    gate = RequestGate()  # one gate per run: the pause spans cases, not just tiles
    failures = 0
    for case_id, aoi in aois.items():
        manifest = fetch_case(
            case_id,
            aoi.bbox,
            root=Path(args.root),
            out=Path(args.out) if args.out else None,
            force=args.force,
            include_buildings=args.include_buildings,
            pause=args.pause,
            attempts=args.attempts,
            backoff=args.backoff,
            timeout=args.timeout,
            endpoints=endpoints,
            retrieved_utc=stamp,
            gate=gate,
        )
        failures += int(manifest["totals"]["failed"])
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
