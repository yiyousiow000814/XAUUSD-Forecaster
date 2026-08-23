import ast
import re
from pathlib import Path

from xauusd_forecaster.news.brief.product import GENERATION_FAILURE_CODES
from xauusd_forecaster.news.retrieval.gemini_embeddings import GEMINI_EMBEDDING_FAILURE_CODES
from xauusd_forecaster.runtime.taxonomy import (
    INTENTIONALLY_UNCORRELATED_FAILURE_CODES,
    normalize_operational_event,
    operational_code_index,
    operational_code_registry,
    validate_operational_code_registry,
)


ROOT = Path(__file__).resolve().parents[1]
FAILURE_CODE_SOURCES = (
    "xauusd_forecaster/gemini_embeddings.py",
    "xauusd_forecaster/news_retrieval.py",
    "xauusd_forecaster/news_scheduler.py",
    "xauusd_forecaster/model_gateway.py",
    "xauusd_forecaster/scheduler_model_gateway.py",
    "xauusd_forecaster/daily_brief.py",
    "xauusd_forecaster/operational_health.py",
    "scripts/run_news_annotator.py",
)
FAILURE_FIELDS = {"failure_code", "latest_failure_code", "dominant_failure_code"}


def _stable_literals(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and re.fullmatch(r"[A-Z][A-Z0-9_]+", child.value)
    }


def _target_names(node: ast.AST) -> set[str]:
    return {
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute))
    }


