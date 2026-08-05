"""Prequential Phase 2F orchestration; never submits an order."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from .forward_ledger import ForwardLedger
from .inference import build_shadow_predictions
from .market import MarketProvider, build_forward_snapshot
from .news import collect_official_news
from .u5_state import U5State


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
            entries = [
                item
                for item in visible
                if decision_time < item.event_time <= decision_time + timedelta(seconds=20)
            ]
            entry = entries[0] if entries else None
            exit_quote = None
            path = []
            if entry is not None:
                target = entry.event_time + timedelta(minutes=30)
                exits = [item for item in visible if item.event_time >= target]
                exit_quote = exits[0] if exits else None
                if exit_quote is not None:
                    path = [
                        item
                        for item in visible
                        if entry.event_time <= item.event_time <= exit_quote.event_time
                    ]
            grace_end = decision_time + timedelta(minutes=31, seconds=20)
            if exit_quote is None and now < grace_end:
                continue
            reasons: list[str] = []
            if provider_error is not None:
                reasons.append("MARKET_PROVIDER_ERROR")
            if entry is None:
                reasons.append("NO_ENTRY_WITHIN_20S")
            elif exit_quote is None:
                reasons.append("NO_EXIT_QUOTE")
            elif (exit_quote.event_time - target).total_seconds() > 60:
                reasons.append("EXIT_DELAY_GT_60S")
            if len(path) > 1:
                maximum_gap = max(
                    (right.event_time - left.event_time).total_seconds()
                    for left, right in zip(path, path[1:])
                )
                if maximum_gap > 60:
                    reasons.append("PATH_GAP_GT_60S")
            if reasons:
                outcome = {
                    "decision_id": row["decision_id"],
                    "appended_at": now,
                    "label_version": "forward-executable-30m-v1",
                    "outcome_status": "INVALID",
                    "reason_codes": reasons,
                    "source_hash": "NO_VALID_EXECUTABLE_PATH",
                }
            else:
                long_path = [math.log(item.bid / entry.ask) for item in path]
                short_path = [math.log(entry.bid / item.ask) for item in path]
                long_return = long_path[-1]
                short_return = short_path[-1]
                outcome = {
                    "decision_id": row["decision_id"],
                    "entry_time": entry.event_time,
                    "exit_time": exit_quote.event_time,
                    "horizon": timedelta(minutes=30),
                    "appended_at": now,
                    "label_version": "forward-executable-30m-v1",
                    "outcome_status": "VALID",
                    "reason_codes": (),
                    "long_return": long_return,
                    "short_return": short_return,
                    "direction_move": (long_return - short_return) / 2.0,
                    "spread_quote_cost": -(long_return + short_return) / 2.0,
                    "long_mfe": max(long_path),
                    "long_mae": min(long_path),
                    "short_mfe": max(short_path),
                    "short_mae": min(short_path),
                    "maximum_spread": max(item.ask - item.bid for item in path),
                    "quote_coverage": 1.0,
                    "source_hash": str(uuid.uuid5(uuid.NAMESPACE_URL, repr(path))),
                }
            self.ledger.append_outcome(outcome)
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
            if (
                outcome["outcome_status"] == "VALID"
                and row["u5"] is not None
                and row["data_health"] == "OK"
            ):
                self.ledger.mark_training_eligible(row["decision_id"], now)
            completed.append(row["decision_id"])
        return completed
