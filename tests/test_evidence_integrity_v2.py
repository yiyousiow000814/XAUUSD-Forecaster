from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from xauusd_forecaster.evidence_v2 import (
    ELIGIBILITY_VERSION,
    V2_SCHEMA,
    install_v2_schema,
)
from xauusd_forecaster.executable_label import build_executable_label_v2
from xauusd_forecaster.execution_costs import net_shadow_log_return
from xauusd_forecaster.forward_ledger import ForwardLedger, canonical_hash
from xauusd_forecaster.learning_curves import _bounded_curve, _stage, learning_curve_payload
from xauusd_forecaster.live_v2 import (
    _append_news_visibility_receipts,
    append_live_decision_v2,
    append_live_outcome_v2,
)
from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.news_evidence import EVIDENCE_POLICY_VERSION, event_evidence_rows
from xauusd_forecaster.news_contracts import (
    CURRENT_NEWS_CONTRACT,
    NewsContract,
)
from xauusd_forecaster.news_features_v2 import (
    aggregate_news_features_v2,
    event_raw_weight,
)
from xauusd_forecaster.news_impact import impact_time_rule, pending_impact_records
from xauusd_forecaster.news_semantics import annotation_topics, effective_record_kind
from xauusd_forecaster.news_time import assess_news_time, category_time_rule
from xauusd_forecaster.repair_v2 import immutable_table_hash
from xauusd_forecaster import inference_v2, news_contract_migration, training_v2
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
                 event_type: str = "monetary_policy",
                 published_at: datetime | None = None,
                 primary_category: str = "rates_fed",
                 include_published_time: bool = True,
                 record_kind: str = "FACT_EVENT",
                 evidence_role: str = "CORE_CLAIM",
                 materiality: float = 0.8,
                 material_event_key: str = "",
                 event_time: str | None = "__DEFAULT__",
                 impact_class: str = "POLICY_SHIFT",
                  impact_update_type: str = "NEW_EVENT",
                  impact_assessed_at: datetime | None = None,
                  source_organization_id: str | None = None,
                  include_impact: bool = True,
                  annotation_overrides: dict | None = None) -> None:
    entities = entities or []
    body = ("publisher full body " * 30) + item
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": source, "source_item_id": item,
        "source_published_time": (
            (published_at or first_seen) if include_published_time else None
        ),
        "collector_first_seen_time": first_seen, "fetched_time": first_seen,
        "headline": item, "body": body, "content_hash": digest, "cluster_id": item,
        "link": link,
    })
    annotation = {
        "event_type": event_type, "entities": entities, "hawkishness": impulse,
        "inflation_impulse": 0.0, "growth_impulse": 0.0,
        "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 1.0, "confidence": 1.0,
        "headline_zh": item, "summary_zh": body,
        "primary_category": primary_category,
        "secondary_categories": [], "emerging_topic_zh": "",
        "record_kind": record_kind,
        "actor": entities[0] if entities else "official source",
        "action": "reported",
        "object": entities[1] if len(entities) > 1 else item,
        "location": "",
        "event_time": (
            (published_at or first_seen).isoformat()
            if event_time == "__DEFAULT__" else event_time
        ),
        "claim_status": "CONFIRMED",
        "materiality": materiality,
        "canonical_actor_id": "official_source",
        "action_family": "OTHER_FACT",
        "canonical_object_id": "reported_event",
        "canonical_location_id": "",
        "episode_key": "",
        "primary_story_title_zh": item,
        "secondary_contexts_zh": [],
        "relation_to_prior": "NONE",
        "document_kind": "NEWS_REPORT",
        "material_event_key": material_event_key,
        "source_organization_id": source_organization_id or source,
        "evidence_role": evidence_role,
    }
    annotation.update(annotation_overrides or {})
    ledger.append_annotation({
        "annotation_id": item, "source": source, "source_item_id": item,
        "revision_number": 1, "raw_content_hash": digest, "event_type": event_type,
        "entities": entities, "hawkishness": impulse, "inflation_impulse": 0.0,
        "growth_impulse": 0.0, "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 1.0, "confidence": 1.0, "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": "news-json-v14-material-event-evidence",
        "parse_started_at": parsed_at, "parsed_at": parsed_at,
        "annotation": annotation,
    })
    if include_impact:
        assessed_at = impact_assessed_at or parsed_at
        ledger.append_news_impact_assessment({
            "assessment_id": f"impact:{source}:{item}",
            "source": source, "source_item_id": item, "revision_number": 1,
            "raw_content_hash": digest, "annotation_id": item,
            "llm_model_version": "gemma-4-31b-it",
            "prompt_version": "news-impact-v2-semantic-prior-candidates",
            "parse_started_at": assessed_at, "assessed_at": assessed_at,
            "impact_class": impact_class,
            "event_state": "ACTIVE" if impact_class != "BACKGROUND" else "BACKGROUND",
            "update_type": impact_update_type,
            "confidence": 1.0, "reason_zh": "测试中的固定影响寿命判断。",
        })


def test_news_freshness_ages_from_published_time_not_parsed_at(tmp_path) -> None:
    first_seen = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = first_seen + timedelta(minutes=30)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first_seen)
    _append_news(ledger, source="federal_reserve_monetary", item="official",
                 first_seen=first_seen, parsed_at=decision, impulse=1.0,
                 published_at=first_seen, impact_class="DATA_RELEASE")
    features = aggregate_news_features_v2(ledger, decision)
    expected_freshness = 2 ** (-30 / 360)
    assert features["features"]["news_event_count"] == pytest.approx(expected_freshness)
    ledger.close()


def test_late_received_news_can_be_used_only_after_receipt_when_still_active() -> None:
    published = datetime(2026, 8, 8, 20, 40, tzinfo=UTC)
    first_seen = published + timedelta(hours=2, minutes=53)
    max_age, _ = impact_time_rule("SAME_DAY")

    before_receipt = assess_news_time(
        {"source_published_time": published, "collector_first_seen_time": first_seen},
        decision_time=first_seen - timedelta(seconds=1),
        forward_epoch=published - timedelta(days=1),
        max_actionable_age=max_age,
        max_discovery_delay=None,
        allow_pre_forward_publication=True,
    )
    after_receipt = assess_news_time(
        {"source_published_time": published, "collector_first_seen_time": first_seen},
        decision_time=first_seen + timedelta(minutes=1),
        forward_epoch=published - timedelta(days=1),
        max_actionable_age=max_age,
        max_discovery_delay=None,
        allow_pre_forward_publication=True,
    )

    assert before_receipt.reason_code == "NOT_YET_VISIBLE"
    assert after_receipt.eligible is True
    assert after_receipt.age_minutes == pytest.approx(174.0)


def test_news_received_after_impact_window_is_expired_on_arrival() -> None:
    published = datetime(2026, 8, 8, 20, 40, tzinfo=UTC)
    first_seen = published + timedelta(hours=2, minutes=53)
    max_age, _ = impact_time_rule("IMMEDIATE")

    timing = assess_news_time(
        {"source_published_time": published, "collector_first_seen_time": first_seen},
        decision_time=first_seen + timedelta(minutes=1),
        forward_epoch=published - timedelta(days=1),
        max_actionable_age=max_age,
        max_discovery_delay=None,
        allow_pre_forward_publication=True,
    )

    assert timing.eligible is False
    assert timing.reason_code == "STALE_EVENT"


