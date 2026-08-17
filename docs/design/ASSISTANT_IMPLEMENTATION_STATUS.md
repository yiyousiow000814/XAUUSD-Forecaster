# Assistant Implementation Status

## Snapshot scope

This status is maintained as the Assistant architecture and its bounded
implementation PRs land. PR #103 established the contracts; PRs #20, #21, #22,
#104, #106, #108, #110, #111, #113, #114, #116, #117, #118, #119, #120, and
#121 are merged on `main`. The rows describe those bounded foundations and the
advanced evidence validation added by the current bounded scope.

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
| Daily Brief | [Daily Brief](../contracts/DAILY_BRIEF.md) | `IMPLEMENTED` | One Kuala Lumpur date-scoped lifecycle supports rolling current revisions, bounded cross-midnight settlement/finalization, event-level candidate selection, unified ROUTINE capacity routing, durable failure backoff, authoritative dashboard state, and append-only reconstruction. |
| Shared news retrieval | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | PR #21 is merged. Search and PR #22 Q&A reuse one D1 service with bounded Chinese/multi-token queries, published/received ranges, metadata filters, stable evidence IDs, deterministic ordering, and explicit provenance. |
| Evidence-grounded Q&A | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | The Assistant is the only user-facing question surface. The authenticated legacy queue remains available for worker compatibility and preserves historical v2 rows as immutable audit evidence; new `news-qa-v3` answers use claim-level citation coverage with server-recomputed receipts. |
| Advanced evidence validation | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Q&A and native Agent finals share deterministic cross-runtime `assistant.evidence.v1`. Canonical answers contain only bounded claim lines; every line must cite an ID from the exact current packet when evidence exists. Unknown, uncited, forged, mismatched, or over-budget finals fail closed. Receipts explicitly record `entailment_status: NOT_VERIFIED`; semantic entailment is not implemented or claimed. Migration `0016` preserves completed v2 audit rows and terminalizes active v2 jobs instead of retaining a legacy executor. Production activation requires that migration and the updated Windows sync worker. |
| Conversation persistence | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Owner-scoped D1 Conversation and immutable Message records form provider-independent canonical history. Chat admission appends a canonical user message, worker completion atomically appends one final Assistant message, and owner reads expose both the active turn and latest terminal turn needed for browser recovery. |
| Conversation title | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | First-message titles use a 32-grapheme provisional excerpt. Answer completion schedules a leased, metered, low-priority AI title job; the responsive workbench exposes manual rename and explicit regeneration while bounded retry and stale-job cancellation remain server-owned. Production generation requires the updated Windows sync worker. |
| Conversation ordering | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Owner lists use `last_activity_at DESC,id DESC`. Only accepted user messages and persisted Assistant finals advance activity; title, rename, regeneration, and archive work are covered as non-activity. |
| Short-term memory | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | The owner-scoped Context Builder assembles Pinned State, the current rolling summary, authoritative bounded historical retrieval, recent verbatim turns, the canonical current user message, and compact tool evidence under an operational profile. Immutable message provenance remains in D1 for audit and anchor derivation but is not duplicated into model-facing verbatim or historical-memory payloads. Required pins or evidence still fail closed when they cannot fit. |
| Long-term memory | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Migration `0018` schedules the hybrid-v2 rebuild of every immutable canonical message. The Windows worker derives bounded lexical terms and a digest-pinned local Qwen embedding; D1 stores immutable source/hash/mutation receipts while owner-namespaced vectors live in Vectorize. Context construction unions lexical and semantic candidates, reranks them, verifies canonical content and point-in-time ownership, and preserves pinned state as the higher-priority layer. A semantic outage falls back to lexical retrieval without claiming complete recall. |
| Incremental compaction | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Migration `0010` adds immutable versioned summaries, append-only origin-linked pins, and leased compaction receipts that freeze exactly the next ordered message chunk. The low-priority Windows worker sends only `summary vN + new chunk + pinned snapshot` through the serial local Qwen 3.5 4B gateway; strict coverage and origin validation reject lossy output, and invalid or failed output leaves the prior summary active. Production activation requires migration `0010` and the updated sync worker. |
| Reasoning router | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `assistant_routing.py` classifies Q&A, title, compaction, and general Assistant chat deterministically as `SIMPLE`, `ANALYTICAL`, or `TOOL_HEAVY` before transport. High-confidence conversation control, recent-message and remembered-fact recall, identity, and capability turns expose no external tools; other chat turns retain bounded model-selected tool use. Q&A declares zero native calls because retrieval already precedes its model request. |
| Model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Current Assistant model calls use enabled operational `ModelProfile` records, context/capability gates, declared candidate order, and a persisted `assistant-routing-v2` receipt. Historical `assistant-routing-v1` receipts remain parseable only as immutable audit evidence; they cannot enable current chat execution. `LARGE_REQUIRED` work fails closed instead of silently selecting a smaller profile. |
| Local model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Interactive chat and incremental compaction use one hardware-validated Qwen 3.5 4B Q4_K_M profile with a real 262K Ollama context. The 9B/8B pair was removed after full-window tests showed avoidable memory pressure and a 43 GB CPU-spilling Ministral path. Chat locks the selected model across one native tool sequence; compaction uses the same serial capacity ledger at background priority. Other AI workloads retain their provider-specific profiles. |
| Multi-credential routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Q&A, title, compaction, and every native chat model turn pass a fixed model plan to one durable pool-by-model Capacity Router. Google work ranks independent account pools. Local chat uses one anonymous loopback Qwen GPU pool with serial admission. Neither route persists a raw account, key, or credential reference. |
| TPM/RPM/RPD accounting | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Assistant admission applies operational pool/model RPD, rolling RPM/TPM, soft-cap headroom, finite in-flight leases, pair health, failure counts, 429 cooldown, and exact-token confirmation against the existing durable scheduler ledger. A chat worker renews its D1 publication lease before capacity reservation; capacity-only work defers inside its finite budget. |
| Unified model gateway | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `model_gateway.py` owns the metered Google transport and the loopback-only Ollama transport. Assistant chat and incremental compaction share the dedicated local profile and serial capacity ledger. News annotation, Daily Brief, Q&A, and titles retain their provider-specific profiles. |
| Function calling | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | A dedicated one-second Windows Assistant worker invokes the native core independently of the 30-second dashboard mirror cycle. It builds canonical context, conditionally exposes shared `search_news_v1`, preserves exact provider call IDs through the Ollama OpenAI-compatible envelope, allows at most two tool rounds, locks the model, meters every turn, and publishes only validated provenance. Market and calendar adapters remain future work. |
| Parallel tool execution | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Independent calls planned in one model turn execute concurrently within the configured bound, return in deterministic plan order, and retain typed timeout, authorization, schema, adapter, and result failures. The worker exposes authoritative compact news retrieval; market and calendar adapters remain future work. |
| Streaming | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | Python and TypeScript share strict `assistant.event.v1`. D1 persists immutable contiguous events, the Windows worker produces reasoning/tool progress, `/api/assistant-chat?mode=events` provides owner-authenticated finite SSE replay, and the browser recovers an active turn and reconnects from the last contiguous sequence. Production activation still requires migrations and the Windows worker configuration. |
| Commentary/progress | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | The Windows producer publishes deterministic public reasoning and each closed idempotent tool start/finish batch as that round settles. The workbench labels these as processing records, distinguishes pending worker admission from model capacity, and collapses a completed trace without an extra model call or private reasoning text. |
| Responsive chat UI | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | The authenticated workbench supports active and archived conversation lists, bounded message paging, safe text rendering, send/cancel, title controls, archive/restore, finite replay, and phone drawers. Page restore and visibility return refresh canonical turn state; terminal failures remain visible with a deliberate retry affordance. The phone send control uses a CSS-colored SVG rather than a platform emoji glyph. Preview uses a visibly labeled local fixture with all mutations disabled. |
| Rich UI blocks | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | `assistant.content.v1` strictly validates and hashes markdown, news-card, table, metric, and callout blocks. Chat completion persists the document atomically and emits matching `content.block` references; Q&A receives a typed text/evidence document; the responsive renderer owns safe JSX, disclosure, links, and table overflow. The worker derives blocks from final text and authoritative news packets rather than allowing arbitrary model components. Production activation requires migration `0014` and the updated Windows worker. |
| Human authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | News Q&A validates a Cloudflare Access JWT signature, issuer, audience, user identity, and configured owner membership before parsing or storage. Runtime Access policy and owner configuration are deployment prerequisites. |
| Machine authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | `INGEST_TOKEN` protects machine writes; a general service-actor model is not implemented. |
| Assistant queue recovery | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | Q&A, title, compaction, and chat-turn D1 records use bounded attempts, append-only receipts, expiry, and time-limited leases. Chat adds atomic admission, one active turn per conversation, owner/global/rate gates, stale-lease recovery, capacity defer, and idempotent owner cancellation. Stale workers cannot append progress or publish a final. |
| Evidence provenance through compaction | [State](../contracts/ASSISTANT_STATE.md) | `MVP` | Summary rows retain a reproducible prior-summary/source-job chain. Server-derived anchors carry canonical evidence IDs, source references, important timestamps, and tool/artifact references, while every pin and retrieved memory item names canonical origin messages. Historical memory remains context rather than current evidence. |
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
