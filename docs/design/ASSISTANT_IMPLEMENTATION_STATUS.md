# Assistant Implementation Status

## Snapshot scope

This status is maintained as the Assistant architecture and its bounded
implementation PRs land. PR #103 established the contracts; PRs #20, #21, #22,
#104, #106, #108, #110, #111, #113, and #114 are merged on `main`. The rows
describe the bounded Assistant foundations covered by those merged PRs and by
the current cross-runtime streaming-protocol implementation.

Status values have precise meanings:

- `NOT_IMPLEMENTED`: no shipping implementation on `main` satisfies the
  capability.
- `PARTIAL`: some reusable infrastructure or behavior exists, but the complete
  contract is not available end to end.
- `MVP`: a bounded end-to-end version exists on `main`, with documented target
  limitations.
- `IMPLEMENTED`: the current contract is implemented and verified on `main`.
- `DEPRECATED`: the capability remains only for migration or historical use.

## Capability matrix

| Capability | Contract | Current status | Evidence and limitation |
| --- | --- | --- | --- |
| Daily Brief | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | PR #20 is merged: complete point-in-time state fingerprinting, bounded candidates, durable debounce, capacity defer, append-only output, and evidence validation are covered. A Preview backed by an older public snapshot may still show the explicit empty state. |
| Shared news retrieval | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | PR #21 is merged. Search and PR #22 Q&A reuse one D1 service with bounded Chinese/multi-token queries, published/received ranges, metadata filters, stable evidence IDs, deterministic ordering, and explicit provenance. |
| Evidence-grounded Q&A | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | PR #22 provides authenticated asynchronous single-question Q&A through shared retrieval, a compact packet, the metered gateway, strict cited-ID validation, and persisted model/prompt/retrieval provenance. It is not a general agent or multi-turn chat. |
| Conversation persistence | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Owner-scoped D1 Conversation and immutable Message records now form provider-independent canonical history. Q&A creates the first user/Assistant pair atomically; production activation requires migration `0009`, and general multi-turn orchestration remains future work. |
| Conversation title | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | First-message titles use a 32-grapheme provisional excerpt. Answer completion schedules a leased, metered, low-priority AI title job; manual rename, explicit regeneration, bounded retry, and stale-job cancellation are implemented. Production generation requires the updated Windows sync worker; chat UI controls remain future work. |
| Conversation ordering | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Owner lists use `last_activity_at DESC,id DESC`. Only accepted user messages and persisted Assistant finals advance activity; title, rename, regeneration, and archive work are covered as non-activity. |
| Short-term memory | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | The owner-scoped Context Builder assembles Pinned State, the current rolling summary, bounded optional historical inputs, recent verbatim turns, the canonical current user message, and compact tool evidence under an operational profile. Required pins, provenance, or evidence fail closed when they cannot fit; the long-term retrieval index remains separate. |
| Long-term memory | [State](../contracts/ASSISTANT_STATE.md) | `NOT_IMPLEMENTED` | No owner-scoped historical memory index exists. |
| Incremental compaction | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Migration `0010` adds immutable versioned summaries, append-only origin-linked pins, and leased compaction receipts that freeze exactly the next ordered message chunk. The low-priority Windows worker sends only `summary vN + new chunk + pinned snapshot` through the metered gateway; invalid or failed output leaves the prior summary active. Production activation requires migration `0010` and the updated sync worker. |
| Reasoning router | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `assistant_routing.py` classifies Q&A, title, compaction, and general Assistant chat deterministically as `SIMPLE`, `ANALYTICAL`, or `TOOL_HEAVY` before transport. Q&A declares zero native calls because retrieval already precedes its model request; any native tool sequence requires a function-capable profile. |
| Model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Current Assistant model calls use enabled operational `ModelProfile` records, context/capability gates, declared candidate order, and a persisted `assistant-routing-v2` receipt. Historical `assistant-routing-v1` receipts remain parseable only as immutable audit evidence; they cannot enable current chat execution. `LARGE_REQUIRED` work fails closed instead of silently selecting a smaller profile. |
| Multi-model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Simple tasks support a declared small-to-large fallback and complex tasks retain a large-only contract. The checked-in safe default enables only the current large model; an actually permitted smaller model must be enabled through `ASSISTANT_MODEL_PROFILES`, so deployment does not guess a provider model ID. |
| Multi-credential routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Q&A, title, and compaction now pass their fixed model plan to one durable pool-by-model Capacity Router. It ranks independent accounts, rotates transport keys without inventing account capacity, tries compatible pools before model fallback, and never stores a raw account or key in completion provenance. The current installed provider remains Google Generative Language. |
| TPM/RPM/RPD accounting | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Assistant admission now applies operational pool/model RPD, rolling RPM/TPM, soft-cap headroom, finite in-flight leases, pair health, failure counts, 429 cooldown, and exact-token confirmation against the existing durable scheduler ledger. Capacity-only Q&A/title/compaction deferrals remain inside the finite work budget. |
| Unified model gateway | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `model_gateway.py` is the single metered Google generation boundary used by the news semantic chain, Daily Brief, Q&A, titles, compaction, and the capacity-routed native Assistant agent core. |
| Function calling | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `PARTIAL` | The server core sends native declarations, preserves exact call IDs and opaque thought signatures inside the ephemeral provider sequence, authorizes read-only calls, and enforces finite context/output/evidence/result/call/turn budgets with at most two tool rounds. Every model turn is metered and capacity-routed while its model remains locked for the sequence. No authenticated user-facing chat queue invokes this core yet. |
| Parallel tool execution | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `PARTIAL` | Independent calls planned in one model turn execute concurrently within the configured bound, return in deterministic plan order, and retain typed timeout, authorization, schema, adapter, and result failures. The only application adapter currently defined is authoritative compact news retrieval; market and calendar adapters remain future work. |
| Streaming | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `PARTIAL` | Python and TypeScript now share the strict `assistant.event.v1` envelope, type payloads, finite state machine, safety budgets, canonical-message boundary, and SSE encoder through one cross-runtime fixture. Durable owner-authenticated replay and a live producer/consumer route remain the next chat-runtime scope. |
| Commentary/progress | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No real tool-event progress surface exists. |
| Rich UI blocks | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No validated Assistant content-block protocol exists. |
| Human authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | News Q&A validates a Cloudflare Access JWT signature, issuer, audience, user identity, and configured owner membership before parsing or storage. Runtime Access policy and owner configuration are deployment prerequisites. |
| Machine authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | `INGEST_TOKEN` protects machine writes; a general service-actor model is not implemented. |
| Assistant queue recovery | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | Q&A, title, and compaction D1 records use bounded attempts, append-only attempt receipts, and time-limited leases; expired work is reclaimed and stale workers cannot publish or consume a failure attempt. Capacity-only deferral releases the lease with backoff but remains bounded by the same orchestration budget. Q&A and compaction admission are bounded per owner and globally. User-turn cancellation is future work. |
| Evidence provenance through compaction | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Summary rows retain a reproducible prior-summary/source-job chain. Server-derived anchors carry canonical evidence IDs, source references, important timestamps, and tool/artifact references, while every pin names canonical origin messages. Advanced claim validation remains separate. |
| Assistant Preview isolation | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | Q&A, conversation, pin, context, and compaction writes or claims reject before authentication, parsing, D1, or model work. Preview GET returns only a labeled synthetic empty private history and the form remains disabled. |

