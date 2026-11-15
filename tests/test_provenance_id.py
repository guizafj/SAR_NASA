"""The provenance id must change when the acquisition window changes.

Why this file exists
--------------------
`pipeline/mock/artifacts.py` builds every mock id through
`contracts.provenance_id(case_id, product, PROCESSING_DATE, digest)`, and `PROCESSING_DATE` is the
fixed constant ``"2026-11-14"`` (a mock has no retrieval event, so the date may not be the wall
clock). The digest was ``sha256(case_id | product | seed)`` — **the window was not in it**, and the
window is the one thing that actually changed between contract 1.0 and contract 1.1 (12 dates at
the old short stack, 62 dates on the two-year window). Measured on the committed tree, the same
three ids were carried by both datasets:

    jakarta+mock+20261114+57d20da8      12 dates (2026-07-02 .. 2026-11-11)  ==  62 dates (2024-11-10 .. 2026-11-12)
    malaga+mock+20261114+bf460c19       12 dates                             ==  62 dates
    mexico-city+mock+20261114+cc912b7a  12 dates                             ==  62 dates

An id that cannot distinguish two datasets makes "rerun and diff, keyed by id" unachievable: the
diff is exactly the thing the id is supposed to expose. The fix puts the window into the digested
material, at the producer, without touching the `contracts.provenance_id` signature:

    window = sha256(",".join(dates))          # date strings, in the order given
    digest = sha256(f"{case_id}|{product}|{seed}|{window}")

The dates are hashed **in the order the stack carries them** (``CaseModel.dates``: acquisition
order, first to last). They are *not* sorted here — the epochs are the stack's, so their order is
meaningful and a re-ordered stack is allowed to digest differently. Two runs of one stack still
agree byte-for-byte, which is what makes the committed artefacts reproducible.

What is pinned here
-------------------
* (a) the exact regression: same case + product + seed, a 12-date window and a 62-date window must
  yield **different** ids — asserted on the two real windows the repo has shipped, for all three
  cases;
* (b) determinism: the same inputs yield the same id (a repeat call, a tuple, a generator), so the
  guard cannot be satisfied by making the id random;
* (c) shape: the id is still ``case+product+YYYYMMDD+hex`` with an 8-character lowercase hex tail
  (`C.PROVENANCE_ID_RE`), so an id remains greppable and traceable by eye;
* the exact digested string is pinned by recomputing it here, independently of the producer;
* the committed `data/processed/*/meta.json` carry the window-derived id, and no longer the
  pre-fix one (skipped when the tree holds no cases, so a bare checkout still collects cleanly).

Non-vacuity
-----------
`_old_digest` / `_old_provenance_id` below are the **verbatim** pre-fix implementation (the
established idiom in `tests/test_contract_meta_types.py`: keep the old code, assert its verdict, so
the regression is *demonstrated* and not asserted from memory). They reproduce the three measured
colliding ids from the window alone — the old function does not even take a date list — which is
what makes ``test_window_changes_the_id`` fail if anyone reverts the digest to
``case + product + seed``.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

from pipeline.mock import artifacts as A
from src.domain import contracts as C

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data/processed"

PRODUCT = "mock"
#: The generator's shipped default seed (`pipeline/generate_mock.py: DEFAULT_SEED`), also published
#: in every `meta.provenance.mock_seed`. Restated here so the guard does not import the CLI module.
SEED = 7
CASES = ("jakarta", "malaga", "mexico-city")

#: The contract-1.0 stack: 12 acquisitions, 12-day cadence, Jul–Nov 2026 (read from
#: `git show 4a02603^:data/processed/<case>/meta.json`).
WINDOW_1_0 = [
    "2026-07-02",
    "2026-07-14",
    "2026-07-26",
    "2026-08-07",
    "2026-08-19",
    "2026-08-31",
    "2026-09-12",
    "2026-09-24",
    "2026-10-06",
    "2026-10-18",
    "2026-10-30",
    "2026-11-11",
]

#: The contract-1.1 stack: 62 acquisitions, a two-year window (the shipped `meta.json` dates).
WINDOW_1_1 = [
    "2024-11-10", "2024-11-22", "2024-12-04", "2024-12-16", "2024-12-28",
    "2025-01-09", "2025-01-21", "2025-02-02", "2025-02-14", "2025-02-26",
    "2025-03-10", "2025-03-22", "2025-04-03", "2025-04-15", "2025-04-27",
    "2025-05-09", "2025-05-21", "2025-06-02", "2025-06-14", "2025-06-26",
    "2025-07-08", "2025-07-20", "2025-08-01", "2025-08-13", "2025-08-25",
    "2025-09-06", "2025-09-18", "2025-09-30", "2025-10-12", "2025-10-24",
    "2025-11-05", "2025-11-17", "2025-11-29", "2025-12-11", "2025-12-23",
    "2026-01-04", "2026-01-16", "2026-01-28", "2026-02-09", "2026-02-21",
    "2026-03-05", "2026-03-17", "2026-03-29", "2026-04-10", "2026-04-22",
    "2026-05-04", "2026-05-16", "2026-05-28", "2026-06-09", "2026-06-21",
    "2026-07-03", "2026-07-15", "2026-07-27", "2026-08-08", "2026-08-20",
    "2026-09-01", "2026-09-13", "2026-09-25", "2026-10-07", "2026-10-19",
    "2026-10-31", "2026-11-12",
]

#: The ids contract 1.0 shipped — and that contract 1.1 still shipped before this fix, because the
#: window never entered the digest. Recording them is what makes the collision a fact, not a story.
PRE_FIX_IDS = {
    "jakarta": "jakarta+mock+20261114+57d20da8",
    "malaga": "malaga+mock+20261114+bf460c19",
    "mexico-city": "mexico-city+mock+20261114+cc912b7a",
}

HEX8_RE = re.compile(r"^[0-9a-f]{8}$")


# ---------------------------------------------------------------------------------------------
# The pre-fix implementation, verbatim, so the guard proves itself non-vacuous
# ---------------------------------------------------------------------------------------------


def _old_digest(case_id: str, seed: int) -> str:
    """Verbatim digest from `pipeline/mock/artifacts.py` before the fix: case + product + seed."""
    return hashlib.sha256(f"{case_id}|{PRODUCT}|{int(seed)}".encode()).hexdigest()


def _old_provenance_id(case_id: str, seed: int) -> str:
    """Pre-fix id: no parameter can carry the window, so no window can change the id."""
    return C.provenance_id(case_id, PRODUCT, A.PROCESSING_DATE, _old_digest(case_id, seed))


def _id(case_id: str, dates, seed: int = SEED) -> str:
    """The producer's id for one stack, through the public seam `generate_mock` uses."""
    return A.provenance_id(case_id, seed, dates)


