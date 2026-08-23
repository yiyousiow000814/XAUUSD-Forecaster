from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from xauusd_forecaster.evidence.schema import (
    ELIGIBILITY_VERSION,
    V2_SCHEMA,
    install_v2_schema,
)
from xauusd_forecaster.evidence.executable_label import build_executable_label_v2
from xauusd_forecaster.execution_costs import net_shadow_log_return
from xauusd_forecaster.evidence.ledger import ForwardLedger, canonical_hash
from xauusd_forecaster.learning_curves import _bounded_curve, _stage, learning_curve_payload
from xauusd_forecaster.decision.live import (
    _append_news_visibility_receipts,
    append_live_decision_v2,
    append_live_outcome_v2,
)
from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.macro_release import (
    macro_release_features_at,
    macro_release_packets_at,
)
from xauusd_forecaster.news_evidence import EVIDENCE_POLICY_VERSION, event_evidence_rows
from xauusd_forecaster.news_identity import canonical_source_organization
from xauusd_forecaster.news_contracts import (
    CURRENT_NEWS_CONTRACT,
    NewsContract,
)
from xauusd_forecaster.news_features_v2 import (
    aggregate_news_features_v2,
    event_raw_weight,
)
from xauusd_forecaster.news_impact import impact_time_rule, pending_impact_records
from xauusd_forecaster.news_source_registry import NEWS_SOURCE_REGISTRY
from xauusd_forecaster.news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
    annotation_topics,
    effective_record_kind,
)
from xauusd_forecaster.news_time import assess_news_time, category_time_rule
from xauusd_forecaster.repair_v2 import immutable_table_hash
from xauusd_forecaster.decision import inference as inference_v2
from xauusd_forecaster import (
    execution_learning, news_contract_migration, training_v2,
)
from xauusd_forecaster.u5_state import U5State, U5_VERSION
from xauusd_forecaster.execution_learning import (
    EXECUTION_CHART_MAX_POINTS, LOT_FEATURES, EXIT_FEATURES,
    _bounded_execution_curve, append_due_exit_predictions,
    append_execution_examples, append_lot_predictions, execution_learning_status,
    score_execution_predictions, train_due_execution,
)
from xauusd_forecaster.training import MARKET_FEATURES


def _append_materializable_training_row(
    ledger, decision_id: str, decision_time: datetime,
) -> None:
    market = {name: float(index + 1) for index, name in enumerate(MARKET_FEATURES)}
    news = {
        name: 0.0 for name in (*training_v2.NEWS_FEATURES,
                              *training_v2.BROAD_MODEL_FEATURES)
    }
    market_hash = canonical_hash((decision_id, "market"))
    news_hash = canonical_hash((decision_id, "news"))
    outcome_hash = canonical_hash((decision_id, "outcome"))
    connection = ledger.connection
    connection.execute(
        """INSERT INTO derived_market_snapshots VALUES
        (?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)""",
        (f"market-{decision_id}", decision_id, decision_time.isoformat(),
         market_hash, "LIVE_OOS", decision_time.isoformat(),
         training_v2.FEATURE_VERSION, "u5-test", 1.0, json.dumps(market),
         "OK", "[]", market_hash, market_hash),
    )
    connection.execute(
        """INSERT INTO derived_news_feature_snapshots VALUES
        (?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"news-{decision_id}", decision_id, decision_time.isoformat(),
         "LIVE_OOS", decision_time.isoformat(), training_v2.NEWS_FEATURE_VERSION,
         training_v2.ELIGIBILITY_VERSION, json.dumps(news), 0, 0, 0, 0,
         news_hash, news_hash),
    )
    connection.execute(
        """INSERT INTO derived_outcomes (
        derived_outcome_id,source_decision_id,decision_time,evidence_lane,
        recomputed_at,label_version,outcome_status,reason_codes_json,
        ambiguity_state,gross_midpoint_direction_move,long_quote_return,
        short_quote_return,commission_status,slippage_status,
        source_evidence_hash,output_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"outcome-{decision_id}", decision_id, decision_time.isoformat(),
         "LIVE_OOS", decision_time.isoformat(), training_v2.LABEL_VERSION,
         "VALID", "[]", "NONE", 0.1, 0.1, -0.1, "UNCONFIGURED",
         "UNAVAILABLE_SHADOW", outcome_hash, outcome_hash),
    )
    connection.execute(
        "INSERT INTO training_eligibility_v2 VALUES (?,?,?,?,?,?,?,?)",
        (f"eligibility-{decision_id}", decision_id, "LIVE_OOS",
         decision_time.isoformat(), training_v2.ELIGIBILITY_VERSION,
         market_hash, outcome_hash, news_hash),
    )
    connection.commit()


def _append_training_event(
    ledger, decision_id: str, decision_time: datetime, suffix: str,
) -> None:
    version_id = f"event-version-{suffix}"
    event_id = f"event-{suffix}"
    ledger.connection.execute(
        "INSERT INTO news_event_catalog_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            version_id, event_id, EVIDENCE_POLICY_VERSION,
            (decision_time - timedelta(minutes=10)).isoformat(),
            "OFFICIAL_RELEASE_TIME", "TIMESTAMP", "official-source",
            f"source-{suffix}", f"source-hash-{suffix}", "A",
            '["OFFICIAL_MODEL"]', "[]", decision_time.isoformat(),
        ),
    )
    ledger.connection.execute(
        "INSERT INTO news_event_source_budgets_v1 VALUES (?,?,?,?)",
        (version_id, f"budget-{suffix}", "REPORTING_ORGANIZATION",
         decision_time.isoformat()),
    )
    ledger.connection.execute(
        "INSERT INTO news_decision_event_snapshots_v1 VALUES (?,?,?,?,?,?,?,?,?)",
        (decision_id, decision_time.isoformat(), event_id, version_id,
         EVIDENCE_POLICY_VERSION, "OFFICIAL_MODEL", 0.75, 10.0,
         f"snapshot-{suffix}"),
    )
    ledger.connection.commit()


def test_training_rows_materialize_incrementally_without_rescanning_history(
    tmp_path, monkeypatch,
) -> None:
    cutoff = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    ledger = ForwardLedger(tmp_path / "incremental-training.sqlite3")
    _append_materializable_training_row(
        ledger, "decision-0", cutoff - timedelta(hours=1)
    )
    calls: list[list[str] | None] = []
    original = training_v2._build_training_rows

    def observed_builder(owner, at, source_ids=None):
        calls.append(source_ids)
        return original(owner, at, source_ids)

    monkeypatch.setattr(training_v2, "_build_training_rows", observed_builder)
    first = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert first["materialization_mode"] == "FULL"
    assert first["processed_source_rows"] == 1
    assert calls == [None]

    unchanged = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert unchanged["materialization_mode"] == "NO_CHANGE"
    assert unchanged["processed_source_rows"] == 0
    assert calls == [None]

    _append_materializable_training_row(
        ledger, "decision-1", cutoff - timedelta(minutes=55)
    )
    one = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert one["materialization_mode"] == "INCREMENTAL"
    assert one["processed_source_rows"] == 1
    assert calls[-1] == ["decision-1"]

    for index in range(2, 52):
        _append_materializable_training_row(
            ledger, f"decision-{index}",
            cutoff - timedelta(minutes=50) + timedelta(seconds=index)
        )
    fifty = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert fifty["processed_source_rows"] == 50
    assert len(calls[-1]) == 50
    assert fifty["row_count"] == 52
    assert len(training_v2.complete_training_rows(ledger, cutoff)) == 52
    ledger.close()


