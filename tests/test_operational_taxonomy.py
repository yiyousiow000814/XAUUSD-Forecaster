import re
from pathlib import Path

from xauusd_forecaster.operational_taxonomy import (
    normalize_operational_event,
    operational_code_index,
    operational_code_registry,
    validate_operational_code_registry,
)


ROOT = Path(__file__).resolve().parents[1]


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
        "ANNOTATOR_HEARTBEAT_STALE", "MODEL_CREDENTIALS_UNAVAILABLE",
        "NEWS_COLLECTOR_POLL_STALE",
    }:
        assert registered[code]["kind"] == "HEALTH_REASON"


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
