from __future__ import annotations

import json

from scripts.build_release_validation_fixtures import build_fixtures


def test_release_validation_fixtures_are_bounded_production_contracts() -> None:
    fixtures = build_fixtures()
    assert set(fixtures) == {
        "status-ingest.json", "audit-write.json", "learning-write.json",
        "market-chart-write.json", "market-history-write.json",
        "learning-history-write.json", "news-evidence-write.json",
        "news-index-write.json",
    }
    decoded = {name: json.loads(payload) for name, payload in fixtures.items()}
    assert len(fixtures["audit-write.json"]) >= 300_000
    assert len(fixtures["learning-write.json"]) >= 150_000
    assert 300_000 <= len(fixtures["market-chart-write.json"]) <= 750_000
    assert 300_000 <= len(fixtures["market-history-write.json"]) <= 400_000
    assert 250_000 <= len(fixtures["learning-history-write.json"]) <= 350_000
    assert len(fixtures["news-evidence-write.json"]) <= 400_000
    assert len(fixtures["news-index-write.json"]) <= 400_000
    assert len(decoded["market-history-write.json"].get("candles", [])) <= 500
    assert len(decoded["market-history-write.json"].get("decisions", [])) <= 2_500
    assert 1 <= len(decoded["learning-history-write.json"]["records"]) <= 1_000
    assert len(decoded["news-evidence-write.json"]["items"]) == 20
    assert 1 <= len(decoded["news-index-write.json"]["items"]) <= 20
