"""Real package and production-entrypoint composition from unrelated roots."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_canonical_owners_import_and_reopen_schema_outside_repository_cwd(tmp_path):
    """Execute package composition without inherited cwd or production state."""
    source = r'''
import importlib, json, pathlib, pkgutil, sys
sys.path.insert(0, sys.argv[1])
import xauusd_forecaster
before = set(pathlib.Path.cwd().iterdir())
for area in ("ai", "assistant", "dashboard", "decision", "evidence", "news", "runtime", "training"):
    package = importlib.import_module("xauusd_forecaster." + area)
    for item in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        importlib.import_module(item.name)
assert set(pathlib.Path.cwd().iterdir()) == before, "IMPORT_MUTATED_WORKING_DIRECTORY"
for name, module in tuple(sys.modules.items()):
    if name.startswith("xauusd_forecaster.") and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve()), (name, module.__file__)
from xauusd_forecaster.news.semantics import contracts
assert json.loads(contracts._SCHEMA_PATH.read_text(encoding="utf-8"))
from xauusd_forecaster.evidence.ledger import ForwardLedger
path = pathlib.Path.cwd() / "isolated.sqlite3"
for _ in range(2):
    ledger = ForwardLedger(path)
    assert ledger.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    ledger.close()
from xauusd_forecaster.dashboard import status_resources
from xauusd_forecaster.dashboard.sync import resources
assert status_resources.MODULE_ROOT == pathlib.Path(sys.argv[1]).resolve()
assert resources.MODULE_ROOT == pathlib.Path(sys.argv[1]).resolve()
'''
    result = subprocess.run([sys.executable, '-c', source, str(ROOT)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('entry', [
    'run_forward_collector.py', 'run_news_annotator.py',
    'run_dashboard_api.py', 'run_dashboard_sync.py',
])
def test_production_entrypoint_resolves_canonical_owners_before_work(tmp_path, entry):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / entry), '--help'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout
    assert list(tmp_path.iterdir()) == []
