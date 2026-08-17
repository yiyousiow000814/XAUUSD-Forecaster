"""Durable credential-pool x model capacity routing for Assistant work."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Generic, TypeVar

from .assistant_routing import (
    ASSISTANT_ROUTING_POLICY_VERSION,
    AssistantRoutePlan,
    ModelProfile,
    provider_thinking_level,
    routing_provenance,
)
from .model_gateway import (
    ModelGatewayCapacityExhausted,
    ModelRequestAccountant,
    ModelRequestUsage,
)
from .news_scheduler import (
    PREEMPTIBLE_POOL,
    ROUTINE_POOL,
    ApiCredential,
    minute_bucket,
    quota_day,
    rolling_account_usage,
)


ASSISTANT_CAPACITY_POLICY_VERSION = "assistant-capacity-v1"
ASSISTANT_CAPACITY_POLICIES_ENV = "ASSISTANT_CAPACITY_POLICIES"
DEFAULT_SOFT_CAP_BASIS_POINTS = 8_000
DEFAULT_MAX_IN_FLIGHT = 2
DEFAULT_RESERVATION_TTL_SECONDS = 180
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_FAILURE_COOLDOWN_THRESHOLD = 2
MAX_ASSISTANT_CREDENTIAL_POOLS = 16
MAX_CREDENTIALS_PER_POOL = 8
MAX_CAPACITY_POLICIES = 128
MAX_ASSISTANT_CAPACITY_ATTEMPTS = 128


ASSISTANT_CAPACITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_capacity_reservations_v1 (
    reservation_id TEXT PRIMARY KEY,
    credential_pool_id TEXT NOT NULL,
    pool_fingerprint TEXT NOT NULL,
    model_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    service_priority TEXT NOT NULL CHECK(service_priority IN (
        'INTERACTIVE','BACKGROUND')),
    quota_day TEXT NOT NULL,
    minute_bucket TEXT NOT NULL,
    estimated_input_tokens INTEGER NOT NULL CHECK(estimated_input_tokens > 0),
    reserved_input_tokens INTEGER NOT NULL CHECK(reserved_input_tokens > 0),
    state TEXT NOT NULL CHECK(state IN (
        'IN_FLIGHT','SUCCEEDED','FAILED','THROTTLED',
        'CAPACITY_REJECTED','ABANDONED')),
    provider_http_status INTEGER,
    failure_code TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS assistant_capacity_reservations_active_v1
ON assistant_capacity_reservations_v1(
    credential_pool_id,model_id,state,expires_at
);

CREATE TABLE IF NOT EXISTS assistant_capacity_health_v1 (
    credential_pool_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    health TEXT NOT NULL CHECK(health IN ('HEALTHY','DEGRADED','COOLDOWN')),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    throttle_count INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    last_provider_http_status INTEGER,
    last_failure_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(credential_pool_id,model_id)
);
"""


class AssistantServicePriority(StrEnum):
    INTERACTIVE = "INTERACTIVE"
    BACKGROUND = "BACKGROUND"


class AssistantCapacityUnavailable(RuntimeError):
    """No credential-pool x model pair can safely admit this request."""


@dataclass(frozen=True)
class AssistantCapacityPolicy:
    credential_pool_id: str
    provider: str
    model_id: str
    shared_model_ids: tuple[str, ...]
    rpd_limit: int
    rpm_limit: int
    tpm_limit: int
    soft_cap_basis_points: int
    max_in_flight: int
    reservation_ttl_seconds: int
    cooldown_seconds: int
    failure_cooldown_threshold: int
    enabled: bool
    source: str


@dataclass(frozen=True)
class AssistantCapacityReservation:
    reservation_id: str
    credential_pool_id: str
    pool_fingerprint: str
    profile_id: str
    model_id: str
    service_priority: AssistantServicePriority
    estimated_input_tokens: int
    policy: AssistantCapacityPolicy


@dataclass(frozen=True)
class _CapacitySnapshot:
    score: float
    available: bool


T = TypeVar("T")


@dataclass(frozen=True)
class AssistantCapacityResult(Generic[T]):
    value: T
    profile: ModelProfile
    routing: dict[str, object]


_POOL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_PROVIDER = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_FAILURE_CODE = re.compile(r"^[A-Z0-9_]{3,64}$")
_CREDENTIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


def install_assistant_capacity_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(ASSISTANT_CAPACITY_SCHEMA)


def _strict_integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"Assistant capacity {field} is invalid")
    return value


def _strict_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Assistant capacity {field} must be boolean")
    return value


