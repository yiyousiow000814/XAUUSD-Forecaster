"""Deterministic XAUTK002 to completed-M1 aggregation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .quotes import HEADER, MAGIC, ROW, VERSION, read_xautk002


DOTNET_EPOCH_TICKS = 621_355_968_000_000_000
TICKS_PER_SECOND = 10_000_000
TICKS_PER_MINUTE = 60 * TICKS_PER_SECOND
TICK_DTYPE = np.dtype(
    [
        ("dotnet_ticks", "<i8"),
        ("bid_units", "<i4"),
        ("ask_units", "<i4"),
    ]
)


@dataclass(frozen=True)
class PriorQuote:
    dotnet_ticks: int
    bid_units: int
    ask_units: int


@dataclass(frozen=True)
class AggregatedFile:
    frame: pd.DataFrame
    qa: dict[str, object]
    last_quote: PriorQuote | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_last_prior_quote(path: str | Path) -> PriorQuote | None:
    """Read only the final raw quote, preserving archive integer units."""
    source = Path(path)
    _, rows, _, _, offset = _read_header(source)
    if rows == 0:
        return None
    values = np.memmap(
        source,
        mode="r",
        offset=offset,
        dtype=TICK_DTYPE,
        shape=(rows,),
    )
    final = values[-1]
    return PriorQuote(
        int(final["dotnet_ticks"]),
        int(final["bid_units"]),
        int(final["ask_units"]),
    )


def _read_header(path: Path) -> tuple[int, int, int, int, int]:
    with path.open("rb") as handle:
        raw = handle.read(HEADER.size)
    if len(raw) != HEADER.size:
        raise ValueError(f"truncated XAUTK002 header: {path}")
    magic, version, scale, _, rows, first, last, _, _ = HEADER.unpack(raw)
    if magic != MAGIC or version != VERSION or scale <= 0 or rows < 0:
        raise ValueError(f"invalid XAUTK002 header: {path}")
    if path.stat().st_size != HEADER.size + rows * ROW.size:
        raise ValueError(f"XAUTK002 size mismatch: {path}")
    return scale, rows, first, last, HEADER.size


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "minute",
            "bid_open",
            "ask_open",
            "bid_high",
            "ask_high",
            "bid_low",
            "ask_low",
            "bid_close",
            "ask_close",
            "tick_count",
            "quote_up_count",
            "quote_down_count",
            "maximum_quote_gap_seconds",
            "close_source_time",
            "source_file",
            "source_file_hash",
        ]
    )


def aggregate_xautk002_batch(
    path: str | Path,
    prior: PriorQuote | None = None,
    source_hash: str | None = None,
) -> AggregatedFile:
    """Vectorized completed-M1 aggregation with explicit source QA."""
    source = Path(path)
    scale, rows, first, last, offset = _read_header(source)
    digest = source_hash or sha256_file(source)
    values = np.memmap(
        source,
        mode="r",
        offset=offset,
        dtype=TICK_DTYPE,
        shape=(rows,),
    )
    if rows == 0:
        qa = {
            "source_file": str(source),
            "source_file_hash": digest,
            "tick_count": 0,
            "duplicate_ticks": 0,
            "out_of_order_ticks": 0,
            "nonpositive_quotes": 0,
            "crossed_quotes": 0,
            "maximum_quote_gap_seconds": None,
        }
        return AggregatedFile(_empty_frame(), qa, prior)

    ticks = np.asarray(values["dotnet_ticks"], dtype=np.int64)
    bids = np.asarray(values["bid_units"], dtype=np.int64)
    asks = np.asarray(values["ask_units"], dtype=np.int64)
    if int(ticks[0]) != first or int(ticks[-1]) != last:
        raise ValueError(f"XAUTK002 boundary mismatch: {source}")

    diffs = np.diff(ticks)
    out_of_order = int(np.count_nonzero(diffs < 0))
    duplicates = int(np.count_nonzero(diffs == 0))
    nonpositive = int(np.count_nonzero((bids <= 0) | (asks <= 0)))
    crossed = int(np.count_nonzero(asks < bids))
    if out_of_order or nonpositive or crossed:
        raise ValueError(
            f"structurally invalid XAUTK002: {source}; "
            f"out_of_order={out_of_order}, nonpositive={nonpositive}, "
            f"crossed={crossed}"
        )

    minutes = (ticks - DOTNET_EPOCH_TICKS) // TICKS_PER_MINUTE
    _, starts = np.unique(minutes, return_index=True)
    ends = np.r_[starts[1:] - 1, rows - 1]
    counts = ends - starts + 1

    midpoint_units = bids + asks
    changes = np.empty(rows, dtype=np.int8)
    if prior is None:
        changes[0] = 0
        first_gap_seconds = 0.0
    else:
        previous_midpoint = prior.bid_units + prior.ask_units
        changes[0] = np.sign(midpoint_units[0] - previous_midpoint)
        first_gap_seconds = max(
            0.0,
            (int(ticks[0]) - prior.dotnet_ticks) / TICKS_PER_SECOND,
        )
    changes[1:] = np.sign(np.diff(midpoint_units)).astype(np.int8)

    quote_gaps = np.empty(rows, dtype=np.float64)
    quote_gaps[0] = first_gap_seconds
    quote_gaps[1:] = np.maximum(diffs, 0) / TICKS_PER_SECOND

    close_ns = (ticks[ends] - DOTNET_EPOCH_TICKS) * 100
    frame = pd.DataFrame(
        {
            "minute": pd.to_datetime(
                minutes[starts] * 60,
                unit="s",
                utc=True,
            ),
            "bid_open": bids[starts] / scale,
            "ask_open": asks[starts] / scale,
            "bid_high": np.maximum.reduceat(bids, starts) / scale,
            "ask_high": np.maximum.reduceat(asks, starts) / scale,
            "bid_low": np.minimum.reduceat(bids, starts) / scale,
            "ask_low": np.minimum.reduceat(asks, starts) / scale,
            "bid_close": bids[ends] / scale,
            "ask_close": asks[ends] / scale,
            "tick_count": counts.astype(np.int32),
            "quote_up_count": np.add.reduceat(changes > 0, starts).astype(
                np.int32
            ),
            "quote_down_count": np.add.reduceat(changes < 0, starts).astype(
                np.int32
            ),
            "maximum_quote_gap_seconds": np.maximum.reduceat(
                quote_gaps,
                starts,
            ),
            "close_source_time": pd.to_datetime(close_ns, unit="ns", utc=True),
            "source_file": source.as_posix(),
            "source_file_hash": digest,
        }
    )
    qa = {
        "source_file": source.as_posix(),
        "source_file_hash": digest,
        "tick_count": rows,
        "duplicate_ticks": duplicates,
        "out_of_order_ticks": out_of_order,
        "nonpositive_quotes": nonpositive,
        "crossed_quotes": crossed,
        "maximum_quote_gap_seconds": float(np.max(quote_gaps)),
        "first_tick_utc": _dotnet_iso(int(ticks[0])),
        "last_tick_utc": _dotnet_iso(int(ticks[-1])),
        "m1_rows": int(len(frame)),
    }
    last_quote = PriorQuote(int(ticks[-1]), int(bids[-1]), int(asks[-1]))
    return AggregatedFile(frame, qa, last_quote)


def aggregate_xautk002_streaming(
    path: str | Path,
    prior: PriorQuote | None = None,
    source_hash: str | None = None,
) -> AggregatedFile:
    """Independent Tick-by-Tick M1 implementation for parity QA."""
    source = Path(path)
    scale, _, _, _, _ = _read_header(source)
    digest = source_hash or sha256_file(source)
    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    previous = prior
    tick_count = 0
    duplicate_ticks = 0
    out_of_order_ticks = 0
    maximum_gap = 0.0

    for quote in read_xautk002(source):
        tick_count += 1
        dotnet = _datetime_to_dotnet_ticks(quote.timestamp)
        bid_units = int(round(quote.bid * scale))
        ask_units = int(round(quote.ask * scale))
        minute = quote.timestamp.replace(second=0, microsecond=0)
        if previous is None:
            gap = 0.0
            change = 0
        else:
            gap = (dotnet - previous.dotnet_ticks) / TICKS_PER_SECOND
            if gap < 0:
                out_of_order_ticks += 1
            elif gap == 0:
                duplicate_ticks += 1
            change = int(
                np.sign(
                    (bid_units + ask_units)
                    - (previous.bid_units + previous.ask_units)
                )
            )
        maximum_gap = max(maximum_gap, gap)

        if current is None or current["minute"] != minute:
            if current is not None:
                rows.append(current)
            current = {
                "minute": minute,
                "bid_open": quote.bid,
                "ask_open": quote.ask,
                "bid_high": quote.bid,
                "ask_high": quote.ask,
                "bid_low": quote.bid,
                "ask_low": quote.ask,
                "bid_close": quote.bid,
                "ask_close": quote.ask,
                "tick_count": 0,
                "quote_up_count": 0,
                "quote_down_count": 0,
                "maximum_quote_gap_seconds": 0.0,
                "close_source_time": quote.timestamp,
                "source_file": source.as_posix(),
                "source_file_hash": digest,
            }
        current["bid_high"] = max(float(current["bid_high"]), quote.bid)
        current["ask_high"] = max(float(current["ask_high"]), quote.ask)
        current["bid_low"] = min(float(current["bid_low"]), quote.bid)
        current["ask_low"] = min(float(current["ask_low"]), quote.ask)
        current["bid_close"] = quote.bid
        current["ask_close"] = quote.ask
        current["tick_count"] = int(current["tick_count"]) + 1
        current["quote_up_count"] = int(current["quote_up_count"]) + int(
            change > 0
        )
        current["quote_down_count"] = int(current["quote_down_count"]) + int(
            change < 0
        )
        current["maximum_quote_gap_seconds"] = max(
            float(current["maximum_quote_gap_seconds"]),
            gap,
        )
        current["close_source_time"] = quote.timestamp
        previous = PriorQuote(dotnet, bid_units, ask_units)

    if current is not None:
        rows.append(current)
    frame = pd.DataFrame(rows) if rows else _empty_frame()
    qa = {
        "source_file": source.as_posix(),
        "source_file_hash": digest,
        "tick_count": tick_count,
        "duplicate_ticks": duplicate_ticks,
        "out_of_order_ticks": out_of_order_ticks,
        "nonpositive_quotes": 0,
        "crossed_quotes": 0,
        "maximum_quote_gap_seconds": maximum_gap,
        "m1_rows": len(frame),
    }
    return AggregatedFile(frame, qa, previous)


def _dotnet_iso(value: int) -> str:
    unix_ns = (value - DOTNET_EPOCH_TICKS) * 100
    return pd.Timestamp(unix_ns, unit="ns", tz="UTC").isoformat()


def _datetime_to_dotnet_ticks(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    return DOTNET_EPOCH_TICKS + int(utc.timestamp() * TICKS_PER_SECOND)
