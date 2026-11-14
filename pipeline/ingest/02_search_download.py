#!/usr/bin/env python3
"""02_search_download.py — P1 ingest: prove what real SAR data exists, then fetch it.

Fallback chain (README §7):
    NISAR GUNW > OPERA DISP-S1 > Sentinel-1 (HyP3 SLC) > EGMS (EU only)

Search is unauthenticated (ASF SearchAPI public). Download needs Earthdata credentials and
fails closed with an actionable message when they are missing.

Search also derives *stacks* — one per acquisition geometry `(track, flight direction)` — and
persists them so `download --stack recommended` fetches a coherent single-geometry series
instead of the first three search hits (which can span several tracks).

Usage
-----
  ./.venv/bin/python pipeline/ingest/02_search_download.py search
  ./.venv/bin/python pipeline/ingest/02_search_download.py report
  ./.venv/bin/python pipeline/ingest/02_search_download.py download --city malaga \
      --source nisar_gunw --stack recommended --max-gb 6 --dry-run
  ./.venv/bin/python pipeline/ingest/02_search_download.py download --city malaga \
      --source nisar_gunw --stack 020 --max-gb 6

Exit codes: 2 usage, 3 credentials, 4 product/geometry guard, 5 budget guard,
6 foreign partial, 7 HTTP 403 (granule restricted).

Output
------
  data/processed/_availability/{city}.json   ← acts on this tonight
  data/raw/{city}/{source}/                  ← granules + provenance.json (gitignored)
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
AOIS = pathlib.Path(__file__).with_name("aois.json")
ASF_API = "https://api.daac.asf.alaska.edu/services/search/param"
TIMEOUT = 45
RETRIES = 2
CONTRACT_VERSION = "1.0"

# Sentinel-1 is the only source with a long public archive; keep the windows honest.
SINCE_5Y = "2019-01-01T00:00:00Z"
OPEN_ENDED = "2035-12-31T23:59:59Z"

# Exit codes are part of the runbook contract (docs/first-granule-runbook.md).
RC_OK = 0
RC_USAGE = 2
RC_CREDENTIALS = 3
RC_PRODUCT_GUARD = 4
RC_BUDGET_GUARD = 5
RC_PARTIAL_GUARD = 6
RC_ACCESS = 7

CHUNK = 1 << 20  # 1 MiB streaming reads: a multi-GB granule is never buffered in memory

# --- stack derivation bounds -----------------------------------------------------------------
# A fetchable unit is a whole stack: cap the ids so one download is a bounded multi-GB job.
STACK_GRANULE_LIMIT = 12
MAX_STACKS = 10
GRANULE_ID_CAP = 120


def _science_min_fit_points() -> int:
    """Read ``MIN_FIT_POINTS`` from ``pipeline/science/transforms.py``.

    The threshold belongs to the science layer, so it is read from there instead of being
    forked here; a missing file falls back to the documented value so ingest still runs
    standalone (``python pipeline/ingest/02_search_download.py`` puts only its own directory on
    ``sys.path``, which makes importing the science module unsafe).
    """
    path = ROOT / "pipeline" / "science" / "transforms.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 4
    match = re.search(r"^MIN_FIT_POINTS\s*=\s*(\d+)", text, re.MULTILINE)
    return int(match.group(1)) if match else 4


#: Epochs a trend fit needs (``transforms.MIN_FIT_POINTS``). A download that yields fewer is
#: refused by ``process_case``, so the recommendation must say so instead of pretending.
MIN_FIT_POINTS = _science_min_fit_points()

# =============================================================================================
# Granule-name grammar (single source of truth: parse once, reuse for stacks and the guard)
# =============================================================================================

_TIMESTAMP = re.compile(r"^(\d{8})T(\d{4,6})$")
_LEVEL = re.compile(r"^L\d$")
_PRODUCT = re.compile(r"^[A-Z0-9]{2,6}$")
_TRACK = re.compile(r"^\d{3}[A-Z]?$")
_DIRECTION = {"A": "ASCENDING", "D": "DESCENDING"}


def _iso_timestamp(token: str) -> str | None:
    """`20261008T011329` -> `2026-10-08T01:13:29Z`; anything else -> None (no guessing)."""
    match = _TIMESTAMP.match(token)
    if not match:
        return None
    day, clock = match.groups()
    clock = (clock + "000000")[:6]
    return f"{day[:4]}-{day[4:6]}-{day[6:]}T{clock[:2]}:{clock[2:4]}:{clock[4:6]}Z"


@dataclass(frozen=True)
class ParsedGranule:
    """Everything the identity guard and stack derivation need, parsed from the name only."""

    raw: str
    level: str | None = None
    product: str | None = None
    track: str | None = None
    direction_code: str | None = None
    reference_start: str | None = None
    reference_stop: str | None = None
    secondary_start: str | None = None
    secondary_stop: str | None = None

    @property
    def direction(self) -> str | None:
        return _DIRECTION.get(self.direction_code or "")

    @property
    def flight_direction(self) -> str | None:
        # ASF calls it flightDirection; keep both spellings so callers do not translate.
        return self.direction


def parse_granule(granule_id: str | None) -> ParsedGranule:
    """Parse a NISAR or Sentinel-1 granule name. Unknown names parse to an all-None record.

    This is the ONE grammar in the module: `derive_stacks` groups with it and
    `evaluate_granule` gates on it, so a stack can never disagree with the guard.
    """
    raw = granule_id or ""
    tokens = raw.split("_")
    stamps = [iso for iso in (_iso_timestamp(token) for token in tokens) if iso]

    if tokens and tokens[0].startswith("S1"):
        # S1A_IW_SLC__1SDV_20260623T111515_...  (the double underscore yields an empty token)
        product = next((token for token in tokens if token == "SLC"), None)
        code = tokens[-1][:1] if tokens and tokens[-1][:1] in _DIRECTION else None
        return ParsedGranule(
            raw=raw,
            level="S1",
            product=product,
            direction_code=code,
            reference_start=stamps[0] if stamps else None,
            reference_stop=stamps[1] if len(stamps) > 1 else None,
        )

    level = tokens[1] if len(tokens) > 1 and _LEVEL.match(tokens[1]) else None
    product = tokens[3] if len(tokens) > 3 and _PRODUCT.match(tokens[3]) else None
    code = tokens[6] if len(tokens) > 6 and tokens[6] in _DIRECTION else None
    if code is None:
        code = next((token for token in tokens[:10] if token in _DIRECTION), None)
    track = tokens[7] if len(tokens) > 7 and _TRACK.match(tokens[7]) else None

    return ParsedGranule(
        raw=raw,
        level=level,
        product=product,
        track=track,
        direction_code=code,
        reference_start=stamps[0] if stamps else None,
        reference_stop=stamps[1] if len(stamps) > 1 else None,
        secondary_start=stamps[2] if len(stamps) > 2 else None,
        secondary_stop=stamps[3] if len(stamps) > 3 else None,
    )


def _even_sample(items: list[str], limit: int) -> list[str]:
    """Evenly sample `limit` ids across `items`, always keeping the first and the last."""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(index * step)] for index in range(limit)]


# --- epoch chaining --------------------------------------------------------------------------

def _epoch_day(timestamp: str | None) -> str | None:
    """The UTC day of an acquisition timestamp: the unit `process_case._chain` compares.

    `process_case` compares `reference_date`/`secondary_date`, i.e. the *day* of each
    acquisition, not the second. Two granules chain when the next one's reference acquisition
    falls on the same day as the previous one's secondary acquisition; the second-level drift
    between repeated orbits (e.g. `...T011330` vs `...T011331`) must not break that chain.
    """
    return timestamp[:10] if timestamp else None


def _run_epochs(run: list[tuple[str, ParsedGranule]]) -> int:
    """Distinct epochs one connected run of granules gives.

    A run of `k` chained pairs yields `k + 1` epochs (the first reference plus every secondary);
    a lone unpaired granule (Sentinel-1 SLC) yields its single reference as one epoch.
    """
    epochs: set[str] = set()
    for _, parsed in run:
        for stamp in (parsed.reference_start, parsed.secondary_start):
            day = _epoch_day(stamp)
            if day:
                epochs.add(day)
    return len(epochs)


def _connected_runs(members: list[tuple[str, ParsedGranule]]) -> list[list[tuple[str, ParsedGranule]]]:
    """Split chronologically ordered members into maximal chained runs.

    Adjacent members belong to the same run iff the earlier granule's secondary acquisition and
    the later granule's reference acquisition are the same acquisition (same UTC day). A missing
    secondary (Sentinel-1 SLC, an unparsed name) starts a new run: nothing proves it chains.
    """
    runs: list[list[tuple[str, ParsedGranule]]] = []
    current: list[tuple[str, ParsedGranule]] = []
    previous_secondary: str | None = None
    for member in members:
        _, parsed = member
        reference = _epoch_day(parsed.reference_start)
        if current and reference is not None and reference == previous_secondary:
            current.append(member)
        else:
            if current:
                runs.append(current)
            current = [member]
        previous_secondary = _epoch_day(parsed.secondary_start)
    if current:
        runs.append(current)
    return runs


def _best_download_run(members: list[tuple[str, ParsedGranule]]) -> tuple[list[tuple[str, ParsedGranule]], int]:
    """The contiguous chain a download should fetch, and how many epochs it gives.

    Selection rule. Only *connected* runs count (each granule's secondary acquisition is the next
    granule's reference, so `k` pairs give `k + 1` epochs and `process_case._chain` accepts the
    stack). Among those runs take the one with the most epochs — `min(STACK_GRANULE_LIMIT, n)`
    consecutive granules by reference date whenever a chain that long exists — and break ties by
    the **most recent** run (latest reference timestamp). A run longer than the limit is cut to
    its most recent `STACK_GRANULE_LIMIT` granules, which is still a chain and keeps the epoch
    maximum at `STACK_GRANULE_LIMIT + 1`.

    Non-interferometric stacks (no granule carries a secondary acquisition, e.g. Sentinel-1 SLC)
    have no pairing to prove: one granule is one epoch, so the run is simply the most recent
    `min(STACK_GRANULE_LIMIT, n)` consecutive granules and the epoch count is the distinct
    reference days.
    """
    if not members:
        return [], 0
    if not any(parsed.secondary_start for _, parsed in members):
        run = members[-STACK_GRANULE_LIMIT:]
        return run, _run_epochs(run)

    runs = _connected_runs(members)
    best = max(
        runs,
        key=lambda run: (len(run), run[-1][1].reference_start or run[-1][0]),
    )
    if len(best) > STACK_GRANULE_LIMIT:
        best = best[-STACK_GRANULE_LIMIT:]
    return best, _run_epochs(best)


def _stack_epochs(members: list[tuple[str, ParsedGranule]]) -> tuple[int, int]:
    """`(n_epochs, connected_epochs)` for the *whole* stack.

    `n_epochs` counts every distinct acquisition day the stack carries; `connected_epochs` is the
    longest chain reachable inside it (what the best download run would give before the cap).
    """
    epochs = {
        day
        for _, parsed in members
        for day in (_epoch_day(parsed.reference_start), _epoch_day(parsed.secondary_start))
        if day
    }
    runs = _connected_runs(members)
    connected = max((_run_epochs(run) for run in runs), default=0)
    return len(epochs), connected


def derive_stacks(granules: list[dict]) -> list[dict]:
    """Group granules by `(track, flight direction)` and describe each stack.

    Every stack *previews* its window: `date_range` is the full `[first, last]` reference date,
    `full_date_range` repeats it, and `granule_ids` is an even sample of at most
    `STACK_GRANULE_LIMIT` ids so the preview spans the window (first and last always included).
    `sample_purpose` is `"preview"` here; `recommend_stack` replaces the chosen stack's sample
    with the contiguous chain a download actually needs (`sample_purpose: "download"`).

    Each stack also carries the whole stack's truth — `n_epochs` (every distinct acquisition day)
    and `connected_epochs` (the longest chain inside the stack) — so a narrowed download window
    never has to be confused with the stack's real span. Sorted largest first, then most recent.
    """
    groups: dict[tuple[str | None, str | None], list[tuple[str, ParsedGranule]]] = {}
    for granule in granules:
        granule_id = granule.get("granuleName") or granule.get("id") or ""
        parsed = parse_granule(granule_id)
        key = (parsed.track, parsed.direction)
        groups.setdefault(key, []).append((granule_id, parsed))

    stacks: list[dict] = []
    for (track, direction), members in groups.items():
        members.sort(
            key=lambda item: item[1].reference_start
            or item[0]  # unparseable names keep a stable, name-based order
        )
        ids = [granule_id for granule_id, _ in members]
        starts = [parsed.reference_start for _, parsed in members if parsed.reference_start]
        date_range = [min(starts), max(starts)] if starts else None

        days = sorted({dt.date.fromisoformat(stamp[:10]) for stamp in starts})
        gaps = [(later - earlier).days for earlier, later in zip(days, days[1:])]
        mean_interval = float(statistics.median(gaps)) if gaps else None

        sampled = _even_sample(ids, STACK_GRANULE_LIMIT)
        n_epochs, connected_epochs = _stack_epochs(members)
        stacks.append(
            {
                "track": track,
                "direction": direction,
                "n_granules": len(members),
                "date_range": date_range,
                "full_date_range": list(date_range) if date_range else None,
                "mean_interval_days": mean_interval,
                "granule_ids": sampled,
                "granule_ids_truncated": len(sampled) < len(ids),
                "sample_purpose": "preview",
                "n_epochs": n_epochs,
                "connected_epochs": connected_epochs,
                # internal: the full ordered member list, never persisted (see `_public`)
                "_members": members,
            }
        )

    # stable two-key sort: most recent first, then largest first (the dominant key)
    stacks.sort(key=lambda stack: (stack["date_range"] or ["", ""])[1], reverse=True)
    stacks.sort(key=lambda stack: stack["n_granules"], reverse=True)
    return stacks


def _public(stack: dict) -> dict:
    """A stack dict without the internal (`_`-prefixed) working fields."""
    return {key: value for key, value in stack.items() if not key.startswith("_")}


def _choose_stack(stacks: list[dict]) -> dict | None:
    """The stack to fetch: the largest *identified* geometry, else the largest group."""
    if not stacks:
        return None
    identified = [stack for stack in stacks if stack["track"] is not None]
    return identified[0] if identified else stacks[0]


def _download_sample(stack: dict, stacks: list[dict]) -> dict:
    """Replace a preview stack's ids with the contiguous connected chain a download needs.

    `date_range` becomes the *sampled run's* window (what `granule_ids` actually covers, which is
    what `tests/test_availability_evidence.py` checks); `full_date_range` keeps the whole stack's
    window. `download_epochs` is the epoch count of this sample (`len(granule_ids) + 1` for a
    chain) and `warning` is set, never swallowed, when it cannot reach `MIN_FIT_POINTS`.
    """
    members = stack.get("_members") or [
        (granule_id, parse_granule(granule_id))
        for granule_id in stack.get("granule_ids") or []
    ]
    run, epochs = _best_download_run(members)
    run_ids = [granule_id for granule_id, _ in run]
    starts = [parsed.reference_start for _, parsed in run if parsed.reference_start]
    sample = _public(stack)
    sample.update(
        {
            "date_range": [starts[0], starts[-1]] if starts else None,
            "granule_ids": run_ids,
            "granule_ids_truncated": len(run_ids) < stack["n_granules"],
            "sample_purpose": "download",
            "download_epochs": epochs,
            "min_fit_points": MIN_FIT_POINTS,
            "epoch_sufficient": epochs >= MIN_FIT_POINTS,
            "warning": _epoch_warning(stack, epochs, stacks),
        }
    )
    return sample


def _epoch_warning(stack: dict, epochs: int, stacks: list[dict]) -> str | None:
    """Say plainly when the recommended download cannot feed a fit, and name a usable stack."""
    if epochs >= MIN_FIT_POINTS:
        return None
    head = (
        f"this stack yields {epochs} connected epoch(s), below MIN_FIT_POINTS={MIN_FIT_POINTS}: "
        "`process_case` would refuse the download"
    )
    usable: list[tuple[int, dict]] = []
    for other in stacks:
        if other is stack or other.get("track") is None:
            continue
        other_epochs = _best_download_run(other.get("_members") or [])[1]
        if other_epochs >= MIN_FIT_POINTS:
            usable.append((other_epochs, other))
    if not usable:
        return f"{head}. No stack in this report reaches the threshold; re-run `search` over a longer window."
    other_epochs, other = max(
        usable,
        key=lambda item: (item[0], (item[1].get("date_range") or ["", ""])[1]),
    )
    return (
        f"{head}. Use `--stack {other['track']}` (track {other['track']}/"
        f"{other['direction']}, {other_epochs} epoch(s)) instead."
    )


def recommend_stack(stacks: list[dict]) -> dict | None:
    """Pick the stack to fetch (largest identified geometry) with a downloadable *chain*.

    The recommendation and the `stacks[]` entry it names carry the same sample: a contiguous,
    connected run selected by `_best_download_run` (max epochs, ties to the most recent run).
    `why` states what was picked; `warning` states when it still cannot reach `MIN_FIT_POINTS`.
    """
    chosen = _choose_stack(stacks)
    if chosen is None:
        return None
    sample = _download_sample(chosen, stacks)
    if chosen["track"] is not None:
        span = (
            f"{sample['date_range'][0][:7]} .. {sample['date_range'][1][:7]}"
            if sample["date_range"]
            else "unknown window"
        )
        detail = (
            f"{len(sample['granule_ids'])} of {sample['n_granules']} granules, "
            f"{sample['download_epochs']} connected epoch(s), {span}"
        )
        why = (
            f"largest single-geometry stack (track {chosen['track']}/{chosen['direction']}); "
            f"{detail}"
        )
        return {**sample, "why": why}

    why = (
        "no granule name carried a track/direction; this group cannot be verified as one "
        f"geometry ({chosen['n_granules']} granule(s)) — re-run `search` before downloading"
    )
    return {**sample, "why": why}


# =============================================================================================
# Search summary
# =============================================================================================


def _ordered_by_reference(granules: list[dict]) -> list[dict]:
    def key(granule: dict) -> tuple[str, str]:
        parsed = parse_granule(granule.get("granuleName") or granule.get("id"))
        return (parsed.reference_start or granule.get("startTime") or "", granule.get("granuleName") or "")

    return sorted(granules, key=key)


def _retained_ids(ordered: list[dict], preferred: set[str], cap: int) -> list[str]:
    """The capped, chronological id sample a source persists.

    `preferred` holds the sampled ids of every published stack. They must survive the cap: a
    stack's `granule_ids` are sampled from *its own* granules (first and last acquisition
    included), so they span its `date_range`, and `download` resolves url+size for stack ids
    through the source-level `granules` records. The rest of the cap is filled from the global
    even sample (window endpoints first) so the source-level evidence still covers the whole
    search window. Stack ids win over the cap: they are already bounded by
    `MAX_STACKS * STACK_GRANULE_LIMIT`, and dropping one would leave `download` without a
    url+size record for a published id.
    """
    names = [g.get("granuleName") or "" for g in ordered]
    keep = {name for name in names if name in preferred}
    if len(keep) < cap:
        global_sample = _even_sample(names, cap)
        if global_sample:
            keep.update((global_sample[0], global_sample[-1]))
        for granule_id in global_sample:
            if len(keep) >= cap:
                break
            keep.add(granule_id)
    return [name for name in names if name in keep]


def summarise(granules: list[dict], requested: int) -> dict:
    if not granules:
        return {
            "n_granules": 0,
            "n_granules_truncated": False,
            "date_range": None,
            "total_bytes": None,
            "total_gb": None,
            "sample_granules": [],
            "granule_ids": [],
            "granule_ids_truncated": False,
            "granules": [],
            "stacks": [],
            "recommended_stack": None,
            "platforms": [],
            "deltas_available": False,
        }
    starts = sorted(g["startTime"] for g in granules if g.get("startTime"))
    sizes = [g.get("sizeMB") or 0 for g in granules]
    total_mb = sum(s for s in sizes if s)
    ordered = _ordered_by_reference(granules)

    all_stacks = derive_stacks(ordered)
    recommended = recommend_stack(all_stacks)
    chosen = _choose_stack(all_stacks)
    published: list[dict] = []
    for stack in all_stacks:
        if stack is chosen and recommended is not None:
            # the recommendation's own entry in `stacks[]` IS the download sample, so the two
            # never disagree about which granules (and which window) the fetch covers
            published.append(
                {key: value for key, value in recommended.items() if key != "why"}
            )
        else:
            published.append(_public(stack))
    # Re-rank after sampling: `date_range` now describes the sample, so the documented
    # largest-first/most-recent order must be recomputed against what is actually published.
    published.sort(key=lambda stack: (stack["date_range"] or ["", ""])[1], reverse=True)
    published.sort(key=lambda stack: stack["n_granules"], reverse=True)
    stacks = published[:MAX_STACKS]

    # Bound the persisted id list without ever dropping a published stack id: each stack keeps
    # its own sample, so its ids span its window and `download` has a url+size record for
    # each of them (the records below are built from the same slice).
    preferred = {
        granule_id
        for stack in ([*stacks, recommended] if recommended else stacks)
        for granule_id in stack.get("granule_ids") or []
    }
    retained = _retained_ids(ordered, preferred, GRANULE_ID_CAP)
    retained_ids = set(retained)
    retained_records = [
        g for g in ordered if (g.get("granuleName") or "") in retained_ids
    ]

    return {
        "n_granules": len(granules),
        "n_granules_truncated": len(granules) >= requested,
        "date_range": [starts[0], starts[-1]] if starts else None,
        "total_bytes": int(total_mb * 1e6) if total_mb else None,
        "total_gb": round(total_mb / 1024, 2) if total_mb else None,
        "sample_granules": [
            {
                "id": g.get("granuleName"),
                "start": g.get("startTime"),
                "stop": g.get("stopTime"),
                "size_mb": g.get("sizeMB"),
                "url": g.get("downloadUrl"),
                "flight_direction": g.get("flightDirection"),
                "polarization": g.get("polarization"),
            }
            for g in granules[:3]
        ],
        "granule_ids": retained,
        "granule_ids_truncated": len(retained) < len(ordered),
        "granules": [
            {
                "id": g.get("granuleName"),
                "url": g.get("downloadUrl"),
                "size_mb": g.get("sizeMB"),
            }
            for g in retained_records
        ],
        "stacks": stacks,
        "recommended_stack": recommended,
        "platforms": sorted({g.get("platform", "?") for g in granules}),
        "deltas_available": None,
    }


def polygon_wkt(bbox: list[float]) -> str:
    w, s, e, n = bbox
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def http_json(url: str) -> tuple[int, Any]:
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SAR_NASA/1.0 ingest"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as fh:
                return fh.status, json.load(fh)
        except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
            body = exc.read()[:400].decode("utf-8", "replace")
            return exc.code, {"_http_error": body}
        except Exception as exc:  # noqa: BLE001 - network flakiness is data, not a crash
            last = exc
            time.sleep(1.5 * (attempt + 1))
    return 0, {"_error": str(last)}


def asf_search(bbox: list[float], params: dict[str, str], max_results: int = 250) -> tuple[int, Any]:
    query = dict(params)
    query.update(
        {
            "output": "json",
            "maxResults": str(max_results),
            "intersectsWith": polygon_wkt(bbox),
        }
    )
    url = f"{ASF_API}?{urllib.parse.urlencode(query)}"
    status, payload = http_json(url)
    granules = []
    if isinstance(payload, list) and payload:
        granules = payload[0] if isinstance(payload[0], list) else payload
    return status, granules


def probe(aoi: dict, key: str, params: dict[str, str], coverage_note: str) -> dict:
    status, granules = asf_search(aoi["bbox"], params)
    entry: dict = {
        "attempted": True,
        "endpoint": ASF_API,
        "query": params,
        "http_status": status,
        "available": None,
        "error": None,
        "coverage_note": coverage_note,
    }
    if status >= 400 or (isinstance(granules, dict) and ("_error" in granules or "_http_error" in granules)):
        entry.update(
            {
                "available": None,
                "n_granules": 0,
                "error": str(granules)[:300],
                "date_range": None,
                "sample_granules": [],
                "granule_ids": [],
                "granule_ids_truncated": False,
                "granules": [],
                "stacks": [],
                "recommended_stack": None,
            }
        )
        return entry
    entry.update(summarise(granules, 250))
    entry["available"] = entry["n_granules"] > 0
    # A granule *intersecting* the AOI is not the same as covering it. Say so.
    entry["deltas_available"] = entry["n_granules"] > 0 and key.startswith(("nisar", "opera"))
    return entry


def egms_entry(aoi: dict) -> dict:
    is_eu = bool(aoi.get("eu_member_state"))
    return {
        "attempted": False,
        "endpoint": "https://egms.land.copernicus.eu",
        "query": None,
        "http_status": None,
        "available": None,
        "n_granules": None,
        "date_range": None,
        "total_bytes": None,
        "sample_granules": [],
        "granule_ids": [],
        "granule_ids_truncated": False,
        "granules": [],
        "stacks": [],
        "recommended_stack": None,
        "deltas_available": False,
        "coverage_note": (
            "EGMS is a Copernicus portal download (EU only). Not scriptable without a manual AOI "
            "job; Málaga IS eligible and should be pulled by hand if the demo needs a calibrated "
            "velocity layer before our own InSAR finishes."
            if is_eu
            else "not eligible: EGMS covers EU member states only."
        ),
        "error": None,
        "manual": True,
    }


def decide(sources: dict[str, dict]) -> tuple[str, str]:
    """First available source in fallback order wins; otherwise report the mock path."""
    order = [
        ("nisar_gunw", "NISAR_L2_GUNW"),
        ("opera_disp_s1", "OPERA_DISP_S1"),
        ("sentinel1_slc", "SENTINEL1_HYP3"),
    ]
    for key, label in order:
        src = sources.get(key) or {}
        if src.get("available") and not src.get("error"):
            return label, f"{key}: {src.get('n_granules')} granules intersecting the AOI"
    return (
        "MOCK",
        "no scriptable displacement product intersects this AOI yet; ship the mock artefacts and "
        "record provenance.source = MOCK so the badge never lies",
    )


def _stack_line(src: dict) -> str:
    recommended = src.get("recommended_stack") or {}
    if not recommended:
        return "no stack"
    track = recommended.get("track")
    return (
        f"stack {track or '?'}/{recommended.get('direction') or '?'} "
        f"({len(recommended.get('granule_ids') or [])} of "
        f"{recommended.get('n_granules')} granules)"
    )


def cmd_search(args: argparse.Namespace) -> int:
    cfg = json.loads(AOIS.read_text())
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cities = args.cities or list(cfg["cities"])
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for city in cities:
        aoi = cfg["cities"][city]
        print(f"[{city}] probing ASF ...", flush=True)
        sources: dict[str, dict] = {}
        sources["nisar_gunw"] = probe(
            aoi,
            "nisar_gunw",
            {"platform": "NISAR", "processingLevel": "GUNW"},
            "NISAR GUNW is provisional and cryosphere-derived; urban coverage is NOT guaranteed "
            "(README §7). An intersecting granule must be verified visually before use.",
        )
        sources["opera_disp_s1"] = probe(
            aoi,
            "opera_disp_s1",
            {"platform": "OPERA-S1", "processingLevel": "DISP-S1"},
            "OPERA DISP-S1 is the calibrated, ready-made velocity product when it exists; it is "
            "the preferred build-time fallback for city-scale displacement.",
        )
        sources["sentinel1_slc"] = probe(
            aoi,
            "sentinel1_slc",
            {
                "platform": "SENTINEL-1A,SENTINEL-1B,SENTINEL-1C",
                "processingLevel": "SLC",
                "start": SINCE_5Y,
                "end": OPEN_ENDED,
            },
            "SLC here means an HyP3 on-demand InSAR stack is *possible* (needs an Earthdata login "
            "and credits); 5-year window, S1C included.",
        )
        sources["egms"] = egms_entry(aoi)

        chosen_source, reason = decide(sources)
        # The evidence carries the SAME provenance contract as the artefacts (contract §5), so a
        # consumer can lift this block into meta.json without reinterpreting it. `product_id` is a
        # real granule when we have one: it is what an auditor greps to confirm which file was seen.
        source_key = {
            "NISAR_L2_GUNW": "nisar_gunw",
            "OPERA_DISP_S1": "opera_disp_s1",
            "SENTINEL1_HYP3": "sentinel1_slc",
            "MOCK": None,
        }[chosen_source]
        chosen_entry = sources.get(source_key) if source_key else None
        chosen_samples = (chosen_entry or {}).get("sample_granules") or []
        provenance = {
            "source": chosen_source,
            "product_id": chosen_samples[0]["id"] if chosen_samples else f"{chosen_source} (search only)",
            "processing_date": stamp[:10],
            "processing": {
                "NISAR_L2_GUNW": "NISAR L2 GUNW (unwrapped interferogram) > MintPy network inversion",
                "OPERA_DISP_S1": "OPERA DISP-S1 (already unwrapped+inverted) > vertical projection",
                "SENTINEL1_HYP3": "Sentinel-1 SLC > HyP3/ISCE2+SNAPHU > MintPy",
                "MOCK": "synthetic stub, pipeline/01_generate_mock.py",
            }[chosen_source],
            "method": (
                "ASF SearchAPI availability query at the AOI bbox; no processing performed at this "
                "stage. The source actually processed is recorded in chosen_source and repeated in "
                "docs/data-source-decision.md."
            ),
            "atmosphere": "none",
            "retrieved_utc": stamp,
            "reference": "Herrera-García et al. 2021, doi:10.1126/science.abb8549",
            "license": "NASA open data (ASF DAAC; Earthdata terms)",
        }
        report = {
            "contract_version": CONTRACT_VERSION,
            "city": city,
            "name": aoi["name"],
            "aoi_bbox": aoi["bbox"],
            "crs": aoi["crs"],
            "generated_utc": stamp,
            "sources": sources,
            "chosen_source": chosen_source,
            "chosen_reason": reason,
            "provenance": provenance,
            "recommendations": recommendations(city, sources, chosen_source),
        }
        path = out_dir / f"{city}.json"
        previous_size = path.stat().st_size if path.exists() else 0
        path.write_text(json.dumps(report, indent=2) + "\n")
        new_size = path.stat().st_size
        print(
            f"[{city}] -> {path}  chosen={chosen_source}  "
            f"json {new_size / 1024:.1f} KB ({new_size - previous_size:+d} B vs previous)"
        )
        for key, src in sources.items():
            print(
                f"    {key:16s} available={src.get('available')} n={src.get('n_granules')} "
                f"range={src.get('date_range')} gb={src.get('total_gb')} "
                f"{_stack_line(src) if key.startswith(('nisar', 'opera', 'sentinel')) else ''}"
            )
        time.sleep(1.0)
    return RC_OK


def recommendations(city: str, sources: dict[str, dict], chosen: str) -> list[str]:
    recs: list[str] = []
    nisar = sources["nisar_gunw"]
    opera = sources["opera_disp_s1"]
    s1 = sources["sentinel1_slc"]
    if chosen == "MOCK":
        recs.append("Ship mock artefacts for tonight's app; keep provenance.source = MOCK.")
    if nisar.get("available"):
        recs.append(
            f"NISAR GUNW intersects the AOI ({nisar['n_granules']} granules, "
            f"{nisar['date_range'][0] if nisar['date_range'] else '?'} .. "
            f"{nisar['date_range'][1] if nisar['date_range'] else '?'}); verify the interferogram "
            "actually covers the urban footprint before committing to a 12-day coherence threshold."
        )
        recommended = nisar.get("recommended_stack") or {}
        if recommended:
            recs.append(
                f"Fetch one geometry: `download --source nisar_gunw --stack recommended` plans "
                f"track {recommended.get('track')}/{recommended.get('direction')} "
                f"({len(recommended.get('granule_ids') or [])} of "
                f"{recommended.get('n_granules')} granules). Do not plan `--stack all`: it mixes "
                "tracks and the geometry guard refuses it."
            )
    if opera.get("available"):
        recs.append(
            "OPERA DISP-S1 present: prefer it for the velocity layer — it is already inverted, so "
            "the 48 h critical path shrinks to clip + project + tile."
        )
    if not opera.get("available"):
        recs.append("No OPERA DISP-S1 for this AOI: fall back to HyP3 SLC pairs or the mock layer.")
    if s1.get("available"):
        recs.append(
            f"Sentinel-1 SLC archive present ({s1['n_granules']} granules in the 5-y window) → "
            "an HyP3 on-demand stack is feasible (6-day revisit: do NOT pool coherence thresholds "
            "with NISAR's 12-day)."
        )
    if city == "malaga":
        recs.append(
            "Málaga is EU: EGMS can give a ready velocity field by hand tonight. Publish the "
            "detection limit and render flat deliberately."
        )
    return recs


# =============================================================================================
# Product identity guard
# =============================================================================================

# Products that cannot yield a displacement time series at all.
BANNED = {
    ("L0", "RRSD"): (
        "Level 0 RRSD is raw radar data: focusing it needs the full processor and ~100+ GB of "
        "output per granule, which is out of scope for this ingest"
    ),
    ("L1", "RSLC"): (
        "Level 1 RSLC is a radar-coordinate SLC: it has no unwrapped phase, so the "
        "radar-coordinate product cannot give displacement without a full ISCE2/SNAPHU run"
    ),
    ("L1", "RIFG"): (
        "Level 1 RIFG is a radar-coordinate interferogram: wrapped, not geocoded, and not "
        "what this chain consumes"
    ),
    ("L1", "RUNW"): (
        "Level 1 RUNW is a radar-coordinate unwrapped product: unwrapped phase in "
        "radar coordinates still needs geocoding + inversion, so it is not a displacement product"
    ),
}

# Geocoded products that are usable only on explicit opt-in (they are not displacement).
OPT_IN = {
    "GSLC": (
        "GSLC is a geocoded SLC: it carries no interferometric phase, so it cannot give "
        "displacement on its own (use it for coregistration only)"
    ),
    "GCOV": (
        "GCOV is a covariance product: no phase and no displacement, useful for amplitude "
        "context only"
    ),
    "GOFF": (
        "GOFF is a geocoded offset product: no interferometric phase, so no InSAR displacement"
    ),
}

SOURCE_LABEL = {
    "nisar_gunw": "NISAR GUNW",
    "opera_disp_s1": "OPERA DISP-S1",
    "sentinel1_slc": "Sentinel-1 SLC (HyP3)",
}


@dataclass(frozen=True)
class Decision:
    """The guard's verdict for one granule id: reason always explains a refusal."""

    accepted: bool
    reason: str
    parsed: ParsedGranule
    warning: str | None = None


