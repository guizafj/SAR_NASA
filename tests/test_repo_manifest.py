"""Tripwire for silently lost files.

On 2026-11-14 a `git reset --hard` reverted every tracked file to HEAD and deleted a staged-only
script. `git` reported **no deletion** — HEAD had never seen the file — and the loss was only
noticed because a documentation test asked for it. This module makes that failure loud and instant:
the critical paths are listed here, so losing one fails a test rather than surfacing days later.

Keeping this list short is deliberate. It is not a file inventory; it is the set of files whose
absence would break another person's work or a documented command.
"""

from __future__ import annotations

import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Files whose disappearance breaks a documented workflow, the handoff, or the gate itself.
CRITICAL = (
    # the gate and the environment wrapper — a staged-only file was lost from here once
    "scripts/check_phase_a.sh",
    "scripts/python.sh",
    # the frozen contract, in both halves
    "src/domain/contracts.py",
    "docs/data-contract-v1.md",
    # the case definition of record and the dependency list a fresh clone needs
    "config/study_cases.yaml",
    "requirements.txt",
    "pytest.ini",
    # the artefact gate and the mock generator: without these there are no artefacts
    "pipeline/validate_artifacts.py",
    "pipeline/generate_mock.py",
    # documents the team is told to read
    "docs/data-source-decision.md",
    "docs/nisar-gunw-layout.md",
    "docs/handoff-mock.md",
    "docs/first-granule-runbook.md",
)

#: Scripts a caller executes directly; losing the executable bit breaks the documented command.
EXECUTABLE = (
    "scripts/check_phase_a.sh",
    "scripts/python.sh",
)


@pytest.mark.parametrize("relative", CRITICAL, ids=lambda p: p.replace("/", ":"))
def test_critical_file_exists(relative: str) -> None:
    path = ROOT / relative
    assert path.is_file(), (
        f"{relative} is missing. It is listed in tests/test_repo_manifest.py as critical, so its "
        f"absence breaks a documented workflow — check `git status` and restore it before committing."
    )


@pytest.mark.parametrize("relative", EXECUTABLE, ids=lambda p: p.replace("/", ":"))
def test_critical_script_is_executable(relative: str) -> None:
    path = ROOT / relative
    assert path.is_file(), f"{relative} is missing"
    assert os.access(path, os.X_OK), f"{relative} exists but lost its executable bit"


def test_no_critical_file_is_empty() -> None:
    """A truncated or zero-byte file is as broken as a missing one, and harder to spot."""
    empty = [p for p in CRITICAL if (ROOT / p).is_file() and (ROOT / p).stat().st_size == 0]
    assert not empty, f"these critical files are empty: {empty}"


def test_documents_the_team_is_told_to_read_are_reachable() -> None:
    """Every `docs/*.md` linked from README must exist — a dangling link is a broken instruction."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    import re

    linked = set(re.findall(r"\]\((docs/[A-Za-z0-9._-]+\.md)\)", readme))
    missing = sorted(target for target in linked if not (ROOT / target).is_file())
    assert not missing, f"README links to missing documents: {missing}"
