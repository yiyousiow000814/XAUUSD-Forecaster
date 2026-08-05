"""Forward market adapter boundary and causal snapshot construction."""

from __future__ import annotations

import json
import gzip
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import deque
from typing import Protocol

import numpy as np

from .forward_ledger import canonical_hash


UTC = timezone.utc
FEATURE_VERSION = "forward-market-v1"


@dataclass(frozen=True)
class MarketObservation:
    event_time: datetime
    received_time: datetime
    bid: float
    ask: float

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask < self.bid:
            raise ValueError("market observation must be positive and non-crossed")
        for value in (self.event_time, self.received_time):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("market observation times must be timezone-aware")


class MarketProvider(Protocol):
    name: str

    def observations(self, decision_time: datetime) -> list[MarketObservation]: ...


class NullMarketProvider:
    name = "unconfigured"

    def observations(self, decision_time: datetime) -> list[MarketObservation]:
        return []


class JsonlMarketProvider:
    """Read an external live quote bridge without controlling or trading it."""

    name = "jsonl-live-quote-bridge"

    def __init__(self, path: str | Path, expected_symbol: str = "XAUUSD") -> None:
        self.path = Path(path)
        self.expected_symbol = expected_symbol
        self._offsets: dict[Path, int] = {}
        self._loaded_gzip: set[Path] = set()
        self._cache: deque[MarketObservation] = deque()

    def observations(self, decision_time: datetime) -> list[MarketObservation]:
        if not self.path.exists():
            return []
        cutoff = decision_time - timedelta(minutes=61)
        files = self._source_files()
        for source in files:
            if source.suffix == ".gz":
                self._load_gzip_once(source)
            else:
                self._load_incremental(source)
        while self._cache and self._cache[0].event_time < cutoff:
            self._cache.popleft()
        return sorted(
            [
                row
                for row in self._cache
                if cutoff <= row.event_time <= decision_time
                and row.received_time <= decision_time
            ],
            key=lambda row: (row.event_time, row.received_time),
        )

    def _source_files(self) -> list[Path]:
        if self.path.is_file():
            return [self.path]
        files = sorted(
            [*self.path.glob("*.jsonl"), *self.path.glob("*.jsonl.gz")],
            key=lambda item: item.name,
        )
        return files[-2:]

    def _load_gzip_once(self, source: Path) -> None:
        if source in self._loaded_gzip:
            return
        with gzip.open(source, "rt", encoding="utf-8") as handle:
            for line in handle:
                self._cache.append(self._parse_line(line, source))
        self._loaded_gzip.add(source)

    def _load_incremental(self, source: Path) -> None:
        offset = self._offsets.get(source, 0)
        if source.stat().st_size < offset:
            raise ValueError(f"live quote file was truncated: {source}")
        with source.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            while True:
                line_start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    handle.seek(line_start)
                    break
                self._cache.append(self._parse_line(line, source))
            self._offsets[source] = handle.tell()

    def _parse_line(self, line: str, source: Path) -> MarketObservation:
        item = json.loads(line)
        symbol = str(item.get("symbol", self.expected_symbol))
        if symbol.casefold() != self.expected_symbol.casefold():
            raise ValueError(
                f"unexpected quote symbol {symbol!r} in {source}; "
                f"expected {self.expected_symbol!r}"
            )
        event = datetime.fromisoformat(item["event_time"].replace("Z", "+00:00"))
        received = datetime.fromisoformat(
            item["received_time"].replace("Z", "+00:00")
        )
        return MarketObservation(event, received, float(item["bid"]), float(item["ask"]))


def _at_or_before(
    observations: list[MarketObservation], target: datetime
) -> MarketObservation | None:
    eligible = [row for row in observations if row.event_time <= target]
    return eligible[-1] if eligible else None


def build_forward_snapshot(
    observations: list[MarketObservation],
    decision_time: datetime,
    collected_at: datetime,
    source: str,
    u5: float | None = None,
    u5_status: str = "WARMUP",
    active_signal: bool = False,
) -> dict:
    visible = [
        row
        for row in observations
        if row.event_time <= decision_time and row.received_time <= decision_time
    ]
    reasons: list[str] = []
    features: dict[str, float | int | None] = {}
    if not visible:
        reasons.append("MARKET_DATA_MISSING")
        health = "MISSING"
        latest = None
    else:
        latest = visible[-1]
        staleness = (decision_time - latest.received_time).total_seconds()
        if staleness < 0:
            raise ValueError("market snapshot contains post-decision receipt")
        health = "OK" if staleness <= 20 else "STALE"
        if health != "OK":
            reasons.append("MARKET_DATA_STALE")
        mids = np.array([(row.bid + row.ask) / 2.0 for row in visible])
        times = [row.event_time for row in visible]
        for horizon in (1, 5, 15, 30, 60):
            prior = _at_or_before(visible, decision_time - timedelta(minutes=horizon))
            name = f"return_{horizon}m"
            if prior is None or abs(
                (decision_time - timedelta(minutes=horizon) - prior.event_time).total_seconds()
            ) > 60:
                features[name] = None
                reasons.append(f"RETURN_{horizon}M_UNAVAILABLE")
            else:
                features[name] = math.log(
                    ((latest.bid + latest.ask) / 2.0) / ((prior.bid + prior.ask) / 2.0)
                )
        changes = np.diff(mids)
        features["tick_count_1m"] = sum(
            row.event_time > decision_time - timedelta(minutes=1) for row in visible
        )
        features["tick_count_5m"] = sum(
            row.event_time > decision_time - timedelta(minutes=5) for row in visible
        )
        features["tick_speed_5m_per_second"] = features["tick_count_5m"] / 300.0
        up = int(np.count_nonzero(changes > 0))
        down = int(np.count_nonzero(changes < 0))
        features["quote_imbalance_60m"] = (
            (up - down) / (up + down) if up + down else None
        )
        log_returns = np.diff(np.log(mids))
        features["realized_volatility_60m"] = (
            float(np.sqrt(np.sum(log_returns * log_returns)))
            if log_returns.size else None
        )
        features["source_staleness_seconds"] = staleness
    if u5 is None or not math.isfinite(u5) or u5 <= 0:
        reasons.append("U5_WARMUP")
        u5 = None
        u5_status = "WARMUP"
    snapshot_id = f"XAU-SNAPSHOT-{decision_time.strftime('%Y%m%dT%H%M%SZ')}"
    return {
        "snapshot_id": snapshot_id,
        "decision_time": decision_time,
        "collected_at": collected_at,
        "data_role": "FORWARD",
        "source": source,
        "source_event_time": latest.event_time if latest else None,
        "source_received_time": latest.received_time if latest else None,
        "bid": latest.bid if latest else None,
        "ask": latest.ask if latest else None,
        "spread": latest.ask - latest.bid if latest else None,
        "features": features,
        "feature_version": FEATURE_VERSION,
        "u5": u5,
        "u5_status": u5_status,
        "data_health": health,
        "active_signal": active_signal,
        "reason_codes": tuple(dict.fromkeys(reasons)),
        "snapshot_hash": canonical_hash(features),
    }
