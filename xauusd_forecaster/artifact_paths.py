"""Finite runtime ownership for persisted model-artifact locators."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


ARTIFACT_FAMILIES = (
    "models-v2",
    "execution-models-v1",
    "execution-models-v2",
)
FORMER_CHECKOUT_FORWARD = PureWindowsPath(
    r"C:\Users\yiyou\XAUUSD-Forecaster\.local\forward"
)
OLDER_AUTOMATED_TRADING_FORWARD = PureWindowsPath(
    r"C:\Users\yiyou\automated-trading\src\XAUUSD-Forecaster\.local\forward"
)


@dataclass(frozen=True)
class ArtifactPathResolution:
    source_family: str
    artifact_family: str
    original: str
    canonical: Path


def _windows_parts(value: str | Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in PureWindowsPath(str(value)).parts)


def _relative_after_prefix(
    value: PureWindowsPath, prefix: PureWindowsPath,
) -> tuple[str, ...] | None:
    parts = _windows_parts(value)
    prefix_parts = _windows_parts(prefix)
    if parts[: len(prefix_parts)] != prefix_parts:
        return None
    return tuple(value.parts[len(prefix_parts) :])


def _known_relative_parts(value: PureWindowsPath) -> tuple[str, ...] | None:
    if value.is_absolute():
        return None
    parts = value.parts
    lowered = tuple(part.lower() for part in parts)
    prefixes = (
        ("src", "xauusd-forecaster", ".local", "forward"),
        (".local", "forward"),
        (),
    )
    for prefix in prefixes:
        if lowered[: len(prefix)] != prefix:
            continue
        remaining = tuple(parts[len(prefix) :])
        if remaining and remaining[0].lower() in ARTIFACT_FAMILIES:
            return remaining
    return None


def canonicalize_artifact_path(
    value: str | Path,
    *,
    runtime_forward_root: Path,
) -> ArtifactPathResolution:
    """Map one proven legacy locator into the authoritative RuntimeRoot."""
    raw = str(value)
    candidate = PureWindowsPath(raw)
    runtime_forward = PureWindowsPath(str(runtime_forward_root.resolve()))
    families = (
        ("ALREADY_CANONICAL", runtime_forward),
        ("FORMER_CHECKOUT", FORMER_CHECKOUT_FORWARD),
        ("OLDER_AUTOMATED_TRADING", OLDER_AUTOMATED_TRADING_FORWARD),
    )
    relative: tuple[str, ...] | None = None
    source_family = ""
    for label, prefix in families:
        relative = _relative_after_prefix(candidate, prefix)
        if relative is not None:
            source_family = label
            break
    if relative is None:
        relative = _known_relative_parts(candidate)
        source_family = "KNOWN_RELATIVE" if relative is not None else ""
    if not source_family or not relative:
        raise ValueError(f"UNKNOWN_ARTIFACT_ROOT:{raw}")
    artifact_family = relative[0].lower()
    if artifact_family not in ARTIFACT_FAMILIES:
        raise ValueError(f"UNKNOWN_ARTIFACT_FAMILY:{raw}")
    canonical_root = (runtime_forward_root / artifact_family).resolve()
    canonical = (canonical_root.joinpath(*relative[1:])).resolve()
    try:
        canonical.relative_to(canonical_root)
    except ValueError as exc:
        raise ValueError(f"ARTIFACT_PATH_OUTSIDE_RUNTIME_ROOT:{raw}") from exc
    return ArtifactPathResolution(
        source_family=source_family,
        artifact_family=artifact_family,
        original=raw,
        canonical=canonical,
    )


def require_runtime_artifact_path(
    value: str | Path,
    *,
    runtime_forward_root: Path,
) -> Path:
    resolution = canonicalize_artifact_path(
        value, runtime_forward_root=runtime_forward_root,
    )
    if not resolution.canonical.is_file():
        raise RuntimeError(
            f"RUNTIME_ARTIFACT_MISSING:{resolution.canonical}"
        )
    return resolution.canonical


def sqlite_runtime_forward_root(connection) -> Path:
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None:
        raise RuntimeError("SQLITE_RUNTIME_ROOT_UNAVAILABLE")
    database = Path(str(row[2])).resolve()
    return database.parent
