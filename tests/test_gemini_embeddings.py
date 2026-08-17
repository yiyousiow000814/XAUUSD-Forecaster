from __future__ import annotations

import numpy as np

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.gemini_embeddings import (
    GEMINI_EMBEDDING_DIMENSIONS,
    GeminiEmbeddingClient,
)
from xauusd_forecaster.news_scheduler import ApiCredential, ROUTINE_POOL


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

    client = GeminiEmbeddingClient(ledger.connection)
    monkeypatch.setattr(client, "_request", request)
    profile = client.profile()

    client.embed(["document one", "document two"], profile)
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
    client = GeminiEmbeddingClient(ledger.connection)

    def request(key: str, texts: list[str], _task: str) -> np.ndarray:
        used_keys.append(key)
        result = np.zeros((len(texts), GEMINI_EMBEDDING_DIMENSIONS), dtype=np.float32)
        result[:, 0] = 1.0
        return result

    monkeypatch.setattr(client, "_request", request)
    client.embed(["a", "b"], client.profile())

    assert used_keys == ["key-open"]
    ledger.close()
