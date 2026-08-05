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

Build inside the repository with external publishing explicitly disabled:

```text
dotnet build XauusdForwardQuoteBridge.csproj -c Release -p:AlgoPublish=false
```

The live CLI launcher is intentionally separate from build and tests. It must
be started by the owner because repository automation policy does not permit an
agent to open a live CLI account session.
