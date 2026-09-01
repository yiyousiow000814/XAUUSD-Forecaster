from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime

import pytest

from xauusd_forecaster.dashboard.sync import progress as module


class _DeclaredPayloadContractError(ValueError):
    error_code = "PAYLOAD_CONTRACT_REJECTED"


def test_sync_state_round_trip_and_malformed_state_fail_to_empty(tmp_path) -> None:
    state_file = tmp_path / "dashboard-news-sync-state.json"
    config = {module.RUNTIME_STATE_ROOT_KEY: str(tmp_path)}
    expected = {"contract_version": "news-v1", "cursor": "abc:12"}

    module._write_news_sync_state(state_file, config, expected)

    assert module._read_news_sync_state(state_file, config) == expected
    assert not state_file.with_suffix(".json.tmp").exists()

    state_file.write_text("not-json", encoding="utf-8")
    assert module._read_news_sync_state(state_file, config) == {}


def test_progress_sinks_revalidate_runtime_root_at_io_boundary(tmp_path) -> None:
    state_root = tmp_path / "runtime"
    outside = tmp_path / "outside.json"
    config = {module.RUNTIME_STATE_ROOT_KEY: str(state_root)}

    with pytest.raises(ValueError, match="must be one JSON file under"):
        module._read_news_sync_state(outside, config)
    with pytest.raises(ValueError, match="must be one JSON file under"):
        module._write_news_sync_state(outside, config, {})
    with pytest.raises(ValueError, match="must be one JSON file under"):
        module.write_sync_status(outside, config, success=True)

    assert not outside.exists()


@pytest.mark.parametrize(("commands", "expected_seconds"), [
    (10, 30),
    (50, 150),
    (100, 300),
])
def test_operator_retry_batch_has_bounded_bulk_sla(commands, expected_seconds) -> None:
    assert module.OPERATOR_RETRY_COMMANDS_PER_CYCLE == 10
    assert module.operator_retry_bulk_sla_seconds(commands) == expected_seconds


def test_sync_status_records_real_success_and_preserves_it_on_error(tmp_path) -> None:
    status_file = tmp_path / "dashboard-sync-status.json"
    config = {module.RUNTIME_STATE_ROOT_KEY: str(tmp_path)}

    observation = [{
        "target": "cloudflare", "resource": "news", "status": "OK",
        "duration_ms": 12.5, "completed_at": "2026-08-17T00:00:00+00:00",
    }]
    module.write_sync_status(
        status_file, config, success=True, attempts_used=2,
        resource_observations=observation,
    )
    succeeded = json.loads(status_file.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(succeeded["last_success"])
    assert succeeded["attempts_used"] == 2
    assert succeeded["last_error"] is None
    assert succeeded["status"] == "OK"
    assert succeeded["resource_observations"] == observation

    module.write_sync_status(
        status_file, config,
        success=False, error=ConnectionResetError("remote closed")
    )
    failed = json.loads(status_file.read_text(encoding="utf-8"))
    assert failed["last_success"] == succeeded["last_success"]
    assert failed["last_error_type"] == "ConnectionResetError"
    assert failed["last_error"] == "remote closed"
    assert failed["last_error_code"] == "TRANSPORT_UNAVAILABLE"
    assert failed["degraded_resources"] == []
    assert failed["status"] == "ERROR"


def test_sync_status_reports_optional_resource_degradation(tmp_path) -> None:
    status_file = tmp_path / "dashboard-sync-status.json"
    config = {module.RUNTIME_STATE_ROOT_KEY: str(tmp_path)}
    degraded = [{"resource": "learning", "error": "too large"}]
    module.write_sync_status(
        status_file, config, success=True, attempts_used=1,
        degraded_resources=degraded,
    )
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "DEGRADED"
    assert status["last_error"] is None
    assert status["degraded_resources"] == degraded


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (401, "AUTH_REJECTED"),
        (403, "AUTH_REJECTED"),
        (413, "PAYLOAD_LIMIT_EXCEEDED"),
        (429, "RATE_LIMITED"),
        (503, "REMOTE_UNAVAILABLE"),
    ],
)
def test_transport_error_family_is_persisted_as_structured_code(
    status_code,
    expected,
) -> None:
    error = urllib.error.HTTPError(
        "https://example.invalid", status_code, "failure", {}, io.BytesIO(),
    )

    assert module.sync_error_code(error) == expected


def test_only_declared_payload_contract_errors_receive_payload_code() -> None:

    assert module.sync_error_code(ValueError("invalid configuration")) == "UNCLASSIFIED"
    assert module.sync_error_code(
        _DeclaredPayloadContractError("bounded payload too large")
    ) == "PAYLOAD_CONTRACT_REJECTED"
    assert module.sync_error_code(
        urllib.error.URLError("name resolution failed")
    ) == "TRANSPORT_UNAVAILABLE"
