"""Domain owner extracted from the current production entry point."""

from __future__ import annotations

import hashlib


import json


import os


import re


import subprocess


import threading


import tempfile


import time


import urllib.request


from datetime import UTC, datetime, timedelta


from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[3]


DEFERRED_PROJECTION_CONTRACT = "deferred-projection-sync-v1"


DEFERRED_PROJECTION_ROUTES = frozenset({
    "/api/audit-briefs", "/api/audit-stories", "/api/audit-decisions",
})


UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


NEWS_PROJECTION_BATCHES_PER_CYCLE = 4


NEWS_EVIDENCE_WRITE_BATCH_ITEMS = 8


NEWS_EVIDENCE_BATCH_LIMIT_BYTES = 80_000


NEWS_EVIDENCE_PAGES_PER_CYCLE = 4


NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE = 8


MARKET_HISTORY_PAGES_PER_CYCLE = 1


MARKET_OVERVIEWS_PER_CYCLE = 2


RESOURCE_BACKOFF_MAX_SECONDS = 3_600


NEWS_EVIDENCE_CONTRACT_VERSION = "news-evidence-paged-v2"


MARKET_HISTORY_CONTRACT_VERSION = "market-history-d1-v2"


MARKET_HISTORY_BATCH_LIMIT_BYTES = 350_000


MARKET_HISTORY_BATCH_ITEMS = 25


MARKET_HISTORY_OVERLAP_SECONDS = 2 * 3_600


LEARNING_HISTORY_FULL_REFRESH_SECONDS = 86_400


_RESOURCE_SCHEDULE_LOCK = threading.Lock()


from xauusd_forecaster.dashboard.resource_contracts import (
    REMOTE_PAYLOAD_LIMIT_BYTES,
    REMOTE_NEWS_LIMIT,
    REMOTE_DECISION_LIMIT,
    REMOTE_DAILY_BRIEF_LIMIT,
    LEARNING_HISTORY_CONTRACT_VERSION,
    LEARNING_HISTORY_BATCH_LIMIT_BYTES,
    LEARNING_SUMMARY_GROUPS_PER_IDENTITY,
    LEARNING_SUMMARY_EXECUTION_RESULTS,
    MARKET_OVERVIEW_DECISIONS_PER_SERIES,
    REMOTE_MARKET_DECISION_LIMIT,
    REMOTE_MARKET_CANDLE_LIMIT,
    REMOTE_MARKET_DENSE_LIMITS,
    REMOTE_MARKET_OVERVIEW_LIMITS,
    MARKET_CHART_SNAPSHOT_LIMIT_BYTES,
    AUDIT_FIRST_PAGE_LIMIT_BYTES,
    AUDIT_DETAIL_LIMIT_BYTES,
    PayloadContractError,
    MARKET_DECISION_FIELDS,
    _stable_news_key,
    news_withdrawal_keys,
    _json_hash,
    news_mirror_parts,
    news_detail_batches,
    news_index_batches,
    _bounded_item_batches,
    _epoch,
    _learning_record,
    _visual_decision_overview,
    _update_decision_overviews,
    learning_history_records,
    learning_history_batches,
    _learning_summary,
    _decision_key,
    _downsample_market_overview,
    compact_market_chart,
    market_chart_snapshot,
    learning_snapshot,
    _encoded_snapshot,
    remote_snapshot,
    audit_snapshot,
    _bounded_audit_snapshot,
    _with_projection_producer,
    audit_briefs_snapshot,
    audit_decisions_snapshot,
    audit_stories_snapshot,
    _audit_detail_snapshot,
)


from xauusd_forecaster.dashboard.payloads import valid_audit_detail_payload


from xauusd_forecaster.news_projection import (
    NEWS_READER_WINDOW_DAYS,
    NEWS_DETAIL_BATCH_ITEMS,
    NEWS_DETAIL_BATCH_LIMIT_BYTES,
    NEWS_INDEX_FIELDS,
    NEWS_INDEX_BATCH_LIMIT_BYTES,
    NEWS_MIRROR_CONTRACT_VERSION,
    NewsProjectionGeneration,
    NewsProjectionRetainedGeneration,
    receipt_payload_hash,
    NEWS_INDEX_BATCH_ITEMS as NEWS_WRITE_BATCH_ITEMS,
    bounded_batches as _projection_bounded_batches,
    sha256_json as _projection_json_hash,
    split_news_rows,
    stable_news_key,
)


from xauusd_forecaster.dashboard.sync.progress import (
    OPERATOR_RETRY_COMMANDS_PER_CYCLE,
    RUNTIME_STATE_ROOT_KEY,
    AllTargetsRejected,
    SyncResourceResults,
    sync_error_code,
)


