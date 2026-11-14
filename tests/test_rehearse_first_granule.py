"""Hermetic tests for the offline first-granule rehearsal.

The rehearsal itself lives in ``scripts/rehearse_first_granule.py``. These tests run it as a real
operator would (through the current interpreter), against a synthetic HDF5 stack under
``tmp_path``. Nothing here touches the network, credentials, ``data/processed`` or a downloaded
granule.

What is asserted:

* the script exits 0 and every rehearsable step reports ``rehearsed``;
* the artefacts it writes under ``/tmp`` pass the file-level gate on their own;
* a second run produces the same ``nisar_grid.parquet`` frame (determinism);
* an unusable work directory is refused with exit 2 and no traceback.

The test file is deliberately light on its own logic: if a step changes, the assertion belongs in
the rehearsal script, where the failure prints the exact command and the stderr tail.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

pd = pytest.importorskip("pandas")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rehearse_first_granule.py"
GATE = ROOT / "pipeline" / "validate_artifacts.py"

#: Steps the runbook can actually rehearse offline. If one disappears, the runbook lost coverage.
REQUIRED_REHEARSED_STEPS = {
    "step2-inspect",
    "step2-reader",
    "step3-probe",
    "step3-coherence",
    "step4-metadata-wins",
    "step4-missing-incidence",
    "step4-missing-heading",
    "step4-grid-incidence",
    "step5-force-guard",
    "step5-process",
    "step5-gate",
    "step5-check-phase-a-preflight",
    "step6-sign-gate",
    "step6-known-field",
}


def run_rehearsal(
    workdir: pathlib.Path, *, json_output: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run the real script through the current interpreter, exactly as an operator would."""
    argv = [sys.executable, str(SCRIPT), "--workdir", str(workdir)]
    if json_output:
        argv.append("--json")
    return subprocess.run(
        argv,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )


