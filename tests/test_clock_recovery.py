"""Immutable incomplete-clock recovery, not retrospective inference."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from xauusd_forecaster.clock_recovery import exclude_snapshot_only_clock, is_excluded_snapshot
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.market import build_forward_snapshot


UTC = timezone.utc
CLOCK = datetime(2026, 9, 4, 16, 5, tzinfo=UTC)


@pytest.fixture
def partial(tmp_path):
    ledger = ForwardLedger(tmp_path / "forward-evidence.sqlite3", now=CLOCK - timedelta(minutes=5))
    snapshot = build_forward_snapshot([], CLOCK, CLOCK + timedelta(seconds=9), "fixture")
    ledger.append_snapshot(snapshot)
    before = tuple(ledger.connection.execute("SELECT * FROM market_snapshots").fetchone())
    # The stored snapshot owner canonicalizes UTC microseconds before hashing.
    snapshot["snapshot_hash"] = before[-1]
    yield ledger, snapshot, before
    ledger.close()


def recover(ledger, snapshot, **kwargs):
    return exclude_snapshot_only_clock(
        ledger.connection, decision_time=kwargs.pop("decision_time", CLOCK),
        expected_snapshot_hash=kwargs.pop("expected_snapshot_hash", snapshot["snapshot_hash"]),
        code_commit="e" * 40, recovered_at=CLOCK + timedelta(days=1), **kwargs,
    )


def test_snapshot_exclusion_preserves_fact_and_is_restart_idempotent(partial):
    ledger, snapshot, before = partial
    first = recover(ledger, snapshot)
    assert first["status"] == "EXCLUDED_INCOMPLETE"
    assert not first["already_recorded"]
    with sqlite3.connect(ledger.path) as restarted:
        second = exclude_snapshot_only_clock(
            restarted, decision_time=CLOCK.astimezone(timezone(timedelta(hours=8))),
            expected_snapshot_hash=snapshot["snapshot_hash"], code_commit="f" * 40,
            recovered_at=CLOCK + timedelta(days=2),
        )
    assert second["already_recorded"]
    assert second["repair_batch_id"] == first["repair_batch_id"]
    assert is_excluded_snapshot(
        ledger.connection, decision_time=CLOCK, snapshot_hash=snapshot["snapshot_hash"],
    )
    assert tuple(ledger.connection.execute("SELECT * FROM market_snapshots").fetchone()) == before
    assert ledger.count("repair_batches") == ledger.count("evidence_lane_assignments") == 1
    for table in ("decision_events", "predictions", "predictions_v2", "collector_runs"):
        assert ledger.count(table) == 0


def test_conflicting_lane_after_recovery_cannot_authorize_skip(partial):
    ledger, snapshot, _ = partial
    recover(ledger, snapshot)
    with ledger.connection:
        ledger.connection.execute(
            "INSERT INTO evidence_lane_assignments VALUES (?,?,?,?,?,?,?,?)",
            ("contradictory", "SNAPSHOT", snapshot["snapshot_id"], "LIVE_OOS",
             CLOCK.isoformat(), "wrong-rule", snapshot["snapshot_hash"], None),
        )
    with pytest.raises(ValueError, match="CLOCK_RECOVERY_RECEIPT_CONFLICT"):
        is_excluded_snapshot(
            ledger.connection, decision_time=CLOCK, snapshot_hash=snapshot["snapshot_hash"],
        )


def test_real_repair_entrypoint_inspects_then_recovers_without_whole_database_work(partial):
    import json

    ledger, snapshot, before = partial
    root = Path(__file__).resolve().parents[1]
    arguments = [
        sys.executable, str(root / "scripts" / "run_evidence_repair_v2.py"),
        "--local-root", str(ledger.path.parent),
        "--snapshot-only-clock", CLOCK.isoformat(),
        "--expected-snapshot-hash", snapshot["snapshot_hash"],
    ]
    for inspect in (True, False, False):
        result = subprocess.run(
            arguments + (["--inspect-snapshot-only"] if inspect else []),
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["snapshot_hash"] == snapshot["snapshot_hash"]
        assert ledger.count("repair_batches") == (0 if inspect else 1)
        assert tuple(ledger.connection.execute("SELECT * FROM market_snapshots").fetchone()) == before
    assert report["already_recorded"]
    assert not (ledger.path.parent / "backups").exists()
    assert ledger.count("decision_events") == ledger.count("collector_runs") == 0


@pytest.mark.parametrize("failure", ["hash", "identity", "downstream", "receipt", "outer_transaction"])
def test_snapshot_recovery_rejects_contradiction_without_writes(partial, failure):
    ledger, snapshot, before = partial
    kwargs = {}
    if failure == "hash":
        kwargs["expected_snapshot_hash"] = "0" * 64
    elif failure == "identity":
        kwargs["decision_time"] = CLOCK + timedelta(minutes=5)
    elif failure == "downstream":
        ledger.append_decision({
            "decision_id": "XAU-20260904T160500Z", "decision_time": CLOCK,
            "created_at": CLOCK, "snapshot_id": snapshot["snapshot_id"],
            "data_health": snapshot["data_health"], "predictions": [],
        })
    elif failure == "receipt":
        with ledger.connection:
            ledger.connection.execute(
                "INSERT INTO evidence_lane_assignments VALUES (?,?,?,?,?,?,?,?)",
                ("wrong", "SNAPSHOT", snapshot["snapshot_id"], "LIVE_OOS",
                 CLOCK.isoformat(), "wrong", snapshot["snapshot_hash"], None),
            )
    else:
        ledger.connection.execute("BEGIN")
    counts = {t: ledger.count(t) for t in ("repair_batches", "evidence_lane_assignments")}
    with pytest.raises(ValueError, match="CLOCK_RECOVERY_"):
        recover(ledger, snapshot, **kwargs)
    assert counts == {t: ledger.count(t) for t in counts}
    assert tuple(ledger.connection.execute("SELECT * FROM market_snapshots").fetchone()) == before
    ledger.connection.rollback()


def test_recovery_second_write_failure_rolls_back_first_write(partial):
    ledger, snapshot, _ = partial
    ledger.connection.execute(
        "CREATE TEMP TRIGGER fail_recovery BEFORE INSERT ON evidence_lane_assignments "
        "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        recover(ledger, snapshot)
    assert not ledger.connection.in_transaction
    assert ledger.count("repair_batches") == ledger.count("evidence_lane_assignments") == 0


@pytest.mark.parametrize("boundary", ["assignment", "commit"])
def test_real_recovery_process_death_cannot_publish_half_exclusion(partial, boundary):
    ledger, snapshot, before = partial
    child = r'''
import os, sqlite3, sys
from datetime import datetime, timezone, timedelta
from xauusd_forecaster.clock_recovery import exclude_snapshot_only_clock
c = sqlite3.connect(sys.argv[1])
def crash(sql):
    prefix = 'INSERT INTO evidence_lane_assignments' if sys.argv[3] == 'assignment' else 'COMMIT'
    if sql.lstrip().startswith(prefix):
        os._exit(94)
c.set_trace_callback(crash)
at = datetime(2026, 9, 4, 16, 5, tzinfo=timezone.utc)
exclude_snapshot_only_clock(c, decision_time=at, expected_snapshot_hash=sys.argv[2],
                           code_commit='e' * 40, recovered_at=at + timedelta(days=1))
'''
    result = subprocess.run(
        [sys.executable, "-c", child, str(ledger.path), snapshot["snapshot_hash"], boundary],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 94, result.stderr
    assert ledger.count("repair_batches") == ledger.count("evidence_lane_assignments") == 0
    assert tuple(ledger.connection.execute("SELECT * FROM market_snapshots").fetchone()) == before
    assert not recover(ledger, snapshot)["already_recorded"]
    assert recover(ledger, snapshot)["already_recorded"]
