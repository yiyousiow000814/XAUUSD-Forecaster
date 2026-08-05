import math
import struct
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster import Quote, build_fixed_horizon_label, read_xautk002


UTC = timezone.utc
DOTNET_EPOCH_TICKS = 621355968000000000
HEADER = struct.Struct("<8siiqqqqii")
ROW = struct.Struct("<qii")


def dotnet_ticks(value: datetime) -> int:
    return DOTNET_EPOCH_TICKS + int(value.timestamp() * 10_000_000)


def test_reads_valid_xautk002_file(tmp_path) -> None:
    start = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    ticks = [dotnet_ticks(start), dotnet_ticks(start + timedelta(seconds=1))]
    path = tmp_path / "sample.xtk"
    with path.open("wb") as handle:
        handle.write(
            HEADER.pack(
                b"XAUTK002",
                2,
                100,
                ticks[0],
                2,
                ticks[0],
                ticks[1],
                240000,
                240030,
            )
        )
        handle.write(ROW.pack(ticks[0], 240000, 240020))
        handle.write(ROW.pack(ticks[1], 240010, 240030))

    quotes = list(read_xautk002(path))

    assert quotes[0] == Quote(start, 2400.0, 2400.2)
    assert quotes[1].timestamp == start + timedelta(seconds=1)


def test_reader_rejects_size_mismatch(tmp_path) -> None:
    start = dotnet_ticks(datetime(2026, 8, 5, 10, 0, tzinfo=UTC))
    path = tmp_path / "truncated.xtk"
    with path.open("wb") as handle:
        handle.write(
            HEADER.pack(
                b"XAUTK002",
                2,
                100,
                start,
                1,
                start,
                start,
                240000,
                240020,
            )
        )

    with pytest.raises(ValueError, match="file size"):
        list(read_xautk002(path))


def test_builds_mirrored_executable_30_minute_label() -> None:
    start = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    quotes = [
        Quote(start + timedelta(seconds=1), 2400.0, 2400.2),
        Quote(start + timedelta(minutes=15), 2404.0, 2404.2),
        Quote(start + timedelta(minutes=30, seconds=1), 2402.0, 2402.2),
    ]

    label = build_fixed_horizon_label(
        decision_id="XAU-001",
        decision_time=start,
        quotes=quotes,
        u5=0.01,
        commission_round_trip_log=0.00001,
        maximum_healthy_gap_seconds=1_000,
    )

    assert label is not None
    expected_long = (math.log(2402.0 / 2400.2) - 0.00001) / 0.01
    expected_short = (math.log(2400.0 / 2402.2) - 0.00001) / 0.01
    assert label.long_return_u5 == pytest.approx(expected_long)
    assert label.short_return_u5 == pytest.approx(expected_short)
    assert label.mfe_long_u5 > label.long_return_u5
    assert label.mae_short_u5 < label.short_return_u5
    assert label.ambiguity_state == "NONE"


def test_missing_entry_or_terminal_quote_produces_no_label() -> None:
    start = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    late = [Quote(start + timedelta(seconds=21), 2400.0, 2400.2)]
    short_path = [Quote(start + timedelta(seconds=1), 2400.0, 2400.2)]

    common = {
        "decision_id": "XAU-001",
        "decision_time": start,
        "u5": 0.01,
        "commission_round_trip_log": 0.0,
    }
    assert build_fixed_horizon_label(quotes=late, **common) is None
    assert build_fixed_horizon_label(quotes=short_path, **common) is None
