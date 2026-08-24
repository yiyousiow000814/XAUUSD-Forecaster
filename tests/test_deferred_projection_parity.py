from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "check_deferred_projection_parity.py"
    spec = importlib.util.spec_from_file_location("deferred_projection_parity", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _authority(generated_at: str) -> dict:
    return {
        "generated_at": generated_at,
        "daily_news_briefs": [],
        "recent_decisions": [],
        "storylines": [],
        "market_narrative_candidates": [],
        "archived_storylines": [],
        "archived_story_event_candidates": [],
        "story_event_candidates": [],
        "market_reaction_streams": [],
        "theme_streams": [],
        "unassigned_story_events": [],
        "storyline_summary": {},
    }


def test_exact_candidate_projection_semantics_pass(monkeypatch) -> None:
    module = _module()
    revision = "b" * 40
    version = "11111111-1111-4111-8111-111111111111"
    generated = datetime.now(UTC)
    authority = _authority(generated.isoformat())
    responses = [(authority, {})]
    for route in module.BUILDERS:
        payload = __import__("json").loads(
            module.BUILDERS[route](authority, revision).decode("utf-8")
        )
        responses.append((payload, {
            "X-Aurum-Worker-Version": version,
            "X-Aurum-Git-SHA": revision,
        }))
    monkeypatch.setattr(module, "_read_json", lambda *_a, **_k: responses.pop(0))
    result = module.verify(
        local_audit_url="http://local/api/audit",
        remote_base_url="https://worker.example",
        worker_name="worker", version_id=version, git_sha=revision,
        producer_revision=revision, routes=list(module.BUILDERS),
        required_after=generated - timedelta(seconds=1),
    )
    assert result["state"] == "PASSED"
    assert all(item["state"] == "PASSED" for item in result["routes"])


def test_old_producer_projection_remains_pending(monkeypatch) -> None:
    module = _module()
    revision = "b" * 40
    version = "11111111-1111-4111-8111-111111111111"
    generated = datetime.now(UTC)
    authority = _authority(generated.isoformat())
    route = "/api/audit-stories"
    observed = __import__("json").loads(
        module.BUILDERS[route](authority, "a" * 40).decode("utf-8")
    )
    responses = [
        (authority, {}),
        (observed, {"X-Aurum-Worker-Version": version,
                    "X-Aurum-Git-SHA": revision}),
    ]
    monkeypatch.setattr(module, "_read_json", lambda *_a, **_k: responses.pop(0))
    result = module.verify(
        local_audit_url="http://local/api/audit",
        remote_base_url="https://worker.example",
        worker_name="worker", version_id=version, git_sha=revision,
        producer_revision=revision, routes=[route],
        required_after=generated - timedelta(seconds=1),
    )
    assert result["state"] == "PENDING"
    assert result["reason"] == "CANDIDATE_PROJECTION_PRODUCER_PENDING"
