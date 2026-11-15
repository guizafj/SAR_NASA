"""Import order must not decide whether the package is importable.

`src/data/ui_bridge.py` imports `loaders` at the top, and `loaders` re-exported the bridge names with a
module-level `from src.data.ui_bridge import (...)`. That made the order decisive: importing
`src.data.ui_bridge` first raised

    ImportError: cannot import name 'get_case_series' from partially initialized module

while importing `loaders` first worked. Nothing caught it, because every test in the suite imports
`loaders` first and `test_science_card_extraction.py` wraps the bridge import in a `try/except` that
skips. `docs/handoff-pozo.md` tells a reader the hooks live in `ui_bridge`, which is exactly the failing
order. Each direction now runs in its own interpreter: import state cannot be reset inside one process.
"""
from __future__ import annotations

import subprocess
import sys

import pytest


def _run_first(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", f"import {module}; print('ok')"],
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.parametrize("first", ["src.data.ui_bridge", "src.data.loaders"])
def test_either_module_can_be_imported_first(first: str) -> None:
    result = _run_first(first)
    assert result.returncode == 0, (
        f"importing {first} first fails:\n{result.stderr[-1200:]}"
    )
    assert "ok" in result.stdout


def test_loaders_still_re_exports_the_bridge_hooks() -> None:
    """The lazy re-export must keep `app.py._loaders()` working."""
    from src.data import loaders

    for name in ("get_cell_detail", "load_study_cases", "get_map_layer_df"):
        assert callable(getattr(loaders, name)), name


def test_unknown_attribute_still_raises_attribute_error() -> None:
    """A lazy `__getattr__` must not swallow typos."""
    from src.data import loaders

    with pytest.raises(AttributeError, match="has no attribute"):
        _ = loaders.definitely_not_a_hook
