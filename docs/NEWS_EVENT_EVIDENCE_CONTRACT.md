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
proceed to the v15 semantic review, which may classify them as irrelevant or
background without granting model authority.

Collector lanes are not independent publishers. Google News and GDELT are
discovery mechanisms; source trust and generation budgets use the first-party
collector identity or the normalized reporting organization. A successful
transport poll with no recent complete document is reported separately from a
healthy evidence-producing source. A current candidate whose publisher body
cannot be fetched is degraded, not successful.

GDELT discovery reads the official 15-minute GKG update archive rather than the
rate-limited DOC API. The collector verifies the manifest size and MD5 digest,
bounds compressed and expanded payloads, and selects at most 25 gold-related
GKG candidates before retrieving publisher text. Gold metadata only scopes the
discovery lane; it does not decide semantic relevance or model permission. A
`PAGE_PRECISEPUBTIMESTAMP` is retained when available. Otherwise the GKG batch
timestamp is a conservative visibility clock, not an inferred event time.

## Event construction

Only complete stored bodies with a matching immutable Gemini annotation are
considered. Both the revision first-seen time and annotation parsed time must be
at or before the decision cutoff. The stable `event_id` uses the material event
key or normalized actor, action, object, location, topic, and entity identity;
calendar date is not part of that identity. Annotation or canonical-document
changes create a new immutable `event_version_id` under the same event.

Identity reconciliation compares symmetric, bounded claim snapshots from the
current document and prior candidates. `SAME_EVENT` requires strict equivalence
of core verifiable facts; publisher, language, headline, wording, and incidental
context do not create an event. A changed core measurement, state, decision,
action, scope, effective time, result, or revision remains in the same episode
but receives a distinct event identity. A distinct occurrence anchor starts a
new episode. Insufficient evidence remains unresolved. The stable anchor,
factual changes, identity differences, and contextual differences are retained
as immutable audit evidence with the resolution.

Training requires a precise event timestamp known at the decision cutoff.
Explicit body time is preferred. Official primary releases may use their
precise publication timestamp because publication is the event. An identified
publisher's structured timestamp is an auditable fallback and its reliability
remains a model feature. Missing, date-only, and future clocks remain
display-only.

The current actionable topics are rates/Fed, inflation, employment,
growth/economy, USD/liquidity, oil/energy, war/geopolitics, central-bank gold,
and risk sentiment. The topic mapper is deterministic and versioned.

## Evidence grades

1. `PRIMARY`: configured first-party complete content.
2. `CORROBORATED`: at least two independently identified publishers report the
   same event with complete annotated content.
3. `SINGLE_RELIABLE`: one reliable publisher reports the event.
4. `SINGLE_SOURCE`: one identified publisher outside the reliability registry
   reports the event.
5. `DISCOVERY_ONLY`: no publisher identity can be verified from the item.

The same eligibility engine grants `OFFICIAL_MODEL`, `BROAD_MODEL`, or
`DISPLAY_ONLY` permission. `OFFICIAL_MODEL` additionally requires a configured
official source. `BROAD_MODEL` accepts all four identified-publisher grades;
official status, reliability, independent-source count, corroboration, and
syndicated-duplicate count are frozen numeric attributes rather than source
permission gates. Both permissions share the same event
identity, time validity, materiality, semantic-schema, and point-in-time checks.

Official EIA and BEA observations are converted into point-in-time release
packets. Each packet carries the current value, previous-period value, previous
visible revision, revision delta, nullable market expectation, release time
when supplied by an authoritative source, collector first-seen time, series
definition, and relation to the prior packet. Missing expectations remain null;
the semantic model may not invent them. EIA and BEA receive separate features
even when another series describes a similar economic signal, so Ridge OOS
evidence, not a collector rule, determines whether either coefficient is useful.

## Model separation

Collection is permission-neutral. A news document or macro observation that
passes the objective intake checks is retained as a Forward candidate; the
collector never assigns a model role. Model permission belongs to the versioned
generation contract and may change only through a complete verified handover.

Gemma scheduling enforces project-scoped request and input-token windows across
impact review and title translation. Input size is obtained from the provider's
token-count endpoint before generation; a conservative byte bound fails closed
when token counting is unavailable. Key rotation never multiplies the shared
project budget.

The official News-residual and Full models remain an independent baseline.
Broad News-residual learns the residual after cross-fitted Market-only
predictions, using official news features plus event-evidence features. Broad
Full equals the same frozen Market-only prediction plus the Broad news
residual. All versions are Shadow-only, run only after creation, and require
manual owner approval for any future promotion.

Every event has one total weight budget per generation. Repeated five-minute
exposures split that budget using their frozen freshness weights. The news
residual Ridge consumes these values as `sample_weight`; repeated visibility
does not create additional event votes. Canonical reporting organizations also
receive one bounded source budget, so several events from one publisher cannot
dominate several independent sources merely through volume.

One complete generation contains Market-only, News residual, Full, Broad News
residual, and Broad Full. All five share one cutoff, policy version, event
snapshot hash, and generation identifier. Activation is a single append-only
record written only after every artifact and member is valid. Future decisions
read only the latest activated generation. An evidence-empty Official lane is
represented by an explicit zero-effect cold-start artifact, never by fabricated
rows or a partial generation. Once a generation has been activated, every
healthy decision must publish the complete model identity set or fail visibly.
