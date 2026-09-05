#!/usr/bin/env python
"""Exact-source real API/continuous Sync rehearsal on an existing isolated copy.

This resource producer is not a Switch/Observe or Cloudflare qualification.
The Worker store executes against the existing test D1 adapter, not production.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], timeout=15, creationflags=NO_WINDOW,
    ).decode("utf-8").strip()


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-copy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database, output = args.database_copy.resolve(strict=True), args.output.resolve()
    # Deliberately narrow retained-copy namespace. Never accept the production
    # repository/runtime, a source checkout, or an arbitrary SQLite filename.
    if (database.parent.name != "rehearsal" or database.name != "production-online.sqlite3"
            or any(part.lower() in {".local", "forward", "xauusd-forecaster-runtime"}
                   for part in database.parts)
            or database.is_relative_to(ROOT) or output.is_relative_to(ROOT)):
        raise ValueError("ISOLATED_COPY_PATH_REQUIRED")
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("CLEAN_SOURCE_REQUIRED")
    if output.exists():
        raise ValueError("EVIDENCE_OUTPUT_ALREADY_EXISTS")
    revision = git("rev-parse", "HEAD")
    report = {
        "state": "NOT_RUN", "source_revision": revision, "target_revision": revision,
        "source_dirty": False, "source_tree": git("rev-parse", "HEAD^{tree}"),
        "execution_boundary": "REAL_API_CONTINUOUS_SYNC_ISOLATED_ACK",
        "producer": "scripts/rehearse_news_recovery_copy.py",
        "python": sys.version, "platform": platform.platform(),
        "installed_python_dependencies": dict(sorted(
            (distribution.metadata["Name"], distribution.version)
            for distribution in importlib.metadata.distributions()
            if distribution.metadata.get("Name")
        )),
        "input_database_copy": {"path": str(database), "size": database.stat().st_size,
                                "sha256": digest_file(database)},
        "dependency_inputs": {str(path.relative_to(ROOT)): digest_file(path)
                              for path in (ROOT / "pyproject.toml", ROOT / "web/package-lock.json")},
        "full_switch_observe": "NOT_RUN", "old_query_reproduced": "NOT_RUN",
        "semantic_equality_verified": "NOT_RUN", "production_evidence": False,
        "provider_boundary": "real Worker store with in-memory SQLite D1 test adapter",
        "critical_status_boundary": "declared synthetic session/heartbeat input",
        "cleanup": "NOT_RUN",
    }
    sys.path.insert(0, str(ROOT))
    api, sync = load("recovery_copy_api", "run_dashboard_api.py"), load("recovery_copy_sync", "run_dashboard_sync.py")
    from xauusd_forecaster.news_projection import receipt_payload_hash
    node = shutil.which("node")
    if not node:
        raise RuntimeError("NODE_UNAVAILABLE")
    report["node"] = subprocess.check_output([node, "--version"], timeout=5, creationflags=NO_WINDOW).decode().strip()
    counts, reads, posted_bytes = Counter(), [], []
    stop = threading.Event()
    servers, threads = [], []
    worker = None
    temporary_owner = None
    previous_cert = os.environ.get("SSL_CERT_FILE")
    started = time.monotonic()
    try:
        temporary_owner = tempfile.TemporaryDirectory(prefix="xauusd-news-recovery-copy-")
        with nullcontext(temporary_owner.name) as temporary:
            runtime = Path(temporary)
            cert, key = runtime / "cert.pem", runtime / "key.pem"
            openssl = shutil.which("openssl") or str(Path(shutil.which("git")).parents[1] / "usr/bin/openssl.exe")
            subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=isolated-fixture",
                            "-addext", "subjectAltName=IP:127.0.0.1"], check=True,
                           capture_output=True, timeout=15, creationflags=NO_WINDOW)
            os.environ["SSL_CERT_FILE"] = str(cert)
            responses = queue.Queue(maxsize=2)
            worker = subprocess.Popen(
                [node, "--import", (ROOT / "web/tests/register-cloudflare-worker-loader.mjs").as_uri(),
                 str(ROOT / "tests/fixtures/news_evidence_ack_worker.mjs"), "--serve"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", creationflags=NO_WINDOW,
            )
            def read_worker():
                for line in worker.stdout:
                    responses.put(line, timeout=15)
            reader = threading.Thread(target=read_worker, daemon=True)
            reader.start()
            threads.append(reader)
            worker_lock = threading.Lock()

            class Local(api.Handler):
                def log_message(self, *_):
                    pass

                def do_GET(self):
                    if self.path == "/api/critical-status":
                        body = json.dumps({"generated_at": datetime.now(UTC).isoformat(),
                                           "system": {"online": True, "quote_age_seconds": 1}}).encode()
                        counts["local_heartbeat"] += 1
                        self._write_json(200, body)
                        return
                    if not self.path.startswith("/api/news-evidence"):
                        self.send_error(403)
                        return
                    before = time.monotonic()
                    try:
                        super().do_GET()
                    finally:
                        reads.append(time.monotonic() - before)
                        counts["local_payload"] += 1
            Local.database = database

            class Remote(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_POST(self):
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 80_000 or self.path not in {"/api/ingest", "/api/news-evidence"}:
                        self.send_error(400)
                        return
                    raw = self.rfile.read(size)
                    if self.path == "/api/ingest":
                        counts["remote_heartbeat"] += 1
                        result = {"ok": True}
                    else:
                        request = json.loads(raw)
                        operation = next(key for key in ("prepare_snapshot", "items", "activate_snapshot", "cleanup_active_snapshot") if key in request)
                        counts[operation] += 1
                        posted_bytes.append(size)
                        with worker_lock:
                            worker.stdin.write(json.dumps(base64.b64encode(raw).decode("ascii")) + "\n")
                            worker.stdin.flush()
                            answer = json.loads(responses.get(timeout=15))
                        if "error" in answer:
                            self.send_error(409, "isolated Worker rejected operation")
                            return
                        result = answer["result"]
                    body = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            local, remote = ThreadingHTTPServer(("127.0.0.1", 0), Local), ThreadingHTTPServer(("127.0.0.1", 0), Remote)
            servers.extend((local, remote))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            remote.socket = context.wrap_socket(remote.socket, server_side=True)
            for server in servers:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                threads.append(thread)
            config = sync.configure_runtime_state({
                "local_status_url": f"http://127.0.0.1:{local.server_port}/api/status",
                "targets": [{"name": "cloudflare", "token": "isolated-fixture",
                             "remote_ingest_url": f"https://127.0.0.1:{remote.server_port}/api/ingest"}],
            }, runtime)
            target = sync.configured_targets(config)[0]
            now, version = datetime.now(UTC), str(uuid.uuid4())
            schedule = {"schema_version": 1, "resources": {p[0]: {
                "next_run_at": (now + timedelta(hours=1)).isoformat()} for p in sync.RESOURCE_POLICIES}}
            sync._write_news_sync_state(Path(target["resource_schedule_state_file"]), schedule)
            request = {
                "schema_version": sync.DEFERRED_PROJECTION_CONTRACT,
                "request_id": str(uuid.uuid4()), "transaction_id": str(uuid.uuid4()),
                "validation_key": version + ":" + revision, "worker_version_id": version,
                "producer_revision": revision, "target": "cloudflare", "required_after": now.isoformat(),
                "created_at": now.isoformat(), "routes": ["/api/news-evidence"],
                "collector_recovery": {"incident": "COLLECTOR_CLOCK_EVENT_ATOMICITY",
                    "broken_revision": "ffe1de29c0891cc3a3cf3d602f3d3ee657faa9b8", "target_revision": revision},
            }
            configuration = {
                "config": config,
                "interval_seconds": 30, "schedule": schedule, "request": request,
                "local_boundary": "loopback", "provider_boundary": "loopback TLS",
            }
            report["configuration"] = configuration
            report["configuration_sha256"] = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
            sync._write_news_sync_state(Path(config["deferred_projection_request_file"]), request)
            status_file = runtime / "status.json"
            def monitor():
                while not stop.wait(.2):
                    receipt = sync._read_news_sync_state(Path(config["deferred_projection_receipt_file"]))
                    status = sync._read_news_sync_state(status_file)
                    observations = {row.get("resource"): row.get("status") for row in status.get("resource_observations", [])}
                    if (receipt.get("state") == "COMPLETED" and status.get("status") == "OK"
                            and observations.get("news_evidence") == "OK"
                            and observations.get("deferred_projection") == "OK") or time.monotonic() - started >= 180:
                        stop.set()
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
            threads.append(watcher)
            sync.run_continuous_sync(config, status_file=status_file, stop_event=stop)
            ack = sync._read_news_sync_state(Path(target["news_evidence_state_file"]))
            receipt = sync._read_news_sync_state(Path(config["deferred_projection_receipt_file"]))
            status = sync._read_news_sync_state(status_file)
            with worker_lock:
                worker.stdin.write(json.dumps({"inspect": True}) + "\n")
                worker.stdin.flush()
                remote_content = json.loads(responses.get(timeout=15))["result"]
            with api._NEWS_EVIDENCE_CACHE_LOCK:
                source_rows = list(api._NEWS_EVIDENCE_CACHE["items"])
            source_digest = receipt_payload_hash(source_rows)
            observations = {row.get("resource"): row.get("status")
                            for row in status.get("resource_observations", [])}
            report.update(ack_verified=bool(
                receipt.get("state") == "COMPLETED" and ack.get("active_snapshot_id")
                and ack.get("active_snapshot_id") == receipt.get("news_recovery", {}).get("snapshot_id")
                and status.get("status") == "OK"
                and observations.get("news_evidence") == "OK"
                and observations.get("deferred_projection") == "OK"
                and counts["remote_heartbeat"] >= 2
                and remote_content["snapshot_id"] == ack.get("active_snapshot_id")
                and remote_content["count"] == remote_content["expected_count"] == ack.get("record_count")
                and remote_content["content_digest"] == source_digest), records=ack.get("record_count"),
                snapshot=ack.get("active_snapshot_id"), final_sync_status=status.get("status"))
            report["output_equivalence"] = {"source_content_digest": source_digest, **remote_content}
            if not report["ack_verified"]:
                raise RuntimeError("CONTINUOUS_SYNC_ACK_INCOMPLETE")
            if git("rev-parse", "HEAD") != revision or git("status", "--porcelain=v1", "--untracked-files=all"):
                raise RuntimeError("SOURCE_CHANGED_DURING_REHEARSAL")
            report["state"] = "API_SYNC_COPY_PASSED"
    except Exception as error:
        report.update(state="FAILED", failure={"type": type(error).__name__, "reason": str(error)[:500]})
    finally:
        stop.set()
        for server in servers:
            server.shutdown()
            server.server_close()
        if worker is not None:
            worker.stdin.close()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.terminate()
                worker.wait(timeout=5)
        for thread in threads:
            thread.join(5)
        if temporary_owner is not None and all(not thread.is_alive() for thread in threads):
            temporary_owner.cleanup()
        if previous_cert is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = previous_cert
        report.update(elapsed_seconds=round(time.monotonic() - started, 3), operations=dict(counts),
                      local_get_count=len(reads), max_local_get_seconds=max(reads, default=0),
                      remote_posts=len(posted_bytes), max_post_bytes=max(posted_bytes, default=0),
                      heartbeat_count=counts["remote_heartbeat"],
                      cleanup="PASSED" if all(not thread.is_alive() for thread in threads) else "UNRESOLVED",
                      d1_rows_read="NOT_MEASURED", d1_rows_written="NOT_MEASURED")
        if report["cleanup"] != "PASSED":
            report["state"] = "FAILED"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"state": report["state"], "output": str(output)}))
    return 0 if report["state"] == "API_SYNC_COPY_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
