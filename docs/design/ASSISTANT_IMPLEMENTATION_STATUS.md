# Assistant Implementation Status

## Snapshot scope

This status was assessed on 2026-08-15 against `main` parent commit `4eb2187`
(merged PR #102). This Architecture branch changes documentation only. Open PRs
are proposals and do not count as implemented on `main`.

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
| Daily Brief | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | Exists only in open PR #20. |
| Shared news retrieval | [Orchestration](../contracts/ASSISTANT_ORCHESTRATION.md) | `NOT_IMPLEMENTED` | Open PR #21 adds one route, not a shared Search/Q&A/tool service. |
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

## Open PR stack audit

The only open pull requests at assessment time are:

```text
main
  -> #20 feat/daily-news-brief
       -> #21 feat/news-search
            -> #22 feat/gemma-news-qa
```

Their latest GitHub checks were green when inspected, but passing current tests
does not satisfy the broader architecture acceptance criteria below.

### PR #20: Daily Brief

The branch adds an append-only brief, a metered Gemma request, UI output, and a
`DEFERRED` capacity path. It is not merge-ready under this architecture because:

- source selection applies `ORDER BY collector_first_seen_time LIMIT 60` before
  hashing, so a 61st or later eligible item cannot affect the fingerprint or
  candidate set;
- every changed limited subset can trigger regeneration; no durable material-
  change/debounce policy exists; and
- coverage does not prove full-state fingerprinting, deterministic later-item
  selection, restart-safe debounce, or the complete capacity-defer family.

### PR #21: News Search

The branch adds bounded D1 search, deterministic ordering, pagination, escaped
LIKE tokens, UI, and a Preview snapshot fallback. It is not merge-ready because:

- query logic is embedded in one web route instead of a reusable retrieval
  service shared with Q&A and tools;
- published/received date-range filtering is absent; and
- the retrieval contract and its family-level tests do not yet exist.

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
`main`. A future target is never promoted to `MVP` or `IMPLEMENTED` merely
because a branch, schema draft, test stub, or pull-request description exists.
