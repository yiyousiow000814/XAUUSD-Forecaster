from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest

from xauusd_forecaster import annotation, daily_brief
from xauusd_forecaster.ai_provider_registry import quota_surface_for_model
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.model_gateway import (
    GeminiModelGateway, ModelGatewayCapacityExhausted, ModelGatewayResponseInvalid,
)
from xauusd_forecaster.news_scheduler import (
    ApiCredential, ROUTINE_POOL, enqueue_job, quota_day,
)
from xauusd_forecaster.scheduler_model_gateway import SchedulerModelAccountant
from tests.model_accounting_fakes import CallbackModelAccountant


def _seed_news_item(
    ledger: ForwardLedger,
    item_id: str,
    *,
    minute: int,
    summary: str | None = None,
    parsed_at: datetime | None = None,
    review_priority: str = "NORMAL",
    material_event_key: str | None = None,
    received_at: datetime | None = None,
    category: str = "油价/能源",
    materiality: float = 0.8,
) -> None:
    received = received_at or (
        datetime(2026, 8, 10, 1, tzinfo=UTC) + timedelta(minutes=minute)
    )
    parsed = parsed_at or received + timedelta(minutes=1)
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_revisions
               (source,source_item_id,revision_number,source_published_time,
                collector_first_seen_time,item_first_seen_time,fetched_time,
                headline,body,link,content_hash,cluster_id,collector_latency_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "Reuters",
                item_id,
                1,
                (received - timedelta(minutes=1)).isoformat(),
                received.isoformat(),
                received.isoformat(),
                received.isoformat(),
                f"黄金新闻 {item_id}",
                "x" * 300,
                f"https://example.com/{item_id}",
                f"hash-{item_id}",
                f"cluster-{item_id}",
                60,
            ),
        )
        ledger.connection.execute(
            """INSERT INTO news_annotations
               (annotation_id,source,source_item_id,revision_number,raw_content_hash,
                event_type,entities_json,hawkishness,inflation_impulse,growth_impulse,
                geopolitical_risk,usd_impulse,novelty,confidence,llm_model_version,
                prompt_version,parse_started_at,parsed_at,annotation_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"annotation-{item_id}",
                "Reuters",
                item_id,
                1,
                f"hash-{item_id}",
                "market",
                "[]",
                0,
                0,
                0,
                0,
                0,
                0.8,
                0.9,
                daily_brief.DEFAULT_GEMINI_MODEL,
                daily_brief.PROMPT_VERSION,
                (parsed - timedelta(seconds=5)).isoformat(),
                parsed.isoformat(),
                json.dumps(
                    {
                        "summary_zh": summary or f"黄金价格出现新变化 {item_id}",
                        "primary_category": category,
                        "review_priority": review_priority,
                        "materiality": materiality,
                        "material_event_key": material_event_key or f"event-{item_id}",
                    }
                ),
            ),
        )


def _seed_news(ledger: ForwardLedger) -> None:
    _seed_news_item(ledger, "item-1", minute=1)


def _seed_unannotated_revision(
    ledger: ForwardLedger, item_id: str, *, received_at: datetime,
    cluster_id: str, body_length: int,
) -> None:
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_revisions
               (source,source_item_id,revision_number,source_published_time,
                collector_first_seen_time,item_first_seen_time,fetched_time,
                headline,body,link,content_hash,cluster_id,collector_latency_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "Reuters", item_id, 1, received_at.isoformat(),
                received_at.isoformat(), received_at.isoformat(), received_at.isoformat(),
                f"Headline {item_id}", "x" * body_length,
                f"https://example.com/{item_id}", f"hash-{item_id}", cluster_id, 0,
            ),
        )


def _seed_bulk_news(
    ledger: ForwardLedger, count: int, *, reverse: bool = False,
) -> None:
    indexes = range(count - 1, -1, -1) if reverse else range(count)
    for index in indexes:
        _seed_news_item(ledger, f"bulk-{index:03d}", minute=index)


def _fake_generation(calls: list[dict]):
    def generate(api_key, **kwargs):
        del api_key
        prompt = kwargs["payload"]["contents"][0]["parts"][0]["text"]
        evidence = json.loads(prompt.split("\nEVIDENCE\n", 1)[1])
        resolved_ids = []
        for row in evidence:
            probe = {"candidates": [{"content": {"parts": [{
                "text": json.dumps({
                    "title": "引用检查", "overview": "引用检查用于测试。",
                    "drivers": ["引用关系经过验证。"],
                    "watch_next": "继续核对引用关系。",
                    "items": [{
                        "headline": "引用检查", "summary": "引用检查。",
                        "evidence_ids": [row["ref"]],
                    }],
                }),
            }]}}]}
            resolved_ids.append(
                kwargs["decode"](probe)["items"][0]["evidence_ids"][0]
            )
        calls.append({**kwargs, "evidence": evidence, "evidence_ids": resolved_ids})
        result = {
            "title": "今日黄金新闻",
            "overview": "多项宏观变化共同影响黄金市场，重点集中在最新政策与价格反应。",
            "drivers": ["政策与价格反应共同影响市场。"],
            "watch_next": "关注后续政策与价格变化。",
            "items": [{
                "headline": str(evidence[-1]["headline"]),
                "summary": "出现新变化",
                "evidence_ids": [str(evidence[-1]["ref"])],
            }],
        }
        envelope = {"candidates": [{"content": {"parts": [{
            "text": json.dumps(result),
        }]}}]}
        return kwargs["decode"](envelope), "gemma-test"

    return generate


def test_daily_brief_only_calls_model_when_source_changes(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    calls = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(calls),
    )
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    accountant = CallbackModelAccountant(lambda usage: True)
    assert daily_brief.update_daily_brief(
        ledger, api_key="test-key", request_accountant=accountant, now=now,
    )["status"] == "OK"
    assert daily_brief.update_daily_brief(
        ledger, api_key="test-key", request_accountant=accountant, now=now,
    )["status"] == "UNCHANGED"
    assert len(calls) == 1
    assert calls[0]["purpose"] == "daily-news-brief"
    assert (
        daily_brief.recent_daily_briefs(ledger.connection)[0]["brief"]["items"][0]["headline"]
        == "黄金新闻 item-1"
    )
    assert "宏观变化" in daily_brief.recent_daily_briefs(
        ledger.connection,
    )[0]["brief"]["overview"]
    ledger.close()


def test_prompt_contract_change_regenerates_unchanged_candidates(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    monkeypatch.setattr(daily_brief, "BRIEF_PROMPT_VERSION", "daily-news-brief-legacy")
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    accountant = CallbackModelAccountant(lambda usage: True)
    assert daily_brief.update_daily_brief(
        ledger, api_key="test-key", request_accountant=accountant, now=now,
    )["revision_number"] == 1

    monkeypatch.setattr(
        daily_brief, "BRIEF_PROMPT_VERSION", "daily-news-brief-v3-synthesis-overview",
    )
    regenerated = daily_brief.update_daily_brief(
        ledger, api_key="test-key", request_accountant=accountant,
        now=now + timedelta(minutes=1),
    )
    assert regenerated["revision_number"] == 2
    assert len(calls) == 2
    ledger.close()


@pytest.mark.parametrize(
    "received",
    (
        datetime(2026, 8, 16, 9, tzinfo=UTC),
        datetime(2026, 8, 16, 17, tzinfo=timezone(timedelta(hours=8))),
    ),
)
def test_receipt_day_and_cutoff_are_offset_invariant(
    tmp_path, monkeypatch, received: datetime,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news_item(ledger, "offset-news", minute=0, received_at=received)
    calls: list[dict] = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(calls),
    )

    result = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        now=datetime(2026, 8, 16, 12, tzinfo=UTC),
    )

    assert result["status"] == "OK"
    assert result["brief_date"] == "2026-08-16"
    assert (result["received_items"], result["reviewed_items"]) == (1, 1)
    assert len(calls) == 1
    ledger.close()


def test_full_state_hash_and_candidates_cover_later_news_deterministically(
    tmp_path, monkeypatch,
) -> None:
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    first = ForwardLedger(tmp_path / "first.sqlite3")
    second = ForwardLedger(tmp_path / "second.sqlite3")
    _seed_bulk_news(first, 65)
    _seed_bulk_news(second, 65, reverse=True)
    first_calls: list[dict] = []
    second_calls: list[dict] = []
    accountant = CallbackModelAccountant(lambda usage: True)
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(first_calls),
    )
    first_result = daily_brief.update_daily_brief(
        first, api_key="test-key", request_accountant=accountant, now=now,
    )
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(second_calls),
    )
    second_result = daily_brief.update_daily_brief(
        second, api_key="test-key", request_accountant=accountant, now=now,
    )
    assert first_result["eligible_items"] == second_result["eligible_items"] == 65
    assert first_result["candidate_items"] == second_result["candidate_items"] == 60

    first_ids = [item.split(":", 2)[1] for item in first_calls[0]["evidence_ids"]]
    second_ids = [item.split(":", 2)[1] for item in second_calls[0]["evidence_ids"]]
    assert first_ids == second_ids
    assert len(first_ids) == daily_brief.BRIEF_EVIDENCE_LIMIT
    assert first_ids[0] == "bulk-005"
    assert first_ids[-1] == "bulk-064"
    first_hash = first.connection.execute(
        "SELECT source_hash FROM daily_news_briefs"
    ).fetchone()[0]
    second_hash = second.connection.execute(
        "SELECT source_hash FROM daily_news_briefs"
    ).fetchone()[0]
    assert first_hash == second_hash

    _seed_news_item(first, "bulk-065", minute=65)
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(first_calls),
    )
    assert daily_brief.update_daily_brief(
        first,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(minutes=1),
    )["reason"] == "ADAPTIVE_REFRESH_WAIT"
    later_result = daily_brief.update_daily_brief(
        first,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(hours=2, minutes=1),
    )
    assert later_result["eligible_items"] == 66
    assert first_calls[-1]["evidence_ids"][-1] == "Reuters:bulk-065:1"
    later_hash = first.connection.execute(
        """SELECT source_hash FROM daily_news_briefs
           ORDER BY revision_number DESC LIMIT 1"""
    ).fetchone()[0]
    assert later_hash != first_hash
    first.close()
    second.close()


def test_adaptive_refresh_state_survives_restart(tmp_path, monkeypatch) -> None:
    path = tmp_path / "forward.sqlite3"
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    calls = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(calls),
    )
    accountant = CallbackModelAccountant(lambda usage: True)
    ledger = ForwardLedger(path)
    _seed_news(ledger)
    assert daily_brief.update_daily_brief(
        ledger, api_key="test-key", request_accountant=accountant, now=now,
    )["status"] == "OK"

    _seed_news_item(ledger, "item-2", minute=30)
    settling = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(minutes=1),
    )
    assert settling["status"] == "DEFERRED"
    assert settling["reason"] == "ADAPTIVE_REFRESH_WAIT"
    ledger.close()

    restarted = ForwardLedger(path)
    assert daily_brief.update_daily_brief(
        restarted,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(minutes=5),
    )["reason"] == "ADAPTIVE_REFRESH_WAIT"
    assert daily_brief.update_daily_brief(
        restarted,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(hours=2, minutes=1),
    )["status"] == "OK"
    assert len(calls) == 2
    restarted.close()


def test_non_candidate_change_does_not_regenerate(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_bulk_news(ledger, 65)
    calls = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(calls),
    )
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    accountant = CallbackModelAccountant(lambda usage: True)
    assert daily_brief.update_daily_brief(
        ledger, api_key="test-key", request_accountant=accountant, now=now,
    )["status"] == "OK"

    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_annotations
               (annotation_id,source,source_item_id,revision_number,raw_content_hash,
                event_type,entities_json,hawkishness,inflation_impulse,growth_impulse,
                geopolitical_risk,usd_impulse,novelty,confidence,llm_model_version,
                prompt_version,parse_started_at,parsed_at,annotation_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "replacement-bulk-000", "Reuters", "bulk-000", 1,
                "hash-bulk-000", "market", "[]", 0, 0, 0, 0, 0, 0.8,
                0.9, daily_brief.DEFAULT_GEMINI_MODEL, daily_brief.PROMPT_VERSION,
                "2026-08-10T03:00:00+00:00",
                "2026-08-10T03:01:00+00:00",
                json.dumps({
                    "summary_zh": "仅改变候选窗口之外的旧资料",
                    "primary_category": "油价/能源",
                    "review_priority": "NORMAL", "materiality": 0.8,
                    "material_event_key": "event-bulk-000",
                }),
            ),
        )
    result = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(minutes=2),
    )
    assert result["status"] == "UNCHANGED"
    assert result["reason"] == "CANDIDATES_UNCHANGED"
    assert result["phase"] == "UPDATING"
    assert len(calls) == 1
    ledger.close()


def test_daily_brief_defers_without_capacity(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)

    def no_capacity(*_args, **_kwargs):
        raise ModelGatewayCapacityExhausted("test capacity")

    monkeypatch.setattr(daily_brief, "generate_metered_json", no_capacity)
    result = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: False),
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )
    assert result["status"] == "DEFERRED"
    assert result["reason"] == "MODEL_CAPACITY_DEFERRED"
    assert not ledger.connection.execute("SELECT 1 FROM daily_news_briefs").fetchone()
    ledger.close()


def test_provider_pacing_deferral_keeps_typed_reason_and_sends_no_http(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    _seed_news(ledger)
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=now,
    )
    _seed_news_item(ledger, "material-2", minute=40)
    _seed_news_item(ledger, "material-3", minute=41)
    blocked_until = datetime.now(UTC) + timedelta(hours=1)
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_ai_provider_dispatch_state_v1
               (provider_scope,next_eligible_at,interval_ms,success_streak,
                throttle_count,cooldown_until,last_outcome,updated_at)
               VALUES ('GOOGLE_GENERATIVE_LANGUAGE',?,250,0,0,NULL,'DISPATCHED',?)""",
            (blocked_until.isoformat(), datetime.now(UTC).isoformat()),
        )
    http_calls: list[object] = []
    monkeypatch.setattr(
        GeminiModelGateway, "_post_json",
        lambda *_args, **_kwargs: http_calls.append(kwargs) or {},
    )
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", annotation.generate_metered_json,
    )
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("routine", ROUTINE_POOL, "secret", "credential"),
        urgent=False,
    )

    result = daily_brief.update_daily_brief(
        ledger, api_key="secret", request_accountant=accountant,
        now=now + timedelta(hours=1, minutes=1),
    )

    assert result["reason"] == "PROVIDER_DISPATCH_DEFERRED"
    assert result["reason"] != "NO_GEMMA_CAPACITY"
    assert http_calls == []
    task = ledger.connection.execute(
        """SELECT task_class,last_pressure_json
           FROM news_ai_provider_dispatch_task_state_v1"""
    ).fetchone()
    assert task["task_class"] == "DAILY_BRIEF"
    assert json.loads(task["last_pressure_json"])["backlog"] >= 2
    assert not ledger.connection.execute(
        "SELECT 1 FROM news_ai_account_request_usage_v1"
    ).fetchone()
    ledger.close()