def _soft_cap_basis_points(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Assistant capacity soft_cap_ratio is invalid")
    try:
        ratio = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Assistant capacity soft_cap_ratio is invalid") from error
    basis_points = ratio * 10_000
    if (
        not ratio.is_finite()
        or ratio <= 0
        or ratio > 1
        or basis_points != basis_points.to_integral_value()
    ):
        raise ValueError("Assistant capacity soft_cap_ratio is invalid")
    return int(basis_points)


def _parse_policy_template(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("each Assistant capacity policy must be an object")
    allowed = {
        "credential_pool_id", "provider", "model_id", "shared_model_ids",
        "rpd_limit", "rpm_limit", "tpm_limit", "soft_cap_ratio",
        "max_in_flight", "reservation_ttl_seconds", "cooldown_seconds",
        "failure_cooldown_threshold", "enabled",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            "Assistant capacity policy contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    pool_id = str(value.get("credential_pool_id") or "").strip()
    provider = str(value.get("provider") or "").strip().upper()
    model_id = str(value.get("model_id") or "").strip()
    if pool_id != "*" and not _POOL_ID.fullmatch(pool_id):
        raise ValueError("Assistant capacity credential_pool_id is invalid")
    if not _PROVIDER.fullmatch(provider):
        raise ValueError("Assistant capacity provider is invalid")
    if not _MODEL_ID.fullmatch(model_id):
        raise ValueError("Assistant capacity model_id is invalid")
    raw_shared = value.get("shared_model_ids", [model_id])
    if (
        not isinstance(raw_shared, list)
        or not raw_shared
        or len(raw_shared) > 16
    ):
        raise ValueError("Assistant capacity shared_model_ids is invalid")
    shared = tuple(str(item or "").strip() for item in raw_shared)
    if (
        any(not _MODEL_ID.fullmatch(item) for item in shared)
        or len(set(shared)) != len(shared)
        or model_id not in shared
    ):
        raise ValueError("Assistant capacity shared_model_ids is invalid")
    enabled = value.get("enabled", True)
    return {
        "credential_pool_id": pool_id,
        "provider": provider,
        "model_id": model_id,
        "shared_model_ids": shared,
        "rpd_limit": _strict_integer(
            value.get("rpd_limit"), "rpd_limit", minimum=1, maximum=100_000_000,
        ),
        "rpm_limit": _strict_integer(
            value.get("rpm_limit"), "rpm_limit", minimum=1, maximum=1_000_000,
        ),
        "tpm_limit": _strict_integer(
            value.get("tpm_limit"), "tpm_limit", minimum=1, maximum=1_000_000_000,
        ),
        "soft_cap_basis_points": _soft_cap_basis_points(
            value.get("soft_cap_ratio")
        ),
        "max_in_flight": _strict_integer(
            value.get("max_in_flight"), "max_in_flight", minimum=1, maximum=1_000,
        ),
        "reservation_ttl_seconds": _strict_integer(
            value.get("reservation_ttl_seconds"), "reservation_ttl_seconds",
            minimum=30, maximum=3_600,
        ),
        "cooldown_seconds": _strict_integer(
            value.get("cooldown_seconds"), "cooldown_seconds",
            minimum=1, maximum=3_600,
        ),
        "failure_cooldown_threshold": _strict_integer(
            value.get("failure_cooldown_threshold"),
            "failure_cooldown_threshold", minimum=1, maximum=20,
        ),
        "enabled": _strict_bool(enabled, "enabled"),
    }


def _default_policy(
    credential_pool_id: str,
    profile: ModelProfile,
) -> AssistantCapacityPolicy | None:
    from .ai_provider_registry import quota_surface_for_model

    try:
        surface = quota_surface_for_model(profile.model_id)
    except ValueError:
        return None
    return AssistantCapacityPolicy(
        credential_pool_id=credential_pool_id,
        provider=profile.provider,
        model_id=profile.model_id,
        shared_model_ids=surface.model_families,
        rpd_limit=surface.daily_limit,
        rpm_limit=surface.requests_per_minute,
        tpm_limit=surface.input_tokens_per_minute,
        soft_cap_basis_points=DEFAULT_SOFT_CAP_BASIS_POINTS,
        max_in_flight=DEFAULT_MAX_IN_FLIGHT,
        reservation_ttl_seconds=DEFAULT_RESERVATION_TTL_SECONDS,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
        failure_cooldown_threshold=DEFAULT_FAILURE_COOLDOWN_THRESHOLD,
        enabled=True,
        source="REGISTRY_DEFAULT",
    )


def configured_assistant_capacity_policies(
    credentials: tuple[ApiCredential, ...],
    profiles: tuple[ModelProfile, ...],
    raw_policies: str | None = None,
) -> tuple[AssistantCapacityPolicy, ...]:
    """Expand operational policy templates to exact pool x model pairs."""
    pools: dict[str, str] = {}
    pool_key_counts: dict[str, int] = {}
    for credential in credentials:
        if not _POOL_ID.fullmatch(credential.account_id):
            raise ValueError("Assistant credential pool id is invalid")
        prior = pools.setdefault(credential.account_id, credential.pool)
        if prior != credential.pool:
            raise ValueError("Assistant credential pool has inconsistent type")
        pool_key_counts[credential.account_id] = (
            pool_key_counts.get(credential.account_id, 0) + 1
        )
    if len(pools) > MAX_ASSISTANT_CREDENTIAL_POOLS:
        raise ValueError("Assistant credential pool count is invalid")
    if any(count > MAX_CREDENTIALS_PER_POOL for count in pool_key_counts.values()):
        raise ValueError("Assistant credential count per pool is invalid")

    enabled_profiles = tuple(profile for profile in profiles if profile.enabled)
    profile_by_model = {profile.model_id: profile for profile in enabled_profiles}
    raw = (
        os.environ.get(ASSISTANT_CAPACITY_POLICIES_ENV, "")
        if raw_policies is None else raw_policies
    )
    if not raw.strip():
        defaults = []
        for pool_id in pools:
            for profile in enabled_profiles:
                policy = _default_policy(pool_id, profile)
                if policy is not None:
                    defaults.append(policy)
        return tuple(defaults)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("ASSISTANT_CAPACITY_POLICIES is not valid JSON") from error
    if (
        not isinstance(parsed, list)
        or not parsed
        or len(parsed) > MAX_CAPACITY_POLICIES
    ):
        raise ValueError("ASSISTANT_CAPACITY_POLICIES must be a bounded list")
    templates = tuple(_parse_policy_template(item) for item in parsed)
    keys = [
        (str(item["credential_pool_id"]), str(item["model_id"]))
        for item in templates
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("Assistant capacity policies contain duplicate pairs")
    for pool_id, model_id in keys:
        if pool_id != "*" and pool_id not in pools:
            raise ValueError("Assistant capacity policy names an unknown pool")
        if model_id not in profile_by_model:
            raise ValueError("Assistant capacity policy names an unknown model")

    exact = {
        (str(item["credential_pool_id"]), str(item["model_id"])): item
        for item in templates if item["credential_pool_id"] != "*"
    }
    wildcard = {
        str(item["model_id"]): item
        for item in templates if item["credential_pool_id"] == "*"
    }
    policies = []
    for pool_id in pools:
        for model_id, profile in profile_by_model.items():
            item = exact.get((pool_id, model_id), wildcard.get(model_id))
            if item is None:
                continue
            if str(item["provider"]) != profile.provider:
                raise ValueError("Assistant capacity provider does not match model")
            policies.append(AssistantCapacityPolicy(
                credential_pool_id=pool_id,
                provider=str(item["provider"]),
                model_id=model_id,
                shared_model_ids=tuple(item["shared_model_ids"]),
                rpd_limit=int(item["rpd_limit"]),
                rpm_limit=int(item["rpm_limit"]),
                tpm_limit=int(item["tpm_limit"]),
                soft_cap_basis_points=int(item["soft_cap_basis_points"]),
                max_in_flight=int(item["max_in_flight"]),
                reservation_ttl_seconds=int(item["reservation_ttl_seconds"]),
                cooldown_seconds=int(item["cooldown_seconds"]),
                failure_cooldown_threshold=int(
                    item["failure_cooldown_threshold"]
                ),
                enabled=bool(item["enabled"]),
                source="CONFIGURED",
            ))
    return tuple(policies)


def _validated_credentials(
    credentials: tuple[ApiCredential, ...],
) -> tuple[ApiCredential, ...]:
    if len(credentials) > MAX_ASSISTANT_CREDENTIAL_POOLS * MAX_CREDENTIALS_PER_POOL:
        raise ValueError("Assistant credential count is invalid")
    pool_types: dict[str, str] = {}
    pool_counts: dict[str, int] = {}
    credential_ids: set[str] = set()
    for credential in credentials:
        if not isinstance(credential, ApiCredential):
            raise ValueError("Assistant credential has an invalid type")
        if not _POOL_ID.fullmatch(credential.account_id):
            raise ValueError("Assistant credential pool id is invalid")
        if credential.pool not in {PREEMPTIBLE_POOL, ROUTINE_POOL}:
            raise ValueError("Assistant credential pool type is invalid")
        if not credential.api_key.strip():
            raise ValueError("Assistant credential reference is empty")
        if not _CREDENTIAL_ID.fullmatch(credential.credential_id):
            raise ValueError("Assistant credential fingerprint is invalid")
        prior_type = pool_types.setdefault(credential.account_id, credential.pool)
        if prior_type != credential.pool:
            raise ValueError("Assistant credential pool has inconsistent type")
        pool_counts[credential.account_id] = pool_counts.get(credential.account_id, 0) + 1
        if credential.credential_id in credential_ids:
            raise ValueError("Assistant credential is duplicated")
        credential_ids.add(credential.credential_id)
    if len(pool_types) > MAX_ASSISTANT_CREDENTIAL_POOLS:
        raise ValueError("Assistant credential pool count is invalid")
    if any(count > MAX_CREDENTIALS_PER_POOL for count in pool_counts.values()):
        raise ValueError("Assistant credential count per pool is invalid")
    return credentials


def _validated_capacity_policies(
    policies: tuple[AssistantCapacityPolicy, ...],
    credentials: tuple[ApiCredential, ...],
    profiles: tuple[ModelProfile, ...],
) -> tuple[AssistantCapacityPolicy, ...]:
    if len(policies) > MAX_CAPACITY_POLICIES:
        raise ValueError("Assistant capacity policy count is invalid")
    pool_ids = {credential.account_id for credential in credentials}
    providers_by_model = {profile.model_id: profile.provider for profile in profiles}
    pairs: list[tuple[str, str]] = []
    for policy in policies:
        if not isinstance(policy, AssistantCapacityPolicy):
            raise ValueError("Assistant capacity policy has an invalid type")
        if policy.credential_pool_id not in pool_ids:
            raise ValueError("Assistant capacity policy names an unknown pool")
        if not _PROVIDER.fullmatch(policy.provider):
            raise ValueError("Assistant capacity provider is invalid")
        if not _MODEL_ID.fullmatch(policy.model_id):
            raise ValueError("Assistant capacity model_id is invalid")
        expected_provider = providers_by_model.get(policy.model_id)
        if expected_provider is not None and policy.provider != expected_provider:
            raise ValueError("Assistant capacity provider does not match model")
        if (
            not policy.shared_model_ids
            or len(policy.shared_model_ids) > 16
            or policy.model_id not in policy.shared_model_ids
            or len(set(policy.shared_model_ids)) != len(policy.shared_model_ids)
            or any(not _MODEL_ID.fullmatch(item) for item in policy.shared_model_ids)
        ):
            raise ValueError("Assistant capacity shared_model_ids is invalid")
        _strict_integer(
            policy.rpd_limit, "rpd_limit", minimum=1, maximum=100_000_000,
        )
        _strict_integer(
            policy.rpm_limit, "rpm_limit", minimum=1, maximum=1_000_000,
        )
        _strict_integer(
            policy.tpm_limit, "tpm_limit", minimum=1, maximum=1_000_000_000,
        )
        _strict_integer(
            policy.soft_cap_basis_points, "soft_cap_basis_points",
            minimum=1, maximum=10_000,
        )
        _strict_integer(
            policy.max_in_flight, "max_in_flight", minimum=1, maximum=1_000,
        )
        _strict_integer(
            policy.reservation_ttl_seconds, "reservation_ttl_seconds",
            minimum=30, maximum=3_600,
        )
        _strict_integer(
            policy.cooldown_seconds, "cooldown_seconds", minimum=1, maximum=3_600,
        )
        _strict_integer(
            policy.failure_cooldown_threshold, "failure_cooldown_threshold",
            minimum=1, maximum=20,
        )
        _strict_bool(policy.enabled, "enabled")
        if policy.source not in {"CONFIGURED", "REGISTRY_DEFAULT"}:
            raise ValueError("Assistant capacity policy source is invalid")
        pairs.append((policy.credential_pool_id, policy.model_id))
    if len(set(pairs)) != len(pairs):
        raise ValueError("Assistant capacity policies contain duplicate pairs")
    return policies


def credential_pool_fingerprint(pool_id: str) -> str:
    return hashlib.sha256(
        f"{ASSISTANT_CAPACITY_POLICY_VERSION}:{pool_id}".encode("utf-8")
    ).hexdigest()[:16]


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Assistant capacity timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _soft_limit(limit: int, basis_points: int) -> int:
    return limit * basis_points // 10_000


def _usage(
    connection: sqlite3.Connection,
    *,
    pool_id: str,
    model_ids: tuple[str, ...],
    now: datetime,
) -> tuple[int, int, int]:
    placeholders = ",".join("?" for _ in model_ids)
    daily = connection.execute(
        f"""SELECT COALESCE(sum(request_count),0) AS requests
            FROM news_ai_account_daily_usage_v1
            WHERE quota_day=? AND account_id=?
              AND model_family IN ({placeholders})""",
        (quota_day(now), pool_id, *model_ids),
    ).fetchone()
    minute_requests, minute_tokens = rolling_account_usage(
        connection,
        account_id=pool_id,
        model_families=model_ids,
        now=now,
    )
    return (
        int(daily["requests"]),
        minute_requests,
        minute_tokens,
    )


def _capacity_snapshot(
    connection: sqlite3.Connection,
    policy: AssistantCapacityPolicy,
    *,
    estimated_input_tokens: int,
    now: datetime,
) -> _CapacitySnapshot:
    health = connection.execute(
        """SELECT health,cooldown_until FROM assistant_capacity_health_v1
           WHERE credential_pool_id=? AND model_id=?""",
        (policy.credential_pool_id, policy.model_id),
    ).fetchone()
    if (
        health is not None
        and str(health["health"]) == "COOLDOWN"
        and health["cooldown_until"]
        and datetime.fromisoformat(str(health["cooldown_until"])) > now
    ):
        return _CapacitySnapshot(0.0, False)
    in_flight = int(connection.execute(
        """SELECT count(*) FROM assistant_capacity_reservations_v1
           WHERE credential_pool_id=? AND model_id=? AND state='IN_FLIGHT'
             AND expires_at>?""",
        (policy.credential_pool_id, policy.model_id, _iso(now)),
    ).fetchone()[0])
    daily, minute_requests, minute_tokens = _usage(
        connection,
        pool_id=policy.credential_pool_id,
        model_ids=policy.shared_model_ids,
        now=now,
    )
    limits = (
        _soft_limit(policy.rpd_limit, policy.soft_cap_basis_points),
        _soft_limit(policy.rpm_limit, policy.soft_cap_basis_points),
        _soft_limit(policy.tpm_limit, policy.soft_cap_basis_points),
        policy.max_in_flight,
    )
    projected = (
        daily + 1,
        minute_requests + 1,
        minute_tokens + estimated_input_tokens,
        in_flight + 1,
    )
    if any(limit <= 0 or used > limit for used, limit in zip(projected, limits)):
        return _CapacitySnapshot(0.0, False)
    ratios = tuple(
        max(0.0, (limit - used) / limit)
        for used, limit in zip(projected, limits)
    )
    health_factor = 0.5 if health is not None and health["health"] == "DEGRADED" else 1.0
    return _CapacitySnapshot(min(ratios) * health_factor, True)


def _reserve_assistant_capacity(
    connection: sqlite3.Connection,
    *,
    policy: AssistantCapacityPolicy,
    profile: ModelProfile,
    service_priority: AssistantServicePriority,
    estimated_input_tokens: int,
    now: datetime,
) -> AssistantCapacityReservation | None:
    timestamp = _iso(now)
    expires_at = _iso(now + timedelta(seconds=policy.reservation_ttl_seconds))
    reservation_id = str(uuid.uuid4())
    fingerprint = credential_pool_fingerprint(policy.credential_pool_id)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """UPDATE assistant_capacity_reservations_v1
               SET state='ABANDONED',failure_code='RESERVATION_EXPIRED',
                   completed_at=?
               WHERE state='IN_FLIGHT' AND expires_at<=?""",
            (timestamp, timestamp),
        )
        snapshot = _capacity_snapshot(
            connection, policy,
            estimated_input_tokens=estimated_input_tokens,
            now=now,
        )
        if not snapshot.available:
            connection.rollback()
            return None
        day = quota_day(now)
        minute = minute_bucket(now)
        connection.execute(
            """INSERT INTO news_ai_account_daily_usage_v1 VALUES (?,?,?,?,?)
               ON CONFLICT(quota_day,account_id,model_family) DO UPDATE SET
                 request_count=request_count+1,updated_at=excluded.updated_at""",
            (day, policy.credential_pool_id, profile.model_id, 1, timestamp),
        )
        connection.execute(
            """INSERT INTO news_ai_account_minute_usage_v1
               (minute_bucket,account_id,model_family,request_count,
                input_token_count,updated_at) VALUES (?,?,?,?,?,?)
               ON CONFLICT(minute_bucket,account_id,model_family) DO UPDATE SET
                 request_count=request_count+1,
                 input_token_count=input_token_count+excluded.input_token_count,
                 updated_at=excluded.updated_at""",
            (
                minute, policy.credential_pool_id, profile.model_id, 1,
                estimated_input_tokens, timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO news_ai_account_request_usage_v1
               (usage_id,account_id,model_family,request_count,
                input_token_count,reserved_at)
               VALUES (?,?,?,?,?,?)""",
            (
                reservation_id, policy.credential_pool_id, profile.model_id,
                1, estimated_input_tokens, timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO assistant_capacity_reservations_v1 VALUES (
                 ?,?,?,?,?,?,?,?,?,?,'IN_FLIGHT',NULL,NULL,?,?,NULL)""",
            (
                reservation_id, policy.credential_pool_id, fingerprint,
                profile.model_id, profile.profile_id, service_priority.value,
                day, minute, estimated_input_tokens, estimated_input_tokens,
                timestamp, expires_at,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return AssistantCapacityReservation(
        reservation_id=reservation_id,
        credential_pool_id=policy.credential_pool_id,
        pool_fingerprint=fingerprint,
        profile_id=profile.profile_id,
        model_id=profile.model_id,
        service_priority=service_priority,
        estimated_input_tokens=estimated_input_tokens,
        policy=policy,
    )


def _confirm_reserved_tokens(
    connection: sqlite3.Connection,
    reservation: AssistantCapacityReservation,
    actual_input_tokens: int,
) -> bool:
    actual = _strict_integer(
        actual_input_tokens, "actual_input_tokens", minimum=1, maximum=1_000_000_000,
    )
    if actual <= reservation.estimated_input_tokens:
        return True
    delta = actual - reservation.estimated_input_tokens
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """SELECT quota_day,minute_bucket,reserved_input_tokens,state
               FROM assistant_capacity_reservations_v1
               WHERE reservation_id=?""",
            (reservation.reservation_id,),
        ).fetchone()
        if row is None or row["state"] != "IN_FLIGHT":
            connection.rollback()
            return False
        policy = reservation.policy
        reservation_minute = datetime.fromisoformat(str(row["minute_bucket"]))
        now = datetime.now(UTC)
        _, _, recent_tokens = _usage(
            connection,
            pool_id=policy.credential_pool_id,
            model_ids=policy.shared_model_ids,
            now=reservation_minute,
        )
        tpm_soft = _soft_limit(policy.tpm_limit, policy.soft_cap_basis_points)
        if recent_tokens + delta > tpm_soft:
            connection.rollback()
            return False
        timestamp = _iso(now)
        connection.execute(
            """UPDATE news_ai_account_minute_usage_v1
               SET input_token_count=input_token_count+?,updated_at=?
               WHERE minute_bucket=? AND account_id=? AND model_family=?""",
            (
                delta, timestamp, row["minute_bucket"],
                policy.credential_pool_id, reservation.model_id,
            ),
        )
        connection.execute(
            """UPDATE news_ai_account_request_usage_v1
               SET input_token_count=input_token_count+?
               WHERE usage_id=?""",
            (delta, reservation.reservation_id),
        )
        connection.execute(
            """UPDATE assistant_capacity_reservations_v1
               SET reserved_input_tokens=? WHERE reservation_id=?""",
            (actual, reservation.reservation_id),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise


class PreReservedAssistantAccountant(ModelRequestAccountant):
    """Confirm the gateway request against one durable pre-reservation."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        reservation: AssistantCapacityReservation,
    ) -> None:
        self.connection = connection
        self.reservation = reservation
        self.confirmed = False

    def reserve(self, usage: ModelRequestUsage) -> bool:
        if self.confirmed:
            return False
        if usage.model != self.reservation.model_id or not usage.purpose.strip():
            raise ValueError("Assistant gateway usage does not match reservation")
        self.confirmed = _confirm_reserved_tokens(
            self.connection, self.reservation, usage.input_tokens,
        )
        return self.confirmed


def _finish_reservation(
    connection: sqlite3.Connection,
    reservation: AssistantCapacityReservation,
    *,
    state: str,
    now: datetime,
    provider_http_status: int | None = None,
    failure_code: str | None = None,
    affects_health: bool = False,
) -> None:
    if failure_code is not None and not _FAILURE_CODE.fullmatch(failure_code):
        raise ValueError("Assistant capacity failure_code is invalid")
    timestamp = _iso(now)
    connection.execute("BEGIN IMMEDIATE")
    try:
        updated = connection.execute(
            """UPDATE assistant_capacity_reservations_v1
               SET state=?,provider_http_status=?,failure_code=?,completed_at=?
               WHERE reservation_id=? AND state='IN_FLIGHT'""",
            (
                state, provider_http_status, failure_code, timestamp,
                reservation.reservation_id,
            ),
        )
        if updated.rowcount != 1:
            connection.rollback()
            return
        policy = reservation.policy
        if state == "SUCCEEDED":
            connection.execute(
                """INSERT INTO assistant_capacity_health_v1 VALUES (
                     ?,?,'HEALTHY',0,0,0,NULL,NULL,NULL,?)
                   ON CONFLICT(credential_pool_id,model_id) DO UPDATE SET
                     health='HEALTHY',consecutive_failures=0,cooldown_until=NULL,
                     last_provider_http_status=NULL,updated_at=excluded.updated_at""",
                (policy.credential_pool_id, policy.model_id, timestamp),
            )
        elif affects_health:
            prior = connection.execute(
                """SELECT consecutive_failures,failure_count,throttle_count
                   FROM assistant_capacity_health_v1
                   WHERE credential_pool_id=? AND model_id=?""",
                (policy.credential_pool_id, policy.model_id),
            ).fetchone()
            consecutive = (int(prior["consecutive_failures"]) if prior else 0) + 1
            failures = (int(prior["failure_count"]) if prior else 0) + 1
            throttles = (int(prior["throttle_count"]) if prior else 0) + (
                1 if provider_http_status == 429 else 0
            )
            cooldown = provider_http_status == 429 or (
                provider_http_status in {None, 500, 502, 503, 504}
                and consecutive >= policy.failure_cooldown_threshold
            )
            cooldown_until = _iso(
                now + timedelta(seconds=policy.cooldown_seconds)
            ) if cooldown else None
            connection.execute(
                """INSERT INTO assistant_capacity_health_v1 VALUES (
                     ?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(credential_pool_id,model_id) DO UPDATE SET
                     health=excluded.health,
                     consecutive_failures=excluded.consecutive_failures,
                     failure_count=excluded.failure_count,
                     throttle_count=excluded.throttle_count,
                     cooldown_until=excluded.cooldown_until,
                     last_provider_http_status=excluded.last_provider_http_status,
                     last_failure_at=excluded.last_failure_at,
                     updated_at=excluded.updated_at""",
                (
                    policy.credential_pool_id, policy.model_id,
                    "COOLDOWN" if cooldown else "DEGRADED",
                    consecutive, failures, throttles, cooldown_until,
                    provider_http_status, timestamp, timestamp,
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _eligible_credentials(
    credentials: tuple[ApiCredential, ...],
    service_priority: AssistantServicePriority,
) -> tuple[ApiCredential, ...]:
    if service_priority is AssistantServicePriority.BACKGROUND:
        return tuple(item for item in credentials if item.pool == ROUTINE_POOL)
    return tuple(
        item for item in credentials
        if item.pool in {PREEMPTIBLE_POOL, ROUTINE_POOL}
    )


def execute_assistant_capacity_route(
    connection: sqlite3.Connection,
    plan: AssistantRoutePlan,
    credentials: tuple[ApiCredential, ...],
    *,
    service_priority: AssistantServicePriority | str,
    invoke: Callable[
        [ModelProfile, ApiCredential, str | None, ModelRequestAccountant], T
    ],
    before_invoke: Callable[[], None] | None = None,
    policies: tuple[AssistantCapacityPolicy, ...] | None = None,
    now: datetime | None = None,
) -> AssistantCapacityResult[T]:
    """Select safe pool x model capacity after the model plan is fixed."""
    try:
        priority = AssistantServicePriority(str(service_priority))
    except ValueError as error:
        raise ValueError("Assistant service priority is invalid") from error
    if before_invoke is not None and not callable(before_invoke):
        raise ValueError("Assistant capacity pre-invoke callback is invalid")
    instant = now or datetime.now(UTC)
    _iso(instant)

    def event_now() -> datetime:
        return datetime.now(UTC) if now is None else instant

    credentials = _validated_credentials(credentials)
    eligible = _eligible_credentials(credentials, priority)
    if not eligible:
        raise AssistantCapacityUnavailable("no eligible Assistant credential pool")
    declared_policies = (
        configured_assistant_capacity_policies(eligible, plan.candidate_profiles)
        if policies is None else policies
    )
    declared_policies = _validated_capacity_policies(
        declared_policies, credentials, plan.candidate_profiles,
    )
    policy_by_pair = {
        (policy.credential_pool_id, policy.model_id): policy
        for policy in declared_policies if policy.enabled
    }
    by_pool: dict[str, list[ApiCredential]] = {}
    pool_order: list[str] = []
    for credential in eligible:
        if credential.account_id not in by_pool:
            by_pool[credential.account_id] = []
            pool_order.append(credential.account_id)
        by_pool[credential.account_id].append(credential)
    pool_index = {pool_id: index for index, pool_id in enumerate(pool_order)}
    candidate_pairs = {
        (pool_id, profile.model_id)
        for profile in plan.candidate_profiles
        for pool_id in by_pool
        if (pool_id, profile.model_id) in policy_by_pair
    }
    candidate_pools = {pool_id for pool_id, _model in candidate_pairs}
    if not candidate_pairs:
        raise AssistantCapacityUnavailable("no configured pool x model policy")

    attempts = 0
    last_retryable_error: Exception | None = None
    saw_capacity_pressure = False
    for profile_index, profile in enumerate(plan.candidate_profiles):
        snapshots: dict[str, _CapacitySnapshot] = {}
        for pool_id in by_pool:
            policy = policy_by_pair.get((pool_id, profile.model_id))
            if policy is None:
                continue
            snapshots[pool_id] = _capacity_snapshot(
                connection, policy,
                estimated_input_tokens=plan.estimated_input_tokens,
                now=instant,
            )
        ordered_pools = sorted(
            (
                pool_id for pool_id, snapshot in snapshots.items()
                if snapshot.available
            ),
            key=lambda pool_id: (
                -snapshots[pool_id].score,
                by_pool[pool_id][0].pool != PREEMPTIBLE_POOL,
                pool_index[pool_id],
            ),
        )
        if not ordered_pools:
            saw_capacity_pressure = True
            continue
        for pool_id in ordered_pools:
            policy = policy_by_pair[(pool_id, profile.model_id)]
            credentials_in_pool = sorted(
                by_pool[pool_id], key=lambda item: item.credential_id,
            )
            for credential_index, credential in enumerate(credentials_in_pool):
                if attempts >= MAX_ASSISTANT_CAPACITY_ATTEMPTS:
                    saw_capacity_pressure = True
                    break
                if before_invoke is not None:
                    before_invoke()
                reservation = _reserve_assistant_capacity(
                    connection,
                    policy=policy,
                    profile=profile,
                    service_priority=priority,
                    estimated_input_tokens=plan.estimated_input_tokens,
                    now=datetime.now(UTC) if now is None else instant,
                )
                if reservation is None:
                    saw_capacity_pressure = True
                    break
                attempts += 1
                accountant = PreReservedAssistantAccountant(connection, reservation)
                try:
                    value = invoke(
                        profile, credential,
                        provider_thinking_level(plan, profile), accountant,
                    )
                    if not accountant.confirmed:
                        raise ValueError(
                            "Assistant invocation bypassed the metered gateway"
                        )
                except ModelGatewayCapacityExhausted:
                    _finish_reservation(
                        connection, reservation,
                        state="CAPACITY_REJECTED",
                        now=event_now(),
                        failure_code="EXACT_TOKEN_CAPACITY_REJECTED",
                    )
                    saw_capacity_pressure = True
                    break
                except urllib.error.HTTPError as error:
                    status = int(error.code)
                    retryable = status in {401, 403, 429, 500, 502, 503, 504}
                    _finish_reservation(
                        connection, reservation,
                        state="THROTTLED" if status == 429 else "FAILED",
                        now=event_now(),
                        provider_http_status=status,
                        failure_code=f"PROVIDER_HTTP_{status}",
                        affects_health=retryable,
                    )
                    if not retryable:
                        raise
                    last_retryable_error = error
                    if status == 429:
                        saw_capacity_pressure = True
                    if status not in {401, 403}:
                        break
                    if credential_index + 1 >= len(credentials_in_pool):
                        break
                    continue
                except (urllib.error.URLError, TimeoutError) as error:
                    _finish_reservation(
                        connection, reservation,
                        state="FAILED",
                        now=event_now(),
                        failure_code="PROVIDER_TRANSPORT_UNAVAILABLE",
                        affects_health=True,
                    )
                    last_retryable_error = error
                    break
                except Exception:
                    _finish_reservation(
                        connection, reservation,
                        state="FAILED",
                        now=event_now(),
                        failure_code="MODEL_REQUEST_FAILED",
                    )
                    raise
                _finish_reservation(
                    connection, reservation,
                    state="SUCCEEDED",
                    now=event_now(),
                )
                route = routing_provenance(plan, profile)
                if route["policy_version"] != ASSISTANT_ROUTING_POLICY_VERSION:
                    raise ValueError("Assistant model routing policy is incompatible")
                route["capacity"] = {
                    "policy_version": ASSISTANT_CAPACITY_POLICY_VERSION,
                    "service_priority": priority.value,
                    "selected_pool_fingerprint": reservation.pool_fingerprint,
                    "selected_pool_type": credential.pool,
                    "candidate_pool_count": len(candidate_pools),
                    "candidate_pair_count": len(candidate_pairs),
                    "attempt_count": attempts,
                    "estimated_input_tokens": plan.estimated_input_tokens,
                    "soft_cap_basis_points": policy.soft_cap_basis_points,
                    "max_in_flight": policy.max_in_flight,
                    "policy_source": policy.source,
                    "model_fallback_used": profile_index > 0,
                }
                return AssistantCapacityResult(
                    value=value, profile=profile, routing=route,
                )
    if saw_capacity_pressure:
        raise AssistantCapacityUnavailable(
            "all compatible Assistant capacity is inside safety limits"
        )
    if last_retryable_error is not None:
        raise last_retryable_error
    raise AssistantCapacityUnavailable("no usable Assistant pool x model route")
