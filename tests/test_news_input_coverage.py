from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.decision import inference as inference_v2
from xauusd_forecaster.training import generation as training_v2
from xauusd_forecaster.news_input_coverage import classify_news_input_coverage
from xauusd_forecaster.news_input_coverage import news_source_observability_summary
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news_source_registry import NEWS_SOURCE_REGISTRY


NOW = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


def _news(*, core: int = 0, broad: int = 0) -> dict:
    return {
        "core_visible_events": [{"id": index} for index in range(core)],
        "broad_visible_events": [{"id": index} for index in range(broad)],
        "source_evidence_hash": "frozen-news-evidence",
    }


def _health(
    *reasons: str,
    annotations: int = 0,
    impacts: int = 0,
    recovering: int = 0,
    terminal_or_overdue: int = 0,
) -> dict:
    return {
        "status": "UNHEALTHY" if reasons else "HEALTHY",
        "reason_codes": reasons,
        "unresolved_annotation_count": annotations,
        "unresolved_impact_count": impacts,
        "recovering_count": recovering,
        "terminal_or_overdue_count": terminal_or_overdue,
        "snapshot_hash": "operational-health",
    }


def _sources(*, current: int = 12, degraded: int = 0, unavailable: int = 0) -> dict:
    return {
        "registered_source_count": current + unavailable,
        "observable_source_count": current,
        "degraded_source_count": degraded,
        "unavailable_source_count": unavailable,
        "observable_sources": (),
        "degraded_sources": (),
        "unavailable_sources": (),
        "source_poll_evidence_hash": "source-polls",
    }


@pytest.mark.parametrize("recovering", [1, 2, 10])
def test_usable_news_with_impact_recovery_is_degraded_not_blocked(
    recovering: int,
) -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(core=30, broad=30),
        operational_health=_health(
            "ACTIONABLE_NEWS_IMPACT_PENDING",
            "ACTIONABLE_NEWS_IMPACT_RECOVERING",
            impacts=recovering,
            recovering=recovering,
        ),
        source_observability=_sources(),
    )

    assert coverage["state"] == "DEGRADED"
    assert coverage["usable_broad_event_count"] == 30
    assert coverage["unresolved_impact_count"] == recovering
    assert coverage["recovering_count"] == recovering


def test_current_two_impact_recovering_shape_keeps_inference_open() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(core=30, broad=30),
        operational_health=_health(
            "ACTIONABLE_NEWS_IMPACT_PENDING",
            "ACTIONABLE_NEWS_IMPACT_RECOVERING",
            impacts=2,
            recovering=2,
        ),
        source_observability=_sources(),
    )

    assert coverage["state"] == "DEGRADED"
    assert coverage["unresolved_impact_count"] == 2
    assert coverage["recovering_count"] == 2
    assert coverage["operational_reason_codes"] == (
        "ACTIONABLE_NEWS_IMPACT_PENDING",
        "ACTIONABLE_NEWS_IMPACT_RECOVERING",
    )
    for identity in inference_v2.NEWS_MODEL_IDENTITIES:
        assert inference_v2._runtime_gate_status(
            identity, market_healthy=True,
            news_input_state=coverage["state"],
        ) is None
    assert inference_v2._runtime_gate_status(
        "MARKET_ONLY", market_healthy=True,
        news_input_state=coverage["state"],
    ) is None


def test_zero_news_with_two_recovering_items_remains_degraded_and_learnable() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(),
        operational_health=_health(
            "ACTIONABLE_NEWS_IMPACT_PENDING",
            "ACTIONABLE_NEWS_IMPACT_RECOVERING",
            impacts=2,
            recovering=2,
        ),
        source_observability=_sources(),
    )

    assert coverage["state"] == "DEGRADED"
    assert coverage["usable_broad_event_count"] == 0
    assert training_v2.news_input_state_is_training_eligible(
        coverage["state"]
    ) is True
    for identity in inference_v2.NEWS_MODEL_IDENTITIES:
        assert inference_v2._runtime_gate_status(
            identity, market_healthy=True,
            news_input_state=coverage["state"],
        ) is None


