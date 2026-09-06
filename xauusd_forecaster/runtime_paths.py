"""Windows runtime path ownership helpers."""

from __future__ import annotations

import os
import hashlib
import json
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit


def isolated_runtime_configuration() -> dict | None:
    """Read the explicit rehearsal authority, never an implicit credential fallback."""
    supplied = os.environ.get("XAUUSD_ISOLATED_CONFIGURATION")
    expected = os.environ.get("XAUUSD_ISOLATED_CONFIGURATION_SHA256")
    if not supplied and not expected:
        return None
    if not supplied or not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        raise ValueError("ISOLATED_CONFIGURATION_REQUIRED")
    if not os.path.isabs(supplied):
        raise ValueError("ISOLATED_CONFIGURATION_ROOT_INVALID")
    locator = Path(os.path.abspath(supplied))
    if str(locator).startswith("\\\\") or locator.name != "fixture-user-environment.json" or not re.fullmatch(r"xauusd-rehearsal-[0-9a-f]{32}", locator.parent.name):
        raise ValueError("ISOLATED_CONFIGURATION_ROOT_INVALID")
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        # Current-token profile, not replaceable USERPROFILE; no secret reads.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, buffer) != 0:
            raise ValueError("ISOLATED_CONFIGURATION_PROFILE_UNAVAILABLE")
        real_profile = Path(buffer.value)
        for name in ("XAUUSD-Forecaster", "XAUUSD-Forecaster-runtime", "XAUUSD-Forecaster.local", ".codex/worktrees"):
            if locator.parent.is_relative_to(real_profile / name):
                raise ValueError("ISOLATED_CONFIGURATION_PRODUCTION_ROOT_DENIED")
    for ancestor in reversed([locator, *locator.parents]):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("ISOLATED_CONFIGURATION_REPARSE_DENIED")
    with locator.open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("ISOLATED_CONFIGURATION_TOO_LARGE")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("ISOLATED_CONFIGURATION_IDENTITY_MISMATCH")
    config = json.loads(raw)
    identity = config.get("fixture_id", "")
    if (type(config.get("schema_version")) is not int or config.get("schema_version") != 1 or config.get("mode") != "ISOLATED_REHEARSAL"
            or not re.fullmatch(r"[0-9a-f]{32}", identity)
            or not isinstance(config.get("values"), dict)
            or config.get("task_namespace") != f"\\XAUUSD-Contract-{identity}\\"):
        raise ValueError("ISOLATED_CONFIGURATION_SCHEMA_INVALID")
    if any(not isinstance(config.get(field), str) or not config[field] for field in (
        "owned_root", "runtime_root", "repository_root", "profile_root", "source_root", "provider_endpoint"
    )):
        raise ValueError("ISOLATED_CONFIGURATION_SCHEMA_INVALID")
    root = Path(os.path.abspath(config["owned_root"]))
    if root.name != f"xauusd-rehearsal-{identity}" or Path(os.path.abspath(supplied)) != root / "fixture-user-environment.json":
        raise ValueError("ISOLATED_CONFIGURATION_ROOT_INVALID")
    declared = [Path(os.path.abspath(config[name])) for name in ("runtime_root", "repository_root", "profile_root", "source_root")]
    if any(path == root or not path.is_relative_to(root) for path in declared):
        raise ValueError("ISOLATED_CONFIGURATION_ROOT_INVALID")
    if declared[0] != declared[2] / "XAUUSD-Forecaster-runtime":
        raise ValueError("ISOLATED_CONFIGURATION_RUNTIME_INVALID")
    for path in [root, *declared]:
        for ancestor in reversed([path, *path.parents]):
            try:
                metadata = ancestor.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ValueError("ISOLATED_CONFIGURATION_REPARSE_DENIED")
    ports = config.get("loopback_ports")
    if not isinstance(ports, list) or not ports or any(type(port) is not int or not 1024 < port <= 65535 or port == 8765 for port in ports):
        raise ValueError("ISOLATED_CONFIGURATION_ENDPOINT_INVALID")
    urls = [config.get("provider_endpoint", "")] + [value for name, value in config["values"].items() if name.endswith("URL") and value]
    for value in urls:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.port not in ports or url.username:
            raise ValueError("ISOLATED_CONFIGURATION_ENDPOINT_INVALID")
    return config


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
