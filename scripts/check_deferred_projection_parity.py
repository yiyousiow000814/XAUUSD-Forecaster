#!/usr/bin/env python
"""Verify Candidate-produced Dashboard projections without mutating either store."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
_root_parser = argparse.ArgumentParser(add_help=False)
_root_parser.add_argument("--runtime-root")
_root_parser.add_argument("--producer-root")
_root_args, _ = _root_parser.parse_known_args()
RUNTIME_ROOT = Path(_root_args.runtime_root or Path.cwd()).resolve()
PRODUCER_ROOT = Path(_root_args.producer_root or Path.cwd()).resolve()
# Projection builders belong to the exact Windows producer revision. Mutable
# authority belongs to RuntimeRoot; neither location is inferred from the other.
sys.path.insert(0, str(PRODUCER_ROOT / "scripts"))

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
LOCAL_DATABASE = RUNTIME_ROOT / ".local" / "forward" / "forward-evidence.sqlite3"
AUDIT_READ_MODEL_CONTRACT = "dashboard-audit-summary-v1"
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


def _read_persisted_audit_authority(database: Path = LOCAL_DATABASE) -> dict | None:
    """Read the bounded audit authority without depending on the serving cache."""
    if not database.is_file():
        return None
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro", uri=True, timeout=5,
    )
    try:
        try:
            row = connection.execute(
                """SELECT contract_version,generated_at,payload_json,payload_hash
                     FROM dashboard_optional_read_models_v1
                    WHERE resource='audit'"""
            ).fetchone()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return None
            raise
    finally:
        connection.close()
    if row is None:
        return None
    contract_version, generated_at, raw_payload, payload_hash = row
    if contract_version != AUDIT_READ_MODEL_CONTRACT:
        raise ValueError("persisted audit authority contract mismatch")
    if not isinstance(raw_payload, str):
        raise ValueError("persisted audit authority payload is not text")
    body = raw_payload.encode("utf-8")
    if not body or len(body) > REMOTE_PAYLOAD_LIMIT_BYTES:
        raise ValueError("persisted audit authority exceeds transport bound")
    if hashlib.sha256(body).hexdigest() != payload_hash:
        raise ValueError("persisted audit authority hash mismatch")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("persisted audit authority is not an object")
    if payload.get("generated_at") != generated_at:
        raise ValueError("persisted audit authority metadata mismatch")
    return payload


def _read_local_authority() -> tuple[dict, str]:
    persisted = _read_persisted_audit_authority()
    if persisted is not None:
        return persisted, "persisted-read-model"
    authority, _ = _read_json(LOCAL_AUDIT_URL)
    return authority, "local-api"


def verify(
    *, version_id: str, git_sha: str, producer_revision: str,
    routes: list[str], required_after: datetime, observe_attempt: str,
) -> dict:
    try:
        authority, authority_source = _read_local_authority()
    except (
        OSError, sqlite3.Error, ValueError, json.JSONDecodeError,
        urllib.error.URLError,
    ) as error:
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
            "authority_source": authority_source, "routes": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--producer-root", required=True)
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
