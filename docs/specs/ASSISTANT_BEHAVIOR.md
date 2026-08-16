# Assistant Behavior Specification

## Scope and status

This specification defines required user-visible and protocol behavior for the
target Assistant. It does not claim that the behavior is implemented. Current
coverage is recorded in
[`ASSISTANT_IMPLEMENTATION_STATUS.md`](../design/ASSISTANT_IMPLEMENTATION_STATUS.md).

State ownership, orchestration, and security are governed by:

- [`ASSISTANT_STATE.md`](../contracts/ASSISTANT_STATE.md);
- [`ASSISTANT_ORCHESTRATION.md`](../contracts/ASSISTANT_ORCHESTRATION.md); and
- [`ASSISTANT_SECURITY.md`](../contracts/ASSISTANT_SECURITY.md).

## Conversation creation

After authentication and acceptance of the first user message, the server
creates the conversation and canonical user message immediately. The response
contains the conversation ID, message ID, and a provisional title. Conversation
creation does not wait for a title-generation model request.

The provisional title is a bounded, single-line excerpt of the first user
message. Truncation counts Unicode grapheme clusters, not bytes, code units, or
JavaScript string length.

## Automatic titles

After the first meaningful Assistant final answer is persisted, title
generation MAY run in the background. It is a `LOW_COMPLEXITY_BACKGROUND_TASK`
with tiny bounded context, minimal reasoning, and a smaller permitted model
where available. It MUST NOT consume capacity reserved for higher-priority
interactive or semantic-pipeline work.

The initial operational default is `MAX_TITLE_GRAPHEMES = 32`. The title is:

- one line, concise, and accurate;
- free of surrounding quotes;
- specific rather than generic; and
- dated only when the date distinguishes the topic.

For example, `CPI 后黄金反常上涨分析` is useful; `关于黄金市场的对话` is not.
Title-generation failure neither blocks nor fails the answer and may be retried
within the background retry budget.

When the user manually renames a conversation, `title_source` becomes `USER`.
Background work MUST NOT overwrite it. Only an explicit `Regenerate title`
action may request a new AI title after manual rename.

Title generation and regeneration do not change `last_activity_at`.

## Conversation list

The default list order is:

```text
ORDER BY last_activity_at DESC, id DESC
```

New accepted user messages and persisted Assistant final answers count as
activity. Title generation, compaction, memory work, embeddings, quota updates,
tool cleanup, and other background maintenance do not. Archived conversations
are excluded from the active list unless the user explicitly requests them.

## Assistant work states

If asynchronous work is used, the durable queue exposes these states:

| State | Meaning |
| --- | --- |
| `PENDING` | Authenticated and admitted, waiting for a worker. |
| `PROCESSING` | Held by one worker under a time-bounded lease. |
| `ANSWERED` | A validated final answer and provenance were persisted. |
| `FAILED` | Retry budget ended without a valid answer. |
| `REJECTED` | Authentication, validation, policy, or admission denied the task. |
| `EXPIRED` | The task became too old to process meaningfully. |
| `CANCELLED` | The authenticated owner cancelled before completion. |

A worker claim records a lease expiry and attempt count. A crashed worker cannot
leave `PROCESSING` permanently: lease recovery returns eligible work to
`PENDING` or moves exhausted work to `FAILED`. Retries are bounded and preserve
the prior failure receipt. An active worker renews the same lease immediately
before a provider attempt. Renewal does not consume another attempt, requires
the unexpired lease token, and is capped by the immutable turn expiry.

A worker that holds a valid lease but cannot obtain safe model capacity records
`CAPACITY_DEFERRED`, clears the lease, applies bounded backoff, and returns the
item to `PENDING` when the orchestration budget remains. No model request is
sent, but the finite claim budget and task expiry still apply. The transition
cannot revive expired, stale-lease, or terminal work.

Conversation reads expose the newest turn in addition to any currently active
turn. Returning from an iOS background state or restoring a page MUST refresh
both records. A terminal turn without an Assistant message remains visibly
failed after reload and offers a user-initiated way to restore the exact
question to the composer. The client MUST NOT silently auto-submit a duplicate.

Authentication occurs before queue creation. Per-owner concurrency and global
capacity are bounded, so anonymous or single-user traffic cannot starve the
queue.

