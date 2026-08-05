# XAUUSD Forecaster

This module is the home of a decision-support system for XAUUSD. Its first
target is deliberately narrow: at a fixed decision clock, estimate the
executable Bid/Ask quote-return value and uncertainty of `LONG`, `SHORT`, and
`WAIT` over a declared horizon. Commission is explicitly unconfigured and
Shadow slippage is unavailable, so current results are not net PnL.

The system is not an autonomous self-modifying trading robot. Production
artifacts stay frozen. Research produces versioned challengers, and a
challenger may become the new champion only after chronological, shadow, cost,
stability, and operational gates pass.

## Current status

Phase 2F: Forward-only Evidence and Learning Engine. The production Champion
is Always Wait; Challengers are Shadow-only and this module does not place
orders. Historical observations may initialize U5 only and cannot enter
training or performance evaluation. Append-only repair rows are isolated as
`REPAIRED_SEED`; only predictions created after `EVALUATION_EPOCH_V2` can
produce `LIVE_OOS` learning-curve evidence.

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
Run `scripts/run_news_annotator.py` to use the fixed-schema news annotator.
Gemini 3.5 Flash-Lite is primary; Gemini 3.1 Flash-Lite takes routine full-text
work only after the primary routine budget reaches its protected reserve.
`GEMINI_API_KEYS` accepts a semicolon-separated
rotation pool, while `GEMINI_API_KEY` remains the single-key fallback. Both are
read from the local environment and never committed. Neither the collector nor the annotator contains an
order-submission surface.

Every outbound Gemini attempt is reserved before transmission. Gemini 3.5 and
3.1 use independent local quota ledgers; both store anonymous key fingerprints
only, stop a key at 500 attempts for the Pacific quota day, and start fresh at
Pacific midnight. Google applies Gemini rate limits per project and model, so
the local ledgers remain conservative safety limits rather than provider truth.
The final 150 Flash requests are reserved for monetary-policy, CPI, and payroll
events. Display-number and language validation problems are recovered locally;
an untrusted translation is retained as a zero-confidence neutral audit record.
Provider and malformed-response failures use the append-only failure ledger and
bounded backoff instead of being retried every minute. The status page separates
ready, queued, backing-off, and isolated items.
Gemini produces a Simplified Chinese display headline and full-content summary.
Mixed-language output and source-inconsistent numeric lexemes are repaired or
neutralized before append. Headline-only translations remain presentation-only and
never create model features. Syndicated duplicate clusters are represented by
the strongest available body instead of repeated mirror rows.

At 96 complete V2 rows, the collector creates a Market-only Preview whose
effective action remains `WAIT`. At 200 rows it creates frozen Shadow
Challengers, then creates a new version after each additional 50 eligible rows.
Each frozen version remains under parallel Shadow evaluation for up to 60
distinct valid OOS days; unhealthy predictions do not shorten that evaluation
window.
News-residual and Full versions require their own minimum news exposure,
cluster, and event-day evidence; they are not fabricated when those gates fail.
Every version is scored only on decisions created after that version. Sixty
trading days is a confidence milestone, not a reason to delay Preview or
Shadow learning. Training never promotes a Champion and never enables orders.
