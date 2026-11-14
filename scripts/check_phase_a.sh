#!/usr/bin/env bash
# Phase A/B gate — the one command that says whether the artefacts are safe to hand to pozo and
# fradiaz. It works for mock artefacts and for real ones: the provenance rule is symmetric.
#
#   ./scripts/check_phase_a.sh
#
# Runs, in order:
#   1. the frozen contract's own self-check (positives + negatives)
#   2. the artefact-facing regression suites
#   3. the file-level artefact gate over data/processed/
#   4. a handoff summary: per case, what exists, what it claims, how big it is
#
# Exit 0 only if all four pass. Nothing here writes to data/ or the pipeline modules.
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
step() { printf '\n▶ %s\n' "$*"; }

step "1/4 contract self-check"
./scripts/python.sh -m src.domain.contracts || fail=1

step "2/4 artefact regression suites"
# Deliberately scoped: the handoff is about the artefacts and the gate, so a module mid-development
# elsewhere (pipeline/science, ingest) must not make the handoff look broken. Add a suite here only
# if it guards something the app consumes.
./scripts/python.sh -m pytest -q \
    tests/test_contracts.py \
    tests/test_contract_docs.py \
    tests/test_config_consistency.py \
    tests/test_mock_expectations.py \
    tests/test_artefact_capabilities.py \
    tests/test_repo_manifest.py \
    tests/test_readme_availability.py \
    tests/test_export_grid_geojson.py \
    tests/test_validate_artifacts.py || fail=1

step "3/4 artefact gate (data/processed)"
# Fail loudly BEFORE the gate runs: an empty tree is the common failure and deserves its own line.
if ! compgen -G "data/processed/*/nisar_grid.parquet" >/dev/null; then
    echo "!! no case artefacts found under data/processed (mock not generated yet, or unexpected path)"
    fail=1
fi
if [[ -f pipeline/validate_artifacts.py ]]; then
    ./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed --strict-size-mb 15 || fail=1
else
    echo "!! pipeline/validate_artifacts.py missing — the artefact gate has not landed"
    fail=1
fi

step "4/4 handoff summary"
./scripts/python.sh - <<'PY' || fail=1
import json
import pathlib
import sys

import pandas as pd

root = pathlib.Path("data/processed")
cases = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
if not cases:
    print("no case directories under data/processed — nothing to hand off")
    sys.exit(1)

required = ["nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json"]
header = f"{'case':14s} {'files':>5s} {'MB':>6s} {'cells':>6s} {'series':>6s} {'dates':>5s} {'product':>11s} {'median mm/yr':>12s}  source"
print(header)
print("-" * len(header))

problems = []
for case in cases:
    directory = root / case
    missing = [name for name in required if not (directory / name).exists()]
    if missing:
        problems.append(f"{case}: missing {missing}")
        continue
    size_mb = sum(f.stat().st_size for f in directory.iterdir() if f.is_file()) / 1e6
    grid = pd.read_parquet(directory / "nisar_grid.parquet")
    series = pd.read_csv(directory / "timeseries.csv")
    meta = json.loads((directory / "meta.json").read_text())
    print(
        f"{case:14s} {len(required):5d} {size_mb:6.2f} {len(grid):6d} {len(series):6d} "
        f"{len(meta['dates']):5d} {meta['product']:>11s} "
        f"{meta['stats']['median_velocity_mm_yr']:12.1f}  {meta['provenance']['source'][:40]}"
    )

    # Symmetric provenance rule: the badge must match the product, in both directions. Requiring
    # "mock" here unconditionally made this gate fail by construction once real data landed.
    source = str(meta["provenance"]["source"]).lower()
    product = str(meta.get("product", ""))
    if product == "mock" and "mock" not in source:
        problems.append(f"{case}: product is mock but provenance.source does not say so")
    if product != "mock" and "mock" in source:
        problems.append(f"{case}: product is {product!r} but provenance.source still says mock")

if problems:
    print()
    for problem in problems:
        print("  !!", problem)
    sys.exit(1)

print("\nhandoff payload: the four files above, per case, exactly as frozen in docs/data-contract-v1.md")
PY

printf '\n'
if [[ $fail -eq 0 ]]; then
    echo "PHASE A/B: PASS — artefacts are contract-conformant and safe to hand off"
else
    echo "PHASE A/B: FAIL — fix the steps marked above before telling the team the artefacts are ready"
    exit 1
fi
