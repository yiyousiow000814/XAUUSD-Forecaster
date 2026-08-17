"""Versioned multi-route retrieval for point-in-time news identity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import re
import sqlite3
import uuid

import numpy as np

from .news_impact import (
    IDENTITY_CANDIDATE_UNIVERSE_LIMIT,
    build_identity_candidate_index,
    load_identity_candidate_universe,
    materialize_identity_candidate,
    prior_identity_similarity,
    retrieve_prior_event_context,
)
from .local_embeddings import EmbeddingProfile
from .gemini_embeddings import (
    GEMINI_EMBEDDING_DIMENSIONS,
    GeminiEmbeddingClient,
    GeminiEmbeddingDispatchDeferred,
    GeminiEmbeddingFailure,
)
from .news_scheduler import (
    PROVIDER_DISPATCH_INITIAL_INTERVAL_MS,
    register_provider_dispatch_demand,
)


NEWS_EMBEDDING_MODEL = "gemini-embedding-2"
NEWS_EMBEDDING_TEXT_VERSION = "news-identity-embedding-v2-gemini2"
NEWS_HYBRID_RETRIEVAL_VERSION = "news-hybrid-retrieval-v2"
NEWS_DETERMINISTIC_FALLBACK_VERSION = "news-deterministic-fallback-v1"
NEWS_EMBEDDING_DIMENSIONS = GEMINI_EMBEDDING_DIMENSIONS
NEWS_ROUTE_LIMIT = 40
NEWS_BACKFILL_BATCH = 50
NEWS_BACKFILL_LEASE_SECONDS = 180
NEWS_BACKFILL_COOLDOWN_BASE_SECONDS = 60
NEWS_BACKFILL_COOLDOWN_MAX_SECONDS = 1800
NEWS_RETRIEVAL_PRESSURE_WINDOW = timedelta(minutes=15)
NEWS_RETRIEVAL_RECOVERY_STABILIZATION = timedelta(seconds=30)
_LATIN_TOKEN = re.compile(r"[a-z0-9]+")
_HAN_RUN = re.compile(r"[\u3400-\u9fff]+")
_LEXICAL_STOPWORDS = frozenset({
    "a", "an", "and", "at", "by", "for", "from", "in", "is", "of",
    "on", "the", "to", "with",
})


@dataclass(frozen=True)
class HybridRetrievalResult:
    candidates: tuple[dict, ...]
    route_rankings: dict[str, tuple[str, ...]]


class NewsEmbeddingBackfillPending(RuntimeError):
    """The append-only embedding universe is still catching up."""


class NewsEmbeddingPrerequisiteCooldown(RuntimeError):
    """A durable generation-level embedding prerequisite is cooling down."""

    def __init__(self, row: sqlite3.Row) -> None:
        self.failure_code = str(
            row["last_failure_code"] or "NEWS_EMBEDDING_BACKFILL_PENDING"
        )
        self.provider_http_status = (
            int(row["provider_http_status"])
            if row["provider_http_status"] is not None else None
        )
        self.next_retry_at = str(row["cooldown_until"])
        try:
            self.diagnostic = json.loads(str(row["diagnostic_json"] or "{}"))
        except json.JSONDecodeError:
            self.diagnostic = {}
        super().__init__(f"{self.failure_code} until {self.next_retry_at}")


def identity_embedding_text(row: dict) -> str:
    """Create the immutable multilingual text used for identity retrieval."""
    annotation = row.get("annotation")
    if not isinstance(annotation, dict):
        raise ValueError("identity embedding annotation is missing")
    evidence = " | ".join(
        " ".join(str(item).split())[:240]
        for item in (annotation.get("supporting_evidence") or ())[:3]
        if str(item).strip()
    )
    fields = (
        ("headline", row.get("headline")),
        ("summary", annotation.get("summary_zh")),
        ("actor", annotation.get("actor")),
        ("canonical_actor", annotation.get("canonical_actor_id")),
        ("action", annotation.get("action")),
        ("action_family", annotation.get("action_family")),
        ("object", annotation.get("object")),
        ("canonical_object", annotation.get("canonical_object_id")),
        ("location", annotation.get("location")),
        ("event_time", annotation.get("event_time")),
        ("material_event", annotation.get("material_event_key")),
        ("episode", annotation.get("episode_key")),
        ("material_change", annotation.get("material_change")),
        ("evidence", evidence),
    )
    return "\n".join(
        f"{name}: {' '.join(str(value).split())}"
        for name, value in fields if str(value or "").strip()
    )


def lexical_identity_tokens(row: dict) -> frozenset[str]:
    text = "\n".join(
        line.partition(":")[2] for line in identity_embedding_text(row).splitlines()
    ).lower()
    tokens = {
        f"w:{token}" for token in _LATIN_TOKEN.findall(text)
        if token not in _LEXICAL_STOPWORDS
    }
    for run in _HAN_RUN.findall(text):
        tokens.update(f"h:{character}" for character in run)
        tokens.update(
            f"b:{run[index:index + 2]}" for index in range(len(run) - 1)
        )
    return frozenset(tokens)


def lexical_identity_similarity(current: dict, prior: dict) -> float:
    left = lexical_identity_tokens(current)
    right = lexical_identity_tokens(prior)
    if not left or not right:
        return 0.0
    return len(left & right) / math.sqrt(len(left) * len(right))


def _embedding_id(annotation_id: str, profile: EmbeddingProfile) -> str:
    source = "|".join((
        annotation_id,
        NEWS_EMBEDDING_TEXT_VERSION,
        profile.model_name,
        profile.model_digest,
    ))
    return "news-embedding-" + hashlib.sha256(source.encode()).hexdigest()[:32]


def _backfill_generation_id(profile: EmbeddingProfile) -> str:
    source = "|".join((
        NEWS_EMBEDDING_TEXT_VERSION,
        profile.model_name,
        profile.model_digest,
        str(profile.dimensions),
    ))
    return hashlib.sha256(source.encode()).hexdigest()


def _open_backfill_lease_connection(
    connection: sqlite3.Connection,
) -> sqlite3.Connection:
    database_path = next(
        (
            str(row[2]) for row in connection.execute("PRAGMA database_list")
            if str(row[1]) == "main" and str(row[2])
        ),
        "",
    )
    if not database_path:
        raise ValueError("embedding backfill leases require a file-backed ledger")
    lease_connection = sqlite3.connect(database_path, timeout=30.0)
    lease_connection.row_factory = sqlite3.Row
    return lease_connection


def _claim_backfill_lease(
    connection: sqlite3.Connection,
    profile: EmbeddingProfile,
    *,
    now: datetime | None = None,
) -> tuple[str, str] | None:
    """Claim one cross-lane generation lease before provider admission."""
    instant = now or datetime.now(UTC)
    generation_id = _backfill_generation_id(profile)
    owner = str(uuid.uuid4())
    expires_at = instant + timedelta(seconds=NEWS_BACKFILL_LEASE_SECONDS)
    lease_connection = _open_backfill_lease_connection(connection)
    try:
        lease_connection.execute("BEGIN IMMEDIATE")
        row = lease_connection.execute(
            """SELECT lease_expires_at,cooldown_until,last_failure_code,
                      provider_http_status,diagnostic_json
               FROM news_identity_embedding_backfill_leases_v1
               WHERE generation_id=?""",
            (generation_id,),
        ).fetchone()
        if (
            row is not None and row["cooldown_until"]
            and datetime.fromisoformat(str(row["cooldown_until"])) > instant
        ):
            lease_connection.rollback()
            raise NewsEmbeddingPrerequisiteCooldown(row)
        if row is not None and datetime.fromisoformat(str(row[0])) > instant:
            lease_connection.rollback()
            return None
        timestamp = instant.isoformat(timespec="microseconds")
        lease_connection.execute(
            """INSERT INTO news_identity_embedding_backfill_leases_v1
               (generation_id,lease_owner,lease_expires_at,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(generation_id) DO UPDATE SET
                 lease_owner=excluded.lease_owner,
                 lease_expires_at=excluded.lease_expires_at,
                 updated_at=excluded.updated_at""",
            (
                generation_id,
                owner,
                expires_at.isoformat(timespec="microseconds"),
                timestamp,
            ),
        )
        lease_connection.commit()
    except Exception:
        lease_connection.rollback()
        raise
    finally:
        lease_connection.close()
    return generation_id, owner


def _release_backfill_lease(
    connection: sqlite3.Connection,
    generation_id: str,
    owner: str,
) -> None:
    lease_connection = _open_backfill_lease_connection(connection)
    try:
        with lease_connection:
            row = lease_connection.execute(
                """SELECT cooldown_until
                   FROM news_identity_embedding_backfill_leases_v1
                   WHERE generation_id=? AND lease_owner=?""",
                (generation_id, owner),
            ).fetchone()
            if row is None:
                return
            now = datetime.now(UTC)
            if row[0] and datetime.fromisoformat(str(row[0])) > now:
                timestamp = now.isoformat(timespec="microseconds")
                lease_connection.execute(
                    """UPDATE news_identity_embedding_backfill_leases_v1
                       SET lease_owner='',lease_expires_at=?,updated_at=?
                       WHERE generation_id=? AND lease_owner=?""",
                    (timestamp, timestamp, generation_id, owner),
                )
            else:
                lease_connection.execute(
                    """DELETE FROM news_identity_embedding_backfill_leases_v1
                       WHERE generation_id=? AND lease_owner=?""",
                    (generation_id, owner),
                )
    finally:
        lease_connection.close()


def _record_backfill_failure(
    connection: sqlite3.Connection,
    generation_id: str,
    owner: str,
    error: GeminiEmbeddingFailure,
) -> None:
    state = _open_backfill_lease_connection(connection)
    try:
        with state:
            row = state.execute(
                """SELECT failure_count
                   FROM news_identity_embedding_backfill_leases_v1
                   WHERE generation_id=? AND lease_owner=?""",
                (generation_id, owner),
            ).fetchone()
            if row is None:
                raise ValueError("embedding backfill lease is not owned")
            failure_count = int(row[0] or 0) + 1
            fallback = min(
                NEWS_BACKFILL_COOLDOWN_MAX_SECONDS,
                NEWS_BACKFILL_COOLDOWN_BASE_SECONDS
                * (2 ** min(failure_count - 1, 10)),
            )
            cooldown_seconds = error.retry_after_seconds or fallback
            cooldown_seconds = min(86_400, max(1, int(cooldown_seconds)))
            failed_at = datetime.now(UTC)
            error.next_retry_at = (
                failed_at + timedelta(seconds=cooldown_seconds)
            ).isoformat(timespec="microseconds")
            diagnostic = {
                **error.diagnostic,
                "cooldown_seconds": cooldown_seconds,
            }
            serialized = json.dumps(
                diagnostic, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            if len(serialized) > 1000:
                diagnostic.pop("quota_identifier", None)
                diagnostic.pop("quota_reason", None)
                serialized = json.dumps(
                    diagnostic, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                )
            state.execute(
                """UPDATE news_identity_embedding_backfill_leases_v1
                   SET cooldown_until=?,failure_count=?,last_failure_code=?,
                       provider_http_status=?,diagnostic_json=?,failed_at=?,
                       updated_at=?
                   WHERE generation_id=? AND lease_owner=?""",
                (
                    error.next_retry_at,
                    failure_count, error.failure_code,
                    error.provider_http_status, serialized,
                    failed_at.isoformat(timespec="microseconds"),
                    failed_at.isoformat(timespec="microseconds"),
                    generation_id, owner,
                ),
            )
    finally:
        state.close()


def _clear_backfill_state(
    connection: sqlite3.Connection, generation_id: str, owner: str,
) -> None:
    state = _open_backfill_lease_connection(connection)
    try:
        with state:
            state.execute(
                """DELETE FROM news_identity_embedding_backfill_leases_v1
                   WHERE generation_id=? AND lease_owner=?""",
                (generation_id, owner),
            )
    finally:
        state.close()


def append_missing_embeddings(
    connection: sqlite3.Connection,
    rows: list[dict],
    client: GeminiEmbeddingClient,
    *,
    limit: int = NEWS_BACKFILL_BATCH,
) -> tuple[EmbeddingProfile, int]:
    """Append a bounded batch of missing immutable vectors."""
    profile = client.profile()
    lease = _claim_backfill_lease(connection, profile)
    if lease is None:
        return profile, 0
    generation_id, owner = lease
    try:
        existing = {
            str(row[0])
            for row in connection.execute(
                """SELECT annotation_id FROM news_identity_embeddings_v1
                   WHERE embedding_text_version=? AND model_name=?
                     AND model_digest=?""",
                (
                    NEWS_EMBEDDING_TEXT_VERSION,
                    profile.model_name,
                    profile.model_digest,
                ),
            ).fetchall()
        }
        selected = [
            row for row in rows
            if str(row["candidate_id"]) not in existing
        ][:max(0, limit)]
        if not selected:
            _clear_backfill_state(connection, generation_id, owner)
            return profile, 0
        texts = [identity_embedding_text(row) for row in selected]
        dispatch_count = getattr(client, "document_dispatch_count", None)
        if callable(dispatch_count):
            count = dispatch_count(texts)
            selected = selected[:count]
            texts = texts[:count]
        vectors = client.embed(texts, profile)
        embedded_at = datetime.now(UTC).isoformat(timespec="microseconds")
        records = []
        for row, text, vector in zip(selected, texts, vectors, strict=True):
            annotation_id = str(row["candidate_id"])
            records.append((
                _embedding_id(annotation_id, profile),
                annotation_id,
                str(row["raw_content_hash"]),
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                NEWS_EMBEDDING_TEXT_VERSION,
                profile.model_name,
                profile.model_digest,
                profile.dimensions,
                np.asarray(vector, dtype="<f4").tobytes(),
                embedded_at,
            ))
        with connection:
            connection.executemany(
                """INSERT OR IGNORE INTO news_identity_embeddings_v1
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                records,
            )
        mark_committed = getattr(client, "mark_last_vectors_committed", None)
        if callable(mark_committed):
            mark_committed()
        _clear_backfill_state(connection, generation_id, owner)
        return profile, len(records)
    except GeminiEmbeddingFailure as error:
        _record_backfill_failure(
            connection, generation_id, owner, error,
        )
        raise
    finally:
        _release_backfill_lease(connection, generation_id, owner)


