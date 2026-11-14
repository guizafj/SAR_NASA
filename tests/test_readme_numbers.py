"""Pin the judge-facing numbers in ``README.md`` to the artefacts they describe.

``README.md`` is the file a judge opens first, and its headline numbers have gone stale four times
as the mock was rebuilt (cell counts, medians, total size, coherence ranges). Schema validation
(``src.domain.contracts``) cannot catch that: a stale table is *valid*, just wrong. So every
artefact-derived claim below is re-derived from ``data/processed/{case}/`` at run time, and the
README is parsed by structure -- heading -> table header -> row label -- never by line number.

What is guarded
---------------
1. §1 "Verify in 15 seconds": per-case ``cells`` and ``median mm/yr`` from
   ``nisar_grid.parquet`` / ``meta.json``, plus the documented date window.
2. The gate line in that same code block: the ``size / budget`` it quotes is what
   ``./scripts/python.sh pipeline/validate_artifacts.py --processed data/processed
   --strict-size-mb 15`` actually prints on its ``total size:`` line. That command is the module's
   only subprocess -- we do not recompute the size, because the README claims what the gate prints.
   The quoted case count is compared with the number of case directories.
3. Grep-checkable prose claims: any quoted ``contract_fingerprint()`` prefix, and any
   ``N suites`` / ``N test files`` count against the pytest invocation in
   ``scripts/check_phase_a.sh``.
4. The two figure captions: the shared velocity scale (``±425 mm/yr``), Málaga's detection floor
   (``±1.5 mm/yr``), and any strongest-spread / null-spread contrast (``N×``) quoted there.
5. A small inventory: every number in those guarded regions is either part of a checked claim or
   matches ``ALLOWLIST`` with a one-line reason. A new unchecked number fails instead of slipping
   through.

Tolerances (documented once, used everywhere, not tuned to be green):

  cells, case count, suite count   exact        integers, printed unrounded
  median, date window              ±0.1 mm/yr   the doc prints one decimal (±0.05 is rounding);
                                   exact for    window because ISO dates are compared verbatim
  total size / budget              ±0.0005 MB   both sides print 3 decimals of decimal MB, so half
                                                a printed unit is the display resolution -- not
                                                slack for a different dataset
  shared velocity scale            ±0.5 mm/yr   the caption prints an integer and make_figures
                                                rounds the observed max up to a multiple of 25
  detection floor                  ±0.05 mm/yr  the caption prints one decimal
  spread contrast                  ±1           the caption rounds the ratio to an integer

Deliberate non-guards (numbers that are not artefact-derived) are listed in ``ALLOWLIST`` with a
reason each. Several are outside the guarded regions today -- ``80 m`` (§13 instrument posting),
NISAR's ``12``-day revisit (§4), the ``20 s`` / ``90 s`` / ``30 s`` video script (§8), years and exit
codes -- but they are declared there anyway, so adding one to a caption cannot silently become an
unexplained number. None of them is a measurement of the committed artefacts.

Skipped with a clear reason while ``data/processed/`` has no generated cases.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROCESSED = ROOT / "data" / "processed"
CHECK_PHASE_A = ROOT / "scripts" / "check_phase_a.sh"

REQUIRED_FILES = ("nisar_grid.parquet", "timeseries.csv", "context_layers.geojson", "meta.json")

VERIFY_HEADING = "Verify in 15 seconds"
VELOCITY_FIGURE = "velocity_shared_scale"
TIMESERIES_FIGURE = "timeseries_three_cities"

MINUS_SIGNS = "\u2212\u2010\u2011\u2012\u2013\u2014\u2015\u2043\ufe63\uff0d"

TOL_VELOCITY = 0.1
TOL_SIZE_MB = 0.0005
TOL_SCALE = 0.5
TOL_DETECTION = 0.05
TOL_CONTRAST = 1.0

GATE_TIMEOUT_S = 60


# --------------------------------------------------------------------------------------------
# case artefacts
# --------------------------------------------------------------------------------------------


def _case_dirs() -> dict[str, pathlib.Path]:
    """Every case directory under data/processed (``_availability`` and dotfiles excluded)."""
    if not PROCESSED.is_dir():
        return {}
    return {
        entry.name: entry
        for entry in sorted(PROCESSED.iterdir())
        if entry.is_dir() and not entry.name.startswith(("_", "."))
    }


CASE_DIRS = _case_dirs()
CASES = sorted(CASE_DIRS)

pytestmark = pytest.mark.skipif(
    not CASES,
    reason=(
        "no case directories under data/processed/ -- generate the mock with "
        "`./scripts/python.sh pipeline/generate_mock.py` before pinning README's numbers"
    ),
)


@dataclass(eq=False)
class Case:
    """One published case, read from its artefacts."""

    case_id: str
    directory: pathlib.Path
    grid: pd.DataFrame
    meta: dict

    @property
    def cells(self) -> int:
        return len(self.grid)

    @property
    def median(self) -> float:
        return float(self.meta["stats"]["median_velocity_mm_yr"])

    @property
    def p05(self) -> float:
        return float(self.meta["stats"]["p05_velocity_mm_yr"])

    @property
    def p95(self) -> float:
        return float(self.meta["stats"]["p95_velocity_mm_yr"])

    @property
    def spread(self) -> float:
        return self.p95 - self.p05

    @property
    def dates(self) -> list[str]:
        return [str(date) for date in self.meta["dates"]]

    @property
    def max_abs_velocity(self) -> float:
        return float(self.grid["velocity_mm_yr"].abs().max())

    @property
    def detection_limit(self) -> float | None:
        value = (self.meta.get("stats") or {}).get("detection_limit_mm_yr")
        return float(value) if isinstance(value, (int, float)) else None


def _load_case(case_id: str) -> Case:
    directory = CASE_DIRS[case_id]
    missing = [name for name in REQUIRED_FILES if not (directory / name).is_file()]
    if missing:
        pytest.fail(
            f"data/processed/{case_id}/ is not a complete case: missing {', '.join(missing)}\n"
            f"  documented: README.md lists {case_id!r} as a published case\n"
            f"  measured:   only {sorted(p.name for p in directory.iterdir())} exist\n"
            f"  tolerance:  exact -- the four artefacts are the published interface\n"
            f"  the README cannot be verified against a half-generated case."
        )
    try:
        grid = pd.read_parquet(directory / "nisar_grid.parquet")
    except Exception as exc:  # pragma: no cover - corrupt artefact, reported clearly
        pytest.fail(f"data/processed/{case_id}/nisar_grid.parquet cannot be read: {exc}")
    try:
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - corrupt artefact, reported clearly
        pytest.fail(f"data/processed/{case_id}/meta.json cannot be read: {exc}")
    for key in ("stats", "dates"):
        if key not in meta:
            pytest.fail(
                f"data/processed/{case_id}/meta.json has no {key!r} block; "
                f"README's {case_id!r} row cannot be measured."
            )
    return Case(case_id=case_id, directory=directory, grid=grid, meta=meta)


@pytest.fixture(scope="module")
def measured() -> dict[str, Case]:
    return {case_id: _load_case(case_id) for case_id in CASES}


# --------------------------------------------------------------------------------------------
# README parsing (structure, not line numbers)
# --------------------------------------------------------------------------------------------


def _readme_lines() -> list[str]:
    if not README.is_file():
        pytest.fail(
            f"{README} is missing. This test exists to keep the judge-facing file honest; restore "
            f"it (or retire this guard deliberately) before publishing again."
        )
    return README.read_text(encoding="utf-8").splitlines()


def _find_heading(lines: list[str], needle: str) -> int:
    for index, line in enumerate(lines):
        if needle.lower() in line.lower():
            return index
    pytest.fail(
        f"README.md: no heading/prose line contains {needle!r}. The section this guard pins was "
        f"renamed or removed; update VERIFY_HEADING (or this guard) in the same change that moved it."
    )


def _first_fence_after(lines: list[str], heading_index: int) -> list[tuple[int, str]]:
    start = None
    for index in range(heading_index + 1, len(lines)):
        if lines[index].strip().startswith("```"):
            start = index
            break
    if start is None:
        pytest.fail(
            f"README.md:{heading_index + 1}: the {VERIFY_HEADING!r} section has no fenced code "
            f"block; the gate line this test pins is gone."
        )
    block: list[tuple[int, str]] = []
    for index in range(start + 1, len(lines)):
        if lines[index].strip().startswith("```"):
            return block
        block.append((index + 1, lines[index]))
    pytest.fail(
        f"README.md:{start + 1}: the code fence opened here never closes; refusing to guess where "
        f"the gate line ends."
    )


def _split_row(text: str) -> list[str]:
    return [cell.strip() for cell in text.strip().strip("|").split("|")]


def _clean(cell: str) -> str:
    return cell.strip().strip("`").strip("*").strip()


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells)


def _number(raw: str, *, what: str, line_no: int, text: str) -> float:
    cleaned = (
        _clean(raw)
        .replace(",", "")
        .replace("\u00a0", "")
        .replace("\u2009", "")
        .translate({ord(char): "-" for char in MINUS_SIGNS})
    )
    try:
        return float(cleaned)
    except ValueError:
        pytest.fail(
            f"README.md:{line_no}: cannot read {what} from {raw!r} in the row\n"
            f"  {text.strip()}\n"
            f"  documented: {raw!r}\n"
            f"  measured:   not a number\n"
            f"  tolerance:  n/a -- keep the judge-facing table machine-readable (e.g. 11,694, -62.1)."
        )


def _integer(raw: str, *, what: str, line_no: int, text: str) -> int:
    value = _number(raw, what=what, line_no=line_no, text=text)
    if abs(value - round(value)) > 1e-9:
        pytest.fail(
            f"README.md:{line_no}: {what} must be an integer, found {raw!r} in the row\n"
            f"  {text.strip()}\n"
            f"  documented: {raw!r}\n"
            f"  measured:   {value}\n"
            f"  tolerance:  exact -- a cell count is not rounded."
        )
    return int(round(value))


def _normalise_header(cell: str) -> str:
    return re.sub(r"\s+", " ", _clean(cell).lower()).strip()


def _column(header: list[str], name: str, *, line_no: int) -> int | None:
    normalised = [_normalise_header(cell) for cell in header]
    wanted = _normalise_header(name)
    return normalised.index(wanted) if wanted in normalised else None


@dataclass(frozen=True)
class VerifyRow:
    case: str
    cells: int
    median: float
    window: tuple[str, str] | None
    line_no: int
    text: str


def _table_after(lines: list[str], heading_index: int) -> tuple[int, list[str], list[tuple[int, str]]]:
    start = None
    for index in range(heading_index + 1, len(lines)):
        text = lines[index]
        if text.lstrip().startswith("|"):
            start = index
            break
        if text.startswith("---") or re.match(r"^#{1,4}\s", text):
            break
    if start is None:
        pytest.fail(
            f"README.md:{heading_index + 1}: the {VERIFY_HEADING!r} section has no markdown table. "
            f"The numbers cannot be pinned if the table is gone."
        )
    block: list[tuple[int, str]] = []
    index = start
    while index < len(lines) and lines[index].lstrip().startswith("|"):
        block.append((index + 1, lines[index]))
        index += 1
    if len(block) < 3 or not _is_separator(_split_row(block[1][1])):
        pytest.fail(
            f"README.md:{start + 1}: expected a header, a |---| separator and at least one data "
            f"row; found {len(block)} line(s). The table this test pins has been restructured."
        )
    header = _split_row(block[0][1])
    rows = [
        (line_no, text)
        for line_no, text in block[2:]
        if not _is_separator(_split_row(text))
    ]
    if not rows:
        pytest.fail(
            f"README.md:{start + 1}: the {VERIFY_HEADING!r} table has a header but no data rows, "
            f"so there is nothing to pin."
        )
    return block[0][0], header, rows


def _parse_window(raw: str, *, line_no: int, text: str) -> tuple[str, str]:
    match = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*(?:→|->|—|–|-)\s*(\d{4}-\d{2}-\d{2})", raw
    )
    if match is None:
        pytest.fail(
            f"README.md:{line_no}: cannot read the date window from {raw!r} in the row\n"
            f"  {text.strip()}\n"
            f"  documented: {raw!r}\n"
            f"  measured:   not a `YYYY-MM-DD → YYYY-MM-DD` window\n"
            f"  tolerance:  exact -- ISO dates are compared verbatim."
        )
    return match.group(1), match.group(2)


def parse_verify_table() -> dict[str, VerifyRow]:
    """The §1 table, keyed by row label (the case slug), columns located by header name."""
    lines = _readme_lines()
    heading_index = _find_heading(lines, VERIFY_HEADING)
    header_line, header, raw_rows = _table_after(lines, heading_index)

    case_ix = _column(header, "case", line_no=header_line)
    cells_ix = _column(header, "cells", line_no=header_line)
    median_ix = _column(header, "median mm/yr", line_no=header_line)
    window_ix = _column(header, "window", line_no=header_line)

    missing = [
        name
        for name, index in (
            ("case", case_ix),
            ("cells", cells_ix),
            ("median mm/yr", median_ix),
        )
        if index is None
    ]
    if missing:
        pytest.fail(
            f"README.md:{header_line}: the {VERIFY_HEADING!r} table header changed.\n"
            f"  documented: {' | '.join(header)}\n"
            f"  measured:   missing column(s): {', '.join(missing)}\n"
            f"  tolerance:  exact -- columns are located by name, so rename this test's lookup in "
            f"the same change that renames the header."
        )

    assert case_ix is not None and cells_ix is not None and median_ix is not None

    previous_window: tuple[str, str] | None = None
    table: dict[str, VerifyRow] = {}
    for line_no, text in raw_rows:
        cells = _split_row(text)
        if len(cells) != len(header):
            pytest.fail(
                f"README.md:{line_no}: {len(cells)} columns where {len(header)} are declared\n"
                f"  {text.strip()}\n"
                f"  documented: {len(header)} columns ({' | '.join(header)})\n"
                f"  measured:   {len(cells)} columns\n"
                f"  tolerance:  exact -- every row must carry the columns the header declares."
            )
        case = _clean(cells[case_ix])
        if not case:
            pytest.fail(f"README.md:{line_no}: the case row label is empty\n  {text.strip()}")
        if case in table:
            pytest.fail(
                f"README.md:{line_no}: duplicate row for {case!r} "
                f"(first seen on line {table[case].line_no})"
            )

        window: tuple[str, str] | None = None
        if window_ix is not None:
            raw_window = _clean(cells[window_ix])
            if raw_window.lower() in {"same window", "same"}:
                if previous_window is None:
                    pytest.fail(
                        f"README.md:{line_no}: {case!r} says {raw_window!r} but no earlier row "
                        f"declares a window to inherit."
                    )
                window = previous_window
            elif raw_window:
                window = _parse_window(raw_window, line_no=line_no, text=text)
                previous_window = window

        table[case] = VerifyRow(
            case=case,
            cells=_integer(cells[cells_ix], what="cells", line_no=line_no, text=text),
            median=_number(cells[median_ix], what="median mm/yr", line_no=line_no, text=text),
            window=window,
            line_no=line_no,
            text=text,
        )
    return table


# --------------------------------------------------------------------------------------------
# failure formatting
# --------------------------------------------------------------------------------------------


def _mismatch(
    *,
    line_no: int,
    line: str,
    claim: str,
    documented: object,
    measured_value: object,
    tolerance: str,
    hint: str = "fix the README (this guard never edits it) or regenerate the artefact it "
    "describes -- never both to make the number agree.",
) -> str:
    return (
        f"README.md:{line_no} -- {claim}\n"
        f"  documented: {documented}\n"
        f"  measured:   {measured_value}\n"
        f"  tolerance:  {tolerance}\n"
        f"  README line: {line.strip()}\n"
        f"  {hint}"
    )


# --------------------------------------------------------------------------------------------
# §1 table: cells and medians
# --------------------------------------------------------------------------------------------


def test_verify_table_lists_exactly_the_cases_on_disk() -> None:
    table = parse_verify_table()
    documented = set(table)
    present = set(CASES)
    if documented == present:
        return
    documented_lines = ", ".join(f"{case} (line {table[case].line_no})" for case in sorted(documented))
    pytest.fail(
        "README.md §1 'Verify in 15 seconds' and data/processed/ disagree about which cases exist\n"
        f"  documented: {', '.join(sorted(documented)) or '(none)'} [{documented_lines}]\n"
        f"  measured:   {', '.join(sorted(present)) or '(none)'} under data/processed/\n"
        f"  tolerance:  exact -- one row per published case, not a sample\n"
        f"  missing from README: {', '.join(sorted(present - documented)) or 'none'}\n"
        f"  missing on disk:     {', '.join(sorted(documented - present)) or 'none'}\n"
        f"  a case with no row is invisible to a judge; a row with no case is a number they cannot "
        f"reproduce. Add or retire both in the same change."
    )


@pytest.mark.parametrize("case", CASES)
def test_verify_row_cells_and_median(
    case: str, measured: dict[str, Case]
) -> None:
    table = parse_verify_table()
    row = table.get(case)
    if row is None:  # covered by the coverage test, but name the row clearly if it is first
        pytest.fail(
            f"README.md §1 has no row for {case!r}, which exists under data/processed/.\n"
            f"  documented: (no row)\n"
            f"  measured:   {measured[case].cells} cells, median {measured[case].median:.1f} mm/yr\n"
            f"  tolerance:  exact -- one row per published case"
        )

    got = measured[case]
    report: list[str] = []
    if row.cells != got.cells:
        report.append(
            _mismatch(
                line_no=row.line_no,
                line=row.text,
                claim=f"cell count for {case!r}",
                documented=f"{row.cells:,}",
                measured_value=f"{got.cells:,} (len(nisar_grid.parquet))",
                tolerance="exact -- an integer count, printed unrounded",
            )
        )
    if abs(row.median - got.median) > TOL_VELOCITY:
        report.append(
            _mismatch(
                line_no=row.line_no,
                line=row.text,
                claim=f"median velocity for {case!r}",
                documented=f"{row.median:+.1f} mm/yr",
                measured_value=f"{got.median:+.4f} mm/yr (meta.stats.median_velocity_mm_yr)",
                tolerance=f"±{TOL_VELOCITY:g} mm/yr -- the README prints one decimal, so ±0.05 is "
                f"the rounding error and 0.1 leaves one digit",
            )
        )
    if report:
        pytest.fail("\n\n".join(report))


@pytest.mark.parametrize("case", CASES)
def test_verify_row_window_matches_meta(
    case: str, measured: dict[str, Case]
) -> None:
    table = parse_verify_table()
    row = table.get(case)
    if row is None or row.window is None:
        return  # the coverage test reports a missing row; an omitted window is not a false claim
    dates = measured[case].dates
    if not dates:
        pytest.fail(
            f"data/processed/{case}/meta.json has an empty dates list; README.md:{row.line_no} "
            f"documents {row.window[0]} → {row.window[1]} and cannot be verified."
        )
    documented = row.window
    actual = (dates[0], dates[-1])
    if documented == actual:
        return
    pytest.fail(
        _mismatch(
            line_no=row.line_no,
            line=row.text,
            claim=f"date window for {case!r}",
            documented=f"{documented[0]} → {documented[1]}",
            measured_value=f"{actual[0]} → {actual[1]} ({len(dates)} dates, meta.json)",
            tolerance="exact -- ISO dates are compared verbatim",
        )
    )


# --------------------------------------------------------------------------------------------
# §1 gate line: the size is what validate_artifacts.py prints, not a second implementation
# --------------------------------------------------------------------------------------------


GATE_SELECTOR_RE = re.compile(r"\bMB\s*/\s*[0-9.]+\s*MB\b", re.IGNORECASE)
GATE_CASES_RE = re.compile(r"(\d+)\s+cases?\b", re.IGNORECASE)
GATE_PROBLEMS_RE = re.compile(r"(\d+)\s+problems?\b", re.IGNORECASE)
GATE_SIZE_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*MB\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*MB", re.IGNORECASE
)
VALIDATOR_SIZE_RE = re.compile(
    r"total size:\s*([0-9]+(?:\.[0-9]+)?)\s*MB\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*MB",
    re.IGNORECASE,
)
VALIDATOR_PROBLEMS_RE = re.compile(r"\bproblems:\s*(\d+)", re.IGNORECASE)
VALIDATOR_CASES_RE = re.compile(
    r"\bcases:\s*(\d+)\s+passed,\s*(\d+)\s+failed", re.IGNORECASE
)


@dataclass(frozen=True)
class GateLine:
    line_no: int
    text: str
    cases: int
    problems: int
    size_mb: float
    budget_mb: float


def parse_gate_line() -> GateLine:
    lines = _readme_lines()
    heading_index = _find_heading(lines, VERIFY_HEADING)
    for line_no, text in _first_fence_after(lines, heading_index):
        if not GATE_SELECTOR_RE.search(text) or not GATE_CASES_RE.search(text):
            continue
        cases = GATE_CASES_RE.search(text)
        problems = GATE_PROBLEMS_RE.search(text)
        size = GATE_SIZE_RE.search(text)
        if cases is None or problems is None or size is None:
            pytest.fail(
                f"README.md:{line_no}: the gate line no longer carries the three claims this test "
                f"pins (`N cases · N problems · SIZE MB / BUDGET MB`)\n"
                f"  documented: {text.strip()}\n"
                f"  measured:   cases={bool(cases)}, problems={bool(problems)}, size={bool(size)}\n"
                f"  tolerance:  exact -- update parse_gate_line in the same change that rewords it."
            )
        return GateLine(
            line_no=line_no,
            text=text,
            cases=int(cases.group(1)),
            problems=int(problems.group(1)),
            size_mb=float(size.group(1)),
            budget_mb=float(size.group(2)),
        )
    pytest.fail(
        f"README.md:{heading_index + 1}: could not find the gate line (`... MB / ... MB`, `N cases`) "
        f"in the {VERIFY_HEADING!r} code block. The number a judge checks first is gone."
    )


@lru_cache(maxsize=1)
def _validator_output() -> tuple[int, str]:
    """Run the documented artefact gate once; return (exit code, combined output)."""
    argv = [
        "./scripts/python.sh",
        "pipeline/validate_artifacts.py",
        "--processed",
        "data/processed",
        "--strict-size-mb",
        "15",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no network
            argv,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=GATE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(
            f"could not run the documented gate: {' '.join(argv)}\n"
            f"  error: {exc}\n"
            f"  the README's gate line cannot be verified without it."
        )
    return completed.returncode, completed.stdout or ""


def test_gate_line_matches_validate_artifacts(measured: dict[str, Case]) -> None:
    gate = parse_gate_line()
    code, output = _validator_output()
    report: list[str] = []

    if code != 0:
        report.append(
            _mismatch(
                line_no=gate.line_no,
                line=gate.text,
                claim="the gate line claims a PASS",
                documented="exit 0 (PHASE A/B: PASS)",
                measured_value=f"exit {code} from validate_artifacts.py --strict-size-mb 15",
                tolerance="exit 0 -- a fail-closed gate that fails is not a PASS",
                hint="run the same command and read its report; fix the artefact, not this test.",
            )
        )

    size_match = VALIDATOR_SIZE_RE.search(output)
    if size_match is None:
        report.append(
            _mismatch(
                line_no=gate.line_no,
                line=gate.text,
                claim="the total size quoted in the gate line",
                documented=f"{gate.size_mb:.3f} MB / {gate.budget_mb:g} MB",
                measured_value=f"no `total size:` line (exit {code})",
                tolerance=f"±{TOL_SIZE_MB:g} MB (three printed decimals)",
                hint="the validator's output shape changed or it crashed; inspect the tail below.",
            )
        )
    else:
        actual_size = float(size_match.group(1))
        actual_budget = float(size_match.group(2))
        if abs(gate.size_mb - actual_size) > TOL_SIZE_MB:
            report.append(
                _mismatch(
                    line_no=gate.line_no,
                    line=gate.text,
                    claim="the total size quoted in the gate line",
                    documented=f"{gate.size_mb:.3f} MB",
                    measured_value=f"{actual_size:.3f} MB (validator `total size:` line)",
                    tolerance=f"±{TOL_SIZE_MB:g} MB -- both sides print three decimals of decimal MB",
                )
            )
        if abs(gate.budget_mb - actual_budget) > TOL_SIZE_MB:
            report.append(
                _mismatch(
                    line_no=gate.line_no,
                    line=gate.text,
                    claim="the size budget quoted in the gate line",
                    documented=f"{gate.budget_mb:g} MB",
                    measured_value=f"{actual_budget:g} MB (validator `total size:` line)",
                    tolerance=f"±{TOL_SIZE_MB:g} MB -- the invocation uses --strict-size-mb 15",
                )
            )

    if gate.cases != len(CASES):
        report.append(
            _mismatch(
                line_no=gate.line_no,
                line=gate.text,
                claim="the case count quoted in the gate line",
                documented=f"{gate.cases} case(s)",
                measured_value=f"{len(CASES)} case directories under data/processed/ "
                f"({', '.join(CASES)})",
                tolerance="exact -- an integer count of published case directories",
            )
        )

    passed_match = VALIDATOR_CASES_RE.search(output)
    if passed_match is not None and int(passed_match.group(1)) != len(CASES):
        report.append(
            _mismatch(
                line_no=gate.line_no,
                line=gate.text,
                claim="the validator's own case count",
                documented=f"{gate.cases} case(s) in the README",
                measured_value=f"{passed_match.group(1)} passed, {passed_match.group(2)} failed "
                f"(validate_artifacts.py)",
                tolerance="exact -- every case directory must validate",
            )
        )

    problems_match = VALIDATOR_PROBLEMS_RE.search(output)
    if problems_match is None:
        report.append(
            _mismatch(
                line_no=gate.line_no,
                line=gate.text,
                claim="the problems count quoted in the gate line",
                documented=f"{gate.problems} problem(s)",
                measured_value="no `problems:` line in the validator output",
                tolerance="exact -- 0 is the only acceptable count for a PASS",
            )
        )
    elif gate.problems != int(problems_match.group(1)):
        report.append(
            _mismatch(
                line_no=gate.line_no,
                line=gate.text,
                claim="the problems count quoted in the gate line",
                documented=f"{gate.problems} problem(s)",
                measured_value=f"{problems_match.group(1)} problem(s) (validate_artifacts.py)",
                tolerance="exact -- 0 is the only acceptable count for a PASS",
            )
        )

    if report:
        tail = "\n".join(output.strip().splitlines()[-12:])
        pytest.fail("\n\n".join(report) + f"\n\nvalidator output tail:\n{tail}")


# --------------------------------------------------------------------------------------------
# grep-checkable prose claims: fingerprint prefix and gate suite count
# --------------------------------------------------------------------------------------------


def test_quoted_contract_fingerprint_prefix_matches() -> None:
    try:
        from src.domain.contracts import contract_fingerprint
    except Exception as exc:  # pragma: no cover - import failure is reported, not hidden
        pytest.fail(f"cannot import src.domain.contracts.contract_fingerprint(): {exc}")
    actual = contract_fingerprint()

    claims: list[tuple[int, str, str]] = []
    for line_no, line in enumerate(_readme_lines(), start=1):
        fingerprint_line = "fingerprint" in line.lower()
        contract_line = "contract" in line.lower()
        if not (fingerprint_line or contract_line):
            continue

        tokens: list[str] = []
        # A quoted hash prefix, e.g. `049376b0…`, whether or not the row says "fingerprint".
        for span in re.findall(r"`([^`]+)`", line):
            candidate = span.strip().rstrip("…").rstrip(".").strip()
            if re.fullmatch(r"[0-9a-fA-F]{8,64}", candidate) and re.search(r"[a-fA-F]", candidate):
                tokens.append(candidate)
        # The fingerprint row may quote the hash outside a code span; only trust it when the line
        # names the fingerprint, otherwise a commit hash or a DOI fragment would false-positive.
        if fingerprint_line:
            tokens.extend(re.findall(r"[0-9a-fA-F]{8,64}", line))
        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            claims.append((line_no, line, token))
    if not claims:
        print(
            "note: README quotes no contract fingerprint prefix; nothing to compare against "
            f"{actual[:8]}… (the fingerprint row may have been removed deliberately)."
        )
        return

    report: list[str] = []
    for line_no, line, token in claims:
        if actual.lower().startswith(token.lower()):
            continue
        report.append(
            _mismatch(
                line_no=line_no,
                line=line,
                claim="quoted contract fingerprint prefix",
                documented=token,
                measured_value=f"{actual} (contract_fingerprint())",
                tolerance="exact prefix, case-insensitive -- the README quotes the first 8 hex chars",
            )
        )
    if report:
        pytest.fail("\n\n".join(report))


def _phase_a_suites() -> list[str]:
    """Test files listed in the pytest invocation of scripts/check_phase_a.sh."""
    if not CHECK_PHASE_A.is_file():
        pytest.fail(
            f"{CHECK_PHASE_A.relative_to(ROOT)} is missing; README cites it as the 15-second gate."
        )
    lines = CHECK_PHASE_A.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"-m\s+pytest\b", line):
            continue
        found: list[str] = []
        cursor = index
        while cursor < len(lines):
            matches = re.findall(r"(tests/[\w./-]+\.py)", lines[cursor])
            if matches:
                for match in matches:
                    if match not in found:
                        found.append(match)
                if not lines[cursor].rstrip().endswith("\\"):
                    break
            elif found:
                break
            elif not lines[cursor].rstrip().endswith("\\"):
                break
            cursor += 1
        return found
    return []


def _suite_count_claims() -> Iterable[tuple[int, str, int]]:
    pattern = re.compile(
        r"(?<![\w.])(\d+)\s+(?:[A-Za-z][\w-]*\s+){0,2}"
        r"(?:suites?|test\s+(?:files|modules)|test\s+suites?)\b",
        re.IGNORECASE,
    )
    for line_no, line in enumerate(_readme_lines(), start=1):
        for match in pattern.finditer(line):
            yield line_no, line, int(match.group(1))


def test_suite_counts_match_check_phase_a() -> None:
    suites = _phase_a_suites()
    if not suites:
        pytest.fail(
            f"{CHECK_PHASE_A.relative_to(ROOT)}: found no `-m pytest` invocation listing "
            f"`tests/*.py`. The README's suite count cannot be checked against a parser that "
            f"found nothing -- refusing to pass silently."
        )

    claims = list(_suite_count_claims())
    if not claims:
        print(
            "note: README quotes no `N suites` / `N test files` count; "
            f"scripts/check_phase_a.sh currently lists {len(suites)} suites "
            f"({', '.join(suites)})."
        )
        return

    report: list[str] = []
    for line_no, line, count in claims:
        if count == len(suites):
            continue
        report.append(
            _mismatch(
                line_no=line_no,
                line=line,
                claim="documented gate suite count",
                documented=f"{count} suite(s)",
                measured_value=f"{len(suites)} suite(s) in scripts/check_phase_a.sh: "
                f"{', '.join(suites)}",
                tolerance="exact -- the script's pytest invocation is the list, not a summary",
                hint="update the README count when a suite is added to or removed from the gate.",
            )
        )
    if report:
        pytest.fail("\n\n".join(report))


# --------------------------------------------------------------------------------------------
# figure captions
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Caption:
    image: str
    line_no: int
    text: str


def _figure_captions() -> list[Caption]:
    lines = _readme_lines()
    captions: list[Caption] = []
    for index, line in enumerate(lines):
        match = re.match(r"\s*!\[[^\]]*\]\(([^)]+)\)", line)
        if match is None:
            continue
        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor >= len(lines) or not lines[cursor].lstrip().startswith("*"):
            continue
        start = cursor
        parts: list[str] = []
        while cursor < len(lines) and lines[cursor].strip():
            parts.append(lines[cursor].strip())
            cursor += 1
        captions.append(
            Caption(image=match.group(1), line_no=start + 1, text=" ".join(parts))
        )
    return captions


def _caption_for(stem: str) -> Caption:
    for caption in _figure_captions():
        if stem in caption.image:
            return caption
    pytest.fail(
        f"README.md: no figure caption follows an image whose path contains {stem!r}. The caption "
        f"this guard pins is gone or the image was renamed; update the stem in the same change."
    )


def _signed_mm_per_yr(caption: Caption) -> float:
    match = re.search(r"±\s*([0-9]+(?:\.[0-9]+)?)\s*mm\s*/\s*yr", caption.text, re.IGNORECASE)
    if match is None:
        pytest.fail(
            f"README.md:{caption.line_no}: this artefact-derived caption states no `±N mm/yr` value:\n"
            f"  {caption.text.strip()}\n"
            f"  documented: (no ±N mm/yr)\n"
            f"  measured:   cannot compare without a number\n"
            f"  tolerance:  exact -- keep the rate machine-readable so it can be pinned."
        )
    return float(match.group(1))


def _nice_symmetric_limit(observed: float) -> float:
    """Mirror of scripts/make_figures.py:nice_symmetric_limit (round up to a multiple of 25).

    The README says the figure is generated by ``scripts/make_figures.py``; the caption's integer
    scale is that function's output, not a free choice, so the guard recomputes the same rule from
    the observed max|velocity_mm_yr|.
    """
    if not math.isfinite(observed) or observed <= 0:
        return 25.0
    return float(math.ceil(observed / 25.0) * 25.0)


def test_velocity_figure_scale_matches_artefacts(measured: dict[str, Case]) -> None:
    caption = _caption_for(VELOCITY_FIGURE)
    documented = _signed_mm_per_yr(caption)
    observed_case, observed = max(
        ((case_id, measured[case_id].max_abs_velocity) for case_id in CASES),
        key=lambda item: item[1],
    )
    expected = _nice_symmetric_limit(observed)
    if abs(documented - expected) <= TOL_SCALE:
        return
    pytest.fail(
        _mismatch(
            line_no=caption.line_no,
            line=caption.text,
            claim="shared symmetric velocity scale in the figure caption",
            documented=f"±{documented:g} mm/yr",
            measured_value=f"±{expected:g} mm/yr = ceil(max|velocity_mm_yr| / 25) × 25, "
            f"with max|v| = {observed:.3f} mm/yr in {observed_case!r}",
            tolerance=f"±{TOL_SCALE:g} mm/yr -- the caption prints an integer and make_figures "
            f"rounds up to a multiple of 25",
            hint="regenerate the figure and update the caption together; a stale scale clips or "
            "over-pads the strongest signal in the demo.",
        )
    )


def test_timeseries_detection_floor_matches_meta(measured: dict[str, Case]) -> None:
    caption = _caption_for(TIMESERIES_FIGURE)
    documented = _signed_mm_per_yr(caption)
    malaga = measured.get("malaga")
    if malaga is None:
        pytest.fail(
            f"README.md:{caption.line_no} shades Málaga's detection floor, but "
            f"data/processed/malaga/ is absent, so the claim cannot be checked."
        )
    actual = malaga.detection_limit
    if actual is None:
        pytest.fail(
            f"README.md:{caption.line_no} claims a ±{documented:g} mm/yr detection floor, but "
            f"data/processed/malaga/meta.json has no stats.detection_limit_mm_yr.\n"
            f"  documented: ±{documented:g} mm/yr\n"
            f"  measured:   (no detection_limit_mm_yr)\n"
            f"  tolerance:  ±{TOL_DETECTION:g} mm/yr -- one printed decimal"
        )
    if abs(documented - actual) <= TOL_DETECTION:
        return
    pytest.fail(
        _mismatch(
            line_no=caption.line_no,
            line=caption.text,
            claim="Málaga detection floor in the figure caption",
            documented=f"±{documented:g} mm/yr",
            measured_value=f"±{actual:g} mm/yr (malaga meta.stats.detection_limit_mm_yr)",
            tolerance=f"±{TOL_DETECTION:g} mm/yr -- the caption prints one decimal",
            hint="the bound is the null result; if it moved, the caption, the figure band and the "
            "README's limitation text move together.",
        )
    )


def test_figure_caption_contrast_matches_meta(measured: dict[str, Case]) -> None:
    claims: list[tuple[int, str, float]] = []
    for line_no, text in _guarded_regions():
        lowered = text.lower()
        if "málaga" not in lowered and "malaga" not in lowered:
            continue
        for match in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*[×x]", text):
            context = text[max(0, match.start() - 40) : match.end() + 40].lower()
            if "málaga" in context or "malaga" in context:
                claims.append((line_no, text, float(match.group(1))))
    if not claims:
        print(
            "note: no `N×` Málaga contrast in the guarded regions; nothing to compare with "
            "strongest spread / null spread."
        )
        return

    malaga = measured.get("malaga")
    if malaga is None:
        pytest.fail(
            "the README quotes a Málaga spread contrast, but data/processed/malaga/ is "
            "absent, so the ratio cannot be measured."
        )
    if malaga.spread == 0:
        pytest.fail(
            "data/processed/malaga/meta.json has p95 == p05, so the spread contrast is undefined; "
            "the caption's ratio cannot be verified."
        )
    strongest_case, strongest = max(
        ((case_id, measured[case_id].spread) for case_id in CASES),
        key=lambda item: item[1],
    )
    ratio = strongest / malaga.spread

    report: list[str] = []
    for line_no, text, documented in claims:
        if abs(documented - ratio) <= TOL_CONTRAST:
            continue
        report.append(
            _mismatch(
                line_no=line_no,
                line=text,
                claim="spread contrast in the README prose/figure caption",
                documented=f"{documented:g}×",
                measured_value=f"{ratio:.2f}× = strongest spread / null spread = "
                f"{strongest:.3f} ({strongest_case!r} p95−p05) / {malaga.spread:.3f} "
                f"(malaga p95−p05)",
                tolerance=f"±{TOL_CONTRAST:g}× -- the caption rounds the ratio to an integer",
                hint="this ratio is the demo's scientific point: if it moved, the figure, the "
                "handoff page and the legend text move with it.",
            )
        )
    if report:
        pytest.fail("\n\n".join(report))


# --------------------------------------------------------------------------------------------
# inventory: no unreviewed number in the guarded regions
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Allowance:
    pattern: re.Pattern[str]
    reason: str


CHECKED_PATTERNS: tuple[Allowance, ...] = (
    Allowance(
        re.compile(r"\d{1,3}(?:,\d{3})+(?!\d)"),
        "cell count -- checked against len(nisar_grid.parquet)",
    ),
    Allowance(
        re.compile(r"(?<=\|)\s*[−-]?\d+(?:\.\d+)?\s*(?=\|)"),
        "table median -- a numeric cell checked against meta.stats.median_velocity_mm_yr",
    ),
    Allowance(
        re.compile(r"\d{4}-\d{2}-\d{2}\s*(?:→|->|—|–|-)\s*\d{4}-\d{2}-\d{2}"),
        "date window -- checked against meta.dates",
    ),
    Allowance(
        re.compile(r"\d+\s+cases?\b", re.IGNORECASE),
        "case count -- checked against the case directories and validate_artifacts.py",
    ),
    Allowance(
        re.compile(r"\d+\s+problems?\b", re.IGNORECASE),
        "problems count -- checked against validate_artifacts.py",
    ),
    Allowance(
        re.compile(r"\d+(?:\.\d+)?\s*MB\s*/\s*\d+(?:\.\d+)?\s*MB", re.IGNORECASE),
        "size / budget -- checked against validate_artifacts.py's `total size:` line",
    ),
    Allowance(
        re.compile(r"±\s*\d+(?:\.\d+)?\s*mm\s*/\s*yr", re.IGNORECASE),
        "figure rate -- checked against the artefacts (scale / detection floor)",
    ),
    Allowance(
        re.compile(r"\d+(?:\.\d+)?\s*[×x]"),
        "spread contrast -- checked against strongest spread / null spread when present",
    ),
)

#: Numbers in the guarded regions that are deliberately not artefact-derived.
#: One line of justification each; keeping them here is cheaper than a judge discovering an
#: unexplained number in the file they trust first.
ALLOWLIST: tuple[Allowance, ...] = (
    Allowance(
        re.compile(r"~?\s*15\s*(?:s|seconds?)\b", re.IGNORECASE),
        "wall-clock: the gate's observed ~15 s is hardware-dependent, not a measurement",
    ),
    Allowance(
        re.compile(r"~?\s*4\.5\s*min\b", re.IGNORECASE),
        "wall-clock: the full suite's runtime is hardware-dependent (measured 2026-11-14 at ~838 tests)",
    ),
    Allowance(
        re.compile(r"~?\s*4\.5[- ]month\b", re.IGNORECASE),
        "approximate stack span; the claim is 'too short to separate a season', not a measurement",
    ),
    Allowance(
        re.compile(r"\b0\s*mm\b", re.IGNORECASE),
        "structural: every series is referenced to its first epoch, so 0 is exact by construction",
    ),
    # Declared non-guards from the brief that are currently outside the guarded regions. They are
    # listed here so adding one to a caption cannot silently become an unexplained number.
    Allowance(
        re.compile(r"\b80\s*m\b", re.IGNORECASE),
        "instrument posting: NISAR GUNW resolution (§13), not a measurement of these artefacts",
    ),
    Allowance(
        re.compile(r"\b12[- ]day\b", re.IGNORECASE),
        "revisit interval from the contract/source documents (§4), not a measurement",
    ),
    Allowance(
        re.compile(r"\b(?:20|90|30)\s*s\b", re.IGNORECASE),
        "video-script durations (§8): production guidance, not data",
    ),
    Allowance(
        re.compile(r"\bexit(?:ed)?\s+(?:code\s+)?\d+", re.IGNORECASE),
        "process exit status, not a measurement",
    ),
    Allowance(
        re.compile(r"\b(?:19|20)\d{2}\b"),
        "calendar year (§s, frozen-at dates and citations), not a measurement",
    ),
)


def _guarded_regions() -> list[tuple[int, str]]:
    lines = _readme_lines()
    heading_index = _find_heading(lines, VERIFY_HEADING)
    regions: list[tuple[int, str]] = []
    for index in range(heading_index, len(lines)):
        text = lines[index]
        if index > heading_index and (
            text.startswith("---") or re.match(r"^#{1,4}\s", text)
        ):
            break
        regions.append((index + 1, text))
    regions.extend((caption.line_no, caption.text) for caption in _figure_captions())
    return regions


def _covering_match(
    text: str, start: int, end: int, patterns: Iterable[Allowance]
) -> tuple[str, str] | None:
    for allowance in patterns:
        for match in allowance.pattern.finditer(text):
            if match.start() <= start and match.end() >= end:
                return match.group(0), allowance.reason
    return None


def test_every_number_in_guarded_regions_is_checked_or_allowlisted() -> None:
    unclassified: list[tuple[int, str, str]] = []
    for line_no, text in _guarded_regions():
        for token in re.finditer(r"\d+(?:[.,]\d+)*", text):
            if _covering_match(text, token.start(), token.end(), CHECKED_PATTERNS) is not None:
                continue
            if _covering_match(text, token.start(), token.end(), ALLOWLIST) is not None:
                continue
            unclassified.append((line_no, text, token.group(0)))
    if not unclassified:
        return

    report: list[str] = []
    for line_no, text, token in unclassified:
        report.append(
            f"README.md:{line_no} -- unreviewed number {token!r} in a guarded region\n"
            f"  documented: {token}\n"
            f"  measured:   not derived -- no checked claim or ALLOWLIST entry covers it\n"
            f"  tolerance:  n/a -- every judge-facing number must be checked or allowlisted\n"
            f"  README line: {text.strip()[:200]}\n"
            f"  add a derivation in tests/test_readme_numbers.py, or an ALLOWLIST entry with a "
            f"one-line reason if the number is not artefact-derived (e.g. wall-clock, a year, an "
            f"exit code). Do not let a new headline number go unreviewed."
        )
    pytest.fail("\n\n".join(report))