def test_existing_dirty_queue_upgrades_to_revisioned_acknowledgement(tmp_path) -> None:
    cutoff = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    ledger = ForwardLedger(tmp_path / "dirty-revision-upgrade.sqlite3")
    _append_materializable_training_row(
        ledger, "decision-upgrade", cutoff - timedelta(minutes=10),
    )
    training_v2.refresh_training_materialization_state(ledger, cutoff)
    with ledger.connection:
        ledger.connection.execute("DROP TABLE training_materialization_dirty_v1")
        ledger.connection.execute(
            """CREATE TABLE training_materialization_dirty_v1 (
                source_decision_id TEXT PRIMARY KEY,
                source_table TEXT NOT NULL,
                change_kind TEXT NOT NULL,
                dirty_at TEXT NOT NULL
            )"""
        )
    result = training_v2.refresh_training_materialization_state(ledger, cutoff)
    columns = {
        row[1] for row in ledger.connection.execute(
            "PRAGMA table_info(training_materialization_dirty_v1)"
        )
    }
    assert result["materialization_mode"] == "NO_CHANGE"
    assert "dirty_revision" in columns
    ledger.close()


def test_training_materialization_rebuilds_late_rows_and_corruption(
    tmp_path, monkeypatch,
) -> None:
    cutoff = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    ledger = ForwardLedger(tmp_path / "dirty-training.sqlite3")
    _append_materializable_training_row(
        ledger, "decision-tail", cutoff - timedelta(minutes=5)
    )
    initial = training_v2.refresh_training_materialization_state(ledger, cutoff)
    authoritative = training_v2._build_training_rows(ledger, cutoff)
    assert training_v2.complete_training_rows(ledger, cutoff) == authoritative

    _append_materializable_training_row(
        ledger, "decision-late", cutoff - timedelta(hours=2)
    )
    late = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert late["materialization_mode"] == "FULL"
    assert late["rebuild_generation"] == initial["rebuild_generation"] + 1

    ledger.connection.execute(
        """UPDATE materialized_training_rows_v1 SET row_json='{}'
            WHERE source_decision_id='decision-tail'"""
    )
    ledger.connection.commit()
    repaired = training_v2.complete_training_rows(ledger, cutoff)
    assert repaired == training_v2._build_training_rows(ledger, cutoff)
    state = ledger.connection.execute(
        "SELECT state,rebuild_generation FROM training_materialization_state_v1"
    ).fetchone()
    assert tuple(state) == ("CLEAN", late["rebuild_generation"] + 1)

    old_contract = training_v2.TRAINING_MATERIALIZATION_CONTRACT
    monkeypatch.setattr(
        training_v2, "TRAINING_MATERIALIZATION_CONTRACT",
        canonical_hash((old_contract, "next-contract")),
    )
    contract = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert contract["materialization_mode"] == "FULL"
    assert contract["rebuild_generation"] == state["rebuild_generation"] + 1
    ledger.close()


def test_incremental_materialization_failure_preserves_prior_rows(
    tmp_path, monkeypatch,
) -> None:
    cutoff = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    ledger = ForwardLedger(tmp_path / "atomic-training.sqlite3")
    _append_materializable_training_row(
        ledger, "decision-0", cutoff - timedelta(minutes=10)
    )
    training_v2.refresh_training_materialization_state(ledger, cutoff)
    before = ledger.connection.execute(
        "SELECT source_decision_id,row_json FROM materialized_training_rows_v1"
    ).fetchall()
    _append_materializable_training_row(
        ledger, "decision-1", cutoff - timedelta(minutes=5)
    )

    def crash_before_commit(*_args):
        raise RuntimeError("injected materialization failure")

    monkeypatch.setattr(training_v2, "_persisted_training_row", crash_before_commit)
    with pytest.raises(RuntimeError, match="injected materialization failure"):
        training_v2.refresh_training_materialization_state(ledger, cutoff)
    after = ledger.connection.execute(
        "SELECT source_decision_id,row_json FROM materialized_training_rows_v1"
    ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert ledger.connection.execute(
        "SELECT count(*) FROM training_materialization_dirty_v1"
    ).fetchone()[0] == 1
    ledger.close()


def test_incremental_materialization_preserves_newer_dirty_revision(
    tmp_path, monkeypatch,
) -> None:
    cutoff = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    path = tmp_path / "incremental-race.sqlite3"
    ledger = ForwardLedger(path)
    _append_materializable_training_row(
        ledger, "decision-race", cutoff - timedelta(minutes=10),
    )
    training_v2.refresh_training_materialization_state(ledger, cutoff)
    _append_training_event(
        ledger, "decision-race", cutoff - timedelta(minutes=10), "first",
    )

    original = training_v2._build_training_rows
    raced = False

    def build_then_mutate(owner, at, source_ids=None):
        nonlocal raced
        rows = original(owner, at, source_ids)
        if not raced:
            raced = True
            concurrent = ForwardLedger(path)
            _append_training_event(
                concurrent, "decision-race", cutoff - timedelta(minutes=10),
                "second",
            )
            concurrent.close()
        return rows

    monkeypatch.setattr(training_v2, "_build_training_rows", build_then_mutate)
    stale = training_v2.refresh_training_materialization_state(ledger, cutoff)
    pending = ledger.connection.execute(
        "SELECT dirty_revision FROM training_materialization_dirty_v1 "
        "WHERE source_decision_id='decision-race'"
    ).fetchone()
    assert stale["materialization_mode"] == "INCREMENTAL"
    assert pending is not None and pending[0] == 2

    newest = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert newest["materialization_mode"] == "INCREMENTAL"
    assert ledger.connection.execute(
        "SELECT count(*) FROM training_materialization_dirty_v1"
    ).fetchone()[0] == 0
    assert training_v2.complete_training_rows(ledger, cutoff) == original(
        ledger, cutoff,
    )
    ledger.close()


def test_full_materialization_preserves_changes_arriving_during_rebuild(
    tmp_path, monkeypatch,
) -> None:
    cutoff = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    path = tmp_path / "full-race.sqlite3"
    ledger = ForwardLedger(path)
    _append_materializable_training_row(
        ledger, "decision-race", cutoff - timedelta(minutes=10),
    )
    training_v2.refresh_training_materialization_state(ledger, cutoff)
    _append_training_event(
        ledger, "decision-race", cutoff - timedelta(minutes=10), "first",
    )
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE training_materialization_state_v1 SET state='DIRTY' WHERE id=1"
        )

    original = training_v2._build_training_rows
    raced = False

    def build_then_mutate(owner, at, source_ids=None):
        nonlocal raced
        rows = original(owner, at, source_ids)
        if not raced and source_ids is None:
            raced = True
            concurrent = ForwardLedger(path)
            _append_training_event(
                concurrent, "decision-race", cutoff - timedelta(minutes=10),
                "second",
            )
            concurrent.close()
        return rows

    monkeypatch.setattr(training_v2, "_build_training_rows", build_then_mutate)
    rebuilt = training_v2.refresh_training_materialization_state(ledger, cutoff)
    pending = ledger.connection.execute(
        "SELECT dirty_revision FROM training_materialization_dirty_v1 "
        "WHERE source_decision_id='decision-race'"
    ).fetchone()
    assert rebuilt["materialization_mode"] == "FULL"
    assert pending is not None and pending[0] == 2

    repaired = training_v2.refresh_training_materialization_state(ledger, cutoff)
    assert repaired["materialization_mode"] == "INCREMENTAL"
    assert training_v2.complete_training_rows(ledger, cutoff) == original(
        ledger, cutoff,
    )
    ledger.close()


