"""Decision-time health contract for action-bearing news semantics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .annotation import (
    PROMPT_VERSION,
    SUPPORTED_GEMINI_MODELS,
    pending_annotation_records,
)
from .forward_ledger import canonical_hash
from .news import NEWS_INTAKE_MAX_AGE
from .news_evidence import annotation_is_actionable_candidate
from .news_impact import IMPACT_MODEL, IMPACT_PROMPT_VERSION
from .news_relevance import google_news_item_is_relevant
from .news_scheduler import configured_api_credentials
from .news_time import category_time_rule
from .news_semantics import validated_annotation_predicate


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
             AND {validated_annotation_predicate('a')}
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
    unresolved: dict[tuple[str, str, str, int, str], datetime] = {}
    actionable_failure_counts: dict[str, dict[str, int]] = {}
    annotation_pending = False

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
        received = _instant(row.get("collector_first_seen_time"))
        if received is not None and intake_floor <= received <= cutoff:
            annotation_pending = True
            add_unresolved("ACTIVE_ANNOTATION", row, received)

    failed_jobs = ledger.connection.execute(
        """SELECT j.created_at,n.source,n.source_item_id,n.revision_number,
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
              AND (length(COALESCE(peer.body,''))>length(COALESCE(n.body,''))
                OR (length(COALESCE(peer.body,''))=length(COALESCE(n.body,''))
                  AND peer.source_item_id<n.source_item_id)))""",
        (PROMPT_VERSION, intake_floor.isoformat(), observed_at.isoformat()),
    ).fetchall()
    for row in failed_jobs:
        received = _instant(row["collector_first_seen_time"])
        published = _instant(row["source_published_time"])
        allowed, _ = google_news_item_is_relevant(
            str(row["source"]), str(row["headline"] or ""), published,
            observed_at,
        )
        if allowed and (created := _instant(row["created_at"])) is not None:
            annotation_pending = True
            add_unresolved(
                "ACTIVE_ANNOTATION", dict(row), created,
                str(row["failure_code"]),
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
    if annotation_pending:
        reasons.append("ACTIONABLE_NEWS_SEMANTICS_PENDING")
    if impact_pending:
        reasons.append("ACTIONABLE_NEWS_IMPACT_PENDING")

    reason_codes = tuple(dict.fromkeys(reasons))
    payload = {
        "observed_at": observed_at.isoformat(),
        "status": "UNHEALTHY" if reason_codes else "HEALTHY",
        "reason_codes": reason_codes,
        "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
        "unresolved_items": len(unresolved),
        "oldest_unresolved_at": (
            min(unresolved.values()).isoformat() if unresolved else None
        ),
        "actionable_failure_counts": actionable_failure_counts,
    }
    return {**payload, "snapshot_hash": canonical_hash(payload)}
