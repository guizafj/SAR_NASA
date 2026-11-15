"""Guard: the tab-4 physics card is a *pair* — our document plus their parser.

Why this module exists
----------------------
`src/ui/educational.py::render_physics_tab` builds two blocks out of `load_documented_limits()`:

* ``science_summary = limits.get("science_section_1")`` guarding an expander that carries the §1
  paragraph **and** the hardcoded regime-index legend (``R = |velocity| / seasonal amplitude``);
* ``case_roles = limits.get("case_roles")``, whose per-case ``role`` / ``expected_regime`` /
  ``published_rate_mm_yr`` feed the "Declared role: … · Expected regime: … · Literature reference
  rate: …" line of the Case card.

Both are produced by parsers that read **our** files: ``_extract_science_section_1`` does
``text.find("## 1. The physics in one paragraph")`` in ``docs/science-guide.md`` and returns
``None`` when that heading text is not found; ``_extract_case_roles`` reads three keys per case from
``config/study_cases.yaml`` and returns whatever the loader's silent defaults produce when a key is
absent (``role`` -> ``""``, ``expected_regime`` -> ``"unknown"``, ``published_rate_mm_yr`` ->
``0.0``). Neither failure raises: the science card is behind ``if science_summary:``, so a reworded
heading **deletes** the panel and its legend, and a dropped ``role:`` renders a Case card that
looks complete and says less. No test in either branch fails, because each branch only ever sees
one half of the pair: the documents are ours (``docs/**``, ``config/**`` — Rol 2/3), the parsers are
Rol 1's, and the pair first exists *after* the merge. This module is the missing test of the pair.

What is pinned (and what is deliberately *not*)
----------------------------------------------
The documents are read as documents — ``yaml.safe_load`` of ``config/study_cases.yaml`` and
``read_text`` of ``docs/science-guide.md`` — and compared against the **real** extractors, imported
and called, never re-implemented: the guard must exercise the code path the card uses, or it only
proves that a second copy of the parser agrees with itself. Every string this file asserts on was
read out of the committed documents (the §1 line each phrase comes from is named at the constant),
not invented here.

One finding is recorded rather than "fixed": ``expected_regime`` is *not* the grid's ``regime``
enum. The contract constrains ``nisar_grid.regime`` (``Field("regime", enum=REGIMES)``); a case's
``expected_regime`` is a forecast about a site, ``loaders.load_study_cases_config`` defaults it to
``"unknown"`` — itself outside ``REGIMES`` — and ``config/study_cases.yaml`` declares Jakarta
``mixed`` ("coastal, mixed regime": 6 inelastic + 1 transitional cell, per ``tests/conftest.py``).
So the guard requires a value that is either contract vocabulary or a word the *same case's* role
prose declares, and refuses any other free token. That keeps the merge-relevant risk (a silent
blank or an invented word) loud without demanding a document edit this branch does not own.

Skip behaviour
--------------
Before the merge ``src/data/ui_bridge.py`` has neither extractor (our UI is the merge base), so the
module skips per test with a reason naming both functions — collected, never erroring. The moment
the extractors exist, a bad *value* fails; only a *missing function* skips.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pytest from the repo root, like the card's own imports
    sys.path.insert(0, str(ROOT))

try:  # the bridge itself is Rol 1's; a checkout without it has no card parser at all
    import src.data.ui_bridge as UI_BRIDGE  # noqa: E402
except ImportError as exc:  # pragma: no cover - only a tree that does not ship the adapter
    pytest.skip(
        f"src/data/ui_bridge.py cannot be imported ({exc}), so this checkout has no card parser to "
        "pin; this module starts checking as soon as the adapter that feeds "
        "src/ui/educational.py::render_physics_tab lands",
        allow_module_level=True,
    )

from src.domain import contracts as C  # noqa: E402

SCIENCE_PATH = ROOT / "docs" / "science-guide.md"
STUDY_CASES_PATH = ROOT / "config" / "study_cases.yaml"

SCIENCE_EXTRACTOR = "_extract_science_section_1"
ROLES_EXTRACTOR = "_extract_case_roles"

_extract_science_section_1: Any = getattr(UI_BRIDGE, SCIENCE_EXTRACTOR, None)
_extract_case_roles: Any = getattr(UI_BRIDGE, ROLES_EXTRACTOR, None)
MISSING = tuple(
    name
    for name, value in (
        (SCIENCE_EXTRACTOR, _extract_science_section_1),
        (ROLES_EXTRACTOR, _extract_case_roles),
    )
    if not callable(value)
)

pytestmark = pytest.mark.skipif(
    bool(MISSING),
    reason=(
        f"src/data/ui_bridge.py in this checkout defines no {' and no '.join(MISSING)}: the tab-4 "
        "card (src/ui/educational.py) is still the merge base's, so there is no document/parser "
        "pair to pin yet — this module starts checking as soon as the adapter lands"
    ),
)

#: The heading `_extract_science_section_1` searches for verbatim (`text.find(marker)`). The card's
#: §1 paragraph is the ONLY place the regime-index legend is reachable from.
MARKER = "## 1. The physics in one paragraph"

#: The card's other stop tokens, in the extractor's own order: the figure, a rule, the next heading.
STOP_TOKENS = ("\n![", "\n---", "\n## ")

#: Distinctive phrases of §1, quoted from `docs/science-guide.md` (line numbers as committed).
#: The extractor joins the paragraph's stripped lines with single spaces, so a phrase that wraps is
#: still present; each entry is the phrase plus the §1 line it was copied from.
PINNED_PHRASES: tuple[tuple[str, str], ...] = (
    # l.16 — what the observation records
    ("A radar satellite sends a pulse, receives the echo, and records **phase**", "§1 l.16"),
    # l.17-18 — what the observation measures
    (
        "the difference is proportional to how far the ground moved along the line of sight",
        "§1 l.17-18",
    ),
    # l.24-25 — what the observation does NOT measure (the honesty clause)
    ("a single pass cannot separate vertical from horizontal motion", "§1 l.24-25"),
    # l.25-26 — and that the assumption is disclosed, not silent
    (
        "the pipeline solves for vertical while assuming the horizontal is zero, and says so in "
        "`provenance`",
        "§1 l.25-26",
    ),
)

#: Text of the figure that follows §1 (l.30-35) — the `![` alt text and its caption. The extractor
#: truncates at `\n![`, so this must never reach the card: it is a second, longer finding, not the
#: paragraph the card claims to quote.
CAPTION_PHRASES: tuple[tuple[str, str], ...] = (
    ("Velocity on one shared scale", "§1 l.30 ![ alt text"),
    ("one symmetric ±425 mm/yr scale", "§1 l.32 caption"),
    ("That blankness **is** the finding", "§1 l.34 caption"),
    ("no per-city auto-stretch", "§1 l.35 caption"),
)

#: Keys the card reads out of `case_roles[case_id]`, and what `loaders.load_study_cases_config`
#: substitutes when the document omits them — silently, with no error and no blank marker.
CASE_KEYS = ("role", "expected_regime", "published_rate_mm_yr")
SILENT_DEFAULTS: dict[str, Any] = {
    "role": "",
    "expected_regime": "unknown",
    "published_rate_mm_yr": 0.0,
}

CARD_FAILURE_HINT = (
    "src/ui/educational.py guards the 'How this measurement works (SAR physics)' expander with "
    "`if science_summary:`, so this is not a degraded card — the paragraph AND the hardcoded "
    "regime-index legend (R = |velocity| / seasonal amplitude) disappear with no error."
)


# ---------------------------------------------------------------------------------------------
# The assertions, factored out so the non-vacuity tests below drive the very same code
# ---------------------------------------------------------------------------------------------


def _assert_section_1(summary: Any, *, source: Any) -> None:
    """`summary` is one non-empty, single-line paragraph of §1 — and only §1."""
    assert summary is not None, (
        f"{source} no longer carries the marker {SCIENCE_EXTRACTOR} searches for: {MARKER!r}. "
        "The heading text is a parser input, not a formatting choice: rewording it (or moving the "
        f"section) makes the extractor return None.\n{CARD_FAILURE_HINT}\n"
        "Restore the heading verbatim, or change the card and this guard in the same commit."
    )
    assert isinstance(summary, str), f"{source}: §1 extracted as {type(summary).__name__}, not str"
    summary = summary.strip()
    assert summary, f"{source}: §1 extracted as an empty string; {CARD_FAILURE_HINT}"
    assert "\n" not in summary, (
        f"{source}: §1 did not collapse to one line (it contains {summary.count(chr(10)) + 1} "
        "lines), so the card renders it inside a single <p> with the newlines swallowed"
    )
    assert MARKER not in summary and "## " not in summary, (
        f"{source}: the extracted §1 still carries a markdown heading "
        f"({[line for line in summary.splitlines() if line.startswith('#')] or MARKER!r}); the "
        "paragraph was not trimmed to its body"
    )
    for phrase, where in PINNED_PHRASES:
        assert phrase in summary, (
            f"{source} no longer carries the phrase pinned from {where}:\n  {phrase!r}\n"
            "If §1 was genuinely rewritten, update PINNED_PHRASES here in the same commit — do not "
            "let the card silently start quoting different text."
        )
    for phrase, where in CAPTION_PHRASES:
        assert phrase not in summary, (
            f"{source}: the extracted §1 contains the figure text pinned from {where} "
            f"({phrase!r}) — the extractor stops at '\\n![' and the terminator is missing or was "
            "weakened, so the card would render the figure caption as if it were the physics "
            "paragraph"
        )
    assert "## 2." not in summary, (
        f"{source}: the extracted §1 runs past the next heading into §2 — none of the stop tokens "
        f"{list(STOP_TOKENS)} fired"
    )


def _declared_cases() -> dict[str, Any]:
    """`config/study_cases.yaml` as the document declares it (the other half of the pair)."""
    payload = yaml.safe_load(STUDY_CASES_PATH.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    assert isinstance(cases, dict) and cases, (
        f"{STUDY_CASES_PATH} has no cases: mapping, so the case card has nothing to describe"
    )
    return cases


def _assert_case_roles(roles: Any, declared: dict[str, Any], *, source: Any) -> None:
    """`roles` describes every declared case, faithfully, in the vocabulary the project uses."""
    assert isinstance(roles, dict) and roles, (
        f"{source}: {ROLES_EXTRACTOR} returned {roles!r}; the Case card reads "
        "limits['case_roles'][case_id] and would render no role line at all"
    )
    assert set(roles) == set(C.CASE_IDS), (
        f"{source}: {ROLES_EXTRACTOR} returned {sorted(roles)} but the contract's CASE_IDS are "
        f"{list(C.CASE_IDS)} — a case the card cannot describe, or one it invented"
    )
    assert set(declared) == set(C.CASE_IDS), (
        f"{STUDY_CASES_PATH} declares cases {sorted(declared)} but the contract's CASE_IDS are "
        f"{list(C.CASE_IDS)}"
    )

    for slug in C.CASE_IDS:
        expected = declared[slug] if isinstance(declared[slug], dict) else {}
        for key in CASE_KEYS:
            assert key in expected, (
                f"config/study_cases.yaml declares no {key}: for case {slug!r}. "
                f"loaders.load_study_cases_config substitutes {SILENT_DEFAULTS[key]!r} for it "
                "without raising, and the card then renders 'Declared role: … · Expected regime: … "
                "· Literature reference rate: …' with that entry missing — a Case card that looks "
                "complete and says less. Declare the key, or remove the card's use of it."
            )

        entry = roles[slug]
        assert isinstance(entry, dict), f"{source}: case {slug!r} is {type(entry).__name__}, not a mapping"
        for key in CASE_KEYS:
            assert key in entry, (
                f"{source}: {ROLES_EXTRACTOR} dropped {key!r} for case {slug!r} (keys: "
                f"{sorted(entry)}) — the card reads it under that exact name"
            )

        role = entry["role"]
        assert isinstance(role, str) and role.strip(), (
            f"{source}: case {slug!r} extracted an empty role: ; the Case card renders it as "
            f"'Declared role: …', so a blank here means the line silently disappears "
            f"(config/study_cases.yaml role: is {expected['role']!r})"
        )
        assert role == expected["role"], (
            f"{source}: case {slug!r} role extracted as {role!r} but "
            f"config/study_cases.yaml declares {expected['role']!r}"
        )

        regime = entry["expected_regime"]
        assert isinstance(regime, str) and regime.strip(), (
            f"{source}: case {slug!r} extracted an empty expected_regime: ; the card's 'Expected "
            "regime: …' clause disappears and nothing else fails"
        )
        assert regime == expected["expected_regime"], (
            f"{source}: case {slug!r} expected_regime extracted as {regime!r} but "
            f"config/study_cases.yaml declares {expected['expected_regime']!r}"
        )
        assert regime in C.REGIMES or regime in str(expected["role"]), (
            f"{source}: case {slug!r} declares expected_regime: {regime!r}, which is neither "
            f"contract vocabulary {list(C.REGIMES)} nor a word its own role: prose declares "
            f"({expected['role']!r}) — the card would print a regime token of unknown provenance. "
            "Note expected_regime is a site forecast, not nisar_grid.regime: 'mixed' is legitimate "
            "only because the role line says so (tests/conftest.py: 6 inelastic + 1 transitional)."
        )

        rate = entry["published_rate_mm_yr"]
        assert isinstance(rate, (int, float)) and not isinstance(rate, bool), (
            f"{source}: case {slug!r} published_rate_mm_yr extracted as {rate!r} "
            f"({type(rate).__name__}); the card formats it with format_number and shows "
            "'Literature reference rate: … mm/year'"
        )
        assert float(rate) == float(expected["published_rate_mm_yr"]), (
            f"{source}: case {slug!r} published_rate_mm_yr extracted as {rate!r} but "
            f"config/study_cases.yaml declares {expected['published_rate_mm_yr']!r} — the card "
            "would print a reference rate nobody wrote"
        )


def _without_key(text: str, case: str, key: str) -> str:
    """`text` with `key:` deleted from `cases.<case>` — a document edit made in tmp, never in place."""
    kept: list[str] = []
    in_case = False
    removed = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if line.startswith("  ") and not line.startswith("   ") and stripped.endswith(":"):
            in_case = stripped == f"{case}:"
        if in_case and not removed and stripped.startswith(f"{key}:"):
            removed = True
            continue
        kept.append(line)
    assert removed, f"config/study_cases.yaml has no {key}: line under cases.{case}: to remove"
    return "".join(kept)


def _without_stop_token(text: str, token: str) -> str:
    """`text` with one of the extractor's stop tokens deleted (the `\\n` kept, the token gone)."""
    assert token in text, f"docs/science-guide.md does not contain the stop token {token!r}"
    return text.replace(token, "\n", 1)


