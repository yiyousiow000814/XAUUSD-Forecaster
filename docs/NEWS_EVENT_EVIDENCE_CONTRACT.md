# News Event Evidence Contract

## Purpose

The evidence layer converts a wide news feed into reproducible event-level
inputs without granting every headline model authority. It preserves broad
awareness while keeping the training boundary point-in-time and auditable.

## Event construction

Only complete stored bodies with a matching immutable Gemini annotation are
considered. Both the revision first-seen time and annotation parsed time must be
at or before the decision cutoff. The event key uses the UTC first-seen date,
normalized topic, entities when available, and normalized headline tokens.

The current actionable topics are rates/Fed, inflation, employment,
growth/economy, USD/liquidity, oil/energy, war/geopolitics, central-bank gold,
and risk sentiment. The topic mapper is deterministic and versioned.

## Evidence grades

1. `PRIMARY`: configured first-party complete content.
2. `CORROBORATED`: at least two independent reliable publisher domains report
   the same event with complete annotated content.
3. `SINGLE_RELIABLE`: one reliable publisher reports the event.
4. `DISCOVERY_ONLY`: an aggregation or unconfirmed source provides the item.

Only `PRIMARY` and `CORROBORATED` events with an actionable topic receive
`BROAD_MODEL` permission. Other events stay visible as `DISPLAY_ONLY` and
cannot affect Broad training or inference.

## Model separation

The official News-residual and Full models remain an independent baseline.
Broad News-residual learns the residual after cross-fitted Market-only
predictions, using official news features plus event-evidence features. Broad
Full equals the same frozen Market-only prediction plus the Broad news
residual. All versions are Shadow-only, run only after creation, and require
manual owner approval for any future promotion.
