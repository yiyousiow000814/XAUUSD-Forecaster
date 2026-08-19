from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_production_shape.py"


@pytest.mark.parametrize(
    ("http_status", "body", "expected_exit", "expected_code"),
    [
        (
            503,
            {"error": "dashboard snapshot refresh is still running"},
            75,
            "STATUS_SNAPSHOT_REFRESH_IN_PROGRESS",
        ),
        (503, {"error": "database unavailable"}, 2, "STATUS_ENDPOINT_HTTP_ERROR"),
        (200, b"not-json", 2, "STATUS_RESPONSE_INVALID"),
    ],
)
def test_status_failure_is_structured_and_only_known_refresh_is_deferred(
    http_status, body, expected_exit, expected_code,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            serialized = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(http_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(serialized)))
            self.end_headers()
            self.wfile.write(serialized)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--status-url",
                f"http://127.0.0.1:{server.server_port}/api/critical-status",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == expected_exit
    assert json.loads(result.stdout)["error_code"] == expected_code
    assert "Traceback" not in result.stderr


def test_status_probe_rejects_non_loopback_url_before_transport() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--status-url",
            "http://example.com/api/critical-status",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["error_code"] == "STATUS_ENDPOINT_URL_INVALID"
