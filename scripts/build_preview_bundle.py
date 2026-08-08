#!/usr/bin/env python3
"""Build an immutable, branch-aware dashboard bundle for Worker previews."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import types
import urllib.parse
import urllib.request
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
package = types.ModuleType("xauusd_forecaster")
package.__path__ = [str(MODULE_ROOT / "xauusd_forecaster")]
sys.modules["xauusd_forecaster"] = package
factor_coverage = importlib.import_module("xauusd_forecaster.factors").factor_coverage


DEFAULT_SOURCE = "https://aurum-signal-room.yiyousiow1234.workers.dev"
SERIES_BY_DOMAIN = {
    "利率": "DGS2",
    "实际收益率": "DFII10",
    "美元": "DTWEXBGS",
    "油价": "DCOILWTICO",
    "流动性": "WALCL",
    "风险偏好": "VIXCLS",
}


def _read_json(base_url: str, path: str) -> dict:
    request = urllib.request.Request(
        urllib.parse.urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/")),
        headers={"Accept": "application/json", "User-Agent": "aurum-preview-builder/1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"preview source returned HTTP {response.status} for {path}")
        return json.load(response)


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
    return factor_coverage(latest_macro, collected_sources)


def build_bundle(base_url: str, branch: str, commit_sha: str) -> dict:
    status = _read_json(base_url, "/api/status")
    learning = _read_json(base_url, "/api/learning")
    market_chart = _read_json(base_url, "/api/market-chart")
    news_index = _read_json(base_url, "/api/news-index?limit=50")

    status["factor_coverage"] = _rebuild_factor_coverage(status)
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
    for item in news_index.get("items", [])[:12]:
        detail_key = item.get("detail_key")
        if not isinstance(detail_key, str):
            continue
        try:
            details[detail_key] = _read_json(
                base_url, f"/api/news-content?key={urllib.parse.quote(detail_key)}",
            )
        except (OSError, RuntimeError, json.JSONDecodeError):
            continue

    return {
        "status": status,
        "learning": learning,
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
