"""Secret-safe, deterministic identities for configured API credentials."""

from __future__ import annotations

import hashlib
import hmac
import io


def derived_credential_id(api_key: str) -> str:
    """Derive a versioned non-secret ID from a high-entropy API key."""
    digest = hmac.digest(
        api_key.encode("utf-8"),
        b"xauusd-forecaster/credential-id/v1",
        "sha256",
    )
    return f"hmac-v1-{digest.hex()[:32]}"


def legacy_credential_id_for_migration(material: bytes) -> str:
    """Resolve the retired SHA-256 ID only to migrate existing state."""
    digest = hashlib.file_digest(io.BytesIO(material), "sha256")
    return digest.hexdigest()[:12]
