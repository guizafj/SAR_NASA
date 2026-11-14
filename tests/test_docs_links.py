"""Link and path integrity for every Markdown file the team reads.

Documentation drifts faster than code: files are renamed (``pipeline/01_generate_mock.py`` became
``pipeline/generate_mock.py``), planned directories are described as if they already exist
(``serve/``), and source documents are deleted while the pages that cite them keep the old link.
The README is already checked in other suites; this module widens the net to every Markdown file a
judge or a teammate is likely to open.

Three things are checked per document:

1. **Relative links** ``[text](target)`` -- resolved against the *linking file's own directory*,
   never against the repository root (that is the classic false-positive/false-negative bug). An
   anchor is checked only when it points into a local Markdown file: the heading must exist under
   GitHub's slug rules.
2. **Backticked repository paths** (inline code only, e.g. ``docs/x.md``) -- the path must exist.
   Tokens that are not real repository paths are skipped, and every such rule/entry carries its
   reason in this file, next to the code.
3. **Same-file anchors** ``](#section)`` -- the heading must exist in that file.

A failure names the linking file, the line, the target and the exact path the link was resolved to.
This module *reports*; it never edits a document. A missing *scope* document is skipped (a doc can
legitimately be absent), but a link pointing at it from an existing document still fails.

Everything here is filesystem-only: no network, no subprocess, no writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Documents in scope. ``docs/*.md`` is expanded at collection time; the rest are named because
#: they are the entry points people actually read. ``flujo...`` is optional (the task calls it
#: "if present"); a missing file is skipped, never failed.
DOC_PATTERNS: tuple[str, ...] = (
    "README.md",
    "docs/*.md",
    "pipeline/README.md",
    "pipeline/ingest/README.md",
    "flujo_trabajo_y_responsabilidades.md",
)

#: Top-level names that make ``a/b`` a repository path rather than an HDF5 path, a branch name or
#: prose. Deliberately hard-coded: if ``serve/`` disappears tomorrow its references must still be
#: checked (a dynamic listing would silently stop treating them as paths).
#: ``.venv`` is excluded on purpose: it is a local environment created by setup, not repository
#: content, so ``.venv/bin/python`` in README is not a documentation link.
REPO_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "app",
        "config",
        "data",
        "deploy",
        "docs",
        "examples",
        "pipeline",
        "scripts",
        "serve",
        "src",
        "tests",
        ".github",
        ".streamlit",
    }
)

#: Placeholder syntax used throughout the docs. A path containing one cannot be resolved: it
#: describes a family of files (``data/processed/{case}/meta.json``), an example name
#: (``NISAR_L2_...``) or an HDF5 internal path (``/science/LSAR/...``).
PLACEHOLDER_MARKERS: tuple[str, ...] = ("<", ">", "{", "}", "…", "...")

#: Globs intentionally describe sets, not one file (``data/processed/_availability/*.json``).
GLOB_MARKERS: tuple[str, ...] = ("*", "?")

#: Paths under this prefix are gitignored downloads (see .gitignore and flujo §3.2), so their
#: absence says nothing about the docs.
ALLOWLIST_PREFIXES: dict[str, str] = {
    "data/raw/": "gitignored download tree (flujo §3.2); contents are never committed",
    "data/staging/": "gitignored rehearsal staging tree (README §15); never committed",
}

#: Singular paths that look checkable but are legitimately absent. Each reason is one line.
ALLOWLIST: dict[str, str] = {
    # README §7 "Produces" and §8 "(planned)" mark these as Rol 2 deliverables, not present files.
    ".streamlit/config.toml": "planned Rol 2 Streamlit config (README §7); app not built yet",
    "src/ui/map_view.py": "planned Rol 2 module (flujo §3.1 'Estructura propuesta'); not built yet",
    "data/raw": "gitignored download directory (submission-checklist §2); absent on clean clone",
    # Post-MVP Option B target paths (route-contract & submission-checklist explicitly mark post-MVP).
    "app/src/": "route-contract §0 post-MVP Option B static frontend directory; not built in MVP",
    "serve/": "submission-checklist §1 post-MVP Option B static server directory; not built in MVP",
    "serve/build.py": "route-contract §7 'Target layout only'; marked (target), post-MVP",
    "serve/dev.py": "route-contract §7 'Target layout only'; marked (target), post-MVP",
    "serve/check_contract.py": "route-contract §7 'Target layout only'; marked (target), post-MVP",
}

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+[\"'][^)]*[\"'])?\s*\)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
#: ``path.py:SYMBOL`` is a path plus a symbol under it; only ``path.py`` is checkable.
PATH_SYMBOL_RE = re.compile(r"^(.+?):[A-Za-z_][A-Za-z0-9_.]*$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


@dataclass(frozen=True)
class LinkFinding:
    doc: Path
    line: int
    target: str
    resolution: str
    problem: str


@dataclass(frozen=True)
class PathRef:
    doc: Path
    line: int
    token: str
    normalized: str
    skip_reason: str | None
    resolution: str


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _where(directory: Path) -> str:
    """Human-readable base for a resolution, e.g. ``docs/`` or ``repository root``."""
    return "repository root" if directory == ROOT else f"{_rel(directory)}/"


def _documents() -> list[Path]:
    docs: list[Path] = []
    for pattern in DOC_PATTERNS:
        if "*" in pattern:
            docs.extend(sorted(ROOT.glob(pattern)))
        else:
            docs.append(ROOT / pattern)
    # Stable order, no duplicates (docs/*.md never overlaps the explicit names, but be safe).
    return sorted(dict.fromkeys(docs))


def _lines_outside_fences(text: str):
    """Yield ``(lineno, line)`` for editable Markdown, skipping fenced code blocks.

    Links and inline code inside fences are examples, not live references.
    """
    fence: str | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        match = FENCE_RE.match(line.lstrip())
        if match:
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None:
            yield lineno, line


def _slugify(heading: str) -> str:
    """GitHub heading slug: lower-case, punctuation stripped, spaces to hyphens.

    ``\\w`` is Unicode-aware, so Spanish accents survive exactly as GitHub keeps them
    (``Málaga`` -> ``málaga``). No accent-stripping fallback is needed; if GitHub ever changes
    that, only this function moves.
    """
    text = heading.strip()
    text = re.sub(r"\s+#+\s*$", "", text)  # optional closing hashes
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text.strip())
    return text


def _anchors(text: str) -> set[str]:
    """Slugs for every heading in ``text``, including GitHub's ``-1``/``-2`` duplicates."""
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for _lineno, line in _lines_outside_fences(text):
        match = HEADING_RE.match(line)
        if not match:
            continue
        slug = _slugify(match.group(2))
        if slug in seen:
            seen[slug] += 1
            anchors.add(f"{slug}-{seen[slug]}")
        else:
            seen[slug] = 0
            anchors.add(slug)
    return anchors