from xauusd_forecaster.dashboard.sync.transport import (
    LOCAL_STATUS_TIMEOUT_SECONDS,
    RemoteInvariantViolation,
    _assistant_worker_id,
    _get_json,
    _get_local_json,
    _local_retry_url,
    _operator_retry_worker_url,
    _post_json as _transport_post_json,
    _post_local_json,
    _validated_sync_state_path,
    _validated_sync_state_write_path,
    configure_runtime_state,
    configured_targets,
)


def _learning_record_identity(row: dict) -> str:
    return f"{row['resource']}\0{row['record_key']}"


def _projection_producer_revision() -> str:
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(MODULE_ROOT), "rev-parse", "HEAD"],
            text=True, timeout=5,
        ).strip().lower()
    except (OSError, subprocess.SubprocessError):
        return ""
    return revision if re.fullmatch(r"[0-9a-f]{40}", revision) else ""


def _write_runtime_signal(payload: object, config: dict) -> None:
    if not isinstance(payload, dict):
        return
    revision = str(payload.get("main_revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return
    target = Path(config["runtime_signal_file"])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "main_revision": revision,
                "observed_at": datetime.now(UTC).isoformat(),
                "source": "CLOUDFLARE_MAIN_DEPLOYMENT",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(target)


def _post_json(url: str, payload: bytes, config: dict) -> dict:
    result = _transport_post_json(url, payload, config)
    _write_runtime_signal(result, config)
    return result


def _sync_operator_retry_mirror(
    items: object, worker_url: str, config: dict,
) -> None:
    jobs = items if isinstance(items, list) else []
    payload = json.dumps(
        {"action": "SYNC_JOBS", "items": jobs}, ensure_ascii=False,
        allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")
    source_digest = hashlib.sha256(payload).hexdigest()
    state_path_value = config.get("operator_retry_state_file")
    state_path = Path(state_path_value) if state_path_value else None
    state = _read_news_sync_state(state_path) if state_path else {}
    if state.get("source_digest") == source_digest:
        return
    result = _post_json(worker_url, payload, config)
    if result.get("complete") is True and state_path:
        _write_news_sync_state(state_path, {
            "contract_version": "operator-retry-delta-v1",
            "source_digest": source_digest,
            "item_count": len(jobs),
            "last_success": datetime.now(UTC).isoformat(),
        }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))


def _sync_operator_retries(_local_payload: dict, config: dict) -> None:
    local_jobs = _get_local_json(_local_retry_url(config, "/api/retry-jobs"))
    worker_url = _operator_retry_worker_url(config)
    _sync_operator_retry_mirror(local_jobs.get("items", []), worker_url, config)
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
        _sync_operator_retry_mirror(
            refreshed_jobs.get("items", []), worker_url, config,
        )


def _sync_assistant_chat(_local_payload: dict, _config: dict):
    """Assistant is intentionally paused until an API model is configured."""
    return {"status": "PAUSED_NO_MODEL"}


def _sync_news_questions(_local_payload: dict, _config: dict) -> None:
    # Private Assistant Q&A, titles, compaction, and memory indexing are paused
    # together. News annotation, impact, and Daily Brief use separate workers.
    return None


def _read_news_sync_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_news_sync_state(path: Path, state: dict, *, state_root: Path) -> None:
    path = _validated_sync_state_write_path(path, state_root)
    authority = Path(os.path.abspath(state_root))
    authority.mkdir(parents=True, exist_ok=True)
    path = _validated_sync_state_write_path(path, state_root)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=authority,
            prefix="dashboard-sync-state-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False)
        # Windows readers may briefly deny delete sharing. Retry only the
        # atomic replace (never the accepted HTTP operation), for <=70 ms.
        for delay in (0.01, 0.02, 0.04, None):
            try:
                path = _validated_sync_state_write_path(path, state_root)
                destination = os.path.normcase(os.path.abspath(path))
                authority_prefix = os.path.normcase(os.path.abspath(authority)) + os.sep
                if not destination.startswith(authority_prefix):
                    raise ValueError("dashboard sync state path escapes runtime authority")
                temporary.replace(destination)
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or delay is None:
                    raise
                time.sleep(delay)
    finally:
        if temporary is not None:
            _validated_sync_state_write_path(path, state_root)
            temporary.unlink(missing_ok=True)


def _learning_payload(local_payload: dict, config: dict) -> dict:
    if not local_payload and config.get("local_status_url"):
        return _read_local_resource(config, "/api/learning")
    return local_payload


def _sync_learning_history(local_payload: dict, config: dict) -> None:
    """Export exact derived rows, never the first-paint learning summary."""
    history_url = config.get("remote_learning_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning-history"
    )
    state_path = Path(config["learning_history_state_file"])
    state = _read_news_sync_state(state_path)
    cursor = state.get("cursor") if state.get("contract_version") == "exact-chart-history-v1" else None
    query = "?cursor=" + urllib.parse.quote(cursor, safe="") if cursor else ""
    page = _read_local_resource(config, "/api/chart-history" + query)
    if page.get("contract") != "exact-chart-history-v1" or not isinstance(page.get("records"), list):
        raise ValueError("Exact chart export is unavailable")
    position = json.loads(page.get("cursor", "null"))
    if (not isinstance(position, list) or len(position) != 3
            or type(position[0]) is not int
            or not all(isinstance(value, str) for value in position[1:])
            or type(page.get("source_revision")) is not int
            or type(page.get("record_count")) is not int
            or not isinstance(page.get("generated_at"), str)
            or len(page["records"]) > 200):
        raise ValueError("Invalid chart export metadata")
    if page["records"] and cursor and tuple(position) <= tuple(json.loads(cursor)):
        raise ValueError("Chart export cursor did not advance")
    records = [{**row, "resource": "exact-" + row["resource"]} for row in page["records"]]
    if records:
        response = _post_json(history_url, json.dumps({"records": records}, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"), config)
        if response.get("accepted") != len(records):
            raise ValueError("Chart history ACK mismatch")
    elif page.get("complete") and state.get("completed_revision") != page["source_revision"]:
        response = _post_json(history_url, json.dumps({"chart_completion": {
            "contract": page["contract"], "source_revision": page["source_revision"],
            "generated_at": page["generated_at"], "record_count": page["record_count"],
            "chart_format": page.get("chart_format", "exact-v1"),
        }}, separators=(",", ":")).encode("utf-8"), config)
        if response.get("status") != "OK":
            raise ValueError("Chart completion ACK mismatch")
    elif not page.get("complete"):
        raise ValueError("Chart export did not advance")
    _write_news_sync_state(state_path, {
        "contract_version": page["contract"], "cursor": page["cursor"],
        "pending_record_count": 1 if records else 0,
        "completed_revision": state.get("completed_revision") if records else page["source_revision"],
        "last_success": datetime.now(UTC).isoformat(),
    }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))


def _sync_learning_summary(local_payload: dict, config: dict) -> None:
    local_payload = _learning_payload(local_payload, config)
    learning_url = config.get("remote_learning_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning"
    )
    learning_state_path = Path(config["learning_state_file"])
    learning_state = _read_news_sync_state(learning_state_path)
    learning_payload = learning_snapshot(local_payload)
    learning_hash = hashlib.sha256(learning_payload).hexdigest()
    if learning_state.get("payload_hash") != learning_hash:
        _post_json(learning_url, learning_payload, config)
        _write_news_sync_state(learning_state_path, {
            "payload_hash": learning_hash,
            "last_success": datetime.now(UTC).isoformat(),
        }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))


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
    state_path = Path(config["market_history_state_file"])
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
            }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))
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
    }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))


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
    if not isinstance(payload, dict):
        raise PayloadContractError("remote news health acknowledgment malformed")
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
        for key, value in expected.items()
        if type(payload.get(key)) is not type(value) or payload.get(key) != value
    }
    if contradictions:
        raise RemoteInvariantViolation({
            "status": "ERROR", "error_code": "NEWS_PROJECTION_HEALTH_MISMATCH",
            "violation_count": len(contradictions), "contradictions": contradictions,
        })
    return payload