def evaluate_granule(granule_id: str, source: str, allow_products: list[str]) -> Decision:
    """Fail closed: accept only a product this chain can actually turn into displacement."""
    parsed = parse_granule(granule_id)
    allowed = {product.upper() for product in allow_products}
    where = (
        f"parsed: level={parsed.level} product={parsed.product} track={parsed.track} "
        f"direction={parsed.direction}"
    )

    if parsed.level is None or parsed.product is None:
        return Decision(
            False,
            f"REFUSED: unrecognised granule name; fail closed rather than guess ({where}; "
            f"grammar=no table entry matched). Name: {granule_id}",
            parsed,
        )

    if source == "sentinel1_slc":
        if parsed.level != "S1" or parsed.product != "SLC":
            return Decision(
                False,
                f"REFUSED: {parsed.level}/{parsed.product} is not a Sentinel-1 SLC granule; "
                f"--source sentinel1_slc needs level=S1 product=SLC. NISAR products belong to "
                f"--source nisar_gunw ({where})",
                parsed,
            )
        return Decision(True, f"accepted Sentinel-1 SLC ({where})", parsed)

    if parsed.level == "S1":
        return Decision(
            False,
            f"REFUSED: Sentinel-1 granule under --source {source}; use --source sentinel1_slc "
            f"({where})",
            parsed,
        )

    banned = BANNED.get((parsed.level, parsed.product))
    if banned:
        return Decision(False, f"REFUSED: {banned} ({where})", parsed)

    if parsed.level == "L2":
        if parsed.product == "GUNW":
            return Decision(
                True,
                f"accepted NISAR GUNW unwrapped interferogram ({where})",
                parsed,
            )
        if parsed.product in OPT_IN:
            if parsed.product not in allowed:
                return Decision(
                    False,
                    f"REFUSED: {OPT_IN[parsed.product]}. Pass `--allow-product "
                    f"{parsed.product}` to fetch it anyway ({where})",
                    parsed,
                )
            return Decision(
                True,
                f"accepted {parsed.product} on explicit opt-in ({where})",
                parsed,
                warning=OPT_IN[parsed.product],
            )

    return Decision(
        False,
        f"REFUSED: no product policy for this granule; fail closed ({where}; "
        f"grammar=no table entry matched). Add it to the policy before downloading",
        parsed,
    )


