"""Read-only runtime-component health projections for the Dashboard API."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta


SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS = 420.0
COLLECTOR_HEARTBEAT_EXPECTED_SECONDS = 60.0
COLLECTOR_HEARTBEAT_FAILURE_SECONDS = 300.0
DECISION_OUTPUT_CADENCE_SECONDS = 300.0
DECISION_OUTPUT_STALLED_SECONDS = 420.0
DECISION_OUTPUT_GRACE_SECONDS = (
    DECISION_OUTPUT_STALLED_SECONDS - DECISION_OUTPUT_CADENCE_SECONDS
)
DECISION_HORIZON = timedelta(minutes=30)


def semantic_pipeline_component(latest, *, now: datetime) -> dict:
    if latest is None:
        return {
            "last_success": None,
            "age_seconds": None,
            "status": "STALE",
            "last_error": "尚无决策时点的新闻语义健康记录",
            "reason_codes": [],
            "actionable_failure_counts": {},
        }
    observed_at = datetime.fromisoformat(str(latest["observed_at"]))
    age_seconds = max(0.0, (now - observed_at).total_seconds())
    latest_keys = latest.keys()
    reason_codes = tuple(
        latest["reason_codes"]
        if "reason_codes" in latest_keys
        else json.loads(latest["reason_codes_json"])
    )
    freshness_failure = any(
        code.endswith("_STALE") or code.endswith("_MISSING")
        for code in reason_codes
        if code.startswith(("ANNOTATOR_HEARTBEAT_", "NEWS_COLLECTOR_POLL_"))
    )
    stale = age_seconds > SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS or freshness_failure
    pending_only = bool(reason_codes) and all(
        code.endswith(("_PENDING", "_RECOVERING"))
        for code in reason_codes
    )
    return {
        "last_success": latest["heartbeat_at"],
        "age_seconds": age_seconds,
        "status": (
            "STALE" if stale else
            "OK" if latest["status"] == "HEALTHY" else
            "WARN" if pending_only else "ERROR"
        ),
        "last_error": None if not reason_codes else ", ".join(reason_codes),
        "reason_codes": list(reason_codes),
        "actionable_failure_counts": (
            latest["actionable_failure_counts"]
            if "actionable_failure_counts" in latest_keys
            else json.loads(latest["actionable_failure_counts_json"] or "{}")
            if "actionable_failure_counts_json" in latest_keys else {}
        ),
    }


def materialized_semantic_health(
    connection: sqlite3.Connection, decision_id: str | None,
) -> dict | None:
    if not decision_id:
        return None
    row = connection.execute(
        """SELECT observed_at,status,reason_codes_json,heartbeat_at,
                  unresolved_items,oldest_unresolved_at,snapshot_hash
           FROM news_semantic_health_snapshots_v1
           WHERE source_decision_id=?""",
        (decision_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["reason_codes"] = json.loads(result.pop("reason_codes_json"))
    result["actionable_failure_counts"] = {}
    return result


def collector_component(
    heartbeat: dict[str, object],
    *,
    latest_poll: str | None,
    now: datetime,
) -> dict[str, object]:
    """Use the supervised process pulse; source cadence has separate health."""
    heartbeat_at = None
    try:
        heartbeat_at = datetime.fromisoformat(str(heartbeat["last_success"]))
    except (KeyError, TypeError, ValueError):
        pass
    age_seconds = (
        max(0.0, (now - heartbeat_at).total_seconds())
        if heartbeat_at is not None else None
    )
    lifecycle_state = str(heartbeat.get("state") or "")
    running = lifecycle_state == "RUNNING"
    starting = lifecycle_state == "STARTING"
    if age_seconds is None:
        status = "STALE"
    elif starting:
        status = (
            "WARN"
            if age_seconds <= COLLECTOR_HEARTBEAT_FAILURE_SECONDS
            else "STALE"
        )
    elif not running:
        status = "STALE"
    elif age_seconds <= COLLECTOR_HEARTBEAT_EXPECTED_SECONDS:
        status = "OK"
    elif age_seconds <= COLLECTOR_HEARTBEAT_FAILURE_SECONDS:
        status = "WARN"
    else:
        status = "STALE"
    if starting:
        lifecycle_error = (
            "采集器启动中" if status == "WARN" else "采集器启动心跳已过期"
        )
    elif running:
        lifecycle_error = str(heartbeat.get("last_error") or "") or None
    else:
        lifecycle_error = "采集器运行心跳不可用"
    latest_poll_at = None
    try:
        latest_poll_at = datetime.fromisoformat(str(latest_poll))
    except (TypeError, ValueError):
        pass
    return {
        "last_success": heartbeat_at.isoformat() if heartbeat_at else None,
        "age_seconds": age_seconds,
        "status": status,
        "last_error": lifecycle_error,
        "latest_source_poll": latest_poll,
        "source_poll_age_seconds": (
            max(0.0, (now - latest_poll_at).total_seconds())
            if latest_poll_at is not None else None
        ),
    }


def decision_collector_component(
    heartbeat: dict[str, object],
    *,
    latest_decision: str | None,
    decision_observation_start: str,
    broker_session: dict | None,
    quote_current: bool,
    now: datetime,
) -> dict[str, object]:
    """Separate supervised collector liveness from decision output cadence."""
    component = collector_component(heartbeat, latest_poll=None, now=now)
    component.pop("latest_source_poll", None)
    component.pop("source_poll_age_seconds", None)
    decision_at = None
    try:
        decision_at = datetime.fromisoformat(str(latest_decision))
    except (TypeError, ValueError):
        pass
    decision_age = (
        max(0.0, (now - decision_at).total_seconds())
        if decision_at is not None else None
    )
    observation_started_at = None
    try:
        observation_started_at = datetime.fromisoformat(
            str(decision_observation_start).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        pass
    output_age = decision_age
    if output_age is None and observation_started_at is not None:
        output_age = max(0.0, (now - observation_started_at).total_seconds())
    output_reference = decision_at or observation_started_at
    eligible_grid = None
    post_reopen_stall_after = None
    try:
        opened_at = datetime.fromisoformat(
            str(broker_session["opened_at"]).replace("Z", "+00:00")
        )
        first_quote_after_open = datetime.fromisoformat(
            str(broker_session["first_quote_after_open_at"]).replace(
                "Z", "+00:00"
            )
        )
    except (KeyError, TypeError, ValueError):
        opened_at = None
        first_quote_after_open = None
    if (
        output_reference is not None
        and opened_at is not None
        and first_quote_after_open is not None
        and output_reference <= opened_at <= first_quote_after_open <= now
    ):
        eligible_grid = first_quote_after_open.replace(second=0, microsecond=0)
        minute_remainder = eligible_grid.minute % 5
        if minute_remainder or eligible_grid < first_quote_after_open:
            eligible_grid += timedelta(minutes=5 - minute_remainder)
        post_reopen_stall_after = eligible_grid + timedelta(
            seconds=DECISION_OUTPUT_GRACE_SECONDS
        )
    output_status = (
        "CURRENT"
        if decision_age is not None
        and decision_age <= DECISION_OUTPUT_STALLED_SECONDS
        else "NO_RECENT_DECISION"
    )
    output_reason = None
    output_message = None
    if broker_session is not None and not broker_session["is_open"]:
        output_status = "MARKET_CLOSED"
    elif broker_session is not None:
        try:
            closes_at = datetime.fromisoformat(
                str(broker_session["next_close_time"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            closes_at = None
        if closes_at is not None and closes_at <= now + DECISION_HORIZON:
            output_status = "EXPECTED_PAUSE"
            output_reason = "FIXED_HORIZON_CROSSES_BROKER_CLOSE"
            output_message = "等待下一个完整 30 分钟决策窗口"
        elif (
            closes_at is not None
            and quote_current
            and output_age is not None
            and (
                now > post_reopen_stall_after
                if post_reopen_stall_after is not None
                else output_age > DECISION_OUTPUT_STALLED_SECONDS
            )
            and output_status == "NO_RECENT_DECISION"
        ):
            output_status = "STALLED"
            output_reason = "DECISION_OUTPUT_CADENCE_EXCEEDED"
            output_message = "决策输出已超过正常 5 分钟节奏"
    component.update({
        "latest_decision": decision_at.isoformat() if decision_at else None,
        "decision_age_seconds": decision_age,
        "decision_observation_started_at": (
            observation_started_at.isoformat() if observation_started_at else None
        ),
        "decision_output_age_seconds": output_age,
        "decision_output_eligible_grid": (
            eligible_grid.isoformat() if eligible_grid else None
        ),
        "decision_output_stall_after": (
            post_reopen_stall_after.isoformat()
            if post_reopen_stall_after else None
        ),
        "collector_state": str(heartbeat.get("state") or ""),
        "decision_output_status": output_status,
        "decision_output_reason": output_reason,
        "decision_output_message": output_message,
        "decision_output_expected_cadence_seconds": DECISION_OUTPUT_CADENCE_SECONDS,
        "decision_output_stalled_after_seconds": DECISION_OUTPUT_STALLED_SECONDS,
        "market_closes_at": (
            broker_session.get("next_close_time") if broker_session else None
        ),
    })
    return component
