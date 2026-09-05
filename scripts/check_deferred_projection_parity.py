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
    _deferred_projection_request_digest,
    NEWS_EVIDENCE_CONTRACT_VERSION,
    UUID_PATTERN,
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
REMOTE_URLS["/api/news-evidence"] = REMOTE_BASE_URL + "/api/news-evidence"
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


def _verify_news_recovery(*, version_id, git_sha, producer_revision, required_after, observe_attempt):
    route = "/api/news-evidence"
    state_root = RUNTIME_ROOT / ".local/forward"
    try:
        def read_state(name):
            with (state_root / name).open("rb") as stream:
                raw = stream.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("deferred News state exceeds bound")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("deferred News state is not an object")
            return value

        request = read_state("deferred-projection-sync-request.json")
        receipt = read_state("deferred-projection-sync-receipt.json")
        if receipt.get("state") != "COMPLETED":
            return {"route": route, "state": "PENDING", "reason": "NEWS_RECOVERY_SYNC_PENDING"}
        incident = request.get("collector_recovery", {})
        expected_key = f"{version_id}:{git_sha}"
        if (
            request.get("schema_version") != "deferred-projection-sync-v1"
            or receipt.get("schema_version") != request["schema_version"]
            or request.get("validation_key") != expected_key
            or receipt.get("validation_key") != expected_key
            or request.get("worker_version_id") != version_id
            or receipt.get("worker_version_id") != version_id
            or request.get("producer_revision") != producer_revision
            or receipt.get("producer_revision") != producer_revision
            or receipt.get("request_id") != request.get("request_id")
            or receipt.get("transaction_id") != request.get("transaction_id")
            or receipt.get("request_digest") != _deferred_projection_request_digest(request)
            or not UUID_PATTERN.fullmatch(str(request.get("request_id") or ""))
            or not UUID_PATTERN.fullmatch(str(request.get("transaction_id") or ""))
            or request.get("target") != "cloudflare"
            or receipt.get("routes") != request.get("routes")
            or datetime.fromisoformat(request["required_after"]) != required_after
            or datetime.fromisoformat(receipt["required_after"]) != required_after
            or route not in request.get("routes", [])
            or incident.get("incident") != "COLLECTOR_CLOCK_EVENT_ATOMICITY"
            or incident.get("broken_revision") != "ffe1de29c0891cc3a3cf3d602f3d3ee657faa9b8"
            or incident.get("target_revision") != producer_revision
        ):
            raise ValueError("deferred News recovery identity conflict")
        news = receipt.get("news_recovery", {})
        read_at = datetime.fromisoformat(news["local_read_completed_at"])
        if read_at.tzinfo is None or read_at < required_after:
            raise ValueError("deferred News read predates cutover")
        snapshot = news.get("snapshot_id")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", str(snapshot or ""))
            or news.get("contract_version") != NEWS_EVIDENCE_CONTRACT_VERSION
            or receipt.get("projection_hashes", {}).get(route) != snapshot
        ):
            raise ValueError("deferred News snapshot contract conflict")
        ack = read_state("dashboard-news-evidence-sync-state-cloudflare.json")
        if (
            ack.get("active_snapshot_id") != snapshot
            or ack.get("contract_version") != NEWS_EVIDENCE_CONTRACT_VERSION
            or type(news.get("record_count")) is not int
            or news["record_count"] < 0
            or ack.get("record_count") != news["record_count"]
        ):
            raise ValueError("deferred News normal ACK conflict")
        status = read_state("dashboard-sync-status.json")
        if status.get("last_error") or any(
            row.get("resource") not in ("news_evidence", "deferred_projection")
            for row in status.get("degraded_resources", [])
        ):
            raise ValueError("unrelated Sync failure during News recovery")
        heartbeat = datetime.fromisoformat(status["last_success"])
        if heartbeat.tzinfo is None or not 0 <= (datetime.now(UTC)-heartbeat).total_seconds() <= 120:
            return {"route": route, "state": "PENDING", "reason": "NEWS_RECOVERY_SYNC_HEARTBEAT_STALE"}
        observations = [row for row in status.get("resource_observations", [])
                        if row.get("target") == "cloudflare" and row.get("resource") == "news_evidence"]
        if len(observations) != 1 or observations[0].get("status") != "OK":
            return {"route": route, "state": "PENDING", "reason": "NEWS_RECOVERY_RESOURCE_SUCCESS_PENDING"}
        completed = datetime.fromisoformat(observations[0]["completed_at"])
        if completed.tzinfo is None or completed < required_after:
            return {"route": route, "state": "PENDING", "reason": "NEWS_RECOVERY_RESOURCE_SUCCESS_PENDING"}
        observed, headers = _read_json(
            _remote_observe_url(route, observe_attempt) + "&mode=all&limit=1",
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache",
                     "Cloudflare-Workers-Version-Overrides": f'{WORKER_NAME}="{version_id}"'},
        )
        if headers.get("X-Aurum-Worker-Version") != version_id or headers.get("X-Aurum-Git-SHA") != git_sha:
            raise ValueError("deferred News exact Worker identity mismatch")
        if observed.get("contract_version") != NEWS_EVIDENCE_CONTRACT_VERSION or observed.get("snapshot_id") != snapshot:
            raise ValueError("deferred News remote generation conflict")
        return {"route": route, "state": "PASSED", "reason": "PASSED",
                "snapshot_id": snapshot, "local_read_completed_at": read_at.isoformat()}
    except (FileNotFoundError, urllib.error.URLError, TimeoutError) as error:
        return {"route": route, "state": "PENDING", "reason": "NEWS_RECOVERY_EVIDENCE_PENDING", "diagnostic": str(error)[:512]}
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {"route": route, "state": "FAILED", "reason": "NEWS_RECOVERY_EVIDENCE_INVALID", "diagnostic": str(error)[:512]}


def verify(
    *, version_id: str, git_sha: str, producer_revision: str,
    routes: list[str], required_after: datetime, observe_attempt: str,
) -> dict:
    try:
        authority, authority_source = (
            _read_local_authority() if any(route in BUILDERS for route in routes)
            else (None, "EXACT_PRODUCER_SYNC_RECEIPT")
        )
    except (
        OSError, sqlite3.Error, ValueError, json.JSONDecodeError,
        urllib.error.URLError,
    ) as error:
        return {"state": "PENDING", "reason": "LOCAL_AUDIT_AUTHORITY_UNAVAILABLE",
                "diagnostic": str(error)[:512], "routes": []}
    results = []
    for route in routes:
        if route == "/api/news-evidence":
            results.append(_verify_news_recovery(
                version_id=version_id, git_sha=git_sha, producer_revision=producer_revision,
                required_after=required_after, observe_attempt=observe_attempt,
            ))
            continue
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
