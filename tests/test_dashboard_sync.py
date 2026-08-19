from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


def _sync_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard_sync.py"
    spec = importlib.util.spec_from_file_location("run_dashboard_sync_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dashboard_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard_api.py"
    spec = importlib.util.spec_from_file_location("run_dashboard_api_sync_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_assistant_capacity_route(monkeypatch, accountant_value: str) -> list[dict]:
    from xauusd_forecaster import assistant_capacity, assistant_routing

    calls: list[dict] = []

    def execute(connection, plan, credentials, *, service_priority, policies, invoke):
        selected = credentials[0]
        profile = plan.candidate_profiles[0]
        calls.append({
            "connection": connection,
            "credentials": credentials,
            "service_priority": service_priority,
            "policies": policies,
        })
        value = invoke(
            profile,
            selected,
            assistant_routing.provider_thinking_level(plan, profile),
            accountant_value,
        )
        routing = assistant_routing.routing_provenance(plan, profile)
        routing["capacity"] = {
            "policy_version": "assistant-capacity-v1",
            "service_priority": service_priority.value,
            "selected_pool_fingerprint": "0123456789abcdef",
            "selected_pool_type": selected.pool,
            "candidate_pool_count": 1,
            "candidate_pair_count": 1,
            "attempt_count": 1,
            "estimated_input_tokens": plan.estimated_input_tokens,
            "soft_cap_basis_points": 8_000,
            "max_in_flight": 2,
            "policy_source": "REGISTRY_DEFAULT",
            "model_fallback_used": False,
        }
        return SimpleNamespace(value=value, profile=profile, routing=routing)

    monkeypatch.setattr(
        assistant_capacity, "execute_assistant_capacity_route", execute,
    )
    return calls


def _annotator_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_news_annotator.py"
    spec = importlib.util.spec_from_file_location("run_news_annotator_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preview_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_preview_bundle.py"
    spec = importlib.util.spec_from_file_location("build_preview_bundle_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    package_modules = {
        name: loaded for name, loaded in sys.modules.items()
        if name == "xauusd_forecaster" or name.startswith("xauusd_forecaster.")
    }
    try:
        spec.loader.exec_module(module)
    finally:
        for name in tuple(sys.modules):
            if name == "xauusd_forecaster" or name.startswith("xauusd_forecaster."):
                sys.modules.pop(name, None)
        sys.modules.update(package_modules)
    return module


def test_preview_bundle_import_does_not_require_sqlite_extension() -> None:
    """Immutable Preview assembly must remain portable to Cloudflare builds."""
    root = Path(__file__).resolve().parents[1]
    code = """
import importlib.abc
import importlib.util
import pathlib

class BlockSqlite(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"sqlite3", "_sqlite3"}:
            raise ModuleNotFoundError(f"blocked optional module: {fullname}")
        return None

import sys
sys.meta_path.insert(0, BlockSqlite())
path = pathlib.Path("scripts/build_preview_bundle.py").resolve()
spec = importlib.util.spec_from_file_location("preview_without_sqlite", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_preview_evidence_fixture_is_stable_and_omits_display_age() -> None:
    module = _preview_module()
    status = {
        "generated_at": "2026-08-19T10:00:00+00:00",
        "news_evidence": [{
            "event_key": "a" * 64,
            "source_hash": "b" * 64,
            "economic_age_minutes": 42.5,
            "broad_model_eligible": True,
            "model_seen": False,
        }],
    }

    first = module._preview_news_evidence(status)
    later = module._preview_news_evidence({
        **status,
        "news_evidence": [{
            **status["news_evidence"][0], "economic_age_minutes": 99.0,
        }],
    })

    assert later == first
    assert "economic_age_minutes" not in first["items"][0]
    changed = module._preview_news_evidence({
        **status,
        "news_evidence": [{
            **status["news_evidence"][0], "source_hash": "c" * 64,
        }],
    })
    assert changed["snapshot_id"] != first["snapshot_id"]


def test_preview_reader_rejects_a_noncanonical_source() -> None:
    module = _preview_module()

    with pytest.raises(ValueError, match="canonical production origin"):
        module._read_json("http://127.0.0.1:8765", "/api/status")


@pytest.mark.parametrize(
    ("source", "published_at", "first_seen_at"),
    (
        (
            "google_news_gold_context",
            "2026-08-08T20:40:28+00:00",
            "2026-08-08T23:34:06+00:00",
        ),
        (
            "gdelt_gold_geopolitics",
            "2026-08-08T23:35:00+00:00",
            "2026-08-08T23:30:07+00:00",
        ),
    ),
)
def test_preview_keeps_timing_anomalies_in_semantic_queue(
    source: str, published_at: str, first_seen_at: str,
) -> None:
    module = _preview_module()
    news_index = {"items": [{
        "annotation_status": "QUEUED",
        "impact_status": "PENDING_ANNOTATION",
        "model_visibility": "NOT_YET_PARSED",
        "source": source,
        "source_published_time": published_at,
        "collector_first_seen_time": first_seen_at,
    }]}

    module._backfill_annotation_reasons(
        news_index, {"forward_epoch": "2026-08-05T00:00:00+00:00"}
    )

    row = news_index["items"][0]
    assert row["annotation_status"] == "QUEUED"
    assert row["impact_status"] == "PENDING_ANNOTATION"
    assert row["model_visibility"] == "NOT_YET_PARSED"
    assert "annotation_reason_code" not in row


def test_preview_repairs_stale_queue_mismatch_for_late_discovery() -> None:
    module = _preview_module()
    news_index = {"items": [{
        "annotation_status": "NOT_REQUIRED",
        "annotation_reason_code": "QUEUE_INVARIANT_MISMATCH",
        "annotation_reason": "正文符合条件但未进入语义队列，需要检查",
        "impact_status": "NOT_REQUIRED",
        "model_visibility": "MODEL_INELIGIBLE",
        "source": "google_news_gold_context",
        "source_published_time": "2026-08-08T20:40:28+00:00",
        "collector_first_seen_time": "2026-08-08T23:34:06+00:00",
    }]}

    module._backfill_annotation_reasons(
        news_index, {"forward_epoch": "2026-08-05T00:00:00+00:00"}
    )

    assert news_index["items"] == [{
        "annotation_status": "QUEUED",
        "impact_status": "PENDING_ANNOTATION",
        "model_visibility": "NOT_YET_PARSED",
        "source": "google_news_gold_context",
        "source_published_time": "2026-08-08T20:40:28+00:00",
        "collector_first_seen_time": "2026-08-08T23:34:06+00:00",
    }]


def test_preview_preserves_legitimate_not_required_reason() -> None:
    module = _preview_module()
    row = {
        "annotation_status": "NOT_REQUIRED",
        "annotation_reason_code": "CANONICAL_COPY_HANDLES_ANNOTATION",
        "annotation_reason": "同一新闻已有 canonical 版本处理",
        "impact_status": "NOT_REQUIRED",
        "model_visibility": "MODEL_INELIGIBLE",
        "source": "google_news_gold_context",
        "source_published_time": "2026-08-08T20:40:28+00:00",
        "collector_first_seen_time": "2026-08-08T23:34:06+00:00",
    }
    news_index = {"items": [dict(row)]}

    module._backfill_annotation_reasons(
        news_index, {"forward_epoch": "2026-08-05T00:00:00+00:00"}
    )

    assert news_index["items"] == [row]


def test_preview_reads_completed_news_from_old_and_current_api_contracts(
    monkeypatch,
) -> None:
    module = _preview_module()
    requested: list[str] = []

    def old_api(_base_url: str, path: str) -> dict:
        requested.append(path)
        page = int(path.split("page=", 1)[1].split("&", 1)[0])
        rows = [
            {
                "annotation_status": "QUEUED" if page == 1 else "READY",
                "detail_key": f"{page:02d}-{index:02d}",
                "category": "其他",
            }
            for index in range(50)
        ]
        return {"items": rows, "total": 500, "totals_scope": "D1_ARCHIVE"}

    monkeypatch.setattr(module, "_read_json", old_api)
    compatible = module._read_completed_news_index("https://example.test")
    assert len(requested) == 2
    assert len(compatible["items"]) == module.PREVIEW_NEWS_PAGE_SIZE
    assert {row["annotation_status"] for row in compatible["items"]} == {"READY"}
    assert compatible["review_state"] == "COMPLETED"
    assert compatible["totals_scope"] == "BUILD_SNAPSHOT"

    monkeypatch.setattr(
        module,
        "_read_json",
        lambda _base_url, _path: {
            "items": [{"annotation_status": "READY"}],
            "review_state": "COMPLETED",
            "review_state_counts": {"COMPLETED": 120},
            "totals_scope": "D1_ARCHIVE",
        },
    )
    current = module._read_completed_news_index("https://example.test")
    assert current["review_state_counts"] == {"COMPLETED": 120}
    assert current["totals_scope"] == "D1_ARCHIVE"


def test_preview_overlays_branch_owned_model_throughput_contract() -> None:
    module = _preview_module()
    status = {
        "annotation_queue": {
            "requests_per_minute": 48,
        },
    }

    module._apply_branch_runtime_contract(status)

    assert status["annotation_queue"] == {
        "requests_per_minute_per_key": 12,
        "requests_per_minute_per_account": 12,
        "requests_per_minute": 12,
        "input_tokens_per_minute": 225_000,
        "minute_scope": "ACCOUNT",
    }
    assert status["llm_routing"]["display_only"] == {
        "configured_account_count": 1,
        "requests_per_minute_per_account": 20,
        "requests_per_minute": 20,
        "input_tokens_per_minute_per_account": 15_000,
        "input_tokens_per_minute": 15_000,
        "provider_lanes_per_account": 2,
        "maximum_concurrent_requests": 2,
        "minute_scope": "ACCOUNT",
    }


def test_preview_backfills_missing_daily_brief_summary_without_fake_counts() -> None:
    module = _preview_module()
    status = {
        "generated_at": "2026-08-16T01:31:55+00:00",
        "annotation_queue": {},
        "daily_news_briefs": [],
    }

    module._apply_branch_runtime_contract(status)

    summary = status["daily_news_brief_summary"]
    assert summary["brief_date"] == "2026-08-16"
    assert summary["phase"] == "WAITING"
    assert summary["received_items"] is None
    assert summary["total_brief_days"] is None
    assert summary["observation_scope"] == "BUILD_SNAPSHOT_COMPATIBILITY"


def test_preview_freezes_both_materialized_curve_overviews(monkeypatch) -> None:
    module = _preview_module()
    requested: list[str] = []

    def fake_read_json(_base_url: str, path: str) -> dict:
        requested.append(path)
        cadence = path.rsplit("=", 1)[-1]
        return {"items": [{
            "model_identity": "MARKET_ONLY",
            "cadence": cadence,
            "source_point_count": 1059 if cadence == "5m" else 174,
            "chart_point_count": 1,
            "chart_downsampled": True,
            "points": [{
                "decision_time": "2026-08-12T01:00:00+00:00",
                "cumulative_quote_return": 0.1,
            }],
        }]}

    monkeypatch.setattr(module, "_read_json", fake_read_json)
    records = module._curve_overview_records("https://example.test")

    assert requested == [
        "/api/learning-history?resource=curve-overview&cadence=5m",
        "/api/learning-history?resource=curve-overview&cadence=30m",
    ]
    assert {(row["record_key"], row["payload"]["source_point_count"])
            for row in records} == {
        ("5m\0MARKET_ONLY", 1059),
        ("30m\0MARKET_ONLY", 174),
    }


def test_preview_freezes_pageable_version_history_beyond_first_page(
    monkeypatch,
) -> None:
    module = _preview_module()
    groups = [{
        "model_identity": "MARKET_ONLY",
        "training_dataset_hash": f"market-{generation}",
        "created_at": (
            datetime(2026, 8, 1, tzinfo=timezone.utc)
            + timedelta(hours=generation)
        ).isoformat(),
        "generation": generation,
        "training_rows": generation * 50,
    } for generation in range(1, 62)]
    groups.append({
        "model_identity": "NEWS_ONLY",
        "training_dataset_hash": "news-1",
        "created_at": "2026-08-14T07:00:00+00:00",
        "generation": 1,
        "training_rows": 124,
    })

    monkeypatch.setattr(
        module, "_read_json",
        lambda _base_url, path: {"items": groups}
        if path.endswith("resource=version-overview") else {},
    )
    records = module._version_history_records("https://example.test")

    version_groups = [
        row for row in records if row["resource"] == "version-group"
    ]
    assert len(version_groups) == 61
    market_groups = sorted(
        (row for row in version_groups
         if row["payload"]["model_identity"] == "MARKET_ONLY"),
        key=lambda row: row["sort_epoch"],
    )
    assert len(market_groups) == module.dashboard_sync.LEARNING_OVERVIEW_GROUPS_PER_IDENTITY
    assert market_groups[0]["payload"]["generation"] == 2
    assert market_groups[-1]["payload"]["generation"] == 61

    bundle = module.build_bundle(
        "https://example.test", "feature/example", "abcdef12",
    )
    stored = bundle["learning_history"]
    assert len([row for row in stored if row["resource"] == "version-group"]) == 61
    assert not [row for row in stored if row["resource"] == "version-overview"]


def test_sync_retries_transient_disconnect(monkeypatch) -> None:
    module = _sync_module()
    calls = []

    def flaky(_config):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError("remote closed")

    monkeypatch.setattr(module, "sync_once", flaky)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    assert module.sync_with_retry({"unused": True}) == (3, [])
    assert len(calls) == 3


def test_sync_does_not_retry_authentication_error(monkeypatch) -> None:
    module = _sync_module()
    calls = []

    def rejected(_config):
        calls.append(1)
        raise urllib.error.HTTPError("https://example", 403, "Forbidden", {}, io.BytesIO())

    monkeypatch.setattr(module, "sync_once", rejected)
    try:
        module.sync_with_retry({"unused": True})
    except urllib.error.HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("403 must be raised immediately")
    assert len(calls) == 1


def test_sync_status_records_real_success_and_preserves_it_on_error(tmp_path) -> None:
    module = _sync_module()
    status_file = tmp_path / "dashboard-sync-status.json"

    observation = [{
        "target": "cloudflare", "resource": "news", "status": "OK",
        "duration_ms": 12.5, "completed_at": "2026-08-17T00:00:00+00:00",
    }]
    module.write_sync_status(
        status_file, success=True, attempts_used=2,
        resource_observations=observation,
    )
    succeeded = json.loads(status_file.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(succeeded["last_success"])
    assert succeeded["attempts_used"] == 2
    assert succeeded["last_error"] is None
    assert succeeded["status"] == "OK"
    assert succeeded["resource_observations"] == observation

    module.write_sync_status(
        status_file, success=False, error=ConnectionResetError("remote closed")
    )
    failed = json.loads(status_file.read_text(encoding="utf-8"))
    assert failed["last_success"] == succeeded["last_success"]
    assert failed["last_error_type"] == "ConnectionResetError"
    assert failed["last_error"] == "remote closed"
    assert failed["last_error_code"] == "TRANSPORT_UNAVAILABLE"
    assert failed["degraded_resources"] == []
    assert failed["status"] == "ERROR"


def test_sync_status_reports_optional_resource_degradation(tmp_path) -> None:
    module = _sync_module()
    status_file = tmp_path / "dashboard-sync-status.json"
    degraded = [{"resource": "learning", "error": "too large"}]
    module.write_sync_status(
        status_file, success=True, attempts_used=1,
        degraded_resources=degraded,
    )
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "DEGRADED"
    assert status["last_error"] is None
    assert status["degraded_resources"] == degraded


def test_news_mirror_health_verifies_completed_contract(monkeypatch) -> None:
    module = _sync_module()
    requested = []
    monkeypatch.setattr(
        module, "_get_json",
        lambda url, _config: requested.append(url) or {
            "status": "OK", "violation_count": 0, "checks": [],
        },
    )

    module._verify_news_mirror_state(
        "https://worker.example/api/news-index", {"token": "test"},
        expected_contract=module.NEWS_MIRROR_CONTRACT_VERSION,
    )

    assert requested == [
        "https://worker.example/api/news-index?health_check=1&current_contract="
        + module.NEWS_MIRROR_CONTRACT_VERSION
        + "&expected_contract="
        + module.NEWS_MIRROR_CONTRACT_VERSION
    ]


def test_news_mirror_health_preserves_bounded_invariant_evidence(monkeypatch) -> None:
    module = _sync_module()
    monkeypatch.setattr(module, "_get_json", lambda *_a, **_k: {
        "status": "ERROR",
        "error_code": "NEWS_MIRROR_STATE_INVARIANT_VIOLATION",
        "violation_count": 21,
        "checks": [{"code": "NEWS_REVIEW_STATE_INVALID", "count": 21}],
    })

    with pytest.raises(module.RemoteInvariantViolation) as captured:
        module._verify_news_mirror_state(
            "https://worker.example/api/news-index", {"token": "test"},
            expected_contract=None,
        )

    assert module.sync_error_code(captured.value) == (
        "NEWS_MIRROR_STATE_INVARIANT_VIOLATION"
    )
    assert captured.value.evidence == {
        "violation_count": 21,
        "checks": [{"code": "NEWS_REVIEW_STATE_INVALID", "count": 21}],
    }


def test_remote_write_rejection_preserves_declared_error_code(monkeypatch) -> None:
    module = _sync_module()
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

    assert module.sync_error_code(captured.value) == (
        "NEWS_MIRROR_STATE_INVARIANT_VIOLATION"
    )


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (401, "AUTH_REJECTED"),
        (403, "AUTH_REJECTED"),
        (413, "PAYLOAD_LIMIT_EXCEEDED"),
        (429, "RATE_LIMITED"),
        (503, "REMOTE_UNAVAILABLE"),
    ],
)
def test_transport_error_family_is_persisted_as_structured_code(
    status_code,
    expected,
) -> None:
    module = _sync_module()
    error = urllib.error.HTTPError(
        "https://example.invalid", status_code, "failure", {}, io.BytesIO(),
    )

    assert module.sync_error_code(error) == expected


def test_only_declared_payload_contract_errors_receive_payload_code() -> None:
    module = _sync_module()

    assert module.sync_error_code(ValueError("invalid configuration")) == "UNCLASSIFIED"
    assert module.sync_error_code(
        module.PayloadContractError("bounded payload too large")
    ) == "PAYLOAD_CONTRACT_REJECTED"
    assert module.sync_error_code(
        urllib.error.URLError("name resolution failed")
    ) == "TRANSPORT_UNAVAILABLE"


def test_all_rejected_heartbeat_targets_preserve_structured_failures(
    monkeypatch,
) -> None:
    module = _sync_module()
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: __import__("contextlib").nullcontext(
            type("Response", (), {
                "read": lambda self: b"{}", "status": 200,
            })()
        ),
    )
    monkeypatch.setattr(module, "remote_snapshot", lambda payload: b"{}")
    monkeypatch.setattr(
        module,
        "configured_targets",
        lambda config: [{
            "name": "cloudflare", "remote_ingest_url": "https://example.invalid",
            "token": "token",
        }],
    )
    monkeypatch.setattr(
        module,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError(
                "https://example.invalid", 413, "too large", {}, io.BytesIO(),
            )
        ),
    )

    with pytest.raises(module.AllTargetsRejected) as captured:
        module.sync_once({"local_status_url": "https://local.invalid"})

    assert module.sync_error_code(captured.value) == "PAYLOAD_LIMIT_EXCEEDED"
    assert len(captured.value.degraded_resources) == 1
    failure = captured.value.degraded_resources[0]
    assert {key: failure[key] for key in (
        "target", "resource", "error_type", "error_code", "error",
    )} == {
        "target": "cloudflare",
        "resource": "heartbeat",
        "error_type": "HTTPError",
        "error_code": "PAYLOAD_LIMIT_EXCEEDED",
        "error": "HTTP Error 413: too large",
    }
    assert failure["duration_ms"] >= 0
    assert captured.value.resource_observations[0]["status"] == "ERROR"


def test_configured_targets_adds_independent_cloudflare_mirror(
    monkeypatch, tmp_path
) -> None:
    module = _sync_module()
    monkeypatch.setenv(
        "CLOUDFLARE_INGEST_URL", "https://example.workers.dev/api/ingest"
    )
    monkeypatch.setenv("CLOUDFLARE_INGEST_TOKEN", "cloudflare-token")
    config = {
        "remote_ingest_url": "https://example.chatgpt.site/api/ingest",
        "token": "sites-token",
        "learning_state_file": str(tmp_path / "learning.json"),
        "news_state_file": str(tmp_path / "news.json"),
    }

    sites, cloudflare = module.configured_targets(config)

    assert sites["name"] == "sites"
    assert sites["learning_state_file"].endswith("learning.json")
    assert cloudflare["name"] == "cloudflare"
    assert cloudflare["remote_ingest_url"].endswith("workers.dev/api/ingest")
    assert cloudflare["learning_state_file"].endswith("learning-cloudflare.json")
    assert cloudflare["news_state_file"].endswith("news-cloudflare.json")


def test_configured_targets_can_disable_retired_sites_mirror(
    monkeypatch, tmp_path
) -> None:
    module = _sync_module()
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
    }

    targets = module.configured_targets(config)

    assert [target["name"] for target in targets] == ["cloudflare"]
    assert targets[0]["remote_ingest_url"].endswith("workers.dev/api/ingest")


def test_sites_bypass_header_is_shared_by_get_and_post_but_not_cloudflare(
    monkeypatch,
) -> None:
    module = _sync_module()
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


def test_ingest_response_records_valid_main_revision(tmp_path, monkeypatch) -> None:
    module = _sync_module()
    revision = "a" * 40
    signal = tmp_path / "remote-main-signal.json"
    monkeypatch.setattr(module, "DEFAULT_RUNTIME_SIGNAL", signal)

    class Response:
        status = 200

        def read(self):
            return json.dumps({"status": "OK", "main_revision": revision}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        module.urllib.request, "urlopen", lambda *_args, **_kwargs: Response()
    )

    result = module._post_json(
        "https://example.workers.dev/api/ingest", b"{}", {"token": "token"}
    )

    assert result["main_revision"] == revision
    assert json.loads(signal.read_text(encoding="utf-8"))["main_revision"] == revision


def test_critical_status_excludes_growing_resources_and_keeps_references() -> None:
    module = _sync_module()
    body = "完整正文" * 2_000
    payload = {
        "training": {"complete_rows": 200, "models": [{"duplicate": body}]},
        "learning_curves": {
            "models": [
                {"lifecycle_status": "LATEST", "model_version": "latest"},
                {"lifecycle_status": "ARCHIVED", "model_version": "old"},
            ],
            "identity_curves": [body],
            "full_minus_market": [body],
            "broad_full_minus_core_full": [body],
        },
        "recent_news": [{
            "source": "example", "source_item_id": str(index),
            "revision_number": 1, "headline": f"新闻 {index}",
            "summary_zh": body, "category": "其他",
            "content_fetch_status": "UNAVAILABLE",
            "content_error_type": "HTTPError",
            "annotation_status": "NOT_REQUIRED",
            "annotation_reason_code": "SEARCH_LEAD",
            "annotation_reason": "搜索线索：来自聚合发现源，不是独立官方发布",
        } for index in range(100)],
        "recent_decisions": [{"id": index} for index in range(30)],
        "news_evidence": [
            {"id": index, "model_seen": index < 97}
            for index in range(202)
        ],
        "market_chart": {
            "candles": [{"time": "2026-08-06T00:00:00Z", "open": 1,
                         "high": 2, "low": 0.5, "close": 1.5}],
            "overview_candles": [{"time": "2026-08-05T00:00:00Z", "open": 1,
                                  "high": 2, "low": 0.5, "close": 1.5}],
            "training_markers": [{"time": "2026-08-06T00:00:00Z"}],
            "decisions": [{
            "source_decision_id": "d1", "decision_time": "2026-08-06T00:00:00+00:00",
            "model_identity": "MARKET_ONLY", "model_version": "large-unused-field",
            "recommended_action": "SHORT", "ev_long_u5": -0.2,
            "ev_short_u5": 0.1, "policy_expected_action": "SHORT",
            "policy_consistent": True, "frozen_record": True,
        }]},
    }

    encoded = module.remote_snapshot(payload)
    mirrored = json.loads(encoded)
    audit = json.loads(module.audit_snapshot(payload))
    index_rows, detail_rows = module.news_mirror_parts(payload)
    learning = json.loads(module.learning_snapshot(payload))

    assert len(encoded) <= module.REMOTE_PAYLOAD_LIMIT_BYTES
    assert mirrored["news_index_resource"] == "/api/news-index"
    assert mirrored["news_evidence_resource"] == "/api/news-evidence"
    assert mirrored["audit_resource"] == "/api/audit"
    assert mirrored["learning_resource"] == "/api/learning"
    assert detail_rows[0]["payload"]["summary_zh"] == body
    assert index_rows[0]["content_fetch_status"] == "UNAVAILABLE"
    assert index_rows[0]["content_error_type"] == "HTTPError"
    assert index_rows[0]["annotation_reason_code"] == "SEARCH_LEAD"
    assert index_rows[0]["annotation_reason"].startswith("搜索线索")
    assert "content_fetch_status" not in detail_rows[0]["payload"]
    assert "annotation_reason" not in detail_rows[0]["payload"]
    assert len(detail_rows[0]["detail_key"]) == 64
    assert mirrored["market_chart"]["decisions"] == []
    assert "news_evidence" not in mirrored
    assert "recent_news" not in mirrored
    assert "recent_decisions" not in mirrored
    market_decision = json.loads(module.market_chart_snapshot(payload))["decisions"][0]
    assert market_decision["source_decision_id"] == "d1"
    assert market_decision["model_version"] == "unused-field"
    assert len(audit["recent_decisions"]) == module.REMOTE_DECISION_LIMIT
    assert audit["news_evidence_resource"] == "/api/news-evidence"
    assert learning["learning_curves"]["models"] == [
        {"lifecycle_status": "LATEST", "model_version": "latest"},
    ]
    assert learning["learning_curves"]["archived_model_count"] == 1
    assert learning["learning_history_resource"] == "/api/learning-history"
    assert learning["learning_curves"]["identity_curves"] == [body]
    assert learning["learning_curves"]["full_minus_market"] == [body]
    assert learning["learning_curves"]["broad_full_minus_core_full"] == [body]
    assert "learning_curves" not in mirrored
    assert "models" not in mirrored["training"]
    assert len(index_rows) == 100


@pytest.mark.parametrize(
    "field",
    (
        "news_evidence", "daily_news_briefs", "storylines",
        "future_accumulated_records", "future_user_history",
    ),
)
def test_critical_status_size_is_independent_of_unknown_growing_state(field) -> None:
    module = _sync_module()
    base = {
        "generated_at": "2026-08-19T10:00:00+00:00",
        "system": {"online": True, "components": {}},
        "counts": {"decision_events": 10},
    }
    baseline = module.remote_snapshot(base)
    grown = {
        **base,
        field: [{"id": index, "body": "x" * 2_000} for index in range(10_000)],
    }

    encoded = module.remote_snapshot(grown)

    assert encoded == baseline
    assert len(encoded) < module.REMOTE_PAYLOAD_LIMIT_BYTES // 4


def test_news_detail_batches_stay_bounded() -> None:
    module = _sync_module()
    rows = [{
        "detail_key": f"{index:064x}", "detail_hash": f"{index + 1:064x}",
        "payload": {"summary_zh": "摘要" * 20_000},
    } for index in range(8)]
    batches = module.news_detail_batches(rows)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == len(rows)
    for batch in batches:
        assert len(batch) <= module.NEWS_WRITE_BATCH_ITEMS
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        assert len(encoded) <= module.NEWS_DETAIL_BATCH_LIMIT_BYTES


def test_news_index_batches_stay_bounded() -> None:
    module = _sync_module()
    rows = [{
        "detail_key": f"{index:064x}", "category": "战争/地缘",
        "collector_first_seen_time": f"2026-08-07T00:{index:02d}:00+00:00",
        "headline": "标题" * 5_000,
    } for index in range(45)]
    batches = module.news_index_batches(rows)
    assert sum(len(batch) for batch in batches) == len(rows)
    for batch in batches:
        assert len(batch) <= module.NEWS_WRITE_BATCH_ITEMS
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        assert len(encoded) <= module.NEWS_INDEX_BATCH_LIMIT_BYTES


def test_learning_history_records_have_stable_keys_and_bounded_batches() -> None:
    module = _sync_module()
    payload = {
        "learning_curves": {
            "models": [{
                "model_identity": "FULL", "model_version": "model-v1",
                "created_at": "2026-08-10T01:00:00+00:00",
            }],
            "version_groups": [{
                "model_identity": "FULL", "training_dataset_hash": "hash-1",
                "created_at": "2026-08-10T01:00:00+00:00", "generation": 3,
            }],
            "identity_curves": [{
                "model_identity": "FULL",
                "points": [{
                    "decision_time": "2026-08-10T01:05:00+00:00",
                    "cumulative_quote_return": 0.01,
                }],
                "points_30m": [{
                    "decision_time": "2026-08-10T01:30:00+00:00",
                    "cumulative_quote_return": 0.02,
                }],
            }],
        },
        "execution_learning": {"models": []},
    }

    first = module.learning_history_records(payload)
    second = module.learning_history_records(payload)

    assert first == second
    assert {row["resource"] for row in first} == {
        "model", "version-group", "curve-5m", "curve-30m",
        "curve-overview", "version-overview",
    }
    assert all(len(row["payload_hash"]) == 64 for row in first)
    batches = module.learning_history_batches(first * 2_000)
    assert len(batches) > 1
    for batch in batches:
        encoded = json.dumps(
            {"records": batch}, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        assert len(encoded) <= module.LEARNING_HISTORY_BATCH_LIMIT_BYTES


def test_visual_overviews_stay_bounded_and_preserve_the_full_span() -> None:
    module = _sync_module()
    points = [{
        "decision_time": (
            datetime(2026, 1, 1, tzinfo=timezone.utc)
            + timedelta(minutes=5 * index)
        ).isoformat(),
        "cumulative_quote_return": (-1 if index == 50_000 else index / 100_000),
    } for index in range(100_000)]

    overview = module._visual_curve_overview(points, 240)

    assert len(overview) <= 240
    assert overview[0]["decision_time"] == points[0]["decision_time"]
    assert overview[-1]["decision_time"] == points[-1]["decision_time"]
    assert any(
        row["decision_time"] == points[50_000]["decision_time"]
        for row in overview
    )
    assert not any(row["source_gap_before"] for row in overview)

    groups = [{
        "created_at": point["decision_time"],
        "generation": index,
        "cumulative_quote_return": point["cumulative_quote_return"],
        "cadence_metrics": {
            "FIXED_30M": {
                "cumulative_quote_return": 2 if index == 75_000 else -index / 100_000,
            },
        },
    } for index, point in enumerate(points)]
    group_overview = module._visual_version_overview(groups, 60)

    assert len(group_overview) <= 60
    assert group_overview[0] == groups[0]
    assert group_overview[-1] == groups[-1]
    assert groups[50_000] in group_overview
    assert groups[75_000] in group_overview


def test_curve_overview_marks_only_real_source_gaps() -> None:
    module = _sync_module()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    offsets = [0, 5, 10, 120, 125]
    points = [{
        "decision_time": (start + timedelta(minutes=offset)).isoformat(),
        "cumulative_quote_return": index / 100,
    } for index, offset in enumerate(offsets)]

    overview = module._visual_curve_overview(points, 240)

    assert [row["source_gap_before"] for row in overview] == [
        False, False, False, True, False,
    ]

    compressed_source = module._visual_curve_overview(
        points, 240, infer_source_gaps=False,
    )
    assert not any(row["source_gap_before"] for row in compressed_source)


def test_decision_overviews_are_incremental_bounded_and_frequency_scoped() -> None:
    module = _sync_module()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decisions = [{
        "source_decision_id": f"decision-{index}",
        "decision_time": (start + timedelta(minutes=5 * index)).isoformat(),
        "model_identity": "FULL",
        "recommended_action": ("SHORT" if index % 7 == 0 else "LONG"),
    } for index in range(2_000)]

    summaries = module._update_decision_overviews({}, decisions, None)
    five = summaries["FULL\0" "5m"]
    half_hour = summaries["FULL\0" "30m"]

    assert five["source_decision_count"] == 2_000
    assert len(five["decisions"]) <= module.MARKET_OVERVIEW_DECISIONS_PER_SERIES
    assert five["decisions"][0]["source_decision_id"] == "decision-0"
    assert five["decisions"][-1]["source_decision_id"] == "decision-1999"
    assert half_hour["source_decision_count"] == 334
    assert all(
        datetime.fromisoformat(row["decision_time"]).minute % 30 == 0
        for row in half_hour["decisions"]
    )

    unchanged = module._update_decision_overviews(
        summaries, decisions[-24:], decisions[-1]["decision_time"],
    )
    assert unchanged == summaries

    settled = {
        **five["decisions"][0],
        "outcome_status": "MATURE",
        "value_quote_return": 0.001,
    }
    refreshed = module._update_decision_overviews(
        summaries, [settled], decisions[-1]["decision_time"],
    )
    refreshed_five = refreshed["FULL\0" "5m"]
    assert refreshed_five["source_decision_count"] == 2_000
    refreshed_row = next(
        row for row in refreshed_five["decisions"]
        if row["source_decision_id"] == settled["source_decision_id"]
    )
    assert refreshed_row["outcome_status"] == "MATURE"
    assert refreshed_row["value_quote_return"] == 0.001


def test_learning_summary_size_is_fixed_as_history_grows() -> None:
    module = _sync_module()
    groups = []
    points = []
    for index in range(1_000):
        stamp = (datetime(2026, 8, 1, tzinfo=timezone.utc)
                 + timedelta(minutes=5 * index)).isoformat()
        groups.append({
            "model_identity": "FULL", "training_dataset_hash": f"hash-{index}",
            "created_at": stamp, "generation": index, "lifecycle_status": "ARCHIVED",
        })
        points.append({"decision_time": stamp, "cumulative_quote_return": index / 1000})
    payload = {
        "learning_curves": {
            "models": [], "version_groups": groups,
            "identity_curves": [{"model_identity": "FULL", "points": points}],
        },
        "execution_learning": {"models": []},
    }

    summary = json.loads(module.learning_snapshot(payload))

    assert len(summary["learning_curves"]["version_groups"]) == 6
    assert len(summary["learning_curves"]["identity_curves"][0]["points"]) == 48
    assert summary["learning_history_manifest"]["version_group_total"] == 1_000
    assert len(module.learning_snapshot(payload)) < 100_000


def test_learning_history_is_durable_before_summary_and_retries_idempotently(
    monkeypatch, tmp_path,
) -> None:
    module = _sync_module()
    payload = {
        "learning_curves": {
            "models": [],
            "version_groups": [{
                "model_identity": "FULL", "training_dataset_hash": "hash-1",
                "created_at": "2026-08-10T01:00:00+00:00", "generation": 1,
            }],
            "identity_curves": [],
        },
        "execution_learning": {"models": []},
    }
    posted: list[str] = []
    monkeypatch.setattr(
        module, "_post_json", lambda url, _body, _config: posted.append(url)
    )
    config = {
        "remote_ingest_url": "https://worker.example/api/ingest",
        "token": "test",
        "learning_state_file": str(tmp_path / "summary.json"),
        "learning_history_state_file": str(tmp_path / "history.json"),
    }

    module._sync_learning(payload, config)

    assert posted == [
        "https://worker.example/api/learning-history",
        "https://worker.example/api/learning",
    ]
    posted.clear()
    module._sync_learning(payload, config)
    assert posted == []


def test_news_details_are_durable_before_index_is_published(monkeypatch, tmp_path) -> None:
    module = _sync_module()
    monkeypatch.setattr(module, "_verify_news_mirror_state", lambda *_a, **_k: None)
    payload = {
        "recent_news": [{
            "source": "example", "source_item_id": "1", "revision_number": 1,
            "category": "其他",
            "collector_first_seen_time": "2026-08-10T00:00:00+00:00",
            "headline": "新闻", "summary_zh": "完整摘要",
        }],
    }
    state_file = tmp_path / "news-state.json"
    state_file.write_text(json.dumps({
        "mirror_contract_version": module.NEWS_MIRROR_CONTRACT_VERSION,
        "hashes": {}, "index_hashes": {},
    }), encoding="utf-8")
    posted: list[str] = []
    monkeypatch.setattr(
        module, "_post_json", lambda url, _body, _config: posted.append(url)
    )
    config = {
        "remote_ingest_url": "https://remote/api/ingest",
        "news_state_file": str(state_file), "token": "test",
    }

    module._sync_news(payload, config)

    assert posted == [
        "https://remote/api/news-content",
        "https://remote/api/news-index",
        "https://remote/api/news-index",
    ]


def test_news_detail_failure_never_publishes_dangling_index(monkeypatch, tmp_path) -> None:
    module = _sync_module()
    monkeypatch.setattr(module, "_verify_news_mirror_state", lambda *_a, **_k: None)
    payload = {
        "recent_news": [{
            "source": "example", "source_item_id": "1", "revision_number": 1,
            "category": "其他",
            "collector_first_seen_time": "2026-08-10T00:00:00+00:00",
            "headline": "新闻", "summary_zh": "完整摘要",
        }],
    }
    state_file = tmp_path / "news-state.json"
    state_file.write_text(json.dumps({
        "mirror_contract_version": module.NEWS_MIRROR_CONTRACT_VERSION,
        "hashes": {}, "index_hashes": {},
    }), encoding="utf-8")
    posted: list[str] = []

    def fail_detail(url, _body, _config):
        posted.append(url)
        raise TimeoutError("detail upload timed out")

    monkeypatch.setattr(module, "_post_json", fail_detail)
    config = {
        "remote_ingest_url": "https://remote/api/ingest",
        "news_state_file": str(state_file), "token": "test",
    }

    with pytest.raises(TimeoutError, match="detail upload timed out"):
        module._sync_news(payload, config)

    assert posted == ["https://remote/api/news-content"]


def test_news_evidence_sync_stages_complete_bounded_pages_before_activation(
    monkeypatch, tmp_path,
) -> None:
    module = _sync_module()
    snapshot_id = "a" * 64
    rows = [{
        "event_key": f"{index:064x}",
        "collector_first_seen_time": f"2026-08-19T10:{index % 60:02d}:00+00:00",
        "source_published_time": None,
        "broad_model_eligible": index % 2 == 0,
        "model_seen": index % 3 == 0,
        "body": "evidence" * 200,
    } for index in range(105)]

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def urlopen(url, *_args, **_kwargs):
        query = __import__("urllib.parse").parse.urlsplit(str(url)).query
        values = __import__("urllib.parse").parse.parse_qs(query)
        cursor = (values.get("cursor") or [None])[0]
        offset = int(cursor.split(":", 1)[1]) if cursor else 0
        page = rows[offset:offset + module.NEWS_EVIDENCE_WRITE_BATCH_ITEMS]
        next_offset = offset + len(page)
        return Response({
            "snapshot_id": snapshot_id,
            "items": page,
            "total": len(rows),
            "has_more": next_offset < len(rows),
            "next_cursor": (
                f"{snapshot_id}:{next_offset}" if next_offset < len(rows) else None
            ),
        })

    posted = []
    remote_next_offset = 0
    remote_active = False

    def post(url, body, _config):
        nonlocal remote_next_offset, remote_active
        payload = json.loads(body)
        posted.append((url, payload))
        if "prepare_snapshot" in payload:
            return {
                "status": "OK", "active": remote_active,
                "next_offset": len(rows) if remote_active else remote_next_offset,
            }
        if "items" in payload:
            assert payload["offset"] == remote_next_offset
            remote_next_offset += len(payload["items"])
            return {"status": "OK", "received": len(payload["items"])}
        if "activate_snapshot" in payload:
            assert remote_next_offset == len(rows)
            remote_active = True
            return {"status": "OK", "activated": snapshot_id, "count": len(rows)}
        return {"status": "OK"}

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module, "_post_json", post)
    config = {
        "local_status_url": "http://local/api/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "token": "test",
        "news_evidence_state_file": str(tmp_path / "evidence-state.json"),
    }

    module._sync_news_evidence({}, config)
    first_cycle_batches = [body for _url, body in posted if "items" in body]
    assert not any("activate_snapshot" in body for _url, body in posted)
    assert len(first_cycle_batches) == module.NEWS_EVIDENCE_PAGES_PER_CYCLE
    assert posted[0][1] == {
        "contract_version": module.NEWS_EVIDENCE_CONTRACT_VERSION,
        "prepare_snapshot": snapshot_id,
        "expected_count": len(rows),
    }
    module._sync_news_evidence({}, config)

    batches = [body for _url, body in posted if "items" in body]
    activation = next(body for _url, body in posted if "activate_snapshot" in body)
    assert sum(len(body["items"]) for body in batches) == len(rows)
    assert [item["event_key"] for body in batches for item in body["items"]] == [
        row["event_key"] for row in rows
    ]
    assert [body["offset"] for body in batches] == list(
        range(0, len(rows), module.NEWS_EVIDENCE_WRITE_BATCH_ITEMS)
    )
    assert all(
        len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
        <= module.NEWS_INDEX_BATCH_LIMIT_BYTES
        for body in batches
    )
    assert activation == {
        "contract_version": module.NEWS_EVIDENCE_CONTRACT_VERSION,
        "activate_snapshot": snapshot_id,
        "expected_count": len(rows),
    }
    state = json.loads((tmp_path / "evidence-state.json").read_text(encoding="utf-8"))
    assert state["active_snapshot_id"] == snapshot_id
    assert posted[-1][1]["cleanup_active_snapshot"] == snapshot_id

    posted.clear()
    module._sync_news_evidence({}, config)
    assert posted == [(
        "https://remote/api/news-evidence",
        {
            "contract_version": module.NEWS_EVIDENCE_CONTRACT_VERSION,
            "cleanup_active_snapshot": snapshot_id,
        },
    )]


def test_news_evidence_sync_resumes_stable_generation_across_volatile_time_fields(
    monkeypatch, tmp_path,
) -> None:
    sync = _sync_module()
    api = _dashboard_module()
    rows = [{
        "event_key": f"{index:064x}",
        "collector_first_seen_time": f"2026-08-19T10:{index:02d}:00+00:00",
        "source_published_time": None,
        "broad_model_eligible": True,
        "model_seen": index % 2 == 0,
        "source_hash": f"{index + 100:064x}",
        "economic_age_minutes": float(index),
        "freshness_status": "FRESH",
        "model_permission": "BROAD_MODEL",
        "reason_codes": ["EVIDENCE_PRIMARY"],
    } for index in range(45)]
    manifest = tmp_path / "news-evidence-generation.json"
    stable_snapshot, frozen_rows = api._materialize_news_evidence_generation(
        rows, manifest,
    )
    assert api._publish_news_evidence_snapshot(frozen_rows) == stable_snapshot
    monkeypatch.setattr(sync, "NEWS_EVIDENCE_PAGES_PER_CYCLE", 2)

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def urlopen(url, *_args, **_kwargs):
        query = __import__("urllib.parse").parse.urlsplit(str(url)).query
        cursor = (__import__("urllib.parse").parse.parse_qs(query).get("cursor") or [None])[0]
        return Response(api._news_evidence_page(cursor, sync.NEWS_EVIDENCE_WRITE_BATCH_ITEMS))

    offsets: dict[str, int] = {}
    received_keys: dict[str, list[str]] = {}
    active = [None]
    activations = []

    def post(_url, body, _config):
        payload = json.loads(body)
        snapshot = payload.get("prepare_snapshot") or payload.get("snapshot_id") \
            or payload.get("activate_snapshot") or payload.get("cleanup_active_snapshot")
        if "prepare_snapshot" in payload:
            return {
                "status": "OK", "active": active[0] == snapshot,
                "next_offset": offsets.get(snapshot, 0),
            }
        if "items" in payload:
            assert payload["offset"] == offsets.get(snapshot, 0)
            received_keys.setdefault(snapshot, []).extend(
                item["event_key"] for item in payload["items"]
            )
            offsets[snapshot] = payload["offset"] + len(payload["items"])
        if "activate_snapshot" in payload:
            assert offsets.get(snapshot, 0) == payload["expected_count"]
            active[0] = snapshot
            activations.append(snapshot)
        return {"status": "OK"}

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sync, "_post_json", post)
    config = {
        "local_status_url": "http://local/api/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "token": "test",
        "news_evidence_state_file": str(tmp_path / "evidence-state.json"),
    }

    sync._sync_news_evidence({}, config)
    assert offsets[stable_snapshot] == 40
    assert activations == []

    volatile_snapshot, restarted_rows = api._materialize_news_evidence_generation([
        {
            **row,
            "economic_age_minutes": row["economic_age_minutes"] + 180,
            "freshness_status": "EVENT_LIFETIME_EXPIRED",
            "broad_model_eligible": False,
            "model_permission": "DISPLAY_ONLY",
            "reason_codes": ["EVIDENCE_PRIMARY", "EVENT_LIFETIME_EXPIRED"],
        }
        for row in rows
    ], manifest)
    assert volatile_snapshot == stable_snapshot
    assert api._publish_news_evidence_snapshot(restarted_rows) == stable_snapshot
    sync = _sync_module()
    monkeypatch.setattr(sync, "NEWS_EVIDENCE_PAGES_PER_CYCLE", 2)
    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sync, "_post_json", post)
    sync._sync_news_evidence({}, config)
    assert activations == [stable_snapshot]
    assert received_keys[stable_snapshot] == [row["event_key"] for row in rows]
    assert len(set(received_keys[stable_snapshot])) == len(rows)

    changed_rows = [{**rows[0], "source_hash": "f" * 64}, *rows[1:]]
    changed_snapshot, changed_rows = api._materialize_news_evidence_generation(
        changed_rows, manifest,
    )
    assert api._publish_news_evidence_snapshot(changed_rows) == changed_snapshot
    assert changed_snapshot != stable_snapshot
    sync._sync_news_evidence({}, config)
    assert changed_snapshot in offsets
    assert active[0] == stable_snapshot


def test_news_evidence_activation_acknowledgement_replays_idempotently(
    monkeypatch, tmp_path,
) -> None:
    module = _sync_module()
    snapshot_id = "a" * 64
    row = {
        "event_key": "b" * 64,
        "collector_first_seen_time": "2026-08-19T10:00:00+00:00",
        "source_published_time": None,
        "broad_model_eligible": True,
        "model_seen": False,
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "snapshot_id": snapshot_id,
                "items": [row],
                "total": 1,
                "has_more": False,
                "next_cursor": None,
            }).encode("utf-8")

    remote = {"next_offset": 0, "active": False, "lose_ack": True}

    def post(_url, body, _config):
        payload = json.loads(body)
        if "prepare_snapshot" in payload:
            return {
                "status": "OK", "active": remote["active"],
                "next_offset": remote["next_offset"],
            }
        if "items" in payload:
            remote["next_offset"] = 1
            return {"status": "OK"}
        if "activate_snapshot" in payload:
            remote["active"] = True
            if remote["lose_ack"]:
                remote["lose_ack"] = False
                raise TimeoutError("activation response was lost")
        return {"status": "OK"}

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_a, **_k: Response())
    monkeypatch.setattr(module, "_post_json", post)
    state_path = tmp_path / "evidence-state.json"
    config = {
        "local_status_url": "http://local/api/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "token": "test",
        "news_evidence_state_file": str(state_path),
    }

    with pytest.raises(TimeoutError, match="response was lost"):
        module._sync_news_evidence({}, config)
    assert not state_path.exists() or "active_snapshot_id" not in json.loads(
        state_path.read_text(encoding="utf-8")
    )

    restarted = _sync_module()
    monkeypatch.setattr(restarted.urllib.request, "urlopen", lambda *_a, **_k: Response())
    monkeypatch.setattr(restarted, "_post_json", post)
    restarted._sync_news_evidence({}, config)
    local = json.loads(state_path.read_text(encoding="utf-8"))
    assert local["active_snapshot_id"] == snapshot_id
    assert local["record_count"] == 1


def test_optional_growing_resource_failure_does_not_block_heartbeat(
    monkeypatch, tmp_path,
) -> None:
    module = _sync_module()
    local_payload = {
        "generated_at": "2026-08-19T10:00:00+00:00",
        "system": {"online": True, "components": {}},
        "future_history": [{"body": "x" * 20_000} for _ in range(100)],
    }

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(local_payload).encode("utf-8")

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_a, **_k: Response())
    target = {
        "name": "cloudflare", "remote_ingest_url": "https://remote/api/ingest",
        "token": "test", "legacy": False,
        "local_status_url": "http://local/api/status",
    }
    monkeypatch.setattr(module, "configured_targets", lambda _config: [target])
    posted = []
    monkeypatch.setattr(
        module, "_post_json", lambda url, body, _config: posted.append((url, body)),
    )
    for name in (
        "_sync_audit", "_sync_learning", "_sync_market", "_sync_market_history",
        "_sync_news", "_sync_news_questions", "_sync_operator_retries",
    ):
        monkeypatch.setattr(module, name, lambda *_a, **_k: None)
    monkeypatch.setattr(
        module, "_sync_news_evidence",
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("evidence unavailable")),
    )

    result = module.sync_once({"local_status_url": "http://local/api/status"})

    assert posted[0][0] == "https://remote/api/ingest"
    assert json.loads(posted[0][1])["generated_at"] == local_payload["generated_at"]
    assert len(result) == 1
    assert result[0]["resource"] == "news_evidence"
    assert result[0]["error_type"] == "TimeoutError"
    heartbeat = next(
        row for row in result.resource_observations if row["resource"] == "heartbeat"
    )
    evidence = next(
        row for row in result.resource_observations if row["resource"] == "news_evidence"
    )
    assert heartbeat["status"] == "OK"
    assert evidence["status"] == "ERROR"


def test_growing_local_snapshot_failure_cannot_block_critical_heartbeat(
    monkeypatch,
) -> None:
    module = _sync_module()
    critical = {
        "generated_at": "2026-08-19T10:00:00+00:00",
        "system": {"online": True},
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(critical).encode("utf-8")

    def urlopen(url, *_args, **_kwargs):
        if str(url).endswith("/api/critical-status"):
            return Response()
        raise TimeoutError("full optional snapshot timed out")

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    target = {
        "name": "cloudflare", "remote_ingest_url": "https://remote/api/ingest",
        "token": "test", "legacy": False,
        "local_status_url": "http://local/api/status",
    }
    monkeypatch.setattr(module, "configured_targets", lambda _config: [target])
    posted = []
    monkeypatch.setattr(
        module, "_post_json", lambda url, body, _config: posted.append((url, body)),
    )
    monkeypatch.setattr(module, "_sync_operator_retries", lambda *_a, **_k: None)

    result = module.sync_once({"local_status_url": "http://local/api/status"})

    assert len(posted) == 1
    assert posted[0][0] == "https://remote/api/ingest"
    assert json.loads(posted[0][1])["system"]["online"] is True
    assert {row["resource"] for row in result} == {
        "audit", "learning", "market_chart", "market_history", "news",
        "news_evidence",
    }
    assert all(row["resource"] != "optional_snapshot" for row in result)
    assert next(
        row for row in result.resource_observations if row["resource"] == "heartbeat"
    )["status"] == "OK"


@pytest.mark.parametrize("failed_resource", ["audit", "learning", "market_chart"])
def test_optional_resource_families_degrade_only_their_owner(
    monkeypatch, failed_resource,
) -> None:
    module = _sync_module()
    critical = {
        "generated_at": "2026-08-19T10:00:00+00:00",
        "system": {"online": True},
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(critical).encode("utf-8")

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_a, **_k: Response())
    target = {
        "name": "cloudflare", "remote_ingest_url": "https://remote/api/ingest",
        "token": "test", "legacy": False,
        "local_status_url": "http://local/api/status",
    }
    monkeypatch.setattr(module, "configured_targets", lambda _config: [target])
    posted = []
    monkeypatch.setattr(
        module, "_post_json",
        lambda url, body, _config: posted.append((url, json.loads(body))) or {},
    )
    operations = {
        "audit": "_sync_audit",
        "learning": "_sync_learning",
        "market_chart": "_sync_market",
        "market_history": "_sync_market_history",
        "news": "_sync_news",
        "news_evidence": "_sync_news_evidence",
        "news_questions": "_sync_news_questions",
        "operator_retries": "_sync_operator_retries",
    }
    called = []
    for resource, name in operations.items():
        def operation(*_args, resource=resource, **_kwargs):
            called.append(resource)
            if resource == failed_resource:
                raise TimeoutError(f"{resource} failed")
        monkeypatch.setattr(module, name, operation)

    result = module.sync_once({"local_status_url": "http://local/api/status"})

    assert posted[0][0] == "https://remote/api/ingest"
    assert called == list(operations)
    assert [row["resource"] for row in result] == [failed_resource]
    by_resource = {row["resource"]: row for row in result.resource_observations}
    assert by_resource["heartbeat"]["status"] == "OK"
    assert by_resource[failed_resource]["status"] == "ERROR"
    assert all(
        row["status"] == "OK" for name, row in by_resource.items()
        if name not in {failed_resource}
    )


def test_sync_skips_unchanged_news_index_and_learning(monkeypatch, tmp_path) -> None:
    module = _sync_module()
    monkeypatch.setenv("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "test-bridge-" + "x" * 32)
    monkeypatch.setattr(module, "_verify_news_mirror_state", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "_sync_news_evidence", lambda *_a, **_k: None)
    payload = {
        "generated_at": "2026-08-07T00:00:00+00:00",
        "learning_curves": {"learning_stage": "EARLY"},
        "execution_learning": {"models": []},
        "recent_news": [{
            "source": "example", "source_item_id": "1", "revision_number": 1,
            "category": "其他", "collector_first_seen_time": "2026-08-07T00:00:00+00:00",
            "headline": "第一条", "summary_zh": "摘要",
        }],
        "market_chart": {"decisions": []},
    }

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    posted: list[str] = []
    news_page_calls = 0

    def urlopen(url, *_args, **_kwargs):
        nonlocal news_page_calls
        if "/api/news-archive" in str(url):
            news_page_calls += 1
            page = {
                "items": payload["recent_news"] if news_page_calls in {1, 3} else [],
                "next_cursor": '["2026-08-07T00:00:00+00:00","example","1",1]',
                "has_more": False,
            }

            class NewsResponse(Response):
                def read(self):
                    return json.dumps(page, ensure_ascii=False).encode("utf-8")

            return NewsResponse()
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module, "_post_json", lambda url, _body, _config: posted.append(url))
    config = {
        "local_status_url": "http://local/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "token": "test",
        "news_state_file": str(tmp_path / "news-state.json"),
        "learning_state_file": str(tmp_path / "learning-state.json"),
        "targets": [{
            "name": "sites", "legacy": True,
            "remote_ingest_url": "https://remote/api/ingest", "token": "test",
        }],
    }

    first_cycle = module.sync_once(config)
    assert first_cycle == []
    assert {row["resource"] for row in first_cycle.resource_observations} == {
        "heartbeat", "audit", "learning", "market_chart", "market_history", "news",
        "news_evidence", "news_questions", "operator_retries",
    }
    assert all(row["status"] == "OK" for row in first_cycle.resource_observations)
    assert all(row["duration_ms"] >= 0 for row in first_cycle.resource_observations)
    assert posted.count("https://remote/api/learning") == 1
    # An unknown mirror contract neutralizes only stale operational state before
    # authoritative replay. Completed reader history stays visible throughout
    # the handover; reconciliation removes stale remote-only rows at the end.
    assert posted.count("https://remote/api/news-index") == 3
    assert posted[0] == "https://remote/api/ingest"

    posted.clear()
    payload["generated_at"] = "2026-08-07T00:00:30+00:00"
    module.sync_once(config)
    assert "https://remote/api/learning" not in posted
    assert "https://remote/api/news-index" not in posted
    assert posted[0] == "https://remote/api/ingest"

    posted.clear()
    payload["learning_curves"]["learning_stage"] = "READY"
    payload["recent_news"][0]["headline"] = "第一条（更新）"
    module.sync_once(config)
    assert posted.count("https://remote/api/learning") == 1
    assert posted.count("https://remote/api/news-index") == 1
    assert posted[0] == "https://remote/api/ingest"

    news_state = json.loads((tmp_path / "news-state.json").read_text(encoding="utf-8"))
    assert news_state["cursor"].startswith('["2026-08-07T00:00:00')
    assert news_state["reconciled_contract"] == module.NEWS_MIRROR_CONTRACT_VERSION
    assert news_state["mirror_contract_version"] == module.NEWS_MIRROR_CONTRACT_VERSION


def test_sync_repopulates_news_index_without_full_refresh_marker(
    monkeypatch, tmp_path
) -> None:
    module = _sync_module()
    monkeypatch.setenv("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "test-bridge-" + "y" * 32)
    monkeypatch.setattr(module, "_verify_news_mirror_state", lambda *_a, **_k: None)
    payload = {
        "generated_at": "2026-08-07T00:00:00+00:00",
        "learning_curves": {},
        "execution_learning": {"models": []},
        "recent_news": [{
            "source": "Federal Reserve",
            "source_item_id": "press-1",
            "revision_number": 1,
            "category": "利率/Fed",
            "collector_first_seen_time": "2026-08-07T00:00:00+00:00",
            "headline": "第一条",
        }],
        "market_chart": {"decisions": []},
    }
    index_rows, _ = module.news_mirror_parts(payload)
    state_file = tmp_path / "news-state.json"
    state_file.write_text(json.dumps({
        "index_hashes": {
            index_rows[0]["detail_key"]: module._json_hash(index_rows[0]),
        },
        "hashes": {},
        "last_full_sync": "2026-08-07T00:00:00+00:00",
    }), encoding="utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    posted: list[str] = []
    monkeypatch.setattr(
        module.urllib.request, "urlopen", lambda *_args, **_kwargs: Response()
    )
    monkeypatch.setattr(
        module, "_post_json", lambda url, _body, _config: posted.append(url)
    )
    config = {
        "local_status_url": "http://local/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "token": "test",
        "news_state_file": str(state_file),
        "learning_state_file": str(tmp_path / "learning-state.json"),
    }

    module.sync_once(config)

    assert posted.count("https://remote/api/news-index") == 3
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["mirror_contract_version"] == module.NEWS_MIRROR_CONTRACT_VERSION
    assert state["reconciled_contract"] == module.NEWS_MIRROR_CONTRACT_VERSION


@pytest.mark.parametrize("previous_contract", [
    "news-60-day-incremental-v2",
    "news-60-day-incremental-v3-semantic-categories",
    "news-60-day-incremental-v4-relevance-filter",
    "news-60-day-incremental-v7-semantic-handover",
    "news-60-day-incremental-v8-recovery-state",
])
def test_news_materialization_contract_upgrade_replays_and_reconciles_old_state(
    monkeypatch, tmp_path, previous_contract
) -> None:
    module = _sync_module()
    monkeypatch.setattr(module, "_verify_news_mirror_state", lambda *_a, **_k: None)
    state_file = tmp_path / "news-state.json"
    stale_cursor = '["2026-08-13T00:00:00Z","example","old",1]'
    state_file.write_text(json.dumps({
        "mirror_contract_version": previous_contract,
        "reconciled_contract": previous_contract,
        "cursor": stale_cursor,
    }), encoding="utf-8")
    requested: list[str] = []
    posted: list[tuple[str, dict]] = []
    page = {
        "items": [{
            "source": "example", "source_item_id": "new", "revision_number": 1,
            "category": "风险情绪 / 避险",
            "collector_first_seen_time": "2026-08-14T00:00:00Z",
            "headline": "新的语义分类",
        }],
        "next_cursor": '["2026-08-14T00:00:00Z","example","new",1]',
        "has_more": False,
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(page, ensure_ascii=False).encode("utf-8")

    def urlopen(url, *_args, **_kwargs):
        requested.append(str(url))
        return Response()

    def post_json(url, body, _config):
        posted.append((url, json.loads(body)))

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module, "_post_json", post_json)
    module._sync_news({}, {
        "local_status_url": "http://local/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "news_state_file": str(state_file),
        "token": "test",
    })

    # A changed materialization contract discards the old cursor and starts at
    # the bounded archive head instead of continuing after a stale row.
    assert len(requested) == 1
    assert "after=" not in requested[0]
    assert f"limit={module.NEWS_WRITE_BATCH_ITEMS}" in requested[0]
    index_payloads = [body for url, body in posted if url.endswith("/news-index")]
    assert index_payloads[0] == {
        "neutralize_operational_state_for_contract": module.NEWS_MIRROR_CONTRACT_VERSION,
    }
    replay_payload = next(payload for payload in index_payloads if "items" in payload)
    assert replay_payload["items"][0]["category"] == "风险情绪 / 避险"
    assert replay_payload["items"][0]["mirror_contract"] == module.NEWS_MIRROR_CONTRACT_VERSION
    assert index_payloads[-1]["reconcile_contract"] == module.NEWS_MIRROR_CONTRACT_VERSION
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["cursor"] != stale_cursor
    assert state["mirror_contract_version"] == module.NEWS_MIRROR_CONTRACT_VERSION
    assert state["reconciled_contract"] == module.NEWS_MIRROR_CONTRACT_VERSION


def test_semantic_projection_upgrade_replays_late_discovery_once(
    monkeypatch, tmp_path,
) -> None:
    module = _sync_module()
    monkeypatch.setattr(module, "_verify_news_mirror_state", lambda *_a, **_k: None)
    state_file = tmp_path / "news-state.json"
    state_file.write_text(json.dumps({
        "mirror_contract_version": "news-60-day-incremental-v8-recovery-state",
        "reconciled_contract": "news-60-day-incremental-v8-recovery-state",
        "cursor": '["2026-08-17T04:09:01+00:00","google_news_gold_context","old",1]',
    }), encoding="utf-8")
    current_item = {
        "source": "google_news_fed_rates",
        "source_item_id": "late-discovery-cpi",
        "revision_number": 1,
        "cluster_id": "late-discovery-cpi-cluster",
        "content_hash": "a" * 64,
        "category": "通胀/就业",
        "source_published_time": "2026-08-15T06:13:28+00:00",
        "collector_first_seen_time": "2026-08-17T04:09:01+00:00",
        "annotation_status": "READY",
        "model_visibility": "IMPACT_PENDING",
        "impact_status": "PENDING_IMPACT",
        "eligibility_version": "news-source-eligibility-v14-resolved-identity",
        "mirror_updated_at": "2026-08-17T04:09:02+00:00",
        "headline": "CPI in Focus: Can the Dollar Turn Lower Again?",
    }
    requests: list[str] = []
    posted: list[tuple[str, dict]] = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def urlopen(url, *_args, **_kwargs):
        requests.append(str(url))
        return Response({
            "items": [] if "after=" in str(url) else [current_item],
            "next_cursor": '["2026-08-17T04:09:02+00:00","google_news_fed_rates","late-discovery-cpi",1]',
            "has_more": False,
        })

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        module, "_post_json",
        lambda url, body, _config: posted.append((url, json.loads(body))),
    )
    config = {
        "local_status_url": "http://local/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "news_state_file": str(state_file),
        "token": "test",
    }

    module._sync_news({}, config)

    index_payloads = [
        body for url, body in posted if url.endswith("/news-index")
    ]
    assert "after=" not in requests[0]
    assert index_payloads[0] == {
        "neutralize_operational_state_for_contract":
            module.NEWS_MIRROR_CONTRACT_VERSION,
    }
    replay = next(payload for payload in index_payloads if "items" in payload)
    assert replay["items"][0]["annotation_status"] == "READY"
    assert replay["items"][0]["model_visibility"] == "IMPACT_PENDING"
    assert replay["items"][0]["source_published_time"] == current_item[
        "source_published_time"
    ]
    assert replay["items"][0]["collector_first_seen_time"] == current_item[
        "collector_first_seen_time"
    ]
    assert index_payloads[-1]["reconcile_contract"] == (
        module.NEWS_MIRROR_CONTRACT_VERSION
    )
    stable_state = json.loads(state_file.read_text(encoding="utf-8"))

    posted.clear()
    module._sync_news({}, config)

    assert all(not url.endswith("/news-index") for url, _body in posted)
    second_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert second_state["mirror_contract_version"] == stable_state[
        "mirror_contract_version"
    ]
    assert second_state["reconciled_contract"] == stable_state[
        "reconciled_contract"
    ]
    assert second_state["cursor"] == stable_state["cursor"]


def test_news_sync_forwards_exact_semantic_withdrawals(monkeypatch, tmp_path) -> None:
    module = _sync_module()
    monkeypatch.setattr(module, "_verify_news_mirror_state", lambda *_a, **_k: None)
    page = {
        "items": [],
        "withdrawals": [{
            "source": "gdelt_gold_geopolitics",
            "source_item_id": "entertainment-one",
            "revision_number": 1,
        }],
        "next_cursor": '["2026-08-15T00:00:00Z","gdelt","one",1]',
        "has_more": True,
    }

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps(page).encode()

    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_a, **_k: Response())
    monkeypatch.setattr(
        module, "_post_json",
        lambda url, body, _config: posted.append((url, json.loads(body))),
    )
    module._sync_news({}, {
        "local_status_url": "http://local/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "news_state_file": str(tmp_path / "news-state.json"),
        "token": "test",
    })

    withdrawals = [
        body["withdraw_detail_keys"] for url, body in posted
        if url.endswith("/news-index") and "withdraw_detail_keys" in body
    ]
    assert withdrawals == [[module._stable_news_key(page["withdrawals"][0])]]


def test_remote_market_chart_is_split_from_status_and_keeps_recent_window() -> None:
    module = _sync_module()
    decisions = [{
        "source_decision_id": f"d-{index}",
        "decision_time": f"2026-08-06T{index // 60:02d}:{index % 60:02d}:00+00:00",
        "model_identity": "BROAD_FULL",
        "model_version": f"model-{index}",
        "recommended_action": "SHORT",
        "prediction_status": "PROVISIONAL",
        "outcome_status": "VALID",
        "ev_long_u5": -0.2,
        "ev_short_u5": 0.1,
    } for index in range(module.REMOTE_MARKET_DECISION_LIMIT + 20)]
    payload = {
        "market_chart": {
            "decisions": list(reversed(decisions)),
            "candles": [{"time": "2026-08-05T00:00:00+00:00", "open": 1.1234567, "high": 2, "low": 0, "close": 1.5, "ticks": 8}],
            "history_start": "2026-08-05T00:00:00+00:00",
            "history_end": "2026-08-07T20:55:00+00:00",
            "source_candle_count": 736,
        },
    }
    mirrored = json.loads(module.remote_snapshot(payload))
    assert mirrored["market_chart"]["decisions"] == []
    assert mirrored["market_chart_resource"] == "/api/market-chart"

    market = json.loads(module.market_chart_snapshot(payload))
    retained = market["decisions"]
    assert len(retained) == module.REMOTE_MARKET_DECISION_LIMIT
    assert retained[0]["source_decision_id"] == "d-20"
    assert all(row["source_decision_id"] != "d-0" for row in retained)
    assert retained[-1]["source_decision_id"] == f"d-{len(decisions) - 1}"
    assert "exit_time" not in retained[1]
    assert retained[1]["model_version"] == "model-21"
    assert len(market["candles"]) == 1
    assert market["candles"][0]["open"] == 1.123
    assert market["candles"][0]["time"] == "2026-08-05T00:00:00Z"
    assert "ticks" not in market["candles"][0]
    assert market["history_end"] == "2026-08-07T20:55:00+00:00"
    assert market["source_candle_count"] == 736


def test_seven_day_market_snapshot_is_recent_only_under_limit() -> None:
    module = _sync_module()
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    identities = ("MARKET_ONLY", "NEWS_RESIDUAL", "FULL", "BROAD_NEWS_RESIDUAL", "BROAD_FULL")
    candles = []
    decisions = []
    for index in range(7 * 288):
        decision_time = (start + timedelta(minutes=5 * index)).isoformat()
        candles.append({
            "time": decision_time, "open": 4300.123456, "high": 4301.123456,
            "low": 4299.123456, "close": 4300.623456, "ticks": 100,
        })
        decisions.extend({
            "source_decision_id": f"d-{index}", "decision_time": decision_time,
            "model_identity": identity, "model_version": "model-version-long",
            "recommended_action": "LONG", "outcome_status": "VALID",
            "ev_long_u5": 0.123456789, "ev_short_u5": -0.123456789,
            "long_quote_return": 0.00123456789,
            "short_quote_return": -0.00123456789,
        } for identity in identities)

    encoded = module.market_chart_snapshot({
        "market_chart": {
            "candles": candles, "overview_candles": candles[::4][:480],
            "decisions": decisions,
            "prediction_history_start": {
                identity: start.isoformat() for identity in identities
            },
        },
    })
    market = json.loads(encoded)

    assert len(encoded) <= module.REMOTE_PAYLOAD_LIMIT_BYTES
    assert len(market["candles"]) == module.REMOTE_MARKET_CANDLE_LIMIT
    assert 0 < len(market["overview_candles"]) <= 480
    assert len(market["decisions"]) <= module.REMOTE_MARKET_DECISION_LIMIT
    assert min(row["decision_time"] for row in market["decisions"]) > "2026-08-01T00:00:00Z"


def test_market_overview_downsampling_preserves_ohlc_extremes() -> None:
    module = _sync_module()
    rows = [{
        "time": f"t-{index}", "open": float(index), "high": float(index + 2),
        "low": float(index - 2), "close": float(index + 0.5),
        "source_candles": 2,
    } for index in range(6)]

    compact = module._downsample_market_overview(rows, 2)

    assert len(compact) == 2
    assert compact[0] == {
        "time": "t-0", "open": 0.0, "high": 4.0, "low": -2.0,
        "close": 2.5, "source_candles": 6,
    }


def test_market_history_ingest_batches_are_bounded_and_complete() -> None:
    module = _sync_module()
    candles = [{
        "time": f"2026-08-07T00:{index:02d}:00+00:00",
        "open": 4300.1234, "high": 4301.1234,
        "low": 4299.1234, "close": 4300.6234, "ticks": 20,
    } for index in range(12)]
    decisions = [{
        "source_decision_id": f"d-{index}",
        "decision_time": f"2026-08-07T00:{index:02d}:00+00:00",
        "model_identity": "BROAD_FULL", "model_version": "very-long-model-version",
        "recommended_action": "LONG", "outcome_status": "VALID",
        "ev_long_u5": 0.12, "ev_short_u5": -0.12,
        "long_quote_return": 0.001, "short_quote_return": -0.001,
    } for index in range(12)]

    payloads = module._market_history_payloads(candles, decisions)
    decoded = [json.loads(payload) for payload in payloads]

    assert all(len(payload) <= module.MARKET_HISTORY_BATCH_LIMIT_BYTES for payload in payloads)
    assert sum(len(row.get("candles", [])) for row in decoded) == len(candles)
    assert sum(len(row.get("decisions", [])) for row in decoded) == len(decisions)
    assert module._overlap_cursor("2026-08-07T04:00:00Z") == "2026-08-07T02:00:00+00:00"


def test_market_decision_overview_payload_is_bounded_and_keeps_edges() -> None:
    module = _sync_module()
    decisions = [{
        "source_decision_id": f"d-{index}",
        "decision_time": f"2026-08-07T{index // 60:02d}:{index % 60:02d}:00+00:00",
        "model_identity": "BROAD_FULL",
        "recommended_action": "LONG",
        "explanation": "x" * 2_000,
    } for index in range(480)]
    summary = {
        "model_identity": "BROAD_FULL",
        "frequency": "5m",
        "source_decision_count": len(decisions),
        "decision_count": len(decisions),
        "decision_downsampled": False,
        "decisions": decisions,
    }

    payload = module._market_decision_overview_payload(summary)
    bounded = json.loads(payload)["decision_overviews"][0]

    assert len(payload) <= module.MARKET_HISTORY_BATCH_LIMIT_BYTES
    assert bounded["decisions"][0]["source_decision_id"] == "d-0"
    assert bounded["decisions"][-1]["source_decision_id"] == "d-479"
    assert bounded["source_decision_count"] == 480
    assert bounded["decision_count"] == len(bounded["decisions"])
    assert bounded["decision_downsampled"] is True


def test_annotator_heartbeat_reports_idle_loop_as_healthy(tmp_path) -> None:
    module = _annotator_module()
    status_file = tmp_path / "news-annotator-status.json"
    module.write_heartbeat(status_file, work_items=0)
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["service"] == "annotator"
    assert status["state"] == "RUNNING"
    assert datetime.fromisoformat(status["last_success"])
    assert status["last_error"] is None
    assert status["work_items"] == 0


def test_assistant_sync_surfaces_are_paused_without_network_or_model_calls(
    monkeypatch,
) -> None:
    module = _sync_module()
    monkeypatch.setattr(
        module, "_get_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("paused Assistant must not claim remote work")
        ),
    )
    monkeypatch.setattr(
        module, "_post_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("paused Assistant must not post worker results")
        ),
    )

    assert module._sync_assistant_chat({}, {}) == {
        "status": "PAUSED_NO_MODEL",
    }
    assert module._sync_news_questions({}, {}) is None


def test_operator_retry_sync_mirrors_claims_applies_and_finishes(monkeypatch) -> None:
    module = _sync_module()
    job = {
        "job_id": "a" * 64, "task_type": "ACTIVE_IMPACT", "title": "Gold",
        "state": "BACKING_OFF", "priority": "NORMAL",
        "available_at": "2026-08-19T06:00:00+00:00", "attempt_count": 3,
        "last_error": "ConnectionResetError",
        "original_available_at": "2026-08-19T06:00:00+00:00",
    }
    claim = {
        "request_id": "request-1", "job_id": job["job_id"],
        "operator_id": "cloudflare-access:owner", "mode": "IMMEDIATE",
        "reason": "fix deployed", "expected_state": "BACKING_OFF",
        "expected_available_at": job["available_at"],
        "requested_available_at": None, "lease_token": "lease-1",
    }
    applied_job = {**job, "available_at": "2026-08-19T03:02:00+00:00"}
    local_reads = iter(({"items": [job]}, {"items": [applied_job]}))
    monkeypatch.setattr(module, "_get_local_json", lambda _url: next(local_reads))
    local_posts = []
    monkeypatch.setattr(
        module, "_post_local_json",
        lambda url, payload: local_posts.append((url, payload)) or {
            "results": [{"request_id": "request-1", "job_id": job["job_id"],
                         "status": "APPLIED", "current": {"state": "BACKING_OFF"}}],
        },
    )
    claims = iter(({"item": claim}, {"item": None}))
    monkeypatch.setattr(module, "_get_json", lambda *_args, **_kwargs: next(claims))
    remote_posts = []
    monkeypatch.setattr(
        module, "_post_json",
        lambda url, body, _config: remote_posts.append((url, json.loads(body))) or {},
    )

    module._sync_operator_retries({}, {
        "local_status_url": "http://127.0.0.1:8765/api/status",
        "remote_ingest_url": "https://example.workers.dev/api/ingest",
        "token": "test",
    })

    assert remote_posts[0][1] == {"action": "SYNC_JOBS", "items": [job]}
    assert local_posts[0][0].endswith("/api/retry-overrides")
    assert local_posts[0][1]["operator_id"] == "cloudflare-access:owner"
    assert remote_posts[1][1]["action"] == "FINISH"
    assert remote_posts[1][1]["status"] == "APPLIED"
    assert remote_posts[2][1] == {"action": "SYNC_JOBS", "items": [applied_job]}


def test_local_operator_bridge_transport_requires_dedicated_secret(monkeypatch) -> None:
    module = _sync_module()
    monkeypatch.delenv("DASHBOARD_OPERATOR_BRIDGE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="credential is not configured"):
        module._get_local_json("http://127.0.0.1:8765/api/retry-jobs")
    with pytest.raises(RuntimeError, match="credential is not configured"):
        module._post_local_json("http://127.0.0.1:8765/api/retry-overrides", {})


def test_operator_retry_bridge_auth_failure_leaves_cloud_lease_reclaimable(monkeypatch) -> None:
    module = _sync_module()
    job = {
        "job_id": "e" * 64, "task_type": "ACTIVE_IMPACT", "title": "Gold",
        "state": "BACKING_OFF", "priority": "NORMAL",
        "available_at": "2026-08-19T06:00:00+00:00", "attempt_count": 3,
        "original_available_at": "2026-08-19T06:00:00+00:00",
    }
    command = {
        "request_id": "request-auth-failure", "job_id": job["job_id"],
        "operator_id": "cloudflare-access:owner", "mode": "IMMEDIATE",
        "reason": "fix deployed", "expected_state": "BACKING_OFF",
        "expected_available_at": job["available_at"], "lease_token": "lease-auth-failure",
    }
    monkeypatch.setattr(module, "_get_local_json", lambda _url: {"items": [job]})
    monkeypatch.setattr(
        module, "_post_local_json",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("operator bridge authorization failed")),
    )
    monkeypatch.setattr(module, "_get_json", lambda *_args, **_kwargs: {"item": command})
    remote_posts = []
    monkeypatch.setattr(
        module, "_post_json",
        lambda url, body, _config: remote_posts.append((url, json.loads(body))) or {},
    )

    with pytest.raises(RuntimeError, match="authorization failed"):
        module._sync_operator_retries({}, {
            "local_status_url": "http://127.0.0.1:8765/api/status",
            "remote_ingest_url": "https://example.workers.dev/api/ingest",
            "token": "test",
        })

    assert len(remote_posts) == 1
    assert remote_posts[0][1] == {"action": "SYNC_JOBS", "items": [job]}


@pytest.mark.parametrize("status,code", [
    ("CONFLICT", "JOB_STATE_CHANGED"),
    ("CONFLICT", "JOB_NOT_MUTABLE"),
])
def test_operator_retry_state_races_finish_with_explicit_terminal_result(
    monkeypatch, status, code,
) -> None:
    module = _sync_module()
    job = {
        "job_id": "f" * 64, "task_type": "ACTIVE_IMPACT", "title": "Gold",
        "state": "BACKING_OFF", "priority": "NORMAL",
        "available_at": "2026-08-19T06:00:00+00:00", "attempt_count": 3,
        "original_available_at": "2026-08-19T06:00:00+00:00",
    }
    command = {
        "request_id": f"request-{code}", "job_id": job["job_id"],
        "operator_id": "cloudflare-access:owner", "mode": "IMMEDIATE",
        "reason": "fix deployed", "expected_state": "BACKING_OFF",
        "expected_available_at": job["available_at"], "lease_token": f"lease-{code}",
    }
    local_reads = iter(({"items": [job]}, {"items": [job]}))
    monkeypatch.setattr(module, "_get_local_json", lambda _url: next(local_reads))
    monkeypatch.setattr(module, "_post_local_json", lambda *_args: {
        "results": [{"request_id": command["request_id"], "job_id": job["job_id"],
                     "status": status, "code": code, "current": {"state": "LEASED"}}],
    })
    claims = iter(({"item": command}, {"item": None}))
    monkeypatch.setattr(module, "_get_json", lambda *_args, **_kwargs: next(claims))
    remote_posts = []
    monkeypatch.setattr(
        module, "_post_json",
        lambda url, body, _config: remote_posts.append((url, json.loads(body))) or {},
    )

    module._sync_operator_retries({}, {
        "local_status_url": "http://127.0.0.1:8765/api/status",
        "remote_ingest_url": "https://example.workers.dev/api/ingest",
        "token": "test",
    })

    finish = next(body for _url, body in remote_posts if body.get("action") == "FINISH")
    assert finish["status"] == status
    assert finish["result"]["code"] == code


def test_operator_retry_worker_urls_keep_human_and_machine_planes_separate() -> None:
    module = _sync_module()
    config = {
        "local_status_url": "http://127.0.0.1:8765/api/status",
        "remote_ingest_url": "https://example.workers.dev/api/ingest",
    }
    assert module._operator_retry_worker_url(config) == (
        "https://example.workers.dev/api/operator-retry-worker"
    )
    assert module._local_retry_url(config, "/api/retry-jobs") == (
        "http://127.0.0.1:8765/api/retry-jobs"
    )
