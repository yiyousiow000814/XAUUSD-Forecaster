from __future__ import annotations

import pytest

from xauusd_forecaster.ai_provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.annotation import DEFAULT_GEMMA_MODEL
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.model_gateway import GeminiModelGateway, ModelRequestUsage
from xauusd_forecaster.news_scheduler import ApiCredential
from xauusd_forecaster.scheduler_model_gateway import SchedulerModelAccountant


@pytest.mark.parametrize(
    "model",
    [surface.model_families[0] for surface in AI_QUOTA_SURFACES],
)
def test_every_registered_model_is_accounted_under_the_model_sent(
    tmp_path, model: str,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )

    assert accountant.reserve(ModelRequestUsage(
        model=model, purpose="headline-translation", input_tokens=321,
    ))
    row = ledger.connection.execute(
        """SELECT model_family,request_count,input_token_count
           FROM news_ai_account_minute_usage_v1"""
    ).fetchone()

    assert dict(row) == {
        "model_family": model,
        "request_count": 1,
        "input_token_count": 321,
    }
    ledger.close()


def test_title_fallback_moves_accounting_to_the_actual_gemini_surface(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )

    assert accountant.reserve(ModelRequestUsage(
        model="gemini-3.1-flash-lite",
        purpose="headline-translation",
        input_tokens=123,
    ))
    families = [str(row["model_family"]) for row in ledger.connection.execute(
        "SELECT model_family FROM news_ai_account_daily_usage_v1"
    )]

    assert families == ["gemini-3.1-flash-lite"]
    ledger.close()


def test_successful_generation_persists_sanitized_provider_usage(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )
    gateway = GeminiModelGateway(
        ("secret",), requests_per_key=1, accountant=accountant,
    )
    gateway._post_json = lambda *args, **kwargs: {
        "value": 7,
        "modelVersion": "gemma-version",
        "usageMetadata": {
            "promptTokenCount": 123,
            "candidatesTokenCount": 45,
            "totalTokenCount": 168,
            "providerSpecificField": "not persisted",
        },
    }

    result, version = gateway.generate(
        0,
        model=DEFAULT_GEMMA_MODEL,
        purpose="news-impact",
        payload={"contents": []},
        input_tokens=321,
        decode=lambda envelope: int(envelope["value"]),
        retryable_http_codes=frozenset(),
    )
    row = ledger.connection.execute(
        """SELECT attempted_at,provider_outcome,provider_prompt_token_count,
                  provider_candidates_token_count,provider_total_token_count
           FROM news_ai_account_request_usage_v1"""
    ).fetchone()

    assert (result, version) == (7, "gemma-version")
    assert row["attempted_at"] is not None
    assert dict(row) == {
        "attempted_at": row["attempted_at"],
        "provider_outcome": "PROVIDER_SUCCEEDED",
        "provider_prompt_token_count": 123,
        "provider_candidates_token_count": 45,
        "provider_total_token_count": 168,
    }
    ledger.close()
