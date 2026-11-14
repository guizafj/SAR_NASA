"""The honesty claims must be true *inside the artefacts*, not only in the README.

The README, the handoff document and the runbook all promise that a reader can find (a) an explicit
"synthetic" label while the product is mock, (b) the ~4.5-month window caveat that forbids publishing
a seasonal amplitude, (c) the posting caveat for the city-scale case, and (d) a detection bound
whenever the signal is at the floor. Those sentences live in `meta.limitations` / `meta.provenance`,
which is what the app renders — so losing one silently degrades the submission's honesty without
breaking any schema check.

This module asserts the content, so a future edit to the config or the generator cannot quietly
remove a caveat.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data/processed"

CASES = sorted(
    p.name
    for p in PROCESSED.iterdir()
    if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "meta.json").exists()
) if PROCESSED.exists() else []

pytestmark = pytest.mark.skipif(not CASES, reason="no artefacts yet — run pipeline/generate_mock.py")


@pytest.fixture(params=CASES, ids=CASES)
def case(request) -> dict:
    directory = PROCESSED / request.param
    return {
        "id": request.param,
        "meta": json.loads((directory / "meta.json").read_text()),
        "limitations": " ".join(json.loads((directory / "meta.json").read_text())["limitations"]).lower(),
    }


def test_a_synthetic_product_says_so_in_its_own_metadata(case):
    """`product: mock` must be echoed by the provenance, verbatim enough to render as a badge."""
    meta = case["meta"]
    if meta["product"] != "mock":
        pytest.skip(f"{case['id']} is not mock — this claim does not apply")
    source = str(meta["provenance"]["source"]).lower()
    assert "mock" in source, f"{case['id']}: product is mock but provenance.source says {source!r}"
    assert "synthetic" in case["limitations"] or "mock" in case["limitations"], (
        f"{case['id']}: no limitation tells the reader the numbers are synthetic"
    )
    method = str(meta["provenance"]["method"]).lower()
    assert any(word in method for word in ("synthetic", "mock", "no observation")), (
        f"{case['id']}: provenance.method does not describe the data as synthesised: {method[:120]!r}"
    )


def test_the_window_caveat_is_present(case):
    """A ~4.5-month window cannot separate seasonal from secular motion — every case must say so."""
    assert "4.5-month" in case["limitations"] or "4.5 month" in case["limitations"], (
        f"{case['id']}: limitations do not carry the short-window caveat, so a reader could publish "
        f"seasonal_amp_mm from a stack that cannot support it"
    )
    assert "seasonal" in case["limitations"], (
        f"{case['id']}: the window caveat exists but does not mention the seasonal ambiguity"
    )


def test_the_posting_caveat_marks_the_resolution_limit(case):
    """City scale, not building scale: GUNW's 80 m posting must be stated where it applies."""
    meta = case["meta"]
    if meta["product"] not in {"mock", "nisar-gunw"}:
        pytest.skip(f"{case['id']}: posting caveat is product-specific")
    assert "80 m" in case["limitations"] or "posting" in case["limitations"], (
        f"{case['id']}: no limitation states the posting / city-scale limit for {meta['product']}"
    )


def test_a_null_result_publishes_its_bound(case):
    """If the median is at the floor, the detection limit is the result — assert it is there."""
    stats = case["meta"]["stats"]
    median = float(stats["median_velocity_mm_yr"])
    if abs(median) >= 5.0:
        pytest.skip(f"{case['id']}: |median| = {abs(median):.1f} mm/yr is above the null-result band")
    assert isinstance(stats.get("detection_limit_mm_yr"), (int, float)), (
        f"{case['id']}: |median| {abs(median):.1f} < 5 mm/yr is a null result but "
        f"stats.detection_limit_mm_yr is missing"
    )
    assert "detection" in case["limitations"], (
        f"{case['id']}: no limitation says the bound IS the result"
    )


def test_limitations_are_substantive_not_decorative(case):
    """A single generic sentence is not a limitations section."""
    limitations = case["meta"]["limitations"]
    assert len(limitations) >= 3, f"{case['id']}: only {len(limitations)} limitation(s)"
    assert all(len(str(item).strip()) > 20 for item in limitations), (
        f"{case['id']}: a limitation is too short to say anything"
    )
