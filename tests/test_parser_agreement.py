"""O-06 regression: the two granule parsers must read the same identity from the same filename.

`pipeline/ingest/02_search_download.py` (search/stack derivation) and `pipeline/process_case.py`
(the science stack-identity guard) parse NISAR GUNW names independently. They used to disagree:
the ingest parser read the *frame* as the track, and the science parser read the repeat-cycle bin
as the track, shifting both one token. A stack that was one real orbit could be refused as a "mix
of relative orbits", or two real orbits could be merged into one fabricated stack.

The authoritative grammar, measured from the 120-name ASF census in
`docs/pipeline-internals.md` §1.1, is::

    NISAR_<L>_<PR>_<GUNW>_<repeat-cycle>_<track>_<A|D>_<frame>_<secondary-cycle>_...

Token 4 is the repeat-cycle bin, 5 the track, 6 the flight direction, 7 the frame. This module
pins three things:

1. both parsers extract the same ``(track, frame, direction)`` from every census name;
2. a valid connected multi-date chain *passes* the science guard;
3. genuinely mixed geometry (a different track, or a different flight direction) is still refused.

The census ids are read from the committed availability evidence, so the test is pinned to real
ASF names rather than to a fixture that could drift from the archive.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # running pytest from another cwd
    sys.path.insert(0, str(ROOT))

from pipeline import process_case as P  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "parser_agreement_ingest", ROOT / "pipeline" / "ingest" / "02_search_download.py"
)
assert _SPEC is not None and _SPEC.loader is not None
M = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = M
_SPEC.loader.exec_module(M)

AVAILABILITY = ROOT / "data" / "processed" / "_availability"
REPORTS = sorted(AVAILABILITY.glob("*.json"))

#: The module's own code→word map, so a rename cannot silently change what agreement means.
CODE_TO_WORD = {"A": "ascending", "D": "descending"}


def census_ids() -> list[str]:
    """Every committed NISAR GUNW id, across all cases (the 120-name census)."""
    ids: list[str] = []
    for path in REPORTS:
        report = json.loads(path.read_text())
        source = (report.get("sources") or {}).get("nisar_gunw") or {}
        for record in source.get("granules") or []:
            granule_id = record.get("id")
            if isinstance(granule_id, str) and granule_id.strip():
                ids.append(granule_id)
    return ids


CENSUS = census_ids()

pytestmark = pytest.mark.skipif(
    not CENSUS,
    reason=(
        f"no committed NISAR GUNW evidence under {AVAILABILITY}; the 120-name census is the "
        "authority this regression is pinned to"
    ),
)


def _identity(parsers: tuple[object, object], granule_id: str) -> tuple[str, int | None, int | None, str]:
    """Normalise one granule to ``(source, track, frame, direction-word)``.

    The two parsers return different *shapes* on purpose (the ingest parser yields zero-padded
    strings and a one-letter code because that is what ASF's payload carries; the science parser
    yields ints and a word). Only the identity has to agree, so normalise to ints and words.
    """
    ingest, _ = parsers
    parsed = ingest.parse_granule(granule_id)
    track = int(parsed.track) if parsed.track is not None else None
    frame = int(parsed.frame) if parsed.frame is not None else None
    direction = CODE_TO_WORD.get(parsed.direction_code, parsed.direction_code)
    return ("ingest", track, frame, direction)


def test_both_parsers_agree_on_every_census_name() -> None:
    """No census name may parse to two different identities (the O-06 defect)."""
    assert len(CENSUS) >= 120, (
        f"expected the 120-name census, found {len(CENSUS)} ids under {AVAILABILITY}"
    )

    problems: list[str] = []
    for granule_id in CENSUS:
        ingest = M.parse_granule(granule_id)
        science = P.parse_granule_identity(pathlib.Path(f"{granule_id}.h5"))

        ingest_identity = (
            int(ingest.track) if ingest.track is not None else None,
            int(ingest.frame) if ingest.frame is not None else None,
            CODE_TO_WORD.get(ingest.direction_code, ingest.direction_code),
        )
        science_identity = (science.track, science.frame, science.direction)

        if ingest_identity != science_identity:
            problems.append(
                f"{granule_id}: ingest says (track, frame, direction)={ingest_identity}, "
                f"science says {science_identity}"
            )

    assert not problems, (
        "the two granule parsers read different identities from the same names (O-06):\n  "
        + "\n  ".join(problems[:10])
        + (f"\n  … and {len(problems) - 10} more" if len(problems) > 10 else "")
    )


def test_census_names_parse_to_a_track_frame_and_direction() -> None:
    """A parsed name must carry a real identity, never `None` (which the guard only warns about)."""
    unparsed = [
        granule_id
        for granule_id in CENSUS
        if M.parse_granule(granule_id).track is None
        or M.parse_granule(granule_id).frame is None
        or M.parse_granule(granule_id).direction is None
    ]
    assert not unparsed, f"{len(unparsed)} census name(s) do not parse: {unparsed[:5]}"


def test_a_valid_connected_chain_passes_the_science_guard() -> None:
    """A same-track, same-direction, one-frame chain must NOT be refused."""
    chain = [
        granule_id
        for granule_id in CENSUS
        if (lambda parsed: (parsed.track, parsed.frame, parsed.direction))(
            P.parse_granule_identity(pathlib.Path(f"{granule_id}.h5"))
        )
        == (113, 79, "descending")
    ]
    assert len(chain) >= 2, "the census should carry a multi-date chain on track 113/frame 79"

    identities = [P.parse_granule_identity(pathlib.Path(f"{item}.h5")) for item in chain]
    warnings: list[str] = []

    # Must not raise: this is exactly the chain the old parser refused as "mixed orbits".
    P._check_stack_identity(identities, warnings)

    assert not [w for w in warnings if "unparseable" in w], warnings


def test_mixed_tracks_are_still_refused_by_the_science_guard() -> None:
    """Changing the *track* token must still raise, or the guard has been weakened."""
    identities = [
        P.parse_granule_identity(
            pathlib.Path("NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH_x.h5")
        ),
        P.parse_granule_identity(
            pathlib.Path("NISAR_L2_PR_GUNW_033_114_D_079_034_4000_SH_x.h5")
        ),
    ]
    with pytest.raises(P.TrackMismatchError, match="relative orbits"):
        P._check_stack_identity(identities, [])


def test_mixed_flight_directions_are_still_refused_by_the_science_guard() -> None:
    """Changing only the *direction* token must still raise."""
    identities = [
        P.parse_granule_identity(
            pathlib.Path("NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH_x.h5")
        ),
        P.parse_granule_identity(
            pathlib.Path("NISAR_L2_PR_GUNW_033_113_A_079_034_4000_SH_x.h5")
        ),
    ]
    with pytest.raises(P.TrackMismatchError, match="flight directions"):
        P._check_stack_identity(identities, [])


def test_adjacent_frames_on_one_track_warn_but_do_not_refuse() -> None:
    """Frames are not part of the refusal rule: a chain may cross adjacent frames."""
    identities = [
        P.parse_granule_identity(
            pathlib.Path("NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH_x.h5")
        ),
        P.parse_granule_identity(
            pathlib.Path("NISAR_L2_PR_GUNW_033_113_D_080_034_4000_SH_x.h5")
        ),
    ]
    warnings: list[str] = []

    P._check_stack_identity(identities, warnings)

    assert any("frames" in warning for warning in warnings), warnings


def test_the_row_that_used_to_collide_is_not_merged_into_one_stack() -> None:
    """Two real tracks sharing a frame value must not be grouped as one acquisition geometry.

    Málaga ascending carries tracks 015 and 087 on frame 020. Under the old grammar the frame was
    read as the track, so both orbits collapsed into one "track 020" stack. The ingest parser must
    keep them apart.
    """
    payload = [
        {
            "granuleName": "NISAR_L2_PR_GUNW_032_095_D_070_033_2000_SH_x",
            "startTime": "2026-10-06T19:10:31Z",
        },
        {
            "granuleName": "NISAR_L2_PR_GUNW_032_087_A_020_033_2000_SH_x",
            "startTime": "2026-10-07T19:10:31Z",
        },
        {
            "granuleName": "NISAR_L2_PR_GUNW_033_015_A_020_034_2000_SH_x",
            "startTime": "2026-10-19T19:10:31Z",
        },
    ]

    stacks = M.derive_stacks(payload)
    grouped = sorted(
        (stack["track"], stack["frame"], stack["direction"]) for stack in stacks
    )

    assert grouped == [
        ("015", "020", "ASCENDING"),
        ("087", "020", "ASCENDING"),
        ("095", "070", "DESCENDING"),
    ], grouped