# =============================================================================================
# Download planning
# =============================================================================================


class StackSelectionError(Exception):
    """`--stack` cannot be honoured; the message tells the operator what to do."""


def _record_id(record: dict) -> str:
    return record.get("id") or record.get("granuleName") or ""


def _dest_for(raw_dir: pathlib.Path, granule_id: str, url: str | None) -> pathlib.Path:
    name = pathlib.Path(urllib.parse.urlparse(url or "").path).name
    return raw_dir / (name or f"{granule_id}.h5")


def build_download_plan(
    records: list[dict],
    source: str,
    allow_products: list[str],
    raw_dir: pathlib.Path,
) -> list[dict]:
    """One plan item per granule: identity decision, destination, size. No bytes move here."""
    plan: list[dict] = []
    for record in records:
        granule_id = _record_id(record)
        if not granule_id:
            continue
        url = record.get("url")
        plan.append(
            {
                "granule_id": granule_id,
                "url": url,
                "size_mb": record.get("size_mb"),
                "dest": _dest_for(raw_dir, granule_id, url),
                "decision": evaluate_granule(granule_id, source, allow_products),
            }
        )
    return plan


def plan_geometries(plan: list[dict]) -> set[tuple[str | None, str | None]]:
    """Distinct `(track, direction)` among the accepted items: the mixed-geometry guard."""
    return {
        (item["decision"].parsed.track, item["decision"].parsed.direction)
        for item in plan
        if item["decision"].accepted
    }


