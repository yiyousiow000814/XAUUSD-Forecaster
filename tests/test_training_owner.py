from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster import training_owner


UTC = timezone.utc


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
