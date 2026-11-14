"""Drift guard for ``docs/video-script.md`` — every number the presenter reads on camera.

The presenter reads that script aloud, so a stale figure is a wrong claim on the recording, not a
typo. This test binds the script to the demo number card (``scripts/demo_numbers.py --json``),
which is the single derived source: it computes every scientific number from ``data/processed/``.

What is checked
---------------
* Every numeric claim in the script must agree with a value the card produces, matched **by
  meaning**: a ``%`` against a coverage fraction, ``mm/yr`` against a velocity, ``×``/``x``
  against the contrast ratio, "cells" against ``cells``, "dates"/"days"/"revisit" against
  ``n_dates``/``window_days``/``revisit_days``, metres against ``posting_m`` / the H3 cell edge,
  "detection bound" against ``detection_limit_mm_yr``.
* A number the card does **not** produce fails loudly and is reported, never allowlisted: that is
  the most valuable failure, because the script and the card have drifted apart.
* Numbers that are legitimately independent of the artefacts (production timings, the card's run
  date, README structure counts) are allowlisted below **with a one-line reason each**.
* The honesty beats that are submission requirements must be present: the mock statement when
  every loaded case is ``product == "mock"`` (and its absence when a case is real), the detection
  bound behind the null result, the coverage caveat, and the seasonal-window caveat.

Tolerances (stated, never widened to make the suite pass)
---------------------------------------------------------
Both documents print rounded decimals, so a claim may differ from the derived value by half of the
last digit the script prints (``54.7`` vs ``54.68`` → ±0.05; ``62 percent`` vs ``61.98 %`` →
±0.5; ``−223.32`` vs ``-223.3191`` → ±0.005). The one extra allowance is for lengths: the card
prints the H3 cell edge as ``~201 m across`` — a tilde, rounded to whole metres — so a metre
claim carries another ±0.5 m (that is what makes the ledger's ``200 m cell`` honest). Cell counts,
velocities, dates and day counts are compared at the script's own printed precision only.

A ``mm/yr`` claim without an explicit minus sign is read as a magnitude: the ledger column prints
``54.7 mm/yr`` for ``median=-54.68`` and the sign is spoken as the word "minus" in the say-aloud
column. Signed claims (``−410.75``, ``minus 54.7``) are compared strictly.

Hermetic: one subprocess read of ``data/processed/``, no clock, no network, no writes.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "video-script.md"
CARD = ROOT / "scripts" / "demo_numbers.py"
PROCESSED = ROOT / "data" / "processed"

#: Calendar months per year, used only to re-derive the "4.3 months" span the card prints.
DAYS_PER_MONTH = 365.25 / 12.0
#: Subprocess ceiling; the card reads three parquet files and normally finishes in ~2 s.
CARD_TIMEOUT_S = 60


def _skip_reason() -> str | None:
    """Skip cleanly (and say why) when the doc under test or its inputs are absent."""
    missing = [str(path.relative_to(ROOT)) for path in (DOC, PROCESSED) if not path.exists()]
    if not missing:
        return None
    return (
        "video-script drift guard needs "
        + " and ".join(missing)
        + " — keep docs/video-script.md in the tree and run "
        "`./scripts/python.sh pipeline/generate_mock.py` to produce data/processed/"
    )


_SKIP = _skip_reason()

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


# ---------------------------------------------------------------------------------------------
# Allowlist: numbers that are legitimately NOT derived from the artefacts.
#
# Every entry carries the reason it is allowed to be independent. An entry here is a promise that
# the number cannot go stale when data/processed/ changes.
# ---------------------------------------------------------------------------------------------

#: Digits that are not scientific claims (matched against the line, must cover the token).
DIGIT_ALLOWLIST: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b\d+\s+words?\b"),
        "table header word cap ('≤ 25 words'); a speaking-time budget, not a measurement",
    ),
    (
        re.compile(r"\(spoken to \d+ dp\)"),
        "print-precision annotation in the ledger; it says how a number is spoken, not what it is",
    ),
    (
        re.compile(r"val_t1\s*=\s*0\b"),
        "handoff §3: the first acquisition defines zero displacement by construction — a series"
        " origin, not a derived statistic",
    ),
)

#: Dates that are legitimately independent of the artefacts (window dates ARE checked).
DATE_ALLOWLIST: dict[str, str] = {
    "2026-11-14": "the card's run date quoted in the header ('run on data/processed/, 2026-11-14')"
    " — when the card was executed, not what it measured",
}

#: Spelled-out numerals the script uses as prose/rhetoric rather than as measurements.
WORD_ALLOWLIST: dict[tuple[str, str], str] = {
    ("one", "presenter"): "shot planning ('One presenter, read aloud'), not a count of anything",
    ("one", "question"): "rhetorical opening line",
    ("one", "place"): "rhetorical opening line ('why does the ground fall in one place')",
    ("one", "of"): "rhetorical ('One of them never leaves the detection floor')",
    ("one", "cell"): "the map unit is singular by definition; not a cell count",
    ("one", "layer"): "provenance wording ('one layer's source')",
    ("one", "id"): "provenance wording ('one ID you can rerun and diff')",
    ("one", "line"): "layout note for the closing card ('one line, hold 4 s')",
    ("one", "shared"): "stage direction ('one shared scale')",
    ("one", "detected"): "bracketed swap: conditional pin text, no count of the artefacts",
    ("one", "real"): "bracketed swap: 'one real NISAR GUNW granule', only if that granule lands",
    ("one", "pump"): "video title ('Three Cities, One Pump')",
    ("two", "layer"): "README §3 architecture diagram ('two-layer diagram')",
    ("two", "things"): "editor note ('the only two things to re-check'), production checklist",
    ("four", "artefacts"): "README §4: the frozen contract has four artefacts — a doc fact, not a"
    " demo-card statistic",
    ("four", "frozen"): "README §4: 'four frozen artefacts', same doc fact",
    ("five", "phrases"): "heading of the 'Do not say' table; the table has five rows",
    ("zero", "at"): "the series starts at zero by construction (handoff §3), not a measurement",
    ("zero", "starts"): "'Zero starts the record' — same construction, not a measurement",
}

#: Spelled-out numerals that ARE claims about the artefacts, and where they are derived from.
WORD_CLAIMS: dict[tuple[str, str], str] = {
    ("three", "cities"): "CASE_COUNT",
    ("three", "cases"): "CASE_COUNT",
    ("three", "panels"): "CASE_COUNT",
    ("three", "panel"): "CASE_COUNT",
    ("twelve", "dates"): "DATE_COUNT",
    ("twelve", "day"): "REVISIT",
}

WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


# ---------------------------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Derived:
    """One value the demo card produces, with the label to name it in a failure message."""

    value: float
    label: str


@dataclass(frozen=True)
class Claim:
    """One numeric claim found in the script."""

    kind: str
    value: float
    raw: str
    line_no: int
    line: str
    decimals: int
    #: True when the script itself writes the minus (word or sign) — compared strictly.
    negative: bool = False
    #: For ``MOCK_FRACTION`` claims: the denominator.
    denominator: float | None = None


@dataclass
class ParsedLine:
    line_no: int
    line: str
    claims: list[Claim] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    provenance_ids: list[str] = field(default_factory=list)
    #: Numeric tokens no rule claimed and no allowlist entry covers: (raw, column).
    leftovers: list[tuple[str, int]] = field(default_factory=list)
    #: Word numerals that are neither derived claims nor allowlisted prose.
    word_leftovers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------

#: A number as both documents print it: `-54.68`, `−410.75`, `13,379`, `49.1`.
NUM = r"[−–-]?\d[\d,]*(?:\.\d+)?"

#: Production scaffolding, blanked before any number is read (not "allowlisted": invisible).
_MASKS: tuple[re.Pattern[str], ...] = (
    # 0:00, 1:50–2:20 — shot timestamps
    re.compile(r"\d{1,2}:\d{2}(?:\s*[–-]\s*\d{1,2}:\d{2})?"),
    # 20 s, 90 s, 30 s, hold 4 s — shot durations
    re.compile(r"\b\d+\s*s(?![\w])"),
    # README §13, §13.3, handoff §3 — cross-references
    re.compile(r"§\s*\d+(?:\.\d+)*"),
    # H3 res-9, Sentinel-1, v1.0 — identifiers and mission names, never measurements
    re.compile(r"\bres-\d+\b"),
    re.compile(r"\bSentinel-\d+[A-Za-z]?\b"),
    re.compile(r"\bH3\b"),
    re.compile(r"\bv\d+(?:\.\d+)?\b"),
    # '## 1 · Problem' — section numbers
    re.compile(r"^#{1,6}\s+\d+"),
)

_PROVENANCE_RE = re.compile(r"provenance_id\s*=\s*([\w.+-]+)")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOKEN_RE = re.compile(rf"(?<![\w.]){NUM}(?!\d)")
_WORD_RE = re.compile(
    rf"\b({'|'.join(WORDS)})\b[\s-]+([A-Za-z]+)",
    re.IGNORECASE,
)
_COVERAGE_CONTEXT_RE = re.compile(r"coverage|percent|per cent|threshold", re.IGNORECASE)

#: Unit-bearing single-number rules, most specific first. Each names the token group ``num``.
_SINGLE_MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "13-day revisit"
    ("REVISIT", re.compile(rf"(?P<num>\d+)\s*[- ]day\s+revisit")),
    ("REVISIT", re.compile(rf"revisit_days\s*=\s*(?P<num>\d+)")),
    # "below its 1.5 detection bound", `detection_limit=1.50`
    ("DETECTION", re.compile(rf"(?P<num>{NUM})\s*(?:mm/yr\s*)?detection\s+(?:bound|floor|limit)")),
    ("DETECTION", re.compile(rf"\bdetection_limit\s*=\s*(?P<num>{NUM})")),
    # "minus 54.7 millimetres a year", "±425 mm/yr", "1.5 mm/yr"
    (
        "VELOCITY",
        re.compile(
            rf"(?:(?P<sign>±|plus or minus|minus)\s+)?(?P<num>{NUM})\s*"
            r"(?:mm\s*/\s*yr|mm\s+(?:a|per)\s+year|millimetres?\s+(?:a|per)\s+year|"
            r"millimetres?/year|millimetres?\s+a\s+year)"
        ),
    ),
    # "±425" on its own: the shared-legend toggle (the only ± figure in the script)
    ("VELOCITY", re.compile(rf"±\s?(?P<num>{NUM})")),
    ("VELOCITY", re.compile(rf"(?P<num>{NUM})\s+core\b")),
    ("VELOCITY", re.compile(rf"\bp0?5\s+(?P<num>{NUM})")),
    ("VELOCITY", re.compile(rf"(?P<num>{NUM})\s+p0?5\b")),
    ("VELOCITY", re.compile(rf"\bmedian\s*=\s*(?P<num>{NUM})")),
    ("VELOCITY", re.compile(rf"max abs velocity\s+(?P<num>{NUM})")),
    # coverage
    ("COVERAGE", re.compile(rf"(?P<num>{NUM})\s*(?:%|percent\b|per cent\b)")),
    ("COVERAGE", re.compile(rf"\bcoverage\s*=\s*(?P<num>{NUM})")),
    # contrast ratio: "49.1x", "49×", "49 times"
    ("RATIO", re.compile(rf"(?P<num>{NUM})\s*(?:×|times\b|x(?![\w]))")),
    ("CELLS", re.compile(rf"(?P<num>\d[\d,]*)\s*cells?(?![\w])")),
    ("CELLS", re.compile(rf"\bcells\s*=\s*(?P<num>\d[\d,]*)")),
    ("DATE_COUNT", re.compile(rf"(?P<num>\d[\d,]*)\s+dates(?![\w])")),
    ("DAYS", re.compile(rf"(?P<num>\d[\d,]*)\s*(?:days?(?![\w])|d(?![\w]))")),
    ("MONTHS", re.compile(rf"(?P<num>{NUM})[- ]months?(?![\w])")),
    ("YEARS", re.compile(rf"(?P<num>{NUM})\s*(?:yr|years?)(?![\w])")),
    ("LENGTH", re.compile(rf"(?P<num>{NUM})\s*(?:m(?![\w])|metres?(?![\w])|meters?(?![\w]))")),
    ("MOCK_FRACTION", re.compile(r"(?P<num>\d+)\s*/\s*(?P<den>\d+)\s+cases?")),
)


def _to_float(raw: str) -> float:
    return float(raw.replace("−", "-").replace("–", "-").replace(",", "").replace("+", ""))


def _decimals(raw: str) -> int:
    return len(raw.split(".", 1)[1]) if "." in raw else 0


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    """Replace matches with spaces, keeping every other offset intact for error columns."""
    return pattern.sub(lambda match: " " * (match.end() - match.start()), text)


def _parse_line(line_no: int, line: str) -> ParsedLine:
    parsed = ParsedLine(line_no=line_no, line=line)

    for match in _PROVENANCE_RE.finditer(line):
        parsed.provenance_ids.append(match.group(1))
    for match in _DATE_RE.finditer(line):
        parsed.dates.append(match.group(0))

    text = _PROVENANCE_RE.sub(lambda m: " " * (m.end() - m.start()), line)
    text = _blank(text, _DATE_RE)
    for pattern in _MASKS:
        text = _blank(text, pattern)

    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e in taken)

    def take(start: int, end: int) -> None:
        taken.append((start, end))

    def add(
        kind: str,
        raw: str,
        negative: bool = False,
        negate: bool = False,
        denominator: float | None = None,
    ) -> None:
        value = _to_float(raw)
        if negate:
            value = -abs(value)
        parsed.claims.append(
            Claim(
                kind=kind,
                value=value,
                raw=raw,
                line_no=line_no,
                line=line,
                decimals=_decimals(raw.replace("−", "-").replace(",", "")),
                negative=negative,
                denominator=denominator,
            )
        )

    # Arithmetic forms first: they own their operands, so no operand is re-read as a lone number.
    #   `206.36 / 4.20 = 49.1x`            → two spreads and the contrast ratio
    #   `... velocity 410.75 / 25) * 25`   → the core velocity and the 25 mm/yr legend step
    for match in re.finditer(
        rf"(?P<a>{NUM})\s*/\s*(?P<b>{NUM})\s*=\s*(?P<r>{NUM})\s*[x×]", text
    ):
        if not free(match.start(), match.end()):
            continue
        add("SPREAD", match.group("a"))
        add("SPREAD", match.group("b"))
        add("RATIO", match.group("r"))
        take(match.start(), match.end())
    for match in re.finditer(
        rf"(?P<v>{NUM})\s*/\s*(?P<step>{NUM})\s*\)\s*\*\s*(?P<step2>{NUM})", text
    ):
        if not free(match.start(), match.end()):
            continue
        add("VELOCITY", match.group("v"))
        add("LEGEND_STEP", match.group("step"))
        add("LEGEND_STEP", match.group("step2"))
        take(match.start(), match.end())
    #   `−1.4 and 1.5 mm/yr` — a unit written once for two claims
    for match in re.finditer(
        rf"(?P<a>{NUM})\s+and\s+(?P<b>{NUM})\s*mm\s*/\s*yr", text
    ):
        if not free(match.start(), match.end()):
            continue
        add("VELOCITY", match.group("a"), negative=match.group("a").startswith(("−", "–", "-")))
        add("VELOCITY", match.group("b"), negative=match.group("b").startswith(("−", "–", "-")))
        take(match.start(), match.end())

    for kind, pattern in _SINGLE_MATCHERS:
        for match in pattern.finditer(text):
            if not free(match.start("num"), match.end("num")):
                continue
            raw = match.group("num")
            sign = match.groupdict().get("sign")
            negative = raw.startswith(("−", "–", "-")) or (sign or "").lower() == "minus"
            denominator = None
            if kind == "MOCK_FRACTION":
                denominator = float(match.group("den"))
            # "minus 54.7 millimetres a year": the sign is a word, so fold it into the value
            add(
                kind,
                raw,
                negative=negative,
                negate=negative and not raw.startswith(("−", "–", "-")),
                denominator=denominator,
            )
            take(match.start(), match.end())

    # An elided unit in a coverage sentence: "Coverage is 62 percent ..., 46 in Jakarta, 39 in
    # Málaga" — the later two are still coverage percentages, so read them as such.
    if _COVERAGE_CONTEXT_RE.search(text):
        for match in _TOKEN_RE.finditer(text):
            if not free(match.start(), match.end()):
                continue
            value = _to_float(match.group(0))
            if not 0.0 <= value <= 100.0:
                continue
            add("COVERAGE", match.group(0), negative=match.group(0).startswith(("−", "–", "-")))
            take(match.start(), match.end())

    # "below half" in a coverage sentence is the 50 % threshold.
    for match in re.finditer(r"\bhalf\b", text, re.IGNORECASE):
        if _COVERAGE_CONTEXT_RE.search(text) and free(match.start(), match.end()):
            add("COVERAGE", "50")
            take(match.start(), match.end())

    for match in _TOKEN_RE.finditer(text):
        if free(match.start(), match.end()):
            parsed.leftovers.append((match.group(0), match.start()))

    for match in _WORD_RE.finditer(text):
        word = match.group(1).lower()
        noun = match.group(2).lower()
        key = (word, noun)
        if key in WORD_CLAIMS:
            add(WORD_CLAIMS[key], str(WORDS[word]))
        elif key not in WORD_ALLOWLIST:
            parsed.word_leftovers.append(f"{word} {noun}")

    return parsed


def _parse_doc() -> list[ParsedLine]:
    return [
        _parse_line(number, line)
        for number, line in enumerate(DOC.read_text(encoding="utf-8").splitlines(), start=1)
    ]


def _parse_prose(text: str) -> ParsedLine:
    """Parse card prose (limitations, do-not-claim) with the same rules as the script.

    Prose figures are still numbers the card produces — ``~201 m across``, ``132 d (4.3 months)``,
    ``274 d`` — so they belong in the derived set. A claim must still land within the token's own
    printed precision, so prose does not buy anyone a looser tolerance.
    """
    return _parse_line(0, text)


# ---------------------------------------------------------------------------------------------
# The derived source: scripts/demo_numbers.py --json
# ---------------------------------------------------------------------------------------------

_VELOCITY_FIELDS = (
    "median_velocity_mm_yr",
    "p05_velocity_mm_yr",
    "p95_velocity_mm_yr",
    "velocity_min_mm_yr",
    "velocity_max_mm_yr",
    "max_abs_velocity_mm_yr",
    "spread_mm_yr",
)


def _card() -> dict:
    """Run the demo number card once; it is the single derived source for every number."""
    completed = subprocess.run(
        [sys.executable, str(CARD), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=CARD_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, (
        f"`{CARD.relative_to(ROOT)} --json` failed (exit {completed.returncode}); the video script "
        f"cannot be checked against a card that does not run.\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:  # pragma: no cover - defensive
        raise AssertionError(
            f"`{CARD.relative_to(ROOT)} --json` did not emit JSON: {error}\n{completed.stdout[:2000]}"
        ) from error


@pytest.fixture(scope="module")
def card() -> dict:
    return _card()


@pytest.fixture(scope="module")
def derived(card: dict) -> dict[str, list[Derived]]:
    return _build_derived(card)


@pytest.fixture(scope="module")
def parsed() -> list[ParsedLine]:
    return _parse_doc()


def _build_derived(card: dict) -> dict[str, list[Derived]]:
    buckets: dict[str, list[Derived]] = defaultdict(list)

    for case in card.get("cases", []):
        case_id = case["case_id"]
        for name in _VELOCITY_FIELDS:
            if case.get(name) is not None:
                buckets["VELOCITY"].append(
                    Derived(float(case[name]), f"{case_id}.{name}")
                )
        limit = case.get("detection_limit_mm_yr")
        if limit is not None:
            buckets["DETECTION"].append(
                Derived(float(limit), f"{case_id}.detection_limit_mm_yr")
            )
            buckets["VELOCITY"].append(
                Derived(float(limit), f"{case_id}.detection_limit_mm_yr (mm/yr)")
            )
        coverage = case.get("coverage") or {}
        if coverage.get("fraction") is not None:
            buckets["COVERAGE"].append(
                Derived(float(coverage["fraction"]) * 100.0, f"{case_id}.coverage.fraction (as %)")
            )
        if coverage.get("warning_threshold") is not None:
            buckets["COVERAGE"].append(
                Derived(
                    float(coverage["warning_threshold"]) * 100.0,
                    f"{case_id}.coverage.warning_threshold (as %)",
                )
            )
        for name in ("covered_cells", "aoi_cells"):
            if coverage.get(name) is not None:
                buckets["CELLS"].append(Derived(float(coverage[name]), f"{case_id}.coverage.{name}"))
        if case.get("cells") is not None:
            buckets["CELLS"].append(Derived(float(case["cells"]), f"{case_id}.cells"))
        for name, bucket in (
            ("window_days", "DAYS"),
            ("revisit_days", "REVISIT"),
            ("n_dates", "DATE_COUNT"),
            ("posting_m", "LENGTH"),
        ):
            if case.get(name) is not None:
                buckets[bucket].append(Derived(float(case[name]), f"{case_id}.{name}"))
        if case.get("window_days") is not None:
            buckets["MONTHS"].append(
                Derived(
                    float(case["window_days"]) / DAYS_PER_MONTH,
                    f"{case_id}.window_days re-expressed in months",
                )
            )

    contrast = card.get("contrast") or {}
    for name in (
        "shared_scale_mm_yr",
        "shared_legend_mm_yr",
        "max_abs_velocity_mm_yr",
        "p05_min_mm_yr",
        "velocity_min_mm_yr",
    ):
        if contrast.get(name) is not None:
            buckets["VELOCITY"].append(Derived(float(contrast[name]), f"contrast.{name}"))
    for case_id, spread in (contrast.get("spreads_mm_yr") or {}).items():
        buckets["SPREAD"].append(Derived(float(spread), f"contrast.spreads_mm_yr[{case_id}]"))
    for name, bucket in (("ratio", "RATIO"), ("legend_step_mm_yr", "LEGEND_STEP")):
        if contrast.get(name) is not None:
            buckets[bucket].append(Derived(float(contrast[name]), f"contrast.{name}"))

    for entry in card.get("null_result", []):
        case_id = entry.get("case_id", "?")
        if entry.get("median_velocity_mm_yr") is not None:
            buckets["VELOCITY"].append(
                Derived(float(entry["median_velocity_mm_yr"]), f"null_result[{case_id}].median")
            )
        if entry.get("detection_limit_mm_yr") is not None:
            value = float(entry["detection_limit_mm_yr"])
            buckets["DETECTION"].append(
                Derived(value, f"null_result[{case_id}].detection_limit_mm_yr")
            )
            buckets["VELOCITY"].append(
                Derived(value, f"null_result[{case_id}].detection_limit (mm/yr)")
            )

    buckets["CASE_COUNT"].append(Derived(float(len(card.get("cases", []))), "len(cases)"))
    buckets["MOCK_FRACTION"].append(
        Derived(float(len((card.get("honesty") or {}).get("mock_cases") or [])), "len(mock_cases)")
    )

    # Everything the card prints as prose is a number the card produces, too.
    honesty = card.get("honesty") or {}
    prose: list[str] = list(honesty.get("do_not_claim") or [])
    for limitations in (honesty.get("limitations") or {}).values():
        prose.extend(limitations)
    for entry in card.get("null_result", []):
        prose.append(entry.get("sentence", ""))
    prose.extend(card.get("skeleton") or [])
    prose.append(honesty.get("mock_warning", ""))
    for text in prose:
        for claim in _parse_prose(text).claims:
            if claim.kind == "MOCK_FRACTION":
                continue
            buckets[claim.kind].append(
                Derived(claim.value, f"card prose: {text.strip()[:60]!r}…")
            )

    return dict(buckets)


# ---------------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------------


def _tolerance(claim: Claim, entry: Derived) -> float:
    """Half of the last digit the script prints; plus half a metre for a printed length.

    Example: script ``54.7`` vs derived ``-54.68`` → 0.05. Script ``200 m`` vs the card's
    ``~201 m across`` → 0.5 + 0.5 = 1.0, because the card prints that edge with a tilde, rounded
    to whole metres. Nothing wider is ever granted: cell counts, velocities, dates and day counts
    are compared at the script's own printed precision only.
    """
    tolerance = 0.5 * 10 ** -claim.decimals
    if claim.kind == "LENGTH":
        tolerance += 0.5  # card: 'one H3 res-9 cell is ~201 m across', rounded to the metre
    return tolerance


def _distance(claim: Claim, entry: Derived) -> float:
    """Smallest gap to the derived value, allowing a magnitude reading for unsigned claims."""
    gap = abs(claim.value - entry.value)
    if not claim.negative and claim.kind in {"VELOCITY", "DETECTION", "SPREAD"}:
        gap = min(gap, abs(abs(claim.value) - abs(entry.value)))
    return gap


def _closest(value: float, buckets: dict[str, list[Derived]]) -> str:
    """Name the derived value nearest to ``value`` so a failure is actionable."""
    candidates = [
        (abs(value - entry.value), entry)
        for name, entries in buckets.items()
        for entry in entries
        if name != "DATE"
    ]
    if not candidates:
        return "no derived values at all"
    candidates.sort(key=lambda item: item[0])
    return "; ".join(
        f"{entry.value:g} ({entry.label})" for _, entry in candidates[:3]
    )


def _check_claim(
    claim: Claim, buckets: dict[str, list[Derived]]
) -> tuple[bool, str]:
    """Return (ok, message) for one claim: matched by kind, by tolerance, never by luck."""
    if claim.kind == "MOCK_FRACTION":
        mocks = [entry.value for entry in buckets.get("MOCK_FRACTION", [])]
        totals = [entry.value for entry in buckets.get("CASE_COUNT", [])]
        expected_mocks = mocks[0] if mocks else None
        expected_total = totals[0] if totals else None
        ok = (
            expected_total is not None
            and expected_mocks is not None
            and claim.denominator == expected_total
            and claim.value == expected_mocks
        )
        if ok:
            return True, ""
        return False, (
            f"docs/video-script.md:{claim.line_no}: {claim.raw!r} ({claim.kind}) says "
            f"{claim.value:g}/{claim.denominator:g} cases are mock, but the card loads "
            f"{expected_mocks:g} mock case(s) out of {expected_total:g} — "
            f"line: {claim.line.strip()!r}"
        )

    entries = buckets.get(claim.kind) or []
    if not entries:
        return False, (
            f"docs/video-script.md:{claim.line_no}: {claim.raw!r} ({claim.kind}) has no derived "
            f"counterpart — the demo card produces no {claim.kind} values at all, so the script "
            f"and the card have drifted apart. Line: {claim.line.strip()!r}"
        )

    best = min(entries, key=lambda entry: _distance(claim, entry) - _tolerance(claim, entry))
    gap = _distance(claim, best)
    tolerance = _tolerance(claim, best)
    if gap <= tolerance + 1e-12:
        return True, ""

    return False, (
        f"docs/video-script.md:{claim.line_no}: {claim.raw!r} ({claim.kind}) disagrees with the "
        f"demo card. Nearest derived value: {best.value:g} ({best.label}); gap {gap:g} > "
        f"tolerance {tolerance:g}. Nearest derived values overall: {_closest(claim.value, buckets)}."
        f"\n    line: {claim.line.strip()!r}"
    )


# ---------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------


def test_the_parser_sees_every_kind_of_claim_the_script_makes(parsed: list[ParsedLine]) -> None:
    """A parser that silently sees nothing would pass every other test: pin its recall.

    Counts, not literal values: a presenter may rewrite a figure, and then the value checks — not
    this test — are what should speak. What must not happen is a whole category of claim (a
    velocity, a coverage percentage, the contrast ratio) quietly dropping out of the parse.
    """
    by_kind: dict[str, list[Claim]] = defaultdict(list)
    for line in parsed:
        for claim in line.claims:
            by_kind[claim.kind].append(claim)

    minimum = {
        "VELOCITY": 4,  # medians, the shared legend, the core, the bound
        "COVERAGE": 3,  # the per-case percentages and the threshold
        "RATIO": 1,  # the ×49 contrast
        "CELLS": 1,  # 13,379 cells
        "DATE_COUNT": 1,  # Twelve dates / 12 dates
        "REVISIT": 1,  # twelve-day revisit
        "DAYS": 1,  # 132 days / the 274 d gate
        "LENGTH": 1,  # 80 m posting, the H3 cell edge
        "DETECTION": 1,  # the Málaga bound
        "CASE_COUNT": 1,  # Three Cities / all three cases
        "MOCK_FRACTION": 1,  # 3/3 cases
    }
    missing = {
        kind: ([claim.raw for claim in by_kind.get(kind, [])], need)
        for kind, need in minimum.items()
        if len(by_kind.get(kind, [])) < need
    }
    assert not missing, (
        "the script parser lost claims it used to see (the doc or the parser drifted): "
        + ", ".join(
            f"{kind}: found {found!r}, expected at least {need}"
            for kind, (found, need) in missing.items()
        )
    )


def test_the_masks_only_swallow_production_scaffolding() -> None:
    """Anything masked must be a timestamp/duration/identifier — never a scientific figure."""
    scientific = re.compile(
        r"cells?|mm|%|×|percent|metres?|dates?|days?|revisit|detection|coverage|times",
        re.IGNORECASE,
    )
    failures = [
        f"docs/video-script.md:{number}: mask {pattern.pattern!r} swallowed {match.group(0)!r}"
        for number, line in _lines()
        for pattern in _MASKS
        for match in pattern.finditer(line)
        if scientific.search(match.group(0))
    ]
    assert not failures, (
        "a mask is hiding a scientific claim instead of production scaffolding:\n\n"
        + "\n".join(failures)
    )


def test_parser_ignores_timestamps_durations_and_section_refs() -> None:
    """The mask list is load-bearing: timestamps must never become scientific claims."""
    sample = (
        "| 0:20–0:30 | README §13.3, handoff §3 | 20 s problem · 90 s demo = 2:20 "
        "| `v1.0` H3 res-9 | hold 4 s |"
    )
    parsed = _parse_line(1, sample)
    assert not parsed.claims, f"production scaffolding was read as claims: {[c.raw for c in parsed.claims]}"
    assert not parsed.leftovers, f"production scaffolding left number tokens behind: {parsed.leftovers}"


def test_every_scientific_number_matches_the_demo_card(
    parsed: list[ParsedLine], derived: dict[str, list[Derived]]
) -> None:
    """The guard: every number the presenter will say is re-derived from data/processed/."""
    failures: list[str] = []
    for line in parsed:
        for claim in line.claims:
            ok, message = _check_claim(claim, derived)
            if not ok:
                failures.append(message)
    assert not failures, (
        f"{len(failures)} spoken number(s) in docs/video-script.md no longer match "
        f"`scripts/demo_numbers.py --json`; the recording would state them as fact:\n\n"
        + "\n\n".join(failures)
    )


def test_every_other_number_is_allowlisted_or_absent(
    parsed: list[ParsedLine], derived: dict[str, list[Derived]]
) -> None:
    """A number neither derived nor allowlisted is the failure the guard exists for."""
    failures: list[str] = []
    for line in parsed:
        for raw, column in line.leftovers:
            allowed = any(
                match.start() <= column and column + len(raw) <= match.end()
                for pattern, _ in DIGIT_ALLOWLIST
                for match in pattern.finditer(line.line)
            )
            if not allowed:
                failures.append(
                    f"docs/video-script.md:{line.line_no}: {raw!r} is not produced by the demo "
                    f"card and is not allowlisted. Nearest derived values: "
                    f"{_closest(_to_float(raw), derived)}."
                    f"\n    line: {line.line.strip()!r}\n"
                    f"    Either fix the number or add it to DIGIT_ALLOWLIST with a one-line "
                    f"reason — never both."
                )
        for phrase in line.word_leftovers:
            failures.append(
                f"docs/video-script.md:{line.line_no}: spelled-out numeral {phrase!r} is neither "
                f"a derived claim nor allowlisted prose."
                f"\n    line: {line.line.strip()!r}\n"
                f"    Add it to WORD_ALLOWLIST (with a reason) or WORD_CLAIMS (if derived)."
            )
    assert not failures, "unclassified numbers in docs/video-script.md:\n\n" + "\n\n".join(failures)


def test_allowlist_entries_carry_a_reason() -> None:
    """An allowlist without reasons rots into a list of things nobody dares delete."""
    for pattern, reason in DIGIT_ALLOWLIST:
        assert reason.strip(), f"allowlist entry {pattern.pattern!r} has no reason"
    for key, reason in WORD_ALLOWLIST.items():
        assert reason.strip(), f"word allowlist entry {key!r} has no reason"
    for date, reason in DATE_ALLOWLIST.items():
        assert reason.strip(), f"date allowlist entry {date!r} has no reason"


def test_dates_are_the_artefact_window(
    parsed: list[ParsedLine], card: dict
) -> None:
    """A window date is as load-bearing as a velocity: 2026-11-11 is the last acquisition."""
    window = {
        value
        for case in card.get("cases", [])
        for value in (case.get("date_first"), case.get("date_last"))
        if value
    }
    failures: list[str] = []
    for line in parsed:
        for date in line.dates:
            if date in window:
                continue
            if date in DATE_ALLOWLIST:
                continue
            failures.append(
                f"docs/video-script.md:{line.line_no}: date {date} is not an acquisition date of "
                f"the loaded cases {sorted(window)} and is not allowlisted."
                f"\n    line: {line.line.strip()!r}"
            )
    assert not failures, "dates in the script that the artefacts do not support:\n\n" + "\n\n".join(
        failures
    )


def test_provenance_id_is_quoted_verbatim(parsed: list[ParsedLine], card: dict) -> None:
    """The presenter says the ID is re-derivable: a stale hash makes that claim false."""
    known = {case.get("provenance_id") for case in card.get("cases", []) if case.get("provenance_id")}
    failures = [
        f"docs/video-script.md:{line.line_no}: provenance_id {value!r} is not one of the card's "
        f"{sorted(known)}.\n    line: {line.line.strip()!r}"
        for line in parsed
        for value in line.provenance_ids
        if value not in known
    ]
    assert not failures, "stale provenance_id in the script:\n\n" + "\n\n".join(failures)


# ---------------------------------------------------------------------------------------------
# Honesty beats — submission requirements, not style
# ---------------------------------------------------------------------------------------------


def _lines() -> list[tuple[int, str]]:
    return list(enumerate(DOC.read_text(encoding="utf-8").splitlines(), start=1))


def _fold(text: str) -> str:
    """Case/accent-insensitive form so 'Málaga' matches 'malaga' in the artefacts."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _spoken(line: str) -> str:
    """The line with inline code spans removed — the words that reach the audience.

    Honesty beats must live in spoken prose. A ``detection_limit=1.50`` in a table cell that
    mirrors a card field is a screen artefact; the presenter saying nothing about the bound is
    exactly the failure this guard is for. Code spans are therefore dropped before the beats are
    looked for.
    """
    return re.sub(r"`[^`]*`", " ", line)


