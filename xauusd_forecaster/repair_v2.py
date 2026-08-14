"""Deterministic append-only repair from retained quote and news evidence."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .evidence_v2 import (
    ELIGIBILITY_VERSION,
    EVIDENCE_CONTRACT_VERSION,
    FEATURE_VERSION,
    LABEL_VERSION,
    NEWS_FEATURE_VERSION,
    install_v2_schema,
)
from .executable_label import build_executable_label_v2
from .forward_ledger import ForwardLedger, canonical_hash
from .m1 import aggregate_xautk002_batch
from .market import MarketObservation, build_forward_snapshot
from .news_features_v2 import aggregate_news_features_v2, frozen_rule_rows
from .u5_state import U5State, U5_VERSION


UTC = timezone.utc
LANE_RULE_VERSION = "phase2f-lanes-v2"
TRAINING_ELIGIBILITY_VERSION = "repaired-training-v2"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _uuid(namespace: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{namespace}:{value}"))


def immutable_table_hash(connection: sqlite3.Connection, tables: tuple[str, ...]) -> str:
    evidence = []
    for table in tables:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        rows = [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        evidence.append((table, columns, rows))
    return canonical_hash(evidence)


def _read_quotes(quote_root: Path, cutoff: datetime) -> list[MarketObservation]:
    rows = []
    sources = sorted([*quote_root.glob("*.jsonl"), *quote_root.glob("*.jsonl.gz")])
    for source in sources:
        opener = gzip.open if source.suffix == ".gz" else open
        with opener(source, "rt", encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                event = datetime.fromisoformat(item["event_time"].replace("Z", "+00:00"))
                received = datetime.fromisoformat(item["received_time"].replace("Z", "+00:00"))
                if received <= cutoff:
                    rows.append(MarketObservation(event, received, float(item["bid"]), float(item["ask"])))
    return sorted(rows, key=lambda row: (row.received_time, row.event_time))


def _warmup_state(receipt_path: Path) -> U5State:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    manifest_path = Path(receipt["source_manifest"])
    root = manifest_path.parent
    state = U5State()
    for relative in receipt["selected_files"]:
        result = aggregate_xautk002_batch(root / relative)
        for row in result.frame.itertuples(index=False):
            state.update(row.minute.to_pydatetime(), row.bid_close, row.ask_close)
    if state.status != "READY":
        raise RuntimeError("canonical U5 warm-up did not reproduce READY state")
    return state


def _minute_closes(quotes: list[MarketObservation]) -> dict[datetime, MarketObservation]:
    grouped: dict[datetime, list[MarketObservation]] = defaultdict(list)
    for quote in quotes:
        minute = quote.event_time.astimezone(UTC).replace(second=0, microsecond=0)
        # A completed close must have been received by the end of that minute.
        if quote.received_time <= minute + timedelta(minutes=1):
            grouped[minute].append(quote)
    return {
        minute: max(rows, key=lambda row: (row.event_time, row.received_time))
        for minute, rows in grouped.items()
    }


def _insert_lane(connection, evidence_type: str, evidence_id: str, lane: str,
                 assigned_at: datetime, source_hash: str, repair_batch_id: str | None) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO evidence_lane_assignments VALUES (?,?,?,?,?,?,?,?)",
        (_uuid("lane", f"{evidence_type}:{evidence_id}:{LANE_RULE_VERSION}"), evidence_type,
         evidence_id, lane, _iso(assigned_at), LANE_RULE_VERSION, source_hash, repair_batch_id),
    )


def run_repair(
    ledger: ForwardLedger,
    *,
    local_root: Path,
    source_cutoff: datetime,
    evaluation_epoch_v2: datetime,
    code_commit: str,
) -> dict:
    """Append one deterministic repaired-seed batch without touching V1 rows."""
    connection = ledger.connection
    legacy_tables = (
        "runtime_metadata", "market_snapshots", "news_revisions", "news_annotations",
        "decision_events", "predictions", "outcomes", "prediction_scores",
        "training_eligibility", "model_updates",
    )
    before_hash = immutable_table_hash(connection, legacy_tables)
    install_v2_schema(connection)
    source_rows = connection.execute(
        """SELECT d.*, s.snapshot_hash FROM decision_events d
        JOIN market_snapshots s USING(snapshot_id)
        WHERE d.decision_time <= ? ORDER BY d.decision_time""",
        (_iso(source_cutoff),),
    ).fetchall()
    source_evidence_hash = canonical_hash([tuple(row) for row in source_rows])
    batch_id = _uuid("repair-batch", f"{_iso(source_cutoff)}:{source_evidence_hash}")
    existing = connection.execute(
        "SELECT * FROM repair_batches WHERE repair_batch_id=?", (batch_id,)
    ).fetchone()
    if existing:
        return dict(existing) | {"legacy_hash_before": before_hash, "legacy_hash_after": before_hash}

    started_at = datetime.now(UTC)
    frozen_rules = frozen_rule_rows()
    rules_hash = canonical_hash(frozen_rules)
    with connection:
        connection.execute(
            "INSERT OR IGNORE INTO source_eligibility_versions VALUES (?,?,?,?)",
            (ELIGIBILITY_VERSION, _iso(started_at), rules_hash,
             "Permission-neutral intake registry; body and point-in-time visibility remain mandatory."),
        )
        for source, tier, requires_body, minimum, rationale in frozen_rules:
            connection.execute(
                "INSERT OR IGNORE INTO source_eligibility_rules VALUES (?,?,?,?,?,?)",
                (ELIGIBILITY_VERSION, source, tier, requires_body, minimum, rationale),
            )

    quotes = _read_quotes(local_root / "quotes", source_cutoff)
    closes = _minute_closes(quotes)
    u5_state = _warmup_state(local_root / "u5-warmup-receipt.json")
    live_minutes = sorted(closes)
    next_minute_index = 0
    repaired = 0
    unrepaired = 0
    reasons = Counter()
    output_receipts = []

    for decision in source_rows:
        decision_time = datetime.fromisoformat(decision["decision_time"])
        while next_minute_index < len(live_minutes):
            minute = live_minutes[next_minute_index]
            if minute + timedelta(minutes=1) > decision_time:
                break
            quote = closes[minute]
            u5_state.update(minute, quote.bid, quote.ask)
            next_minute_index += 1

        visible = [
            quote for quote in quotes
            if decision_time - timedelta(minutes=61) <= quote.event_time <= decision_time
            and quote.received_time <= decision_time
        ]
        snapshot = build_forward_snapshot(
            visible, decision_time, decision_time, "repair-raw-quote-ledger-v2",
            u5=u5_state.last_u5, u5_status=u5_state.status,
        )
        snapshot["features"]["decision_bid"] = snapshot["bid"]
        snapshot["features"]["decision_ask"] = snapshot["ask"]
        market_source_hash = canonical_hash([
            (q.event_time.isoformat(), q.received_time.isoformat(), q.bid, q.ask)
            for q in visible
        ])
        market_payload = {
            "decision_id": decision["decision_id"], "decision_time": _iso(decision_time),
            "source_snapshot_hash": decision["snapshot_hash"], "feature_version": FEATURE_VERSION,
            "u5_version": U5_VERSION, "u5": snapshot["u5"], "features": snapshot["features"],
            "data_health": snapshot["data_health"], "reason_codes": snapshot["reason_codes"],
            "source_evidence_hash": market_source_hash,
        }
        market_hash = canonical_hash(market_payload)
        market_id = _uuid("derived-market", f"{decision['decision_id']}:{FEATURE_VERSION}")

        news = aggregate_news_features_v2(ledger, decision_time)
        news_payload = {
            "decision_id": decision["decision_id"], "decision_time": _iso(decision_time),
            "feature_version": NEWS_FEATURE_VERSION, "eligibility_version": ELIGIBILITY_VERSION,
            **news,
        }
        news_hash = canonical_hash(news_payload)
        news_id = _uuid("derived-news", f"{decision['decision_id']}:{NEWS_FEATURE_VERSION}:{ELIGIBILITY_VERSION}")

        label = build_executable_label_v2(decision_time=decision_time, quotes=quotes)
        label_values = label.payload()
        hashable_label = {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in label_values.items()
        }
        label_payload = {"decision_id": decision["decision_id"], "decision_time": _iso(decision_time),
                         "label_version": LABEL_VERSION, **hashable_label}
        outcome_source_hash = canonical_hash([
            (q.event_time.isoformat(), q.received_time.isoformat(), q.bid, q.ask)
            for q in quotes
            if decision_time < q.received_time <= min(source_cutoff, decision_time + timedelta(minutes=32))
        ])
        label_payload["source_evidence_hash"] = outcome_source_hash
        outcome_hash = canonical_hash(label_payload)
        outcome_id = _uuid("derived-outcome", f"{decision['decision_id']}:{LABEL_VERSION}")
        recomputed = started_at

        with connection:
            _insert_lane(connection, "DECISION", decision["decision_id"], "LEGACY_ENGINEERING",
                         recomputed, decision["snapshot_hash"], batch_id)
            connection.execute(
                """INSERT OR IGNORE INTO derived_market_snapshots VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (market_id, decision["decision_id"], _iso(decision_time), decision["snapshot_hash"],
                 batch_id, "REPAIRED_SEED", _iso(recomputed), FEATURE_VERSION, U5_VERSION,
                 snapshot["u5"], json.dumps(snapshot["features"], sort_keys=True, separators=(",", ":")),
                 snapshot["data_health"], json.dumps(snapshot["reason_codes"], separators=(",", ":")),
                 market_source_hash, market_hash),
            )
            connection.execute(
                """INSERT OR IGNORE INTO derived_news_feature_snapshots VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (news_id, decision["decision_id"], _iso(decision_time), batch_id, "REPAIRED_SEED",
                 _iso(recomputed), NEWS_FEATURE_VERSION, ELIGIBILITY_VERSION,
                 json.dumps(news["features"], sort_keys=True, separators=(",", ":")),
                 news["model_visible_items"], news["news_exposed"], news["distinct_news_clusters"],
                 news["distinct_event_types"], news["source_evidence_hash"], news_hash),
            )
            values = label_values
            connection.execute(
                f"INSERT OR IGNORE INTO derived_outcomes VALUES ({','.join('?' for _ in range(34))})",
                (outcome_id, decision["decision_id"], _iso(decision_time), batch_id, "REPAIRED_SEED",
                 _iso(recomputed), LABEL_VERSION, values["outcome_status"],
                 json.dumps(values["reason_codes"], separators=(",", ":")),
                 _iso(values["entry_event_time"]) if values["entry_event_time"] else None,
                 _iso(values["entry_received_time"]) if values["entry_received_time"] else None,
                 values["entry_receipt_delay_seconds"],
                 _iso(values["exit_event_time"]) if values["exit_event_time"] else None,
                 _iso(values["exit_received_time"]) if values["exit_received_time"] else None,
                 values["exit_receipt_delay_seconds"], values["maximum_event_gap"],
                 values["maximum_receipt_gap"], values["quote_coverage"], values["ambiguity_state"],
                 values["gross_midpoint_direction_move"], values["long_quote_return"],
                 values["short_quote_return"], values["spread_quote_cost"], values["long_mfe"],
                 values["long_mae"], values["short_mfe"], values["short_mae"],
                 values["maximum_spread"], values["break_even_commission_long"],
                 values["break_even_commission_short"], values["commission_status"],
                 values["slippage_status"], outcome_source_hash, outcome_hash),
            )
            _insert_lane(connection, "DERIVED_MARKET", market_id, "REPAIRED_SEED", recomputed, market_hash, batch_id)
            _insert_lane(connection, "DERIVED_NEWS", news_id, "REPAIRED_SEED", recomputed, news_hash, batch_id)
            _insert_lane(connection, "DERIVED_OUTCOME", outcome_id, "REPAIRED_SEED", recomputed, outcome_hash, batch_id)
            feature_values = [snapshot["features"].get(name) for name in (
                "return_1m", "return_5m", "return_15m", "return_30m", "return_60m",
                "tick_speed_5m_per_second", "quote_imbalance_60m", "realized_volatility_60m",
            )]
            if (values["outcome_status"] == "VALID" and values["ambiguity_state"] == "NONE"
                    and snapshot["u5"] is not None
                    and snapshot["data_health"] == "OK" and all(v is not None for v in feature_values)):
                connection.execute(
                    "INSERT OR IGNORE INTO training_eligibility_v2 VALUES (?,?,?,?,?,?,?,?)",
                    (_uuid("training-v2", decision["decision_id"]), decision["decision_id"],
                     "REPAIRED_SEED", _iso(recomputed), TRAINING_ELIGIBILITY_VERSION,
                     market_hash, outcome_hash, news_hash),
                )
                repaired += 1
            else:
                unrepaired += 1
                row_reasons = list(values["reason_codes"])
                if snapshot["u5"] is None: row_reasons.append("U5_UNAVAILABLE")
                if values["ambiguity_state"] != "NONE": row_reasons.append(values["ambiguity_state"])
                if snapshot["data_health"] != "OK": row_reasons.append(f"MARKET_{snapshot['data_health']}")
                if any(v is None for v in feature_values): row_reasons.append("INCOMPLETE_MARKET_FEATURES")
                reasons.update(set(row_reasons or ["NOT_TRAINING_COMPLETE"]))
        output_receipts.append((market_hash, news_hash, outcome_hash))

    output_hash = canonical_hash(output_receipts)
    completed_at = datetime.now(UTC)
    collection_epoch = ledger.forward_epoch
    epoch_id = _uuid("evaluation-epoch", EVIDENCE_CONTRACT_VERSION)
    with connection:
        connection.execute(
            "INSERT OR IGNORE INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
            (epoch_id, _iso(collection_epoch), _iso(evaluation_epoch_v2), _iso(source_cutoff),
             _iso(completed_at), code_commit, EVIDENCE_CONTRACT_VERSION),
        )
        connection.execute(
            "INSERT INTO repair_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, _iso(source_cutoff),
             json.dumps(["forward-market-v1", "forward-executable-30m-v1", "forward-news-v1"]),
             json.dumps([FEATURE_VERSION, NEWS_FEATURE_VERSION, LABEL_VERSION, U5_VERSION]),
             code_commit, _iso(started_at), _iso(completed_at), source_evidence_hash,
             output_hash, repaired, unrepaired,
             json.dumps(dict(sorted(reasons.items())), sort_keys=True, separators=(",", ":")),
             "COMPLETED" if not unrepaired else "COMPLETED_WITH_GAPS"),
        )
    after_hash = immutable_table_hash(connection, legacy_tables)
    if after_hash != before_hash:
        raise AssertionError("legacy evidence changed during append-only repair")
    return {
        "repair_batch_id": batch_id, "source_cutoff": _iso(source_cutoff),
        "collection_epoch": _iso(collection_epoch), "evaluation_epoch_v2": _iso(evaluation_epoch_v2),
        "source_evidence_hash": source_evidence_hash, "output_evidence_hash": output_hash,
        "repaired_row_count": repaired, "unrepaired_row_count": unrepaired,
        "unrepaired_reason_distribution": dict(sorted(reasons.items())),
        "legacy_hash_before": before_hash, "legacy_hash_after": after_hash,
        "u5_version": U5_VERSION, "feature_version": FEATURE_VERSION,
        "news_feature_version": NEWS_FEATURE_VERSION, "label_version": LABEL_VERSION,
        "eligibility_version": ELIGIBILITY_VERSION,
    }
