from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from xauusd_forecaster.news_scheduler import enqueue_job, install_scheduler_schema
from xauusd_forecaster.operational_health import (
    extend_with_component_alerts,
    scheduler_health_snapshot,
)


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    install_scheduler_schema(connection)
    return connection


def test_scheduler_health_exposes_retry_capacity_stall_and_age_codes() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection,
        task_type="ACTIVE_IMPACT",
        source="source",
        source_item_id="stuck",
        revision_number=1,
        annotation_id="annotation",
        prompt_version="prompt",
        priority="BACKGROUND",
        now=NOW - timedelta(hours=1),
    )
    connection.execute(
        "UPDATE news_ai_jobs_v1 SET attempt_count=12 WHERE job_id=?",
        (job_id,),
    )
    for attempt in range(1, 12):
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,'DEFERRED','MODEL_CAPACITY_DEFERRED',NULL,NULL,
                'capacity',?,NULL)""",
            (
                f"attempt-{attempt}", job_id, attempt, f"account-{attempt}",
                f"credential-{attempt}",
                (NOW - timedelta(minutes=attempt)).isoformat(),
            ),
        )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)

    codes = {alert["code"] for alert in snapshot["alerts"]}
    assert snapshot["status"] == "ERROR"
    assert {
        "OPS_AI_JOB_RETRY_LOOP",
        "OPS_AI_ROUTE_CAPACITY_SATURATED",
        "OPS_AI_PIPELINE_STALLED",
        "OPS_AI_BACKLOG_OVERDUE",
    }.issubset(codes)
    impact = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_IMPACT"
    )
    assert impact["max_claim_count"] == 12
    assert impact["max_claim_is_claimable"] is True
    assert impact["deferred_15m"] == 11
    assert impact["all_deferred_15m"] == 11
    assert impact["capacity_deferred_15m"] == 11
    assert impact["oldest_age_seconds"] == 3600
    assert impact["failure_codes_15m"] == [
        {"code": "MODEL_CAPACITY_DEFERRED", "count": 11},
    ]


def test_scheduled_retry_loop_is_visible_without_claiming_current_impact() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection,
        task_type="ACTIVE_ANNOTATION",
        source="source",
        source_item_id="scheduled-repair",
        revision_number=1,
        prompt_version="prompt",
        priority="NORMAL",
        now=NOW - timedelta(hours=1),
    )
    next_retry = NOW + timedelta(minutes=5)
    connection.execute(
        """UPDATE news_ai_jobs_v1
           SET state='BACKING_OFF',attempt_count=10,available_at=?
           WHERE job_id=?""",
        (next_retry.isoformat(), job_id),
    )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)

    alert = next(
        item for item in snapshot["alerts"]
        if item["code"] == "OPS_AI_JOB_RETRY_LOOP"
    )
    annotation = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_ANNOTATION"
    )
    assert snapshot["status"] == "WARNING"
    assert alert["severity"] == "WARNING"
    assert alert["blocking"] is False
    assert alert["evidence"] == {
        "max_claim_count": 10,
        "job_ref": job_id[:12],
        "state": "BACKING_OFF",
        "claimable": False,
        "next_retry_at": next_retry.isoformat(),
    }
    assert annotation["claimable"] == 0
    assert annotation["scheduled_retry"] == 1


def test_embedding_maintenance_is_not_capacity_or_retry_failure() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection,
        task_type="ACTIVE_IMPACT",
        source="source",
        source_item_id="embedding-maintenance",
        revision_number=1,
        annotation_id="annotation",
        prompt_version="prompt",
        priority="FAST",
        now=NOW - timedelta(minutes=5),
    )
    connection.execute(
        "UPDATE news_ai_jobs_v1 SET attempt_count=14 WHERE job_id=?",
        (job_id,),
    )
    for attempt in range(1, 15):
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,'DEFERRED','NEWS_EMBEDDING_BACKFILL_PENDING',NULL,NULL,
                'maintenance',?,NULL)""",
            (
                f"maintenance-{attempt}", job_id, attempt, "embedding-account",
                "embedding-credential",
                (NOW - timedelta(seconds=attempt)).isoformat(),
            ),
        )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)

    codes = {alert["code"] for alert in snapshot["alerts"]}
    assert "OPS_AI_ROUTE_CAPACITY_SATURATED" not in codes
    assert "OPS_AI_JOB_RETRY_LOOP" not in codes
    impact = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_IMPACT"
    )
    assert impact["max_claim_count"] == 14
    assert impact["all_deferred_15m"] == 14
    assert impact["capacity_deferred_15m"] == 0
    assert impact["deferred_15m"] == 0


