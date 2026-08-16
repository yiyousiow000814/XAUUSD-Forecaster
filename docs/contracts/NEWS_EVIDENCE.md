# News Evidence Contract

## Purpose

The evidence layer converts a wide news feed into reproducible event-level
inputs without granting every headline model authority. It preserves broad
awareness while keeping the training boundary point-in-time and auditable.

## Source intake

Direct first-party feeds and listing pages apply only objective pre-AI controls:
the Forward epoch, a 72-hour publication window, immutable item deduplication,
complete publisher text, and a fixed per-source fetch limit. They do not use
headline or body keywords to decide XAUUSD meaning. Complete bounded documents
proceed to semantic review, which may classify them as irrelevant or
background without granting model authority.

Chinese display text is Chinese-primary rather than Chinese-only. Natural
names, company names, tickers, identifiers, and common abbreviations MAY remain
in English when English improves readability. Display-language repair MUST NOT
rewrite an otherwise valid semantic category, direction, impact, evidence, or
confidence measurement. If readable Chinese-primary display text cannot be
produced, the annotation MUST be withheld from model permission and retried; it
MUST NOT be persisted as irrelevant or admitted behind a placeholder. A
semantic-schema or source-evidence failure MUST fail independently and MUST NOT
be disguised as a translation failure.
Language validation MUST distinguish ordinary untranslated prose from natural
English identifiers and names. A complete foreign-language clause or a script
other than Chinese and Latin requires repair; punctuation, numbers, symbols,
and natural English proper nouns do not by themselves make Chinese display
unreadable.

Supporting evidence MUST be an exact span of the immutable headline or body.
When an otherwise valid semantic response fails only this anchor check, a
bounded repair MAY ask the same annotation model to select one to three opaque
IDs from system-generated exact source spans. The repair MUST freeze every
semantic and display field, map selected IDs back to source text
deterministically, and re-run the complete semantic contract. Free-form repaired
evidence, unknown IDs, or a failed second validation remain a model-output
contract failure. The rejected output and repair stage remain bounded failure
evidence for diagnosis.

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
discovery lane; it does not decide semantic relevance or model permission. The
collector MUST NOT infer article meaning from title words or provider-specific
theme combinations. A `PAGE_PRECISEPUBTIMESTAMP` is retained when available.
Otherwise the GKG batch
timestamp is a conservative visibility clock, not an inferred event time.

GKG field bounds apply to archive metadata, not publisher article length. A
single oversized or malformed GKG row MUST be rejected independently and MUST
NOT abort the remaining archive. The collector retains at most ten immutable
row-rejection receipts per poll containing the archive name, row number, row
hash, reason code, and bounded size diagnostics; it MUST NOT retain the full
rejected row. Archive-wide rejection is reserved for manifest, digest, ZIP
layout, compressed-size, or expanded-size failures that make the batch itself
untrustworthy.

An immutable article that semantic review marks `IRRELEVANT` remains in the
local evidence ledger for audit, but MUST NOT be materialized into the public
news reader or its bounded D1 mirror. Missing or pending semantic review is not
equivalent to `IRRELEVANT`. The public reader MUST default to completed review
and present pending work and terminally isolated work in separate, explicitly
labelled review zones. These records remain inspectable for diagnosis, but MUST
NOT be mixed into the completed-news list.

During an annotation-contract handover, the public mirror MUST neutralize stale
operational annotation states from older contracts before bounded
current-contract replay begins. It MUST NOT hide or rewrite completed historical
reader rows while that replay is in progress. Completed classifications and
semantic-review counts remain visible; only old model-candidate flags and
unfinished operational states are neutralized. Historical local annotations and
failure receipts remain immutable audit evidence; their old operational status
MUST NOT be presented as the current backlog, current candidates, or current
isolation count. Runtime cutover MUST preserve every incremental mirror cursor;
losing a cursor MUST NOT masquerade as a new annotation-contract handover.

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

