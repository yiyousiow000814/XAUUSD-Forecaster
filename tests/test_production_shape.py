from __future__ import annotations

from datetime import UTC, datetime, timedelta

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.inference_v2 import MODEL_IDENTITIES
from xauusd_forecaster.news_scheduler import reserve_account_request
from xauusd_forecaster.production_shape import production_shape_violations


def _status(now: datetime) -> dict:
    return {
        "system": {
            "market_session": "OPEN",
            "market_session_observed_at": now.isoformat(),
        },
        "gemini_quota": {
            "accounting_source": "SCHEDULER_DB", "total_sent": 1,
        },
        "gemini_31_quota": {
            "accounting_source": "SCHEDULER_DB", "total_sent": 0,
        },
        "gemma_quota": {
            "accounting_source": "SCHEDULER_DB", "total_sent": 2,
        },
        "news_source_health": [{
            "source": "gdelt_gold_geopolitics", "health": "HEALTHY",
            "latest_status": "OK", "recovery_mode": None,
            "next_retry_time": None,
        }],
    }


def _seed_active_generation(ledger: ForwardLedger, now: datetime) -> str:
    generation_id = "generation-live"
    ledger.connection.execute(
        "INSERT INTO news_model_generations_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (generation_id, "SHADOW", now.isoformat(), now.isoformat(),
         "policy", "features", "eligibility", "events", "market", "official",
         "broad", "weights", 5, "READY"),
    )
    for identity in sorted(MODEL_IDENTITIES):
        version = f"model-{identity.lower()}"
        ledger.connection.execute(
            "INSERT INTO model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (version, identity, "SHADOW", now.isoformat(), now.isoformat(),
             0, 0, 0, 0, 0, 0, f"hash-{identity}", "features", "eligibility",
             "artifact", f"artifact-{identity}", "CHALLENGER"),
        )
        table = (
            "news_model_generation_aux_members_v1"
            if identity == "NEWS_ONLY" else "news_model_generation_members_v1"
        )
        ledger.connection.execute(
            f"INSERT INTO {table} VALUES (?,?,?)",
            (generation_id, identity, version),
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generation_activations_v1 VALUES (?,?,?,?,?)",
        ("activation-live", generation_id, None,
         (now + timedelta(seconds=1)).isoformat(), "TEST"),
    )
    ledger.connection.commit()
    return generation_id


def test_production_shape_detects_cross_component_regressions(tmp_path) -> None:
    now = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    _seed_active_generation(ledger, now)
    reserve_account_request(
        ledger.connection, account_id="account", model_family="gemini-3.5-flash-lite",
        daily_limit=500, requests_per_minute=12, now=now,
    )
    for family in ("gemma-impact", "gemma-title"):
        reserve_account_request(
            ledger.connection, account_id="account", model_family=family,
            daily_limit=15_000, requests_per_minute=12, now=now,
        )
    ledger.append_source_poll({
        "poll_id": "gdelt-ok", "source": "gdelt_gold_geopolitics",
        "fetched_time": now, "status": "OK",
    })
    broken = _status(now)
    broken["gemini_quota"]["total_sent"] = 0
    broken["news_source_health"][0].update({
        "health": "DEGRADED", "latest_status": "RATE_LIMITED",
        "recovery_mode": "FALLBACK_ACTIVE",
    })

    violations = production_shape_violations(
        ledger.connection, broken,
        sync_status={"degraded_resources": [{
            "resource": "market_history", "error": "HTTP Error 413: Payload Too Large",
        }]},
        now=now + timedelta(minutes=5),
    )

    assert any("no subsequent live decision" in item for item in violations)
    assert any("gemini_quota" in item for item in violations)
    assert any("GDELT" in item for item in violations)
    assert any("payload limit" in item for item in violations)


def test_production_shape_accepts_complete_live_shape(tmp_path) -> None:
    now = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    _seed_active_generation(ledger, now)
    decision_time = now + timedelta(minutes=5)
    ledger.append_snapshot({
        "snapshot_id": "snapshot-live", "decision_time": decision_time,
        "collected_at": decision_time, "data_role": "FORWARD", "source": "TEST",
        "source_event_time": decision_time, "source_received_time": decision_time,
        "bid": 4300.0, "ask": 4300.1, "spread": 0.1, "features": {},
        "feature_version": "features", "u5": 1.0, "u5_status": "READY",
        "data_health": "HEALTHY", "active_signal": False, "reason_codes": [],
    })
    ledger.append_decision({
        "decision_id": "decision-live", "decision_time": decision_time,
        "snapshot_id": "snapshot-live", "data_health": "HEALTHY",
        "reason_codes": [], "predictions": [], "created_at": decision_time,
    })
    for identity in sorted(MODEL_IDENTITIES):
        ledger.connection.execute(
            """INSERT INTO predictions_v2 VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("decision-live", f"model-{identity.lower()}", identity,
             decision_time.isoformat(), decision_time.isoformat(), "LIVE_OOS",
             "snapshot-hash", 0.0, 0.0, 0.0, 0.0, None, None,
                 "UTC_DAY_BLOCK_OOS_ABS_RESIDUAL_Q95", "calibration", 0, 0, 0,
                 None, "UNCALIBRATED", "WAIT", "WAIT", "PROVISIONAL"),
            )
    ledger.connection.commit()
    reserve_account_request(
        ledger.connection, account_id="account", model_family="gemini-3.5-flash-lite",
        daily_limit=500, requests_per_minute=12, now=now,
    )
    for family in ("gemma-impact", "gemma-title"):
        reserve_account_request(
            ledger.connection, account_id="account", model_family=family,
            daily_limit=15_000, requests_per_minute=12, now=now,
        )
    ledger.append_source_poll({
        "poll_id": "gdelt-ok", "source": "gdelt_gold_geopolitics",
        "fetched_time": now, "status": "OK",
    })

    assert production_shape_violations(
        ledger.connection, _status(now), sync_status={"degraded_resources": []},
        now=now + timedelta(minutes=10),
    ) == []