def _shipped_meta(case_id: str) -> dict:
    path = PROCESSED / case_id / "meta.json"
    if not path.is_file():
        pytest.skip(f"no committed artefacts: {path} is absent")
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------------------------
# (a) The regression: two windows must not share an id
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
def test_window_changes_the_id(case_id: str) -> None:
    """Same case, product and seed; a 12-date window and a 62-date window → different ids.

    This is the measured defect: both windows used to produce `PRE_FIX_IDS[case_id]`.
    """
    short = _id(case_id, WINDOW_1_0)
    long = _id(case_id, WINDOW_1_1)

    assert short != long, (
        f"{case_id}: the 12-date and the 62-date stack share the id {short!r} — the acquisition "
        "window is not part of the provenance digest, so 'rerun and diff by id' cannot work"
    )
    # The whole id differs, and it differs in the digest tail (case/product/date are shared on
    # purpose: those describe the case, not the dataset).
    assert short.split("+")[:3] == long.split("+")[:3] == [case_id, PRODUCT, "20261114"]
    assert short.split("+")[-1] != long.split("+")[-1]


@pytest.mark.parametrize("case_id", CASES)
def test_a_shifted_window_of_equal_length_changes_the_id(case_id: str) -> None:
    """A different window of the *same size* also changes the id — the epochs are digested."""
    shifted = WINDOW_1_0[1:] + ["2026-11-23"]  # 12 dates again, one cadence step later
    assert len(shifted) == len(WINDOW_1_0)
    assert _id(case_id, shifted) != _id(case_id, WINDOW_1_0)


@pytest.mark.parametrize("case_id", CASES)
def test_order_is_hashed_as_given(case_id: str) -> None:
    """The dates are digested in the order given (acquisition order), not sorted.

    Nothing is sorted in the producer: the epochs are the stack's, so a re-ordered stack is a
    different dataset and may digest differently. This test pins that choice, so a later
    "helpful" `sorted()` is a deliberate change and not a silent one.
    """
    assert _id(case_id, list(reversed(WINDOW_1_1))) != _id(case_id, WINDOW_1_1)


# ---------------------------------------------------------------------------------------------
# (b) Determinism: same inputs, same id
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
def test_same_window_same_id(case_id: str) -> None:
    """Determinism, not randomness: repeats and container types do not move the id."""
    first = _id(case_id, WINDOW_1_1)
    assert _id(case_id, WINDOW_1_1) == first
    assert _id(case_id, list(WINDOW_1_1)) == first  # same list content
    assert _id(case_id, tuple(WINDOW_1_1)) == first  # a tuple is a window too
    assert _id(case_id, (date for date in WINDOW_1_1)) == first  # so is any iterable


