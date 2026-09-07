"""Declared CLOSED broker-input boundary, not a cTrader connection or heartbeat.

The real launcher builds the real Algo before invoking this finite CLI adapter.
No Collector, health, transaction, receipt or Observe output is produced here.
"""
from datetime import datetime, timedelta, timezone
import json
import importlib.util
import math
import os
from pathlib import Path
import sys
import time

configuration_owner = Path(__file__).resolve().parents[2] / "xauusd_forecaster/runtime_paths.py"
spec = importlib.util.spec_from_file_location("quote_input_configuration", configuration_owner)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def remaining_input_seconds(config, now):
    duration = config.get("quote_input_seconds")
    if type(duration) is not int or duration < 1:
        raise RuntimeError("QUOTE_INPUT_BUDGET_INVALID")
    if duration <= 120:
        return duration
    # Long input belongs to the one finite connected scenario, not to each
    # quote-process restart. Its original deadline is never renewed here.
    budget = config.get("connected_scenario_timeout_seconds")
    try:
        started = datetime.fromisoformat(config["connected_scenario_started_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("QUOTE_INPUT_BUDGET_INVALID") from error
    if (type(budget) is not int or duration != budget or not 120 < budget <= 2700 or
            started.utcoffset() is None or started > now):
        raise RuntimeError("QUOTE_INPUT_BUDGET_INVALID")
    remaining = math.ceil((started + timedelta(seconds=budget) - now).total_seconds())
    if not 0 < remaining <= budget:
        raise RuntimeError("QUOTE_INPUT_BUDGET_EXHAUSTED")
    return remaining


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
        if not path.is_relative_to(owned):
            raise RuntimeError("QUOTE_INPUT_PATH_OUTSIDE_ROOT")
    expected = ["run", str(artifact), "--ctid", "isolated-sentinel", "--pwd-file",
        str(secret / "ctrader-cli.pwd"), "--account", "isolated-sentinel", "--symbol", "XAUUSD",
        "--period", "m1", "--full-access", "--exit-on-stop", f"--OutputDirectory={output}",
        "--ExpectedSymbol=XAUUSD", "--FlushIntervalSeconds=1"]
    if sys.argv[1:] != expected:
        raise RuntimeError("QUOTE_INPUT_ARGUMENTS_MISMATCH")
    duration = remaining_input_seconds(config, datetime.now(timezone.utc))
    if config.get("quote_input_session") != "CLOSED":
        raise RuntimeError("QUOTE_INPUT_SESSION_UNDECLARED")
    for _ in range(duration):
        now = datetime.now(timezone.utc)
        value = {"schema": "xauusd.forward.market-session.v1", "source": "ctrader-cli",
            "symbol": "XAUUSD", "observed_at": now.isoformat(), "server_time": now.isoformat(),
            "is_open": False, "time_till_open_seconds": 3600, "time_till_close_seconds": 0,
            "next_open_time": (now + timedelta(hours=1)).isoformat(), "next_close_time": None,
            "opened_at": None, "first_quote_after_open_at": None}
        temporary = output / ("market-session." + str(os.getpid()) + ".tmp")
        temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, output / "market-session.json")
        time.sleep(1)


if __name__ == "__main__":
    main()