def _published_failure_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    published: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in FAILURE_FIELDS:
                    published.update(_stable_literals(keyword.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in FAILURE_FIELDS:
                    published.update(_stable_literals(value))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = set().union(*(_target_names(target) for target in targets))
            value = node.value
            if value is not None and (
                names & FAILURE_FIELDS
                or any(name.endswith("FAILURE_CODES") for name in names)
            ):
                published.update(_stable_literals(value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = [*node.args.posonlyargs, *node.args.args]
            defaults = node.args.defaults
            if not defaults:
                continue
            for argument, default in zip(
                positional[-len(defaults):], defaults, strict=True,
            ):
                if argument.arg in FAILURE_FIELDS:
                    published.update(_stable_literals(default))
    return published


def _emitted_alert_calls(path: Path) -> list[tuple[set[str], set[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    emitted: list[tuple[set[str], set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name != "_alert" or not node.args:
            continue
        severity_node = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "severity"),
            None,
        )
        if severity_node is None:
            continue
        codes = _stable_literals(node.args[0])
        severities = _stable_literals(severity_node) & {"ERROR", "WARNING", "INFO"}
        if codes and severities:
            emitted.append((codes, severities))
    return emitted


def test_operational_registry_is_valid_and_all_published_codes_are_registered() -> None:
    assert validate_operational_code_registry() == []
    registered = operational_code_index()
    emitted: set[str] = set()
    for base in (ROOT / "xauusd_forecaster", ROOT / "scripts", ROOT / "web" / "app"):
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            emitted.update(re.findall(r"\bOPS_[A-Z0-9_]+\b", path.read_text(encoding="utf-8")))
    assert emitted <= registered.keys()
    assert all(registered[code]["kind"] == "ALERT" for code in emitted)


def test_registry_covers_correlation_failure_and_health_reasons() -> None:
    registered = operational_code_index()
    for code in {
        "MODEL_CAPACITY_DEFERRED", "PROVIDER_DISPATCH_DEFERRED",
        "MODEL_OUTPUT_CONTRACT_FAILED", "MODEL_OUTPUT_INVALID",
        "NEWS_EMBEDDING_BACKFILL_PENDING",
    }:
        assert registered[code]["kind"] == "FAILURE_REASON"
    for code in {
        "ACTIONABLE_NEWS_SEMANTICS_PENDING", "ACTIONABLE_NEWS_IMPACT_PENDING",
        "ACTIONABLE_NEWS_SEMANTICS_RECOVERING", "ACTIONABLE_NEWS_IMPACT_RECOVERING",
        "ACTIONABLE_NEWS_SEMANTICS_TERMINAL", "ACTIONABLE_NEWS_IMPACT_TERMINAL",
        "ACTIONABLE_NEWS_SEMANTICS_OVERDUE", "ACTIONABLE_NEWS_IMPACT_OVERDUE",
        "ANNOTATOR_HEARTBEAT_STALE", "MODEL_CREDENTIALS_UNAVAILABLE",
        "NEWS_COLLECTOR_POLL_STALE",
    }:
        assert registered[code]["kind"] == "HEALTH_REASON"


def test_adjacent_stable_failure_code_families_cannot_escape_registry() -> None:
    registered = operational_code_index()
    published = set(GENERATION_FAILURE_CODES) | set(GEMINI_EMBEDDING_FAILURE_CODES)
    for relative in FAILURE_CODE_SOURCES:
        published.update(_published_failure_literals(ROOT / relative))
    assert INTENTIONALLY_UNCORRELATED_FAILURE_CODES == {"UNCLASSIFIED"}
    assert published - INTENTIONALLY_UNCORRELATED_FAILURE_CODES <= registered.keys()
    assert all(
        registered[code]["kind"] == "FAILURE_REASON"
        for code in published - INTENTIONALLY_UNCORRELATED_FAILURE_CODES
    )


def test_python_and_typescript_consume_the_same_registry_revision() -> None:
    registry = operational_code_registry()
    source = (ROOT / "web" / "app" / "_lib" / "operational-health.ts").read_text(encoding="utf-8")
    assert registry["schema_version"] == "operational-code-registry.v1"
    assert '../../../xauusd_forecaster/operational_codes.json' in source
    assert "OPERATIONAL_CODE_REGISTRY_VERSION = operationalCodeRegistry.schema_version" in source


def test_unknown_operational_event_remains_visible_with_taxonomy_error() -> None:
    event = normalize_operational_event(
        "OPS_FUTURE_UNREGISTERED", severity="ERROR", scope="test",
        message_zh="未知运行问题", evidence={"count": 1}, blocking=True,
    )
    assert event["code"] == "OPS_FUTURE_UNREGISTERED"
    assert event["blocking"] is True
    assert event["evidence"] == {
        "count": 1,
        "taxonomy_error": "UNREGISTERED_OPERATIONAL_CODE:OPS_FUTURE_UNREGISTERED",
    }


def test_disallowed_emitted_severity_fails_visibly_instead_of_hiding_event() -> None:
    event = normalize_operational_event(
        "OPS_AI_ROUTE_CAPACITY_SATURATED", severity="ERROR",
        scope="ACTIVE_IMPACT", message_zh="容量异常", evidence={}, blocking=True,
    )
    assert event["code"] == "OPS_AI_ROUTE_CAPACITY_SATURATED"
    assert event["severity"] == "ERROR"
    assert event["blocking"] is True
    assert event["evidence"]["taxonomy_error"] == (
        "SEVERITY_NOT_ALLOWED:OPS_AI_ROUTE_CAPACITY_SATURATED:ERROR"
    )


def test_current_python_operational_emitters_use_allowed_severities() -> None:
    registered = operational_code_index()
    emitted = _emitted_alert_calls(
        ROOT / "xauusd_forecaster" / "runtime" / "operational_health.py"
    )
    assert emitted
    for codes, severities in emitted:
        if len(codes) == 1:
            code = next(iter(codes))
            assert severities <= set(registered[code]["allowed_severities"])
        else:
            # A conditional code paired with a conditional severity must retain
            # at least one valid branch for every code and every severity.
            assert all(
                severities & set(registered[code]["allowed_severities"])
                for code in codes
            )
            assert all(
                any(severity in registered[code]["allowed_severities"] for code in codes)
                for severity in severities
            )
