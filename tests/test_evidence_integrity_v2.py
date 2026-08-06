from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from xauusd_forecaster.evidence_v2 import V2_SCHEMA, install_v2_schema
from xauusd_forecaster.executable_label import build_executable_label_v2
from xauusd_forecaster.forward_ledger import ForwardLedger, canonical_hash
from xauusd_forecaster.learning_curves import _bounded_curve, _stage, learning_curve_payload
from xauusd_forecaster.live_v2 import append_live_outcome_v2
from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.news_evidence import event_evidence_rows
from xauusd_forecaster.news_features_v2 import aggregate_news_features_v2
from xauusd_forecaster.repair_v2 import immutable_table_hash
from xauusd_forecaster import inference_v2, training_v2
from xauusd_forecaster.u5_state import U5State, U5_VERSION
from xauusd_forecaster.execution_learning import (
    EXECUTION_CHART_MAX_POINTS, LOT_FEATURES, EXIT_FEATURES,
    _bounded_execution_curve, append_due_exit_predictions,
    append_execution_examples, append_lot_predictions, execution_learning_status,
    score_execution_predictions, train_due_execution,
)
from xauusd_forecaster.training import MARKET_FEATURES


def test_install_repairs_invalid_execution_score_foreign_key() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(V2_SCHEMA)
    connection.executescript(
        """
        DROP TABLE execution_position_scores_v2;
        CREATE TABLE execution_position_scores_v2 (
            source_decision_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            model_identity TEXT NOT NULL,
            scored_at TEXT NOT NULL,
            direction TEXT NOT NULL,
            selected_action TEXT NOT NULL,
            exit_minutes INTEGER NOT NULL,
            selected_quote_return REAL NOT NULL,
            baseline_quote_return REAL NOT NULL,
            delta_quote_return REAL NOT NULL,
            score_hash TEXT NOT NULL,
            PRIMARY KEY(source_decision_id,model_version),
            FOREIGN KEY(source_decision_id,model_version)
              REFERENCES execution_predictions_v2(source_decision_id,model_version)
        );
        """
    )
    install_v2_schema(connection)
    foreign_keys = connection.execute(
        "PRAGMA foreign_key_list(execution_position_scores_v2)"
    ).fetchall()
    assert len(foreign_keys) == 1
    assert foreign_keys[0][2] == "execution_model_updates_v2"
    assert foreign_keys[0][3:5] == ("model_version", "model_version")


def test_execution_status_distinguishes_prediction_and_settlement_times(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    decision = datetime(2026, 8, 6, 10, 50, tzinfo=timezone.utc)
    settled = datetime(2026, 8, 6, 11, 28, 17, tzinfo=timezone.utc)
    ledger.connection.execute(
        "INSERT INTO execution_model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("lot-model", "LOT_RIDGE", "SHADOW", decision.isoformat(),
         decision.isoformat(), 50, 50, "dataset", "features", "labels", "{}",
         "artifact", "BROAD_FULL", "CHALLENGER"),
    )
    ledger.connection.execute(
        "INSERT INTO execution_predictions_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("XAU-20260806T105000Z", "lot-model", "LOT_RIDGE", "BROAD_FULL",
         "direction-model", "SHORT", 0, decision.isoformat(), decision.isoformat(),
         0.1, "2.0X", None, "SHADOW_ONLY", "feature-hash"),
    )
    ledger.connection.execute(
        "INSERT INTO execution_position_scores_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("XAU-20260806T105000Z", "lot-model", "LOT_RIDGE", settled.isoformat(),
         "SHORT", "2.0X", 30, 0.004, 0.002, 0.002, "score-hash"),
    )

    result = execution_learning_status(ledger)["models"][0]["evaluation"]["results"][0]

    assert result["decision_time"] == decision.isoformat()
    assert result["scored_at"] == settled.isoformat()
    assert result["time"] == settled.isoformat()
    assert result["model_version"] == "lot-model"
    ledger.close()


def test_execution_curve_is_bounded_and_preserves_endpoints_and_extrema() -> None:
    points = []
    selected = baseline = 0.0
    for index in range(10_000):
        selected += 0.001 if index % 9 else -0.004
        baseline += 0.0002 if index % 7 else -0.0005
        if index == 4_321:
            selected += 2.0
        if index == 4_322:
            selected -= 2.0
        points.append({
            "time": f"point-{index}",
            "selected_cumulative_return": selected,
            "baseline_cumulative_return": baseline,
        })

    bounded = _bounded_execution_curve(points)

    assert len(bounded) <= EXECUTION_CHART_MAX_POINTS
    assert bounded[0] == points[0]
    assert bounded[-1] == points[-1]
    assert points[4_321] in bounded


UTC = timezone.utc


def _quote(event: datetime, received: datetime, bid: float = 4000.0) -> MarketObservation:
    return MarketObservation(event, received, bid, bid + 0.2)


