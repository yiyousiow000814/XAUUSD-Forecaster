# Live OOS Learning Curve Contract

## Training stages

| Complete V2 rows | State | Allowed output |
|---:|---|---|
| 0–29 | `ENGINEERING` | integrity, labels, receipt times, raw distributions |
| 30–95 | `EARLY_LEARNING` | simple distributions and feature stability |
| 96–199 | `PREVIEW` | regularized Market Preview; no effective action |
| 200+ | `INITIAL_SHADOW` | frozen Shadow Challengers; no promotion |

Each additional 50 complete rows creates a new frozen model version. The row
clock is independent of the version's longer OOS evaluation lifetime.
Twenty distinct trading days may be labelled `RESEARCH_CANDIDATE`; 60 days is
`HIGHER_CONFIDENCE`. Neither day count blocks early display or model fitting.

## OOS rule

A model version is scored only on Decisions after its `created_at` and
`training_cutoff`. Seed rows train the model but never appear on its Live OOS
curve. Every frozen version continues parallel Shadow evaluation through 60
distinct UTC days containing valid OOS scores. Unhealthy predictions and
invalid outcomes remain auditable but do not consume this evaluation lifetime.
Each version retains its training rows, subsequent OOS rows, effective UTC-day
blocks, distinct days, average quote-adjusted value, interval state, and error
diagnostics.

Identity-level curves select the newest version that existed at each Decision.
They never sum parallel versions from the same model identity. Paired
Full-minus-Market value applies the same latest-version rule to both sides.

The dashboard includes cumulative identity value, per-version OOS value,
paired Full-minus-Market value, interval width and calibration growth, error
and recommendation frequencies, and sample growth. Early curves explicitly
state that they do not prove profitability.

## Uncertainty

Training residual standard deviation is diagnostic only. It is never called a
95% lower confidence bound. Calibration uses only residuals appended after the
model was trained, grouped by UTC day. `FULL` is calibrated from its own OOS
residuals. Early values are `UNCALIBRATED` or `EARLY`; effective action remains
`WAIT`.

## Cost language

Long and Short outcomes use executable Bid/Ask quotes and therefore include
the observed quote spread. Until a broker commission version exists, the
metric is `quote-cost-adjusted return`, not after-cost or net PnL.
`commission_status=UNCONFIGURED` and
`slippage_status=UNAVAILABLE_SHADOW` are always displayed. Shadow mode does
not fabricate fills or slippage.
