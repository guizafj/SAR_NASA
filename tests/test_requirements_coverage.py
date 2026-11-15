"""Guard: every third-party import in the test suite and in the deployed app is a declared dependency.

CI installs ``requirements.txt`` and then runs ``pytest tests``; the container installs
``requirements-app.txt`` and then runs ``app.py``. So an import that no installed distribution
provides is not a broken test — it is a **missing dependency that only fails after the push**, and
the traceback points at the test (``ModuleNotFoundError: yaml``) rather than at the requirement
file. Checking that by hand survives one commit; this file survives the next one.

What it does
------------
1. Parses every ``tests/**/*.py`` with :mod:`ast` (``ast.walk`` over ``ast.Import`` /
   ``ast.ImportFrom``, so **imports nested inside functions and ``try`` blocks are included**)
   and keeps the top-level module names.
2. Classifies each name: stdlib (``sys.stdlib_module_names``), first-party (``src``, ``pipeline``,
   ``examples``, ``tests``, ``conftest``, plus the modules listed in ``FIRST_PARTY_EXTRA``), or
   third-party.
3. Asserts every third-party name is provided by a distribution declared in ``requirements.txt``.
   The same audit runs for the deployed runtime over ``app.py`` + ``src/**`` against
   ``requirements-app.txt`` — the container's install set, deliberately a subset of the workspace.

Also here: parsing the requirements files, and the ``MODULE_TO_DIST`` map itself.

Limits (read these before "fixing" a failure by loosening the test)
-------------------------------------------------------------------
* **A lazy import still counts.** A module imported inside a function body is audited exactly like a
  top-level one, because the whole point of the guard is that it would fail at *run* time. Nesting
  the import does not remove the dependency.
* **Name collisions need the map, and the map is the pressure valve.** A distribution does not
  always ship a module of the same name (``pyyaml`` ships ``yaml``, ``pillow`` ships ``PIL``). That
  is what ``MODULE_TO_DIST`` is for: extend it when you meet a new alias. It is a pressure valve,
  not a loophole — it maps one module to one distribution, it never means "no requirement needed".
* **Transitive availability is not guessed.** ``pyarrow`` is declared explicitly even though
  ``pandas`` can pull it in; ``pillow`` is *not* declared even though ``matplotlib`` requires it.
  This test will not run a resolver, so the one module the tree gets only transitively is listed in
  ``TRANSITIVE_ONLY`` with the declared distribution that carries it, and a companion test keeps
  that list honest (provider must still be declared and still be mapped). Treat an entry as a
  documented decision to rely on another package's dependency pin, not as a free pass: if such a
  module ever becomes load-bearing on its own, declare it and drop the entry.
* **Only direct, static imports are seen.** ``importlib.import_module("x")`` /
  ``pytest.importorskip("x")`` take strings, so the AST cannot see them (today they name only
  already-declared distributions: ``h5py``, ``h3``, ``pandas``, ``pyproj``). Equally out of scope:
  ``pipeline/**`` and ``scripts/**``, which are workspace-only (installed by ``requirements.txt``,
  absent from the container) and are not the subject of this guard.
* **Direct imports, per file.** The audit is the union of the imports each file names itself — the
  first order of the runtime closure, which is what a container install has to satisfy.
"""

from __future__ import annotations

import ast
import functools
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
REQUIREMENTS_WORKSPACE = ROOT / "requirements.txt"
REQUIREMENTS_APP = ROOT / "requirements-app.txt"
APP_SOURCES = (ROOT / "app.py", *sorted((ROOT / "src").rglob("*.py")))

#: Packages of this repository, importable by their top-level name from the repo root.
FIRST_PARTY_ROOTS = frozenset({"src", "pipeline", "examples", "tests", "conftest"})

#: First-party modules that are *not* packages of the repo, only plain files put on ``sys.path`` by
#: the importing test. Kept explicit (and verified to exist) so a deleted file cannot hide here.
FIRST_PARTY_EXTRA = {
    # `tests/test_reference_loader.py` / `tests/test_handoff_snippets.py` insert `examples/` into
    # sys.path and then `import reference_loader` — that is docs/handoff-mock.md's copyable module.
    "reference_loader": "examples/reference_loader.py",
}

#: Module name -> distribution name, for the pairs that differ. Everything else is assumed to ship
#: an identically named module (``numpy`` -> ``numpy``), which is why this map stays small.
#:
#: Only entries the current tree actually needs are listed, so that a dead entry cannot pretend to
#: be documentation. Well-known aliases to add the day such an import appears:
#: ``sklearn`` -> ``scikit-learn``, ``cv2`` -> ``opencv-python``, ``dotenv`` -> ``python-dotenv``,
#: ``dateutil`` -> ``python-dateutil``, ``bs4`` -> ``beautifulsoup4``, ``attr`` -> ``attrs``,
#: ``fiona`` -> ``fiona`` (a dist that needs no entry is the normal case, not an exception).
MODULE_TO_DIST = {
    "yaml": "pyyaml",   # config/study_cases.yaml, YAML dumps in the artefact writers
    "PIL": "pillow",    # image assertions in tests/test_make_figures.py (QC PNGs)
}