def test_real_account_capacity_keeps_model_capacity_reason(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    policy = quota_surface_for_model(daily_brief.DEFAULT_GEMMA_MODEL)
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_ai_account_daily_usage_v1
               (quota_day,account_id,model_family,request_count,updated_at)
               VALUES (?,?,?,?,?)""",
            (
                quota_day(datetime.now(UTC)), "routine",
                daily_brief.DEFAULT_GEMMA_MODEL, policy.daily_limit,
                datetime.now(UTC).isoformat(),
            ),
        )
    http_calls: list[object] = []
    monkeypatch.setattr(
        GeminiModelGateway, "_post_json",
        lambda *_args, **_kwargs: http_calls.append(kwargs) or {},
    )
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("routine", ROUTINE_POOL, "secret", "credential"),
        urgent=False,
    )

    result = daily_brief.update_daily_brief(
        ledger, api_key="secret", request_accountant=accountant,
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )

    assert result["reason"] == "MODEL_CAPACITY_DEFERRED"
    assert http_calls == []
    ledger.close()


def test_minor_same_event_churn_waits_without_generation(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    _seed_news_item(ledger, "original", minute=1, material_event_key="episode-1")
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    accountant = CallbackModelAccountant(lambda _: True)
    daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=accountant, now=now,
    )
    _seed_news_item(
        ledger, "syndicated-update", minute=30,
        material_event_key="episode-1", materiality=0.2,
    )

    result = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=accountant,
        now=now + timedelta(hours=1),
    )

    assert result["reason"] == "CANDIDATES_UNCHANGED"
    assert len(calls) == 1
    ledger.close()


def test_material_accumulation_and_major_event_refresh_early(
    tmp_path, monkeypatch,
) -> None:
    accountant = CallbackModelAccountant(lambda _: True)
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)

    accumulated = ForwardLedger(tmp_path / "accumulated.sqlite3")
    _seed_news(accumulated)
    accumulated_calls: list[dict] = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(accumulated_calls),
    )
    daily_brief.update_daily_brief(
        accumulated, api_key="test", request_accountant=accountant, now=now,
    )
    _seed_news_item(accumulated, "event-2", minute=30)
    _seed_news_item(accumulated, "event-3", minute=31)
    accumulated_result = daily_brief.update_daily_brief(
        accumulated, api_key="test", request_accountant=accountant,
        now=now + timedelta(hours=1, minutes=1),
    )
    assert accumulated_result["status"] == "OK"
    assert len(accumulated_calls) == 2
    accumulated.close()

    major = ForwardLedger(tmp_path / "major.sqlite3")
    _seed_news(major)
    major_calls: list[dict] = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(major_calls),
    )
    daily_brief.update_daily_brief(
        major, api_key="test", request_accountant=accountant, now=now,
    )
    _seed_news_item(
        major, "cpi", minute=20, category="inflation", materiality=0.95,
    )
    major_result = daily_brief.update_daily_brief(
        major, api_key="test", request_accountant=accountant,
        now=now + timedelta(minutes=31),
    )
    assert major_result["status"] == "OK"
    assert len(major_calls) == 2
    major.close()


def test_pipeline_pressure_yields_then_aging_prevents_starvation(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    _seed_news(ledger)
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    accountant = CallbackModelAccountant(lambda _: True)
    daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=accountant, now=now,
    )
    _seed_news_item(ledger, "event-2", minute=30)
    _seed_news_item(ledger, "event-3", minute=31)
    for index in range(10):
        enqueue_job(
            ledger.connection, task_type="ACTIVE_IMPACT", source="pressure",
            source_item_id=str(index), revision_number=1,
            annotation_id=f"annotation-{index}", prompt_version="impact-test",
            priority="NORMAL", now=now,
        )

    busy = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=accountant,
        now=now + timedelta(hours=2, minutes=1),
    )
    assert busy["reason"] == "ADAPTIVE_REFRESH_WAIT"
    assert len(calls) == 1

    aged = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=accountant,
        now=now + timedelta(hours=6, minutes=1),
    )
    assert aged["status"] == "OK"
    assert len(calls) == 2
    ledger.close()


def test_daily_brief_rejects_fake_evidence(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    envelope = {"candidates": [{"content": {"parts": [{"text": json.dumps({
        "title": "今日黄金新闻",
        "overview": "今日主要变化",
        "drivers": ["资料显示黄金出现变化。"],
        "watch_next": "继续关注后续变化。",
        "items": [{
            "headline": "黄金变化",
            "summary": "没有真实引用",
            "evidence_ids": ["invented:item:1"],
        }],
    })}]}}]}

    def fake_generation(_api_key, **kwargs):
        try:
            return kwargs["decode"](envelope), "gemma-test"
        except ValueError as error:
            raise ModelGatewayResponseInvalid(error) from error

    monkeypatch.setattr(daily_brief, "generate_metered_json", fake_generation)
    result = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )
    assert result["status"] == "DEFERRED"
    assert result["reason"] == "MODEL_OUTPUT_CONTRACT_FAILED"
    failure = ledger.connection.execute(
        """SELECT failure_code,error_type,error,failure_evidence_json
           FROM daily_news_brief_failures_v1"""
    ).fetchone()
    assert tuple(failure[:2]) == (
        "MODEL_OUTPUT_CONTRACT_FAILED", "DailyBriefEvidenceContractFailed",
    )
    assert "invented:item:1" in failure["error"]
    assert "没有真实引用" not in failure["error"]
    evidence = json.loads(failure["failure_evidence_json"])
    assert evidence["selected_output"] == {
        "allowed_evidence_count": 1,
        "unknown_evidence_ids": ["invented:item:1"],
    }
    assert len(evidence["response_hash"]) == 64
    summary = daily_brief.daily_brief_summary(
        ledger.connection, now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )
    assert summary["last_failure_evidence"] == evidence
    assert not ledger.connection.execute("SELECT 1 FROM daily_news_briefs").fetchone()
    ledger.close()


def test_daily_brief_classifies_malformed_model_output(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)

    def invalid_generation(_api_key, **_kwargs):
        raise ModelGatewayResponseInvalid(ValueError("invalid response shape"))

    monkeypatch.setattr(daily_brief, "generate_metered_json", invalid_generation)
    result = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )
    failure = ledger.connection.execute(
        "SELECT failure_code,error_type,error FROM daily_news_brief_failures_v1"
    ).fetchone()
    assert result["reason"] == "MODEL_OUTPUT_INVALID"
    assert tuple(failure) == (
        "MODEL_OUTPUT_INVALID", "ValueError", "invalid response shape",
    )
    ledger.close()


def test_daily_brief_failure_evidence_column_upgrades_existing_ledger(
    tmp_path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE daily_news_brief_failures_v1 (
           failure_id TEXT PRIMARY KEY, brief_date TEXT NOT NULL,
           attempt_number INTEGER NOT NULL, failure_code TEXT NOT NULL,
           error_type TEXT NOT NULL, error_signature TEXT NOT NULL,
           error TEXT NOT NULL, failed_at TEXT NOT NULL,
           next_retry_at TEXT NOT NULL, UNIQUE(brief_date,attempt_number))"""
    )
    connection.close()

    ledger = ForwardLedger(path)
    columns = {
        row["name"] for row in ledger.connection.execute(
            "PRAGMA table_info(daily_news_brief_failures_v1)"
        )
    }
    assert "failure_evidence_json" in columns
    ledger.close()


