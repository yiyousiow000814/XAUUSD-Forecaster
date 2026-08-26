"""Prequential Phase 2F orchestration; never submits an order."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.evidence.executable_label import build_executable_label_v2
from xauusd_forecaster.evidence.ledger import ForwardLedger, canonical_hash
from xauusd_forecaster.inference import build_shadow_predictions
from xauusd_forecaster.market import MarketProvider, build_forward_snapshot
from xauusd_forecaster.news.collection.intake import collect_official_news
from xauusd_forecaster.u5_state import U5State


UTC = timezone.utc


def floor_five_minutes(value: datetime) -> datetime:
    utc = value.astimezone(UTC).replace(second=0, microsecond=0)
    return utc.replace(minute=utc.minute - utc.minute % 5)


class ForwardEngine:
    def __init__(
        self,
        ledger: ForwardLedger,
        market_provider: MarketProvider,
        u5_state: U5State | None = None,
    ) -> None:
        self.ledger = ledger
        self.market_provider = market_provider
        self.u5_state = u5_state or U5State()

    def collect_news(self, now: datetime) -> list[dict[str, object]]:
        return collect_official_news(self.ledger, now)

    def append_clock_event(
        self,
        decision_time: datetime,
        collected_at: datetime,
        news_status: list[dict[str, object]] | None = None,
    ) -> tuple[str, str]:
        if decision_time != floor_five_minutes(decision_time):
            raise ValueError("decision must be on the UTC five-minute grid")
        if decision_time < self.ledger.forward_epoch:
            raise ValueError("decision predates FORWARD_EPOCH")
        provider_error = None
        try:
            observations = self.market_provider.observations(decision_time)
        except Exception as error:
            observations = []
            provider_error = f"{type(error).__name__}:{str(error)[:200]}"
        completed: dict[datetime, object] = {}
        for observation in observations:
            minute = observation.event_time.replace(second=0, microsecond=0)
            if minute + timedelta(minutes=1) <= decision_time:
                completed[minute] = observation
        for minute in sorted(completed):
            observation = completed[minute]
            self.u5_state.update(minute, observation.bid, observation.ask)
        snapshot = build_forward_snapshot(
            observations,
            decision_time,
            collected_at,
            self.market_provider.name,
            u5=self.u5_state.last_u5,
            u5_status=self.u5_state.status,
        )
        if provider_error is not None:
            snapshot["reason_codes"] = tuple(
                dict.fromkeys((*snapshot["reason_codes"], "MARKET_PROVIDER_ERROR"))
            )
            snapshot["features"]["market_provider_error"] = provider_error
        self.ledger.append_snapshot(snapshot)
        decision_id = f"XAU-{decision_time.strftime('%Y%m%dT%H%M%SZ')}"
        predictions = build_shadow_predictions(self.ledger, snapshot, decision_time)
        self.ledger.append_decision(
            {
                "decision_id": decision_id,
                "decision_time": decision_time,
                "snapshot_id": snapshot["snapshot_id"],
                "created_at": collected_at,
                "data_health": snapshot["data_health"],
                "reason_codes": snapshot["reason_codes"],
                "predictions": predictions,
            }
        )
        from xauusd_forecaster.decision.live import append_live_decision_v2
        from xauusd_forecaster.news.scheduler.health import news_semantic_pipeline_health_at

        coverage_health = news_semantic_pipeline_health_at(
            self.ledger, observed_at=decision_time,
        )

        append_live_decision_v2(
            self.ledger, decision_id=decision_id, decision_time=decision_time,
            created_at=collected_at, snapshot=snapshot,
            news_pipeline_health=coverage_health,
        )
        run_id = str(uuid.uuid4())
        with self.ledger.connection:
            self.ledger.connection.execute(
                "INSERT INTO collector_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    collected_at.isoformat(),
                    datetime.now(UTC).isoformat(),
                    decision_time.isoformat(),
                    snapshot["data_health"],
                    json.dumps(news_status or [], sort_keys=True, separators=(",", ":")),
                    snapshot["snapshot_id"],
                    decision_id,
                ),
            )
        return snapshot["snapshot_id"], decision_id

    def settle_due_outcomes(self, now: datetime) -> list[str]:
        due = self.ledger.connection.execute(
            """SELECT d.decision_id, d.decision_time, s.u5, s.data_health
            FROM decision_events d JOIN market_snapshots s USING(snapshot_id)
            LEFT JOIN outcomes o USING(decision_id)
            WHERE o.decision_id IS NULL AND d.decision_time <= ?
            ORDER BY d.decision_time""",
            ((now - timedelta(minutes=30)).isoformat(),),
        ).fetchall()
        completed: list[str] = []
        if not due:
            return completed
        provider_error = None
        try:
            observations = self.market_provider.observations(now)
        except Exception as error:
            observations = []
            provider_error = f"{type(error).__name__}:{str(error)[:200]}"
        for row in due:
            decision_time = datetime.fromisoformat(row["decision_time"])
            visible = [item for item in observations if item.received_time <= now]
            label = build_executable_label_v2(decision_time=decision_time, quotes=visible)
            grace_end = decision_time + timedelta(minutes=31, seconds=20)
            if "NO_EXIT_RECEIVED_AFTER_HORIZON" in label.reason_codes and now < grace_end:
                continue
            reasons = list(label.reason_codes)
            if provider_error is not None:
                reasons.append("MARKET_PROVIDER_ERROR")
            source_hash = canonical_hash([
                (item.event_time.isoformat(), item.received_time.isoformat(), item.bid, item.ask)
                for item in visible
                if decision_time < item.received_time <= min(now, grace_end)
            ])
            if label.outcome_status != "VALID" or provider_error is not None:
                outcome = {
                    "decision_id": row["decision_id"],
                    "appended_at": now,
                    "label_version": "received-time-executable-30m-v2",
                    "outcome_status": "INVALID",
                    "reason_codes": reasons,
                    "source_hash": source_hash,
                }
            else:
                outcome = {
                    "decision_id": row["decision_id"],
                    "entry_time": label.entry_received_time,
                    "exit_time": label.exit_received_time,
                    "horizon": timedelta(minutes=30),
                    "appended_at": now,
                    "label_version": "received-time-executable-30m-v2",
                    "outcome_status": "VALID",
                    "reason_codes": (),
                    "long_return": label.long_quote_return,
                    "short_return": label.short_quote_return,
                    "direction_move": label.gross_midpoint_direction_move,
                    "spread_quote_cost": label.spread_quote_cost,
                    "long_mfe": label.long_mfe,
                    "long_mae": label.long_mae,
                    "short_mfe": label.short_mfe,
                    "short_mae": label.short_mae,
                    "maximum_spread": label.maximum_spread,
                    "quote_coverage": label.quote_coverage,
                    "source_hash": source_hash,
                }
            self.ledger.append_outcome(outcome)
            from xauusd_forecaster.decision.live import append_live_outcome_v2

            append_live_outcome_v2(
                self.ledger, decision_id=row["decision_id"], decision_time=decision_time,
                appended_at=now, label=label, source_evidence_hash=source_hash,
            )
            predictions = self.ledger.connection.execute(
                "SELECT * FROM predictions WHERE decision_id=? ORDER BY model_version",
                (row["decision_id"],),
            ).fetchall()
            for prediction in predictions:
                score = {"outcome_status": outcome["outcome_status"]}
                if outcome["outcome_status"] == "VALID" and row["u5"]:
                    target_u5 = outcome["direction_move"] / row["u5"]
                    predicted = prediction["predicted_direction_u5"]
                    score["target_direction_u5"] = target_u5
                    score["squared_error"] = (
                        (predicted - target_u5) ** 2 if predicted is not None else None
                    )
                self.ledger.append_score(
                    {
                        "decision_id": row["decision_id"],
                        "model_version": prediction["model_version"],
                        "scored_at": now,
                        "score": score,
                    }
                )
            # Phase 2F v1 eligibility is frozen as LEGACY_ENGINEERING.  The
            # repaired pipeline appends its own evidence-lane assignments and
            # never extends the legacy training_eligibility table.
            completed.append(row["decision_id"])
        return completed
