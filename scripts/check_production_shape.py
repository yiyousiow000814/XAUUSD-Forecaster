#!/usr/bin/env python
"""Audit one self-consistent production status snapshot."""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import urllib.parse
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.production_shape import production_shape_violations  # noqa: E402


TRANSIENT_STATUS_EXIT = 75
STATUS_REFRESH_IN_PROGRESS = "dashboard snapshot refresh is still running"


def _loopback_status_port(value: str) -> int | None:
    """Reduce a permitted local status URL to its loopback port."""
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.path not in {"/api/critical-status", "/api/status"}
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        return None
    return port


def _read_status(status_port: int) -> tuple[dict | None, dict | None]:
    """Return a status snapshot or one structured transport failure."""
    connection = http.client.HTTPConnection("127.0.0.1", status_port, timeout=20)
    try:
        connection.request(
            "GET", "/api/critical-status", headers={"Accept": "application/json"},
        )
        response = connection.getresponse()
        serialized = response.read()
        if response.status >= 400:
            try:
                payload = json.loads(serialized)
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
            message = str(
                payload.get("error") if isinstance(payload, dict) else ""
            ) or str(response.reason or f"HTTP {response.status}")
            transient = (
                response.status == 503 and message == STATUS_REFRESH_IN_PROGRESS
            )
            return None, {
                "status": "DEFERRED" if transient else "ERROR",
                "error_code": (
                    "STATUS_SNAPSHOT_REFRESH_IN_PROGRESS"
                    if transient else "STATUS_ENDPOINT_HTTP_ERROR"
                ),
                "http_status": response.status,
                "error": message,
            }
        try:
            payload = json.loads(serialized)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return None, {
                "status": "ERROR",
                "error_code": "STATUS_RESPONSE_INVALID",
                "error": f"{type(error).__name__}: {error}",
            }
        if not isinstance(payload, dict):
            return None, {
                "status": "ERROR",
                "error_code": "STATUS_RESPONSE_INVALID",
                "error": "status response is not a JSON object",
            }
        return payload, None
    except (TimeoutError, OSError, http.client.HTTPException) as error:
        return None, {
            "status": "ERROR",
            "error_code": "STATUS_ENDPOINT_UNAVAILABLE",
            "error": f"{type(error).__name__}: {error}",
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status-url", default="http://127.0.0.1:8765/api/critical-status",
    )
    parser.add_argument(
        "--allow-pending-generation-decision", action="store_true",
        help="During post-reload observation, wait for the next live boundary.",
    )
    args = parser.parse_args()

    status_port = _loopback_status_port(args.status_url)
    if status_port is None:
        print(json.dumps({
            "status": "ERROR",
            "error_code": "STATUS_ENDPOINT_URL_INVALID",
            "error": "status URL must be a permitted loopback status endpoint",
        }, ensure_ascii=False, sort_keys=True))
        return 2
    status, transport_failure = _read_status(status_port)
    if transport_failure:
        print(json.dumps(transport_failure, ensure_ascii=False, sort_keys=True))
        return (
            TRANSIENT_STATUS_EXIT
            if transport_failure["status"] == "DEFERRED" else 2
        )
    assert status is not None
    violations = production_shape_violations(
        status,
        allow_pending_generation_decision=args.allow_pending_generation_decision,
    )
    print(json.dumps({
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
