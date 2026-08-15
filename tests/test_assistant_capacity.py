from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.assistant_capacity import (
    ASSISTANT_CAPACITY_POLICY_VERSION,
    AssistantCapacityPolicy,
    AssistantCapacityUnavailable,
    AssistantServicePriority,
    configured_assistant_capacity_policies,
    credential_pool_fingerprint,
    execute_assistant_capacity_route,
)
from xauusd_forecaster.assistant_routing import (
    AssistantTaskType,
    ModelCapacityClass,
    ModelProfile,
    plan_assistant_route,
)
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.model_gateway import (
    ModelGatewayCapacityExhausted,
    ModelRequestUsage,
)
from xauusd_forecaster.news_scheduler import (
    PREEMPTIBLE_POOL,
    ROUTINE_POOL,
    ApiCredential,
    minute_bucket,
    quota_day,
)


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
PROVIDER = "GOOGLE_GENERATIVE_LANGUAGE"


def _credential(
    account_id: str,
    *,
    pool: str = PREEMPTIBLE_POOL,
    key: str | None = None,
    credential_id: str | None = None,
) -> ApiCredential:
    return ApiCredential(
        account_id=account_id,
        pool=pool,
        api_key=key or f"secret-{account_id}",
        credential_id=credential_id or f"fingerprint-{account_id}",
    )


def _profile(
    profile_id: str,
    model_id: str,
    capacity_class: ModelCapacityClass,
) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        model_id=model_id,
        provider=PROVIDER,
        context_limit=32_768,
        supports_thinking=True,
        supports_function_calling=False,
        supports_streaming=False,
        capacity_class=capacity_class,
    )


def _policy(
    pool_id: str,
    model_id: str,
    *,
    rpd: int = 100,
    rpm: int = 100,
    tpm: int = 100_000,
    soft_cap_basis_points: int = 8_000,
    max_in_flight: int = 2,
    failure_threshold: int = 2,
) -> AssistantCapacityPolicy:
    return AssistantCapacityPolicy(
        credential_pool_id=pool_id,
        provider=PROVIDER,
        model_id=model_id,
        shared_model_ids=(model_id,),
        rpd_limit=rpd,
        rpm_limit=rpm,
        tpm_limit=tpm,
        soft_cap_basis_points=soft_cap_basis_points,
        max_in_flight=max_in_flight,
        reservation_ttl_seconds=180,
        cooldown_seconds=60,
        failure_cooldown_threshold=failure_threshold,
        enabled=True,
        source="CONFIGURED",
    )


def _plan(
    profiles: tuple[ModelProfile, ...],
    *,
    simple: bool = False,
    estimated_input_tokens: int = 1_000,
):
    return plan_assistant_route(
        (
            AssistantTaskType.CONVERSATION_TITLE
            if simple else AssistantTaskType.NEWS_QA
        ),
        estimated_input_tokens=estimated_input_tokens,
        reserved_output_tokens=80 if simple else 1_200,
        user_text="为什么黄金变化？",
        planned_tool_calls=0,
        profiles=profiles,
    )


def _success(value: object):
    def invoke(profile, credential, thinking_level, accountant):
        assert credential.api_key.startswith("secret-")
        assert thinking_level in {"minimal", "high"}
        assert accountant.reserve(ModelRequestUsage(
            model=profile.model_id,
            purpose="assistant-test",
            input_tokens=900,
        ))
        return value(credential, profile) if callable(value) else value

    return invoke


def test_capacity_policy_expands_wildcards_without_secret_material() -> None:
    credentials = (
        _credential("pool-a"),
        _credential("pool-b", pool=ROUTINE_POOL),
    )
    profiles = (
        _profile("small-v1", "small-model", ModelCapacityClass.SMALL),
        _profile("large-v1", "large-model", ModelCapacityClass.LARGE),
    )
    raw = json.dumps([
        {
            "credential_pool_id": "*",
            "provider": PROVIDER,
            "model_id": profile.model_id,
            "shared_model_ids": [profile.model_id],
            "rpd_limit": 1_000,
            "rpm_limit": 20,
            "tpm_limit": 50_000,
            "soft_cap_ratio": "0.8",
            "max_in_flight": 3,
            "reservation_ttl_seconds": 180,
            "cooldown_seconds": 60,
            "failure_cooldown_threshold": 2,
        }
        for profile in profiles
    ])

    policies = configured_assistant_capacity_policies(
        credentials, profiles, raw,
    )

    assert {
        (policy.credential_pool_id, policy.model_id) for policy in policies
    } == {
        ("pool-a", "small-model"), ("pool-a", "large-model"),
        ("pool-b", "small-model"), ("pool-b", "large-model"),
    }
    assert all(policy.soft_cap_basis_points == 8_000 for policy in policies)
    assert "secret-pool" not in repr(policies)


