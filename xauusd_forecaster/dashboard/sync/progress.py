"""Durable Dashboard Sync progress and status primitives."""

from __future__ import annotations

import http.client
import math
import urllib.error


OPERATOR_RETRY_COMMANDS_PER_CYCLE = 10
RUNTIME_STATE_ROOT_KEY = "_runtime_state_root"


class AllTargetsRejected(RuntimeError):
    """Every configured target rejected the critical heartbeat."""

    def __init__(self, degraded_resources: list[dict]) -> None:
        super().__init__("all dashboard mirror targets rejected the heartbeat")
        self.degraded_resources = degraded_resources
        codes = {
            str(item.get("error_code"))
            for item in degraded_resources
            if item.get("error_code")
        }
        self.error_code = next(iter(codes)) if len(codes) == 1 else "ALL_TARGETS_REJECTED"


class SyncResourceResults(list[dict]):
    """Degraded resources plus bounded timing evidence for one sync cycle."""

    def __init__(
        self, degraded_resources: list[dict], resource_observations: list[dict],
    ) -> None:
        super().__init__(degraded_resources)
        self.resource_observations = resource_observations


def operator_retry_bulk_sla_seconds(
    command_count: int,
    *,
    cadence_seconds: int = 30,
    commands_per_cycle: int = OPERATOR_RETRY_COMMANDS_PER_CYCLE,
) -> int:
    """Return the product SLA for draining an already queued command batch."""
    if command_count <= 0:
        return 0
    if cadence_seconds <= 0 or commands_per_cycle <= 0:
        raise ValueError("retry cadence and batch size must be positive")
    return math.ceil(command_count / commands_per_cycle) * cadence_seconds


def sync_error_code(error: Exception | None) -> str:
    """Classify transport failures once, before they enter persisted status."""
    if error is None:
        return "UNKNOWN"
    declared = getattr(error, "error_code", None)
    if declared:
        return str(declared)
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 413:
            return "PAYLOAD_LIMIT_EXCEEDED"
        if error.code in {401, 403}:
            return "AUTH_REJECTED"
        if error.code == 429:
            return "RATE_LIMITED"
        if error.code >= 500:
            return "REMOTE_UNAVAILABLE"
        return "HTTP_REJECTED"
    if isinstance(error, (
        TimeoutError,
        ConnectionError,
        http.client.RemoteDisconnected,
        urllib.error.URLError,
    )):
        return "TRANSPORT_UNAVAILABLE"
    return "UNCLASSIFIED"
