"""Decision-time health contract for action-bearing news semantics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .annotation import PROMPT_VERSION, pending_annotation_records
from .forward_ledger import canonical_hash
from .news import NEWS_INTAKE_MAX_AGE
from .news_relevance import google_news_item_is_relevant
from .news_scheduler import configured_api_credentials


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
    unresolved_times: list[datetime] = []
    for row in pending_annotation_records(
        ledger.connection, observed_at=observed_at, limit=100_000,
        prompt_version=PROMPT_VERSION,
    ):
        received = _instant(row.get("collector_first_seen_time"))
        if received is not None and intake_floor <= received <= cutoff:
            unresolved_times.append(received)

    failed_jobs = ledger.connection.execute(
        """SELECT j.created_at,n.source,n.headline,n.source_published_time,
                  n.collector_first_seen_time
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
            unresolved_times.append(created)
    if unresolved_times:
        reasons.append("ACTIONABLE_NEWS_SEMANTICS_PENDING")

    reason_codes = tuple(dict.fromkeys(reasons))
    payload = {
        "observed_at": observed_at.isoformat(),
        "status": "UNHEALTHY" if reason_codes else "HEALTHY",
        "reason_codes": reason_codes,
        "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
        "unresolved_items": len(unresolved_times),
        "oldest_unresolved_at": (
            min(unresolved_times).isoformat() if unresolved_times else None
        ),
    }
    return {**payload, "snapshot_hash": canonical_hash(payload)}