def test_unknown_model_limits_are_not_guessed() -> None:
    profile = _profile("future-v1", "unregistered-model", ModelCapacityClass.LARGE)
    credential = _credential("pool-a")

    assert configured_assistant_capacity_policies(
        (credential,), (profile,), raw_policies="",
    ) == ()
    connection = sqlite3_connection()
    with pytest.raises(AssistantCapacityUnavailable, match="configured"):
        execute_assistant_capacity_route(
            connection, _plan((profile,)), (credential,),
            service_priority=AssistantServicePriority.INTERACTIVE,
            policies=(), invoke=_success("unused"), now=NOW,
        )
    connection.close()


@pytest.mark.parametrize(
    "field,value",
    (
        ("rpd_limit", -1),
        ("rpm_limit", 1.5),
        ("soft_cap_ratio", 0),
        ("soft_cap_ratio", 1.01),
        ("soft_cap_ratio", 0.80001),
        ("max_in_flight", True),
    ),
)
def test_capacity_policy_rejects_malformed_operational_limits(
    field: str,
    value: object,
) -> None:
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    item = {
        "credential_pool_id": "*",
        "provider": PROVIDER,
        "model_id": profile.model_id,
        "rpd_limit": 100,
        "rpm_limit": 10,
        "tpm_limit": 10_000,
        "soft_cap_ratio": 0.8,
        "max_in_flight": 2,
        "reservation_ttl_seconds": 180,
        "cooldown_seconds": 60,
        "failure_cooldown_threshold": 2,
        field: value,
    }
    with pytest.raises(ValueError, match="Assistant capacity"):
        configured_assistant_capacity_policies(
            (_credential("pool-a"),), (profile,), json.dumps([item]),
        )


