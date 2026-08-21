from __future__ import annotations

import threading
import time
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster import training_owner
from xauusd_forecaster.market import BrokerMarketSession, MarketObservation
from xauusd_forecaster.runtime_health import write_runtime_heartbeat
from scripts.run_forward_collector import append_current_grid_events


UTC = timezone.utc


def test_blocked_training_does_not_stop_multiple_decision_cycles(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    training_owner.install_training_owner_schema(ledger.connection)
    entered = threading.Event()
    release = threading.Event()
    active_generation = {"id": "stable-generation", "activations": 0}

    def blocked_training(*_args):
        entered.set()
        assert release.wait(3)
        active_generation.update(id="candidate-generation", activations=1)
        return [{"status": "TRAINED"}]

    monkeypatch.setattr(training_owner, "train_due_v2", blocked_training)
    monkeypatch.setattr(training_owner, "train_due_execution", lambda *_: [])
    owner = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    owner.start()
    start = datetime(2026, 8, 20, 12, tzinfo=UTC)
    training_owner.request_background_training(ledger.connection, start)
    owner.wake()
    assert entered.wait(3)

    class Provider:
        def observations(self, boundary):
            return [MarketObservation(
                boundary - timedelta(seconds=2),
                boundary - timedelta(seconds=1), 4500.0, 4500.1,
            )]

        def market_session(self, observed_at):
            return BrokerMarketSession(
                observed_at=observed_at, server_time=observed_at,
                is_open=True, time_till_open=timedelta(0),
                time_till_close=timedelta(hours=2),
                next_open_time=None, next_close_time=observed_at + timedelta(hours=2),
            )

    appended_times = []

    class Engine:
        def append_clock_event(self, decision_time, _collected_at, _news):
            appended_times.append(decision_time)
            return f"snapshot-{len(appended_times)}", f"decision-{len(appended_times)}"

    decision_ledger = SimpleNamespace(forward_epoch=start)
    last_decision = start
    heartbeat_path = tmp_path / "collector-status.json"
    for cycle in range(1, 4):
        now = start + timedelta(minutes=5 * cycle, seconds=10)
        _, last_decision, appended, skipped = append_current_grid_events(
            decision_ledger, Engine(), Provider(), last_decision, [], clock=lambda: now,
        )
        assert len(appended) == 1
        assert skipped == {}
        write_runtime_heartbeat(heartbeat_path, service="collector")
        assert json.loads(heartbeat_path.read_text())["state"] == "RUNNING"
        assert active_generation == {"id": "stable-generation", "activations": 0}

    assert appended_times == [start + timedelta(minutes=5 * index) for index in range(1, 4)]
    release.set()
    deadline = time.time() + 3
    while active_generation["activations"] == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert active_generation == {"id": "candidate-generation", "activations": 1}
    owner.close()
    ledger.close()


def test_slow_training_is_owned_off_the_requesting_decision_connection(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    training_owner.install_training_owner_schema(ledger.connection)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow_training(*_args):
        calls.append("train")
        entered.set()
        assert release.wait(3)
        return [{"status": "NOT_DUE"}]

    monkeypatch.setattr(training_owner, "train_due_v2", slow_training)
    monkeypatch.setattr(training_owner, "train_due_execution", lambda *_: [])
    owner = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    owner.start()
    cutoff = datetime(2026, 8, 20, 12, tzinfo=UTC)
    training_owner.request_background_training(ledger.connection, cutoff)
    owner.wake()
    assert entered.wait(3)

    started = time.perf_counter()
    # A new decision-cycle request stays a short durable write while training
    # is deliberately blocked on the independent owner connection.
    training_owner.request_background_training(
        ledger.connection, cutoff + timedelta(minutes=5), reconcile=True,
    )
    assert time.perf_counter() - started < 0.5
    row = ledger.connection.execute(
        "SELECT state,rerun_requested,reconcile FROM background_training_owner_v1"
    ).fetchone()
    assert tuple(row) == ("RUNNING", 1, 1)

    release.set()
    deadline = time.time() + 5
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(calls) == 2
    owner.close()
    ledger.close()


def test_expired_training_lease_is_reclaimed_without_duplicate_live_owner(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    training_owner.install_training_owner_schema(ledger.connection)
    cutoff = datetime(2026, 8, 20, 12, tzinfo=UTC)
    training_owner.request_background_training(ledger.connection, cutoff)
    first = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    second = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    first_ledger = ForwardLedger(ledger.path)
    second_ledger = ForwardLedger(ledger.path)
    assert first._claim(first_ledger.connection) is not None
    assert second._claim(second_ledger.connection) is None
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE background_training_owner_v1 SET lease_expires_at=? WHERE id=1",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    assert second._claim(second_ledger.connection) is not None
    first_ledger.close()
    second_ledger.close()
    ledger.close()