def test_materialized_training_row_preserves_exact_event_and_dataset_evidence(
    tmp_path,
) -> None:
    decision_time = datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc)
    cutoff = decision_time + timedelta(hours=1)
    ledger = ForwardLedger(tmp_path / "event-materialization.sqlite3")
    _append_materializable_training_row(ledger, "decision-event", decision_time)
    event = (
        "event-version", "event", EVIDENCE_POLICY_VERSION,
        (decision_time - timedelta(minutes=10)).isoformat(),
        "OFFICIAL_RELEASE_TIME", "TIMESTAMP", "official-source",
        "source-item", "source-hash", "A", '["OFFICIAL_MODEL"]', "[]",
        decision_time.isoformat(),
    )
    ledger.connection.execute(
        "INSERT INTO news_event_catalog_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        event,
    )
    ledger.connection.execute(
        "INSERT INTO news_event_source_budgets_v1 VALUES (?,?,?,?)",
        ("event-version", "budget-official", "REPORTING_ORGANIZATION",
         decision_time.isoformat()),
    )
    ledger.connection.execute(
        "INSERT INTO news_decision_event_snapshots_v1 VALUES (?,?,?,?,?,?,?,?,?)",
        ("decision-event", decision_time.isoformat(), "event", "event-version",
         EVIDENCE_POLICY_VERSION, "OFFICIAL_MODEL", 0.75, 10.0,
         "snapshot-hash"),
    )
    ledger.connection.commit()

    authoritative = training_v2._build_training_rows(ledger, cutoff)
    materialized = training_v2.complete_training_rows(ledger, cutoff)
    assert materialized == authoritative
    assert canonical_hash([row["receipt"] for row in materialized]) == canonical_hash(
        [row["receipt"] for row in authoritative]
    )
    assert materialized[0]["core_events"] == [{
        "source_decision_id": "decision-event", "event_id": "event",
        "event_version_id": "event-version", "model_permission": "OFFICIAL_MODEL",
        "raw_weight": 0.75,
        "event_occurred_at": (decision_time - timedelta(minutes=10)).isoformat(),
        "event_clock_source": "OFFICIAL_RELEASE_TIME",
        "event_time_precision": "TIMESTAMP", "evidence_grade": "A",
        "source_budget_id": "budget-official",
    }]
    ledger.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("federal_reserve_monetary", "federal_reserve"),
        ("federal_reserve_speeches_testimony", "federal_reserve"),
        ("bls_consumer_price_index", "bureau_of_labor_statistics"),
        ("google_news_bls_official_releases", "bureau_of_labor_statistics"),
        ("finance.yahoo.com", "yahoo_finance"),
        ("kitco_news", "kitco"),
        ("bitcoinworld", "bitcoin_world"),
    ],
)
def test_reporting_source_aliases_share_one_identity(raw: str, expected: str) -> None:
    assert canonical_source_organization(raw) == expected


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


def test_execution_collecting_gate_does_not_materialize_rows(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    monkeypatch.setattr(
        execution_learning, "_training_rows",
        lambda *_: pytest.fail("COLLECTING must not materialize execution rows"),
    )
    statuses = train_due_execution(
        ledger, datetime.now(UTC), tmp_path / "execution-models",
    )
    assert {row["status"] for row in statuses} == {"COLLECTING"}
    ledger.close()


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


def test_execution_ridges_follow_one_frozen_live_direction(tmp_path, monkeypatch) -> None:
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
    with monkeypatch.context() as due_gate:
        due_gate.setattr(
            execution_learning, "_training_rows",
            lambda *_: pytest.fail("NOT_DUE must not materialize execution rows"),
        )
        assert {row["status"] for row in train_due_execution(
            ledger, start + timedelta(days=1), tmp_path / "execution-models"
        )} == {"NOT_DUE"}
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
                 identity_relation: str | None = "NEW_EPISODE",
                 canonical_episode_id: str | None = None,
                 canonical_event_id: str | None = None,
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
        "headline_zh": "测试新闻",
        "summary_zh": "完整正文显示这是用于证据合同测试的新闻事件。",
        "primary_category": primary_category,
        "secondary_categories": [], "emerging_topic_zh": "",
        "record_kind": record_kind,
        "actor": entities[0] if entities else "official source",
        "action": "reported",
        "object": entities[1] if len(entities) > 1 else (material_event_key or item),
        "location": "",
        "event_time": (
            (published_at or first_seen).isoformat()
            if event_time == "__DEFAULT__" else event_time
        ),
        "claim_status": "CONFIRMED",
        "materiality": materiality,
        "canonical_actor_id": "official_source",
        "action_family": "OTHER_FACT",
        "canonical_object_id": (
            entities[1] if len(entities) > 1 else (material_event_key or item)
        ),
        "canonical_location_id": "",
        "episode_key": "",
        "primary_story_title_zh": "测试新闻",
        "secondary_contexts_zh": [],
        "relation_to_prior": "NONE",
        "document_kind": "NEWS_REPORT",
        "material_event_key": material_event_key,
        "source_organization_id": source_organization_id or source,
        "evidence_role": evidence_role,
        "xauusd_relevance": (
            "IRRELEVANT" if primary_category == "regulation_other" else "MACRO_DRIVER"
        ),
        "review_priority": "FAST",
        "material_change": impact_update_type,
        "time_sensitivity": "SAME_DAY",
        "semantic_reason_zh": "完整正文显示这是可能影响黄金的宏观事件。",
        "supporting_evidence": ["publisher full body"],
    }
    annotation.update(annotation_overrides or {})
    ledger.append_annotation({
        "annotation_id": item, "source": source, "source_item_id": item,
        "revision_number": 1, "raw_content_hash": digest, "event_type": event_type,
        "entities": entities, "hawkishness": impulse, "inflation_impulse": 0.0,
        "growth_impulse": 0.0, "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 1.0, "confidence": 1.0, "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
        "parse_started_at": parsed_at, "parsed_at": parsed_at,
        "annotation": annotation,
    })
    if include_impact:
        assessed_at = impact_assessed_at or parsed_at
        identity_anchor = material_event_key or "::".join(entities) or item
        impact_record = {
            "assessment_id": f"impact:{source}:{item}",
            "source": source, "source_item_id": item, "revision_number": 1,
            "raw_content_hash": digest, "annotation_id": item,
            "llm_model_version": "gemma-4-31b-it",
            "prompt_version": "news-impact-v3-independent-semantic-review",
            "parse_started_at": assessed_at, "assessed_at": assessed_at,
            "impact_class": impact_class,
            "event_state": "ACTIVE" if impact_class != "BACKGROUND" else "BACKGROUND",
            "update_type": impact_update_type,
            "confidence": 1.0, "reason_zh": "测试中的固定影响寿命判断。",
        }
        if identity_relation is not None:
            impact_record.update({
                "resolution_id": f"resolution:{source}:{item}",
                "identity_relation": identity_relation,
                "identity_anchor_zh": "测试中的固定现实事件身份。",
                "core_fact_changes_zh": [],
                "identity_differences_zh": (
                    ["测试中的独立现实事件。"]
                    if identity_relation == "NEW_EPISODE" else []
                ),
                "context_differences_zh": [],
                "canonical_episode_id": (
                    canonical_episode_id or f"test-episode:{identity_anchor}"
                ),
                "canonical_event_id": (
                    canonical_event_id or f"test-event:{identity_anchor}"
                ),
            })
        ledger.append_news_impact_assessment(impact_record)


