#!/usr/bin/env python
"""Verify Candidate-produced Dashboard projections without mutating either store."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "scripts"))

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


def _read_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[dict, object]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(REMOTE_PAYLOAD_LIMIT_BYTES + 1)
        if len(body) > REMOTE_PAYLOAD_LIMIT_BYTES:
            raise ValueError("projection response exceeds transport bound")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("projection response is not an object")
        return payload, response.headers


def verify(
    *, local_audit_url: str, remote_base_url: str, worker_name: str,
    version_id: str, git_sha: str, producer_revision: str,
    routes: list[str], required_after: datetime,
) -> dict:
    try:
        authority, _ = _read_json(local_audit_url)
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
                remote_base_url.rstrip("/") + route,
                headers={"Cloudflare-Workers-Version-Overrides":
                         f'{worker_name}="{version_id}"'},
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
    parser.add_argument("--route", action="append", required=True)
    args = parser.parse_args()
    required_after = datetime.fromisoformat(args.required_after)
    if required_after.tzinfo is None:
        raise SystemExit("required-after must be timezone-aware")
    result = verify(
        local_audit_url=LOCAL_AUDIT_URL,
        remote_base_url=REMOTE_BASE_URL,
        worker_name=WORKER_NAME,
        version_id=args.version_id,
        git_sha=args.git_sha,
        producer_revision=args.producer_revision,
        routes=args.route,
        required_after=required_after,
    )
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["state"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
