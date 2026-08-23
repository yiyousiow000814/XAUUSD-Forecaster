"""Independent scoring for point-in-time news candidate retrieval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from xauusd_forecaster.news.annotation.impact import (
    IDENTITY_CANDIDATE_UNIVERSE_LIMIT,
    build_identity_candidate_index,
    load_identity_candidate_universe,
    retrieve_prior_event_context,
)
from xauusd_forecaster.news.retrieval.search import (
    EmbeddingProfile,
    load_embeddings,
    retrieve_hybrid_prior_event_context,
)


BENCHMARK_SCHEMA_VERSION = "news-candidate-retrieval-benchmark.v1"
BENCHMARK_POSITIVE_CASES = 100
BENCHMARK_NEGATIVE_CASES = 100
BENCHMARK_TOP_K = 5
_RELATIONS = frozenset({"SAME_EVENT", "SAME_EPISODE"})
_LABEL_BASES = frozenset({
    "same_verifiable_fact",
    "same_episode_material_update",
    "different_reference_period",
    "different_release_series",
    "different_occurrence",
})


def load_benchmark_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("news retrieval benchmark manifest must be an object")
    if payload.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise ValueError("news retrieval benchmark schema is unsupported")
    positives = payload.get("positive_cases")
    negatives = payload.get("negative_cases")
    if not isinstance(positives, list) or not isinstance(negatives, list):
        raise ValueError("news retrieval benchmark cases must be lists")
    if len(positives) != BENCHMARK_POSITIVE_CASES:
        raise ValueError("news retrieval benchmark must contain 100 positives")
    if len(negatives) != BENCHMARK_NEGATIVE_CASES:
        raise ValueError("news retrieval benchmark must contain 100 negatives")
    identifiers = set()
    for kind, cases in (("positive", positives), ("negative", negatives)):
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError("news retrieval benchmark case is invalid")
            case_id = str(case.get("case_id") or "")
            current = str(case.get("current_annotation_id") or "")
            other_field = (
                "expected_prior_annotation_id"
                if kind == "positive" else "forbidden_prior_annotation_id"
            )
            other = str(case.get(other_field) or "")
            basis = str(case.get("label_basis") or "")
            if (
                not case_id or case_id in identifiers or not current or not other
                or current == other or basis not in _LABEL_BASES
                or case.get("reviewed") is not True
            ):
                raise ValueError("news retrieval benchmark case contract failed")
            identifiers.add(case_id)
            if kind == "positive" and case.get("relation") not in _RELATIONS:
                raise ValueError("positive retrieval relation is unsupported")
            for field in ("current_content_hash", "prior_content_hash"):
                value = str(case.get(field) or "")
                if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                    raise ValueError("benchmark content hash is invalid")
    return payload


def _load_current_rows(connection, annotation_ids: tuple[str, ...]) -> dict[str, dict]:
    placeholders = ",".join("?" for _ in annotation_ids)
    rows = connection.execute(
        f"""SELECT a.annotation_id,a.annotation_json,a.raw_content_hash,
                   n.source,n.source_item_id,n.revision_number,n.headline,
                   n.collector_first_seen_time,n.cluster_id,n.content_hash
            FROM news_annotations a JOIN news_revisions n
              ON n.source=a.source AND n.source_item_id=a.source_item_id
             AND n.revision_number=a.revision_number
             AND n.content_hash=a.raw_content_hash
            WHERE a.annotation_id IN ({placeholders})""",
        annotation_ids,
    ).fetchall()
    result = {}
    for raw in rows:
        row = dict(raw)
        row["annotation"] = json.loads(row.pop("annotation_json") or "{}")
        result[str(row["annotation_id"])] = row
    return result


def score_candidate_rankings(
    positive_cases: list[dict],
    negative_cases: list[dict],
    rankings: dict[str, tuple[str, ...]],
    *,
    top_k: int = BENCHMARK_TOP_K,
) -> dict[str, Any]:
    """Score recall and explicit hard-negative collisions without model output."""
    if not 1 <= top_k <= 20:
        raise ValueError("benchmark top-k is invalid")
    positive_hits = {1: 0, top_k: 0}
    reciprocal_rank = 0.0
    empty = 0
    by_relation: dict[str, dict[str, int]] = {}
    details = []
    for case in positive_cases:
        ranked = rankings.get(str(case["current_annotation_id"]), ())
        expected = str(case["expected_prior_annotation_id"])
        rank = ranked.index(expected) + 1 if expected in ranked else None
        positive_hits[1] += int(rank == 1)
        positive_hits[top_k] += int(rank is not None and rank <= top_k)
        reciprocal_rank += 1.0 / rank if rank is not None and rank <= top_k else 0.0
        empty += int(not ranked)
        relation = str(case["relation"])
        bucket = by_relation.setdefault(relation, {"cases": 0, "hits_at_k": 0})
        bucket["cases"] += 1
        bucket["hits_at_k"] += int(rank is not None and rank <= top_k)
        details.append({
            "case_id": case["case_id"], "kind": "positive",
            "rank": rank, "retrieved": list(ranked[:top_k]),
        })

    collisions = 0
    for case in negative_cases:
        ranked = rankings.get(str(case["current_annotation_id"]), ())
        forbidden = str(case["forbidden_prior_annotation_id"])
        rank = ranked.index(forbidden) + 1 if forbidden in ranked else None
        collision = rank is not None and rank <= top_k
        collisions += int(collision)
        details.append({
            "case_id": case["case_id"], "kind": "negative",
            "rank": rank, "retrieved": list(ranked[:top_k]),
        })

    positive_count = len(positive_cases)
    negative_count = len(negative_cases)
    return {
        "positive_cases": positive_count,
        "negative_cases": negative_count,
        "recall_at_1": positive_hits[1] / positive_count,
        f"recall_at_{top_k}": positive_hits[top_k] / positive_count,
        f"mrr_at_{top_k}": reciprocal_rank / positive_count,
        "positive_empty_candidate_rate": empty / positive_count,
        f"hard_negative_collision_rate_at_{top_k}": collisions / negative_count,
        "by_relation": {
            relation: {
                **bucket,
                f"recall_at_{top_k}": bucket["hits_at_k"] / bucket["cases"],
            }
            for relation, bucket in sorted(by_relation.items())
        },
        "details": details,
    }


def evaluate_candidate_retrieval(
    connection,
    manifest: dict[str, Any],
    *,
    embedding_profile: EmbeddingProfile | None = None,
) -> dict[str, Any]:
    """Replay production retrieval over frozen, independently reviewed labels."""
    positives = manifest["positive_cases"]
    negatives = manifest["negative_cases"]
    current_ids = tuple(dict.fromkeys(
        str(case["current_annotation_id"])
        for case in positives + negatives
    ))
    current_rows = _load_current_rows(connection, current_ids)
    if set(current_ids) != set(current_rows):
        raise ValueError("benchmark current annotation is missing")
    max_first_seen = max(
        str(row["collector_first_seen_time"]) for row in current_rows.values()
    )
    universe = load_identity_candidate_universe(
        connection, max_first_seen=max_first_seen,
        limit=IDENTITY_CANDIDATE_UNIVERSE_LIMIT,
    )
    universe_by_id = {
        str(row["candidate_id"]): row for row in universe
    }
    index = build_identity_candidate_index(universe)
    route_rankings: dict[str, dict[str, tuple[str, ...]]] = {
        "deterministic": {},
    }
    if embedding_profile is None:
        rankings = {
            annotation_id: tuple(
                str(candidate["candidate_id"])
                for candidate in retrieve_prior_event_context(row, index, limit=20)
            )
            for annotation_id, row in current_rows.items()
        }
        route_rankings["deterministic"] = rankings
        retrieval_version = "deterministic"
    else:
        embeddings = load_embeddings(
            connection, embedding_profile, expected_rows=universe,
        )
        expected_ids = {str(row["candidate_id"]) for row in universe}
        if not expected_ids <= set(embeddings):
            raise ValueError("benchmark embedding universe is incomplete")
        hybrid_results = {
            annotation_id: retrieve_hybrid_prior_event_context(
                row, universe, embeddings, limit=20,
            )
            for annotation_id, row in current_rows.items()
        }
        for route in ("deterministic", "lexical", "semantic", "combined"):
            route_rankings[route] = {
                annotation_id: result.route_rankings[route]
                for annotation_id, result in hybrid_results.items()
            }
        rankings = route_rankings["combined"]
        retrieval_version = "hybrid"
    for case in positives + negatives:
        current_id = str(case["current_annotation_id"])
        prior_id = str(
            case.get("expected_prior_annotation_id")
            or case.get("forbidden_prior_annotation_id")
        )
        current = current_rows[current_id]
        prior = universe_by_id.get(prior_id)
        if prior is None:
            raise ValueError("benchmark prior annotation is outside the universe")
        if current["content_hash"] != case["current_content_hash"]:
            raise ValueError("benchmark current content hash drifted")
        prior_row = connection.execute(
            "SELECT raw_content_hash FROM news_annotations WHERE annotation_id=?",
            (prior_id,),
        ).fetchone()
        if prior_row is None or prior_row[0] != case["prior_content_hash"]:
            raise ValueError("benchmark prior content hash drifted")
        if str(prior["collector_first_seen_time"]) > str(
            current["collector_first_seen_time"]
        ):
            raise ValueError("benchmark pair violates point-in-time ordering")

    metrics = score_candidate_rankings(positives, negatives, rankings)
    ablations = {
        route: score_candidate_rankings(positives, negatives, values)
        for route, values in route_rankings.items()
    }
    universe_digest = hashlib.sha256()
    for candidate in sorted(universe, key=lambda row: str(row["candidate_id"])):
        universe_digest.update(json.dumps(
            {
                "candidate_id": candidate["candidate_id"],
                "raw_content_hash": candidate["raw_content_hash"],
                "collector_first_seen_time": candidate["collector_first_seen_time"],
                "cluster_id": candidate.get("cluster_id"),
                "annotation": candidate["annotation"],
            },
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8"))
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "candidate_universe_sha256": universe_digest.hexdigest(),
        "candidate_universe_rows": len(universe),
        "top_k": BENCHMARK_TOP_K,
        "retrieval_version": retrieval_version,
        "route_ablations": ablations,
        **metrics,
    }
