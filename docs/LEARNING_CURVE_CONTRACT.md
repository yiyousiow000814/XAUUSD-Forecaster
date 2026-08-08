# Live OOS Learning Curve Contract

## Training stages

| Complete V2 rows | State | Allowed output |
|---:|---|---|
| 0–29 | `ENGINEERING` | integrity, labels, receipt times, raw distributions |
| 30–95 | `EARLY_LEARNING` | simple distributions and feature stability |
| 96–199 | `PREVIEW` | complete five-model Preview when event evidence is sufficient; no effective action |
| 200+ | `INITIAL_SHADOW` | complete frozen five-model Shadow generation; no promotion |

Each additional 50 complete rows creates a new frozen model version. The row
clock is independent of the rolling process's longer OOS evaluation lifetime.
The rolling process may be labelled `RESEARCH_CANDIDATE` after 20 distinct
trading days and `HIGHER_CONFIDENCE` after 60 days. Neither day count blocks
early display or model fitting.

## OOS rule

A model version is scored only on Decisions after its `created_at` and
`training_cutoff`. Seed rows train the model but never appear on its Live OOS
curve. One activated generation supplies exactly one version of each Ridge
identity for future Shadow predictions. A complete new generation atomically
replaces it. Older artifacts, predictions, and scores remain immutable and
become `ARCHIVED`.

Long-horizon calibration belongs to the rolling model identity. At each prior
Decision it uses only the newest version that existed then, grouped into UTC-day
blocks. Unhealthy predictions and invalid outcomes remain auditable but cannot
enter calibration. Each version retains its own training rows, subsequent OOS
rows, value, and error diagnostics for adjacent-version comparison.

Identity-level curves select the newest version that existed at each Decision.
They never sum parallel versions from the same model identity. Paired
Full-minus-Market value applies the same latest-version rule to both sides.

`CHAMPION_0` is shown separately as the zero-return safety baseline. It is not a
trained model and does not occupy a generation member slot.

The dashboard includes cumulative identity value, per-version OOS value,
paired Full-minus-Market value, interval width and calibration growth, error
and recommendation frequencies, and sample growth. Early curves explicitly
state that they do not prove profitability.

## Uncertainty

Training residual standard deviation is diagnostic only. It is never called a
95% lower confidence bound. Calibration uses rolling-identity residuals from
prior Decisions, grouped by UTC day. Each Decision contributes only its newest
eligible version. `FULL` is calibrated from the `FULL` lineage only. Early
values are `UNCALIBRATED` or `EARLY`; effective action remains `WAIT`.

## Cost language

Long and Short outcomes use executable Bid/Ask quotes and therefore include
the observed quote spread. Until a broker commission version exists, the
metric is `quote-cost-adjusted return`, not after-cost or net PnL.
`commission_status=UNCONFIGURED` and
`slippage_status=UNAVAILABLE_SHADOW` are always displayed. Shadow mode does
not fabricate fills or slippage.
