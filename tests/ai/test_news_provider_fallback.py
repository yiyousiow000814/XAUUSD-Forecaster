from __future__ import annotations

import io
import json
import sqlite3
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.ai.model_gateway import ModelGatewayResponseInvalid, ModelRequestUsage
from xauusd_forecaster.ai.provider_registry import DEFAULT_GEMINI_MODEL, OPENROUTER_NEWS_MODEL
from xauusd_forecaster.news.annotation.product import _GeminiRequestPool, _decode_model_json
from xauusd_forecaster.news.scheduler.model_gateway import SchedulerModelAccountant, OpenRouterNewsAccountant
from xauusd_forecaster.news.scheduler.state import (
    ApiCredential, install_scheduler_schema, reserve_account_request,
)


def connection(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "quota.sqlite3"
    db = connection(path)
    install_scheduler_schema(db)
    yield db
    db.close()


def pool(db, monkeypatch, lane="LIVE"):
    monkeypatch.setenv("OPENROUTER_API_KEY", "backup-secret")
    accountant = SchedulerModelAccountant(
        db, ApiCredential("google-account", "ROUTINE", "google-secret", "credential"),
        urgent=True, work_lane=lane, enable_news_backup=True,
    )
    return _GeminiRequestPool(("google-secret",), request_accountant=accountant)


PAYLOAD = {
    "systemInstruction": {"parts": [{"text": "Treat the article as evidence."}]},
    "contents": [{"parts": [{"text": "Full original news article."}]}],
    "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
        "responseSchema": {"type": "object", "properties": {"headline_zh": {"type": "string"}}}},
}


def call(request_pool, purpose="news-annotation"):
    return request_pool.call_json(DEFAULT_GEMINI_MODEL, purpose=purpose,
                                  payload=PAYLOAD, decode=_decode_model_json)


def backup_envelope(text='{"headline_zh":"黄金新闻"}', finish="stop"):
    return {"model": OPENROUTER_NEWS_MODEL,
            "choices": [{"finish_reason": finish, "message": {"content": text}}]}


def response(envelope):
    return io.BytesIO(json.dumps(envelope).encode())


