#!/usr/bin/env python3
"""Build an immutable branch snapshot for Worker Previews."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import types
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
package = types.ModuleType("xauusd_forecaster")
package.__path__ = [str(MODULE_ROOT / "xauusd_forecaster")]
sys.modules["xauusd_forecaster"] = package
factor_coverage = importlib.import_module("xauusd_forecaster.factors").factor_coverage
model_limits = importlib.import_module("xauusd_forecaster.model_limits")
dashboard_sync = importlib.import_module("scripts.run_dashboard_sync")
assess_news_semantic_eligibility = importlib.import_module(
    "xauusd_forecaster.news_time"
).assess_news_semantic_eligibility


DEFAULT_SOURCE = "https://aurum-signal-room.yiyousiow1234.workers.dev"
SERIES_BY_DOMAIN = {
    "利率": "DGS2",
    "实际收益率": "DFII10",
    "美元": "DTWEXBGS",
    "油价": "DCOILWTICO",
    "流动性": "WALCL",
    "风险偏好": "VIXCLS",
}
PREVIEW_MANIFEST = json.loads(
    (MODULE_ROOT / "web" / "preview-manifest.json").read_text(encoding="utf-8")
)
PREVIEW_NEWS_PAGE_SIZE = int(PREVIEW_MANIFEST["newsPageSize"])


def _apply_branch_runtime_contract(status: dict) -> None:
    """Overlay branch-owned limits that an older production snapshot cannot know."""
    queue = status.setdefault("annotation_queue", {})
    account_count = max(
        1,
        int(queue.get("configured_account_count")
            or queue.get("configured_key_count") or 0),
    )
    queue.update({
        "requests_per_minute_per_key": model_limits.GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
        "requests_per_minute_per_account": model_limits.GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
        "requests_per_minute": (
            model_limits.GEMINI_REQUESTS_PER_MINUTE_PER_KEY * account_count
        ),
        "input_tokens_per_minute": (
            model_limits.GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL * account_count
        ),
        "minute_scope": "ACCOUNT",
    })
    display_only = status.setdefault("llm_routing", {}).setdefault(
        "display_only", {}
    )
    display_only.update({
        "configured_account_count": account_count,
        "requests_per_minute_per_account": (
            model_limits.GEMMA_SAFE_REQUESTS_PER_MINUTE_PER_ACCOUNT
        ),
        "requests_per_minute": (
            model_limits.GEMMA_SAFE_REQUESTS_PER_MINUTE_PER_ACCOUNT
            * account_count
        ),
        "input_tokens_per_minute_per_account": (
            model_limits.GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT
        ),
        "input_tokens_per_minute": (
            model_limits.GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT
            * account_count
        ),
        "provider_lanes_per_account": model_limits.GEMMA_PROVIDER_LANES_PER_ACCOUNT,
        "maximum_concurrent_requests": (
            model_limits.GEMMA_PROVIDER_LANES_PER_ACCOUNT * account_count
        ),
        "minute_scope": "ACCOUNT",
    })
    if (not isinstance(status.get("daily_news_brief_summary"), dict)
            and status.get("generated_at")):
        generated = datetime.fromisoformat(str(status["generated_at"]))
        brief_date = generated.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date().isoformat()
        briefs = status.get("daily_news_briefs")
        current = next((
            row for row in briefs if isinstance(row, dict)
            and row.get("brief_date") == brief_date
        ), None) if isinstance(briefs, list) else None
        status["daily_news_brief_summary"] = {
            "brief_date": brief_date,
            "phase": "UPDATING" if current else "WAITING",
            "received_items": None,
            "reviewed_items": None,
            "pending_items": None,
            "terminal_failure_items": None,
            "latest_revision": current.get("revision_number") if current else None,
            "last_generated_at": current.get("generated_at") if current else None,
            "next_retry_at": None,
            "is_final": False,
            "total_brief_days": None,
            "observation_scope": "BUILD_SNAPSHOT_COMPATIBILITY",
        }


def _read_json(base_url: str, path: str) -> dict:
    if base_url.rstrip("/") != DEFAULT_SOURCE:
        raise ValueError("Preview snapshots must read the canonical production origin")
    request = urllib.request.Request(
        urllib.parse.urljoin(f"{DEFAULT_SOURCE}/", path.lstrip("/")),
        headers={"Accept": "application/json", "User-Agent": "aurum-preview-builder/1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"preview source returned HTTP {response.status} for {path}")
        return json.load(response)


def _execution_history_records(base_url: str) -> list[dict]:
    """Copy bounded public execution pages into the immutable build snapshot."""
    records: dict[tuple[str, str], dict] = {}
    for identity in ("LOT_RIDGE", "EXIT_RIDGE"):
        cursor: str | None = None
        while True:
            query = (
                "/api/learning-history?resource=execution-point"
                f"&identity={urllib.parse.quote(identity)}&limit=500"
            )
            if cursor:
                query += f"&cursor={urllib.parse.quote(cursor)}"
            page = _read_json(base_url, query)
            for point in page.get("items", []):
                if not isinstance(point, dict) or not point.get("time"):
                    continue
                payload = {"model_identity": identity, **point}
                record = dashboard_sync._learning_record(
                    "execution-point", f"{identity}\0{point['time']}",
                    dashboard_sync._epoch(point["time"]), payload,
                )
                records[(record["resource"], record["record_key"])] = record
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not isinstance(cursor, str) or not cursor:
                break
    return list(records.values())


def _curve_overview_records(base_url: str) -> list[dict]:
    """Freeze production's materialized curve overviews into Preview."""
    records: dict[tuple[str, str], dict] = {}
    for cadence in ("5m", "30m"):
        response = _read_json(
            base_url,
            f"/api/learning-history?resource=curve-overview&cadence={cadence}",
        )
        for item in response.get("items", []):
            if not isinstance(item, dict):
                continue
            identity = str(item.get("model_identity") or "")
            points = item.get("points") or []
            if not identity or not isinstance(points, list) or not points:
                continue
            last_time = points[-1].get("decision_time")
            if not last_time:
                continue
            payload = {**item, "cadence": cadence}
            record = dashboard_sync._learning_record(
                "curve-overview", f"{cadence}\0{identity}",
                dashboard_sync._epoch(last_time), payload,
            )
            records[(record["resource"], record["record_key"])] = record
    return list(records.values())


