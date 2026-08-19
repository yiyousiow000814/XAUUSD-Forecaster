"""Secret-safe, deterministic identities for configured API credentials."""

from __future__ import annotations

import hmac


def derived_credential_id(api_key: str) -> str:
    """Derive a versioned non-secret ID from a high-entropy API key."""
    digest = hmac.digest(
        api_key.encode("utf-8"),
        b"xauusd-forecaster/credential-id/v1",
        "sha256",
    )
    return f"hmac-v1-{digest.hex()[:32]}"