def test_daily_brief_excludes_future_annotations(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    cutoff = datetime(2026, 8, 10, 3, tzinfo=UTC)
    _seed_news_item(
        ledger,
        "future-annotation",
        minute=1,
        parsed_at=cutoff + timedelta(seconds=1),
    )
    result = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        now=cutoff,
    )
    assert result["status"] == "NO_REVIEWED_NEWS"
    assert result["received_items"] == result["pending_items"] == 1
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


def test_current_day_reports_partial_review_progress(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    _seed_news_item(ledger, "ready", minute=1)
    _seed_news_item(ledger, "pending", minute=2, parsed_at=now + timedelta(hours=1))
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation([]))
    result = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=now,
    )
    assert result["phase"] == "UPDATING"
    assert (result["reviewed_items"], result["received_items"], result["pending_items"]) == (1, 2, 1)
    summary = daily_brief.daily_brief_summary(ledger.connection, now=now)
    assert summary["phase"] == "UPDATING"
    assert summary["is_final"] is False
    ledger.close()


def test_previous_day_finishes_after_midnight_when_late_annotation_arrives(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    parsed = datetime(2026, 8, 11, 0, 5, tzinfo=UTC)
    _seed_news_item(ledger, "late", minute=1, parsed_at=parsed)
    before = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", now=parsed - timedelta(minutes=1),
        api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
    )
    assert before["phase"] == "UPDATING"
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation([]))
    after = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", now=parsed + timedelta(minutes=1),
        api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
    )
    assert after["phase"] == "FINAL"
    assert ledger.connection.execute(
        "SELECT final_status FROM daily_news_brief_finalizations_v1 WHERE brief_date='2026-08-10'"
    ).fetchone()[0] == "FINAL"
    ledger.close()


