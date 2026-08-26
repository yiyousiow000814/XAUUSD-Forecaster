#!/usr/bin/env python
"""Local dashboard API plus the audited scheduler operator bridge."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
from xauusd_forecaster.dashboard_payloads import critical_status_payload
from xauusd_forecaster.dashboard.status_cache import (
    STATUS_SNAPSHOT_MAX_STALE_SECONDS,
    STATUS_SNAPSHOT_TTL_SECONDS,
    STATUS_SNAPSHOT_WAIT_SECONDS,
    StatusSnapshotCache,
    StatusSnapshotUnavailable,
)
from xauusd_forecaster.dashboard.health_projection import (
    COLLECTOR_HEARTBEAT_EXPECTED_SECONDS,
    COLLECTOR_HEARTBEAT_FAILURE_SECONDS,
    DECISION_HORIZON,
    DECISION_OUTPUT_CADENCE_SECONDS,
    DECISION_OUTPUT_GRACE_SECONDS,
    DECISION_OUTPUT_STALLED_SECONDS,
    SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS,
    _collector_component,
    _decision_collector_component,
    _materialized_semantic_health,
    _semantic_pipeline_component,
)
from xauusd_forecaster.dashboard_read_models import (
    DashboardReadModelOwner,
    DashboardReadModelUnavailable,
    read_dashboard_read_model,
)
from xauusd_forecaster.dashboard.news_resources import (
    NEWS_ARCHIVE_PAGE_LIMIT,
    NEWS_CATEGORY_LABELS,
    NEWS_EVIDENCE_PAGE_LIMIT,
    NEWS_EVIDENCE_PAGE_LIMIT_BYTES,
    OTHER_NEWS_CATEGORY_LABEL,
    NEWS_PROJECTION_MAX_ITEMS,
    NEWS_PROJECTION_SOURCE_REFRESH_SECONDS,
    NEWS_PROJECTION_SOURCE_RETRY_SECONDS,
    NewsProjectionGeneration,
    NewsProjectionSourcePending,
    _NEWS_EVIDENCE_CACHE,
    _NEWS_EVIDENCE_CACHE_LOCK,
    _NEWS_EVIDENCE_MANIFEST_VERSION,
    _NEWS_EVIDENCE_VOLATILE_FIELDS,
    _NEWS_PROJECTION_CACHE,
    _NEWS_PROJECTION_CACHE_LOCK,
    _annotation_failure_reason,
    _apply_impact_status,
    _build_news_evidence_resource,
    _build_news_projection_source,
    _build_news_projection_source_from_database,
    _durable_news_evidence_rows,
    _frozen_event_article_identity,
    _finish_news_projection_source_build,
    _materialize_news_evidence_generation,
    _news_archive_page,
    _news_archive_context,
    _news_category_label,
    _news_evidence_display_rows,
    _news_evidence_page,
    _news_metrics,
    _news_mirror_candidate_keys,
    _news_reader_rows,
    _news_projection_batch,
    _news_projection_source,
    _news_projection_source_for_request,
    _not_required_reason,
    _publish_news_evidence_snapshot,
    _serialize_news_rows,
)
from xauusd_forecaster.dashboard.market_resources import (
    MARKET_DETAIL_CANDLE_LIMIT,
    MARKET_HISTORY_PAGE_LIMIT,
    MARKET_OVERVIEW_CANDLE_LIMIT,
    _QUOTE_CANDLE_CACHE,
    _QUOTE_CANDLE_CACHE_LOCK,
    _all_market_candles,
    _append_quote_candle,
    _downsample_candles,
    _market_decisions,
    _market_history_page,
    _quote_file_candles,
    _quote_history_files,
    _recent_market_chart,
)
from xauusd_forecaster.dashboard.status_resources import (
    DEPLOYMENT_PROVENANCE,
    PAYLOAD_SCHEMA_VERSION,
    U5_CONTEXT_SAMPLE_LIMIT,
    _LEARNING_CACHE,
    _LEARNING_CACHE_LOCK,
    _LEARNING_REVISION_TABLES,
    _broker_market_session,
    _dashboard_payload,
    _deployment_provenance,
    _deployment_status,
    _has_recent_evidence,
    _latest_decision_created_at,
    _latest_quote_received,
    _learning_revision,
    _learning_surfaces,
    _market_session_observed_at,
    _market_session_status,
    _news_source_health,
    _optional_resource_payload,
    _parse_utc,
    _runtime_heartbeat,
)
from xauusd_forecaster.news_scheduler import (  # noqa: E402
    RetryScheduleConflict,
    apply_retry_schedule_override,
    install_scheduler_schema,
    list_retry_schedule_jobs,
)
from xauusd_forecaster.operational_health import extend_with_component_alerts
DEFAULT_DATABASE = MODULE_ROOT / ".local" / "forward" / "forward-evidence.sqlite3"




class Handler(BaseHTTPRequestHandler):
    database: Path
    status_cache = StatusSnapshotCache()
    critical_status_cache = StatusSnapshotCache()
    audit_cache = StatusSnapshotCache()
    learning_cache = StatusSnapshotCache()
    market_chart_cache = StatusSnapshotCache()
    news_evidence_cache = StatusSnapshotCache()

    def _operator_bridge_auth_error(self) -> tuple[int, bytes] | None:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            return 403, b'{"error":"localhost operator bridge only"}'
        # Browser-origin requests have no reason to reach this machine bridge.
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Mode"):
            return 403, b'{"error":"browser origin is not permitted"}'
        expected = os.environ.get("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "").strip()
        supplied = self.headers.get("X-Aurum-Operator-Bridge-Token", "").strip()
        if not 32 <= len(expected) <= 512:
            return 503, b'{"error":"operator bridge credential is not configured"}'
        if not supplied or not hmac.compare_digest(supplied, expected):
            return 401, b'{"error":"operator bridge authorization failed"}'
        return None

    def _write_json(self, status: int, body: bytes, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/health":
            try:
                self.critical_status_cache.get(
                    self.database,
                    lambda database: critical_status_payload(
                        _dashboard_payload(database, include_optional=False)
                    ),
                )
            except Exception:
                pass
            status, critical = self.critical_status_cache.health()
            payload = {
                **critical,
                "readiness_scope": "PROCESS_AND_CRITICAL_STATUS",
                "optional_resources": "SEPARATE_DEGRADATION",
            }
            body = json.dumps(payload, separators=(",", ":")).encode()
            self._write_json(status, body)
            return
        if path == "/api/market-history":
            query = urllib.parse.parse_qs(parsed.query)
            after = (query.get("after") or [None])[0]
            try:
                limit = min(
                    MARKET_HISTORY_PAGE_LIMIT,
                    max(1, int((query.get("limit") or [MARKET_HISTORY_PAGE_LIMIT])[0])),
                )
                connection = sqlite3.connect(
                    f"file:{self.database}?mode=ro", uri=True, timeout=5,
                )
                connection.row_factory = sqlite3.Row
                try:
                    payload = _market_history_page(
                        self.database, connection, after, limit,
                    )
                finally:
                    connection.close()
                body = json.dumps(payload, allow_nan=False).encode()
                status = 200
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 400
            self._write_json(status, body)
            return
        if path == "/api/news-archive":
            auth_error = self._operator_bridge_auth_error()
            if auth_error is not None:
                self._write_json(*auth_error)
                return
            query = urllib.parse.parse_qs(parsed.query)
            mode = (query.get("mode") or ["manifest"])[0]
            activated_snapshot_id = (
                query.get("activated_snapshot_id") or [None]
            )[0]
            try:
                if activated_snapshot_id and not re.fullmatch(
                    r"[a-f0-9]{64}", activated_snapshot_id,
                ):
                    raise ValueError("invalid activated news snapshot identity")
                generation = _news_projection_source_for_request(
                    self.database, activated_snapshot_id,
                )
                if mode == "manifest":
                    payload = {"manifest": generation.manifest}
                elif mode == "batch":
                    snapshot_id = (query.get("snapshot_id") or [""])[0]
                    if snapshot_id != generation.manifest["snapshot_id"]:
                        raise ValueError("news projection snapshot is no longer available")
                    kind = (query.get("kind") or [""])[0]
                    offset = int((query.get("offset") or ["0"])[0])
                    payload = _news_projection_batch(generation, kind, offset)
                else:
                    raise ValueError("invalid news projection source mode")
                body = json.dumps(payload, allow_nan=False).encode()
                status = 200
            except NewsProjectionSourcePending as error:
                body = json.dumps({
                    "error": str(error),
                    "error_code": "NEWS_PROJECTION_SOURCE_BUILDING",
                    "projection_state": "REPLAYING",
                }).encode()
                self._write_json(503, body, Retry_After="30")
                return
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 400
            self._write_json(status, body)
            return
        if path == "/api/news-evidence":
            query = urllib.parse.parse_qs(parsed.query)
            cursor = (query.get("cursor") or [None])[0]
            activated_snapshot_id = (
                query.get("activated_snapshot_id") or [None]
            )[0]
            if activated_snapshot_id and not re.fullmatch(
                r"[a-f0-9]{64}", activated_snapshot_id,
            ):
                self._write_json(400, b'{"error":"invalid activated snapshot id"}')
                return
            try:
                self.news_evidence_cache.get(
                    self.database,
                    lambda database: _build_news_evidence_resource(
                        database, activated_snapshot_id=activated_snapshot_id,
                    ),
                )
                limit = min(
                    NEWS_EVIDENCE_PAGE_LIMIT,
                    max(1, int((query.get("limit") or [NEWS_EVIDENCE_PAGE_LIMIT])[0])),
                )
                payload = _news_evidence_page(cursor, limit)
                body = json.dumps(
                    payload, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                status = 200
            except StatusSnapshotUnavailable as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 503
            except (TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 409
            self._write_json(status, body)
            return
        read_model_resources = {
            "/api/audit": "audit",
            "/api/learning": "learning",
            "/api/market-chart": "market_chart",
        }
        if path in read_model_resources:
            try:
                body, metadata = read_dashboard_read_model(
                    self.database, read_model_resources[path],
                )
                status = 200
                headers = {
                    "X_Dashboard_Read_Model": str(metadata["state"]),
                    "X_Dashboard_Snapshot_Age": f"{metadata['age_seconds']:.3f}",
                    "X_Dashboard_Source_Revision": str(metadata["source_revision"]),
                }
            except DashboardReadModelUnavailable as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 503
                headers = {}
            except Exception as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 500
                headers = {}
            self._write_json(status, body, **headers)
            return
        if path == "/api/retry-jobs":
            auth_error = self._operator_bridge_auth_error()
            if auth_error:
                self._write_json(*auth_error)
                return
            try:
                connection = sqlite3.connect(
                    f"file:{self.database}?mode=ro", uri=True, timeout=5,
                )
                connection.row_factory = sqlite3.Row
                try:
                    payload = {"items": list_retry_schedule_jobs(connection)}
                finally:
                    connection.close()
                body = json.dumps(payload, allow_nan=False).encode()
                status = 200
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 400
            self._write_json(status, body)
            return
        if path not in {"/api/status", "/api/critical-status"}:
            self.send_error(404)
            return
        try:
            # /api/status is the canonical bounded first-paint contract. Heavy
            # audit, learning, and market detail have independent lazy/paged
            # owners and may never inflate this request path again.
            body, snapshot_state, snapshot_age = self.critical_status_cache.get(
                self.database,
                lambda database: critical_status_payload(
                    _dashboard_payload(database, include_optional=False)
                ),
            )
            status = 200
        except StatusSnapshotUnavailable as error:
            body = json.dumps({"error": str(error)[:500]}).encode()
            status = 503
        except Exception as error:
            body = json.dumps({"error": str(error)[:500]}).encode()
            status = 500
        headers = {}
        if status == 200:
            headers = {
                "X_Dashboard_Snapshot_State": snapshot_state,
                "X_Dashboard_Snapshot_Age": f"{snapshot_age:.3f}",
            }
        self._write_json(status, body, **headers)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.rstrip("/") != "/api/retry-overrides":
            self.send_error(404)
            return
        auth_error = self._operator_bridge_auth_error()
        if auth_error:
            self._write_json(*auth_error)
            return
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            self._write_json(415, b'{"error":"application/json content type required"}')
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 2 or content_length > 100_000:
                raise ValueError("retry override payload size is invalid")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("retry override items are required")
            items = payload["items"]
            if not 1 <= len(items) <= 100:
                raise ValueError("retry override batch size is invalid")
            operator_id = str(payload.get("operator_id") or "").strip()
            if not operator_id.startswith("cloudflare-access:") or len(operator_id) > 500:
                raise ValueError("retry override operator identity is invalid")
            connection = sqlite3.connect(self.database, timeout=10)
            connection.row_factory = sqlite3.Row
            try:
                install_scheduler_schema(connection)
                results = []
                for item in items:
                    if not isinstance(item, dict):
                        results.append({"status": "REJECTED", "code": "INVALID_ITEM"})
                        continue
                    try:
                        requested_at = item.get("requested_available_at")
                        custom_time = (
                            datetime.fromisoformat(str(requested_at))
                            if requested_at else None
                        )
                        current = apply_retry_schedule_override(
                            connection,
                            request_id=str(item.get("request_id") or ""),
                            job_id=str(item.get("job_id") or ""),
                            operator_id=operator_id,
                            mode=str(item.get("mode") or ""),
                            reason=str(item.get("reason") or ""),
                            expected_state=str(item.get("expected_state") or ""),
                            expected_available_at=str(
                                item.get("expected_available_at") or ""
                            ),
                            requested_available_at=custom_time,
                        )
                        results.append({
                            "request_id": item.get("request_id"),
                            "job_id": item.get("job_id"),
                            "status": "APPLIED",
                            "current": current,
                        })
                    except RetryScheduleConflict as error:
                        results.append({
                            "request_id": item.get("request_id"),
                            "job_id": item.get("job_id"),
                            "status": "CONFLICT",
                            "code": error.code,
                            "current": error.current,
                        })
                    except (TypeError, ValueError) as error:
                        results.append({
                            "request_id": item.get("request_id"),
                            "job_id": item.get("job_id"),
                            "status": "REJECTED",
                            "code": "INVALID_REQUEST",
                            "error": str(error)[:500],
                        })
            finally:
                connection.close()
            status = 200 if all(item["status"] == "APPLIED" for item in results) else 207
            body = json.dumps({"results": results}, allow_nan=False).encode()
        except (json.JSONDecodeError, OSError, sqlite3.Error, TypeError, ValueError) as error:
            status = 400
            body = json.dumps({"error": str(error)[:500]}).encode()
        self._write_json(status, body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    Handler.database = args.database.resolve()
    read_model_owner = DashboardReadModelOwner(
        Handler.database,
        {
            resource: (
                lambda database, resource=resource: _optional_resource_payload(
                    database, resource,
                )
            )
            for resource in ("audit", "learning", "market_chart")
        },
    )
    read_model_owner.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        json.dumps(
            {
                "event": "DASHBOARD_API_STARTED",
                "url": f"http://{args.host}:{args.port}/api/status",
                "database": str(Handler.database),
                "read_only": False,
                "writes": ["audited retry schedule overrides"],
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        read_model_owner.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
