"""Cross-document guard for the SAR constant-geometry numbers.

The same physical constants are restated in several documents.  Prose audited by hand is not a
control, so this module parses the documents and recomputes every number from the code:

* ``pipeline/science/transforms.vertical_from_los`` -- the LOS -> vertical geometry;
* ``pipeline/process_case.DEFAULT_WAVELENGTH_M`` -- the documented L-band wavelength (0.2384 m);
* ``pipeline/science/transforms.DAYS_PER_YEAR`` -- 365.25 d;
* ``config/study_cases.yaml`` -- the mock peak rate and the declared revisit;
* ``src.domain.contracts.contract_fingerprint()`` -- the frozen contract digest.

Nothing here hardcodes a second copy of a formula.  The one value that is not exported by the
science module is the 39 degree incidence angle (it is a study-case geometry, stated in
``docs/nisar-gunw-layout.md`` §4); :data:`INCIDENCE_DEG` is checked against every document that
restates it.

The historical mistake this file exists to prevent
--------------------------------------------------
The 12-day anchor was once written as ``−420 mm/yr -> −13.8 mm -> 0.09 fringe`` while a guard
argued for ``0.12``.  Both arithmetic chains were self-consistent: 13.8 / 153.4 = 0.090 (the
*vertical* fringe), 13.8 / 119.2 = 0.116 (the *LOS* fringe).  A velocity in mm/yr and the
displacement it produces are vertical quantities, so the fraction belongs over the **vertical**
fringe (153.4 mm).  :func:`test_fringe_fractions_use_the_vertical_denominator` asserts that on
every document claim, and explicitly fails the LOS reading.

Coverage is checked only for the conflation invariant, not re-derived from artefacts: the
availability metadata carries no coverage fraction, and recomputing AOI coverage means loading
the grids and rings (already pinned by ``tests/test_demo_numbers.py``).  What matters here is that
a bbox-envelope figure is never presented as coverage of the AOI.
"""

from __future__ import annotations

import math
import pathlib
import re
import sys