def test_scheduler_retirement_is_progress_without_becoming_completion() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection,
        task_type="TITLE_TRANSLATION",
        source="source",
        source_item_id="obsolete",
        revision_number=1,
        annotation_id="",
        prompt_version="prompt",
        priority="BACKGROUND",
        now=NOW - timedelta(hours=3),
    )
    connection.execute(
        """INSERT INTO news_ai_job_attempts_v1 VALUES
           (?,?,?,?,?,'NOT_CURRENT',NULL,NULL,NULL,NULL,?,NULL)""",
        (
            "retired-attempt", job_id, 1, "account", "credential",
            (NOW - timedelta(minutes=1)).isoformat(),
        ),
    )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)
    title = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "TITLE_TRANSLATION"
    )

    assert title["completed_15m"] == 0
    assert title["retired_15m"] == 1
    assert not any(
        alert["code"] == "OPS_AI_PIPELINE_STALLED"
        and alert["scope"] == "TITLE_TRANSLATION"
        for alert in snapshot["alerts"]
    )


def test_background_route_stalls_only_after_its_own_sla() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection,
        task_type="TITLE_TRANSLATION",
        source="source",
        source_item_id="waiting",
        revision_number=1,
        annotation_id="",
        prompt_version="prompt",
        priority="BACKGROUND",
        now=NOW - timedelta(hours=1),
    )

    within_sla = scheduler_health_snapshot(connection, now=NOW)
    assert not any(
        alert["code"] == "OPS_AI_PIPELINE_STALLED"
        and alert["scope"] == "TITLE_TRANSLATION"
        for alert in within_sla["alerts"]
    )

    connection.execute(
        """UPDATE news_ai_jobs_v1 SET created_at=?,available_at=?
           WHERE job_id=?""",
        (
            (NOW - timedelta(hours=3)).isoformat(),
            (NOW - timedelta(hours=3)).isoformat(),
            job_id,
        ),
    )
    connection.commit()
    overdue = scheduler_health_snapshot(connection, now=NOW)
    stalled = next(
        alert for alert in overdue["alerts"]
        if alert["code"] == "OPS_AI_PIPELINE_STALLED"
        and alert["scope"] == "TITLE_TRANSLATION"
    )
    assert stalled["evidence"]["stall_sla_seconds"] == 2 * 60 * 60


def test_component_and_source_failures_use_the_same_alert_contract() -> None:
    connection = _connection()
    snapshot = scheduler_health_snapshot(connection, now=NOW)

    result = extend_with_component_alerts(
        snapshot,
        components={
            "collector": {
                "status": "STALE", "age_seconds": 900,
                "last_error": "heartbeat expired",
            },
        },
        news_sources=[{
            "source": "feed", "label": "Feed", "health": "DEGRADED",
            "last_error_type": "HTTPError", "last_error": "rate limited",
        }],
        runtime_update_failure={
            "status": "ROLLED_BACK", "failed_at": NOW.isoformat(),
        },
        daily_news_brief={
            "phase": "UPDATING",
            "pending_since": (NOW - timedelta(hours=1)).isoformat(),
            "pending_items": 3,
        },
    )

    assert result["status"] == "ERROR"
    assert {alert["code"] for alert in result["alerts"]} == {
        "OPS_COMPONENT_UNHEALTHY",
        "OPS_NEWS_SOURCE_UNHEALTHY",
        "OPS_RUNTIME_UPDATE_FAILED",
        "OPS_DAILY_BRIEF_STALLED",
    }


def test_daily_brief_deferral_keeps_the_underlying_failure_code() -> None:
    connection = _connection()
    snapshot = scheduler_health_snapshot(connection, now=NOW)

    result = extend_with_component_alerts(
        snapshot,
        components={},
        news_sources=[],
        runtime_update_failure=None,
        daily_news_brief={
            "phase": "DEFERRED",
            "last_failure_code": "PROVIDER_DISPATCH_DEFERRED",
            "generation_failure_count": 4,
            "next_retry_at": (NOW + timedelta(minutes=1)).isoformat(),
            "last_failure_evidence": {
                "failure_stage": "DAILY_BRIEF_EVIDENCE_IDS",
                "selected_output": {"unknown_evidence_ids": ["invented:1"]},
            },
        },
    )

    alert = next(
        item for item in result["alerts"]
        if item["code"] == "OPS_DAILY_BRIEF_DEFERRED"
    )
    assert alert["evidence"]["failure_code"] == "PROVIDER_DISPATCH_DEFERRED"
    assert "自适应服务商调度器延后" in alert["message_zh"]
    assert alert["evidence"]["failure_count"] == 4
    assert alert["evidence"]["failure_evidence"]["selected_output"] == {
        "unknown_evidence_ids": ["invented:1"]
    }


