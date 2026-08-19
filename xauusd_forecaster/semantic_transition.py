"""Declared semantic-contract transition and demand policy.

Historical evidence is immutable. This policy decides whether a new active
contract may reuse it, transform it deterministically, or must schedule a
bounded model review. It never performs provider work itself.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from .news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
    PREVIOUS_NEWS_PROMPT_VERSION,
)

REUSE_COMPATIBLE = "REUSE_COMPATIBLE"
DETERMINISTIC_MIGRATION = "DETERMINISTIC_MIGRATION"
MODEL_REVIEW_REQUIRED = "MODEL_REVIEW_REQUIRED"
TRANSITION_KINDS = frozenset({
    REUSE_COMPATIBLE, DETERMINISTIC_MIGRATION, MODEL_REVIEW_REQUIRED,
})

CURRENT_OPERATIONAL = "CURRENT_OPERATIONAL"
TRAINING_REQUIRED = "TRAINING_REQUIRED"
ARCHIVAL_ONLY = "ARCHIVAL_ONLY"
DEMAND_CLASSES = frozenset({CURRENT_OPERATIONAL, TRAINING_REQUIRED, ARCHIVAL_ONLY})
TRANSITION_CONTRACT_VERSION = "semantic-transition-v1"


@dataclass(frozen=True)
class SemanticTransition:
    from_version: str
    to_version: str
    kind: str
    rationale: str
    migrator_version: str | None = None


@dataclass(frozen=True)
class SemanticTransitionContract:
    fingerprint: str
    contract_json: str


class SemanticTransitionContractChanged(RuntimeError):
    """Persisted progress belongs to a different transition implementation."""

    code = "SEMANTIC_TRANSITION_CONTRACT_CHANGED"


def semantic_transition_contract(
    transition: SemanticTransition,
) -> SemanticTransitionContract:
    """Return the stable declared identity that may resume one cursor."""
    if transition.kind not in TRANSITION_KINDS:
        raise ValueError("semantic transition kind is not controlled")
    if transition.kind == DETERMINISTIC_MIGRATION:
        if not transition.migrator_version:
            raise ValueError("deterministic transition has no migrator version")
    elif transition.migrator_version is not None:
        raise ValueError("non-deterministic transition cannot declare a migrator")
    components = {
        "contract_version": TRANSITION_CONTRACT_VERSION,
        "from_prompt_version": transition.from_version,
        "kind": transition.kind,
        "migrator_version": transition.migrator_version,
        "to_prompt_version": transition.to_version,
    }
    contract_json = json.dumps(
        components, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    return SemanticTransitionContract(
        hashlib.sha256(contract_json.encode("utf-8")).hexdigest(),
        contract_json,
    )


DECLARED_TRANSITIONS = {
    (PREVIOUS_NEWS_PROMPT_VERSION, CURRENT_NEWS_PROMPT_VERSION): SemanticTransition(
        PREVIOUS_NEWS_PROMPT_VERSION,
        CURRENT_NEWS_PROMPT_VERSION,
        MODEL_REVIEW_REQUIRED,
        "V17 changes source-grounded visible-Latin validation semantics.",
    ),
}


def transition_for(from_version: str, to_version: str) -> SemanticTransition:
    if from_version == to_version:
        return SemanticTransition(
            from_version, to_version, REUSE_COMPATIBLE,
            "The semantic contract version is unchanged.",
        )
    try:
        return DECLARED_TRANSITIONS[(from_version, to_version)]
    except KeyError:
        raise ValueError("semantic contract transition is not declared") from None


def requires_model_review(from_version: str, to_version: str) -> bool:
    return transition_for(from_version, to_version).kind == MODEL_REVIEW_REQUIRED


def provider_dispatches_for_transition(transition: SemanticTransition) -> int:
    """Return the maximum provider work admitted per demanded record.

    Reuse and deterministic migration preserve prior model provenance. They
    cannot be represented as a new model invocation.
    """
    if transition.kind not in TRANSITION_KINDS:
        raise ValueError("semantic transition kind is not controlled")
    return 1 if transition.kind == MODEL_REVIEW_REQUIRED else 0


def _canonical_cleanup(annotation: dict[str, object], source_text: str) -> None:
    from .news_semantics import canonicalize_active_annotation
    canonicalize_active_annotation(annotation, source_text=source_text)


DETERMINISTIC_MIGRATORS: dict[str, Callable[[dict[str, object], str], None]] = {
    "canonical-cleanup-v1": _canonical_cleanup,
}


def execute_transition_page(
    connection: sqlite3.Connection,
    transition: SemanticTransition,
    *,
    activated_at: datetime,
    now: datetime,
    page_size: int = 50,
) -> dict[str, object]:
    """Apply one replay-safe, zero-provider historical transition page."""
    contract = semantic_transition_contract(transition)
    migrator = None
    if transition.kind == DETERMINISTIC_MIGRATION:
        try:
            assert transition.migrator_version is not None
            migrator = DETERMINISTIC_MIGRATORS[transition.migrator_version]
        except KeyError:
            raise ValueError("deterministic transition migrator is not declared") from None
    bounded_page = max(1, min(100, int(page_size)))
    timestamp = now.astimezone(UTC).isoformat(timespec="microseconds")
    from .annotation import ANNOTATION_BODY_MIN_CHARACTERS, SUPPORTED_GEMINI_MODELS
    from .news_identity import preferred_cluster_peer_predicate
    from .news_semantics import model_usable_annotation_predicate
    from .news_time import (
        assess_news_time,
        category_time_rule,
        register_news_semantic_eligibility_sql,
        semantic_eligibility_sql_predicate,
    )
    register_news_semantic_eligibility_sql(connection)
    forward_epoch = datetime.fromisoformat(str(connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()[0]))
    source_usable = model_usable_annotation_predicate("a")
    current_usable = model_usable_annotation_predicate("current")
    newer_usable = model_usable_annotation_predicate("newer")
    state = connection.execute(
        """SELECT * FROM news_annotation_transition_state_v1
           WHERE from_prompt_version=? AND to_prompt_version=?""",
        (transition.from_version, transition.to_version),
    ).fetchone()
    if state is None:
        with connection:
            connection.execute(
                """INSERT INTO news_annotation_transition_state_v1
                   (from_prompt_version,to_prompt_version,transition_kind,
                    transition_fingerprint,transition_contract_json,
                    state,updated_at) VALUES (?,?,?,?,?,'ACTIVE',?)""",
                (
                    transition.from_version, transition.to_version,
                    transition.kind, contract.fingerprint,
                    contract.contract_json, timestamp,
                ),
            )
        state = connection.execute(
            """SELECT * FROM news_annotation_transition_state_v1
               WHERE from_prompt_version=? AND to_prompt_version=?""",
            (transition.from_version, transition.to_version),
        ).fetchone()
    persisted_fingerprint = str(state["transition_fingerprint"] or "")
    persisted_contract_json = str(state["transition_contract_json"] or "")
    if not persisted_fingerprint or not persisted_contract_json:
        projection_count = int(connection.execute(
            """SELECT count(*)
               FROM news_annotation_transition_projections_v1
               WHERE from_prompt_version=? AND to_prompt_version=?""",
            (transition.from_version, transition.to_version),
        ).fetchone()[0])
        safe_to_initialize = (
            str(state["transition_kind"]) == transition.kind
            and int(state["processed_count"]) == 0
            and state["cursor_parsed_at"] is None
            and state["cursor_annotation_id"] is None
            and projection_count == 0
        )
        if safe_to_initialize:
            with connection:
                connection.execute(
                    """UPDATE news_annotation_transition_state_v1
                       SET transition_fingerprint=?,transition_contract_json=?,
                           updated_at=?
                       WHERE from_prompt_version=? AND to_prompt_version=?""",
                    (
                        contract.fingerprint, contract.contract_json, timestamp,
                        transition.from_version, transition.to_version,
                    ),
                )
            state = connection.execute(
                """SELECT * FROM news_annotation_transition_state_v1
                   WHERE from_prompt_version=? AND to_prompt_version=?""",
                (transition.from_version, transition.to_version),
            ).fetchone()
            persisted_fingerprint = contract.fingerprint
            persisted_contract_json = contract.contract_json
    contract_changed = (
        str(state["transition_kind"]) != transition.kind
        or persisted_fingerprint != contract.fingerprint
        or persisted_contract_json != contract.contract_json
    )
    if contract_changed:
        detail = (
            "persisted semantic transition progress belongs to a different "
            "declared contract"
        )
        with connection:
            connection.execute(
                """INSERT INTO news_annotation_transition_contract_failures_v1
                   (from_prompt_version,to_prompt_version,persisted_fingerprint,
                    current_fingerprint,failure_code,failure_detail,failed_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(from_prompt_version,to_prompt_version) DO UPDATE SET
                     persisted_fingerprint=excluded.persisted_fingerprint,
                     current_fingerprint=excluded.current_fingerprint,
                     failure_code=excluded.failure_code,
                     failure_detail=excluded.failure_detail,
                     failed_at=excluded.failed_at""",
                (
                    transition.from_version, transition.to_version,
                    persisted_fingerprint or None, contract.fingerprint,
                    SemanticTransitionContractChanged.code, detail, timestamp,
                ),
            )
        raise SemanticTransitionContractChanged(
            f"{SemanticTransitionContractChanged.code}: {detail}"
        )
    if str(state["state"]) == "COMPLETE":
        return {
            "kind": transition.kind, "processed": 0, "complete": True,
            "transition_fingerprint": contract.fingerprint,
        }
    if transition.kind == MODEL_REVIEW_REQUIRED:
        return {
            "kind": transition.kind, "processed": 0, "complete": False,
            "transition_fingerprint": contract.fingerprint,
        }
    cursor_clause = ""
    if state["cursor_parsed_at"] is not None:
        cursor_clause = "AND (a.parsed_at,a.annotation_id)>(?,?)"
    rows = connection.execute(
        f"""SELECT a.*,n.source_published_time,n.collector_first_seen_time,
                   n.headline,n.body,n.cluster_id
            FROM news_annotations a
            JOIN news_revisions n ON n.source=a.source
             AND n.source_item_id=a.source_item_id
             AND n.revision_number=a.revision_number
            WHERE a.prompt_version=?
              AND a.llm_model_version IN (?,?)
              AND {source_usable}
              AND {semantic_eligibility_sql_predicate('n')}
              AND length(trim(COALESCE(n.body,'')))>=
                  {ANNOTATION_BODY_MIN_CHARACTERS}
              AND n.collector_first_seen_time<?
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions newer_revision
                WHERE newer_revision.source=n.source
                  AND newer_revision.source_item_id=n.source_item_id
                  AND newer_revision.revision_number>n.revision_number)
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions peer
                WHERE peer.cluster_id=n.cluster_id
                  AND length(trim(COALESCE(peer.body,'')))>=
                      {ANNOTATION_BODY_MIN_CHARACTERS}
                  AND NOT EXISTS (
                    SELECT 1 FROM news_revisions peer_newer
                    WHERE peer_newer.source=peer.source
                      AND peer_newer.source_item_id=peer.source_item_id
                      AND peer_newer.revision_number>peer.revision_number)
                  AND {semantic_eligibility_sql_predicate('peer')}
                  AND {preferred_cluster_peer_predicate('peer', 'n')})
              AND NOT EXISTS (
                SELECT 1 FROM news_annotations current
                WHERE current.source=a.source
                  AND current.source_item_id=a.source_item_id
                  AND current.revision_number=a.revision_number
                  AND current.prompt_version=?
                  AND current.llm_model_version IN (?,?)
                  AND {current_usable})
              AND NOT EXISTS (
                SELECT 1 FROM news_annotations newer
                WHERE newer.source=a.source
                  AND newer.source_item_id=a.source_item_id
                  AND newer.revision_number=a.revision_number
                  AND newer.prompt_version=a.prompt_version
                  AND newer.llm_model_version IN (?,?)
                  AND {newer_usable}
                  AND (newer.parsed_at,newer.annotation_id)>
                      (a.parsed_at,a.annotation_id))
              AND NOT EXISTS (
                SELECT 1 FROM news_annotation_transition_failures_v1 f
                WHERE f.source_annotation_id=a.annotation_id
                  AND f.to_prompt_version=?)
              {cursor_clause}
            ORDER BY a.parsed_at,a.annotation_id LIMIT ?""",
        (
            transition.from_version,
            *SUPPORTED_GEMINI_MODELS,
            forward_epoch.isoformat(),
            activated_at.isoformat(timespec="microseconds"),
            forward_epoch.isoformat(),
            transition.to_version, *SUPPORTED_GEMINI_MODELS,
            *SUPPORTED_GEMINI_MODELS, transition.to_version,
            *((state["cursor_parsed_at"], state["cursor_annotation_id"])
              if state["cursor_parsed_at"] is not None else ()),
            bounded_page,
        ),
    ).fetchall()
    from .critical_annotation_state import (
        record_annotation_completion, refresh_news_revision_state,
    )
    from .news_semantics import (
        canonical_annotation_source_text, validate_news_annotation,
    )
    applied = 0
    failed = 0
    actionable_age, _ = category_time_rule(None)
    with connection:
        for row in rows:
            if not assess_news_time(
                row,
                decision_time=activated_at,
                forward_epoch=forward_epoch,
                max_actionable_age=actionable_age,
                max_discovery_delay=None,
            ).eligible:
                continue
            try:
                source_json = str(row["annotation_json"])
                projected = json.loads(source_json)
                source_text = canonical_annotation_source_text(
                    str(row["headline"]), str(row["body"] or ""),
                )
                if migrator is not None:
                    migrator(projected, source_text)
                validate_news_annotation(
                    projected, prompt_version=transition.to_version,
                    source_text=source_text,
                )
                projected_json = json.dumps(
                    projected, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                )
                target_id = hashlib.sha256(
                    ("transition-projection\x1f" + str(row["annotation_id"])
                     + "\x1f" + transition.to_version).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """INSERT OR IGNORE INTO news_annotations VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        target_id, row["source"], row["source_item_id"],
                        row["revision_number"], row["raw_content_hash"],
                        projected["event_type"],
                        json.dumps(projected["entities"], separators=(",", ":")),
                        projected["hawkishness"], projected["inflation_impulse"],
                        projected["growth_impulse"], projected["geopolitical_risk"],
                        projected["usd_impulse"], projected["novelty"],
                        projected["confidence"], row["llm_model_version"],
                        transition.to_version, row["parse_started_at"],
                        row["parsed_at"], projected_json,
                    ),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO
                       news_annotation_transition_projections_v1
                       (target_annotation_id,source_annotation_id,
                        from_prompt_version,to_prompt_version,transition_kind,
                        migrator_version,transition_fingerprint,
                        source_annotation_hash,projected_annotation_hash,applied_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        target_id, row["annotation_id"], transition.from_version,
                        transition.to_version, transition.kind,
                        transition.migrator_version,
                        contract.fingerprint,
                        hashlib.sha256(source_json.encode("utf-8")).hexdigest(),
                        hashlib.sha256(projected_json.encode("utf-8")).hexdigest(),
                        timestamp,
                    ),
                )
                record_annotation_completion(
                    connection, source=str(row["source"]),
                    source_item_id=str(row["source_item_id"]),
                    revision_number=int(row["revision_number"]),
                    prompt_version=transition.to_version,
                    completed_at=timestamp,
                )
                refresh_news_revision_state(
                    connection, str(row["source"]), str(row["source_item_id"]),
                    int(row["revision_number"]),
                )
                applied += 1
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                connection.execute(
                    """INSERT OR IGNORE INTO news_annotation_transition_failures_v1
                       VALUES (?,?,?,?,?,?)""",
                    (
                        row["annotation_id"], transition.to_version,
                        transition.kind, "SEMANTIC_TRANSITION_CONTRACT_FAILED",
                        str(error)[:300], timestamp,
                    ),
                )
                failed += 1
        if rows:
            last = rows[-1]
            connection.execute(
                """UPDATE news_annotation_transition_state_v1
                   SET cursor_parsed_at=?,cursor_annotation_id=?,
                       processed_count=processed_count+?,updated_at=?
                   WHERE from_prompt_version=? AND to_prompt_version=?""",
                (
                    last["parsed_at"], last["annotation_id"], len(rows), timestamp,
                    transition.from_version, transition.to_version,
                ),
            )
        complete = len(rows) < bounded_page
        if complete:
            connection.execute(
                """UPDATE news_annotation_transition_state_v1
                   SET state='COMPLETE',updated_at=?
                   WHERE from_prompt_version=? AND to_prompt_version=?""",
                (timestamp, transition.from_version, transition.to_version),
            )
    return {
        "kind": transition.kind, "processed": len(rows), "applied": applied,
        "failed": failed, "complete": complete,
        "transition_fingerprint": contract.fingerprint,
    }


def demand_allows_scheduling(
    demand_class: str, *, training_generation_requested: bool = False,
) -> bool:
    if demand_class not in DEMAND_CLASSES:
        raise ValueError("semantic demand class is not controlled")
    if demand_class == CURRENT_OPERATIONAL:
        return True
    if demand_class == TRAINING_REQUIRED:
        return training_generation_requested
    return False
