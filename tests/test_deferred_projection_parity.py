from datetime import UTC, datetime, timedelta
import importlib.util
import io
import json
from pathlib import Path
import urllib.error
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
OBSERVE_ATTEMPT = "c" * 32


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


class _Response:
    def __init__(self, payload: dict, headers: dict[str, str]) -> None:
        self._body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


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
        version_id=version, git_sha=revision,
        producer_revision=revision, routes=list(module.BUILDERS),
        required_after=generated - timedelta(seconds=1),
        observe_attempt=OBSERVE_ATTEMPT,
    )
    assert result["state"] == "PASSED"
    assert all(item["state"] == "PASSED" for item in result["routes"])


def test_real_entrypoint_owns_http_identity_for_all_deferred_routes(monkeypatch) -> None:
    module = _module()
    revision = "b" * 40
    version = "11111111-1111-4111-8111-111111111111"
    generated = datetime.now(UTC)
    authority = _authority(generated.isoformat())
    requests = []

    def urlopen(request, *, timeout):
        assert timeout == 15
        requests.append(request)
        if request.full_url == module.LOCAL_AUDIT_URL:
            return _Response(authority, {})
        parsed = urlsplit(request.full_url)
        route = parsed.path
        assert parse_qs(parsed.query) == {"__release_observe": [OBSERVE_ATTEMPT]}
        payload = json.loads(
            module.BUILDERS[route](authority, revision).decode("utf-8")
        )
        return _Response(payload, {
            "X-Aurum-Worker-Version": version,
            "X-Aurum-Git-SHA": revision,
        })

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    result = module.verify(
        version_id=version, git_sha=revision,
        producer_revision=revision, routes=list(module.BUILDERS),
        required_after=generated - timedelta(seconds=1),
        observe_attempt=OBSERVE_ATTEMPT,
    )

    assert result["state"] == "PASSED"
    assert [urlsplit(request.full_url).path for request in requests[1:]] == list(
        module.BUILDERS
    )
    assert all(
        request.get_header("User-agent") == module.RELEASE_CONTROL_USER_AGENT
        for request in requests
    )
    assert requests[0].get_header("Cloudflare-workers-version-overrides") is None
    assert all(
        request.get_header("Cloudflare-workers-version-overrides")
        == f'{module.WORKER_NAME}="{version}"'
        for request in requests[1:]
    )
    assert all(request.get_header("Cache-control") == "no-cache" for request in requests[1:])
    assert all(request.get_header("Pragma") == "no-cache" for request in requests[1:])


def test_remote_403_remains_pending(monkeypatch) -> None:
    module = _module()
    generated = datetime.now(UTC)
    authority = _authority(generated.isoformat())

    def urlopen(request, *, timeout):
        if request.full_url == module.LOCAL_AUDIT_URL:
            return _Response(authority, {})
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(b"error code: 1010")
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    result = module.verify(
        version_id="11111111-1111-4111-8111-111111111111",
        git_sha="b" * 40, producer_revision="b" * 40,
        routes=list(module.BUILDERS),
        required_after=generated - timedelta(seconds=1),
        observe_attempt=OBSERVE_ATTEMPT,
    )
    assert result["state"] == "PENDING"
    assert all(item["reason"] == "PROJECTION_READ_PENDING" for item in result["routes"])


def test_http_200_with_wrong_candidate_identity_fails_closed(monkeypatch) -> None:
    module = _module()
    revision = "b" * 40
    generated = datetime.now(UTC)
    authority = _authority(generated.isoformat())
    route = "/api/audit-decisions"

    def urlopen(request, *, timeout):
        if request.full_url == module.LOCAL_AUDIT_URL:
            return _Response(authority, {})
        payload = json.loads(module.BUILDERS[route](authority, revision).decode("utf-8"))
        return _Response(payload, {
            "X-Aurum-Worker-Version": "22222222-2222-4222-8222-222222222222",
            "X-Aurum-Git-SHA": revision,
        })

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    result = module.verify(
        version_id="11111111-1111-4111-8111-111111111111",
        git_sha=revision, producer_revision=revision, routes=[route],
        required_after=generated - timedelta(seconds=1),
        observe_attempt=OBSERVE_ATTEMPT,
    )
    assert result["state"] == "FAILED"
    assert result["reason"] == "EXACT_VERSION_IDENTITY_MISMATCH"