def test_partial_source_failure_with_usable_news_is_degraded() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(core=5, broad=8),
        operational_health=_health(),
        source_observability=_sources(current=11, unavailable=1),
    )

    assert coverage["state"] == "DEGRADED"
    assert "NEWS_SOURCES_PARTIALLY_OBSERVABLE" in coverage["coverage_reason_codes"]


def test_healthy_observable_zero_news_is_quiet() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(), operational_health=_health(),
        source_observability=_sources(),
    )

    assert coverage["state"] == "QUIET"


def test_zero_news_during_observation_outage_is_unavailable() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(),
        operational_health=_health(
            "ANNOTATOR_HEARTBEAT_STALE", "NEWS_COLLECTOR_POLL_STALE",
        ),
        source_observability=_sources(current=0, unavailable=12),
    )

    assert coverage["state"] == "UNAVAILABLE"


def test_old_usable_event_during_total_source_outage_is_unavailable() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(broad=1),
        operational_health=_health(),
        source_observability=_sources(current=0, unavailable=12),
    )

    assert coverage["state"] == "UNAVAILABLE"
    assert coverage["usable_broad_event_count"] == 1
    assert training_v2.news_input_state_is_training_eligible(
        coverage["state"]
    ) is False
    for identity in inference_v2.NEWS_MODEL_IDENTITIES:
        assert inference_v2._runtime_gate_status(
            identity, market_healthy=True,
            news_input_state=coverage["state"],
        ) == "NEWS_INPUT_UNAVAILABLE"
    assert inference_v2._runtime_gate_status(
        "MARKET_ONLY", market_healthy=True,
        news_input_state=coverage["state"],
    ) is None


def test_terminal_item_does_not_hide_other_usable_evidence() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(core=20, broad=30),
        operational_health=_health(
            "ACTIONABLE_NEWS_IMPACT_PENDING",
            "ACTIONABLE_NEWS_IMPACT_TERMINAL",
            impacts=1,
            terminal_or_overdue=1,
        ),
        source_observability=_sources(),
    )

    assert coverage["state"] == "DEGRADED"
    assert coverage["terminal_or_overdue_count"] == 1


def test_zero_news_with_partial_source_coverage_continues_degraded() -> None:
    coverage = classify_news_input_coverage(
        news_snapshot=_news(), operational_health=_health(),
        source_observability=_sources(current=10, unavailable=2),
    )

    assert coverage["state"] == "DEGRADED"


def test_later_source_poll_never_changes_older_observability(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW - timedelta(days=1))
    source = NEWS_SOURCE_REGISTRY[0].source
    ledger.append_source_poll({
        "poll_id": "future-poll", "source": source,
        "fetched_time": NOW + timedelta(minutes=1), "status": "OK",
    })

    before = news_source_observability_summary(
        ledger.connection, observed_at=NOW,
    )
    after = news_source_observability_summary(
        ledger.connection, observed_at=NOW + timedelta(minutes=1),
    )

    assert source in before["unavailable_sources"]
    assert source not in before["observable_sources"]
    assert source in after["observable_sources"]


def test_source_evidence_hash_binds_latest_usable_poll(tmp_path) -> None:
    ledgers = [
        ForwardLedger(
            tmp_path / f"forward-{index}.sqlite3",
            now=NOW - timedelta(days=1),
        )
        for index in range(2)
    ]
    source = NEWS_SOURCE_REGISTRY[0].source
    for index, ledger in enumerate(ledgers):
        ledger.append_source_poll({
            "poll_id": f"usable-{index}", "source": source,
            "fetched_time": NOW - timedelta(minutes=5 - index), "status": "OK",
        })
        ledger.append_source_poll({
            "poll_id": f"latest-{index}", "source": source,
            "fetched_time": NOW - timedelta(minutes=1), "status": "ERROR",
        })

    summaries = [
        news_source_observability_summary(
            ledger.connection, observed_at=NOW,
        )
        for ledger in ledgers
    ]

    assert summaries[0]["observable_sources"] == summaries[1]["observable_sources"]
    assert summaries[0]["degraded_sources"] == summaries[1]["degraded_sources"]
    assert (
        summaries[0]["source_poll_evidence_hash"]
        != summaries[1]["source_poll_evidence_hash"]
    )
