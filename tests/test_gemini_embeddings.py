from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.gemini_embeddings import (
    GEMINI_EMBEDDING_DIMENSIONS,
    GeminiEmbeddingCapacityDeferred,
    GeminiEmbeddingClient,
    GeminiEmbeddingFailure,
)
from xauusd_forecaster.news_scheduler import (
    ApiCredential,
    CONTRACT_BACKFILL_WORKLOAD,
    LIVE_OPERATIONAL_WORKLOAD,
    ROUTINE_POOL,
    quota_day,
    reserve_account_request,
)


def _client(connection) -> GeminiEmbeddingClient:
    return GeminiEmbeddingClient(
        connection, workload_class=LIVE_OPERATIONAL_WORKLOAD,
    )


def _seed_embedding_backfill_forecast(connection) -> None:
    now = datetime.now(UTC)
    pacific_now = now.astimezone(ZoneInfo("America/Los_Angeles"))
    pacific_day = pacific_now.date()
    for offset in range(1, 8):
        connection.execute(
            """INSERT INTO news_ai_quota_day_workload_v1
               VALUES (?,?,?,?,?,?,?)""",
            (
                (pacific_day - timedelta(days=offset)).isoformat(),
                pacific_now.hour,
                "account", "gemini_embedding_quota", "LIVE_OPERATIONAL",
                900, now.isoformat(),
            ),
        )
    connection.commit()


def test_embedding_inherits_backfill_admission_without_reserving_or_calling(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credential = ApiCredential("account", ROUTINE_POOL, "key", "fingerprint")
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: (credential,),
    )
    _seed_embedding_backfill_forecast(ledger.connection)
    provider_calls = 0
    client = GeminiEmbeddingClient(
        ledger.connection, workload_class=CONTRACT_BACKFILL_WORKLOAD,
    )

    def request(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        pytest.fail("backfill without forecast-safe surplus must not call provider")

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(GeminiEmbeddingCapacityDeferred) as caught:
        client.embed(["historical event"], client.profile())

    assert caught.value.failure_code == "BACKFILL_BUDGET_DEFERRED"
    assert provider_calls == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_daily_usage_v1"
    ).fetchone()[0] == 0

    live = _client(ledger.connection)
    monkeypatch.setattr(
        live, "_request",
        lambda _key, texts, _task: np.tile(
            np.eye(1, GEMINI_EMBEDDING_DIMENSIONS, dtype=np.float32),
            (len(texts), 1),
        ),
    )
    live.embed(["current event"], live.profile())
    usage = ledger.connection.execute(
        """SELECT quota_authority,workload_class,request_count
           FROM news_ai_quota_day_workload_v1
           WHERE quota_day=?""",
        (quota_day(datetime.now(UTC)),),
    ).fetchone()
    assert tuple(usage) == ("gemini_embedding_quota", "LIVE_OPERATIONAL", 1)
    ledger.close()


def test_embedding_batch_reserves_each_content_item_and_uses_asymmetric_tasks(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credential = ApiCredential(
        "independent-account", ROUTINE_POOL, "secret", "credential",
    )
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: (credential,),
    )
    calls: list[tuple[list[str], str]] = []

    def request(_key: str, texts: list[str], task_type: str) -> np.ndarray:
        calls.append((texts, task_type))
        result = np.zeros((len(texts), GEMINI_EMBEDDING_DIMENSIONS), dtype=np.float32)
        result[:, 0] = 1.0
        return result

    client = _client(ledger.connection)
    monkeypatch.setattr(client, "_request", request)
    profile = client.profile()

    client.embed(["document one", "document two"], profile)
    ledger.connection.execute(
        """UPDATE news_ai_provider_dispatch_state_v1
           SET next_eligible_at='2000-01-01T00:00:00+00:00'"""
    )
    ledger.connection.commit()
    client.embed_queries(["query one"], profile)

    assert [task for _, task in calls] == [
        "RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY",
    ]
    assert calls[0][0][0].startswith("title: news identity | text: ")
    assert calls[1][0][0].startswith("task: find the same news event | query: ")
    usage = ledger.connection.execute(
        """SELECT request_count FROM news_ai_account_daily_usage_v1
           WHERE model_family='gemini-embedding-2'"""
    ).fetchone()
    assert int(usage[0]) == 3
    dispatch_classes = ledger.connection.execute(
        "SELECT task_class FROM news_ai_provider_dispatch_task_state_v1"
    ).fetchall()
    assert {str(row[0]) for row in dispatch_classes} == {"EMBEDDING"}
    ledger.close()


