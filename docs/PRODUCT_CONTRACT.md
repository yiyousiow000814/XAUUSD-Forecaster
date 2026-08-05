# Frozen Product Contract

## Product identity

This is an XAUUSD forecasting application, not an automated trading system.
Version 1 answers one question:

```text
At this decision event, should a 30-minute XAUUSD shadow position be LONG,
SHORT, or WAIT?
```

## Frozen version 1 behavior

| Surface | Contract |
|---|---|
| Instrument | XAUUSD only |
| Decision clock | Every 5 minutes |
| Forecast horizon | Fixed 30 minutes |
| Forecast actions | `LONG`, `SHORT`, `WAIT` |
| Forecast exit | First executable quote at or after 30 minutes |
| User-facing shadow exposure | At most one active directional signal |
| Deployment mode | Shadow only; no order submission |
| Model lifecycle | Frozen Champion; no in-place or online update |
| Promotion owner | Repository owner, manually approved |
| Primary objective | Executable quote-cost-adjusted direction value, not accuracy |

Every 5-minute event is still predicted and labelled for paired research. When
a user-facing `LONG` or `SHORT` shadow signal is active, later forecasts remain
in the research ledger but their effective user-facing action is `WAIT` with
reason `ACTIVE_SIGNAL`. This preserves both non-overlapping signals and a
common decision universe for Full versus XAU-only comparison.

## Information scope

The XAU-only baseline may use point-in-time XAUUSD Bid/Ask Tick data, returns,
spread, quote activity, volatility, extension, and the frozen U5 scale.

The first Full-model campaign may add only source-qualified versions of:

- a USD market factor;
- a US short-rate or market-rate-expectation proxy;
- a real-yield background or proxy;
- a high-impact macro release observation;
- XAUUSD confirmation of the external move.

Official full-body news may enter only the frozen News-residual eligibility
lane. Display-only or collect-only oil, geopolitical, and central-bank-purchase
coverage cannot enter a model. Reinforcement learning, dynamic exits, multiple
horizons, LLM direction decisions, and unrestricted feature search are
excluded.

## Direction and uncertainty gate

The direction with the larger predicted quote-cost-adjusted EV is the proposed
direction. It is admitted only when its precomputed 95% lower confidence bound
is strictly positive. Equal directional EVs, unhealthy data, or a non-positive
best-direction lower bound produce `WAIT`.

The gate consumes uncertainty estimates from a separately validated model or
calibration process. It does not manufacture a confidence interval from one
forecast.

## Success hierarchy

1. **Technical integrity:** point-in-time inputs, executable Bid/Ask labels,
   deterministic replay, append-only evidence, and fail-closed data health.
2. **Incremental information value:** on paired decision events,
   `LCB95(EV_Full) > 0` and `LCB95(EV_Full - EV_XAU-only) > 0`.
3. **Trading-quality evidence:** `PF >= 1.5`, `MaxDD <= 20%`, `Sharpe >= 1.0`,
   and about 10 to 50 non-overlapping directional signals per month.

The learning pipeline creates a non-actionable Preview at 96 eligible rows, an
initial Shadow version at 200 rows, and a new immutable version after every 50
additional rows. Sixty trading days is a confidence milestone, not a training
blocker. Candidate-valid evidence still requires at least 60 trading days and
100 non-overlapping signals. Version-1 success requires at least 6 months of
frozen Shadow evidence and 200 non-overlapping signals, including different
volatility and macro-event conditions.

Uncertainty must be clustered by day or week. Five-minute observations and
overlapping 30-minute labels are not independent rows.

## Owner-approved operational defaults

- Start on the local machine using free, source-qualified data.
- Consider a VPS only after the frozen system shows useful evidence.
- A local web interface and API are desirable after the ledger/replay core.
- Telegram and cTrader display adapters are optional consumers, not sources of
  trading authority.
- Only the owner can approve a model promotion.
- Promotion remains manual and versioned with rollback.
