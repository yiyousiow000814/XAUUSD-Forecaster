# Assistant Implementation Status

## Snapshot scope

This status is maintained as the Assistant architecture and its bounded
implementation PRs land. PR #103 established the contracts, and PRs #20 and
#21 are merged on `main`. The Q&A rows describe the post-merge state carried by
PR #22; an unmerged branch does not alter the copy visible on `main`.

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
| Conversation persistence | [State](../contracts/ASSISTANT_STATE.md) | `NOT_IMPLEMENTED` | No Forecaster-owned Conversation/Message store exists. |
| Conversation title | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No provisional, AI, manual, or regeneration lifecycle exists. |
| Conversation ordering | [State](../contracts/ASSISTANT_STATE.md) | `NOT_IMPLEMENTED` | No `last_activity_at` conversation list exists. |
| Short-term memory | [State](../contracts/ASSISTANT_STATE.md) | `NOT_IMPLEMENTED` | No pinned/summary/recent-turn Context Builder exists. |
| Long-term memory | [State](../contracts/ASSISTANT_STATE.md) | `NOT_IMPLEMENTED` | No owner-scoped historical memory index exists. |
| Incremental compaction | [State](../contracts/ASSISTANT_STATE.md) | `NOT_IMPLEMENTED` | No versioned summary lifecycle exists. |
| Reasoning router | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | No Assistant task/effort classification exists. |
| Model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `PARTIAL` | `ai_task_registry.py` declares routes for existing news AI tasks; there is no Assistant router. |
| Multi-model routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `PARTIAL` | Existing annotation/title routes have declared fallbacks; mixed Assistant 31B/26B policy is absent. |
| Multi-credential routing | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `PARTIAL` | The news scheduler ranks independent accounts; Assistant tasks are not integrated. |
| TPM/RPM/RPD accounting | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | Q&A reserves interactive model use through the durable account/model scheduler accountant. A general Assistant capacity router and mixed-model policy remain future work. |
| Unified model gateway | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `model_gateway.py` is the single metered Google generation boundary used by the news semantic chain, Daily Brief, and Q&A. |
| Function calling | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | No typed Assistant tool loop exists. |
| Parallel tool execution | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | No Assistant tool planner/executor exists. |
| Streaming | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No versioned Assistant event transport exists. |
| Commentary/progress | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No real tool-event progress surface exists. |
| Rich UI blocks | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No validated Assistant content-block protocol exists. |
| Human authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | News Q&A validates a Cloudflare Access JWT signature, issuer, audience, user identity, and configured owner membership before parsing or storage. Runtime Access policy and owner configuration are deployment prerequisites. |
| Machine authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | `INGEST_TOKEN` protects machine writes; a general service-actor model is not implemented. |
| Assistant queue recovery | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `MVP` | D1 records enforce owner/global admission, idempotency, expiry, `PENDING`/`PROCESSING` leases, bounded backoff, exhausted `FAILED`, stale-lease recovery, and attempt receipts. Cancellation is future work. |
| Evidence provenance through compaction | [State](../contracts/ASSISTANT_STATE.md) | `PARTIAL` | Q&A now persists stable evidence, retrieval, prompt, model, and timestamps. No conversation or compaction path exists yet to carry that provenance through summaries. |
| Assistant Preview isolation | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | Q&A writes reject before authentication, parsing, D1, or model work. Preview GET returns only a labeled synthetic empty private history and the form remains disabled. |

## Historical stack remediation

The old stack is being collapsed into `main` in dependency order rather than
extended with more stacked work:

```text
#103 Assistant Architecture -> main
#20 Daily Brief             -> main
#21 Shared Retrieval        -> main
#22 Q&A Foundation          -> this revision -> main
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

This revision replaces the old public two-state queue with a bounded private
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

The deliberately excluded boundary remains important: PR #22 does not create
canonical Conversation/Message storage, titles, memory, compaction, streaming,
function calling, a general model router, or rich content blocks. Those remain
separate roadmap PRs created from merged `main`.

## Update rule

Every implementation PR updates this matrix only for behavior merged to
`main`. The implementation PR carrying a row update may describe its post-merge
state only when the same diff contains the working behavior and its local, CI,
and required Preview evidence; merge is the final publication gate. A future
target is never promoted merely because a schema draft, test stub, or pull-
request description exists.
