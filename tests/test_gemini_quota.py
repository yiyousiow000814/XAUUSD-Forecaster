from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from xauusd_forecaster.gemini_quota import GeminiQuotaLedger, key_fingerprint


KEY = "secret-api-key-for-migration"
LEGACY_FINGERPRINT = "2ede7a1eaf23"
DAY = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)
NEXT_DAY = datetime(2026, 8, 20, 7, 0, tzinfo=UTC)


def _write_state(path, *, legacy: int | None, canonical: int | None) -> None:
    counts = {}
    if legacy is not None:
        counts[LEGACY_FINGERPRINT] = legacy
    if canonical is not None:
        counts[key_fingerprint(KEY)] = canonical
    path.write_text(
        json.dumps({"quota_day": "2026-08-18", "counts": counts}),
        encoding="utf-8",
    )


def _saved_counts(path) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8"))["counts"]


def test_snapshot_reads_old_only_count_without_modifying_file(tmp_path) -> None:
    path = tmp_path / "gemini-quota.json"
    _write_state(path, legacy=420, canonical=None)
    original = path.read_bytes()

    snapshot = GeminiQuotaLedger(path).snapshot((KEY,), DAY)

    assert snapshot["keys"][0]["sent"] == 420
    assert path.read_bytes() == original


def test_reserve_continues_from_legacy_count(tmp_path) -> None:
    path = tmp_path / "gemini-quota.json"
    _write_state(path, legacy=420, canonical=None)

    assert GeminiQuotaLedger(path).reserve(KEY, DAY)

    assert _saved_counts(path) == {key_fingerprint(KEY): 421}
    assert KEY not in path.read_text(encoding="utf-8")


def test_seed_never_lowers_migrated_legacy_count(tmp_path) -> None:
    path = tmp_path / "gemini-quota.json"
    _write_state(path, legacy=420, canonical=None)

    GeminiQuotaLedger(path).seed(KEY, 300, DAY)

    assert _saved_counts(path) == {key_fingerprint(KEY): 420}
    assert KEY not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("legacy", "canonical"), ((420, 400), (300, 420)))
def test_snapshot_uses_maximum_without_modifying_file(
    tmp_path, legacy: int, canonical: int,
) -> None:
    path = tmp_path / "gemini-quota.json"
    _write_state(path, legacy=legacy, canonical=canonical)
    original = path.read_bytes()

    snapshot = GeminiQuotaLedger(path).snapshot((KEY,), DAY)

    assert snapshot["keys"][0]["sent"] == max(legacy, canonical)
    assert path.read_bytes() == original


def test_repeated_snapshot_and_restart_are_read_only(tmp_path) -> None:
    path = tmp_path / "gemini-quota.json"
    _write_state(path, legacy=420, canonical=400)
    original = path.read_bytes()

    first = GeminiQuotaLedger(path).snapshot((KEY,), DAY)
    restarted = GeminiQuotaLedger(path).snapshot((KEY,), DAY)

    assert first == restarted
    assert path.read_bytes() == original


def test_snapshot_quota_day_reset_is_read_only(tmp_path) -> None:
    path = tmp_path / "gemini-quota.json"
    _write_state(path, legacy=420, canonical=400)
    original = path.read_bytes()

    assert GeminiQuotaLedger(path).snapshot((KEY,), NEXT_DAY)["keys"][0]["sent"] == 0
    assert path.read_bytes() == original