The persisted identity resolution is the sole authority for every resolved
current-contract event. Training, statistics, weighting, and storylines MUST
use its canonical episode and event identifiers and MUST NOT derive a competing
identity from free-form annotation keys. `UNRESOLVED` or missing current-contract
identity is display-only and MUST NOT receive model permission. A deterministic
fallback may organize legacy display records, but it is provisional presentation
state rather than canonical identity and cannot enter training or corroboration.

Candidate identity is never admitted by a shared topic or object alone. It
requires either the same normalized material/episode key or the same normalized
actor-and-object pair. Continuous market observations additionally require the
same observation interval and occurrence; a shared instrument, nearby value,
calendar month, direction word, or broad driver does not join separate price,
yield, index, or flow observations into one episode.

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

The same eligibility engine grants Core, Broad, or display-only permission.
Core accepts complete first-party evidence or one event corroborated by at
least two independently identified reliable publishers. Broad is a strict
superset: it also accepts single reliable and other identified publishers at
lower weights. Publisher identity, first-party status, reliability,
independent-source count, corroboration, and syndicated-duplicate count are
frozen attributes rather than publisher-name permission gates. Both lanes
share the same event identity, time validity, materiality, semantic-schema,
and point-in-time checks. The immutable V2 database tokens `OFFICIAL_MODEL`
and `OFFICIAL` encode the Core lane for historical schema compatibility; they
do not describe the active admission rule.

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

AI scheduling enforces project-scoped request and input-token windows for each
Gemini annotation model and across Gemma impact review and title translation.
Input size is obtained from the provider's token-count endpoint before
generation; a conservative byte bound fails closed when token counting is
unavailable. Keys belonging to one account share that account's budget; keys
belonging to independently configured accounts are metered independently. If the
primary Gemini annotation model has no safe capacity, the existing scheduler
may try the separately metered fallback annotation model; final event-identity
review remains Gemma-owned rather than silently changing classifier semantics.

Every live decision records semantic-pipeline health. A newly received
candidate gets one five-minute decision interval to finish its current-contract
annotation. An annotation that satisfies the current model-admission semantics
gets one interval from annotation completion to finish its current impact and
identity review. A current job in backoff or dead letter closes the applicable
gate immediately. Otherwise unresolved work closes it after that interval.
Only recent evidence still inside its configured actionable lifetime can close
the decision gate; historical archive and recovery backfill remain observable
without pausing current inference. A stale/missing annotator heartbeat or no
usable model credential also makes the pipeline unhealthy. Every news-dependent
model must then append `WAIT` with `NEWS_PIPELINE_UNHEALTHY`; `MARKET_ONLY`
remains observable as the control. Recovery requires the actual current-contract
backlog and runtime dependency failures to clear. A provider status page or
synthetic probe alone cannot reopen the gate.

The immutable publisher body and content hash always remain the audit source of
truth. Gemma receives that complete body whenever it fits the project token
window. For an oversized body, the existing full-body Gemini annotation anchors
one exact source window around every validated supporting-evidence excerpt;
Gemma receives those windows, the complete structured event claim, and the same
prior-event candidates. This is not presented as complete-body review. The
context mode and original character count are persisted with the identity
comparison, and a missing or non-verbatim evidence anchor fails closed rather
than falling back to arbitrary leading-text truncation.

Core News-residual learns the residual after cross-fitted Market-only
predictions from the narrower evidence lane. Core Full equals the same frozen
Market-only prediction plus that residual. Broad News-residual uses the Core
features plus wider event-evidence attributes; Broad Full adds that residual
to the same Market-only prediction. All versions are Shadow-only, run only
after creation, and require manual owner approval for any future promotion.

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
read only the latest activated generation. An evidence-empty Core lane is
represented by an explicit zero-effect cold-start artifact, never by fabricated
rows or a partial generation. Once a generation has been activated, every
healthy decision must publish the complete model identity set or fail visibly.
