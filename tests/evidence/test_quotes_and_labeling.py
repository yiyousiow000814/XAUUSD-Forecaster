import math
import struct
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster import Quote, read_xautk002


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