The operational chat queue admits at most one active turn per conversation,
two active turns per owner, ten globally, and five new turns per owner per
minute. A turn expires after 30 minutes, a processing lease after five minutes,
and at most three claims may be consumed. These are bounded v1 operational
values, not permanent product constants. An accepted user message, its turn
job, and `conversation.started` are one transaction; admission failure leaves
none of them behind. Owner cancellation is idempotent and terminal.

## Streaming event protocol

Streaming is a versioned event stream, not an alternate conversation database.
The initial target envelope is equivalent to:

```text
assistant.event.v1
- protocol
- event_id
- conversation_id
- user_turn_id
- message_id (when assigned)
- sequence
- type
- occurred_at
- payload
```

`sequence` is monotonic within one user turn. Reconnection MAY resume from a
known sequence, but replayed transport events do not create duplicate canonical
messages.

The operational web transport returns a finite owner-authenticated SSE replay,
not an indefinitely held Worker connection. The client sends the last consumed
numeric sequence in `Last-Event-ID` or `after`, consumes the bounded page, and
reconnects while the turn is non-terminal or more events remain. Every replay
rechecks owner authorization. A response may therefore be empty without
meaning that the turn failed.

The authenticated browser recovers an admitted `PENDING` or `PROCESSING` turn
from the owner conversation read, replays its immutable event sequence from the
beginning through bounded pages, and resumes from the last contiguous sequence.
It pauses reconnect work while the document is hidden and aborts requests when
the view is left. Reconnect attempts have a finite retry count and cannot run
past the turn's bounded orchestration lifetime. After a terminal event the
browser refreshes canonical conversation and message records. Interrupted
presentation deltas are labeled as provisional and never become a locally
invented final answer; they disappear after failure or cancellation.

The operational v1 codec is shared by the Python orchestrator and TypeScript
web boundary. Envelopes and type-specific payloads use exact fields and strict
JSON: unknown fields, non-finite numbers, malformed identifiers, oversized
payloads, and unsupported protocol versions fail closed. A turn carries at
most 256 events, one payload at most 16,384 UTF-8 bytes, one answer delta at
most 4,096 bytes, and all presentation deltas/block references at most 65,536
bytes. These are versioned operational safety values rather than assumed model
or provider limits. Progress admission reserves enough sequence capacity for
the largest permitted final-answer event set, so progress cannot prevent a turn
from reaching one durable terminal state.

The initial event vocabulary is:

```text
conversation.started
reasoning.started
tool.started
tool.completed
tool.failed
retrieval.started
retrieval.completed
answer.started
answer.delta
content.block
answer.completed
conversation.completed
error
cancelled
```

`answer.delta` is presentation transport. `answer.completed` identifies the
validated canonical final message. A partial stream interrupted before final
persistence MUST NOT be presented later as a completed answer.

The first event is exactly `conversation.started`. Tool and retrieval
completions must match a prior start; all active operations close before
`answer.started`. Only deltas and validated content-block references occur
while the answer is open. `answer.completed` is the only event allowed to name
the canonical Assistant message, and `conversation.completed` follows it.
`error` and `cancelled` are terminal, and no event follows a terminal event.
Reasoning progress contains only the public reasoning class, never private
reasoning text. SSE uses the numeric sequence as `Last-Event-ID`/resume state
and includes one complete JSON envelope in each `data` record.

`content.block` carries only a bounded block identity, version, type, and
content hash in v1. During completion the server validates the separately
versioned rich-content document, persists it with the canonical message, and
appends one reference event per block in the same transaction. The event
transport never treats arbitrary model HTML as renderable content.

The event protocol is independently versioned from message storage. Streaming
can therefore be added or replaced without migrating canonical conversation
history.

Machine progress writes require the active turn lease. Tool and retrieval start
and finish records are admitted together as one closed idempotent batch; a
transport failure cannot persist an unclosed operation. Final persistence
atomically appends the canonical Assistant message, answer events, terminal
conversation event, turn status, conversation activity, and first-title job
admission. Compaction scheduling happens afterward and cannot invalidate that
final.

The Windows producer emits the deterministic public reasoning class before
model work. After each successful native run it projects exact public tool
receipts into closed start/finish batches before publishing the final; retry
attempts use distinct bounded public call identities. Unknown rejected tools
without an authoritative version remain in model provenance but do not receive
a fabricated presentation event.