def load_embeddings(
    connection: sqlite3.Connection,
    profile: EmbeddingProfile,
    *,
    expected_rows: list[dict] | None = None,
) -> dict[str, np.ndarray]:
    rows = connection.execute(
        """SELECT annotation_id,raw_content_hash,embedding_text_hash,
                  dimensions,vector_blob
           FROM news_identity_embeddings_v1
           WHERE embedding_text_version=? AND model_name=? AND model_digest=?""",
        (
            NEWS_EMBEDDING_TEXT_VERSION,
            profile.model_name,
            profile.model_digest,
        ),
    ).fetchall()
    expected = {
        str(row["candidate_id"]): row for row in (expected_rows or ())
    }
    result = {}
    for annotation_id, raw_hash, text_hash, dimensions, blob in rows:
        if int(dimensions) != profile.dimensions:
            raise ValueError("stored news embedding dimensions are invalid")
        source = expected.get(str(annotation_id))
        if source is not None:
            if str(raw_hash) != str(source["raw_content_hash"]):
                raise ValueError("stored news embedding source hash drifted")
            expected_text_hash = hashlib.sha256(
                identity_embedding_text(source).encode("utf-8")
            ).hexdigest()
            if str(text_hash) != expected_text_hash:
                raise ValueError("stored news embedding text hash drifted")
        vector = np.frombuffer(blob, dtype="<f4")
        if vector.size != profile.dimensions:
            raise ValueError("stored news embedding payload is invalid")
        result[str(annotation_id)] = vector
    return result


