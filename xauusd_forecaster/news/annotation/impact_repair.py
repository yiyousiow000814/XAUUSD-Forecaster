"""Immutable pending repair evidence; existing jobs own retry and completion."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

REPAIR_CONTRACT = "contract-repair-v3"
# Repair prompting can evolve without invalidating the frozen initial evidence.
CHECKPOINT_CONTRACT = "contract-repair-v2"
MAX_CHECKPOINT_BYTES = 262144
CONTEXT_FIELDS = ("annotation", "prior_event_context", "identity_context_truncated",
                  "source_context_mode", "source_body_character_count",
                  "identity_retrieval_mode", "identity_retrieval_reason")


def checkpoint_key(row, prompt_version, model):
    identity = [row["annotation_id"], row["content_hash"], prompt_version, model, CHECKPOINT_CONTRACT]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def load_checkpoint(connection, key):
    found = connection.execute(
        "SELECT payload_json,payload_hash FROM news_impact_repair_checkpoints_v1 WHERE checkpoint_key=?", (key,)
    ).fetchone()
    if found is None:
        return None
    encoded = found[0].encode("utf-8")
    if len(encoded) > MAX_CHECKPOINT_BYTES or hashlib.sha256(key.encode("utf-8") + encoded).hexdigest() != found[1]:
        raise ValueError("Invalid impact repair checkpoint integrity")
    return json.loads(found[0])


def save_checkpoint(connection, key, payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CHECKPOINT_BYTES:
        raise ValueError("Impact repair checkpoint exceeds bounded context")
    with connection:
        connection.execute(
            "INSERT OR IGNORE INTO news_impact_repair_checkpoints_v1 VALUES (?,?,?,?)",
            (key, encoded.decode("utf-8"), hashlib.sha256(key.encode("utf-8") + encoded).hexdigest(), datetime.now(timezone.utc).isoformat()),
        )


def load_repair_feedback(connection, row, prompt_version, key):
    """Read one prior rejection, never a success authority or replacement output."""
    job = connection.execute(
        """SELECT job_id FROM news_ai_jobs_v1 WHERE task_type='ACTIVE_IMPACT'
        AND annotation_id=? AND source=? AND source_item_id=?
        AND revision_number=? AND prompt_version=?""",
        (row["annotation_id"], row["source"], row["source_item_id"],
         row["revision_number"], prompt_version),
    ).fetchone()
    if job is None:
        return None
    found = connection.execute(
        """SELECT error_detail FROM news_ai_job_attempts_v1
        WHERE job_id=? AND outcome='ERROR'
          AND failure_code='MODEL_OUTPUT_CONTRACT_FAILED'
        ORDER BY attempt_number DESC,attempted_at DESC LIMIT 1""", (job[0],),
    ).fetchone()
    if found is None or not found[0] or len(found[0]) > 8192:
        return None
    try:
        evidence = json.loads(found[0])
    except (ValueError, TypeError):
        return None
    if not isinstance(evidence, dict) or evidence.get("failure_stage") != "IMPACT_CONTRACT_REPAIR":
        return None
    if evidence.get("checkpoint_key", key) != key:
        return None
    cause = evidence.get("cause")
    selected = evidence.get("selected_output", {})
    if not isinstance(cause, str) or not cause or not isinstance(selected, dict):
        return None
    return {"cause": cause[:500], "selected_output": selected,
            "response_hash": evidence.get("response_hash"), "partial_evidence": True}
