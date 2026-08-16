from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from xauusd_forecaster import daily_brief
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.model_gateway import ModelGatewayCapacityExhausted
from xauusd_forecaster.news_scheduler import ApiCredential, ROUTINE_POOL, enqueue_job
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
                        "primary_category": "油价/能源",
                        "review_priority": review_priority,
                        "materiality": 0.8,
                        "material_event_key": material_event_key or f"event-{item_id}",
                    }
                ),
            ),
        )


def _seed_news(ledger: ForwardLedger) -> None:
    _seed_news_item(ledger, "item-1", minute=1)


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
        calls.append({**kwargs, "evidence": evidence})
        return ({
            "title": "今日黄金新闻",
            "items": [{
                "headline": str(evidence[-1]["headline"]),
                "summary": "出现新变化",
                "evidence_ids": [str(evidence[-1]["id"])],
            }],
        }, "gemma-test")

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

    first_ids = [row["id"].split(":", 2)[1] for row in first_calls[0]["evidence"]]
    second_ids = [row["id"].split(":", 2)[1] for row in second_calls[0]["evidence"]]
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
    )["reason"] == "SOURCE_SETTLING"
    later_result = daily_brief.update_daily_brief(
        first,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(minutes=12),
    )
    assert later_result["eligible_items"] == 66
    assert first_calls[-1]["evidence"][-1]["id"] == "Reuters:bulk-065:1"
    later_hash = first.connection.execute(
        """SELECT source_hash FROM daily_news_briefs
           ORDER BY revision_number DESC LIMIT 1"""
    ).fetchone()[0]
    assert later_hash != first_hash
    first.close()
    second.close()


def test_regeneration_debounce_survives_restart(tmp_path, monkeypatch) -> None:
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
    assert settling["reason"] == "SOURCE_SETTLING"
    ledger.close()

    restarted = ForwardLedger(path)
    assert daily_brief.update_daily_brief(
        restarted,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(minutes=5),
    )["reason"] == "SOURCE_SETTLING"
    assert daily_brief.update_daily_brief(
        restarted,
        api_key="test-key",
        request_accountant=accountant,
        now=now + timedelta(minutes=12),
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
    assert result["reason"] == "NO_GEMMA_CAPACITY"
    assert not ledger.connection.execute("SELECT 1 FROM daily_news_briefs").fetchone()
    ledger.close()


def test_daily_brief_rejects_fake_evidence(tmp_path, monkeypatch) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _seed_news(ledger)
    envelope = {"candidates": [{"content": {"parts": [{"text": json.dumps({
        "title": "今日黄金新闻",
        "items": [{
            "headline": "黄金变化",
            "summary": "没有真实引用",
            "evidence_ids": ["invented:item:1"],
        }],
    })}]}}]}

    def fake_generation(_api_key, **kwargs):
        return kwargs["decode"](envelope), "gemma-test"

    monkeypatch.setattr(daily_brief, "generate_metered_json", fake_generation)
    result = daily_brief.update_daily_brief(
        ledger,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        now=datetime(2026, 8, 10, 3, tzinfo=UTC),
    )
    assert result["status"] == "DEFERRED"
    assert result["reason"] == "INVALID_MODEL_OUTPUT"
    failure = ledger.connection.execute(
        "SELECT failure_code,error_type FROM daily_news_brief_failures_v1"
    ).fetchone()
    assert tuple(failure) == ("INVALID_MODEL_OUTPUT", "ValueError")
    assert not ledger.connection.execute("SELECT 1 FROM daily_news_briefs").fetchone()
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
    assert any(row["id"] == "Reuters:morning-policy:1" for row in calls[0]["evidence"])
    ledger.close()


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
        now=now + timedelta(minutes=12),
    )
    assert failed["status"] == "DEFERRED"

    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    recovered = daily_brief.update_daily_brief(
        ledger, api_key="test", request_accountant=CallbackModelAccountant(lambda _: True),
        now=now + timedelta(minutes=14),
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
    assert row["brief"]["items"][0]["evidence_ids"] == ["Reuters:item-1:1"]
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM daily_news_brief_failures_v1"
    ).fetchone()[0] == daily_brief.BRIEF_FAILURE_ATTEMPT_LIMIT
    ledger.close()
