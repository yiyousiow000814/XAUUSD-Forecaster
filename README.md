# XAUUSD Forecaster

A research system that estimates the direction of XAUUSD over the next 30
minutes.

Every five minutes, it records a `LONG`, `SHORT`, or `WAIT` forecast. After 30
minutes, it records the executable Bid/Ask outcome and uses only matured results
to train the next model group.

**It does not place orders or connect to a trading account.**

[Open the live research dashboard](https://aurum-signal-room.yiyousiow1234.workers.dev/)

## Open-source boundary

The source code is available under the [MIT License](LICENSE). Training data,
runtime databases, market quotes, news archives, trained model artifacts,
credentials, and production configuration are not published. A user of this
repository must provide lawful data sources and train their own models; cloning
the repository does not reproduce the deployed forecasts.

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

Never commit provider keys or local deployment configuration. Use ignored
`.env`, `.dev.vars`, or `.local/` files and keep shareable examples free of
credentials.

## Detailed documentation

- [Documentation index and taxonomy](docs/README.md)
- [Product specification](docs/specs/PRODUCT.md)
- [System boundaries contract](docs/contracts/SYSTEM_BOUNDARIES.md)
- [Forward-only evidence contract](docs/contracts/FORWARD_ONLY.md)
- [Cloudflare hosting design](docs/design/CLOUDFLARE_HOSTING.md)
- [Assistant target architecture](docs/design/ASSISTANT_ARCHITECTURE.md)

Security issues should be reported through
[GitHub private vulnerability reporting](SECURITY.md), not a public issue.
