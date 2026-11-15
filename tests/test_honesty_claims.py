"""The honesty claims must be true *inside the artefacts*, not only in the README.

The README, the handoff document and the runbook all promise that a reader can find (a) an explicit
"synthetic" label while the product is mock, (b) the window caveat that states how far the observation
span identifies the seasonal term — contract 1.1 grew the demo window from 132 d (~4.5 months, 0.36
cycles, design condition 66.8) to 2 years (62 dates, 732 d, 2.00 annual cycles, condition 4.1 vs a
limit of 30), so the artefacts now DO publish a fitted `seasonal_amp_mm` and the old "do not publish"
prohibition would itself be dishonest, (c) the posting caveat for the city-scale case, and (d) a
detection bound whenever the signal is at the floor. Those sentences live in `meta.limitations` /
`meta.provenance`, which is what the app renders — so losing one silently degrades the submission's
honesty without breaking any schema check.

This module asserts the content, so a future edit to the config or the generator cannot quietly
remove a caveat.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data/processed"

# Wording is generated from config/study_cases.yaml, so each claim is matched by a small family of
# alternatives rather than one exact phrase. The numbers below are not invented here: they are the
# ones the artefacts publish, and each group is what makes the caveat audit-able rather than decorative.
WINDOW_SPAN = (
    "2-year", "2 year", "two-year", "two year", "24-month", "24 month", "732 d", "732-day",
    "732 day", "2.00 annual cycles",
)
SEASONAL_IDENTIFIED = (
    "does identify the seasonal", "identifies the seasonal", "seasonal term is identifiable",
    "is identifiable", "can be separated", "separates seasonal",
)
SEASONAL_PUBLISHED = ("fitted and published", "is published", "are published", "is reported")
HARMONIC_GATE = ("per-harmonic", "per harmonic", "per cycle", "cycles per harmonic")
SHORT_WINDOW = ("132-day", "132 day", "4.5-month", "4.5 month")
SHORT_WINDOW_DEFICIT = ("0.36", "66.8")
# Phrases that re-assert the obsolete 1.0 premise: "cannot separate seasonal from secular motion: do
# not publish seasonal_amp_mm from this stack". Note the artefacts do say the previous window "could
# not" — a past-tense statement about the short window is the honest contrast, so it is not listed.
PUBLICATION_FORBIDDEN = (
    "do not publish", "must not publish", "should not publish", "cannot publish",
    "can not publish", "not be published", "not publish seasonal", "cannot separate",
    "can not separate", "too short to publish", "withhold", "suppress the seasonal",
    "publication is forbidden", "prohibit",
)

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
    """The 2-year window identifies the seasonal term — every case must say so, with its numbers."""
    limitations = case["limitations"]
    case_id = case["id"]

    assert any(token in limitations for token in WINDOW_SPAN), (
        f"{case_id}: limitations no longer state the window span (2-year / 62 dates / 732 d / 2.00 "
        f"annual cycles), so a reader cannot judge whether the stack supports a seasonal fit at all"
    )
    assert "seasonal" in limitations, (
        f"{case_id}: the window caveat exists but does not mention the seasonal term"
    )
    assert any(token in limitations for token in SEASONAL_IDENTIFIED), (
        f"{case_id}: limitations never say the seasonal term IS identified by this window, so the "
        f"published seasonal_amp_mm looks unsupported — or invented — to a reader"
    )

    # Identifiability must be quantitative: a design condition below its gate limit, per harmonic.
    assert any(token in limitations for token in HARMONIC_GATE), (
        f"{case_id}: limitations no longer tie the window to the per-harmonic identifiability gate "
        f"(0.75 yr per harmonic), so 'the window is long enough' is an assertion, not a result"
    )
    condition = re.search(r"condition\D{0,12}(\d+(?:\.\d+)?)", limitations)
    limit = re.search(r"limit\D{0,6}(\d+(?:\.\d+)?)", limitations)
    assert condition and limit, (
        f"{case_id}: limitations give no design condition against the identifiability limit, so the "
        f"support for a published seasonal amplitude is never measured (expected e.g. "
        f"'condition 4.1 (limit 30)')"
    )
    condition_value, limit_value = float(condition.group(1)), float(limit.group(1))
    assert condition_value < limit_value, (
        f"{case_id}: limitations design at condition {condition_value:g} against limit {limit_value:g} — "
        f"a condition at or above the limit means the window does NOT identify the seasonal term, "
        f"yet seasonal_amp_mm is published from it"
    )

    assert any(token in limitations for token in SHORT_WINDOW) and any(
        token in limitations for token in SHORT_WINDOW_DEFICIT
    ), (
        f"{case_id}: limitations no longer record that the previous ~4.5-month / 132-day window "
        f"(0.36 cycles, condition 66.8) could not support the fit, so a reader may wrongly assume "
        f"the old short stack supported the seasonal amplitude too"
    )

    # The corollary: publishing the amplitude and forbidding its publication are contradictory.
    publishes_amplitude = isinstance(case["meta"]["stats"].get("median_seasonal_amp_mm"), (int, float))
    states_publication = any(token in limitations for token in SEASONAL_PUBLISHED)
    if publishes_amplitude:
        assert states_publication, (
            f"{case_id}: stats.median_seasonal_amp_mm is published but no limitation says the seasonal "
            f"amplitude is fitted and published, so the artefact and its caveat disagree"
        )
        for phrase in PUBLICATION_FORBIDDEN:
            assert phrase not in limitations, (
                f"{case_id}: limitations still say {phrase!r} while stats.median_seasonal_amp_mm is "
                f"published — that resurrects the obsolete ~4.5-month premise and tells the reader a "
                f"number they can see is not publishable"
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
