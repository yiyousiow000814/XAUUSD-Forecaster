"""Serializable causal finite-memory U5 state for Forward operation."""

from __future__ import annotations

import json
import math
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


U5_VERSION = "finite-memory-u5-v5-contiguous-m1"


class U5State:
    def __init__(self) -> None:
        self.midpoints: deque[float] = deque(maxlen=31)
        self.excursions: deque[float] = deque(maxlen=10_000)
        self.last_minute: datetime | None = None
        self.last_u5: float | None = None
        self.continuity_resets = 0

    @property
    def status(self) -> str:
        return "READY" if len(self.excursions) == 10_000 else "WARMUP"

    def update(
        self,
        minute: datetime,
        bid_close: float,
        ask_close: float,
    ) -> float | None:
        if self.last_minute is not None and minute <= self.last_minute:
            return self.last_u5
        if self.last_minute is not None and minute - self.last_minute != timedelta(minutes=1):
            # An A30 observation is defined by 31 consecutive completed M1
            # closes.  Preserve the finite 10,000-observation authority window,
            # but rebuild the current path after any missing minute.
            self.midpoints.clear()
            self.continuity_resets += 1
        midpoint = (bid_close + ask_close) / 2.0
        if midpoint <= 0 or ask_close < bid_close:
            raise ValueError("U5 update requires positive non-crossed close")
        self.midpoints.append(midpoint)
        self.last_minute = minute
        if len(self.midpoints) == 31:
            base = math.log(self.midpoints[0])
            logs = [math.log(value) for value in self.midpoints]
            excursion = max(max(logs) - base, base - min(logs))
            self.excursions.append(excursion)
        if self.status == "READY":
            tail = float(np.quantile(np.asarray(self.excursions), 0.99))
            cost_floor = 2.0 * (ask_close - bid_close) / midpoint
            self.last_u5 = max(tail, self.excursions[-1], cost_floor)
        else:
            self.last_u5 = None
        return self.last_u5

    def as_dict(self) -> dict:
        return {
            "schema": "xauusd.forward.u5-state.v1",
            "u5_version": U5_VERSION,
            "data_role": "WARMUP_ONLY_AND_FORWARD_STATE",
            "midpoints": list(self.midpoints),
            "excursions": list(self.excursions),
            "last_minute": self.last_minute.isoformat() if self.last_minute else None,
            "last_u5": self.last_u5,
            "status": self.status,
            "continuity_resets": self.continuity_resets,
            "training_allowed": False,
            "performance_evaluation_allowed": False,
        }

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.as_dict(), separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "U5State":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        state = cls()
        state.midpoints.extend(float(value) for value in payload["midpoints"])
        state.excursions.extend(float(value) for value in payload["excursions"])
        state.last_minute = (
            datetime.fromisoformat(payload["last_minute"])
            if payload.get("last_minute") else None
        )
        state.last_u5 = payload.get("last_u5")
        state.continuity_resets = int(payload.get("continuity_resets", 0))
        return state
