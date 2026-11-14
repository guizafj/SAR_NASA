"""Provenance guard: the committed mock must be reproducible from the shipped defaults.

`docs/data-contract-v1.md` (and pipeline/README) promise that the mock is deterministic — same
config + same default seed => the same `data/processed/`. Every artefact carries the seed inside
its provenance digest, so a non-deterministic value (unordered dict/set iteration, a wall clock, a
changed default seed) silently invalidates the committed provenance. Schema tests cannot see that;
only comparing a fresh generation can.

This module pins the claim:

* regenerate all study cases with the *defaults* (no `--seed`, so a changed default is caught) into
  a pytest `tmp_path`, then compare all four artefacts per case to `data/processed/`, SHA-256 first;
* when the bytes differ, compare **content** (columns/rows/keys, floats within 1e-11) so that an
  interpreter or BLAS upgrade cannot masquerade as scientific drift. Identical content with
  different bytes raises a warning: provenance must be quoted from `provenance_id`, not a file hash;
* a control run with `--seed 8` proves the comparison is non-vacuous: if those digests did not
  move, the equality assertions above would be meaningless;
* skipped when `data/processed/` holds no cases, so a bare checkout still collects cleanly.

Hermetic: subprocess only (no imports of the heavy pipeline), no network, all writes confined to
`tmp_path`; `data/processed/` and `config/` are only ever read.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "pipeline" / "generate_mock.py"
COMMITTED = ROOT / "data" / "processed"
ARTEFACTS = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")
CONTROL_SEED = 8
TIMEOUT_S = 300

SKIP_REASON = (
    "no mock artefacts in data/processed — run ./scripts/python.sh pipeline/generate_mock.py"
)


def _committed_cases() -> list[str]:
    """Case directories that hold the full frozen artefact set (`_availability` etc. excluded)."""
    if not COMMITTED.is_dir():
        return []
    return sorted(
        path.name
        for path in COMMITTED.iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and all((path / name).is_file() for name in ARTEFACTS)
    )


CASES = _committed_cases()
pytestmark = pytest.mark.skipif(not CASES, reason=SKIP_REASON)


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generate(out: pathlib.Path, *extra: str) -> str:
    """Run the generator into `out`; return its stderr for context in assertions."""
    env = dict(os.environ)
    # Do not pin PYTHONHASHSEED: an unordered set of strings would then look deterministic, and
    # catching exactly that kind of non-determinism is the point of this module.
    env.pop("PYTHONHASHSEED", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # keep even __pycache__ out of the repo tree
    argv = [sys.executable, str(GENERATOR), "--out", str(out), *extra]
    proc = subprocess.run(
        argv,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        env=env,
    )
    assert proc.returncode == 0, (
        f"mock generation failed (exit {proc.returncode})\n"
        f"  command: {' '.join(argv)}\n"
        f"  stderr:\n{proc.stderr.strip() or '(empty)'}\n"
        f"  stdout:\n{proc.stdout.strip() or '(empty)'}"
    )
    return proc.stderr


def _mismatch_message(case: str, name: str, committed: str, fresh: str) -> str:
    return (
        f"{case}/{name} is not reproducible from the shipped defaults.\n"
        f"  committed   data/processed/{case}/{name}  sha256 {committed}\n"
        f"  regenerated <tmp>/{case}/{name}          sha256 {fresh}\n"
        "Likely causes:\n"
        "  1. the generator's default seed changed (meta.json/provenance digests pin it: different\n"
        "     seed => different provenance_id in every artefact);\n"
        "  2. a value is non-deterministic (unordered dict/set iteration, a wall clock or now(),\n"
        "     locale/float formatting, an unpinned pyarrow/pandas version);\n"
        "  3. the artefacts were legitimately regenerated but not committed — rerun\n"
        "     `./scripts/python.sh pipeline/generate_mock.py --out data/processed` and commit the diff."
    )


def _semantic_difference(committed: pathlib.Path, fresh: pathlib.Path, name: str) -> str | None:
    """Compare two artefacts by *content*, tolerating version-dependent rendering.

    Byte equality is the strong claim and is tried first. When a fresh interpreter/version renders
    the same numbers with a different float format or key order, byte comparison turns a green
    claim into a red herring: the science is identical, only `repr(float)` or a library default
    moved. Parquet is compared column-wise, CSV row-wise, JSON/GeoJSON structurally.

    Returns `None` when the contents agree, else a human-readable description of the first drift.
    """
    tolerance = 1e-11

    if name.endswith(".parquet"):
        df_committed = pd.read_parquet(committed)
        df_fresh = pd.read_parquet(fresh)
    elif name.endswith(".csv"):
        df_committed = pd.read_csv(committed)
        df_fresh = pd.read_csv(fresh)
    else:
        return _structural_difference(committed, fresh, tolerance)

    if list(df_fresh.columns) != list(df_committed.columns):
        return f"column order/set differs: {list(df_fresh.columns)} != {list(df_committed.columns)}"
    if len(df_fresh) != len(df_committed):
        return f"row count differs: {len(df_fresh)} != {len(df_committed)}"
    for col in df_committed.columns:
        left = df_committed[col]
        right = df_fresh[col]
        if pd.api.types.is_float_dtype(left) and pd.api.types.is_float_dtype(right):
            lhs = left.to_numpy(dtype=np.float64)
            rhs = right.to_numpy(dtype=np.float64)
            worst = float(np.max(np.abs(lhs - rhs))) if len(lhs) else 0.0
            if not np.allclose(lhs, rhs, rtol=0.0, atol=tolerance, equal_nan=True):
                return f"float column {col!r} drifted by up to {worst:g} (tolerance {tolerance:g})"
        elif not left.astype(str).equals(right.astype(str)):
            return f"column {col!r} differs"
    return None


def _structural_difference(
    committed: pathlib.Path, fresh: pathlib.Path, tolerance: float
) -> str | None:
    """Recursive JSON/GeoJSON comparison with a float tolerance and order-insensitive keys."""
    left = json.loads(committed.read_text(encoding="utf-8"))
    right = json.loads(fresh.read_text(encoding="utf-8"))

    def walk(a: object, b: object, path: str) -> str | None:
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                return f"{path or '<root>'}: keys differ ({sorted(set(a) ^ set(b))})"
            for key in a:
                found = walk(a[key], b[key], f"{path}.{key}")
                if found:
                    return found
            return None
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return f"{path}: list length {len(a)} != {len(b)}"
            for index, (item_a, item_b) in enumerate(zip(a, b, strict=True)):
                found = walk(item_a, item_b, f"{path}[{index}]")
                if found:
                    return found
            return None
        if isinstance(a, bool) or isinstance(b, bool):
            return None if a == b else f"{path}: {a!r} != {b!r}"
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if a == b:
                return None
            scale = max(abs(float(a)), abs(float(b)), 1.0)
            if abs(float(a) - float(b)) <= tolerance * scale:
                return None
            return f"{path}: {a!r} != {b!r}"
        return None if a == b else f"{path}: {a!r} != {b!r}"

    return walk(left, right, "")


@pytest.fixture(scope="session")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """All study cases generated once, with the shipped defaults (seed 7, every case)."""
    out = tmp_path_factory.mktemp("mock-default-seed")
    _generate(out)
    return out


PAIRS = [(case, name) for case in CASES for name in ARTEFACTS]


@pytest.mark.parametrize(("case", "name"), PAIRS, ids=[f"{case}/{name}" for case, name in PAIRS])
def test_default_seed_reproduces_committed_artefact(
    regenerated: pathlib.Path, case: str, name: str
) -> None:
    fresh = regenerated / case / name
    committed = COMMITTED / case / name
    assert fresh.is_file(), (
        f"the default run did not emit {case}/{name} even though data/processed/{case}/{name} is "
        "committed: either the case is no longer generated by default (check --cities/defaults) "
        "or the artefact set changed and data/processed/ is stale."
    )
    fresh_digest, committed_digest = _digest(fresh), _digest(committed)
    if fresh_digest == committed_digest:
        return

    drift = _semantic_difference(committed, fresh, name)
    assert drift is None, (
        f"{name}: content drifted, not just its rendering — {drift}\n"
        + _mismatch_message(case, name, committed_digest, fresh_digest)
    )
    # Byte-level difference with identical content: the generator is reproducible, the interpreter/
    # library rendering is not pinned. Not a failure (CI runs 3.12, developers may run 3.14), but it
    # means the published SHA-256 is environment-specific, so say so instead of hiding it.
    warnings.warn(
        f"{case}/{name} differs byte-wise from data/processed/ although its content is identical "
        f"(committed {committed_digest[:12]}…, regenerated {fresh_digest[:12]}…): the artefact "
        "rendering depends on the interpreter/library versions, so quote provenance from "
        "meta.json/provenance_id rather than from a file SHA-256.",
        stacklevel=2,
    )


def test_control_seed_changes_digests(tmp_path: pathlib.Path) -> None:
    """Non-vacuity control: the hashes must be sensitive to the seed.

    Regenerates the smallest committed case with `--seed 8` and requires at least one artefact to
    differ from the committed one. Without this, a comparison that could never fail (or a control
    case whose fields ignore the seed) would make the equality assertions above meaningless.
    """
    case = min(
        CASES, key=lambda c: sum((COMMITTED / c / n).stat().st_size for n in ARTEFACTS)
    )
    out = tmp_path / f"seed-{CONTROL_SEED}"
    _generate(out, "--cities", case, "--seed", str(CONTROL_SEED))

    for name in ARTEFACTS:
        assert (out / case / name).is_file(), f"control run did not emit {case}/{name}"

    changed = [
        name for name in ARTEFACTS if _digest(out / case / name) != _digest(COMMITTED / case / name)
    ]
    assert changed, (
        f"control failed: regenerating {case} with --seed {CONTROL_SEED} produced byte-identical "
        f"{list(ARTEFACTS)} — the seed does not reach the artefacts, so the SHA-256 equality "
        "checks in this module cannot detect a non-deterministic mock. Investigate the generator "
        "before trusting the reproducibility claim."
    )