# ---------------------------------------------------------------------------------------------
# 1. The document and the extractor agree — §1
# ---------------------------------------------------------------------------------------------


def test_section_1_summary_is_pinned() -> None:
    """The card quotes §1 verbatim, and only §1: non-empty, one line, caption excluded."""
    summary = _extract_science_section_1(SCIENCE_PATH)
    _assert_section_1(summary, source=SCIENCE_PATH.relative_to(ROOT).as_posix())
    print(
        f"docs/science-guide.md §1 -> {len(summary)} chars, "
        f"{len(PINNED_PHRASES)} phrases matched, caption excluded"
    )


# ---------------------------------------------------------------------------------------------
# 2. The document and the extractor agree — the case roles
# ---------------------------------------------------------------------------------------------


def test_case_roles_are_pinned() -> None:
    """Every declared case reaches the Case card with its role, regime and reference rate intact."""
    roles = _extract_case_roles(STUDY_CASES_PATH)
    _assert_case_roles(roles, _declared_cases(), source=STUDY_CASES_PATH.relative_to(ROOT).as_posix())
    for slug in C.CASE_IDS:
        entry = roles[slug]
        print(
            f"{slug}: regime={entry['expected_regime']!r} "
            f"rate={entry['published_rate_mm_yr']!r} role={entry['role']!r}"
        )


