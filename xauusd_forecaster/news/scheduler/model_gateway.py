"""Scheduler-owned durable accounting for generative model requests."""

from __future__ import annotations

import math
import os
import sqlite3
import uuid
from datetime import UTC

from xauusd_forecaster.ai.provider_registry import quota_surface_for_model, GROQ_NEWS_MODELS
from xauusd_forecaster.news.annotation.product import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_DAILY_PRIORITY_RESERVE,
)
from xauusd_forecaster.ai.model_gateway import (
    ModelRequestAccountant,
    ModelRequestUsage,
    NewsBackupGateway,
    NewsBackupChain,
)
from xauusd_forecaster.news.scheduler.state import (
    ApiCredential,
    CONTRACT_BACKFILL_LANE,
    CONTRACT_BACKFILL_WORKLOAD,
    LIVE_OPERATIONAL_WORKLOAD,
    calibrated_input_tokens,
    provider_dispatch_next_eligible,
    mark_account_request_attempted,
    record_account_request_outcome,
    reserve_account_request,
)


class SchedulerModelAccountant(ModelRequestAccountant):
    """Bind provider requests to one scheduler credential and quota ledger."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        credential: ApiCredential,
        *,
        urgent: bool,
        work_lane: str = "LIVE",
        enable_news_backup: bool = False,
    ) -> None:
        self.connection = connection
        self.credential = credential
        self.urgent = urgent
        self.workload_class = (
            CONTRACT_BACKFILL_WORKLOAD
            if work_lane == CONTRACT_BACKFILL_LANE
            else LIVE_OPERATIONAL_WORKLOAD
        )
        self._next_retry_at: str | None = None
        self._failure_code: str | None = None
        self._failure_evidence: dict[str, object] | None = None
        self._usage_id: str | None = None
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if groq_key and work_lane == "LIVE" and enable_news_backup:
            self.fallback_gateway = NewsBackupChain(tuple(
                NewsBackupGateway(groq_key, GroqNewsAccountant(connection, model), model=model)
                for model in GROQ_NEWS_MODELS
            ))

    def reserve(self, usage: ModelRequestUsage) -> bool:
        policy = quota_surface_for_model(usage.model)
        admitted_tokens, calibration_model_version, safe_ratio = (
            calibrated_input_tokens(
                self.connection,
                requested_model=usage.model,
                purpose=usage.purpose,
                prompt_contract=usage.prompt_contract,
                estimator_version=usage.estimator_version,
                base_estimated_input_tokens=usage.input_tokens,
            )
        )
        reserve_total = (
            GEMINI_DAILY_PRIORITY_RESERVE
            if usage.model == DEFAULT_GEMINI_MODEL
            and usage.purpose == "news-annotation"
            else 0
        )
        decision: dict[str, object] = {}
        usage_id = str(uuid.uuid4())
        reserved = reserve_account_request(
            self.connection,
            account_id=self.credential.account_id,
            model_family=usage.model,
            daily_limit=policy.daily_limit,
            requests_per_minute=policy.requests_per_minute,
            input_tokens=admitted_tokens,
            input_tokens_per_minute=policy.input_tokens_per_minute,
            shared_model_families=policy.model_families,
            share_minute_across_accounts=policy.share_minute_across_accounts,
            reserve_total=reserve_total,
            urgent=self.urgent,
            provider_task=usage.purpose,
            requested_model=usage.model,
            purpose=usage.purpose,
            prompt_contract=usage.prompt_contract,
            estimator_version=usage.estimator_version,
            base_estimated_input_tokens=usage.input_tokens,
            calibration_provider_model_version=calibration_model_version,
            calibration_safe_ratio=safe_ratio,
            workload_class=self.workload_class,
            quota_authority=policy.payload_key,
            usage_id=usage_id,
            decision=decision,
        )
        self._usage_id = usage_id if reserved else None
        self._next_retry_at = (
            str(decision["next_retry_at"])
            if decision.get("next_retry_at") else None
        )
        self._failure_code = (
            str(decision["failure_code"])
            if decision.get("failure_code") else None
        )
        self._failure_evidence = (
            {key: value for key, value in decision.items()
             if key not in {"failure_code"}}
            if not reserved and self._failure_code else None
        )
        return reserved

    def effective_base_input_token_budget(
        self,
        usage: ModelRequestUsage,
        *,
        input_tokens_per_minute: int,
    ) -> int:
        _, _, safe_ratio = calibrated_input_tokens(
            self.connection,
            requested_model=usage.model,
            purpose=usage.purpose,
            prompt_contract=usage.prompt_contract,
            estimator_version=usage.estimator_version,
            base_estimated_input_tokens=usage.input_tokens,
        )
        return max(0, math.floor(input_tokens_per_minute / safe_ratio))

    def record_provider_outcome(
        self, outcome: str, *, retry_after_seconds: int | None = None,
        usage_metadata: dict[str, int] | None = None,
        provider_model_version: str | None = None,
    ) -> None:
        if self._usage_id is None:
            raise ValueError("provider outcome has no reserved model request")
        record_account_request_outcome(
            self.connection,
            self._usage_id,
            outcome=outcome,
            retry_after_seconds=retry_after_seconds,
            usage_metadata=usage_metadata,
            provider_model_version=provider_model_version,
        )
        self._usage_id = None
        self._next_retry_at = provider_dispatch_next_eligible(self.connection)

    def mark_provider_attempted(self) -> None:
        if self._usage_id is not None:
            mark_account_request_attempted(self.connection, self._usage_id)

    @property
    def next_retry_at(self) -> str | None:
        return self._next_retry_at

    @property
    def failure_code(self) -> str | None:
        return self._failure_code

    @property
    def failure_evidence(self) -> dict[str, object] | None:
        return self._failure_evidence


class GroqNewsAccountant(ModelRequestAccountant):
    """Reuse atomic scheduler admission with one shared free-provider budget."""

    def __init__(self, connection: sqlite3.Connection, model: str) -> None:
        if model not in GROQ_NEWS_MODELS:
            raise ValueError("unapproved Groq model")
        self.connection = connection
        self._usage_id: str | None = None
        self.account_id = "GROQ_NEWS"
        self.model_family = model
        self.daily_limit, self.rpm, self.tpm, self.tpd = 1000, 30, 8000, 200000
        self.scope = "GROQ_NEWS/" + model

    def reserve(self, usage: ModelRequestUsage) -> bool:
        usage_id = str(uuid.uuid4())
        reserved = reserve_account_request(
            self.connection, account_id=self.account_id,
            model_family=self.model_family, daily_limit=self.daily_limit, requests_per_minute=self.rpm,
            input_tokens_per_minute=self.tpm, tokens_per_24_hours=self.tpd,
            input_tokens=usage.input_tokens, usage_id=usage_id,
            requested_model=usage.model, purpose=usage.purpose,
            prompt_contract=usage.prompt_contract, estimator_version=usage.estimator_version,
            quota_authority=self.model_family, quota_timezone=UTC,
            independent_provider_scope=self.scope,
        )
        self._usage_id = usage_id if reserved else None
        return reserved

    def mark_provider_attempted(self) -> None:
        if self._usage_id is None:
            raise ValueError("backup request has no reservation")
        mark_account_request_attempted(self.connection, self._usage_id)

    def record_provider_outcome(
        self, outcome: str, *, retry_after_seconds: int | None = None,
        usage_metadata: dict[str, int] | None = None,
        provider_model_version: str | None = None,
    ) -> None:
        if self._usage_id is None:
            raise ValueError("backup request has no reservation")
        record_account_request_outcome(
            self.connection, self._usage_id, outcome=outcome,
            retry_after_seconds=retry_after_seconds, usage_metadata=usage_metadata,
            provider_model_version=provider_model_version,
            independent_provider_scope=self.scope,
        )
        self._usage_id = None
