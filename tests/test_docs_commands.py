"""Keep the documented commands honest.

Documentation drift has bitten this project three times in one day: a missing dependency, a
command pointing at a file that did not exist, and a flag that had changed. Prose is not
testable, but the command lines in the docs are: they either name a path that exists, a flag the
module accepts, and a program that exits 0 -- or they are stale.

This module is deliberately **fail-closed**:

* a documented command whose shape we cannot classify fails instead of being skipped;
* a documented ``--flag`` is checked against that module's own ``--help`` output (one subprocess
  per module, cached for the session);
* the only commands *executed* are on a hard-coded, side-effect-free allowlist (``--help``,
  ``--self-test`` and a ``--dry-run`` whose availability fixture is built/copied inside
  ``tmp_path``). ``scripts/check_phase_a.sh`` is never run -- it runs the whole suite -- and any
  documented command without ``--dry-run`` is refused before ``subprocess.run`` is called.

It reports; it never edits a doc. A failure names the file, the line, the command and the exact
missing flag/path, so the fix is a one-line edit in the doc or the CLI -- not an archaeology dig.

Scope is intentionally narrow: only ``README.md``, ``docs/handoff-mock.md``,
``docs/data-source-contract*`` (when present), ``docs/first-granule-runbook.md`` (when present)
and ``pipeline/README.md`` are parsed. Nothing here opens a socket.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Docs that must keep their commands honest. Globs are expanded; a missing file is reported,
#: never treated as "nothing to check".
DOC_PATTERNS: tuple[str, ...] = (
    "README.md",
    "docs/handoff-mock.md",
    "docs/data-source-contract*",
    "docs/first-granule-runbook.md",
    "pipeline/README.md",
)

PREFIXES = ("./scripts/python.sh", "pipeline/")

#: A documented path is a candidate if it looks like a path. Bare words (``mexico-city``,
#: ``nisar_gunw``) are option values, not paths, and must not produce noise.
PATH_EXTENSIONS = (
    ".py",
    ".sh",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".h5",
    ".parquet",
    ".csv",
    ".geojson",
    ".txt",
    ".png",
    ".toml",
    ".ini",
)

#: Placeholders documented by hand: they name no real file and must not be resolved on disk.
PLACEHOLDER_RE = re.compile(r"<[^>]*>|\.\.\.|\{[^}]*\}|\*")
PLACEHOLDER_NAMES = {"a.h5", "b.h5"}

LONG_FLAG_RE = re.compile(r"(?<![\w-])(--[A-Za-z0-9][A-Za-z0-9_-]*)")
USAGE_SUBCOMMANDS_RE = re.compile(r"^usage:[^\n]*?\{([a-z0-9_,-]+)\}\s+\.\.\.", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
TRAILING_COMMENT_RE = re.compile(r"(?<!\S)#.*$")

HELP_TIMEOUT_S = 180
EXEC_TIMEOUT_S = 300


# --------------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentedCommand:
    r"""One documented command line, normalised across ``\`` continuations."""

    doc: str  # repo-relative path, as written in DOC_PATTERNS
    line: int  # first physical line of the command
    text: str  # normalised, comment-stripped command
    origin: str  # "fence" or "inline"
    skip_reason: str | None = None  # why extraction dropped it, when it did


@dataclass(frozen=True)
class Invocation:
    """How a documented command reaches its program."""

    kind: str  # script | module | inline-code | wrapper-only | unknown
    target: str | None
    target_index: int


def _iter_doc_files() -> tuple[list[Path], list[str]]:
    found: list[Path] = []
    missing: list[str] = []
    for pattern in DOC_PATTERNS:
        matches = sorted(REPO_ROOT.glob(pattern))
        if matches:
            found.extend(path for path in matches if path.is_file())
        else:
            missing.append(pattern)
    return found, missing


def _strip_comment(line: str) -> str:
    return TRAILING_COMMENT_RE.sub("", line).strip()


def _starts_command(text: str) -> bool:
    return text.startswith(PREFIXES)


def _iter_fenced_commands(text: str):
    r"""Yield ``(lineno, command)`` for fenced blocks, joining ``\`` continuations.

    A line is its own command unless the previous physical line ended with a backslash. Blank
    lines and ``#`` comments end a block and are ignored, exactly as bash would.
    """
    in_fence = False
    start = 0
    parts: list[str] = []

    def flush():
        nonlocal start, parts
        if parts:
            yield start, " ".join(parts)
        start = 0
        parts = []

    for lineno, raw in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(raw):
            yield from flush()
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        line = _strip_comment(raw)
        if not line:
            yield from flush()
            continue
        if parts:
            parts.append(line.rstrip("\\").strip())
        elif _starts_command(line):
            start = lineno
            parts = [line.rstrip("\\").strip()]
        else:
            continue
        if not raw.rstrip().endswith("\\"):
            yield from flush()
    yield from flush()


def _iter_inline_commands(text: str):
    for lineno, raw in enumerate(text.splitlines(), 1):
        for match in INLINE_CODE_RE.finditer(raw):
            candidate = _strip_comment(match.group(1))
            if _starts_command(candidate):
                yield lineno, candidate


def _collect() -> tuple[list[DocumentedCommand], list[DocumentedCommand]]:
    """Return ``(commands, skipped)``.

    ``skipped`` records *why* a candidate was dropped, so "we parsed nothing" can never look
    like "everything is fine".
    """
    files, _missing = _iter_doc_files()
    commands: list[DocumentedCommand] = []
    skipped: list[DocumentedCommand] = []
    seen: set[tuple[str, int, str]] = set()

    for path in files:
        doc = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        candidates = [
            *((line, cmd, "fence") for line, cmd in _iter_fenced_commands(text)),
            *((line, cmd, "inline") for line, cmd in _iter_inline_commands(text)),
        ]
        candidates.sort(key=lambda item: (item[0], 0 if item[2] == "fence" else 1))
        for line, raw, origin in candidates:
            key = (doc, line, raw)
            if key in seen:
                continue
            seen.add(key)
            reason = _skip_reason(raw, origin)
            entry = DocumentedCommand(doc=doc, line=line, text=raw, origin=origin)
            if reason is None:
                commands.append(entry)
            else:
                skipped.append(
                    DocumentedCommand(
                        doc=doc, line=line, text=raw, origin=origin, skip_reason=reason
                    )
                )
    return commands, skipped


def _skip_reason(command: str, origin: str) -> str | None:
    lowered = command.lower()
    if "http://" in lowered or "https://" in lowered:
        return "contains an http(s) URL; not executed or path-checked"
    if re.search(r"(?<![\w-])git(?![\w-])", lowered):
        return "git command, outside the documentation-drift scope"
    if command == "./scripts/python.sh" and origin == "inline":
        # Prose mention of the wrapper ("always through ./scripts/python.sh"), not a command.
        return "mentions the wrapper without naming a command"
    return None


COMMANDS, SKIPPED_COMMANDS = _collect()
DOC_FILES, MISSING_DOC_PATTERNS = _iter_doc_files()


def _command_id(command: DocumentedCommand) -> str:
    preview = command.text if len(command.text) <= 70 else command.text[:67] + "..."
    return f"{command.doc}: {preview}"


PARAMS = [pytest.param(command, id=_command_id(command)) for command in COMMANDS]


# --------------------------------------------------------------------------------------------
# Command shape
# --------------------------------------------------------------------------------------------


def _tokenize(command: str) -> list[str]:
    # ``shlex`` turns markdown's ``\|`` escape into ``|`` and keeps ``-c "..."`` as one token.
    return shlex.split(command, posix=True)


def _normalise_target(target: str) -> str:
    """Turn an inline code reference such as ``pipeline/ingest/nisar_gunw.py:DATASETS`` into its path.

    Docs name symbols as ``path:SYMBOL``; the symbol is not part of the filesystem path.
    """
    if ":" not in target:
        return target
    head = target.split(":", 1)[0]
    if head.startswith(PREFIXES) or (REPO_ROOT / head).exists():
        return head
    return target


def _target_of(command: str) -> Invocation:
    tokens = _tokenize(command)
    if not tokens:
        return Invocation("unknown", None, 0)
    first = tokens[0]
    if first == "./scripts/python.sh":
        rest = tokens[1:]
        if not rest:
            return Invocation("wrapper-only", None, 0)
        if rest[0] == "-m":
            if len(rest) < 2:
                return Invocation("unknown", None, 1)
            return Invocation("module", rest[1], 2)
        if rest[0] == "-c":
            return Invocation("inline-code", None, 1)
        if rest[0].startswith("-"):
            return Invocation("unknown", None, 1)
        return Invocation("script", _normalise_target(rest[0]), 1)
    if first.startswith(PREFIXES):
        return Invocation("script", _normalise_target(first), 0)
    return Invocation("unknown", first, 0)


def _is_placeholder(token: str) -> bool:
    if token in PLACEHOLDER_NAMES:
        return True
    if PLACEHOLDER_RE.search(token):
        return True
    # A token cut off by the markdown table escape, e.g. ``search\|report``.
    return "|" in token


def _looks_like_path(token: str) -> bool:
    if token.startswith(("-", "/", "~")) or _is_placeholder(token):
        # Absolute paths are outputs (``--out /tmp/...``), not repo files to verify.
        return False
    if "/" in token or "\\" in token:
        return True
    if token.lower().endswith(PATH_EXTENSIONS):
        return True
    # A bare name that exists (``tests``) is a path too; a bare option value is not.
    return (REPO_ROOT / token).exists()


def _module_path(module: str) -> Path | None:
    root = module.split(".", 1)[0]
    if root not in {"src", "pipeline", "tests", "app"}:
        return None
    return REPO_ROOT.joinpath(*module.split(".")).with_suffix(".py")


def _is_runnable(documented: DocumentedCommand) -> bool:
    """True when the docs present this as a command, not as a prose path reference.

    ``pipeline/science/transforms.py`` mentioned in a sentence is a path to read, not a CLI to
    execute; a fenced line or a wrapper invocation is an executable claim.
    """
    if documented.origin == "fence":
        return True
    tokens = _tokenize(documented.text)
    if tokens and tokens[0].startswith("./scripts/"):
        return True
    invocation = _target_of(documented.text)
    return invocation.kind in {"script", "module"} and len(tokens) > invocation.target_index + 1


HELP_TARGETS: dict[str, list[tuple[str, int]]] = {}
for _documented in COMMANDS:
    if not _is_runnable(_documented):
        continue
    _invocation = _target_of(_documented.text)
    if _invocation.kind == "script" and _invocation.target and _invocation.target.endswith(
        ".py"
    ):
        HELP_TARGETS.setdefault(_invocation.target, []).append(
            (_documented.doc, _documented.line)
        )


# --------------------------------------------------------------------------------------------
# --help cache: one subprocess per (program, subcommand) for the whole session
# --------------------------------------------------------------------------------------------


def _run(argv: list[str], *, timeout: int, env: dict[str, str] | None = None):
    return subprocess.run(  # noqa: S603 - argv is built here, never from network input
        argv,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        env=env,
    )


@lru_cache(maxsize=None)
def _help_output(argv: tuple[str, ...]) -> tuple[int, str]:
    try:
        completed = _run(list(argv), timeout=HELP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {HELP_TIMEOUT_S}s"
    return completed.returncode, completed.stdout or ""


def _help_argv(invocation: Invocation, subcommand: str | None = None) -> tuple[str, ...]:
    assert invocation.target is not None
    if invocation.kind == "module":
        argv = [sys.executable, "-m", invocation.target, "--help"]
    else:
        argv = [sys.executable, invocation.target, "--help"]
    if subcommand:
        # ``--help`` goes last: ``prog sub --help`` prints the subparser's flags.
        argv = argv[:-1] + [subcommand, "--help"]
    return tuple(argv)


def _subcommands(help_text: str) -> set[str]:
    match = USAGE_SUBCOMMANDS_RE.search(help_text)
    if not match:
        return set()
    return {name.strip() for name in match.group(1).split(",") if name.strip()}


def _subcommand_for(invocation: Invocation, tokens: list[str], base_help: str) -> str | None:
    known = _subcommands(base_help)
    if not known:
        return None
    for token in tokens[invocation.target_index + 1 :]:
        if token.startswith("-"):
            continue
        return token if token in known else None
    return None


def _long_flags(tokens: list[str]) -> list[str]:
    flags: list[str] = []
    for token in tokens:
        for match in LONG_FLAG_RE.finditer(token.split("=", 1)[0]):
            if match.group(1) not in flags:
                flags.append(match.group(1))
    return flags


# --------------------------------------------------------------------------------------------
# Static checks
# --------------------------------------------------------------------------------------------


def _static_problems(command: DocumentedCommand) -> list[str]:
    try:
        tokens = _tokenize(command.text)
    except ValueError as exc:
        return [f"cannot tokenise the command ({exc}); refusing to pass it silently"]

    invocation = _target_of(command.text)
    problems: list[str] = []

    if invocation.kind in {"unknown", "wrapper-only"}:
        return [
            "cannot determine the script/module this command runs "
            f"(shape: {invocation.kind}); refusing to pass it silently"
        ]

    if invocation.kind == "inline-code":
        # ``-c "..."`` has no module path and no argparse flags; nothing static to verify.
        return problems

    target = invocation.target
    assert target is not None

    if invocation.kind == "module":
        module_file = _module_path(target)
        if module_file is not None and not module_file.exists():
            problems.append(
                f"module `{target}` (documented as `-m {target}`) resolves to "
                f"`{module_file.relative_to(REPO_ROOT)}`, which does not exist"
            )

    if invocation.kind == "script":
        target_path = REPO_ROOT / target
        if not target_path.exists():
            problems.append(f"path `{target}` does not exist")

    # ``./scripts/*.sh`` anywhere in the command (not just the target) must exist and be runnable.
    for token in tokens:
        if not re.fullmatch(r"\./scripts/[^/]+\.sh", token):
            continue
        script_path = REPO_ROOT / token
        if not script_path.exists():
            problems.append(f"script `{token}` does not exist")
        elif not os.access(script_path, os.X_OK):
            problems.append(f"script `{token}` exists but is not executable")

    # Every non-placeholder path argument must exist.
    for token in tokens[invocation.target_index + 1 :]:
        if token.startswith("--") and "=" in token:
            candidates = [token.split("=", 1)[1]]
        else:
            candidates = [token]
        for candidate in candidates:
            if not _looks_like_path(candidate):
                continue
            if not (REPO_ROOT / candidate).exists():
                problems.append(f"path `{candidate}` does not exist")

    # Flags: the module's own --help is the source of truth. A documented command with no long
    # flags never needs a subprocess, which keeps pytest/pip out of the execution path.
    wanted = _long_flags(tokens)
    if not wanted:
        return problems
    if invocation.kind == "script" and not target.endswith(".py"):
        # A shell script's flags are not argparse flags; only the executable bit is testable.
        return problems

    base_code, base_text = _help_output(_help_argv(invocation))
    if base_code != 0:
        problems.append(
            f"`{' '.join(_help_argv(invocation)[1:])}` exited {base_code} "
            f"(cannot verify flags): {base_text.strip()[-400:]}"
        )
        return problems

    subcommand = _subcommand_for(invocation, tokens, base_text)
    accepted = set(LONG_FLAG_RE.findall(base_text))
    if subcommand:
        code, text = _help_output(_help_argv(invocation, subcommand))
        if code != 0:
            problems.append(
                f"`{' '.join(_help_argv(invocation, subcommand)[1:])}` exited {code} "
                f"(cannot verify flags): {text.strip()[-400:]}"
            )
            return problems
        accepted |= set(LONG_FLAG_RE.findall(text))

    for flag in wanted:
        if flag not in accepted:
            program = " ".join(_help_argv(invocation)[1:-1])
            problems.append(
                f"flag `{flag}` is not accepted by `{program}`; its --help lists: "
                + ", ".join(sorted(accepted))
            )
    return problems


def _format_failure(command: DocumentedCommand, problems: list[str]) -> str:
    lines = [
        f"{command.doc}:{command.line} documents a command that no longer works.",
        f"  command: {command.text}",
    ]
    lines.extend(f"  - {problem}" for problem in problems)
    lines.append("  fix the doc or the CLI; this test only reports the drift.")
    return "\n".join(lines)


@pytest.mark.parametrize("command", PARAMS)
def test_documented_command_static_checks(command: DocumentedCommand) -> None:
    """Every documented program/path exists and every documented flag is in its --help."""
    problems = _static_problems(command)
    if problems:
        pytest.fail(_format_failure(command, problems), pytrace=False)


def test_missing_docs_are_skipped_with_a_reason() -> None:
    """A doc that does not exist yet is a visible skip, never silent coverage."""
    if not MISSING_DOC_PATTERNS:
        pytest.skip("every documented file exists")
    pytest.skip(
        "documented file(s) not present yet, nothing to parse: "
        + ", ".join(MISSING_DOC_PATTERNS)
    )


def test_every_existing_doc_yields_at_least_one_command() -> None:
    """Fail closed if the parser silently stops finding commands in a doc that has them."""
    assert COMMANDS, "parser found no documented commands at all"
    counts: dict[str, int] = {}
    for path in DOC_FILES:
        doc = path.relative_to(REPO_ROOT).as_posix()
        counts[doc] = sum(1 for command in COMMANDS if command.doc == doc)
    empty = [doc for doc, count in counts.items() if count == 0]
    assert not empty, (
        f"parser found no commands in {empty}; either the doc lost its commands or the "
        "extractor is broken -- refusing to pass silently"
    )
    print(f"parsed {len(COMMANDS)} documented command(s) from {len(counts)} doc(s)")
    for doc, count in counts.items():
        print(f"  {doc}: {count}")
    for skipped in SKIPPED_COMMANDS:
        print(f"  skipped {skipped.doc}:{skipped.line}: {skipped.skip_reason}")


# --------------------------------------------------------------------------------------------
# Safe execution allowlist
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", sorted(HELP_TARGETS), ids=sorted(HELP_TARGETS))
def test_documented_module_help_exits_zero(target: str) -> None:
    """A documented module must at least be importable and able to print its own help."""
    argv = [sys.executable, target, "--help"]
    completed = _run(argv, timeout=HELP_TIMEOUT_S)
    if completed.returncode != 0:
        where = ", ".join(f"{doc}:{line}" for doc, line in HELP_TARGETS[target])
        pytest.fail(
            f"`{target} --help` exited {completed.returncode} (documented at {where}).\n"
            f"  output tail: {(completed.stdout or '').strip()[-800:]}",
            pytrace=False,
        )


def test_validate_artifacts_self_test_exits_zero() -> None:
    """The self-test is documented and must stay green; it writes only to a temp dir."""
    module = REPO_ROOT / "pipeline" / "validate_artifacts.py"
    assert module.exists(), "pipeline/validate_artifacts.py is documented but does not exist"
    completed = _run(
        [sys.executable, "pipeline/validate_artifacts.py", "--self-test"],
        timeout=EXEC_TIMEOUT_S,
    )
    if completed.returncode != 0:
        pytest.fail(
            f"`pipeline/validate_artifacts.py --self-test` exited {completed.returncode}.\n"
            f"  output tail: {(completed.stdout or '').strip()[-1200:]}",
            pytrace=False,
        )


def test_check_phase_a_is_executable_but_never_run() -> None:
    """Deliberately not executed: it runs the whole suite. Existence and +x are the contract."""
    script = REPO_ROOT / "scripts" / "check_phase_a.sh"
    assert script.exists(), "scripts/check_phase_a.sh is documented but does not exist"
    assert os.access(script, os.X_OK), "scripts/check_phase_a.sh exists but is not executable"


# --- --dry-run: the only documented command we execute beyond --help/--self-test -------------


def _documented_dry_runs() -> list[DocumentedCommand]:
    return [
        command
        for command in COMMANDS
        if "pipeline/ingest/02_search_download.py" in command.text
        and re.search(r"(?<![\w-])download(?![\w-])", command.text)
        and "--dry-run" in command.text
    ]


DRY_RUNS = [
    pytest.param(command, id=f"{command.doc}:{command.line}")
    for command in _documented_dry_runs()
]


def _dry_run_argv(command: DocumentedCommand, *, availability: Path, raw: Path) -> list[str]:
    """Rebuild the documented dry-run, adding only the two sandbox overrides.

    ``--availability``/``--raw`` are not in the docs; they keep every read and every potential
    write inside ``tmp_path``. The documented flags are passed through untouched, so a flag
    drift still fails here.
    """
    tokens = _tokenize(command.text)
    assert tokens[0] == "./scripts/python.sh", "dry-run helper only knows the documented wrapper"
    argv = [sys.executable, *tokens[1:]]
    if "--dry-run" not in argv:
        pytest.fail("refusing to execute a documented command without --dry-run", pytrace=False)
    return [*argv, "--availability", str(availability), "--raw", str(raw)]


def _dry_run_env() -> dict[str, str]:
    """No credentials: the dry-run must never be able to turn into a download."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("EARTHDATA_", "ASF_"))
    }