## Historical stack remediation

The old stack is being collapsed into `main` in dependency order rather than
extended with more stacked work:

```text
#103 Assistant Architecture -> main
#20 Daily Brief             -> main
#21 Shared Retrieval        -> main
#22 Q&A Foundation          -> main
```

### PR #20: Daily Brief remediation

PR #20 was repaired and merged. It fingerprints the complete visible daily
state before selecting at most 60 deterministic candidates. A persisted refresh
record preserves the material-change settling window across restarts, and
capacity or evidence failures defer or fail closed without inventing a brief.

### PR #21: Shared Retrieval remediation

PR #21 was repaired and merged. It resolves the architecture gaps found in the
old Search branch:

- query parsing, SQL construction, in-memory Preview matching, ordering, and
  provenance live in one reusable service rather than the route;
- both published-time and received-time ranges are supported alongside source,
  category, and evidence-ID filters;
- `%`, `_`, backslash, Chinese, multi-token, empty, bounded-page, D1 failure,
  and Preview-fallback behavior share one contract suite; and
- the UI exposes date filtering and labels an incomplete Preview build snapshot
  rather than presenting it as the complete archive.

### PR #22: News Q&A

PR #22 replaced the old public two-state queue with a bounded private
foundation:

- Cloudflare Access JWT verification and explicit owner membership occur before
  human payload parsing or D1 access; the ingest bearer remains machine-only;
- owner-scoped idempotency, per-owner/global admission, expiry, time-bounded
  processing leases, stale-lease recovery, retry backoff, and terminal failure
  are persisted in D1;
- the Windows worker calls PR #21 shared retrieval with the question cutoff,
  sends only a compact evidence packet to Gemma through the metered gateway,
  and never uses `recent_news[:200]`;
- any invented evidence ID rejects the entire model result, while an empty
  retrieval publishes a fixed `INSUFFICIENT_EVIDENCE` answer without a model
  call; and
- final answer, evidence IDs, retrieval ordering/cutoff, model, prompt, and
  timestamps persist together under the valid lease.

PR #22 deliberately excluded canonical Conversation/Message storage. The
conversation foundation now supplies that storage and title lifecycle without
adding streaming, function calling, a general model router, or rich content
blocks. A later bounded implementation adds incremental memory and compaction as
its own scope; long-term retrieval remains a separate roadmap PR.

## Update rule

Every implementation PR updates this matrix only for behavior merged to
`main`. The implementation PR carrying a row update may describe its post-merge
state only when the same diff contains the working behavior and its local, CI,
and required Preview evidence; merge is the final publication gate. A future
target is never promoted merely because a schema draft, test stub, or pull-
request description exists.
