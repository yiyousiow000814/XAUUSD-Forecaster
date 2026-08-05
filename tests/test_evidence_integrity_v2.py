from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from xauusd_forecaster.evidence_v2 import install_v2_schema
from xauusd_forecaster.executable_label import build_executable_label_v2
from xauusd_forecaster.forward_ledger import ForwardLedger, canonical_hash
from xauusd_forecaster.learning_curves import _stage, learning_curve_payload
from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.news_features_v2 import aggregate_news_features_v2
from xauusd_forecaster.repair_v2 import immutable_table_hash
from xauusd_forecaster import inference_v2, training_v2
from xauusd_forecaster.u5_state import U5State, U5_VERSION


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
                 first_seen: datetime, parsed_at: datetime, impulse: float) -> None:
    body = ("publisher full body " * 30) + item
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": source, "source_item_id": item,
        "collector_first_seen_time": first_seen, "fetched_time": first_seen,
        "headline": item, "body": body, "content_hash": digest, "cluster_id": item,
    })
    ledger.append_annotation({
        "annotation_id": item, "source": source, "source_item_id": item,
        "revision_number": 1, "raw_content_hash": digest, "event_type": "monetary_policy",
        "entities": [], "hawkishness": impulse, "inflation_impulse": 0.0,
        "growth_impulse": 0.0, "geopolitical_risk": 0.0, "usd_impulse": 0.0,
        "novelty": 1.0, "confidence": 1.0, "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": "news-json-v9-local-display-recovery",
        "parse_started_at": parsed_at, "parsed_at": parsed_at,
        "annotation": {
            "event_type": "monetary_policy", "entities": [], "hawkishness": impulse,
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
                       value_quote_return: float = 2.0) -> None:
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
        (decision_id, model_version, decision_time.isoformat(), value_quote_return, 0.2, 0.1,
         0.01, 1, 0, f"score-{decision_id}"),
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
    assert [point["decision_time"] for point in market_curve["points"]] == [
        (created_at + timedelta(minutes=5)).isoformat()
    ]
    ledger.close()


def test_identity_curve_uses_only_latest_parallel_version_per_decision(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    created = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision = created + timedelta(hours=1)
    _insert_model_update(ledger.connection, "market-old", "MARKET_ONLY", created)
    _insert_model_update(
        ledger.connection, "market-new", "MARKET_ONLY", created + timedelta(minutes=30)
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
    assert len(curve["points"]) == 1
    assert curve["points"][0]["cumulative_quote_return"] == pytest.approx(2.0)
    ledger.close()


def _training_rows(count: int) -> list[dict]:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    return [{
        "decision_id": f"d-{index}", "lane": "REPAIRED_SEED",
        "decision_time": (start + timedelta(minutes=5 * index)).isoformat(),
        "market": [float(index + offset) for offset in range(len(training_v2.MARKET_FEATURES))],
        "news": [0.0] * len(training_v2.NEWS_FEATURES), "target": float(index) / 100,
        "news_exposed": False, "distinct_news_clusters": 0,
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


def test_uncertainty_uses_only_same_version_prior_oos_residuals(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    base = datetime(2026, 8, 1, tzinfo=UTC)
    for model, residual in (("market", 9.0), ("full", 0.25)):
        ledger.connection.execute(
            "INSERT INTO predictions_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{model}-d", model, model.upper(), base.isoformat(), base.isoformat(),
             "LIVE_OOS", "features", 0.0, None, 0.0, 0.0, None, None, "method",
             "cal", 0, 0, 0, None, "EARLY", "WAIT", "WAIT", "PROVISIONAL"),
        )
        ledger.connection.execute(
            "INSERT INTO prediction_scores_v2 VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"{model}-d", model, base.isoformat(), 0.0, 0.0, residual, residual ** 2,
             0, 0, f"score-{model}"),
        )
    calibration = inference_v2._calibration(
        ledger, "full", "FULL", base + timedelta(days=1)
    )
    assert calibration["rows"] == 1
    assert calibration["half_width"] == pytest.approx(0.25)
    ledger.close()


def test_evaluation_lifetime_uses_valid_days_not_prediction_count(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    empty = {"version": "none", "rows": 0, "blocks": 0, "days": 0,
             "half_width": None, "status": "UNCALIBRATED"}
    for index in range(75):
        when = base + timedelta(minutes=5 * index)
        inference_v2._insert_prediction(
            ledger, decision_id=f"unhealthy-{index}", decision_time=when, created_at=when,
            model_version="long-lived", model_identity="MARKET_ONLY", feature_hash="features",
            predicted=None, news_residual=None, ev_long=None, ev_short=None,
            calibration=empty, recommended="WAIT", status="DATA_UNHEALTHY",
        )
    assert inference_v2._evaluation_window_open(
        ledger, "long-lived", base + timedelta(days=1)
    )

    for day in range(60):
        when = base + timedelta(days=day, hours=12)
        _insert_prediction(
            ledger.connection, f"valid-{day}", when,
            model_version="long-lived", value_quote_return=0.1,
        )
    assert inference_v2._evaluation_window_open(
        ledger, "long-lived", base + timedelta(days=59, hours=13)
    )
    assert not inference_v2._evaluation_window_open(
        ledger, "long-lived", base + timedelta(days=60)
    )
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