def _iter_links(doc: Path):
    """Yield ``(lineno, raw_target)`` for every Markdown link/image, fences excluded."""
    for lineno, line in _lines_outside_fences(doc.read_text(encoding="utf-8")):
        for match in LINK_RE.finditer(line):
            target = match.group(1).strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            yield lineno, target


def _check_anchor(doc: Path, resolved: Path, fragment: str, lineno: int, target: str) -> LinkFinding | None:
    fragment = unquote(fragment)
    if not fragment:
        return None
    if resolved.is_dir():
        # A directory anchor is not a Markdown heading reference; nothing cheap to verify.
        return None
    if resolved.suffix.lower() != ".md":
        # Anchors into non-Markdown files are opaque to us (e.g. a line in a script).
        return None
    available = _anchors(resolved.read_text(encoding="utf-8"))
    if fragment in available:
        return None
    return LinkFinding(
        doc=doc,
        line=lineno,
        target=target,
        resolution=f"{_rel(resolved)} (heading anchors: {', '.join(sorted(available))[:300]})",
        problem=f"fragment '#{fragment}' has no matching heading in {_rel(resolved)}",
    )


def check_links(doc: Path) -> list[LinkFinding]:
    """Every broken *relative* link in ``doc`` (same-file anchors have their own check)."""
    findings: list[LinkFinding] = []
    for lineno, target in _iter_links(doc):
        if not target or target.startswith("#"):
            continue
        if URI_SCHEME_RE.match(target):
            # http(s):// and friends are external; the test is hermetic and never fetches them.
            continue

        path_part, _, fragment = target.partition("#")
        if not path_part:
            continue
        path_part = unquote(path_part)
        # Relative to the linking file's directory -- not the repo root.
        resolved = (doc.parent / path_part).resolve()
        if not resolved.exists():
            findings.append(
                LinkFinding(
                    doc=doc,
                    line=lineno,
                    target=target,
                    resolution=f"{_rel(resolved)} (relative to {_where(doc.parent)})",
                    problem="target file does not exist",
                )
            )
            continue
        if fragment:
            finding = _check_anchor(doc, resolved, fragment, lineno, target)
            if finding:
                findings.append(finding)
    return findings


def check_same_file_anchors(doc: Path) -> list[LinkFinding]:
    """Broken ``](#section)`` anchors, checked against ``doc``'s own headings."""
    findings: list[LinkFinding] = []
    for lineno, target in _iter_links(doc):
        if not target.startswith("#") or len(target) == 1:
            continue
        finding = _check_anchor(doc, doc, target[1:], lineno, target)
        if finding:
            findings.append(finding)
    return findings


