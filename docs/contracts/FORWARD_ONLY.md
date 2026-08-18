# Forward-only Evidence Contract

## Authority and epoch

This contract replaces historical Replay as the active development route.
The runtime creates `COLLECTION_EPOCH` exactly once, using the UTC time at which
the real collector first initializes its append-only database. The value is
stored in immutable runtime metadata and a local epoch receipt. It is never
backdated. Evidence earlier than that value is ineligible for training and
performance reporting. Phase 2F also freezes `EVALUATION_EPOCH_V2`; predictions
at or after that epoch are the only source of `LIVE_OOS` scores. Existing
records remain `LEGACY_ENGINEERING`; deterministic append-only repair products
are `REPAIRED_SEED` and may train a model but never count as that model's OOS.

Historical XAUUSD observations may be read only to initialize frozen U5 state.
They must carry `data_role=WARMUP_ONLY`; they cannot create decisions,
outcomes, training rows, performance rows, or news matches.

## Clock and outcome

- Decision times are UTC boundaries divisible by five minutes.
- One decision event is appended for every live clock boundary, including an
  unexpected current outage. Expected weekly-closure grids and historical
  grids missed while the process was stopped are not reconstructed as if they
  had been live predictions.
- Every model identity predicts from the same immutable snapshot.
- Champion-0 is `always-wait-v1` and its effective action is always `WAIT`.
- The primary outcome uses the first valid quote received strictly after the
  decision and within 20 seconds, then the first valid quote received at or
  after 30 minutes from that actual received entry.
- Long enters at Ask and exits at Bid. Short enters at Bid and exits at Ask.
- An outcome is appended; it never updates its decision or prediction.

## Weekly market closure

- News collection and annotation continue during the expected weekly XAUUSD
  closure. Their immutable first-seen clocks continue advancing normally.
- A decision grid is eligible only when its complete fixed 30-minute horizon
  ends before the expected Friday close. Grids from Friday 16:30 New York time
  onward are skipped even when the entry quote is fresh, because an executable
  30-minute exit cannot be guaranteed.
- A scheduled closed grid without a fresh point-in-time Bid/Ask does not create
  a market snapshot, prediction, outcome, or training row.
- A genuinely received fresh quote takes precedence over the closure clock.
- The operational closure clock follows Friday 17:00 through Sunday 18:00 in
  `America/New_York`, so daylight-saving transitions do not require a fixed UTC
  offset. Broker quotes remain authoritative when they are actually present.
- After restart, missed grids without their own point-in-time quote are skipped;
  the runtime never manufactures retrospective predictions to fill a gap.
- Collection resumes on the first live grid with a fresh quote. Weekend news is
  eligible only under the ordinary publication-time, first-seen, parsing,
  collection-latency, and category-specific freshness rules. There is no Monday
  batch training of empty weekend market grids.

## Point-in-time visibility

A market observation is visible only when its `received_time` is not later
than the decision. A news revision is visible only when its
`collector_first_seen_time` is not later than the decision. Publisher time is
descriptive metadata and never overrides collector visibility.

Visibility, collection latency, and economic freshness are separate clocks.
Action-bearing news must also have a recorded `source_published_time` at or
after `FORWARD_EPOCH`, not later than the decision, and must first be collected
within 60 minutes of publication. The maximum economic age is frozen by
controlled category: 24 hours for inflation/employment and risk sentiment,
36 hours for war/geopolitics, 48 hours for growth, USD/liquidity and oil/energy,
72 hours for rates/Fed, and seven days for central-bank gold. Missing publisher
timestamps, pre-epoch archive items, discoveries delayed by more than 60
minutes, and stale events remain visible for audit but cannot create a current
model impulse. Pre-epoch rows are not sent to semantic model queues. Discovery
delay and publication age do not prevent a readable current revision from
receiving semantic classification: the impact contract decides economic
actionability and lifetime after classification, and the trading contract
separately enforces decision-time visibility. A row with a missing publisher
timestamp may be summarized for display, but it remains ineligible for
training. Freshness decay is measured from publisher time, never from parser
completion or collector startup. Controlled category `regulation_other` is
display-only even when it comes from an official source.

Revised news is a new row with a larger revision number and a new content
hash. Existing revisions are never updated. An annotation is usable only when
all of the following hold:

```text
annotation.parsed_at <= decision_time
annotation.raw_content_hash == visible_news_revision.content_hash
annotation.prompt_version and llm_model_version are recorded
```

An RSS headline or one-line description is not semantic evidence. For Federal
Reserve releases, the collector must append a new revision containing the
official HTML body, accessible tables, or extracted PDF text before Gemini may
annotate it. The active annotation pipeline rejects source bodies shorter than
240 characters and only uses an annotation that matches the latest revision
visible at decision time. Headline-only rows remain auditable but cannot enter
model features.

The LLM emits only the frozen structured-news schema. It has no authority to
choose `LONG`, `SHORT`, model promotion, or training eligibility.
The active Gemini prompt receives the complete stored source body without an
application-level character slice and appends a concise Simplified-Chinese
summary together with the structured impulse fields. A new prompt version is a
new immutable annotation; it never rewrites an earlier interpretation.

The active annotation contract is
`news-json-v16-xauusd-transmission-evidence`. V16 retains the explicit
semantic relevance, review priority, material-change, time-sensitivity,
reason, and source-evidence fields introduced by V15, while requiring a narrow
XAUUSD transmission test. Company, product, local, and non-US macro stories do
not qualify without an explicit current bullion, USD, US Treasury-yield, US
monetary-policy, or major geopolitical transmission. CONTEXT_ONLY is not a
fallback for otherwise irrelevant content. Evidence must resolve to a unique
contiguous excerpt in the stored headline or body; punctuation-only model drift
may be restored deterministically to the original source characters, while
changed words, numbers, ordering, or joined clauses fail closed. Casing,
spelling, one keyword, or publisher identity cannot determine meaning. V15
annotations remain immutable historical evidence but cannot be generated or
used by the active feature, storyline, training, or inference paths.

Gemini 3.5 Flash-Lite is the primary annotator. Its last 150 daily local
requests remain reserved for monetary-policy, CPI, and payroll events. Gemini
3.1 Flash-Lite may annotate routine full-text items only after the 3.5 routine
budget reaches that reserve. Both model identities and quota ledgers remain
separate and are accepted by the same point-in-time training contract.

Broad training accepts only the current material-event schema. The canonical
event must be a `FACT_EVENT`, `OFFICIAL_CLAIM`, or `MARKET_REACTION`; its
evidence role must be `CORE_CLAIM`, `EVIDENCE_DOCUMENT`, or `MARKET_REACTION`;
and materiality must be at least 0.50. Commentary, forecasts, low-materiality
items, legacy annotation schemas, and unsupported source domains remain
display-only. Syndicated headlines sharing one `material_event_key` form one
event and never create multiple votes. A primary-source grade is based on the
source organization, not the number of feeds or documents carrying it.
Every model-eligible event also requires a precise, live-known event clock.
An explicit timestamp extracted from the body is preferred. A precise
publisher timestamp may serve as `OFFICIAL_RELEASE_TIME` only when the official
publication is itself the event. Date-only, missing, future, and media-derived
substitute clocks fail closed for training.

Display-number formatting is repaired deterministically against source
lexemes. A rejected display response gets one bounded, feedback-guided repair:
the repair request includes the prior rejection reason and rejected display
fields, freezes semantic and already-valid display fields, and may return only
the rejected fields. The repair model may copy exact source-number spellings or
remove the unsupported numeric claim; it may never convert units or magnitudes.
An ambiguous or unsupported number remains a validation failure and is never
replaced by manufactured prose. Validated semantics are checkpointed before a
failed display attempt is released. Later attempts resume only the rejected
display fields, carry the latest validation reason, and may use the declared
fallback display route; they never repeat semantic analysis. Until the display
passes, no annotation is persisted and no model permission is granted. Display
repair remains nonterminal and uses bounded retry intervals. Provider,
transport, malformed-JSON, and model-output contract failures append a
`news_llm_failures` row before retry. A rejected structured response also
appends bounded diagnostic evidence: its failure stage and code, response hash,
validation cause, and only the selected output fields needed to reproduce the
failure. When structured fields cannot be decoded, only a 500-character output
prefix and the complete response hash may replace selected fields. Full rejected
responses, source bodies, prompts, and credentials MUST NOT be duplicated into
the failure evidence table. Model-output contract
failures retry once after five minutes and become terminal when the same failure
repeats, except checkpointed display repair, which remains retryable because it
cannot change semantic measurements or grant model permission. Each retry
preserves bounded failure evidence, and a later versioned recovery may authorize
one new attempt after another repair mechanism changes.

