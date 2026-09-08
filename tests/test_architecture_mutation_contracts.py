"""Behavior assertions for the three historically surviving mutation families."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from scripts import run_dashboard_sync as sync
from xauusd_forecaster.evidence.ledger import ForwardLedger

ROOT = Path(__file__).resolve().parents[1]


def test_sync_publishes_heartbeat_before_optional_work(monkeypatch):
    events = []
    target = {'name': 'isolated-test'}
    monkeypatch.setattr(sync, 'configured_targets', lambda _: [target])

    def heartbeat(_):
        events.append('heartbeat-accepted')
        return [target], sync.SyncResourceResults([], [])

    def optional(targets):
        assert events == ['heartbeat-accepted'], 'HEARTBEAT_ORDER_VIOLATED'
        assert targets == [target]
        events.append('optional-admitted')
        return sync.SyncResourceResults([], [])

    monkeypatch.setattr(sync, 'sync_heartbeat_once', heartbeat)
    monkeypatch.setattr(sync, 'sync_resource_lane', optional)
    sync.sync_once({})
    assert events == ['heartbeat-accepted', 'optional-admitted']


def test_forward_ledger_rejects_real_update_and_delete(tmp_path):
    recorded_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ledger = ForwardLedger(tmp_path / 'isolated.sqlite3', now=recorded_at)
    try:
        ledger.append_snapshot({
            'snapshot_id': 'isolated-snapshot',
            'decision_time': recorded_at,
            'collected_at': recorded_at,
            'data_role': 'FORWARD',
            'source': 'isolated-fixture',
            'bid': 2000.0,
            'ask': 2000.2,
            'feature_version': 'isolated-v1',
            'u5_status': 'VALID',
            'data_health': 'HEALTHY',
        })
        # Exercise real historical evidence as well as the separately guarded
        # epoch. Operational metadata exceptions must not mask evidence drift.
        for query, statements in (
            (
                "SELECT * FROM market_snapshots WHERE snapshot_id='isolated-snapshot'",
                (
                    "UPDATE market_snapshots SET bid=1999.0 WHERE snapshot_id='isolated-snapshot'",
                    "DELETE FROM market_snapshots WHERE snapshot_id='isolated-snapshot'",
                ),
            ),
            (
                "SELECT * FROM runtime_metadata WHERE key='FORWARD_EPOCH'",
                (
                    "UPDATE runtime_metadata SET value='changed' WHERE key='FORWARD_EPOCH'",
                    "DELETE FROM runtime_metadata WHERE key='FORWARD_EPOCH'",
                ),
            ),
        ):
            original = tuple(ledger.connection.execute(query).fetchone())
            for statement in statements:
                rejected = False
                try:
                    ledger.connection.execute(statement)
                except sqlite3.IntegrityError as error:
                    rejected = 'append-only' in str(error)
                finally:
                    ledger.connection.rollback()
                assert rejected, f'FORWARD_APPEND_ONLY_VIOLATED: {statement}'
                assert tuple(ledger.connection.execute(query).fetchone()) == original
    finally:
        ledger.close()
