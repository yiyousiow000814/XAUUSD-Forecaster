from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from xauusd_forecaster import execution_learning
from xauusd_forecaster.execution_learning import _read_execution_quote_windows
from xauusd_forecaster.forward_ledger import ForwardLedger, canonical_hash
from xauusd_forecaster.training import MARKET_FEATURES


UTC = timezone.utc


def _quote(received_at: datetime, *, bid: float = 4000.0) -> dict:
    return {
        "symbol": "XAUUSD",
        "event_time": received_at.isoformat(),
        "received_time": received_at.isoformat(),
        "bid": bid,
        "ask": bid + 0.2,
    }


def _write_quotes(path: Path, rows: list[dict]) -> None:
    content = "".join(json.dumps(row) + "\n" for row in rows)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(content)
    else:
        path.write_text(content, encoding="utf-8")


def _seed_execution_bootstrap(ledger: ForwardLedger, decision: datetime) -> None:
    decision_id = "execution-window"
    features = {name: 0.001 * (index + 1) for index, name in enumerate(MARKET_FEATURES)}
    market_hash = canonical_hash((decision_id, features))
    outcome_hash = canonical_hash((decision_id, "outcome"))
    ledger.connection.execute(
        """INSERT INTO derived_market_snapshots VALUES
        (?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)""",
        (
            "market-window", decision_id, decision.isoformat(), market_hash,
            "LIVE_OOS", decision.isoformat(), "market-feature-test", "u5-test",
            1.0, json.dumps(features), "OK", "[]", market_hash, market_hash,
        ),
    )
    ledger.connection.execute(
        """INSERT INTO derived_outcomes (
        derived_outcome_id,source_decision_id,decision_time,evidence_lane,
        recomputed_at,label_version,outcome_status,reason_codes_json,
        ambiguity_state,gross_midpoint_direction_move,long_quote_return,
        short_quote_return,commission_status,slippage_status,
        source_evidence_hash,output_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "outcome-window", decision_id, decision.isoformat(), "LIVE_OOS",
            (decision + timedelta(minutes=31)).isoformat(), "label-test", "VALID",
            "[]", "NONE", 0.1, 0.1, -0.1, "UNCONFIGURED",
            "UNAVAILABLE_SHADOW", outcome_hash, outcome_hash,
        ),
    )
    ledger.connection.execute(
        "INSERT INTO predictions_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            decision_id, "broad-full-test", "BROAD_FULL", decision.isoformat(),
            decision.isoformat(), "LIVE_OOS", "feature-hash", 0.1, None, 0.1,
            -0.1, None, None, "UTC_DAY_BLOCK_OOS_ABS_RESIDUAL_Q95",
            "calibration-test", 0, 0, 0, None, "UNCALIBRATED", "LONG", "WAIT",
            "PROVISIONAL",
        ),
    )
    ledger.connection.commit()


def test_execution_quote_windows_read_each_required_day_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = datetime(2026, 8, 5, 10, tzinfo=UTC)
    second = first + timedelta(minutes=5)
    rows = [
        _quote(first),
        _quote(first + timedelta(seconds=1)),
        _quote(first + timedelta(minutes=6)),
        _quote(first + timedelta(minutes=32)),
    ]
    _write_quotes(tmp_path / "xauusd-quotes-20260805.jsonl", rows)
    (tmp_path / "xauusd-quotes-20260804.jsonl").write_text(
        "not-json\n", encoding="utf-8"
    )
    parsed = 0
    original = execution_learning.parse_quote_line

    def counting_parser(line: str, source: Path):
        nonlocal parsed
        parsed += 1
        return original(line, source)

    monkeypatch.setattr(execution_learning, "parse_quote_line", counting_parser)
    windows = _read_execution_quote_windows(
        tmp_path, [first, second], first + timedelta(hours=1)
    )

    assert parsed == len(rows)
    assert [row.received_time for row in windows[first]] == [
        first + timedelta(seconds=1),
        first + timedelta(minutes=6),
    ]
    assert [row.received_time for row in windows[second]] == [
        first + timedelta(minutes=6),
        first + timedelta(minutes=32),
    ]


def test_execution_quote_window_crosses_utc_day_and_preserves_cutoff(
    tmp_path: Path,
) -> None:
    decision = datetime(2026, 8, 5, 23, 50, tzinfo=UTC)
    before_midnight = decision + timedelta(minutes=5)
    after_midnight = decision + timedelta(minutes=15)
    after_cutoff = decision + timedelta(minutes=20)
    _write_quotes(
        tmp_path / "xauusd-quotes-20260805.jsonl.gz",
        [_quote(before_midnight)],
    )
    _write_quotes(
        tmp_path / "xauusd-quotes-20260806.jsonl",
        [_quote(after_midnight), _quote(after_cutoff)],
    )

    windows = _read_execution_quote_windows(
        tmp_path, [decision], decision + timedelta(minutes=16)
    )

    assert [row.received_time for row in windows[decision]] == [
        before_midnight,
        after_midnight,
    ]


def test_execution_quote_window_rejects_wrong_symbol_in_required_partition(
    tmp_path: Path,
) -> None:
    decision = datetime(2026, 8, 5, 10, tzinfo=UTC)
    wrong = _quote(decision + timedelta(seconds=1))
    wrong["symbol"] = "EURUSD"
    _write_quotes(tmp_path / "xauusd-quotes-20260805.jsonl", [wrong])

    with pytest.raises(ValueError, match="unexpected quote symbol"):
        _read_execution_quote_windows(
            tmp_path, [decision], decision + timedelta(hours=1)
        )


def test_execution_bootstrap_without_missing_rows_never_reads_quote_history(
    tmp_path: Path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    quote_root = tmp_path / "quotes"
    quote_root.mkdir()
    (quote_root / "xauusd-quotes-20260805.jsonl").write_text(
        "not-json\n", encoding="utf-8"
    )

    assert execution_learning.bootstrap_execution_examples(
        ledger, quote_root, datetime(2026, 8, 5, 12, tzinfo=UTC)
    ) == 0
    ledger.close()


def test_execution_bootstrap_is_deterministic_from_exact_window_only(
    tmp_path: Path,
) -> None:
    decision = datetime(2026, 8, 5, 10, tzinfo=UTC)
    quote_root = tmp_path / "quotes"
    quote_root.mkdir()
    rows = [
        _quote(decision + timedelta(seconds=1), bid=4000.0),
        *[
            _quote(decision + timedelta(minutes=minute, seconds=1), bid=4000.0 + minute)
            for minute in range(1, 31)
        ],
    ]
    _write_quotes(quote_root / "xauusd-quotes-20260805.jsonl", rows)
    (quote_root / "xauusd-quotes-20260801.jsonl").write_text(
        "not-json\n", encoding="utf-8"
    )
    receipts = []
    for suffix in ("a", "b"):
        ledger = ForwardLedger(tmp_path / f"forward-{suffix}.sqlite3", now=decision)
        _seed_execution_bootstrap(ledger, decision)
        assert execution_learning.bootstrap_execution_examples(
            ledger, quote_root, decision + timedelta(hours=1)
        ) == 1
        row = ledger.connection.execute(
            """SELECT source_decision_id,source_hash,checkpoint_path_json
            FROM execution_training_examples_v2"""
        ).fetchone()
        receipts.append(tuple(row))
        ledger.close()

    assert receipts[0] == receipts[1]