Chinese-facing annotation fields share one source-grounded allowed-Latin-span
contract. The validator MAY exclude only a bounded span that occurs in the
immutable source headline/body and has deterministic referential proof: an
exact declared identity, strong identifier shape, a bounded reference delimited
in both display and source, or a conservative established source-reference
context. It MUST mask only the proven span before applying the unchanged
Chinese-primary ratio to the remaining text. Source occurrence, casing, or
display punctuation alone is not permission: copied English prose, long clauses,
invented references, and bracket-wrapped sentences remain invalid. Existing
display checkpoints MUST be revalidated locally against the current
deterministic rules before another provider request is attempted.

The model gateway distinguishes a request that produced no trustworthy response
from a response that failed decoding or validation. Capacity, provider pacing,
HTTP, URL, connection, and timeout failures retain request/transport failure
codes across display repair and MUST NOT become `MODEL_OUTPUT_CONTRACT_FAILED`.
Only a returned response rejected by schema, semantic, or display validation may
enter the model-output failure family.
Waiting six hours would outlive the decision value of timely news.
HTTP 429 and transient 5xx failures use bounded progressive backoff and become
terminal after five attempts. Terminal rows remain auditable and are not
automatically requeued. The primary Flash budget keeps 150 requests reserved
for monetary-policy, CPI, and payroll events; routine news cannot consume that
reserve. Exhausting the current minute's local request slots leaves the item
pending for the next batch and does not create a failure record.

## Prequential learning order

For event time `t`, the immutable order is:

```text
snapshot -> visible news/annotations -> Champion and Challenger predictions
-> append decision and predictions -> wait for outcome
-> append 30-minute outcome -> score old predictions
-> append training eligibility
```

Training code must reject any row whose outcome is missing, whose decision is
before `FORWARD_EPOCH`, whose data role is not `FORWARD`, or whose outcome was
not appended before the model training cutoff. The training dataset hash,
cutoff, feature version, prompt version, hyperparameters, and artifact hash
are immutable model-update fields.

## Models and promotion

- Champion-0: Always Wait.
- Challenger-A: strongly regularized Market-only Ridge.
- Challenger-B: News residual Ridge.
- Full estimate equals Market estimate plus News residual estimate.
- U5 is a scale and reporting unit only; it cannot vote on direction.
- A new Challenger is trained after each 50 additional eligible rows for the
  same model stage.
- The collector trains a non-actionable Market Preview at 96 V2-eligible rows,
  the first Shadow Challenger set at 200 rows, then a new version after each 50
  new eligible rows. Sixty trading days is a confidence milestone, not a
  training blocker. A failed training attempt is logged and cannot alter a
  prior artifact.
- Five Ridge identities form one immutable generation with one training cutoff,
  policy version, event-snapshot hash, and `generation_id`. Training writes all
  five artifacts before one activation record atomically replaces the active
  generation. Only the active generation produces future Shadow predictions.
  Archived generations retain artifacts, predictions, scores, and receipts.
- Calibration follows the rolling model identity and selects the newest version
  that existed at each prior Decision. Unhealthy predictions and invalid
  outcomes cannot enter it. Twenty valid UTC-day blocks are required for
  `CALIBRATED` uncertainty.
- Training never changes the active Champion.
- Only the owner may append a manual promotion approval after forward gates.
- Unknown, missing, stale, or unhealthy data always produces effective
  `WAIT`; it never invents a replacement direction.
- Automatic generation training requires 30 event-exposed rows in the Broad
  lane. If the Official lane has fewer than 30 rows, its residual is an explicit
  zero-effect cold-start artifact; `Full` therefore remains Market-only until a
  later complete generation has enough Official evidence. This keeps all model
  identities present without fabricating an Official news signal. One event and
  one event day permit an explicitly experimental Broad Shadow generation; 10
  events and three event days establish the standard evidence state.
  Artifacts trained from one or two distinct event days are explicitly labelled
  `EXPERIMENTAL_SINGLE_DAY` or `EXPERIMENTAL_TWO_DAY`; three distinct event days
  establish the standard news evidence state. All remain Shadow Challengers.

