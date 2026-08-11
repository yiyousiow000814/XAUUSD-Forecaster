# News Event Evidence Contract

## Purpose

The evidence layer converts a wide news feed into reproducible event-level
inputs without granting every headline model authority. It preserves broad
awareness while keeping the training boundary point-in-time and auditable.

## Source intake

Direct official feeds and listing pages apply only objective pre-AI controls:
the Forward epoch, a 72-hour publication window, immutable item deduplication,
complete publisher text, and a fixed per-source fetch limit. They do not use
headline or body keywords to decide XAUUSD meaning. Complete bounded documents
proceed to semantic review, which may classify them as irrelevant or background
without granting model authority.

Collector lanes are not independent publishers. Google News and GDELT are
discovery mechanisms; source trust uses the first-party collector identity or
the normalized reporting organization. A successful transport poll with no
recent complete document is reported separately from a healthy
evidence-producing source. A current candidate whose publisher body cannot be
fetched is degraded, not successful.

## Event construction

Only complete stored bodies with a matching immutable Gemini annotation are
considered. Both the revision first-seen time and annotation parsed time must be
at or before the decision cutoff. The stable `event_id` uses the material event
key or normalized actor, action, object, location, topic, and entity identity;
calendar date is not part of that identity. Annotation or canonical-document
changes create a new immutable `event_version_id` under the same event.

Training requires a precise event timestamp known at the decision cutoff.
Explicit body time is preferred. Official primary releases may use their
precise publication timestamp because publication is the event. Missing,
date-only, future, and media-publication substitute clocks remain display-only.

The current actionable topics are rates/Fed, inflation, employment,
growth/economy, USD/liquidity, oil/energy, war/geopolitics, central-bank gold,
and risk sentiment. The topic mapper is deterministic and versioned.

## Evidence grades

1. `PRIMARY`: configured first-party complete content.
2. `CORROBORATED`: at least two independent reliable publisher domains report
   the same event with complete annotated content.
3. `SINGLE_RELIABLE`: one reliable publisher reports the event.
4. `DISCOVERY_ONLY`: an aggregation or unconfirmed source provides the item.

The same eligibility engine grants `OFFICIAL_MODEL`, `BROAD_MODEL`, or
`DISPLAY_ONLY` permission. `OFFICIAL_MODEL` additionally requires a configured
official source. `BROAD_MODEL` accepts qualified `PRIMARY` and `CORROBORATED`
events. Both permissions share the same event identity, time validity,
materiality, semantic-schema, and point-in-time checks.

## Model separation

The official News-residual and Full models remain an independent baseline.
Broad News-residual learns the residual after cross-fitted Market-only
predictions, using official news features plus event-evidence features. Broad
Full equals the same frozen Market-only prediction plus the Broad news
residual. All versions are Shadow-only, run only after creation, and require
manual owner approval for any future promotion.

Every event has one total weight budget per generation. Repeated five-minute
exposures split that budget using their frozen freshness weights. The news
residual Ridge consumes these values as `sample_weight`; repeated visibility
does not create additional event votes.

One complete generation contains Market-only, News residual, Full, Broad News
residual, and Broad Full. All five share one cutoff, policy version, event
snapshot hash, and generation identifier. Activation is a single append-only
record written only after every artifact and member is valid. Future decisions
read only the latest activated generation.
