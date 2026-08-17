"""Scheduler-owned durable accounting for generative model requests."""

from __future__ import annotations

import sqlite3

from .ai_provider_registry import quota_surface_for_model
from .annotation import DEFAULT_GEMINI_MODEL, GEMINI_DAILY_PRIORITY_RESERVE
from .model_gateway import ModelRequestAccountant, ModelRequestUsage
from .news_scheduler import (
    ApiCredential,
    provider_dispatch_next_eligible,
    record_provider_dispatch_outcome,
    reserve_account_request,
    reserve_provider_dispatch,
)


class SchedulerModelAccountant(ModelRequestAccountant):
    """Bind provider requests to one scheduler credential and quota ledger."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        credential: ApiCredential,
        *,
        urgent: bool,
    ) -> None:
        self.connection = connection
        self.credential = credential
        self.urgent = urgent
        self._next_retry_at: str | None = None

    def reserve(self, usage: ModelRequestUsage) -> bool:
        policy = quota_surface_for_model(usage.model)
        reserve_total = (
            GEMINI_DAILY_PRIORITY_RESERVE
            if usage.model == DEFAULT_GEMINI_MODEL
            and usage.purpose == "news-annotation"
            else 0
        )
        reserved = reserve_account_request(
            self.connection,
            account_id=self.credential.account_id,
            model_family=usage.model,
            daily_limit=policy.daily_limit,
            requests_per_minute=policy.requests_per_minute,
            input_tokens=usage.input_tokens,
            input_tokens_per_minute=policy.input_tokens_per_minute,
            shared_model_families=policy.model_families,
            share_minute_across_accounts=policy.share_minute_across_accounts,
            reserve_total=reserve_total,
            urgent=self.urgent,
            provider_task=usage.purpose,
        )
        self._next_retry_at = (
            None if reserved else provider_dispatch_next_eligible(self.connection)
        )
        return reserved

    def reserve_dispatch(self, purpose: str) -> bool:
        reserved, next_eligible_at = reserve_provider_dispatch(
            self.connection, provider_task=purpose,
        )
        self._next_retry_at = None if reserved else next_eligible_at
        return reserved

    def record_provider_outcome(
        self, outcome: str, *, retry_after_seconds: int | None = None,
    ) -> None:
        record_provider_dispatch_outcome(
            self.connection,
            outcome=outcome,
            retry_after_seconds=retry_after_seconds,
        )
        self._next_retry_at = provider_dispatch_next_eligible(self.connection)

    @property
    def next_retry_at(self) -> str | None:
        return self._next_retry_at

    @property
    def allow_provider_token_count(self) -> bool:
        # A synchronous countTokens call followed by generateContent would
        # either violate pacing or perpetually defer the generation. The local
        # UTF-8 estimate is deliberately conservative and needs no transport.
        return False