import pandas as pd
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python -m pytest` puts the cwd on the path; be explicit anyway
    sys.path.insert(0, str(ROOT))

from pipeline.process_case import DEFAULT_WAVELENGTH_M  # noqa: E402
from pipeline.science import transforms as T  # noqa: E402
from src.domain import contracts as C  # noqa: E402

#: Documents allowed to restate the science constants. Missing files are skipped. The detailed
#: guides inherited these claims from CONTEXT.md, so modularising the prose must not drop the guard.
DOCUMENTS: tuple[pathlib.Path, ...] = (
    pathlib.Path("docs/nisar-gunw-layout.md"),
    pathlib.Path("README.md"),
    pathlib.Path("pipeline/README.md"),
    pathlib.Path("CONTEXT.md"),
    pathlib.Path("docs/data-contract-v1.md"),
    pathlib.Path("docs/science-guide.md"),
    pathlib.Path("docs/pipeline-internals.md"),
    # The two documents that actually carry the coverage percentages (D-20/N-09): the decision memo
    # labels the AOI and bbox denominators, and the video script quotes both cards. Without them the
    # "no document states a coverage percentage" assertion fires even though the figures are written
    # down and the guard has nothing to police.
    pathlib.Path("docs/data-source-decision.md"),
    pathlib.Path("docs/video-script.md"),
)

CONFIG_PATH = ROOT / "config" / "study_cases.yaml"
MEXICO_GRID = ROOT / "data" / "processed" / "mexico-city" / "nisar_grid.parquet"

#: Incidence angle of every worked example (docs/nisar-gunw-layout.md §4, θ = 39°).  transforms.py
#: takes it as an argument rather than exporting it, so it is stated once here and checked against
#: each document that restates it.
INCIDENCE_DEG = 39.0

#: Coverage figures from ``scripts/demo_numbers.py``.  The AOI is the threshold denominator; the
#: bbox is the AOI's envelope and is never coverage of the city (_availability carries no fraction,
#: so these two sets are the reference values, not recomputed here).
AOI_COVERAGE = {53.69, 55.41, 57.26}
BBOX_COVERAGE = {52.73, 47.61, 34.83}
COVERAGE_THRESHOLD = 50.0

#: One radian and one fringe, in millimetres, on both bases.
LOS_PER_RADIAN_MM = DEFAULT_WAVELENGTH_M / (4.0 * math.pi) * 1000.0
VERTICAL_PER_RADIAN_MM = float(T.vertical_from_los(LOS_PER_RADIAN_MM, INCIDENCE_DEG, 192.0))
LOS_PER_FRINGE_MM = DEFAULT_WAVELENGTH_M / 2.0 * 1000.0
VERTICAL_PER_FRINGE_MM = float(T.vertical_from_los(LOS_PER_FRINGE_MM, INCIDENCE_DEG, 192.0))

FRACTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:of\s+a\s+)?fringe", re.IGNORECASE)
RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm/yr", re.IGNORECASE)
VERTICAL_DISPLACEMENT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*mm\s+vertical(?:ly)?", re.IGNORECASE
)
DAYS_RE = re.compile(r"(\d+)[\-\u2011\u2013]?\s*day\s+pair", re.IGNORECASE)
DECIMAL_PERCENT_RE = re.compile(r"(\d+\.\d+)\s*%")
FULL_HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
ABBREV_HASH_RE = re.compile(r"\b([0-9a-f]{6,64})\s*(?:\u2026|\.{3})\s*([0-9a-f]{2,64})?")


# ---------------------------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------------------------


def _documents() -> dict[pathlib.Path, str]:
    """Existing documents, keyed by repo-relative path.  A missing file is simply absent."""
    found: dict[pathlib.Path, str] = {}
    for relative in DOCUMENTS:
        path = ROOT / relative
        if path.is_file():
            found[relative] = path.read_text(encoding="utf-8")
    if not found:
        pytest.skip("none of the constants documents are present in this checkout")
    return found


def _tolerance(printed: str) -> float:
    """Half a unit in the last decimal the document printed (0.005 for ``0.09``)."""
    if "." not in printed:
        return 0.5
    return 0.5 * 10.0 ** (-len(printed.split(".", 1)[1]))


def _agrees(printed: str, expected: float) -> bool:
    return abs(float(printed) - expected) <= _tolerance(printed) + 1e-12


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _line_text(text: str, line_number: int) -> str:
    lines = text.splitlines()
    return lines[line_number - 1] if 1 <= line_number <= len(lines) else ""


def _snippet(text: str, index: int, width: int = 160) -> str:
    start = max(0, index - width // 2)
    return " ".join(text[start : start + width].split())


def _claim_units(text: str):
    """Yield ``(line_number, unit_text)`` for table rows and prose sentences.

    A Markdown table row is one claim; consecutive prose lines are joined into a paragraph and
    split into sentences.  Joining prose matters because several anchors wrap the displacement and
    the fringe fraction onto two physical lines.
    """
    lines = text.splitlines()
    offsets: list[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line) + 1

    # "i.e." and "e.g." end with a period but do not end a sentence.  The layout document wraps
    # the 12-day anchor across such an abbreviation, so splitting there would separate the
    # fraction from its displacement.
    abbreviation = re.compile(r"(?:^|\s)(?:i\.e|e\.g|cf|vs|etc)\.$", re.IGNORECASE)

    def sentences(block: str, first_line: int, base: int):
        start = 0
        for match in re.finditer(r"(?<=[.!?])\s+", block):
            if abbreviation.search(block[: match.start()]):
                continue
            piece = block[start : match.start()]
            if piece.strip():
                line_number = first_line + block[:start].count("\n")
                yield line_number, piece
            start = match.end()
        piece = block[start:]
        if piece.strip():
            line_number = first_line + block[:start].count("\n")
            yield line_number, piece

    buffer: list[str] = []
    buffer_line = 1
    buffer_offset = 0

    def flush():
        nonlocal buffer
        if buffer:
            yield from sentences("\n".join(buffer), buffer_line, buffer_offset)
            buffer = []

    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith("|"):
            yield from flush()
            yield number, line
        elif line.strip():
            if not buffer:
                buffer_line = number
                buffer_offset = offsets[number - 1]
            buffer.append(line)
        else:
            yield from flush()
    yield from flush()


def _scale_rows(text: str):
    """Yield ``(line_number, kind, printed_values, line)`` for the phase -> mm scale table."""
    for number, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        label = re.sub(r"[*`]", "", cells[0]).strip().lower()
        if not re.match(r"^(?:(?:1|one)\s+)?(?:radian|fringe)\b", label):
            continue
        kind = "radian" if "radian" in label else "fringe"
        values: list[str] = []
        for cell in cells[1:]:
            values.extend(match.group(1) for match in re.finditer(r"(\d+(?:\.\d+)?)\s*mm", cell))
        yield number, kind, values, line


