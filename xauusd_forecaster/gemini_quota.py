"""Persistent Gemini request accounting using anonymous key fingerprints."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .credential_identity import (
    derived_credential_id,
    legacy_credential_id_for_migration,
)

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
GEMINI_REQUESTS_PER_DAY_PER_KEY = 500


def key_fingerprint(api_key: str) -> str:
    return derived_credential_id(api_key)


class GeminiQuotaLedger:
    """Count attempted requests and reset at Google's Pacific quota boundary."""

    def __init__(self, path: Path, *, daily_limit: int = GEMINI_REQUESTS_PER_DAY_PER_KEY):
        self.path = Path(path)
        self.daily_limit = daily_limit
        self._lock = threading.Lock()

    @staticmethod
    def quota_day(now: datetime | None = None) -> str:
        instant = now or datetime.now(UTC)
        return instant.astimezone(PACIFIC).date().isoformat()

    @staticmethod
    def next_reset_at(now: datetime | None = None) -> datetime:
        instant = (now or datetime.now(UTC)).astimezone(PACIFIC)
        next_midnight = (instant + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return next_midnight.astimezone(UTC)

    def _load(self, now: datetime | None = None) -> dict:
        day = self.quota_day(now)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            state = {}
        if state.get("quota_day") != day:
            return {"quota_day": day, "counts": {}}
        counts = state.get("counts")
        return {"quota_day": day, "counts": counts if isinstance(counts, dict) else {}}

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _migrate_credential_count(
        state: dict, api_key: str,
    ) -> tuple[str, int, bool]:
        """Move a legacy count to its canonical HMAC identity conservatively."""
        counts = state["counts"]
        fingerprint = key_fingerprint(api_key)
        legacy_fingerprint = legacy_credential_id_for_migration(api_key)
        canonical_count = int(counts.get(fingerprint, 0))
        legacy_count = int(counts.get(legacy_fingerprint, 0))
        effective_count = max(canonical_count, legacy_count)
        changed = (
            legacy_fingerprint in counts
            or (
                fingerprint in counts
                and counts[fingerprint] != effective_count
            )
        )
        if changed:
            counts[fingerprint] = effective_count
            counts.pop(legacy_fingerprint, None)
        return fingerprint, effective_count, changed

    def reserve(self, api_key: str, now: datetime | None = None) -> bool:
        """Reserve and persist one request before it is sent."""
        with self._lock:
            state = self._load(now)
            fingerprint, sent, migrated = self._migrate_credential_count(
                state, api_key,
            )
            if sent >= self.daily_limit:
                if migrated:
                    self._save(state)
                return False
            state["counts"][fingerprint] = sent + 1
            self._save(state)
            return True

    def seed(self, api_key: str, sent: int, now: datetime | None = None) -> None:
        """Set a conservative known usage floor for the active quota day."""
        with self._lock:
            state = self._load(now)
            fingerprint, effective, _ = self._migrate_credential_count(
                state, api_key,
            )
            state["counts"][fingerprint] = max(
                effective,
                min(max(0, int(sent)), self.daily_limit),
            )
            self._save(state)

    def snapshot(self, api_keys: tuple[str, ...], now: datetime | None = None) -> dict:
        with self._lock:
            state = self._load(now)
            keys = []
            migrated = False
            for slot, api_key in enumerate(api_keys, 1):
                fingerprint, sent, changed = self._migrate_credential_count(
                    state, api_key,
                )
                migrated = migrated or changed
                keys.append(
                    {
                        "slot": slot,
                        "fingerprint": fingerprint,
                        "sent": sent,
                        "remaining": max(0, self.daily_limit - sent),
                        "status": (
                            "AVAILABLE" if sent < self.daily_limit else "DAILY_LIMIT"
                        ),
                    }
                )
            if migrated:
                self._save(state)
        return {
            "quota_day_pacific": state["quota_day"],
            "daily_limit_per_key": self.daily_limit,
            "next_reset_at": self.next_reset_at(now).isoformat(),
            "keys": keys,
            "total_sent": sum(item["sent"] for item in keys),
            "total_remaining": sum(item["remaining"] for item in keys),
        }