def test_embedding_batch_moves_to_an_independent_account_when_rpm_is_full(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings
    from xauusd_forecaster.news_scheduler import reserve_account_request

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credentials = (
        ApiCredential("full-account", ROUTINE_POOL, "key-full", "full"),
        ApiCredential("open-account", ROUTINE_POOL, "key-open", "open"),
    )
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: credentials,
    )
    assert reserve_account_request(
        ledger.connection,
        account_id="full-account",
        model_family="gemini-embedding-2",
        daily_limit=1_000,
        requests_per_minute=100,
        request_count=99,
    )
    used_keys: list[str] = []
    client = _client(ledger.connection)

    def request(key: str, texts: list[str], _task: str) -> np.ndarray:
        used_keys.append(key)
        result = np.zeros((len(texts), GEMINI_EMBEDDING_DIMENSIONS), dtype=np.float32)
        result[:, 0] = 1.0
        return result

    monkeypatch.setattr(client, "_request", request)
    client.embed(["a", "b"], client.profile())

    assert used_keys == ["key-open"]
    ledger.close()


def test_embedding_http_429_keeps_bounded_safe_quota_provenance(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credential = ApiCredential(
        "configured-label", ROUTINE_POOL, "secret-key", "fingerprint",
    )
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: (credential,),
    )
    body = json.dumps({
        "error": {
            "status": "RESOURCE_EXHAUSTED",
            "details": [{
                "quotaMetric": "generativelanguage.googleapis.com/embed_requests",
                "quotaId": "EmbedRequestsPerProjectPerMinute",
                "quotaDimensions": {
                    "project_number": "project-123", "model": "embedding-2",
                },
            }],
        },
    }).encode()
    failure = urllib.error.HTTPError(
        "https://provider.invalid", 429, "Too Many Requests",
        {"Retry-After": "300"}, io.BytesIO(body),
    )
    client = _client(ledger.connection)
    monkeypatch.setattr(
        client, "_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(GeminiEmbeddingFailure) as caught:
        client.embed(["document one", "document two"], client.profile())

    error = caught.value
    assert error.failure_code == "NEWS_EMBEDDING_PROVIDER_THROTTLED"
    assert error.provider_http_status == 429
    assert error.retry_after_seconds == 300
    assert error.diagnostic["batch_item_count"] == 2
    assert error.diagnostic["estimated_input_tokens"] > 0
    assert error.diagnostic["quota_reason"] == "RESOURCE_EXHAUSTED"
    assert error.diagnostic["quota_metric"].endswith("embed_requests")
    assert error.diagnostic["quota_limit_name"] == (
        "EmbedRequestsPerProjectPerMinute"
    )
    serialized = json.dumps(error.diagnostic)
    assert "project-123" in serialized
    assert "secret-key" not in serialized
    assert "fingerprint" not in serialized
    usage = ledger.connection.execute(
        """SELECT request_count,attempted_at,provider_outcome,
                  provider_http_status,vectors_committed_at
           FROM news_ai_account_request_usage_v1"""
    ).fetchone()
    assert usage["request_count"] == 2
    assert usage["attempted_at"] is not None
    assert usage["provider_outcome"] == "PROVIDER_THROTTLED"
    assert usage["provider_http_status"] == 429
    assert usage["vectors_committed_at"] is None
    ledger.close()


def test_embedding_local_admission_has_distinct_failure_code(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings
    from xauusd_forecaster.news_scheduler import reserve_account_request

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credential = ApiCredential("full", ROUTINE_POOL, "key", "fingerprint")
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: (credential,),
    )
    assert reserve_account_request(
        ledger.connection, account_id="full",
        model_family="gemini-embedding-2", daily_limit=1_000,
        requests_per_minute=100, request_count=100,
    )

    with pytest.raises(GeminiEmbeddingCapacityDeferred) as caught:
        _client(ledger.connection).embed(
            ["document"], _client(ledger.connection).profile(),
        )

    assert caught.value.failure_code == "NEWS_EMBEDDING_CAPACITY_DEFERRED"
    assert caught.value.provider_http_status is None
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 1
    ledger.close()


def test_five_account_daily_cap_blocks_transport_and_next_day_resets(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credentials = tuple(
        ApiCredential(
            f"account-{index}", ROUTINE_POOL, f"key-{index}", f"fp-{index}",
        )
        for index in range(5)
    )
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: credentials,
    )
    now = datetime.now(UTC)
    day = quota_day(now)
    for credential in credentials:
        ledger.connection.execute(
            """INSERT INTO news_ai_account_daily_usage_v1
               (quota_day,account_id,model_family,request_count,updated_at)
               VALUES (?,?,?,?,?)""",
            (
                day, credential.account_id, "gemini-embedding-2", 1_000,
                now.isoformat(),
            ),
        )
    ledger.connection.commit()
    provider_calls = 0

    def request(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        pytest.fail("daily safety cap must prevent embedding transport")

    client = _client(ledger.connection)
    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(GeminiEmbeddingCapacityDeferred):
        client.embed(["document"], client.profile())

    assert provider_calls == 0
    capped = ledger.connection.execute(
        """SELECT account_id,request_count
           FROM news_ai_account_daily_usage_v1
           WHERE quota_day=? AND model_family='gemini-embedding-2'
           ORDER BY account_id""",
        (day,),
    ).fetchall()
    assert [tuple(row) for row in capped] == [
        (f"account-{index}", 1_000) for index in range(5)
    ]

    next_day = now + timedelta(days=1)
    assert reserve_account_request(
        ledger.connection,
        account_id="account-0",
        model_family="gemini-embedding-2",
        daily_limit=1_000,
        requests_per_minute=100,
        request_count=1,
        now=next_day,
    )
    assert ledger.connection.execute(
        """SELECT request_count FROM news_ai_account_daily_usage_v1
           WHERE quota_day=? AND account_id='account-0'
             AND model_family='gemini-embedding-2'""",
        (quota_day(next_day),),
    ).fetchone()[0] == 1
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_account_daily_usage_v1
           WHERE quota_day=? AND model_family='gemini-embedding-2'""",
        (day,),
    ).fetchone()[0] == 5
    ledger.close()


def test_first_provider_429_stops_unproven_account_failover(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credentials = tuple(
        ApiCredential(f"account-{index}", ROUTINE_POOL, f"key-{index}", f"fp-{index}")
        for index in range(3)
    )
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: credentials,
    )
    client = _client(ledger.connection)

    def throttled(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://provider.invalid", 429, "Too Many Requests",
            {}, io.BytesIO(b'{"error":{"status":"RESOURCE_EXHAUSTED"}}'),
        )

    monkeypatch.setattr(client, "_request", throttled)
    with pytest.raises(GeminiEmbeddingFailure):
        client.embed(["one", "two"], client.profile())

    rows = ledger.connection.execute(
        """SELECT account_id,request_count,provider_outcome
           FROM news_ai_account_request_usage_v1 ORDER BY account_id"""
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("account-0", 2, "PROVIDER_THROTTLED"),
    ]
    daily = ledger.connection.execute(
        "SELECT request_count FROM news_ai_account_daily_usage_v1 "
        "WHERE model_family='gemini-embedding-2' ORDER BY account_id"
    ).fetchall()
    assert [tuple(row) for row in daily] == [(2,)]
    ledger.close()


@pytest.mark.parametrize(("provider_error", "failure_code"), [
    (
        urllib.error.URLError("connection reset"),
        "NEWS_EMBEDDING_PROVIDER_TRANSPORT_FAILED",
    ),
    (
        ValueError("dimensions do not match contract"),
        "NEWS_EMBEDDING_PROVIDER_RESPONSE_INVALID",
    ),
])
def test_embedding_provider_failure_family_keeps_distinct_provenance(
    tmp_path, monkeypatch, provider_error, failure_code,
) -> None:
    from xauusd_forecaster import gemini_embeddings

    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credential = ApiCredential("account", ROUTINE_POOL, "key", "fingerprint")
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: (credential,),
    )
    client = _client(ledger.connection)
    monkeypatch.setattr(
        client, "_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(provider_error),
    )

    with pytest.raises(GeminiEmbeddingFailure) as caught:
        client.embed(["document"], client.profile())

    assert caught.value.failure_code == failure_code
    usage = ledger.connection.execute(
        "SELECT attempted_at,provider_outcome,provider_http_status "
        "FROM news_ai_account_request_usage_v1"
    ).fetchone()
    assert usage["attempted_at"] is not None
    assert usage["provider_outcome"] == "PROVIDER_FAILED"
    assert usage["provider_http_status"] is None
    ledger.close()