def _version_history_records(base_url: str) -> list[dict]:
    """Freeze the bounded production version overview as pageable Preview rows."""
    response = _read_json(
        base_url, "/api/learning-history?resource=version-overview",
    )
    groups_by_identity: dict[str, list[dict]] = {}
    for group in response.get("items", []):
        if not isinstance(group, dict):
            continue
        identity = str(group.get("model_identity") or "")
        dataset_hash = str(group.get("training_dataset_hash") or "")
        created_at = group.get("created_at")
        if not identity or not dataset_hash or not created_at:
            continue
        groups_by_identity.setdefault(identity, []).append(group)

    records: dict[tuple[str, str], dict] = {}
    for identity, groups in groups_by_identity.items():
        ordered = sorted(groups, key=lambda row: (
            row.get("created_at") or "", row.get("generation") or 0,
        ))[-dashboard_sync.LEARNING_OVERVIEW_GROUPS_PER_IDENTITY:]
        for group in ordered:
            dataset_hash = str(group["training_dataset_hash"])
            record = dashboard_sync._learning_record(
                "version-group", f"{identity}\0{dataset_hash}",
                dashboard_sync._epoch(group["created_at"]), group,
            )
            records[(record["resource"], record["record_key"])] = record
    return list(records.values())


def _rebuild_factor_coverage(status: dict) -> list[dict[str, object]]:
    latest_macro: dict[str, dict[str, object]] = {}
    for row in status.get("factor_coverage", []):
        series_id = SERIES_BY_DOMAIN.get(row.get("domain"))
        if series_id and row.get("value") is not None:
            latest_macro[series_id] = {
                "value": row.get("value"),
                "observation_period": row.get("observed_at"),
                "unit": row.get("unit"),
            }
    collected_sources = {
        str(row["source"])
        for row in status.get("news_source_health", [])
        if row.get("source") and int(row.get("item_count") or 0) > 0
    }
    monitored_sources = {
        str(row["source"])
        for row in status.get("news_source_health", [])
        if row.get("source") and row.get("health") == "HEALTHY"
    }
    return factor_coverage(latest_macro, collected_sources, monitored_sources)


def _backfill_annotation_reasons(news_index: dict, status: dict) -> None:
    """Make new audit labels visible against an older production snapshot."""
    epoch_raw = status.get("forward_epoch")
    if not epoch_raw:
        return
    epoch = datetime.fromisoformat(str(epoch_raw))
    for item in news_index.get("items", []):
        annotation_status = item.get("annotation_status")
        if annotation_status not in {"QUEUED", "NOT_REQUIRED"}:
            continue
        if (
            annotation_status == "NOT_REQUIRED"
            and item.get("annotation_reason_code")
            and item.get("annotation_reason")
        ):
            continue
        published_raw = item.get("source_published_time")
        if not published_raw:
            code, reason = "HISTORICAL_MATERIAL", "历史资料：缺少可靠发布时间"
        else:
            published = datetime.fromisoformat(str(published_raw))
            if published < epoch:
                code, reason = (
                    "HISTORICAL_MATERIAL", "历史资料：发布时间早于系统开始记录",
                )
            else:
                assessment = assess_news_semantic_eligibility(
                    item, forward_epoch=epoch,
                )
                if assessment.reason_code == "STALE_EVENT":
                    code, reason = (
                        "STALE_AT_INTAKE", "收到时已超过72小时，不进入语义处理",
                    )
                elif assessment.reason_code == "LATE_DISCOVERY":
                    code, reason = (
                        "LATE_DISCOVERY",
                        "采集时距发布时间已超过60分钟，不进入语义处理",
                    )
                elif assessment.reason_code == "PUBLISHED_AFTER_DECISION":
                    code, reason = (
                        "INVALID_PUBLISHED_TIME", "发布时间晚于收到时间，时间证据无效",
                    )
                else:
                    code, reason = (
                        "QUEUE_INVARIANT_MISMATCH",
                        "正文符合条件但未进入语义队列，需要检查",
                    )
        if annotation_status == "QUEUED" and code == "QUEUE_INVARIANT_MISMATCH":
            continue
        if annotation_status == "QUEUED":
            item["annotation_status"] = "NOT_REQUIRED"
            item["model_visibility"] = "MODEL_INELIGIBLE"
            if not item.get("parsed_at"):
                item["impact_status"] = "NOT_REQUIRED"
        item["annotation_reason_code"] = code
        item["annotation_reason"] = reason