def test_important_early_event_survives_full_day_candidate_bound(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    for index in range(100):
        _seed_news_item(
            ledger, f"ordinary-{index:03d}", minute=index + 1,
            review_priority="NORMAL",
        )
    _seed_news_item(ledger, "morning-policy", minute=0, review_priority="IMMEDIATE")
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    result = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )
    assert result["candidate_items"] == daily_brief.BRIEF_EVIDENCE_LIMIT
    assert "Reuters:morning-policy:1" in calls[0]["evidence_ids"]
    ledger.close()


def test_daily_brief_packet_keeps_strongest_evidence_within_tpm_budget(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    for index in range(60):
        _seed_news_item(
            ledger, f"ordinary-{index:03d}", minute=index + 1,
            summary="黄金市场背景与宏观变化" * 60,
        )
    _seed_news_item(
        ledger, "priority-policy", minute=0, review_priority="IMMEDIATE",
        summary="重要政策变化" * 80,
    )
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))

    result = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )

    serialized = json.dumps(
        calls[0]["payload"], ensure_ascii=False, separators=(",", ":"),
    )
    assert result["candidate_items"] == daily_brief.BRIEF_EVIDENCE_LIMIT
    assert len(calls[0]["evidence"]) < result["candidate_items"]
    assert "Reuters:priority-policy:1" in calls[0]["evidence_ids"]
    assert (
        daily_brief.conservative_input_token_estimate(serialized)
        <= daily_brief.BRIEF_INPUT_TOKEN_BUDGET
    )
    assert daily_brief.conservative_input_token_estimate(serialized) + 512 < 15_000
    ledger.close()


