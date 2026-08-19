"""Persistent Gemini request accounting using anonymous key fingerprints."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .credential_identity import derived_credential_id

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

    def reserve(self, api_key: str, now: datetime | None = None) -> bool:
        """Reserve and persist one request before it is sent."""
        fingerprint = key_fingerprint(api_key)
        with self._lock:
            state = self._load(now)
            sent = int(state["counts"].get(fingerprint, 0))
            if sent >= self.daily_limit:
                return False
            state["counts"][fingerprint] = sent + 1
            self._save(state)
            return True

    def seed(self, api_key: str, sent: int, now: datetime | None = None) -> None:
        """Set a conservative known usage floor for the active quota day."""
        fingerprint = key_fingerprint(api_key)
        with self._lock:
            state = self._load(now)
            state["counts"][fingerprint] = max(
                int(state["counts"].get(fingerprint, 0)),
                min(max(0, int(sent)), self.daily_limit),
            )
            self._save(state)

    def snapshot(self, api_keys: tuple[str, ...], now: datetime | None = None) -> dict:
        with self._lock:
            state = self._load(now)
        keys = []
        for slot, api_key in enumerate(api_keys, 1):
            sent = int(state["counts"].get(key_fingerprint(api_key), 0))
            keys.append(
                {
                    "slot": slot,
                    "fingerprint": key_fingerprint(api_key),
                    "sent": sent,
                    "remaining": max(0, self.daily_limit - sent),
                    "status": "AVAILABLE" if sent < self.daily_limit else "DAILY_LIMIT",
                }
            )
        return {
            "quota_day_pacific": state["quota_day"],
            "daily_limit_per_key": self.daily_limit,
            "next_reset_at": self.next_reset_at(now).isoformat(),
            "keys": keys,
            "total_sent": sum(item["sent"] for item in keys),
            "total_remaining": sum(item["remaining"] for item in keys),
        }
