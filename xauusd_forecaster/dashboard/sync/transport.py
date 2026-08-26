"""Dashboard Sync local/remote transport and target configuration."""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from xauusd_forecaster.dashboard.sync.progress import (
    DEFAULT_LEARNING_HISTORY_STATE,
    DEFAULT_LEARNING_STATE,
    DEFAULT_MARKET_HISTORY_STATE,
    DEFAULT_NEWS_EVIDENCE_STATE,
    DEFAULT_NEWS_STATE,
    DEFAULT_RESOURCE_SCHEDULE_STATE,
    _target_state_path,
    _write_runtime_signal,
)

LOCAL_STATUS_TIMEOUT_SECONDS = 20
REMOTE_POST_TIMEOUT_SECONDS = 30
SYNC_STATE_ROOT = DEFAULT_NEWS_STATE.parent.resolve()

class RemoteInvariantViolation(RuntimeError):
    """A remote resource answered but its persisted state is contradictory."""

    def __init__(self, payload: dict) -> None:
        self.error_code = str(
            payload.get("error_code") or "REMOTE_STATE_INVARIANT_VIOLATION"
        )
        checks = payload.get("checks")
        self.evidence = {
            "violation_count": int(payload.get("violation_count") or 0),
            "checks": checks[:12] if isinstance(checks, list) else [],
        }
        if isinstance(payload.get("contradictions"), dict):
            self.evidence["contradictions"] = dict(
                list(payload["contradictions"].items())[:12]
            )
        if payload.get("staging_generation_id"):
            self.evidence["staging_generation_id"] = str(
                payload["staging_generation_id"]
            )[:64]
        super().__init__(
            f"remote invariant check failed: {self.error_code} "
            f"({self.evidence['violation_count']} violations)"
        )


def _remote_request_headers(url: str, config: dict) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "User-Agent": "AurumSignalRoomMirror/1.0",
    }
    sites_bypass_token = os.environ.get("SITES_BYPASS_TOKEN", "").strip()
    remote_host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if sites_bypass_token and remote_host.endswith(".chatgpt.site"):
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_bypass_token}"
    return headers


def _post_json(url: str, payload: bytes, config: dict) -> dict:
    headers = {
        **_remote_request_headers(url, config),
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=REMOTE_POST_TIMEOUT_SECONDS
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"dashboard sync returned HTTP {response.status}")
            body = response.read()
    except urllib.error.HTTPError as error:
        try:
            failure = json.loads(error.read())
        except (TypeError, ValueError):
            raise error
        if isinstance(failure, dict) and failure.get("error_code"):
            raise RemoteInvariantViolation(failure) from error
        raise error
    try:
        result = json.loads(body) if body else {}
    except (TypeError, ValueError):
        result = {}
    _write_runtime_signal(result)
    return result if isinstance(result, dict) else {}


