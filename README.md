# XAUUSD Forecaster

A dashboard for XAUUSD quotes, curated news events, source evidence, daily
briefs, and event storylines. Live OOS forecasting and its prediction, scoring,
and decision history have been retired from this source revision.

**It does not place orders or connect to a trading account.**

## Open-source boundary

The source code is available under the [MIT License](LICENSE). Training data
and trained model artifacts from retired experiments, runtime
databases, market quotes, news archives,
credentials, and production configuration are not published. A user of this
repository must provide lawful data sources; cloning does not reproduce
the deployed data.

This is a personal, owner-maintained repository published to support its CI
workflow. External contributions, issues, feature requests, and support requests
are not accepted.

## What you can see

- current quotes and market availability;
- curated current news events and their source evidence;
- daily briefs and event storylines;
- data-source, component, and synchronization health.

## How it works

```text
cTrader Bid/Ask + timestamped news
                 ↓
       Collector / Annotator
                 ↓
      Curated news / event evidence
                 ↓
        Dashboard publication
```

Source timestamps, annotation evidence, and event identity remain traceable.

## Research boundaries

- XAUUSD only;
- executable Bid/Ask prices and traceable news timestamps;
- observation only, with no order authority.

## Run locally

On Windows, use the control center to start the Collector, Annotator, Dashboard
API, and synchronizer:

```powershell
powershell -File scripts/run_main_services.ps1 -Action StatusJson
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
