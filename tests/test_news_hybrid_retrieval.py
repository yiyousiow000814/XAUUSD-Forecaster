from datetime import UTC, datetime, timedelta
import json
import sqlite3

import numpy as np
import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.gemini_embeddings import (
    GeminiEmbeddingCapacityDeferred,
    GeminiEmbeddingClient,
    GeminiEmbeddingFailure,
)
from xauusd_forecaster.news_scheduler import ApiCredential, ROUTINE_POOL
from xauusd_forecaster.news_retrieval import (
    EmbeddingProfile,
    append_missing_embeddings,
    attach_hybrid_prior_event_context,
    lexical_identity_similarity,
    load_embeddings,
    NewsEmbeddingPrerequisiteCooldown,
    retrieve_hybrid_prior_event_context,
)


def _row(
    candidate_id: str,
    headline: str,
    *,
    first_seen: str,
    cluster_id: str = "",
    actor: str = "",
    object_id: str = "",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "annotation_id": candidate_id,
        "raw_content_hash": candidate_id.ljust(64, "0")[:64],
        "source": "source",
        "source_item_id": candidate_id,
        "revision_number": 1,
        "headline": headline,
        "collector_first_seen_time": first_seen,
        "cluster_id": cluster_id,
        "update_type": "NEW_EVENT",
        "annotation": {
            "summary_zh": headline,
            "record_kind": "EVENT_REPORT",
            "evidence_role": "CORE_CLAIM",
            "actor": actor,
            "action": "announced",
            "action_family": "POLICY_DECISION",
            "object": object_id,
            "canonical_actor_id": actor,
            "canonical_object_id": object_id,
            "material_event_key": "",
            "episode_key": "",
            "supporting_evidence": [headline],
        },
    }


class _FakeEmbeddingClient:
    def __init__(self, digest: str = "d" * 64) -> None:
        self._profile = EmbeddingProfile("fake-embedding", digest, 4)

    def profile(self) -> EmbeddingProfile:
        return self._profile

    def embed(self, texts, profile):
        vectors = []
        for index, _ in enumerate(texts):
            vector = np.zeros(profile.dimensions, dtype=np.float32)
            vector[index % profile.dimensions] = 1.0
            vectors.append(vector)
        return np.asarray(vectors)


class _ReentrantEmbeddingClient(_FakeEmbeddingClient):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def embed(self, texts, profile):
        self._callback()
        return super().embed(texts, profile)


def test_hybrid_retrieval_unions_semantic_recall_without_losing_pinned_match():
    current = _row(
        "current", "Venezuela begins moving sovereign gold reserves",
        first_seen="2026-08-17T10:00:00+00:00", actor="venezuela",
        object_id="sovereign_gold_reserves",
    )
    pinned = _row(
        "pinned", "Venezuela moves its sovereign gold reserves",
        first_seen="2026-08-16T10:00:00+00:00", cluster_id="same-cluster",
        actor="venezuela", object_id="sovereign_gold_reserves",
    )
    current["cluster_id"] = "same-cluster"
    semantic = _row(
        "semantic", "Caracas shifts state bullion holdings",
        first_seen="2026-08-15T10:00:00+00:00", actor="caracas",
        object_id="state_bullion",
    )
    future = _row(
        "future", "Venezuela gold reserve transfer",
        first_seen="2026-08-18T10:00:00+00:00", actor="venezuela",
        object_id="sovereign_gold_reserves",
    )
    unit_x = np.asarray([1.0, 0.0], dtype=np.float32)
    embeddings = {
        "current": unit_x,
        "pinned": np.asarray([0.0, 1.0], dtype=np.float32),
        "semantic": unit_x,
        "future": unit_x,
    }

    result = retrieve_hybrid_prior_event_context(
        current, [current, pinned, semantic, future], embeddings, limit=5,
    )

    assert result.route_rankings["deterministic"][0] == "pinned"
    assert result.route_rankings["semantic"][0] == "semantic"
    assert [row["candidate_id"] for row in result.candidates][:2] == [
        "pinned", "semantic",
    ]
    assert "future" not in result.route_rankings["combined"]


def test_lexical_similarity_ignores_shared_field_labels_and_stopwords():
    left = _row(
        "left", "Federal Reserve rate decision",
        first_seen="2026-08-17T10:00:00+00:00",
    )
    related = _row(
        "related", "Fed decision on interest rates",
        first_seen="2026-08-16T10:00:00+00:00",
    )
    unrelated = _row(
        "unrelated", "Oil tankers cross the strait",
        first_seen="2026-08-16T10:00:00+00:00",
    )

    assert lexical_identity_similarity(left, related) > lexical_identity_similarity(
        left, unrelated,
    )