def test_event_evidence_groups_only_by_persisted_canonical_identity(tmp_path) -> None:
    first_seen = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first_seen)
    shared = {
        "canonical_episode_id": "semantic-episode-july-jobs",
        "canonical_event_id": "semantic-event-july-jobs-release",
    }
    _append_news(
        ledger, source="bls_employment_situation", item="official-jobs",
        first_seen=first_seen, parsed_at=first_seen, impulse=0.4,
        material_event_key="us_july_2026_jobs_report_release",
        identity_relation="NEW_EPISODE", **shared,
    )
    _append_news(
        ledger, source="google_news_gold_context", item="publisher-jobs",
        first_seen=first_seen + timedelta(minutes=1),
        parsed_at=first_seen + timedelta(minutes=1), impulse=0.4,
        material_event_key="july_2026_us_jobs_report_release",
        impact_update_type="DUPLICATE_REPORT",
        identity_relation="SAME_EVENT", **shared,
    )

    events = event_evidence_rows(
        ledger, first_seen + timedelta(minutes=5),
    )
    matching = [
        event for event in events
        if event["resolved_event_id"] == shared["canonical_event_id"]
    ]

    assert len(matching) == 1
    assert matching[0]["member_count"] == 2
    assert matching[0]["identity_status"] == "RESOLVED"
    assert matching[0]["broad_model_eligible"] is True


@pytest.mark.parametrize(
    ("relation", "expected_status", "reason"),
    [
        ("UNRESOLVED", "UNRESOLVED", "IDENTITY_UNRESOLVED"),
        (None, "MISSING", "IDENTITY_NOT_RESOLVED"),
    ],
)
def test_unresolved_or_missing_identity_is_display_only(
    tmp_path, relation, expected_status, reason,
) -> None:
    first_seen = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=first_seen)
    _append_news(
        ledger, source="bls_employment_situation", item="identity-pending",
        first_seen=first_seen, parsed_at=first_seen, impulse=0.4,
        material_event_key="us_july_2026_jobs_report_release",
        identity_relation=relation,
    )

    event = event_evidence_rows(
        ledger, first_seen + timedelta(minutes=5),
    )[0]

    assert event["identity_status"] == expected_status
    assert event["broad_model_eligible"] is False
    assert event["core_model_eligible"] is False
    assert event["model_permission"] == "DISPLAY_ONLY"
    assert reason in event["reason_codes"]


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


def test_bounded_publication_skew_never_advances_visibility_before_receipt() -> None:
    first_seen = datetime(2026, 8, 19, 15, 51, 15, 685775, tzinfo=UTC)
    published = first_seen + timedelta(seconds=2.314225)

    timing = assess_news_time(
        {
            "source_published_time": published,
            "collector_first_seen_time": first_seen,
        },
        decision_time=first_seen - timedelta(microseconds=1),
        forward_epoch=first_seen - timedelta(days=1),
    )

    assert timing.eligible is False
    assert timing.reason_code == "NOT_YET_VISIBLE"


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
    assert "SINGLE_SOURCE_ATTRIBUTE" in event["reason_codes"]
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


def test_identified_single_publisher_is_candidate_not_source_banned(tmp_path) -> None:
    cutoff = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=cutoff - timedelta(hours=1))
    _append_news(
        ledger, source="gdelt_gold_geopolitics", item="Regional port is closed",
        first_seen=cutoff - timedelta(minutes=10), parsed_at=cutoff - timedelta(minutes=5),
        impulse=0.2, link="https://regional-example.test/report/1",
        source_organization_id="regional-example", entities=["Port", "closure"],
        event_type="geopolitical_conflict",
    )

    event = event_evidence_rows(ledger, cutoff)[0]
    features = aggregate_news_features_v2(ledger, cutoff)["features"]

    assert event["evidence_grade"] == "SINGLE_SOURCE"
    assert event["broad_model_eligible"] is True
    assert event["source_reliability"] == pytest.approx(0.35)
    assert event["independent_publishers"] == 1
    assert features["broad_single_source_event_count"] > 0
    assert features["broad_source_reliability"] > 0
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
    features = aggregate_news_features_v2(ledger, cutoff)["features"]
    assert features["broad_independent_source_count"] > 1.0
    assert features["broad_corroborated_event_count"] > 0
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
    assert event["syndicated_duplicate_count"] == 1
    assert event["source_organizations"] == ["reuters"]
    features = aggregate_news_features_v2(ledger, cutoff)["features"]
    assert features["broad_syndicated_duplicate_count"] > 0
    ledger.close()


def test_macro_release_packets_are_point_in_time_and_keep_revisions(tmp_path) -> None:
    start = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=start)

    def append(period: str, value: float, seen: datetime) -> None:
        payload = {
            "title": "Cushing, OK WTI Spot Price FOB",
            "value": value,
            "expectation_value": None,
        }
        ledger.append_macro_observation({
            "source": "eia_open_data_v2", "series_id": "EIA_RWTC",
            "observation_period": period, "collector_first_seen_time": seen,
            "fetched_time": seen, "value": value, "unit": "USD/barrel",
            "payload": payload,
            "content_hash": canonical_hash((period, value)),
        })

    append("2026-08-04", 70.0, start)
    append("2026-08-05", 71.0, start + timedelta(hours=1))
    append("2026-08-05", 72.0, start + timedelta(hours=3))

    early = macro_release_packets_at(ledger, start + timedelta(minutes=30))[0]
    visible = macro_release_packets_at(ledger, start + timedelta(hours=2))[0]
    revised = macro_release_packets_at(ledger, start + timedelta(hours=4))[0]
    features, packets = macro_release_features_at(
        ledger, start + timedelta(hours=2)
    )

    assert early["current_value"] == 70.0
    assert early["previous_period_value"] is None
    assert visible["current_value"] == 71.0
    assert visible["previous_period_value"] == 70.0
    assert visible["prior_revision_value"] is None
    assert visible["expectation_value"] is None
    assert revised["current_value"] == 72.0
    assert revised["prior_revision_value"] == 71.0
    assert revised["revision_delta"] == 1.0
    assert revised["relation_to_prior"] == "REVISION"
    assert features["eia_wti_level"] == 71.0
    assert features["eia_wti_change"] == 1.0
    assert features["eia_wti_revision_delta"] == 0.0
    assert datetime.fromisoformat(packets[0]["collector_first_seen_time"]) == (
        start + timedelta(hours=1)
    )
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
            "core_visible_events": [], "broad_visible_events": [],
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


def _insert_unscored_prediction(connection, decision_id: str, decision_time: datetime, *,
                                model_version: str,
                                model_identity: str = "MARKET_ONLY") -> None:
    connection.execute(
        "INSERT INTO predictions_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision_id, model_version, model_identity, decision_time.isoformat(),
         decision_time.isoformat(), "LIVE_OOS", "feature-hash", 0.1, None,
         0.1, -0.1, None, None, "UTC_DAY_BLOCK_OOS_ABS_RESIDUAL_Q95",
         "calibration-test", 0, 0, 0, None, "UNCALIBRATED", "LONG", "WAIT",
         "PROVISIONAL"),
    )


