from __future__ import annotations

import pytest

from xauusd_forecaster.ai_provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.model_gateway import ModelRequestUsage
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
