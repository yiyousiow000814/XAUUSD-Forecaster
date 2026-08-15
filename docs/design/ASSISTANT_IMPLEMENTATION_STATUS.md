# Assistant Implementation Status

## Snapshot scope

This status is maintained as the Assistant architecture and its bounded
implementation PRs land. PR #103 established the contracts; PRs #20, #21, #22,
#104, #106, #108, #110, #111, #113, #114, #116, #117, and #118 are merged on
`main`. The rows describe those bounded foundations and the responsive chat
integration in this branch.

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
| Conversation persistence | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Owner-scoped D1 Conversation and immutable Message records form provider-independent canonical history. Chat admission appends a canonical user message, worker completion atomically appends one final Assistant message, and owner reads expose the one indexed active turn needed for browser recovery. Production activation requires migrations `0009` through `0013`. |
| Conversation title | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | First-message titles use a 32-grapheme provisional excerpt. Answer completion schedules a leased, metered, low-priority AI title job; the responsive workbench exposes manual rename and explicit regeneration while bounded retry and stale-job cancellation remain server-owned. Production generation requires the updated Windows sync worker. |
| Conversation ordering | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Owner lists use `last_activity_at DESC,id DESC`. Only accepted user messages and persisted Assistant finals advance activity; title, rename, regeneration, and archive work are covered as non-activity. |
| Short-term memory | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | The owner-scoped Context Builder assembles Pinned State, the current rolling summary, bounded optional historical inputs, recent verbatim turns, the canonical current user message, and compact tool evidence under an operational profile. Required pins, provenance, or evidence fail closed when they cannot fit; the long-term retrieval index remains separate. |
| Long-term memory | [State](../contracts/ASSISTANT_STATE.md) | `NOT_IMPLEMENTED` | No owner-scoped historical memory index exists. |
| Incremental compaction | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Migration `0010` adds immutable versioned summaries, append-only origin-linked pins, and leased compaction receipts that freeze exactly the next ordered message chunk. The low-priority Windows worker sends only `summary vN + new chunk + pinned snapshot` through the metered gateway; invalid or failed output leaves the prior summary active. Production activation requires migration `0010` and the updated sync worker. |
| Reasoning router | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `assistant_routing.py` classifies Q&A, title, compaction, and general Assistant chat deterministically as `SIMPLE`, `ANALYTICAL`, or `TOOL_HEAVY` before transport. Q&A declares zero native calls because retrieval already precedes its model request; any native tool sequence requires a function-capable profile. |
| Model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Current Assistant model calls use enabled operational `ModelProfile` records, context/capability gates, declared candidate order, and a persisted `assistant-routing-v2` receipt. Historical `assistant-routing-v1` receipts remain parseable only as immutable audit evidence; they cannot enable current chat execution. `LARGE_REQUIRED` work fails closed instead of silently selecting a smaller profile. |
| Multi-model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Simple tasks support a declared small-to-large fallback and complex tasks retain a large-only contract. The checked-in safe default enables only the current large model; an actually permitted smaller model must be enabled through `ASSISTANT_MODEL_PROFILES`, so deployment does not guess a provider model ID. |
| Multi-credential routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Q&A, title, compaction, and every native chat model turn pass a fixed model plan to one durable pool-by-model Capacity Router. It ranks independent accounts, rotates transport keys without inventing account capacity, tries compatible pools before model fallback, and never stores a raw account or key in completion provenance. The current installed provider remains Google Generative Language. |
| TPM/RPM/RPD accounting | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Assistant admission applies operational pool/model RPD, rolling RPM/TPM, soft-cap headroom, finite in-flight leases, pair health, failure counts, 429 cooldown, and exact-token confirmation against the existing durable scheduler ledger. A chat worker renews its D1 publication lease before capacity reservation; capacity-only work defers inside its finite budget. |
| Unified model gateway | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `model_gateway.py` is the single metered Google generation boundary used by the news semantic chain, Daily Brief, Q&A, titles, compaction, and the capacity-routed native Assistant agent core. |
| Function calling | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | The Windows worker invokes the native core for an authenticated durable chat turn. It builds canonical context, registers shared `search_news_v1`, preserves exact call IDs and opaque signatures inside the provider sequence, allows at most two tool rounds, locks the model, meters every turn, and publishes only validated provenance. Production migrations and configuration remain activation scopes. |
| Parallel tool execution | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Independent calls planned in one model turn execute concurrently within the configured bound, return in deterministic plan order, and retain typed timeout, authorization, schema, adapter, and result failures. The worker exposes authoritative compact news retrieval; market and calendar adapters remain future work. |
| Streaming | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | Python and TypeScript share strict `assistant.event.v1`. D1 persists immutable contiguous events, the Windows worker produces reasoning/tool progress, `/api/assistant-chat?mode=events` provides owner-authenticated finite SSE replay, and the browser recovers an active turn and reconnects from the last contiguous sequence. Production activation still requires migrations and the Windows worker configuration. |
| Commentary/progress | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | The Windows producer publishes deterministic public reasoning and closed idempotent tool start/finish batches derived from exact receipts. The workbench renders current progress and collapses a completed trace without an extra model call or private reasoning text. |
| Responsive chat UI | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | The authenticated workbench supports active and archived conversation lists, bounded message paging, safe text rendering, send/cancel, title controls, archive/restore, finite replay, and phone drawers. Preview uses a visibly labeled local fixture with all mutations disabled. |
| Rich UI blocks | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No validated Assistant content-block protocol exists. |
| Human authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | News Q&A validates a Cloudflare Access JWT signature, issuer, audience, user identity, and configured owner membership before parsing or storage. Runtime Access policy and owner configuration are deployment prerequisites. |
| Machine authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | `INGEST_TOKEN` protects machine writes; a general service-actor model is not implemented. |
| Assistant queue recovery | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | Q&A, title, compaction, and chat-turn D1 records use bounded attempts, append-only receipts, expiry, and time-limited leases. Chat adds atomic admission, one active turn per conversation, owner/global/rate gates, stale-lease recovery, capacity defer, and idempotent owner cancellation. Stale workers cannot append progress or publish a final. |
| Evidence provenance through compaction | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Summary rows retain a reproducible prior-summary/source-job chain. Server-derived anchors carry canonical evidence IDs, source references, important timestamps, and tool/artifact references, while every pin names canonical origin messages. Advanced claim validation remains separate. |
| Assistant Preview isolation | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | Q&A, conversation, memory, and chat writes or claims reject before authentication, parsing, D1, or model work. Chat turn reads return a labeled synthetic empty object and event reads a finite empty SSE stream; neither accesses production Assistant state. |

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
