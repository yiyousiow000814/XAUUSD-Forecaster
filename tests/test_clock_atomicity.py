"""Real-process WAL crashes at the single clock-event transaction boundary."""

import os
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster.clock_commit import read_completed_clock, read_signal_timing
from xauusd_forecaster.forward_engine import ForwardEngine
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.market import NullMarketProvider
from xauusd_forecaster.u5_state import U5State
from xauusd_forecaster.signal_timing import (
    ClockTiming, MAX_PREDICTION_OBSERVATIONS, MAX_TIMING_BYTES, TIMING_SOURCE,
    timing_bytes,
)


UTC = timezone.utc
CLOCK = datetime(2026, 9, 4, 16, 10, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import json, os, sys
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
def observed(event):
    if stage == 'before_observation':
        os._exit(94)
    print(json.dumps(event), flush=True)
ForwardEngine(ledger, Provider(), u5_checkpoint_path=Path(sys.argv[1]).with_suffix('.json'),
              on_clock_committed=observed).append_clock_event(clock, clock)
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


@pytest.mark.parametrize("stage", ["after_snapshot", "after_decision", "during_v2", "before_checkpoint", "after_commit", "before_observation"])
def test_process_death_has_all_or_no_clock_and_restart_replays(clock_ledger, stage):
    ledger = clock_ledger
    checkpoint = ledger.path.with_suffix('.json')
    U5State().save(checkpoint)
    process = subprocess.run(
        [sys.executable, "-c", CHILD, str(ledger.path), stage],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    committed = stage in {"before_checkpoint", "after_commit", "before_observation"}
    assert process.returncode == (94 if stage == "before_observation" else 93 if stage == "before_checkpoint" else 92 if committed else 91), process.stderr
    events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith('{')]
    assert len(events) == int(stage in {"before_checkpoint", "after_commit"})
    if events:
        assert events[0]["scope"] == "COMMIT_RETURN_OBSERVED"
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


class _TimingClock:
    """Declared synthetic observation clock, not a production market clock."""

    def __init__(self):
        self.tick = 0

    def wall(self):
        return CLOCK + timedelta(seconds=self.tick)

    def monotonic(self):
        self.tick += 1
        return self.tick * 1_000_000_000

    def factory(self, *identity):
        return ClockTiming(*identity, clock=self.wall, monotonic=self.monotonic)


@pytest.mark.parametrize("observer_fails", [False, True])
def test_clock_timing_is_atomic_and_postcommit_observation_cannot_fail_clock(clock_ledger, observer_fails):
    ledger = clock_ledger
    observer = sqlite3.connect(f"file:{ledger.path}?mode=ro", uri=True)
    observer.row_factory = sqlite3.Row
    observer.execute("BEGIN")
    assert observer.execute("SELECT count(*) FROM collector_runs").fetchone()[0] == 0
    events = []
    before_commit = []

    def trace(sql):
        if sql == "COMMIT":
            before_commit.append(observer.execute("SELECT count(*) FROM collector_runs").fetchone()[0])

    def committed(event):
        assert not ledger.connection.in_transaction
        assert observer.execute("SELECT count(*) FROM collector_runs").fetchone()[0] == 0
        observer.rollback()  # Only a new WAL read view may see the new commit.
        assert observer.execute("SELECT count(*) FROM collector_runs").fetchone()[0] == 1
        events.append(event)
        if observer_fails:
            raise OSError("synthetic log delivery failure")

    ledger.connection.set_trace_callback(trace)
    engine = ForwardEngine(ledger, NullMarketProvider(), clock_timing_factory=_TimingClock().factory,
                           on_clock_committed=committed)
    try:
        ids = engine.append_clock_event(CLOCK, CLOCK)
        assert before_commit == [0]
        assert len(events) == 1
        prediction = observer.execute("SELECT * FROM predictions_v2").fetchone()
        timing = read_signal_timing(observer, decision_id=ids[1], snapshot_id=ids[0], prediction=prediction)
        assert timing["status"] == "OBSERVED"
        assert timing["collector_started_at"] < timing["prediction_computed_at"] < timing["write_batch_ready_at"]
        assert timing["write_batch_ready_at"] < events[0]["commit_return_observed_at"]
        assert prediction["created_at"] == CLOCK.isoformat()  # Existing semantics unchanged.
        changes = ledger.connection.total_changes
        assert engine.append_clock_event(CLOCK, CLOCK + timedelta(hours=1)) == ids
        assert ledger.connection.total_changes == changes and len(events) == 1
        assert read_completed_clock(ledger, CLOCK) == ids
    finally:
        observer.close()


@pytest.mark.parametrize("fault", ["old", "schema", "digest", "identity", "prediction", "clock", "source_clock", "oversize"])
def test_timing_evidence_failure_never_rewrites_or_invalidates_clock(clock_ledger, fault):
    from xauusd_forecaster.forward_ledger import canonical_hash

    class Faulted(ClockTiming):
        def finish(self, *args):
            result = super().finish(*args)
            if fault == "old":
                return {"source": "LEGACY_DIAGNOSTIC"}
            if fault == "schema":
                result["schema"] = "unknown"
            elif fault == "identity":
                result["collector_run_id"] = "wrong"
            elif fault == "prediction":
                result["predictions"][0]["prediction_hash"] = "a" * 64
            elif fault == "clock":
                result["stages"]["write_batch_ready"]["observed_at"] = CLOCK.isoformat()
            elif fault == "source_clock":
                result["market_source_received_at"] = "not-a-time"
            elif fault == "oversize":
                result["untrusted"] = "x" * MAX_TIMING_BYTES
            result["timing_digest"] = canonical_hash({k: v for k, v in result.items() if k != "timing_digest"})
            if fault == "digest":
                result["timing_digest"] = "0" * 64
            return result

    ids = ForwardEngine(clock_ledger, NullMarketProvider(), clock_timing_factory=Faulted).append_clock_event(CLOCK, CLOCK)
    prediction = clock_ledger.connection.execute("SELECT * FROM predictions_v2").fetchone()
    assert read_signal_timing(clock_ledger.connection, decision_id=ids[1], snapshot_id=ids[0], prediction=prediction)["status"] == (
        "UNKNOWN" if fault == "old" else "INVALID")
    assert read_completed_clock(clock_ledger, CLOCK) == ids


def test_timing_bounds_and_clock_anomaly_are_evidence_not_model_permission():
    from xauusd_forecaster.forward_ledger import canonical_hash

    at = iter([CLOCK, CLOCK - timedelta(seconds=1), CLOCK + timedelta(seconds=2)])
    timing = ClockTiming("run", "decision", "snapshot", clock=lambda: next(at), monotonic=iter([10, 20, 30]).__next__)
    timing.record("market_features_completed")
    entry = timing.finish({"source": "completion"}, None)
    assert entry["state"] == "CLOCK_ANOMALY"
    assert entry["stages"]["market_features_completed"]["observed_at"] < entry["stages"]["collector_started"]["observed_at"]
    assert entry["stages"]["market_features_completed"]["elapsed_ns"] == 10
    assert entry["timing_digest"] == canonical_hash({k: v for k, v in entry.items() if k != "timing_digest"})
    row = ("decision", "version", "model", "", "", "LIVE_OOS", "hash", *([None] * 15), "READY")
    bounded = ClockTiming("run", "decision", "snapshot")
    for _ in range(MAX_PREDICTION_OBSERVATIONS + 1):
        bounded.prediction(row)
    assert bounded.state == "UNAVAILABLE"
    assert len(bounded.predictions) == MAX_PREDICTION_OBSERVATIONS
    assert len(timing_bytes(bounded.finish({}, None))) <= MAX_TIMING_BYTES

    clock_values = iter([None, CLOCK, CLOCK + timedelta(seconds=1)])
    failed = ClockTiming("run", "decision", "snapshot", clock=lambda: next(clock_values))
    failed.record("market_features_completed")
    assert failed.finish({}, None)["state"] == "UNAVAILABLE"


@pytest.mark.parametrize("fault", ["clock_unavailable", "clock_regression", "new_identity_decode"])
def test_consumer_timing_failure_cannot_renew_or_rebind_pending_evidence(clock_ledger, monkeypatch, fault):
    from xauusd_forecaster import clock_commit
    from xauusd_forecaster.signal_timing import DashboardSignalObserver, SignalLogDelivery

    wall, tick, deliveries, reads = [CLOCK], [0], [], []
    latest = {"decision_id": "first", "snapshot_id": "snapshot", "snapshot_hash": "hash"}
    ForwardEngine(clock_ledger, NullMarketProvider()).append_clock_event(CLOCK, CLOCK)
    prediction = clock_ledger.connection.execute("SELECT * FROM predictions_v2").fetchone()

    def read(_connection, **identity):
        reads.append(identity["decision_id"])
        if fault == "new_identity_decode" and identity["decision_id"] == "second":
            raise ValueError("synthetic invalid timing")
        return {"status": "UNKNOWN", "reason": "SIGNAL_TIMING_NOT_RECORDED"}

    def sink(event):
        deliveries.append(event)
        return fault != "new_identity_decode"

    monkeypatch.setattr(clock_commit, "read_signal_timing", read)
    delivery = SignalLogDelivery()
    observer = DashboardSignalObserver(clock=lambda: wall[0], monotonic=lambda: tick[0],
                                      sink=sink, delivery=delivery)
    observer.observe(None, "synthetic-locator", latest, prediction)
    assert delivery.wait(timeout=5)
    assert len(deliveries) == 1
    if fault == "clock_unavailable":
        wall[0] = None
        observer.observe(None, "synthetic-locator", latest, prediction)
        wall[0] = CLOCK + timedelta(seconds=61)
    elif fault == "clock_regression":
        wall[0] = CLOCK - timedelta(seconds=1)
        observer.observe(None, "synthetic-locator", latest, prediction)
        wall[0] = CLOCK + timedelta(seconds=61)
    tick[0] = 61_000_000_000
    latest = {**latest, "decision_id": "second"}
    observer.observe(None, "synthetic-locator", latest, prediction)
    assert delivery.wait(timeout=5)
    tick[0] += 61_000_000_000
    observer.observe(None, "synthetic-locator", latest, prediction)
    assert delivery.wait(timeout=5)
    assert reads == ["first", "second"]
    if fault == "new_identity_decode":
        assert len(deliveries) == 1  # No old failed event may be retried as second.
    else:
        assert len(deliveries) == 2
        assert deliveries[-1]["decision_id"] == "second"
        assert deliveries[-1]["clock_state"] == (
            "UNAVAILABLE" if fault == "clock_unavailable" else "CLOCK_ANOMALY")
    assert delivery.close()


@pytest.mark.parametrize("number", [-0.0, 0, 1, -1, 0.123456789])
def test_prediction_timing_identity_matches_real_sqlite_numeric_affinity(clock_ledger, number):
    from xauusd_forecaster.forward_ledger import canonical_hash
    from xauusd_forecaster.inference_v2 import _prediction_values, persist_live_predictions_v2
    from xauusd_forecaster.signal_timing import prediction_timing_hash

    row = _prediction_values(
        decision_id="timing-numeric", decision_time=CLOCK, created_at=CLOCK,
        model_version="numeric-v1", model_identity="MARKET_ONLY", feature_hash="hash",
        predicted=number, news_residual=number, ev_long=number, ev_short=number,
        calibration={"half_width": number, "version": "test", "rows": 0,
                     "blocks": 0, "days": 0, "status": "INSUFFICIENT"},
        recommended="WAIT", status="INSUFFICIENT",  # Equal directional EV is WAIT.
    )
    original_hash = canonical_hash(row)
    with clock_ledger.connection:
        persist_live_predictions_v2(clock_ledger.connection, [row])
    actual = clock_ledger.connection.execute("SELECT * FROM predictions_v2").fetchone()
    assert prediction_timing_hash(row) == prediction_timing_hash(actual)
    assert canonical_hash(row) == original_hash  # No mutation of the prediction.


def test_blocked_optional_sink_cannot_stop_new_clock_or_consumer(clock_ledger, monkeypatch):
    from xauusd_forecaster import clock_commit
    from xauusd_forecaster.signal_timing import DashboardSignalObserver, SignalLogDelivery

    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    delivery = SignalLogDelivery()

    def blocked(_event):
        entered.set()
        assert release.wait(timeout=5)
        return True

    engine = ForwardEngine(clock_ledger, NullMarketProvider(),
                           on_clock_committed=lambda event: delivery.submit(event, sink=blocked))
    try:
        first = engine.append_clock_event(CLOCK, CLOCK)
        assert entered.wait(timeout=5)
        later = CLOCK + timedelta(minutes=5)
        second = engine.append_clock_event(later, later)
        assert first != second and read_completed_clock(clock_ledger, later) == second
        assert delivery.accepted == 1 and delivery.dropped == 1
        observer = DashboardSignalObserver(delivery=delivery)
        row = clock_ledger.connection.execute("SELECT * FROM predictions_v2 LIMIT 1").fetchone()
        monkeypatch.setattr(clock_commit, "read_signal_timing", lambda *args, **kwargs:
                            pytest.fail("Busy optional log must not reread timing metadata"))

        def read():
            observer.observe(None, "synthetic", {
                "decision_id": first[1], "snapshot_id": first[0], "snapshot_hash": "hash",
            }, row)
            returned.set()

        reader = threading.Thread(target=read)
        reader.start()
        assert returned.wait(timeout=1)
        reader.join(timeout=1)
        assert not reader.is_alive()
        assert not delivery.close()  # Still running, not killed or delivered.
        assert delivery.delivered == 0
        assert not delivery.submit({"event": "AFTER_CLOSE"}, sink=blocked)
    finally:
        release.set()
        assert delivery.wait(timeout=5)
    assert delivery.delivered == 1


def test_concurrent_consumer_clock_sampling_does_not_invent_regression(clock_ledger, monkeypatch):
    from xauusd_forecaster import clock_commit
    from xauusd_forecaster.signal_timing import DashboardSignalObserver, SignalLogDelivery

    ForwardEngine(clock_ledger, NullMarketProvider()).append_clock_event(CLOCK, CLOCK)
    row = clock_ledger.connection.execute("SELECT * FROM predictions_v2 LIMIT 1").fetchone()
    entered, release, second_sample = threading.Event(), threading.Event(), threading.Event()
    calls, events = [0], []

    def clock():
        calls[0] += 1
        current = calls[0]
        if current == 1:
            entered.set()
            assert release.wait(timeout=5)
        else:
            second_sample.set()
        return CLOCK + timedelta(seconds=61 * current)

    delivery = SignalLogDelivery()
    observer = DashboardSignalObserver(clock=clock, monotonic=lambda: calls[0] * 61_000_000_000,
                                      delivery=delivery, sink=lambda event: events.append(event))
    monkeypatch.setattr(clock_commit, "read_signal_timing", lambda *args, **kwargs: {"status": "UNKNOWN"})
    latest = {"decision_id": "first", "snapshot_id": "snapshot", "snapshot_hash": "hash"}
    first = threading.Thread(target=observer.observe, args=(None, "fixture", latest, row))
    second = threading.Thread(target=observer.observe, args=(None, "fixture", {**latest, "decision_id": "second"}, row))
    try:
        first.start()
        assert entered.wait(timeout=5)
        second.start()
        assert not second_sample.wait(timeout=0.05)  # Capture follows the same lock order.
    finally:
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert delivery.wait(timeout=5)
    observer.observe(None, "fixture", {**latest, "decision_id": "third"}, row)
    assert delivery.wait(timeout=5)
    assert events[-1]["decision_id"] == "third"
    assert all(event["clock_state"] == "OBSERVED" for event in events)
    assert delivery.close()


@pytest.mark.parametrize("mode", ["normal", "failed", "blocked"])
def test_once_style_process_exit_keeps_optional_sink_bounded_and_honest(mode):
    code = r'''
import os, sys, threading
from xauusd_forecaster.signal_timing import SIGNAL_LOG_DELIVERY, queue_signal_event
mode = sys.argv[1]
if mode == "blocked":
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            os.write(write_fd, b"x" * 4096)
    except BlockingIOError:
        pass
    os.set_blocking(write_fd, True)
    entered = threading.Event()
    class PipeStdout:
        def fileno(self):
            entered.set()
            return write_fd
        def flush(self):
            pass
    sys.stdout = PipeStdout()
elif mode == "failed":
    class MissingStdout:
        def fileno(self):
            raise OSError("synthetic unavailable stdout")
        def flush(self):
            pass
    sys.stdout = MissingStdout()
assert queue_signal_event({"event": "OWNED_SYNTHETIC_ONCE_OBSERVATION"})
if mode == "blocked":
    assert entered.wait(timeout=2)
    assert not SIGNAL_LOG_DELIVERY.close()
    assert SIGNAL_LOG_DELIVERY.delivered == 0
    assert not queue_signal_event({"event": "NO_SECOND_FLIGHT"})
    os.write(2, b"UNKNOWN: sink still running; no delivery claim\n")
else:
    assert SIGNAL_LOG_DELIVERY.wait(timeout=2)
    assert SIGNAL_LOG_DELIVERY.close()
    assert SIGNAL_LOG_DELIVERY.delivered == int(mode == "normal")
    assert SIGNAL_LOG_DELIVERY.failed == int(mode == "failed")
'''
    result = subprocess.run([sys.executable, "-c", code, mode], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr
    assert "buffered_busy" not in result.stderr
    if mode == "normal":
        assert json.loads(result.stdout)["event"] == "OWNED_SYNTHETIC_ONCE_OBSERVATION"
    elif mode == "blocked":
        assert "UNKNOWN: sink still running" in result.stderr