def test_capacity_router_rejects_one_key_fingerprint_across_independent_pools(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    credentials = (
        _credential("pool-a", key="shared-secret", credential_id="shared-key"),
        _credential("pool-b", key="shared-secret", credential_id="shared-key"),
    )

    with pytest.raises(ValueError, match="credential is duplicated"):
        execute_assistant_capacity_route(
            ledger.connection, _plan((profile,)), credentials,
            service_priority=AssistantServicePriority.INTERACTIVE,
            policies=tuple(
                _policy(pool_id, profile.model_id)
                for pool_id in ("pool-a", "pool-b")
            ),
            invoke=_success("unused"), now=NOW,
        )
    ledger.close()


def test_capacity_router_ignores_policy_outside_fixed_model_plan(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)

    result = execute_assistant_capacity_route(
        ledger.connection, _plan((profile,)), (_credential("pool-a"),),
        service_priority=AssistantServicePriority.INTERACTIVE,
        policies=(
            _policy("pool-a", "configured-small-model"),
            _policy("pool-a", profile.model_id),
        ),
        invoke=_success("ok"), now=NOW,
    )

    assert result.value == "ok"
    assert result.profile == profile
    assert result.routing["capacity"]["candidate_pair_count"] == 1
    ledger.close()


def test_capacity_router_rejects_naive_accounting_time(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)

    with pytest.raises(ValueError, match="timezone-aware"):
        execute_assistant_capacity_route(
            ledger.connection, _plan((profile,)), (_credential("pool-a"),),
            service_priority=AssistantServicePriority.INTERACTIVE,
            policies=(_policy("pool-a", profile.model_id),),
            invoke=_success("unused"), now=NOW.replace(tzinfo=None),
        )
    ledger.close()


def test_pre_invoke_gate_runs_before_any_capacity_reservation(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    invoked = False

    def invoke(*_args):
        nonlocal invoked
        invoked = True
        return "must-not-run"

    def reject_stale_lease() -> None:
        raise RuntimeError("chat lease was lost")

    with pytest.raises(RuntimeError, match="lease was lost"):
        execute_assistant_capacity_route(
            ledger.connection,
            _plan((profile,)),
            (_credential("pool-a"),),
            service_priority=AssistantServicePriority.INTERACTIVE,
            policies=(_policy("pool-a", profile.model_id),),
            invoke=invoke,
            before_invoke=reject_stale_lease,
            now=NOW,
        )

    assert invoked is False
    assert ledger.connection.execute(
        "SELECT count(*) FROM assistant_capacity_reservations_v1",
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_daily_usage_v1",
    ).fetchone()[0] == 0
    ledger.close()


def test_router_uses_another_pool_before_crossing_soft_rpd(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    plan = _plan((profile,))
    credentials = (_credential("pool-a"), _credential("pool-b"))
    policies = (
        _policy("pool-a", profile.model_id, rpd=10),
        _policy("pool-b", profile.model_id, rpd=10),
    )
    with ledger.connection:
        ledger.connection.execute(
            "INSERT INTO news_ai_account_daily_usage_v1 VALUES (?,?,?,?,?)",
            (quota_day(NOW), "pool-a", profile.model_id, 8, NOW.isoformat()),
        )

    result = execute_assistant_capacity_route(
        ledger.connection, plan, credentials,
        service_priority=AssistantServicePriority.INTERACTIVE,
        policies=policies,
        invoke=_success(lambda credential, _profile: credential.account_id),
        now=NOW,
    )

    assert result.value == "pool-b"
    capacity = result.routing["capacity"]
    assert capacity["policy_version"] == ASSISTANT_CAPACITY_POLICY_VERSION
    assert capacity["selected_pool_fingerprint"] == credential_pool_fingerprint(
        "pool-b"
    )
    serialized = json.dumps(result.routing, sort_keys=True)
    assert "pool-a" not in serialized
    assert "pool-b" not in serialized
    assert "secret-" not in serialized
    ledger.close()


def test_simple_route_falls_back_model_only_after_preferred_capacity_is_full(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    small = _profile("small-v1", "small-model", ModelCapacityClass.SMALL)
    large = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    plan = _plan((small, large), simple=True)
    credential = _credential("pool-a", pool=ROUTINE_POOL)
    policies = (
        _policy("pool-a", small.model_id, tpm=1_000),
        _policy("pool-a", large.model_id, tpm=100_000),
    )

    result = execute_assistant_capacity_route(
        ledger.connection, plan, (credential,),
        service_priority=AssistantServicePriority.BACKGROUND,
        policies=policies,
        invoke=_success(lambda _credential, profile: profile.model_id),
        now=NOW,
    )

    assert result.value == "large-model"
    assert result.routing["capacity"]["model_fallback_used"] is True
    ledger.close()


def test_background_cannot_consume_preemptible_pool() -> None:
    connection = sqlite3_connection()
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    with pytest.raises(AssistantCapacityUnavailable, match="eligible"):
        execute_assistant_capacity_route(
            connection, _plan((profile,)), (_credential("pool-a"),),
            service_priority=AssistantServicePriority.BACKGROUND,
            policies=(_policy("pool-a", profile.model_id),),
            invoke=_success("unused"),
            now=NOW,
        )
    connection.close()


def sqlite3_connection():
    import sqlite3

    from xauusd_forecaster.assistant_capacity import (
        install_assistant_capacity_schema,
    )
    from xauusd_forecaster.news_scheduler import install_scheduler_schema

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    install_scheduler_schema(connection)
    install_assistant_capacity_schema(connection)
    return connection


def test_429_cools_one_pool_and_continues_with_another(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    credentials = (_credential("pool-a"), _credential("pool-b"))
    policies = tuple(
        _policy(pool_id, profile.model_id) for pool_id in ("pool-a", "pool-b")
    )
    calls: list[str] = []

    def invoke(selected, credential, _thinking, accountant):
        calls.append(credential.account_id)
        assert accountant.reserve(ModelRequestUsage(
            model=selected.model_id, purpose="assistant-test", input_tokens=900,
        ))
        if credential.account_id == "pool-a":
            raise urllib.error.HTTPError(
                "https://provider.invalid", 429, "quota", {}, None,
            )
        return "ok"

    result = execute_assistant_capacity_route(
        ledger.connection, _plan((profile,)), credentials,
        service_priority=AssistantServicePriority.INTERACTIVE,
        policies=policies, invoke=invoke, now=NOW,
    )

    assert result.value == "ok"
    assert calls == ["pool-a", "pool-b"]
    health = ledger.connection.execute(
        "SELECT * FROM assistant_capacity_health_v1 WHERE credential_pool_id='pool-a'",
    ).fetchone()
    assert health["health"] == "COOLDOWN"
    assert health["throttle_count"] == 1
    ledger.close()


def test_transport_failure_degrades_one_pool_and_continues_with_another(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    credentials = (_credential("pool-a"), _credential("pool-b"))
    policies = tuple(
        _policy(pool_id, profile.model_id) for pool_id in ("pool-a", "pool-b")
    )
    calls: list[str] = []

    def invoke(selected, credential, _thinking, accountant):
        calls.append(credential.account_id)
        assert accountant.reserve(ModelRequestUsage(
            model=selected.model_id, purpose="assistant-test", input_tokens=900,
        ))
        if credential.account_id == "pool-a":
            raise urllib.error.URLError("temporary network failure")
        return "ok"

    result = execute_assistant_capacity_route(
        ledger.connection, _plan((profile,)), credentials,
        service_priority=AssistantServicePriority.INTERACTIVE,
        policies=policies, invoke=invoke, now=NOW,
    )

    assert result.value == "ok"
    assert calls == ["pool-a", "pool-b"]
    health = ledger.connection.execute(
        "SELECT * FROM assistant_capacity_health_v1 WHERE credential_pool_id='pool-a'",
    ).fetchone()
    assert health["health"] == "DEGRADED"
    assert health["failure_count"] == 1
    ledger.close()


def test_auth_failure_can_try_another_key_without_inventing_pool_capacity(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    credentials = (
        _credential("pool-a", key="secret-first", credential_id="a"),
        _credential("pool-a", key="secret-second", credential_id="b"),
        _credential("pool-a", key="secret-third", credential_id="c"),
    )
    calls: list[str] = []

    def invoke(selected, credential, _thinking, accountant):
        calls.append(credential.credential_id)
        assert accountant.reserve(ModelRequestUsage(
            model=selected.model_id, purpose="assistant-test", input_tokens=900,
        ))
        if credential.credential_id in {"a", "b"}:
            raise urllib.error.HTTPError(
                "https://provider.invalid", 401, "auth", {}, None,
            )
        return "ok"

    result = execute_assistant_capacity_route(
        ledger.connection, _plan((profile,)), credentials,
        service_priority=AssistantServicePriority.INTERACTIVE,
        policies=(_policy(
            "pool-a", profile.model_id, failure_threshold=1,
        ),),
        invoke=invoke, now=NOW,
    )

    assert result.value == "ok"
    assert calls == ["a", "b", "c"]
    assert result.routing["capacity"]["candidate_pool_count"] == 1
    assert result.routing["capacity"]["attempt_count"] == 3
    ledger.close()


def test_expired_in_flight_reservation_is_recovered_before_admission(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    policy = _policy("pool-a", profile.model_id, max_in_flight=1)
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO assistant_capacity_reservations_v1 VALUES (
                 'expired','pool-a','fingerprint','large-model','large-v1',
                 'INTERACTIVE',?,?,1000,1000,'IN_FLIGHT',NULL,NULL,?,?,NULL)""",
            (
                quota_day(NOW), minute_bucket(NOW),
                (NOW - timedelta(minutes=5)).isoformat(),
                (NOW - timedelta(minutes=2)).isoformat(),
            ),
        )

    result = execute_assistant_capacity_route(
        ledger.connection, _plan((profile,)), (_credential("pool-a"),),
        service_priority=AssistantServicePriority.INTERACTIVE,
        policies=(policy,), invoke=_success("ok"), now=NOW,
    )

    assert result.value == "ok"
    state = ledger.connection.execute(
        "SELECT state FROM assistant_capacity_reservations_v1 WHERE reservation_id='expired'",
    ).fetchone()[0]
    assert state == "ABANDONED"
    ledger.close()


def test_gateway_must_confirm_the_pre_reserved_request(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)

    with pytest.raises(ValueError, match="metered gateway"):
        execute_assistant_capacity_route(
            ledger.connection, _plan((profile,)), (_credential("pool-a"),),
            service_priority=AssistantServicePriority.INTERACTIVE,
            policies=(_policy("pool-a", profile.model_id),),
            invoke=lambda *_args: "bypassed",
            now=NOW,
        )
    state = ledger.connection.execute(
        "SELECT state FROM assistant_capacity_reservations_v1",
    ).fetchone()[0]
    assert state == "FAILED"
    ledger.close()


def test_exact_token_growth_fails_closed_at_soft_tpm(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    profile = _profile("large-v1", "large-model", ModelCapacityClass.LARGE)
    plan = _plan((profile,), estimated_input_tokens=1_000)

    def invoke(selected, _credential, _thinking, accountant):
        if not accountant.reserve(ModelRequestUsage(
            model=selected.model_id,
            purpose="assistant-test",
            input_tokens=1_700,
        )):
            raise ModelGatewayCapacityExhausted("exact request exceeds TPM")
        return "unexpected"

    with pytest.raises(AssistantCapacityUnavailable):
        execute_assistant_capacity_route(
            ledger.connection, plan, (_credential("pool-a"),),
            service_priority=AssistantServicePriority.INTERACTIVE,
            policies=(_policy("pool-a", profile.model_id, tpm=2_000),),
            invoke=invoke, now=NOW,
        )
    row = ledger.connection.execute(
        "SELECT state,reserved_input_tokens FROM assistant_capacity_reservations_v1",
    ).fetchone()
    assert tuple(row) == ("CAPACITY_REJECTED", 1_000)
    ledger.close()
