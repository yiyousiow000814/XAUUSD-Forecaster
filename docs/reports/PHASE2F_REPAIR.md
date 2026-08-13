# Phase 2F Repair Report

## Immutable migration receipt

The append-only migration ran on 05-08-2026. It did not reset the collection
epoch, delete rows, update legacy evidence, or regenerate historical
predictions.

| Field | Receipt |
|---|---|
| Collection epoch | `2026-08-05T05:14:34.915197+00:00` |
| Evaluation epoch V2 | `2026-08-05T16:21:00.259891+00:00` |
| Repair batch | `823f2538-155b-506d-ac3c-da8faa46d511` |
| Backup SHA-256 | `a36abedaca23439d2ed69bd4f0c265400ac5ecb6630152f8396294a2a55d968e` |
| Source evidence SHA-256 | `40a66b84d98b4d970c7b063c4996ab9dcfb731cb32093880279ac827302dff2a` |
| Derived output SHA-256 | `ddee5f22beabe53b7dd36c525fbd43475e635e9b469f5daa8d7f3153eee119b2` |
| Legacy hash before and after | `74383373a917b59557d5ca852a23e79ebc842af2601b512f1d07221ab8462d4e` |
| SQLite integrity | `ok` |

The local online backup and machine-readable receipt remain under the ignored
`.local/forward` evidence tree. They are operational evidence, not source
files.

## Repair result

- 134 decisions received independently derived Market, News, and executable
  outcome rows in `REPAIRED_SEED`.
- 111 rows passed V2 training eligibility.
- 23 rows remained explicitly unrepaired. Reasons may overlap: 17 incomplete
  market features, five missing market snapshots, five entries outside expiry,
  six missing terminal quotes, and 11 other unrecoverable rows.
- U5 was reconstructed with `finite-memory-u5-v5-contiguous-m1`; stored legacy
  U5 values were not trusted as repair inputs.
- The first Market Preview used 111 Repaired Seed rows. News-residual training
  was correctly withheld because the repair cut contained only one distinct
  news event day.

## Interpretation boundary

Repaired Seed can initialize a Challenger but cannot score it. Learning curves
start only with predictions appended after the model's creation and after
`EVALUATION_EPOCH_V2`. Current quote returns include executable Bid/Ask spread;
commission is unconfigured and Shadow slippage is unavailable. No net-PnL,
promotion, confidence, or trading claim follows from this repair.