def test_weekend_closure_does_not_consume_actionable_lifetime() -> None:
    published = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    first_seen = published + timedelta(minutes=5)
    monday_open = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    max_age, _ = impact_time_rule("ONGOING_EVENT")

    timing = assess_news_time(
        {"source_published_time": published, "collector_first_seen_time": first_seen},
        decision_time=monday_open,
        forward_epoch=published - timedelta(days=1),
        max_actionable_age=max_age,
        max_discovery_delay=None,
        allow_pre_forward_publication=True,
        exclude_weekly_closure=True,
    )

    assert timing.eligible is True
    assert timing.age_minutes == pytest.approx(3 * 60)


def test_release_categories_expire_faster_than_central_bank_gold() -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = epoch + timedelta(hours=30)
    row = {
        "source_published_time": epoch,
        "collector_first_seen_time": epoch + timedelta(minutes=1),
    }
    release_age, release_half_life = category_time_rule("inflation_employment")
    gold_age, gold_half_life = category_time_rule("central_bank_gold")

    release = assess_news_time(
        row, decision_time=decision, forward_epoch=epoch,
        max_actionable_age=release_age,
    )
    gold = assess_news_time(
        row, decision_time=decision, forward_epoch=epoch,
        max_actionable_age=gold_age,
    )

    assert release.reason_code == "STALE_EVENT"
    assert gold.eligible is True
    assert release_half_life < gold_half_life


def test_news_older_than_72_hours_is_not_a_current_feature(tmp_path) -> None:
    decision = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    first_seen = decision - timedelta(days=5, hours=2, seconds=1)
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


def test_archive_collected_after_epoch_does_not_become_current_news(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = epoch + timedelta(minutes=30)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="federal_reserve_monetary", item="archive",
        first_seen=epoch + timedelta(minutes=2), parsed_at=epoch + timedelta(minutes=4),
        published_at=epoch - timedelta(days=30), impulse=1.0,
    )
    features = aggregate_news_features_v2(ledger, decision)
    assert features["model_visible_items"] == 0
    assert features["features"]["news_event_count"] == 0.0
    ledger.close()


def test_missing_publisher_time_is_display_only_not_a_current_impulse(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = epoch + timedelta(minutes=30)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="federal_reserve_monetary", item="unknown-time",
        first_seen=epoch + timedelta(minutes=2), parsed_at=epoch + timedelta(minutes=4),
        impulse=1.0, include_published_time=False,
    )
    features = aggregate_news_features_v2(ledger, decision)
    assert features["model_visible_items"] == 0
    ledger.close()


def test_regulatory_category_cannot_enter_direction_features(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = epoch + timedelta(minutes=30)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="federal_reserve_press_all", item="bank-application",
        first_seen=epoch + timedelta(minutes=2), parsed_at=epoch + timedelta(minutes=4),
        impulse=1.0, primary_category="regulation_other",
    )
    features = aggregate_news_features_v2(ledger, decision)
    assert features["model_visible_items"] == 0
    assert features["features"]["news_hawkishness"] == 0.0
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


def test_single_reliable_publisher_is_provisional_and_downweighted(tmp_path) -> None:
    cutoff = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=cutoff - timedelta(hours=1))
    _append_news(
        ledger, source="gdelt_gold_geopolitics", item="War disrupts oil routes",
        first_seen=cutoff - timedelta(minutes=10), parsed_at=cutoff - timedelta(minutes=5),
        impulse=1.0, link="https://www.reuters.com/world/example",
        entities=["Iran", "Strait of Hormuz"], event_type="geopolitical_conflict",
    )
    event = event_evidence_rows(ledger, cutoff)[0]
    assert event["evidence_grade"] == "SINGLE_RELIABLE"
    assert event["broad_model_eligible"] is True
    assert event["model_permission"] == "BROAD_MODEL"
    assert "RELIABLE_SINGLE_SOURCE_PROVISIONAL" in event["reason_codes"]
    assert event_raw_weight(event) == pytest.approx(
        0.35 * 2 ** (-10 / 1440)
    )
    features = aggregate_news_features_v2(ledger, cutoff)["features"]
    assert features["broad_news_hawkishness"] == pytest.approx(
        0.35 * 2 ** (-10 / 1440)
    )
    assert features["broad_news_event_count"] == pytest.approx(
        0.35 * 2 ** (-10 / 1440)
    )
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
    assert early["event_version_id"] != later["event_version_id"]
    assert early["source_hash"] != later["source_hash"]
    ledger.close()


def test_evidence_upgrade_versions_membership_when_canonical_item_is_unchanged(
    tmp_path,
) -> None:
    first = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first)
    common = {
        "first_seen": first, "impulse": 0.5,
        "entities": ["Federal Reserve", "rates"],
        "material_event_key": "same-event",
    }
    _append_news(
        ledger, source="google_news_fed_rates", item="zzzz",
        parsed_at=first + timedelta(minutes=1),
        link="https://reuters.com/z", source_organization_id="reuters", **common,
    )
    early = event_evidence_rows(ledger, first + timedelta(minutes=2))[0]
    _append_news(
        ledger, source="google_news_gold_context", item="aaaa",
        parsed_at=first + timedelta(minutes=3),
        link="https://bbc.com/a", source_organization_id="bbc", **common,
    )
    later = event_evidence_rows(ledger, first + timedelta(minutes=4))[0]

    assert early["canonical_source_item_id"] == "zzzz"
    assert later["canonical_source_item_id"] == "zzzz"
    assert early["event_version_id"] != later["event_version_id"]
    ledger.close()


def test_syndicated_copy_does_not_create_independent_confirmation(tmp_path) -> None:
    cutoff = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=cutoff - timedelta(hours=1))
    common = {
        "first_seen": cutoff - timedelta(minutes=10),
        "parsed_at": cutoff - timedelta(minutes=5), "impulse": 0.5,
        "entities": ["Federal Reserve", "interest rates"],
        "event_type": "monetary_policy", "material_event_key": "fed-guidance",
        "source_organization_id": "reuters",
    }
    _append_news(
        ledger, source="google_news_fed_rates", item="Reuters original",
        link="https://www.reuters.com/world/example", **common,
    )
    _append_news(
        ledger, source="google_news_gold_context", item="Reuters syndicated copy",
        link="https://www.kitco.com/news/example", **common,
    )

    event = event_evidence_rows(ledger, cutoff)[0]

    assert event["evidence_grade"] == "SINGLE_RELIABLE"
    assert event["independent_publishers"] == 1
    assert event["source_organizations"] == ["reuters"]
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


