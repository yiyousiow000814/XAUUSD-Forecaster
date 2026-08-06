# Phase 2F Forward-only Evidence Contract

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
- One decision event is appended for every clock boundary, including outages.
- Every model identity predicts from the same immutable snapshot.
- Champion-0 is `always-wait-v1` and its effective action is always `WAIT`.
- The primary outcome uses the first valid quote received strictly after the
  decision and within 20 seconds, then the first valid quote received at or
  after 30 minutes from that actual received entry.
- Long enters at Ask and exits at Bid. Short enters at Bid and exits at Ask.
- An outcome is appended; it never updates its decision or prediction.

## Point-in-time visibility

A market observation is visible only when its `received_time` is not later
than the decision. A news revision is visible only when its
`collector_first_seen_time` is not later than the decision. Publisher time is
descriptive metadata and never overrides collector visibility.

Visibility and economic freshness are separate clocks. Action-bearing news
must also have a recorded `source_published_time` at or after `FORWARD_EPOCH`,
not later than the decision, and no more than 72 hours before both first receipt
and the decision. Missing publisher timestamps, pre-epoch archive items, late
discoveries, and stale events remain visible for audit but cannot create a
current model impulse. Freshness decay is measured from publisher time, never
from parser completion or collector startup. Controlled category
`regulation_other` is display-only even when it comes from an official source.

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
Gemini 3.5 Flash-Lite is the primary annotator. Its last 150 daily local
requests remain reserved for monetary-policy, CPI, and payroll events. Gemini
3.1 Flash-Lite may annotate routine full-text items only after the 3.5 routine
budget reaches that reserve. Both model identities and quota ledgers remain
separate and are accepted by the same point-in-time training contract.

Display-number formatting is repaired deterministically against source
lexemes. Unsupported numbers are replaced by a nonnumeric disclosure and lower
display confidence instead of rejecting the structured receipt. If a Chinese
repair cannot pass validation, the display text becomes an explicit audit notice and
all directional impulses, novelty, and confidence become zero. Provider,
transport, or malformed-JSON failures append a `news_llm_failures` row before
retry. HTTP 429 and transient 5xx failures use bounded progressive backoff and
become terminal after five attempts. Terminal rows remain auditable and are
not automatically requeued. The primary Flash budget keeps 150 requests reserved
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
- Only the newest and immediately preceding version of each Ridge identity
  continue producing Shadow predictions. Older versions are archived without
  deleting their artifacts, predictions, scores, or receipts.
- Calibration follows the rolling model identity and selects the newest version
  that existed at each prior Decision. Unhealthy predictions and invalid
  outcomes cannot enter it. Twenty valid UTC-day blocks are required for
  `CALIBRATED` uncertainty.
- Training never changes the active Champion.
- Only the owner may append a manual promotion approval after forward gates.
- Unknown, missing, stale, or unhealthy data always produces effective
  `WAIT`; it never invents a replacement direction.
- Automatic training always evaluates Market-only eligibility. News-residual
  and Full artifacts require 30 news-exposed rows and 10 distinct clusters.
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

## Active free source boundary

The official News-residual path accepts source-qualified official full bodies.
The separate Broad News-residual path manages evidence at event level under
`news-event-evidence-v1`:

- `PRIMARY`: a complete annotated body from a configured first-party source;
- `CORROBORATED`: complete annotated bodies about the same event from at least
  two independent publishers on the reliable-domain list;
- `SINGLE_RELIABLE`: one reliable publisher, display-only;
- `DISCOVERY_ONLY`: unconfirmed discovery or aggregation source, display-only.

An event also needs at least one declared XAUUSD topic before it can enter Broad
features. Topics cover rates/Fed, inflation, employment, growth, USD/liquidity,
oil/energy, war/geopolitics, central-bank gold, and risk sentiment. Source
identity never grants media content model permission by itself. Event grouping,
evidence grade, permission, members, first-seen cutoff, and source hash are
deterministic and visible on the evidence dashboard.

Broad News-residual and Broad Full are independent Shadow identities. Their
learning curves are compared with the official Full identity; they never
replace it or gain order authority automatically. No FedWatch scraping,
historical news backfill, or consensus backfill is permitted.

## Explicit exclusions

This phase does not perform 2016-2026 Replay, historical news matching, deep
learning, reinforcement learning, real order submission, automatic Champion
promotion, or retroactive prediction regeneration.