def _paragraphs(text: str):
    """Yield ``(paragraph_text, start_offset)`` separated by blank lines."""
    start = 0
    for match in re.finditer(r"\n\s*\n", text):
        yield text[start : match.start()], start
        start = match.end()
    yield text[start:], start


def _config_anchor() -> tuple[str, float, int]:
    """Primary case, its configured peak rate (mm/yr, unsigned) and declared revisit (days)."""
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    case = str(config["primary"])
    case_config = config["cases"][case]
    peak = abs(float(case_config["mock"]["peak_velocity_mm_yr"]))
    return case, peak, int(case_config["revisit_days"])


def _report_failures(report: list[str]) -> None:
    if report:
        pytest.fail("\n\n".join(report))


# ---------------------------------------------------------------------------------------------
# 1. Per-radian and per-fringe: 18.97 / 24.41 and 119.2 / 153.4
# ---------------------------------------------------------------------------------------------


def test_phase_scale_restatements_agree_with_transforms() -> None:
    expected = {
        "radian": (LOS_PER_RADIAN_MM, VERTICAL_PER_RADIAN_MM),
        "fringe": (LOS_PER_FRINGE_MM, VERTICAL_PER_FRINGE_MM),
    }
    report: list[str] = []
    rows_seen = 0

    for relative, text in _documents().items():
        for number, kind, values, line in _scale_rows(text):
            rows_seen += 1
            if len(values) != 2:
                report.append(
                    f"{relative}:{number}: expected LOS and vertical mm values in the "
                    f"{kind} row, found {values!r}\n  {line.strip()}"
                )
                continue
            for column, printed, truth in zip(("LOS", "vertical"), values, expected[kind]):
                if not _agrees(printed, truth):
                    report.append(
                        f"{relative}:{number}: {kind} {column} is {printed} mm, computed "
                        f"{truth:.4f} mm\n  tolerance: half a unit in the last printed digit "
                        f"({_tolerance(printed):g} mm)\n  {line.strip()}"
                    )

        # Inline restatements, e.g. "119.2 mm LOS / 153.4 mm vertical".
        for match in re.finditer(
            r"(\d+(?:\.\d+)?)\s*mm\s*LOS\s*/\s*(\d+(?:\.\d+)?)\s*mm\s+vertical",
            text,
            re.IGNORECASE,
        ):
            number = _line_number(text, match.start())
            line = _line_text(text, number)
            if re.search(r"\b1\s*radian|\bone\s+radian", line, re.IGNORECASE):
                truth = expected["radian"]
            elif "fringe" in line.lower() or "2π" in line:
                truth = expected["fringe"]
            else:
                continue
            for column, printed, value in zip(("LOS", "vertical"), match.groups(), truth):
                if not _agrees(printed, value):
                    report.append(
                        f"{relative}:{number}: inline {column} is {printed} mm, computed "
                        f"{value:.4f} mm\n  {line.strip()}"
                    )

        # "one full 2π fringe is ~119 mm of line-of-sight motion" (rounded on purpose).
        for match in re.finditer(
            r"(\d+(?:\.\d+)?)\s*mm\s+of\s+line[\s-]of[\s-]sight", text, re.IGNORECASE
        ):
            number = _line_number(text, match.start())
            line = _line_text(text, number)
            if "fringe" not in line.lower():
                continue
            printed = match.group(1)
            if not _agrees(printed, LOS_PER_FRINGE_MM):
                report.append(
                    f"{relative}:{number}: rounded fringe LOS is {printed} mm, computed "
                        f"{LOS_PER_FRINGE_MM:.2f} mm\n  {line.strip()}"
                )

        # A reproduce command prints the per-radian vertical scale after an arrow.
        for match in re.finditer(r"vertical_from_los\(", text):
            window = text[match.start() : match.start() + 500]
            arrow = re.search(r"(?:\u2192|->)\s*`?(\d+(?:\.\d+)?)`?", window)
            if arrow is None or (
                "4*math.pi" not in window and "4 * math.pi" not in window
            ):
                continue
            printed = arrow.group(1)
            if not _agrees(printed, VERTICAL_PER_RADIAN_MM):
                report.append(
                    f"{relative}:{_line_number(text, match.start())}: reproduce command prints "
                    f"{printed} mm for one radian, computed {VERTICAL_PER_RADIAN_MM:.2f} mm"
                )

        # Prose restatement of the fringe scale: "one cycle slip is 2π = 153.4 mm vertical".
        for number, unit in _claim_units(text):
            if "slip" not in unit.lower():
                continue
            for match in re.finditer(
                r"(\d+(?:\.\d+)?)\s*mm\s+(?:of\s+)?vertical", unit, re.IGNORECASE
            ):
                printed = match.group(1)
                if not _agrees(printed, VERTICAL_PER_FRINGE_MM):
                    report.append(
                        f"{relative}:{number}: a cycle slip is {printed} mm vertical, one fringe "
                        f"is {VERTICAL_PER_FRINGE_MM:.1f} mm\n  {unit.strip()}"
                    )

    assert rows_seen, "no phase -> mm scale row was found in any document"
    _report_failures(report)


