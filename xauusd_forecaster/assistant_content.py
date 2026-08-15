"""Validated, provider-independent rich content for canonical Assistant messages."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import urllib.parse
from datetime import UTC, datetime
from typing import Final


ASSISTANT_CONTENT_PROTOCOL_VERSION: Final = "assistant.content.v1"
MAX_ASSISTANT_CONTENT_BLOCKS: Final = 12
MAX_ASSISTANT_CONTENT_BYTES: Final = 65_536
MAX_ASSISTANT_NEWS_CARDS: Final = 4

_BLOCK_TYPES = frozenset({"markdown", "news_card", "table", "metric", "callout"})
_BLOCK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_COLUMN_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
_UNSAFE_TEXT = frozenset({"\x00", "\x0b", "\x0c"})


class AssistantContentContractError(ValueError):
    """Structured output cannot be persisted or rendered safely."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise AssistantContentContractError("Assistant content is not strict JSON") from error


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _strict_object(value: object, keys: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise AssistantContentContractError(f"{label} fields are invalid")
    return value


def _text(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
    multiline: bool = False,
) -> str:
    if not isinstance(value, str):
        raise AssistantContentContractError(f"{label} is invalid")
    if any(character in value for character in _UNSAFE_TEXT):
        raise AssistantContentContractError(f"{label} contains unsafe controls")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not multiline and ("\n" in normalized or "\t" in normalized):
        raise AssistantContentContractError(f"{label} must be one line")
    if not minimum <= len(normalized) <= maximum:
        raise AssistantContentContractError(f"{label} length is invalid")
    return normalized


def _nullable_text(value: object, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _time(value: object, label: str) -> str | None:
    if value is None:
        return None
    timestamp = _text(value, label, minimum=24, maximum=24)
    if not _CANONICAL_TIME.fullmatch(timestamp):
        raise AssistantContentContractError(f"{label} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise AssistantContentContractError(f"{label} is invalid") from error
    if parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z",
    ) != timestamp:
        raise AssistantContentContractError(f"{label} is invalid")
    return timestamp


def _https_url(value: object) -> str | None:
    if value is None:
        return None
    url = _text(value, "news_card.source_url", minimum=1, maximum=2_048)
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AssistantContentContractError("news_card.source_url must be public HTTPS")
    return url


def _cell(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 10**15:
            raise AssistantContentContractError("table integer is out of range")
        return value
    if isinstance(value, float):
        raise AssistantContentContractError(
            "table decimals must be formatted as bounded strings"
        )
    if isinstance(value, str):
        return _text(value, "table cell", maximum=500, multiline=True)
    raise AssistantContentContractError("table cell type is invalid")


def _validate_data(block_type: str, value: object) -> dict[str, object]:
    if block_type == "markdown":
        data = _strict_object(value, frozenset({"text"}), "markdown")
        return {"text": _text(
            data["text"], "markdown.text", minimum=1, maximum=32_000, multiline=True,
        )}
    if block_type == "news_card":
        data = _strict_object(value, frozenset({
            "evidence_id", "source", "published_at", "received_at", "headline",
            "summary", "category", "impact", "relevance", "source_url",
        }), "news_card")
        evidence_id = _text(
            data["evidence_id"], "news_card.evidence_id", minimum=1, maximum=128,
        )
        if not _EVIDENCE_ID.fullmatch(evidence_id):
            raise AssistantContentContractError("news_card.evidence_id is invalid")
        return {
            "evidence_id": evidence_id,
            "source": _text(data["source"], "news_card.source", maximum=100),
            "published_at": _time(data["published_at"], "news_card.published_at"),
            "received_at": _time(data["received_at"], "news_card.received_at"),
            "headline": _text(
                data["headline"], "news_card.headline", minimum=1, maximum=300,
            ),
            "summary": _text(
                data["summary"], "news_card.summary", maximum=600, multiline=True,
            ),
            "category": _text(data["category"], "news_card.category", maximum=80),
            "impact": _text(
                data["impact"], "news_card.impact", maximum=600, multiline=True,
            ),
            "relevance": _nullable_text(data["relevance"], "news_card.relevance", 600),
            "source_url": _https_url(data["source_url"]),
        }
    if block_type == "table":
        data = _strict_object(value, frozenset({"caption", "columns", "rows"}), "table")
        columns = data["columns"]
        rows = data["rows"]
        if not isinstance(columns, list) or not 1 <= len(columns) <= 6:
            raise AssistantContentContractError("table columns are invalid")
        normalized_columns: list[dict[str, str]] = []
        keys: set[str] = set()
        for raw in columns:
            column = _strict_object(raw, frozenset({"key", "label", "align"}), "table column")
            key = _text(column["key"], "table column key", minimum=1, maximum=32)
            align = _text(column["align"], "table column align", minimum=1, maximum=8)
            if not _COLUMN_KEY.fullmatch(key) or key in keys or align not in {"left", "right", "center"}:
                raise AssistantContentContractError("table column is invalid")
            keys.add(key)
            normalized_columns.append({
                "key": key,
                "label": _text(
                    column["label"], "table column label", minimum=1, maximum=80,
                ),
                "align": align,
            })
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise AssistantContentContractError("table rows are invalid")
        normalized_rows: list[list[object]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(normalized_columns):
                raise AssistantContentContractError("table row width is invalid")
            normalized_rows.append([_cell(item) for item in row])
        return {
            "caption": _nullable_text(data["caption"], "table.caption", 160),
            "columns": normalized_columns,
            "rows": normalized_rows,
        }
    if block_type == "metric":
        data = _strict_object(
            value, frozenset({"label", "value", "unit", "trend", "detail"}), "metric",
        )
        trend = _text(data["trend"], "metric.trend", minimum=2, maximum=7)
        if trend not in {"UP", "DOWN", "FLAT", "UNKNOWN"}:
            raise AssistantContentContractError("metric.trend is invalid")
        return {
            "label": _text(data["label"], "metric.label", minimum=1, maximum=80),
            "value": _text(data["value"], "metric.value", minimum=1, maximum=80),
            "unit": _nullable_text(data["unit"], "metric.unit", 32),
            "trend": trend,
            "detail": _nullable_text(data["detail"], "metric.detail", 240),
        }
    if block_type == "callout":
        data = _strict_object(value, frozenset({"tone", "title", "body"}), "callout")
        tone = _text(data["tone"], "callout.tone", minimum=4, maximum=32)
        if tone not in {"INFO", "WARNING", "INSUFFICIENT_EVIDENCE", "BOUNDARY"}:
            raise AssistantContentContractError("callout.tone is invalid")
        return {
            "tone": tone,
            "title": _text(data["title"], "callout.title", minimum=1, maximum=120),
            "body": _text(
                data["body"], "callout.body", minimum=1, maximum=1_000, multiline=True,
            ),
        }
    raise AssistantContentContractError("Assistant content block type is unsupported")


def validate_assistant_content_document(
    value: object,
    *,
    answer: str,
    evidence_ids: tuple[str, ...] | list[str] = (),
) -> dict[str, object]:
    """Return a detached strict document after checking content and cryptographic binding."""
    document = _strict_object(
        value, frozenset({"protocol", "blocks", "document_sha256"}), "content document",
    )
    if document["protocol"] != ASSISTANT_CONTENT_PROTOCOL_VERSION:
        raise AssistantContentContractError("Assistant content protocol is unsupported")
    blocks = document["blocks"]
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_ASSISTANT_CONTENT_BLOCKS:
        raise AssistantContentContractError("Assistant content block count is invalid")
    allowed_evidence = set(evidence_ids)
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    seen_news: set[str] = set()
    for raw in blocks:
        block = _strict_object(
            raw,
            frozenset({"id", "type", "version", "data", "content_sha256"}),
            "content block",
        )
        block_id = _text(block["id"], "content block id", minimum=1, maximum=128)
        block_type = _text(block["type"], "content block type", minimum=1, maximum=32)
        if (
            not _BLOCK_ID.fullmatch(block_id)
            or block_id in seen_ids
            or block_type not in _BLOCK_TYPES
            or block["version"] != "v1"
        ):
            raise AssistantContentContractError("Assistant content block identity is invalid")
        seen_ids.add(block_id)
        data = _validate_data(block_type, block["data"])
        if block_type == "news_card":
            evidence_id = str(data["evidence_id"])
            if evidence_id not in allowed_evidence or evidence_id in seen_news:
                raise AssistantContentContractError("news_card evidence is not in turn provenance")
            seen_news.add(evidence_id)
        core = {"id": block_id, "type": block_type, "version": "v1", "data": data}
        content_hash = block["content_sha256"]
        if not isinstance(content_hash, str) or not _SHA256.fullmatch(content_hash):
            raise AssistantContentContractError("Assistant content block hash is invalid")
        if content_hash != _sha256(core):
            raise AssistantContentContractError("Assistant content block hash does not match")
        normalized.append({**core, "content_sha256": content_hash})
    if normalized[0]["type"] != "markdown" or normalized[0]["data"] != {"text": answer}:
        raise AssistantContentContractError("First markdown block must equal the canonical answer")
    core_document = {
        "protocol": ASSISTANT_CONTENT_PROTOCOL_VERSION,
        "blocks": normalized,
    }
    document_hash = document["document_sha256"]
    if (
        not isinstance(document_hash, str)
        or not _SHA256.fullmatch(document_hash)
        or document_hash != _sha256(core_document)
    ):
        raise AssistantContentContractError("Assistant content document hash does not match")
    normalized_document = {**core_document, "document_sha256": document_hash}
    if len(_canonical_json(normalized_document).encode("utf-8")) > MAX_ASSISTANT_CONTENT_BYTES:
        raise AssistantContentContractError("Assistant content document exceeds its bound")
    return copy.deepcopy(normalized_document)


def _canonical_optional_time(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _bounded(value: object, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _safe_source_url(value: object) -> str | None:
    raw = str(value or "").strip()[:2_048]
    if not raw:
        return None
    try:
        return _https_url(raw)
    except AssistantContentContractError:
        return None


def _block(block_id: str, block_type: str, data: dict[str, object]) -> dict[str, object]:
    core = {"id": block_id, "type": block_type, "version": "v1", "data": data}
    return {**core, "content_sha256": _sha256(core)}


def build_assistant_content_document(
    answer: str,
    *,
    evidence_items: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
    evidence_ids: tuple[str, ...] | list[str] = (),
    retrieval_cutoff: str | None = None,
) -> dict[str, object]:
    """Build deterministic rich output from a final answer and authoritative tool packets."""
    canonical_answer = _text(
        answer, "Assistant answer", minimum=1, maximum=32_000, multiline=True,
    )
    allowed = tuple(dict.fromkeys(str(item) for item in evidence_ids))
    allowed_set = set(allowed)
    cards: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        evidence_id = _bounded(item.get("evidence_id"), 128)
        headline = _bounded(item.get("headline"), 300)
        if (
            not _EVIDENCE_ID.fullmatch(evidence_id)
            or evidence_id not in allowed_set
            or evidence_id in seen
            or not headline
        ):
            continue
        seen.add(evidence_id)
        cards.append({
            "evidence_id": evidence_id,
            "source": _bounded(item.get("source"), 100),
            "published_at": _canonical_optional_time(item.get("published_at")),
            "received_at": _canonical_optional_time(item.get("received_at")),
            "headline": headline,
            "summary": _bounded(item.get("summary"), 600),
            "category": _bounded(item.get("category"), 80),
            "impact": _bounded(item.get("impact"), 600),
            "relevance": None,
            "source_url": _safe_source_url(item.get("source_url")),
        })
        if len(cards) >= MAX_ASSISTANT_NEWS_CARDS:
            break

    blocks = [_block("block:answer", "markdown", {"text": canonical_answer})]
    if cards:
        cutoff = _canonical_optional_time(retrieval_cutoff)
        blocks.append(_block("block:metric:evidence", "metric", {
            "label": "本轮检索证据",
            "value": str(len(allowed)),
            "unit": "条",
            "trend": "UNKNOWN",
            "detail": f"检索截止 {cutoff}" if cutoff else "只读证据检索结果",
        }))
        for index, card in enumerate(cards, start=1):
            digest = hashlib.sha256(str(card["evidence_id"]).encode("utf-8")).hexdigest()[:12]
            blocks.append(_block(f"block:news:{index}:{digest}", "news_card", card))
        if len(cards) >= 2:
            blocks.append(_block("block:table:evidence-times", "table", {
                "caption": "本轮检索证据时间",
                "columns": [
                    {"key": "source", "label": "来源", "align": "left"},
                    {"key": "published", "label": "发布时间", "align": "left"},
                    {"key": "received", "label": "系统收到", "align": "left"},
                ],
                "rows": [[
                    card["source"], card["published_at"], card["received_at"],
                ] for card in cards],
            }))
    blocks.append(_block("block:boundary", "callout", {
        "tone": "BOUNDARY",
        "title": "决策支持边界",
        "body": "该回答不会下单、执行交易或自动晋升模型；请按证据时间与来源自行判断。",
    }))
    core = {"protocol": ASSISTANT_CONTENT_PROTOCOL_VERSION, "blocks": blocks}
    document = {**core, "document_sha256": _sha256(core)}
    return validate_assistant_content_document(
        document, answer=canonical_answer, evidence_ids=allowed,
    )
