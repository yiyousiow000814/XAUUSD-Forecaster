"""External production smoke probe with stable operational error codes."""

from __future__ import annotations

import json
import sys
import argparse
import time
from datetime import UTC, datetime
from pathlib import Path
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "https://aurum-signal-room.yiyousiow1234.workers.dev"
TIMEOUT_SECONDS = 20
PAGE_MARKERS = {
    "/": "Aurum Signal Room",
    "/health": "系统健康状态",
    "/audit": "证据台页面",
}


class ProbeFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def read_runtime_json(path: Path) -> dict:
    # Follow the existing runtime heartbeat's bounded Windows sharing retry.
    # A replacement may briefly deny a new reader; persistent failure is an
    # error, not an empty or healthy status.
    for attempt in range(50):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def read(url: str) -> tuple[int, bytes, str]:
    status, body, headers = read_response(url)
    return status, body, headers.get("Content-Type", "")


def read_response(url: str) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={
        "Accept": "text/html,application/json",
        "User-Agent": "AurumExternalHealthProbe/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return (
                int(response.status), response.read(2_000_000),
                dict(response.headers.items()),
            )
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProbeFailure(
            "OPS_PUBLIC_ENDPOINT_UNAVAILABLE", f"{url}: {error}",
        ) from error


def check_publication(
    *, local_revision: str, worker_revision: str, worker_version: str,
    started_after: datetime, sync_status: Path,
    public_base: str = DEFAULT_BASE_URL,
    local_base: str = "http://127.0.0.1:8765",
) -> dict:
    """Check actual identity, critical business state and the sync owner's ACK.

    Optional news catch-up is reported separately; it cannot be relabelled as
    complete by a successful maintenance exit.
    """
    if started_after.tzinfo is None:
        raise ValueError("Maintenance start must include its time zone")
    now = datetime.now(UTC)

    def fresh(value: object) -> bool:
        try:
            stamp = datetime.fromisoformat(str(value))
            return stamp.tzinfo is not None and started_after <= stamp <= now and (now - stamp).total_seconds() <= 180
        except (ValueError, TypeError):
            return False

    observations = {}
    for name, base in (("local", local_base), ("public", public_base)):
        status, raw, headers = read_response(base.rstrip("/") + "/api/status")
        try:
            payload = json.loads(raw)
            system = payload["system"]
            provenance = system["deployment"]
        except (ValueError, TypeError, KeyError) as error:
            raise ProbeFailure("OPS_PUBLIC_RESPONSE_INVALID", f"{name}: missing business status") from error
        if status != 200 or provenance.get("runtime_git_sha") != local_revision or provenance.get("runtime_dirty") is not False:
            raise ProbeFailure("OPS_PUBLICATION_IDENTITY_MISMATCH", f"{name}: source identity is not the deployed clean revision")
        if not fresh(provenance.get("payload_generated_at")):
            raise ProbeFailure("OPS_PUBLICATION_STALE", f"{name}: no fresh post-maintenance snapshot")
        market_closed = system.get("market_session") in {"CLOSED", "WEEKLY_CLOSED"}
        if (system.get("online") is not True and not market_closed) or system.get("trading_enabled") is not False or system.get("mode") != "SHADOW":
            raise ProbeFailure("OPS_PUBLICATION_BUSINESS_UNHEALTHY", f"{name}: critical business state is not ready")
        collector = system.get("components", {}).get("decision_collector", {})
        if collector.get("status") not in {"OK", "MARKET_CLOSED"}:
            raise ProbeFailure("OPS_PUBLICATION_BUSINESS_UNHEALTHY", f"{name}: collector is not ready")
        if name == "public":
            normalized = {key.lower(): value for key, value in headers.items()}
            if normalized.get("x-aurum-worker-version") != worker_version or normalized.get("x-aurum-git-sha") != worker_revision:
                raise ProbeFailure("OPS_PUBLICATION_IDENTITY_MISMATCH", "public: Worker identity mismatch")
            if payload.get("observation_scope") != "D1_SNAPSHOT":
                raise ProbeFailure("OPS_PUBLICATION_SYNC_UNCONFIRMED", "public: authoritative D1 snapshot is not being served")
        observations[name] = provenance
    epoch = observations["local"].get("source_database_epoch")
    if not epoch or observations["public"].get("source_database_epoch") != epoch:
        raise ProbeFailure("OPS_PUBLICATION_IDENTITY_MISMATCH", "local/public database epoch mismatch")
    try:
        sync = read_runtime_json(sync_status)
        collector_status = read_runtime_json(sync_status.parent / "collector-status.json")
    except (OSError, ValueError) as error:
        raise ProbeFailure("OPS_PUBLICATION_SYNC_UNCONFIRMED", "sync owner status is unavailable") from error
    if not isinstance(collector_status, dict) or collector_status.get("service") != "collector" or collector_status.get("state") != "RUNNING" or not fresh(collector_status.get("last_success")):
        raise ProbeFailure("OPS_PUBLICATION_BUSINESS_UNHEALTHY", "collector has no fresh running heartbeat, including during market closure")
    if not isinstance(sync, dict) or not fresh(sync.get("last_success")) or not any(
        row.get("target") == "cloudflare" and row.get("resource") == "heartbeat"
        and row.get("status") == "OK" and fresh(row.get("completed_at"))
        for row in sync.get("resource_observations", []) if isinstance(row, dict)
    ):
        raise ProbeFailure("OPS_PUBLICATION_SYNC_UNCONFIRMED", "no fresh Cloudflare heartbeat ACK from the real sync owner")
    return {
        "local_revision": local_revision, "worker_revision": worker_revision,
        "worker_version": worker_version, "source_database_epoch": epoch,
        "critical_business": "PASS", "heartbeat_sync": "PASS",
        "degraded_resources": sync.get("degraded_resources", []),
        "news_backlog_acceptance": "SEPARATE", "capacity_acceptance": "SEPARATE",
    }