def test_seed_still_changes_the_id() -> None:
    """The seed must keep moving the id: adding the window must not have replaced it."""
    assert _id("jakarta", WINDOW_1_1, seed=7) != _id("jakarta", WINDOW_1_1, seed=8)


def test_case_still_changes_the_id() -> None:
    assert _id("jakarta", WINDOW_1_1) != _id("malaga", WINDOW_1_1)


# ---------------------------------------------------------------------------------------------
# (c) Shape: still case+product+date+8 hex characters
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
@pytest.mark.parametrize("window", (WINDOW_1_0, WINDOW_1_1), ids=("12-dates", "62-dates"))
def test_id_shape(case_id: str, window: list[str]) -> None:
    value = _id(case_id, window)

    assert C.PROVENANCE_ID_RE.match(value), f"{value!r} does not match {C.PROVENANCE_ID_RE.pattern}"
    case, product, day, tail = value.split("+")
    assert (case, product, day) == (case_id, PRODUCT, "20261114")
    assert len(tail) == 8 and HEX8_RE.match(tail), f"digest tail {tail!r} is not 8 lowercase hex"


def test_digest_input_string_is_pinned() -> None:
    """The exact digested material, recomputed here without calling the producer.

    ``sha256(f"{case_id}|{product}|{seed}|sha256(",".join(dates))")``
    """
    case_id, seed = "mexico-city", 7
    window = hashlib.sha256(",".join(WINDOW_1_1).encode()).hexdigest()
    expected = hashlib.sha256(f"{case_id}|{PRODUCT}|{seed}|{window}".encode()).hexdigest()

    assert A.provenance_digest(case_id, seed, WINDOW_1_1) == expected
    assert _id(case_id, WINDOW_1_1) == C.provenance_id(case_id, PRODUCT, "2026-11-14", expected)


# ---------------------------------------------------------------------------------------------
# The committed artefacts must carry the window-derived id
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
def test_shipped_artefacts_carry_the_window_derived_id(case_id: str) -> None:
    """`data/processed/*/meta.json` must agree with the producer for its own dates and seed."""
    meta = _shipped_meta(case_id)
    provenance = meta["provenance"]
    dates, seed = meta["dates"], int(provenance["mock_seed"])

    assert provenance["provenance_id"] == _id(case_id, dates, seed), (
        f"{case_id}: meta.json carries {provenance['provenance_id']!r}, but the {len(dates)}-date "
        "window digests to a different id — the committed artefacts were not regenerated"
    )
    assert provenance["provenance_id"] == _id(case_id, WINDOW_1_1, seed)
    assert provenance["provenance_id"] != PRE_FIX_IDS[case_id], (
        f"{case_id}: still the pre-fix id {PRE_FIX_IDS[case_id]!r}: the window is not digested"
    )
    # `provenance.product_id` embeds the same digest tail, so it must have moved with the id.
    tail = provenance["provenance_id"].split("+")[-1]
    assert provenance["product_id"] == f"{PRODUCT}:{case_id}:{provenance['processing_date']}:{tail}"


# ---------------------------------------------------------------------------------------------
# Non-vacuity: the old digest collides on exactly these two windows
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
def test_guard_is_not_vacuous(case_id: str) -> None:
    """The pre-fix digest reproduces the collision, so `test_window_changes_the_id` can fail.

    If the guard were vacuous — e.g. `_id` had been given a random or a date-independent salt —
    the old implementation would *also* distinguish the windows, and the regression test above
    would prove nothing. Here the old implementation yields the measured 1.0 id for **both**
    windows (it cannot even see a date), while the shipped implementation must yield two.
    """
    # The old function has no date parameter at all, so 12 dates and 62 dates are one call —
    # that single call is the measured 1.0 id, i.e. the collision is reproduced, not remembered.
    assert _old_provenance_id(case_id, SEED) == PRE_FIX_IDS[case_id], (
        "the recorded pre-fix id no longer matches the pre-fix digest, so the non-vacuity "
        "argument is stale"
    )

    # The verdicts differ, which is the whole point: old = one id for both windows, new = two.
    new_short, new_long = _id(case_id, WINDOW_1_0), _id(case_id, WINDOW_1_1)
    assert len({_old_provenance_id(case_id, SEED), new_short, new_long}) == 3, (
        "the new ids must be distinct from each other and from the pre-fix id"
    )