def test_received_time_after_expiry_invalidates_entry_even_when_event_time_is_early() -> None:
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    rows = [
        _quote(decision + timedelta(seconds=5), decision + timedelta(seconds=21)),
        _quote(decision + timedelta(minutes=31), decision + timedelta(minutes=31)),
    ]
    label = build_executable_label_v2(decision_time=decision, quotes=rows)
    assert label.outcome_status == "UNREPAIRABLE"
    assert label.reason_codes == ("NO_ENTRY_RECEIVED_WITHIN_EXPIRY",)


def test_executable_horizon_starts_from_entry_received_time() -> None:
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    entry_received = decision + timedelta(seconds=19)
    rows = [
        _quote(decision + timedelta(seconds=18), entry_received),
        _quote(decision + timedelta(minutes=30, seconds=18),
               decision + timedelta(minutes=30, seconds=18)),
        _quote(decision + timedelta(minutes=30, seconds=19),
               decision + timedelta(minutes=30, seconds=19)),
    ]
    label = build_executable_label_v2(decision_time=decision, quotes=rows)
    assert label.outcome_status == "VALID"
    assert label.exit_received_time == entry_received + timedelta(minutes=30)


def test_execution_ridges_follow_one_frozen_live_direction(tmp_path) -> None:
    start = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=start)
    quotes = [
        _quote(start + timedelta(seconds=1), start + timedelta(seconds=1), 4000.0),
        *[
            _quote(start + timedelta(minutes=minute, seconds=1),
                   start + timedelta(minutes=minute, seconds=1),
                   4000.0 + minute)
            for minute in range(1, 31)
        ],
    ]
    label = build_executable_label_v2(decision_time=start, quotes=quotes)
    assert [row["minutes"] for row in label.checkpoint_path] == [5, 10, 15, 20, 25]
    features = {name: 0.001 * (index + 1) for index, name in enumerate(MARKET_FEATURES)}
    for index in range(48):
        decision_id = f"execution-{index}"
        decision = start + timedelta(minutes=5 * index)
        market_hash = canonical_hash((decision_id, features))
        ledger.connection.execute(
            """INSERT INTO derived_market_snapshots VALUES
            (?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)""",
            (f"market-{index}", decision_id, decision.isoformat(), market_hash,
             "LIVE_OOS", decision.isoformat(), "repaired-market-v2",
             "finite-memory-u5-v5-contiguous-m1", 0.01,
             json.dumps(features), "OK", "[]", market_hash, market_hash),
        )
        _insert_prediction(
            ledger.connection, decision_id, decision,
            model_version="broad-full-frozen", model_identity="BROAD_FULL",
        )
        assert append_execution_examples(
            ledger, decision_id=decision_id,
            appended_at=decision + timedelta(minutes=31), label=label,
            source_hash=canonical_hash((decision_id, "quotes")),
        ) == 1
    statuses = train_due_execution(
        ledger, start + timedelta(days=1), tmp_path / "execution-models"
    )
    assert {row["model_identity"] for row in statuses if row["status"] == "TRAINED"} == {
        "LOT_RIDGE", "EXIT_RIDGE",
    }
    assert ledger.connection.execute(
        "SELECT count(*) FROM execution_model_updates_v2"
    ).fetchone()[0] == 2
    lot = ledger.connection.execute(
        "SELECT artifact_paths_json FROM execution_model_updates_v2 WHERE model_identity='LOT_RIDGE'"
    ).fetchone()
    exit_model = ledger.connection.execute(
        "SELECT model_version,artifact_paths_json FROM execution_model_updates_v2 WHERE model_identity='EXIT_RIDGE'"
    ).fetchone()
    lot_paths = json.loads(lot["artifact_paths_json"])
    assert set(lot_paths) == {"0.5X", "1.0X", "2.0X"}
    assert tuple(json.loads(Path(lot_paths["1.0X"]).read_text())["feature_names"]) == LOT_FEATURES
    exit_path = json.loads(exit_model["artifact_paths_json"])["CONTINUATION"]
    assert tuple(json.loads(Path(exit_path).read_text())["feature_names"]) == EXIT_FEATURES
    status = execution_learning_status(ledger)
    by_identity = {row["model_identity"]: row for row in status["models"]}
    assert by_identity["LOT_RIDGE"]["evaluation"]["unit"] == "QUOTE_RETURN"
    assert by_identity["EXIT_RIDGE"]["evaluation"]["unit"] == "QUOTE_RETURN"

    live_decision = start + timedelta(days=1, minutes=5)
    live_id = "execution-live-checkpoint"
    live_hash = canonical_hash((live_id, features))
    ledger.connection.execute(
        """INSERT INTO derived_market_snapshots VALUES
        (?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)""",
        ("market-live", live_id, live_decision.isoformat(), live_hash,
         "LIVE_OOS", live_decision.isoformat(), "repaired-market-v2",
         "finite-memory-u5-v5-contiguous-m1", 0.01,
         json.dumps(features), "OK", "[]", live_hash, live_hash),
    )
    _insert_prediction(
        ledger.connection, live_id, live_decision,
        model_version="broad-full-live", model_identity="BROAD_FULL",
    )
    assert append_lot_predictions(
        ledger, decision_id=live_id, decision_time=live_decision,
        created_at=live_decision, market_snapshot={
            "features_json": json.dumps(features), "data_health": "OK",
            "output_hash": live_hash,
        },
    ) == 1
    live_quotes = [
        _quote(live_decision + timedelta(seconds=1),
               live_decision + timedelta(seconds=1), 4000.0),
        _quote(live_decision + timedelta(minutes=5, seconds=1),
               live_decision + timedelta(minutes=5, seconds=1), 4001.0),
    ]
    observed_at = live_decision + timedelta(minutes=5, seconds=2)
    assert append_due_exit_predictions(
        ledger, checkpoint_time=observed_at, created_at=observed_at,
        quotes=live_quotes,
    ) == 1
    rows = ledger.connection.execute(
        """SELECT direction,checkpoint_minutes,prediction_time
        FROM execution_predictions_v2 WHERE model_identity='EXIT_RIDGE'
        ORDER BY direction"""
    ).fetchall()
    assert [(row["direction"], row["checkpoint_minutes"]) for row in rows] == [("LONG", 5)]
    assert all(row["prediction_time"] == live_quotes[-1].received_time.isoformat()
               for row in rows)
    exit_status = {
        row["model_identity"]: row for row in execution_learning_status(ledger)["models"]
    }["EXIT_RIDGE"]
    assert exit_status["action_counts"] == {
        ledger.connection.execute(
            "SELECT recommended_action FROM execution_predictions_v2 WHERE model_identity='EXIT_RIDGE'"
        ).fetchone()[0]: 1
    }

    # Missing later checkpoints are a data gap, not permission to invent a
    # completed HOLD_TO_30M position score.
    ledger.connection.execute(
        "INSERT INTO execution_predictions_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("execution-0", exit_model["model_version"], "EXIT_RIDGE", "BROAD_FULL",
         "broad-full-frozen", "LONG", 5, observed_at.isoformat(),
         observed_at.isoformat(), 0.1, "HOLD", 0.01, "SHADOW_ONLY",
         canonical_hash(("execution-0", "incomplete-exit-path"))),
    )
    assert score_execution_predictions(
        ledger, decision_id="execution-0", scored_at=observed_at + timedelta(minutes=30)
    ) == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM execution_position_scores_v2 WHERE model_identity='EXIT_RIDGE'"
    ).fetchone()[0] == 0


