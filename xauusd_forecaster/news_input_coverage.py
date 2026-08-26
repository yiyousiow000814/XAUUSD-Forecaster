"""Frozen decision-time news input coverage, separate from operations health."""

from __future__ import annotations

from datetime import datetime

from xauusd_forecaster.evidence.ledger import canonical_hash
from .news_source_registry import NEWS_SOURCE_REGISTRY


NEWS_INPUT_STATES = frozenset({
    "AVAILABLE", "DEGRADED", "QUIET", "UNAVAILABLE",
})
NEWS_OBSERVATION_OUTAGE_REASONS = frozenset({
    "ANNOTATOR_HEARTBEAT_MISSING",
    "ANNOTATOR_HEARTBEAT_STALE",
    "ANNOTATOR_NOT_RUNNING",
    "MODEL_CREDENTIALS_INVALID",
    "MODEL_CREDENTIALS_UNAVAILABLE",
    "NEWS_COLLECTOR_POLL_MISSING",
    "NEWS_COLLECTOR_POLL_STALE",
})


def _instant(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def news_source_observability_summary(
    connection, *, observed_at: datetime,
) -> dict[str, object]:
    """Summarize the registered source freshness contract at one instant."""
    current: list[str] = []
    degraded: list[str] = []
    unavailable: list[str] = []
    evidence: list[
        tuple[str, str | None, str | None, str | None, str | None]
    ] = []
    cutoff = observed_at.isoformat(timespec="microseconds")
    for spec in NEWS_SOURCE_REGISTRY:
        latest = connection.execute(
            """SELECT fetched_time,status FROM source_polls
               WHERE source=? AND fetched_time<=?
               ORDER BY fetched_time DESC,poll_id DESC LIMIT 1""",
            (spec.source, cutoff),
        ).fetchone()
        usable = connection.execute(
            """SELECT fetched_time,status FROM source_polls
               WHERE source=? AND fetched_time<=? AND status IN ('OK','PARTIAL')
               ORDER BY fetched_time DESC,poll_id DESC LIMIT 1""",
            (spec.source, cutoff),
        ).fetchone()
        latest_status = str(latest["status"]) if latest is not None else None
        usable_at = _instant(usable["fetched_time"] if usable is not None else None)
        usable_status = str(usable["status"]) if usable is not None else None
        fresh = bool(
            usable_at is not None
            and usable_at <= observed_at
            and (observed_at - usable_at).total_seconds() <= spec.stale_minutes * 60
        )
        if fresh:
            current.append(spec.source)
            if latest_status != "OK" or usable_status != "OK":
                degraded.append(spec.source)
        else:
            unavailable.append(spec.source)
        evidence.append((
            spec.source,
            latest["fetched_time"] if latest is not None else None,
            latest_status,
            usable["fetched_time"] if usable is not None else None,
            usable_status,
        ))
    payload = {
        "evidence_cutoff": observed_at.isoformat(),
        "registered_source_count": len(NEWS_SOURCE_REGISTRY),
        "observable_source_count": len(current),
        "degraded_source_count": len(degraded),
        "unavailable_source_count": len(unavailable),
        "observable_sources": tuple(current),
        "degraded_sources": tuple(degraded),
        "unavailable_sources": tuple(unavailable),
        "source_poll_evidence_hash": canonical_hash(evidence),
    }
    return payload


def classify_news_input_coverage(
    *, news_snapshot: dict, operational_health: dict,
    source_observability: dict[str, object],
) -> dict[str, object]:
    """Classify whether the frozen visible news input is trustworthy to use."""
    core_count = len(news_snapshot.get("core_visible_events") or ())
    broad_count = len(news_snapshot.get("broad_visible_events") or ())
    usable_count = max(core_count, broad_count)
    operational_reasons = tuple(
        str(code) for code in operational_health.get("reason_codes") or ()
    )
    unresolved_annotation = int(
        operational_health.get("unresolved_annotation_count") or 0
    )
    unresolved_impact = int(
        operational_health.get("unresolved_impact_count") or 0
    )
    recovering_count = int(operational_health.get("recovering_count") or 0)
    terminal_or_overdue_count = int(
        operational_health.get("terminal_or_overdue_count") or 0
    )
    observable_sources = int(
        source_observability.get("observable_source_count") or 0
    )
    degraded_sources = int(
        source_observability.get("degraded_source_count") or 0
    )
    unavailable_sources = int(
        source_observability.get("unavailable_source_count") or 0
    )
    source_reasons: list[str] = []
    if observable_sources == 0:
        source_reasons.append("NEWS_SOURCES_UNOBSERVABLE")
    elif degraded_sources or unavailable_sources:
        source_reasons.append("NEWS_SOURCES_PARTIALLY_OBSERVABLE")

    observation_outage = (
        observable_sources == 0
        or any(code in NEWS_OBSERVATION_OUTAGE_REASONS for code in operational_reasons)
    )
    if observation_outage:
        state = "UNAVAILABLE"
    elif usable_count:
        state = (
            "DEGRADED"
            if operational_reasons or source_reasons
            else "AVAILABLE"
        )
    elif operational_reasons or source_reasons:
        state = "DEGRADED"
    else:
        state = "QUIET"

    reason_codes = tuple(dict.fromkeys((*operational_reasons, *source_reasons)))
    payload = {
        "state": state,
        "usable_core_event_count": core_count,
        "usable_broad_event_count": broad_count,
        "unresolved_annotation_count": unresolved_annotation,
        "unresolved_impact_count": unresolved_impact,
        "recovering_count": recovering_count,
        "terminal_or_overdue_count": terminal_or_overdue_count,
        "operational_reason_codes": operational_reasons,
        "coverage_reason_codes": reason_codes,
        "source_observability": source_observability,
        "source_evidence_hash": str(news_snapshot["source_evidence_hash"]),
        "operational_snapshot_hash": str(
            operational_health.get("snapshot_hash") or ""
        ),
    }
    return {**payload, "snapshot_hash": canonical_hash(payload)}


def news_input_coverage_at(
    ledger, *, decision_time: datetime, news_snapshot: dict,
    operational_health: dict,
) -> dict[str, object]:
    return classify_news_input_coverage(
        news_snapshot=news_snapshot,
        operational_health=operational_health,
        source_observability=news_source_observability_summary(
            ledger.connection, observed_at=decision_time,
        ),
    )
