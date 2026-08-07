# Free Data Feasibility Plan

Free does not mean decision-time safe. Phase 2F admits a source only after its
real Forward receipt time, revision behavior, latency, and license are logged.
There is no historical news or consensus backfill.

## Tier 1: Forward Shadow core

### XAUUSD Bid/Ask Tick

- Source: a repository-local read-only live quote bridge.
- Historical role: only the recent tail needed to initialize U5, marked
  `WARMUP_ONLY` and excluded from training and performance.
- Forward role: executable labels, spread, returns, quote rate, imbalance,
  volatility, U5, and Market-only Challenger features.
- Status: required but not configured; the collector currently records
  `MARKET_DATA_MISSING` and `WAIT`.

### Broker-visible USD factor

- Source: cTrader-visible FX Bid/Ask quotes from the same account clock.
- Existing local candidates include EURUSD, USDJPY, and USDCAD Tick archives
  from 2017 through 2026. The live account symbol inventory must be captured
  before fixing the basket.
- Role: a transparent signed USD factor using one frozen formula; no downloaded
  DXY series and no outcome-selected basket weights.
- Status: preferred free intraday USD route. The basket formula is not frozen
  until common-clock coverage and symbol availability pass.

### Official Forward news

- Federal Reserve official press, monetary-policy, and speeches/testimony RSS
  feeds are active.
- BLS Employment Situation, CPI, and JOLTS RSS adapters are configured but the
  current machine receives HTTP 403; each failure is recorded independently.
  A separate Google News query restricted to `bls.gov` discovers official
  Employment Situation, CPI, and JOLTS release pages. It uses the later local
  receipt time and may enter the model only when the resolved publisher is
  `bls.gov`, a complete body was actually received, and Gemini finished before
  the decision. The BLS Public Data API remains the authoritative free path for
  actual payroll, earnings, unemployment, CPI, and JOLTS values and revisions.
- Separate employment, inflation, and Fed/rates Google News lanes process the
  complete returned feed before applying a bounded unseen-first work limit.
  These general media lanes are discovery/display evidence and do not become
  model votes merely because their headlines were collected.
- `collector_first_seen_time`, not publisher time, controls visibility.
- Revisions append new rows and never replace old content.

## Tier 2: optional slow-frequency background only

### Treasury nominal and real yields

- Source: US Treasury daily nominal and real yield XML feeds.
- Role: latest-known slow background state, never a five-minute yield move.
- Constraint: daily publication cannot support an intraday `5m/1h` yield
  feature. Receipt time must be logged; no value is usable before publication.

### FRED and ALFRED

Deferred. Phase 2F does not need them to start and will not backfill them.

### BEA official releases

Deferred until an official Forward adapter is added. Publisher timestamps do
not replace local first-seen timestamps.

## Tier 3: deferred from version 1 Full-model actions

### FedWatch probabilities

CME documents a FedWatch API, but the official API is a licensed/fee surface.
The public webpage is not accepted as a free production data feed. Scraping it
would create stability, licensing, latency, and historical-replay debt.

Rate-expectation change is therefore deferred unless the broker exposes a
usable cTrader instrument with sufficient historical and live coverage, or the
owner later approves a licensed source.

### High-impact macro surprise

Official actual releases are free; reliable point-in-time consensus history is
not yet established. Version 1 may record release events and actual values for
diagnosis, but `surprise` cannot drive `LONG` or `SHORT` until the consensus
source contract passes.

## Fail-closed rules

- A stale or missing Tier-1 field makes the affected model output unhealthy.
- The effective action becomes `WAIT`; values are never silently forward-filled.
- Tier-2 daily values retain their actual availability timestamp and a
  `slow_background` marker.
- A missing optional external module may allow the XAU-only baseline to run,
  but it cannot be represented as a healthy Full-model prediction.
- Every source response is hashed or stored with enough provenance to replay
  the exact as-known feature snapshot.