## Versioned prequential Shadow evaluation

- Every frozen model version predicts only future decisions created after its
  own `created_at`. Repaired Seed and training rows are excluded from its
  learning curve. Old predictions and scorecards are never rewritten.
- A simulated Long or Short is admitted at decision time, before its outcome is
  visible. Each model version owns an independent Shadow portfolio with at most
  one position, a 20-second entry expiry, and a fixed 30-minute holding period.
- Predictions blocked by an existing simulated position remain append-only
  `OVERLAP_BLOCK` evidence. Waits and unhealthy predictions are recorded but do
  not create a simulated position.
- Executable Bid/Ask labels include spread. Commission is `UNCONFIGURED` and
  Shadow slippage is `UNAVAILABLE_SHADOW`; neither is silently treated as zero,
  and the dashboard must call results quote-cost-adjusted rather than net.
- U5 controls only the reporting scale: one U5 of simulated PnL maps to one
  percent of virtual equity. It does not change direction or admission.
- PF, MaxDD, Sharpe, action frequency, calibration, and cumulative value are
  reported per immutable version from subsequent `LIVE_OOS` rows only. Full
  minus Market uses paired same-clock rows. Early values are descriptive and
  remain exposed to market-regime differences. These metrics never authorize a
  real order or automatic Champion promotion.
- Identity-level and paired aggregate curves select only the newest eligible
  version at each Decision, so parallel version evaluation cannot duplicate
  returns.
- Champion-0 is reported separately as a zero-return safety baseline. It is not
  a trained model and does not occupy a Ridge version slot.

## Storage and separation

SQLite contains immutable metadata, minute/snapshot evidence, news revisions,
annotations, decision events, per-model predictions, outcomes, scores, and
model-update records. Raw high-frequency quotes belong in compressed daily
files outside the Prediction Ledger. Historical warm-up state and forward
evidence use separate roles and cannot share training queries.
Raw news revisions are append-only. Maintenance appends visibility
classifications such as `CONTENT_UNAVAILABLE`, `DUPLICATE_DOCUMENT`, and
`ARCHIVAL_ONLY`; it does not delete or rewrite raw evidence.

## Active free source boundary

The official News-residual path accepts objectively qualified official full bodies.
Official and Broad news paths share one event snapshot and event-clock policy.
The Broad permission uses these evidence grades:

- `PRIMARY`: a complete annotated body from a configured first-party source;
- `CORROBORATED`: complete annotated bodies about the same event from at least
  two independently identified publishers;
- `SINGLE_RELIABLE`: one publisher on the reliability registry;
- `SINGLE_SOURCE`: one identified publisher outside that registry;
- `DISCOVERY_ONLY`: no publisher identity can be verified, display-only.

An event also needs at least one declared XAUUSD topic before it can enter Broad
features. Topics cover rates/Fed, inflation, employment, growth, USD/liquidity,
oil/energy, war/geopolitics, central-bank gold, and risk sentiment. Source
identity never grants media content model permission by itself. Officiality,
reliability, independent-source count, corroboration, and syndication are model
features instead of source bans. Event grouping,
evidence grade, permission, members, first-seen cutoff, and source hash are
deterministic and visible on the evidence dashboard.

Each event receives one total training budget within a generation. Its
five-minute decision exposures divide that budget according to the frozen
freshness and evidence weight. Ridge fits use the resulting `sample_weight`, so
repeated visibility preserves continuous estimation without turning one event
into many independent votes. Events attributed to the same canonical reporting
organization also share one bounded source budget. Official 30-minute evaluation uses fixed,
non-overlapping `:00` and `:30` decisions; five-minute results remain a clearly
labelled overlapping diagnostic.

Broad News-residual and Broad Full are independent Shadow identities. Their
learning curves are compared with the official Full identity; they never
replace it or gain order authority automatically. No FedWatch scraping,
historical news backfill, or consensus backfill is permitted.

## Explicit exclusions

This phase does not perform 2016-2026 Replay, historical news matching, deep
learning, reinforcement learning, real order submission, automatic Champion
promotion, or retroactive prediction regeneration.
