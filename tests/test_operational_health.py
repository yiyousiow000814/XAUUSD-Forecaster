from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from xauusd_forecaster.news_scheduler import (
    apply_retry_schedule_override,
    enqueue_job,
    install_scheduler_schema,
)
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
        "OPS_AI_ROUTE_CAPACITY_SATURATED",
        "OPS_AI_PIPELINE_STALLED",
        "OPS_AI_BACKLOG_OVERDUE",
    }.issubset(codes)
    assert "OPS_AI_JOB_RETRY_LOOP" not in codes
    impact = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_IMPACT"
    )
    assert impact["max_claim_count"] == 12
    assert impact["max_claim_is_claimable"] is True
    assert impact["deferred_15m"] == 11
    assert impact["all_deferred_15m"] == 11
    assert impact["capacity_deferred_15m"] == 11
    assert impact["provider_dispatch_deferred_15m"] == 0
    assert impact["capacity_dimensions_15m"] == []
    assert impact["oldest_age_seconds"] == 3600
    assert impact["failure_codes_15m"] == [
        {"code": "MODEL_CAPACITY_DEFERRED", "count": 11},
    ]


def test_scheduler_health_separates_local_limits_from_provider_pacing() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection,
        task_type="ACTIVE_IMPACT",
        source="source",
        source_item_id="capacity-evidence",
        revision_number=1,
        annotation_id="annotation",
        prompt_version="prompt",
        priority="FAST",
        now=NOW - timedelta(minutes=5),
    )
    connection.execute(
        """INSERT INTO news_ai_job_attempts_v1 VALUES
           (?,?,?,?,?,'DEFERRED','MODEL_CAPACITY_DEFERRED',NULL,NULL,?,?,NULL)""",
        (
            "capacity-attempt", job_id, 1, "account", "credential",
            json.dumps({
                "dimension": "TPM",
                "dimensions": ["TPM"],
                "current": 12_000,
                "requested": 6_000,
                "limit": 15_000,
            }),
            (NOW - timedelta(minutes=1)).isoformat(),
        ),
    )
    connection.execute(
        """INSERT INTO news_ai_scheduler_deferrals_v1
           (deferral_id,task_type,job_id,account_id,failure_code,
            next_retry_at,deferred_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            "pacing-deferral", "ACTIVE_IMPACT", job_id, "account",
            "PROVIDER_DISPATCH_DEFERRED",
            (NOW + timedelta(seconds=1)).isoformat(),
            (NOW - timedelta(seconds=30)).isoformat(),
        ),
    )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)
    impact = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_IMPACT"
    )

    assert impact["capacity_deferred_15m"] == 1
    assert impact["provider_dispatch_deferred_15m"] == 1
    assert impact["all_deferred_15m"] == 2
    assert impact["capacity_dimensions_15m"] == [
        {"dimension": "TPM", "count": 1},
    ]
    assert impact["failure_codes_15m"] == [
        {"code": "MODEL_CAPACITY_DEFERRED", "count": 1},
        {"code": "PROVIDER_DISPATCH_DEFERRED", "count": 1},
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
    for attempt in range(1, 11):
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,'ERROR','MODEL_OUTPUT_CONTRACT_FAILED','ValueError',NULL,
                'display invalid',?,?)""",
            (
                f"failure-{attempt}", job_id, attempt, "account", "credential",
                (NOW - timedelta(minutes=attempt)).isoformat(),
                next_retry.isoformat(),
            ),
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
        "lifetime_claim_count": 10,
        "effective_failure_streak": 10,
        "job_ref": job_id[:12],
        "state": "BACKING_OFF",
        "claimable": False,
        "next_retry_at": next_retry.isoformat(),
        "latest_failure_code": "MODEL_OUTPUT_CONTRACT_FAILED",
    }
    assert annotation["claimable"] == 0
    assert annotation["scheduled_retry"] == 1


