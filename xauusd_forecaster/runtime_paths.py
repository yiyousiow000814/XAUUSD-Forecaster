"""Windows runtime path ownership helpers."""

from __future__ import annotations

import os
from pathlib import Path

RUNTIME_STATE_ROOT_ENV = "XAUUSD_RUNTIME_STATE_ROOT"


def logical_absolute_path(path: str | Path) -> Path:
    """Normalize traversal without dereferencing the declared state authority."""
    return Path(os.path.abspath(os.fspath(path)))


def authoritative_runtime_root(declared: str | Path) -> Path:
    """Bind a service declaration to the launcher's inherited authority."""
    inherited = os.environ.get(RUNTIME_STATE_ROOT_ENV, "").strip()
    if not inherited:
        raise ValueError(f"{RUNTIME_STATE_ROOT_ENV} is required")
    authority = logical_absolute_path(inherited)
    if logical_absolute_path(declared) != authority:
        raise ValueError("declared runtime state root does not match launcher authority")
    return authority


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
