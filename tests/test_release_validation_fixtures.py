from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from fnmatch import fnmatch
from pathlib import Path

import pytest

from scripts.build_release_validation_fixtures import _news, build_fixtures
from xauusd_forecaster.news_projection import (
    NEWS_PROJECTION_IMPACT_CLOCK_FIELDS,
    canonicalize_news_projection_impact_clocks,
)


def test_release_validation_fixtures_are_bounded_production_contracts() -> None:
    fixtures = build_fixtures()
    manifest = json.loads((
        Path(__file__).resolve().parents[1] / "web" / "worker-validation-manifest.json"
    ).read_text(encoding="utf-8"))
    expected = set()
    for route in manifest["routes"]:
        if route.get("fixture"):
            expected.add(route["fixture"])
        expected.update(
            scenario["fixture"] for scenario in route.get("scenarios", [])
            if scenario.get("fixture")
        )
    assert set(fixtures) == expected
    decoded = {name: json.loads(payload) for name, payload in fixtures.items()}
    for name, payload in fixtures.items():
        limits = [limit for pattern, limit in manifest["fixture_contracts"].items()
                  if fnmatch(name, pattern)]
        assert len(limits) == 1, f"missing exact fixture bound for {name}"
        assert 0 < len(payload) <= limits[0]
        assert payload == json.dumps(
            decoded[name], ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
    assert len(decoded["market-history-write.json"].get("candles", [])) <= 500
    assert len(decoded["market-history-write.json"].get("decisions", [])) <= 2_500
    assert 1 <= len(decoded["learning-history-write.json"]["records"]) <= 1_000
    assert len(decoded["news-evidence-stage.json"]["items"]) == 8
    assert decoded["news-index-prepare.json"]["manifest"]["expected_index_count"] == 200
    assert 1 <= len(decoded["news-index-stage.json"]["items"]) <= 4
    assert len(fixtures["news-index-stage.json"]) <= 100_000
    assert decoded["news-content-stage.json"]["action"] == "stage_details"
    assert 1 <= len(decoded["news-content-stage.json"]["items"]) <= 8
    assert len(fixtures["news-evidence-stage.json"]) <= 80_000
    assert len(fixtures["audit-write.json"]) <= 16_000
    for name in (
        "audit-briefs-write.json", "audit-stories-write.json",
        "audit-decisions-write.json",
    ):
        assert len(fixtures[name]) <= 120_000


def test_release_news_fixtures_use_production_impact_clock_contract() -> None:
    zero = _news(0)["impact_expires_at"]
    nonzero = _news(1)["impact_expires_at"]
    non_utc_source = _news(
        2,
        impact_expiry=datetime(
            2026, 8, 13, 14, 2, tzinfo=timezone(timedelta(hours=8)),
        ),
    )["impact_expires_at"]

    assert zero == "2026-08-13T06:00:00.000000+00:00"
    assert nonzero == "2026-08-13T06:01:00.123456+00:00"
    assert non_utc_source == "2026-08-13T06:02:00.000000+00:00"
    assert set(NEWS_PROJECTION_IMPACT_CLOCK_FIELDS) == {
        "impact_event_at", "impact_available_at", "impact_expires_at",
    }
    assert {
        field for field in NEWS_PROJECTION_IMPACT_CLOCK_FIELDS if field in _news(0)
    } == {"impact_expires_at"}


@pytest.mark.parametrize("value", [
    "not-a-timestamp",
    "2026-08-13T06:00:00.000000",
])
def test_release_news_fixture_rejects_invalid_impact_clock(value: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_news_projection_impact_clocks({"impact_expires_at": value})


def test_release_fixture_generation_identities_hashes_and_checked_bytes_are_deterministic() -> None:
    first = build_fixtures()
    second = build_fixtures()
    golden_root = (
        Path(__file__).parent
        / "fixtures"
        / "release_validation"
    )
    golden = {
        path.name: path.read_bytes()
        for path in golden_root.glob("*.json")
    }

    assert first == second
    assert golden == first
    assert {
        name: sha256(payload).hexdigest() for name, payload in first.items()
    } == {
        name: sha256(payload).hexdigest() for name, payload in second.items()
    }
    first_stage = json.loads(first["news-index-stage.json"])
    second_stage = json.loads(second["news-index-stage.json"])
    assert first_stage["generation_id"] == second_stage["generation_id"]
    assert sha256(first["news-index-stage.json"]).digest() == sha256(
        second["news-index-stage.json"]
    ).digest()