def _normalise_track(value: str) -> str:
    wanted = value.strip()
    return wanted.zfill(3) if wanted.isdigit() else wanted


def select_stack_granules(
    src: dict, choice: str
) -> tuple[list[dict], str, list[str]]:
    """Resolve `--stack {recommended|<track>|all}` to granule records.

    Returns `(records, label, notes)`. `notes` carries the legacy-schema warning. Raises
    `StackSelectionError` when the choice names no stack (usage error, exit 2).
    """
    all_records = src.get("granules") or src.get("sample_granules") or []
    preview_only = not src.get("granules") and bool(src.get("sample_granules"))
    stacks = src.get("stacks") or []
    recommended = src.get("recommended_stack")
    notes: list[str] = []

    if not stacks:
        notes = [
            "no `stacks`/`recommended_stack` in the availability report (legacy schema)",
            "Re-run `search` to persist stacks and bound the fetch to one geometry",
        ]
        stacks = derive_stacks(all_records)
        recommended = recommend_stack(stacks)

    if not all_records and not stacks:
        raise StackSelectionError(
            "source has no granule records to plan; re-run `search` (and check it returned "
            "`available: true`)"
        )

    by_id = {_record_id(record): record for record in all_records}
    tracks = sorted({stack["track"] for stack in stacks if stack["track"]})

    def records_for(stack: dict) -> list[dict]:
        records = []
        for granule_id in stack.get("granule_ids") or []:
            record = by_id.get(granule_id)
            if record is None:
                # The report may cap `granules`; keep the id so the plan can still name it.
                record = {"id": granule_id, "url": None, "size_mb": None}
            records.append(record)
        return records

    def legacy_label(count: int) -> str:
        return f"legacy preview ({count} granule(s))"

    def epoch_notes(stack: dict) -> list[str]:
        """Carry the report's epoch verdict into the plan: never fetch an unfittable stack quietly."""
        warning = stack.get("warning")
        return [warning] if stack.get("epoch_sufficient") is False and warning else []

    normalised = choice.strip().lower()
    if normalised == "all":
        label = (
            legacy_label(len(all_records))
            if preview_only
            else f"all ({len(all_records)} granule(s))"
        )
        return all_records, label, notes

    if normalised == "recommended":
        stack = recommended or (stacks[0] if stacks else None)
        if stack is None:
            return all_records, legacy_label(len(all_records)), notes
        selected = records_for(stack)
        if preview_only:
            return selected, legacy_label(len(selected)), notes
        if stack["track"] is None:
            label = f"recommended: unidentified geometry ({len(selected)} granule(s))"
        else:
            label = (
                f"recommended: track {stack['track']} {stack['direction']} "
                f"({len(selected)} of {stack['n_granules']} granule(s))"
            )
        return selected, label, [*notes, *epoch_notes(stack)]

    wanted = _normalise_track(choice)
    match = next(
        (stack for stack in stacks if _normalise_track(str(stack["track"])) == wanted), None
    )
    if match is None:
        available = ", ".join(tracks) if tracks else "none"
        raise StackSelectionError(
            f"--stack {choice} matches no derived stack; tracks in this report: {available}. "
            "Run `search` again to re-derive stacks, or pass `--stack recommended`."
        )
    selected = records_for(match)
    if preview_only:
        return selected, legacy_label(len(selected)), notes
    label = (
        f"track {match['track']} {match['direction']} "
        f"({len(selected)} of {match['n_granules']} granule(s))"
    )
    return selected, label, [*notes, *epoch_notes(match)]