## Progress and reasoning display

Progress copy is derived from real backend and tool events. The UI may render
states such as:

```text
正在搜索相关新闻…
✓ 找到 14 条相关证据
正在检查价格走势…
✓ 数据已取得
正在整理回答…
```

The system MUST NOT spend an extra model call merely to generate "thinking"
copy. `reasoning.started` reports a phase and selected public policy metadata;
it never exposes private chain-of-thought. A completed progress trace MAY be
collapsed behind `查看分析过程`.

The responsive workbench exposes conversation selection, older-message paging,
title controls, archive and restore, turn cancellation, and the finite progress
trace on both desktop and phone layouts. Canonical message text is rendered as
text, not arbitrary model HTML. The phone conversation rail is a dismissible
drawer and all primary controls remain reachable without horizontal overflow.

## Structured content protocol

Operational Assistant output is a validated `assistant.content.v1` document
containing at most 12 typed blocks and 65,536 canonical JSON bytes. Initial
block types are:

- `markdown`;
- `news_card`;
- `table`;
- `metric`; and
- `callout`.

Future compatible types may include `price_chart`, `timeline`,
`calendar_event`, `evidence_group`, `comparison`, `warning`, `model_status`, and
`code`.

The frontend owns rendering, responsive layout, link behavior, and
accessibility. A model MUST NOT emit arbitrary HTML, scripts, styles, event
handlers, or unvalidated component names. Unknown block types fail validation;
pre-migration messages without a document continue to render their canonical
plain text. Neither path injects HTML.

Every block has an immutable ID, `v1` block version, typed data, and SHA-256 of
its canonical core. The document has an independent SHA-256 over its protocol
and ordered blocks. IDs are unique, hashes are verified by the web persistence
boundary, and the first block is markdown whose text exactly equals the
canonical message `content`. Runtime worker output that is absent, malformed,
over budget, hash-inconsistent, or detached from the answer fails closed before
the message is inserted.

The Windows worker builds the document deterministically from the final answer
and authoritative compact tool packets. It may add evidence metrics, news
cards, a received-time table, and the fixed decision-support boundary. The
model does not choose arbitrary frontend components. News-card evidence IDs
must be a unique subset of the exact successful tool provenance for the turn.

### News cards

A news card contains bounded structured fields such as:

```text
evidence_id
source
published_at
received_at
headline
summary
category
impact
relevance
source_url
```

The evidence ID, publication time, receipt time, and source link remain
available in the expanded view. The card does not imply that a publisher,
headline, and independent event are interchangeable.
Only public HTTPS links without embedded credentials are renderable. Missing or
unsafe links remain unavailable instead of becoming executable navigation.

### Tables

Tables contain structured columns and bounded rows. The backend validates cell
types and limits; the frontend decides desktop and mobile rendering. Models do
not generate `<table>` markup.

The current renderer uses semantic JSX for all five v1 types. Table overflow is
contained inside a keyboard-focusable region on narrow screens; news-card
details use a native disclosure; canonical markdown is rendered as text rather
than interpreted as HTML.

## Failure behavior

- Missing or unverified identity denies a model-consuming action without
  revealing private object existence.
- No relevant evidence produces an explicit insufficient-evidence answer, not a
  fabricated market explanation.
- A final with available evidence is rejected as a whole when any answer line
  lacks a retrieved citation, cites an unknown ID, or fails its deterministic
  `assistant.evidence.v1` receipt. Citation coverage is shown as structural
  validation only; the product does not claim semantic entailment.
- A failed independent tool is shown as unavailable; successful tool results may
  still support a bounded partial answer when policy permits.
- Capacity exhaustion may defer, queue, offer a declared fallback, or reject
  gracefully. It does not silently change a required model policy.
- Title or compaction failure does not erase the conversation or replace the
  last valid derived record.
- Pending or failed historical indexing leaves canonical messages available and
  marks recall incomplete; it never converts an empty partial index into a
  claim that no relevant history exists.
- Error events contain public error codes and recovery guidance, not secrets,
  provider payloads, or private reasoning.

## Product boundary

Assistant answers are decision support. They are excluded from forecasting
training, cannot place orders, cannot promote a model, and cannot change the
frozen XAUUSD-only, five-minute, 30-minute, `LONG`/`SHORT`/`WAIT` Shadow product
contract.
