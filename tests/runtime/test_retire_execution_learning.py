"""Retirement deletes only the explicitly authorized research family."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from xauusd_forecaster.evidence.ledger import ForwardLedger

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/maintenance/retire_execution_learning.py"
spec = importlib.util.spec_from_file_location("retirement", SCRIPT)
retirement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retirement)


def test_real_cli_cleanup_preserves_direction_and_is_restart_safe(tmp_path):
    database = tmp_path / "forward-evidence.sqlite3"
    ledger = ForwardLedger(database)
    ledger.connection.execute("CREATE TABLE preserved_direction (value TEXT)")
    ledger.connection.execute("INSERT INTO preserved_direction VALUES ('untouched')")
    for table in retirement.TABLES:
        ledger.connection.execute(f"CREATE TABLE {table} (id INTEGER)")
        ledger.connection.execute(f"INSERT INTO {table} VALUES (1)")
        ledger.connection.execute(f"CREATE TRIGGER prevent_delete_{table} BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END")
    ledger.connection.commit()
    ledger.close()
    for name in retirement.ARTIFACT_DIRS:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "model.json").write_text("{}")
    (tmp_path / "models-v2").mkdir()
    (tmp_path / "models-v2" / "direction.json").write_text("keep")
    for apply in (False, True, True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--runtime-state-root", str(tmp_path), *(["--apply"] if apply else [])],
            cwd=tmp_path, capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["applied"] == apply
    reopened = ForwardLedger(database)
    assert reopened.connection.execute("SELECT value FROM preserved_direction").fetchone()[0] == "untouched"
    assert not reopened.connection.execute("SELECT name FROM sqlite_master WHERE name GLOB 'execution_*'").fetchall()
    assert all(not (tmp_path / name).exists() for name in retirement.ARTIFACT_DIRS)
    assert (tmp_path / "models-v2" / "direction.json").read_text() == "keep"
    reopened.close()


def test_cleanup_fails_closed_before_mutation_on_link(tmp_path):
    ledger = ForwardLedger(tmp_path / "forward-evidence.sqlite3")
    ledger.close()
    target = tmp_path / "keep"
    target.mkdir()
    link = tmp_path / retirement.ARTIFACT_DIRS[0]
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit test symlinks")
    with pytest.raises(ValueError, match="reparse"):
        retirement.cleanup(tmp_path, apply=True)
    assert target.exists()