def test_structured_brief_output_budget_covers_multi_item_contract() -> None:
    payload = daily_brief._brief_payload("2026-08-16", [{
        "id": "source:item:1", "headline": "标题", "summary": "摘要",
        "category": "宏观", "impact_class": "DATA_RELEASE",
        "update_type": "NEW_EVENT", "published_at": None,
        "received_at": "2026-08-16T00:00:00+00:00",
    }])
    config = payload["generationConfig"]

    assert config["maxOutputTokens"] == daily_brief.BRIEF_OUTPUT_TOKEN_BUDGET
    assert config["maxOutputTokens"] >= 4_096
    assert config["responseSchema"]["required"] == [
        "title", "overview", "drivers", "watch_next", "items",
    ]
    assert config["responseSchema"]["properties"]["drivers"]["maxItems"] == 3
    assert config["responseSchema"]["properties"]["overview"]["maxLength"] == 180
    assert config["responseSchema"]["properties"]["items"]["maxItems"] == 5
    assert config["thinkingConfig"] == {"thinkingLevel": "minimal"}
    prompt = payload["contents"][0]["parts"][0]["text"]
    packet = json.loads(prompt.split("\nEVIDENCE\n", 1)[1])
    assert packet[0]["ref"] == "E01"
    assert "id" not in packet[0]
    assert config["responseSchema"]["properties"]["items"]["items"][
        "properties"
    ]["evidence_ids"]["items"]["enum"] == ["E01"]


def test_daily_brief_rejects_incomplete_provider_completion() -> None:
    evidence = [{"id": "source:item:1"}]
    envelope = {
        "candidates": [{
            "finishReason": "MAX_TOKENS",
            "content": {"parts": [{"text": '{"title":"truncated"'}]},
        }],
    }

    with pytest.raises(ValueError, match="ended with MAX_TOKENS"):
        daily_brief._decode_brief(envelope, evidence)


def test_short_citation_is_mapped_to_exact_evidence_id() -> None:
    evidence = [{"id": "Reuters:opaque-long-item:7"}]
    envelope = {"candidates": [{"content": {"parts": [{"text": json.dumps({
        "title": "黄金简报",
        "overview": "宏观资料共同显示市场定价正在变化。",
        "drivers": ["政策路径正在被重新评估。"],
        "watch_next": "关注后续政策信号。",
        "items": [{
            "headline": "政策预期变化",
            "summary": "市场重新评估政策路径。",
            "evidence_ids": ["E01"],
        }],
    })}]}}]}

    result = daily_brief._decode_brief(envelope, evidence)

    assert result["items"][0]["evidence_ids"] == ["Reuters:opaque-long-item:7"]
    assert result["drivers"] == ["政策路径正在被重新评估。"]
    assert result["watch_next"] == "关注后续政策信号。"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("overview", ""),
        ("drivers", []),
        ("drivers", [""]),
        ("watch_next", ""),
    ),
)
def test_daily_brief_rejects_incomplete_reading_structure(field, value) -> None:
    result = {
        "title": "黄金简报",
        "overview": "黄金市场定价正在变化。",
        "drivers": ["政策路径正在被重新评估。"],
        "watch_next": "关注后续政策信号。",
        "items": [],
    }
    result[field] = value
    envelope = {"candidates": [{"content": {"parts": [{
        "text": json.dumps(result),
    }]}}]}

    with pytest.raises(ValueError, match="returned an invalid result"):
        daily_brief._decode_brief(envelope, [])


def test_duplicate_event_flood_consumes_one_candidate(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    for index in range(40):
        _seed_news_item(
            ledger, f"duplicate-{index:03d}", minute=index,
            material_event_key="same-real-world-event",
        )
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    result = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )
    assert result["eligible_items"] == 40
    assert result["candidate_items"] == len(calls[0]["evidence"]) == 1
    ledger.close()


def test_summary_total_is_not_recent_list_length(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    with ledger.connection:
        for index in range(100):
            day = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)).date().isoformat()
            ledger.connection.execute(
                "INSERT INTO daily_news_briefs VALUES (?,?,?,?,?,?,?,?)",
                (day, 1, f"hash-{index}", f"{day}T00:00:00+00:00",
                 f"{day}T00:00:00+00:00", "gemma", "v2", '{"title":"x","items":[]}'),
            )
    assert len(daily_brief.recent_daily_briefs(ledger.connection, limit=3)) == 3
    assert daily_brief.daily_brief_summary(
        ledger.connection, now=datetime(2026, 8, 10, tzinfo=UTC),
    )["total_brief_days"] == 100
    ledger.close()


