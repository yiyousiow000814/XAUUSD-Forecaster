#!/usr/bin/env python
"""Verify checked-in PowerShell AST evidence on the Windows gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

from architecture_compiler import _powershell_digest, extract_powershell_exact


def main() -> int:
    if os.name != "nt":
        print("Windows PowerShell AST evidence is unavailable on this platform.")
        return 2
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "architecture/generated/windows-evidence.json").read_text(encoding="utf-8"))
    expected_digest = _powershell_digest(root)
    expected_facts = extract_powershell_exact(root)
    if evidence.get("status") != "CURRENT" or evidence.get("powershell_digest") != expected_digest or evidence.get("facts") != expected_facts:
        print("Windows PowerShell AST evidence is stale.")
        return 1
    print(f"Windows PowerShell AST evidence passed ({len(expected_facts)} facts).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