def latest_embedding_profile(
    connection: sqlite3.Connection,
) -> EmbeddingProfile:
    row = connection.execute(
        """SELECT model_name,model_digest,dimensions
           FROM news_identity_embeddings_v1
           WHERE embedding_text_version=? AND model_name=?
           ORDER BY embedded_at DESC LIMIT 1""",
        (NEWS_EMBEDDING_TEXT_VERSION, NEWS_EMBEDDING_MODEL),
    ).fetchone()
    if row is None:
        raise ValueError("news identity embeddings have not been backfilled")
    return EmbeddingProfile(str(row[0]), str(row[1]), int(row[2]))


def _write_retrieval_mode_state(
    connection: sqlite3.Connection,
    *,
    mode: str,
    reason: str,
    pressure: dict[str, object],
    now: datetime,
    recovery_observed_at: str | None = None,
) -> None:
    prior = connection.execute(
        """SELECT mode,mode_since FROM news_ai_retrieval_mode_state_v1
           WHERE state_id='NEWS_IDENTITY'"""
    ).fetchone()
    mode_since = (
        str(prior["mode_since"])
        if prior is not None and str(prior["mode"]) == mode
        else now.isoformat(timespec="microseconds")
    )
    with connection:
        connection.execute(
            """INSERT INTO news_ai_retrieval_mode_state_v1
               (state_id,mode,reason,mode_since,recovery_observed_at,
                pressure_json,updated_at)
               VALUES ('NEWS_IDENTITY',?,?,?,?,?,?)
               ON CONFLICT(state_id) DO UPDATE SET
                 mode=excluded.mode,reason=excluded.reason,
                 mode_since=excluded.mode_since,
                 recovery_observed_at=excluded.recovery_observed_at,
                 pressure_json=excluded.pressure_json,
                 updated_at=excluded.updated_at""",
            (
                mode, reason, mode_since, recovery_observed_at,
                json.dumps(pressure, sort_keys=True, separators=(",", ":")),
                now.isoformat(timespec="microseconds"),
            ),
        )