def _synthetic_availability(city: str) -> dict:
    """A current-schema report holding one single-geometry stack (the shape ``search`` writes)."""
    stem = "NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH"
    tail = "P05023_N_F_J_001"
    ids = [
        f"{stem}_20260901T011329_20260901T011403_20260913T011329_20260913T011403_{tail}",
        f"{stem}_20260913T011329_20260913T011403_20260925T011329_20260925T011403_{tail}",
    ]
    return {
        "contract_version": "1.0",
        "city": city,
        "sources": {
            "nisar_gunw": {
                "available": True,
                "granules": [{"id": granule_id} for granule_id in ids],
            }
        },
    }


def _city_from(command: DocumentedCommand) -> str | None:
    tokens = _tokenize(command.text)
    for index, token in enumerate(tokens):
        if token == "--city" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--city="):
            return token.split("=", 1)[1]
    return None


@pytest.mark.parametrize("command", DRY_RUNS)
def test_documented_dry_run_exits_zero(command: DocumentedCommand, tmp_path: Path) -> None:
    """The documented dry-run must exit 0 against a report whose schema the code accepts."""
    city = _city_from(command)
    assert city, f"{command.doc}:{command.line}: dry-run command names no --city"
    availability = tmp_path / "availability"
    availability.mkdir()
    (availability / f"{city}.json").write_text(
        json.dumps(_synthetic_availability(city)), encoding="utf-8"
    )
    raw = tmp_path / "raw"

    argv = _dry_run_argv(command, availability=availability, raw=raw)
    completed = _run(argv, timeout=EXEC_TIMEOUT_S, env=_dry_run_env())
    if completed.returncode != 0:
        pytest.fail(
            f"{command.doc}:{command.line} documents a dry-run that exited "
            f"{completed.returncode}.\n"
            f"  command: {command.text}\n"
            f"  output tail: {(completed.stdout or '').strip()[-1200:]}",
            pytrace=False,
        )


