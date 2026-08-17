from datetime import UTC, datetime
import sqlite3

import numpy as np
import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news_retrieval import (
    EmbeddingProfile,
    append_missing_embeddings,
    attach_hybrid_prior_event_context,
    lexical_identity_similarity,
    load_embeddings,
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


def test_embedding_rows_are_versioned_by_exact_local_model_digest(tmp_path):
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
