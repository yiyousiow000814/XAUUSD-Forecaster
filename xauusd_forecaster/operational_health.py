"""Stable operational error codes derived from durable runtime evidence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from .news_scheduler import TASKS
from .news_identity import preferred_cluster_peer_predicate
from .news_semantics import (
    display_repair_checkpoint_predicate,
    model_usable_annotation_predicate,
)


MONITOR_WINDOW = timedelta(minutes=15)
RETRY_LOOP_THRESHOLD = 10
CAPACITY_DEFERRED_THRESHOLD = 10
ERROR_COUNT_THRESHOLD = 10
TASK_QUEUE_SLA = {
    "ACTIVE_ANNOTATION": timedelta(minutes=15),
    "ACTIVE_IMPACT": timedelta(minutes=30),
    "TITLE_TRANSLATION": timedelta(hours=2),
}
TASK_LABELS = {
    "ACTIVE_ANNOTATION": "Gemini 语义复核",
    "ACTIVE_IMPACT": "Gemma 事件与影响复核",
    "TITLE_TRANSLATION": "中文标题展示",
}
SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}


def _instant(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _alert(
    code: str,
    *,
    severity: str,
    scope: str,
    message_zh: str,
    evidence: dict[str, object],
    blocking: bool = False,
) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity,
        "scope": scope,
        "message_zh": message_zh,
        "blocking": blocking,
        "evidence": evidence,
    }


def scheduler_health_snapshot(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Describe scheduler progress, pressure and anomalies by task route."""
    instant = now or datetime.now(UTC)
    cutoff = (instant - MONITOR_WINDOW).isoformat(timespec="microseconds")
    summaries = {
        task: {
            "task_type": task,
            "queued": 0,
            "leased": 0,
            "backing_off": 0,
            "dead_letter": 0,
            "completed_15m": 0,
            "retired_15m": 0,
            "deferred_15m": 0,
            "errors_15m": 0,
            "failure_codes_15m": {},
            "claimable": 0,
            "scheduled_retry": 0,
            "earliest_retry_at": None,
            "oldest_active_at": None,
            "oldest_age_seconds": None,
            "max_claim_count": 0,
            "max_claim_job_ref": None,
            "max_claim_state": None,
            "max_claim_is_claimable": False,
            "max_claim_next_retry_at": None,
        }
        for task in TASKS
    }
    state_rows = connection.execute(
        """SELECT task_type,state,count(*) AS total
           FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_ANNOTATION','ACTIVE_IMPACT',
                               'TITLE_TRANSLATION')
           GROUP BY task_type,state"""
    ).fetchall()
    for row in state_rows:
        task = str(row["task_type"])
        state = str(row["state"]).lower()
        if task in summaries and state in summaries[task]:
            summaries[task][state] = int(row["total"])

    instant_iso = instant.isoformat(timespec="microseconds")
    active_rows = connection.execute(
        """SELECT task_type,
                  sum(CASE WHEN state='LEASED' OR available_at<=? THEN 1 ELSE 0 END)
                    AS claimable,
                  sum(CASE WHEN state IN ('QUEUED','BACKING_OFF')
                                AND available_at>? THEN 1 ELSE 0 END)
                    AS scheduled_retry,
                  min(CASE WHEN state IN ('QUEUED','BACKING_OFF')
                                AND available_at>? THEN available_at END)
                    AS earliest_retry_at,
                  min(CASE WHEN state='LEASED' OR available_at<=?
                           THEN CASE WHEN available_at>created_at
                                     THEN available_at ELSE created_at END END)
                    AS oldest_active_at,
                  max(attempt_count) AS max_claim_count
           FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_ANNOTATION','ACTIVE_IMPACT',
                               'TITLE_TRANSLATION')
             AND state IN ('QUEUED','LEASED','BACKING_OFF')
           GROUP BY task_type""",
        (instant_iso, instant_iso, instant_iso, instant_iso),
    ).fetchall()
    for row in active_rows:
        task = str(row["task_type"])
        summary = summaries[task]
        summary["claimable"] = int(row["claimable"] or 0)
        summary["scheduled_retry"] = int(row["scheduled_retry"] or 0)
        summary["earliest_retry_at"] = row["earliest_retry_at"]
        oldest = _instant(row["oldest_active_at"])
        summary["oldest_active_at"] = (
            oldest.isoformat() if oldest is not None else None
        )
        summary["oldest_age_seconds"] = (
            max(0, int((instant - oldest).total_seconds()))
            if oldest is not None else None
        )
        summary["max_claim_count"] = int(row["max_claim_count"] or 0)
        top = connection.execute(
            """SELECT job_id,state,available_at FROM news_ai_jobs_v1
               WHERE task_type=? AND state IN ('QUEUED','LEASED','BACKING_OFF')
               ORDER BY attempt_count DESC,created_at,job_id LIMIT 1""",
            (task,),
        ).fetchone()
        summary["max_claim_job_ref"] = str(top["job_id"])[:12] if top else None
        if top:
            state = str(top["state"])
            available_at = str(top["available_at"])
            is_claimable = state == "LEASED" or available_at <= instant_iso
            summary["max_claim_state"] = state
            summary["max_claim_is_claimable"] = is_claimable
            summary["max_claim_next_retry_at"] = (
                None if is_claimable else available_at
            )

    outcome_rows = connection.execute(
        """SELECT j.task_type,a.outcome,a.failure_code,count(*) AS total
           FROM news_ai_job_attempts_v1 a
           JOIN news_ai_jobs_v1 j ON j.job_id=a.job_id
           WHERE a.attempted_at>=?
             AND j.task_type IN ('ACTIVE_ANNOTATION','ACTIVE_IMPACT',
                                 'TITLE_TRANSLATION')
           GROUP BY j.task_type,a.outcome,a.failure_code""",
        (cutoff,),
    ).fetchall()
    outcome_fields = {
        "OK": "completed_15m",
        "NOT_CURRENT": "retired_15m",
        "DEFERRED": "deferred_15m",
        "DISABLED": "deferred_15m",
        "ERROR": "errors_15m",
    }
    for row in outcome_rows:
        summary = summaries[str(row["task_type"])]
        field = outcome_fields.get(str(row["outcome"]))
        if field:
            summary[field] += int(row["total"])
        code = str(row["failure_code"] or "").strip()
        if code:
            codes = summary["failure_codes_15m"]
            codes[code] = int(codes.get(code, 0)) + int(row["total"])

    recent_dead_letters = {
        str(row["task_type"]): int(row["total"])
        for row in connection.execute(
            """SELECT task_type,count(*) AS total FROM news_ai_jobs_v1
               WHERE task_type IN ('ACTIVE_ANNOTATION','ACTIVE_IMPACT',
                                   'TITLE_TRANSLATION')
                 AND state='DEAD_LETTER' AND updated_at>=?
                 AND COALESCE(last_error,'')<>'CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE'
               GROUP BY task_type""",
            (cutoff,),
        ).fetchall()
    }

    alerts: list[dict[str, object]] = []
    for task, summary in summaries.items():
        label = TASK_LABELS[task]
        active = int(summary["claimable"])
        completed = int(summary["completed_15m"])
        retired = int(summary["retired_15m"])
        progressed = completed + retired
        deferred = int(summary["deferred_15m"])
        errors = int(summary["errors_15m"])
        max_claims = int(summary["max_claim_count"])
        max_claim_is_claimable = bool(summary["max_claim_is_claimable"])
        oldest_age = int(summary["oldest_age_seconds"] or 0)

        if max_claims >= RETRY_LOOP_THRESHOLD:
            scheduled = not max_claim_is_claimable
            alerts.append(_alert(
                "OPS_AI_JOB_RETRY_LOOP",
                severity="WARNING" if scheduled else "ERROR", scope=task,
                message_zh=(
                    f"{label}（{task}）有任务已领取 {max_claims} 次，"
                    + (
                        "目前按计划等待下次重试。"
                        if scheduled else "当前仍可处理，需要检查。"
                    )
                ),
                blocking=not scheduled,
                evidence={
                    "max_claim_count": max_claims,
                    "job_ref": summary["max_claim_job_ref"],
                    "state": summary["max_claim_state"],
                    "claimable": max_claim_is_claimable,
                    "next_retry_at": summary["max_claim_next_retry_at"],
                },
            ))
        if deferred >= CAPACITY_DEFERRED_THRESHOLD and deferred > completed:
            alerts.append(_alert(
                "OPS_AI_ROUTE_CAPACITY_SATURATED",
                severity="WARNING", scope=task,
                message_zh=(
                    f"{label} 最近15分钟容量延后 {deferred} 次，"
                    f"完成 {completed} 次。"
                ),
                evidence={
                    "deferred_15m": deferred,
                    "completed_15m": completed,
                },
            ))
        stall_sla = int(TASK_QUEUE_SLA[task].total_seconds())
        if active and progressed == 0 and oldest_age >= stall_sla:
            alerts.append(_alert(
                "OPS_AI_PIPELINE_STALLED",
                severity="ERROR", scope=task,
                message_zh=(
                    f"{label} 有 {active} 条待处理，但15分钟没有完成任务。"
                ),
                blocking=True,
                evidence={
                    "active_jobs": active,
                    "oldest_age_seconds": oldest_age,
                    "stall_sla_seconds": stall_sla,
                },
            ))
        if active and oldest_age >= int(TASK_QUEUE_SLA[task].total_seconds()):
            alerts.append(_alert(
                "OPS_AI_BACKLOG_OVERDUE",
                severity="WARNING", scope=task,
                message_zh=f"{label} 最旧任务已等待 {oldest_age // 60} 分钟。",
                evidence={
                    "active_jobs": active,
                    "oldest_age_seconds": oldest_age,
                    "sla_seconds": int(TASK_QUEUE_SLA[task].total_seconds()),
                },
            ))
        if errors >= ERROR_COUNT_THRESHOLD and errors * 4 > max(1, completed):
            alerts.append(_alert(
                "OPS_AI_FAILURE_RATE_HIGH",
                severity="WARNING", scope=task,
                message_zh=f"{label} 最近15分钟出现 {errors} 次模型或校验失败。",
                evidence={"errors_15m": errors, "completed_15m": completed},
            ))
        if recent_dead_letters.get(task, 0):
            alerts.append(_alert(
                "OPS_AI_NEW_DEAD_LETTER",
                severity="WARNING", scope=task,
                message_zh=(
                    f"{label} 最近15分钟新增 "
                    f"{recent_dead_letters[task]} 条隔离任务。"
                ),
                evidence={"new_dead_letters_15m": recent_dead_letters[task]},
            ))

        summary["failure_codes_15m"] = [
            {"code": code, "count": count}
            for code, count in sorted(
                summary["failure_codes_15m"].items(),
                key=lambda item: (-item[1], item[0]),
            )[:8]
        ]

    has_annotations = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_annotations'"
    ).fetchone() is not None
    invalid_display_rows = int(connection.execute(
        f"""SELECT count(*) FROM news_annotations fallback
            JOIN news_revisions current
              ON current.source=fallback.source
             AND current.source_item_id=fallback.source_item_id
             AND current.revision_number=fallback.revision_number
            WHERE {display_repair_checkpoint_predicate('fallback')}
              AND COALESCE(json_extract(
                    fallback.annotation_json,'$.xauusd_relevance'),'IRRELEVANT')
                    <> 'IRRELEVANT'
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions newer
                WHERE newer.source=current.source
                  AND newer.source_item_id=current.source_item_id
                  AND newer.revision_number>current.revision_number)
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions peer
                WHERE peer.cluster_id=current.cluster_id
                  AND NOT EXISTS (
                    SELECT 1 FROM news_revisions peer_newer
                    WHERE peer_newer.source=peer.source
                      AND peer_newer.source_item_id=peer.source_item_id
                      AND peer_newer.revision_number>peer.revision_number)
                  AND {preferred_cluster_peer_predicate('peer', 'current')})
              AND NOT EXISTS (
                SELECT 1 FROM news_annotations repaired
                WHERE repaired.source=fallback.source
                  AND repaired.source_item_id=fallback.source_item_id
                  AND repaired.revision_number=fallback.revision_number
                  AND repaired.prompt_version=fallback.prompt_version
                  AND repaired.parsed_at>fallback.parsed_at
                  AND {model_usable_annotation_predicate('repaired')})""",
    ).fetchone()[0]) if has_annotations else 0
    if invalid_display_rows:
        alerts.append(_alert(
            "OPS_NEWS_ANNOTATION_CONTRACT_STATE_INVALID",
            severity="ERROR", scope="ACTIVE_ANNOTATION",
            message_zh=(
                f"有 {invalid_display_rows} 条中文展示校验失败记录被旧版本错误标记为完成。"
            ),
            blocking=True,
            evidence={"unrepaired_invalid_annotations": invalid_display_rows},
        ))

    alerts.sort(key=lambda item: (
        SEVERITY_ORDER[str(item["severity"])], str(item["code"]),
        str(item["scope"]),
    ))
    status = (
        "ERROR" if any(item["severity"] == "ERROR" for item in alerts)
        else "WARNING" if alerts else "HEALTHY"
    )
    return {
        "schema_version": "operational-health.v1",
        "observed_at": instant.isoformat(),
        "window_seconds": int(MONITOR_WINDOW.total_seconds()),
        "status": status,
        "alerts": alerts,
        "scheduler": {
            "status": status,
            "tasks": list(summaries.values()),
        },
    }