def test_stable_ctrader_server_clock_lead_within_freshness_is_valid() -> None:
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    rows = [
        _quote(decision + timedelta(seconds=11), decision + timedelta(seconds=5)),
        _quote(decision + timedelta(minutes=30, seconds=11),
               decision + timedelta(minutes=30, seconds=5)),
    ]
    label = build_executable_label_v2(decision_time=decision, quotes=rows)
    assert label.outcome_status == "VALID"


def test_server_clock_lead_beyond_quote_freshness_is_invalid() -> None:
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    rows = [
        _quote(decision + timedelta(seconds=26), decision + timedelta(seconds=5)),
        _quote(decision + timedelta(minutes=30, seconds=26),
               decision + timedelta(minutes=30, seconds=5)),
    ]
    label = build_executable_label_v2(decision_time=decision, quotes=rows)
    assert label.outcome_status == "UNREPAIRABLE"
    assert label.reason_codes == ("ENTRY_EVENT_CLOCK_AHEAD",)


def test_u5_requires_a_new_contiguous_31_minute_path_after_gap() -> None:
    state = U5State()
    start = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    for minute in range(31):
        state.update(start + timedelta(minutes=minute), 4000 + minute, 4000.2 + minute)
    assert len(state.excursions) == 1
    state.update(start + timedelta(minutes=33), 4033, 4033.2)
    assert len(state.midpoints) == 1
    assert len(state.excursions) == 1
    assert state.continuity_resets == 1
    assert U5_VERSION == "finite-memory-u5-v5-contiguous-m1"


def _append_news(ledger: ForwardLedger, *, source: str, item: str,
                 first_seen: datetime, parsed_at: datetime, impulse: float,
                 link: str | None = None,
                 entities: list[str] | None = None,
                 event_type: str = "monetary_policy") -> None:
    entities = entities or []
    body = ("publisher full body " * 30) + item
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": source, "source_item_id": item,
        "collector_first_seen_time": first_seen, "fetched_time": first_seen,
        "headline": item, "body": body, "content_hash": digest, "cluster_id": item,
        "link": link,
    })
    ledger.append_annotation({
        "annotation_id": item, "source": source, "source_item_id": item,
        "revision_number": 1, "raw_content_hash": digest, "event_type": event_type,
        "entities": entities, "hawkishness": impulse, "inflation_impulse": 0.0,
        "growth_impulse": 0.0, "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 1.0, "confidence": 1.0, "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": "news-json-v9-local-display-recovery",
        "parse_started_at": parsed_at, "parsed_at": parsed_at,
        "annotation": {
            "event_type": event_type, "entities": entities, "hawkishness": impulse,
            "inflation_impulse": 0.0, "growth_impulse": 0.0,
            "geopolitical_risk": 0.0, "usd_impulse": 0.0,
            "novelty": 1.0, "confidence": 1.0,
        },
    })


