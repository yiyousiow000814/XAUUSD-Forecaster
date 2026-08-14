"""Persisted semantic identity resolution for real-world news events."""

from __future__ import annotations

import hashlib


def _stable_id(kind: str, annotation_id: str) -> str:
    digest = hashlib.sha256(f"{kind}|{annotation_id}".encode()).hexdigest()[:24]
    return f"semantic-{kind}-{digest}"


def resolve_event_identity(row: dict, assessment: dict, *, connection=None) -> dict:
    """Turn Gemma's bounded candidate choice into stable system-owned IDs."""
    relation = str(assessment["identity_relation"])
    matched_id = str(assessment.get("matched_candidate_id") or "")
    candidates = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in row.get("prior_event_context") or ()
    }
    candidate = candidates.get(matched_id)
    if relation in {"SAME_EVENT", "SAME_EPISODE"} and candidate is None:
        raise ValueError("identity resolution references an unavailable candidate")

    if candidate is not None and connection is not None:
        persisted = connection.execute(
            """SELECT canonical_episode_id,canonical_event_id
               FROM news_event_identity_resolutions_v1
               WHERE annotation_id=? ORDER BY resolved_at DESC LIMIT 1""",
            (matched_id,),
        ).fetchone()
        if persisted is not None:
            candidate = {
                **candidate,
                "canonical_episode_id": persisted["canonical_episode_id"],
                "canonical_event_id": persisted["canonical_event_id"],
            }

    annotation_id = str(row["annotation_id"])
    if candidate is None:
        episode_id = _stable_id("episode", annotation_id)
        event_id = _stable_id("event", annotation_id)
    else:
        episode_id = str(candidate.get("canonical_episode_id") or "").strip()
        if not episode_id:
            episode_id = _stable_id("episode", matched_id)
        if relation == "SAME_EVENT":
            event_id = str(candidate.get("canonical_event_id") or "").strip()
            if not event_id:
                event_id = _stable_id("event", matched_id)
        else:
            event_id = _stable_id("event", annotation_id)

    return {
        "identity_relation": relation,
        "matched_annotation_id": matched_id or None,
        "canonical_episode_id": episode_id,
        "canonical_event_id": event_id,
    }