def test_closed_empty_date_has_explicit_empty_phase(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    result = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-09", now=datetime(2026, 8, 10, 3, tzinfo=UTC),
        api_key=None, request_accountant=None,
    )
    assert result["status"] == "NO_NEWS"
    assert result["phase"] == "EMPTY"
    ledger.close()


def test_routine_only_account_generates_daily_brief(tmp_path, monkeypatch) -> None:
    from scripts.run_news_annotator import run_daily_brief_batch

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation([]))
    statuses = run_daily_brief_batch(
        ledger, now=datetime(2026, 8, 10, 3, tzinfo=UTC),
        credentials=(ApiCredential("routine-account", ROUTINE_POOL, "secret", "key-1"),),
    )
    current = next(row for row in statuses if row["brief_date"] == "2026-08-10")
    assert current["status"] == "OK"
    assert current["pool"] == ROUTINE_POOL
    assert current["account_id"] == "routine-account"
    assert "secret" not in json.dumps(statuses)
    ledger.close()


def test_daily_brief_reranks_account_headroom_for_each_date(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credentials = (
        ApiCredential("account-a", ROUTINE_POOL, "secret-a", "key-a"),
        ApiCredential("account-b", ROUTINE_POOL, "secret-b", "key-b"),
    )
    rankings = iter(((credentials[0], credentials[1]), (credentials[1], credentials[0])))
    monkeypatch.setattr(
        runner, "brief_dates_to_process",
        lambda *_args, **_kwargs: ["2026-08-16", "2026-08-15"],
    )
    monkeypatch.setattr(
        runner, "credentials_for_background_task",
        lambda *_args, **_kwargs: next(rankings),
    )
    monkeypatch.setattr(
        runner, "update_daily_brief",
        lambda _ledger, *, brief_date, **_kwargs: {
            "status": "OK", "brief_date": brief_date,
        },
    )

    statuses = runner.run_daily_brief_batch(
        ledger, now=datetime(2026, 8, 16, tzinfo=UTC), credentials=credentials,
    )

    assert [row["account_id"] for row in statuses] == ["account-a", "account-b"]
    ledger.close()


def test_annotator_cycle_reconciles_jobs_before_brief_and_reserves_capacity(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    calls: list[str] = []
    monkeypatch.setattr(
        runner, "sync_pending_jobs",
        lambda connection, **kwargs: calls.append("reconcile") or {},
    )
    monkeypatch.setattr(
        runner, "run_daily_brief_batch",
        lambda ledger: calls.append("daily_brief") or [],
    )
    monkeypatch.setattr(
        runner, "run_scheduled_batch_with_lock_retry",
        lambda ledger, **kwargs: calls.append("annotation") or [],
    )
    monkeypatch.setattr(
        sys, "argv",
        [
            "run_news_annotator.py",
            "--database", str(tmp_path / "forward.sqlite3"),
            "--status-file", str(tmp_path / "status.json"),
            "--once",
        ],
    )

    assert runner.main() == 0
    assert calls == ["reconcile", "daily_brief", "annotation"]


def test_capacity_blocked_brief_leaves_gemma_window_for_retry(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    scheduled: list[frozenset[str]] = []
    monkeypatch.setattr(
        runner, "run_daily_brief_batch",
        lambda ledger: [{
            "status": "DEFERRED", "reason": "MODEL_CAPACITY_DEFERRED",
            "account_id": "brief-account",
        }],
    )
    monkeypatch.setattr(
        runner, "run_scheduled_batch_with_lock_retry",
        lambda ledger, **kwargs: scheduled.append(
            kwargs.get("gemma_reserved_accounts", frozenset())
        ) or [],
    )
    monkeypatch.setattr(
        sys, "argv",
        [
            "run_news_annotator.py",
            "--database", str(tmp_path / "forward.sqlite3"),
            "--status-file", str(tmp_path / "status.json"),
            "--once",
        ],
    )

    assert runner.main() == 0
    assert scheduled == [frozenset({"brief-account"})]


def test_terminal_annotation_failure_settles_historical_date_as_degraded(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 11, 3, tzinfo=UTC)
    _seed_news_item(ledger, "terminal", minute=1, parsed_at=now + timedelta(hours=1))
    enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
        source_item_id="terminal", revision_number=1,
        prompt_version=daily_brief.PROMPT_VERSION, priority="NORMAL", now=now,
    )
    with ledger.connection:
        ledger.connection.execute(
            """UPDATE news_ai_jobs_v1 SET state='DEAD_LETTER',updated_at=?
               WHERE task_type='ACTIVE_ANNOTATION' AND source_item_id='terminal'""",
            (now.isoformat(),),
        )
    result = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", now=now,
        api_key=None, request_accountant=None,
    )
    assert result["phase"] == "DEGRADED"
    assert result["pending_items"] == 0
    assert result["terminal_failure_items"] == 1
    assert daily_brief.recent_daily_briefs(ledger.connection)[0]["is_final"] is True
    ledger.close()


def test_cross_date_superseding_evidence_does_not_settle_historical_items(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 11, 3, tzinfo=UTC)
    _seed_news_item(
        ledger, "superseded-revision", minute=1,
        parsed_at=now + timedelta(hours=1),
    )
    _seed_news_item(
        ledger, "superseded-cluster", minute=2,
        parsed_at=now + timedelta(hours=1),
    )
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_revisions
               (source,source_item_id,revision_number,source_published_time,
                collector_first_seen_time,item_first_seen_time,fetched_time,
                headline,body,link,content_hash,cluster_id,collector_latency_seconds)
               SELECT source,source_item_id,2,source_published_time,?,
                      item_first_seen_time,?,headline,body,link,?,cluster_id,
                      collector_latency_seconds
               FROM news_revisions WHERE source_item_id='superseded-revision'""",
            (now.isoformat(), now.isoformat(), "hash-superseded-revision-v2"),
        )
        ledger.connection.execute(
            """INSERT INTO news_revisions
               (source,source_item_id,revision_number,source_published_time,
                collector_first_seen_time,item_first_seen_time,fetched_time,
                headline,body,link,content_hash,cluster_id,collector_latency_seconds)
               SELECT 'Preferred', 'preferred-peer', 1, source_published_time, ?,
                      ?, ?, 'Preferred peer', body || ' longer',
                      'https://example.com/preferred-peer', 'hash-preferred-peer',
                      cluster_id, collector_latency_seconds
               FROM news_revisions
               WHERE source_item_id='superseded-cluster'""",
            (now.isoformat(), now.isoformat(), now.isoformat()),
        )

    result = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", now=now,
        api_key=None, request_accountant=None,
    )

    assert result["phase"] == "UPDATING"
    assert result["pending_items"] == 2
    assert result["terminal_failure_items"] == 0
    ledger.close()


def test_protected_brief_day_claims_its_representative_despite_cross_date_peer(
    tmp_path,
) -> None:
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3",
        now=datetime(2026, 8, 10, 1, tzinfo=UTC),
    )
    _seed_unannotated_revision(
        ledger, "day-one", received_at=datetime(2026, 8, 10, 2, tzinfo=UTC),
        cluster_id="shared-cluster", body_length=300,
    )
    _seed_unannotated_revision(
        ledger, "day-two", received_at=datetime(2026, 8, 11, 2, tzinfo=UTC),
        cluster_id="shared-cluster", body_length=400,
    )

    general = annotation.pending_annotation_records(
        ledger.connection, observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    protected = annotation.pending_annotation_records(
        ledger.connection, observed_at=datetime(2026, 8, 12, tzinfo=UTC),
        priority_receipt_days=("2026-08-10",),
    )

    assert [row["source_item_id"] for row in general] == ["day-two"]
    assert {row["source_item_id"] for row in protected} == {"day-one", "day-two"}
    ledger.close()


def test_same_date_superseding_evidence_keeps_new_representatives_pending(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 11, 3, tzinfo=UTC)
    same_day_later = datetime(2026, 8, 10, 4, tzinfo=UTC).isoformat()
    _seed_news_item(
        ledger, "superseded-revision", minute=1,
        parsed_at=now + timedelta(hours=1),
    )
    _seed_news_item(
        ledger, "superseded-cluster", minute=2,
        parsed_at=now + timedelta(hours=1),
    )
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO news_revisions
               (source,source_item_id,revision_number,source_published_time,
                collector_first_seen_time,item_first_seen_time,fetched_time,
                headline,body,link,content_hash,cluster_id,collector_latency_seconds)
               SELECT source,source_item_id,2,source_published_time,?,
                      item_first_seen_time,?,headline,body,link,?,cluster_id,
                      collector_latency_seconds
               FROM news_revisions WHERE source_item_id='superseded-revision'""",
            (same_day_later, same_day_later, "hash-superseded-revision-v2"),
        )
        ledger.connection.execute(
            """INSERT INTO news_revisions
               (source,source_item_id,revision_number,source_published_time,
                collector_first_seen_time,item_first_seen_time,fetched_time,
                headline,body,link,content_hash,cluster_id,collector_latency_seconds)
               SELECT 'Preferred', 'preferred-peer', 1, source_published_time, ?,
                      ?, ?, 'Preferred peer', body || ' longer',
                      'https://example.com/preferred-peer', 'hash-preferred-peer',
                      cluster_id, collector_latency_seconds
               FROM news_revisions
               WHERE source_item_id='superseded-cluster'""",
            (same_day_later, same_day_later, same_day_later),
        )

    result = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", now=now,
        api_key=None, request_accountant=None,
    )

    assert result["phase"] == "UPDATING"
    assert result["pending_items"] == 2
    assert result["terminal_failure_items"] == 0
    ledger.close()