def _find_line(predicate, what: str) -> tuple[int, str]:
    hits = [(number, line) for number, line in _lines() if predicate(line)]
    assert hits, f"docs/video-script.md is missing {what}"
    return hits[0]


def test_mock_statement_tracks_the_artefact_product(card: dict) -> None:
    """'Runs on mock artefacts' is only true while every loaded case says product == 'mock'."""
    products = {case.get("product") for case in card.get("cases", [])}
    doc = _lines()

    if products == {"mock"}:
        _find_line(
            lambda text: re.search(r"\bmock\b", _spoken(text), re.IGNORECASE)
            and re.search(r"synthetic|stub|generate_mock", _spoken(text), re.IGNORECASE),
            "the mock statement ('the demo runs on mock artefacts … every number is synthetic') "
            "while all loaded cases are product == 'mock'",
        )
        return

    stale = [
        (number, line)
        for number, line in doc
        if re.search(
            r"every number is synthetic|this demo runs on mock|status:\s*mock", _spoken(line), re.I
        )
    ]
    assert not stale, (
        "a loaded case is no longer mock (products: "
        f"{sorted(filter(None, products))}) but docs/video-script.md still calls the whole demo "
        "mock:\n\n"
        + "\n\n".join(f"docs/video-script.md:{n}: {l.strip()!r}" for n, l in stale)
    )
    folded = _fold(DOC.read_text(encoding="utf-8"))
    for product in products - {None, "mock"}:
        assert _fold(str(product)) in folded, (
            f"docs/video-script.md never names the real product {product!r} that data/processed/ "
            f"now carries"
        )


