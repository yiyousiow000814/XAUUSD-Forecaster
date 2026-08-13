# Execution Model Research Protocol

This protocol defines how exposure and exit candidates are evaluated. It does
not grant runtime or order authority. The governing evidence invariants remain
in [`FORWARD_ONLY.md`](../contracts/FORWARD_ONLY.md).

## Separation

Direction, exposure, and exit are separate research questions. No execution model may alter the frozen 30-minute direction ledger or authorize an order.

## Exposure Ridge

The candidate actions are `0.5x`, `1.0x`, and `2.0x` relative Shadow exposure, not broker-specific lot sizes. Each matured decision supplies separate LONG and SHORT counterfactual examples. The frozen target maximizes:

```text
size * executable_return_u5 - 0.5 * (size * absolute_adverse_excursion_u5)^2
```

The quadratic adverse-path term prevents positive-return rows from mechanically selecting the largest multiplier. Commission, slippage, account equity, contract value, and margin remain unconfigured, so this model must be described as a relative exposure experiment and never as a live lot recommendation.

## Exit Ridge

The candidate actions are `EXIT` and `HOLD` at 5, 10, 15, 20, and 25 minutes after the executable entry receipt. Features contain only the original decision snapshot and the quote path received by that checkpoint. The continuous target is:

```text
30-minute executable return - checkpoint executable return
```

Positive predicted continuation value maps to `HOLD`; zero or negative value maps to `EXIT`. Retained forward quotes may reconstruct training labels after the complete 30-minute path matures, but they may not backfill a prediction or an OOS score.

## Lifecycle

Both models begin after their minimum complete training sets exist. A new frozen version is created for every additional 50 examples. Predictions are appended before their respective outcome is known, and only those forward predictions may receive an OOS score.

## Required gates

1. Append predictions before outcomes.
2. Keep all models Shadow-only.
3. Train only after the relevant executable checkpoint outcome matures.
4. Score direction, exposure, and exit separately before evaluating a combined policy.
5. Preserve the fixed 30-minute direction benchmark as the comparison control.
