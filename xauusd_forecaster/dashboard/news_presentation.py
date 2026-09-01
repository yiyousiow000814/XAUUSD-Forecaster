"""Deterministic presentation policy for local Dashboard News rows."""

from __future__ import annotations

from datetime import datetime

from xauusd_forecaster.news_evidence import resolve_event_clock
from xauusd_forecaster.news_impact import (
    impact_is_actionable,
    impact_time_rule,
)
from xauusd_forecaster.news_projection import (
    canonicalize_news_projection_impact_clocks,
)
from xauusd_forecaster.news_time import assess_news_semantic_eligibility


NEWS_CATEGORY_LABELS = {
    "rates_fed": "利率/Fed",
    "inflation_employment": "通胀/就业",
    "growth_economy": "增长/经济",
    "usd_liquidity": "美元/流动性",
    "oil_energy": "油价/能源",
    "war_geopolitics": "战争/地缘",
    "central_bank_gold": "央行购金",
    "risk_sentiment": "风险情绪 / 避险",
    "regulation_other": "监管/其他",
}
OTHER_NEWS_CATEGORY_LABEL = "其他"


def news_category_label(primary_category: object) -> str:
    """Map one completed semantic category without inferring workflow state."""
    category = str(primary_category or "").strip()
    return NEWS_CATEGORY_LABELS.get(category, OTHER_NEWS_CATEGORY_LABEL)


def not_required_reason(item: dict, forward_epoch: str) -> tuple[str, str]:
    """Explain the single reason a readable row will not consume AI quota."""
    published_raw = item.get("source_published_time")
    if not published_raw:
        return "HISTORICAL_MATERIAL", "历史资料：缺少可靠发布时间"
    published = datetime.fromisoformat(str(published_raw))
    epoch = datetime.fromisoformat(forward_epoch)
    if published < epoch:
        return "HISTORICAL_MATERIAL", "历史资料：发布时间早于系统开始记录"
    assessment = assess_news_semantic_eligibility(item, forward_epoch=epoch)
    if assessment.reason_code == "PUBLISHED_AFTER_DECISION":
        return "INVALID_PUBLISHED_TIME", "发布时间晚于收到时间，时间证据无效"
    if assessment.reason_code == "PUBLISHED_TIME_MISSING":
        return "HISTORICAL_MATERIAL", "历史资料：缺少可靠发布时间"
    if assessment.reason_code in {
        "PRE_FORWARD_PUBLICATION",
        "PRE_FORWARD_RECEIPT",
    }:
        return "HISTORICAL_MATERIAL", "历史资料：时间早于系统开始记录"
    if item.get("has_canonical_content_peer"):
        return (
            "CANONICAL_COPY_HANDLES_ANNOTATION",
            "同一篇新闻已由另一采集入口的规范副本负责处理，不会重复消耗模型配额",
        )
    if assessment.eligible:
        return "QUEUE_INVARIANT_MISMATCH", "正文符合条件但未进入语义队列，需要检查"
    return "INTAKE_REJECTED", "未通过客观采集条件，不进入语义处理"


def annotation_failure_reason(error: object, failure_code: object) -> str:
    """Explain a bounded model failure without exposing rejected output."""
    message = str(error or "")
    code = str(failure_code or "")
    if "supporting evidence is absent from source" in message:
        return "Gemini 返回的证据片段无法在来源正文中逐字找到。"
    if "supporting_evidence contains a long item" in message:
        return "Gemini 返回的证据片段超过允许长度。"
    if "display repair failed" in message:
        return "Gemini 的语义响应已收到，但中文展示字段修复仍未通过。"
    if code == "MODEL_OUTPUT_CONTRACT_FAILED":
        return "Gemini 响应未通过当前输出合同。"
    if code == "MODEL_OUTPUT_INVALID":
        return "Gemini 返回的内容无法解析为当前 JSON 合同。"
    if code == "PROVIDER_HTTP_ERROR":
        return "Gemini 服务返回 HTTP 错误。"
    return "Gemini 请求未成功完成；已保留有限诊断证据。"


def apply_impact_status(item: dict, now: datetime) -> None:
    """Expose the current Gemma lifetime decision in auditable states."""
    if not item.get("parsed_at"):
        item["impact_status"] = (
            "NOT_REQUIRED"
            if item.get("annotation_status") == "NOT_REQUIRED"
            else "PENDING_ANNOTATION"
        )
        return
    if not item.get("impact_assessed_at"):
        item["impact_status"] = "PENDING_IMPACT"
        return

    update_type = str(item.get("impact_update_type") or "")
    impact_class = str(item.get("impact_class") or "BACKGROUND")
    if not impact_is_actionable(
        {
            "impact_class": impact_class,
            "event_state": item.get("impact_event_state"),
            "update_type": update_type,
        }
    ):
        item["impact_status"] = {
            "DUPLICATE_REPORT": "DUPLICATE_REPORT",
            "COMMENTARY": "COMMENTARY_ONLY",
            "HISTORICAL_CONTEXT": "HISTORICAL_CONTEXT",
        }.get(update_type, "BACKGROUND")
        item["model_visibility"] = "MODEL_INELIGIBLE"
        return

    event_at, clock_source, _ = resolve_event_clock(item, primary_source=True)
    if event_at is None:
        item["impact_status"] = "MISSING_PUBLICATION_TIME"
        item["model_visibility"] = "MODEL_INELIGIBLE"
        return
    max_age, _ = impact_time_rule(impact_class)
    expires_at = event_at + max_age
    item["impact_event_at"] = event_at
    item["impact_clock_source"] = clock_source
    item["impact_expires_at"] = expires_at
    first_seen = datetime.fromisoformat(str(item["collector_first_seen_time"]))
    assessed_at = datetime.fromisoformat(str(item["impact_assessed_at"]))
    available_at = max(first_seen, assessed_at)
    item["impact_available_at"] = available_at
    canonicalize_news_projection_impact_clocks(item)
    if first_seen >= expires_at:
        item["impact_status"] = "EXPIRED_ON_RECEIPT"
        item["model_visibility"] = "IMPACT_EXPIRED"
    elif available_at >= expires_at:
        item["impact_status"] = "EXPIRED_BEFORE_AVAILABLE"
        item["model_visibility"] = "IMPACT_EXPIRED"
    elif now >= expires_at:
        item["impact_status"] = "EXPIRED"
        item["model_visibility"] = "IMPACT_EXPIRED"
    else:
        item["impact_status"] = "ACTIVE"
        item["model_visibility"] = "MODEL_VISIBLE"