def test_sync_resource_failures_keep_codes_and_promote_state_divergence() -> None:
    connection = _connection()
    snapshot = scheduler_health_snapshot(connection, now=NOW)

    result = extend_with_component_alerts(
        snapshot,
        components={
            "sites_synchronizer": {
                "status": "WARN", "age_seconds": 10,
                "last_error": "resource degraded",
            },
        },
        news_sources=[],
        runtime_update_failure=None,
        sync_degraded_resources=[{
            "target": "cloudflare",
            "resource": "news",
            "error_type": "RemoteInvariantViolation",
            "error_code": "NEWS_MIRROR_STATE_INVARIANT_VIOLATION",
            "error": "21 violations",
            "evidence": {
                "violation_count": 21,
                "checks": [{
                    "code": "NEWS_REVIEW_STATE_INVALID", "count": 21,
                }],
            },
        }, {
            "target": "cloudflare",
            "resource": "learning",
            "error_type": "HTTPError",
            "error_code": "RATE_LIMITED",
            "error": "HTTP 429",
        }],
    )

    assert result["status"] == "ERROR"
    alerts = {alert["code"]: alert for alert in result["alerts"]}
    assert set(alerts) == {
        "OPS_NEWS_MIRROR_STATE_DIVERGED", "OPS_SYNC_RESOURCE_FAILED",
    }
    assert alerts["OPS_NEWS_MIRROR_STATE_DIVERGED"]["blocking"] is True
    assert alerts["OPS_NEWS_MIRROR_STATE_DIVERGED"]["evidence"][
        "upstream_error_code"
    ] == "NEWS_MIRROR_STATE_INVARIANT_VIOLATION"
    assert alerts["OPS_NEWS_MIRROR_STATE_DIVERGED"]["evidence"]["details"] == {
        "violation_count": 21,
        "checks": [{"code": "NEWS_REVIEW_STATE_INVALID", "count": 21}],
    }
    assert alerts["OPS_SYNC_RESOURCE_FAILED"]["evidence"][
        "upstream_error_code"
    ] == "RATE_LIMITED"


def test_future_backoff_is_scheduled_retry_not_overdue_backlog() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection,
        task_type="ACTIVE_IMPACT",
        source="source",
        source_item_id="future-retry",
        revision_number=1,
        annotation_id="annotation",
        prompt_version="prompt",
        priority="BACKGROUND",
        now=NOW - timedelta(hours=10),
    )
    retry_at = NOW + timedelta(hours=6)
    connection.execute(
        """UPDATE news_ai_jobs_v1
           SET state='BACKING_OFF',available_at=?,attempt_count=2
           WHERE job_id=?""",
        (retry_at.isoformat(), job_id),
    )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)
    impact = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_IMPACT"
    )

    assert impact["backing_off"] == 1
    assert impact["claimable"] == 0
    assert impact["scheduled_retry"] == 1
    assert impact["earliest_retry_at"] == retry_at.isoformat()
    assert impact["oldest_age_seconds"] is None
    assert not any(
        alert["code"] in {
            "OPS_AI_BACKLOG_OVERDUE", "OPS_AI_PIPELINE_STALLED",
        }
        for alert in snapshot["alerts"]
    )


def test_scheduler_health_detects_unrepaired_display_placeholder() -> None:
    connection = _connection()
    connection.execute(
        """CREATE TABLE news_revisions (
             source TEXT,source_item_id TEXT,revision_number INTEGER,
             cluster_id TEXT,body TEXT)"""
    )
    connection.execute(
        """CREATE TABLE news_annotations (
             source TEXT,source_item_id TEXT,revision_number INTEGER,
             prompt_version TEXT,parsed_at TEXT,annotation_json TEXT)"""
    )
    connection.execute(
        "INSERT INTO news_annotations VALUES (?,?,?,?,?,?)",
        (
            "source", "item", 1, "prompt", NOW.isoformat(),
            json.dumps({
                "semantic_reason_zh": (
                    "语义已完成，但中文展示未通过校验；本记录仅供审计。"
                ),
                "xauusd_relevance": "MACRO_DRIVER",
            }, ensure_ascii=False),
        ),
    )
    connection.executemany(
        "INSERT INTO news_revisions VALUES (?,?,?,?,?)",
        [
            ("source", "item", 1, "actionable", "complete evidence"),
            ("source", "duplicate-short", 1, "duplicate", "short"),
            ("source", "duplicate-long", 1, "duplicate", "longer evidence"),
            ("source", "irrelevant", 1, "irrelevant", "complete evidence"),
        ],
    )
    placeholder = json.dumps({
        "semantic_reason_zh": (
            "语义已完成，但中文展示未通过校验；本记录仅供审计。"
        ),
        "xauusd_relevance": "MACRO_DRIVER",
    }, ensure_ascii=False)
    irrelevant = json.dumps({
        "semantic_reason_zh": (
            "语义已完成，但中文展示未通过校验；本记录仅供审计。"
        ),
        "xauusd_relevance": "IRRELEVANT",
    }, ensure_ascii=False)
    connection.executemany(
        "INSERT INTO news_annotations VALUES (?,?,?,?,?,?)",
        [
            ("source", "duplicate-short", 1, "prompt", NOW.isoformat(),
             placeholder),
            ("source", "irrelevant", 1, "prompt", NOW.isoformat(), irrelevant),
        ],
    )

    snapshot = scheduler_health_snapshot(connection, now=NOW)

    alert = next(
        item for item in snapshot["alerts"]
        if item["code"] == "OPS_NEWS_ANNOTATION_CONTRACT_STATE_INVALID"
    )
    assert alert["blocking"] is True
    assert alert["evidence"]["unrepaired_invalid_annotations"] == 1
