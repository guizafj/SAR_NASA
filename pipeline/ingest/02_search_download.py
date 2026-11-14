#!/usr/bin/env python3
"""02_search_download.py — P1 ingest: prove what real SAR data exists, then fetch it.

Fallback chain (README §7):
    NISAR GUNW > OPERA DISP-S1 > Sentinel-1 (HyP3 SLC) > EGMS (EU only)

Search is unauthenticated (ASF SearchAPI public). Download needs Earthdata credentials and
fails closed with an actionable message when they are missing.

Usage
-----
  ./.venv/bin/python pipeline/ingest/02_search_download.py search
  ./.venv/bin/python pipeline/ingest/02_search_download.py report
  ./.venv/bin/python pipeline/ingest/02_search_download.py download --city malaga \
      --source opera_disp_s1 --max-gb 2

Output
------
  data/processed/_availability/{city}.json   ← acts on this tonight
  data/raw/{city}/{source}/                  ← granules + provenance.json (gitignored)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
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


def summarise(granules: list[dict], requested: int) -> dict:
    if not granules:
        return {
            "n_granules": 0,
            "n_granules_truncated": False,
            "date_range": None,
            "total_bytes": None,
            "total_gb": None,
            "sample_granules": [],
            "platforms": [],
            "deltas_available": False,
        }
    starts = sorted(g["startTime"] for g in granules if g.get("startTime"))
    sizes = [g.get("sizeMB") or 0 for g in granules]
    total_mb = sum(s for s in sizes if s)
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
        "platforms": sorted({g.get("platform", "?") for g in granules}),
        "deltas_available": None,
    }


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
        provenance = {
            "source": chosen_source,
            "processing": {
                "NISAR_L2_GUNW": "NISAR L2 GUNW (unwrapped interferogram) > MintPy network inversion",
                "OPERA_DISP_S1": "OPERA DISP-S1 (already unwrapped+inverted) > vertical projection",
                "SENTINEL1_HYP3": "Sentinel-1 SLC > HyP3/ISCE2+SNAPHU > MintPy",
                "MOCK": "synthetic stub, pipeline/01_generate_mock.py",
            }[chosen_source],
            "atmosphere": "none",
            "retrieved_utc": stamp,
            "reference": "Herrera-García et al. 2021, doi:10.1126/science.abb8549",
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
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"[{city}] -> {path}  chosen={chosen_source}")
        for key, src in sources.items():
            print(f"    {key:16s} available={src.get('available')} n={src.get('n_granules')} "
                  f"range={src.get('date_range')} gb={src.get('total_gb')}")
        time.sleep(1.0)
    return 0


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


def download_one(url: str, dest: pathlib.Path, max_bytes: int, user: str, pwd: str) -> int:
    """Range-resumable single-file download. Returns bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    if start >= max_bytes:
        raise RuntimeError(f"{dest.name}: already {start/1e9:.2f} GB, over the --max-gb guard")
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-"} if start else {})
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, url, user, pwd)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(manager))
    written = start
    with opener.open(req, timeout=TIMEOUT) as resp:
        with part.open("ab") as fh:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                fh.write(chunk)
                written += len(chunk)
                if written > max_bytes:
                    fh.flush()
                    raise RuntimeError(
                        f"{dest.name}: exceeded --max-gb guard at {written/1e9:.2f} GB; "
                        f"partial kept at {part}"
                    )
    part.rename(dest)
    return written


def cmd_download(args: argparse.Namespace) -> int:
    cfg = json.loads(AOIS.read_text())
    if args.city not in cfg["cities"]:
        print(f"unknown city {args.city}; known: {', '.join(cfg['cities'])}", file=sys.stderr)
        return 2
    report_path = pathlib.Path("data/processed/_availability") / f"{args.city}.json"
    if not report_path.exists():
        print(f"missing {report_path}; run `search` first", file=sys.stderr)
        return 2
    report = json.loads(report_path.read_text())
    src = report["sources"].get(args.source)
    if not src or not src.get("available") or not src.get("sample_granules"):
        print(f"source {args.source} is not available for {args.city}", file=sys.stderr)
        return 2
    creds = earthdata_creds()
    if creds is None:
        print(
            "No Earthdata credentials found — download is disabled (fail closed).\n"
            "  export EARTHDATA_USER=...  EARTHDATA_PASS=...\n"
            "  or add a machine urs.earthdata.nasa.gov entry to ~/.netrc\n"
            "Register at https://urs.earthdata.nasa.gov and accept the ASF EULA.",
            file=sys.stderr,
        )
        return 3
    user, pwd = creds
    raw_dir = pathlib.Path(args.raw) / args.city / args.source
    max_bytes = int(args.max_gb * 1e9)
    records = []
    for granule in src["sample_granules"]:
        url = granule["url"]
        dest = raw_dir / pathlib.Path(urllib.parse.urlparse(url).path).name
        if dest.exists():
            print(f"  skip (complete) {dest.name}")
        else:
            print(f"  get {dest.name} ({granule.get('size_mb')} MB)")
            written = download_one(url, dest, max_bytes, user, pwd)
            print(f"      {written/1e6:.1f} MB")
        records.append(
            {
                "granule_id": granule["id"],
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
                "retrieved_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "earthdata_user": user,
                "files": records,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"provenance -> {raw_dir/'provenance.json'}")
    return 0


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
    return 0


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
    p_dl.add_argument("--max-gb", type=float, default=2.0)
    p_dl.add_argument("--raw", default="data/raw")
    p_dl.set_defaults(func=cmd_download)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