def test_null_result_states_the_detection_bound(card: dict) -> None:
    """'Málaga is flat' is the forbidden phrasing: the bound is the result."""
    nulls = [entry for entry in card.get("null_result", []) if entry.get("detection_limit_mm_yr")]
    if not nulls:
        pytest.skip("the demo card publishes no null-result case, so there is no bound to state")

    failures: list[str] = []
    for entry in nulls:
        case_id = str(entry["case_id"])
        tokens = {_fold(case_id), _fold(case_id.replace("-", " "))}
        bound = f"{float(entry['detection_limit_mm_yr']):.1f}"
        hits = [
            (number, line)
            for number, line in _lines()
            if re.search(r"detection", _spoken(line), re.IGNORECASE)
            and bound in _spoken(line)
            and any(token in _fold(_spoken(line)) for token in tokens)
        ]
        if not hits:
            failures.append(
                f"{case_id}: no line states the detection bound {bound} mm/yr for this case "
                f"alongside the word 'detection' — a flat result without its bound is a claim the "
                f"artefacts do not support"
            )
    assert not failures, "null-result honesty beat missing:\n\n" + "\n\n".join(failures)


def test_coverage_caveat_names_the_cases_below_the_threshold(card: dict) -> None:
    """No city-wide claim where coverage is below the card's warning threshold."""
    low = [
        str(case["case_id"])
        for case in card.get("cases", [])
        if (case.get("coverage") or {}).get("below_warning_threshold")
    ]
    assert low, "no case is below the coverage threshold; the caveat has nothing to say"
    threshold = None
    for case in card.get("cases", []):
        value = (case.get("coverage") or {}).get("warning_threshold")
        if value is not None:
            threshold = float(value) * 100.0
            break
    assert threshold is not None, "the card publishes no coverage warning threshold"

    caveats = [
        (number, line)
        for number, line in _lines()
        if re.search(r"city-wide", _spoken(line), re.IGNORECASE)
        and re.search(r"below|under", _spoken(line), re.IGNORECASE)
        and re.search(r"claim|only above|no ", _spoken(line), re.IGNORECASE)
    ]
    assert caveats, (
        f"docs/video-script.md has no coverage caveat ('no city-wide claim where coverage is below "
        f"{threshold:g} %') although {low} are below it"
    )
    folded = [(number, _fold(_spoken(line))) for number, line in _lines()]
    for case_id in low:
        tokens = {_fold(case_id), _fold(case_id.replace("-", " "))}
        assert any(
            any(token in line for token in tokens)
            and re.search(r"coverage|percent|%|city-wide", line)
            for _, line in folded
        ), (
            f"the coverage caveat never names {case_id!r}, which is below the {threshold:g} % "
            f"threshold — the caveat must be specific to be honest"
        )


