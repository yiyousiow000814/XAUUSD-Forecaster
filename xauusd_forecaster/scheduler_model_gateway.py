"""Scheduler-owned durable accounting for generative model requests."""

from __future__ import annotations

import sqlite3

from .ai_provider_registry import quota_surface_for_model
from .annotation import DEFAULT_GEMINI_MODEL, GEMINI_DAILY_PRIORITY_RESERVE
from .model_gateway import ModelRequestAccountant, ModelRequestUsage
from .news_scheduler import ApiCredential, reserve_account_request


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

    def reserve(self, usage: ModelRequestUsage) -> bool:
        policy = quota_surface_for_model(usage.model)
        reserve_total = (
            GEMINI_DAILY_PRIORITY_RESERVE
            if usage.model == DEFAULT_GEMINI_MODEL
            and usage.purpose == "news-annotation"
            else 0
        )
        return reserve_account_request(
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
        )
