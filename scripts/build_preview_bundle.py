#!/usr/bin/env python3
"""Build an immutable branch snapshot for Worker Previews."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import types
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
package = types.ModuleType("xauusd_forecaster")
package.__path__ = [str(MODULE_ROOT / "xauusd_forecaster")]
sys.modules["xauusd_forecaster"] = package
factor_coverage = importlib.import_module("xauusd_forecaster.factors").factor_coverage
model_limits = importlib.import_module("xauusd_forecaster.model_limits")
storylines = importlib.import_module("xauusd_forecaster.storylines")
dashboard_sync = importlib.import_module("scripts.run_dashboard_sync")


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
    queue.update({
        "requests_per_minute_per_key": model_limits.GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
        "requests_per_minute": model_limits.GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
        "input_tokens_per_minute": model_limits.GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
        "minute_scope": "PROJECT",
    })


def _read_json(base_url: str, path: str) -> dict:
    request = urllib.request.Request(
        urllib.parse.urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/")),
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
        if item.get("annotation_status") != "NOT_REQUIRED":
            continue
        if item.get("annotation_reason_code") and item.get("annotation_reason"):
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
                first_seen = datetime.fromisoformat(
                    str(item["collector_first_seen_time"])
                )
                if str(item.get("source") or "").startswith(("google_news_", "gdelt_")):
                    code, reason = (
                        "SEARCH_LEAD", "搜索线索：来自聚合发现源，不是独立官方发布",
                    )
                else:
                    code, reason = (
                        "DUPLICATE_CONTENT", "重复内容：同一事件已有正文更完整的版本",
                    )
        item["annotation_reason_code"] = code
        item["annotation_reason"] = reason


def _rebuild_story_snapshot(status: dict) -> None:
    """Replay serialized story nodes through the branch's current policy.

    Preview builds intentionally start from the public production snapshot.
    Replaying its compact timeline rows lets a storyline-policy PR demonstrate
    grouping changes without mutating production or copying grouping logic into
    the frontend.
    """
    events: list[dict[str, object]] = []
    kinds = {
        "timeline": "FACT_EVENT",
        "market_reactions": "MARKET_REACTION",
        "commentary": "COMMENTARY_FORECAST",
        "background": "BACKGROUND",
    }
    for story in status.get("storylines", []):
        for bucket, record_kind in kinds.items():
            for item in story.get(bucket, []):
                event = dict(item)
                event.update({
                    "event_cluster_id": item["event_key"],
                    "canonical_headline": item["headline"],
                    "episode_key": story["episode_key"],
                    "primary_story_title_zh": story["title"],
                    "prompt_version": storylines.CURRENT_EVENT_PROMPT_VERSION,
                    "record_kind": record_kind,
                    "document_kind": (item.get("document_kinds") or ["NEWS_REPORT"])[0],
                    "source_organizations": story.get("source_organizations") or [],
                    "source_names": [],
                    "publisher_domains": [],
                    "member_count": item.get("evidence_documents") or 1,
                    "relation_to_prior": item.get("relation"),
                })
                events.append(event)
    if not events:
        return

    graph = storylines.temporal_event_graph(events)
    existing_candidates = {
        row.get("episode_key"): row
        for row in status.get("story_event_candidates", [])
        if row.get("episode_key")
    }
    for row in graph["event_candidates"]:
        existing_candidates[row["episode_key"]] = row
    status.update({
        "storylines": graph["stories"],
        "market_narrative_candidates": graph["market_narrative_candidates"],
        "story_event_candidates": sorted(
            existing_candidates.values(),
            key=lambda row: row.get("first_seen") or "",
            reverse=True,
        ),
    })
    summary = status.setdefault("storyline_summary", {})
    summary.update({
        "policy_version": graph["policy_version"],
        "total": len(graph["stories"]),
        "market_narrative_total": len(graph["market_narrative_candidates"]),
        "candidate_total": len(existing_candidates),
    })


def build_bundle(base_url: str, branch: str, commit_sha: str) -> dict:
    status = _read_json(base_url, "/api/status")
    _apply_branch_runtime_contract(status)
    learning = _read_json(base_url, "/api/learning")
    market_chart = _read_json(base_url, "/api/market-chart")
    market_chart["history_resource"] = "/api/market-history"
    news_index = _read_json(base_url, "/api/news-index?limit=50")

    status["factor_coverage"] = _rebuild_factor_coverage(status)
    _backfill_annotation_reasons(news_index, status)
    _rebuild_story_snapshot(status)
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
    indexed_history = {
        (row["resource"], row["record_key"]): row
        for row in [*learning_history, *execution_history, *curve_overviews]
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
    parser.add_argument(
        "--source",
        default=os.environ.get("AURUM_PREVIEW_SOURCE_URL", DEFAULT_SOURCE),
    )
    args = parser.parse_args()
    json.dump(
        build_bundle(args.source, args.branch, args.commit),
        sys.stdout,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