def _frozen_news_projection_batch(
    generation: NewsProjectionGeneration | NewsProjectionRetainedGeneration,
    *, kind: str, offset: int,
) -> list[dict]:
    try:
        return generation.batch_items(kind, offset)
    except ValueError as error:
        raise PayloadContractError(str(error)) from error


def _require_news_ack(result: object, expected: dict, *, action: str) -> dict:
    """HTTP success is not acceptance; never coerce missing or mistyped facts."""
    if not isinstance(result, dict) or any(
        type(result.get(key)) is not type(value) or result.get(key) != value
        for key, value in {"status": "OK", **expected}.items()
    ):
        raise PayloadContractError(f"remote news {action} acknowledgment mismatched")
    return result


def _news_stage_receipt(previous: str, kind: str, offset: int, items: list) -> str:
    return hashlib.sha256(
        f"{previous}\n{kind}|{offset}|{len(items)}|{receipt_payload_hash(items)}".encode()
    ).hexdigest()


def _sync_news(
    _local_payload: dict, config: dict, *,
    frozen_generation: NewsProjectionGeneration | NewsProjectionRetainedGeneration | None = None,
) -> None:
    """Advance one immutable generation without exposing partial replacement."""
    state_path = Path(config["news_state_file"])
    state = _read_news_sync_state(state_path)
    if state.get("contract_version") != NEWS_MIRROR_CONTRACT_VERSION:
        state = {"contract_version": NEWS_MIRROR_CONTRACT_VERSION}
    news_index_url = config.get("remote_news_index_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-index"
    )
    news_url = config.get("remote_news_ingest_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-content"
    )
    target_binding = {
        "index_url": news_index_url, "detail_url": news_url,
        "contract_version": NEWS_MIRROR_CONTRACT_VERSION,
    }
    if state.get("target_binding") is not None and state["target_binding"] != target_binding:
        raise PayloadContractError("pinned news target changed; explicit recovery is required")

    if frozen_generation is not None:
        if isinstance(frozen_generation, NewsProjectionRetainedGeneration):
            frozen_generation.require_target(
                index_url=news_index_url, detail_url=news_url,
                contract_version=NEWS_MIRROR_CONTRACT_VERSION,
            )
        manifest = frozen_generation.manifest
    elif config.get("local_status_url"):
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
        raise PayloadContractError(
            "pinned news generation changed before reaching CURRENT"
        )

    prepare_payload = json.dumps({
        "action": "prepare", "generation_id": generation_id,
        "manifest": manifest,
    }, ensure_ascii=False, separators=(",", ":")).encode()
    # A busy generation may belong to another exact producer (for example the
    # still-active Stable mirror while a Candidate bootstrap is replaying).
    # Preserve a foreign staging generation and let the caller retry after the
    # owning producer advances it. Abandonment requires explicit recovery.
    prepare = _require_news_ack(
        _post_json(news_index_url, prepare_payload, config),
        {"generation_id": generation_id}, action="prepare",
    )
    detail_offset = prepare.get("next_detail_offset")
    index_offset = prepare.get("next_index_offset")
    if (type(prepare.get("active")) is not bool
            or type(detail_offset) is not int or type(index_offset) is not int
            or not 0 <= detail_offset <= manifest["expected_detail_count"]
            or not 0 <= index_offset <= manifest["expected_index_count"]
            or index_offset > 0 and detail_offset != manifest["expected_detail_count"]
            or prepare["active"] and (
                detail_offset != manifest["expected_detail_count"]
                or index_offset != manifest["expected_index_count"]
            )):
        raise PayloadContractError("remote news prepare progress mismatched")
    receipt = prepare.get("receipt_digest")
    if not prepare["active"] and (
        not isinstance(receipt, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt)
    ):
        raise PayloadContractError("remote news prepare receipt missing")
    work = 0
    snapshot_id = str(manifest["snapshot_id"])
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset < int(manifest["expected_detail_count"])
    ):
        if frozen_generation is not None:
            items = _frozen_news_projection_batch(
                frozen_generation, kind="detail", offset=detail_offset,
            )
        else:
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
        receipt = _news_stage_receipt(receipt, "detail", detail_offset, items)
        _require_news_ack(result, {
            "received": len(items), "receipt_digest": receipt,
        }, action="stage_details")
        detail_offset += len(items)
        work += 1
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset == int(manifest["expected_detail_count"])
        and index_offset < int(manifest["expected_index_count"])
    ):
        if frozen_generation is not None:
            items = _frozen_news_projection_batch(
                frozen_generation, kind="index", offset=index_offset,
            )
        else:
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
        receipt = _news_stage_receipt(receipt, "index", index_offset, items)
        _require_news_ack(result, {
            "received": len(items), "receipt_digest": receipt,
        }, action="stage_index")
        index_offset += len(items)
        work += 1

    complete = (
        detail_offset == int(manifest["expected_detail_count"])
        and index_offset == int(manifest["expected_index_count"])
    )
    if not prepare.get("active") and complete:
        if receipt != manifest["expected_receipt_digest"]:
            raise PayloadContractError("remote news completed receipt mismatched")
        activation = _post_json(news_index_url, json.dumps({
            "action": "activate", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
        _require_news_ack(activation, {
            "activated": generation_id, "index_count": manifest["expected_index_count"],
            "detail_count": manifest["expected_detail_count"],
        }, action="activate")
        verification = _post_json(news_index_url, json.dumps({
            "action": "verify", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
        _require_news_ack(verification, {"generation_id": generation_id}, action="verify")
    if prepare.get("active") or complete:
        _verify_news_projection_state(news_index_url, config, manifest)
        state["active_snapshot_id"] = snapshot_id
        state["projection_state"] = "CURRENT"
        state["last_success"] = datetime.now(UTC).isoformat()
    else:
        state["projection_state"] = "REPLAYING"
    state.update({
        "target_binding": target_binding,
        "generation_id": generation_id, "snapshot_id": snapshot_id,
        "source_digest": manifest["source_digest"],
        "expected_receipt_digest": manifest["expected_receipt_digest"],
        "next_detail_offset": detail_offset, "next_index_offset": index_offset,
        "expected_detail_count": manifest["expected_detail_count"],
        "expected_index_count": manifest["expected_index_count"],
        "updated_at": datetime.now(UTC).isoformat(),
    })
    _write_news_sync_state(state_path, state, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))


def _audit_projection_bytes(
    local_payload: dict, config: dict, *, read_local_details: bool = False,
) -> dict[str, bytes]:
    """Read each bounded local resource; a landing summary is never detail."""
    from_local = not local_payload and bool(config.get("local_status_url"))
    if from_local:
        local_payload = _read_local_resource(config, "/api/audit")
    read_local_details = read_local_details or from_local
    producer_revision = _projection_producer_revision()
    if not producer_revision:
        raise PayloadContractError("projection producer revision is unavailable")
    projected = {"/api/audit": audit_snapshot(local_payload)}
    for family, builder in (
        ("briefs", audit_briefs_snapshot),
        ("stories", audit_stories_snapshot),
        ("decisions", audit_decisions_snapshot),
    ):
        route = f"/api/audit-{family}"
        detail = _read_local_resource(config, route) if read_local_details else local_payload
        if read_local_details and (
            not valid_audit_detail_payload(detail, family)
            or detail.get("generated_at") != local_payload.get("generated_at")
        ):
            raise PayloadContractError(f"{route} source snapshot is unavailable or changed")
        projected[route] = builder(detail, producer_revision)
    return projected


def _publish_audit_projection_bytes(projected: dict[str, bytes], config: dict) -> None:
    audit_url = config.get("remote_audit_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/audit"
    )
    root = audit_url.rsplit("/", 1)[0]
    for route, body in projected.items():
        _post_json(f"{root}/{route.rsplit('/', 1)[-1]}", body, config)


def _sync_audit(local_payload: dict, config: dict) -> None:
    _publish_audit_projection_bytes(_audit_projection_bytes(local_payload, config), config)


def _deferred_projection_request_digest(request: dict) -> str:
    encoded = json.dumps(
        request, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deferred_projection_paths(config: dict) -> tuple[Path, Path]:
    return (
        Path(config["deferred_projection_request_file"]),
        Path(config["deferred_projection_receipt_file"]),
    )


def _read_deferred_projection_request(config: dict) -> dict | None:
    request_path, _receipt_path = _deferred_projection_paths(config)
    if not request_path.exists():
        return None
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PayloadContractError(
            "deferred projection request is unreadable"
        ) from error
    if not isinstance(request, dict):
        raise PayloadContractError("deferred projection request must be an object")
    routes = request.get("routes")
    if (
        request.get("schema_version") != DEFERRED_PROJECTION_CONTRACT
        or not re.fullmatch(
            r"[0-9a-f]{40}", str(request.get("producer_revision") or "")
        )
        or not UUID_PATTERN.fullmatch(str(request.get("worker_version_id") or ""))
        or not UUID_PATTERN.fullmatch(str(request.get("request_id") or ""))
        or not UUID_PATTERN.fullmatch(str(request.get("transaction_id") or ""))
        or not str(request.get("validation_key") or "")
        or request.get("target") != "cloudflare"
        or not isinstance(routes, list)
        or not routes
        or len(routes) != len(set(routes))
        or any(route not in DEFERRED_PROJECTION_ROUTES and route != "/api/news-evidence" for route in routes)
    ):
        raise PayloadContractError("deferred projection request contract mismatch")
    if "/api/news-evidence" in routes:
        incident = request.get("collector_recovery")
        if not isinstance(incident, dict) or (
            incident.get("incident") != "COLLECTOR_CLOCK_EVENT_ATOMICITY"
            or incident.get("broken_revision") != "ffe1de29c0891cc3a3cf3d602f3d3ee657faa9b8"
            or incident.get("target_revision") != request["producer_revision"]
            or incident.get("target_revision") == incident.get("broken_revision")
        ):
            raise PayloadContractError("deferred News recovery incident mismatch")
    try:
        timestamps = [
            datetime.fromisoformat(str(request[field]).replace("Z", "+00:00"))
            for field in ("required_after", "created_at")
        ]
    except (KeyError, ValueError) as error:
        raise PayloadContractError(
            "deferred projection request freshness boundary is invalid"
        ) from error
    if any(value.tzinfo is None for value in timestamps):
        raise PayloadContractError(
            "deferred projection request freshness boundary must be timezone-aware"
        )
    return request


def _deferred_projection_pending(config: dict) -> bool:
    if (
        "deferred_projection_request_file" not in config
        or "deferred_projection_receipt_file" not in config
    ):
        return False
    request = _read_deferred_projection_request(config)
    if request is None:
        return False
    _request_path, receipt_path = _deferred_projection_paths(config)
    receipt = _read_news_sync_state(receipt_path)
    return not (
        receipt.get("schema_version") == DEFERRED_PROJECTION_CONTRACT
        and receipt.get("request_id") == request["request_id"]
        and receipt.get("request_digest")
        == _deferred_projection_request_digest(request)
        and receipt.get("state") == "COMPLETED"
    )


def sync_deferred_projection_once(
    targets: list[dict], config: dict,
) -> SyncResourceResults:
    """Advance one exact post-cutover projection through the existing owner."""
    started = time.perf_counter()
    try:
        request = _read_deferred_projection_request(config)
        if request is None or not _deferred_projection_pending(config):
            return SyncResourceResults([], [])
        producer_revision = _projection_producer_revision()
        if producer_revision != request["producer_revision"]:
            raise PayloadContractError(
                "deferred projection producer revision mismatch"
            )
        matching_targets = [
            target for target in targets if target.get("name") == request["target"]
        ]
        if len(matching_targets) != 1:
            raise RuntimeError("deferred projection target is not healthy and unique")
        target = matching_targets[0]
        required_after = datetime.fromisoformat(
            str(request["required_after"]).replace("Z", "+00:00")
        )
        _request_path, receipt_path = _deferred_projection_paths(config)
        prior = _read_news_sync_state(receipt_path)
        request_digest = _deferred_projection_request_digest(request)
        reusable = (
            prior.get("schema_version") == DEFERRED_PROJECTION_CONTRACT
            and prior.get("request_id") == request["request_id"]
            and prior.get("request_digest") == request_digest
            and prior.get("producer_revision") == producer_revision
            and prior.get("state") == "PARTIAL"
        )
        hashes = dict(prior.get("projection_hashes", {})) if reusable else {}
        audit_routes = [route for route in request["routes"] if route != "/api/news-evidence"]
        generated_at = datetime.fromisoformat(prior["generated_at"]) if reusable else datetime.now(UTC)
        if any(route not in hashes for route in audit_routes):
            local_payload = _read_local_resource(target, "/api/audit")
            generated_at = datetime.fromisoformat(
                str(local_payload.get("generated_at") or "").replace("Z", "+00:00")
            )
            if generated_at.tzinfo is None:
                raise PayloadContractError(
                    "deferred projection source timestamp must be timezone-aware"
                )
            if generated_at.astimezone(UTC) < required_after.astimezone(UTC):
                return SyncResourceResults([], [])
            # Preserve resumable accepted routes, while the audit producer owns
            # the actual full-detail bytes and common pinned snapshot identity.
            projection_bytes = _audit_projection_bytes(
                local_payload, target, read_local_details=True,
            )
            hashes.update({route: hashlib.sha256(
                projection_bytes[route],
            ).hexdigest() for route in audit_routes})
            _publish_audit_projection_bytes(projection_bytes, target)
            _persist_resource_schedule_result(
                _resource_schedule_path(target), target, "audit", 300,
                now=datetime.now(UTC), success=True,
            )
        receipt = {
            "schema_version": DEFERRED_PROJECTION_CONTRACT,
            "state": "PARTIAL",
            "request_id": request["request_id"],
            "transaction_id": request["transaction_id"],
            "request_digest": request_digest,
            "validation_key": request["validation_key"],
            "worker_version_id": request["worker_version_id"],
            "producer_revision": producer_revision,
            "required_after": required_after.astimezone(UTC).isoformat(),
            "generated_at": generated_at.astimezone(UTC).isoformat(),
            "routes": list(request["routes"]),
            "projection_hashes": hashes,
        }
        observations = []
        if "/api/news-evidence" in request["routes"]:
            # Preserve accepted Audit work while the existing News cursor moves
            # by its normal one-page budget. There is still one serial Sync owner.
            _write_news_sync_state(receipt_path, receipt, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))
            prior_news = _read_news_sync_state(Path(target["news_evidence_state_file"]))
            snapshot = _sync_news_evidence({}, target)
            ack = _read_news_sync_state(Path(target["news_evidence_state_file"]))
            if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise PayloadContractError("deferred News snapshot identity invalid")
            if (
                ack.get("contract_version") != NEWS_EVIDENCE_CONTRACT_VERSION
                or ack.get("active_snapshot_id") != snapshot
                or ack.get("ack_remote_url") != (target.get("remote_news_evidence_url") or (
                    target["remote_ingest_url"].rsplit("/", 1)[0] + "/news-evidence"
                ))
                or not re.fullmatch(r"[a-f0-9]{64}", str(ack.get("ack_request_sha256") or ""))
            ):
                # Only real accepted page progress drains immediately. Cleanup
                # debt or an unchanged cursor must not create a busy retry loop.
                if (
                    ack.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
                    and ack.get("staging_snapshot_id") == snapshot
                    and int(ack.get("staged_count") or 0) > (
                        int(prior_news.get("staged_count") or 0)
                        if prior_news.get("staging_snapshot_id") == snapshot else 0
                    )
                ):
                    return SyncResourceResults([], [{
                        "target": request["target"], "resource": "deferred_projection",
                        "status": "PROGRESS",
                        "duration_ms": round((time.perf_counter()-started)*1000, 1),
                        "completed_at": datetime.now(UTC).isoformat(),
                    }])
                return SyncResourceResults([], [])
            hashes["/api/news-evidence"] = snapshot
            receipt["news_recovery"] = {
                "snapshot_id": snapshot, "record_count": ack.get("record_count"),
                "local_read_completed_at": datetime.now(UTC).isoformat(),
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
            }
            _persist_resource_schedule_result(
                _resource_schedule_path(target), target, "news_evidence", 300,
                now=datetime.now(UTC), success=True,
            )
            observations.append({
                "target": request["target"], "resource": "news_evidence", "status": "OK",
                "duration_ms": round((time.perf_counter()-started)*1000, 1),
                "completed_at": datetime.now(UTC).isoformat(),
            })
        completed_at = datetime.now(UTC)
        receipt.update(state="COMPLETED", completed_at=completed_at.isoformat())
        _write_news_sync_state(receipt_path, receipt, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))
        return SyncResourceResults([], [*observations, {
            "target": request["target"],
            "resource": "deferred_projection",
            "status": "OK",
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "completed_at": completed_at.isoformat(),
        }])
    except Exception as error:
        completed_at = datetime.now(UTC).isoformat()
        failure = {
            "target": "cloudflare",
            "resource": "deferred_projection",
            "error_type": type(error).__name__,
            "error_code": sync_error_code(error),
            "error": str(error)[:500],
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        return SyncResourceResults([failure], [{
            "target": "cloudflare", "resource": "deferred_projection",
            "status": "ERROR", "duration_ms": failure["duration_ms"],
            "completed_at": completed_at,
        }])


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


def _post_news_evidence(remote_url: str, payload: bytes, config: dict) -> dict:
    """Advance only on an exact, operation-complete remote acknowledgement."""
    request = json.loads(payload)
    result = _post_json(remote_url, payload, config)
    snapshot_id = next((request[key] for key in (
        "prepare_snapshot", "snapshot_id", "activate_snapshot", "cleanup_active_snapshot",
    ) if key in request), None)

    def require(condition: bool) -> None:
        if not condition:
            raise PayloadContractError("NEWS_EVIDENCE_ACK_INVALID")

    def integer(name: str, maximum: int) -> int:
        value = result.get(name)
        require(type(value) is int and 0 <= value <= maximum)
        return value

    require(isinstance(result, dict))
    require(result.get("status") == "OK")
    require(result.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION)
    require(result.get("snapshot_id") == snapshot_id)
    require(result.get("request_sha256") == hashlib.sha256(payload).hexdigest())
    if "prepare_snapshot" in request:
        total = request["expected_count"]
        offset = integer("next_offset", total)
        require(type(result.get("active")) is bool)
        require(not result["active"] or offset == total)
        if "repaired_from" in result:
            require(not result["active"] and integer("repaired_from", total) > offset)
    elif "items" in request:
        require(integer("received", len(request["items"])) == len(request["items"]))
        if "duplicate" in result:
            require(type(result["duplicate"]) is bool)
    elif "activate_snapshot" in request:
        require(result.get("activated") == snapshot_id)
        require(integer("count", request["expected_count"]) == request["expected_count"])
    else:
        require(result.get("cleanup") == "advanced")
        require(type(result.get("cleanup_pending")) is bool)
        for field, limit in (("deleted_records", 200), ("deleted_batches", 20),
                             ("deleted_staging", 20)):
            integer(field, limit)
    return result


def _cleanup_news_evidence_snapshots(
    remote_url: str, snapshot_id: str, config: dict,
) -> bool:
    """Advance cleanup debt within the bounded per-cycle request allowance."""
    payload = json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "cleanup_active_snapshot": snapshot_id,
    }, separators=(",", ":")).encode("utf-8")
    cleanup_pending = False
    for _ in range(NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE):
        result = _post_news_evidence(remote_url, payload, config)
        cleanup_pending = result.get("cleanup_pending") is True
        if not cleanup_pending:
            return False
    return cleanup_pending


def _sync_news_evidence(_local_payload: dict, config: dict) -> str | None:
    """Advance a bounded staging window and activate only a complete snapshot."""
    if not config.get("local_status_url"):
        return
    remote_url = config.get("remote_news_evidence_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-evidence"
    )
    state_path = Path(config["news_evidence_state_file"])
    state = _read_news_sync_state(state_path)
    # Older target-owned state still owns cleanup debt, but cannot use the
    # no-change fast path until a complete new acknowledgement is obtained.
    ack_target_matches = state.get("ack_remote_url", remote_url) == remote_url
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
                and ack_target_matches
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
        and ack_target_matches
        and state.get("active_snapshot_id") else ""
    )
    if active_snapshot:
        cleanup_pending = _cleanup_news_evidence_snapshots(remote_url, active_snapshot, config)
        if cleanup_pending or state.get("cleanup_pending"):
            state = {**state, "cleanup_pending": cleanup_pending}
            _write_news_sync_state(
                state_path, state, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]),
            )
        if cleanup_pending:
            return first_snapshot
    if (
        state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("active_snapshot_id") == first_snapshot
        and state.get("ack_remote_url") == remote_url
        and isinstance(state.get("ack_request_sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", state["ack_request_sha256"])
    ):
        return first_snapshot
    snapshot_id = first_snapshot
    total = first_page.get("total")
    if type(total) is not int or total < 0:
        raise PayloadContractError("local news evidence count is invalid")
    prepared = _post_news_evidence(remote_url, json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "prepare_snapshot": snapshot_id,
        "expected_count": total,
    }, separators=(",", ":")).encode("utf-8"), config)
    if prepared.get("active") is True:
        _write_news_sync_state(state_path, {
            "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
            "active_snapshot_id": snapshot_id,
            "record_count": total,
            "ack_remote_url": remote_url,
            "ack_request_sha256": prepared["request_sha256"],
            "last_success": datetime.now(UTC).isoformat(),
        }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))
        return snapshot_id
    received = prepared["next_offset"]
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
            _post_news_evidence(remote_url, encoded, config)
            received += len(items)
        next_cursor = page.get("next_cursor")
        if not page.get("has_more"):
            if total is None or received != total:
                raise PayloadContractError(
                    f"news evidence snapshot expected {total} rows but staged {received}"
                )
            activated = _post_news_evidence(remote_url, json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "activate_snapshot": snapshot_id,
                "expected_count": total,
            }, separators=(",", ":")).encode("utf-8"), config)
            _write_news_sync_state(state_path, {
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "active_snapshot_id": snapshot_id,
                "record_count": total,
                "ack_remote_url": remote_url,
                "ack_request_sha256": activated["request_sha256"],
                "last_success": datetime.now(UTC).isoformat(),
            }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))
            _cleanup_news_evidence_snapshots(remote_url, snapshot_id, config)
            return snapshot_id
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
    }, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))
    return snapshot_id


