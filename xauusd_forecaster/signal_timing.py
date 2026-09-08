"""Bounded optional clock observations, never forecasting or commit authority."""

from __future__ import annotations

import atexit
import json
import math
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from .forward_ledger import canonical_hash


TIMING_SOURCE = "COLLECTOR_SIGNAL_TIMING"
TIMING_VERSION = "collector-signal-timing-v1"
MAX_TIMING_BYTES = 16_384
MAX_PREDICTION_OBSERVATIONS = 16
MAX_LOG_ATTEMPTS_PER_OBSERVATION = 2
LOG_RETRY_INTERVAL_NS = 60_000_000_000
STAGES = frozenset({"collector_started", "market_features_completed",
                    "legacy_predictions_completed", "news_features_completed",
                    "write_batch_ready"})


def utc_now():
    return datetime.now(timezone.utc)


def utc_text(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("SIGNAL_TIMING_UNQUALIFIED_CLOCK")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def timing_bytes(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_TIMING_BYTES:
        raise ValueError("SIGNAL_TIMING_OVERSIZED")
    return encoded


def prediction_timing_hash(row):
    """Bind predictions_v2's declared persisted types, not Python numeric spelling.

    SQLite REAL affinity returns integer inputs as float and normalizes -0.0.
    Only this optional evidence identity is normalized; model values and the
    existing global/completion hashing contract remain unchanged.
    """
    values = list(row)
    if len(values) != 23:
        raise ValueError("SIGNAL_TIMING_PREDICTION_SCHEMA")
    for index in (7, 8, 9, 10, 11, 12, 18):
        if values[index] is not None:
            value = float(values[index])
            if not math.isfinite(value):
                raise ValueError("SIGNAL_TIMING_NONFINITE_PREDICTION")
            values[index] = value if value != 0 else 0.0
    return canonical_hash(values)


def emit_signal_event(event):
    """Write existing redirected stdout without taking a Python buffered lock.

    Only the single-flight diagnostic worker calls this. A blocked OS write
    must not hold stdout's buffered lock during Python interpreter shutdown.
    """
    try:
        data = timing_bytes(event) + b"\n"
        descriptor = sys.stdout.fileno()
        while data:
            written = os.write(descriptor, data)
            if written <= 0:
                return False
            data = data[written:]
        return True
    except Exception:
        return False


class SignalLogDelivery:
    """One optional sink flight, no queue and no unbounded shutdown wait."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._closed = False
        self.accepted = self.delivered = self.failed = self.dropped = 0

    @property
    def busy(self):
        with self._lock:
            return self._thread is not None

    def submit(self, event, *, sink=emit_signal_event, completed=None):
        try:
            captured = json.loads(timing_bytes(event))
        except Exception:
            return False
        with self._lock:
            if self._closed or self._thread is not None:
                self.dropped += 1
                return False
            worker = threading.Thread(target=self._run, args=(captured, sink, completed),
                                      name="signal-observation-log", daemon=True)
            self._thread = worker
            try:
                worker.start()
                self.accepted += 1
                return True
            except Exception:
                self._thread = None
                self.failed += 1
                return False

    def _run(self, event, sink, completed):
        success = False
        try:
            success = sink(event) is not False
        except Exception:
            pass
        try:
            if completed is not None:
                completed(success)
        finally:
            with self._lock:
                self.delivered += int(success)
                self.failed += int(not success)
                self._thread = None

    def wait(self, timeout=0.1):
        with self._lock:
            worker = self._thread
        if worker is not None:
            worker.join(timeout=min(5.0, max(0.0, timeout)))
        return not self.busy

    def close(self):
        with self._lock:
            self._closed = True
        # False means a still-running optional sink, never a killed thread or
        # delivered receipt. New submissions remain disabled after close.
        return self.wait(timeout=0.1)


SIGNAL_LOG_DELIVERY = SignalLogDelivery()
atexit.register(SIGNAL_LOG_DELIVERY.close)


def queue_signal_event(event):
    return SIGNAL_LOG_DELIVERY.submit(event)


class ClockTiming:
    """One in-memory attempt; only its precommit facts join the atomic row."""

    def __init__(self, run_id, decision_id, snapshot_id, *, clock=utc_now,
                 monotonic=time.monotonic_ns):
        self.clock = clock
        self.monotonic = monotonic
        self.origin = None
        self.previous = None
        self.sequence = 0
        self.state = "OBSERVED"
        self.stages = {}
        self.predictions = []
        self.identity = {"collector_run_id": run_id, "decision_id": decision_id,
                         "snapshot_id": snapshot_id}
        self.record("collector_started")

    def _stamp(self):
        wall = utc_text(self.clock())
        tick = self.monotonic()
        if type(tick) is not int or not 0 <= tick < 2**63:
            raise ValueError("SIGNAL_TIMING_INVALID_MONOTONIC")
        if self.origin is None:
            self.origin = tick
        if self.state != "UNAVAILABLE" and self.previous and (wall < self.previous[0] or tick < self.previous[1]):
            self.state = "CLOCK_ANOMALY"
        self.previous = (wall, tick)
        self.sequence += 1
        return {"observed_at": wall, "elapsed_ns": tick - self.origin,
                "sequence": self.sequence}

    def record(self, stage):
        try:
            if stage not in STAGES or stage in self.stages:
                raise ValueError("SIGNAL_TIMING_INVALID_STAGE")
            self.stages[stage] = self._stamp()
        except Exception:
            self.state = "UNAVAILABLE"

    def prediction(self, row):
        try:
            if (len(row) != 23 or len(self.predictions) >= MAX_PREDICTION_OBSERVATIONS
                    or any(not isinstance(value, str) or not 0 < len(value) <= 256
                           for value in (row[1], row[2], row[6], row[22]))):
                raise ValueError("SIGNAL_TIMING_PREDICTION_BOUND")
            self.predictions.append({
                "model_identity": row[2], "model_version": row[1],
                "feature_snapshot_hash": row[6], "prediction_status": row[22],
                "prediction_hash": prediction_timing_hash(row), **self._stamp(),
            })
        except Exception:
            self.state = "UNAVAILABLE"

    def finish(self, completion, source_received_at):
        self.record("write_batch_ready")
        entry = {"source": TIMING_SOURCE, "schema": TIMING_VERSION,
                 **self.identity, "state": self.state,
                 "completion_digest": canonical_hash(completion)}
        try:
            entry.update({
                "market_source_received_at": (
                    utc_text(source_received_at) if source_received_at else None),
                "stages": self.stages, "predictions": self.predictions,
            })
            timing_bytes(entry)
        except Exception:
            self.state = "UNAVAILABLE"
            entry = {**entry, "state": "UNAVAILABLE", "stages": {}, "predictions": []}
            entry.pop("market_source_received_at", None)
        entry["timing_digest"] = canonical_hash(entry)
        try:
            timing_bytes(entry)
        except Exception:
            self.state = "UNAVAILABLE"
            entry = {"source": TIMING_SOURCE, "schema": TIMING_VERSION,
                     **self.identity, "state": "UNAVAILABLE",
                     "completion_digest": canonical_hash(completion)}
            entry["timing_digest"] = canonical_hash(entry)
        return entry

    def committed(self, entry, decision_time, observer):
        if observer is None:
            return
        # This runs only after SQLite's context manager returned successfully.
        # Never make an already committed clock fail because observation failed.
        try:
            stamp = self._stamp()
            observer({
                "event": "DECISION_APPENDED", "schema": TIMING_VERSION,
                "scope": "COMMIT_RETURN_OBSERVED", **self.identity,
                "decision_time": utc_text(decision_time),
                "timing_digest": entry["timing_digest"],
                "completion_digest": entry["completion_digest"],
                "timing_state": self.state,
                "commit_return_observed_at": stamp["observed_at"],
                "elapsed_ns": stamp["elapsed_ns"],
            })
        except Exception:
            return


class DashboardSignalObserver:
    """Change-of-identity diagnostic deduplication; never a read/cache authority."""

    def __init__(self, *, clock=utc_now, monotonic=time.monotonic_ns,
                 sink=emit_signal_event, delivery=SIGNAL_LOG_DELIVERY):
        self.clock, self.monotonic, self.sink = clock, monotonic, sink
        self.delivery = delivery
        self._lock = threading.Lock()
        self._last_key = None
        self._previous_clock = None
        self._clock_state = "OBSERVED"
        self._instance = str(uuid.uuid4())
        self._attempt_key = None
        self._pending_event = None
        self._attempts = 0
        self._last_attempt_tick = 0

    def observe(self, connection, database, latest, prediction):
        if latest is None or prediction is None:
            return
        try:
            key = (str(database), latest["decision_id"], latest["snapshot_id"],
                   latest["snapshot_hash"], prediction_timing_hash(prediction))
            with self._lock:
                # Serialize clock sampling and comparison, not just comparison:
                # thread scheduling order is not clock-regression evidence.
                try:
                    wall, tick = utc_text(self.clock()), self.monotonic()
                    if type(tick) is not int or not 0 <= tick < 2**63:
                        raise ValueError("SIGNAL_TIMING_INVALID_MONOTONIC")
                except Exception:
                    self._clock_state = "UNAVAILABLE"
                    return
                if (self._clock_state != "UNAVAILABLE" and self._previous_clock is not None
                        and (wall < self._previous_clock[0] or tick < self._previous_clock[1])):
                    self._clock_state = "CLOCK_ANOMALY"
                self._previous_clock = (wall, tick)
                if self.delivery.busy:
                    return
                if key == self._last_key:
                    return
                if self._attempt_key is not None and tick - self._last_attempt_tick < LOG_RETRY_INTERVAL_NS:
                    return
                if key == self._attempt_key:
                    if (self._pending_event is None
                            or self._attempts >= MAX_LOG_ATTEMPTS_PER_OBSERVATION
                            or tick - self._last_attempt_tick < LOG_RETRY_INTERVAL_NS):
                        return
                    # Retry only the same bounded diagnostic bytes. Never reread
                    # the database or renew the original observation timestamp.
                    self._deliver(key, self._pending_event, tick)
                    return
                self._attempt_key, self._attempts = key, 0
                self._pending_event = None
                self._last_attempt_tick = tick
                from .clock_commit import read_signal_timing

                timing = read_signal_timing(
                    connection, decision_id=latest["decision_id"],
                    snapshot_id=latest["snapshot_id"], prediction=prediction,
                )
                event = {
                    "event": "SIGNAL_CONSUMER_OBSERVED", "schema": TIMING_VERSION,
                    "scope": "DASHBOARD_SQLITE_READ_OBSERVED_NO_LATER_THAN",
                    "database_locator_hash": canonical_hash(str(database)),
                    "decision_id": latest["decision_id"],
                    "snapshot_id": latest["snapshot_id"],
                    "model_identity": prediction["model_identity"],
                    "model_version": prediction["model_version"],
                    "prediction_hash": key[-1],
                    "consumer_observed_at": wall,
                    "observer_instance_id": self._instance,
                    "consumer_monotonic_ns": tick,
                    "clock_state": self._clock_state,
                    "producer_timing": timing,
                }
                timing_bytes(event)
                self._pending_event = event
                self._deliver(key, event, tick)
        except Exception:
            # Optional observability cannot make the critical payload unavailable.
            return

    def _deliver(self, key, event, tick):
        self._last_attempt_tick = tick

        def completed(success):
            with self._lock:
                if success and key == self._attempt_key:
                    self._last_key = key
                    self._pending_event = None

        if self.delivery.submit(event, sink=self.sink, completed=completed):
            self._attempts += 1