#: Modules that this tree only gets *transitively*: no direct declaration exists, and inventing one
#: here would mean guessing pip resolution. Each entry names the distribution that ships the module
#: and the **declared** distribution that pulls it in. See the module docstring, "Limits".
TRANSITIVE_ONLY = {
    # `matplotlib` declares `pillow>=9` as a hard requirement in its own metadata (verified against
    # the installed wheel: `importlib.metadata.requires("matplotlib")`), so `from PIL import Image`
    # in test_make_figures.py is satisfied by `matplotlib>=3.8`. Deliberate decision: rely on that
    # pin instead of adding a second, implicit requirement to requirements.txt.
    "PIL": {"dist": "pillow", "provider": "matplotlib"},
}

#: PEP 503 normalization: distribution names are case-insensitive and `-`/`_`/`.` are equivalent.
_NON_ALNUM_RE = re.compile(r"[-_.]+")
_REQ_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalise(name: str) -> str:
    return _NON_ALNUM_RE.sub("-", name).strip().lower()


def parse_requirements(path: pathlib.Path) -> frozenset[str]:
    """Declared distribution names of *path*, normalized; comments, blanks and pip options dropped."""
    declared: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()  # inline "pyarrow>=16.0  # parquet artefacts"
        if not line or line.startswith("-"):  # comments, -r/-e/--index-url/--extra-index-url
            continue
        match = _REQ_NAME_RE.match(line)
        if match is not None:
            declared.add(_normalise(match.group(0)))
    return frozenset(declared)


class UnparsableSource(Exception):
    """A scanned file is not valid Python, so its imports cannot be audited."""


def iter_imports(paths: tuple[pathlib.Path, ...]) -> dict[str, tuple[tuple[pathlib.Path, int], ...]]:
    """Top-level module name -> the import sites, for every ``Import``/``ImportFrom`` in *paths*.

    ``ast.walk`` is depth-first over the whole tree, so imports inside functions, ``if`` bodies and
    ``try``/``except ImportError`` blocks are all found. Relative imports (``from . import x``) are
    intra-package and cannot name a distribution, so they are skipped.
    """
    sites: dict[str, set[tuple[pathlib.Path, int]]] = {}
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            # Reported as a test failure (see `_imports`) instead of escaping at collection time:
            # a sibling test file mid-edit must not abort the whole session from inside this guard.
            raise UnparsableSource(
                f"{path.relative_to(ROOT).as_posix()} cannot be parsed, so its imports cannot be "
                f"audited: {exc.msg} (line {exc.lineno}). That file fails pytest collection on its "
                f"own as well; fix it rather than excluding it here."
            ) from None  # keep the failure to one message: the cause is already spelled out above
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    sites.setdefault(alias.name.split(".")[0], set()).add((path, node.lineno))
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                sites.setdefault(node.module.split(".")[0], set()).add((path, node.lineno))
    return {
        module: tuple(sorted(found, key=lambda site: (str(site[0]), site[1])))
        for module, found in sites.items()
    }


def _is_first_party(module: str) -> bool:
    return module in FIRST_PARTY_ROOTS or module in FIRST_PARTY_EXTRA


def third_party(
    imports: dict[str, tuple[tuple[pathlib.Path, int], ...]],
) -> dict[str, tuple[tuple[pathlib.Path, int], ...]]:
    """The imports that must be satisfied by an installed distribution."""
    return {
        module: sites
        for module, sites in imports.items()
        if module not in sys.stdlib_module_names and not _is_first_party(module)
    }


def uncovered(
    imports: dict[str, tuple[tuple[pathlib.Path, int], ...]],
    requirements: pathlib.Path,
) -> list[str]:
    """One message per third-party module that no declared distribution provides."""
    declared = parse_requirements(requirements)
    gaps: list[str] = []
    for module, sites in sorted(third_party(imports).items()):
        required = MODULE_TO_DIST.get(module, module)
        if _normalise(required) in declared:
            continue
        allowance = TRANSITIVE_ONLY.get(module)
        if (
            allowance is not None
            and _normalise(allowance["dist"]) == _normalise(required)
            and _normalise(allowance["provider"]) in declared
        ):
            continue
        first = sites[0][0].relative_to(ROOT).as_posix()
        message = (
            f"module {module!r} (imported in {first}) is not provided by any distribution in "
            f"{requirements.name} — add {required!r} or extend MODULE_TO_DIST"
        )
        others = sorted({site[0].relative_to(ROOT).as_posix() for site in sites[1:]})
        if others:
            message += f" (also imported in {', '.join(others)})"
        gaps.append(message)
    return gaps


