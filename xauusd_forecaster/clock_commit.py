"""Exact-clock completeness within the existing collector completion record."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .forward_ledger import canonical_hash, snapshot_evidence_hash


COMPLETION_SOURCE = "COLLECTOR_CLOCK_ATOMIC"
# Only facts frozen by this clock. Later outcomes/scores are independent work.
_FAMILIES = (
    ("predictions", "decision_id"),
    ("shadow_trade_intents", "decision_id"),
    ("derived_market_snapshots", "source_decision_id"),
    ("derived_news_feature_snapshots", "source_decision_id"),
    ("predictions_v2", "source_decision_id"),
    ("news_semantic_health_snapshots_v1", "source_decision_id"),
    ("news_input_coverage_snapshots_v1", "source_decision_id"),
    ("news_decision_event_snapshots_v1", "source_decision_id"),
    ("news_model_visibility_receipts_v1", "source_decision_id"),
    ("news_only_visibility_receipts_v1", "source_decision_id"),
)


def clock_ids(decision_time: datetime) -> tuple[str, str]:
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("CLOCK_EVENT_UNQUALIFIED_TIME")
    at = decision_time.astimezone(timezone.utc)
    return f"XAU-SNAPSHOT-{at:%Y%m%dT%H%M%SZ}", f"XAU-{at:%Y%m%dT%H%M%SZ}"


def clock_evidence(connection, decision_time: datetime) -> dict:
    snapshot_id, decision_id = clock_ids(decision_time)
    evidence = {}
    for table, key, value in (
        ("market_snapshots", "snapshot_id", snapshot_id),
        ("decision_events", "decision_id", decision_id),
        *((table, key, decision_id) for table, key in _FAMILIES),
    ):
        rows = connection.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchall()
        # Sort canonical row encodings, independent of SQLite's access plan.
        evidence[table] = sorted(canonical_hash(tuple(row)) for row in rows)
    rows = connection.execute(
        "SELECT * FROM execution_predictions_v2 WHERE source_decision_id=? AND checkpoint_minutes=0",
        (decision_id,),
    ).fetchall()
    evidence["execution_predictions_v2"] = sorted(canonical_hash(tuple(row)) for row in rows)
    rows = connection.execute(
        "SELECT c.* FROM calibration_snapshots_v2 c WHERE c.calibration_version IN ("
        "SELECT calibration_version FROM predictions_v2 WHERE source_decision_id=? "
        "AND model_identity<>'CHAMPION_0')", (decision_id,),
    ).fetchall()
    evidence["calibration_snapshots_v2"] = sorted(canonical_hash(tuple(row)) for row in rows)
    # Shared immutable inputs are addressed through this clock's receipts, not
    # by scanning their whole catalogs. Unrelated later events cannot change
    # this completion identity.
    for table in ("news_event_catalog_v1", "news_event_source_budgets_v1"):
        rows = connection.execute(
            f"SELECT e.* FROM {table} e WHERE e.event_version_id IN ("
            "SELECT event_version_id FROM news_decision_event_snapshots_v1 "
            "WHERE source_decision_id=?)", (decision_id,),
        ).fetchall()
        evidence[table] = sorted(canonical_hash(tuple(row)) for row in rows)
    rows = connection.execute(
        "SELECT e.* FROM news_model_visibility_events_v1 e WHERE e.event_source_hash IN ("
        "SELECT event_source_hash FROM news_model_visibility_receipts_v1 WHERE source_decision_id=? "
        "UNION SELECT event_source_hash FROM news_only_visibility_receipts_v1 WHERE source_decision_id=?)",
        (decision_id, decision_id),
    ).fetchall()
    evidence["news_model_visibility_events_v1"] = sorted(canonical_hash(tuple(row)) for row in rows)
    return evidence


def completion_status(connection, decision_time: datetime) -> dict:
    evidence = clock_evidence(connection, decision_time)
    if len(evidence["market_snapshots"]) != 1 or len(evidence["decision_events"]) != 1:
        raise ValueError("CLOCK_EVENT_INCOMPLETE")
    if not evidence["predictions"] or len(evidence["shadow_trade_intents"]) != len(evidence["predictions"]):
        raise ValueError("CLOCK_EVENT_INCOMPLETE_PREDICTIONS")
    return {"source": COMPLETION_SOURCE, "status": "COMPLETED", "evidence": evidence}


def read_completed_clock(ledger, decision_time: datetime) -> tuple[str, str] | None:
    """A snapshot or MAX(decision_time) alone is never completion authority."""
    snapshot_id, decision_id = clock_ids(decision_time)
    connection = ledger.connection
    snapshot = connection.execute(
        "SELECT * FROM market_snapshots WHERE snapshot_id=?", (snapshot_id,),
    ).fetchone()
    if snapshot is None:
        return None
    record = dict(snapshot)
    record["features"] = json.loads(record["features_json"])
    record["decision_time"] = datetime.fromisoformat(record["decision_time"])
    if (record["data_role"] != "FORWARD" or record["decision_time"] != decision_time
            or snapshot_evidence_hash(record) != record["snapshot_hash"]):
        raise ValueError("CLOCK_EVENT_SNAPSHOT_CONFLICT")
    runs = connection.execute(
        "SELECT * FROM collector_runs WHERE decision_id=?", (decision_id,),
    ).fetchmany(2)
    if not runs:
        from .clock_recovery import ExcludedIncompleteClock, is_excluded_snapshot

        if is_excluded_snapshot(connection, decision_time=decision_time,
                                snapshot_hash=record["snapshot_hash"]):
            raise ExcludedIncompleteClock("CLOCK_EVENT_EXCLUDED_INCOMPLETE")
    if (len(runs) != 1 or runs[0]["snapshot_id"] != snapshot_id
            or datetime.fromisoformat(runs[0]["decision_time"]) != decision_time):
        raise ValueError("CLOCK_EVENT_INCOMPLETE")
    decision = connection.execute(
        "SELECT snapshot_id,decision_time FROM decision_events WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    if (decision is None or decision["snapshot_id"] != snapshot_id
            or datetime.fromisoformat(decision["decision_time"]) != decision_time):
        raise ValueError("CLOCK_EVENT_DECISION_CONFLICT")
    actual = completion_status(connection, decision_time)
    recorded = [item for item in json.loads(runs[0]["news_status_json"])
                if item.get("source") == COMPLETION_SOURCE]
    if recorded:
        if len(recorded) == 1 and "u5_checkpoint_hash" in recorded[0]:
            actual["u5_checkpoint_hash"] = recorded[0]["u5_checkpoint_hash"]
        if recorded != [actual]:
            raise ValueError("CLOCK_EVENT_COMPLETION_CONFLICT")
    else:
        # Historical completion is retained, but still check mandatory families.
        from .evidence_v2 import evaluation_epoch
        from .inference_v2 import _require_complete_active_generation

        epoch = evaluation_epoch(connection)
        if epoch is not None and decision_time >= epoch:
            for family in ("derived_market_snapshots", "derived_news_feature_snapshots",
                           "news_semantic_health_snapshots_v1", "news_input_coverage_snapshots_v1"):
                if len(actual["evidence"][family]) != 1:
                    raise ValueError("CLOCK_EVENT_INCOMPLETE_V2")
            rows = connection.execute(
                "SELECT model_identity,model_version FROM predictions_v2 WHERE source_decision_id=?",
                (decision_id,),
            ).fetchall()
            if not any(row["model_identity"] == "CHAMPION_0" for row in rows):
                raise ValueError("CLOCK_EVENT_INCOMPLETE_V2")
            _require_complete_active_generation(ledger, decision_time, [dict(row) for row in rows])
    return snapshot_id, decision_id