def test_wavelength_and_incidence_restatements_agree_with_the_code() -> None:
    report: list[str] = []
    lambda_seen = 0
    theta_seen = 0
    for relative, text in _documents().items():
        for match in re.finditer(r"λ(?:_?L)?\s*[=≈]\s*(\d+(?:\.\d+)?)\s*m\b", text):
            lambda_seen += 1
            printed = match.group(1)
            if not _agrees(printed, DEFAULT_WAVELENGTH_M):
                report.append(
                    f"{relative}:{_line_number(text, match.start())}: wavelength is {printed} m, "
                    f"pipeline/process_case.DEFAULT_WAVELENGTH_M is {DEFAULT_WAVELENGTH_M} m"
                )
        for match in re.finditer(r"θ\s*=\s*(\d+(?:\.\d+)?)\s*°", text):
            theta_seen += 1
            printed = match.group(1)
            if not _agrees(printed, INCIDENCE_DEG):
                report.append(
                    f"{relative}:{_line_number(text, match.start())}: incidence is {printed}°, "
                    f"the worked geometry is {INCIDENCE_DEG:g}°"
                )

    assert lambda_seen, "no document restates the NISAR wavelength λ_L"
    assert theta_seen, "no document restates the incidence angle θ"
    _report_failures(report)


# ---------------------------------------------------------------------------------------------
# 2. The 12-day anchor and its fringe fraction
# ---------------------------------------------------------------------------------------------


def _anchors(text: str):
    """Yield parsed anchors: ``(line_number, rate, displacement, days, fraction, unit)``."""
    for line_number, unit in _claim_units(text):
        rate = RATE_RE.search(unit)
        displacement = VERTICAL_DISPLACEMENT_RE.search(unit)
        days = DAYS_RE.search(unit)
        fraction = FRACTION_RE.search(unit)
        if rate and displacement and days and fraction:
            yield (
                line_number,
                float(rate.group(1)),
                float(displacement.group(1)),
                int(days.group(1)),
                fraction.group(1),
                unit,
            )


def test_twelve_day_anchor_is_computed_from_config_and_days_per_year() -> None:
    case, peak, revisit = _config_anchor()
    expected_displacement = peak * revisit / T.DAYS_PER_YEAR
    assert expected_displacement == pytest.approx(13.7988, abs=1e-4)
    expected_fraction = expected_displacement / VERTICAL_PER_FRINGE_MM
    assert expected_fraction == pytest.approx(0.09, abs=0.005)
    los_fraction = expected_displacement / LOS_PER_FRINGE_MM
    assert los_fraction / expected_fraction > 1.2, "the two bases must stay distinguishable"

    report: list[str] = []
    peak_anchors = 0
    measured_rates: list[float] = []

    for relative, text in _documents().items():
        for line_number, rate, displacement, days, fraction, unit in _anchors(text):
            snippet = f"\n  {_snippet(unit, 0)!r}"
            if days != revisit:
                report.append(
                    f"{relative}:{line_number}: anchor uses a {days}-day pair, "
                    f"{case} declares revisit_days={revisit}{snippet}"
                )
            computed = rate * days / T.DAYS_PER_YEAR
            if abs(displacement - computed) > _tolerance(f"{displacement:.1f}") + 1e-9:
                report.append(
                    f"{relative}:{line_number}: {rate:g} mm/yr over {days} d is "
                    f"{computed:.2f} mm, document prints {displacement:.1f} mm{snippet}"
                )

            is_peak = abs(rate - peak) <= _tolerance(f"{peak:g}")
            if is_peak:
                peak_anchors += 1
            else:
                measured_rates.append(rate)

            vertical = displacement / VERTICAL_PER_FRINGE_MM
            los = displacement / LOS_PER_FRINGE_MM
            if not _agrees(fraction, vertical):
                report.append(
                    f"{relative}:{line_number}: {displacement:.1f} mm over the VERTICAL fringe "
                    f"({VERTICAL_PER_FRINGE_MM:.1f} mm) is {vertical:.3f}, document prints "
                    f"{fraction} fringe{snippet}"
                )
            if abs(vertical - los) > 4 * _tolerance(fraction) and abs(
                float(fraction) - los
            ) <= _tolerance(fraction):
                report.append(
                    f"{relative}:{line_number}: {fraction} fringe is the LOS-basis fraction "
                    f"({displacement:.1f}/{LOS_PER_FRINGE_MM:.1f}); use the vertical basis "
                    f"({displacement:.1f}/{VERTICAL_PER_FRINGE_MM:.1f} = {vertical:.3f}){snippet}"
                )

    assert peak_anchors, (
        f"no document states the configured peak anchor for {case} "
        f"({peak:g} mm/yr, {revisit}-day pair)"
    )

    if measured_rates:
        if MEXICO_GRID.is_file():
            strongest = abs(float(pd.read_parquet(MEXICO_GRID, columns=["velocity_mm_yr"])[
                "velocity_mm_yr"
            ].min()))
            for rate in measured_rates:
                if abs(rate - strongest) > 0.005:
                    report.append(
                        f"a measured anchor quotes {rate:.2f} mm/yr but the strongest grid cell "
                        f"in {MEXICO_GRID.relative_to(ROOT)} is {strongest:.2f} mm/yr"
                    )
        else:  # pragma: no cover - a checkout without generated artefacts
            print(
                f"note: {MEXICO_GRID.relative_to(ROOT)} absent; measured anchor rate not compared "
                "with the grid"
            )

    _report_failures(report)


