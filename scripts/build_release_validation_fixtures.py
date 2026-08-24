#!/usr/bin/env python3
"""Build deterministic, production-shaped Worker release validation fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from scripts import run_dashboard_sync as dashboard_sync
from xauusd_forecaster.news_projection import build_news_projection_generation


FIXED_START = datetime(2026, 8, 13, tzinfo=UTC)


def _decision(index: int, identity: str = "BROAD_FULL") -> dict:
    at = FIXED_START + timedelta(minutes=5 * index)
    return {
        "source_decision_id": f"release-validation-{index:04d}",
        "decision_time": at.isoformat(),
        "model_identity": identity,
        "model_version": "release-validation-v17",
        "recommended_action": "LONG" if index % 2 == 0 else "SHORT",
        "prediction_status": "PROVISIONAL",
        "outcome_status": "VALID",
        "ev_long_u5": 0.12,
        "ev_short_u5": -0.08,
        "explanation": "Bounded release validation decision evidence. " * 8,
    }


def _candle(index: int) -> dict:
    at = FIXED_START + timedelta(minutes=5 * index)
    base = 4300 + index / 100
    return {
        "time": at.isoformat(), "open": base, "high": base + 1.2,
        "low": base - 1.1, "close": base + 0.25, "ticks": 100 + index,
    }


def _news(index: int) -> dict:
    at = FIXED_START + timedelta(minutes=index)
    key = f"{index + 1:064x}"
    return {
        "source": "release-validation", "source_item_id": f"fixture-{index}",
        "revision_number": 1, "detail_key": key, "event_key": key,
        "cluster_id": f"cluster-{index:04d}", "category": "美国宏观",
        "collector_first_seen_time": at.isoformat(),
        "source_published_time": (at - timedelta(minutes=2)).isoformat(),
        "headline": (
            f"Release validation production-shaped headline {index} "
            + "bounded evidence " * 1_200
        ),
        "summary_zh": "用于候选版本验证的有界新闻证据。" * 120,
        "annotation_status": "READY", "model_visibility": "MODEL_VISIBLE",
        "parsed_at": at.isoformat(), "impact_expires_at": (at + timedelta(hours=6)).isoformat(),
        "mirror_contract": dashboard_sync.NEWS_MIRROR_CONTRACT_VERSION,
        "broad_model_eligible": True, "model_seen": index % 2 == 0,
    }


def _source_payload() -> dict:
    identities = (
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
    )
    candles = [_candle(index) for index in range(576)]
    decisions = [
        _decision(index, identities[index % len(identities)])
        for index in range(1_440)
    ]
    curve_points = [{
        "decision_time": (FIXED_START + timedelta(minutes=5 * index)).isoformat(),
        "cumulative_quote_return": round(index / 100_000, 8),
        "sample_count": index + 1,
        "evidence": "bounded learning evidence " * 40,
    } for index in range(480)]
    return {
        "generated_at": (FIXED_START + timedelta(days=7)).isoformat(),
        "system": {"online": True, "components": {"decision_collector": {"status": "OK"}}},
        "counts": {"decision_events": len(decisions), "news": 200},
        "training": {"complete_rows": 2_500},
        "recent_decisions": [_decision(index) for index in range(20)],
        "news_evidence": [_news(index) for index in range(200)],
        "recent_news": [_news(index) for index in range(200)],
        "daily_news_briefs": [{
            "brief_date": f"2026-08-{13 + index:02d}",
            "summary_zh": "候选版本有界每日简报。" * 140,
            "phase": "FINAL",
        } for index in range(8)],
        "storylines": [{
            "storyline_id": f"story-{index:03d}",
            "headline": f"Production-shaped storyline {index}",
            "narrative_zh": "候选版本故事线与时间脉络证据。" * 60,
        } for index in range(24)],
        "market_reaction_streams": [{
            "stream_id": f"reaction-{index:03d}",
            "summary_zh": "市场反应流的有界审计内容。" * 40,
        } for index in range(24)],
        "storyline_summary": {"total": 24, "active": 24},
        "news_evidence_summary": {"total_events": 200, "model_eligible": 200},
        "market_chart": {
            "candles": candles,
            "overview_candles": candles[::5][:480],
            "decisions": decisions,
            "history_start": candles[0]["time"], "history_end": candles[-1]["time"],
            "source_candle_count": len(candles),
            "prediction_history_start": {
                identity: candles[0]["time"]
                for identity in identities
            },
        },
        "learning_curves": {
            "models": [{
                "model_identity": "BROAD_FULL", "model_version": "release-validation-v17",
                "lifecycle_status": "LATEST", "created_at": FIXED_START.isoformat(),
            }],
            "version_groups": [{
                "model_identity": "BROAD_FULL", "training_dataset_hash": "a" * 64,
                "created_at": FIXED_START.isoformat(), "generation": 17,
            }],
            "identity_curves": [{
                "model_identity": "BROAD_FULL", "points": curve_points,
                "points_30m": curve_points[::6],
            }],
            "full_minus_market": curve_points,
            "broad_full_minus_core_full": curve_points,
        },
        "execution_learning": {"models": []},
    }


def build_fixtures() -> dict[str, bytes]:
    source = _source_payload()
    candles = source["market_chart"]["candles"]
    identities = ("MARKET_ONLY", "NEWS_RESIDUAL", "FULL", "BROAD_FULL")
    decisions = [
        _decision(index, identities[index % len(identities)])
        for index in range(2_500)
    ]
    market_history = max(
        dashboard_sync._market_history_payloads(candles[:500], decisions[:2_500]),
        key=len,
    )
    learning_records = dashboard_sync.learning_history_records(source)
    learning_batch = max(
        dashboard_sync.learning_history_batches(learning_records),
        key=lambda rows: len(json.dumps(
            {"records": rows}, ensure_ascii=False, separators=(",", ":"),
        ).encode()),
    )
    learning_history = json.dumps(
        {"records": learning_batch}, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode()
    news_generation = build_news_projection_generation(
        source["recent_news"], [],
        window_start=FIXED_START.isoformat(),
        watermark=(FIXED_START + timedelta(days=7)).isoformat(),
    )
    evidence_items = [
        {**_news(index), "headline": f"Bounded evidence headline {index}"}
        for index in range(20)
    ]
    news_evidence = json.dumps({
        "contract_version": dashboard_sync.NEWS_EVIDENCE_CONTRACT_VERSION,
        "snapshot_id": "f" * 64, "offset": 0, "items": evidence_items,
    }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    snapshot_id = "f" * 64
    encode = lambda value: json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "status-ingest.json": dashboard_sync.remote_snapshot(source),
        "audit-write.json": dashboard_sync.audit_snapshot(source),
        "audit-briefs-write.json": dashboard_sync.audit_briefs_snapshot(source),
        "audit-stories-write.json": dashboard_sync.audit_stories_snapshot(source),
        "audit-decisions-write.json": dashboard_sync.audit_decisions_snapshot(source),
        "learning-write.json": dashboard_sync.learning_snapshot(source),
        "market-chart-write.json": dashboard_sync.market_chart_snapshot(source),
        "market-history-write.json": market_history,
        "learning-history-write.json": learning_history,
        "news-evidence-prepare.json": encode({
            "contract_version": dashboard_sync.NEWS_EVIDENCE_CONTRACT_VERSION,
            "prepare_snapshot": snapshot_id, "expected_count": len(evidence_items),
        }),
        "news-evidence-stage.json": news_evidence,
        "news-evidence-activate.json": encode({
            "contract_version": dashboard_sync.NEWS_EVIDENCE_CONTRACT_VERSION,
            "activate_snapshot": snapshot_id, "expected_count": len(evidence_items),
        }),
        "news-evidence-cleanup.json": encode({
            "contract_version": dashboard_sync.NEWS_EVIDENCE_CONTRACT_VERSION,
            "cleanup_active_snapshot": snapshot_id,
        }),
        "news-index-prepare.json": encode({
            "action": "prepare",
            "generation_id": news_generation.manifest["generation_id"],
            "manifest": news_generation.manifest,
        }),
        "news-index-stage.json": encode({
            "action": "stage_index",
            "generation_id": news_generation.manifest["generation_id"],
            "offset": 0, "items": list(news_generation.index_batches[0]),
        }),
        "news-index-activate.json": encode({
            "action": "activate",
            "generation_id": news_generation.manifest["generation_id"],
        }),
        "news-index-verify.json": encode({
            "action": "verify",
            "generation_id": news_generation.manifest["generation_id"],
        }),
        "news-index-abandon.json": encode({
            "action": "abandon",
            "generation_id": news_generation.manifest["generation_id"],
        }),
        "news-content-stage.json": encode({
            "action": "stage_details",
            "generation_id": news_generation.manifest["generation_id"],
            "offset": 0, "items": list(news_generation.detail_batches[0]),
        }),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixtures = build_fixtures()
    for name, payload in fixtures.items():
        (args.output / name).write_bytes(payload)
    print(json.dumps({name: len(payload) for name, payload in fixtures.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