def test_news_contract_migration_is_point_in_time_and_idempotent(
    tmp_path, monkeypatch
) -> None:
    decision = datetime(2026, 8, 5, 10, tzinfo=UTC)
    cutoff = decision + timedelta(hours=1)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision)
    ledger.connection.execute(
        "INSERT INTO derived_market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "market", "decision", decision.isoformat(), "source", None,
            "LIVE_OOS", decision.isoformat(), news_contract_migration.FEATURE_VERSION,
            "u5", 1.0, "[]", "HEALTHY", "[]", "source", "market-output",
        ),
    )
    ledger.connection.execute(
        """INSERT INTO derived_outcomes (
        derived_outcome_id,source_decision_id,decision_time,evidence_lane,
        recomputed_at,label_version,outcome_status,reason_codes_json,
        ambiguity_state,commission_status,slippage_status,source_evidence_hash,
        output_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "outcome", "decision", decision.isoformat(), "LIVE_OOS",
            cutoff.isoformat(), news_contract_migration.LABEL_VERSION, "VALID", "[]",
            "NONE", "UNCONFIGURED", "UNAVAILABLE_SHADOW", "source", "outcome-output",
        ),
    )
    ledger.connection.execute(
        "INSERT INTO training_eligibility_v2 VALUES (?,?,?,?,?,?,?,?)",
        (
            "eligibility", "decision", "LIVE_OOS", cutoff.isoformat(),
            "test", "market-output", "outcome-output", None,
        ),
    )
    ledger.connection.commit()

    observed_cutoffs = []

    def fake_aggregate(_ledger, visible_at):
        observed_cutoffs.append(visible_at)
        return {
            "features": [0.25], "model_visible_items": 1, "news_exposed": True,
            "distinct_news_clusters": 1, "distinct_event_types": 1,
            "source_evidence_hash": "news-source",
            "official_visible_events": [], "broad_visible_events": [],
        }

    monkeypatch.setattr(
        news_contract_migration, "aggregate_news_features_v2", fake_aggregate
    )
    first = news_contract_migration.append_missing_current_news_snapshots(
        ledger, cutoff, recomputed_at=cutoff
    )
    second = news_contract_migration.append_missing_current_news_snapshots(
        ledger, cutoff, recomputed_at=cutoff
    )

    assert first["appended"] == 1
    assert second["appended"] == 0
    assert observed_cutoffs == [decision]
    snapshot = ledger.connection.execute(
        "SELECT * FROM derived_news_feature_snapshots"
    ).fetchone()
    assert snapshot["decision_time"] == decision.isoformat()
    assert snapshot["feature_version"] == news_contract_migration.NEWS_FEATURE_VERSION
    assert ledger.connection.execute(
        "SELECT count(*) FROM evidence_lane_assignments WHERE evidence_type='DERIVED_NEWS'"
    ).fetchone()[0] == 1
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


def test_model_news_visibility_receipt_is_exact_and_append_only(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    decision = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    _insert_prediction(
        ledger.connection,
        "news-receipt-decision",
        decision,
        model_version="broad-full-receipt-test",
        model_identity="BROAD_FULL",
    )
    prediction = {
        "model_identity": "BROAD_FULL",
        "model_version": "broad-full-receipt-test",
        "eligibility_version": f"{ELIGIBILITY_VERSION}+{EVIDENCE_POLICY_VERSION}",
    }
    news = {
        "official_visible_events": [],
        "broad_visible_events": [
            {"event_key": "event-a", "source_hash": "hash-a"},
            {"event_key": "event-b", "source_hash": "hash-b"},
        ],
    }

    inserted = _append_news_visibility_receipts(
        ledger.connection,
        decision_id="news-receipt-decision",
        decision_time=decision,
        recorded_at=decision,
        predictions=[prediction],
        news_by_eligibility={prediction["eligibility_version"]: news},
    )
    assert inserted == 2
    assert _append_news_visibility_receipts(
        ledger.connection,
        decision_id="news-receipt-decision",
        decision_time=decision,
        recorded_at=decision,
        predictions=[prediction],
        news_by_eligibility={prediction["eligibility_version"]: news},
    ) == 0
    rows = ledger.connection.execute(
        """SELECT event_key,event_source_hash,receipt_origin
        FROM news_model_visibility_receipts_v1 ORDER BY event_key"""
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("event-a", "hash-a", "LIVE"),
        ("event-b", "hash-b", "LIVE"),
    ]
    catalog = ledger.connection.execute(
        """SELECT event_key,event_source_hash,canonical_headline
        FROM news_model_visibility_events_v1 ORDER BY event_key"""
    ).fetchall()
    assert [tuple(row) for row in catalog] == [
        ("event-a", "hash-a", "event-a"),
        ("event-b", "hash-b", "event-b"),
    ]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.connection.execute(
            "UPDATE news_model_visibility_receipts_v1 SET event_key='changed'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.connection.execute("DELETE FROM news_model_visibility_events_v1")
    ledger.close()


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
    assert model["cumulative_quote_return"] == pytest.approx(net_shadow_log_return(2.0))
    market_curve = next(
        row for row in payload["identity_curves"] if row["model_identity"] == "MARKET_ONLY"
    )
    assert len(market_curve["points"]) == 1
    assert market_curve["points"][0]["cumulative_quote_return"] == pytest.approx(
        net_shadow_log_return(2.0)
    )
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
    assert curve["points"][0]["cumulative_quote_return"] == pytest.approx(
        net_shadow_log_return(1.5)
    )
    assert curve["points"][0]["model_version"] == "market-old"
    assert curve["points"][1]["cumulative_quote_return"] == pytest.approx(
        net_shadow_log_return(1.5) + net_shadow_log_return(2.0)
    )
    assert curve["points"][1]["model_version"] == "market-new"
    models = {row["model_version"]: row for row in payload["models"]}
    assert models["market-new"]["lifecycle_status"] == "LATEST"
    assert models["market-old"]["lifecycle_status"] == "ARCHIVED"
    assert models["market-archived"]["lifecycle_status"] == "ARCHIVED"
    rolling = next(
        row for row in payload["rolling_processes"] if row["model_identity"] == "MARKET_ONLY"
    )
    assert rolling["oos_rows"] == 2
    assert rolling["cumulative_quote_return"] == pytest.approx(
        net_shadow_log_return(1.5) + net_shadow_log_return(2.0)
    )
    ledger.close()


def test_learning_curves_expose_true_fixed_30m_non_overlapping_evaluation(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    created = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    _insert_model_update(ledger.connection, "market-cadence", "MARKET_ONLY", created)
    _insert_prediction(
        ledger.connection, "five-minute-only-a", created + timedelta(minutes=5),
        model_version="market-cadence", value_quote_return=1.0,
    )
    _insert_prediction(
        ledger.connection, "fixed-grid", created + timedelta(minutes=30),
        model_version="market-cadence", value_quote_return=2.0,
    )
    _insert_prediction(
        ledger.connection, "five-minute-only-b", created + timedelta(minutes=35),
        model_version="market-cadence", value_quote_return=-0.5,
    )
    payload = learning_curve_payload(ledger.connection)

    group = next(
        row for row in payload["version_groups"]
        if row["model_identity"] == "MARKET_ONLY"
    )
    assert group["cadence_metrics"]["EVERY_5M"]["oos_rows"] == 3
    assert group["cadence_metrics"]["EVERY_5M"]["cumulative_quote_return"] == pytest.approx(
        sum(net_shadow_log_return(value) for value in (1.0, 2.0, -0.5))
    )
    assert group["cadence_metrics"]["FIXED_30M"]["oos_rows"] == 1
    assert group["cadence_metrics"]["FIXED_30M"]["cumulative_quote_return"] == pytest.approx(
        net_shadow_log_return(2.0)
    )

    curve = next(
        row for row in payload["identity_curves"] if row["model_identity"] == "MARKET_ONLY"
    )
    assert len(curve["points"]) == 3
    assert len(curve["points_30m"]) == 1
    assert curve["points_30m"][0]["decision_time"] == (created + timedelta(minutes=30)).isoformat()
    assert curve["points_30m"][0]["cumulative_quote_return"] == pytest.approx(
        net_shadow_log_return(2.0)
    )
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
        "official_events": [], "broad_events": [],
        "receipt": (f"d-{index}", f"m-{index}", f"n-{index}", f"o-{index}"),
    } for index in range(count)]


def _attach_event_exposure(rows: list[dict], *, event_days: int = 1,
                           event_count: int = 3) -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    for index, row in enumerate(rows):
        event_index = index % event_count
        event = {
            "event_id": f"event-{event_index}",
            "event_version_id": f"event-version-{event_index}",
            "event_occurred_at": (
                start + timedelta(days=event_index % event_days)
            ).isoformat(),
            "raw_weight": 1.0,
        }
        row["news_exposed"] = True
        row["broad_news_exposed"] = True
        row["news"] = [0.1] * len(training_v2.NEWS_FEATURES)
        row["broad_news"] = [0.1] * len(training_v2.BROAD_MODEL_FEATURES)
        row["official_events"] = [{**event, "model_permission": "OFFICIAL_MODEL"}]
        row["broad_events"] = [{**event, "model_permission": "BROAD_MODEL"}]


@pytest.mark.parametrize("count", [96, 200])
def test_generation_waits_for_news_evidence_without_partial_market_update(
    tmp_path, monkeypatch, count: int
) -> None:
    ledger = ForwardLedger(tmp_path / f"forward-{count}.sqlite3")
    rows = _training_rows(count)
    monkeypatch.setattr(training_v2, "complete_training_rows", lambda *_: rows)
    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 5, 12, tzinfo=UTC), tmp_path / "models"
    )
    update = ledger.connection.execute("SELECT * FROM model_updates_v2").fetchone()
    assert result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"
    assert update is None
    assert ledger.connection.execute("SELECT count(*) FROM predictions_v2").fetchone()[0] == 0
    ledger.close()


def test_policy_generation_does_not_reuse_legacy_retrain_clock(tmp_path, monkeypatch) -> None:
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
    assert result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"

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
    assert result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"
    ledger.close()


@pytest.mark.parametrize(
    "event_days,expected_status",
    [(1, "EXPERIMENTAL_SINGLE_DAY"), (2, "EXPERIMENTAL_TWO_DAY")],
)
def test_news_models_train_early_with_explicit_experimental_status(
    tmp_path, monkeypatch, event_days: int, expected_status: str
) -> None:
    first_seen = datetime(2026, 8, 1, 10, tzinfo=UTC)
    ledger = ForwardLedger(
        tmp_path / f"forward-news-{event_days}.sqlite3", now=first_seen
    )
    rows = _training_rows(120)
    _attach_event_exposure(rows, event_days=event_days)
    for index in range(3):
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
    (tmp_path / "market.json").write_text("{}", encoding="utf-8")
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
    trained_targets: list[tuple[tuple[str, ...], list[float]]] = []

    def capture_train(_matrix, targets, feature_names, *_args):
        trained_targets.append((tuple(feature_names), list(targets)))
        return Artifact()

    monkeypatch.setattr(training_v2, "train_ridge", capture_train)

    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 3, 12, tzinfo=UTC), tmp_path / "models"
    )
    trained = {row.get("model_identity"): row for row in result if row["status"] == "TRAINED"}
    assert trained["NEWS_RESIDUAL"]["news_evidence_status"] == expected_status
    assert trained["FULL"]["news_evidence_status"] == expected_status
    assert trained["BROAD_NEWS_RESIDUAL"]["news_evidence_status"] == expected_status
    assert trained["BROAD_FULL"]["news_evidence_status"] == expected_status
    assert trained["NEWS_ONLY"]["news_evidence_status"] == expected_status
    updates = {
        row["model_identity"]: row
        for row in ledger.connection.execute("SELECT * FROM model_updates_v2")
    }
    assert expected_status.lower().replace("_", "-") in updates["NEWS_RESIDUAL"]["model_version"]
    assert updates["NEWS_RESIDUAL"]["distinct_event_days"] == event_days
    broad_targets = [
        targets for features, targets in trained_targets
        if features == tuple(training_v2.BROAD_MODEL_FEATURES)
    ]
    assert len(broad_targets) == 2
    assert broad_targets[0][0] == pytest.approx(rows[48]["target"] - 0.01)
    assert broad_targets[1][0] == pytest.approx(rows[48]["target"])
    ledger.close()


def test_generation_activates_all_six_models_only_after_news_is_eligible(
    tmp_path, monkeypatch
) -> None:
    first_seen = datetime(2026, 8, 1, 10, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward-news-bootstrap.sqlite3", now=first_seen)
    rows = _training_rows(320)
    market_path = tmp_path / "market.json"
    market_path.write_text("{}", encoding="utf-8")

    class Artifact:
        artifact_hash = "artifact-hash"

        def write(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(training_v2, "complete_training_rows", lambda *_: rows)
    monkeypatch.setattr(
        training_v2, "_write_market_artifact",
        lambda _rows, root, cutoff, stage: (
            "market-shadow-bootstrap-test", Artifact(), market_path, "dataset-hash",
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

    first_result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 2, 12, tzinfo=UTC), tmp_path / "models"
    )
    assert first_result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"
    assert ledger.connection.execute(
        "SELECT count(*) FROM model_updates_v2"
    ).fetchone()[0] == 0

    _attach_event_exposure(rows)
    for index in range(3):
        _append_news(
            ledger, source="federal_reserve_monetary", item=f"bootstrap-{index}",
            first_seen=first_seen, parsed_at=first_seen, impulse=0.1,
        )

    second_result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 2, 13, tzinfo=UTC), tmp_path / "models"
    )
    assert second_result[0]["status"] == "TRAINED"
    assert {
        row.get("model_identity") for row in second_result if row["status"] == "TRAINED"
    } == {
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL", "BROAD_NEWS_RESIDUAL",
        "BROAD_FULL", "NEWS_ONLY",
    }
    market_generations = ledger.connection.execute(
        "SELECT count(*) FROM model_updates_v2 WHERE model_identity='MARKET_ONLY'"
    ).fetchone()[0]
    assert market_generations == 1
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
    def generation(version: int) -> list[dict]:
        return [
            {"model_identity": "MARKET_ONLY", "model_version": f"market-{version}"},
            {"model_identity": "NEWS_RESIDUAL", "model_version": f"news-{version}",
             "feature_version": inference_v2.NEWS_FEATURE_VERSION,
             "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
            {"model_identity": "FULL", "model_version": f"full-{version}",
             "feature_version": f"{inference_v2.FEATURE_VERSION}+{inference_v2.NEWS_FEATURE_VERSION}",
             "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
            {"model_identity": "BROAD_NEWS_RESIDUAL", "model_version": f"broad-news-{version}",
             "feature_version": inference_v2.NEWS_FEATURE_VERSION,
             "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
            {"model_identity": "BROAD_FULL", "model_version": f"broad-full-{version}",
             "feature_version": (
                 f"{inference_v2.FEATURE_VERSION}+{inference_v2.NEWS_FEATURE_VERSION}"
                 f"+{inference_v2.EVIDENCE_POLICY_VERSION}"
             ),
             "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
            {"model_identity": "NEWS_ONLY", "model_version": f"news-only-{version}",
             "feature_version": (
                 f"{inference_v2.NEWS_FEATURE_VERSION}"
                 f"+{inference_v2.EVIDENCE_POLICY_VERSION}"
             ),
             "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
        ]

    updates = generation(3) + generation(2) + generation(1)
    active = inference_v2._active_updates(updates)
    assert [row["model_version"] for row in active] == [
        "market-3", "news-3", "full-3", "broad-news-3", "broad-full-3",
        "news-only-3",
    ]


def test_activated_generation_directly_replaces_previous_generation(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    base = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    identities = (
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
    )
    for generation_index in (1, 2):
        generation_id = f"generation-{generation_index}"
        for identity in identities:
            version = f"{identity.lower()}-{generation_index}"
            _insert_model_update(ledger.connection, version, identity, base)
        ledger.connection.execute(
            "INSERT INTO news_model_generations_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (generation_id, "PREVIEW_ONLY", base.isoformat(), base.isoformat(),
             EVIDENCE_POLICY_VERSION, training_v2.NEWS_FEATURE_VERSION,
             training_v2.ELIGIBILITY_VERSION, f"events-{generation_index}",
             f"market-{generation_index}", f"official-{generation_index}",
             f"broad-{generation_index}", training_v2.EVENT_WEIGHTING_VERSION, 5, "READY"),
        )
        for identity in identities:
            ledger.connection.execute(
                "INSERT INTO news_model_generation_members_v1 VALUES (?,?,?)",
                (generation_id, identity, f"{identity.lower()}-{generation_index}"),
            )
        ledger.connection.execute(
            "INSERT INTO news_model_generation_activations_v1 VALUES (?,?,?,?,?)",
            (f"activation-{generation_index}", generation_id,
             f"generation-{generation_index - 1}" if generation_index > 1 else None,
             (base + timedelta(minutes=generation_index)).isoformat(), "TEST"),
        )
    updates = inference_v2._activated_generation_updates(
        ledger, base + timedelta(minutes=3)
    )
    assert {row["model_version"] for row in updates} == {
        f"{identity.lower()}-2" for identity in identities
    }


def test_retired_contract_cannot_remain_active_after_cleanup(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    base = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    retired = NewsContract(
        name="retired-test-contract",
        policy_version="retired-policy",
        feature_version="retired-features",
        eligibility_version="retired-eligibility",
    )
    identities = (
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
    )
    for identity in identities:
        _insert_model_update(
            ledger.connection, f"retired-{identity.lower()}", identity, base,
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generations_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "retired-generation", "PREVIEW_ONLY", base.isoformat(), base.isoformat(),
            retired.policy_version, retired.feature_version, retired.eligibility_version,
            "events", "market", "official", "broad", "weighting", 5, "READY",
        ),
    )
    for identity in identities:
        ledger.connection.execute(
            "INSERT INTO news_model_generation_members_v1 VALUES (?,?,?)",
            ("retired-generation", identity, f"retired-{identity.lower()}"),
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generation_activations_v1 VALUES (?,?,?,?,?)",
        (
            "retired-activation", "retired-generation", None,
            (base + timedelta(minutes=1)).isoformat(), "TEST",
        ),
    )

    assert inference_v2._has_activated_generation(
        ledger, base + timedelta(minutes=2),
    ) is True
    assert inference_v2._allow_activated_transition_contract(
        ledger, base + timedelta(minutes=2),
    ) is False
    transition = learning_curve_payload(ledger.connection)["news_contract_transition"]
    assert transition["state"] == "BLOCKED_UNSUPPORTED_ACTIVE_CONTRACT"
    assert transition["active_contract"]["policy_version"] == retired.policy_version
    assert transition["target_contract"]["name"] == CURRENT_NEWS_CONTRACT.name
    ledger.close()


def test_live_decision_writes_only_current_news_contract(tmp_path) -> None:
    decision = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision)
    ledger.connection.execute(
        "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
        (
            "epoch", decision.isoformat(), decision.isoformat(), decision.isoformat(),
            decision.isoformat(), "commit", "contract",
        ),
    )
    snapshot = {
        "features": {name: 0.0 for name in MARKET_FEATURES},
        "bid": 2400.0,
        "ask": 2400.1,
        "snapshot_hash": "market-snapshot",
        "source_event_time": decision,
        "source_received_time": decision,
        "u5": 0.0,
        "data_health": "OK",
        "reason_codes": [],
    }

    append_live_decision_v2(
        ledger, decision_id="current-only", decision_time=decision,
        created_at=decision, snapshot=snapshot,
    )

    versions = ledger.connection.execute(
        """SELECT DISTINCT feature_version,eligibility_version
        FROM derived_news_feature_snapshots WHERE source_decision_id=?""",
        ("current-only",),
    ).fetchall()
    assert [(row["feature_version"], row["eligibility_version"]) for row in versions] == [
        (CURRENT_NEWS_CONTRACT.feature_version, CURRENT_NEWS_CONTRACT.eligibility_version)
    ]
    ledger.close()


def test_contract_upgrade_bypasses_old_generation_retrain_clock(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward-contract-upgrade.sqlite3")
    base = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    retired = NewsContract(
        name="retired-test-contract",
        policy_version="retired-policy",
        feature_version="retired-features",
        eligibility_version="retired-eligibility",
    )
    identities = (
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
    )
    for identity in identities:
        _insert_model_update(
            ledger.connection, f"old-{identity.lower()}", identity, base,
            training_rows=96,
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generations_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "old-generation", "PREVIEW_ONLY", base.isoformat(), base.isoformat(),
            retired.policy_version, retired.feature_version, retired.eligibility_version,
            "events", "market", "official", "broad", "weighting", 5, "READY",
        ),
    )
    for identity in identities:
        ledger.connection.execute(
            "INSERT INTO news_model_generation_members_v1 VALUES (?,?,?)",
            ("old-generation", identity, f"old-{identity.lower()}"),
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generation_activations_v1 VALUES (?,?,?,?,?)",
        (
            "old-activation", "old-generation", None,
            (base + timedelta(minutes=1)).isoformat(), "TEST",
        ),
    )
    monkeypatch.setattr(
        training_v2, "complete_training_rows", lambda *_: _training_rows(120),
    )

    result = training_v2.train_due_v2(
        ledger, base + timedelta(days=1), tmp_path / "models",
    )

    assert result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"
    assert result[0]["active_contract_current"] is False
    assert result[0]["minimum_exposed_rows"] == training_v2.NEWS_MIN_EXPOSED_ROWS
    ledger.close()


def test_news_generation_stays_inactive_until_all_five_news_models_exist() -> None:
    updates = [
        {"model_identity": "MARKET_ONLY", "model_version": "market-current"},
        {"model_identity": "NEWS_RESIDUAL", "model_version": "news-current",
         "feature_version": inference_v2.NEWS_FEATURE_VERSION,
         "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
        {"model_identity": "FULL", "model_version": "full-current",
         "feature_version": f"{inference_v2.FEATURE_VERSION}+{inference_v2.NEWS_FEATURE_VERSION}",
         "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
    {"model_identity": "BROAD_NEWS_RESIDUAL", "model_version": "broad-news-current",
         "feature_version": inference_v2.NEWS_FEATURE_VERSION,
         "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
    ]

    active = inference_v2._active_updates(updates)

    assert [row["model_version"] for row in active] == ["market-current"]
    status = inference_v2.news_model_activation_status(updates)
    assert {
        row["model_identity"]: row["status"] for row in status
    } == {
        "NEWS_RESIDUAL": "GENERATION_WAIT",
        "FULL": "GENERATION_WAIT",
        "BROAD_NEWS_RESIDUAL": "GENERATION_WAIT",
        "BROAD_FULL": "NOT_TRAINED",
        "NEWS_ONLY": "NOT_TRAINED",
    }


def test_active_updates_reject_old_news_eligibility_but_keep_market() -> None:
    updates = [
        {"model_identity": "FULL", "model_version": "old-full",
         "eligibility_version": "news-source-eligibility-v2-event-evidence"},
        {"model_identity": "BROAD_FULL", "model_version": "old-broad",
         "eligibility_version": "news-source-eligibility-v2-event-evidence+old"},
        {"model_identity": "MARKET_ONLY", "model_version": "market-current"},
    ]
    active = inference_v2._active_updates(updates)
    assert [row["model_version"] for row in active] == ["market-current"]


def test_active_updates_do_not_revive_frozen_legacy_news() -> None:
    legacy = "news-source-eligibility-v2-event-evidence"
    legacy_broad = f"{legacy}+news-event-evidence-v1"
    updates = [
        {"model_identity": "FULL", "model_version": "old-full",
         "eligibility_version": legacy},
        {"model_identity": "BROAD_FULL", "model_version": "old-broad",
         "eligibility_version": legacy_broad},
        {"model_identity": "MARKET_ONLY", "model_version": "market-current"},
    ]
    active = inference_v2._active_updates(updates, {legacy, legacy_broad})
    assert [row["model_version"] for row in active] == ["market-current"]


def test_transition_set_requires_an_explicit_exact_contract() -> None:
    contract = NewsContract(
        name="transition-test-contract",
        policy_version="transition-policy",
        feature_version="transition-features",
        eligibility_version="transition-eligibility",
    )
    updates = [
        {"model_identity": "MARKET_ONLY", "model_version": "market-live"},
        {"model_identity": "NEWS_RESIDUAL", "model_version": "news-live",
         "feature_version": contract.feature_version,
         "eligibility_version": contract.eligibility_version},
        {"model_identity": "FULL", "model_version": "full-live",
         "feature_version": f"{inference_v2.FEATURE_VERSION}+{contract.feature_version}",
         "eligibility_version": contract.eligibility_version},
        {"model_identity": "BROAD_NEWS_RESIDUAL", "model_version": "broad-news-live",
         "feature_version": contract.feature_version,
         "eligibility_version": contract.eligibility_version},
        {"model_identity": "BROAD_FULL", "model_version": "broad-full-live",
         "feature_version": (
             f"{inference_v2.FEATURE_VERSION}+{contract.feature_version}"
             f"+{contract.policy_version}"
         ),
         "eligibility_version": contract.eligibility_version},
    ]
    blocked = inference_v2._active_updates(
        updates, {contract.eligibility_version}, enforce_current_contract=False,
    )
    assert [row["model_version"] for row in blocked] == ["market-live"]

    active = inference_v2._active_updates(
        updates, {contract.eligibility_version}, enforce_current_contract=False,
        news_contract=contract,
    )
    assert {row["model_version"] for row in active} == {
        "market-live", "news-live", "full-live", "broad-news-live", "broad-full-live",
    }


def test_transition_contract_keeps_exact_old_four_model_set_during_handover() -> None:
    contract = NewsContract(
        name="transition-test-contract",
        policy_version="transition-policy",
        feature_version="transition-features",
        eligibility_version="transition-eligibility",
    )
    updates = [
        {"model_identity": "MARKET_ONLY", "model_version": "market-live"},
        {"model_identity": "NEWS_RESIDUAL", "model_version": "news-live",
         "feature_version": contract.feature_version,
         "eligibility_version": contract.eligibility_version},
        {"model_identity": "FULL", "model_version": "full-live",
         "feature_version": f"{inference_v2.FEATURE_VERSION}+{contract.feature_version}",
         "eligibility_version": contract.eligibility_version},
        {"model_identity": "BROAD_NEWS_RESIDUAL", "model_version": "broad-news-live",
         "feature_version": contract.feature_version,
         "eligibility_version": contract.eligibility_version},
        {"model_identity": "BROAD_FULL", "model_version": "broad-full-live",
         "feature_version": (
             f"{inference_v2.FEATURE_VERSION}+{contract.feature_version}"
             f"+{contract.policy_version}"
         ),
         "eligibility_version": contract.eligibility_version},
    ]

    active = inference_v2._active_updates(
        updates, {contract.eligibility_version},
        enforce_current_contract=False, news_contract=contract,
    )
    assert {row["model_version"] for row in active} == {
        "market-live", "news-live", "full-live", "broad-news-live", "broad-full-live",
    }
    statuses = inference_v2.news_model_activation_status(
        updates, allow_transition_contract=True, transition_contract=contract,
    )
    by_identity = {row["model_identity"]: row for row in statuses}
    assert {
        row["status"] for identity, row in by_identity.items()
        if identity != "NEWS_ONLY"
    } == {"TRANSITION_ACTIVE"}
    assert by_identity["NEWS_ONLY"]["status"] == "NOT_TRAINED"
    assert all(
        "整组切换" in row["reason"]
        for identity, row in by_identity.items() if identity != "NEWS_ONLY"
    )

    updates[-1]["feature_version"] = "corrupt-contract"
    blocked = inference_v2._active_updates(
        updates, {contract.eligibility_version},
        enforce_current_contract=False, news_contract=contract,
    )
    assert [row["model_version"] for row in blocked] == ["market-live"]


def test_news_exposure_flag_prevents_residual_without_visible_event() -> None:
    features = {"news_event_count": 1.0, "broad_news_event_count": 1.0}
    assert inference_v2._news_snapshot_exposed(
        "NEWS_RESIDUAL", {"news_exposed": 0}, features,
    ) is False
    assert inference_v2._news_snapshot_exposed(
        "BROAD_FULL", {"broad_news_exposed": 0}, features,
    ) is False
    assert inference_v2._news_snapshot_exposed(
        "NEWS_ONLY", {"broad_news_exposed": 0}, features,
    ) is False
    assert inference_v2._news_snapshot_exposed("FULL", {}, features) is True


def test_newest_news_policy_mismatch_blocks_older_current_model() -> None:
    current_feature = f"{inference_v2.FEATURE_VERSION}+{inference_v2.NEWS_FEATURE_VERSION}"
    updates = [
        {"model_identity": "FULL", "model_version": "new-but-incompatible",
         "feature_version": "obsolete-feature", "eligibility_version": "obsolete-policy"},
        {"model_identity": "FULL", "model_version": "old-current",
         "feature_version": current_feature,
         "eligibility_version": inference_v2.ELIGIBILITY_VERSION},
        {"model_identity": "MARKET_ONLY", "model_version": "market-current"},
    ]
    active = inference_v2._active_updates(updates)
    assert [row["model_version"] for row in active] == ["market-current"]


def test_news_activation_reports_policy_mismatch() -> None:
    statuses = inference_v2.news_model_activation_status([
        {"model_identity": "FULL", "model_version": "old-full",
         "feature_version": "old-feature", "eligibility_version": "old-policy"},
    ])
    full = next(row for row in statuses if row["model_identity"] == "FULL")
    assert full["status"] == "POLICY_MISMATCH"
    assert full["reason"] == "最新版不符合当前新闻规则"


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


def test_news_discovered_more_than_one_hour_late_uses_gemma_lifetime(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    first_seen = epoch + timedelta(hours=2)
    decision = first_seen + timedelta(minutes=5)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="federal_reserve_monetary", item="late-official",
        first_seen=first_seen, parsed_at=first_seen, published_at=epoch,
        impulse=1.0,
    )
    event = event_evidence_rows(ledger, decision)[0]
    assert event["broad_model_eligible"] is True
    assert event["freshness_status"] == "CURRENT_EVENT"
    assert event["impact_class"] == "POLICY_SHIFT"
    assert event["economic_age_minutes"] == pytest.approx(125.0)
    ledger.close()


def test_impact_assessment_cannot_retroactively_enter_earlier_decision(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    assessed = epoch + timedelta(minutes=10)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="federal_reserve_monetary", item="assessed-later",
        first_seen=epoch, parsed_at=epoch, published_at=epoch, impulse=1.0,
        impact_assessed_at=assessed,
    )

    before = event_evidence_rows(ledger, assessed - timedelta(seconds=1))[0]
    after = event_evidence_rows(ledger, assessed)[0]

    assert before["broad_model_eligible"] is False
    assert "IMPACT_NOT_ASSESSED" in before["reason_codes"]
    assert after["broad_model_eligible"] is True
    ledger.close()


def test_duplicate_report_cannot_extend_material_event_lifetime(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    common = {
        "source": "federal_reserve_monetary", "impulse": 1.0,
        "material_event_key": "same-policy-event", "impact_class": "SAME_DAY",
    }
    _append_news(
        ledger, item="initial-policy", first_seen=epoch, parsed_at=epoch,
        published_at=epoch, **common,
    )
    duplicate_time = epoch + timedelta(hours=3)
    _append_news(
        ledger, item="duplicate-policy", first_seen=duplicate_time,
        parsed_at=duplicate_time, published_at=duplicate_time,
        impact_update_type="DUPLICATE_REPORT", **common,
    )

    event = event_evidence_rows(ledger, duplicate_time + timedelta(minutes=1))[0]

    assert event["member_count"] == 2
    assert event["canonical_source_item_id"] == "initial-policy"
    assert event["event_occurred_at"] == epoch.isoformat()
    ledger.close()


def test_material_update_creates_new_version_without_new_event_budget(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    common = {
        "source": "federal_reserve_monetary", "impulse": 1.0,
        "material_event_key": "ongoing-policy-event", "impact_class": "POLICY_SHIFT",
    }
    _append_news(
        ledger, item="initial-event", first_seen=epoch, parsed_at=epoch,
        published_at=epoch, **common,
    )
    update_time = epoch + timedelta(hours=3)
    _append_news(
        ledger, item="update-event", first_seen=update_time, parsed_at=update_time,
        published_at=update_time, impact_update_type="MATERIAL_UPDATE", **common,
    )

    event = event_evidence_rows(ledger, update_time + timedelta(minutes=1))[0]

    assert len(event_evidence_rows(ledger, update_time + timedelta(minutes=1))) == 1
    assert event["canonical_source_item_id"] == "update-event"
    assert event["event_occurred_at"] == update_time.isoformat()
    ledger.close()


def test_event_budget_preserves_freshness_between_events() -> None:
    rows = [
        {"decision_id": "fresh-1", "official_events": [{
            "event_id": "fresh", "event_version_id": "fresh-v1",
            "raw_weight": 0.9,
        }]},
        {"decision_id": "fresh-2", "official_events": [{
            "event_id": "fresh", "event_version_id": "fresh-v1",
            "raw_weight": 0.8,
        }]},
        {"decision_id": "late-1", "official_events": [{
            "event_id": "late", "event_version_id": "late-v1",
            "raw_weight": 0.1,
        }]},
    ]

    weights, receipts, summary = training_v2._event_budget_weights(
        rows, "official_events"
    )
    event_totals = {
        event_id: sum(
            receipt["normalized_event_weight"]
            for receipt in receipts if receipt["event_id"] == event_id
        )
        for event_id in ("fresh", "late")
    }

    assert event_totals["fresh"] / event_totals["late"] == pytest.approx(9.0)
    assert weights[2] < weights[0]
    assert summary["maximum_event_weight_share"] == pytest.approx(0.9)


def test_single_reliable_training_rows_keep_absolute_35_percent_trust() -> None:
    rows = [
        {"decision_id": f"row-{index}", "broad_events": [{
            "event_id": "reliable", "event_version_id": "reliable-v1",
            "raw_weight": 0.35, "evidence_grade": "SINGLE_RELIABLE",
        }]}
        for index in range(3)
    ]

    weights, _, _ = training_v2._event_budget_weights(rows, "broad_events")

    assert weights.mean() == pytest.approx(0.35)


def test_commentary_and_low_materiality_are_not_training_evidence(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = epoch + timedelta(minutes=10)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="federal_reserve_monetary", item="policy-commentary",
        first_seen=epoch + timedelta(minutes=1), parsed_at=epoch + timedelta(minutes=2),
        impulse=0.2, record_kind="COMMENTARY_FORECAST",
        evidence_role="COMMENTARY", materiality=0.2,
    )
    event = event_evidence_rows(ledger, decision)[0]
    assert event["evidence_grade"] == "PRIMARY"
    assert event["broad_model_eligible"] is False
    assert "RECORD_KIND_NOT_ACTIONABLE" in event["reason_codes"]
    assert "EVIDENCE_ROLE_NOT_ACTIONABLE" in event["reason_codes"]
    assert "LOW_MATERIALITY" in event["reason_codes"]
    ledger.close()


def test_residual_model_keeps_research_direction_visible(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "visible-residual.sqlite3")
    now = datetime(2026, 8, 10, tzinfo=UTC)
    calibration = {"version": "early", "rows": 30, "blocks": 1, "days": 1,
                   "half_width": 0.2, "status": "EARLY"}
    inference_v2._insert_prediction(
        ledger, decision_id="residual", decision_time=now, created_at=now,
        model_version="broad-news", model_identity="BROAD_NEWS_RESIDUAL",
        feature_hash="features",
        predicted=0.3, news_residual=0.2, ev_long=0.25, ev_short=-0.35,
        calibration=calibration, recommended="LONG",
        status="RESEARCH_RESIDUAL_DIRECTION",
    )

    row = ledger.connection.execute(
        "SELECT recommended_action,ev_long_u5,ev_short_u5,prediction_status "
        "FROM predictions_v2"
    ).fetchone()
    assert row["recommended_action"] == "LONG"
    assert row["ev_long_u5"] == pytest.approx(0.25)
    assert row["prediction_status"] == "RESEARCH_RESIDUAL_DIRECTION"
    ledger.close()


def test_shadow_composite_keeps_research_direction_visible(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "visible-direction.sqlite3")
    now = datetime(2026, 8, 10, tzinfo=UTC)
    calibration = {"version": "early", "rows": 30, "blocks": 1, "days": 1,
                   "half_width": 0.2, "status": "EARLY"}
    inference_v2._insert_prediction(
        ledger, decision_id="visible", decision_time=now, created_at=now,
        model_version="full", model_identity="FULL", feature_hash="features",
        predicted=0.3, news_residual=0.2, ev_long=0.25, ev_short=-0.35,
        calibration=calibration, recommended="LONG",
        status="PROVISIONAL_POST_COST_EV",
    )

    row = ledger.connection.execute(
        "SELECT recommended_action,effective_action,prediction_status "
        "FROM predictions_v2"
    ).fetchone()
    assert row["recommended_action"] == "LONG"
    assert row["effective_action"] == "WAIT"
    assert row["prediction_status"] == "PROVISIONAL_POST_COST_EV"
    assert not hasattr(inference_v2, "_news_reference_gate")
    ledger.close()


def test_controlled_news_semantics_do_not_reclassify_by_substring() -> None:
    annotation = {
        "record_kind": "FACT_EVENT",
        "primary_category": "rates_fed",
        "secondary_categories": ["growth_economy"],
    }

    assert effective_record_kind(annotation) == "FACT_EVENT"
    assert effective_record_kind(
        annotation, "forward guidance argues against a cut"
    ) == "FACT_EVENT"
    assert effective_record_kind(
        annotation, "Gold gains as Treasury yields fall"
    ) == "MARKET_REACTION"
    assert annotation_topics(annotation) == ("rates_fed", "growth_economy")
    # These used to trigger substring bugs: war in forward and gain in against.
    assert annotation_topics({
        **annotation, "headline_zh": "forward guidance argues against a cut",
    }) == ("rates_fed", "growth_economy")


def test_market_wrap_is_display_only_even_when_llm_calls_it_fact(tmp_path) -> None:
    epoch = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward-market-wrap.sqlite3", now=epoch)
    _append_news(
        ledger, source="google_news_fed_rates", item="Gold gains as Treasury yields fall",
        first_seen=epoch, parsed_at=epoch + timedelta(seconds=30), impulse=0.2,
        link="https://finance.yahoo.com/example", primary_category="rates_fed",
        record_kind="FACT_EVENT", materiality=0.8,
    )

    row = event_evidence_rows(ledger, epoch + timedelta(minutes=5))[0]

    assert row["record_kind"] == "MARKET_REACTION"
    assert row["broad_model_eligible"] is False
    assert "RECORD_KIND_NOT_ACTIONABLE" in row["reason_codes"]
    ledger.close()


def test_current_material_annotation_rejects_unknown_record_kind(tmp_path) -> None:
    epoch = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "invalid-record-kind.sqlite3", now=epoch)
    with pytest.raises(ValueError, match="record_kind is not controlled"):
        _append_news(
            ledger, source="google_news_fed_rates", item="official release",
            first_seen=epoch, parsed_at=epoch + timedelta(seconds=30), impulse=0.2,
            record_kind="RESPONSE",
        )
    ledger.close()


@pytest.mark.parametrize(
    ("field", "value"),
    (("document_kind", "NOT_A_KIND"), ("evidence_role", "NOT_A_ROLE")),
)
def test_current_material_annotation_rejects_unknown_schema_enum(
    tmp_path, field: str, value: str,
) -> None:
    epoch = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / f"invalid-{field}.sqlite3", now=epoch)
    with pytest.raises(ValueError, match=rf"{field} is not controlled"):
        _append_news(
            ledger, source="google_news_fed_rates", item=f"invalid {field}",
            first_seen=epoch, parsed_at=epoch + timedelta(seconds=30), impulse=0.2,
            annotation_overrides={field: value},
        )
    ledger.close()


def test_material_event_key_deduplicates_different_headlines(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 23, 55, tzinfo=UTC)
    decision = epoch + timedelta(minutes=20)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    for offset, source, item in (
        (1, "gdelt_gold_geopolitics", "Oil jumps after shipping disruption"),
        (10, "google_news_gold_context", "Vessel incident lifts crude and gold"),
    ):
        _append_news(
            ledger, source=source, item=item,
            first_seen=epoch + timedelta(minutes=offset),
            parsed_at=epoch + timedelta(minutes=offset + 1), impulse=0.0,
            primary_category="oil_energy",
            material_event_key="hormuz_shipping_incident_20260805",
        )
    events = event_evidence_rows(ledger, decision)
    assert len(events) == 1
    assert events[0]["member_count"] == 2
    ledger.close()


def test_canonical_occurrence_deduplicates_alias_keys_for_model(tmp_path) -> None:
    epoch = datetime(2026, 8, 7, 17, 30, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    rows = (
        ("gdelt_gold_geopolitics", "Trump renews bid to fire Lisa Cook",
         "https://cnbc.com/a", "cnbc", "donald_trump", "OFFICIAL_STATEMENT",
         "trump_fires_lisa_cook_2026_08", "cook-dismissal-effort"),
        ("google_news_gold_context", "Trump again attempts to remove Lisa Cook",
         "https://theguardian.com/b", "the_guardian", "donald_trump", "POLICY_DECISION",
         "trump_fire_cook_aug_2026", "cook-firing-push"),
        ("gdelt_gold_context", "White House pushes Lisa Cook removal",
         "https://npr.org/c", "npr", "white_house", "REGULATORY_ACTION",
         "trump_fires_lisa_cook_aug_2026", "cook-removal-attempt"),
    )
    for offset, row in enumerate(rows, start=1):
        source, item, link, organization, actor, action, episode, material = row
        _append_news(
            ledger, source=source, item=item, link=link,
            first_seen=epoch + timedelta(minutes=offset),
            parsed_at=epoch + timedelta(minutes=offset + 1), impulse=0.0,
            primary_category="regulation_other",
            source_organization_id=organization,
            material_event_key=material,
            annotation_overrides={
                "actor": actor, "canonical_actor_id": actor,
                "action": "renews removal attempt", "action_family": action,
                "object": "Lisa Cook", "canonical_object_id": "lisa_cook",
                "episode_key": episode, "relation_to_prior": "ESCALATES",
            },
        )
    events = event_evidence_rows(ledger, epoch + timedelta(minutes=20))
    assert len(events) == 1
    assert events[0]["member_count"] == 3
    assert events[0]["independent_publishers"] == 3
    ledger.close()


def test_source_identity_is_visible_without_granting_reliability(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="gdelt_gold_geopolitics", item="publisher identity",
        link="https://news.example.test/item", first_seen=epoch,
        parsed_at=epoch + timedelta(minutes=1), impulse=0.0,
        primary_category="rates_fed", source_organization_id="Example Media",
    )
    event = event_evidence_rows(ledger, epoch + timedelta(minutes=10))[0]
    assert event["source_identity_organizations"] == ["example_media"]
    assert event["source_organizations"] == []
    assert event["independent_publishers"] == 0
    ledger.close()


def test_reliable_media_publication_time_is_safe_event_clock_proxy(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    for source, item, link in (
        ("gdelt_gold_geopolitics", "event-a", "https://apnews.com/a"),
        ("google_news_gold_context", "event-b", "https://reuters.com/b"),
    ):
        _append_news(
            ledger, source=source, item=item, link=link,
            first_seen=epoch + timedelta(minutes=1),
            parsed_at=epoch + timedelta(minutes=2), impulse=0.0,
            primary_category="war_geopolitics", material_event_key="same-event",
            event_time="",
        )
    event = event_evidence_rows(ledger, epoch + timedelta(minutes=10))[0]
    assert event["evidence_grade"] == "CORROBORATED"
    assert event["broad_model_eligible"] is True
    assert event["event_clock_source"] == "SOURCE_STRUCTURED_TIME"
    assert event["event_time_precision"] == "TIMESTAMP"
    assert "RELIABLE_PUBLISHER_TIME_PROXY" in event["reason_codes"]
    assert "EVENT_TIME_INVALID" not in event["reason_codes"]


def test_terminal_429_impact_failure_reenters_pending_queue(tmp_path) -> None:
    epoch = datetime(2026, 8, 7, 12, 30, tzinfo=UTC)
    item = "The Employment Situation - July 2026"
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="google_news_bls_official_releases", item=item,
        link="https://www.bls.gov/news.release/empsit.nr0.htm",
        first_seen=epoch + timedelta(days=1),
        parsed_at=epoch + timedelta(days=1, minutes=1),
        published_at=epoch, impulse=0.8,
        primary_category="inflation_employment", include_impact=False,
    )
    ledger.append_news_impact_failure({
        "failure_id": "terminal-429", "source": "google_news_bls_official_releases",
        "source_item_id": item, "revision_number": 1,
        "raw_content_hash": ledger.connection.execute(
            "SELECT content_hash FROM news_revisions"
        ).fetchone()[0],
        "annotation_id": item, "llm_model_version": "gemma-4-31b-it",
        "prompt_version": "news-impact-v2-semantic-prior-candidates",
        "attempt_number": 5, "error_type": "HTTPError",
        "error_signature": "quota", "error": "HTTP Error 429: Too Many Requests",
        "failed_at": epoch + timedelta(days=1, minutes=2),
        "next_retry_at": None, "is_terminal": True,
    })

    pending = pending_impact_records(
        ledger.connection, observed_at=epoch + timedelta(days=2), limit=10,
    )

    assert [row["source_item_id"] for row in pending] == [item]
    ledger.close()


def test_official_release_timestamp_is_valid_event_clock(tmp_path) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    _append_news(
        ledger, source="federal_reserve_monetary", item="official-release",
        first_seen=epoch, parsed_at=epoch, impulse=0.3, event_time="",
    )
    event = event_evidence_rows(ledger, epoch + timedelta(minutes=1))[0]
    assert event["official_model_eligible"] is True
    assert event["event_clock_source"] == "OFFICIAL_RELEASE_TIME"
