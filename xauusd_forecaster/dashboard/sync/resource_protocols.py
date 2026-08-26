"""Dashboard Sync resource protocols and cursor/checkpoint advancement."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from xauusd_forecaster.dashboard.resource_contracts import (
    AUDIT_DETAIL_LIMIT_BYTES,
    AUDIT_FIRST_PAGE_LIMIT_BYTES,
    LEARNING_HISTORY_BATCH_LIMIT_BYTES,
    LEARNING_HISTORY_CONTRACT_VERSION,
    LEARNING_OVERVIEW_CURVE_POINTS,
    LEARNING_OVERVIEW_GROUPS_PER_IDENTITY,
    LEARNING_SUMMARY_CURVE_POINTS,
    LEARNING_SUMMARY_EXECUTION_RESULTS,
    LEARNING_SUMMARY_GROUPS_PER_IDENTITY,
    MARKET_CHART_SNAPSHOT_LIMIT_BYTES,
    MARKET_DECISION_FIELDS,
    MARKET_OVERVIEW_DECISIONS_PER_SERIES,
    NEWS_DETAIL_BATCH_ITEMS,
    NEWS_DETAIL_BATCH_LIMIT_BYTES,
    NEWS_INDEX_BATCH_LIMIT_BYTES,
    NEWS_INDEX_FIELDS,
    NEWS_MIRROR_CONTRACT_VERSION,
    NEWS_READER_WINDOW_DAYS,
    NEWS_WRITE_BATCH_ITEMS,
    REMOTE_DAILY_BRIEF_LIMIT,
    REMOTE_DECISION_LIMIT,
    REMOTE_MARKET_CANDLE_LIMIT,
    REMOTE_MARKET_DECISION_LIMIT,
    REMOTE_MARKET_DENSE_LIMITS,
    REMOTE_MARKET_OVERVIEW_LIMITS,
    REMOTE_NEWS_LIMIT,
    REMOTE_PAYLOAD_LIMIT_BYTES,
    PayloadContractError,
    _bounded_audit_snapshot,
    _bounded_item_batches,
    _decision_key,
    _downsample_market_overview,
    _encoded_snapshot,
    _epoch,
    _json_hash,
    _learning_overview_records,
    _learning_record,
    _learning_summary,
    _stable_news_key,
    _update_decision_overviews,
    _version_metric,
    _visual_curve_overview,
    _visual_decision_overview,
    _visual_version_overview,
    audit_briefs_snapshot,
    audit_decisions_snapshot,
    audit_snapshot,
    audit_stories_snapshot,
    compact_market_chart,
    learning_history_batches,
    learning_history_records,
    learning_snapshot,
    market_chart_snapshot,
    news_detail_batches,
    news_index_batches,
    news_mirror_parts,
    news_withdrawal_keys,
    remote_snapshot,
)

from xauusd_forecaster.dashboard.sync.progress import (
    DEFAULT_LEARNING_HISTORY_STATE,
    DEFAULT_LEARNING_STATE,
    DEFAULT_MARKET_HISTORY_STATE,
    DEFAULT_NEWS_EVIDENCE_STATE,
    DEFAULT_NEWS_STATE,
    OPERATOR_RETRY_COMMANDS_PER_CYCLE,
    _read_news_sync_state,
    _write_news_sync_state,
)
from xauusd_forecaster.dashboard.sync.transport import (
    LOCAL_STATUS_TIMEOUT_SECONDS,
    RemoteInvariantViolation,
    _assistant_worker_id,
    _get_json,
    _get_local_json,
    _local_retry_url,
    _operator_retry_worker_url,
    _post_json,
    _post_local_json,
)

MODULE_ROOT = Path(__file__).resolve().parents[3]


def _projection_producer_revision() -> str:
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(MODULE_ROOT), "rev-parse", "HEAD"],
            text=True, timeout=5,
        ).strip().lower()
    except (OSError, subprocess.SubprocessError):
        return ""
    return revision if re.fullmatch(r"[0-9a-f]{40}", revision) else ""

NEWS_EVIDENCE_WRITE_BATCH_ITEMS = 8
NEWS_EVIDENCE_BATCH_LIMIT_BYTES = 80_000
NEWS_EVIDENCE_PAGES_PER_CYCLE = 1
NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE = 8
MARKET_HISTORY_PAGES_PER_CYCLE = 1
MARKET_OVERVIEWS_PER_CYCLE = 2
NEWS_EVIDENCE_CONTRACT_VERSION = "news-evidence-paged-v2"
MARKET_HISTORY_CONTRACT_VERSION = "market-history-d1-v2"
MARKET_HISTORY_BATCH_LIMIT_BYTES = 350_000
MARKET_HISTORY_BATCH_ITEMS = 25
MARKET_HISTORY_OVERLAP_SECONDS = 2 * 3_600
LEARNING_HISTORY_FULL_REFRESH_SECONDS = 86_400
NEWS_PROJECTION_BATCHES_PER_CYCLE = 4

def _sync_operator_retries(_local_payload: dict, config: dict) -> None:
    local_jobs = _get_local_json(_local_retry_url(config, "/api/retry-jobs"))
    worker_url = _operator_retry_worker_url(config)
    _post_json(
        worker_url,
        json.dumps({"action": "SYNC_JOBS", "items": local_jobs.get("items", [])}).encode(),
        config,
    )
    worker_id = _assistant_worker_id()
    processed = False
    for _ in range(OPERATOR_RETRY_COMMANDS_PER_CYCLE):
        command = _get_json(
            f"{worker_url}?{urllib.parse.urlencode({'worker_id': worker_id})}", config,
        ).get("item")
        if not isinstance(command, dict):
            break
        local_result = _post_local_json(
            _local_retry_url(config, "/api/retry-overrides"),
            {
                "operator_id": command.get("operator_id"),
                "items": [{
                    "request_id": command.get("request_id"),
                    "job_id": command.get("job_id"),
                    "mode": command.get("mode"),
                    "reason": command.get("reason"),
                    "expected_state": command.get("expected_state"),
                    "expected_available_at": command.get("expected_available_at"),
                    "requested_available_at": command.get("requested_available_at"),
                }],
            },
        )
        result = (local_result.get("results") or [{}])[0]
        status = str(result.get("status") or "REJECTED")
        _post_json(
            worker_url,
            json.dumps({
                "action": "FINISH",
                "request_id": command.get("request_id"),
                "lease_token": command.get("lease_token"),
                "status": status,
                "result": result,
            }).encode(),
            config,
        )
        processed = True
    if processed:
        # A command result and the scheduler mirror advance in the same bounded
        # sync pass; the browser need not wait for an unrelated later cycle.
        refreshed_jobs = _get_local_json(_local_retry_url(config, "/api/retry-jobs"))
        _post_json(
            worker_url,
            json.dumps({
                "action": "SYNC_JOBS", "items": refreshed_jobs.get("items", []),
            }).encode(),
            config,
        )


def _sync_assistant_chat(_local_payload: dict, _config: dict):
    """Assistant is intentionally paused until an API model is configured."""
    return {"status": "PAUSED_NO_MODEL"}


def _sync_news_questions(_local_payload: dict, _config: dict) -> None:
    # Private Assistant Q&A, titles, compaction, and memory indexing are paused
    # together. News annotation, impact, and Daily Brief use separate workers.
    return None


def _learning_payload(local_payload: dict, config: dict) -> dict:
    if not local_payload and config.get("local_status_url"):
        return _read_local_resource(config, "/api/learning")
    return local_payload


def _sync_learning_history(local_payload: dict, config: dict) -> None:
    local_payload = _learning_payload(local_payload, config)
    remote_host = urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or ""
    if remote_host.lower().endswith(".chatgpt.site"):
        return
    history_url = config.get("remote_learning_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning-history"
    )
    history_state_path = Path(config.get(
        "learning_history_state_file", DEFAULT_LEARNING_HISTORY_STATE,
    ))
    history_state = _read_news_sync_state(history_state_path)
    hashes = history_state.get("hashes", {})
    if not isinstance(hashes, dict):
        hashes = {}
    now = datetime.now(UTC)
    last_full = history_state.get("last_full_sync")
    refresh_in_progress = bool(history_state.get("full_refresh_started_at"))
    try:
        full_refresh_due = (
            history_state.get("contract_version") != LEARNING_HISTORY_CONTRACT_VERSION
            or not last_full
            or (now - datetime.fromisoformat(str(last_full))).total_seconds()
            >= LEARNING_HISTORY_FULL_REFRESH_SECONDS
        )
    except (TypeError, ValueError):
        full_refresh_due = True
    if full_refresh_due and not refresh_in_progress:
        hashes = {}
        history_state["full_refresh_started_at"] = now.isoformat()

    records = learning_history_records(local_payload)
    pending = [
        row for row in records
        if hashes.get(f"{row['resource']}\0{row['record_key']}")
        != row["payload_hash"]
    ]
    batches = learning_history_batches(pending)
    if batches:
        batch = batches[0]
        encoded = json.dumps(
            {"records": batch}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _post_json(history_url, encoded, config)
        for row in batch:
            hashes[f"{row['resource']}\0{row['record_key']}"] = row["payload_hash"]
        _write_news_sync_state(history_state_path, {
            "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
            "hashes": hashes,
            "last_full_sync": last_full,
            "full_refresh_started_at": history_state.get("full_refresh_started_at"),
            "pending_record_count": len(pending) - len(batch),
            "last_progress": now.isoformat(),
        })
        return

    _write_news_sync_state(history_state_path, {
        "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
        "hashes": hashes,
        "last_full_sync": now.isoformat() if full_refresh_due else last_full,
        "last_success": now.isoformat(),
        "pending_record_count": 0,
    })


def _sync_learning_summary(local_payload: dict, config: dict) -> None:
    local_payload = _learning_payload(local_payload, config)
    learning_url = config.get("remote_learning_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning"
    )
    learning_state_path = Path(
        config.get("learning_state_file", DEFAULT_LEARNING_STATE)
    )
    learning_state = _read_news_sync_state(learning_state_path)
    learning_payload = learning_snapshot(local_payload)
    learning_hash = hashlib.sha256(learning_payload).hexdigest()
    if learning_state.get("payload_hash") != learning_hash:
        _post_json(learning_url, learning_payload, config)
        _write_news_sync_state(learning_state_path, {
            "payload_hash": learning_hash,
            "last_success": datetime.now(UTC).isoformat(),
        })


def _sync_learning(local_payload: dict, config: dict) -> None:
    """Compatibility helper; the scheduler owns these as separate resources."""
    payload = _learning_payload(local_payload, config)
    _sync_learning_history(payload, config)
    _sync_learning_summary(payload, config)


def _sync_market(local_payload: dict, config: dict) -> None:
    if not local_payload and config.get("local_status_url"):
        local_payload = _read_local_resource(config, "/api/market-chart")
    market_url = config.get("remote_market_chart_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-chart"
    )
    snapshot = market_chart_snapshot(local_payload)
    _post_json(market_url, snapshot, config)
    if (urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or "").lower().endswith(
        ".chatgpt.site"
    ):
        return
    market = json.loads(snapshot)
    overview_candles = market.get("overview_candles") or _downsample_market_overview(
        market.get("candles", []), REMOTE_MARKET_OVERVIEW_LIMITS[0],
    )
    history_url = config.get("remote_market_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-history"
    )
    overview = {
        "candles": overview_candles,
        "source_candle_count": int(market.get("source_candle_count") or len(overview_candles)),
        "history_start": market.get("history_start"),
        "history_end": market.get("history_end"),
    }
    _post_json(history_url, json.dumps(
        {"overview": overview}, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8"), config)


def _market_history_payloads(candles: list[dict], decisions: list[dict]) -> list[bytes]:
    """Keep D1 ingest requests bounded while preserving every row."""
    compacted = compact_market_chart({
        "market_chart": {
            "candles": candles, "overview_candles": [], "decisions": decisions,
        },
    }, dense_limit=max(1, len(decisions)), overview_limit=1)
    candles = compacted["candles"]
    decisions = compacted["decisions"]
    payloads = []
    for key, rows in (("candles", candles), ("decisions", decisions)):
        current: list[dict] = []
        for row in rows:
            candidate = [*current, row]
            encoded = json.dumps(
                {key: candidate}, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if current and (
                len(candidate) > MARKET_HISTORY_BATCH_ITEMS
                or len(encoded) > MARKET_HISTORY_BATCH_LIMIT_BYTES
            ):
                payloads.append(json.dumps(
                    {key: current}, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8"))
                current = [row]
            else:
                current = candidate
        if current:
            payloads.append(json.dumps(
                {key: current}, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8"))
    return payloads


def _market_decision_overview_payload(summary: dict) -> bytes:
    """Bound a replace-in-place overview without splitting its D1 row."""
    source = summary.get("decisions", [])
    decisions = [row for row in source if isinstance(row, dict)]
    limit = min(len(decisions), MARKET_OVERVIEW_DECISIONS_PER_SERIES)
    while True:
        bounded = {
            **summary,
            "decisions": _visual_decision_overview(decisions, limit),
        }
        bounded["decision_count"] = len(bounded["decisions"])
        bounded["decision_downsampled"] = (
            int(bounded.get("source_decision_count") or 0)
            > bounded["decision_count"]
        )
        encoded = json.dumps(
            {"decision_overviews": [bounded]}, ensure_ascii=False,
            allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) <= MARKET_HISTORY_BATCH_LIMIT_BYTES:
            return encoded
        if limit <= 1:
            raise PayloadContractError(
                "market decision overview row exceeds payload limit"
            )
        limit = max(1, limit // 2)


def _local_market_history_url(config: dict, after: str | None) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": "500"}
    if after:
        query["after"] = after
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/market-history",
        urllib.parse.urlencode(query), "",
    ))


def _overlap_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        value = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        return datetime.fromtimestamp(
            value.timestamp() - MARKET_HISTORY_OVERLAP_SECONDS, UTC,
        ).isoformat()
    except (TypeError, ValueError):
        return None


def _sync_market_history(config: dict) -> None:
    remote_host = urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or ""
    if remote_host.lower().endswith(".chatgpt.site"):
        return  # Sites remains on the bounded compatibility snapshot; D1 is Cloudflare-only.
    remote_url = config.get("remote_market_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-history"
    )
    state_path = Path(config.get(
        "market_history_state_file", DEFAULT_MARKET_HISTORY_STATE,
    ))
    state = _read_news_sync_state(state_path)
    cursor = (
        state.get("cursor")
        if state.get("contract_version") == MARKET_HISTORY_CONTRACT_VERSION
        else None
    )
    decision_overviews = (
        state.get("decision_overviews", {})
        if state.get("contract_version") == MARKET_HISTORY_CONTRACT_VERSION
        else {}
    )
    new_after = cursor
    after = _overlap_cursor(cursor)
    pages = 0
    while pages < MARKET_HISTORY_PAGES_PER_CYCLE:
        with urllib.request.urlopen(
            _local_market_history_url(config, after),
            timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
        ) as response:
            page = json.loads(response.read())
        candles = page.get("candles") if isinstance(page.get("candles"), list) else []
        decisions = page.get("decisions") if isinstance(page.get("decisions"), list) else []
        decision_overviews = _update_decision_overviews(
            decision_overviews, decisions, new_after,
        )
        for payload in _market_history_payloads(candles, decisions):
            _post_json(remote_url, payload, config)
        next_cursor = page.get("next_cursor")
        if next_cursor:
            cursor = str(next_cursor)
            _write_news_sync_state(state_path, {
                "contract_version": MARKET_HISTORY_CONTRACT_VERSION,
                "cursor": cursor,
                "decision_overviews": decision_overviews,
                "last_success": datetime.now(UTC).isoformat(),
            })
        pages += 1
        if not page.get("has_more") or not next_cursor or next_cursor == after:
            break
        after = str(next_cursor)
    summaries = sorted(decision_overviews.items())
    overview_offset = int(state.get("overview_offset") or 0)
    selected_summaries = []
    if summaries:
        for index in range(min(MARKET_OVERVIEWS_PER_CYCLE, len(summaries))):
            selected_summaries.append(
                summaries[(overview_offset + index) % len(summaries)][1]
            )
        overview_offset = (
            overview_offset + len(selected_summaries)
        ) % len(summaries)
    for summary in selected_summaries:
        _post_json(
            remote_url, _market_decision_overview_payload(summary), config,
        )
    _write_news_sync_state(state_path, {
        "contract_version": MARKET_HISTORY_CONTRACT_VERSION,
        "cursor": cursor,
        "decision_overviews": decision_overviews,
        "overview_offset": overview_offset,
        "has_more": bool(page.get("has_more")),
        "last_success": datetime.now(UTC).isoformat(),
    })


def _local_news_archive_url(
    config: dict, *, mode: str, snapshot_id: str | None = None,
    kind: str | None = None, offset: int | None = None,
    activated_snapshot_id: str | None = None,
) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"mode": mode}
    if snapshot_id:
        query["snapshot_id"] = snapshot_id
    if kind:
        query["kind"] = kind
    if offset is not None:
        query["offset"] = str(offset)
    if activated_snapshot_id:
        query["activated_snapshot_id"] = activated_snapshot_id
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-archive",
        urllib.parse.urlencode(query), "",
    ))


def _verify_news_projection_state(
    news_index_url: str, config: dict, manifest: dict,
) -> dict:
    payload = _get_json(news_index_url + "?health_check=1", config)
    expected = {
        "status": "OK",
        "projection_state": "CURRENT",
        "verified_complete": True,
        "active_generation_id": manifest["generation_id"],
        "snapshot_id": manifest["snapshot_id"],
        "source_digest": manifest["source_digest"],
        "receipt_digest": manifest["expected_receipt_digest"],
        "index_count": manifest["expected_index_count"],
        "detail_count": manifest["expected_detail_count"],
        "missing_detail_count": 0,
        "invariant_violation_count": 0,
    }
    contradictions = {
        key: {"expected": value, "received": payload.get(key)}
        for key, value in expected.items() if payload.get(key) != value
    }
    if contradictions:
        raise RemoteInvariantViolation({
            "status": "ERROR", "error_code": "NEWS_PROJECTION_HEALTH_MISMATCH",
            "violation_count": len(contradictions), "contradictions": contradictions,
        })
    return payload


def _sync_news(_local_payload: dict, config: dict) -> None:
    """Advance one immutable generation without exposing partial replacement."""
    state_path = Path(config.get("news_state_file", DEFAULT_NEWS_STATE))
    state = _read_news_sync_state(state_path)
    if state.get("contract_version") != NEWS_MIRROR_CONTRACT_VERSION:
        state = {"contract_version": NEWS_MIRROR_CONTRACT_VERSION}
    news_index_url = config.get("remote_news_index_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-index"
    )
    news_url = config.get("remote_news_ingest_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-content"
    )
    if config.get("local_status_url"):
        manifest_page = _get_local_json(_local_news_archive_url(
            config, mode="manifest",
            activated_snapshot_id=state.get("active_snapshot_id"),
        ))
        manifest = manifest_page.get("manifest")
    else:
        raise PayloadContractError(
            "news generation sync requires local_status_url for frozen batch replay"
        )
    if not isinstance(manifest, dict):
        raise PayloadContractError("local news projection manifest is missing")
    generation_id = str(manifest.get("generation_id") or "")
    previous_generation = state.get("generation_id")
    if (
        previous_generation and previous_generation != generation_id
        and state.get("projection_state") != "CURRENT"
    ):
        _post_json(news_index_url, json.dumps({
            "action": "abandon", "generation_id": previous_generation,
        }, separators=(",", ":")).encode(), config)
        state = {"contract_version": NEWS_MIRROR_CONTRACT_VERSION}
    prepare_payload = json.dumps({
        "action": "prepare", "generation_id": generation_id,
        "manifest": manifest,
    }, ensure_ascii=False, separators=(",", ":")).encode()
    # A busy generation may belong to another exact producer (for example the
    # still-active Stable mirror while a Candidate bootstrap is replaying).
    # Only the generation recorded in this producer's own state may be
    # abandoned above. Preserve a foreign staging generation and let the
    # caller retry after the owning producer advances it.
    prepare = _post_json(news_index_url, prepare_payload, config)
    detail_offset = int(prepare.get("next_detail_offset", 0))
    index_offset = int(prepare.get("next_index_offset", 0))
    work = 0
    snapshot_id = str(manifest["snapshot_id"])
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset < int(manifest["expected_detail_count"])
    ):
        page = _get_local_json(_local_news_archive_url(
            config, mode="batch", snapshot_id=snapshot_id,
            kind="detail", offset=detail_offset,
        ))
        items = page.get("items")
        if not isinstance(items, list) or not items:
            raise PayloadContractError("local news detail batch did not advance")
        result = _post_json(news_url, json.dumps({
            "action": "stage_details", "generation_id": generation_id,
            "offset": detail_offset, "items": items,
        }, ensure_ascii=False, separators=(",", ":")).encode(), config)
        detail_offset += len(items)
        if int(result.get("received", -1)) != len(items):
            raise PayloadContractError("remote news detail receipt count mismatched")
        work += 1
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset == int(manifest["expected_detail_count"])
        and index_offset < int(manifest["expected_index_count"])
    ):
        page = _get_local_json(_local_news_archive_url(
            config, mode="batch", snapshot_id=snapshot_id,
            kind="index", offset=index_offset,
        ))
        items = page.get("items")
        if not isinstance(items, list) or not items:
            raise PayloadContractError("local news index batch did not advance")
        result = _post_json(news_index_url, json.dumps({
            "action": "stage_index", "generation_id": generation_id,
            "offset": index_offset, "items": items,
        }, ensure_ascii=False, separators=(",", ":")).encode(), config)
        index_offset += len(items)
        if int(result.get("received", -1)) != len(items):
            raise PayloadContractError("remote news index receipt count mismatched")
        work += 1
    complete = (
        detail_offset == int(manifest["expected_detail_count"])
        and index_offset == int(manifest["expected_index_count"])
    )
    if not prepare.get("active") and complete:
        _post_json(news_index_url, json.dumps({
            "action": "activate", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
        _post_json(news_index_url, json.dumps({
            "action": "verify", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
    if prepare.get("active") or complete:
        _verify_news_projection_state(news_index_url, config, manifest)
        state["active_snapshot_id"] = snapshot_id
        state["projection_state"] = "CURRENT"
        state["last_success"] = datetime.now(UTC).isoformat()
    else:
        state["projection_state"] = "REPLAYING"
    state.update({
        "generation_id": generation_id, "snapshot_id": snapshot_id,
        "source_digest": manifest["source_digest"],
        "expected_receipt_digest": manifest["expected_receipt_digest"],
        "next_detail_offset": detail_offset, "next_index_offset": index_offset,
        "expected_detail_count": manifest["expected_detail_count"],
        "expected_index_count": manifest["expected_index_count"],
        "updated_at": datetime.now(UTC).isoformat(),
    })
    _write_news_sync_state(state_path, state)


def _sync_audit(local_payload: dict, config: dict) -> None:
    if not local_payload and config.get("local_status_url"):
        local_payload = _read_local_resource(config, "/api/audit")
    audit_url = config.get("remote_audit_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/audit"
    )
    _post_json(audit_url, audit_snapshot(local_payload), config)
    root = audit_url.rsplit("/", 1)[0]
    producer_revision = _projection_producer_revision()
    if not producer_revision:
        raise PayloadContractError("projection producer revision is unavailable")
    for resource, snapshot in (
        ("audit-briefs", audit_briefs_snapshot(local_payload, producer_revision)),
        ("audit-stories", audit_stories_snapshot(local_payload, producer_revision)),
        ("audit-decisions", audit_decisions_snapshot(local_payload, producer_revision)),
    ):
        _post_json(f"{root}/{resource}", snapshot, config)


def _local_news_evidence_url(
    config: dict, cursor: str | None, *, activated_snapshot_id: str | None = None,
) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": str(NEWS_EVIDENCE_WRITE_BATCH_ITEMS)}
    if cursor:
        query["cursor"] = cursor
    if activated_snapshot_id:
        query["activated_snapshot_id"] = activated_snapshot_id
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-evidence",
        urllib.parse.urlencode(query), "",
    ))


def _local_resource_url(config: dict, path: str) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, path, "", "",
    ))


def _read_local_resource(config: dict, path: str) -> dict:
    with urllib.request.urlopen(
        _local_resource_url(config, path),
        timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
    ) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise PayloadContractError(f"local resource {path} is not an object")
    return payload


def _local_critical_status_url(config: dict) -> str:
    return _local_resource_url(config, "/api/critical-status")


def _cleanup_news_evidence_snapshots(
    remote_url: str, snapshot_id: str, config: dict, *, post_json=None,
) -> bool:
    """Drain bounded cleanup debt faster than one replacement can create it."""
    post = post_json or _post_json
    payload = json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "cleanup_active_snapshot": snapshot_id,
    }, separators=(",", ":")).encode("utf-8")
    cleanup_pending = False
    for _ in range(NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE):
        result = post(remote_url, payload, config)
        cleanup_pending = result.get("cleanup_pending") is True
        if not cleanup_pending:
            return False
    return cleanup_pending


def _sync_news_evidence(
    _local_payload: dict, config: dict, *, post_json=None,
) -> None:
    """Advance a bounded staging window and activate only a complete snapshot."""
    post = post_json or _post_json
    if not config.get("local_status_url"):
        return
    remote_url = config.get("remote_news_evidence_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-evidence"
    )
    state_path = Path(config.get(
        "news_evidence_state_file", DEFAULT_NEWS_EVIDENCE_STATE,
    ))
    state = _read_news_sync_state(state_path)
    cursor = None
    snapshot_id = None
    total = None
    received = 0
    first_page = None
    with urllib.request.urlopen(
        _local_news_evidence_url(
            config,
            None,
            activated_snapshot_id=(
                str(state.get("active_snapshot_id"))
                if state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
                and state.get("active_snapshot_id") else None
            ),
        ),
        timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
    ) as response:
        first_page = json.loads(response.read())
    first_snapshot = str(first_page.get("snapshot_id") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", first_snapshot):
        raise PayloadContractError("local news evidence snapshot id is invalid")
    active_snapshot = (
        str(state.get("active_snapshot_id"))
        if state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("active_snapshot_id") else ""
    )
    if active_snapshot and _cleanup_news_evidence_snapshots(
        remote_url, active_snapshot, config, post_json=post,
    ):
        return
    if (
        state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("active_snapshot_id") == first_snapshot
    ):
        return
    snapshot_id = first_snapshot
    total = int(first_page.get("total") or 0)
    prepared = post(remote_url, json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "prepare_snapshot": snapshot_id,
        "expected_count": total,
    }, separators=(",", ":")).encode("utf-8"), config) or {}
    if prepared.get("active") is True:
        _write_news_sync_state(state_path, {
            "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
            "active_snapshot_id": snapshot_id,
            "record_count": total,
            "last_success": datetime.now(UTC).isoformat(),
        })
        return
    received = int(prepared.get("next_offset") or 0)
    if received < 0 or received > total:
        raise PayloadContractError("remote news evidence staging offset is invalid")
    cursor = f"{snapshot_id}:{received}" if received else None

    for page_number in range(NEWS_EVIDENCE_PAGES_PER_CYCLE):
        if page_number == 0 and cursor is None:
            page = first_page
        else:
            with urllib.request.urlopen(
                _local_news_evidence_url(config, cursor),
                timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
            ) as response:
                page = json.loads(response.read())
        page_snapshot = str(page.get("snapshot_id") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", page_snapshot):
            raise PayloadContractError("local news evidence snapshot id is invalid")
        if page_snapshot != snapshot_id:
            raise PayloadContractError("local news evidence snapshot changed during paging")
        items = page.get("items")
        if not isinstance(items, list):
            raise PayloadContractError("local news evidence page has invalid items")
        if items:
            encoded = json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "snapshot_id": snapshot_id,
                "offset": received,
                "items": items,
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > NEWS_EVIDENCE_BATCH_LIMIT_BYTES:
                raise PayloadContractError(
                    f"news evidence batch is {len(encoded)} bytes "
                    f"(limit {NEWS_EVIDENCE_BATCH_LIMIT_BYTES})"
                )
            post(remote_url, encoded, config)
            received += len(items)
        next_cursor = page.get("next_cursor")
        if not page.get("has_more"):
            if total is None or received != total:
                raise PayloadContractError(
                    f"news evidence snapshot expected {total} rows but staged {received}"
                )
            post(remote_url, json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "activate_snapshot": snapshot_id,
                "expected_count": total,
            }, separators=(",", ":")).encode("utf-8"), config)
            _write_news_sync_state(state_path, {
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "active_snapshot_id": snapshot_id,
                "record_count": total,
                "last_success": datetime.now(UTC).isoformat(),
            })
            _cleanup_news_evidence_snapshots(
                remote_url, snapshot_id, config, post_json=post,
            )
            return
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise PayloadContractError("local news evidence cursor did not advance")
        cursor = next_cursor
    _write_news_sync_state(state_path, {
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "staging_snapshot_id": snapshot_id,
        "record_count": total,
        "staged_count": received,
        "next_cursor": cursor,
        "last_progress": datetime.now(UTC).isoformat(),
    })
