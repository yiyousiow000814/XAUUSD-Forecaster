from __future__ import annotations

from xauusd_forecaster.assistant_local_runtime import (
    LOCAL_ASSISTANT_CONTEXT_LIMIT,
    MINISTRAL_ASSISTANT_MODEL,
    QWEN_ASSISTANT_MODEL,
    local_assistant_capacity_policies,
    local_assistant_credentials,
    local_assistant_profiles,
)
from xauusd_forecaster.assistant_routing import OLLAMA_LOCAL


def test_local_runtime_is_one_serial_assistant_only_gpu_pool() -> None:
    profiles = local_assistant_profiles()
    credentials = local_assistant_credentials()
    policies = local_assistant_capacity_policies()

    assert [profile.model_id for profile in profiles] == [
        QWEN_ASSISTANT_MODEL,
        MINISTRAL_ASSISTANT_MODEL,
    ]
    assert all(profile.provider == OLLAMA_LOCAL for profile in profiles)
    assert all(
        profile.context_limit == LOCAL_ASSISTANT_CONTEXT_LIMIT
        and profile.supports_function_calling
        for profile in profiles
    )
    assert len(credentials) == 1
    assert all(policy.max_in_flight == 1 for policy in policies)
    assert all(policy.shared_model_ids == (
        QWEN_ASSISTANT_MODEL, MINISTRAL_ASSISTANT_MODEL,
    ) for policy in policies)
