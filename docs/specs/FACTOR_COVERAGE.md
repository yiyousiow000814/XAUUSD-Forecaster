# Factor Coverage Specification

This specification defines coverage states, feature exposure, and dashboard
behavior. It does not redefine the hard system, point-in-time, append-only, or
news-authority invariants owned by the
[`SYSTEM_BOUNDARIES`](../contracts/SYSTEM_BOUNDARIES.md),
[`FORWARD_ONLY`](../contracts/FORWARD_ONLY.md), and
[`NEWS_EVIDENCE`](../contracts/NEWS_EVIDENCE.md) contracts.

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

XAUUSD Bid/Ask is the only action-bearing live market source. News intake is
permission-neutral: every item must pass publisher-identity, full-body,
first-seen, parsed-at, event-time, and content-hash checks. Source officiality,
reliability, corroboration, independence, and syndication are versioned model
features rather than source allowlists. BLS, EIA, BEA, and FRED observations are
retained point-in-time; configured v15 series are exposed as separate numeric
features so OOS learning determines their incremental value. News never
receives order authority.

Rates, real yields, direct USD market observations, oil, broad geopolitical
news, central-bank purchases, liquidity, and risk-asset proxies remain explicit
coverage gaps until a free point-in-time source passes provenance, cadence,
freshness, outage, and explicit eligibility tests.

## Incremental-value rule

New domains enter as separate versioned modules. They must be compared against
the same-clock XAU-only baseline. A domain is retained only when untouched
Forward evidence shows positive incremental quote-cost-adjusted value with uncertainty
reported. Disagreement, stale inputs, missing sources, or unexplained shocks
increase uncertainty and route the effective action to `WAIT`.