def test_operator_advance_changes_claimability_without_claiming_health_success() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="operator-health", revision_number=1,
        prompt_version="prompt", priority="NORMAL", now=NOW - timedelta(hours=1),
    )
    automatic = (NOW + timedelta(hours=5)).isoformat()
    connection.execute(
        """UPDATE news_ai_jobs_v1 SET state='BACKING_OFF',attempt_count=10,
           available_at=?,last_error='MODEL_OUTPUT_CONTRACT_FAILED' WHERE job_id=?""",
        (automatic, job_id),
    )
    for attempt in range(1, 11):
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,'ERROR','MODEL_OUTPUT_CONTRACT_FAILED','ValueError',NULL,
                'display invalid',?,?)""",
            (
                f"operator-health-{attempt}", job_id, attempt, "account", "credential",
                (NOW - timedelta(minutes=attempt)).isoformat(), automatic,
            ),
        )
    connection.commit()
    before = scheduler_health_snapshot(connection, now=NOW)
    assert before["status"] == "WARNING"

    apply_retry_schedule_override(
        connection, request_id="operator-health", job_id=job_id,
        operator_id="cloudflare-access:owner", mode="IMMEDIATE",
        reason="repair deployed", expected_state="BACKING_OFF",
        expected_available_at=automatic, now=NOW,
    )
    after = scheduler_health_snapshot(connection, now=NOW)
    annotation = next(
        task for task in after["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_ANNOTATION"
    )
    assert after["status"] != "HEALTHY"
    assert annotation["claimable"] == 1
    assert annotation["scheduled_retry"] == 0
    assert connection.execute(
        "SELECT next_retry_at FROM news_ai_job_attempts_v1 WHERE job_id=? LIMIT 1",
        (job_id,),
    ).fetchone()[0] == automatic


def test_historical_capacity_debt_does_not_become_a_retry_loop() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="historical-capacity", revision_number=1,
        annotation_id="annotation", prompt_version="prompt",
        priority="BACKGROUND", now=NOW - timedelta(hours=6),
    )
    connection.execute(
        "UPDATE news_ai_jobs_v1 SET attempt_count=82 WHERE job_id=?", (job_id,),
    )
    for attempt in range(1, 81):
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,'DEFERRED','MODEL_CAPACITY_DEFERRED',NULL,NULL,
                'historical capacity',?,NULL)""",
            (
                f"capacity-{attempt}", job_id, attempt, "account", "credential",
                (NOW - timedelta(hours=2, seconds=attempt)).isoformat(),
            ),
        )
    for attempt in (81, 82):
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,'ERROR','PROVIDER_HTTP_ERROR','HTTPError',503,
                'HTTP 503',?,?)""",
            (
                f"http-{attempt}", job_id, attempt, "account", "credential",
                (NOW - timedelta(minutes=attempt - 80)).isoformat(),
                (NOW + timedelta(minutes=15)).isoformat(),
            ),
        )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)
    impact = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_IMPACT"
    )

    assert impact["max_claim_count"] == 82
    assert impact["max_effective_failure_streak"] == 2
    assert "OPS_AI_JOB_RETRY_LOOP" not in {
        alert["code"] for alert in snapshot["alerts"]
    }


def test_interleaved_deferrals_are_neutral_and_retirement_resets_failures() -> None:
    connection = _connection()
    job_id = enqueue_job(
        connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="interleaved-history", revision_number=1,
        annotation_id="annotation", prompt_version="prompt",
        priority="BACKGROUND", now=NOW - timedelta(hours=2),
    )
    attempts = (
        (1, "ERROR", "PROVIDER_HTTP_ERROR", "HTTPError", 503),
        (2, "ERROR", "MODEL_CAPACITY_DEFERRED",
         "ModelGatewayCapacityExhausted", None),
        (3, "DEFERRED", "PROVIDER_DISPATCH_DEFERRED", None, None),
        (4, "DEFERRED", "NEWS_EMBEDDING_COOLDOWN", None, None),
        (5, "ERROR", "MODEL_REQUEST_FAILED", "TimeoutError", None),
        (6, "ERROR", "SCHEDULER_MAINTENANCE_DEFERRED", "RuntimeError", None),
        (7, "NOT_CURRENT", None, None, None),
        (8, "ERROR", "MODEL_CAPACITY_DEFERRED",
         "ModelGatewayCapacityExhausted", None),
        (9, "ERROR", "PROVIDER_HTTP_ERROR", "HTTPError", 503),
        (10, "DEFERRED", "NEWS_EMBEDDING_BACKFILL_PENDING", None, None),
        (11, "ERROR", "MODEL_REQUEST_FAILED", "TimeoutError", None),
    )
    connection.execute(
        "UPDATE news_ai_jobs_v1 SET attempt_count=? WHERE job_id=?",
        (len(attempts), job_id),
    )
    for number, outcome, failure_code, error_type, http_status in attempts:
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"interleaved-{number}", job_id, number, "account", "credential",
                outcome, failure_code, error_type, http_status,
                "bounded evidence", (NOW - timedelta(minutes=number)).isoformat(),
                None,
            ),
        )
    connection.commit()

    snapshot = scheduler_health_snapshot(connection, now=NOW)
    impact = next(
        task for task in snapshot["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_IMPACT"
    )

    assert impact["max_claim_count"] == 11
    assert impact["max_effective_failure_streak"] == 2
    assert "OPS_AI_JOB_RETRY_LOOP" not in {
        alert["code"] for alert in snapshot["alerts"]
    }


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


def test_component_alert_preserves_structured_semantic_reason_codes() -> None:
    result = extend_with_component_alerts(
        scheduler_health_snapshot(_connection(), now=NOW),
        components={
            "news_semantic_pipeline": {
                "status": "ERROR", "age_seconds": 30, "last_error": "opaque",
                "reason_codes": [
                    "ACTIONABLE_NEWS_SEMANTICS_PENDING",
                    "ACTIONABLE_NEWS_IMPACT_PENDING",
                ],
                "actionable_failure_counts": {
                    "ACTIVE_ANNOTATION": {"MODEL_OUTPUT_CONTRACT_FAILED": 2},
                },
            },
        },
        news_sources=[], runtime_update_failure=None,
    )

    alert = next(
        item for item in result["alerts"]
        if item["code"] == "OPS_COMPONENT_UNHEALTHY"
    )
    assert alert["evidence"]["reason_codes"] == [
        "ACTIONABLE_NEWS_SEMANTICS_PENDING",
        "ACTIONABLE_NEWS_IMPACT_PENDING",
    ]
    assert alert["evidence"]["actionable_failure_counts"] == {
        "ACTIVE_ANNOTATION": {"MODEL_OUTPUT_CONTRACT_FAILED": 2},
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
