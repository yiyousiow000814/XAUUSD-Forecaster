from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_repository_declares_license_security_and_data_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "MIT License" in license_text
    assert "Training data" in readme
    assert "trained model artifacts" in readme
    assert "private vulnerability reporting" in security


def test_public_repository_ignores_local_secret_file_families() -> None:
    ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    assert {".env", ".env.*", ".dev.vars", ".dev.vars.*"} <= ignored
    assert {"*.pem", "*.key", "*.p12", "*.pfx"} <= ignored


def test_public_quota_document_has_no_installation_specific_account_count() -> None:
    quota = (ROOT / "docs" / "AI_PROVIDER_QUOTAS.md").read_text(encoding="utf-8")

    assert "current local installation uses" not in quota
    assert "installation-specific account counts must never" in quota
