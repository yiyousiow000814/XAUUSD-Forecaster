from __future__ import annotations

import json
from datetime import UTC, datetime

from xauusd_forecaster import daily_brief
from xauusd_forecaster.forward_ledger import ForwardLedger


def _seed_news(ledger: ForwardLedger) -> None:
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_revisions
               (source,source_item_id,revision_number,source_published_time,
                collector_first_seen_time,item_first_seen_time,fetched_time,
                headline,body,link,content_hash,cluster_id,collector_latency_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("Reuters", "item-1", 1, "2026-08-10T01:00:00+00:00",
             "2026-08-10T01:01:00+00:00", "2026-08-10T01:01:00+00:00",
             "2026-08-10T01:01:00+00:00", "黄金上涨", "x" * 300,
             "https://example.com/1", "hash-1", "cluster-1", 60),
        )
        ledger.connection.execute(
            """INSERT INTO news_annotations
               (annotation_id,source,source_item_id,revision_number,raw_content_hash,
                event_type,entities_json,hawkishness,inflation_impulse,growth_impulse,
                geopolitical_risk,usd_impulse,novelty,confidence,llm_model_version,
                prompt_version,parse_started_at,parsed_at,annotation_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("a-1", "Reuters", "item-1", 1, "hash-1", "market", "[]",
             0, 0, 0, 0, 0, 0.8, 0.9, "gemini", "v1",
             "2026-08-10T01:02:00+00:00", "2026-08-10T01:03:00+00:00",
             json.dumps({"summary_zh": "黄金价格出现新变化", "primary_category": "油价/能源"})),
        )


def test_daily_brief_only_calls_model_when_source_changes(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    monkeypatch.setattr(daily_brief, "configured_gemini_api_keys", lambda: ("key",))
    monkeypatch.setattr(daily_brief.GeminiQuotaLedger, "reserve", lambda self, key, now: True)
    calls = []
    monkeypatch.setattr(daily_brief, "_call_gemma", lambda key, rows: (
        calls.append(rows) or {"title": "今日黄金新闻", "items": [{
            "headline": "黄金上涨", "summary": "出现新变化",
            "evidence_ids": ["Reuters:item-1:1"],
        }]}, "gemma-test",
    ))
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    assert daily_brief.update_daily_brief(ledger, now)["status"] == "OK"
    assert daily_brief.update_daily_brief(ledger, now)["status"] == "UNCHANGED"
    assert len(calls) == 1
    assert daily_brief.recent_daily_briefs(ledger.connection)[0]["brief"]["items"][0]["headline"] == "黄金上涨"
    ledger.close()


def test_daily_brief_is_append_only(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    with ledger.connection:
        ledger.connection.execute(
            "INSERT INTO daily_news_briefs VALUES (?,?,?,?,?,?,?,?)",
            ("2026-08-10", 1, "hash", "2026-08-10T00:00:00+00:00",
             "2026-08-10T00:00:00+00:00", "gemma", "v1", '{"title":"x","items":[]}'),
        )
    try:
        ledger.connection.execute("DELETE FROM daily_news_briefs")
        raise AssertionError("append-only trigger did not fire")
    except Exception as error:
        assert "append-only" in str(error)
    ledger.close()
