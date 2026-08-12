"""Append point-in-time news snapshots after a frozen news contract changes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .evidence_v2 import (
    ELIGIBILITY_VERSION,
    FEATURE_VERSION,
    LABEL_VERSION,
    NEWS_FEATURE_VERSION,
    install_v2_schema,
)
from .forward_ledger import canonical_hash
from .news_features_v2 import aggregate_news_features_v2
from .repair_v2 import LANE_RULE_VERSION


UTC = timezone.utc


def _uuid(namespace: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{namespace}:{value}"))


def _current_news_snapshot(decision_id: str, decision_time: datetime, news: dict) -> dict:
    values = {
        key: value for key, value in news.items()
        if key not in {"official_visible_events", "broad_visible_events", "event_snapshots"}
    }
    payload = {
        "decision_id": decision_id,
        "decision_time": decision_time.isoformat(),
        "feature_version": NEWS_FEATURE_VERSION,
        "eligibility_version": ELIGIBILITY_VERSION,
        **values,
    }
    return {
        "snapshot_id": _uuid(
            "derived-news",
            f"{decision_id}:{NEWS_FEATURE_VERSION}:{ELIGIBILITY_VERSION}",
        ),
        "payload": payload,
        "output_hash": canonical_hash(payload),
    }


def append_missing_current_news_snapshots(
    ledger, cutoff: datetime, *, recomputed_at: datetime | None = None,
) -> dict[str, int | str]:
    """Append current-contract snapshots for already mature direction samples.

    The aggregation is evaluated at each original decision time.  This migrates
    feature contracts without rewriting predictions, outcomes, or earlier
    snapshot versions, and without making later news visible to an old decision.
    """
    install_v2_schema(ledger.connection)
    cutoff = cutoff.astimezone(UTC)
    recomputed_at = (recomputed_at or datetime.now(UTC)).astimezone(UTC)
    rows = ledger.connection.execute(
        """SELECT e.source_decision_id, e.evidence_lane, m.decision_time
        FROM training_eligibility_v2 e
        JOIN derived_market_snapshots m
          ON m.source_decision_id=e.source_decision_id AND m.feature_version=?
        JOIN derived_outcomes o
          ON o.source_decision_id=e.source_decision_id AND o.label_version=?
        LEFT JOIN derived_news_feature_snapshots n
          ON n.source_decision_id=e.source_decision_id
         AND n.feature_version=? AND n.eligibility_version=?
        WHERE e.eligible_at<=? AND o.outcome_status='VALID'
          AND n.derived_news_snapshot_id IS NULL
        ORDER BY m.decision_time, e.source_decision_id""",
        (
            FEATURE_VERSION,
            LABEL_VERSION,
            NEWS_FEATURE_VERSION,
            ELIGIBILITY_VERSION,
            cutoff.isoformat(),
        ),
    ).fetchall()

    appended = 0
    with ledger.connection:
        for row in rows:
            decision_id = str(row["source_decision_id"])
            decision_time = datetime.fromisoformat(str(row["decision_time"]))
            news = aggregate_news_features_v2(ledger, decision_time)
            snapshot = _current_news_snapshot(decision_id, decision_time, news)
            cursor = ledger.connection.execute(
                """INSERT OR IGNORE INTO derived_news_feature_snapshots VALUES
                (?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot["snapshot_id"],
                    decision_id,
                    decision_time.isoformat(),
                    str(row["evidence_lane"]),
                    recomputed_at.isoformat(),
                    NEWS_FEATURE_VERSION,
                    ELIGIBILITY_VERSION,
                    json.dumps(news["features"], sort_keys=True, separators=(",", ":")),
                    news["model_visible_items"],
                    news["news_exposed"],
                    news["distinct_news_clusters"],
                    news["distinct_event_types"],
                    news["source_evidence_hash"],
                    snapshot["output_hash"],
                ),
            )
            if cursor.rowcount <= 0:
                continue
            appended += 1
            for event in news.get("event_snapshots", []):
                ledger.connection.execute(
                    """INSERT OR IGNORE INTO news_event_catalog_v1 VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event["event_version_id"], event["event_id"],
                        event["policy_version"], event["event_occurred_at"],
                        event["event_clock_source"], event["event_time_precision"],
                        event["canonical_source"], event["canonical_source_item_id"],
                        event["source_hash"], event["evidence_grade"],
                        json.dumps(event["model_permissions"], separators=(",", ":")),
                        json.dumps(event["reason_codes"], separators=(",", ":")),
                        recomputed_at.isoformat(),
                    ),
                )
                source_budget_id = str(event["source_budget_id"])
                ledger.connection.execute(
                    """INSERT OR IGNORE INTO news_event_source_budgets_v1
                    VALUES (?,?,?,?)""",
                    (
                        event["event_version_id"], source_budget_id,
                        (
                            "REPORTING_ORGANIZATION"
                            if source_budget_id != event["canonical_source"]
                            else "COLLECTOR_SOURCE"
                        ),
                        recomputed_at.isoformat(),
                    ),
                )
                event_snapshot_hash = canonical_hash((
                    decision_id, decision_time.isoformat(), event["event_id"],
                    event["event_version_id"], event["model_permission"],
                    event["raw_weight"], event["age_minutes"],
                ))
                ledger.connection.execute(
                    """INSERT OR IGNORE INTO news_decision_event_snapshots_v1 VALUES
                    (?,?,?,?,?,?,?,?,?)""",
                    (
                        decision_id, decision_time.isoformat(), event["event_id"],
                        event["event_version_id"], event["policy_version"],
                        event["model_permission"], event["raw_weight"],
                        event["age_minutes"], event_snapshot_hash,
                    ),
                )
            ledger.connection.execute(
                """INSERT OR IGNORE INTO evidence_lane_assignments
                VALUES (?,?,?,?,?,?,?,NULL)""",
                (
                    _uuid(
                        "lane",
                        f"DERIVED_NEWS:{snapshot['snapshot_id']}:{LANE_RULE_VERSION}",
                    ),
                    "DERIVED_NEWS",
                    snapshot["snapshot_id"],
                    str(row["evidence_lane"]),
                    recomputed_at.isoformat(),
                    LANE_RULE_VERSION,
                    snapshot["output_hash"],
                ),
            )

    return {
        "status": "MIGRATED" if appended else "CURRENT",
        "candidates": len(rows),
        "appended": appended,
        "feature_version": NEWS_FEATURE_VERSION,
        "eligibility_version": ELIGIBILITY_VERSION,
    }
