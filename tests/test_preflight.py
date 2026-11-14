"""Hermetic tests for ``scripts/preflight.sh`` — the pre-submission gate runner.

Every test shells out to the real script (subprocess, cwd=repo root) and only uses
``--fast`` so the two deliberately slow gates (the full pytest suite, the committed-state
clone) are exercised as SKIP rows instead of costing minutes per test. ``TMPDIR`` is
redirected into ``tmp_path`` so the script's own scratch directory is observable: it must
exist during the run and be gone afterwards (trap cleanup, nothing written into the repo).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "preflight.sh"
PROCESSED = ROOT / "data" / "processed"

CASES = (
    sorted(
        path.name
        for path in PROCESSED.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )
    if PROCESSED.is_dir()
    else []
)

pytestmark = pytest.mark.skipif(
    not CASES, reason="data/processed has no case directories — nothing to preflight"
)

ALL_GATES = [
    "contract self-check",
    "pytest suite",
    "artefact gate",
    "phase A/B handoff",
    "clone reproduction",
    "figures regenerate",
    "demo numbers",
]


def run_preflight(
    *args: str, tmp_root: Path, timeout: int = 900
) -> subprocess.CompletedProcess[str]:
    """Run the real script from the repo root with all scratch data under ``tmp_root``."""
    env = dict(os.environ)
    # Keep the nested pytest inside check_phase_a.sh from racing the outer run on the repo's
    # .pytest_cache; this changes no gate, only where the cache would be written.
    env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    return subprocess.run(
        [str(PREFLIGHT), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def table_rows(stdout: str) -> dict[str, str]:
    """Map gate name -> full table row line for the text table."""
    rows: dict[str, str] = {}
    for line in stdout.splitlines():
        for name in ALL_GATES:
            if line.startswith(("1 ", "2 ", "3 ", "4 ", "5 ", "6 ", "7 ")) and name in line:
                rows[name] = line
    return rows


def gate_status(row: str) -> str:
    """Status column of a table row (PASS / FAIL / SKIP), i.e. the first status word."""
    match = re.search(r"\b(PASS|FAIL|SKIP)\b", row)
    assert match is not None, f"row has no status column: {row!r}"
    return match.group(1)


def test_script_is_executable() -> None:
    assert PREFLIGHT.is_file(), "scripts/preflight.sh is missing"
    assert os.access(PREFLIGHT, os.X_OK), "scripts/preflight.sh is not executable"


def test_fast_mode_prints_all_seven_rows_and_verdict_matches_exit_code(
    tmp_path: Path,
) -> None:
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    result = run_preflight("--fast", tmp_root=tmp_root)

    context = result.stdout + result.stderr
    assert "preflight —" in result.stdout, context

    rows = table_rows(result.stdout)
    assert set(rows) == set(ALL_GATES), f"missing table rows: {set(ALL_GATES) - set(rows)}"
    order = [
        int(match.group(1))
        for line in result.stdout.splitlines()
        if (match := re.match(r"^([1-7])\s{2,}\S", line))
    ]
    assert order == [1, 2, 3, 4, 5, 6, 7], f"table rows are not 1..7: {order}"

    # --fast executes 1, 3, 4, 6, 7 and skips exactly the two slow gates, with the reason shown.
    for slow in ("pytest suite", "clone reproduction"):
        assert gate_status(rows[slow]) == "SKIP", rows[slow]
        assert "--fast" in rows[slow], rows[slow]
    for executed in ("contract self-check", "artefact gate", "phase A/B handoff",
                     "figures regenerate", "demo numbers"):
        assert gate_status(rows[executed]) != "SKIP", f"{executed} must run under --fast: {rows[executed]}"

    # The verdict must agree with the rows: exit 0 iff no executed gate failed. On a healthy tree
    # that means exit 0 and READY; if another team's gate is red (docs/evidence mid-edit, say),
    # preflight must instead say NOT READY and name it rather than pretend the tree is fine.
    failed = [name for name in ALL_GATES if gate_status(rows[name]) == "FAIL"]
    if failed:
        assert result.returncode != 0, f"preflight claimed success with red gates: {failed}"
        assert "VERDICT: NOT READY" in result.stdout, result.stdout
        verdict = result.stdout.split("VERDICT:", 1)[1]
        for name in failed:
            assert name in verdict, f"verdict does not name failed gate {name!r}"
    else:
        assert result.returncode == 0, (
            "preflight --fast failed although every executed gate reported PASS:\n" + context
        )
        assert "VERDICT: READY" in result.stdout, result.stdout
        assert "failed:" not in result.stdout.split("VERDICT:", 1)[1], result.stdout

    # The scratch dir is created under TMPDIR and removed by the EXIT trap.
    leftovers = list(tmp_root.iterdir())
    assert leftovers == [], f"preflight left scratch files behind: {leftovers}"


def test_json_output_has_one_entry_per_gate_with_status_and_seconds(tmp_path: Path) -> None:
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    result = run_preflight("--fast", "--json", tmp_root=tmp_root)

    document = json.loads(result.stdout)  # raises if stdout is not clean JSON
    steps = document["steps"]
    assert len(steps) == 7, f"expected one entry per gate, got {len(steps)}"
    assert [step["id"] for step in steps] == [1, 2, 3, 4, 5, 6, 7]

    for step in steps:
        assert set(step) >= {"id", "name", "status", "exit", "seconds", "evidence"}
        assert step["status"] in {"PASS", "FAIL", "SKIP"}
        assert isinstance(step["seconds"], (int, float))
        assert step["seconds"] >= 0
        assert step["evidence"], f"gate {step['id']} has no evidence"

    by_id = {step["id"]: step for step in steps}
    assert by_id[2]["status"] == "SKIP"
    assert by_id[5]["status"] == "SKIP"
    for skipped in (2, 5):
        assert "--fast" in by_id[skipped]["evidence"], by_id[skipped]

    # JSON verdict must agree with the per-step statuses (see the text-table test above).
    failed = [step["name"] for step in steps if step["status"] == "FAIL"]
    assert document["failed"] == failed
    if failed:
        assert result.returncode != 0
        assert document["ready"] is False
        assert document["verdict"] == "NOT READY"
    else:
        assert result.returncode == 0
        assert document["ready"] is True
        assert document["verdict"] == "READY"


def test_empty_processed_dir_fails_and_names_the_gate(tmp_path: Path) -> None:
    empty = tmp_path / "processed"
    empty.mkdir()
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()

    result = run_preflight("--fast", "--processed", str(empty), tmp_root=tmp_root)

    assert result.returncode != 0, "an empty artefact root must not be reported as ready"
    assert "NOT READY" in result.stdout

    rows = table_rows(result.stdout)
    assert gate_status(rows["artefact gate"]) == "FAIL", rows["artefact gate"]
    assert "total size" in rows["artefact gate"], "the row must show why the gate failed"
    # The verdict lists the failing steps by name.
    verdict = result.stdout.split("VERDICT:", 1)[1]
    assert "artefact gate (size)" in verdict
    # The failing gate's log tail is surfaced on stderr, together with the artefact root it read.
    assert "FAIL artefact gate (size)" in result.stderr
    assert str(empty) in result.stderr
