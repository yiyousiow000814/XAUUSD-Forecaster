"""Windows runtime path ownership helpers."""

from __future__ import annotations

import os
from pathlib import Path


def logical_absolute_path(path: str | Path) -> Path:
    """Normalize traversal without dereferencing the declared state authority."""
    return Path(os.path.abspath(os.fspath(path)))


PRODUCTION_RUNTIME_STATE_ROOT = logical_absolute_path(
    Path.home() / "XAUUSD-Forecaster-runtime" / ".local" / "forward"
)
PREFLIGHT_RUNTIME_STATE_ROOT = logical_absolute_path(
    Path.home() / "XAUUSD-Forecaster-runtime" / ".local" / "preflight"
)


def authoritative_runtime_root(
    declared: str | Path,
    *,
    role: str = "production",
) -> Path:
    """Bind a service declaration to one contract-owned Windows authority."""
    authorities = {
        "production": PRODUCTION_RUNTIME_STATE_ROOT,
        "preflight": PREFLIGHT_RUNTIME_STATE_ROOT,
    }
    if role not in authorities:
        raise ValueError("runtime role is invalid")
    authority = authorities[role]
    if logical_absolute_path(declared) != authority:
        raise ValueError("declared runtime state root does not match contract authority")
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
