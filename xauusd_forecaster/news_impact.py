"""Versioned semantic impact-lifetime contract for action-bearing news."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import re

from .news_relevance import google_news_item_is_relevant
from .news_semantics import ACTIONABLE_RECORD_KINDS, CURRENT_NEWS_PROMPT_VERSION


IMPACT_MODEL = "gemma-4-31b-it"
IMPACT_PROMPT_VERSION = "news-impact-v4-event-identity-reconciliation"
HANDOVER_IMPACT_PROMPT_VERSION = "news-impact-v3-independent-semantic-review"

IMPACT_TIME_RULES = {
    "IMMEDIATE": (timedelta(hours=2), 30.0),
    "SAME_DAY": (timedelta(hours=12), 120.0),
    "DATA_RELEASE": (timedelta(hours=24), 360.0),
    "POLICY_SHIFT": (timedelta(hours=72), 1440.0),
    "ONGOING_EVENT": (timedelta(days=7), 4320.0),
    "BACKGROUND": (timedelta(0), 1.0),
}
IMPACT_CLASSES = frozenset(IMPACT_TIME_RULES)
EVENT_STATES = frozenset({"ACTIVE", "COMPLETED", "UNCERTAIN", "BACKGROUND"})
UPDATE_TYPES = frozenset({
    "NEW_EVENT", "MATERIAL_UPDATE", "DUPLICATE_REPORT", "COMMENTARY",
    "HISTORICAL_CONTEXT",
})
IDENTITY_RELATIONS = frozenset({
    "SAME_EVENT", "SAME_EPISODE", "NEW_EPISODE", "UNRESOLVED",
})
IDENTITY_CANDIDATE_SCAN_LIMIT = 500
IDENTITY_CANDIDATE_UNIVERSE_LIMIT = 10_000

IMPACT_RESPONSE_SCHEMA = {
    "type": "object",
    "required": [
        "impact_class", "event_state", "update_type", "identity_relation",
        "matched_candidate_id", "confidence", "reason_zh",
    ],
    "properties": {
        "impact_class": {"type": "string", "enum": sorted(IMPACT_CLASSES)},
        "event_state": {"type": "string", "enum": sorted(EVENT_STATES)},
        "update_type": {"type": "string", "enum": sorted(UPDATE_TYPES)},
        "identity_relation": {"type": "string", "enum": sorted(IDENTITY_RELATIONS)},
        "matched_candidate_id": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_zh": {"type": "string"},
    },
}


def impact_time_rule(impact_class: str | None) -> tuple[timedelta, float]:
    """Map a frozen Gemma class to deterministic expiry and decay."""
    return IMPACT_TIME_RULES.get(str(impact_class or "BACKGROUND"), IMPACT_TIME_RULES["BACKGROUND"])


def impact_is_actionable(assessment: dict | None) -> bool:
    """Keep semantic judgment bounded; system policy owns final admission."""
    if not assessment:
        return False
    return (
        assessment.get("impact_class") in IMPACT_CLASSES - {"BACKGROUND"}
        and assessment.get("event_state") != "BACKGROUND"
        and assessment.get("update_type") in {"NEW_EVENT", "MATERIAL_UPDATE"}
    )


def validate_impact_assessment(
    result: dict, *, candidate_ids: set[str] | None = None,
    same_event_candidate_ids: set[str] | None = None,
) -> dict:
    """Validate the frozen classifier contract before append-only persistence."""
    expected = set(IMPACT_RESPONSE_SCHEMA["required"])
    if set(result) != expected:
        raise ValueError("Gemma impact response fields do not match frozen schema")
    if result["impact_class"] not in IMPACT_CLASSES:
        raise ValueError("Gemma impact class is not controlled")
    if result["event_state"] not in EVENT_STATES:
        raise ValueError("Gemma event state is not controlled")
    if result["update_type"] not in UPDATE_TYPES:
        raise ValueError("Gemma update type is not controlled")
    relation = str(result["identity_relation"])
    if relation not in IDENTITY_RELATIONS:
        raise ValueError("Gemma identity relation is not controlled")
    matched = str(result["matched_candidate_id"] or "").strip()
    if relation in {"SAME_EVENT", "SAME_EPISODE"}:
        if not matched or (candidate_ids is not None and matched not in candidate_ids):
            raise ValueError("Gemma identity match is not an offered candidate")
    elif matched:
        raise ValueError("Gemma identity match must be empty without a prior relation")
    if result["update_type"] == "DUPLICATE_REPORT" and relation != "SAME_EVENT":
        raise ValueError("A duplicate report must resolve to the same event")
    if (
        relation == "SAME_EVENT" and same_event_candidate_ids is not None
        and matched not in same_event_candidate_ids
    ):
        raise ValueError("Gemma same-event match is not a core fact candidate")
    if result["update_type"] == "MATERIAL_UPDATE" and relation != "SAME_EPISODE":
        raise ValueError("A material update must resolve inside the same episode")
    if result["update_type"] == "NEW_EVENT" and relation != "NEW_EPISODE":
        raise ValueError("A new event must start a new episode")
    confidence = float(result["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("Gemma impact confidence is outside [0, 1]")
    reason = str(result["reason_zh"] or "").strip()
    if len(reason) < 4:
        raise ValueError("Gemma impact reason is empty")
    return {
        "impact_class": str(result["impact_class"]),
        "event_state": str(result["event_state"]),
        "update_type": str(result["update_type"]),
        "identity_relation": relation,
        "matched_candidate_id": matched,
        "confidence": confidence,
        "reason_zh": reason,
    }


def _identity_tokens(annotation: dict) -> set[str]:
    values = [
        annotation.get("material_event_key"), annotation.get("episode_key"),
        annotation.get("canonical_actor_id"), annotation.get("canonical_object_id"),
        annotation.get("actor"), annotation.get("object"),
        *(annotation.get("entities") or []),
    ]
    return {
        token for value in values
        for token in re.findall(r"[a-z0-9\u4e00-\u9fff]+", str(value or "").casefold())
        if len(token) >= 3
    }


def _prior_similarity(current: dict, prior: dict) -> float:
    current_tokens = _identity_tokens(current)
    prior_tokens = _identity_tokens(prior)
    if not current_tokens or not prior_tokens:
        return 0.0
    overlap = len(current_tokens & prior_tokens) / len(current_tokens | prior_tokens)
    current_object = str(current.get("canonical_object_id") or "").casefold()
    prior_object = str(prior.get("canonical_object_id") or "").casefold()
    object_match = bool(
        current_object and prior_object
        and (current_object == prior_object
             or current_object in prior_object or prior_object in current_object)
    )
    return max(overlap, 0.75 if object_match else 0.0)


def pending_impact_records(
    connection,
    *,
    observed_at: datetime | None = None,
    limit: int = 500,
    annotation_prompt_version: str = CURRENT_NEWS_PROMPT_VERSION,
    impact_prompt_version: str = IMPACT_PROMPT_VERSION,
    selection_order: str = "oldest",
) -> list[dict]:
    """Return current annotations that still need the frozen Gemma assessment."""
    if selection_order not in {"oldest", "newest"}:
        raise ValueError("impact selection order is not controlled")
    now = observed_at or datetime.now(UTC)
    official_priority = """CASE WHEN n.source IN (
                   'federal_reserve_monetary','bls_employment_situation',
                   'bls_consumer_price_index','bls_job_openings',
                   'us_treasury_press_releases') THEN 0 ELSE 1 END"""
    event_time = "COALESCE(n.source_published_time,n.collector_first_seen_time)"
    pending_order = (
        f"{official_priority}, {event_time} ASC"
        if selection_order == "oldest"
        else f"{event_time} DESC, {official_priority}"
    )
    rows = connection.execute(
        f"""SELECT n.*,a.annotation_id,a.annotation_json,a.parsed_at
        FROM news_revisions n JOIN news_annotations a
          ON a.source=n.source AND a.source_item_id=n.source_item_id
         AND a.revision_number=n.revision_number
         AND a.raw_content_hash=n.content_hash
        WHERE length(trim(COALESCE(n.body,'')))>=240
          AND a.prompt_version=?
          AND a.llm_model_version IN (
            'gemini-3.5-flash-lite','gemini-3.1-flash-lite')
          AND NOT EXISTS (
            SELECT 1 FROM news_impact_assessments_v1 i
            WHERE i.annotation_id=a.annotation_id
              AND i.llm_model_version=? AND i.prompt_version=?)
          AND NOT EXISTS (
            SELECT 1 FROM news_revisions newer
            WHERE newer.source=n.source AND newer.source_item_id=n.source_item_id
              AND newer.revision_number>n.revision_number)
          AND NOT EXISTS (
            SELECT 1 FROM news_impact_failures_v1 f
            WHERE f.annotation_id=a.annotation_id
              AND f.llm_model_version=? AND f.prompt_version=?
              AND f.attempt_number=(
                SELECT max(f2.attempt_number) FROM news_impact_failures_v1 f2
                WHERE f2.annotation_id=f.annotation_id
                  AND f2.llm_model_version=f.llm_model_version
                  AND f2.prompt_version=f.prompt_version)
              AND (
                (f.is_terminal=1 AND f.attempt_number>=5
                 AND NOT (f.error_type='HTTPError' AND f.error LIKE '%429%'))
                OR (f.next_retry_at IS NOT NULL AND f.next_retry_at>?)))
        ORDER BY {pending_order}
        LIMIT ?""",
        (
            annotation_prompt_version,
            IMPACT_MODEL, impact_prompt_version,
            IMPACT_MODEL, impact_prompt_version,
            now.isoformat(timespec="microseconds"), max(1, limit * 4),
        ),
    ).fetchall()
    selected = []
    for raw in rows:
        row = dict(raw)
        published = (
            datetime.fromisoformat(str(row["source_published_time"]))
            if row.get("source_published_time") else None
        )
        first_seen = datetime.fromisoformat(str(row["collector_first_seen_time"]))
        allowed, _ = google_news_item_is_relevant(
            str(row["source"]), str(row.get("headline") or ""),
            published, first_seen,
        )
        if allowed:
            row["annotation"] = json.loads(row.get("annotation_json") or "{}")
            row["prior_event_context"] = []
            selected.append(row)
        if len(selected) >= limit:
            break

    if not selected:
        return []

    # Load one bounded point-in-time candidate universe for the whole batch.
    # Per-row candidate queries make backlog recovery O(pending rows) in SQL
    # round trips and can block new arrivals for an entire scheduler cycle.
    candidate_universe_limit = min(
        IDENTITY_CANDIDATE_UNIVERSE_LIMIT,
        max(IDENTITY_CANDIDATE_SCAN_LIMIT, len(selected) * 4 + 500),
    )
    max_first_seen = max(
        str(row["collector_first_seen_time"]) for row in selected
    )
    prior_rows = connection.execute(
        """SELECT p.source,p.source_item_id,p.revision_number,p.headline,
                  p.collector_first_seen_time,
                  pa.annotation_id AS candidate_id,pa.annotation_json,
                  json_extract(pa.annotation_json,'$.summary_zh') AS summary_zh,
                  pi.impact_class,pi.update_type,
                  er.canonical_episode_id,er.canonical_event_id
           FROM news_revisions p JOIN news_annotations pa
             ON pa.source=p.source
            AND pa.source_item_id=p.source_item_id
            AND pa.revision_number=p.revision_number
            AND pa.raw_content_hash=p.content_hash
           LEFT JOIN news_impact_assessments_v1 pi
             ON pi.assessment_id=(
               SELECT selected_pi.assessment_id
               FROM news_impact_assessments_v1 selected_pi
               WHERE selected_pi.annotation_id=pa.annotation_id
                 AND selected_pi.llm_model_version=?
                 AND selected_pi.prompt_version IN (?,?)
               ORDER BY CASE selected_pi.prompt_version
                 WHEN ? THEN 0 ELSE 1 END,
                 selected_pi.assessed_at DESC LIMIT 1)
           LEFT JOIN news_event_identity_resolutions_v1 er
             ON er.annotation_id=pa.annotation_id
            AND er.llm_model_version=? AND er.prompt_version=?
           WHERE pa.prompt_version=?
             AND p.collector_first_seen_time<=?
           ORDER BY p.collector_first_seen_time DESC LIMIT ?""",
        (
            IMPACT_MODEL, impact_prompt_version,
            HANDOVER_IMPACT_PROMPT_VERSION, impact_prompt_version,
            IMPACT_MODEL, impact_prompt_version,
            annotation_prompt_version, max_first_seen, candidate_universe_limit,
        ),
    ).fetchall()
    candidate_universe = []
    for prior in prior_rows:
        candidate = dict(prior)
        candidate["annotation"] = json.loads(
            candidate.pop("annotation_json") or "{}"
        )
        candidate_universe.append(candidate)

    for row in selected:
        scanned = 0
        candidates = []
        for prior in candidate_universe:
            if (
                str(prior["collector_first_seen_time"])
                > str(row["collector_first_seen_time"])
            ):
                continue
            if (
                prior["source"] == row["source"]
                and prior["source_item_id"] == row["source_item_id"]
                and prior["revision_number"] == row["revision_number"]
            ):
                continue
            scanned += 1
            if scanned > IDENTITY_CANDIDATE_SCAN_LIMIT:
                break
            candidate = {
                key: value for key, value in prior.items()
                if key != "annotation"
            }
            prior_annotation = prior["annotation"]
            similarity = _prior_similarity(row["annotation"], prior_annotation)
            if similarity < 0.25:
                continue
            candidate["similarity"] = round(similarity, 3)
            candidate["material_event_key"] = prior_annotation.get(
                "material_event_key"
            )
            candidate["canonical_actor_id"] = prior_annotation.get(
                "canonical_actor_id"
            )
            candidate["canonical_object_id"] = prior_annotation.get(
                "canonical_object_id"
            )
            candidate["identity_anchor_eligible"] = bool(
                str(prior_annotation.get("record_kind") or "")
                in ACTIONABLE_RECORD_KINDS
                and str(prior_annotation.get("evidence_role") or "")
                in {"CORE_CLAIM", "EVIDENCE_DOCUMENT"}
                and candidate.get("update_type")
                not in {"COMMENTARY", "HISTORICAL_CONTEXT"}
            )
            candidates.append(candidate)
        current_is_core_fact = bool(
            str(row["annotation"].get("record_kind") or "")
            in ACTIONABLE_RECORD_KINDS
            and str(row["annotation"].get("evidence_role") or "")
            in {"CORE_CLAIM", "EVIDENCE_DOCUMENT"}
        )
        eligible_candidates = (
            [candidate for candidate in candidates
             if candidate["identity_anchor_eligible"]]
            if current_is_core_fact else candidates
        )
        row["prior_event_context"] = sorted(
            eligible_candidates,
            key=lambda candidate: (
                bool(candidate["identity_anchor_eligible"]),
                float(candidate["similarity"]),
                str(candidate["collector_first_seen_time"]),
            ),
            reverse=True,
        )[:5]
    return selected