def test_version_group_evaluation_state_distinguishes_pending_from_no_run(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    created = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    _insert_model_update(
        ledger.connection, "market-never-ran", "MARKET_ONLY", created
    )
    _insert_model_update(
        ledger.connection, "market-pending", "MARKET_ONLY",
        created + timedelta(hours=1),
    )
    _insert_unscored_prediction(
        ledger.connection, "pending-decision", created + timedelta(hours=1, minutes=5),
        model_version="market-pending",
    )

    groups = {
        row["model_versions"][0]: row
        for row in learning_curve_payload(
            ledger.connection, observed_at=created + timedelta(hours=1, minutes=10)
        )["version_groups"]
    }
    assert groups["market-never-ran"]["lifecycle_status"] == "PREVIOUS"
    assert groups["market-never-ran"]["evaluation_status"] == "NO_PREDICTIONS"
    assert groups["market-never-ran"]["subsequent_prediction_rows"] == 0
    assert groups["market-pending"]["evaluation_status"] == "AWAITING_OUTCOME"
    assert groups["market-pending"]["unscored_oos_rows"] == 1
    assert (
        groups["market-pending"]["cadence_metrics"]["FIXED_30M"]["evaluation_status"]
        == "AWAITING_FIRST_PREDICTION"
    )
    settled_groups = {
        row["model_versions"][0]: row
        for row in learning_curve_payload(
            ledger.connection, observed_at=created + timedelta(hours=2)
        )["version_groups"]
    }
    assert settled_groups["market-pending"]["evaluation_status"] == "OUTCOME_UNAVAILABLE"
    assert settled_groups["market-pending"]["overdue_oos_rows"] == 1
    ledger.close()


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
        "core_visible_events": [],
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
    assert group["cadence_metrics"]["EVERY_5M"]["evaluation_status"] == "HAS_RESULTS"
    assert group["cadence_metrics"]["EVERY_5M"]["prediction_rows"] == 3
    assert group["cadence_metrics"]["EVERY_5M"]["cumulative_quote_return"] == pytest.approx(
        sum(net_shadow_log_return(value) for value in (1.0, 2.0, -0.5))
    )
    assert group["cadence_metrics"]["FIXED_30M"]["oos_rows"] == 1
    assert group["cadence_metrics"]["FIXED_30M"]["evaluation_status"] == "HAS_RESULTS"
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
        "core_events": [], "broad_events": [],
        "receipt": (f"d-{index}", f"m-{index}", f"n-{index}", f"o-{index}"),
    } for index in range(count)]


def _attach_event_exposure(rows: list[dict], *, event_days: int = 1,
                           event_count: int = 3, core: bool = True) -> None:
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
            "source_budget_id": f"source-{event_index}",
        }
        row["news_exposed"] = core
        row["broad_news_exposed"] = True
        row["news"] = [0.1 if core else 0.0] * len(training_v2.NEWS_FEATURES)
        row["broad_news"] = [0.1] * len(training_v2.BROAD_MODEL_FEATURES)
        row["core_events"] = (
            [{**event, "model_permission": "OFFICIAL_MODEL"}] if core else []
        )
        row["broad_events"] = [{**event, "model_permission": "BROAD_MODEL"}]


def _stub_training_rows(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(
        training_v2, "refresh_training_materialization_state",
        lambda *_: {"row_count": len(rows), "state": "CLEAN"},
    )
    monkeypatch.setattr(training_v2, "complete_training_rows", lambda *_: rows)


def test_not_due_gate_does_not_materialize_training_rows(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward-not-due.sqlite3")
    monkeypatch.setattr(
        training_v2, "refresh_training_materialization_state",
        lambda *_: {"row_count": 12, "state": "CLEAN"},
    )
    monkeypatch.setattr(
        training_v2, "complete_training_rows",
        lambda *_: pytest.fail("NOT_DUE must not materialize training rows"),
    )

    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 5, 12, tzinfo=UTC), tmp_path / "models",
    )

    assert result == [{
        "status": "ENGINEERING", "complete_rows": 12,
        "next_threshold": training_v2.PREVIEW_ROWS,
    }]
    ledger.close()


@pytest.mark.parametrize("count", [96, 200])
def test_generation_waits_for_news_evidence_without_partial_market_update(
    tmp_path, monkeypatch, count: int
) -> None:
    ledger = ForwardLedger(tmp_path / f"forward-{count}.sqlite3")
    rows = _training_rows(count)
    _stub_training_rows(monkeypatch, rows)
    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 5, 12, tzinfo=UTC), tmp_path / "models"
    )
    update = ledger.connection.execute("SELECT * FROM model_updates_v2").fetchone()
    assert result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"
    assert update is None
    assert ledger.connection.execute("SELECT count(*) FROM predictions_v2").fetchone()[0] == 0
    ledger.close()


def test_generation_treats_fully_expired_news_as_insufficient(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward-expired.sqlite3")
    rows = _training_rows(200)
    _attach_event_exposure(rows, event_days=3, event_count=10)
    for row in rows:
        for event in (*row["core_events"], *row["broad_events"]):
            event["raw_weight"] = 0.0
    _stub_training_rows(monkeypatch, rows)

    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 5, 12, tzinfo=UTC), tmp_path / "models"
    )

    assert result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"
    assert result[0]["core_has_positive_weight"] is False
    assert result[0]["broad_has_positive_weight"] is False
    assert not (tmp_path / "models").exists()
    update_count = ledger.connection.execute(
        "SELECT count(*) FROM model_updates_v2"
    ).fetchone()[0]
    assert update_count == 0
    ledger.close()


def test_current_contract_generation_gate_fails_closed_without_complete_activation(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward-no-generation.sqlite3")

    with pytest.raises(
        RuntimeError, match="Core/Broad news contract has no active generation"
    ):
        training_v2.require_current_contract_generation(ledger.connection)

    ledger.close()


@pytest.mark.parametrize(
    ("stage", "accepted"),
    [("SHADOW", True), ("PREVIEW_ONLY", False)],
)
def test_live_generation_gate_accepts_only_the_live_stage(
    tmp_path, stage: str, accepted: bool,
) -> None:
    """A complete activation is not live-safe unless its stage is SHADOW."""
    ledger = ForwardLedger(tmp_path / f"forward-{stage.lower()}.sqlite3")
    created_at = datetime(2026, 8, 5, 12, tzinfo=UTC)
    generation_id = f"current-{stage.lower()}"
    identities = (
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL", "NEWS_ONLY",
    )
    for identity in identities:
        model_version = f"{identity.lower()}-{stage.lower()}"
        artifact_path = tmp_path / model_version / "artifact.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        ledger.connection.execute(
            "INSERT INTO model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                model_version, identity, stage, created_at.isoformat(),
                created_at.isoformat(), 200, 0, 200, 30, 10, 3,
                f"dataset-{model_version}", "features", None,
                str(artifact_path), f"hash-{model_version}", "CHALLENGER",
            ),
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generations_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            generation_id, stage, created_at.isoformat(), created_at.isoformat(),
            CURRENT_NEWS_CONTRACT.policy_version,
            CURRENT_NEWS_CONTRACT.feature_version,
            CURRENT_NEWS_CONTRACT.eligibility_version,
            "events", "market", "core", "broad",
            training_v2.EVENT_WEIGHTING_VERSION, 5, "READY",
        ),
    )
    for identity in identities:
        table = (
            "news_model_generation_aux_members_v1"
            if identity == "NEWS_ONLY"
            else "news_model_generation_members_v1"
        )
        ledger.connection.execute(
            f"INSERT INTO {table} VALUES (?,?,?)",
            (generation_id, identity, f"{identity.lower()}-{stage.lower()}"),
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generation_activations_v1 VALUES (?,?,?,?,?)",
        ("activation", generation_id, None, created_at.isoformat(), "TEST"),
    )

    if accepted:
        assert training_v2.require_current_contract_generation(
            ledger.connection
        ) == generation_id
    else:
        with pytest.raises(
            RuntimeError,
            match="live collector requires a SHADOW generation; "
                  "latest active generation is PREVIEW_ONLY",
        ):
            training_v2.require_current_contract_generation(ledger.connection)

    ledger.close()


