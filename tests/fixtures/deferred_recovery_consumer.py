"""Run the normal deferred consumer with an explicit isolated network adapter.

No receipt, health, count, digest or verdict is supplied by this adapter.
It only maps the provider URL and refuses redirects/non-owned destinations.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fixture-provider", required=True)
    parser.add_argument("--runtime-root", required=True)
    fixture, remaining = parser.parse_known_args()
    runtime = Path(fixture.runtime_root).resolve(strict=True)
    marker = runtime / "fixture-owned.json"
    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != {"root": str(runtime)}:
        raise ValueError("ISOLATED_CONSUMER_OWNERSHIP_REQUIRED")
    if runtime.is_relative_to(ROOT) or any(part.lower() in {
        "xauusd-forecaster-runtime", "xauusd-forecaster", "xauusd-forecaster.local",
    } for part in runtime.parts):
        raise ValueError("ISOLATED_CONSUMER_PRODUCTION_PATH_DENIED")
    provider = urlsplit(fixture.fixture_provider)
    if (provider.scheme != "https" or provider.hostname != "127.0.0.1" or not provider.port
            or provider.path or provider.username or provider.password or provider.query or provider.fragment):
        raise ValueError("ISOLATED_CONSUMER_LOOPBACK_REQUIRED")
    sys.argv = [sys.argv[0], "--runtime-root", str(runtime), "--producer-root", str(ROOT), *remaining]
    spec = importlib.util.spec_from_file_location("isolated_deferred_consumer", ROOT / "scripts/check_deferred_projection_parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REMOTE_URLS = {"/api/news-evidence": fixture.fixture_provider + "/api/news-evidence"}

    class OwnedOnly(urllib.request.BaseHandler):
        def https_request(self, request):
            target = urlsplit(request.full_url)
            if target.netloc != provider.netloc or target.path != "/api/news-evidence":
                raise ValueError("ISOLATED_CONSUMER_ENDPOINT_DENIED")
            return request

        def http_request(self, request):
            raise ValueError("ISOLATED_CONSUMER_ENDPOINT_DENIED")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError("ISOLATED_CONSUMER_REDIRECT_DENIED")

    urllib.request.install_opener(urllib.request.build_opener(
        urllib.request.ProxyHandler({}), OwnedOnly(), NoRedirect(),
    ))
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