def test_news_freshness_ages_from_first_seen_not_parsed_at(tmp_path) -> None:
    first_seen = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = first_seen + timedelta(minutes=30)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first_seen)
    _append_news(ledger, source="federal_reserve_monetary", item="official",
                 first_seen=first_seen, parsed_at=decision, impulse=1.0)
    features = aggregate_news_features_v2(ledger, decision)
    expected_freshness = 2 ** (-30 / 360)
    assert features["features"]["news_event_count"] == pytest.approx(expected_freshness)
    ledger.close()


def test_news_older_than_72_hours_is_not_a_current_feature(tmp_path) -> None:
    decision = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    first_seen = decision - timedelta(hours=72, seconds=1)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first_seen)
    _append_news(
        ledger, source="federal_reserve_monetary", item="old-official",
        first_seen=first_seen, parsed_at=first_seen + timedelta(minutes=1),
        impulse=1.0,
    )
    features = aggregate_news_features_v2(ledger, decision)
    assert features["model_visible_items"] == 0
    assert features["news_exposed"] == 0
    assert features["features"]["news_event_count"] == 0.0
    ledger.close()


def test_collect_only_news_cannot_change_model_feature_hash(tmp_path) -> None:
    decision = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision - timedelta(hours=1))
    before = aggregate_news_features_v2(ledger, decision)
    _append_news(ledger, source="google_news_gold_geopolitics", item="headline-only",
                 first_seen=decision - timedelta(minutes=5), parsed_at=decision, impulse=1.0)
    after = aggregate_news_features_v2(ledger, decision)
    assert canonical_hash(before["features"]) == canonical_hash(after["features"])
    assert after["model_visible_items"] == 0
    ledger.close()


def test_single_reliable_publisher_remains_display_only(tmp_path) -> None:
    cutoff = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=cutoff - timedelta(hours=1))
    _append_news(
        ledger, source="gdelt_gold_geopolitics", item="War disrupts oil routes",
        first_seen=cutoff - timedelta(minutes=10), parsed_at=cutoff - timedelta(minutes=5),
        impulse=0.0, link="https://www.reuters.com/world/example",
        entities=["Iran", "Strait of Hormuz"], event_type="geopolitical_conflict",
    )
    event = event_evidence_rows(ledger, cutoff)[0]
    assert event["evidence_grade"] == "SINGLE_RELIABLE"
    assert event["broad_model_eligible"] is False
    assert event["model_permission"] == "DISPLAY_ONLY"
    ledger.close()


def test_two_independent_publishers_corroborate_same_event(tmp_path) -> None:
    cutoff = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=cutoff - timedelta(hours=1))
    common = {
        "first_seen": cutoff - timedelta(minutes=10),
        "parsed_at": cutoff - timedelta(minutes=5), "impulse": 0.0,
        "entities": ["Iran", "Strait of Hormuz"],
        "event_type": "geopolitical_conflict",
    }
    _append_news(
        ledger, source="gdelt_gold_geopolitics", item="War disrupts oil routes",
        link="https://www.reuters.com/world/example", **common,
    )
    _append_news(
        ledger, source="google_news_gold_context", item="Conflict threatens crude supply",
        link="https://www.bbc.com/news/example", **common,
    )
    events = event_evidence_rows(ledger, cutoff)
    assert len(events) == 1
    assert events[0]["evidence_grade"] == "CORROBORATED"
    assert events[0]["independent_publishers"] == 2
    assert events[0]["broad_model_eligible"] is True
    ledger.close()


def test_confirmation_is_not_visible_before_second_publisher_is_parsed(tmp_path) -> None:
    first = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first - timedelta(minutes=5))
    common = {
        "first_seen": first, "impulse": 0.0,
        "entities": ["Iran", "Strait of Hormuz"],
        "event_type": "geopolitical_conflict",
    }
    _append_news(
        ledger, source="gdelt_gold_geopolitics", item="War disrupts oil routes",
        parsed_at=first + timedelta(minutes=1),
        link="https://www.reuters.com/world/example", **common,
    )
    _append_news(
        ledger, source="google_news_gold_context", item="Conflict threatens crude supply",
        parsed_at=first + timedelta(minutes=8),
        link="https://www.bbc.com/news/example", **common,
    )
    early = event_evidence_rows(ledger, first + timedelta(minutes=5))[0]
    later = event_evidence_rows(ledger, first + timedelta(minutes=10))[0]
    assert early["evidence_grade"] == "SINGLE_RELIABLE"
    assert later["evidence_grade"] == "CORROBORATED"
    ledger.close()


