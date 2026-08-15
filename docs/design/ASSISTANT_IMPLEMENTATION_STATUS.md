# Assistant Implementation Status

## Snapshot scope

This status is maintained as the Assistant architecture and its bounded
implementation PRs land. PR #103 established the contracts, and PR #20 is now
merged on `main`. The shared-retrieval row describes the post-merge state of
PR #21; an unmerged branch does not alter the copy visible on `main`.

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
| Shared news retrieval | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | PR #21 provides one reusable D1/Preview service with bounded Chinese/multi-token queries, published/received ranges, metadata filters, stable evidence IDs, deterministic pagination, and family-level tests. Search is the first caller; Q&A adoption remains PR #22. |
| Evidence-grounded Q&A | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | Exists only in open PR #22. |
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
| TPM/RPM/RPD accounting | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `PARTIAL` | Durable news-AI accounting exists at account/model scope; no Assistant admission path exists. |
| Unified model gateway | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `MVP` | `model_gateway.py` is the single metered Google generation boundary used by the current news chain. |
| Function calling | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | No typed Assistant tool loop exists. |
| Parallel tool execution | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | No Assistant tool planner/executor exists. |
| Streaming | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No versioned Assistant event transport exists. |
| Commentary/progress | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No real tool-event progress surface exists. |
| Rich UI blocks | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No validated Assistant content-block protocol exists. |
| Human authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `PARTIAL` | A Sites identity helper exists, but no owner-authorized model-consuming Assistant endpoint exists. |
| Machine authentication | [Security](../contracts/ASSISTANT_SECURITY.md) | `MVP` | `INGEST_TOKEN` protects machine writes; a general service-actor model is not implemented. |
| Assistant queue recovery | [Behavior](../specs/ASSISTANT_BEHAVIOR.md) | `NOT_IMPLEMENTED` | No leased Assistant queue with bounded recovery exists. |
| Evidence provenance through compaction | [State](../contracts/ASSISTANT_STATE.md) | `PARTIAL` | News evidence has stable provenance, but no conversation/compaction path carries it. |
| Assistant Preview isolation | [Security](../contracts/ASSISTANT_SECURITY.md) | `PARTIAL` | General Preview write rejection exists; Assistant routes and fixtures do not yet exist. |

## Historical stack remediation

The old stack is being collapsed into `main` in dependency order rather than
extended with more stacked work:

```text
#103 Assistant Architecture -> main
#20 Daily Brief             -> main
#21 Shared Retrieval        -> this revision -> main
#22 Q&A Foundation          -> refresh from main after #21
```

### PR #20: Daily Brief remediation

PR #20 was repaired and merged. It fingerprints the complete visible daily
state before selecting at most 60 deterministic candidates. A persisted refresh
record preserves the material-change settling window across restarts, and
capacity or evidence failures defer or fail closed without inventing a brief.

### PR #21: Shared Retrieval remediation

This revision resolves the architecture gaps found in the old Search branch:

- query parsing, SQL construction, in-memory Preview matching, ordering, and
  provenance live in one reusable service rather than the route;
- both published-time and received-time ranges are supported alongside source,
  category, and evidence-ID filters;
- `%`, `_`, backslash, Chinese, multi-token, empty, bounded-page, D1 failure,
  and Preview-fallback behavior share one contract suite; and
- the UI exposes date filtering and labels an incomplete Preview build snapshot
  rather than presenting it as the complete archive.

### PR #22: News Q&A

The branch adds a public question queue, a metered Gemma answer, evidence-ID
filtering, D1 persistence, and Preview write rejection. It is not merge-ready
because:

- anonymous users can create model-consuming queue items;
- the worker reads `recent_news[:200]` instead of shared relevant retrieval;
- queue state is effectively `PENDING`/`ANSWERED`, without leases, crash
  recovery, bounded retry, terminal failure, rejection, or expiry;
- questions and answers have no owner-scoped conversation/message foundation;
- filtering invented evidence IDs does not prove that the remaining answer is
  grounded; and
- no-evidence handling raises a worker error instead of persisting an honest
  insufficient-evidence result.

## Update rule

Every implementation PR updates this matrix only for behavior merged to
`main`. The implementation PR carrying a row update may describe its post-merge
state only when the same diff contains the working behavior and its local, CI,
and required Preview evidence; merge is the final publication gate. A future
target is never promoted merely because a schema draft, test stub, or pull-
request description exists.