# ---------------------------------------------------------------------------------------------
# 3. The card's own entry point carries both halves under the keys it reads
# ---------------------------------------------------------------------------------------------


def test_the_card_reads_both_halves() -> None:
    """`load_documented_limits` is what `render_physics_tab` calls; the two keys must survive it.

    Pinning the extractors alone would miss a rename *inside* the loader — the card reads
    `limits["science_section_1"]` and `limits["case_roles"]`, not the extractors' return values.
    """
    try:
        limits = UI_BRIDGE.load_documented_limits(
            readme_path=ROOT / "README.md",
            science_path=SCIENCE_PATH,
            study_cases_path=STUDY_CASES_PATH,
        )
    except TypeError as exc:
        pytest.fail(
            "src/data/ui_bridge.py::load_documented_limits no longer accepts "
            f"science_path/study_cases_path ({exc}); the card calls it with the defaults, so this "
            "guard cannot point it at the committed documents — restore the parameters",
            pytrace=False,
        )
    assert isinstance(limits, dict), f"load_documented_limits returned {type(limits).__name__}"
    assert "science_section_1" in limits, (
        f"load_documented_limits returns {sorted(limits)}; render_physics_tab reads "
        "'science_section_1' and would take the `if science_summary:` branch for ever"
    )
    assert "case_roles" in limits, (
        f"load_documented_limits returns {sorted(limits)}; render_physics_tab reads 'case_roles' "
        "and the Case card would lose its role line"
    )
    _assert_section_1(limits["science_section_1"], source="load_documented_limits()")
    _assert_case_roles(limits["case_roles"], _declared_cases(), source="load_documented_limits()")


