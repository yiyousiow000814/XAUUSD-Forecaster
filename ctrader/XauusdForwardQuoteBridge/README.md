# XAUUSD Forward Quote Bridge

This cTrader Algo only writes broker-native XAUUSD Bid/Ask Tick observations.
It contains no order, position, volume, or account-management call.

Each UTC day is appended to a repository-local JSONL file with:

```text
schema, source, symbol, event_time, received_time, bid, ask, sequence
```

`event_time` is cTrader server time. `received_time` is the local UTC time when
the Algo callback handled the Tick. The Forecaster applies the receipt-time
cutoff and reads only observations visible at the decision boundary.

The Algo also atomically refreshes `market-session.json` on every timer tick.
That heartbeat contains broker-native `MarketHours.IsOpened()`,
`TimeTillOpen()`, and `TimeTillClose()` observations. The Forecaster requires a
fresh heartbeat and never emits a decision while the market is closed or the
fixed 30-minute horizon would cross the next broker close.

Build inside the repository with external publishing explicitly disabled:

```text
dotnet build XauusdForwardQuoteBridge.csproj -c Release -p:AlgoPublish=false
```

The live CLI launcher is intentionally separate from build and tests. It must
be started by the owner because repository automation policy does not permit an
agent to open a live CLI account session.

For the standalone repository, configure the two local paths once at user
scope. They contain paths only; account credentials remain in the external
secret directory:

```powershell
[Environment]::SetEnvironmentVariable('CTRADER_CLI_PATH', 'C:\path\to\ctrader-cli.exe', 'User')
[Environment]::SetEnvironmentVariable('CTRADER_SECRET_ROOT', 'C:\path\to\secret-directory', 'User')
```

The launcher also accepts `-CliPath` and `-SecretRoot`, or reads pointer files
from `.local\config`. None of these local values belong in Git.
