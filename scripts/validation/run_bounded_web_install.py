from __future__ import annotations

import argparse
from collections import deque
import hashlib
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import threading
import time


DEFAULT_TIMEOUT_SECONDS = 150
DEFAULT_KILL_GRACE_SECONDS = 10
TAIL_LINE_LIMIT = 120
NPM_CI_ARGS = (
    "ci",
    "--prefer-offline",
    "--no-audit",
    "--no-fund",
    "--fetch-retries=1",
    "--fetch-retry-mintimeout=1000",
    "--fetch-retry-maxtimeout=5000",
    "--fetch-timeout=30000",
)

_SECRET = re.compile(
    r"(?i)(?:_authToken|authorization|bearer|password)(\s*[=:]\s*)(\S+)"
)
_URL_CREDENTIAL = re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE)


def _redact(value: str) -> str:
    value = _SECRET.sub(lambda match: f"[REDACTED_KEY]{match.group(1)}[REDACTED]", value)
    return _URL_CREDENTIAL.sub(r"\1[REDACTED]@", value)


def _bounded_probe(command: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"UNAVAILABLE:{type(exc).__name__}"
    output = (result.stdout or result.stderr).strip().splitlines()
    return _redact(output[-1]) if output else f"EXIT_{result.returncode}"


def _start_process(command: list[str], cwd: Path) -> subprocess.Popen[str]:
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _terminate_tree(process: subprocess.Popen[str], grace_seconds: float) -> bool:
    if process.poll() is not None:
        return True
    if os.name == "nt":
        killer = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=max(1.0, grace_seconds),
        )
        if killer.returncode != 0 and process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=max(1.0, grace_seconds))
    except subprocess.TimeoutExpired:
        return False
    return process.poll() is not None


def run_bounded_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    kill_grace_seconds: float,
) -> tuple[int, tuple[str, ...], float, bool]:
    started = time.monotonic()
    process = _start_process(command, cwd)
    tail: deque[str] = deque(maxlen=TAIL_LINE_LIMIT)

    def relay() -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = _redact(raw_line.rstrip("\r\n"))
            tail.append(line)
            print(line, flush=True)

    reader = threading.Thread(target=relay, name="bounded-web-install-output", daemon=True)
    reader.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminated = _terminate_tree(process, kill_grace_seconds)
        return_code = 124 if terminated else 125
    reader.join(timeout=max(1.0, kill_grace_seconds))
    elapsed = time.monotonic() - started
    return return_code, tuple(tail), elapsed, timed_out


def _debug_log_tail(cache_path: Path) -> tuple[str, ...]:
    log_dir = cache_path / "_logs"
    if not log_dir.is_dir():
        return ()
    candidates = sorted(
        log_dir.glob("*-debug-0.log"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if not candidates:
        return ()
    try:
        lines = candidates[0].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()
    return tuple(_redact(line) for line in lines[-TAIL_LINE_LIMIT:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--kill-grace-seconds", type=float, default=DEFAULT_KILL_GRACE_SECONDS
    )
    parser.add_argument("--npm-executable", default="npm")
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0 or args.kill_grace_seconds <= 0:
        parser.error("timeouts must be positive")

    cwd = Path.cwd().resolve()
    lockfile = cwd / "package-lock.json"
    if not lockfile.is_file():
        print("WEB_DEPENDENCY_LOCKFILE_MISSING", file=sys.stderr)
        return 2

    npm = shutil.which(args.npm_executable)
    if npm is None:
        print("WEB_DEPENDENCY_NPM_EXECUTABLE_MISSING", file=sys.stderr)
        return 2
    cache_value = _bounded_probe([npm, "config", "get", "cache"], cwd)
    cache_available = not cache_value.startswith(("UNAVAILABLE:", "EXIT_"))
    cache_path = Path(cache_value) if cache_available else Path()
    print(f"node_version={_bounded_probe(['node', '--version'], cwd)}")
    print(f"npm_version={_bounded_probe([npm, '--version'], cwd)}")
    print(f"package_lock_sha256={hashlib.sha256(lockfile.read_bytes()).hexdigest()}")
    print(f"npm_cache_path={_redact(cache_value)}")
    command = [npm, *NPM_CI_ARGS]
    return_code, output_tail, elapsed, timed_out = run_bounded_command(
        command,
        cwd=cwd,
        timeout_seconds=args.timeout_seconds,
        kill_grace_seconds=args.kill_grace_seconds,
    )
    print(f"web_dependency_install_elapsed_seconds={elapsed:.3f}")
    if timed_out:
        print("WEB_DEPENDENCY_INSTALL_TIMEOUT", file=sys.stderr)
        for line in output_tail[-40:]:
            print(f"npm_output_tail: {line}", file=sys.stderr)
        if cache_available:
            for line in _debug_log_tail(cache_path)[-40:]:
                print(f"npm_debug_tail: {line}", file=sys.stderr)
    elif return_code != 0:
        print(f"WEB_DEPENDENCY_INSTALL_FAILED exit_code={return_code}", file=sys.stderr)
        if cache_available:
            for line in _debug_log_tail(cache_path)[-40:]:
                print(f"npm_debug_tail: {line}", file=sys.stderr)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