def _normalize_path_token(token: str) -> str | None:
    """Return the repository-relative path in ``token``, or ``None`` if it is not one."""
    text = token.strip()
    if not text or any(char.isspace() for char in text):
        return None  # a command line, a table cell of prose, ...
    if text.startswith(("~", "/")):
        return None  # home directory or absolute OS path, outside the repository
    if "://" in text:
        return None
    if text.startswith("./"):
        text = text[2:]
    symbol = PATH_SYMBOL_RE.match(text)
    if symbol:
        text = symbol.group(1)  # drop ``:SYMBOL``
    if "/" not in text:
        return None  # bare filename or symbol, not a repository path
    first = text.split("/", 1)[0]
    if first not in REPO_TOP_LEVEL:
        return None  # e.g. ``origin/HEAD``, ``release/mvp``, ``asf.alaska.edu/...``
    return text


def _skip_reason(token: str) -> str | None:
    if any(marker in token for marker in PLACEHOLDER_MARKERS):
        return "placeholder/example path (contains <...>, {...}, … or ...)"
    if any(marker in token for marker in GLOB_MARKERS):
        return "glob, describes a set of files rather than one path"
    for prefix, reason in ALLOWLIST_PREFIXES.items():
        if token.startswith(prefix):
            return reason
    return ALLOWLIST.get(token)


def iter_backticked_paths(doc: Path):
    """Yield a :class:`PathRef` for every inline-code token that looks like a repo path."""
    for lineno, line in _lines_outside_fences(doc.read_text(encoding="utf-8")):
        for raw in INLINE_CODE_RE.findall(line):
            normalized = _normalize_path_token(raw)
            if normalized is None:
                continue
            resolved = (ROOT / normalized).resolve()
            yield PathRef(
                doc=doc,
                line=lineno,
                token=raw.strip(),
                normalized=normalized,
                skip_reason=_skip_reason(normalized),
                resolution=f"{_rel(resolved)} (repository root)",
            )


def check_backticked_paths(doc: Path) -> list[PathRef]:
    """Backticked repo paths in ``doc`` that neither exist nor carry an allowlist reason."""
    return [
        ref
        for ref in iter_backticked_paths(doc)
        if ref.skip_reason is None and not (ROOT / ref.normalized).exists()
    ]


def _format_link_finding(finding: LinkFinding) -> str:
    return (
        f"{_rel(finding.doc)}:{finding.line}: {finding.target!r} -> {finding.resolution}\n"
        f"    {finding.problem}"
    )


def _format_path_finding(ref: PathRef) -> str:
    return (
        f"{_rel(ref.doc)}:{ref.line}: `{ref.token}` -> {ref.resolution}\n"
        f"    path does not exist (and is not allowlisted)"
    )


DOCUMENTS = _documents()


@pytest.mark.parametrize("doc", DOCUMENTS, ids=[_rel(doc) for doc in DOCUMENTS])
def test_relative_links_resolve(doc: Path) -> None:
    """Relative links resolve against the linking file's directory; local anchors exist."""
    if not doc.exists():
        pytest.skip(f"{_rel(doc)} is not present; no links to check")
    problems = check_links(doc)
    assert not problems, (
        f"{len(problems)} broken relative link(s) in {_rel(doc)} "
        "(resolved against the linking file's directory):\n"
        + "\n".join(_format_link_finding(problem) for problem in problems)
    )


@pytest.mark.parametrize("doc", DOCUMENTS, ids=[_rel(doc) for doc in DOCUMENTS])
def test_backticked_repo_paths_exist(doc: Path) -> None:
    """Every backticked repository path exists, unless an allowlist above says why it cannot."""
    if not doc.exists():
        pytest.skip(f"{_rel(doc)} is not present; no paths to check")
    checked = [ref for ref in iter_backticked_paths(doc) if ref.skip_reason is None]
    problems = check_backticked_paths(doc)
    assert not problems, (
        f"{len(problems)} broken backticked path(s) in {_rel(doc)} "
        f"({len(checked)} checked, allowlist reasons live in tests/test_docs_links.py):\n"
        + "\n".join(_format_path_finding(problem) for problem in problems)
    )


@pytest.mark.parametrize("doc", DOCUMENTS, ids=[_rel(doc) for doc in DOCUMENTS])
def test_same_file_anchors_resolve(doc: Path) -> None:
    """``](#section)`` must name a heading that exists in the same document."""
    if not doc.exists():
        pytest.skip(f"{_rel(doc)} is not present; no anchors to check")
    findings = check_same_file_anchors(doc)
    assert not findings, (
        f"{len(findings)} broken same-file anchor(s) in {_rel(doc)}:\n"
        + "\n".join(_format_link_finding(finding) for finding in findings)
    )
