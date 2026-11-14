"""Domain contracts, filters, metrics and PyDeck map adapters."""

from __future__ import annotations

from typing import Any

__all__ = [
    "contracts",
    "filters",
    "map_adapter",
    "metrics",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
