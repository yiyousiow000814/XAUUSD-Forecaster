"""Windows runtime path ownership helpers."""

from __future__ import annotations

import os
from pathlib import Path


def logical_absolute_path(path: str | Path) -> Path:
    """Normalize traversal without dereferencing the declared state authority."""
    return Path(os.path.abspath(os.fspath(path)))


def runtime_child_path(
    state_root: str | Path,
    path: str | Path | None,
    *,
    name: str,
) -> Path:
    """Return one fixed runtime-owned child and reject another authority."""
    authority = logical_absolute_path(state_root)
    expected = authority / name
    candidate = logical_absolute_path(path) if path is not None else expected
    if candidate != expected:
        raise ValueError(f"runtime path must be {expected}")
    return expected
