from __future__ import annotations

import json

from xauusd_forecaster.dashboard.operator_bridge import (
    apply_retry_overrides,
    operator_bridge_auth_error,
    retry_jobs_response,
)


def test_operator_bridge_auth_contract_is_loopback_non_browser_and_dedicated() -> None:
    token = "operator-bridge-token-" + "x" * 32
    environment = {"DASHBOARD_OPERATOR_BRIDGE_TOKEN": token}

    assert operator_bridge_auth_error(
        client_host="127.0.0.1", origin=None, fetch_mode=None,
        supplied_token=token, environ=environment,
    ) is None
    assert operator_bridge_auth_error(
        client_host="192.0.2.1", origin=None, fetch_mode=None,
        supplied_token=token, environ=environment,
    )[0] == 403
    assert operator_bridge_auth_error(
        client_host="::1", origin="https://example.test", fetch_mode=None,
        supplied_token=token, environ=environment,
    )[0] == 403
    assert operator_bridge_auth_error(
        client_host="127.0.0.1", origin=None, fetch_mode=None,
        supplied_token="wrong", environ=environment,
    )[0] == 401
    assert operator_bridge_auth_error(
        client_host="127.0.0.1", origin=None, fetch_mode=None,
        supplied_token=token, environ={},
    )[0] == 503


def test_operator_bridge_services_fail_closed_at_their_contract_boundaries(
    tmp_path,
) -> None:
    status, body = retry_jobs_response(tmp_path / "missing.sqlite3")
    assert status == 400
    assert "error" in json.loads(body)

    status, body = apply_retry_overrides(
        tmp_path / "forward.sqlite3", {"items": [], "operator_id": "invalid"},
    )
    assert status == 400
    assert json.loads(body) == {"error": "retry override batch size is invalid"}