def _embedding_universe_progress(
    connection: sqlite3.Connection,
    universe: list[dict],
    profile: EmbeddingProfile,
    *,
    since: datetime,
) -> tuple[int, int, str | None]:
    candidate_ids = [str(row["candidate_id"]) for row in universe]
    stored = 0
    recent = 0
    latest: str | None = None
    for start in range(0, len(candidate_ids), 800):
        chunk = candidate_ids[start:start + 800]
        placeholders = ",".join("?" for _ in chunk)
        row = connection.execute(
            f"""SELECT count(*) AS stored,
                       sum(CASE WHEN embedded_at>=? THEN 1 ELSE 0 END) AS recent,
                       max(embedded_at) AS latest
                FROM news_identity_embeddings_v1
                WHERE embedding_text_version=? AND model_name=?
                  AND model_digest=?
                  AND annotation_id IN ({placeholders})""",
            (
                since.isoformat(), NEWS_EMBEDDING_TEXT_VERSION,
                profile.model_name, profile.model_digest, *chunk,
            ),
        ).fetchone()
        stored += int(row["stored"])
        recent += int(row["recent"] or 0)
        if row["latest"] and (latest is None or str(row["latest"]) > latest):
            latest = str(row["latest"])
    return stored, recent, latest


def _adaptive_retrieval_policy(
    connection: sqlite3.Connection,
    universe: list[dict],
    profile: EmbeddingProfile,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Choose wait/fallback/hybrid from live pressure with durable hysteresis."""
    instant = now or datetime.now(UTC)
    expected = len(universe)
    stored, recent_progress, latest_progress_raw = _embedding_universe_progress(
        connection, universe, profile,
        since=instant - NEWS_RETRIEVAL_PRESSURE_WINDOW,
    )
    impact = connection.execute(
        """SELECT count(*) AS backlog,min(created_at) AS oldest,
                  sum(CASE WHEN last_error LIKE 'NEWS_EMBEDDING_%'
                           THEN 1 ELSE 0 END) AS blocked
           FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_IMPACT'
             AND state IN ('QUEUED','LEASED','BACKING_OFF')
             AND (state='LEASED' OR available_at<=?)""",
        (instant.isoformat(),),
    ).fetchone()
    recent_created = int(connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_IMPACT' AND created_at>=?""",
        ((instant - NEWS_RETRIEVAL_PRESSURE_WINDOW).isoformat(),),
    ).fetchone()[0])
    recent_completed = int(connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_IMPACT' AND state='COMPLETED'
             AND completed_at>=?""",
        ((instant - NEWS_RETRIEVAL_PRESSURE_WINDOW).isoformat(),),
    ).fetchone()[0])
    provider = connection.execute(
        """SELECT interval_ms,cooldown_until,next_eligible_at
           FROM news_ai_provider_dispatch_state_v1
           WHERE provider_scope='GOOGLE_GENERATIVE_LANGUAGE'"""
    ).fetchone()
    generation = connection.execute(
        """SELECT cooldown_until,last_failure_code
           FROM news_identity_embedding_backfill_leases_v1
           WHERE generation_id=?""",
        (_backfill_generation_id(profile),),
    ).fetchone()
    provider_cooldown = (
        datetime.fromisoformat(str(provider["cooldown_until"]))
        if provider is not None and provider["cooldown_until"] else None
    )
    generation_cooldown = (
        datetime.fromisoformat(str(generation["cooldown_until"]))
        if generation is not None and generation["cooldown_until"] else None
    )
    latest_progress = (
        datetime.fromisoformat(str(latest_progress_raw))
        if latest_progress_raw else None
    )
    missing = max(0, expected - stored)
    backlog = int(impact["backlog"])
    blocked = int(impact["blocked"] or 0)
    oldest_age_seconds = (
        max(0, int((instant - datetime.fromisoformat(
            str(impact["oldest"]),
        )).total_seconds()))
        if impact["oldest"] else 0
    )
    pressure = {
        "expected": expected,
        "stored": stored,
        "missing": missing,
        "embedding_progress_15m": recent_progress,
        "impact_backlog": backlog,
        "impact_created_15m": recent_created,
        "impact_completed_15m": recent_completed,
        "impact_oldest_age_seconds": oldest_age_seconds,
        "dependency_blocking_fanout": blocked,
        "provider_interval_ms": (
            int(provider["interval_ms"]) if provider is not None else None
        ),
    }
    prior = connection.execute(
        """SELECT * FROM news_ai_retrieval_mode_state_v1
           WHERE state_id='NEWS_IDENTITY'"""
    ).fetchone()
    healthy_complete = (
        missing == 0
        and (provider_cooldown is None or provider_cooldown <= instant)
        and (generation_cooldown is None or generation_cooldown <= instant)
    )
    if healthy_complete:
        if prior is not None and str(prior["mode"]) == "DETERMINISTIC_FALLBACK":
            observed = (
                datetime.fromisoformat(str(prior["recovery_observed_at"]))
                if prior["recovery_observed_at"] else None
            )
            if observed is None:
                observed = instant
            if instant - observed < NEWS_RETRIEVAL_RECOVERY_STABILIZATION:
                _write_retrieval_mode_state(
                    connection, mode="DETERMINISTIC_FALLBACK",
                    reason="HYBRID_RECOVERY_STABILIZING", pressure=pressure,
                    now=instant, recovery_observed_at=observed.isoformat(),
                )
                return "DETERMINISTIC_FALLBACK", "HYBRID_RECOVERY_STABILIZING"
        _write_retrieval_mode_state(
            connection, mode="HYBRID", reason="EMBEDDING_GENERATION_READY",
            pressure=pressure, now=instant,
        )
        return "HYBRID", "EMBEDDING_GENERATION_READY"

    cooldown_reason = None
    if generation_cooldown is not None and generation_cooldown > instant:
        cooldown_reason = str(
            generation["last_failure_code"]
            or "NEWS_EMBEDDING_PREREQUISITE_COOLDOWN"
        )
    elif provider_cooldown is not None and provider_cooldown > instant:
        cooldown_reason = "GOOGLE_PROVIDER_SHARED_COOLDOWN"
    provider_pressure = (
        provider is not None
        and int(provider["interval_ms"]) > PROVIDER_DISPATCH_INITIAL_INTERVAL_MS
    )
    stalled = (
        latest_progress is None
        or latest_progress < instant - NEWS_RETRIEVAL_PRESSURE_WINDOW
    )
    queue_not_draining = backlog > recent_completed
    growth_pressure = recent_created > recent_completed
    progress_insufficient = missing > recent_progress
    adaptive_pressure = (
        backlog > 0 and queue_not_draining and progress_insufficient
        and (growth_pressure or stalled or provider_pressure or blocked > 0)
    )
    prior_fallback = (
        prior is not None and str(prior["mode"]) == "DETERMINISTIC_FALLBACK"
    )
    if cooldown_reason or adaptive_pressure or prior_fallback:
        reason = cooldown_reason or (
            "ADAPTIVE_RETRIEVAL_PRESSURE"
            if adaptive_pressure else "ADAPTIVE_FALLBACK_HYSTERESIS"
        )
        _write_retrieval_mode_state(
            connection, mode="DETERMINISTIC_FALLBACK", reason=reason,
            pressure=pressure, now=instant,
        )
        return "DETERMINISTIC_FALLBACK", reason
    _write_retrieval_mode_state(
        connection, mode="WAIT_FOR_HYBRID",
        reason="EMBEDDING_CATCHUP_HEALTHY", pressure=pressure, now=instant,
    )
    return "WAIT_FOR_HYBRID", "EMBEDDING_CATCHUP_HEALTHY"


def _force_retrieval_fallback(
    connection: sqlite3.Connection, *, reason: str,
) -> None:
    row = connection.execute(
        """SELECT pressure_json FROM news_ai_retrieval_mode_state_v1
           WHERE state_id='NEWS_IDENTITY'"""
    ).fetchone()
    try:
        pressure = json.loads(str(row[0])) if row is not None else {}
    except json.JSONDecodeError:
        pressure = {}
    _write_retrieval_mode_state(
        connection, mode="DETERMINISTIC_FALLBACK", reason=reason,
        pressure=pressure, now=datetime.now(UTC),
    )


def attach_hybrid_prior_event_context(
    connection: sqlite3.Connection,
    records: list[dict],
    *,
    client: GeminiEmbeddingClient | None = None,
) -> list[dict]:
    """Attach complete hybrid context before the final identity request."""
    if not records:
        return records
    embedding_client = client or GeminiEmbeddingClient(connection)
    max_first_seen = max(
        str(row["collector_first_seen_time"]) for row in records
    )
    universe = load_identity_candidate_universe(
        connection,
        max_first_seen=max_first_seen,
        limit=IDENTITY_CANDIDATE_UNIVERSE_LIMIT,
    )
    current_ids = {str(row["annotation_id"]) for row in records}
    current_rows = [
        row for row in universe if str(row["candidate_id"]) in current_ids
    ]
    if len(current_rows) != len(current_ids):
        raise ValueError("current news embedding source is outside the universe")
    register_provider_dispatch_demand(
        connection, provider_task="news-impact",
    )
    profile = embedding_client.profile()
    initial_mode, initial_reason = _adaptive_retrieval_policy(
        connection, universe, profile,
    )
    # New annotations can become eligible between the deployment backfill and
    # the next impact cycle. Catch up the complete point-in-time universe, not
    # only the record currently holding the scheduler lease.
    try:
        profile, _ = append_missing_embeddings(
            connection, universe, embedding_client, limit=NEWS_BACKFILL_BATCH,
        )
    except (GeminiEmbeddingFailure, GeminiEmbeddingDispatchDeferred,
            NewsEmbeddingPrerequisiteCooldown) as error:
        _force_retrieval_fallback(
            connection, reason=error.failure_code,
        )
        return _attach_deterministic_fallback(
            connection, records, universe, reason=error.failure_code,
        )
    embeddings = load_embeddings(
        connection, profile, expected_rows=universe,
    )
    missing = [
        str(row["candidate_id"]) for row in universe
        if str(row["candidate_id"]) not in embeddings
    ]
    selected_mode, selected_reason = _adaptive_retrieval_policy(
        connection, universe, profile,
    )
    if initial_mode == "DETERMINISTIC_FALLBACK" or (
        selected_mode == "DETERMINISTIC_FALLBACK"
    ):
        return _attach_deterministic_fallback(
            connection, records, universe,
            reason=(
                initial_reason
                if initial_mode == "DETERMINISTIC_FALLBACK"
                else selected_reason
            ),
        )
    if missing:
        raise NewsEmbeddingBackfillPending(
            f"news identity embedding backfill is incomplete: {len(missing)} missing"
        )
    pending_queries: list[dict] = []
    for row in records:
        frozen = _load_retrieval_receipt(connection, row, profile)
        if frozen is not None:
            candidates, route_rankings = frozen
            row["prior_event_context"] = candidates
            row["identity_retrieval_version"] = NEWS_HYBRID_RETRIEVAL_VERSION
            row["identity_retrieval_mode"] = "HYBRID"
            row["identity_retrieval_reason"] = "EMBEDDING_GENERATION_READY"
            row["identity_retrieval_routes"] = route_rankings
            continue
        pending_queries.append(row)
    pending_ids = {str(item["annotation_id"]) for item in pending_queries}
    query_rows = [
        row for row in current_rows
        if str(row["candidate_id"]) in pending_ids
    ]
    embed_queries = getattr(embedding_client, "embed_queries", embedding_client.embed)
    try:
        query_vectors = embed_queries(
            [identity_embedding_text(row) for row in query_rows], profile,
        )
    except (GeminiEmbeddingFailure,
            GeminiEmbeddingDispatchDeferred) as error:
        return _attach_deterministic_fallback(
            connection, records, universe, reason=error.failure_code,
        )
    query_by_id = dict(zip(
        [str(row["candidate_id"]) for row in query_rows],
        query_vectors,
        strict=True,
    ))
    for row in pending_queries:
        result = retrieve_hybrid_prior_event_context(
            row, universe, embeddings,
            query_vector=query_by_id[str(row["annotation_id"])],
        )
        row["prior_event_context"] = list(result.candidates)
        row["identity_retrieval_version"] = NEWS_HYBRID_RETRIEVAL_VERSION
        row["identity_retrieval_mode"] = "HYBRID"
        row["identity_retrieval_reason"] = "EMBEDDING_GENERATION_READY"
        row["identity_retrieval_routes"] = {
            route: list(candidate_ids)
            for route, candidate_ids in result.route_rankings.items()
        }
        _append_retrieval_receipt(
            connection, row, universe, profile, result,
        )
    return records


def _candidate_universe_hash(universe: list[dict]) -> str:
    payload = [
        (
            str(row["candidate_id"]),
            str(row["raw_content_hash"]),
            str(row["collector_first_seen_time"]),
        )
        for row in sorted(universe, key=lambda item: str(item["candidate_id"]))
    ]
    return hashlib.sha256(json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _load_deterministic_fallback_receipt(
    connection: sqlite3.Connection, current: dict,
) -> tuple[list[dict], str] | None:
    row = connection.execute(
        """SELECT selected_candidates_json,fallback_reason
           FROM news_identity_deterministic_fallback_receipts_v1
           WHERE annotation_id=? AND retrieval_version=?""",
        (
            str(current["annotation_id"]),
            NEWS_DETERMINISTIC_FALLBACK_VERSION,
        ),
    ).fetchone()
    if row is None:
        return None
    candidates = json.loads(row[0])
    if not isinstance(candidates, list) or any(
        not isinstance(candidate, dict) for candidate in candidates
    ):
        raise ValueError("stored deterministic fallback receipt is invalid")
    return candidates, str(row[1])


def _attach_deterministic_fallback(
    connection: sqlite3.Connection,
    records: list[dict],
    universe: list[dict],
    *,
    reason: str,
) -> list[dict]:
    """Attach the frozen pre-embedding route during a durable provider wait."""
    candidate_index = build_identity_candidate_index(universe)
    universe_hash = _candidate_universe_hash(universe)
    for row in records:
        frozen = _load_deterministic_fallback_receipt(connection, row)
        if frozen is None:
            candidates = retrieve_prior_event_context(row, candidate_index)
            frozen_reason = reason
            candidate_ids = [
                str(candidate["candidate_id"]) for candidate in candidates
            ]
            identity = "|".join((
                str(row["annotation_id"]),
                NEWS_DETERMINISTIC_FALLBACK_VERSION,
            ))
            with connection:
                connection.execute(
                    """INSERT OR IGNORE INTO
                       news_identity_deterministic_fallback_receipts_v1
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        "news-retrieval-fallback-" + hashlib.sha256(
                            identity.encode()
                        ).hexdigest()[:32],
                        str(row["annotation_id"]),
                        NEWS_DETERMINISTIC_FALLBACK_VERSION,
                        "DETERMINISTIC_FALLBACK",
                        frozen_reason,
                        universe_hash,
                        json.dumps(
                            candidate_ids, separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            candidates, separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                        datetime.now(UTC).isoformat(timespec="microseconds"),
                    ),
                )
        else:
            candidates, frozen_reason = frozen
            candidate_ids = [
                str(candidate["candidate_id"]) for candidate in candidates
            ]
        row["prior_event_context"] = list(candidates)
        row["identity_retrieval_version"] = (
            NEWS_DETERMINISTIC_FALLBACK_VERSION
        )
        row["identity_retrieval_mode"] = "DETERMINISTIC_FALLBACK"
        row["identity_retrieval_reason"] = frozen_reason
        row["identity_retrieval_routes"] = {
            "deterministic": candidate_ids,
        }
    return records


