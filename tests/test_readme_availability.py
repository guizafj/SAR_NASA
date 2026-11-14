"""The judge-facing availability table must match the evidence it cites.

README §5 publishes granule counts, date ranges and the "OPERA DISP-S1: 0" statement. Those numbers
come from `data/processed/_availability/*.json`, which is refreshed by a network call — so the table
can silently drift from the evidence while still looking authoritative. A judge reads that table to
decide whether the team checked its data sources; a stale number there is a credibility failure, not
a typo.

Note the parsing constraint: §5 contains **two** tables. "OPERA DISP-S1" and "EGMS" appear in the
source/URL table as well, which has three columns. Everything here is scoped to the availability
table — the one whose row mentions both "NISAR GUNW" and "granules".
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/processed/_availability"

#: README column header (lower-cased) → case slug. Parsed from the header, never assumed.
CASE_COLUMNS = {
    "mexico city": "mexico-city",
    "jakarta": "jakarta",
    "málaga": "malaga",
    "malaga": "malaga",
}

#: The four source rows the availability table must carry, by their first cell.
SOURCE_ROWS = {
    "nisar": r"NISAR GUNW",
    "opera": r"OPERA DISP-S1",
    "s1": r"Sentinel-1 SLC",
    "egms": r"EGMS",
}


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _cells(line: str) -> list[str]:
    return [cell.strip().strip("*").strip() for cell in line.strip().strip("|").split("|")]


def _availability_rows(text: str) -> list[list[str]]:
    """Rows of the availability table only, ignoring the source/URL table above it."""
    lines = text.splitlines()
    anchor = None
    for index, line in enumerate(lines):
        if line.strip().startswith("|") and "NISAR GUNW" in line and "granules" in line:
            anchor = index
            break
    if anchor is None:
        return []

    start = anchor
    while start > 0 and lines[start - 1].strip().startswith("|"):
        start -= 1
    end = anchor
    while end + 1 < len(lines) and lines[end + 1].strip().startswith("|"):
        end += 1
    return [_cells(line) for line in lines[start : end + 1] if not re.match(r"^\|[\s:-]+\|", line.strip())]


def _row(text: str, pattern: str) -> list[str] | None:
    for cells in _availability_rows(text):
        if cells and re.search(pattern, cells[0]):
            return cells
    return None


def _case_order(rows: list[list[str]]) -> list[str]:
    """Map column index → case slug using the table header."""
    for cells in rows:
        if cells and cells[0].lower().startswith("source"):
            return [CASE_COLUMNS[h.lower()] for h in cells[1:] if h.lower() in CASE_COLUMNS]
    return []


def _evidence(case: str) -> dict:
    path = EVIDENCE / f"{case}.json"
    if not path.exists():
        pytest.skip(f"no evidence for {case} — run 02_search_download.py search")
    return json.loads(path.read_text())["sources"]


def test_availability_table_exists_with_the_three_case_columns() -> None:
    rows = _availability_rows(_readme())
    assert rows, "README §5 has no availability table (the row mentioning NISAR GUNW and granules)"
    order = _case_order(rows)
    assert order == ["mexico-city", "jakarta", "malaga"], (
        f"parsed case columns as {order}; the test maps columns by header, so update CASE_COLUMNS "
        f"if the headers were renamed"
    )


def test_granule_counts_and_date_ranges_match_the_evidence() -> None:
    text = _readme()
    rows = _availability_rows(text)
    order = _case_order(rows)
    row = _row(text, SOURCE_ROWS["nisar"])
    assert row is not None, "the availability table lost its NISAR GUNW row"
    for slug, cell in zip(order, row[1:]):
        nisar = _evidence(slug)["nisar_gunw"]
        assert f"{nisar['n_granules']} granules" in cell, (
            f"README says {cell!r} for {slug} but the evidence says {nisar['n_granules']} granules"
        )
        if nisar.get("date_range"):
            low, high = (stamp[:10] for stamp in nisar["date_range"])
            assert f"{low} → {high}" in cell, (
                f"README window for {slug} is stale: evidence is {low} → {high}"
            )


def test_opera_disp_s1_is_still_absent_everywhere() -> None:
    """The README's boldest sourcing claim. If OPERA ever appears, the contract's product enum changes."""
    text = _readme()
    order = _case_order(_availability_rows(text)) or ["mexico-city", "jakarta", "malaga"]
    row = _row(text, SOURCE_ROWS["opera"])
    assert row is not None, "the availability table lost its OPERA DISP-S1 row"
    for slug, cell in zip(order, row[1:]):
        opera = _evidence(slug)["opera_disp_s1"]
        if opera["n_granules"] == 0:
            assert cell == "0", f"README claims {cell!r} for {slug} but the evidence says 0"
        else:
            pytest.fail(
                f"OPERA DISP-S1 now has {opera['n_granules']} granules for {slug}: README, "
                f"docs/data-source-decision.md and the contract's product enum all say it is absent"
            )


def test_every_source_row_has_a_cell_per_case() -> None:
    """A malformed row (missing column) would make the checks above silently skip a case."""
    text = _readme()
    for name, pattern in SOURCE_ROWS.items():
        row = _row(text, pattern)
        assert row is not None, f"the availability table lost its {name} row ({pattern})"
        assert len(row) == 4, f"the {name} row has {len(row)} cells, expected 4 (label + 3 cases)"
