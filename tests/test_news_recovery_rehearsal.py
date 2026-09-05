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
