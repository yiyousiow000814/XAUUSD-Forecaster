# XAUUSD Forecaster

A research system that estimates the direction of XAUUSD over the next 30
minutes.

Every five minutes, it records a `LONG`, `SHORT`, or `WAIT` forecast. After 30
minutes, it records the executable Bid/Ask outcome and uses only matured results
to train the next model group.

**It does not place orders or connect to a trading account.**

[Open the live research dashboard](https://aurum-signal-room.yiyousiow1234.workers.dev/)

## What you can see

- the latest 30-minute forecast;
- the observed outcome of each forecast;
- results from price-only and news-assisted models;
- the news evidence available before each decision;
- data-source, component, and synchronization health.

## How it works

```text
cTrader Bid/Ask + timestamped news
                 ↓
       Collector / Annotator
                 ↓
        Frozen Shadow models
                 ↓
   Record the outcome after 30 minutes
```

Each forecast is frozen before its outcome exists. Late news cannot rewrite a
past decision, and new data cannot alter an old model version.

## Research boundaries

- XAUUSD only;
- one forecast every five minutes;
- one fixed 30-minute horizon;
- executable Bid/Ask prices and traceable news timestamps;
- Shadow research only, with no order authority;
- model promotion requires manual owner approval.

## Run locally

On Windows, use the control center to start the Collector, Annotator, Dashboard
API, and synchronizer:

```powershell
powershell -File scripts/xauusd_control_center.ps1
```

Run the test suite:

```powershell
python -m pytest -q tests
```

Databases, logs, quotes, model files, and other runtime artifacts stay in the
ignored `.local/forward/` directory and are not uploaded to GitHub.

## Detailed documentation

- [Product contract](docs/PRODUCT_CONTRACT.md)
- [System and data boundaries](docs/SYSTEM_CONTRACT.md)
- [Forward-only learning contract](docs/FORWARD_ONLY_CONTRACT.md)
- [Cloudflare hosting](docs/CLOUDFLARE_HOSTING.md)
