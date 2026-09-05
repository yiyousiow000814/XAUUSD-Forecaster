"""Bounded, append-only exclusion of an unreconstructable snapshot-only clock.

This is not inference or a completion receipt. The retained market observation
remains engineering evidence; absent historical predictions remain absent.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from .forward_ledger import canonical_hash


RULE = "clock-event-snapshot-exclusion-v1"
REASON = "SNAPSHOT_ONLY_HISTORICAL_PREDICTION_INPUTS_NOT_FROZEN"


class ExcludedIncompleteClock(ValueError):
    """A recorded engineering gap, never a successfully appended decision."""


_DESCENDANTS = (
    ("predictions", "decision_id"),
    ("outcomes", "decision_id"),
    ("shadow_trade_intents", "decision_id"),
    ("derived_market_snapshots", "source_decision_id"),
    ("derived_news_feature_snapshots", "source_decision_id"),
    ("derived_outcomes", "source_decision_id"),
    ("predictions_v2", "source_decision_id"),
    ("execution_predictions_v2", "source_decision_id"),
    ("news_semantic_health_snapshots_v1", "source_decision_id"),
    ("news_input_coverage_snapshots_v1", "source_decision_id"),
)


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("CLOCK_RECOVERY_TIME_UNQUALIFIED")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def snapshot_only_evidence(
    connection: sqlite3.Connection, *, decision_time: datetime,
    expected_snapshot_hash: str,
) -> dict:
    """Read exact-key facts; never initialize a ledger or execute schema DDL."""
    clock = _utc(decision_time)
    instant = datetime.fromisoformat(clock)
    if instant.second or instant.microsecond or instant.minute % 5:
        raise ValueError("CLOCK_RECOVERY_OFF_GRID")
    snapshot_id = f"XAU-SNAPSHOT-{instant:%Y%m%dT%H%M%SZ}"
    decision_id = f"XAU-{instant:%Y%m%dT%H%M%SZ}"
    cursor = connection.execute(
        "SELECT * FROM market_snapshots WHERE snapshot_id=? OR decision_time=?",
        (snapshot_id, clock),
    )
    rows = cursor.fetchmany(2)
    if len(rows) != 1:
        raise ValueError("CLOCK_RECOVERY_SNAPSHOT_IDENTITY_CONFLICT")
    row = dict(zip((col[0] for col in cursor.description), rows[0]))
    if (row["snapshot_id"] != snapshot_id or row["decision_time"] != clock
            or row["data_role"] != "FORWARD"):
        raise ValueError("CLOCK_RECOVERY_SNAPSHOT_IDENTITY_CONFLICT")
    hashed = {
        **json.loads(row["features_json"]), "bid": row["bid"], "ask": row["ask"],
        "decision_time": clock, "feature_version": row["feature_version"],
    }
    if not (canonical_hash(hashed) == row["snapshot_hash"] == expected_snapshot_hash):
        raise ValueError("CLOCK_RECOVERY_SNAPSHOT_HASH_CONFLICT")
    for table, query, args in (
        ("decision_events", "decision_id=? OR snapshot_id=? OR decision_time=?",
         (decision_id, snapshot_id, clock)),
        ("collector_runs", "decision_id=? OR snapshot_id=?", (decision_id, snapshot_id)),
        *((table, f"{key}=?", (decision_id,)) for table, key in _DESCENDANTS),
    ):
        if connection.execute(f"SELECT 1 FROM {table} WHERE {query} LIMIT 1", args).fetchone():
            raise ValueError(f"CLOCK_RECOVERY_NOT_SNAPSHOT_ONLY:{table}")
    return {
        "decision_time": clock, "decision_id": decision_id,
        "snapshot_id": snapshot_id, "snapshot_hash": row["snapshot_hash"],
        "source_evidence_hash": canonical_hash(row), "reason": REASON,
    }


def exclude_snapshot_only_clock(
    connection: sqlite3.Connection, *, decision_time: datetime,
    expected_snapshot_hash: str, code_commit: str, recovered_at: datetime,
) -> dict:
    """One transaction, two existing-family rows, zero historical rewrites.

    Caller owns database/maintenance authority. Never commits an outer caller's
    transaction. This API does not start services or mark the clock complete.
    """
    if connection.in_transaction:
        raise ValueError("CLOCK_RECOVERY_TRANSACTION_ALREADY_ACTIVE")
    if not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("CLOCK_RECOVERY_CODE_IDENTITY_INVALID")
    at = _utc(recovered_at)
    if recovered_at <= decision_time:
        raise ValueError("CLOCK_RECOVERY_NOT_HISTORICAL")
    connection.execute("BEGIN IMMEDIATE")
    try:
        evidence = snapshot_only_evidence(
            connection, decision_time=decision_time,
            expected_snapshot_hash=expected_snapshot_hash,
        )
        batch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{RULE}:{evidence['source_evidence_hash']}"))
        assignment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{RULE}:{evidence['snapshot_id']}"))
        output_hash = canonical_hash(evidence | {"lane": "LEGACY_ENGINEERING"})
        existing = connection.execute(
            "SELECT source_evidence_hash,output_evidence_hash,status,repaired_row_count,"
            "unrepaired_row_count FROM repair_batches WHERE repair_batch_id=?", (batch_id,),
        ).fetchone()
        lanes = connection.execute(
            "SELECT assignment_id,evidence_type,evidence_id,lane,rule_version,source_hash,"
            "repair_batch_id FROM evidence_lane_assignments WHERE evidence_id IN (?,?)",
            (evidence["snapshot_id"], evidence["decision_id"]),
        ).fetchall()
        expected_lane = (assignment_id, "SNAPSHOT", evidence["snapshot_id"],
                         "LEGACY_ENGINEERING", RULE, evidence["source_evidence_hash"], batch_id)
        expected_batch = (evidence["source_evidence_hash"], output_hash, "COMPLETED_WITH_GAPS", 0, 1)
        if existing is not None or lanes:
            if (existing is None or tuple(existing) != expected_batch
                    or [tuple(row) for row in lanes] != [expected_lane]):
                raise ValueError("CLOCK_RECOVERY_RECEIPT_CONFLICT")
        else:
            connection.execute(
                "INSERT INTO repair_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, evidence["decision_time"], json.dumps(["forward-market-v1"]),
                 json.dumps([RULE]), code_commit, at, at, evidence["source_evidence_hash"],
                 output_hash, 0, 1, json.dumps({REASON: 1}, sort_keys=True), "COMPLETED_WITH_GAPS"),
            )
            connection.execute(
                "INSERT INTO evidence_lane_assignments VALUES (?,?,?,?,?,?,?,?)",
                (assignment_id, "SNAPSHOT", evidence["snapshot_id"], "LEGACY_ENGINEERING",
                 at, RULE, evidence["source_evidence_hash"], batch_id),
            )
        connection.commit()
        return evidence | {"repair_batch_id": batch_id, "output_evidence_hash": output_hash,
                           "status": "EXCLUDED_INCOMPLETE", "already_recorded": existing is not None}
    except BaseException:
        connection.rollback()
        raise


def is_excluded_snapshot(connection, *, decision_time: datetime, snapshot_hash: str) -> bool:
    """Read-only verification of an existing recovery; never authorizes a write."""
    evidence = snapshot_only_evidence(
        connection, decision_time=decision_time, expected_snapshot_hash=snapshot_hash,
    )
    batch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{RULE}:{evidence['source_evidence_hash']}"))
    assignment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{RULE}:{evidence['snapshot_id']}"))
    rows = connection.execute(
        "SELECT a.assignment_id,a.lane,a.source_hash,a.repair_batch_id,"
        "b.source_evidence_hash,b.output_evidence_hash,b.repaired_row_count,"
        "b.unrepaired_row_count,b.status,a.evidence_type,a.rule_version FROM evidence_lane_assignments a "
        "LEFT JOIN repair_batches b USING(repair_batch_id) "
        "WHERE a.evidence_id IN (?,?)",
        (evidence["snapshot_id"], evidence["decision_id"]),
    ).fetchmany(2)
    if not rows:
        return False
    if len(rows) != 1 or tuple(rows[0]) != (
        assignment_id, "LEGACY_ENGINEERING", evidence["source_evidence_hash"], batch_id,
        evidence["source_evidence_hash"], canonical_hash(evidence | {"lane": "LEGACY_ENGINEERING"}),
        0, 1, "COMPLETED_WITH_GAPS", "SNAPSHOT", RULE,
    ):
        raise ValueError("CLOCK_RECOVERY_RECEIPT_CONFLICT")
    return True