def _load_retrieval_receipt(
    connection: sqlite3.Connection,
    current: dict,
    profile: EmbeddingProfile,
) -> tuple[list[dict], dict[str, list[str]]] | None:
    row = connection.execute(
        """SELECT route_rankings_json,selected_candidates_json
           FROM news_identity_retrieval_receipts_v1
           WHERE annotation_id=? AND retrieval_version=? AND model_digest=?""",
        (
            str(current["annotation_id"]),
            NEWS_HYBRID_RETRIEVAL_VERSION,
            profile.model_digest,
        ),
    ).fetchone()
    if row is None:
        return None
    routes = json.loads(row[0])
    candidates = json.loads(row[1])
    if not isinstance(routes, dict) or not isinstance(candidates, list):
        raise ValueError("stored news retrieval receipt is invalid")
    if any(not isinstance(candidate, dict) for candidate in candidates):
        raise ValueError("stored news retrieval candidates are invalid")
    return candidates, {
        str(route): [str(candidate_id) for candidate_id in candidate_ids]
        for route, candidate_ids in routes.items()
        if isinstance(candidate_ids, list)
    }


def _append_retrieval_receipt(
    connection: sqlite3.Connection,
    current: dict,
    universe: list[dict],
    profile: EmbeddingProfile,
    result: HybridRetrievalResult,
) -> None:
    annotation_id = str(current["annotation_id"])
    universe_hash = _candidate_universe_hash(universe)
    identity = "|".join((
        annotation_id,
        NEWS_HYBRID_RETRIEVAL_VERSION,
        profile.model_digest,
    ))
    with connection:
        connection.execute(
            """INSERT OR IGNORE INTO news_identity_retrieval_receipts_v1
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "news-retrieval-" + hashlib.sha256(
                    identity.encode()
                ).hexdigest()[:32],
                annotation_id,
                NEWS_HYBRID_RETRIEVAL_VERSION,
                NEWS_EMBEDDING_TEXT_VERSION,
                profile.model_name,
                profile.model_digest,
                universe_hash,
                json.dumps(
                    result.route_rankings,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                json.dumps(
                    result.candidates,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                datetime.now(UTC).isoformat(timespec="microseconds"),
            ),
        )


def _valid_prior(current: dict, prior: dict) -> bool:
    if str(prior["collector_first_seen_time"]) > str(
        current["collector_first_seen_time"]
    ):
        return False
    return not (
        prior["source"] == current["source"]
        and prior["source_item_id"] == current["source_item_id"]
        and prior["revision_number"] == current["revision_number"]
    )


def _rank_scores(scores: dict[str, float], limit: int) -> tuple[str, ...]:
    return tuple(
        candidate_id for candidate_id, _ in sorted(
            scores.items(), key=lambda item: (item[1], item[0]), reverse=True,
        )[:limit]
    )


def retrieve_hybrid_prior_event_context(
    current: dict,
    universe: list[dict],
    embeddings: dict[str, np.ndarray],
    *,
    query_vector: np.ndarray | None = None,
    limit: int = 5,
) -> HybridRetrievalResult:
    """Union deterministic, lexical, and semantic recall, then rerank."""
    if not 1 <= limit <= 20:
        raise ValueError("hybrid identity result bound is invalid")
    universe_by_id = {str(row["candidate_id"]): row for row in universe}
    deterministic = retrieve_prior_event_context(
        current, build_identity_candidate_index(universe), limit=20,
    )
    deterministic_ids = tuple(str(row["candidate_id"]) for row in deterministic)

    lexical_scores = {
        candidate_id: lexical_identity_similarity(current, prior)
        for candidate_id, prior in universe_by_id.items()
        if _valid_prior(current, prior)
    }
    lexical_scores = {
        key: value for key, value in lexical_scores.items() if value >= 0.08
    }
    lexical_ids = _rank_scores(lexical_scores, NEWS_ROUTE_LIMIT)

    current_id = str(current.get("annotation_id") or current.get("candidate_id") or "")
    current_vector = query_vector if query_vector is not None else embeddings.get(current_id)
    semantic_scores = {}
    if current_vector is not None:
        semantic_scores = {
            candidate_id: float(np.dot(current_vector, vector))
            for candidate_id, vector in embeddings.items()
            if candidate_id in universe_by_id
            and _valid_prior(current, universe_by_id[candidate_id])
        }
        semantic_scores = {
            key: value for key, value in semantic_scores.items() if value >= 0.45
        }
    semantic_ids = _rank_scores(semantic_scores, NEWS_ROUTE_LIMIT)

    route_rankings = {
        "deterministic": deterministic_ids,
        "lexical": lexical_ids,
        "semantic": semantic_ids,
    }
    fused_ids = set(deterministic_ids) | set(lexical_ids) | set(semantic_ids)
    fused_scores = {}
    for candidate_id in fused_ids:
        prior = universe_by_id[candidate_id]
        identity = prior_identity_similarity(
            {**current["annotation"], "cluster_id": current.get("cluster_id")},
            {**prior["annotation"], "cluster_id": prior.get("cluster_id")},
        )
        score = identity * 2.5
        score += lexical_scores.get(candidate_id, 0.0) * 0.65
        score += semantic_scores.get(candidate_id, 0.0) * 0.85
        for route, weight in (
            (deterministic_ids, 1.4),
            (lexical_ids, 0.8),
            (semantic_ids, 1.0),
        ):
            if candidate_id in route:
                score += weight / (5 + route.index(candidate_id) + 1)
        fused_scores[candidate_id] = score

    reranked_ids = _rank_scores(fused_scores, len(fused_scores))
    candidates = []
    for candidate_id in reranked_ids:
        candidate = materialize_identity_candidate(
            current,
            universe_by_id[candidate_id],
            prior_identity_similarity(
                {**current["annotation"], "cluster_id": current.get("cluster_id")},
                {
                    **universe_by_id[candidate_id]["annotation"],
                    "cluster_id": universe_by_id[candidate_id].get("cluster_id"),
                },
            ),
        )
        if candidate is not None:
            candidates.append(candidate)
        if len(candidates) >= limit:
            break
    route_rankings["combined"] = tuple(
        str(candidate["candidate_id"]) for candidate in candidates
    )
    return HybridRetrievalResult(tuple(candidates), route_rankings)
