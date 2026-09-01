from __future__ import annotations

import io
import urllib.error

import pytest

from xauusd_forecaster.dashboard.sync import progress as module


class _DeclaredPayloadContractError(ValueError):
    error_code = "PAYLOAD_CONTRACT_REJECTED"


@pytest.mark.parametrize(("commands", "expected_seconds"), [
    (10, 30),
    (50, 150),
    (100, 300),
])
def test_operator_retry_batch_has_bounded_bulk_sla(commands, expected_seconds) -> None:
    assert module.OPERATOR_RETRY_COMMANDS_PER_CYCLE == 10
    assert module.operator_retry_bulk_sla_seconds(commands) == expected_seconds


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
