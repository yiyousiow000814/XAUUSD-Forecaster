"""Operating-system process identity for maintenance leases."""

from __future__ import annotations

import os
from pathlib import Path


def _windows_process_start_token(process_id: int) -> tuple[str | None, bool | None]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    process = kernel32.OpenProcess(0x1000, False, process_id)
    if not process:
        error = ctypes.get_last_error()
        return None, False if error == 87 else None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            process, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            return None, None
        value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"windows-filetime:{value}", True
    finally:
        kernel32.CloseHandle(process)


def _process_start_token(process_id: int) -> tuple[str | None, bool | None]:
    """Return an OS process-start identity and whether that PID is alive."""
    if os.name == "nt":
        return _windows_process_start_token(process_id)
    stat = Path(f"/proc/{process_id}/stat")
    try:
        value = stat.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, None
    try:
        fields = value[value.rfind(")") + 2:].split()
        return f"proc-start:{fields[19]}", True
    except (IndexError, ValueError):
        return None, None


def _process_identity_alive(process_id: int, start_token: str) -> bool | None:
    current_token, alive = _process_start_token(process_id)
    if alive is not True:
        return alive
    if not current_token or not start_token:
        return None
    return current_token == start_token
