#!/usr/bin/env python
"""Audit one self-consistent production status snapshot."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.production_shape import production_shape_violations  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-url", default="http://127.0.0.1:8765/api/status")
    args = parser.parse_args()

    with urllib.request.urlopen(args.status_url, timeout=20) as response:
        status = json.loads(response.read())
    violations = production_shape_violations(status)
    print(json.dumps({
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
