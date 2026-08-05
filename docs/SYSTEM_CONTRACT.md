# XAUUSD Forecasting System Contract

## Objective

At each frozen decision event, estimate the executable Bid/Ask
quote-cost-adjusted value and uncertainty of `LONG`, `SHORT`, and `WAIT`. The
first implementation should use
one primary clock and one primary horizon. It must not attempt to explain or
predict every possible gold narrative.

The product contract must distinguish two questions:

1. **Forecast label:** the counterfactual value at a fixed future horizon.
2. **Trading policy:** entry, holding, stop, target, sizing, and early-exit
   behavior.

A useful forecast does not by itself prove that a trading policy is profitable.

The version-1 product decisions and quantitative evidence gates are frozen in
`PRODUCT_CONTRACT.md`.

## Production and learning isolation

```text
point-in-time sources -> feature snapshot -> frozen Champion -> decision ledger
                                                     |
completed labels -> replay/training -> Challenger -> shadow ledger -> gates
                                                               |
                                                  manual promotion + rollback
```

- The Champion is immutable during a deployed version.
- Training writes a new versioned Challenger; it cannot edit the Champion.
- Champion and Challenger predict on the same eligible decision events.
- Promotion creates a new version and preserves immediate rollback.
- A detected drift increases uncertainty, raises the `WAIT` rate, or starts a
  bounded research campaign. It never implies automatic signal reversal.

## Decision-event contract

Before any outcome is opened, freeze:

- event clock, time zone, horizon, signal expiry, and overlap policy;
- exact observable fields, source timestamps, receipt timestamps, units,
  lookbacks, shifts, null handling, and freshness limits;
- executable entry and exit quote semantics, commission, swap, and slippage;
- `U5` definition and every normalization/conversion;
- eligible event universe and reasons an event becomes `WAIT` or unavailable;
- candidate budget, training cutoff, controls, gates, and kill conditions.

The new bar's open may be used only after it is observable. Completed high,
low, close, MFE, MAE, final PnL, revised macro data, and final news labels may
not influence an earlier decision.

## Ledger contract

The ledger is append-only. A decision record is never updated after outcomes
are known. Outcome labels and execution reports are separate records joined by
`decision_id`.

Minimum decision record:

```text
decision_id, decision_time, decision_clock_version
feature_snapshot_hash, source_snapshot_ids, source_event_times
source_received_times, freshness_state, model_version
ev_long, ev_short, uncertainty_long, uncertainty_short
estimated_cost_long, estimated_cost_short, action, signal_expiry
reason_codes, eligibility_or_wait_reason
```

Minimum label record:

```text
decision_id, label_time, label_contract_version
counterfactual_long_return, counterfactual_short_return
mfe_long, mae_long, mfe_short, mae_short
quote_coverage, ambiguity_state
```

Execution reports remain separate because actual fills and slippage exist only
for submitted orders. Counterfactual labels must use the same executable quote
rules for both directions and must expose missing-quote ambiguity.

For `received-time-executable-30m-v2`, entry is the first valid Bid/Ask quote
whose collector `received_time` is strictly after the decision event and no
later than signal expiry. The terminal quote is the first valid quote whose
`received_time` is at or after 30 minutes from the actual received entry. Both
event and receipt times and delays remain in the outcome. Long enters
at Ask and exits at Bid; Short enters at Bid and exits at Ask. One explicitly
configured round-trip commission may be subtracted only after a versioned
account contract exists. Current evidence reports
`commission_status=UNCONFIGURED` and
`slippage_status=UNAVAILABLE_SHADOW`; it is not net PnL.

Every 5-minute event has two action fields:

- `recommended_action`: the model's direction gate on the common research
  clock;
- `effective_action`: the user-facing Shadow action after data-health and
  active-signal safety checks.

This distinction prevents the 30-minute no-overlap rule from deleting the
paired Full versus XAU-only comparison set.

## Frozen U5 reporting unit

Version 1 reuses the repository's causal finite-memory U5 formula as a
reporting and scale feature, not as a Long/Short selector:

```text
A_t = max over j=0..30 of abs(log(mid_(t-30+j) / mid_(t-30)))
Q_t = rolling 0.99 quantile of the latest 10,000 completed A observations
K_t = 2 * (ask_close_t - bid_close_t) / mid_close_t
U5_t = max(Q_t, A_t, K_t)
```

It becomes available only after 10,000 completed observations. It is frozen at
the decision event for later EV normalization. U5 does not vote for direction,
does not repair missing data, and is not a complete slippage or tail-loss bound.

## Model boundaries

The proposed macro, market-confirmation, and execution-timing components are
evidence modules, not independent traders. Their outputs may be combined only
after they share the same target, unit, horizon, cost convention, and
calibration contract.

Correlated inputs must not be counted repeatedly. For example, a USD move used
by both the macro and confirmation modules is one observation, not two votes.
Combination weights must be frozen or learned inside the training partition;
they cannot be chosen from final OOS performance.

The baseline should be a regularized linear model. A small gradient-boosting
model may be a bounded Challenger. Deep learning, reinforcement learning, and
unrestricted automated feature search are outside the first version.

## Point-in-time data contract

Each raw field needs a source inventory containing:

```text
field, provider, instrument_or_series, event_time, received_time
as_known_value, revision_id, transformation, lookback, shift
unit, freshness_limit, null_policy, license, action_surface
```

Macro releases need the value known at the decision time, including the
then-known previous value and subsequent revision identity. Latest revised
history cannot stand in for a historical live feed. News, war, risk sentiment,
central-bank purchases, and liquidity are not valid features until each has an
objective point-in-time source and latency contract.

No source may silently forward-fill across a stale interval. Missing required
action-bearing data produces `WAIT` and a machine-readable reason.

## Validation contract

- Use chronological walk-forward evaluation. Random row splits are invalid.
- Purge and embargo samples whose 30-minute outcome windows overlap a later
  partition boundary.
- Compare against `WAIT`, exact opposite direction, simple price-only models,
  and same-clock matched controls.
- Evaluate every eligible decision event, not only executed trades or events a
  model liked.
- Report absolute quote-cost-adjusted EV separately from directional accuracy and from
  incremental value over controls.
- Report calibration, uncertainty coverage, complete-period stability, tail
  loss, drawdown, cost stress, source outages, and winner concentration.
- Keep one final OOS partition sealed. Shadow-live results cannot be backfilled
  or retroactively redefined.

Paired model-value inference uses every same-clock event with day/week block
bootstrap. The secondary user-facing policy replay applies the independent
30-minute active-signal lock and reports non-overlapping PF, drawdown, Sharpe,
frequency, and concentration. Neither view may replace the other.

The thresholds are frozen in `PRODUCT_CONTRACT.md`. Passing them still creates
research evidence only; this application has no order-submission authority.


## Runtime safety boundary

Version 1 defaults to prediction and shadow recording only. Any later execution
adapter must be separately approved and fail closed to `WAIT` on stale data,
service loss, clock drift, abnormal spread, duplicate decisions, unknown model
versions, invalid size, or breached risk limits.

The execution adapter may reject unsafe signals but must not invent a different
direction, timing model, or exit policy. Research replay and runtime must use
the same live-feasible decision semantics before any promotion claim.
