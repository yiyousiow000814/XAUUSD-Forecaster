from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from xauusd_forecaster.dashboard.sync import transport as module
from xauusd_forecaster.dashboard.sync.progress import sync_error_code


def test_remote_write_rejection_preserves_declared_error_code(monkeypatch) -> None:
    body = io.BytesIO(json.dumps({
        "error_code": "NEWS_MIRROR_STATE_INVARIANT_VIOLATION",
        "violation_count": 1,
        "checks": [{"code": "NEWS_REVIEW_STATE_INVALID", "count": 1}],
    }).encode())
    monkeypatch.setattr(
        module.urllib.request, "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.HTTPError(
            "https://worker.example/api/news-index", 409, "Conflict", {}, body,
        )),
    )

    with pytest.raises(module.RemoteInvariantViolation) as captured:
        module._post_json(
            "https://worker.example/api/news-index", b"{}", {"token": "test"},
        )

    assert sync_error_code(captured.value) == (
        "NEWS_MIRROR_STATE_INVARIANT_VIOLATION"
    )


def test_configured_targets_adds_independent_cloudflare_mirror(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(
        "CLOUDFLARE_INGEST_URL", "https://example.workers.dev/api/ingest"
    )
    monkeypatch.setenv("CLOUDFLARE_INGEST_TOKEN", "cloudflare-token")
    config = {
        "remote_ingest_url": "https://example.chatgpt.site/api/ingest",
        "token": "sites-token",
        "learning_state_file": str(tmp_path / "learning.json"),
        "news_state_file": str(tmp_path / "news.json"),
        "market_history_state_file": str(tmp_path / "market-history.json"),
        "learning_history_state_file": str(tmp_path / "learning-history.json"),
        "news_evidence_state_file": str(tmp_path / "news-evidence.json"),
        "resource_schedule_state_file": str(tmp_path / "schedule.json"),
    }

    sites, cloudflare = module.configured_targets(
        module.configure_runtime_state(config, tmp_path)
    )

    assert sites["name"] == "sites"
    assert sites["learning_state_file"].endswith("learning.json")
    assert cloudflare["name"] == "cloudflare"
    assert cloudflare["remote_ingest_url"].endswith("workers.dev/api/ingest")
    assert cloudflare["learning_state_file"].endswith("learning-cloudflare.json")
    assert cloudflare["news_state_file"].endswith("news-cloudflare.json")


def test_configured_targets_can_disable_retired_sites_mirror(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(
        "CLOUDFLARE_INGEST_URL", "https://example.workers.dev/api/ingest"
    )
    monkeypatch.setenv("CLOUDFLARE_INGEST_TOKEN", "cloudflare-token")
    config = {
        "enabled": False,
        "remote_ingest_url": "https://retired.chatgpt.site/api/ingest",
        "token": "retired-token",
        "learning_state_file": str(tmp_path / "learning.json"),
        "news_state_file": str(tmp_path / "news.json"),
        "market_history_state_file": str(tmp_path / "market-history.json"),
        "learning_history_state_file": str(tmp_path / "learning-history.json"),
        "news_evidence_state_file": str(tmp_path / "news-evidence.json"),
        "resource_schedule_state_file": str(tmp_path / "schedule.json"),
    }

    targets = module.configured_targets(
        module.configure_runtime_state(config, tmp_path)
    )

    assert [target["name"] for target in targets] == ["cloudflare"]
    assert targets[0]["remote_ingest_url"].endswith("workers.dev/api/ingest")


def test_configured_targets_rejects_every_state_path_outside_runtime_root(
    monkeypatch, tmp_path
) -> None:
    state_root = tmp_path / "private-state"
    state_root.mkdir()
    monkeypatch.setenv(
        "CLOUDFLARE_INGEST_URL", "https://example.workers.dev/api/ingest"
    )
    monkeypatch.setenv("CLOUDFLARE_INGEST_TOKEN", "cloudflare-token")
    state_keys = (
        "learning_state_file", "news_state_file", "market_history_state_file",
        "learning_history_state_file", "news_evidence_state_file",
        "resource_schedule_state_file",
    )
    for state_key in state_keys:
        config = {
            "enabled": False,
            "remote_ingest_url": "https://retired.chatgpt.site/api/ingest",
            "token": "retired-token",
            **{
                key: str(state_root / f"{key}.json")
                for key in state_keys
            },
            state_key: str(tmp_path / "outside.json"),
        }
        with pytest.raises(ValueError, match="must be one JSON file under"):
            module.configure_runtime_state(config, state_root)


@pytest.mark.parametrize("value", [
    "../escape.json", "nested/state.json", "state.txt", "state name.json",
    f"{'a' * 129}.json",
])
def test_sync_state_path_rejects_traversal_and_non_json_names(
    monkeypatch, tmp_path, value
) -> None:

    with pytest.raises(ValueError, match="must be one JSON file under"):
        module._validated_sync_state_path(Path(value), tmp_path)


def test_sites_bypass_header_is_shared_by_get_and_post_but_not_cloudflare(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SITES_BYPASS_TOKEN", "sites-bypass")
    captured = []

    class Response:
        status = 200

        def read(self):
            return b'{}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, **_kwargs):
        captured.append(dict(request.header_items()))
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    config = {"token": "ingest-token"}

    module._post_json(
        "https://example.chatgpt.site/api/ingest", b"{}", config
    )
    module._get_json("https://example.chatgpt.site/api/assistant-worker/chat", config)
    module._post_json("https://example.workers.dev/api/ingest", b"{}", config)
    module._get_json("https://example.workers.dev/api/assistant-worker/chat", config)

    assert "Oai-sites-authorization" in captured[0]
    assert "Oai-sites-authorization" in captured[1]
    assert "Oai-sites-authorization" not in captured[2]
    assert "Oai-sites-authorization" not in captured[3]
    assert all(
        headers["User-agent"] == "AurumSignalRoomMirror/1.0"
        for headers in captured
    )


def test_get_json_can_explicitly_read_structured_health_error_evidence(
    monkeypatch,
) -> None:
    payload = {
        "status": "ERROR",
        "projection_state": "REPLAYING",
        "error_code": "NEWS_PROJECTION_NOT_SYNCHRONIZED",
        "staging": {"generation_id": "a" * 64},
    }

    def reject(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.workers.dev/api/news-index?health_check=1",
            503,
            "Service Unavailable",
            {},
            io.BytesIO(json.dumps(payload).encode()),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", reject)

    with pytest.raises(module.RemoteInvariantViolation):
        module._get_json("https://example.workers.dev/api/news-index", {"token": "x"})
    assert module._get_json(
        "https://example.workers.dev/api/news-index",
        {"token": "x"},
        allow_error_payload=True,
    ) == payload
