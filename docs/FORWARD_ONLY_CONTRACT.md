# Phase 2F Forward-only Evidence Contract

## Authority and epoch

This contract replaces historical Replay as the active development route.
The runtime creates `FORWARD_EPOCH` exactly once, using the UTC time at which
the real collector first initializes its append-only database. The value is
stored in immutable runtime metadata and a local epoch receipt. It is never
backdated. Evidence earlier than that value is ineligible for training and
performance reporting.

Historical XAUUSD observations may be read only to initialize frozen U5 state.
They must carry `data_role=WARMUP_ONLY`; they cannot create decisions,
outcomes, training rows, performance rows, or news matches.

## Clock and outcome

- Decision times are UTC boundaries divisible by five minutes.
- One decision event is appended for every clock boundary, including outages.
- Every model identity predicts from the same immutable snapshot.
- Champion-0 is `always-wait-v1` and its effective action is always `WAIT`.
- The primary outcome uses the first valid quote strictly after the decision,
  then the first valid quote at or after 30 minutes from that actual entry.
- Long enters at Ask and exits at Bid. Short enters at Bid and exits at Ask.
- An outcome is appended; it never updates its decision or prediction.

## Point-in-time visibility

A market observation is visible only when its `received_time` is not later
than the decision. A news revision is visible only when its
`collector_first_seen_time` is not later than the decision. Publisher time is
descriptive metadata and never overrides collector visibility.

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

Display-number formatting is repaired deterministically against source
lexemes. Unsupported numbers are replaced by a nonnumeric disclosure and lower
display confidence instead of rejecting the structured receipt. If a Chinese
repair cannot pass validation, the display text becomes an explicit audit notice and
all directional impulses, novelty, and confidence become zero. Provider,
transport, or malformed-JSON failures append a `news_llm_failures` row before
retry. HTTP 429 and transient 5xx failures use bounded progressive backoff and
become terminal after five attempts. Terminal rows remain auditable and are
not automatically requeued. The daily Flash budget keeps 150 requests reserved
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
- A new Challenger is trained only after a fixed new-sample threshold.
- The collector automatically trains the first Shadow Challenger set at 200
  matured complete Forward rows, then trains a new version after each 50 new
  eligible rows. A failed training attempt is logged and cannot alter a prior
  artifact.
- Training never changes the active Champion.
- Only the owner may append a manual promotion approval after forward gates.
- Unknown, missing, stale, or unhealthy data always produces effective
  `WAIT`; it never invents a replacement direction.
- Automatic training creates Market-only, News-residual, and Full composite
  artifacts from one immutable cutoff. All remain Shadow Challengers.

## Storage and separation

SQLite contains immutable metadata, minute/snapshot evidence, news revisions,
annotations, decision events, per-model predictions, outcomes, scores, and
model-update records. Raw high-frequency quotes belong in compressed daily
files outside the Prediction Ledger. Historical warm-up state and forward
evidence use separate roles and cannot share training queries.

## Active free source boundary

Version 1 enables only official RSS adapters for Federal Reserve press
releases, monetary-policy releases, speeches/testimony, and BLS Employment
Situation, CPI, and JOLTS releases. No FedWatch scraping, historical news
backfill, consensus backfill, or broad web-news crawling is permitted.

## Explicit exclusions

This phase does not perform 2016-2026 Replay, historical news matching, deep
learning, reinforcement learning, real order submission, automatic Champion
promotion, or retroactive prediction regeneration.
