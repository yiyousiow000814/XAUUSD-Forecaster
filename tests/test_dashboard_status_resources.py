from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xauusd_forecaster.dashboard import status_resources


UTC = timezone.utc


def test_deployment_status_does_not_mislabel_local_edits_as_remote_drift() -> None:
    module = status_resources

    assert module._deployment_status("same", "same", False) == "MATCHED"
    assert module._deployment_status("same", "same", True) == "LOCAL_CHANGES"
    assert module._deployment_status("local", "remote", False) == "DEPLOYMENT_DRIFT"
    assert module._deployment_status(None, "remote", False) == "PROVENANCE_UNKNOWN"


def test_dashboard_reads_only_fresh_ctrader_market_session(tmp_path) -> None:
    module = status_resources
    now = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    quotes = tmp_path / "quotes"
    quotes.mkdir()
    session_path = quotes / "market-session.json"
    session_path.write_text(json.dumps({
        "schema": "xauusd.forward.market-session.v1",
        "symbol": "XAUUSD",
        "observed_at": now.isoformat(),
        "is_open": False,
        "next_open_time": (now + timedelta(hours=1)).isoformat(),
        "next_close_time": None,
    }), encoding="utf-8")

    session = module._broker_market_session(database, now)

    assert session == {
        "is_open": False,
        "observed_at": now.isoformat(),
        "next_open_time": (now + timedelta(hours=1)).isoformat(),
        "next_close_time": None,
    }
    assert module._broker_market_session(database, now + timedelta(seconds=21)) is None


def test_dashboard_distinguishes_weekly_close_from_missing_open_market_data() -> None:
    module = status_resources
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    monday = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)

    assert module._market_session_status(
        None, online=False, now=saturday,
    ) == "WEEKLY_CLOSED"
    assert module._market_session_status(
        None, online=False, now=monday,
    ) == "DATA_UNAVAILABLE"
    assert module._market_session_status(
        {"is_open": False}, online=False, now=monday,
    ) == "CLOSED"
    assert module._market_session_status(
        {"is_open": True}, online=True, now=monday,
    ) == "OPEN"
    assert module._market_session_status(
        {"is_open": True}, online=False, now=saturday,
    ) == "DATA_UNAVAILABLE"
    assert module._market_session_observed_at(
        None, market_session="WEEKLY_CLOSED", now=saturday,
    ) == saturday.isoformat()
    assert module._market_session_observed_at(
        None, market_session="DATA_UNAVAILABLE", now=monday,
    ) is None
    assert module._market_session_observed_at(
        {"observed_at": monday.isoformat()},
        market_session="OPEN",
        now=monday,
    ) == monday.isoformat()


def test_deployment_provenance_discovers_git_from_standalone_module_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = status_resources
    calls: list[Path] = []

    def fake_run(args, *, cwd, **_kwargs):
        calls.append(Path(cwd))
        command = tuple(args[1:])
        outputs = {
            ("rev-parse", "HEAD"): "abc123\n",
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "origin/main\n",
            ("rev-parse", "origin/main"): "abc123\n",
            ("status", "--porcelain", "--", "."): "",
        }
        return type("Result", (), {"stdout": outputs[command]})()

    monkeypatch.setattr(module, "MODULE_ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    provenance = module._deployment_provenance(datetime.now(UTC), None)

    assert provenance["status"] == "MATCHED"
    assert provenance["runtime_git_sha"] == "abc123"
    assert calls and set(calls) == {tmp_path}


def test_detached_runtime_compares_against_origin_main(tmp_path, monkeypatch) -> None:
    module = status_resources

    def fake_run(args, *, cwd, **_kwargs):
        command = tuple(args[1:])
        outputs = {
            ("rev-parse", "HEAD"): "abc123\n",
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "",
            ("rev-parse", "origin/main"): "abc123\n",
            ("status", "--porcelain", "--", "."): "",
        }
        return type("Result", (), {"stdout": outputs[command]})()

    monkeypatch.setattr(module, "MODULE_ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    provenance = module._deployment_provenance(datetime.now(UTC), None)

    assert provenance["expected_git_sha"] == "abc123"
    assert provenance["status"] == "MATCHED"


def test_learning_surfaces_rebuild_only_when_source_counts_change(monkeypatch) -> None:
    module = status_resources
    connection = sqlite3.connect(":memory:")
    for table in module._LEARNING_REVISION_TABLES:
        connection.execute(f"CREATE TABLE {table} (id INTEGER)")
    calls = {"learning": 0, "execution": 0}

    def learning(_connection):
        calls["learning"] += 1
        return {"generation": calls["learning"]}

    def execution(_ledger):
        calls["execution"] += 1
        return {"generation": calls["execution"]}

    monkeypatch.setattr(module, "learning_curve_payload", learning)
    monkeypatch.setattr(module, "execution_learning_status", execution)

    first = module._learning_surfaces(connection)
    second = module._learning_surfaces(connection)
    assert first == second
    assert calls == {"learning": 1, "execution": 1}

    connection.execute("INSERT INTO derived_outcomes VALUES (1)")
    third = module._learning_surfaces(connection)
    assert third != second
    assert calls == {"learning": 2, "execution": 2}
    connection.close()

