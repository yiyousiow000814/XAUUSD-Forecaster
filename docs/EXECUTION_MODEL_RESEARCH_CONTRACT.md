# Execution Model Research Contract

## Separation

Direction, exposure, and exit are separate research questions. No execution model may alter the frozen 30-minute direction ledger or authorize an order.

## Exposure Ridge

The candidate actions are `0.5x`, `1.0x`, and `2.0x` relative exposure, not broker-specific lot sizes. Training is prohibited until account equity, contract value, margin, commission, slippage, and a frozen risk utility are configured. Without those fields, return scales mechanically with size and a model would learn to choose the largest multiplier whenever expected return is positive.

## Exit Ridge

The candidate actions are `EXIT` and `HOLD`. Training is prohibited until point-in-time executable outcomes exist at fixed 5-minute checkpoints inside the 30-minute horizon. The label must include the value of continuing from each checkpoint and must not use a later path value at the checkpoint decision.

## Required gates

1. Append predictions before outcomes.
2. Keep all models Shadow-only.
3. Train only after the relevant executable checkpoint outcome matures.
4. Score direction, exposure, and exit separately before evaluating a combined policy.
5. Preserve the fixed 30-minute direction benchmark as the comparison control.
