from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path

from scripts.build_release_validation_fixtures import build_fixtures


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
    assert len(decoded["news-evidence-stage.json"]["items"]) == 20
    assert decoded["news-index-prepare.json"]["manifest"]["expected_index_count"] == 200
    assert 1 <= len(decoded["news-index-stage.json"]["items"]) <= 20
    assert decoded["news-content-stage.json"]["action"] == "stage_details"
    assert 1 <= len(decoded["news-content-stage.json"]["items"]) <= 20
    assert len(fixtures["audit-write.json"]) <= 16_000
    for name in (
        "audit-briefs-write.json", "audit-stories-write.json",
        "audit-decisions-write.json",
    ):
        assert len(fixtures[name]) <= 120_000
