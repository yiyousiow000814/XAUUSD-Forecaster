"""Daily Brief cycle orchestration over scheduler-owned routine capacity."""
from __future__ import annotations

from datetime import UTC, datetime

from xauusd_forecaster.daily_brief import brief_dates_to_process, update_daily_brief
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news_scheduler import (
    ApiCredential, LIVE_LANE, ROUTINE_POOL, configured_api_credentials,
    credentials_for_background_task,
)
from xauusd_forecaster.scheduler_model_gateway import SchedulerModelAccountant

def run_daily_brief_batch(
    ledger: ForwardLedger, *, now: datetime | None = None,
    credentials: tuple[ApiCredential, ...] | None = None,
) -> list[dict[str, object]]:
    """Advance the bounded brief backlog using scheduler-owned routine capacity."""
    instant = now or datetime.now(UTC)
    configured = credentials if credentials is not None else configured_api_credentials()
    results = []
    for day in brief_dates_to_process(ledger.connection, now=instant):
        # Account usage changes after every model request. Re-rank for each
        # date so one exhausted account cannot starve the remaining backlog.
        ranking_instant = instant if now is not None else datetime.now(UTC)
        ordered = credentials_for_background_task(
            ledger.connection, configured, task_type="DAILY_BRIEF",
            now=ranking_instant,
        )
        credential = ordered[0] if ordered else None
        result = update_daily_brief(
            ledger, brief_date=day, now=instant,
            api_key=credential.api_key if credential else None,
            request_accountant=(SchedulerModelAccountant(
                ledger.connection, credential, urgent=False,
                work_lane=LIVE_LANE,
            ) if credential else None),
        )
        results.append({
            **result,
            "pool": credential.pool if credential else ROUTINE_POOL,
            "account_id": credential.account_id if credential else None,
        })
    return results