def test_failed_regeneration_does_not_claim_candidate_was_generated(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    _seed_news(ledger)
    calls: list[dict] = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=now,
    )
    _seed_news_item(ledger, "new-candidate", minute=30)
    daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=now + timedelta(minutes=1),
    )

    monkeypatch.setattr(
        daily_brief, "generate_metered_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad output")),
    )
    failed = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=now + timedelta(hours=2, minutes=1),
    )
    assert failed["status"] == "DEFERRED"

    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    recovered = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=now + timedelta(hours=2, minutes=3),
    )
    assert recovered["status"] == "OK"
    assert recovered["revision_number"] == 2
    ledger.close()


def test_historical_generation_failure_has_finite_degraded_fallback(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    monkeypatch.setattr(
        daily_brief, "generate_metered_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad output")),
    )
    start = datetime(2026, 8, 11, 3, tzinfo=UTC)
    result = None
    for attempt in range(daily_brief.BRIEF_FAILURE_ATTEMPT_LIMIT):
        result = daily_brief.update_daily_brief(
            ledger, brief_date="2026-08-10", api_key="test",
            request_accountant=CallbackModelAccountant(lambda _: True),
            now=start + timedelta(hours=attempt * 2),
        )
    assert result and result["phase"] == "DEGRADED"
    assert result["reason"] == "GENERATION_FAILURE_TERMINAL_FALLBACK"
    row = daily_brief.recent_daily_briefs(ledger.connection)[0]
    assert row["model_version"] == "system-degraded-fallback"
    assert row["brief"]["drivers"]
    assert row["brief"]["watch_next"] == "关注上述已复核事件是否出现重要更新。"
    assert row["brief"]["items"][0]["evidence_ids"] == ["Reuters:item-1:1"]
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM daily_news_brief_failures_v1"
    ).fetchone()[0] == daily_brief.BRIEF_FAILURE_ATTEMPT_LIMIT
    ledger.close()


