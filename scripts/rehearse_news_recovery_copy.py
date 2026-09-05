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
import sqlite3
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


def compare_news_queries(database, api, instant, *, deadline_seconds=30, row_budget=2_000_000):
    """Read one frozen transaction; independently aggregate actual receipt rows.

    This proves query/display equivalence, not a historical HTTP timeout.
    The legacy SQL is retained as a bounded oracle; interruption is not equality.
    """
    started = time.monotonic()
    captured = {}

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection(sqlite3.Connection):
        oracle_rows = None

        def execute(self, sql, parameters=(), /):
            if "SELECT canonical_event_key AS event_key" not in sql:
                return super().execute(sql, parameters)
            if self.oracle_rows is not None:
                return Rows(self.oracle_rows)
            result = super().execute(sql, parameters).fetchall()
            captured.update(sql=sql, parameters=parameters, rows=[dict(row) for row in result])
            return Rows(result)

    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, factory=Connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    connection.set_progress_handler(lambda: int(time.monotonic() - started > deadline_seconds), 10000)
    try:
        evidence = api.event_evidence_rows_from_connection(connection, instant)
        optimized = api._news_evidence_display_rows(connection, evidence)
        if "sql" not in captured:
            raise RuntimeError("OPTIMIZED_QUERY_DID_NOT_COMPLETE")
        aliases = json.loads(captured["parameters"][0])
        tables = ["news_model_visibility_receipts_v1"]
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='news_only_visibility_receipts_v1'").fetchone():
            tables.append("news_only_visibility_receipts_v1")
        sql = " UNION ALL ".join(
            "SELECT event_key,source_decision_id,event_source_hash,decision_time,model_identity,model_version FROM " + table
            for table in tables
        )
        groups, receipt_count, distinct_count = {}, 0, 0
        for row in connection.execute(sql):
            receipt_count += 1
            if receipt_count > row_budget or time.monotonic() - started > deadline_seconds:
                raise RuntimeError("QUERY_ORACLE_BUDGET_EXHAUSTED")
            key = aliases.get(row["event_key"], row["event_key"])
            group = groups.setdefault(key, {"count": 0, "times": [None, None],
                **{field: set() for field in ("source_decision_id", "event_source_hash", "model_identity", "model_version")}})
            group["count"] += 1
            for field in ("source_decision_id", "event_source_hash", "model_identity", "model_version"):
                value = row[field]
                if value is not None and value not in group[field]:
                    group[field].add(value)
                    distinct_count += 1
            if distinct_count > row_budget:
                raise RuntimeError("QUERY_ORACLE_DISTINCT_BUDGET_EXHAUSTED")
            value = row["decision_time"]
            if value is not None:
                lower, upper = group["times"]
                group["times"] = [value if lower is None else min(lower, value),
                                  value if upper is None else max(upper, value)]
        connection.oracle_rows = [{"event_key": key, "frozen_model_uses": group["count"],
            "frozen_decisions": len(group["source_decision_id"]),
            "frozen_versions": len(group["event_source_hash"]),
            "first_model_decision_time": group["times"][0], "last_model_decision_time": group["times"][1],
            "model_identities": ",".join(sorted(group["model_identity"])),
            "model_versions": ",".join(sorted(group["model_version"]))}
            for key, group in groups.items()]
        independent = api._news_evidence_display_rows(connection, evidence)
        def digest(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        result = {"frozen_at": instant.isoformat(), "receipt_count": receipt_count,
            "compared_display_count": len(optimized), "semantic_equality_verified": optimized == independent,
            "optimized_digest": digest(optimized), "independent_digest": digest(independent),
            "durable_digest": digest(api._durable_news_evidence_rows(optimized)),
            "sql_sha256": digest(captured["sql"]), "parameters_sha256": digest(captured["parameters"])}
        legacy = captured["sql"][captured["sql"].index("SELECT canonical_event_key AS event_key"):].replace(
            "LEFT JOIN event_aliases AS alias", "LEFT JOIN json_each(?) AS alias")
        legacy_started = time.monotonic()
        connection.set_progress_handler(lambda: int(time.monotonic() - legacy_started > deadline_seconds), 10000)
        try:
            legacy_rows = sqlite3.Connection.execute(connection, legacy, captured["parameters"]).fetchall()
            connection.oracle_rows = legacy_rows
            result["legacy_display_equal"] = api._news_evidence_display_rows(connection, evidence) == optimized
            result["legacy_query_state"] = "COMPLETED"
        except sqlite3.OperationalError as error:
            if str(error) != "interrupted":
                raise
            result["legacy_query_state"] = "BOUNDED_INTERRUPT"
            result["legacy_display_equal"] = "UNKNOWN"
        result["legacy_seconds"] = round(time.monotonic() - legacy_started, 3)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result
    finally:
        connection.set_progress_handler(None, 0)
        connection.rollback()
        connection.close()


class OwnedHTTPServer(ThreadingHTTPServer):
    """Keep exact request-thread ownership, including abnormal cleanup paths."""
    def __init__(self, *args, **kwargs):
        self._request_owners = set()
        self._request_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        thread = threading.Thread(target=self.process_request_thread,
                                  args=(request, client_address), daemon=True)
        # Register before start, so shutdown cannot overlook a queued handler.
        with self._request_lock:
            self._request_owners.add(thread)
            thread.start()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._request_lock:
                self._request_owners.discard(threading.current_thread())

    def owned_requests(self):
        with self._request_lock:
            return tuple(self._request_owners)


def join_owned_threads(threads, timeout=5):
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    return all(not thread.is_alive() for thread in threads)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-copy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--historical-failure-evidence", type=Path,
                        help="Retained reviewed incident artifact; never a new timeout assertion")
    parser.add_argument("--query-comparison-only", action="store_true",
                        help="Development query evidence only; never a release admission receipt")
    args = parser.parse_args()
    database, output = args.database_copy.resolve(strict=True), args.output.resolve()
    # Deliberately narrow retained-copy namespace. Never accept the production
    # repository/runtime, a source checkout, or an arbitrary SQLite filename.
    if (database.parent.name != "rehearsal" or database.name != "production-online.sqlite3"
            or any(part.lower() in {".local", "forward", "xauusd-forecaster-runtime"}
                   for part in database.parts)
            or database.is_relative_to(ROOT) or output.is_relative_to(ROOT)):
        raise ValueError("ISOLATED_COPY_PATH_REQUIRED")
    if output.exists():
        raise ValueError("EVIDENCE_OUTPUT_ALREADY_EXISTS")
    dirty = bool(git("status", "--porcelain=v1", "--untracked-files=all"))
    if args.query_comparison_only:
        sys.path.insert(0, str(ROOT))
        before = database.stat()
        result = {"state": "NOT_RUN", "source_revision": git("rev-parse", "HEAD"),
            "source_dirty": dirty, "production_evidence": False,
            "producer_sha256": digest_file(Path(__file__)),
            "api_sha256": digest_file(ROOT / "scripts/run_dashboard_api.py"),
            "input_database": {"path": str(database), "size": before.st_size,
                "mtime_ns": before.st_mtime_ns, "whole_file_sha256": "NOT_RECOMPUTED"},
            "old_http_timeout_reproduced": "NOT_RUN"}
        try:
            result["comparison"] = compare_news_queries(
                database, load("query_comparison_api", "run_dashboard_api.py"), datetime.now(UTC))
            result["state"] = "COMPARED" if result["comparison"]["semantic_equality_verified"] else "MISMATCH"
        except Exception as error:
            result.update(state="UNRESOLVED", failure=type(error).__name__ + ":" + str(error)[:1024])
        after = database.stat()
        result["input_stat_unchanged"] = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    if dirty:
        raise ValueError("CLEAN_SOURCE_REQUIRED")
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
        "historical_failure_evidence_sha256": "NOT_RUN",
        "semantic_equality_verified": "NOT_RUN", "production_evidence": False,
        "provider_boundary": "real Worker store with in-memory SQLite D1 test adapter",
        "critical_status_boundary": "declared synthetic session/heartbeat input",
        "cleanup": "NOT_RUN",
    }
    if args.historical_failure_evidence is not None:
        historical = args.historical_failure_evidence.resolve(strict=True)
        if not 0 < historical.stat().st_size <= 262144:
            raise ValueError("HISTORICAL_FAILURE_EVIDENCE_SIZE_INVALID")
        historical_digest = digest_file(historical)
        if historical_digest != "86d7b591c06a295fa3cb4085bb47e6b42c40274878735602ea712c12b1234447":
            raise ValueError("HISTORICAL_FAILURE_EVIDENCE_IDENTITY_INVALID")
        report["historical_failure_evidence_sha256"] = historical_digest
    sys.path.insert(0, str(ROOT))
    api, sync = load("recovery_copy_api", "run_dashboard_api.py"), load("recovery_copy_sync", "run_dashboard_sync.py")
    from xauusd_forecaster.news_projection import receipt_payload_hash
    node = shutil.which("node")
    if not node:
        raise RuntimeError("NODE_UNAVAILABLE")
    report["node"] = subprocess.check_output([node, "--version"], timeout=5, creationflags=NO_WINDOW).decode().strip()
    counts, reads, posted_bytes = Counter(), [], []
    observed_failures = set()
    input_stat = database.stat()
    frozen_at = datetime.now(UTC)
    stop = threading.Event()
    servers, threads = [], []
    worker = None
    temporary_owner = None
    previous_cert = os.environ.get("SSL_CERT_FILE")
    started = time.monotonic()
    try:
        temporary_owner = tempfile.mkdtemp(prefix="xauusd-news-recovery-copy-")
        with nullcontext(temporary_owner) as temporary:
            runtime_owner = Path(temporary).resolve(strict=True)
            (runtime_owner / "fixture-owned.json").write_text(
                json.dumps({"root": str(runtime_owner)}), encoding="utf-8")
            runtime = runtime_owner / ".local/forward"
            runtime.mkdir(parents=True)
            # Only time and the mutable manifest location are isolated. Execute
            # the real resource builder on demand, without prewarming the API.
            real_builder = api._build_news_evidence_resource
            def frozen_builder(source, **kwargs):
                return real_builder(source, **{**kwargs, "clock": lambda: frozen_at,
                    "manifest_path": runtime / "news-generation.json"})
            api._build_news_evidence_resource = frozen_builder
            report["resource_time_boundary"] = {
                "frozen_at": frozen_at.isoformat(),
                "wall_started_at": datetime.now(UTC).isoformat(),
                "manifest_boundary": "fresh isolated runtime; not the retained database directory",
            }
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

                def do_GET(self):
                    if not self.path.startswith("/api/news-evidence?"):
                        self.send_error(403)
                        return
                    counts["remote_ack_reconciliation"] += 1
                    with worker_lock:
                        worker.stdin.write(json.dumps({"read": True}) + "\n")
                        worker.stdin.flush()
                        result = json.loads(responses.get(timeout=15))["result"]
                    body = json.dumps(result).encode()
                    self.send_response(200)
                    # Declared provider identity adapter, never production proof.
                    self.send_header("X-Aurum-Worker-Version", version)
                    self.send_header("X-Aurum-Git-SHA", revision)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

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

            local, remote = OwnedHTTPServer(("127.0.0.1", 0), Local), OwnedHTTPServer(("127.0.0.1", 0), Remote)
            servers.extend((local, remote))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
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
            status_file = runtime / "dashboard-sync-status.json"
            def monitor():
                while not stop.wait(.2):
                    receipt = sync._read_news_sync_state(Path(config["deferred_projection_receipt_file"]))
                    status = sync._read_news_sync_state(status_file)
                    for failure in status.get("degraded_resources", []):
                        observed_failures.add((str(failure.get("resource")), str(failure.get("error_type"))))
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
            consumer = subprocess.run([
                sys.executable, str(ROOT / "tests/fixtures/deferred_recovery_consumer.py"),
                "--fixture-provider", f"https://127.0.0.1:{remote.server_port}",
                "--runtime-root", str(runtime_owner), "--version-id", version,
                "--git-sha", revision, "--producer-revision", revision,
                "--required-after", now.isoformat(), "--observe-attempt", uuid.uuid4().hex,
                "--route", "/api/news-evidence",
            ], capture_output=True, text=True, encoding="utf-8", timeout=25, creationflags=NO_WINDOW)
            report["independent_deferred_consumer"] = {
                "exit_code": consumer.returncode,
                "result": json.loads(consumer.stdout) if consumer.stdout.strip() else None,
                "diagnostic": consumer.stderr[-2000:],
            }
            if consumer.returncode != 0 or report["independent_deferred_consumer"]["result"].get("state") != "PASSED":
                raise RuntimeError("INDEPENDENT_DEFERRED_CONSUMER_REJECTED")
            # Run after measured API/Sync work, using its exact frozen input.
            # Legacy SQL timing is not an HTTP timeout and is reported separately.
            comparison = compare_news_queries(database, api, frozen_at)
            report["query_comparison"] = comparison
            report["semantic_equality_verified"] = bool(
                comparison["semantic_equality_verified"]
                and comparison["legacy_query_state"] == "COMPLETED"
                and comparison["legacy_display_equal"] is True
                and comparison["durable_digest"] == receipt.get("news_recovery", {}).get("snapshot_id")
                and comparison["compared_display_count"] == report["records"])
            final_stat = database.stat()
            report["input_stat_unchanged"] = (input_stat.st_size, input_stat.st_mtime_ns) == (
                final_stat.st_size, final_stat.st_mtime_ns)
            if not report["semantic_equality_verified"] or not report["input_stat_unchanged"]:
                raise RuntimeError("SAME_INPUT_QUERY_EQUIVALENCE_NOT_PROVEN")
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
        request_threads = [thread for server in servers for thread in server.owned_requests()]
        requests_stopped = join_owned_threads(request_threads)
        if worker is not None:
            worker.stdin.close()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.terminate()
                worker.wait(timeout=5)
        for thread in threads:
            thread.join(5)
        if temporary_owner is not None and requests_stopped and all(not thread.is_alive() for thread in threads):
            owned_path = Path(temporary_owner).resolve(strict=True)
            marker = owned_path / "fixture-owned.json"
            if (not owned_path.name.startswith("xauusd-news-recovery-copy-")
                    or not marker.is_file()
                    or json.loads(marker.read_text(encoding="utf-8")) != {"root": str(owned_path)}):
                requests_stopped = False
            else:
                shutil.rmtree(owned_path)
        if previous_cert is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = previous_cert
        report.update(elapsed_seconds=round(time.monotonic() - started, 3), operations=dict(counts),
                      local_get_count=len(reads), max_local_get_seconds=max(reads, default=0),
                      remote_posts=len(posted_bytes), max_post_bytes=max(posted_bytes, default=0),
                      heartbeat_count=counts["remote_heartbeat"],
                      transient_failure_families=sorted(observed_failures),
                      remaining_request_threads=sum(thread.is_alive() for thread in request_threads),
                      retained_runtime=None if temporary_owner is None or not Path(temporary_owner).exists() else temporary_owner,
                      cleanup="PASSED" if requests_stopped and all(not thread.is_alive() for thread in threads) else "UNRESOLVED",
                      d1_rows_read="NOT_MEASURED", d1_rows_written="NOT_MEASURED")
        if report["cleanup"] != "PASSED":
            report["state"] = "FAILED"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"state": report["state"], "output": str(output)}))
    return 0 if report["state"] == "API_SYNC_COPY_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