def _get_json(
    url: str,
    config: dict,
    *,
    timeout_seconds: float = REMOTE_POST_TIMEOUT_SECONDS,
) -> dict:
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise ValueError("dashboard GET timeout is invalid")
    request = urllib.request.Request(url, headers={
        **_remote_request_headers(url, config),
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(
            request, timeout=min(REMOTE_POST_TIMEOUT_SECONDS, float(timeout_seconds)),
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (TypeError, ValueError):
            raise error
        if isinstance(payload, dict) and payload.get("error_code"):
            raise RemoteInvariantViolation(payload) from error
        raise error


def _assistant_worker_id() -> str:
    worker_suffix = re.sub(
        r"[^A-Za-z0-9._:-]", "-",
        os.environ.get("COMPUTERNAME", "windows-sync"),
    )[:64]
    return f"dashboard-sync:{worker_suffix}"


def _operator_retry_worker_url(config: dict) -> str:
    remote = urllib.parse.urlsplit(str(config["remote_ingest_url"]))
    return urllib.parse.urlunsplit((
        remote.scheme, remote.netloc, "/api/operator-retry-worker", "", "",
    ))


def _local_retry_url(config: dict, path: str) -> str:
    local = urllib.parse.urlsplit(str(config["local_status_url"]))
    return urllib.parse.urlunsplit((local.scheme, local.netloc, path, "", ""))


def _post_local_json(url: str, payload: dict) -> dict:
    token = os.environ.get("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "").strip()
    if not 32 <= len(token) <= 512:
        raise RuntimeError("dashboard operator bridge credential is not configured")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AurumOperatorBridge/1.0",
            "X-Aurum-Operator-Bridge-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=LOCAL_STATUS_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code != 207:
            raise
        return json.loads(error.read())


def _get_local_json(url: str) -> dict:
    token = os.environ.get("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "").strip()
    if not 32 <= len(token) <= 512:
        raise RuntimeError("dashboard operator bridge credential is not configured")
    request = urllib.request.Request(
        url, headers={
            "Accept": "application/json",
            "User-Agent": "AurumOperatorBridge/1.0",
            "X-Aurum-Operator-Bridge-Token": token,
        },
    )
    with urllib.request.urlopen(request, timeout=LOCAL_STATUS_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def _validated_sync_state_path(path: Path) -> Path:
    """Keep mutable sync cursors inside the private runtime state directory."""
    if path.is_absolute():
        parent = path.parent
    else:
        parent = SYNC_STATE_ROOT if path.parent == Path(".") else path.parent
    filename = path.name
    allowed_characters = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
    )
    if (
        parent != SYNC_STATE_ROOT
        or not 6 <= len(filename) <= 128
        or not filename[0].isalnum()
        or not filename.endswith(".json")
        or any(character not in allowed_characters for character in filename)
    ):
        raise ValueError(
            f"dashboard sync state path must be one JSON file under {SYNC_STATE_ROOT}"
        )
    return SYNC_STATE_ROOT / filename


def configured_targets(config: dict) -> list[dict]:
    """Resolve legacy or multi-target mirror configuration without sharing state."""
    declared = config.get("targets")
    if not isinstance(declared, list):
        declared = [{**config, "name": config.get("name", "sites"), "legacy": True}]
        cloudflare_url = os.environ.get("CLOUDFLARE_INGEST_URL", "").strip()
        cloudflare_token = os.environ.get("CLOUDFLARE_INGEST_TOKEN", "").strip()
        if cloudflare_url or cloudflare_token:
            declared.append({
                "name": "cloudflare",
                "remote_ingest_url": cloudflare_url,
                "token": cloudflare_token,
                "legacy": False,
            })

    targets = []
    for index, target in enumerate(declared):
        if not isinstance(target, dict):
            raise ValueError(f"dashboard target {index + 1} must be an object")
        if target.get("enabled") is False:
            continue
        name = str(target.get("name") or f"mirror-{index + 1}").strip()
        remote_url = str(target.get("remote_ingest_url") or "").strip()
        token_env = str(target.get("token_env") or "").strip()
        token = str(
            target.get("token") or (os.environ.get(token_env) if token_env else "") or ""
        ).strip()
        if not remote_url.startswith("https://") or not token:
            raise ValueError(f"dashboard target {name!r} needs https URL and token")
        scoped = {
            **config,
            **target,
            "name": name,
            "token": token,
            "legacy": bool(target.get("legacy", False)),
        }
        scoped.pop("targets", None)
        scoped["learning_state_file"] = str(_validated_sync_state_path(_target_state_path(
            Path(target.get(
                "learning_state_file",
                config.get("learning_state_file", DEFAULT_LEARNING_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        )))
        scoped["news_state_file"] = str(_validated_sync_state_path(_target_state_path(
            Path(target.get(
                "news_state_file",
                config.get("news_state_file", DEFAULT_NEWS_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        )))
        scoped["market_history_state_file"] = str(_validated_sync_state_path(_target_state_path(
            Path(target.get(
                "market_history_state_file",
                config.get("market_history_state_file", DEFAULT_MARKET_HISTORY_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        )))
        scoped["learning_history_state_file"] = str(_validated_sync_state_path(_target_state_path(
            Path(target.get(
                "learning_history_state_file",
                config.get("learning_history_state_file", DEFAULT_LEARNING_HISTORY_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        )))
        scoped["news_evidence_state_file"] = str(_validated_sync_state_path(_target_state_path(
            Path(target.get(
                "news_evidence_state_file",
                config.get("news_evidence_state_file", DEFAULT_NEWS_EVIDENCE_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        )))
        scoped["resource_schedule_state_file"] = str(_validated_sync_state_path(_target_state_path(
            Path(target.get(
                "resource_schedule_state_file",
                config.get(
                    "resource_schedule_state_file",
                    DEFAULT_RESOURCE_SCHEDULE_STATE,
                ),
            )),
            name,
            legacy=scoped["legacy"],
        )))
        targets.append(scoped)
    if not targets:
        raise ValueError("dashboard sync has no configured targets")
    return targets

