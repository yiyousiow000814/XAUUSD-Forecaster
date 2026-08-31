#!/usr/bin/env python
"""Verify Candidate-produced Dashboard projections without mutating either store."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
# Release Control starts this bundled probe with the authoritative RuntimeRoot
# as its explicit working directory. Projection builders remain owned by that
# exact Windows revision rather than by the Control Plane bundle.
RUNTIME_ROOT = Path.cwd().resolve()
sys.path.insert(0, str(RUNTIME_ROOT / "scripts"))

from run_dashboard_sync import (  # noqa: E402
    REMOTE_PAYLOAD_LIMIT_BYTES,
    audit_briefs_snapshot,
    audit_decisions_snapshot,
    audit_stories_snapshot,
)

BUILDERS = {
    "/api/audit-briefs": audit_briefs_snapshot,
    "/api/audit-stories": audit_stories_snapshot,
    "/api/audit-decisions": audit_decisions_snapshot,
}
LOCAL_AUDIT_URL = "http://127.0.0.1:8765/api/audit"
REMOTE_BASE_URL = "https://aurum-signal-room.yiyousiow1234.workers.dev"
WORKER_NAME = "aurum-signal-room"
RELEASE_CONTROL_USER_AGENT = "XAUUSD-Forecaster-Release-Control/1"
REMOTE_URLS = {route: REMOTE_BASE_URL + route for route in BUILDERS}
OBSERVE_ATTEMPT_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _remote_observe_url(route: str, observe_attempt: str) -> str:
    if not OBSERVE_ATTEMPT_PATTERN.fullmatch(observe_attempt):
        raise ValueError("observe attempt must be a lowercase 32-character hex id")
    return REMOTE_URLS[route] + "?" + urllib.parse.urlencode(
        {"__release_observe": observe_attempt}
    )


def _read_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[dict, object]:
    request_headers = dict(headers or {})
    request_headers["User-Agent"] = RELEASE_CONTROL_USER_AGENT
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(REMOTE_PAYLOAD_LIMIT_BYTES + 1)
        if len(body) > REMOTE_PAYLOAD_LIMIT_BYTES:
            raise ValueError("projection response exceeds transport bound")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("projection response is not an object")
        return payload, response.headers


def verify(
    *, version_id: str, git_sha: str, producer_revision: str,
    routes: list[str], required_after: datetime, observe_attempt: str,
) -> dict:
    try:
        authority, _ = _read_json(LOCAL_AUDIT_URL)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        return {"state": "PENDING", "reason": "LOCAL_AUDIT_AUTHORITY_UNAVAILABLE",
                "diagnostic": str(error)[:512], "routes": []}
    results = []
    for route in routes:
        builder = BUILDERS.get(route)
        if builder is None:
            return {"state": "FAILED", "reason": "DEFERRED_PROJECTION_ROUTE_NOT_ALLOWED",
                    "routes": results}
        expected = json.loads(builder(authority, producer_revision).decode("utf-8"))
        try:
            observed, headers = _read_json(
                _remote_observe_url(route, observe_attempt),
                headers={
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Cloudflare-Workers-Version-Overrides":
                        f'{WORKER_NAME}="{version_id}"',
                },
            )
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            results.append({"route": route, "state": "PENDING",
                            "reason": "PROJECTION_READ_PENDING",
                            "diagnostic": str(error)[:512]})
            continue
        identity_ok = (
            headers.get("X-Aurum-Worker-Version") == version_id
            and headers.get("X-Aurum-Git-SHA") == git_sha
        )
        if not identity_ok:
            results.append({"route": route, "state": "FAILED",
                            "reason": "EXACT_VERSION_IDENTITY_MISMATCH"})
            continue
        try:
            generated_at = datetime.fromisoformat(str(observed["generated_at"]))
            if generated_at.tzinfo is None:
                raise ValueError("generated_at must be timezone-aware")
            fresh = generated_at.astimezone(UTC) >= required_after.astimezone(UTC)
        except (KeyError, TypeError, ValueError):
            results.append({"route": route, "state": "FAILED",
                            "reason": "PROJECTION_GENERATED_AT_INVALID"})
            continue
        if observed.get("producer_revision") != producer_revision:
            results.append({"route": route, "state": "PENDING",
                            "reason": "CANDIDATE_PROJECTION_PRODUCER_PENDING"})
        elif not fresh or observed != expected:
            results.append({"route": route, "state": "PENDING",
                            "reason": "CANDIDATE_PROJECTION_PARITY_PENDING"})
        else:
            results.append({"route": route, "state": "PASSED", "reason": "PASSED",
                            "generated_at": generated_at.astimezone(UTC).isoformat()})
    failed = [item for item in results if item["state"] == "FAILED"]
    pending = [item for item in results if item["state"] == "PENDING"]
    state = "FAILED" if failed else "PENDING" if pending else "PASSED"
    return {"state": state, "reason": (failed or pending or [{"reason": "PASSED"}])[0]["reason"],
            "routes": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--producer-revision", required=True)
    parser.add_argument("--required-after", required=True)
    parser.add_argument("--observe-attempt", required=True)
    parser.add_argument("--route", action="append", required=True)
    args = parser.parse_args()
    required_after = datetime.fromisoformat(args.required_after)
    if required_after.tzinfo is None:
        raise SystemExit("required-after must be timezone-aware")
    result = verify(
        version_id=args.version_id,
        git_sha=args.git_sha,
        producer_revision=args.producer_revision,
        routes=args.route,
        required_after=required_after,
        observe_attempt=args.observe_attempt,
    )
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["state"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