@pytest.fixture(scope="module")
def rehearsal(tmp_path_factory: pytest.TempPathFactory) -> tuple[pathlib.Path, dict]:
    """One successful rehearsal shared by the read-only assertions in this module."""
    workdir = tmp_path_factory.mktemp("rehearse-first-granule-a")
    completed = run_rehearsal(workdir)
    assert completed.returncode == 0, (
        f"the rehearsal failed (exit {completed.returncode}).\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "pass", payload
    return workdir, payload


# ---------------------------------------------------------------------------------------------
# 1. Exit status, table and the rehearsable/not-rehearsable split
# ---------------------------------------------------------------------------------------------


def test_script_exits_zero_and_every_rehearsable_step_reports_rehearsed(
    rehearsal: tuple[pathlib.Path, dict],
) -> None:
    _workdir, payload = rehearsal

    assert payload["runbook"] == "docs/first-granule-runbook.md"
    assert payload["summary"]["failed"] == 0
    assert payload["rows"], "the rehearsal emitted no rows"

    by_id = {row["id"]: row for row in payload["rows"]}
    missing = REQUIRED_REHEARSED_STEPS - set(by_id)
    assert not missing, f"the rehearsal table lost these rehearsable steps: {sorted(missing)}"

    for row in payload["rows"]:
        if row["rehearsable"]:
            assert row["verdict"].startswith("rehearsed"), row
        else:
            assert row["verdict"].startswith("not rehearsable offline:"), row
        assert not row["verdict"].startswith("failed"), row

    # The successful commands and the expected refusals have the documented exit codes.
    for step_id in REQUIRED_REHEARSED_STEPS - {
        "step4-missing-incidence",
        "step4-missing-heading",
        "step4-grid-incidence",
        "step5-force-guard",
    }:
        assert by_id[step_id]["exit_code"] == 0, by_id[step_id]
    for step_id in (
        "step4-missing-incidence",
        "step4-missing-heading",
        "step4-grid-incidence",
        "step5-force-guard",
    ):
        assert by_id[step_id]["exit_code"] == 2, by_id[step_id]

    # The commands that cannot be rehearsed are named, not silently skipped.
    not_rehearsable = {
        row["id"] for row in payload["rows"] if not row["rehearsable"]
    }
    assert {
        "step1-preconditions",
        "step1-get-granule",
        "step3-real-coverage-decision",
        "step4-real-geometry-decision",
        "step5-check-phase-a-full",
        "step6-gnss-provenance",
        "step7-record",
    } <= not_rehearsable


# ---------------------------------------------------------------------------------------------
# 2. What the commands actually printed (coverage, coherence, geometry, sign)
# ---------------------------------------------------------------------------------------------


def test_rehearsal_checks_the_documented_scientific_gates(
    rehearsal: tuple[pathlib.Path, dict],
) -> None:
    _workdir, payload = rehearsal
    checks = payload["checks"]

    # The fixture covers a small patch, so the 50% warning path must have fired. The real
    # granule's number is deliberately left to Step 3 of the runbook.
    coverage = checks["coverage"]
    assert 0.0 < coverage["fraction"] < 0.5
    assert coverage["warning_fired"] is True
    assert coverage["aoi_cells"] > coverage["covered_cells"] > 0

    # Step 3's separate coherence snippet must clear the frozen sensor threshold.
    assert checks["coherence_median"] >= 0.35

    # Step 6's sign gate, against the injected negative deformation.
    velocity = checks["velocity"]
    assert velocity["median_mm_yr"] < 0
    assert abs(velocity["median_mm_yr"]) >= 5.0
    assert -900.0 <= velocity["p05_mm_yr"] <= -225.0

    # Step 4's "metadata wins" rule: the deliberately-off flags must not be the recorded source.
    geometry = checks["geometry"]
    assert "radarGrid/incidenceAngleCenter" in geometry
    assert "radarGrid/heading" in geometry
    assert "CLI --incidence" not in geometry
    assert "CLI --heading" not in geometry

    # Step 2's structural read: HH discovered, mask semantics recorded, wavelength from metadata.
    reader = checks["reader"]
    assert reader["polarisation"] == "HH"
    assert reader["mask_values"] == [0, 2, 127]
    assert reader["mask_unknown_values"] == []
    assert reader["wavelength_m"] == pytest.approx(0.2384)
    assert str(reader["wavelength_source"]).startswith("metadata:")
    assert checks["gate_exit_code"] == 0


# ---------------------------------------------------------------------------------------------
# 3. The emitted /tmp artefacts pass the file-level gate independently
# ---------------------------------------------------------------------------------------------


def test_produced_tmp_artefacts_pass_the_gate(
    rehearsal: tuple[pathlib.Path, dict],
) -> None:
    _workdir, payload = rehearsal
    artifacts = payload["artifacts"]
    processed_root = pathlib.Path(artifacts["processed_root"])
    case_dir = pathlib.Path(artifacts["processed_case"])

    for name in (
        "nisar_grid.parquet",
        "timeseries.csv",
        "context_layers.geojson",
        "meta.json",
    ):
        path = case_dir / name
        assert path.is_file(), f"missing artefact: {path}"
        assert path.stat().st_size > 0, f"empty artefact: {path}"

    completed = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--processed",
            str(processed_root),
            "--json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    assert completed.returncode == 0, completed.stderr
    gate_payload = json.loads(completed.stdout)
    assert gate_payload["status"] == "pass"
    assert gate_payload["exit_code"] == 0
    assert gate_payload["cases_validated"] == 1
    assert gate_payload["problems"] == []
    assert gate_payload["totals"]["failed"] == 0


# ---------------------------------------------------------------------------------------------
# 4. Determinism: two rehearsals emit the same grid frame
# ---------------------------------------------------------------------------------------------


def test_rehearsal_is_deterministic(
    rehearsal: tuple[pathlib.Path, dict], tmp_path: pathlib.Path
) -> None:
    _first_workdir, first_payload = rehearsal

    second_workdir = tmp_path / "rehearse-first-granule-b"
    completed = run_rehearsal(second_workdir)
    assert completed.returncode == 0, completed.stderr
    second_payload = json.loads(completed.stdout)
    assert second_payload["status"] == "pass"

    first_grid = pd.read_parquet(first_payload["artifacts"]["nisar_grid"])
    second_grid = pd.read_parquet(second_payload["artifacts"]["nisar_grid"])
    pd.testing.assert_frame_equal(first_grid, second_grid)

    # The series is aggregated from the same field; comparing it catches a grid that happens to
    # sort identically while its values move.
    first_series = pd.read_csv(first_payload["artifacts"]["timeseries"])
    second_series = pd.read_csv(second_payload["artifacts"]["timeseries"])
    pd.testing.assert_frame_equal(first_series, second_series)


# ---------------------------------------------------------------------------------------------
# 5. Unusable work directory: a clean refusal, never a traceback
# ---------------------------------------------------------------------------------------------


def test_refuses_cleanly_when_workdir_is_unwritable(tmp_path: pathlib.Path) -> None:
    # A regular file where a parent directory is required makes mkdir fail for any user, including
    # root (chmod-based tests are unreliable under a root test runner).
    blocker = tmp_path / "blocker"
    blocker.write_text("this is a file, not a directory\n", encoding="utf-8")
    requested = blocker / "nested-workdir"

    completed = run_rehearsal(requested, json_output=False)

    assert completed.returncode == 2, (
        f"expected clean refusal (exit 2), got {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    lowered = completed.stderr.lower()
    assert "work directory is not writable" in lowered
    assert "traceback" not in lowered
    assert not requested.exists()

    # On a non-root runner, also prove a genuinely readonly directory is refused.
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o500)
        try:
            completed = run_rehearsal(readonly, json_output=False)
            assert completed.returncode == 2, completed.stderr
            assert "work directory is not writable" in completed.stderr.lower()
            assert "traceback" not in completed.stderr.lower()
        finally:
            readonly.chmod(0o700)