# =============================================================================================
# Earthdata / ASF authenticated streaming download
# =============================================================================================


class AuthError(RuntimeError):
    """401 / EDL login form: credentials or the Authorized Apps grant are wrong."""


class AccessError(RuntimeError):
    """403: the granule is restricted for this account (not a password problem)."""


def _earthaccess_session(user: str, pwd: str):
    """Optional earthaccess session (it carries the EDL cookies). Never required."""
    try:  # pragma: no cover - exercised only when earthaccess is installed and online
        import earthaccess  # type: ignore
    except Exception:  # noqa: BLE001 - earthaccess is optional
        return None
    try:  # pragma: no cover
        return earthaccess.login(strategy="environment")
    except Exception:  # noqa: BLE001 - fall back to plain requests
        return None


def build_earthdata_session(user: str, pwd: str):
    """A session whose credentials are attached per hop, never baked in for every host."""
    session = _earthaccess_session(user, pwd)
    if session is None:
        import requests  # imported lazily so `--help` works without the dependency

        session = requests.Session()
    # earthaccess sets session.auth globally; that would leak the password to the presigned
    # S3 redirect target. Clear it and authenticate only the EDL host in `download_one`.
    session.auth = None
    session.headers["User-Agent"] = "SAR_NASA/1.0 ingest"
    return session