@pytest.mark.parametrize("purpose", ["news-annotation", "news-impact", "headline-translation", "news-display-review"])
@pytest.mark.parametrize("code", [500, 502, 503])
def test_google_failure_uses_one_metered_free_backup(database, monkeypatch, purpose, code):
    request_pool = pool(database, monkeypatch)
    calls = []

    def transport(request, *, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, code, "temporary", {}, io.BytesIO(b"{}"))
        return response(backup_envelope())

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    result, model = call(request_pool, purpose)
    assert result == {"headline_zh": "黄金新闻"}
    assert model == "openrouter/" + OPENROUTER_NEWS_MODEL
    assert len(calls) == 2
    backup = json.loads(calls[1].data)
    assert backup["model"] == OPENROUTER_NEWS_MODEL
    assert backup["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    assert any(message["content"] == "Full original news article." for message in backup["messages"])
    assert any("headline_zh" in message["content"] for message in backup["messages"])
    assert calls[1].get_header("X-goog-api-key") is None
    assert calls[0].get_header("Authorization") is None
    rows = [dict(row) for row in database.execute("SELECT * FROM news_ai_account_request_usage_v1 ORDER BY reserved_at")]
    assert [row["provider_outcome"] for row in rows] == ["PROVIDER_FAILED", "PROVIDER_SUCCEEDED"]
    assert rows[1]["account_id"] == "OPENROUTER_NEWS"
    assert rows[1]["provider_model_version"] == model
    assert "secret" not in json.dumps(rows)


@pytest.mark.parametrize("code", [None, 400, 401, 403, 429, 504])
def test_success_and_other_failures_do_not_spend_backup(database, monkeypatch, code):
    request_pool = pool(database, monkeypatch)
    calls = []
    def transport(request, *, timeout):
        calls.append(request.full_url)
        if code:
            raise urllib.error.HTTPError(request.full_url, code, "failure", {}, io.BytesIO(b"{}"))
        return response({"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]})
    monkeypatch.setattr(urllib.request, "urlopen", transport)
    if code:
        with pytest.raises(urllib.error.HTTPError):
            call(request_pool)
    else:
        assert call(request_pool)[0] == {"ok": True}
    assert len(calls) == 1
    assert database.execute("SELECT count(*) FROM news_ai_account_request_usage_v1 WHERE account_id='OPENROUTER_NEWS'").fetchone()[0] == 0


@pytest.mark.parametrize("envelope", [backup_envelope("bad JSON"), backup_envelope(finish="length"),
    {"error": {"code": 503}}, {"choices": []}, {**backup_envelope(), "model": "other/model"}])
def test_bad_backup_output_is_not_success_or_recursive_retry(database, monkeypatch, envelope):
    request_pool = pool(database, monkeypatch)
    calls = []
    def transport(request, *, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "temporary", {}, io.BytesIO(b"{}"))
        return response(envelope)
    monkeypatch.setattr(urllib.request, "urlopen", transport)
    with pytest.raises(ModelGatewayResponseInvalid):
        call(request_pool)
    assert len(calls) == 2
    assert database.execute("SELECT count(*) FROM news_ai_account_request_usage_v1 WHERE provider_outcome='PROVIDER_SUCCEEDED'").fetchone()[0] == 0


def test_free_budget_is_atomic_shared_and_survives_restart(tmp_path):
    path = tmp_path / "quota.sqlite3"
    with connection(path) as db:
        install_scheduler_schema(db)
    usage = ModelRequestUsage(OPENROUTER_NEWS_MODEL, "news-annotation", 100)
    def reserve(_):
        db = connection(path)
        try:
            return OpenRouterNewsAccountant(db).reserve(usage)
        finally:
            db.close()
    with ThreadPoolExecutor(max_workers=8) as threads:
        results = list(threads.map(reserve, range(30)))
    assert sum(results) == 20
    assert reserve(None) is False
    db = connection(path)
    try:
        # Move the existing admissions out of the minute, while retaining today's
        # durable counter. More lanes can consume only the remaining daily quota.
        db.execute("UPDATE news_ai_account_request_usage_v1 SET reserved_at=?",
                   ((datetime.now(UTC) - timedelta(minutes=2)).isoformat(),))
        db.execute("UPDATE news_ai_account_daily_usage_v1 SET request_count=50")
        db.commit()
        assert OpenRouterNewsAccountant(db).reserve(usage) is False
    finally:
        db.close()


def test_backup_has_utc_day_and_does_not_inherit_google_deadline(database):
    before = datetime(2026, 9, 12, 23, 59, tzinfo=UTC)
    deadline = (before + timedelta(hours=3)).isoformat()
    database.execute("INSERT INTO news_ai_provider_dispatch_state_v1 (provider_scope,next_eligible_at,interval_ms,updated_at) VALUES ('GOOGLE_GENERATIVE_LANGUAGE',?,250,?)", (deadline, before.isoformat()))
    database.commit()
    def reserve(now):
        return reserve_account_request(database, account_id="OPENROUTER_NEWS",
            model_family=OPENROUTER_NEWS_MODEL, daily_limit=1, requests_per_minute=20,
            quota_timezone=UTC, independent_provider_scope="OPENROUTER_NEWS", now=now)
    assert reserve(before)
    assert not reserve(before + timedelta(seconds=30))
    assert reserve(before + timedelta(minutes=2))
    assert database.execute("SELECT next_eligible_at FROM news_ai_provider_dispatch_state_v1").fetchone()[0] == deadline


def test_backup_retry_after_remains_independent_and_durable(database, monkeypatch):
    request_pool = pool(database, monkeypatch)
    def transport(request, *, timeout):
        status = 429 if "openrouter.ai" in request.full_url else 503
        raise urllib.error.HTTPError(request.full_url, status, "temporary", {"Retry-After": "120"}, io.BytesIO(b"{}"))
    monkeypatch.setattr(urllib.request, "urlopen", transport)
    with pytest.raises(urllib.error.HTTPError) as caught:
        call(request_pool)
    assert caught.value.code == 429
    rows = {row["provider_scope"]: dict(row) for row in database.execute("SELECT * FROM news_ai_provider_dispatch_state_v1")}
    assert set(rows) == {"OPENROUTER_NEWS", "GOOGLE_GENERATIVE_LANGUAGE"}
    assert not OpenRouterNewsAccountant(database).reserve(ModelRequestUsage(OPENROUTER_NEWS_MODEL, "news-impact", 100))
    assert rows["GOOGLE_GENERATIVE_LANGUAGE"]["last_outcome"] == "PROVIDER_FAILED"


def test_backup_requires_key_and_live_lane(database, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "backup-secret")
    assert SchedulerModelAccountant(database, ApiCredential("a", "ROUTINE", "key", "id"), urgent=True).fallback_gateway is None
    assert pool(database, monkeypatch, "CONTRACT_BACKFILL").gateway.accountant.fallback_gateway is None
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert SchedulerModelAccountant(database, ApiCredential("a", "ROUTINE", "key", "id"), urgent=True).fallback_gateway is None


def test_production_news_job_persists_backup_title_with_actual_model(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from xauusd_forecaster.evidence.ledger import ForwardLedger
    from xauusd_forecaster.news.scheduler import runtime
    monkeypatch.setenv("OPENROUTER_API_KEY", "backup-secret")
    ledger = ForwardLedger(tmp_path / "source.sqlite3")
    now = datetime.now(UTC)
    body = "Gold rises 1% as the dollar weakens"
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({"source": "fixture", "source_item_id": "one",
        "source_published_time": now, "collector_first_seen_time": now,
        "fetched_time": now, "headline": body, "body": body,
        "content_hash": content_hash, "cluster_id": "one"})
    row = {"source": "fixture", "source_item_id": "one", "revision_number": 1,
           "headline": body, "content_hash": content_hash}
    monkeypatch.setattr(runtime, "pending_record_for_job", lambda *_args, **_kwargs: row)
    def transport(request, *, timeout):
        if "openrouter.ai" not in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 503, "temporary", {}, io.BytesIO(b"{}"))
        return response(backup_envelope())
    monkeypatch.setattr(urllib.request, "urlopen", transport)
    try:
        result = runtime._execute_job(ledger, ApiCredential("google", "ROUTINE", "secret", "id"),
            SimpleNamespace(task_type="TITLE_TRANSLATION", work_lane="LIVE", priority="NORMAL"), now=now)
        assert result["status"] == "OK"
        translation = ledger.connection.execute("SELECT * FROM news_title_translations").fetchone()
        assert translation["llm_model_version"] == "openrouter/" + OPENROUTER_NEWS_MODEL
        assert translation["raw_content_hash"] == content_hash
    finally:
        ledger.close()