def _table_displacement_before(text: str, line_number: int) -> tuple[float, int] | None:
    """Nearest ``vertical motion`` row above a fraction row in the same Markdown table."""
    lines = text.splitlines()
    for previous in range(line_number - 1, 0, -1):
        line = lines[previous - 1]
        if not line.lstrip().startswith("|"):
            if line.strip():
                break
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        label = re.sub(r"[*`]", "", cells[0]).lower()
        if "vertical motion" in label or "vertical displacement" in label:
            match = re.search(r"(\d+(?:\.\d+)?)\s*mm", cells[1])
            if match:
                return float(match.group(1)), previous
    return None


def test_fringe_fractions_use_the_vertical_denominator() -> None:
    report: list[str] = []
    claims = 0

    for relative, text in _documents().items():
        for line_number, unit in _claim_units(text):
            for fraction in FRACTION_RE.finditer(unit):
                claims += 1
                documented = fraction.group(1)
                displacement_match = VERTICAL_DISPLACEMENT_RE.search(unit)
                source_line = line_number
                if displacement_match:
                    displacement = float(displacement_match.group(1))
                else:
                    # The worked-example table prints displacement and fraction on separate rows.
                    looked_up = _table_displacement_before(text, line_number)
                    if looked_up is None:
                        # A bare fraction ("0.09 of a fringe") or a parenthetical fringe length
                        # ("0.09 of a fringe ≈ 11 mm") is not a vertical-displacement claim.
                        # When a length is printed, it must still equal one of the two bases.
                        length = re.search(r"(\d+(?:\.\d+)?)\s*mm", unit)
                        if length is not None:
                            printed_mm = float(length.group(1))
                            los_mm = float(documented) * LOS_PER_FRINGE_MM
                            vertical_mm = float(documented) * VERTICAL_PER_FRINGE_MM
                            if not (
                                _agrees(length.group(1), los_mm)
                                or _agrees(length.group(1), vertical_mm)
                            ):
                                report.append(
                                    f"{relative}:{line_number}: {documented} of a fringe is "
                                    f"{printed_mm:.1f} mm, but one fringe is "
                                    f"{LOS_PER_FRINGE_MM:.1f} mm LOS / "
                                    f"{VERTICAL_PER_FRINGE_MM:.1f} mm vertical\n"
                                    f"  {unit.strip()}"
                                )
                        continue
                    displacement, source_line = looked_up

                vertical = displacement / VERTICAL_PER_FRINGE_MM
                los = displacement / LOS_PER_FRINGE_MM
                tolerance = _tolerance(documented)
                if not _agrees(documented, vertical):
                    report.append(
                        f"{relative}:{line_number}: {documented} fringe next to "
                        f"{displacement:.1f} mm; the vertical fringe is "
                        f"{VERTICAL_PER_FRINGE_MM:.1f} mm so the fraction is {vertical:.3f} "
                        f"(displacement/153.4, not displacement/119.2 = {los:.3f})"
                        f"\n  displacement row: line {source_line}"
                    )
                if abs(vertical - los) > 4 * tolerance and abs(float(documented) - los) <= tolerance:
                    report.append(
                        f"{relative}:{line_number}: {documented} fringe is displacement over the "
                        f"LOS fringe ({displacement:.1f}/{LOS_PER_FRINGE_MM:.1f}); a vertical "
                        f"displacement belongs over the vertical fringe "
                        f"({displacement:.1f}/{VERTICAL_PER_FRINGE_MM:.1f} = {vertical:.3f})"
                    )

    assert claims, "no document states a numeric fringe fraction"
    _report_failures(report)


