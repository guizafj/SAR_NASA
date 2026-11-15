"""Tests for `pipeline/validate_artifacts.py` — the file-level contract gate.

Hermetic: every test builds the fixture in pytest's `tmp_path`; no network, no data/processed.
Fast: a handful of tiny parquet/CSV/JSON files per test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import validate_artifacts as va  # noqa: E402

NO_CONFIG: dict = {}


@pytest.fixture()
def fixture_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "processed"
    case = va.build_fixture(root)
    return root, case


def problems(report: va.RunReport) -> str:
    return "\n".join(va._all_problems(report))


def warnings(report: va.RunReport) -> str:
    return "\n".join(va._all_warnings(report))


def edit_meta(case: Path, mutate) -> None:
    path = case / va.META_ARTEFACT
    meta = json.loads(path.read_text(encoding="utf-8"))
    mutate(meta)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------------------------


def test_valid_fixture_passes(fixture_root):
    report = va.validate_tree(fixture_root[0], config=NO_CONFIG)

    assert report.cases, "fixture must be discovered as a case"
    assert not report.failed, problems(report)
    assert not va._all_warnings(report), warnings(report)
    assert report.exit_code == 0
    assert report.cases[0].case_id == "mexico-city"


def test_flat_layout_is_accepted(fixture_root):
    """A single case may live directly in data/processed (no per-case subdirectory)."""
    _root, case = fixture_root
    report = va.validate_tree(case, config=NO_CONFIG)

    assert len(report.cases) == 1
    assert report.cases[0].dir_name is None
    assert not report.failed, problems(report)


def test_human_and_json_rendering(fixture_root):
    report = va.validate_tree(fixture_root[0], config=NO_CONFIG)

    human = va.render_human(report)
    assert "PASS mexico-city" in human
    assert "RESULT: PASS" in human

    payload = va.report_to_dict(report)
    assert payload["status"] == "pass"
    assert payload["exit_code"] == 0
    assert payload["contract"]["schema_version"] == "1.1"
    assert payload["cases"][0]["case_id"] == "mexico-city"


# ---------------------------------------------------------------------------------------------
# The five injected defects required by the brief
# ---------------------------------------------------------------------------------------------


def test_missing_file_is_caught(fixture_root):
    _root, case = fixture_root
    (case / va.TIMESERIES_ARTEFACT).unlink()

    report = va.validate_tree(case.parent, config=NO_CONFIG)

    assert report.failed
    assert "missing required artefact 'timeseries.csv'" in problems(report)


def test_bad_delta_change_is_caught(fixture_root):
    _root, case = fixture_root
    path = case / va.GRID_ARTEFACT
    frame = pd.read_parquet(path)
    frame.loc[0, "delta_change"] = float(frame.loc[0, "delta_change"]) + 1.0
    frame.to_parquet(path, index=False)

    report = va.validate_tree(case.parent, config=NO_CONFIG)

    assert report.failed
    assert "delta_change != val_t2 - val_t1" in problems(report)


def test_orphan_provenance_id_is_caught(fixture_root):
    _root, case = fixture_root
    path = case / va.CONTEXT_ARTEFACT
    context = json.loads(path.read_text(encoding="utf-8"))
    context["features"][0]["properties"]["provenance_id"] = va.C.provenance_id(
        "mexico-city", "nisar-gunw", "2026-01-01", "deadbeef"
    )
    path.write_text(json.dumps(context), encoding="utf-8")

    report = va.validate_tree(case.parent, config=NO_CONFIG)

    assert report.failed
    assert "orphan provenance_id" in problems(report)


def test_oversized_case_is_caught(fixture_root):
    _root, case = fixture_root
    # Pad a contract artefact (trailing whitespace keeps the JSON valid): the size gate must
    # catch it on its own, without any schema violation alongside.
    context_path = case / va.CONTEXT_ARTEFACT
    context_path.write_text(
        context_path.read_text(encoding="utf-8") + " " * 60_000, encoding="utf-8"
    )

    report = va.validate_tree(case.parent, config=NO_CONFIG, budget_mb=0.02)

    assert report.failed
    messages = problems(report)
    assert "budget" in messages
    assert "largest: context_layers.geojson" in messages


def test_date_outside_meta_dates_is_caught(fixture_root):
    _root, case = fixture_root
    path = case / va.TIMESERIES_ARTEFACT
    series = pd.read_csv(path)
    series.loc[0, "date"] = "2026-08-01"
    series.to_csv(path, index=False)

    report = va.validate_tree(case.parent, config=NO_CONFIG)

    assert report.failed
    assert "not in meta.dates" in problems(report)


# ---------------------------------------------------------------------------------------------
# File hygiene and identity
# ---------------------------------------------------------------------------------------------


def test_undeclared_file_is_reported(fixture_root):
    _root, case = fixture_root
    (case / "leftover.tmp").write_text("stale", encoding="utf-8")

    report = va.validate_tree(case.parent, config=NO_CONFIG)

    assert report.failed
    assert "undeclared file 'leftover.tmp'" in problems(report)


def test_case_directory_name_mismatch_is_caught(fixture_root):
    root, case = fixture_root
    case.rename(root / "jakarta")

    report = va.validate_tree(root, config=NO_CONFIG)

    assert report.failed
    assert "case directory name 'jakarta'" in problems(report)


def test_availability_probe_directory_is_ignored(fixture_root):
    root, _case = fixture_root
    probe = root / "_availability"
    probe.mkdir()
    (probe / "mexico-city.json").write_text("{}", encoding="utf-8")

    report = va.validate_tree(root, config=NO_CONFIG)

    assert not report.failed, problems(report)


# ---------------------------------------------------------------------------------------------
# Honesty checks
# ---------------------------------------------------------------------------------------------


def test_declared_null_result_requires_detection_limit(fixture_root):
    _root, case = fixture_root
    config = {"cases": {"mexico-city": {"null_result": True}}}

    report = va.validate_tree(case.parent, config=config)

    assert report.failed
    assert "detection_limit_mm_yr" in problems(report)


def test_declared_null_result_with_limit_passes(fixture_root):
    _root, case = fixture_root

    def mutate(meta):
        meta["stats"]["median_velocity_mm_yr"] = -1.2
        meta["stats"]["detection_limit_mm_yr"] = 1.5

    edit_meta(case, mutate)

    report = va.validate_tree(
        case.parent, config={"cases": {"mexico-city": {"null_result": True}}}
    )

    assert not report.failed, problems(report)
    assert not va._all_warnings(report), warnings(report)


def test_small_median_without_null_flag_warns(fixture_root):
    _root, case = fixture_root

    def mutate(meta):
        meta["stats"]["median_velocity_mm_yr"] = -1.2
        meta["stats"]["detection_limit_mm_yr"] = 1.5

    edit_meta(case, mutate)

    report = va.validate_tree(case.parent, config=NO_CONFIG)

    assert not report.failed, problems(report)
    assert "not declared null_result" in warnings(report)


def test_zero_coherence_and_zero_velocity_warns(fixture_root):
    _root, case = fixture_root
    path = case / va.GRID_ARTEFACT
    frame = pd.read_parquet(path)
    frame.loc[0, "coherence"] = 0.0
    frame.loc[0, "velocity_mm_yr"] = 0.0
    frame.loc[0, "ratio_R"] = 0.0
    frame.to_parquet(path, index=False)

    report = va.validate_tree(case.parent, config=NO_CONFIG)

    assert not report.failed, problems(report)
    assert "coherence == 0 and velocity_mm_yr == 0" in warnings(report)


def test_strict_promotes_warnings_to_failure(fixture_root):
    _root, case = fixture_root

    def mutate(meta):
        meta["stats"]["median_velocity_mm_yr"] = -1.2
        meta["stats"]["detection_limit_mm_yr"] = 1.5

    edit_meta(case, mutate)

    assert va.validate_tree(case.parent, config=NO_CONFIG).exit_code == 0
    strict = va.validate_tree(case.parent, config=NO_CONFIG, strict=True)
    assert strict.exit_code == 1
    assert "RESULT: FAIL" in va.render_human(strict)


# ---------------------------------------------------------------------------------------------
# The self-test itself
# ---------------------------------------------------------------------------------------------


def test_self_test_passes():
    assert va.run_self_test(verbose=False) == 0


# ---------------------------------------------------------------------------------------------
# Fail-closed: a run that validated nothing is never a pass
# ---------------------------------------------------------------------------------------------


def assert_status_agrees(report: va.RunReport) -> None:
    """`--json` status and exit_code must never contradict each other [or the real exit code]."""
    payload = va.report_to_dict(report)
    assert payload["status"] == ("fail" if payload["exit_code"] else "pass")
    assert payload["exit_code"] == report.exit_code
    assert payload["status"] == report.status
    assert payload["cases_validated"] == len(payload["cases"])


def test_empty_tree_fails(tmp_path):
    root = tmp_path / "processed"
    root.mkdir()

    report = va.validate_tree(root, config=NO_CONFIG)

    assert report.cases == []
    assert report.failed
    assert report.exit_code == 1
    assert report.vacuous is False
    assert "nothing was validated" in "\n".join(report.problems)
    assert_status_agrees(report)

    human = va.render_human(report)
    assert "RESULT: FAIL" in human
    assert "nothing was validated" in human


def test_empty_tree_allow_empty_is_vacuous_not_a_silent_pass(tmp_path):
    root = tmp_path / "processed"
    root.mkdir()

    report = va.validate_tree(root, config=NO_CONFIG, allow_empty=True)

    assert not report.failed
    assert report.exit_code == 0
    assert report.vacuous is True
    assert_status_agrees(report)

    human = va.render_human(report)
    assert "RESULT: PASS (exit 0)" in human
    assert "vacuous" in human.lower()
    assert "0 cases validated" in human

    payload = va.report_to_dict(report)
    assert payload["vacuous"] is True and payload["allow_empty"] is True
    assert any("vacuous" in note.lower() for note in payload["notes"])


def test_empty_existing_dir_fails_even_under_strict(tmp_path):
    """Defect #3: an existing but empty tree must not PASS, strict or not."""
    root = tmp_path / "processed"
    root.mkdir()

    for strict in (False, True):
        report = va.validate_tree(root, config=NO_CONFIG, strict=strict)
        assert report.exit_code == 1, f"strict={strict}"
        assert "nothing was validated" in "\n".join(report.problems)
        assert_status_agrees(report)