def _basic_auth(user: str, pwd: str) -> str:
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return f"Basic {token}"


def _redacted(url: str) -> str:
    return f"{urllib.parse.urlsplit(url).scheme}://{urllib.parse.urlsplit(url).netloc}" \
           f"{urllib.parse.urlsplit(url).path}?<redacted>"


AUTH_HELP = (
    "401 from Earthdata Login: the credentials were rejected or this app is not authorised.\n"
    "  1. Check EARTHDATA_USER / EARTHDATA_PASS (a wrong password fails exactly like a\n"
    "     missing grant: both end here instead of at the S3 redirect).\n"
    "  2. Grant the app: https://urs.earthdata.nasa.gov -> Applications -> Authorized Apps ->\n"
    '     "Alaska Satellite Facility Data Access" = approved (accept the ASF EULA once).\n'
    "  3. Or add a `machine urs.earthdata.nasa.gov login ... password ...` entry to ~/.netrc.\n"
    "The .part file is kept; rerun to resume."
)


def download_one(
    url: str,
    dest: pathlib.Path,
    max_bytes: int,
    user: str,
    pwd: str,
    session=None,
) -> int:
    """Range-resumable streamed download through the ASF -> EDL -> presigned-S3 chain.

    Returns bytes written. Credentials are sent to the Earthdata Login host only; the presigned
    redirect carries its authorisation in the (never logged) query string.
    """
    session = session or build_earthdata_session(user, pwd)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    if start >= max_bytes:
        raise RuntimeError(
            f"{dest.name}: already {start / 1e9:.2f} GB, over the --max-gb guard; "
            f"delete or move {part.name}"
        )

    headers: dict[str, str] = {"Range": f"bytes={start}-"} if start else {}
    current_url = url
    for _hop in range(10):
        host = (urllib.parse.urlsplit(current_url).hostname or "").lower()
        hop_headers = dict(headers)
        if host.endswith("urs.earthdata.nasa.gov"):
            hop_headers["Authorization"] = _basic_auth(user, pwd)
        response = session.get(
            current_url,
            headers=hop_headers,
            stream=True,
            allow_redirects=False,
            timeout=TIMEOUT,
        )
        status = response.status_code

        if status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location") or response.headers.get("location")
            response.close()
            if not location:
                raise RuntimeError(f"{dest.name}: HTTP {status} without a Location header")
            current_url = location
            continue

        content_type = (response.headers.get("Content-Type") or "").lower()
        if status == 401:
            response.close()
            raise AuthError(AUTH_HELP)
        if status == 403:
            response.close()
            raise AccessError(
                f"403 Forbidden: this granule is restricted for this Earthdata account\n"
                f"  {_redacted(current_url)}\n"
                "  The account is authenticated but not authorised for this product: accept the\n"
                "  ASF EULA / authorised-apps grant listed in the runbook, then rerun."
            )
        if status == 200 and content_type.startswith("text/html") and host.endswith(
            "urs.earthdata.nasa.gov"
        ):
            response.close()
            raise AuthError(
                "Earthdata Login form returned instead of the granule redirect.\n" + AUTH_HELP
            )
        if status not in (200, 206):
            response.close()
            raise RuntimeError(
                f"{dest.name}: HTTP {status} from {_redacted(current_url)}; nothing written"
            )

        # 206 means the server honoured Range and we append; 200 means it ignored it, so restart.
        append = bool(start) and status == 206
        written = start if append else 0
        mode = "ab" if append else "wb"
        try:
            with part.open(mode) as handle:
                for chunk in response.iter_content(CHUNK):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if written > max_bytes:
                        handle.flush()
                        response.close()
                        raise RuntimeError(
                            f"{dest.name}: exceeded --max-gb guard at {written / 1e9:.2f} GB; "
                            f"partial kept at {part}"
                        )
        finally:
            response.close()
        part.rename(dest)
        return written

    raise RuntimeError(f"{dest.name}: too many redirects (10 hops) — aborting without a write")


