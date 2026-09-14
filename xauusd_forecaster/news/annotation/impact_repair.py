"""Immutable pending repair evidence; existing jobs own retry and completion."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

REPAIR_CONTRACT = "contract-repair-v2"
MAX_CHECKPOINT_BYTES = 262144
CONTEXT_FIELDS = ("annotation", "prior_event_context", "identity_context_truncated",
                  "source_context_mode", "source_body_character_count",
                  "identity_retrieval_mode", "identity_retrieval_reason")


def checkpoint_key(row, prompt_version, model):
    identity = [row["annotation_id"], row["content_hash"], prompt_version, model, REPAIR_CONTRACT]
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