def check_public_surface(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    evidence: list[str] = []
    for path, marker in PAGE_MARKERS.items():
        status, body, _ = read(base + path)
        text = body.decode("utf-8", errors="replace")
        if status != 200:
            raise ProbeFailure(
                "OPS_PUBLIC_ENDPOINT_UNAVAILABLE", f"{path}: HTTP {status}",
            )
        if marker not in text:
            raise ProbeFailure(
                "OPS_PUBLIC_RENDER_CONTRACT_FAILED",
                f"{path}: missing server-rendered marker {marker!r}",
            )
        evidence.append(f"{path}=200:{len(body)}B")

    for path in ("/api/status",):
        status, body, _ = read(base + path)
        if status != 200:
            raise ProbeFailure("OPS_PUBLIC_ENDPOINT_UNAVAILABLE", f"{path}: HTTP {status}")
        try:
            payload = json.loads(body)
        except (TypeError, ValueError) as error:
            raise ProbeFailure(
                "OPS_PUBLIC_RESPONSE_INVALID", f"{path}: invalid JSON",
            ) from error
        if not isinstance(payload, dict):
            raise ProbeFailure(
                "OPS_PUBLIC_RESPONSE_INVALID", f"{path}: response contract mismatch",
            )
        evidence.append(f"{path}=200:{len(body)}B")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-revision")
    parser.add_argument("--worker-revision")
    parser.add_argument("--worker-version")
    parser.add_argument("--started-after")
    parser.add_argument("--sync-status", type=Path)
    args = parser.parse_args()
    publication_values = (args.local_revision, args.worker_revision, args.worker_version, args.started_after, args.sync_status)
    if any(publication_values) and not all(publication_values):
        parser.error("publication verification requires all identity, maintenance time and sync status arguments")
    try:
        # This operational probe intentionally targets one fixed public surface.
        # Keeping the destination out of CLI input prevents it from becoming a
        # general-purpose server-side URL fetcher.
        evidence = check_public_surface(DEFAULT_BASE_URL)
        if all(publication_values):
            result = check_publication(
                local_revision=args.local_revision, worker_revision=args.worker_revision,
                worker_version=args.worker_version, started_after=datetime.fromisoformat(args.started_after),
                sync_status=args.sync_status,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
    except ProbeFailure as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    print("PRODUCTION_ANONYMOUS_ACCESS_RESULT PUBLIC_PASS " + " ".join(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
