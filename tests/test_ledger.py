import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster import (
    DataHealth,
    Forecast,
    OutcomeLabel,
    PredictionLedger,
    ShadowDecisionGate,
)


UTC = timezone.utc


def make_decision(at: datetime):
    forecast = Forecast(
        decision_id="XAU-001",
        decision_time=at,
        model_version="champion-001",
        feature_snapshot_hash="sha256:test",
        ev_long_u5=0.14,
        ev_short_u5=-0.08,
        lcb_long_u5=0.03,
        lcb_short_u5=-0.17,
        uncertainty_long_u5=0.05,
        uncertainty_short_u5=0.05,
        estimated_cost_long_u5=0.01,
        estimated_cost_short_u5=0.01,
        data_health=DataHealth.OK,
        reason_codes=("usd_confirmation",),
    )
    return ShadowDecisionGate().decide(forecast)


def test_ledger_appends_decision_and_separate_outcome(tmp_path) -> None:
    at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = PredictionLedger(tmp_path / "ledger.sqlite3")
    try:
        ledger.append_decision(make_decision(at))
        ledger.append_outcome(
            OutcomeLabel(
                decision_id="XAU-001",
                label_time=at + timedelta(minutes=30),
                label_contract_version="fixed-30m-v1",
                long_return_u5=0.2,
                short_return_u5=-0.22,
                mfe_long_u5=0.3,
                mae_long_u5=-0.1,
                mfe_short_u5=0.1,
                mae_short_u5=-0.3,
                maximum_spread=0.35,
                quote_coverage=1.0,
                ambiguity_state="NONE",
            )
        )
        decision_count = ledger.connection.execute(
            "SELECT COUNT(*) FROM decisions"
        ).fetchone()[0]
        label_count = ledger.connection.execute(
            "SELECT COUNT(*) FROM outcome_labels"
        ).fetchone()[0]
        assert decision_count == 1
        assert label_count == 1
    finally:
        ledger.close()


def test_ledger_rejects_mutation_and_duplicate_records(tmp_path) -> None:
    at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = PredictionLedger(tmp_path / "ledger.sqlite3")
    try:
        ledger.append_decision(make_decision(at))
        with pytest.raises(sqlite3.IntegrityError):
            ledger.append_decision(make_decision(at))
        with pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ):
            ledger.connection.execute(
                "UPDATE decisions SET model_version = 'changed' "
                "WHERE decision_id = 'XAU-001'"
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ):
            ledger.connection.execute(
                "DELETE FROM decisions WHERE decision_id = 'XAU-001'"
            )
    finally:
        ledger.close()
