from __future__ import annotations

import threading
import time
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster import training_owner
from xauusd_forecaster.market import BrokerMarketSession, MarketObservation
from xauusd_forecaster.runtime_health import write_runtime_heartbeat
from scripts.run_forward_collector import append_current_grid_events


UTC = timezone.utc


def test_real_windows_process_identity_tracks_child_lifetime() -> None:
    if os.name != "nt":
        return
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        token, alive = training_owner._process_start_token(child.pid)
        assert alive is True
        assert token and token.startswith("windows-filetime:")
        assert training_owner._process_identity_alive(child.pid, token) is True
        assert training_owner._process_identity_alive(
            child.pid, "windows-filetime:0",
        ) is False
    finally:
        child.terminate()
        child.wait(timeout=10)
        child._handle.Close()
    # Windows can retain the terminated process object briefly after the last
    # user handle closes. The ownership contract is eventual bounded fencing,
    # not zero-duration kernel object reclamation.
    deadline = time.monotonic() + 2
    while training_owner._process_identity_alive(child.pid, token):
        if time.monotonic() >= deadline:
            raise AssertionError("terminated Windows process identity remained live")
        time.sleep(0.05)


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


def test_expired_training_lease_cannot_be_stolen_from_live_owner(tmp_path) -> None:
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
    assert second._claim(second_ledger.connection) is None
    first_ledger.close()
    second_ledger.close()
    ledger.close()


def test_blocked_training_renews_lease_across_twenty_logical_minutes(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    training_owner.install_training_owner_schema(ledger.connection)
    entered = threading.Event()
    release = threading.Event()
    logical_now = [datetime.now(UTC)]

    def blocked(*_args):
        entered.set()
        assert release.wait(3)
        return [{"status": "NOT_DUE"}]

    monkeypatch.setattr(training_owner, "train_due_v2", blocked)
    monkeypatch.setattr(training_owner, "train_due_execution", lambda *_: [])
    first = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
        lease_seconds=60, heartbeat_seconds=0.01, clock=lambda: logical_now[0],
    )
    first.start()
    training_owner.request_background_training(
        ledger.connection, logical_now[0], clock=lambda: logical_now[0],
    )
    first.wake()
    assert entered.wait(3)
    initial = ledger.connection.execute(
        """SELECT lease_heartbeat_at,lease_expires_at,process_id,
                  process_start_token FROM background_training_owner_v1"""
    ).fetchone()
    logical_now[0] += timedelta(minutes=21)
    deadline = time.time() + 2
    renewed = None
    while time.time() < deadline:
        renewed = ledger.connection.execute(
            """SELECT lease_heartbeat_at,lease_expires_at
               FROM background_training_owner_v1"""
        ).fetchone()
        if renewed[0] != initial[0]:
            break
        time.sleep(0.01)
    assert renewed[0] == logical_now[0].isoformat()
    assert datetime.fromisoformat(renewed[1]) > logical_now[0]
    assert initial[2] and initial[3]

    second = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
        clock=lambda: logical_now[0],
    )
    second_ledger = ForwardLedger(ledger.path)
    assert second._claim(second_ledger.connection) is None
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE background_training_owner_v1 SET lease_expires_at=? WHERE id=1",
            ((logical_now[0] - timedelta(seconds=1)).isoformat(),),
        )
    assert second._claim(second_ledger.connection) is None

    release.set()
    first.close()
    assert first._lease_thread is None
    second_ledger.close()
    ledger.close()


def test_confirmed_dead_training_process_is_recoverable(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    training_owner.install_training_owner_schema(ledger.connection)
    cutoff = datetime(2026, 8, 20, 12, tzinfo=UTC)
    training_owner.request_background_training(ledger.connection, cutoff)
    first = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    first_ledger = ForwardLedger(ledger.path)
    assert first._claim(first_ledger.connection) is not None
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE background_training_owner_v1 SET lease_expires_at=? WHERE id=1",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    recovered = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
        process_probe=lambda _pid, _token: False,
    )
    recovered_ledger = ForwardLedger(ledger.path)
    assert recovered._claim(recovered_ledger.connection) is not None
    row = ledger.connection.execute(
        "SELECT lease_owner,process_id,process_start_token FROM background_training_owner_v1"
    ).fetchone()
    assert row[0] == recovered.owner_id
    assert row[1] == recovered.process_id
    assert row[2] == recovered.process_start_token
    first_ledger.close()
    recovered_ledger.close()
    ledger.close()


def test_inconsistent_expired_owner_identity_fails_closed(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    training_owner.install_training_owner_schema(ledger.connection)
    cutoff = datetime(2026, 8, 20, 12, tzinfo=UTC)
    training_owner.request_background_training(ledger.connection, cutoff)
    first = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    first_ledger = ForwardLedger(ledger.path)
    assert first._claim(first_ledger.connection) is not None
    with ledger.connection:
        ledger.connection.execute(
            """UPDATE background_training_owner_v1
                  SET process_start_token=NULL,lease_expires_at=? WHERE id=1""",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    second = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    second_ledger = ForwardLedger(ledger.path)
    assert second._claim(second_ledger.connection) is None
    row = ledger.connection.execute(
        "SELECT state,last_error FROM background_training_owner_v1"
    ).fetchone()
    assert tuple(row) == ("RUNNING", "TRAINING_OWNER_IDENTITY_UNRESOLVED")
    first_ledger.close()
    second_ledger.close()
    ledger.close()


def test_training_failure_preserves_active_generation_and_retries_once_later(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    training_owner.install_training_owner_schema(ledger.connection)
    active_generation = {"id": "stable", "activations": 0}
    attempted = threading.Event()

    def fail_training(*_args):
        attempted.set()
        raise RuntimeError("controlled training failure")

    monkeypatch.setattr(training_owner, "train_due_v2", fail_training)
    monkeypatch.setattr(training_owner, "train_due_execution", lambda *_: [])
    owner = training_owner.BackgroundTrainingOwner(
        ledger.path, tmp_path / "models", tmp_path / "execution",
    )
    owner.start()
    training_owner.request_background_training(
        ledger.connection, datetime.now(UTC),
    )
    owner.wake()
    assert attempted.wait(3)
    deadline = time.time() + 2
    row = None
    while time.time() < deadline:
        row = ledger.connection.execute(
            "SELECT state,last_error,lease_owner FROM background_training_owner_v1"
        ).fetchone()
        if row[0] == "PENDING":
            break
        time.sleep(0.01)
    assert row[0] == "PENDING"
    assert "controlled training failure" in row[1]
    assert row[2] is None
    assert active_generation == {"id": "stable", "activations": 0}
    owner.close()
    ledger.close()
