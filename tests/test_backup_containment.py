from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xauusd_forecaster import maintenance
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.training_owner import _process_start_token
from scripts import run_forward_collector as collector


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)


def _ledger(tmp_path: Path) -> ForwardLedger:
    runtime = tmp_path / "production-runtime" / ".local" / "forward"
    return ForwardLedger(runtime / "forward-evidence.sqlite3", now=NOW)


def _owner_evidence(
    backup_root: Path, target: Path, *, process_id: int, token: str,
    temporary: Path,
) -> None:
    owner_root, owner_path = maintenance._owner_paths(backup_root, target)
    owner_root.mkdir(parents=True)
    owner_path.write_text(json.dumps({
        "schema": "xauusd.forward.daily-backup-owner.v1",
        "process_id": process_id,
        "process_start_token": token,
        "temporary_path": str(temporary.resolve()),
        "target_path": str(target.resolve()),
        "acquired_at": NOW.isoformat(),
    }), encoding="utf-8")


def test_same_day_backup_is_receipted_and_heavy_work_runs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    backup_root = tmp_path / "backup-root"
    original = maintenance._perform_verified_backup
    heavy_calls = 0

    def counted(*args):
        nonlocal heavy_calls
        heavy_calls += 1
        return original(*args)

    monkeypatch.setattr(maintenance, "_perform_verified_backup", counted)
    first = maintenance.ensure_daily_forward_backup(
        ledger.path, backup_root, NOW, source_connection=ledger.connection,
    )
    second = maintenance.ensure_daily_forward_backup(
        ledger.path, backup_root, NOW, source_connection=ledger.connection,
    )

    assert (first.status, first.heavy_operation) == ("CREATED", True)
    assert (second.status, second.heavy_operation) == ("NO_CHANGE", False)
    assert heavy_calls == 1
    receipt = json.loads(first.receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == maintenance.BACKUP_RECEIPT_SCHEMA
    assert receipt["integrity_contract"].startswith("SQLITE_ONLINE_BACKUP")
    assert receipt["receipt_digest"]
    ledger.close()


def test_existing_atomic_legacy_final_is_adopted_without_heavy_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    backup_root = tmp_path / "backup-root"
    backup_root.mkdir()
    target = backup_root / "forward-evidence-20260830.sqlite3"
    destination = sqlite3.connect(target)
    ledger.connection.backup(destination)
    destination.close()
    monkeypatch.setattr(
        maintenance, "_perform_verified_backup",
        lambda *_: (_ for _ in ()).throw(AssertionError("heavy backup repeated")),
    )

    result = maintenance.ensure_daily_forward_backup(
        ledger.path, backup_root, NOW, source_connection=ledger.connection,
    )

    assert result.status == "LEGACY_ADOPTED"
    assert result.heavy_operation is False
    assert result.receipt_path.is_file()
    ledger.close()


def test_live_owner_returns_in_progress_without_second_heavy_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    backup_root = tmp_path / "backup-root"
    backup_root.mkdir()
    target = backup_root / "forward-evidence-20260830.sqlite3"
    token, alive = _process_start_token(os.getpid())
    assert alive is True and token
    temporary = backup_root / (
        f"{maintenance.BACKUP_TEMP_PREFIX}20260830-{os.getpid()}-active.tmp"
    )
    _owner_evidence(
        backup_root, target, process_id=os.getpid(), token=token,
        temporary=temporary,
    )
    monkeypatch.setattr(
        maintenance, "_perform_verified_backup",
        lambda *_: (_ for _ in ()).throw(AssertionError("second backup started")),
    )

    result = maintenance.ensure_daily_forward_backup(
        ledger.path, backup_root, NOW, source_connection=ledger.connection,
    )

    assert result.status == "IN_PROGRESS"
    assert result.heavy_operation is False
    ledger.close()


def test_dead_owner_cleans_only_its_new_temp_and_preserves_audited_stale_set(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    backup_root = tmp_path / "backup-root"
    backup_root.mkdir()
    target = backup_root / "forward-evidence-20260830.sqlite3"
    owned = backup_root / (
        f"{maintenance.BACKUP_TEMP_PREFIX}20260830-2147483647-dead.tmp"
    )
    owned.write_bytes(b"interrupted")
    preexisting = backup_root / ".forward-evidence-legacy-audited.tmp"
    preexisting.write_bytes(b"do-not-delete")
    _owner_evidence(
        backup_root, target, process_id=2147483647, token="gone",
        temporary=owned,
    )

    result = maintenance.ensure_daily_forward_backup(
        ledger.path, backup_root, NOW, source_connection=ledger.connection,
    )

    assert result.status == "CREATED_AFTER_INTERRUPTED_OWNER"
    assert result.recovered_interrupted_owner is True
    assert not owned.exists()
    assert preexisting.read_bytes() == b"do-not-delete"
    ledger.close()


def test_wrong_owner_target_and_tampered_receipt_fail_closed(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    backup_root = tmp_path / "backup-root"
    backup_root.mkdir()
    target = backup_root / "forward-evidence-20260830.sqlite3"
    token, alive = _process_start_token(os.getpid())
    assert alive is True and token
    temporary = backup_root / (
        f"{maintenance.BACKUP_TEMP_PREFIX}20260830-{os.getpid()}-wrong.tmp"
    )
    _owner_evidence(
        backup_root, target, process_id=os.getpid(), token=token,
        temporary=temporary,
    )
    _, owner_path = maintenance._owner_paths(backup_root, target)
    payload = json.loads(owner_path.read_text(encoding="utf-8"))
    payload["target_path"] = str(tmp_path / "other.sqlite3")
    owner_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BACKUP_OWNER_EVIDENCE_INVALID"):
        maintenance.ensure_daily_forward_backup(
            ledger.path, backup_root, NOW, source_connection=ledger.connection,
        )
    owner_path.parent.joinpath("owner.json").unlink()
    owner_path.parent.rmdir()

    created = maintenance.ensure_daily_forward_backup(
        ledger.path, backup_root, NOW, source_connection=ledger.connection,
    )
    receipt = json.loads(created.receipt_path.read_text(encoding="utf-8"))
    receipt["utc_day"] = "20990101"
    created.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BACKUP_RECEIPT_TAMPERED"):
        maintenance.ensure_daily_forward_backup(
            ledger.path, backup_root, NOW, source_connection=ledger.connection,
        )
    ledger.close()


def test_completion_receipt_rejects_another_source_database(tmp_path: Path) -> None:
    first = _ledger(tmp_path / "first")
    backup_root = tmp_path / "backup-root"
    maintenance.ensure_daily_forward_backup(
        first.path, backup_root, NOW, source_connection=first.connection,
    )
    second = _ledger(tmp_path / "second")

    with pytest.raises(RuntimeError, match="BACKUP_STALE_SOURCE:path"):
        maintenance.ensure_daily_forward_backup(
            second.path, backup_root, NOW, source_connection=second.connection,
        )
    first.close()
    second.close()


def test_concurrent_restart_storm_starts_only_one_heavy_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    database = ledger.path
    ledger.close()
    backup_root = tmp_path / "backup-root"
    entered = threading.Event()
    release = threading.Event()
    original = maintenance._perform_verified_backup
    heavy_calls = 0

    def slow(*args):
        nonlocal heavy_calls
        heavy_calls += 1
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(maintenance, "_perform_verified_backup", slow)
    results: list[maintenance.DailyBackupResult] = []
    first = threading.Thread(target=lambda: results.append(
        maintenance.ensure_daily_forward_backup(database, backup_root, NOW)
    ))
    first.start()
    assert entered.wait(5)
    for _ in range(4):
        results.append(maintenance.ensure_daily_forward_backup(
            database, backup_root, NOW,
        ))
    release.set()
    first.join(5)

    assert heavy_calls == 1
    assert sum(result.status == "IN_PROGRESS" for result in results) == 4
    assert sum(result.status == "CREATED" for result in results) == 1


def test_failed_heavy_attempt_is_receipted_and_not_repeated_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    backup_root = tmp_path / "backup-root"
    heavy_calls = 0

    def fail(*_args):
        nonlocal heavy_calls
        heavy_calls += 1
        raise OSError("simulated backup failure")

    monkeypatch.setattr(maintenance, "_perform_verified_backup", fail)
    with pytest.raises(OSError, match="simulated backup failure"):
        maintenance.ensure_daily_forward_backup(
            ledger.path, backup_root, NOW, source_connection=ledger.connection,
        )
    with pytest.raises(RuntimeError, match="BACKUP_PREVIOUS_ATTEMPT_FAILED"):
        maintenance.ensure_daily_forward_backup(
            ledger.path, backup_root, NOW, source_connection=ledger.connection,
        )
    assert heavy_calls == 1
    assert list(backup_root.glob("*.failure.json"))
    ledger.close()


def test_collector_backup_is_eligible_only_after_generation_and_heartbeat() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "run_forward_collector.py"
    ).read_text(encoding="utf-8")
    viability = source.index("startup_plan = startup_reconciliation_plan")
    running = source.index(
        'write_runtime_heartbeat(status_file, service="collector")'
    )
    synchronous_backup = source.index("backup_result = ensure_daily_forward_backup")
    background_backup = source.index("backup_owner.start()")
    archive = source.rindex("archive_completed_quote_days")

    assert viability < running < synchronous_backup
    assert viability < running < background_backup
    assert viability < running < archive
    assert "backup_forward_ledger" not in source


def test_real_collector_viability_failure_never_enters_backup_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_called = False

    def backup(*_args, **_kwargs):
        nonlocal backup_called
        backup_called = True
        raise AssertionError("backup ran before viability")

    monkeypatch.setattr(
        collector.ForwardEngine, "collect_news", lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        collector, "reconcile_news_contract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("active generation invalid")
        ),
    )
    monkeypatch.setattr(collector, "ensure_daily_forward_backup", backup)
    monkeypatch.setattr(
        collector, "authoritative_runtime_root", lambda value: Path(value).resolve(),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_forward_collector.py", "--state-root",
        str(tmp_path / "production-runtime" / ".local" / "forward"), "--once",
    ])

    with pytest.raises(RuntimeError, match="active generation invalid"):
        collector.main()
    assert backup_called is False
