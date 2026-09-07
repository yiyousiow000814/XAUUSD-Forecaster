"""Declared isolated broker inputs, not a cTrader connection or heartbeat.

The real launcher builds the real Algo before invoking this finite CLI adapter.
No Collector, health, transaction, receipt or Observe output is produced here.
OPEN is explicitly synthetic; its real-time inputs prove execution plumbing,
not historical point-in-time data, broker connectivity or prediction quality.
"""
from datetime import datetime, timedelta, timezone
import json
import importlib.util
import math
import os
from pathlib import Path
import stat
import sys
import time

configuration_owner = Path(__file__).resolve().parents[2] / "xauusd_forecaster/runtime_paths.py"
spec = importlib.util.spec_from_file_location("quote_input_configuration", configuration_owner)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SYNTHETIC_INPUT = "ISOLATED_SYNTHETIC_CONTEMPORANEOUS_V1"
MAX_INPUT_RECORD_BYTES = 1024
MAX_INPUT_SECONDS = 2700


def remaining_input_seconds(config, now):
    duration = config.get("quote_input_seconds")
    if type(duration) is not int or duration < 1:
        raise RuntimeError("QUOTE_INPUT_BUDGET_INVALID")
    if duration <= 120 and "connected_scenario_timeout_seconds" not in config:
        return duration
    # Long input belongs to the one finite connected scenario, not to each
    # quote-process restart. Its original deadline is never renewed here.
    budget = config.get("connected_scenario_timeout_seconds")
    try:
        started = datetime.fromisoformat(config["connected_scenario_started_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("QUOTE_INPUT_BUDGET_INVALID") from error
    if (type(budget) is not int or duration != budget or not 0 < budget <= MAX_INPUT_SECONDS or
            started.utcoffset() is None or started > now):
        raise RuntimeError("QUOTE_INPUT_BUDGET_INVALID")
    remaining = math.ceil((started + timedelta(seconds=budget) - now).total_seconds())
    if not 0 < remaining <= budget:
        raise RuntimeError("QUOTE_INPUT_BUDGET_EXHAUSTED")
    return remaining


def _input_policy(config, now):
    remaining_input_seconds(config, now)
    session = config.get("quote_input_session")
    if session not in {"CLOSED", "OPEN"}:
        raise RuntimeError("QUOTE_INPUT_SESSION_UNDECLARED")
    started = now
    if "connected_scenario_timeout_seconds" in config:
        started = datetime.fromisoformat(config["connected_scenario_started_at"])
    deadline = started + timedelta(seconds=config["quote_input_seconds"])
    if session == "CLOSED":
        return session, started, deadline, None
    if (config.get("quote_input_kind") != SYNTHETIC_INPUT or
            "connected_scenario_timeout_seconds" not in config):
        raise RuntimeError("QUOTE_INPUT_SYNTHETIC_DECLARATION_REQUIRED")
    prices = []
    for name in ("quote_input_bid", "quote_input_ask"):
        value = config.get(name)
        try:
            valid = type(value) in {int, float} and math.isfinite(value) and value > 0
        except OverflowError:
            valid = False
        if not valid:
            raise RuntimeError("QUOTE_INPUT_PRICE_INVALID")
        prices.append(float(value))
    if prices[1] < prices[0]:
        raise RuntimeError("QUOTE_INPUT_PRICE_INVALID")
    return session, started, deadline, prices


def _bounded_json(value):
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    if len(raw) > MAX_INPUT_RECORD_BYTES:
        raise RuntimeError("QUOTE_INPUT_RECORD_TOO_LARGE")
    return raw


def _check_owned_path(path, owned):
    if not path.is_relative_to(owned) or path.resolve() != path:
        raise RuntimeError("QUOTE_INPUT_PATH_OUTSIDE_ROOT")
    if path.exists():
        metadata = path.stat()
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise RuntimeError("QUOTE_INPUT_PATH_OUTSIDE_ROOT")


def _append_synthetic_quote(path, owned, config, started, now, prices):
    """Append one complete bridge-format row; restart reads only bounded edges.

    The dedicated fixture file never replaces retained daily quote files. Its
    original config hash, byte cap and elapsed-second sequence survive restart.
    One real launcher owner is required, as for the real bridge's append writer.
    """
    _check_owned_path(path, owned)
    identity = {"source": "isolated-synthetic", "fixture_id": config["fixture_id"],
        "input_contract": SYNTHETIC_INPUT,
        "configuration_sha256": os.environ["XAUUSD_ISOLATED_CONFIGURATION_SHA256"]}
    sequence = math.floor((now - started).total_seconds()) + 1
    limit = config["quote_input_seconds"] * MAX_INPUT_RECORD_BYTES
    opened = now.isoformat()
    with path.open("a+b") as stream:
        size = stream.tell()
        if size > limit:
            raise RuntimeError("QUOTE_INPUT_TOTAL_BYTES_EXCEEDED")
        if size:
            stream.seek(0)
            first = stream.readline(MAX_INPUT_RECORD_BYTES + 1)
            stream.seek(max(0, size - MAX_INPUT_RECORD_BYTES - 1))
            tail = stream.read(MAX_INPUT_RECORD_BYTES + 1)
            try:
                if not first.endswith(b"\n") or len(first) > MAX_INPUT_RECORD_BYTES or not tail.endswith(b"\n"):
                    raise ValueError("incomplete input record")
                first_row = json.loads(first)
                last_row = json.loads(tail.splitlines()[-1])
                for row in (first_row, last_row):
                    if (any(row.get(key) != value for key, value in identity.items()) or
                            row.get("schema") != "xauusd.forward.quote.v1"):
                        raise ValueError("foreign input record")
                last_time = datetime.fromisoformat(last_row["received_time"])
                first_time = datetime.fromisoformat(first_row["event_time"])
                last_sequence = last_row["sequence"]
                if (first_time.utcoffset() is None or last_time.utcoffset() is None or
                        not started <= first_time <= last_time or
                        first_row["received_time"] != first_row["event_time"] or
                        last_row["received_time"] != last_row["event_time"] or
                        type(last_sequence) is not int or
                        last_sequence != math.floor((last_time - started).total_seconds()) + 1 or
                        not 0 < last_sequence <= config["quote_input_seconds"]):
                    raise ValueError("invalid input sequence")
                opened = first_row["event_time"]
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                raise RuntimeError("QUOTE_INPUT_RESTART_RECORD_INVALID") from error
            if last_time > now or last_sequence > sequence:
                raise RuntimeError("QUOTE_INPUT_CLOCK_REGRESSION")
            if last_sequence == sequence or (now - last_time).total_seconds() < 1:
                return False, opened
        row = {"schema": "xauusd.forward.quote.v1", **identity, "symbol": "XAUUSD",
            "event_time": now.isoformat(), "received_time": now.isoformat(),
            "bid": prices[0], "ask": prices[1], "sequence": sequence}
        raw = _bounded_json(row)
        if size + len(raw) > limit:
            raise RuntimeError("QUOTE_INPUT_TOTAL_BYTES_EXCEEDED")
        stream.seek(0, os.SEEK_END)
        stream.write(raw)
        stream.flush()
    return True, opened


def main() -> None:
    # Same owner as every other child; no second locator/digest implementation.
    config = module.isolated_runtime_configuration()
    if config is None:
        raise RuntimeError("QUOTE_INPUT_CONFIGURATION_REQUIRED")
    owned = Path(config["owned_root"])
    runtime = Path(config["runtime_root"])
    secret = Path(config["values"]["CTRADER_SECRET_ROOT"])
    artifact = runtime / "ctrader/XauusdForwardQuoteBridge/bin/Release/net6.0/XauusdForwardQuoteBridge.algo"
    output = runtime / ".local/forward/quotes"
    for path in (runtime, secret, artifact, output):
        _check_owned_path(path, owned)
    expected = ["run", str(artifact), "--ctid", "isolated-sentinel", "--pwd-file",
        str(secret / "ctrader-cli.pwd"), "--account", "isolated-sentinel", "--symbol", "XAUUSD",
        "--period", "m1", "--full-access", "--exit-on-stop", f"--OutputDirectory={output}",
        "--ExpectedSymbol=XAUUSD", "--FlushIntervalSeconds=1"]
    if sys.argv[1:] != expected:
        raise RuntimeError("QUOTE_INPUT_ARGUMENTS_MISMATCH")
    session, started, deadline, prices = _input_policy(config, datetime.now(timezone.utc))
    monotonic_deadline = time.monotonic() + max(0, (deadline - datetime.now(timezone.utc)).total_seconds())
    previous = None
    while time.monotonic() < monotonic_deadline:
        now = datetime.now(timezone.utc)
        if now >= deadline:
            break
        if previous is not None and now < previous:
            raise RuntimeError("QUOTE_INPUT_CLOCK_REGRESSION")
        previous = now
        opened = None
        if session == "OPEN":
            path = output / ("xauusd-quotes-synthetic-" + config["fixture_id"] + ".jsonl")
            appended, opened = _append_synthetic_quote(path, owned, config, started, now, prices)
            if not appended:
                time.sleep(min(1, max(0, monotonic_deadline - time.monotonic())))
                continue
        value = {"schema": "xauusd.forward.market-session.v1", "source": "ctrader-cli",
            "symbol": "XAUUSD", "observed_at": now.isoformat(), "server_time": now.isoformat(),
            "is_open": False, "time_till_open_seconds": 3600, "time_till_close_seconds": 0,
            "next_open_time": (now + timedelta(hours=1)).isoformat(), "next_close_time": None,
            "opened_at": None, "first_quote_after_open_at": None}
        if session == "OPEN":
            close = deadline + timedelta(minutes=30, seconds=1)
            value.update(source="isolated-synthetic", fixture_id=config["fixture_id"],
                input_contract=SYNTHETIC_INPUT,
                configuration_sha256=os.environ["XAUUSD_ISOLATED_CONFIGURATION_SHA256"],
                is_open=True, time_till_open_seconds=0,
                time_till_close_seconds=(close - now).total_seconds(),
                next_open_time=None, next_close_time=close.isoformat(),
                opened_at=opened, first_quote_after_open_at=opened)
        temporary = output / ("market-session." + str(os.getpid()) + ".tmp")
        _check_owned_path(temporary, owned)
        target = output / "market-session.json"
        _check_owned_path(target, owned)
        try:
            temporary.write_bytes(_bounded_json(value))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        time.sleep(min(1, max(0, monotonic_deadline - time.monotonic())))


if __name__ == "__main__":
    main()