#: The two audits. `requirements.txt` covers the test suite, `requirements-app.txt` covers the
#: deployed app + `src/**` and is deliberately a strict subset of the workspace. Scanning happens
#: lazily (once per session) so that an unparsable sibling file is a readable failure, not a
#: collection-time crash that hides which audit was running.
@functools.lru_cache(maxsize=1)
def workspace_imports() -> dict[str, tuple[tuple[pathlib.Path, int], ...]]:
    return iter_imports(tuple(sorted(TESTS_DIR.rglob("*.py"))))


@functools.lru_cache(maxsize=1)
def app_imports() -> dict[str, tuple[tuple[pathlib.Path, int], ...]]:
    return iter_imports(APP_SOURCES)


def _imports(scan) -> dict[str, tuple[tuple[pathlib.Path, int], ...]]:
    try:
        return scan()
    except UnparsableSource as exc:
        pytest.fail(str(exc), pytrace=False)


def test_workspace_test_imports_are_covered_by_requirements() -> None:
    """Every third-party import under ``tests/`` has a distribution in requirements.txt (CI install)."""
    gaps = uncovered(_imports(workspace_imports), REQUIREMENTS_WORKSPACE)
    assert not gaps, "undeclared dependency for the test suite:\n" + "\n".join(gaps)


def test_deployed_app_imports_are_covered_by_app_requirements() -> None:
    """Every third-party import in ``app.py`` + ``src/**`` has a distribution in requirements-app.txt."""
    gaps = uncovered(_imports(app_imports), REQUIREMENTS_APP)
    assert not gaps, "undeclared dependency for the container runtime:\n" + "\n".join(gaps)


def test_import_scan_is_not_vacuous() -> None:
    """A parser regression must fail loudly instead of reporting zero gaps on an empty scan."""
    suite = third_party(_imports(workspace_imports))
    app = third_party(_imports(app_imports))
    assert len(suite) >= 10, f"only {len(suite)} third-party imports found under tests/: {sorted(suite)}"
    assert {"pytest", "numpy", "pandas", "yaml"} <= set(suite), (
        f"the scan lost well-known third-party imports; found: {sorted(suite)}"
    )
    assert {"streamlit", "plotly", "pydeck"} <= set(app), (
        f"the app scan lost its UI dependencies; found: {sorted(app)}"
    )
    # This file is scanned like any other test, so it audits its own imports too.
    assert "test_requirements_coverage.py" in {
        site[0].name for sites in _imports(workspace_imports).values() for site in sites
    }


def test_requirements_parsing_sees_the_declared_distributions() -> None:
    """If parsing silently returned nothing, ``uncovered`` would pass everything — pin the parser."""
    workspace = parse_requirements(REQUIREMENTS_WORKSPACE)
    app = parse_requirements(REQUIREMENTS_APP)
    expected_workspace = {"pyyaml", "numpy", "pandas", "matplotlib", "pytest", "asf-search"}
    assert expected_workspace <= workspace, sorted(workspace)
    assert {"pyyaml", "numpy", "pandas", "pyarrow", "h3", "streamlit"} <= app, sorted(app)
    assert not any(name[:1].isdigit() or ">" in name for name in workspace | app), (
        "a version specifier leaked into a distribution name"
    )
    # The container's set really is a subset: that asymmetry is the reason both audits exist.
    assert app < workspace, "requirements-app.txt is expected to be a strict subset of requirements.txt"


def test_module_to_dist_map_and_transitive_allowlist_are_coherent() -> None:
    """The map may rename a requirement; it may never remove one, nor disagree with the allowlist."""
    declared = parse_requirements(REQUIREMENTS_WORKSPACE)
    for module, dist in MODULE_TO_DIST.items():
        assert _normalise(dist) != _normalise(module), f"{module!r} maps to itself; drop the entry"
    for module, allowance in TRANSITIVE_ONLY.items():
        assert module in MODULE_TO_DIST, f"{module!r} is allowlisted but not mapped in MODULE_TO_DIST"
        assert _normalise(MODULE_TO_DIST[module]) == _normalise(allowance["dist"]), (
            f"{module!r}: MODULE_TO_DIST and TRANSITIVE_ONLY disagree about the distribution"
        )
        assert _normalise(allowance["provider"]) in declared, (
            f"{module!r} relies on {allowance['provider']!r}, which requirements.txt does not declare"
        )


def test_first_party_extras_exist_on_disk() -> None:
    """An allowlisted first-party module that vanished must fail here, not silently pass the audit."""
    for module, relative in FIRST_PARTY_EXTRA.items():
        path = ROOT / relative
        assert path.is_file(), f"{module!r} is first-party only because {relative} exists; it does not"
