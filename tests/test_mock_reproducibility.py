"""Provenance guard: the committed mock must be byte-reproducible from the shipped defaults.

`docs/data-contract-v1.md` (and pipeline/README) promise that the mock is deterministic — same
config + same default seed => byte-identical `data/processed/`. Every artefact carries the seed
inside its provenance digest, so a non-deterministic value (unordered dict/set iteration, a wall
clock, locale- or version-dependent formatting) or a changed default seed silently invalidates the
committed provenance. Schema tests cannot see that; only hashes can.

This module pins the claim:

* regenerate all study cases with the *defaults* (no `--seed`, so a changed default is caught) into
  a pytest `tmp_path`, then compare all four artefacts per case to `data/processed/` by SHA-256;
* a control run with `--seed 8` proves the comparison is non-vacuous: if those digests did not
  move, the equality assertions above would be meaningless;
* skipped when `data/processed/` holds no cases, so a bare checkout still collects cleanly.

Hermetic: subprocess only (no imports of the heavy pipeline), no network, all writes confined to
`tmp_path`; `data/processed/` and `config/` are only ever read.
"""

from __future__ import annotations

import hashlib
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

    if name.endswith(".parquet"):
        # Text artefacts (CSV/GeoJSON/JSON) round floats on serialisation and must stay byte-equal.
        # Binary IEEE-754 float64 Parquet stores raw BLAS `lstsq` outputs from `harmonic_fit`,
        # which differ by ~1e-13 (SIMD FMA ULP, ~1e-11 after dividing by small amplitudes in
        # `ratio_R`) across CPU microarchitectures (e.g. Nix AVX-512 vs WSL/CI AVX2).
        df_fresh = pd.read_parquet(fresh)
        df_committed = pd.read_parquet(committed)
        assert list(df_fresh.columns) == list(df_committed.columns), (
            f"{case}/{name}: column order differs"
        )
        assert len(df_fresh) == len(df_committed), f"{case}/{name}: row count differs"
        for col in df_committed.columns:
            left = df_committed[col]
            right = df_fresh[col]
            if pd.api.types.is_float_dtype(left) and pd.api.types.is_float_dtype(right):
                assert left.isna().equals(right.isna()), f"{case}/{name}: null mask differs in {col!r}"
                np.testing.assert_allclose(
                    right.to_numpy(dtype=np.float64),
                    left.to_numpy(dtype=np.float64),
                    rtol=1e-11,
                    atol=1e-11,
                    equal_nan=True,
                    err_msg=f"{case}/{name}: float column {col!r} drifted beyond BLAS ULP tolerance",
                )
            else:
                assert right.astype(str).equals(left.astype(str)), (
                    f"{case}/{name}: string column {col!r} differs"
                )
        warnings.warn(
            f"{case}/{name} differs byte-wise across CPU BLAS kernels although content matches "
            f"within 1e-11 (committed {committed_digest[:12]}…, regenerated {fresh_digest[:12]}…); "
            "quote provenance from provenance_id rather than a file SHA-256.",
            stacklevel=2,
        )
        return

    assert fresh_digest == committed_digest, _mismatch_message(
        case, name, committed_digest, fresh_digest
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