def test_v2_tables_are_append_only_and_legacy_hash_is_unchanged(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    legacy = ("runtime_metadata", "market_snapshots", "decision_events", "outcomes")
    before = immutable_table_hash(ledger.connection, legacy)
    install_v2_schema(ledger.connection)
    after = immutable_table_hash(ledger.connection, legacy)
    assert before == after
    with pytest.raises(Exception, match="append-only"):
        ledger.connection.execute(
            "INSERT INTO source_eligibility_versions VALUES ('v','t','h','d')"
        )
        ledger.connection.execute(
            "UPDATE source_eligibility_versions SET description='changed' WHERE eligibility_version='v'"
        )
    ledger.close()


def test_live_settler_is_idempotent_after_repair_created_outcome(tmp_path) -> None:
    decision = datetime(2026, 8, 5, 10, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision)
    ledger.connection.execute(
        "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
        ("epoch", decision.isoformat(), decision.isoformat(), decision.isoformat(),
         decision.isoformat(), "commit", "contract"),
    )
    ledger.connection.execute(
        """INSERT INTO derived_outcomes (
        derived_outcome_id,source_decision_id,decision_time,evidence_lane,
        recomputed_at,label_version,outcome_status,reason_codes_json,
        ambiguity_state,commission_status,slippage_status,source_evidence_hash,
        output_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("outcome", "decision", decision.isoformat(), "REPAIRED_SEED",
         decision.isoformat(), "received-time-executable-30m-v2", "UNREPAIRABLE",
         "[]", "NONE", "UNCONFIGURED", "UNAVAILABLE_SHADOW", "source", "output"),
    )
    ledger.connection.commit()
    appended = append_live_outcome_v2(
        ledger, decision_id="decision", decision_time=decision,
        appended_at=decision + timedelta(minutes=31), label=SimpleNamespace(),
        source_evidence_hash="later-source",
    )
    assert appended is False
    assert ledger.connection.execute(
        "SELECT count(*) FROM derived_outcomes WHERE source_decision_id='decision'"
    ).fetchone()[0] == 1
    ledger.close()


def test_commission_and_slippage_are_never_fabricated() -> None:
    decision = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    rows = [
        _quote(decision + timedelta(seconds=1), decision + timedelta(seconds=1)),
        _quote(decision + timedelta(minutes=30, seconds=1),
               decision + timedelta(minutes=30, seconds=1), 4001.0),
    ]
    label = build_executable_label_v2(decision_time=decision, quotes=rows)
    assert label.commission_status == "UNCONFIGURED"
    assert label.slippage_status == "UNAVAILABLE_SHADOW"
    assert label.long_quote_return is not None


def test_learning_stages_do_not_wait_for_sixty_days() -> None:
    assert _stage(95, 0, 1) == "EARLY_LEARNING"
    assert _stage(96, 0, 1) == "PREVIEW"
    assert _stage(199, 0, 59) == "PREVIEW"
    assert _stage(200, 0, 1) == "INITIAL_SHADOW"
    assert _stage(200, 0, 20) == "RESEARCH_CANDIDATE"
    assert _stage(200, 0, 60) == "HIGHER_CONFIDENCE"


def _insert_prediction(connection, decision_id: str, decision_time: datetime, *,
                       model_version: str = "market-test",
                       model_identity: str = "MARKET_ONLY",
                       value_quote_return: float = 2.0,
                       residual_u5: float = 0.1) -> None:
    connection.execute(
        "INSERT INTO predictions_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision_id, model_version, model_identity, decision_time.isoformat(),
         decision_time.isoformat(), "LIVE_OOS", "feature-hash", 0.1, None,
         0.1, -0.1, None, None, "UTC_DAY_BLOCK_OOS_ABS_RESIDUAL_Q95",
         "calibration-test", 0, 0, 0, None, "UNCALIBRATED", "LONG", "WAIT",
         "PROVISIONAL"),
    )
    connection.execute(
        "INSERT INTO prediction_scores_v2 VALUES (?,?,?,?,?,?,?,?,?,?)",
        (decision_id, model_version, decision_time.isoformat(), value_quote_return, 0.2,
         residual_u5, residual_u5 ** 2, 1, 0, f"score-{decision_id}-{model_version}"),
    )


def _insert_model_update(connection, model_version: str, model_identity: str,
                         created_at: datetime, training_rows: int = 96) -> None:
    connection.execute(
        "INSERT INTO model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (model_version, model_identity, "PREVIEW_ONLY", created_at.isoformat(),
         created_at.isoformat(), training_rows, training_rows, 0, 0, 0, 0,
         f"dataset-{model_version}", "features", None, "artifact",
         f"artifact-{model_version}", "CHALLENGER"),
    )


def test_learning_curve_excludes_predictions_not_after_model_creation(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    created_at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger.connection.execute(
        "INSERT INTO model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("market-test", "MARKET_ONLY", "PREVIEW_ONLY", created_at.isoformat(),
         created_at.isoformat(), 96, 96, 0, 0, 0, 0, "dataset", "features", None,
         "artifact", "artifact-hash", "CHALLENGER"),
    )
    _insert_prediction(ledger.connection, "too-early", created_at)
    _insert_prediction(ledger.connection, "true-oos", created_at + timedelta(minutes=5))
    ledger.connection.commit()
    payload = learning_curve_payload(ledger.connection)
    model = payload["models"][0]
    assert model["subsequent_oos_rows"] == 1
    assert model["cumulative_quote_return"] == pytest.approx(2.0)
    market_curve = next(
        row for row in payload["identity_curves"] if row["model_identity"] == "MARKET_ONLY"
    )
    assert len(market_curve["points"]) == 1
    assert market_curve["points"][0]["cumulative_quote_return"] == pytest.approx(2.0)
    ledger.close()


def test_identity_curve_uses_only_latest_parallel_version_per_decision(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    created = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = created + timedelta(hours=1)
    _insert_model_update(
        ledger.connection, "market-archived", "MARKET_ONLY", created - timedelta(hours=1)
    )
    _insert_model_update(ledger.connection, "market-old", "MARKET_ONLY", created)
    _insert_model_update(
        ledger.connection, "market-new", "MARKET_ONLY", created + timedelta(minutes=30)
    )
    _insert_prediction(
        ledger.connection, "historical-decision", created + timedelta(minutes=15),
        model_version="market-old", value_quote_return=1.5,
    )
    _insert_prediction(
        ledger.connection, "same-decision", decision, model_version="market-old",
        value_quote_return=1.0,
    )
    _insert_prediction(
        ledger.connection, "same-decision", decision, model_version="market-new",
        value_quote_return=2.0,
    )
    payload = learning_curve_payload(ledger.connection)
    curve = next(
        row for row in payload["identity_curves"] if row["model_identity"] == "MARKET_ONLY"
    )
    assert len(curve["points"]) == 2
    assert curve["points"][0]["cumulative_quote_return"] == pytest.approx(1.5)
    assert curve["points"][0]["model_version"] == "market-old"
    assert curve["points"][1]["cumulative_quote_return"] == pytest.approx(3.5)
    assert curve["points"][1]["model_version"] == "market-new"
    models = {row["model_version"]: row for row in payload["models"]}
    assert models["market-new"]["lifecycle_status"] == "LATEST"
    assert models["market-old"]["lifecycle_status"] == "PREVIOUS"
    assert models["market-archived"]["lifecycle_status"] == "ARCHIVED"
    rolling = next(
        row for row in payload["rolling_processes"] if row["model_identity"] == "MARKET_ONLY"
    )
    assert rolling["oos_rows"] == 2
    assert rolling["cumulative_quote_return"] == pytest.approx(3.5)
    ledger.close()


def test_learning_curve_dashboard_envelope_is_bounded_and_keeps_history_landmarks() -> None:
    points = []
    for index in range(5000):
        point = {
            "decision_time": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * index)).isoformat(),
            "cumulative_quote_return": float((index % 101) - 50),
        }
        if index % 250 == 0:
            point["model_version"] = f"version-{index}"
        points.append(point)
    bounded = _bounded_curve(points, max_points=120)
    assert len(bounded) <= 120
    assert bounded[0] == points[0]
    assert bounded[-1] == points[-1]
    assert min(row["cumulative_quote_return"] for row in bounded) == -50.0
    assert max(row["cumulative_quote_return"] for row in bounded) == 50.0
    assert {row.get("model_version") for row in bounded if row.get("model_version")} == {
        f"version-{index}" for index in range(0, 5000, 250)
    }


def _training_rows(count: int) -> list[dict]:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    return [{
        "decision_id": f"d-{index}", "lane": "REPAIRED_SEED",
        "decision_time": (start + timedelta(minutes=5 * index)).isoformat(),
        "market": [float(index + offset) for offset in range(len(training_v2.MARKET_FEATURES))],
        "news": [0.0] * len(training_v2.NEWS_FEATURES), "target": float(index) / 100,
        "broad_news": [0.0] * len(training_v2.BROAD_MODEL_FEATURES),
        "news_exposed": False, "broad_news_exposed": False,
        "distinct_news_clusters": 0,
        "receipt": (f"d-{index}", f"m-{index}", f"n-{index}", f"o-{index}"),
    } for index in range(count)]


@pytest.mark.parametrize("count,expected_stage", [(96, "PREVIEW_ONLY"), (200, "SHADOW")])
def test_preview_and_shadow_thresholds_create_challengers_only(
    tmp_path, monkeypatch, count: int, expected_stage: str
) -> None:
    ledger = ForwardLedger(tmp_path / f"forward-{count}.sqlite3")
    rows = _training_rows(count)
    monkeypatch.setattr(training_v2, "complete_training_rows", lambda *_: rows)
    monkeypatch.setattr(
        training_v2, "_write_market_artifact",
        lambda _rows, root, cutoff, stage: (
            f"market-{stage.lower()}-test", SimpleNamespace(artifact_hash="artifact-hash"),
            tmp_path / "model.json", "dataset-hash"),
    )
    monkeypatch.setattr(training_v2, "chronological_crossfit_market", lambda *_: [])
    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 5, 12, tzinfo=UTC), tmp_path / "models"
    )
    update = ledger.connection.execute("SELECT * FROM model_updates_v2").fetchone()
    assert result[0]["status"] == "TRAINED"
    assert update["model_stage"] == expected_stage
    assert update["status"] == "CHALLENGER"
    assert update["repaired_seed_rows"] == count
    assert ledger.connection.execute("SELECT count(*) FROM predictions_v2").fetchone()[0] == 0
    assert learning_curve_payload(ledger.connection)["models"][0]["subsequent_oos_rows"] == 0
    ledger.close()


def test_retraining_occurs_after_fifty_additional_rows(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    initial_cutoff = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_model_update(ledger.connection, "market-existing", "MARKET_ONLY", initial_cutoff)
    _insert_model_update(ledger.connection, "full-existing", "FULL", initial_cutoff)
    _insert_model_update(
        ledger.connection, "broad-full-existing", "BROAD_FULL", initial_cutoff
    )
    monkeypatch.setattr(training_v2, "complete_training_rows", lambda *_: _training_rows(145))
    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 6, 20, tzinfo=UTC), tmp_path / "models"
    )
    assert result[0]["status"] == "NOT_DUE"
    assert result[0]["next_threshold"] == 146

    monkeypatch.setattr(training_v2, "complete_training_rows", lambda *_: _training_rows(146))
    monkeypatch.setattr(
        training_v2, "_write_market_artifact",
        lambda _rows, root, cutoff, stage: (
            "market-preview-only-new", SimpleNamespace(artifact_hash="artifact-hash"),
            tmp_path / "model.json", "dataset-hash-new",
        ),
    )
    monkeypatch.setattr(training_v2, "chronological_crossfit_market", lambda *_: [])
    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 7, 20, tzinfo=UTC), tmp_path / "models"
    )
    assert result[0]["status"] == "TRAINED"
    ledger.close()


@pytest.mark.parametrize(
    "event_days,expected_status",
    [(1, "EXPERIMENTAL_SINGLE_DAY"), (2, "EXPERIMENTAL_TWO_DAY")],
)
def test_news_models_train_early_with_explicit_experimental_status(
    tmp_path, monkeypatch, event_days: int, expected_status: str
) -> None:
    ledger = ForwardLedger(tmp_path / f"forward-news-{event_days}.sqlite3")
    rows = _training_rows(120)
    for row in rows:
        row["news_exposed"] = True
        row["news"] = [0.1] * len(training_v2.NEWS_FEATURES)
        row["broad_news_exposed"] = True
        row["broad_news"] = [0.1] * len(training_v2.BROAD_MODEL_FEATURES)
    first_seen = datetime(2026, 8, 1, 10, tzinfo=UTC)
    for index in range(10):
        seen = first_seen + timedelta(days=index % event_days)
        _append_news(
            ledger, source="federal_reserve_monetary", item=f"official-{index}",
            first_seen=seen, parsed_at=seen, impulse=0.1,
        )

    class Artifact:
        artifact_hash = "news-artifact-hash"

        def write(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(training_v2, "complete_training_rows", lambda *_: rows)
    monkeypatch.setattr(
        training_v2, "_write_market_artifact",
        lambda _rows, root, cutoff, stage: (
            "market-preview-test", Artifact(), tmp_path / "market.json", "dataset-hash",
        ),
    )
    monkeypatch.setattr(
        training_v2, "chronological_crossfit_market",
        lambda _ledger, crossfit_rows, *_: [
            {
                "decision_id": row["decision_id"], "artifact_hash": "crossfit-hash",
                "residual": row["target"] - 0.01,
            }
            for row in crossfit_rows[48:]
        ],
    )
    monkeypatch.setattr(training_v2, "train_ridge", lambda *_: Artifact())

    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 3, 12, tzinfo=UTC), tmp_path / "models"
    )
    trained = {row.get("model_identity"): row for row in result if row["status"] == "TRAINED"}
    assert trained["NEWS_RESIDUAL"]["news_evidence_status"] == expected_status
    assert trained["FULL"]["news_evidence_status"] == expected_status
    assert trained["BROAD_NEWS_RESIDUAL"]["news_evidence_status"] == expected_status
    assert trained["BROAD_FULL"]["news_evidence_status"] == expected_status
    updates = {
        row["model_identity"]: row
        for row in ledger.connection.execute("SELECT * FROM model_updates_v2")
    }
    assert expected_status.lower().replace("_", "-") in updates["NEWS_RESIDUAL"]["model_version"]
    assert updates["NEWS_RESIDUAL"]["distinct_event_days"] == event_days
    ledger.close()


def test_market_crossfit_uses_purged_chronological_training_only(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    seen_training_targets = []

    class Artifact:
        artifact_hash = "crossfit-artifact"

        def predict(self, values):
            return [0.0] * len(values)

    def fake_train(_x, targets, *_args):
        seen_training_targets.append(list(targets))
        return Artifact()

    monkeypatch.setattr(training_v2, "train_ridge", fake_train)
    records = training_v2.chronological_crossfit_market(
        ledger, _training_rows(120), tmp_path, datetime(2026, 8, 5, tzinfo=UTC)
    )
    assert records
    assert all(
        datetime.fromisoformat(row["training_cutoff"])
        < datetime.fromisoformat(row["purged_through"])
        for row in records
    )
    assert seen_training_targets
    ledger.close()


def test_rolling_uncertainty_uses_latest_version_per_prior_decision(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    base = datetime(2026, 8, 1, tzinfo=UTC)
    _insert_model_update(
        ledger.connection, "market-old", "MARKET_ONLY", base - timedelta(hours=2)
    )
    _insert_model_update(
        ledger.connection, "market-new", "MARKET_ONLY", base - timedelta(hours=1)
    )
    _insert_prediction(
        ledger.connection, "same-decision", base, model_version="market-old",
        residual_u5=9.0,
    )
    _insert_prediction(
        ledger.connection, "same-decision", base, model_version="market-new",
        residual_u5=0.25,
    )
    calibration = inference_v2._calibration(ledger, "MARKET_ONLY", base + timedelta(days=1))
    assert calibration["rows"] == 1
    assert calibration["half_width"] == pytest.approx(0.25)
    ledger.close()


def test_only_latest_and_previous_versions_are_active() -> None:
    updates = [
        {"model_identity": "MARKET_ONLY", "model_version": "market-3"},
        {"model_identity": "FULL", "model_version": "full-3"},
        {"model_identity": "MARKET_ONLY", "model_version": "market-2"},
        {"model_identity": "FULL", "model_version": "full-2"},
        {"model_identity": "MARKET_ONLY", "model_version": "market-1"},
        {"model_identity": "FULL", "model_version": "full-1"},
    ]
    active = inference_v2._active_updates(updates)
    assert [row["model_version"] for row in active] == [
        "market-3", "full-3", "market-2", "full-2",
    ]


def test_unhealthy_predictions_do_not_enter_rolling_calibration(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    _insert_model_update(
        ledger.connection, "market-live", "MARKET_ONLY", base - timedelta(hours=1)
    )
    empty = {"version": "none", "rows": 0, "blocks": 0, "days": 0,
             "half_width": None, "status": "UNCALIBRATED"}
    for index in range(75):
        when = base + timedelta(minutes=5 * index)
        inference_v2._insert_prediction(
            ledger, decision_id=f"unhealthy-{index}", decision_time=when, created_at=when,
            model_version="market-live", model_identity="MARKET_ONLY", feature_hash="features",
            predicted=None, news_residual=None, ev_long=None, ev_short=None,
            calibration=empty, recommended="WAIT", status="DATA_UNHEALTHY",
        )
    calibration = inference_v2._calibration(
        ledger, "MARKET_ONLY", base + timedelta(days=1)
    )
    assert calibration["rows"] == 0
    assert calibration["status"] == "UNCALIBRATED"
    ledger.close()


def test_uncalibrated_prediction_has_no_lcb(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 5, tzinfo=UTC)
    calibration = {"version": "none", "rows": 0, "blocks": 0, "days": 0,
                   "half_width": None, "status": "UNCALIBRATED"}
    inference_v2._insert_prediction(
        ledger, decision_id="d", decision_time=now, created_at=now,
        model_version="market", model_identity="MARKET_ONLY", feature_hash="features",
        predicted=1.0, news_residual=None, ev_long=0.8, ev_short=-1.2,
        calibration=calibration, recommended="LONG", status="PROVISIONAL",
    )
    row = ledger.connection.execute("SELECT * FROM predictions_v2").fetchone()
    assert row["lcb_long_u5"] is None
    assert row["lcb_short_u5"] is None
    assert row["effective_action"] == "WAIT"
    assert row["calibration_status"] == "UNCALIBRATED"
    ledger.close()


def test_prediction_insert_rejects_action_that_violates_post_cost_ev_policy(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 5, tzinfo=UTC)
    calibration = {"version": "cal", "rows": 20, "blocks": 2, "days": 2,
                   "half_width": 0.35, "status": "EARLY"}
    with pytest.raises(ValueError, match="violates frozen post-cost EV policy"):
        inference_v2._insert_prediction(
            ledger, decision_id="bad-action", decision_time=now, created_at=now,
            model_version="market", model_identity="MARKET_ONLY", feature_hash="features",
            predicted=0.3, news_residual=None, ev_long=0.3, ev_short=-0.4,
            calibration=calibration, recommended="WAIT", status="PROVISIONAL",
        )
    assert ledger.connection.execute("SELECT count(*) FROM predictions_v2").fetchone()[0] == 0
    ledger.close()


def test_recommendation_uses_positive_post_cost_ev_and_retains_wait() -> None:
    assert inference_v2._recommended_action(0.30, -0.40, None) == "LONG"
    assert inference_v2._recommended_action(0.30, -0.40, 0.35) == "LONG"
    assert inference_v2._recommended_action(0.30, -0.40, 0.20) == "LONG"
    assert inference_v2._recommended_action(-0.40, 0.30, 0.20) == "SHORT"
    assert inference_v2._recommended_action(0.30, 0.30, 0.20) == "WAIT"
    assert inference_v2._recommended_action(-0.10, -0.20, 0.20) == "WAIT"