def extend_with_component_alerts(
    snapshot: dict[str, object],
    *,
    components: dict[str, dict[str, object]],
    news_sources: list[dict[str, object]],
    runtime_update_failure: dict[str, object] | None,
    daily_news_brief: dict[str, object] | None = None,
    sync_degraded_resources: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Cover every published component/source with the same alert contract."""
    alerts = list(snapshot.get("alerts") or [])
    sync_degraded_resources = sync_degraded_resources or []
    for name, component in components.items():
        status = str(component.get("status") or "UNKNOWN")
        if (
            status not in {"OK", "MARKET_CLOSED"}
            and not (name == "sites_synchronizer" and sync_degraded_resources)
        ):
            alerts.append(_alert(
                "OPS_COMPONENT_UNHEALTHY",
                severity="ERROR" if status in {"ERROR", "STALE"} else "WARNING",
                scope=name,
                message_zh=f"组件 {name} 当前状态为 {status}。",
                blocking=status in {"ERROR", "STALE"},
                evidence={
                    "status": status,
                    "age_seconds": component.get("age_seconds"),
                    "last_error": component.get("last_error"),
                },
            ))
    for source in news_sources:
        health = str(source.get("health") or "UNKNOWN")
        if health not in {"HEALTHY", "WARMING_UP"}:
            alerts.append(_alert(
                "OPS_NEWS_SOURCE_UNHEALTHY",
                severity="ERROR" if health in {"ERROR", "STALE"} else "WARNING",
                scope=str(source.get("source") or "unknown"),
                message_zh=(
                    f"新闻来源 {source.get('label') or source.get('source')} "
                    f"当前状态为 {health}。"
                ),
                blocking=health in {"ERROR", "STALE"},
                evidence={
                    "health": health,
                    "last_error_type": source.get("last_error_type"),
                    "last_error": source.get("last_error"),
                },
            ))
    if runtime_update_failure is not None:
        alerts.append(_alert(
            "OPS_RUNTIME_UPDATE_FAILED",
            severity="ERROR", scope="runtime_update",
            message_zh="运行版本更新失败，系统已保留或恢复上一版本。",
            blocking=True, evidence=dict(runtime_update_failure),
        ))
    if daily_news_brief is not None:
        phase = str(daily_news_brief.get("phase") or "UNKNOWN")
        failure_code = str(
            daily_news_brief.get("last_failure_code") or ""
        ).strip()
        pending_since = _instant(daily_news_brief.get("pending_since"))
        observed_at = _instant(snapshot.get("observed_at")) or datetime.now(UTC)
        pending_age = (
            max(0, int((observed_at - pending_since).total_seconds()))
            if pending_since is not None else None
        )
        if phase == "DEFERRED" or failure_code:
            alerts.append(_alert(
                "OPS_DAILY_BRIEF_DEFERRED",
                severity="WARNING", scope="daily_news_brief",
                message_zh=(
                    "每日简报生成已延后。"
                    + (f"原因码：{failure_code}。" if failure_code else "")
                ),
                evidence={
                    "phase": phase,
                    "failure_code": failure_code or None,
                    "failure_count": daily_news_brief.get(
                        "generation_failure_count"
                    ),
                    "next_retry_at": daily_news_brief.get("next_retry_at"),
                    "failure_evidence": daily_news_brief.get(
                        "last_failure_evidence"
                    ),
                },
            ))
        if phase == "UPDATING" and pending_age is not None and pending_age >= 1800:
            alerts.append(_alert(
                "OPS_DAILY_BRIEF_STALLED",
                severity="ERROR", scope="daily_news_brief",
                message_zh="每日简报有待生成内容，但30分钟没有完成。",
                blocking=True,
                evidence={
                    "phase": phase,
                    "pending_age_seconds": pending_age,
                    "pending_items": daily_news_brief.get("pending_items"),
                },
            ))
        if phase == "DEGRADED":
            alerts.append(_alert(
                "OPS_DAILY_BRIEF_DEGRADED",
                severity="WARNING", scope="daily_news_brief",
                message_zh="每日简报已生成，但包含未完成复核的内容。",
                evidence={
                    "phase": phase,
                    "terminal_failure_items": daily_news_brief.get(
                        "terminal_failure_items"
                    ),
                },
            ))
    for resource in sync_degraded_resources:
        target = str(resource.get("target") or "unknown")
        name = str(resource.get("resource") or "unknown")
        upstream_code = str(resource.get("error_code") or "UNCLASSIFIED")
        mirror_diverged = upstream_code in {
            "NEWS_MIRROR_STATE_INVARIANT_VIOLATION",
            "NEWS_MIRROR_HEALTH_UNAVAILABLE",
        }
        alerts.append(_alert(
            (
                "OPS_NEWS_MIRROR_STATE_DIVERGED"
                if mirror_diverged else "OPS_SYNC_RESOURCE_FAILED"
            ),
            severity=(
                "ERROR" if mirror_diverged or name == "heartbeat" else "WARNING"
            ),
            scope=f"{target}:{name}",
            message_zh=(
                "公开新闻镜像与预期状态不一致，已停止把本轮同步视为健康。"
                if mirror_diverged else f"同步资源 {target}/{name} 本轮失败。"
            ),
            blocking=mirror_diverged or name == "heartbeat",
            evidence={
                "target": target,
                "resource": name,
                "upstream_error_code": upstream_code,
                "error_type": resource.get("error_type"),
                "error": resource.get("error"),
                "details": resource.get("evidence"),
            },
        ))
    alerts.sort(key=lambda item: (
        SEVERITY_ORDER[str(item["severity"])], str(item["code"]),
        str(item["scope"]),
    ))
    status = (
        "ERROR" if any(item["severity"] == "ERROR" for item in alerts)
        else "WARNING" if alerts else "HEALTHY"
    )
    return {**snapshot, "status": status, "alerts": alerts[:50]}
