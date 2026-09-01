"""Git and data provenance for local Dashboard payloads."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable


def deployment_status(
    runtime_sha: str | None,
    expected_sha: str | None,
    module_dirty: bool,
) -> str:
    """Keep unpublished edits distinct from deployed revision drift."""
    if not runtime_sha or not expected_sha:
        return "PROVENANCE_UNKNOWN"
    if runtime_sha != expected_sha:
        return "DEPLOYMENT_DRIFT"
    if module_dirty:
        return "LOCAL_CHANGES"
    return "MATCHED"


class DeploymentProvenanceOwner:
    """Read bounded repository identity through one UTF-8 native boundary."""

    def __init__(
        self,
        *,
        repo: Path,
        storyline_policy_version: str,
        payload_schema_version: str,
        runner: Callable[..., object] = subprocess.run,
    ) -> None:
        self._repo = repo
        self._storyline_policy_version = storyline_policy_version
        self._payload_schema_version = payload_schema_version
        self._runner = runner

    def _git(self, *args: str) -> str | None:
        try:
            result = self._runner(
                ("git", *args),
                cwd=self._repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=5,
                check=True,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return None

    def provenance(
        self,
        generated_at: datetime,
        database_epoch: str | None,
    ) -> dict:
        """Expose code/data identity so a stale mirror cannot look current."""
        runtime_sha = self._git("rev-parse", "HEAD")
        upstream = self._git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
        )
        expected_sha = self._git("rev-parse", upstream or "origin/main")
        module_dirty = bool(self._git("status", "--porcelain", "--", "."))
        return {
            "runtime_git_sha": runtime_sha,
            "expected_git_sha": expected_sha,
            "runtime_dirty": module_dirty,
            "status": deployment_status(runtime_sha, expected_sha, module_dirty),
            "storyline_policy_version": self._storyline_policy_version,
            "payload_schema_version": self._payload_schema_version,
            "payload_generated_at": generated_at.isoformat(),
            "source_database_epoch": database_epoch,
        }
