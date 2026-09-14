"""Sparse News transport; complete source membership remains authoritative."""
from __future__ import annotations

import json
import re

from xauusd_forecaster.news_projection import receipt_payload_hash

DELTA_CONTRACT = "news-projection-delta-v1"


def make_news_delta(inventory: dict, baseline: dict, get_batch) -> dict | None:
    """Return a bounded complete patch, or use full replay for large changes."""
    entries, old = inventory["entries"], baseline["inventory"]["entries"]
    changed_index = sorted(k for k, v in entries.items()
                           if k not in old or v["index_hash"] != old[k]["index_hash"])
    changed_detail = sorted(k for k, v in entries.items()
                            if k not in old or v["detail_hash"] != old[k]["detail_hash"])
    # Detail identity is immutable. Include its index in the patch as well so
    # the receiver can prove complete membership of each supplied detail.
    changed_index = sorted(set(changed_index) | set(changed_detail))
    removed = sorted(set(old) - set(entries))
    if len(changed_index) > 32 or len(changed_detail) > 8 or len(removed) > 32:
        return None

    def items(kind: str, keys: list[str]) -> list[dict]:
        selected = {}
        for offset in sorted({entries[k][kind + "_offset"] for k in keys}):
            for row in get_batch(kind, offset):
                key = row["detail_key"]
                if key in keys:
                    actual = receipt_payload_hash(row) if kind == "index" else row["detail_hash"]
                    if actual != entries[key][kind + "_hash"]:
                        raise ValueError("news delta batch contradicts source inventory")
                    selected[key] = row
        if set(selected) != set(keys):
            raise ValueError("news delta batch is incomplete")
        return [selected[k] for k in keys]

    applied = baseline["applied_manifest"]
    patch = {
        "base": {"generation_id": applied["generation_id"],
                 "snapshot_id": applied["snapshot_id"],
                 "receipt_digest": applied["expected_receipt_digest"]},
        "source": inventory["manifest"], "indexes": items("index", changed_index),
        "details": items("detail", changed_detail), "removed": removed,
    }
    digest = receipt_payload_hash(patch)
    request = {"action": "apply_delta", "generation_id": digest, "patch": patch}
    if len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()) > 120_000:
        return None
    return request


def applied_delta_manifest(request: dict) -> dict:
    return {**request["patch"]["source"], "contract_version": DELTA_CONTRACT,
            "generation_id": request["generation_id"],
            "expected_receipt_digest": request["generation_id"]}


def news_delta_baseline(inventory: dict, applied: dict) -> dict:
    value = {"inventory": inventory, "applied_manifest": applied}
    return {**value, "digest": receipt_payload_hash(value)}


def valid_news_delta_baseline(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"inventory", "applied_manifest", "digest"}:
        return False
    try:
        inventory, applied = value["inventory"], value["applied_manifest"]
        entries, source = inventory["entries"], inventory["manifest"]
        if not isinstance(entries, dict) or len(entries) != source["expected_index_count"] or len(entries) > 10_000:
            return False
        for key, entry in entries.items():
            if (not re.fullmatch(r"[a-f0-9]{64}", key)
                    or set(entry) != {"index_hash", "detail_hash", "index_offset", "detail_offset"}
                    or any(not re.fullmatch(r"[a-f0-9]{64}", entry[field]) for field in ("index_hash", "detail_hash"))
                    or any(type(entry[field]) is not int or not 0 <= entry[field] < len(entries)
                           for field in ("index_offset", "detail_offset"))):
                return False
        for field in ("snapshot_id", "source_digest", "expected_index_count", "expected_detail_count", "watermark", "window_start"):
            if source[field] != applied[field]:
                return False
        return value["digest"] == receipt_payload_hash({k: value[k] for k in ("inventory", "applied_manifest")})
    except (KeyError, TypeError, ValueError):
        return False
