# XAUUSD Forecaster

This module is the home of a decision-support system for XAUUSD. Its first
target is deliberately narrow: at a fixed decision clock, estimate the
after-cost value and uncertainty of `LONG`, `SHORT`, and `WAIT` over a declared
horizon.

The system is not an autonomous self-modifying trading robot. Production
artifacts stay frozen. Research produces versioned challengers, and a
challenger may become the new champion only after chronological, shadow, cost,
stability, and operational gates pass.

## Current status

Phase 2F: Forward-only Evidence and Learning Engine. The production Champion
is Always Wait; Challengers are Shadow-only and this module does not place
orders. Historical observations may initialize U5 only and cannot enter
training or performance evaluation.

The accepted owner decisions are recorded in
[`docs/PRODUCT_CONTRACT.md`](docs/PRODUCT_CONTRACT.md). The system and
validation boundaries are recorded in
[`docs/SYSTEM_CONTRACT.md`](docs/SYSTEM_CONTRACT.md), and the free-source
feasibility review is in [`docs/FREE_DATA_PLAN.md`](docs/FREE_DATA_PLAN.md).
The active behavior is frozen in
[`docs/FORWARD_ONLY_CONTRACT.md`](docs/FORWARD_ONLY_CONTRACT.md). The older
Replay contract is inactive and retained only as design history.

## Intended delivery order

1. Freeze the product, data, label, validation, and deployment contracts.
2. Build the append-only prediction ledger and point-in-time source inventory.
3. Start the Forward collector and record every five-minute event, including
   explicit outages.
4. Establish simple frozen baselines and a calibrated `WAIT` decision.
5. Train versioned Challengers only from matured Forward outcomes.
6. Add a separately approved execution adapter only after the research and
   runtime behavior match.

## Placement

Source code, tests, and reviewable design documents belong in this directory.
Raw market data, feature snapshots, databases, model binaries, logs, replay
outputs, and other generated evidence must stay in an ignored local artifact
tree defined before ingestion; they must not be committed beside the source.

## Local development

```text
python -m pytest -q tests
```

Initialize the bounded U5 state once. Build and start the read-only cTrader
quote bridge, then point the Forward collector at its output directory:

```text
python scripts/initialize_u5_warmup.py
powershell -File ctrader/XauusdForwardQuoteBridge/run_live_quote_bridge.ps1
python scripts/run_forward_collector.py --market-jsonl .local/forward/quotes
```

## Local control center

Use `scripts/xauusd_control_center.ps1` as the single Windows control surface
for the collector, Gemini annotator, dashboard API, and Sites synchronizer. It
supports a visible GUI, component-level start/stop controls, logs, and an
explicit opt-in Windows logon task. Auto-start is disabled until the owner
enables it in the control center.

Without `--market-jsonl`, the market adapter is intentionally unconfigured and
every five-minute event records `WAIT / MARKET_DATA_MISSING`. A read-only live
quote bridge can be supplied with `--market-jsonl <path>`. The required JSONL
fields are frozen in `config/forward.example.json`.

Generated databases, epoch receipts, U5 state, logs, and model artifacts stay
under the ignored `.local/forward/` tree. Completed quote days are compressed
with checksum receipts, and a verified SQLite online backup is kept locally.
Run `scripts/run_news_annotator.py` to use the Gemini 3.5 Flash-Lite
fixed-schema news annotator. `GEMINI_API_KEYS` accepts a semicolon-separated
rotation pool, while `GEMINI_API_KEY` remains the single-key fallback. Both are
read from the local environment and never committed. Neither the collector nor the annotator contains an
order-submission surface.

Every outbound Gemini attempt is reserved in `.local/forward/gemini-quota.json`
before transmission. The file stores anonymous key fingerprints only, stops a
key at 500 attempts for the Pacific quota day, and starts a fresh counter at
Pacific midnight. Google applies Gemini rate limits per project rather than per
API key, so keys belonging to one project may still share the upstream quota.
The final 150 Flash requests are reserved for monetary-policy, CPI, and payroll
events. Failed validation is written to the append-only failure ledger and
backed off; a repeated permanent error is isolated instead of being retried
every minute. The status page separates ready, queued, backing-off, and
isolated items.
Gemini produces a Simplified Chinese display headline and full-content summary.
Mixed-language output and source-inconsistent numeric lexemes are rejected or
repaired before append. Headline-only translations remain presentation-only and
never create model features. Syndicated duplicate clusters are represented by
the strongest available body instead of repeated mirror rows.

After 200 matured complete Forward rows, the collector automatically trains a
versioned Market-only, News-residual, and Full Shadow Challenger set. A new set
is trained after each additional 50 eligible rows. Training never promotes a
Champion and never changes the effective `WAIT` action without an owner
approval recorded against frozen evidence.