def test_missing_case_filter_fails_in_text(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(va, "_load_config", lambda *a, **k: {})
    root = tmp_path / "processed"
    va.build_fixture(root)

    code = va.main(["--processed", str(root), "--case", "does-not-exist"])
    out = capsys.readouterr().out

    assert code == 1
    assert "not found" in out
    assert "nothing was validated" in out
    assert "RESULT: FAIL" in out


def test_missing_case_filter_fails_in_json(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(va, "_load_config", lambda *a, **k: {})
    root = tmp_path / "processed"
    va.build_fixture(root)

    code = va.main(["--processed", str(root), "--case", "does-not-exist", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["exit_code"] == 1
    assert payload["status"] == "fail"
    assert payload["cases"] == []
    assert payload["requested_case"] == "does-not-exist"
    assert any("not found" in message for message in payload["problems"])


def test_allow_empty_does_not_excuse_a_missing_case_filter(tmp_path):
    root = tmp_path / "processed"
    va.build_fixture(root)

    report = va.validate_tree(
        root, only_case="does-not-exist", config=NO_CONFIG, allow_empty=True
    )

    assert report.failed
    assert report.vacuous is False
    assert_status_agrees(report)


def test_partial_artefact_set_is_discovered_and_failed_not_skipped(tmp_path):
    """A directory holding only some artefacts must be a case that fails, not a skipped dir."""
    root = tmp_path / "processed"
    case = root / "mexico-city"
    case.mkdir(parents=True)
    (case / va.META_ARTEFACT).write_text("{}", encoding="utf-8")

    report = va.validate_tree(root, config=NO_CONFIG)

    assert len(report.cases) == 1, "partial case must be discovered"
    assert report.failed
    for name in (va.GRID_ARTEFACT, va.TIMESERIES_ARTEFACT, va.CONTEXT_ARTEFACT):
        assert f"missing required artefact {name!r}" in problems(report)
    assert not report.notes or not any("skipped" in note for note in report.notes)
    assert_status_agrees(report)


def test_cli_valid_fixture_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(va, "_load_config", lambda *a, **k: {})
    root = tmp_path / "processed"
    va.build_fixture(root)

    assert va.main(["--processed", str(root)]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out

    assert va.main(["--processed", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass" and payload["exit_code"] == 0
    assert payload["cases_validated"] == 1 and payload["vacuous"] is False


def test_cli_empty_tree_exit_code_and_vacuous_override(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(va, "_load_config", lambda *a, **k: {})
    root = tmp_path / "processed"
    root.mkdir()

    assert va.main(["--processed", str(root)]) == 1
    assert "nothing was validated" in capsys.readouterr().out

    assert va.main(["--processed", str(root), "--allow-empty"]) == 0
    out = capsys.readouterr().out
    assert "vacuous" in out.lower()

    assert va.main(["--processed", str(root), "--allow-empty", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass" and payload["exit_code"] == 0
    assert payload["vacuous"] is True


# ---------------------------------------------------------------------------------------------
# Audited fail-open routes beyond the three reported defects
# ---------------------------------------------------------------------------------------------


def test_missing_h3_engine_fails_closed(fixture_root, monkeypatch):
    """The H3 guard used to skip silently; a check that cannot run must not report success."""
    root, _case = fixture_root
    monkeypatch.setattr(va, "h3", None)

    report = va.validate_tree(root, config=NO_CONFIG)

    assert report.failed
    assert "h3 is not installed" in "\n".join(report.problems)
    assert "RESULT: FAIL" in va.render_human(report)
    assert_status_agrees(report)


def test_unreadable_config_is_a_warning_and_strict_fails(fixture_root, monkeypatch):
    """A skipped config check is at least visible; --strict turns it into a failure."""
    root, _case = fixture_root
    monkeypatch.setattr(va, "yaml", None)

    report = va.validate_tree(root, config=None)
    assert not report.failed, "warnings keep their semantics without --strict"
    assert any("case config could not be loaded" in w for w in report.warnings)
    assert "RUN-LEVEL WARNINGS" in va.render_human(report)
    assert_status_agrees(report)

    strict = va.validate_tree(root, config=None, strict=True)
    assert strict.exit_code == 1
    assert "RESULT: FAIL" in va.render_human(strict)
    assert_status_agrees(strict)


def test_stray_subdirectory_is_reported_not_silently_ignored(fixture_root):
    root, _case = fixture_root
    (root / "half-generated").mkdir()

    report = va.validate_tree(root, config=NO_CONFIG)
    assert not report.failed
    assert any("half-generated" in w for w in report.warnings)

    strict = va.validate_tree(root, config=NO_CONFIG, strict=True)
    assert strict.exit_code == 1
    assert_status_agrees(strict)


def test_flat_artefacts_shadowed_by_case_dirs_are_reported(tmp_path):
    """Root artefacts next to case subdirectories used to be silently unvalidated."""
    root = tmp_path / "processed"
    va.build_fixture(root)
    for name in va.ARTEFACTS:
        (root / name).write_bytes((root / "mexico-city" / name).read_bytes())

    report = va.validate_tree(root, config=NO_CONFIG)

    assert report.failed
    assert "shadowed" in "\n".join(report.problems)
    assert_status_agrees(report)
