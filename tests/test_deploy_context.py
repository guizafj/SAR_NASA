"""Hermetic guard for container deployment (`deploy/Dockerfile`, `.dockerignore`, `requirements-app.txt`).

Even on machines without a `docker` daemon, this suite verifies that:
1. `.dockerignore` never strips files read by the Streamlit app at runtime (`docs/science-guide.md`,
   `examples/reference_loader.py`, `.streamlit/config.toml`, `README.md`, `config/study_cases.yaml`,
   `data/processed/*`), while still excluding secrets, raw granules, tests and heavy doc figures.
2. Every `COPY <src> <dst>` instruction in `deploy/Dockerfile` resolves to an existing, non-ignored
   repository path, and `load_documented_limits()` returns non-empty `science_section_1`,
   `section_8_caveat`, `section_13`, and `case_roles` from the materialised container tree.
3. Both `requirements.txt` and `requirements-app.txt` require `streamlit>=1.67` so `[theme.dark]` /
   `[theme.light]` in `.streamlit/config.toml` are honoured in production builds.
"""

from __future__ import annotations

import fnmatch
import pathlib
import shutil

from src.data.ui_bridge import load_documented_limits

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _parse_dockerignore(path: pathlib.Path) -> list[tuple[bool, str]]:
    rules: list[tuple[bool, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:].strip().strip("/") if negated else line.strip("/")
        if pattern:
            rules.append((negated, pattern))
    return rules


def _is_docker_ignored(rel_posix: str, rules: list[tuple[bool, str]]) -> bool:
    """Evaluate ordered `.dockerignore` rules (last matching rule wins, prefix-aware)."""
    parts = rel_posix.strip("/").split("/")
    ignored = False
    for negated, pat in rules:
        matched = False
        if "/" in pat:
            if fnmatch.fnmatchcase(rel_posix, pat) or rel_posix.startswith(pat.rstrip("*") + "/"):
                matched = True
        else:
            if fnmatch.fnmatchcase(rel_posix, pat) or any(
                fnmatch.fnmatchcase(seg, pat) for seg in parts
            ):
                matched = True
        if matched:
            ignored = not negated
    return ignored


def test_dockerignore_preserves_runtime_assets_and_blocks_secrets_and_bloat() -> None:
    rules = _parse_dockerignore(ROOT / ".dockerignore")

    must_keep = (
        "app.py",
        "README.md",
        "requirements-app.txt",
        ".streamlit/config.toml",
        "config/study_cases.yaml",
        "docs/science-guide.md",
        "examples/reference_loader.py",
        "src/data/ui_bridge.py",
        "src/ui/educational.py",
        "data/processed/mexico-city/nisar_grid.parquet",
    )
    for rel in must_keep:
        assert (ROOT / rel).exists(), f"missing runtime file {rel}"
        assert not _is_docker_ignored(rel, rules), f".dockerignore strips runtime asset {rel!r}"

    must_ignore = (
        ".git/HEAD",
        ".env",
        ".env.local",
        ".streamlit/secrets.toml",
        "data/raw/granule.h5",
        "data/staging/land_use.csv",
        "pipeline/process_case.py",
        "tests/test_contracts.py",
        "docs/figures/coherence.png",
    )
    for rel in must_ignore:
        assert _is_docker_ignored(rel, rules), f".dockerignore failed to exclude {rel!r}"


def test_dockerfile_copy_tree_supports_runtime_science_and_limits(tmp_path: pathlib.Path) -> None:
    rules = _parse_dockerignore(ROOT / ".dockerignore")
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")

    copy_pairs: list[tuple[str, str]] = []
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if line.startswith("COPY "):
            tokens = line.split()[1:]
            assert len(tokens) == 2, f"unexpected COPY syntax in Dockerfile: {line!r}"
            copy_pairs.append((tokens[0], tokens[1]))

    assert copy_pairs, "deploy/Dockerfile has no COPY instructions"
    app_root = tmp_path / "app"
    app_root.mkdir()

    for src_rel, dst_rel in copy_pairs:
        src_clean = src_rel.removeprefix("./")
        src_path = ROOT / src_clean
        assert src_path.exists(), f"Dockerfile COPY source {src_rel!r} does not exist"
        assert not _is_docker_ignored(src_clean, rules), (
            f"Dockerfile COPY source {src_rel!r} is excluded by .dockerignore"
        )
        dst_clean = dst_rel.removeprefix("./")
        dst_path = app_root / dst_clean if dst_clean else app_root
        if src_path.is_dir():
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)

    limits = load_documented_limits(
        readme_path=app_root / "README.md",
        science_path=app_root / "docs" / "science-guide.md",
        study_cases_path=app_root / "config" / "study_cases.yaml",
    )
    assert limits["science_section_1"], "Tab 4 science summary vanished inside container layout"
    assert limits["section_8_caveat"], "Tab 4 GNSS caveat vanished inside container layout"
    assert len(limits["section_13"]) == 13
    assert set(limits["case_roles"]) == {"mexico-city", "jakarta", "malaga"}


def test_streamlit_floor_supports_dual_light_dark_theme() -> None:
    for req_name in ("requirements.txt", "requirements-app.txt"):
        text = (ROOT / req_name).read_text(encoding="utf-8")
        assert "streamlit>=1.67" in text, (
            f"{req_name} must require streamlit>=1.67 for [theme.dark]/[theme.light] support"
        )
