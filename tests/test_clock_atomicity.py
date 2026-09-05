"""Real-process WAL crashes at the single clock-event transaction boundary."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster.clock_commit import read_completed_clock
from xauusd_forecaster.forward_engine import ForwardEngine
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.market import NullMarketProvider
from xauusd_forecaster.u5_state import U5State


UTC = timezone.utc
CLOCK = datetime(2026, 9, 4, 16, 10, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.forward_engine import ForwardEngine
from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.u5_state import U5State
clock = datetime(2026, 9, 4, 16, 10, tzinfo=timezone.utc)
ledger = ForwardLedger(sys.argv[1])
stage = sys.argv[2]
next_statement = {
    'after_snapshot': 'INSERT INTO decision_events',
    'after_decision': 'INSERT INTO predictions ',
    'during_v2': 'INSERT INTO news_semantic_health_snapshots_v1',
}.get(stage)
def crash(sql):
    if next_statement and sql.lstrip().startswith(next_statement):
        os._exit(91)
ledger.connection.set_trace_callback(crash)
class Provider:
    name = 'fixture'
    def observations(self, at):
        return [MarketObservation(at - timedelta(minutes=1), at - timedelta(minutes=1), 2400, 2401)]
if stage == 'before_checkpoint':
    U5State.reconcile_checkpoint = staticmethod(lambda *_: os._exit(93))
ForwardEngine(ledger, Provider(), u5_checkpoint_path=Path(sys.argv[1]).with_suffix('.json')).append_clock_event(clock, clock)
os._exit(92)
'''


@pytest.fixture
def clock_ledger(tmp_path):
    ledger = ForwardLedger(tmp_path / "clock.sqlite3", now=CLOCK - timedelta(minutes=5))
    with ledger.connection:
        ledger.connection.execute(
            "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
            ("test-epoch", ledger.forward_epoch.isoformat(), CLOCK.isoformat(),
             CLOCK.isoformat(), CLOCK.isoformat(), "e" * 40, "fixture"),
        )
    yield ledger
    ledger.close()


@pytest.mark.parametrize("stage", ["after_snapshot", "after_decision", "during_v2", "before_checkpoint", "after_commit"])
def test_process_death_has_all_or_no_clock_and_restart_replays(clock_ledger, stage):
    ledger = clock_ledger
    checkpoint = ledger.path.with_suffix('.json')
    U5State().save(checkpoint)
    process = subprocess.run(
        [sys.executable, "-c", CHILD, str(ledger.path), stage],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    committed = stage in {"before_checkpoint", "after_commit"}
    assert process.returncode == (93 if stage == "before_checkpoint" else 92 if committed else 91), process.stderr
    for table in ("market_snapshots", "decision_events", "collector_runs",
                  "derived_market_snapshots", "news_semantic_health_snapshots_v1"):
        assert ledger.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == (
            1 if committed else 0
        ), table
    U5State.reconcile_checkpoint(ledger, checkpoint)
    restored = U5State.load(checkpoint)
    assert restored.last_minute == (CLOCK - timedelta(minutes=1) if committed else None)
    assert not checkpoint.with_name(checkpoint.name + '.pending').exists()
    engine = ForwardEngine(ledger, NullMarketProvider())
    first = engine.append_clock_event(CLOCK, CLOCK)
    count = ledger.connection.total_changes
    assert engine.append_clock_event(CLOCK.astimezone(timezone(timedelta(hours=8))), CLOCK) == first
    assert ledger.connection.total_changes == count
    assert read_completed_clock(ledger, CLOCK) == first
    engine.append_clock_event(CLOCK + timedelta(minutes=5), CLOCK + timedelta(minutes=5))
    assert ledger.count("collector_runs") == 2


def test_preparation_and_failure_do_not_advance_u5_or_commit_helpers(clock_ledger, monkeypatch):
    from xauusd_forecaster.market import MarketObservation
    from xauusd_forecaster import live_v2

    ledger = clock_ledger

    class Provider:
        name = "fixture"

        def observations(self, _):
            return [MarketObservation(CLOCK - timedelta(minutes=1), CLOCK - timedelta(minutes=1), 2400, 2401)]

    engine = ForwardEngine(ledger, Provider())
    before = engine.u5_state.as_dict()
    original = live_v2.prepare_live_decision_v2

    def prepare(*args, **kwargs):
        assert ledger.connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert ledger.count("market_snapshots") == 0
        # An unrelated writer can acquire the writer reservation during inference.
        with sqlite3.connect(ledger.path, timeout=0.1) as other:
            other.execute("BEGIN IMMEDIATE")
        return original(*args, **kwargs)

    monkeypatch.setattr(live_v2, "prepare_live_decision_v2", prepare)
    ledger.connection.execute(
        "CREATE TEMP TRIGGER fail_completion BEFORE INSERT ON collector_runs "
        "BEGIN SELECT RAISE(ABORT, 'fixture completion failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="fixture completion failure"):
        engine.append_clock_event(CLOCK, CLOCK)
    assert engine.u5_state.as_dict() == before
    for table in ("market_snapshots", "decision_events", "predictions_v2", "collector_runs"):
        assert ledger.count(table) == 0


def test_completion_references_only_this_clocks_shared_immutable_news(clock_ledger):
    from xauusd_forecaster.clock_commit import clock_evidence
    from xauusd_forecaster.forward_ledger import canonical_hash

    connection = clock_ledger.connection
    _, decision_id = ForwardEngine(clock_ledger, NullMarketProvider()).append_clock_event(CLOCK, CLOCK)
    at = CLOCK.isoformat()
    with connection:
        for key in ("referenced", "unrelated"):
            connection.execute(
                "INSERT INTO news_event_catalog_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, key, "fixture", at, "OFFICIAL_RELEASE_TIME", "TIMESTAMP",
                 "fixture", key, key, "fixture", "[]", "[]", at),
            )
            connection.execute(
                "INSERT INTO news_event_source_budgets_v1 VALUES (?,?,?,?)",
                (key, key, "COLLECTOR_SOURCE", at),
            )
            connection.execute(
                "INSERT INTO news_model_visibility_events_v1 VALUES (?,?,?,?,?,?,?,?,?)",
                (key, key, key, "fixture", at, at, "[]", "fixture", at),
            )
        connection.execute(
            "INSERT INTO news_decision_event_snapshots_v1 VALUES (?,?,?,?,?,?,?,?,?)",
            (decision_id, at, "referenced", "referenced", "fixture", "DISPLAY_ONLY", 0, 0, "hash"),
        )
    evidence = clock_evidence(connection, CLOCK)
    for table in ("news_event_catalog_v1", "news_event_source_budgets_v1"):
        row = connection.execute(f"SELECT * FROM {table} WHERE event_version_id='referenced'").fetchone()
        assert evidence[table] == [canonical_hash(tuple(row))]
    assert evidence["news_model_visibility_events_v1"] == []
    # A late alteration of the clock's frozen inputs cannot reuse its receipt.
    with pytest.raises(ValueError, match="CLOCK_EVENT_COMPLETION_CONFLICT"):
        read_completed_clock(clock_ledger, CLOCK)


def test_calibration_preparation_is_read_only_and_persistence_is_transaction_owned(clock_ledger):
    from xauusd_forecaster.inference_v2 import _calibration, persist_calibration_rows

    connection = clock_ledger.connection
    before = connection.total_changes
    rows = []
    with clock_ledger.clock_preparation():
        calibration = _calibration(clock_ledger, "MARKET_ONLY", CLOCK, prepared_rows=rows)
    assert len(rows) == 1
    assert connection.total_changes == before
    with pytest.raises(RuntimeError, match="crash fixture"):
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            persist_calibration_rows(connection, rows)
            raise RuntimeError("crash fixture")
    assert connection.execute("SELECT 1 FROM calibration_snapshots_v2").fetchone() is None
    with connection:
        persist_calibration_rows(connection, rows)
    assert connection.execute(
        "SELECT calibration_version FROM calibration_snapshots_v2"
    ).fetchone()[0] == calibration["version"]


@pytest.mark.parametrize("activated", [False, True])
def test_epoch_change_after_preparation_cannot_commit_a_partial_generation(tmp_path, monkeypatch, activated):
    from contextlib import contextmanager

    ledger = ForwardLedger(tmp_path / "epoch.sqlite3", now=CLOCK - timedelta(minutes=5))
    if activated:
        with ledger.connection:
            ledger.connection.execute(
                "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
                ("old-epoch", CLOCK.isoformat(), CLOCK.isoformat(),
                 CLOCK.isoformat(), CLOCK.isoformat(), "e" * 40, "fixture"),
            )
    # Exercise the absent-epoch branch as well as an already activated epoch.
    original = ledger.clock_preparation

    @contextmanager
    def changed_epoch():
        with original():
            yield
        with ledger.connection:
            ledger.connection.execute(
                "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
                ("new-epoch", CLOCK.isoformat(), (CLOCK + timedelta(minutes=5)).isoformat(),
                 CLOCK.isoformat(), (CLOCK + timedelta(seconds=1)).isoformat(), "f" * 40, "fixture"),
            )

    monkeypatch.setattr(ledger, "clock_preparation", changed_epoch)
    with pytest.raises(ValueError, match="CLOCK_EVENT_FROZEN_GENERATION_CHANGED"):
        ForwardEngine(ledger, NullMarketProvider()).append_clock_event(CLOCK, CLOCK)
    assert ledger.count("market_snapshots") == ledger.count("collector_runs") == 0
    ledger.close()
