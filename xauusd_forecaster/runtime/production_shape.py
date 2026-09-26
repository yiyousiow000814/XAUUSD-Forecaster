"""Read-only cross-component validation of one production status snapshot."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from xauusd_forecaster.ai.provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.news.scheduler.state import quota_day
from xauusd_forecaster.news.collection.source_registry import NEWS_SOURCE_REGISTRY

PAYLOAD_LIMIT_EXCEEDED = "PAYLOAD_LIMIT_EXCEEDED"
PAYLOAD_CONTRACT_REJECTED = "PAYLOAD_CONTRACT_REJECTED"
PAYLOAD_ERROR_CODES = frozenset({
    PAYLOAD_LIMIT_EXCEEDED, PAYLOAD_CONTRACT_REJECTED,
})


def _utc_instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def production_contract_snapshot(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    account_ids: frozenset[str] | None = None,
) -> dict:
    """Capture all database facts at one SQLite read boundary."""
    instant = now or datetime.now(UTC)
    snapshot: dict = {
        "observed_at": instant.isoformat(),
        "scheduler_usage_available": False,
        "scheduler_usage": {},
        "registered_sources": [spec.source for spec in NEWS_SOURCE_REGISTRY],
        "latest_source_status": {},
    }
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN")
    try:
        scheduler_table = connection.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table'
               AND name='news_ai_account_daily_usage_v1'"""
        ).fetchone()
        if scheduler_table is not None:
            snapshot["scheduler_usage_available"] = True
            day = quota_day(instant)
            for surface in AI_QUOTA_SURFACES:
                placeholders = ",".join("?" for _ in surface.model_families)
                account_clause = ""
                parameters: list[object] = [day, *surface.model_families]
                if account_ids is not None:
                    if account_ids:
                        account_placeholders = ",".join("?" for _ in account_ids)
                        account_clause = f" AND account_id IN ({account_placeholders})"
                        parameters.extend(sorted(account_ids))
                    else:
                        account_clause = " AND 0"
                row = connection.execute(
                    f"""SELECT COALESCE(sum(request_count),0)
                        FROM news_ai_account_daily_usage_v1
                        WHERE quota_day=? AND model_family IN ({placeholders})
                        {account_clause}""",
                    tuple(parameters),
                ).fetchone()
                snapshot["scheduler_usage"][surface.payload_key] = int(row[0])

        for spec in NEWS_SOURCE_REGISTRY:
            row = connection.execute(
                """SELECT status FROM source_polls WHERE source=?
                   ORDER BY fetched_time DESC,poll_id DESC LIMIT 1""",
                (spec.source,),
            ).fetchone()
            snapshot["latest_source_status"][spec.source] = (
                str(row[0]) if row is not None else None
            )
    finally:
        if owns_transaction:
            connection.rollback()
    return snapshot


def production_shape_violations(
    status: dict,
) -> list[str]:
    """Validate externally meaningful contracts within one status snapshot."""
    violations: list[str] = []
    contract = status.get("production_contract")
    if not isinstance(contract, dict):
        return ["production status does not include a contract snapshot"]

    scheduler_usage = contract.get("scheduler_usage") or {}
    if contract.get("scheduler_usage_available") is not True:
        violations.append("scheduler quota ledger is unavailable")
    for surface in AI_QUOTA_SURFACES:
        if surface.payload_key not in scheduler_usage:
            violations.append(
                f"scheduler usage is missing quota surface: {surface.payload_key}"
            )
            continue
        expected_count = scheduler_usage[surface.payload_key]
        quota = status.get(surface.payload_key)
        actual_count = quota.get("total_sent") if isinstance(quota, dict) else None
        accounting = quota.get("accounting_source") if isinstance(quota, dict) else None
        if accounting != "SCHEDULER_DB" or actual_count != expected_count:
            violations.append(
                f"{surface.payload_key} does not match scheduler usage: "
                f"expected {expected_count}, got {actual_count} "
                f"from {accounting or 'unknown'}"
            )

    health_by_source = {
        str(row.get("source")): row
        for row in status.get("news_source_health", [])
        if isinstance(row, dict) and row.get("source")
    }
    latest_source_status = contract.get("latest_source_status") or {}
    expected_sources = {spec.source for spec in NEWS_SOURCE_REGISTRY}
    registered_sources = set(contract.get("registered_sources") or [])
    if registered_sources != expected_sources:
        violations.append("production source registry does not match runtime collectors")
    observed_sources = set(health_by_source)
    if observed_sources != expected_sources:
        missing = sorted(expected_sources - observed_sources)
        unexpected = sorted(observed_sources - expected_sources)
        violations.append(
            f"source health family mismatch: missing={missing}, unexpected={unexpected}"
        )
    for spec in NEWS_SOURCE_REGISTRY:
        if latest_source_status.get(spec.source) != "OK":
            continue
        row = health_by_source.get(spec.source)
        if not isinstance(row, dict):
            continue
        if (
            row.get("latest_status") != "OK"
            or row.get("health") in {"ERROR", "DEGRADED", "FALLBACK_ACTIVE"}
            or row.get("recovery_mode") is not None
            or row.get("next_retry_time") is not None
        ):
            violations.append(
                f"successful source poll is still reported as degraded: {spec.source}"
            )

    system = status.get("system", {})
    market_session = system.get("market_session")
    if market_session not in {"OPEN", "CLOSED", "WEEKLY_CLOSED", "DATA_UNAVAILABLE"}:
        violations.append("broker market session status is unavailable")
    if market_session in {"CLOSED", "WEEKLY_CLOSED"}:
        observed_at = system.get("market_session_observed_at")
        observed_instant = _utc_instant(observed_at)
        if observed_instant is None:
            violations.append("broker market-close observation time is invalid")

    sync_status = status.get("dashboard_sync")
    if not isinstance(sync_status, dict) or sync_status.get("status") not in {
        "OK", "DEGRADED", "ERROR",
    }:
        violations.append("dashboard synchronizer status is unavailable")
        sync_status = {}
    if sync_status.get("last_error_code") in PAYLOAD_ERROR_CODES:
        violations.append("dashboard heartbeat exceeds the remote payload limit")
    for item in sync_status.get("degraded_resources", []):
        if item.get("error_code") in PAYLOAD_ERROR_CODES:
            resource = str(item.get("resource") or "unknown")
            violations.append(
                f"{resource} sync still exceeds the remote payload limit"
            )

    return violations
