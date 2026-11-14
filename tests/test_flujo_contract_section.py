"""Guard the *other* copy of the contract: §5 of `flujo_trabajo_y_responsabilidades.md`.

`docs/data-contract-v1.md` is guarded by `tests/test_contract_docs.py`, but the project's original
operative document keeps its own §5 table — and it was left describing the rejected `1.1` schema
(`seasonal_amp_mm`, `ratio_R`, `regime` as *required*), which contradicts both the frozen contract
and every artefact in `data/processed/`. Whoever reads §5 first would code against fields that are
never published.

This module parses §5 as markdown (not substring matching) and pins it to `src/domain/contracts.py`:

* every `GRID_FIELDS` entry appears in the §5.1 table with the right required flag, and no extra
  field is presented as part of the contract;
* every `TIMESERIES_FIELDS` entry appears in §5.2, and the per-cell columns that the contract does
  *not* publish (`trend_mm_yr`, `seasonal_mm`) are not promised;
* §5.3 shows `"schema_version": "<SCHEMA_VERSION>"`;
* any contract fingerprint quoted in §5 is the current one.

Hermetic: one file read, no network, no writes.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from src.domain import contracts as C

DOC = pathlib.Path(__file__).resolve().parents[1] / "flujo_trabajo_y_responsabilidades.md"

SECTION_5_1 = "### 5.1."
SECTION_5_2 = "### 5.2."
SECTION_5_3 = "### 5.3."
SECTION_5_4 = "### 5.4."


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.exists(), f"missing {DOC}"
    return DOC.read_text(encoding="utf-8")


def _slice(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    assert start != -1, f"§5 section {start_marker!r} disappeared from {DOC.name}"
    end = text.find(end_marker, start + len(start_marker))
    assert end != -1, f"§5 section {start_marker!r} has no closing marker {end_marker!r}"
    return text[start:end]


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|---") or "|---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and cells[0].lower() in ("campo", "field"):
            continue  # header row
        rows.append(cells)
    return rows


def _field_name(cell: str) -> str:
    match = re.match(r"`([^`]+)`", cell)
    return match.group(1) if match else cell.strip()


def test_grid_table_matches_grid_fields(doc: str) -> None:
    """§5.1 must describe exactly `GRID_FIELDS`, with the same required flags."""
    rows = _table_rows(_slice(doc, SECTION_5_1, SECTION_5_2))
    assert rows, "§5.1 lost its field table"

    documented = {_field_name(cells[0]): cells for cells in rows}
    documented_names = set(documented)
    expected = {field.name: field for field in C.GRID_FIELDS}

    phantom = sorted(documented_names - set(expected))
    assert not phantom, (
        f"§5.1 documents fields that the frozen contract does not publish: {phantom}. "
        "Either the field exists in GRID_FIELDS (bump the contract, see docs/data-contract-v1.md §8) "
        "or the table is stale."
    )
    missing = sorted(set(expected) - documented_names)
    assert not missing, f"§5.1 does not document GRID_FIELDS entries: {missing}"

    wrong_flag = sorted(
        name
        for name, cells in documented.items()
        if len(cells) >= 3
        and (cells[2].lower().startswith("s") and "sí" in cells[2].lower())
        != bool(expected[name].required)
    )
    assert not wrong_flag, (
        "§5.1 disagrees with GRID_FIELDS on whether these fields are required: "
        f"{wrong_flag} (contract says "
        f"{ {name: expected[name].required for name in wrong_flag} })"
    )


def test_seasonal_fields_are_not_presented_as_required(doc: str) -> None:
    """D-28/O-05 rejected schema 1.1; §5 must not re-promise those fields as contract fields."""
    section = _slice(doc, SECTION_5_1, SECTION_5_2)
    rejected = (
        "seasonal_amp_mm",
        "ratio_R",
        "regime",
        "median_seasonal_amp_mm",
        "median_ratio_R",
        "dominant_regime",
    )
    documented = {_field_name(cells[0]) for cells in _table_rows(section)}
    promised = sorted(set(rejected) & documented)
    assert not promised, (
        f"§5.1 promises rejected schema-1.1 fields as contract columns: {promised}. "
        "`docs/decisions.md` (D-28/O-05) froze schema 1.0: over a 132-day window the annual "
        "harmonic is not identifiable, so those fields are never published."
    )


def test_timeseries_columns_match_the_contract(doc: str) -> None:
    section = _slice(doc, SECTION_5_2, SECTION_5_3)
    for field in C.TIMESERIES_FIELDS:
        assert field.name in section, f"§5.2 does not list TIMESERIES_FIELDS entry {field.name!r}"
    for unpublished in ("trend_mm_yr", "seasonal_mm"):
        assert unpublished not in section, (
            f"§5.2 still promises {unpublished!r}, which schema 1.0 does not publish; "
            "per-cell series are rebuilt from nisar_grid.parquet instead"
        )


def test_meta_example_quotes_the_frozen_schema_version(doc: str) -> None:
    section = _slice(doc, SECTION_5_3, SECTION_5_4)
    assert f'"schema_version": "{C.SCHEMA_VERSION}"' in section, (
        f"§5.3 example does not show \"schema_version\": \"{C.SCHEMA_VERSION}\""
    )
    for key in C.META_REQUIRED_KEYS:
        assert f'"{key}"' in section or f"`{key}`" in section, (
            f"§5.3 example omits required meta key {key!r}"
        )


def test_quoted_fingerprint_is_current(doc: str) -> None:
    section = _slice(doc, "## 5.", "## 6.")
    hashes = re.findall(r"\b[0-9a-f]{64}\b", section)
    for digest in hashes:
        assert digest == C.contract_fingerprint(), (
            "§5 quotes a stale contract fingerprint — the document must describe the code it ships"
        )