@pytest.mark.parametrize("command", DRY_RUNS)
def test_documented_dry_run_works_on_committed_availability(
    command: DocumentedCommand, tmp_path: Path
) -> None:
    """The same command against the repo's committed ``_availability`` evidence (copied, read-only).

    The docs present these reports as the output of a live ``search`` whose result the dry-run
    consumes. If the committed report predates a schema/guard change, the documented command
    cannot succeed for anyone checking out the repo -- that is drift, and it is reported here
    with the offending doc line instead of being hidden by a synthetic fixture.
    """
    city = _city_from(command)
    assert city, f"{command.doc}:{command.line}: dry-run command names no --city"
    source = REPO_ROOT / "data" / "processed" / "_availability" / f"{city}.json"
    if not source.exists():
        pytest.skip(f"no committed availability report for {city!r} to replay")

    availability = tmp_path / "availability"
    availability.mkdir()
    (availability / f"{city}.json").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    raw = tmp_path / "raw"

    completed = _run(
        _dry_run_argv(command, availability=availability, raw=raw),
        timeout=EXEC_TIMEOUT_S,
        env=_dry_run_env(),
    )
    if completed.returncode != 0:
        pytest.fail(
            f"{command.doc}:{command.line} documents a dry-run that exits "
            f"{completed.returncode} against the committed availability report "
            f"`{source.relative_to(REPO_ROOT)}`.\n"
            f"  command: {command.text}\n"
            "  the report is stale relative to the documented command; refresh it with "
            "`search` or document the flag the new guard requires.\n"
            f"  output tail: {(completed.stdout or '').strip()[-1200:]}",
            pytrace=False,
        )
