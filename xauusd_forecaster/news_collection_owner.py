"""One bounded news-polling owner isolated from the decision connection."""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from xauusd_forecaster.decision.engine import ForwardEngine
from xauusd_forecaster.evidence.ledger import ForwardLedger
from .market import NullMarketProvider


UTC = timezone.utc
NewsStatus = list[dict[str, object]]
NewsCollector = Callable[[ForwardLedger, datetime], NewsStatus]


def _collect_official_news(ledger: ForwardLedger, observed_at: datetime) -> NewsStatus:
    return ForwardEngine(ledger, NullMarketProvider()).collect_news(observed_at)


class NewsCollectionOwner:
    """Poll news on one background thread with a connection owned by that thread."""

    def __init__(
        self,
        ledger_path: str | Path,
        *,
        poll_seconds: float,
        collector: NewsCollector = _collect_official_news,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ledger_path = Path(ledger_path)
        self._poll_seconds = max(1.0, float(poll_seconds))
        self._collector = collector
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._latest_status: NewsStatus | None = None
        self._last_completed_at: datetime | None = None
        self._in_progress_since: datetime | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("news collection owner may only be started once")
        self._thread = threading.Thread(
            target=self._run,
            name="xauusd-news-collection-owner",
            daemon=True,
        )
        self._thread.start()

    def snapshot(self, observed_at: datetime) -> NewsStatus:
        """Return the last completed poll plus explicit freshness degradation."""
        with self._lock:
            latest = deepcopy(self._latest_status)
            completed_at = self._last_completed_at
            in_progress_since = self._in_progress_since
        if completed_at is None:
            return [
                {
                    "source": "NEWS_COLLECTION_OWNER",
                    "status": "DEGRADED",
                    "reason_code": "NEWS_COLLECTION_PENDING",
                    "in_progress_since": (
                        in_progress_since.isoformat() if in_progress_since else None
                    ),
                }
            ]
        age_seconds = max(0.0, (observed_at - completed_at).total_seconds())
        if age_seconds <= self._poll_seconds * 2:
            return latest or []
        return [
            *(latest or []),
            {
                "source": "NEWS_COLLECTION_OWNER",
                "status": "DEGRADED",
                "reason_code": "NEWS_COLLECTION_STALE",
                "last_completed_at": completed_at.isoformat(),
                "age_seconds": round(age_seconds, 3),
                "poll_in_progress": in_progress_since is not None,
            },
        ]

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        """Request shutdown and wait only for the bounded caller-supplied timeout."""
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout_seconds))
        return not thread.is_alive()

    def _run(self) -> None:
        ledger = ForwardLedger(self._ledger_path, now=self._clock())
        # Schema installers may issue writes.  Release their transaction before
        # any source can block on network I/O so the decision connection never
        # waits behind owner initialization.
        ledger.connection.commit()
        try:
            while not self._stop.is_set():
                started_at = self._clock()
                with self._lock:
                    self._in_progress_since = started_at
                try:
                    status = self._collector(ledger, started_at)
                except Exception as error:
                    status = [
                        {
                            "source": "NEWS_COLLECTION_OWNER",
                            "status": "ERROR",
                            "reason_code": "NEWS_COLLECTION_FAILED",
                            "error_type": type(error).__name__,
                        }
                    ]
                completed_at = self._clock()
                with self._lock:
                    self._latest_status = deepcopy(status)
                    self._last_completed_at = completed_at
                    self._in_progress_since = None
                if self._stop.wait(self._poll_seconds):
                    break
        finally:
            ledger.close()