def test_policy_generation_does_not_reuse_legacy_retrain_clock(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    initial_cutoff = datetime(2026, 8, 1, 12, tzinfo=UTC)
    _insert_model_update(ledger.connection, "market-existing", "MARKET_ONLY", initial_cutoff)
    _insert_model_update(ledger.connection, "full-existing", "FULL", initial_cutoff)
    _insert_model_update(
        ledger.connection, "broad-full-existing", "BROAD_FULL", initial_cutoff
    )
    _stub_training_rows(monkeypatch, _training_rows(145))
    result = training_v2.train_due_v2(
        ledger, datetime(2026, 8, 6, 20, tzinfo=UTC), tmp_path / "models"
    )
    assert result[0]["status"] == "NEWS_GENERATION_EVIDENCE_INSUFFICIENT"

    _stub_training_rows(monkeypatch, _training_rows(146))
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

    _stub_training_rows(monkeypatch, rows)
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


def test_generation_activates_all_six_models_with_broad_news_and_cold_core_lane(
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

    _stub_training_rows(monkeypatch, rows)
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

    _attach_event_exposure(rows, core=False)
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
    updates = {
        row["model_identity"]: row
        for row in ledger.connection.execute("SELECT * FROM model_updates_v2")
    }
    assert updates["NEWS_RESIDUAL"]["news_exposed_rows"] == 0
    assert updates["FULL"]["news_exposed_rows"] == 0
    assert updates["BROAD_FULL"]["news_exposed_rows"] >= 30
    assert "insufficient" in updates["NEWS_RESIDUAL"]["model_version"]
    active = inference_v2._active_updates(
        inference_v2._activated_generation_updates(
            ledger, datetime.now(UTC) + timedelta(minutes=1)
        )
    )
    assert {row["model_identity"] for row in active} == {
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL", "BROAD_NEWS_RESIDUAL",
        "BROAD_FULL", "NEWS_ONLY",
    }
    assert training_v2.require_current_contract_generation(
        ledger.connection
    ) == second_result[0]["generation_id"]
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


def test_market_crossfit_reuses_immutable_persisted_predictions(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    rows = _training_rows(120)

    class Artifact:
        artifact_hash = "crossfit-artifact"

        def predict(self, values):
            return [0.25] * len(values)

    monkeypatch.setattr(training_v2, "train_ridge", lambda *_args: Artifact())
    first = training_v2.chronological_crossfit_market(
        ledger, rows, tmp_path, datetime(2026, 8, 5, tzinfo=UTC)
    )
    assert first

    def unexpected_retrain(*_args):
        raise AssertionError("an immutable crossfit receipt must be reused")

    monkeypatch.setattr(training_v2, "train_ridge", unexpected_retrain)
    second = training_v2.chronological_crossfit_market(
        ledger, rows, tmp_path, datetime(2026, 8, 6, tzinfo=UTC)
    )

    assert second == first
    assert ledger.connection.execute(
        "SELECT count(*) FROM market_crossfit_predictions"
    ).fetchone()[0] == len(first)
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
    with pytest.raises(RuntimeError, match="incomplete prediction set"):
        inference_v2._require_complete_active_generation(
            ledger,
            base + timedelta(minutes=2),
            [{"model_identity": "CHAMPION_0"}],
        )
    transition = learning_curve_payload(ledger.connection)["news_contract_transition"]
    assert transition["state"] == "BLOCKED_RETIRED_GENERATION"
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
        news_pipeline_health={
            "observed_at": decision.isoformat(), "status": "HEALTHY",
            "reason_codes": (), "heartbeat_at": decision.isoformat(),
            "unresolved_items": 0, "oldest_unresolved_at": None,
            "snapshot_hash": "healthy-news-pipeline",
        },
    )

    versions = ledger.connection.execute(
        """SELECT DISTINCT feature_version,eligibility_version
        FROM derived_news_feature_snapshots WHERE source_decision_id=?""",
        ("current-only",),
    ).fetchall()
    assert [(row["feature_version"], row["eligibility_version"]) for row in versions] == [
        (CURRENT_NEWS_CONTRACT.feature_version, CURRENT_NEWS_CONTRACT.eligibility_version)
    ]
    coverage = ledger.connection.execute(
        """SELECT state,usable_core_event_count,usable_broad_event_count,
                  source_evidence_hash,snapshot_hash
           FROM news_input_coverage_snapshots_v1
           WHERE source_decision_id=?""",
        ("current-only",),
    ).fetchone()
    assert coverage["state"] == "UNAVAILABLE"
    assert coverage["usable_core_event_count"] == 0
    assert coverage["usable_broad_event_count"] == 0
    assert coverage["source_evidence_hash"]
    assert coverage["snapshot_hash"]
    ledger.close()


def test_catch_up_decision_freezes_source_observability_at_decision_time(
    tmp_path,
) -> None:
    decision = datetime(2026, 8, 10, 20, 5, tzinfo=UTC)
    created = decision + timedelta(minutes=25)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision)
    ledger.connection.execute(
        "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
        (
            "epoch", decision.isoformat(), decision.isoformat(),
            decision.isoformat(), decision.isoformat(), "commit", "contract",
        ),
    )
    for index, spec in enumerate(NEWS_SOURCE_REGISTRY):
        ledger.append_source_poll({
            "poll_id": f"later-poll-{index}", "source": spec.source,
            "fetched_time": decision + timedelta(minutes=10), "status": "OK",
        })
    snapshot = {
        "features": {name: 0.0 for name in MARKET_FEATURES},
        "bid": 2400.0,
        "ask": 2400.1,
        "snapshot_hash": "catch-up-market-snapshot",
        "source_event_time": decision,
        "source_received_time": decision,
        "u5": 0.0,
        "data_health": "OK",
        "reason_codes": [],
    }

    with pytest.raises(
        ValueError, match="semantic health uses evidence after decision time",
    ):
        append_live_decision_v2(
            ledger, decision_id="catch-up", decision_time=decision,
            created_at=created, snapshot=snapshot,
            news_pipeline_health={
                "observed_at": created.isoformat(), "status": "HEALTHY",
                "reason_codes": (), "heartbeat_at": created.isoformat(),
                "unresolved_items": 0, "oldest_unresolved_at": None,
                "snapshot_hash": "future-health",
            },
        )

    append_live_decision_v2(
        ledger, decision_id="catch-up", decision_time=decision,
        created_at=created, snapshot=snapshot,
        news_pipeline_health={
            "observed_at": decision.isoformat(), "status": "HEALTHY",
            "reason_codes": (), "heartbeat_at": None,
            "unresolved_items": 0, "oldest_unresolved_at": None,
            "snapshot_hash": "catch-up-health",
        },
    )

    coverage = ledger.connection.execute(
        """SELECT observed_at,state,source_observability_json
           FROM news_input_coverage_snapshots_v1
           WHERE source_decision_id='catch-up'"""
    ).fetchone()
    source_observability = json.loads(coverage["source_observability_json"])
    assert coverage["observed_at"] == decision.isoformat()
    assert coverage["state"] == "UNAVAILABLE"
    assert source_observability["observable_source_count"] == 0
    assert source_observability["unavailable_source_count"] == len(
        NEWS_SOURCE_REGISTRY
    )
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
    _stub_training_rows(monkeypatch, _training_rows(120))

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

    assert active == []
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


def test_active_updates_reject_entire_generation_with_old_news_eligibility() -> None:
    updates = [
        {"model_identity": "FULL", "model_version": "old-full",
         "eligibility_version": "news-source-eligibility-v2-event-evidence"},
        {"model_identity": "BROAD_FULL", "model_version": "old-broad",
         "eligibility_version": "news-source-eligibility-v2-event-evidence+old"},
        {"model_identity": "MARKET_ONLY", "model_version": "market-current"},
    ]
    active = inference_v2._active_updates(updates)
    assert active == []


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
    assert active == []


def test_noncurrent_contract_is_never_runnable() -> None:
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
    blocked = inference_v2._active_updates(updates, {contract.eligibility_version})
    assert blocked == []

    statuses = inference_v2.news_model_activation_status(updates)
    assert {
        row["status"] for row in statuses if row["model_identity"] != "NEWS_ONLY"
    } == {"POLICY_MISMATCH"}


def test_retired_four_model_set_cannot_bypass_five_model_contract() -> None:
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

    active = inference_v2._active_updates(updates, {contract.eligibility_version})
    assert active == []
    statuses = inference_v2.news_model_activation_status(updates)
    by_identity = {row["model_identity"]: row for row in statuses}
    assert {
        row["status"] for identity, row in by_identity.items()
        if identity != "NEWS_ONLY"
    } == {"POLICY_MISMATCH"}
    assert by_identity["NEWS_ONLY"]["status"] == "NOT_TRAINED"
    updates[-1]["feature_version"] = "corrupt-contract"
    blocked = inference_v2._active_updates(updates, {contract.eligibility_version})
    assert blocked == []


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


@pytest.mark.parametrize("identity", sorted(inference_v2.NEWS_MODEL_IDENTITIES))
def test_every_news_model_fails_closed_when_news_input_is_unavailable(
    identity: str,
) -> None:
    assert inference_v2._runtime_gate_status(
        identity, market_healthy=True, news_input_state="UNAVAILABLE",
    ) == "NEWS_INPUT_UNAVAILABLE"


def test_market_only_remains_observable_when_news_input_is_unavailable() -> None:
    assert inference_v2._runtime_gate_status(
        "MARKET_ONLY", market_healthy=True, news_input_state="UNAVAILABLE",
    ) is None


def test_unknown_news_input_state_fails_closed() -> None:
    assert inference_v2._runtime_gate_status(
        "BROAD_FULL", market_healthy=True, news_input_state="UNKNOWN",
    ) == "NEWS_INPUT_UNAVAILABLE"


def test_news_learning_keeps_degraded_and_quiet_but_excludes_unavailable() -> None:
    rows = [
        {"decision_id": "available", "news_training_eligible": True},
        {"decision_id": "degraded", "news_training_eligible": True},
        {"decision_id": "quiet", "news_training_eligible": True},
        {"decision_id": "unavailable", "news_training_eligible": False},
    ]

    assert [
        row["decision_id"] for row in training_v2._news_learning_rows(rows)
    ] == ["available", "degraded", "quiet"]


@pytest.mark.parametrize("state", ["AVAILABLE", "DEGRADED", "QUIET"])
@pytest.mark.parametrize("identity", sorted(inference_v2.NEWS_MODEL_IDENTITIES))
def test_observable_news_input_states_do_not_force_wait(
    identity: str, state: str,
) -> None:
    assert inference_v2._runtime_gate_status(
        identity, market_healthy=True, news_input_state=state,
    ) is None


@pytest.mark.parametrize(
    ("market_health", "news_input_state", "expected_status"),
    [
        ("STALE", "AVAILABLE", "DATA_UNHEALTHY"),
        ("OK", "UNAVAILABLE", "NEWS_INPUT_UNAVAILABLE"),
    ],
)
def test_generation_receipts_remain_complete_when_runtime_gates_force_wait(
    monkeypatch, market_health: str, news_input_state: str, expected_status: str,
) -> None:
    updates = [
        {
            "model_identity": identity,
            "model_version": f"{identity.lower()}-live",
            "eligibility_version": inference_v2.ELIGIBILITY_VERSION,
            "artifact_path": "unused-artifact",
        }
        for identity in sorted(inference_v2.MODEL_IDENTITIES)
    ]
    inserted = []
    monkeypatch.setattr(
        inference_v2, "_activated_generation_updates", lambda *_: updates,
    )
    monkeypatch.setattr(inference_v2, "_active_updates", lambda *_: updates)
    monkeypatch.setattr(
        inference_v2, "_calibration", lambda *_: {
            "version": "test", "rows": 0, "blocks": 0, "days": 0,
            "half_width": None, "status": "UNCALIBRATED",
        },
    )
    monkeypatch.setattr(
        inference_v2, "_insert_prediction",
        lambda _ledger, **values: inserted.append(values),
    )
    monkeypatch.setattr(inference_v2, "_has_activated_generation", lambda *_: True)

    class Artifact:
        feature_names = inference_v2.MARKET_FEATURES

        @staticmethod
        def predict(_values):
            return [0.0]

    monkeypatch.setattr(inference_v2.RidgeArtifact, "read", lambda *_: Artifact())
    market_features = {name: 0.0 for name in inference_v2.MARKET_FEATURES}
    market_features.update({"decision_bid": 2400.0, "decision_ask": 2400.1})
    market_snapshot = {
        "features_json": json.dumps(market_features),
        "output_hash": "market", "data_health": market_health, "u5": 1.0,
    }
    news_snapshot = {
        "features_json": json.dumps({}), "output_hash": "news",
        "news_exposed": 0, "broad_news_exposed": 0,
    }

    created = inference_v2.append_live_predictions_v2(
        object(), decision_id="decision", decision_time=datetime.now(UTC),
        created_at=datetime.now(UTC), market_snapshot=market_snapshot,
        news_snapshot=news_snapshot,
        news_input_coverage={
            "state": news_input_state, "snapshot_hash": "coverage",
        },
    )

    assert {row["model_identity"] for row in created} == {
        "CHAMPION_0", *inference_v2.MODEL_IDENTITIES,
    }
    gated = [row for row in inserted if row["model_identity"] != "CHAMPION_0"]
    if expected_status == "NEWS_INPUT_UNAVAILABLE":
        gated = [
            row for row in gated
            if row["model_identity"] in inference_v2.NEWS_MODEL_IDENTITIES
        ]
    assert gated
    assert {row["status"] for row in gated} == {expected_status}


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
    assert active == []


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
        {"decision_id": "fresh-1", "core_events": [{
            "event_id": "fresh", "event_version_id": "fresh-v1",
            "raw_weight": 0.9,
        }]},
        {"decision_id": "fresh-2", "core_events": [{
            "event_id": "fresh", "event_version_id": "fresh-v1",
            "raw_weight": 0.8,
        }]},
        {"decision_id": "late-1", "core_events": [{
            "event_id": "late", "event_version_id": "late-v1",
            "raw_weight": 0.1,
        }]},
    ]

    weights, receipts, _, summary = training_v2._event_budget_weights(
        rows, "core_events"
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

    weights, _, _, _ = training_v2._event_budget_weights(rows, "broad_events")

    assert weights.mean() == pytest.approx(0.35)


def test_multiple_events_from_one_source_share_one_source_budget() -> None:
    rows = [
        {"decision_id": "a-1", "broad_events": [{
            "event_id": "event-a-1", "event_version_id": "event-a-1-v1",
            "raw_weight": 1.0, "source_budget_id": "source-a",
        }]},
        {"decision_id": "a-2", "broad_events": [{
            "event_id": "event-a-2", "event_version_id": "event-a-2-v1",
            "raw_weight": 1.0, "source_budget_id": "source-a",
        }]},
        {"decision_id": "b-1", "broad_events": [{
            "event_id": "event-b-1", "event_version_id": "event-b-1-v1",
            "raw_weight": 1.0, "source_budget_id": "source-b",
        }]},
    ]

    _, _, source_receipts, summary = training_v2._event_budget_weights(
        rows, "broad_events",
    )
    budgets = {
        receipt["source_budget_id"]: receipt["bounded_weight"]
        for receipt in source_receipts
    }

    assert budgets == {"source-a": pytest.approx(1.0), "source-b": pytest.approx(1.0)}
    assert summary["maximum_source_weight_share"] == pytest.approx(0.5)


def test_zero_weight_events_do_not_break_a_mixed_source_budget() -> None:
    rows = [
        {"decision_id": "live", "broad_events": [{
            "event_id": "live-event", "event_version_id": "live-v1",
            "raw_weight": 0.8, "source_budget_id": "live-source",
        }]},
        {"decision_id": "expired", "broad_events": [{
            "event_id": "expired-event", "event_version_id": "expired-v1",
            "raw_weight": 0.0, "source_budget_id": "expired-source",
        }]},
    ]

    weights, receipts, source_receipts, summary = training_v2._event_budget_weights(
        rows, "broad_events",
    )

    assert weights[0] > 0
    assert weights[1] == 0
    assert receipts[1]["normalized_event_weight"] == 0
    assert {
        receipt["source_budget_id"]: receipt["bounded_weight"]
        for receipt in source_receipts
    } == {"expired-source": 0, "live-source": pytest.approx(0.8)}
    assert summary["maximum_source_weight_share"] == pytest.approx(1.0)


def test_observable_zero_news_rows_receive_one_bounded_environment_budget() -> None:
    rows = [
        {
            "decision_id": "event-row",
            "broad_events": [{
                "event_id": "event-a", "event_version_id": "event-a-v1",
                "raw_weight": 0.8, "evidence_grade": "PRIMARY",
                "source_budget_id": "source-a",
            }],
        },
        {"decision_id": "quiet-a", "broad_events": []},
        {"decision_id": "quiet-b", "broad_events": []},
    ]

    weights, receipts, _, summary = training_v2._event_budget_weights(
        rows, "broad_events",
    )

    assert weights[0] > 0
    assert weights[1] == pytest.approx(weights[2])
    assert weights[1] > 0
    assert len(receipts) == 1
    assert summary["observable_zero_news_rows"] == 2
    assert summary["observable_zero_news_budget"] == pytest.approx(0.8)


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


def test_controlled_news_semantics_do_not_reclassify_by_headline() -> None:
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
    ) == "FACT_EVENT"
    assert annotation_topics(annotation) == ("rates_fed", "growth_economy")
    # These used to trigger substring bugs: war in forward and gain in against.
    assert annotation_topics({
        **annotation, "headline_zh": "forward guidance argues against a cut",
    }) == ("rates_fed", "growth_economy")


