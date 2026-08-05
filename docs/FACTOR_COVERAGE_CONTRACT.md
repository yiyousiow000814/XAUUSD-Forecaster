# XAUUSD Factor Coverage Contract

The forecaster must keep a broad view without pretending that every named
driver is available, timely, or useful at a 30-minute horizon. Every domain is
reported as `LIVE`, `COLLECTING`, `NEWS_ONLY`, `LIMITED_NEWS`, or
`NOT_CONNECTED`. A missing domain is visible in the audit dashboard and cannot
be silently replaced by an LLM opinion.

## Time scales

- Fast factors: XAUUSD, USD, nominal yields, real-yield proxies, oil,
  liquidity, and risk appetite require point-in-time minute observations.
- Event factors: inflation, employment, central-bank communication, and
  geopolitical news require actual collector receipt times and immutable
  revisions.
- Slow factors: central-bank gold purchases and structural liquidity may set a
  background regime but cannot directly trigger a five-minute action.

## Active boundary

XAUUSD Bid/Ask is the only action-bearing live market source. Federal Reserve
RSS, BLS observations, and Gemini annotations are collected evidence. News may
enter a News-residual Challenger only after the annotation was visible at the
decision time and after the fixed training threshold. It never receives order
authority.

Rates, real yields, direct USD market observations, oil, broad geopolitical
news, central-bank purchases, liquidity, and risk-asset proxies remain explicit
coverage gaps until a free point-in-time source passes provenance, cadence,
freshness, and outage tests.

## Incremental-value rule

New domains enter as separate versioned modules. They must be compared against
the same-clock XAU-only baseline. A domain is retained only when untouched
Forward evidence shows positive incremental after-cost value with uncertainty
reported. Disagreement, stale inputs, missing sources, or unexplained shocks
increase uncertainty and route the effective action to `WAIT`.
