from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.dashboard import market_resources as module
from xauusd_forecaster.evidence.ledger import ForwardLedger


UTC = timezone.utc


def test_live_quote_candle_cache_reads_only_appended_bytes(tmp_path) -> None:
    quote_file = tmp_path / "xauusd-quotes-20260812.jsonl"

    def quote(second: int, bid: float) -> str:
        return json.dumps({
            "received_time": f"2026-08-12T06:30:{second:02d}+00:00",
            "bid": bid,
            "ask": bid + 0.1,
        }) + "\n"

    quote_file.write_text(quote(1, 4300.0) + quote(2, 4301.0), encoding="utf-8")
    first = module._quote_file_candles(quote_file)
    first_offset = module._QUOTE_CANDLE_CACHE[str(quote_file)]["offset"]

    with quote_file.open("a", encoding="utf-8") as handle:
        handle.write(quote(3, 4302.0))
    second = module._quote_file_candles(quote_file)

    assert first[0]["ticks"] == 2
    assert second[0]["ticks"] == 3
    assert second[0]["open"] == 4300.05
    assert second[0]["close"] == 4302.05
    assert module._QUOTE_CANDLE_CACHE[str(quote_file)]["offset"] > first_offset


def test_market_chart_keeps_last_session_on_weekend_and_reads_gzip(tmp_path) -> None:
    now = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    quote_dir = tmp_path / "quotes"
    quote_dir.mkdir()
    friday = datetime(2026, 8, 7, 20, 55, tzinfo=UTC)
    rows = [
        {"received_time": (friday + timedelta(minutes=index)).isoformat(), "bid": 3400 + index, "ask": 3400.2 + index}
        for index in range(2)
    ]
    with gzip.open(quote_dir / "xauusd-quotes-20260807.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(row) for row in rows) + "\n")
    (quote_dir / "xauusd-quotes-20260807.jsonl").write_text("", encoding="utf-8")
    (quote_dir / "xauusd-quotes-20260809.jsonl").write_text("", encoding="utf-8")

    payload = module._recent_market_chart(database, ledger.connection, now)

    assert len(payload["candles"]) == 1
    assert payload["candles"][0]["time"] == "2026-08-07T20:55:00+00:00"
    assert payload["history_end"] == "2026-08-07T20:55:00+00:00"
    assert payload["source_candle_count"] == 1
    assert payload["overview_downsampled"] is False
    assert payload["prediction_history_start"] == {}


def test_market_chart_overview_preserves_ohlc_extremes() -> None:
    candles = [{
        "time": f"2026-08-07T00:{index:02d}:00+00:00",
        "open": float(index), "high": float(index + 1), "low": float(index - 1),
        "close": float(index + 0.5), "ticks": 2,
    } for index in range(6)]

    compact = module._downsample_candles(candles, 2)

    assert len(compact) == 2
    assert compact[0] == {
        "time": candles[0]["time"], "open": 0.0, "high": 3.0, "low": -1.0,
        "close": 2.5, "ticks": 6, "source_candles": 3,
    }
    assert compact[1]["open"] == 3.0
    assert compact[1]["close"] == 5.5


def test_market_history_pages_are_complete_and_cursor_safe(tmp_path) -> None:
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=datetime(2026, 8, 7, tzinfo=UTC))
    quote_dir = tmp_path / "quotes"
    quote_dir.mkdir()
    start = datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
    rows = [{
        "received_time": (start + timedelta(minutes=5 * index)).isoformat(),
        "bid": 3400 + index, "ask": 3400.2 + index,
    } for index in range(5)]
    (quote_dir / "xauusd-quotes-20260807.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8",
    )

    first = module._market_history_page(database, ledger.connection, None, 2)
    second = module._market_history_page(
        database, ledger.connection, first["next_cursor"], 2,
    )
    third = module._market_history_page(
        database, ledger.connection, second["next_cursor"], 2,
    )

    times = [row["time"] for page in (first, second, third) for row in page["candles"]]
    assert len(times) == len(set(times)) == 5
    assert first["has_more"] is True
    assert second["has_more"] is True
    assert third["has_more"] is False
    assert third["next_cursor"] == times[-1]