def _read_completed_news_index(base_url: str) -> dict:
    """Read completed news across the old and current public API contracts."""
    completed: list[dict] = []
    first_page: dict | None = None
    for page in range(1, 11):
        payload = _read_json(
            base_url,
            f"/api/news-index?page={page}&limit=50&review_state=COMPLETED",
        )
        if first_page is None:
            first_page = payload
        if payload.get("review_state") == "COMPLETED":
            return payload
        rows = payload.get("items", [])
        completed.extend(
            row for row in rows
            if row.get("annotation_status") in {"READY", "NOT_REQUIRED"}
        )
        if len(completed) >= PREVIEW_NEWS_PAGE_SIZE or len(rows) < 50:
            break

    fallback = dict(first_page or {})
    fallback["items"] = completed[:PREVIEW_NEWS_PAGE_SIZE]
    fallback["total"] = len(completed)
    fallback["category_counts"] = {
        category: sum(
            1 for row in completed if str(row.get("category") or "其他") == category
        )
        for category in {
            str(row.get("category") or "其他") for row in completed
        }
    }
    fallback["review_state"] = "COMPLETED"
    fallback["review_state_counts"] = {"COMPLETED": len(completed)}
    # The compatibility fallback sees only a bounded old-API window. It must
    # never advertise those partial counts as the authoritative D1 archive.
    fallback["totals_scope"] = "BUILD_SNAPSHOT"
    return fallback


def build_bundle(base_url: str, branch: str, commit_sha: str) -> dict:
    status = _read_json(base_url, "/api/status")
    _apply_branch_runtime_contract(status)
    learning = _read_json(base_url, "/api/learning")
    market_chart = _read_json(base_url, "/api/market-chart")
    market_chart["history_resource"] = "/api/market-history"
    news_index = _read_completed_news_index(base_url)

    status["factor_coverage"] = _rebuild_factor_coverage(status)
    _backfill_annotation_reasons(news_index, status)
    status["preview"] = {
        "is_preview": True,
        "branch": branch,
        "commit_sha": commit_sha,
        "snapshot_generated_at": status.get("generated_at"),
        "source": "production-public-snapshot",
        "live": False,
    }
    system = status.setdefault("system", {})
    system["online"] = False
    system["market_session"] = "DATA_UNAVAILABLE"
    system["source_of_truth"] = "PR 构建时公开快照"
    system["sites_mirror"] = "分支内置只读快照"
    deployment = system.setdefault("deployment", {})
    deployment.update({
        "runtime_git_sha": commit_sha,
        "expected_git_sha": commit_sha,
        "runtime_dirty": False,
        "status": "PREVIEW_SNAPSHOT",
    })

    details: dict[str, dict] = {}
    for item in news_index.get("items", [])[:PREVIEW_NEWS_PAGE_SIZE]:
        detail_key = item.get("detail_key")
        if not isinstance(detail_key, str):
            continue
        try:
            details[detail_key] = _read_json(
                base_url, f"/api/news-content?key={urllib.parse.quote(detail_key)}",
            )
        except (OSError, RuntimeError, json.JSONDecodeError):
            continue

    learning_history = dashboard_sync.learning_history_records(
        learning, infer_source_gaps=False,
    )
    try:
        execution_history = _execution_history_records(base_url)
    except (OSError, RuntimeError, json.JSONDecodeError):
        execution_history = []
    try:
        curve_overviews = _curve_overview_records(base_url)
    except (OSError, RuntimeError, json.JSONDecodeError):
        curve_overviews = []
    try:
        version_history = _version_history_records(base_url)
    except (OSError, RuntimeError, json.JSONDecodeError):
        version_history = []
    if version_history:
        # The Preview route derives its overview from these exact bounded rows.
        # Drop the smaller first-paint summary so it cannot mask the fuller set.
        learning_history = [
            row for row in learning_history
            if row["resource"] != "version-overview"
        ]
    indexed_history = {
        (row["resource"], row["record_key"]): row
        for row in [
            *learning_history, *execution_history, *curve_overviews,
            *version_history,
        ]
    }

    return {
        "status": status,
        "learning": learning,
        # Production's public learning payload is already compressed. Wider
        # spacing there is not proof of a source-data gap, so Preview must not
        # infer dashed segments from it.
        "learning_history": list(indexed_history.values()),
        "market_chart": market_chart,
        "news_index": news_index,
        "news_details": details,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    json.dump(
        build_bundle(DEFAULT_SOURCE, args.branch, args.commit),
        sys.stdout,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
