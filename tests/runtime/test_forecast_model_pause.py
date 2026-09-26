from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from scripts.runtime import run_forward_collector as collector


def test_retired_collector_keeps_maintenance_without_model_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "forward"
    state_root.mkdir()
    (state_root / "forecast-model-activity.json").write_text(
        json.dumps({"state": "paused"}), encoding="utf-8",
    )
    started = []
    closed = []

    class Owner:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            started.append(self.__class__.__name__)

        def close(self):
            closed.append(self.__class__.__name__)

        def snapshot(self, *_args):
            return {}

        def update(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    class NewsOwner(Owner):
        pass

    class BackupOwner(Owner):
        pass

    class WalOwner(Owner):
        pass

    class EndIteration(Exception):
        pass

    monkeypatch.setattr(collector, "authoritative_runtime_root", lambda value: Path(value))
    monkeypatch.setattr(collector, "NewsCollectionOwner", NewsOwner)
    monkeypatch.setattr(collector, "DailyBackupOwner", BackupOwner)
    monkeypatch.setattr(collector, "ForwardWalCheckpointOwner", WalOwner)
    monkeypatch.setattr(collector, "RuntimeHeartbeatPulse", Owner)
    monkeypatch.setattr(collector, "time", SimpleNamespace(sleep=lambda *_: (_ for _ in ()).throw(EndIteration)))
    monkeypatch.setattr(sys, "argv", ["run_forward_collector.py", "--state-root", str(state_root)])

    with pytest.raises(EndIteration):
        collector.main()
    assert {"NewsOwner", "BackupOwner", "WalOwner"} <= set(closed)
    assert {"NewsOwner", "BackupOwner", "WalOwner"} <= set(started)
    connection = sqlite3.connect(state_root / "forward-evidence.sqlite3")
    try:
        assert connection.execute("SELECT count(*) FROM news_model_generations_v1").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM predictions_v2").fetchone()[0] == 0
    finally:
        connection.close()
