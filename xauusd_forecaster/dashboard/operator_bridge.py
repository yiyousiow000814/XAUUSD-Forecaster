"""Audited local bridge from Dashboard HTTP requests to scheduler transitions."""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Mapping

from xauusd_forecaster.news.scheduler.state import (
    RetryScheduleConflict,
    apply_retry_schedule_override,
    install_scheduler_schema,
    list_retry_schedule_jobs,
)


def operator_bridge_auth_error(
    *,
    client_host: str,
    origin: str | None,
    fetch_mode: str | None,
    supplied_token: str,
    environ: Mapping[str, str] | None = None,
) -> tuple[int, bytes] | None:
    """Authorize only non-browser loopback callers with the dedicated secret."""
    if client_host not in {"127.0.0.1", "::1"}:
        return 403, b'{"error":"localhost operator bridge only"}'
    if origin or fetch_mode:
        return 403, b'{"error":"browser origin is not permitted"}'
    environment = os.environ if environ is None else environ
    expected = environment.get("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "").strip()
    if not 32 <= len(expected) <= 512:
        return 503, b'{"error":"operator bridge credential is not configured"}'
    if not supplied_token or not hmac.compare_digest(supplied_token, expected):
        return 401, b'{"error":"operator bridge authorization failed"}'
    return None


def retry_jobs_response(database: Path) -> tuple[int, bytes]:
    """Read the bounded scheduler job projection without taking transition authority."""
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro", uri=True, timeout=5,
        )
        connection.row_factory = sqlite3.Row
        try:
            payload = {"items": list_retry_schedule_jobs(connection)}
        finally:
            connection.close()
        return 200, json.dumps(payload, allow_nan=False).encode()
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        return 400, json.dumps({"error": str(error)[:500]}).encode()


def apply_retry_overrides(
    database: Path, payload: object,
) -> tuple[int, bytes]:
    """Validate a bounded batch and delegate each transition to the scheduler."""
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("retry override items are required")
        items = payload["items"]
        if not 1 <= len(items) <= 100:
            raise ValueError("retry override batch size is invalid")
        operator_id = str(payload.get("operator_id") or "").strip()
        if not operator_id.startswith("cloudflare-access:") or len(operator_id) > 500:
            raise ValueError("retry override operator identity is invalid")
        connection = sqlite3.connect(database, timeout=10)
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
        return status, json.dumps({"results": results}, allow_nan=False).encode()
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        return 400, json.dumps({"error": str(error)[:500]}).encode()
