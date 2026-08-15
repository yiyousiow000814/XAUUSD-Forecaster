# Assistant State Contract

## Purpose

This contract defines who owns Assistant state and how durable conversation
history is separated from the bounded context sent to a model. Product behavior
is specified in
[`ASSISTANT_BEHAVIOR.md`](../specs/ASSISTANT_BEHAVIOR.md). The target component
design is described in
[`ASSISTANT_ARCHITECTURE.md`](../design/ASSISTANT_ARCHITECTURE.md).

These rules apply to every future Assistant implementation. The current
implementation status is recorded separately in
[`ASSISTANT_IMPLEMENTATION_STATUS.md`](../design/ASSISTANT_IMPLEMENTATION_STATUS.md).

## Canonical ownership

XAUUSD Forecaster MUST own the canonical conversation state. A conversation
MUST NOT belong to or depend for continuity on:

- one provider session;
- one model or model version;
- one API key, provider account, project, or credential pool; or
- one in-memory worker, browser, or deployment instance.

A later turn MAY use a different permitted model, provider, or credential pool
without copying, rewriting, or forking the conversation. Provider request IDs
and model session IDs are provenance, not conversation identity.

## Conceptual records

The architecture preserves the following semantics without freezing a SQL
schema:

```text
Conversation
- id
- owner_id
- title
- title_source
- created_at
- last_activity_at
- archived_at
- summary_version
- status

Message
- id
- conversation_id
- role
- content
- created_at
- provenance

Turn
- id
- owner_id
- conversation_id
- user_message_id
- status and bounded lease/attempt receipts
- assistant_message_id (only after atomic completion)

TransportEvent
- turn_id
- protocol and contiguous sequence
- type, occurrence time, and bounded payload
- canonical message ID (only for answer completion)
```

`owner_id` is a stable Forecaster actor identity. Email addresses and provider
account IDs MUST NOT be schema identity. `title_source` distinguishes at least
`PROVISIONAL`, `AI`, and `USER`.

Original accepted user messages and completed Assistant messages are canonical
history. Compaction MUST NOT rewrite or delete them merely to reduce model
context. Corrections, regenerated answers, and derived summaries MUST remain
distinguishable from the records from which they were produced.

## Persistence and recovery

The accepted user message is durable before model work begins. Each client
submission carries an owner-scoped idempotency identity so a network retry does
not create a second user message or queue item.

A completed Assistant message and its model, prompt, retrieval, tool, and
evidence provenance are committed atomically. One orchestration attempt can
publish at most one canonical final message. A crash between provider transport
and persistence records an ambiguous or retryable attempt; recovery may retry
within policy but MUST NOT append duplicate final messages.

Title, summary, compaction, and memory-index jobs are idempotent by their input
version. A worker restart reclaims expired leases rather than depending on
in-memory timers. Failed derived work leaves the last valid canonical or derived
record active.

Turn jobs and transport events are durable orchestration receipts, not a second
conversation history. They never substitute for canonical Message rows. Events
are append-only and contiguous within a turn; only `answer.completed` may link
to the immutable final Assistant message. The final message, completion events,
terminal turn transition, and activity timestamp commit together. Progress,
retry, lease, cancellation, title, and compaction records do not advance
conversation activity.

## Activity ordering

Conversation lists sort by `last_activity_at DESC` with a deterministic
secondary key. Only these events advance `last_activity_at`:

- acceptance of a new user message; and
- persistence of an Assistant final answer.

Title generation, compaction, memory indexing, summary refresh, embedding,
quota accounting, retries, tool cleanup, and other background maintenance MUST
NOT advance it. Background work therefore cannot move an old conversation to
the top of the list.

## Persistent history is not active context

Persistent conversation history and active model context are different
resources. The database MAY retain the complete canonical history while each
model request MUST receive a bounded, purpose-built context.

The Context Builder composes active context from these ordered layers:

```text
Pinned State
+ Rolling Summary
+ Relevant Historical Memory
+ Recent Verbatim Turns
+ Current User Message
+ Relevant Tool Evidence
```

Every layer has a configured budget under the selected `ModelProfile`. The
builder MUST reserve room for system instructions, tool definitions, the user
message, tool results, reasoning overhead, and output. A model's current context
limit, including 256K where applicable, MUST be operational profile data rather
than an architecture constant.

## Memory layers

### Pinned State

Pinned State preserves information that repeated summarization cannot be
allowed to erase:

- explicit user constraints;
- unresolved questions and work;
- important decisions;
- current task scope;
- evidence IDs, source references, and important timestamps; and
- referenced tools and artifacts.

Pinned entries MUST retain their origin message or evidence reference.

### Rolling Summary

Rolling summaries are versioned derived records. Compaction MUST be incremental:

```text
Summary vN + newly compactable messages -> Summary vN+1
```

The system MUST NOT re-summarize the complete growing history on every turn.
The summary input range, prior summary version, output version, model profile,
and completion status MUST be reproducible from persisted metadata.
Admission freezes the exact ordered canonical message identities in the next
compactable range. Messages accepted later cannot enter an already admitted
job, and independently persisted Pinned State is not replaced by asking the
model to repeat it inside every summary.

### Recent Verbatim Turns

A bounded recent window remains verbatim so references such as "the second
one", "why", or "continue that" retain local meaning. Its token and turn limits
are operational configuration, not permanent architecture constants.

### Retrieved Historical Memory

Older history is retrieved only when relevant to the current turn. Retrieval
MUST be bounded and owner-scoped. The system MUST NOT inject all historical
messages into every request.

Long-term memory is an optional future derived index. It MUST link back to
canonical messages and MUST NOT silently become a second conversation store.

## Compaction admission and integrity

Compaction begins before hard context exhaustion. Implementations MUST expose
conceptual `GREEN`, `YELLOW`, and `RED` capacity states; their thresholds remain
operational configuration per model profile.
The bounded recent-turn or recent-message window MAY trigger incremental
compaction while token capacity is still `GREEN`; a turn-count trigger records
the real capacity state rather than relabeling it as token pressure.

Every compaction result MUST preserve:

- unresolved work and user constraints;
- decisions and current topic;
- evidence IDs, provenance, and important timestamps; and
- referenced tools and artifacts.

Compaction is an active-context optimization, not history replacement. A failed
or unvalidated summary MUST NOT replace the last valid summary. The request may
use a smaller bounded context, defer, or fail honestly, but it MUST NOT silently
discard pinned state or evidence provenance.

## Evidence continuity

Messages and summaries that depend on tools MUST persist the evidence IDs and
retrieval provenance needed to audit the answer. Summaries MAY shorten display
text, but they MUST retain stable references to the canonical evidence packet.
Retrieved historical memory MUST not elevate previously untrusted text into
verified evidence.

News timing and evidence eligibility remain governed by
[`NEWS_EVIDENCE.md`](NEWS_EVIDENCE.md) and
[`FORWARD_ONLY.md`](FORWARD_ONLY.md).
