"""Isolation and cleanup contracts of the retained-copy recovery producer."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("row_budget", [100, 1])
def test_query_oracle_compares_real_duplicate_null_receipts(tmp_path, row_budget, monkeypatch):
    import sqlite3
    from datetime import datetime, timezone
    spec = importlib.util.spec_from_file_location("query_rehearsal", ROOT / "scripts/rehearse_news_recovery_copy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    api = module.load("query_oracle_api", "run_dashboard_api.py")
    # No live event rows: production display must use the immutable catalog.
    monkeypatch.setattr(api, "event_evidence_rows_from_connection", lambda *_args: [])
    database = tmp_path / "fixture.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE news_model_visibility_receipts_v1 (
                source_decision_id TEXT,decision_time TEXT,model_identity TEXT,
                model_version TEXT,event_key TEXT,event_source_hash TEXT);
            CREATE TABLE news_model_visibility_events_v1 (
                event_source_hash TEXT,event_key TEXT,canonical_headline TEXT,
                canonical_source TEXT,source_published_time TEXT,
                collector_first_seen_time TEXT,topics_json TEXT,evidence_grade TEXT);
            INSERT INTO news_model_visibility_events_v1 VALUES
                ('h1','old','headline','source','2026-09-01','2026-09-01','[]','PRIMARY'),
                ('h2','new','headline','source','2026-09-01','2026-09-01','[]','PRIMARY');
            INSERT INTO news_model_visibility_receipts_v1 VALUES
                ('d','2026-09-01','FULL','v1','old','h1'),
                ('d','2026-09-02','FULL','v2','new','h2'),
                (NULL,NULL,NULL,NULL,'old',NULL);
            CREATE TABLE news_only_visibility_receipts_v1 AS
                SELECT * FROM news_model_visibility_receipts_v1;
        """)
    if row_budget == 1:
        with pytest.raises(RuntimeError, match="BUDGET_EXHAUSTED"):
            module.compare_news_queries(database, api, datetime.now(timezone.utc), row_budget=row_budget)
    else:
        result = module.compare_news_queries(database, api, datetime.now(timezone.utc), row_budget=row_budget)
        assert result["receipt_count"] == 6
        assert result["compared_display_count"] == 1
        assert result["semantic_equality_verified"] is True
        assert result["legacy_query_state"] == "COMPLETED"
        assert result["legacy_display_equal"] is True
        assert result["optimized_digest"] == result["independent_digest"]


def test_request_handler_must_exit_before_rehearsal_cleanup_can_pass():
    spec = importlib.util.spec_from_file_location("recovery_rehearsal", ROOT / "scripts/rehearse_news_recovery_copy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    started, release = threading.Event(), threading.Event()

    class Handler(module.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            started.set()
            release.wait(3)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = module.OwnedHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    responses = []
    def get():
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=4) as response:
            responses.append(response.status)
    client = threading.Thread(target=get, daemon=True)
    client.start()
    try:
        assert started.wait(2)
        server.shutdown()
        server.server_close()
        owned = server.owned_requests()
        assert len(owned) == 1
        assert not module.join_owned_threads(owned, timeout=.01)
        release.set()
        assert module.join_owned_threads(owned, timeout=2)
        client.join(2)
        assert responses == [200]
        assert not server.owned_requests()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        serving.join(2)
        client.join(2)
        assert module.join_owned_threads(server.owned_requests())


@pytest.mark.parametrize("provider", [
    "https://example.invalid:443", "http://127.0.0.1:1",
    "https://127.0.0.1:1/path", "https://user:secret@127.0.0.1:1",
])
def test_independent_consumer_rejects_non_owned_endpoint_before_network(tmp_path, provider):
    root = tmp_path.resolve()
    (root / "fixture-owned.json").write_text(json.dumps({"root": str(root)}), encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(ROOT / "tests/fixtures/deferred_recovery_consumer.py"),
        "--runtime-root", str(root), "--fixture-provider", provider,
    ], capture_output=True, text=True, timeout=5,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode != 0
    assert "ISOLATED_CONSUMER_LOOPBACK_REQUIRED" in result.stderr
