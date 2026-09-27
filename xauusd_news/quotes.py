"""Read repository-local XAUTK002 executable Bid/Ask Tick files."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


HEADER = struct.Struct("<8siiqqqqii")
ROW = struct.Struct("<qii")
MAGIC = b"XAUTK002"
VERSION = 2
DOTNET_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Quote:
    timestamp: datetime
    bid: float
    ask: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("quote timestamp must be timezone-aware")
        if self.timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("quote timestamp must be UTC")
        if self.bid <= 0 or self.ask < self.bid:
            raise ValueError("quote must have positive, non-crossed Bid/Ask")


def _from_dotnet_ticks(value: int) -> datetime:
    if value < 0:
        raise ValueError("negative .NET timestamp")
    return DOTNET_EPOCH + timedelta(microseconds=value // 10)


def read_xautk002(path: str | Path) -> Iterator[Quote]:
    """Yield validated quotes and reject truncated or out-of-order archives."""
    source = Path(path)
    with source.open("rb") as handle:
        header_bytes = handle.read(HEADER.size)
        if len(header_bytes) != HEADER.size:
            raise ValueError("truncated XAUTK002 header")
        magic, version, scale, _, rows, first, last, _, _ = HEADER.unpack(
            header_bytes
        )
        if magic != MAGIC or version != VERSION or scale <= 0 or rows < 0:
            raise ValueError("invalid XAUTK002 header")
        expected_bytes = HEADER.size + rows * ROW.size
        if source.stat().st_size != expected_bytes:
            raise ValueError("XAUTK002 file size does not match row count")

        previous_ticks: int | None = None
        first_seen: int | None = None
        last_seen: int | None = None
        for _ in range(rows):
            dotnet_ticks, bid_units, ask_units = ROW.unpack(handle.read(ROW.size))
            if previous_ticks is not None and dotnet_ticks < previous_ticks:
                raise ValueError("XAUTK002 rows are out of order")
            if bid_units <= 0 or ask_units < bid_units:
                raise ValueError("XAUTK002 contains an invalid quote")
            if first_seen is None:
                first_seen = dotnet_ticks
            last_seen = dotnet_ticks
            previous_ticks = dotnet_ticks
            yield Quote(
                timestamp=_from_dotnet_ticks(dotnet_ticks),
                bid=bid_units / scale,
                ask=ask_units / scale,
            )

        if rows and (first_seen != first or last_seen != last):
            raise ValueError("XAUTK002 header boundaries do not match rows")