def find_foreign_partials(raw_dir: pathlib.Path, dests: list[pathlib.Path]) -> list[pathlib.Path]:
    """Partials we did not create (Chrome's `.crdownload`, curl's `.part` elsewhere, ...).

    Our own `<name>.part` next to its destination is the resume mechanism, not a foreign file.
    """
    if not raw_dir.exists():
        return []
    ours = {
        dest.with_suffix(dest.suffix + ".part").resolve() for dest in dests
    }
    foreign: list[pathlib.Path] = []
    for pattern in ("*.crdownload", "*.part", "*.download", "*.tmp"):
        for path in raw_dir.glob(pattern):
            if path.resolve() in ours:
                continue
            foreign.append(path)
    return sorted(foreign)


def earthdata_creds() -> tuple[str, str] | None:
    user = os.environ.get("EARTHDATA_USER")
    pwd = os.environ.get("EARTHDATA_PASS")
    if user and pwd:
        return user, pwd
    netrc = pathlib.Path.home() / ".netrc"
    if netrc.exists():
        for line in netrc.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 6 and parts[0] == "machine" and "urs.earthdata.nasa.gov" in parts[1]:
                kv = dict(zip(parts[2::2], parts[3::2]))
                if "login" in kv and "password" in kv:
                    return kv["login"], kv["password"]
    return None


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _print_plan(city: str, source: str, label: str, notes: list[str], plan: list[dict],
                max_gb: float) -> None:
    print(f"Download plan for {city} / {source}")
    print(f"  Stack selection: {label}")
    for note in notes:
        print(f"  note: {note}")

    known_mb = sum((item["size_mb"] or 0) for item in plan)
    cumulative_mb = 0.0
    for index, item in enumerate(plan, start=1):
        decision = item["decision"]
        print(f"  [{index}/{len(plan)}] {item['granule_id']}")
        print(
            f"        level={decision.parsed.level} product={decision.parsed.product} "
            f"track={decision.parsed.track} direction={decision.parsed.direction}"
        )
        print(
            f"        reference={decision.parsed.reference_start} "
            f"secondary={decision.parsed.secondary_start}"
        )
        size = item["size_mb"]
        cumulative_mb += size or 0.0
        size_text = f"{size:.1f} MB" if size is not None else "unknown size"
        print(f"        size={size_text}  cumulative={cumulative_mb / 1000:.2f} GB")
        status = "ACCEPT" if decision.accepted else "REJECT"
        suffix = "" if decision.accepted else f" — {decision.reason}"
        print(f"        status: {status}{suffix}")
        if decision.warning:
            print(f"        warning: {decision.warning}")

    geometry_count = len(plan_geometries(plan))
    print(
        f"  plan: {len(plan)} granule(s), {known_mb / 1000:.2f} GB known size, "
        f"{geometry_count} geometry/geometries, --max-gb {max_gb:.2f} (cumulative, "
        f"checked before any transfer)"
    )