# ---------------------------------------------------------------------------------------------
# 3. Coverage denominators: AOI is not the bbox envelope
# ---------------------------------------------------------------------------------------------


def test_coverage_denominators_are_never_conflated() -> None:
    report: list[str] = []
    coverage_claims = 0

    for relative, text in _documents().items():
        for paragraph, start in _paragraphs(text):
            figures = [
                (match, float(match.group(1))) for match in DECIMAL_PERCENT_RE.finditer(paragraph)
            ]
            for match, figure in figures:
                line_number = _line_number(text, start + match.start())
                line = _line_text(text, line_number)
                paragraph_has_bbox = bool(re.search(r"\bbbox\b|envelope", paragraph, re.I))
                paragraph_has_coverage = bool(
                    re.search(r"\bAOI\b|\bcoverage\b|footprint", paragraph, re.I)
                )
                line_has_bbox = bool(re.search(r"\bbbox\b|envelope", line, re.I))
                line_has_aoi = bool(re.search(r"\bAOI\b", line, re.I))

                if figure in BBOX_COVERAGE:
                    coverage_claims += 1
                    if not line_has_bbox:
                        report.append(
                            f"{relative}:{line_number}: bbox-envelope coverage {figure:.2f} % "
                            f"is not labelled 'bbox'/'envelope' on its line; it must never read "
                            f"as coverage of the AOI\n  {line.strip()}"
                        )
                elif figure in AOI_COVERAGE:
                    coverage_claims += 1
                    if line_has_bbox and not line_has_aoi:
                        report.append(
                            f"{relative}:{line_number}: AOI coverage {figure:.2f} % sits on a "
                            f"line labelled bbox/envelope\n  {line.strip()}"
                        )
                    if paragraph_has_bbox and not re.search(
                        r"\bAOI\b|\bcoverage\b|footprint", paragraph, re.I
                    ):
                        report.append(
                            f"{relative}:{line_number}: AOI coverage {figure:.2f} % appears "
                            "beside a bbox claim with no AOI denominator named"
                        )
                elif figure != COVERAGE_THRESHOLD and paragraph_has_coverage:
                    report.append(
                        f"{relative}:{line_number}: {figure:.2f} % is quoted in a coverage "
                        f"paragraph but is neither an AOI figure {sorted(AOI_COVERAGE)} nor a "
                        f"bbox figure {sorted(BBOX_COVERAGE)}\n  {line.strip()}"
                    )

    assert coverage_claims, "no document states a coverage percentage"
    _report_failures(report)


# ---------------------------------------------------------------------------------------------
# 4. Contract fingerprint
# ---------------------------------------------------------------------------------------------


def test_contract_fingerprint_matches_the_contracts_module() -> None:
    fingerprint = C.contract_fingerprint()
    assert (
        fingerprint == "049376b00f9f23906b1ccaa2b9555699e14a8d3bec9fcb6ba0fc0d336678ce1e"
    ), "the frozen contract digest moved; every document must be updated in the same change"

    report: list[str] = []
    full_seen = 0
    for relative, text in _documents().items():
        for match in FULL_HASH_RE.finditer(text):
            full_seen += 1
            if match.group(0) != fingerprint:
                report.append(
                    f"{relative}:{_line_number(text, match.start())}: fingerprint "
                    f"{match.group(0)} != contract_fingerprint() {fingerprint}"
                )
        for match in ABBREV_HASH_RE.finditer(text):
            prefix, suffix = match.group(1), match.group(2)
            if not fingerprint.startswith(prefix) or (suffix and not fingerprint.endswith(suffix)):
                report.append(
                    f"{relative}:{_line_number(text, match.start())}: abbreviated fingerprint "
                    f"{match.group(0)} does not agree with {fingerprint}"
                )

    assert full_seen, "no document states the full contract fingerprint"
    _report_failures(report)
