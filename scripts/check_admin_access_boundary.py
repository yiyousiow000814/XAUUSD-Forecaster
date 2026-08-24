"""Anonymous production probe for the Cloudflare Access Admin boundary."""

from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://aurum-signal-room.yiyousiow1234.workers.dev"
TIMEOUT_SECONDS = 20
PUBLIC_PATHS = ("/", "/health", "/api/status")
HUMAN_PATHS = (
    "/admin",
    "/admin/assistant",
    "/admin/retry-jobs",
    "/admin/ai-usage",
    "/admin/auth-complete",
    "/admin/api/session",
    "/admin/api/admin-status",
    "/admin/api/assistant-health",
    "/admin/api/assistant-chat",
    "/admin/api/assistant-conversations",
    "/admin/api/news-questions",
    "/admin/api/operator-retry",
    "/assistant",
    "/retry-jobs",
    "/status",
)
APPLICATION_AUTH_PATHS = (
    "/api/admin-status",
    "/api/assistant-health",
    "/api/assistant-chat",
    "/api/assistant-conversations",
    "/api/news-questions",
    "/api/operator-retry",
)
MACHINE_PATHS = (
    "/api/assistant-worker/chat",
    "/api/assistant-worker/conversations",
    "/api/assistant-worker/news-questions",
    "/api/operator-retry-worker",
)


class ProbeFailure(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def read(url: str) -> tuple[int, bytes, str | None]:
    request = urllib.request.Request(url, headers={
        "Accept": "text/html,application/json",
        "User-Agent": "AurumAdminAccessProbe/1.0",
    })
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status), response.read(100_000), response.headers.get("Location")
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(100_000), error.headers.get("Location")
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProbeFailure(f"{url}: {error}") from error


def _is_access_login(location: str | None) -> bool:
    if not location:
        return False
    parsed = urllib.parse.urlparse(location)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.endswith(".cloudflareaccess.com")
        and parsed.path.startswith("/cdn-cgi/access/login")
    )


def check_admin_access_boundary(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    evidence: list[str] = []
    for path in PUBLIC_PATHS:
        status, _, location = read(base + path)
        if _is_access_login(location) or status not in {200, 301, 302, 303, 307, 308}:
            raise ProbeFailure(f"public {path}: expected public response, got {status}")
        evidence.append(f"public:{path}={status}")
    for path in HUMAN_PATHS:
        status, _, location = read(base + path)
        if status not in {301, 302, 303, 307, 308} or not _is_access_login(location):
            raise ProbeFailure(
                f"human {path}: expected Cloudflare Access login redirect, got {status}",
            )
        evidence.append(f"access:{path}={status}")
    for path in APPLICATION_AUTH_PATHS:
        status, _, location = read(base + path)
        if _is_access_login(location):
            raise ProbeFailure(f"application-auth {path}: stale separate Access destination")
        if status != 401:
            raise ProbeFailure(f"application-auth {path}: expected Worker 401, got {status}")
        evidence.append(f"application-auth:{path}=401")
    for path in MACHINE_PATHS:
        status, _, location = read(base + path)
        if _is_access_login(location):
            raise ProbeFailure(f"machine {path}: incorrectly covered by human Access")
        if status != 401:
            raise ProbeFailure(f"machine {path}: expected Worker 401, got {status}")
        evidence.append(f"machine:{path}=401")
    return evidence


def main() -> int:
    try:
        evidence = check_admin_access_boundary(DEFAULT_BASE_URL)
    except ProbeFailure as error:
        print(f"OPS_ADMIN_ACCESS_BOUNDARY_FAILED: {error}", file=sys.stderr)
        return 1
    print("PRODUCTION_ANONYMOUS_ACCESS_RESULT ACCESS_PASS " + " ".join(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
