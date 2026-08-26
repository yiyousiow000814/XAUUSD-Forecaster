"""Decision-time health contract for action-bearing news semantics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from xauusd_forecaster.news.annotation.product import (
    PROMPT_VERSION,
    SUPPORTED_GEMINI_MODELS,
    pending_annotation_records,
)
from xauusd_forecaster.news.semantics.critical_state import RETIRED_ERROR
from xauusd_forecaster.evidence.ledger import canonical_hash
from xauusd_forecaster.news.collection.intake import NEWS_INTAKE_MAX_AGE
from xauusd_forecaster.news.semantics.evidence import annotation_is_actionable_candidate
from xauusd_forecaster.news.annotation.impact import IMPACT_MODEL, IMPACT_PROMPT_VERSION
from xauusd_forecaster.news.semantics.relevance import google_news_item_is_relevant
from xauusd_forecaster.news.retrieval.identity import preferred_cluster_peer_predicate
from xauusd_forecaster.news.scheduler.state import (
    LIVE_LANE,
    WORK_PROVENANCE_VERSION,
    configured_api_credentials,
    resolve_work_provenance,
)
from xauusd_forecaster.news.semantics.time import (
    assess_news_semantic_eligibility,
    category_time_rule,
    register_news_semantic_eligibility_sql,
    semantic_eligibility_sql_predicate,
)
from xauusd_forecaster.news.semantics.contracts import model_usable_annotation_predicate


ANNOTATOR_HEARTBEAT_MAX_AGE = timedelta(minutes=5)
ANNOTATION_DECISION_GRACE = timedelta(minutes=5)


def _instant(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _current_actionable_impact_rows(
    ledger, *, observed_at: datetime,
) -> list[dict[str, object]]:
    """Return recent model-eligible annotations awaiting current impact review."""
    model_placeholders = ",".join("?" for _ in SUPPORTED_GEMINI_MODELS)
    rows = ledger.connection.execute(
        f"""SELECT n.source,n.source_item_id,n.revision_number,n.headline,
                  n.source_published_time,n.collector_first_seen_time,
                  a.annotation_id,a.annotation_json,a.llm_model_version,a.parsed_at,
                  j.job_id,j.state AS job_state,j.created_at AS job_created_at,
                  j.available_at AS job_available_at,
                  COALESCE((
                    SELECT ja.failure_code FROM news_ai_job_attempts_v1 ja
                    WHERE ja.job_id=j.job_id AND ja.outcome='ERROR'
                    ORDER BY ja.attempt_number DESC,ja.attempted_at DESC LIMIT 1
                  ),'UNCLASSIFIED') AS failure_code
           FROM news_revisions n JOIN news_annotations a
             ON a.source=n.source AND a.source_item_id=n.source_item_id
            AND a.revision_number=n.revision_number
            AND a.raw_content_hash=n.content_hash
           LEFT JOIN news_ai_jobs_v1 j
             ON j.task_type='ACTIVE_IMPACT'
            AND j.annotation_id=a.annotation_id
            AND j.prompt_version=?
           WHERE a.prompt_version=?
             AND {model_usable_annotation_predicate('a')}
             AND a.llm_model_version IN ({model_placeholders})
             AND length(trim(COALESCE(n.body,'')))>=240
             AND NOT EXISTS (
               SELECT 1 FROM news_impact_assessments_v1 i
               WHERE i.annotation_id=a.annotation_id
                 AND i.llm_model_version=? AND i.prompt_version=?)
             AND NOT EXISTS (
               SELECT 1 FROM news_revisions newer
               WHERE newer.source=n.source
                 AND newer.source_item_id=n.source_item_id
                 AND newer.revision_number>n.revision_number)""",
        (
            IMPACT_PROMPT_VERSION, PROMPT_VERSION, *SUPPORTED_GEMINI_MODELS,
            IMPACT_MODEL, IMPACT_PROMPT_VERSION,
        ),
    ).fetchall()
    current: list[dict[str, object]] = []
    for raw in rows:
        row = dict(raw)
        provenance = resolve_work_provenance(
            ledger.connection,
            source=str(row["source"]),
            source_item_id=str(row["source_item_id"]),
            revision_number=int(row["revision_number"]),
            annotation_id=str(row["annotation_id"]),
            current_prompt_version=PROMPT_VERSION,
        )
        if provenance is None or provenance.work_lane != LIVE_LANE:
            continue
        annotation = json.loads(str(row.get("annotation_json") or "{}"))
        if not annotation_is_actionable_candidate(
            annotation, str(row.get("headline") or ""),
        ):
            continue
        published = _instant(row.get("source_published_time"))
        received = _instant(row.get("collector_first_seen_time"))
        parsed = _instant(row.get("parsed_at"))
        if published is None or received is None or parsed is None:
            continue
        max_age, _ = category_time_rule(str(annotation.get("primary_category") or ""))
        if (
            published > observed_at
            or received > observed_at
            or observed_at - published > min(max_age, NEWS_INTAKE_MAX_AGE)
        ):
            continue
        allowed, _ = google_news_item_is_relevant(
            str(row["source"]), str(row.get("headline") or ""),
            published, received,
        )
        if allowed:
            row["stage_ready_at"] = parsed
            current.append(row)
    return current


def _pending_operational_reason(
    task_type: str,
    row: dict[str, object],
    *,
    observed_at: datetime,
) -> str | None:
    """Classify operator action without weakening decision-time readiness."""
    from xauusd_forecaster.operational_health import TASK_QUEUE_SLA

    state = str(row.get("job_state") or "")
    prefix = (
        "ACTIONABLE_NEWS_SEMANTICS"
        if task_type == "ACTIVE_ANNOTATION"
        else "ACTIONABLE_NEWS_IMPACT"
    )
    if state == "DEAD_LETTER":
        return f"{prefix}_TERMINAL"
    available_at = _instant(row.get("job_available_at"))
    if state == "BACKING_OFF" and available_at is not None:
        if available_at > observed_at:
            return f"{prefix}_RECOVERING"
        if observed_at - available_at > TASK_QUEUE_SLA[task_type]:
            return f"{prefix}_OVERDUE"
    created_at = _instant(row.get("job_created_at"))
    if (
        state in {"QUEUED", "LEASED", "BACKING_OFF"}
        and created_at is not None
        and observed_at - created_at > TASK_QUEUE_SLA[task_type]
    ):
        return f"{prefix}_OVERDUE"
    return None


def news_semantic_pipeline_health(ledger, *, observed_at: datetime) -> dict[str, object]:
    """Inspect our real semantic pipeline; external provider status is advisory only."""
    reasons: list[str] = []
    heartbeat_at: datetime | None = None
    heartbeat_path = ledger.path.parent / "news-annotator-status.json"
    try:
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        heartbeat_at = _instant(heartbeat.get("last_success"))
        if heartbeat.get("service") != "annotator" or heartbeat.get("state") != "RUNNING":
            reasons.append("ANNOTATOR_NOT_RUNNING")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        heartbeat = {}
        reasons.append("ANNOTATOR_HEARTBEAT_MISSING")
    if heartbeat_at is not None and (
        heartbeat_at > observed_at + timedelta(minutes=1)
        or observed_at - heartbeat_at > ANNOTATOR_HEARTBEAT_MAX_AGE
    ):
        reasons.append("ANNOTATOR_HEARTBEAT_STALE")

    latest_poll = ledger.connection.execute(
        "SELECT max(fetched_time) FROM source_polls"
    ).fetchone()[0]
    latest_poll_at = _instant(latest_poll)
    if latest_poll_at is None:
        reasons.append("NEWS_COLLECTOR_POLL_MISSING")
    elif observed_at - latest_poll_at > ANNOTATOR_HEARTBEAT_MAX_AGE:
        reasons.append("NEWS_COLLECTOR_POLL_STALE")

    try:
        credentials = configured_api_credentials()
    except ValueError:
        credentials = ()
        reasons.append("MODEL_CREDENTIALS_INVALID")
    if not credentials:
        reasons.append("MODEL_CREDENTIALS_UNAVAILABLE")

    cutoff = observed_at - ANNOTATION_DECISION_GRACE
    intake_floor = observed_at - NEWS_INTAKE_MAX_AGE
    epoch_row = ledger.connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()
    if epoch_row is None:
        raise ValueError("FORWARD_EPOCH is missing")
    forward_epoch = datetime.fromisoformat(str(epoch_row[0]))
    register_news_semantic_eligibility_sql(ledger.connection)
    unresolved: dict[tuple[str, str, str, int, str], datetime] = {}
    actionable_failure_counts: dict[str, dict[str, int]] = {}
    operational_reasons: list[str] = []
    operational_reason_counts: dict[str, int] = {}
    annotation_pending = False
    live_annotation_origins = {
        (str(row[0]), str(row[1]), int(row[2]))
        for row in ledger.connection.execute(
            """SELECT source,source_item_id,revision_number
               FROM news_ai_jobs_v1
               WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?
                 AND work_lane=? AND lane_classified=1""",
            (PROMPT_VERSION, LIVE_LANE),
        )
    }

    def add_unresolved(
        task_type: str, row: dict[str, object], at: datetime,
        failure_code: str | None = None,
    ) -> None:
        key = (
            task_type, str(row.get("source") or ""),
            str(row.get("source_item_id") or ""),
            int(row.get("revision_number") or 0),
            str(row.get("annotation_id") or ""),
        )
        unresolved[key] = min(at, unresolved.get(key, at))
        if failure_code:
            task_counts = actionable_failure_counts.setdefault(task_type, {})
            task_counts[failure_code] = task_counts.get(failure_code, 0) + 1
    for row in pending_annotation_records(
        ledger.connection, observed_at=observed_at, limit=100_000,
        prompt_version=PROMPT_VERSION,
    ):
        origin = (
            str(row.get("source") or ""),
            str(row.get("source_item_id") or ""),
            int(row.get("revision_number") or 0),
        )
        if origin not in live_annotation_origins:
            continue
        received = _instant(row.get("collector_first_seen_time"))
        if received is not None and intake_floor <= received <= cutoff:
            annotation_pending = True
            add_unresolved("ACTIVE_ANNOTATION", row, received)

    failed_jobs = ledger.connection.execute(
        f"""SELECT j.created_at AS job_created_at,j.state AS job_state,
                  j.available_at AS job_available_at,
                  n.source,n.source_item_id,n.revision_number,
                  n.headline,n.source_published_time,n.collector_first_seen_time,
                  COALESCE((
                    SELECT a.failure_code FROM news_ai_job_attempts_v1 a
                    WHERE a.job_id=j.job_id AND a.outcome='ERROR'
                    ORDER BY a.attempt_number DESC,a.attempted_at DESC LIMIT 1
                  ),'UNCLASSIFIED') AS failure_code
        FROM news_ai_jobs_v1 j
        JOIN news_revisions n
          ON n.source=j.source AND n.source_item_id=j.source_item_id
         AND n.revision_number=j.revision_number
        WHERE j.task_type='ACTIVE_ANNOTATION' AND j.prompt_version=?
          AND j.work_lane=? AND j.lane_classified=1
          AND j.state IN ('BACKING_OFF','DEAD_LETTER')
          AND j.created_at>=? AND j.created_at<=?
          AND NOT EXISTS (
            SELECT 1 FROM news_revisions newer
            WHERE newer.source=n.source AND newer.source_item_id=n.source_item_id
              AND newer.revision_number>n.revision_number)
          AND NOT EXISTS (
            SELECT 1 FROM news_revisions peer
            WHERE peer.cluster_id=n.cluster_id
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions peer_newer
                WHERE peer_newer.source=peer.source
                  AND peer_newer.source_item_id=peer.source_item_id
                  AND peer_newer.revision_number>peer.revision_number)
              AND {semantic_eligibility_sql_predicate('peer')}
              AND {preferred_cluster_peer_predicate('peer', 'n')})""",
        (
            PROMPT_VERSION, LIVE_LANE,
            intake_floor.isoformat(), observed_at.isoformat(),
            forward_epoch.isoformat(),
        ),
    ).fetchall()
    for row in failed_jobs:
        received = _instant(row["collector_first_seen_time"])
        eligibility = assess_news_semantic_eligibility(
            row, forward_epoch=forward_epoch,
        )
        if eligibility.eligible and (
            created := _instant(row["job_created_at"])
        ) is not None:
            annotation_pending = True
            item = dict(row)
            add_unresolved("ACTIVE_ANNOTATION", item, created,
                           str(row["failure_code"]))
            if operational_reason := _pending_operational_reason(
                "ACTIVE_ANNOTATION", item, observed_at=observed_at,
            ):
                operational_reasons.append(operational_reason)
                operational_reason_counts[operational_reason] = (
                    operational_reason_counts.get(operational_reason, 0) + 1
                )

    impact_pending = False
    for row in _current_actionable_impact_rows(ledger, observed_at=observed_at):
        ready_at = row["stage_ready_at"]
        job_state = str(row.get("job_state") or "")
        failed = job_state in {"BACKING_OFF", "DEAD_LETTER"}
        if failed or ready_at <= cutoff:
            impact_pending = True
            add_unresolved(
                "ACTIVE_IMPACT", row, ready_at,
                str(row["failure_code"]) if failed else None,
            )
            if operational_reason := _pending_operational_reason(
                "ACTIVE_IMPACT", row, observed_at=observed_at,
            ):
                operational_reasons.append(operational_reason)
                operational_reason_counts[operational_reason] = (
                    operational_reason_counts.get(operational_reason, 0) + 1
                )
    if annotation_pending:
        reasons.append("ACTIONABLE_NEWS_SEMANTICS_PENDING")
    if impact_pending:
        reasons.append("ACTIONABLE_NEWS_IMPACT_PENDING")
    reasons.extend(operational_reasons)

    reason_codes = tuple(dict.fromkeys(reasons))
    payload = {
        "observed_at": observed_at.isoformat(),
        "status": "UNHEALTHY" if reason_codes else "HEALTHY",
        "reason_codes": reason_codes,
        "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
        "unresolved_items": len(unresolved),
        "unresolved_annotation_count": sum(
            task_type == "ACTIVE_ANNOTATION" for task_type, *_ in unresolved
        ),
        "unresolved_impact_count": sum(
            task_type == "ACTIVE_IMPACT" for task_type, *_ in unresolved
        ),
        "recovering_count": sum(
            count for code, count in operational_reason_counts.items()
            if code.endswith("_RECOVERING")
        ),
        "terminal_or_overdue_count": sum(
            count for code, count in operational_reason_counts.items()
            if code.endswith(("_TERMINAL", "_OVERDUE"))
        ),
        "oldest_unresolved_at": (
            min(unresolved.values()).isoformat() if unresolved else None
        ),
        "actionable_failure_counts": actionable_failure_counts,
    }
    return {**payload, "snapshot_hash": canonical_hash(payload)}


def news_semantic_pipeline_health_at(
    ledger, *, observed_at: datetime,
) -> dict[str, object]:
    """Project semantic readiness from durable evidence known by one instant.

    This historical path deliberately excludes the mutable annotator heartbeat,
    current credentials, and the job table's latest state.  Job creation,
    append-only attempts, and immutable semantic outputs are the only scheduler
    evidence that can be replayed without applying later state backwards. The
    authoritative provenance classification selects operational membership;
    it is classification metadata, not later execution evidence.
    """
    from xauusd_forecaster.operational_health import TASK_QUEUE_SLA

    cutoff = observed_at.isoformat()
    query_cutoff = observed_at.isoformat(timespec="microseconds")
    intake_floor = (observed_at - NEWS_INTAKE_MAX_AGE).isoformat(
        timespec="microseconds"
    )
    latest_poll = ledger.connection.execute(
        "SELECT max(fetched_time) FROM source_polls WHERE fetched_time<=?",
        (query_cutoff,),
    ).fetchone()[0]
    latest_poll_at = _instant(latest_poll)
    reasons: list[str] = []
    if latest_poll_at is None:
        reasons.append("NEWS_COLLECTOR_POLL_MISSING")
    elif observed_at - latest_poll_at > ANNOTATOR_HEARTBEAT_MAX_AGE:
        reasons.append("NEWS_COLLECTOR_POLL_STALE")

    jobs = ledger.connection.execute(
        """SELECT job_id,task_type,source,source_item_id,revision_number,
                  annotation_id,prompt_version,created_at,last_error,completed_at
           FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_ANNOTATION','ACTIVE_IMPACT')
             AND work_lane=? AND lane_classified=1
             AND (task_type='ACTIVE_ANNOTATION' OR
                  (provenance_resolved=1 AND provenance_version=?))
             AND created_at>=? AND created_at<=?
           ORDER BY created_at,job_id""",
        (LIVE_LANE, WORK_PROVENANCE_VERSION, intake_floor, query_cutoff),
    ).fetchall()
    unresolved: list[tuple[str, datetime]] = []
    operational_reason_counts: dict[str, int] = {}
    actionable_failure_counts: dict[str, dict[str, int]] = {}
    evidence: list[tuple[object, ...]] = []

    for raw in jobs:
        row = dict(raw)
        task_type = str(row["task_type"])
        if task_type == "ACTIVE_ANNOTATION":
            completed = ledger.connection.execute(
                """SELECT min(parsed_at) FROM news_annotations
                   WHERE source=? AND source_item_id=? AND revision_number=?
                     AND prompt_version=? AND parsed_at<=?""",
                (
                    row["source"], row["source_item_id"], row["revision_number"],
                    row["prompt_version"], query_cutoff,
                ),
            ).fetchone()[0]
        else:
            completed = ledger.connection.execute(
                """SELECT min(assessed_at) FROM news_impact_assessments_v1
                   WHERE annotation_id=? AND prompt_version=? AND assessed_at<=?""",
                (row["annotation_id"], row["prompt_version"], query_cutoff),
            ).fetchone()[0]
        attempt = ledger.connection.execute(
            """SELECT attempt_number,outcome,failure_code,provider_http_status,
                      attempted_at,next_retry_at
               FROM news_ai_job_attempts_v1
               WHERE job_id=? AND attempted_at<=?
               ORDER BY attempted_at DESC,attempt_number DESC,attempt_id DESC
               LIMIT 1""",
            (row["job_id"], query_cutoff),
        ).fetchone()
        attempt_values = dict(attempt) if attempt is not None else None
        deferral = ledger.connection.execute(
            """SELECT failure_code,deferred_at,next_retry_at
               FROM news_ai_scheduler_deferrals_v1
               WHERE job_id=? AND deferred_at<=?
               ORDER BY deferred_at DESC,deferral_id DESC LIMIT 1""",
            (row["job_id"], query_cutoff),
        ).fetchone()
        deferral_values = dict(deferral) if deferral is not None else None
        retired_at = _instant(row["completed_at"])
        retired = bool(
            str(row["last_error"] or "") == RETIRED_ERROR
            and retired_at is not None
            and retired_at <= observed_at
        )
        evidence.append((
            row["job_id"], task_type, row["created_at"], completed,
            RETIRED_ERROR if retired else None,
            retired_at.isoformat() if retired and retired_at else None,
            *(
                (
                    attempt_values["attempt_number"], attempt_values["outcome"],
                    attempt_values["failure_code"],
                    attempt_values["provider_http_status"],
                    attempt_values["attempted_at"], attempt_values["next_retry_at"],
                )
                if attempt_values is not None else (None,) * 6
            ),
            *(
                (
                    deferral_values["failure_code"],
                    deferral_values["deferred_at"],
                    deferral_values["next_retry_at"],
                )
                if deferral_values is not None else (None,) * 3
            ),
        ))
        if completed is not None or retired:
            continue

        created_at = _instant(row["created_at"])
        if created_at is None:
            continue
        unresolved.append((task_type, created_at))
        prefix = (
            "ACTIONABLE_NEWS_SEMANTICS"
            if task_type == "ACTIVE_ANNOTATION"
            else "ACTIONABLE_NEWS_IMPACT"
        )
        reason = None
        failure_code = ""
        transition = attempt_values
        if deferral_values is not None and (
            transition is None
            or str(deferral_values["deferred_at"]) > str(transition["attempted_at"])
        ):
            transition = {
                "outcome": "DEFERRED",
                "failure_code": deferral_values["failure_code"],
                "attempted_at": deferral_values["deferred_at"],
                "next_retry_at": deferral_values["next_retry_at"],
            }
        if transition is not None:
            failure_code = str(transition.get("failure_code") or "")
            if failure_code:
                counts = actionable_failure_counts.setdefault(task_type, {})
                counts[failure_code] = counts.get(failure_code, 0) + 1
            retry_at = _instant(transition.get("next_retry_at"))
            if retry_at is not None and retry_at > observed_at:
                reason = f"{prefix}_RECOVERING"
            elif retry_at is not None and observed_at - retry_at > TASK_QUEUE_SLA[task_type]:
                reason = f"{prefix}_OVERDUE"
            elif str(transition.get("outcome") or "") in {
                "ERROR", "DISABLED", "NOT_CURRENT",
            } and retry_at is None:
                reason = f"{prefix}_TERMINAL"
        elif observed_at - created_at > TASK_QUEUE_SLA[task_type]:
            reason = f"{prefix}_OVERDUE"
        if failure_code in {
            "GEMINI_API_KEY_MISSING", "MODEL_CREDENTIALS_INVALID",
            "MODEL_CREDENTIALS_UNAVAILABLE", "MODEL_ACCOUNTING_REQUIRED",
            "UNKNOWN_LLM_PROVIDER",
        }:
            reasons.append("MODEL_CREDENTIALS_UNAVAILABLE")
        if reason is not None:
            reasons.append(reason)
            operational_reason_counts[reason] = (
                operational_reason_counts.get(reason, 0) + 1
            )

    task_types = {task_type for task_type, _ in unresolved}
    if "ACTIVE_ANNOTATION" in task_types:
        reasons.append("ACTIONABLE_NEWS_SEMANTICS_PENDING")
    if "ACTIVE_IMPACT" in task_types:
        reasons.append("ACTIONABLE_NEWS_IMPACT_PENDING")
    reason_codes = tuple(dict.fromkeys(reasons))
    semantic_evidence_hash = canonical_hash({
        "evidence_cutoff": cutoff,
        "latest_source_poll_at": latest_poll,
        "jobs_and_attempts": evidence,
        "mutable_heartbeat_included": False,
        "current_credentials_included": False,
    })
    payload = {
        "observed_at": cutoff,
        "evidence_mode": "DURABLE_POINT_IN_TIME",
        "status": "UNHEALTHY" if reason_codes else "HEALTHY",
        "reason_codes": reason_codes,
        "heartbeat_at": None,
        "unresolved_items": len(unresolved),
        "unresolved_annotation_count": sum(
            task_type == "ACTIVE_ANNOTATION" for task_type, _ in unresolved
        ),
        "unresolved_impact_count": sum(
            task_type == "ACTIVE_IMPACT" for task_type, _ in unresolved
        ),
        "recovering_count": sum(
            count for code, count in operational_reason_counts.items()
            if code.endswith("_RECOVERING")
        ),
        "terminal_or_overdue_count": sum(
            count for code, count in operational_reason_counts.items()
            if code.endswith(("_TERMINAL", "_OVERDUE"))
        ),
        "oldest_unresolved_at": (
            min(at for _, at in unresolved).isoformat() if unresolved else None
        ),
        "actionable_failure_counts": actionable_failure_counts,
        "semantic_evidence_hash": semantic_evidence_hash,
    }
    return {**payload, "snapshot_hash": canonical_hash(payload)}