def test_embedding_rows_are_versioned_by_exact_provider_model_digest(tmp_path):
    ledger = ForwardLedger(tmp_path / "evidence.sqlite3")
    try:
        row = _row(
            "annotation-1", "Federal Reserve decision",
            first_seen="2026-08-17T10:00:00+00:00",
        )
        ledger.connection.execute(
            """INSERT INTO news_revisions VALUES (
               'source','annotation-1',1,NULL,?,?,?,'Federal Reserve decision',
               'body',NULL,?,'cluster',NULL)""",
            (
                row["collector_first_seen_time"],
                row["collector_first_seen_time"],
                row["collector_first_seen_time"],
                row["raw_content_hash"],
            ),
        )
        ledger.connection.execute(
            """INSERT INTO news_annotations VALUES (
               'annotation-1','source','annotation-1',1,?,'EVENT', '[]',
               0,0,0,0,0,0,1,'model','prompt',?,?,?)""",
            (
                row["raw_content_hash"],
                row["collector_first_seen_time"],
                row["collector_first_seen_time"],
                "{}",
            ),
        )
        ledger.connection.commit()
        first_profile, first_count = append_missing_embeddings(
            ledger.connection, [row], _FakeEmbeddingClient("a" * 64), limit=1,
        )
        second_profile, second_count = append_missing_embeddings(
            ledger.connection, [row], _FakeEmbeddingClient("b" * 64), limit=1,
        )

        assert first_count == second_count == 1
        assert len(load_embeddings(
            ledger.connection, first_profile, expected_rows=[row],
        )) == 1
        assert len(load_embeddings(ledger.connection, second_profile)) == 1
        changed = {**row, "headline": "Drifted headline"}
        with pytest.raises(ValueError, match="text hash drifted"):
            load_embeddings(
                ledger.connection, first_profile, expected_rows=[changed],
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger.connection.execute(
                "UPDATE news_identity_embeddings_v1 SET dimensions=3"
            )
    finally:
        ledger.close()


def test_embedding_backfill_lease_prevents_duplicate_provider_admission(tmp_path):
    database = tmp_path / "evidence.sqlite3"
    first_ledger = ForwardLedger(database)
    second_ledger = ForwardLedger(database)
    row = _row(
        "annotation-1", "Federal Reserve decision",
        first_seen="2026-08-17T10:00:00+00:00",
    )
    second_calls = 0

    class _CountingClient(_FakeEmbeddingClient):
        def embed(self, texts, profile):
            nonlocal second_calls
            second_calls += 1
            return super().embed(texts, profile)

    def attempt_competing_backfill() -> None:
        _, count = append_missing_embeddings(
            second_ledger.connection, [row], _CountingClient(), limit=1,
        )
        assert count == 0

    try:
        first_ledger.connection.execute(
            """INSERT INTO news_revisions VALUES (
               'source','annotation-1',1,NULL,?,?,?,'Federal Reserve decision',
               'body',NULL,?,'cluster',NULL)""",
            (
                row["collector_first_seen_time"],
                row["collector_first_seen_time"],
                row["collector_first_seen_time"],
                row["raw_content_hash"],
            ),
        )
        first_ledger.connection.execute(
            """INSERT INTO news_annotations VALUES (
               'annotation-1','source','annotation-1',1,?,'EVENT', '[]',
               0,0,0,0,0,0,1,'model','prompt',?,?,?)""",
            (
                row["raw_content_hash"],
                row["collector_first_seen_time"],
                row["collector_first_seen_time"],
                "{}",
            ),
        )
        first_ledger.connection.commit()

        _, count = append_missing_embeddings(
            first_ledger.connection,
            [row],
            _ReentrantEmbeddingClient(attempt_competing_backfill),
            limit=1,
        )

        assert count == 1
        assert second_calls == 0
        assert first_ledger.connection.execute(
            "SELECT count(*) FROM news_identity_embeddings_v1"
        ).fetchone()[0] == 1
        assert first_ledger.connection.execute(
            "SELECT count(*) FROM news_identity_embedding_backfill_leases_v1"
        ).fetchone()[0] == 0
    finally:
        second_ledger.close()
        first_ledger.close()


def test_embedding_throttle_cooldown_survives_restart_and_clears_on_progress(
    tmp_path,
) -> None:
    database = tmp_path / "evidence.sqlite3"
    ledger = ForwardLedger(database)
    row = _row(
        "annotation-1", "Federal Reserve decision",
        first_seen="2026-08-17T10:00:00+00:00",
    )
    ledger.connection.execute(
        """INSERT INTO news_revisions VALUES (
           'source','annotation-1',1,NULL,?,?,?,'Federal Reserve decision',
           'body',NULL,?,'cluster',NULL)""",
        (
            row["collector_first_seen_time"], row["collector_first_seen_time"],
            row["collector_first_seen_time"], row["raw_content_hash"],
        ),
    )
    ledger.connection.execute(
        """INSERT INTO news_annotations VALUES (
           'annotation-1','source','annotation-1',1,?,'EVENT','[]',
           0,0,0,0,0,0,1,'model','prompt',?,?,?)""",
        (
            row["raw_content_hash"], row["collector_first_seen_time"],
            row["collector_first_seen_time"], "{}",
        ),
    )
    ledger.connection.commit()

    class _ThrottledClient(_FakeEmbeddingClient):
        def embed(self, texts, profile):
            raise GeminiEmbeddingFailure(
                "provider throttled",
                failure_code="NEWS_EMBEDDING_PROVIDER_THROTTLED",
                provider_http_status=429,
                retry_after_seconds=300,
                diagnostic={
                    "request_timestamp": "2026-08-17T10:00:00+00:00",
                    "batch_item_count": len(texts),
                    "estimated_input_tokens": 1234,
                    "quota_reason": "RESOURCE_EXHAUSTED",
                },
            )

    try:
        with pytest.raises(GeminiEmbeddingFailure):
            append_missing_embeddings(
                ledger.connection, [row], _ThrottledClient(), limit=1,
            )
        state = ledger.connection.execute(
            "SELECT * FROM news_identity_embedding_backfill_leases_v1"
        ).fetchone()
        assert state["lease_owner"] == ""
        assert state["last_failure_code"] == (
            "NEWS_EMBEDDING_PROVIDER_THROTTLED"
        )
        assert state["provider_http_status"] == 429
        assert json.loads(state["diagnostic_json"])["cooldown_seconds"] == 300
    finally:
        ledger.close()

    reopened = ForwardLedger(database)
    provider_calls = 0

    class _CountingClient(_FakeEmbeddingClient):
        def embed(self, texts, profile):
            nonlocal provider_calls
            provider_calls += 1
            return super().embed(texts, profile)

    try:
        with pytest.raises(NewsEmbeddingPrerequisiteCooldown) as caught:
            append_missing_embeddings(
                reopened.connection, [row], _CountingClient(), limit=1,
            )
        assert caught.value.failure_code == (
            "NEWS_EMBEDDING_PROVIDER_THROTTLED"
        )
        assert caught.value.provider_http_status == 429
        assert provider_calls == 0

        reopened.connection.execute(
            """UPDATE news_identity_embedding_backfill_leases_v1
               SET cooldown_until=?""",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
        reopened.connection.commit()
        _, count = append_missing_embeddings(
            reopened.connection, [row], _CountingClient(), limit=1,
        )
        assert count == 1
        assert provider_calls == 1
        assert reopened.connection.execute(
            "SELECT count(*) FROM news_identity_embeddings_v1"
        ).fetchone()[0] == 1
        assert reopened.connection.execute(
            "SELECT count(*) FROM news_identity_embedding_backfill_leases_v1"
        ).fetchone()[0] == 0
    finally:
        reopened.close()


def test_embedding_local_capacity_uses_bounded_exponential_generation_cooldown(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "evidence.sqlite3")
    row = _row(
        "annotation-1", "Federal Reserve decision",
        first_seen="2026-08-17T10:00:00+00:00",
    )

    class _CapacityDeferredClient(_FakeEmbeddingClient):
        def embed(self, texts, profile):
            raise GeminiEmbeddingCapacityDeferred(
                "local admission deferred",
                failure_code="NEWS_EMBEDDING_CAPACITY_DEFERRED",
                diagnostic={
                    "batch_item_count": len(texts),
                    "estimated_input_tokens": 1200,
                },
            )

    try:
        for expected_count, expected_seconds in ((1, 60), (2, 120)):
            with pytest.raises(GeminiEmbeddingCapacityDeferred):
                append_missing_embeddings(
                    ledger.connection, [row], _CapacityDeferredClient(), limit=1,
                )
            state = ledger.connection.execute(
                "SELECT * FROM news_identity_embedding_backfill_leases_v1"
            ).fetchone()
            assert state["failure_count"] == expected_count
            assert state["last_failure_code"] == (
                "NEWS_EMBEDDING_CAPACITY_DEFERRED"
            )
            assert state["provider_http_status"] is None
            assert json.loads(state["diagnostic_json"])["cooldown_seconds"] == (
                expected_seconds
            )
            ledger.connection.execute(
                "UPDATE news_identity_embedding_backfill_leases_v1 "
                "SET cooldown_until=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
            )
            ledger.connection.commit()
    finally:
        ledger.close()


def test_successful_embedding_admission_records_vector_commit(
    tmp_path, monkeypatch,
) -> None:
    from xauusd_forecaster import gemini_embeddings

    ledger = ForwardLedger(tmp_path / "evidence.sqlite3")
    row = _row(
        "annotation-1", "Federal Reserve decision",
        first_seen="2026-08-17T10:00:00+00:00",
    )
    credential = ApiCredential(
        "account", ROUTINE_POOL, "not-a-real-key", "fingerprint",
    )
    monkeypatch.setattr(
        gemini_embeddings, "configured_api_credentials", lambda: (credential,),
    )
    client = GeminiEmbeddingClient(ledger.connection)
    ledger.connection.execute(
        """INSERT INTO news_revisions VALUES (
           'source','annotation-1',1,NULL,?,?,?,'Federal Reserve decision',
           'body',NULL,?,'cluster',NULL)""",
        (
            row["collector_first_seen_time"], row["collector_first_seen_time"],
            row["collector_first_seen_time"], row["raw_content_hash"],
        ),
    )
    ledger.connection.execute(
        """INSERT INTO news_annotations VALUES (
           'annotation-1','source','annotation-1',1,?,'EVENT','[]',
           0,0,0,0,0,0,1,'model','prompt',?,?,?)""",
        (
            row["raw_content_hash"], row["collector_first_seen_time"],
            row["collector_first_seen_time"], "{}",
        ),
    )
    ledger.connection.commit()

    def request(_key, texts, _task):
        result = np.zeros(
            (len(texts), gemini_embeddings.GEMINI_EMBEDDING_DIMENSIONS),
            dtype=np.float32,
        )
        result[:, 0] = 1.0
        return result

    monkeypatch.setattr(client, "_request", request)
    try:
        _, count = append_missing_embeddings(
            ledger.connection, [row], client, limit=1,
        )

        assert count == 1
        usage = ledger.connection.execute(
            """SELECT request_count,attempted_at,provider_outcome,
                      provider_http_status,vectors_committed_at
               FROM news_ai_account_request_usage_v1"""
        ).fetchone()
        assert usage["request_count"] == 1
        assert usage["attempted_at"] is not None
        assert usage["provider_outcome"] == "PROVIDER_SUCCEEDED"
        assert usage["provider_http_status"] is None
        assert usage["vectors_committed_at"] is not None
        assert ledger.connection.execute(
            "SELECT count(*) FROM news_identity_embeddings_v1"
        ).fetchone()[0] == 1
    finally:
        ledger.close()


def test_runtime_catches_up_historical_embedding_gap_before_retrieval(
    tmp_path, monkeypatch,
):
    import xauusd_forecaster.news_retrieval as retrieval

    ledger = ForwardLedger(tmp_path / "evidence.sqlite3")
    prior = _row(
        "prior", "Federal Reserve held rates steady",
        first_seen="2026-08-16T10:00:00+00:00",
    )
    current = _row(
        "current", "Markets await the next Federal Reserve decision",
        first_seen="2026-08-17T10:00:00+00:00",
    )
    try:
        for row in (prior, current):
            ledger.connection.execute(
                """INSERT INTO news_revisions VALUES (
                   ?,?,1,NULL,?,?,?,?,'body',NULL,?,'cluster',NULL)""",
                (
                    row["source"], row["source_item_id"],
                    row["collector_first_seen_time"],
                    row["collector_first_seen_time"],
                    row["collector_first_seen_time"], row["headline"],
                    row["raw_content_hash"],
                ),
            )
            ledger.connection.execute(
                """INSERT INTO news_annotations VALUES (
                   ?,?,?,1,?,'EVENT','[]',0,0,0,0,0,0,1,
                   'model','prompt',?,?,?)""",
                (
                    row["annotation_id"], row["source"], row["source_item_id"],
                    row["raw_content_hash"], row["collector_first_seen_time"],
                    row["collector_first_seen_time"], "{}",
                ),
            )
        ledger.connection.commit()
        monkeypatch.setattr(
            retrieval, "load_identity_candidate_universe",
            lambda *_args, **_kwargs: [prior, current],
        )

        attached = attach_hybrid_prior_event_context(
            ledger.connection, [current], client=_FakeEmbeddingClient(),
        )

        assert attached[0]["identity_retrieval_version"] == (
            retrieval.NEWS_HYBRID_RETRIEVAL_VERSION
        )
        assert ledger.connection.execute(
            "SELECT count(*) FROM news_identity_embeddings_v1"
        ).fetchone()[0] == 2
    finally:
        ledger.close()
