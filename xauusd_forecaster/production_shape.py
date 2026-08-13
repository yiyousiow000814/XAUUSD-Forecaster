"""Cross-component production-shape checks for staged runtime releases."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .inference_v2 import MODEL_IDENTITIES
from .news_scheduler import quota_day
from .news_source_registry import MONITORED_NEWS_SOURCES


GEMINI_FAMILIES = ("gemini-3.5-flash-lite",)
GEMINI_FALLBACK_FAMILIES = ("gemini-3.1-flash-lite",)
GEMMA_FAMILIES = ("gemma-4-31b-it", "gemma-impact", "gemma-title")


def _daily_usage(
    connection: sqlite3.Connection, families: tuple[str, ...], now: datetime,
) -> int:
    placeholders = ",".join("?" for _ in families)
    row = connection.execute(
        f"""SELECT COALESCE(sum(request_count),0)
            FROM news_ai_account_daily_usage_v1
            WHERE quota_day=? AND model_family IN ({placeholders})""",
        (quota_day(now), *families),
    ).fetchone()
    return int(row[0])


def production_shape_violations(
    connection: sqlite3.Connection,
    status: dict,
    *,
    sync_status: dict | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Return durable contract violations without changing runtime state."""
    instant = now or datetime.now(UTC)
    violations: list[str] = []

    activation = connection.execute(
        """SELECT generation_id,activated_at
           FROM news_model_generation_activations_v1
           WHERE activated_at<? ORDER BY activated_at DESC,activation_id DESC LIMIT 1""",
        (instant.isoformat(),),
    ).fetchone()
    if activation is not None:
        generation_id, activated_at = str(activation[0]), str(activation[1])
        members = {
            str(row[0]) for row in connection.execute(
                """SELECT model_identity FROM news_model_generation_members_v1
                   WHERE generation_id=? UNION ALL
                   SELECT model_identity FROM news_model_generation_aux_members_v1
                   WHERE generation_id=?""",
                (generation_id, generation_id),
            )
        }
        missing_members = sorted(MODEL_IDENTITIES - members)
        if missing_members:
            violations.append(
                "active generation is incomplete: " + ", ".join(missing_members)
            )
        decision = connection.execute(
            """SELECT decision_id FROM decision_events WHERE decision_time>=?
               ORDER BY decision_time DESC LIMIT 1""",
            (activated_at,),
        ).fetchone()
        if decision is None:
            violations.append("active generation has no subsequent live decision")
        else:
            predicted = {
                str(row[0]) for row in connection.execute(
                    """SELECT model_identity FROM predictions_v2
                       WHERE source_decision_id=?""",
                    (str(decision[0]),),
                )
            }
            missing_predictions = sorted(MODEL_IDENTITIES - predicted)
            if missing_predictions:
                violations.append(
                    "latest decision is missing models: "
                    + ", ".join(missing_predictions)
                )

    scheduler_table = connection.execute(
        """SELECT 1 FROM sqlite_master WHERE type='table'
           AND name='news_ai_account_daily_usage_v1'"""
    ).fetchone()
    if scheduler_table is not None:
        for payload_key, families in (
            ("gemini_quota", GEMINI_FAMILIES),
            ("gemini_31_quota", GEMINI_FALLBACK_FAMILIES),
            ("gemma_quota", GEMMA_FAMILIES),
        ):
            expected = _daily_usage(connection, families, instant)
            quota = status.get(payload_key) if isinstance(status, dict) else None
            actual = quota.get("total_sent") if isinstance(quota, dict) else None
            source = quota.get("accounting_source") if isinstance(quota, dict) else None
            if source != "SCHEDULER_DB" or actual != expected:
                violations.append(
                    f"{payload_key} does not match scheduler usage: "
                    f"expected {expected}, got {actual} from {source or 'unknown'}"
                )

    source_rows = status.get("news_source_health", [])
    source_health = {
        str(row.get("source")): row
        for row in source_rows
        if isinstance(row, dict) and row.get("source")
    }
    for source in MONITORED_NEWS_SOURCES:
        latest_poll = connection.execute(
            """SELECT status FROM source_polls WHERE source=?
               ORDER BY fetched_time DESC,poll_id DESC LIMIT 1""",
            (source,),
        ).fetchone()
        if latest_poll is None or str(latest_poll[0]) != "OK":
            continue
        row = source_health.get(source)
        if not isinstance(row, dict) or (
            row.get("latest_status") != "OK"
            or row.get("health") in {"ERROR", "DEGRADED", "FALLBACK_ACTIVE"}
            or row.get("recovery_mode") is not None
            or row.get("next_retry_time") is not None
        ):
            violations.append(
                f"successful source poll is still reported as degraded: {source}"
            )

    system = status.get("system", {}) if isinstance(status, dict) else {}
    if system.get("market_session") in {"CLOSED", "WEEKLY_CLOSED"}:
        observed_at = system.get("market_session_observed_at")
        latest_decision = connection.execute(
            "SELECT max(decision_time) FROM decision_events"
        ).fetchone()[0]
        if observed_at and latest_decision and str(latest_decision) > str(observed_at):
            violations.append("decision was appended after broker-confirmed market close")

    for item in (sync_status or {}).get("degraded_resources", []):
        error = str(item.get("error") or "")
        if item.get("resource") == "market_history" and (
            "413" in error or "Payload Too Large" in error
        ):
            violations.append("market history sync still exceeds the remote payload limit")

    return violations