def cmd_download(args: argparse.Namespace) -> int:
    cfg = json.loads(AOIS.read_text())
    if args.city not in cfg["cities"]:
        print(f"unknown city {args.city}; known: {', '.join(cfg['cities'])}", file=sys.stderr)
        return RC_USAGE
    report_path = pathlib.Path(args.availability) / f"{args.city}.json"
    if not report_path.exists():
        print(f"missing {report_path}; run `search` first", file=sys.stderr)
        return RC_USAGE
    report = json.loads(report_path.read_text())
    src = report["sources"].get(args.source)
    if not src or not src.get("available"):
        print(f"source {args.source} is not available for {args.city}", file=sys.stderr)
        return RC_USAGE

    try:
        records, label, notes = select_stack_granules(src, args.stack)
    except StackSelectionError as exc:
        print(str(exc), file=sys.stderr)
        return RC_USAGE

    raw_dir = pathlib.Path(args.raw) / args.city / args.source
    max_bytes = int(args.max_gb * 1e9)
    plan = build_download_plan(records, args.source, args.allow_product or [], raw_dir)
    if not plan:
        print(
            f"source {args.source} has no plannable granule records; re-run `search`",
            file=sys.stderr,
        )
        return RC_USAGE

    _print_plan(args.city, args.source, label, notes, plan, args.max_gb)

    rejected = [item for item in plan if not item["decision"].accepted]
    if rejected:
        print(
            f"REFUSED: {len(rejected)} of {len(plan)} granule(s) failed the product identity "
            f"guard; nothing was fetched (exit {RC_PRODUCT_GUARD})",
            file=sys.stderr,
        )
        for item in rejected:
            print(f"  {item['granule_id']}: {item['decision'].reason}", file=sys.stderr)
        return RC_PRODUCT_GUARD

    geometries = plan_geometries(plan)
    if len(geometries) > 1 and not args.allow_mixed_geometry:
        print(
            f"REFUSED: this plan mixes acquisition geometries ({len(geometries)} tracks/flight "
            f"directions); relative displacement cannot be compared across them"
        )
        for track, direction in sorted(
            geometries, key=lambda geometry: (str(geometry[0]), str(geometry[1]))
        ):
            print(f"  track {track} {direction}")
        print(
            "  a stack must share one orbit track and flight direction; re-plan with "
            "`--stack recommended` or `--stack <track>` (planned from a legacy report? "
            "re-run `search`)"
        )
        return RC_PRODUCT_GUARD

    known_mb = sum((item["size_mb"] or 0) for item in plan)
    if known_mb / 1000 > args.max_gb:
        print(
            f"planned size {known_mb / 1000:.2f} GB exceeds --max-gb {args.max_gb:g}; "
            f"refusing before any transfer (raise --max-gb deliberately to fetch it)",
            file=sys.stderr,
        )
        return RC_BUDGET_GUARD

    if args.dry_run:
        print("  dry run: nothing written, no credentials needed")
        return RC_OK

    foreign = find_foreign_partials(raw_dir, [item["dest"] for item in plan])
    if foreign:
        print(
            "REFUSED: found foreign partial download(s) in "
            f"{raw_dir} — a browser (.crdownload) or another tool owns them, so resuming "
            "could mix two files:",
            file=sys.stderr,
        )
        for path in foreign:
            print(f"  {path.name}", file=sys.stderr)
        print(
            "  Cancel the browser download, move or delete those files, then rerun.",
            file=sys.stderr,
        )
        return RC_PARTIAL_GUARD

    creds = earthdata_creds()
    if creds is None:
        print(
            "No Earthdata credentials found — download is disabled (fail closed).\n"
            "  export EARTHDATA_USER=...  EARTHDATA_PASS=...\n"
            "  or add a machine urs.earthdata.nasa.gov entry to ~/.netrc\n"
            "Register at https://urs.earthdata.nasa.gov and accept the ASF EULA.",
            file=sys.stderr,
        )
        return RC_CREDENTIALS
    user, pwd = creds
    session = build_earthdata_session(user, pwd)

    records_out = []
    for item in plan:
        granule_id, url, dest = item["granule_id"], item["url"], item["dest"]
        if not url:
            print(f"{granule_id}: no download URL in the report; re-run `search`", file=sys.stderr)
            return RC_USAGE
        if dest.exists():
            print(f"  skip (complete) {dest.name}")
        else:
            print(f"  get {dest.name} ({item['size_mb']} MB)")
            try:
                written = download_one(url, dest, max_bytes, user, pwd, session=session)
            except AuthError as exc:
                print(f"{granule_id}: {exc}", file=sys.stderr)
                return RC_CREDENTIALS
            except AccessError as exc:
                print(f"{granule_id}: {exc}", file=sys.stderr)
                return RC_ACCESS
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return RC_BUDGET_GUARD
            print(f"      {written / 1e6:.1f} MB")
        records_out.append(
            {
                "granule_id": granule_id,
                "url": url,
                "file": str(dest),
                "bytes": dest.stat().st_size if dest.exists() else None,
                "sha256": sha256(dest) if dest.exists() else None,
            }
        )
    (raw_dir / "provenance.json").write_text(
        json.dumps(
            {
                "contract_version": CONTRACT_VERSION,
                "city": args.city,
                "source": args.source,
                "max_gb": args.max_gb,
                "stack": label,
                "retrieved_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "earthdata_user": user,
                "files": records_out,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"provenance -> {raw_dir / 'provenance.json'}")
    return RC_OK


def cmd_report(args: argparse.Namespace) -> int:
    out_dir = pathlib.Path(args.out)
    rows = []
    for path in sorted(out_dir.glob("*.json")):
        rep = json.loads(path.read_text())
        for key, src in rep["sources"].items():
            rng = src.get("date_range")
            rows.append(
                (
                    rep["city"],
                    key,
                    str(src.get("available")),
                    str(src.get("n_granules")),
                    f"{rng[0][:10]}..{rng[1][:10]}" if rng else "—",
                    f"{src.get('total_gb')}" if src.get("total_gb") else "—",
                    rep["chosen_source"],
                )
            )
    print("| city | source | available | n | date range | GB | chosen |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(row) + " |")
    return RC_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="probe data availability for the study AOIs")
    p_search.add_argument("--cities", nargs="*", default=None)
    p_search.add_argument("--out", default="data/processed/_availability")
    p_search.set_defaults(func=cmd_search)

    p_report = sub.add_parser("report", help="markdown table of the availability reports")
    p_report.add_argument("--out", default="data/processed/_availability")
    p_report.set_defaults(func=cmd_report)

    p_dl = sub.add_parser("download", help="resumable, size-guarded download of a chosen source")
    p_dl.add_argument("--city", required=True)
    p_dl.add_argument("--source", required=True, choices=["nisar_gunw", "opera_disp_s1", "sentinel1_slc"])
    p_dl.add_argument(
        "--stack",
        default="recommended",
        help="recommended (largest single geometry) | a track number | all (mixes tracks, "
        "refused by the geometry guard)",
    )
    p_dl.add_argument("--max-gb", type=float, default=2.0)
    p_dl.add_argument("--raw", default="data/raw")
    p_dl.add_argument("--availability", default="data/processed/_availability")
    p_dl.add_argument(
        "--allow-product",
        action="append",
        default=[],
        metavar="PRODUCT",
        help="fetch a non-displacement product (GSLC/GCOV/GOFF) anyway; recorded as opt-in",
    )
    p_dl.add_argument(
        "--allow-mixed-geometry",
        action="store_true",
        help="do NOT use: the science layer refuses mixed geometries anyway",
    )
    p_dl.add_argument(
        "--dry-run",
        action="store_true",
        help="print the identity/budget/geometry plan and exit 0 without credentials or writes",
    )
    p_dl.set_defaults(func=cmd_download)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