def test_response_parity_mismatch_remains_pending(monkeypatch) -> None:
    module = _module()
    revision = "b" * 40
    version = "11111111-1111-4111-8111-111111111111"
    generated = datetime.now(UTC)
    authority = _authority(generated.isoformat())
    route = "/api/audit-briefs"
    observed = json.loads(module.BUILDERS[route](authority, revision).decode("utf-8"))
    observed["daily_news_briefs"] = [{"unexpected": True}]

    def urlopen(request, *, timeout):
        if request.full_url == module.LOCAL_AUDIT_URL:
            return _Response(authority, {})
        return _Response(observed, {
            "X-Aurum-Worker-Version": version,
            "X-Aurum-Git-SHA": revision,
        })

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    result = module.verify(
        version_id=version, git_sha=revision,
        producer_revision=revision, routes=[route],
        required_after=generated - timedelta(seconds=1),
        observe_attempt=OBSERVE_ATTEMPT,
    )
    assert result["state"] == "PENDING"
    assert result["reason"] == "CANDIDATE_PROJECTION_PARITY_PENDING"


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
        version_id=version, git_sha=revision,
        producer_revision=revision, routes=[route],
        required_after=generated - timedelta(seconds=1),
        observe_attempt=OBSERVE_ATTEMPT,
    )
    assert result["state"] == "PENDING"
    assert result["reason"] == "CANDIDATE_PROJECTION_PRODUCER_PENDING"


def test_distinct_observe_attempt_escapes_stale_cached_projection(monkeypatch) -> None:
    module = _module()
    revision = "b" * 40
    version = "11111111-1111-4111-8111-111111111111"
    fresh_at = datetime.now(UTC)
    stale_authority = _authority((fresh_at - timedelta(hours=1)).isoformat())
    fresh_authority = _authority(fresh_at.isoformat())
    current_authority = stale_authority
    cache: dict[str, tuple[dict, dict[str, str]]] = {}

    def urlopen(request, *, timeout):
        nonlocal current_authority
        if request.full_url == module.LOCAL_AUDIT_URL:
            return _Response(current_authority, {})
        if request.full_url not in cache:
            route = urlsplit(request.full_url).path
            payload = json.loads(
                module.BUILDERS[route](current_authority, revision).decode("utf-8")
            )
            cache[request.full_url] = (payload, {
                "X-Aurum-Worker-Version": version,
                "X-Aurum-Git-SHA": revision,
            })
        payload, headers = cache[request.full_url]
        return _Response(payload, headers)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    stale = module.verify(
        version_id=version, git_sha=revision, producer_revision=revision,
        routes=list(module.BUILDERS), required_after=fresh_at - timedelta(seconds=1),
        observe_attempt="a" * 32,
    )
    current_authority = fresh_authority
    same_attempt = module.verify(
        version_id=version, git_sha=revision, producer_revision=revision,
        routes=list(module.BUILDERS), required_after=fresh_at - timedelta(seconds=1),
        observe_attempt="a" * 32,
    )
    next_attempt = module.verify(
        version_id=version, git_sha=revision, producer_revision=revision,
        routes=list(module.BUILDERS), required_after=fresh_at - timedelta(seconds=1),
        observe_attempt="b" * 32,
    )

    assert stale["state"] == "PENDING"
    assert same_attempt["state"] == "PENDING"
    assert next_attempt["state"] == "PASSED"
    assert len(cache) == 2 * len(module.BUILDERS)


def test_genuinely_stale_generated_at_remains_pending(monkeypatch) -> None:
    module = _module()
    revision = "b" * 40
    version = "11111111-1111-4111-8111-111111111111"
    generated = datetime.now(UTC) - timedelta(minutes=5)
    authority = _authority(generated.isoformat())
    route = "/api/audit-briefs"
    responses = [
        (authority, {}),
        (json.loads(module.BUILDERS[route](authority, revision).decode("utf-8")), {
            "X-Aurum-Worker-Version": version,
            "X-Aurum-Git-SHA": revision,
        }),
    ]
    monkeypatch.setattr(module, "_read_json", lambda *_a, **_k: responses.pop(0))

    result = module.verify(
        version_id=version, git_sha=revision, producer_revision=revision,
        routes=[route], required_after=datetime.now(UTC),
        observe_attempt=OBSERVE_ATTEMPT,
    )

    assert result["state"] == "PENDING"
    assert result["reason"] == "CANDIDATE_PROJECTION_PARITY_PENDING"