def test_market_wrap_is_display_only_when_ai_calls_it_market_reaction(tmp_path) -> None:
    epoch = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward-market-wrap.sqlite3", now=epoch)
    _append_news(
        ledger, source="google_news_fed_rates", item="Gold gains as Treasury yields fall",
        first_seen=epoch, parsed_at=epoch + timedelta(seconds=30), impulse=0.2,
        link="https://finance.yahoo.com/example", primary_category="rates_fed",
        record_kind="MARKET_REACTION", materiality=0.8,
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
            identity_relation=("NEW_EPISODE" if offset == 1 else "SAME_EVENT"),
            canonical_episode_id="semantic-episode-cook-removal",
            canonical_event_id="semantic-event-cook-removal-attempt",
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


def test_source_identity_is_a_feature_without_granting_high_reliability(tmp_path) -> None:
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
    assert event["source_organizations"] == ["example_media"]
    assert event["independent_publishers"] == 1
    assert event["evidence_grade"] == "SINGLE_SOURCE"
    assert event["source_reliability"] == pytest.approx(0.35)
    assert event["broad_model_eligible"] is True
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


@pytest.mark.parametrize(
    ("case", "reports", "expected_grade", "expected_core"),
    (
        (
            "first-party",
            (("eia_today_in_energy", "https://eia.gov/a", None),),
            "PRIMARY",
            True,
        ),
        (
            "independent-reliable",
            (
                ("gdelt_gold_geopolitics", "https://apnews.com/a", None),
                ("google_news_gold_context", "https://reuters.com/b", None),
            ),
            "CORROBORATED",
            True,
        ),
        (
            "single-reliable",
            (("gdelt_gold_geopolitics", "https://apnews.com/a", None),),
            "SINGLE_RELIABLE",
            False,
        ),
        (
            "independent-unregistered",
            (
                ("gdelt_gold_geopolitics", "https://one.example/a", "publisher_one"),
                ("google_news_gold_context", "https://two.example/b", "publisher_two"),
            ),
            "CORROBORATED",
            False,
        ),
    ),
)
def test_core_is_evidence_defined_subset_of_broad(
    tmp_path, case: str, reports: tuple[tuple[str, str, str | None], ...],
    expected_grade: str, expected_core: bool,
) -> None:
    epoch = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / f"core-{case}.sqlite3", now=epoch)
    for index, (source, link, organization) in enumerate(reports):
        _append_news(
            ledger,
            source=source,
            item=f"{case}-{index}",
            link=link,
            first_seen=epoch + timedelta(seconds=index),
            parsed_at=epoch + timedelta(minutes=1, seconds=index),
            impulse=0.1,
            primary_category="oil_energy",
            material_event_key=f"{case}-event",
            source_organization_id=organization,
        )

    event = event_evidence_rows(ledger, epoch + timedelta(minutes=10))[0]

    assert event["evidence_grade"] == expected_grade
    assert event["broad_model_eligible"] is True
    assert event["core_model_eligible"] is expected_core
    assert not event["core_model_eligible"] or event["broad_model_eligible"]
    ledger.close()


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
    assert event["core_model_eligible"] is True
    assert event["event_clock_source"] == "OFFICIAL_RELEASE_TIME"