def test_seasonal_window_caveat_is_present(card: dict) -> None:
    """The 132-day window cannot separate seasonal from secular motion: the script must say so."""
    months = sorted(
        {
            f"{float(case['window_days']) / DAYS_PER_MONTH:.1f}"
            for case in card.get("cases", [])
            if case.get("window_days")
        }
    )
    gate = None
    for text in (card.get("honesty") or {}).get("do_not_claim") or []:
        match = re.search(r"below the (\d+)\s*d\b", text)
        if match:
            gate = match.group(1)
            break

    def mentions_window(line: str) -> bool:
        month_hit = any(
            re.search(rf"{re.escape(value)}\s*[- ]month", line, re.IGNORECASE) for value in months
        ) or re.search(r"4\.5\s*[- ]month", line, re.IGNORECASE)
        gate_hit = gate is not None and re.search(rf"\b{gate}\s*d\b", line)
        return bool(month_hit or gate_hit)

    hits = [
        (number, line)
        for number, line in _lines()
        if re.search(r"seasonal", _spoken(line), re.IGNORECASE) and mentions_window(_spoken(line))
    ]
    assert hits, (
        "docs/video-script.md is missing the seasonal-window caveat: a line about 'seasonal' must "
        f"quote the short window ({', '.join(months)} months) or the "
        f"{gate or '?'} d identifiability gate, because the card refuses to publish "
        "seasonal_amp_mm from this stack"
    )
