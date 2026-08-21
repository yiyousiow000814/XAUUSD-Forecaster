"""External production smoke probe with stable operational error codes."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "https://aurum-signal-room.yiyousiow1234.workers.dev"
TIMEOUT_SECONDS = 20
PAGE_MARKERS = {
    "/": "Aurum Signal Room",
    "/health": "系统健康状态",
    "/audit": "证据台页面",
}


class ProbeFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def read(url: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(url, headers={
        "Accept": "text/html,application/json",
        "User-Agent": "AurumExternalHealthProbe/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return (
                int(response.status), response.read(2_000_000),
                str(response.headers.get("Content-Type") or ""),
            )
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProbeFailure(
            "OPS_PUBLIC_ENDPOINT_UNAVAILABLE", f"{url}: {error}",
        ) from error


def check_public_surface(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    evidence: list[str] = []
    for path, marker in PAGE_MARKERS.items():
        status, body, _ = read(base + path)
        text = body.decode("utf-8", errors="replace")
        if status != 200:
            raise ProbeFailure(
                "OPS_PUBLIC_ENDPOINT_UNAVAILABLE", f"{path}: HTTP {status}",
            )
        if marker not in text:
            raise ProbeFailure(
                "OPS_PUBLIC_RENDER_CONTRACT_FAILED",
                f"{path}: missing server-rendered marker {marker!r}",
            )
        evidence.append(f"{path}=200:{len(body)}B")

    for path in ("/api/status",):
        status, body, _ = read(base + path)
        if status != 200:
            raise ProbeFailure("OPS_PUBLIC_ENDPOINT_UNAVAILABLE", f"{path}: HTTP {status}")
        try:
            payload = json.loads(body)
        except (TypeError, ValueError) as error:
            raise ProbeFailure(
                "OPS_PUBLIC_RESPONSE_INVALID", f"{path}: invalid JSON",
            ) from error
        if not isinstance(payload, dict):
            raise ProbeFailure(
                "OPS_PUBLIC_RESPONSE_INVALID", f"{path}: response contract mismatch",
            )
        evidence.append(f"{path}=200:{len(body)}B")
    return evidence


def main() -> int:
    try:
        # This operational probe intentionally targets one fixed public surface.
        # Keeping the destination out of CLI input prevents it from becoming a
        # general-purpose server-side URL fetcher.
        evidence = check_public_surface(DEFAULT_BASE_URL)
    except ProbeFailure as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    print("OPS_PUBLIC_SURFACE_OK " + " ".join(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