def _schedule_epoch(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _record_resource_schedule(
    state: dict,
    resource: str,
    cadence_seconds: int,
    *,
    now: datetime,
    success: bool,
    pending: bool = False,
) -> None:
    resources = state.setdefault("resources", {})
    current = resources.get(resource)
    if not isinstance(current, dict):
        current = {}
    failures = 0 if success else int(current.get("consecutive_failures") or 0) + 1
    delay = cadence_seconds if success else min(
        RESOURCE_BACKOFF_MAX_SECONDS,
        max(cadence_seconds, 30 * (2 ** min(failures - 1, 7))),
    )
    if success and pending:
        next_run_at = now
    elif success:
        previous_due = _schedule_epoch(current.get("next_run_at"))
        next_run_epoch = (
            previous_due + cadence_seconds
            if previous_due > 0 else now.timestamp() + cadence_seconds
        )
        while next_run_epoch <= now.timestamp():
            next_run_epoch += cadence_seconds
        next_run_at = datetime.fromtimestamp(next_run_epoch, tz=UTC)
    else:
        next_run_at = now + timedelta(seconds=delay)
    resources[resource] = {
        **current,
        "last_attempt_at": now.isoformat(),
        "last_success_at": now.isoformat() if success else current.get("last_success_at"),
        "consecutive_failures": failures,
        "next_run_at": next_run_at.isoformat(),
    }
    state["schema_version"] = 1
    state["updated_at"] = now.isoformat()


def _resource_schedule_path(config: dict) -> Path:
    return Path(config["resource_schedule_state_file"])


def _persist_resource_schedule_result(
    path: Path,
    config: dict,
    resource: str,
    cadence_seconds: int,
    *,
    now: datetime,
    success: bool,
    pending: bool = False,
) -> None:
    """Merge one lane's result without overwriting another lane's progress."""
    with _RESOURCE_SCHEDULE_LOCK:
        state = _read_news_sync_state(path)
        _record_resource_schedule(
            state, resource, cadence_seconds, now=now, success=success, pending=pending,
        )
        _write_news_sync_state(path, state, state_root=Path(config[RUNTIME_STATE_ROOT_KEY]))