# ---------------------------------------------------------------------------------------------
# 4. Non-vacuity: the assertions above really can fail, on the very documents they read
# ---------------------------------------------------------------------------------------------


def test_renaming_the_heading_breaks_the_card(tmp_path: pathlib.Path) -> None:
    """Somebody rewords §1's heading: §1 is still there, the card is not, and this file goes red."""
    source = SCIENCE_PATH.read_text(encoding="utf-8")
    assert MARKER in source, f"docs/science-guide.md does not contain the marker {MARKER!r}"
    renamed = tmp_path / "science-guide.md"
    renamed.write_text(
        source.replace(MARKER, "## 1. The physics, in one paragraph"), encoding="utf-8"
    )

    assert _extract_science_section_1(renamed) is None, (
        "renaming the heading did not stop the extractor: it no longer keys on MARKER verbatim, "
        "so this guard's '§1 is present' assertion is vacuous"
    )
    with pytest.raises(AssertionError) as failure:
        _assert_section_1(_extract_science_section_1(renamed), source=str(renamed))
    assert MARKER in str(failure.value), (
        "the failure does not name the missing heading, which is the one thing a reader needs: "
        f"{failure.value}"
    )
    print(f"renamed heading -> extractor None; failure names {MARKER!r}")


def test_dropping_the_figure_terminator_breaks_the_card(tmp_path: pathlib.Path) -> None:
    """Somebody deletes the `![` rule above the figure: §1 now swallows the caption past it."""
    source = SCIENCE_PATH.read_text(encoding="utf-8")
    leaky = tmp_path / "science-guide.md"
    leaky.write_text(_without_stop_token(source, STOP_TOKENS[0]), encoding="utf-8")

    summary = _extract_science_section_1(leaky)
    assert summary is not None and CAPTION_PHRASES[1][0] in summary, (
        "with the '![' terminator removed the extractor still excluded the figure caption, so the "
        "caption assertion is vacuous"
    )
    with pytest.raises(AssertionError) as failure:
        _assert_section_1(summary, source=str(leaky))
    assert CAPTION_PHRASES[1][0] in str(failure.value), (
        f"the failure does not name the caption text that leaked: {failure.value}"
    )
    print(f"terminator removed -> caption leaks; failure names {CAPTION_PHRASES[1][0]!r}")


