"""Hermetic tests for ``scripts/demo_numbers.py`` — the demo number card.

The card is what the presenter reads out loud, so the tests treat every sentence as a claim that
must be re-derived:

* numbers come from the parquet (pandas is the oracle), not from ``meta.stats`` alone;
* the contrast ratio and the shared legend are recomputed from those numbers;
* every ``meta.limitations[]`` string appears verbatim;
* the mock warning is gated on ``product == "mock"`` in the artefacts, never on a filename or a
  provenance string;
* ``--json`` and the text card are the same document;
* a missing or inconsistent case is skipped with a note and exit 0.

Every test runs the real script in a subprocess with ``sys.executable`` against a ``tmp_path`` copy
of ``data/processed/``. Nothing writes to the repository, nothing reads a clock, nothing touches
the network. The whole module skips cleanly when ``data/processed/`` has no generated case.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import shutil
import subprocess
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_numbers.py"
SOURCE = ROOT / "data" / "processed"
REQUIRED_FILES = ("meta.json", "nisar_grid.parquet")

#: mm/yr granularity of the shared legend; a presentation choice, re-derived by the test too.
LEGEND_STEP_MM_YR = 25.0


def _source_cases() -> list[str]:
    if not SOURCE.is_dir():
        return []
    return sorted(
        path.name
        for path in SOURCE.iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and all((path / name).is_file() for name in REQUIRED_FILES)
    )


CASES = _source_cases()

pytestmark = pytest.mark.skipif(
    not CASES,
    reason=(
        "no generated cases under data/processed/ — run "
        "`./scripts/python.sh pipeline/generate_mock.py` before testing the demo card"
    ),
)


# ---------------------------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------------------------


@pytest.fixture()
def processed(tmp_path: pathlib.Path) -> pathlib.Path:
    """A disposable copy of the artefact tree; tests may corrupt it freely."""
    root = tmp_path / "processed"
    root.mkdir()
    for case in CASES:
        shutil.copytree(SOURCE / case, root / case)
    return root


def _run(processed_root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--processed", str(processed_root), *args]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _as_json(processed_root: pathlib.Path, *args: str) -> dict:
    result = _run(processed_root, "--json", *args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _as_text(processed_root: pathlib.Path, *args: str) -> str:
    result = _run(processed_root, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _meta(processed_root: pathlib.Path, case: str) -> dict:
    return json.loads((processed_root / case / "meta.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------
# 1. Per-case numbers: the parquet is the oracle, meta.stats is only a cross-check
# ---------------------------------------------------------------------------------------------


def test_case_numbers_equal_what_pandas_reads_from_the_parquet(processed: pathlib.Path) -> None:
    document = _as_json(processed)
    by_case = {case["case_id"]: case for case in document["cases"]}
    assert set(by_case) == set(CASES)

    text = _as_text(processed)
    for case in CASES:
        frame = pd.read_parquet(processed / case / "nisar_grid.parquet")
        velocity = frame["velocity_mm_yr"]
        coherence = frame["coherence"]
        meta = _meta(processed, case)
        entry = by_case[case]

        assert entry["cells"] == len(frame)
        assert entry["median_velocity_mm_yr"] == pytest.approx(float(velocity.median()))
        assert entry["p05_velocity_mm_yr"] == pytest.approx(float(velocity.quantile(0.05)))
        assert entry["p95_velocity_mm_yr"] == pytest.approx(float(velocity.quantile(0.95)))
        assert entry["spread_mm_yr"] == pytest.approx(
            float(velocity.quantile(0.95)) - float(velocity.quantile(0.05))
        )
        assert entry["coherence_min"] == pytest.approx(float(coherence.min()))
        assert entry["coherence_max"] == pytest.approx(float(coherence.max()))
        assert entry["date_first"] == meta["dates"][0]
        assert entry["date_last"] == meta["dates"][-1]
        assert entry["n_dates"] == len(meta["dates"])
        assert entry["revisit_days"] == meta["revisit_days"]
        assert entry["provenance_id"] == meta["provenance"]["provenance_id"]
        assert entry["n_limitations"] == len(meta["limitations"])

        # The text card must carry the same numbers, to display precision.
        assert f"{entry['median_velocity_mm_yr']:.2f}" in text
        assert f"{entry['p05_velocity_mm_yr']:.2f}" in text
        assert f"{entry['p95_velocity_mm_yr']:.2f}" in text
        assert f"{entry['spread_mm_yr']:.2f}" in text
        assert f"{entry['cells']:,}" in text
        assert entry["provenance_id"] in text
        assert entry["date_first"] in text and entry["date_last"] in text


# ---------------------------------------------------------------------------------------------
# 2. Contrast: spread, ratio, shared legend — all recomputed here, independently
# ---------------------------------------------------------------------------------------------


def test_contrast_ratio_and_shared_legend_are_recomputed(processed: pathlib.Path) -> None:
    document = _as_json(processed)
    velocities = {
        case: pd.read_parquet(processed / case / "nisar_grid.parquet")["velocity_mm_yr"]
        for case in CASES
    }
    spreads = {
        case: float(values.quantile(0.95) - values.quantile(0.05))
        for case, values in velocities.items()
    }
    strongest = max(spreads, key=lambda case: (spreads[case], case))
    null_cases = [
        case["case_id"] for case in document["cases"] if case["detection_limit_mm_yr"] is not None
    ]
    assert null_cases, "the artefacts must declare at least one null case with a detection bound"
    null_case = min(null_cases, key=lambda case: (spreads[case], case))
    ratio = spreads[strongest] / spreads[null_case]
    max_abs = max(
        max(abs(float(values.min())), abs(float(values.max())))
        for values in velocities.values()
    )
    legend = math.ceil(max_abs / LEGEND_STEP_MM_YR) * LEGEND_STEP_MM_YR

    contrast = document["contrast"]
    assert contrast["strongest_case"] == strongest
    assert contrast["null_case"] == null_case
    assert contrast["strongest_spread_mm_yr"] == pytest.approx(spreads[strongest])
    assert contrast["null_spread_mm_yr"] == pytest.approx(spreads[null_case])
    assert contrast["ratio"] == pytest.approx(ratio)
    assert contrast["shared_scale_mm_yr"] == legend
    assert contrast["max_abs_velocity_mm_yr"] == pytest.approx(max_abs)
    # The legend must cover every cell, including the tail beyond p05.
    assert contrast["shared_scale_mm_yr"] >= max_abs

    text = _as_text(processed)
    assert f"{ratio:.1f}x" in text
    assert f"±{legend:g} mm/yr" in text
    assert "SHARED scale" in text
    assert "not a per-case scale" in text
    assert "clipping at p05 hides the core" in text


# ---------------------------------------------------------------------------------------------
# 3. Mock warning: artefact product field, never a filename or provenance string
# ---------------------------------------------------------------------------------------------


def test_mock_warning_appears_exactly_when_product_is_mock(processed: pathlib.Path) -> None:
    warning_marker = "every number below is synthetic"
    text = _as_text(processed)
    assert warning_marker in text
    assert "badge must say MOCK" in text

    # Flip every case to a real product in BOTH meta.json and the grid, while deliberately
    # leaving the mock-flavoured provenance_id and limitations in place: if the warning were
    # gated on a filename/provenance string it would survive this edit, and that is the bug.
    for case in CASES:
        meta_path = processed / case / "meta.json"
        meta = _meta(processed, case)
        meta["product"] = "nisar-gunw"
        meta["sensor"] = "nisar"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        grid_path = processed / case / "nisar_grid.parquet"
        grid = pd.read_parquet(grid_path)
        grid["product"] = "nisar-gunw"
        grid["sensor"] = "nisar"
        grid.to_parquet(grid_path, index=False)

    real_text = _as_text(processed)
    assert warning_marker not in real_text
    assert "badge must say MOCK" not in real_text
    real_document = _as_json(processed)
    assert real_document["honesty"]["mock_warning"] is None
    assert real_document["honesty"]["mock_cases"] == []
    assert {case["product"] for case in real_document["cases"]} == {"nisar-gunw"}


# ---------------------------------------------------------------------------------------------
# 4. Limitations are verbatim, in both views
# ---------------------------------------------------------------------------------------------


def test_every_limitation_appears_verbatim(processed: pathlib.Path) -> None:
    text = _as_text(processed)
    document = _as_json(processed)
    for case in CASES:
        limitations = _meta(processed, case)["limitations"]
        assert document["honesty"]["limitations"][case] == limitations
        for limitation in limitations:
            assert limitation in text, f"{case}: limitation missing from the card: {limitation!r}"


# ---------------------------------------------------------------------------------------------
# 5. --json and text are the same document
# ---------------------------------------------------------------------------------------------


def test_json_agrees_with_the_text_card(processed: pathlib.Path) -> None:
    document = _as_json(processed)
    text = _as_text(processed)

    for case in document["cases"]:
        assert f"CASE {case['case_id']}" in text
        assert case["provenance_id"] in text
        for limitation in case["limitations"]:
            assert limitation in text

    for item in document["honesty"]["do_not_claim"]:
        assert item in text
    for sentence in document["skeleton"]:
        assert sentence in text
    for null in document["null_result"]:
        assert null["sentence"] in text
        assert f"{null['detection_limit_mm_yr']:.2f}" in text
        assert f"{null['median_velocity_mm_yr']:.2f}" in text
    for block in document["scale_check"]:
        assert f"{block['one_radian_los_mm']:.2f}" in text
        assert f"{block['one_radian_vertical_mm']:.2f}" in text
        assert f"{block['one_fringe_los_mm']:.2f}" in text
        assert f"{block['one_fringe_vertical_mm']:.2f}" in text

    assert document["contrast"]["ratio"] is not None


# ---------------------------------------------------------------------------------------------
# 6. Phase→mm scale check is computed through the library, not typed into the script
# ---------------------------------------------------------------------------------------------


def test_phase_to_mm_scale_check_comes_from_the_library(processed: pathlib.Path) -> None:
    sys.path.insert(0, str(ROOT))
    from pipeline.ingest import nisar_gunw as G  # noqa: PLC0415
    from pipeline.science import transforms as T  # noqa: PLC0415

    document = _as_json(processed)
    assert document["scale_check"], "the card must publish the scale check"
    for block in document["scale_check"]:
        expected_los = float(
            G.phase_to_los_displacement_mm(1.0, block["wavelength_m"], sign=1.0)
        )
        expected_vertical = float(
            T.vertical_from_los(
                expected_los, block["incidence_deg"], block["heading_deg"]
            )
        )
        expected_fringe = float(
            G.phase_to_los_displacement_mm(2.0 * math.pi, block["wavelength_m"], sign=1.0)
        )
        expected_fringe_vertical = float(
            T.vertical_from_los(
                expected_fringe, block["incidence_deg"], block["heading_deg"]
            )
        )
        assert block["one_radian_los_mm"] == pytest.approx(expected_los)
        assert block["one_radian_vertical_mm"] == pytest.approx(expected_vertical)
        assert block["one_fringe_los_mm"] == pytest.approx(expected_fringe)
        assert block["one_fringe_vertical_mm"] == pytest.approx(expected_fringe_vertical)
        # Sanity: a fringe is 2*pi radians of the same linear scale.
        assert block["one_fringe_los_mm"] == pytest.approx(2.0 * math.pi * expected_los)
        assert block["one_radian_vertical_mm"] == pytest.approx(
            expected_los / math.cos(math.radians(block["incidence_deg"]))
        )


# ---------------------------------------------------------------------------------------------
# 7. --case restricts, and a missing/inconsistent case is skipped rather than fatal
# ---------------------------------------------------------------------------------------------


def test_case_flag_restricts_the_card_to_one_case(processed: pathlib.Path) -> None:
    target = CASES[0]
    document = _as_json(processed, "--case", target)
    assert [case["case_id"] for case in document["cases"]] == [target]
    assert document["contrast"]["spreads_mm_yr"].keys() == {target}

    text = _as_text(processed, "--case", target)
    assert f"CASE {target}" in text
    for other in CASES:
        if other != target:
            assert f"CASE {other}" not in text


def test_missing_case_is_skipped_with_a_note_not_a_crash(processed: pathlib.Path) -> None:
    missing = CASES[0]
    shutil.rmtree(processed / missing)

    document = _as_json(processed)
    assert missing not in {case["case_id"] for case in document["cases"]}
    skipped = {entry["case"]: entry["reason"] for entry in document["skipped"]}
    assert missing in skipped
    assert "missing" in skipped[missing]

    text = _as_text(processed)
    assert "SKIPPED" in text
    assert missing in text


def test_inconsistent_case_is_skipped_instead_of_published(processed: pathlib.Path) -> None:
    broken = CASES[-1]
    meta_path = processed / broken / "meta.json"
    meta = _meta(processed, broken)
    original = meta["stats"]["median_velocity_mm_yr"]
    meta["stats"]["median_velocity_mm_yr"] = original + 100.0
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    document = _as_json(processed)
    assert broken not in {case["case_id"] for case in document["cases"]}
    skipped = {entry["case"]: entry["reason"] for entry in document["skipped"]}
    assert broken in skipped
    assert "disagrees" in skipped[broken]
    # The healthy cases are still published.
    assert set(CASES) - {broken} == {case["case_id"] for case in document["cases"]}


# ---------------------------------------------------------------------------------------------
# 8. Presentation contract: deterministic, parseable, terminal-sized
# ---------------------------------------------------------------------------------------------


def test_card_is_deterministic_and_fits_the_line_budget(processed: pathlib.Path) -> None:
    first = _as_text(processed)
    second = _as_text(processed)
    assert first == second
    assert len(first.splitlines()) <= 60, "the demo card must stay under 60 terminal lines"