def test_degraded_finalization_is_recovered_with_append_only_correction(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    monkeypatch.setattr(
        daily_brief, "generate_metered_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad output")),
    )
    start = datetime(2026, 8, 11, 3, tzinfo=UTC)
    for attempt in range(daily_brief.BRIEF_FAILURE_ATTEMPT_LIMIT):
        daily_brief.update_daily_brief(
            ledger, brief_date="2026-08-10", api_key="test",
            request_accountant=CallbackModelAccountant(lambda _: True),
            now=start + timedelta(hours=attempt * 2),
        )

    calls: list[dict] = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(calls),
    )
    recovered = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", api_key="test",
        request_accountant=CallbackModelAccountant(lambda _: True),
        now=start + timedelta(hours=12),
    )

    assert recovered["phase"] == "FINAL"
    assert recovered["revision_number"] == 2
    assert ledger.connection.execute(
        """SELECT final_status FROM daily_news_brief_finalizations_v1
           WHERE brief_date='2026-08-10'"""
    ).fetchone()[0] == "DEGRADED"
    correction = ledger.connection.execute(
        """SELECT final_status,revision_number
           FROM daily_news_brief_finalization_corrections_v1
           WHERE brief_date='2026-08-10'"""
    ).fetchone()
    assert tuple(correction) == ("FINAL", 2)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.connection.execute(
            """UPDATE daily_news_brief_finalization_corrections_v1
               SET final_status='DEGRADED' WHERE brief_date='2026-08-10'"""
        )
    assert daily_brief.recent_daily_briefs(ledger.connection)[0]["model_version"] == "gemma-test"
    assert "2026-08-10" not in daily_brief.brief_dates_to_process(
        ledger.connection, now=start + timedelta(hours=13),
    )

    first_recovery_version = daily_brief.BRIEF_RECOVERY_VERSION
    monkeypatch.setattr(
        daily_brief, "BRIEF_RECOVERY_VERSION", "daily-brief-test-recovery-next",
    )
    assert "2026-08-10" in daily_brief.brief_dates_to_process(
        ledger.connection, now=start + timedelta(hours=13),
    )
    next_recovery = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", api_key="test",
        request_accountant=CallbackModelAccountant(lambda _: True),
        now=start + timedelta(hours=14),
    )
    corrections = ledger.connection.execute(
        """SELECT recovery_version,final_status,revision_number
           FROM daily_news_brief_finalization_corrections_v1
           WHERE brief_date='2026-08-10' ORDER BY revision_number"""
    ).fetchall()
    assert next_recovery["revision_number"] == 3
    assert [tuple(row) for row in corrections] == [
        (first_recovery_version, "FINAL", 2),
        ("daily-brief-test-recovery-next", "FINAL", 3),
    ]
    ledger.close()


def test_recovery_resumes_revision_after_finalization_interruption(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    monkeypatch.setattr(
        daily_brief, "generate_metered_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad output")),
    )
    start = datetime(2026, 8, 11, 3, tzinfo=UTC)
    for attempt in range(daily_brief.BRIEF_FAILURE_ATTEMPT_LIMIT):
        daily_brief.update_daily_brief(
            ledger, brief_date="2026-08-10", api_key="test",
            request_accountant=CallbackModelAccountant(lambda _: True),
            now=start + timedelta(hours=attempt * 2),
        )

    calls: list[dict] = []
    monkeypatch.setattr(
        daily_brief, "generate_metered_json", _fake_generation(calls),
    )
    original_finalize = daily_brief._finalize
    monkeypatch.setattr(
        daily_brief, "_finalize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated finalization interruption")
        ),
    )
    with pytest.raises(RuntimeError, match="finalization interruption"):
        daily_brief.update_daily_brief(
            ledger, brief_date="2026-08-10", api_key="test",
            request_accountant=CallbackModelAccountant(lambda _: True),
            now=start + timedelta(hours=12),
        )
    assert ledger.connection.execute(
        "SELECT count(*) FROM daily_news_briefs WHERE brief_date='2026-08-10'",
    ).fetchone()[0] == 2

    monkeypatch.setattr(daily_brief, "_finalize", original_finalize)
    resumed = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", api_key="test",
        request_accountant=CallbackModelAccountant(lambda _: True),
        now=start + timedelta(hours=13),
    )

    assert resumed["status"] == "UNCHANGED"
    assert resumed["phase"] == "FINAL"
    assert ledger.connection.execute(
        """SELECT revision_number FROM daily_news_brief_finalization_corrections_v1
           WHERE brief_date='2026-08-10'""",
    ).fetchone()[0] == 2
    assert len(calls) == 1
    assert ledger.connection.execute(
        "SELECT count(*) FROM daily_news_briefs WHERE brief_date='2026-08-10'",
    ).fetchone()[0] == 2
    ledger.close()


def test_degraded_fallback_resumes_after_finalization_interruption(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    monkeypatch.setattr(
        daily_brief, "generate_metered_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad output")),
    )
    start = datetime(2026, 8, 11, 3, tzinfo=UTC)
    for attempt in range(daily_brief.BRIEF_FAILURE_ATTEMPT_LIMIT - 1):
        daily_brief.update_daily_brief(
            ledger, brief_date="2026-08-10", api_key="test",
            request_accountant=CallbackModelAccountant(lambda _: True),
            now=start + timedelta(hours=attempt * 2),
        )

    original_finalize = daily_brief._finalize
    monkeypatch.setattr(
        daily_brief, "_finalize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated fallback finalization interruption")
        ),
    )
    with pytest.raises(RuntimeError, match="fallback finalization interruption"):
        daily_brief.update_daily_brief(
            ledger, brief_date="2026-08-10", api_key="test",
            request_accountant=CallbackModelAccountant(lambda _: True),
            now=start + timedelta(hours=12),
        )
    assert ledger.connection.execute(
        "SELECT count(*) FROM daily_news_briefs WHERE brief_date='2026-08-10'",
    ).fetchone()[0] == 1

    monkeypatch.setattr(daily_brief, "_finalize", original_finalize)
    resumed = daily_brief.update_daily_brief(
        ledger, brief_date="2026-08-10", api_key="test",
        request_accountant=CallbackModelAccountant(lambda _: True),
        now=start + timedelta(hours=13),
    )

    assert resumed["status"] == "OK"
    assert resumed["phase"] == "DEGRADED"
    assert resumed["revision_number"] == 1
    assert ledger.connection.execute(
        "SELECT count(*) FROM daily_news_briefs WHERE brief_date='2026-08-10'",
    ).fetchone()[0] == 1
    ledger.close()