@pytest.mark.parametrize("key", CASE_KEYS)
def test_dropping_a_declared_key_breaks_the_card(key: str, tmp_path: pathlib.Path) -> None:
    """Drop one key from one case: the parser returns a plausible entry, the card goes quiet.

    `load_study_cases_config` substitutes :data:`SILENT_DEFAULTS`, so the extractor still returns
    a dict — this is the failure mode the card cannot show and the human eye cannot read.
    """
    case = "jakarta"
    mutated = tmp_path / "study_cases.yaml"
    mutated.write_text(
        _without_key(STUDY_CASES_PATH.read_text(encoding="utf-8"), case, key), encoding="utf-8"
    )

    roles = _extract_case_roles(mutated)
    assert roles[case][key] == SILENT_DEFAULTS[key], (
        f"deleting {key}: for {case!r} did not fall back to {SILENT_DEFAULTS[key]!r} "
        f"(extracted {roles[case][key]!r}), so this non-vacuity proof no longer models the silent "
        "default it is meant to model"
    )
    declared = yaml.safe_load(mutated.read_text(encoding="utf-8"))["cases"]
    with pytest.raises(AssertionError) as failure:
        _assert_case_roles(roles, declared, source=str(mutated))
    message = str(failure.value)
    assert case in message and key in message, (
        f"the failure names neither the case nor the key, so a reader cannot act on it: {message}"
    )
    print(f"deleted {key}: of {case} -> extractor returns {roles[case][key]!r}; failure names it")
