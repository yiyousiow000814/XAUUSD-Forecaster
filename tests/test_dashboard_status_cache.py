from __future__ import annotations

import concurrent.futures
import json
import threading
import time

import pytest

from xauusd_forecaster.dashboard.status_cache import (
    StatusSnapshotCache,
    StatusSnapshotUnavailable,
)


def test_status_snapshot_cache_singleflights_concurrent_builds(tmp_path) -> None:
    cache = StatusSnapshotCache(wait_seconds=1.0)
    database = tmp_path / "forward.sqlite3"
    started = threading.Event()
    release = threading.Event()
    calls = 0
    call_lock = threading.Lock()

    def builder(_database):
        nonlocal calls
        with call_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"generated_at": "2026-08-12T00:00:00+00:00"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.get, database, builder) for _ in range(8)]
        assert started.wait(timeout=1)
        time.sleep(0.05)
        assert calls == 1
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert len({body for body, _state, _age in results}) == 1
    assert {state for _body, state, _age in results} == {"fresh"}


def test_status_snapshot_cache_serves_bounded_stale_during_slow_refresh(
    tmp_path,
) -> None:
    now = [0.0]
    cache = StatusSnapshotCache(
        ttl_seconds=15, wait_seconds=0.01, max_stale_seconds=90,
        clock=lambda: now[0],
    )
    database = tmp_path / "forward.sqlite3"
    cache.get(database, lambda _database: {"version": 1})

    now[0] = 16.0
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def slow_refresh(_database):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"version": 2}

    stale_body, stale_state, stale_age = cache.get(database, slow_refresh)
    assert started.wait(timeout=1)
    second_body, second_state, second_age = cache.get(database, slow_refresh)
    assert json.loads(stale_body) == {"version": 1}
    assert json.loads(second_body) == {"version": 1}
    assert (stale_state, second_state) == ("stale", "stale")
    assert (stale_age, second_age) == (16.0, 16.0)
    assert calls == 1

    release.set()
    for _ in range(100):
        health_status, health = cache.health()
        if health_status == 200 and health["refreshing"] is False:
            break
        time.sleep(0.01)
    refreshed_body, refreshed_state, _age = cache.get(database, slow_refresh)
    assert json.loads(refreshed_body) == {"version": 2}
    assert refreshed_state == "fresh"


def test_status_snapshot_cache_exposes_unavailable_error_and_stale_health(
    tmp_path,
) -> None:
    now = [0.0]
    cache = StatusSnapshotCache(
        ttl_seconds=15, max_stale_seconds=90, clock=lambda: now[0],
    )
    database = tmp_path / "forward.sqlite3"

    assert cache.health() == (503, {
        "status": "UNAVAILABLE",
        "snapshot_age_seconds": None,
        "last_error": None,
    })
    cache.get(database, lambda _database: {"version": 1})
    assert cache.health() == (200, {
        "status": "OK",
        "snapshot_age_seconds": 0.0,
        "refreshing": False,
    })
    now[0] = 91.0
    assert cache.health() == (503, {
        "status": "STALE",
        "snapshot_age_seconds": 91.0,
        "last_error": None,
    })


def test_status_snapshot_cache_waiter_times_out_without_duplicate_build(
    tmp_path,
) -> None:
    cache = StatusSnapshotCache(wait_seconds=0.01)
    database = tmp_path / "forward.sqlite3"
    started = threading.Event()
    release = threading.Event()

    def builder(_database):
        started.set()
        assert release.wait(timeout=2)
        return {"version": 1}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.get, database, builder)
        assert started.wait(timeout=1)
        with pytest.raises(
            StatusSnapshotUnavailable,
            match="^dashboard snapshot refresh is still running$",
        ):
            cache.get(database, builder)
        release.set()
        future.result(timeout=2)


def test_status_snapshot_cache_shares_refresh_error_with_waiter(tmp_path) -> None:
    cache = StatusSnapshotCache(wait_seconds=1.0)
    database = tmp_path / "forward.sqlite3"
    started = threading.Event()
    release = threading.Event()

    def builder(_database):
        started.set()
        assert release.wait(timeout=2)
        raise ValueError("shared failure")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        builder_future = pool.submit(cache.get, database, builder)
        assert started.wait(timeout=1)
        waiter_future = pool.submit(cache.get, database, builder)
        time.sleep(0.05)
        release.set()
        with pytest.raises(ValueError, match="^shared failure$"):
            builder_future.result(timeout=2)
        with pytest.raises(
            StatusSnapshotUnavailable, match="^ValueError: shared failure$",
        ):
            waiter_future.result(timeout=2)
