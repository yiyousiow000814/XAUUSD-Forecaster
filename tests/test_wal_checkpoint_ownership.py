from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.sqlite_wal import (
    FORWARD_WAL_SIZE_LIMIT_BYTES,
    checkpoint_forward_wal,
    open_forward_writer_connection,
)


def _receipt_is_valid(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = str(payload.pop("receipt_digest"))
    actual = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return digest == actual


def test_forward_writer_policy_removes_checkpoint_work_from_commit_path(
    tmp_path: Path,
) -> None:
    connection = open_forward_writer_connection(tmp_path / "forward.sqlite3")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
        assert (
            connection.execute("PRAGMA journal_size_limit").fetchone()[0]
            == FORWARD_WAL_SIZE_LIMIT_BYTES
        )
    finally:
        connection.close()


def test_checkpoint_preserves_reader_then_truncates_after_full_backfill(
    tmp_path: Path,
) -> None:
    database = tmp_path / "forward.sqlite3"
    writer = open_forward_writer_connection(database)
    writer.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, body BLOB NOT NULL)")
    writer.commit()
    reader = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    reader.execute("BEGIN")
    assert reader.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    writer.executemany(
        "INSERT INTO evidence(body) VALUES (?)",
        [(b"x" * 8192,) for _ in range(32)],
    )
    writer.commit()

    pinned = checkpoint_forward_wal(
        database, tmp_path, datetime.now(UTC), size_limit_bytes=4096,
    )
    assert pinned.status == "READER_PINNED"
    assert pinned.pending_frames > 0
    assert pinned.truncate_attempted is False
    assert _receipt_is_valid(pinned.state_path)

    reader.rollback()
    reader.close()
    completed = checkpoint_forward_wal(
        database, tmp_path, datetime.now(UTC), size_limit_bytes=4096,
    )
    assert completed.status == "TRUNCATED"
    assert completed.pending_frames == 0
    assert completed.truncate_attempted is True
    assert completed.wal_bytes_after <= 4096
    assert writer.execute("SELECT count(*) FROM evidence").fetchone()[0] == 32
    writer.close()


def test_checkpoint_rejects_database_outside_authoritative_runtime_root(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    database = tmp_path / "outside.sqlite3"
    connection = open_forward_writer_connection(database)
    connection.close()
    with pytest.raises(ValueError, match="one file under runtime root"):
        checkpoint_forward_wal(database, runtime_root, datetime.now(UTC))


def test_forward_ledger_uses_shared_writer_policy(tmp_path: Path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    try:
        assert ledger.connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
        assert (
            ledger.connection.execute("PRAGMA journal_size_limit").fetchone()[0]
            == FORWARD_WAL_SIZE_LIMIT_BYTES
        )
    finally:
        ledger.close()


def test_every_runtime_writer_crosses_shared_wal_policy_boundary() -> None:
    root = Path(__file__).resolve().parents[1]
    owners = {
        "xauusd_forecaster/dashboard_read_models.py": "open_forward_writer_connection",
        "xauusd_forecaster/forward_ledger.py": "open_forward_writer_connection",
        "xauusd_forecaster/news_pruning.py": "open_forward_writer_connection",
        "xauusd_forecaster/news_retrieval.py": "open_forward_writer_connection",
        "xauusd_forecaster/training_owner.py": "open_forward_writer_connection",
        "scripts/run_dashboard_api.py": "open_forward_writer_connection",
        "scripts/migrate_runtime_artifact_paths.py": "open_forward_writer_connection",
    }
    for relative, boundary in owners.items():
        assert boundary in (root / relative).read_text(encoding="utf-8"), relative

    collector = (root / "scripts/run_forward_collector.py").read_text(encoding="utf-8")
    annotator = (root / "scripts/run_news_annotator.py").read_text(encoding="utf-8")
    assert "ForwardWalCheckpointOwner" in collector
    assert "ForwardWalCheckpointOwner" not in annotator
