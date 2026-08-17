"""Dedicated local model boundary for Assistant chat and context compaction."""

from __future__ import annotations

import hashlib

from .assistant_capacity import AssistantCapacityPolicy
from .assistant_routing import ModelCapacityClass, ModelProfile, OLLAMA_LOCAL
from .news_scheduler import ROUTINE_POOL, ApiCredential


QWEN_ASSISTANT_MODEL = "assistant-qwen35-4b-256k:latest"
LOCAL_ASSISTANT_POOL_ID = "local-assistant-gpu"
LOCAL_ASSISTANT_CONTEXT_LIMIT = 262_144


def local_assistant_profiles() -> tuple[ModelProfile, ...]:
    """Return the single hardware-validated Assistant profile."""
    return (
        ModelProfile(
            profile_id="assistant-qwen35-4b-local-v1",
            model_id=QWEN_ASSISTANT_MODEL,
            provider=OLLAMA_LOCAL,
            context_limit=LOCAL_ASSISTANT_CONTEXT_LIMIT,
            supports_thinking=True,
            supports_function_calling=True,
            supports_streaming=False,
            capacity_class=ModelCapacityClass.LARGE,
        ),
    )


def local_assistant_credentials() -> tuple[ApiCredential, ...]:
    reference = "ollama-loopback"
    return (ApiCredential(
        account_id=LOCAL_ASSISTANT_POOL_ID,
        pool=ROUTINE_POOL,
        api_key=reference,
        credential_id=hashlib.sha256(reference.encode("utf-8")).hexdigest()[:24],
    ),)


def local_assistant_capacity_policies() -> tuple[AssistantCapacityPolicy, ...]:
    models = (QWEN_ASSISTANT_MODEL,)
    return tuple(
        AssistantCapacityPolicy(
            credential_pool_id=LOCAL_ASSISTANT_POOL_ID,
            provider=OLLAMA_LOCAL,
            model_id=model,
            shared_model_ids=models,
            rpd_limit=100_000,
            rpm_limit=120,
            tpm_limit=100_000_000,
            soft_cap_basis_points=9_500,
            # Serial admission keeps the 256K KV-cache and embedding service
            # inside one predictable GPU capacity boundary.
            max_in_flight=1,
            reservation_ttl_seconds=240,
            cooldown_seconds=15,
            failure_cooldown_threshold=1,
            enabled=True,
            source="CONFIGURED",
        )
        for model in models
    )
