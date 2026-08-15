# Assistant Implementation Roadmap

## Status

This is a sequencing plan, not current architecture authority. Contracts and
specifications are linked from
[`ASSISTANT_ARCHITECTURE.md`](../design/ASSISTANT_ARCHITECTURE.md).

## Branch rule

The Architecture documentation PR is created directly from the latest
`main`. It does not modify or depend on PR #20, #21, or #22. No new feature PR
is stacked on #22 while this plan is being established.

At plan creation, the graph is:

```text
main
├── Architecture documentation PR
└── #20 Daily Brief
      └── #21 Shared Retrieval / Search
            └── #22 Q&A Foundation
```

The Architecture PR is independent. The existing three-PR stack is historical
work to collapse, not a base for more architecture branches.

## Existing stack integration

### 1. Merge the Architecture documentation

Acceptance:

- contracts, specification, design, ADRs, status matrix, and roadmap agree;
- links and terminology validate;
- target behavior is not described as implemented; and
- the PR remains documentation-only and independently mergeable.

### 2. Repair and merge PR #20

Keep the scope to Daily Brief:

- fingerprint the complete eligible daily source state;
- select a deterministic bounded evidence set that can include later items;
- preserve point-in-time and received-time semantics;
- implement durable material-change gating and restart-safe debounce;
- keep the work lower-priority/preemptible with explicit deferral; and
- extend family-level tests for more than 60 items, deterministic selection,
  duplicate-call prevention, restart behavior, fake evidence, and capacity.

Do not add conversations, memory, streaming, authentication, an agent loop, or
a rich UI framework to #20.

After #20 merges, `main` becomes the base for the next update.

### 3. Rebase, repair, and merge PR #21

Move the Search behavior onto one shared retrieval service used by both server
and UI paths. Add bounded query/filter contracts, date ranges, deterministic
pagination, D1 unavailability, Preview fallback, and Chinese/multi-token/special-
character coverage.

Do not add conversations, memory, streaming, model routing, an agent loop, or
unrelated authentication to #21.

After #21 merges, rebase the next branch onto the new `main`; do not keep the
old stack base.

### 4. Rebase, repair, and merge PR #22

Keep the scope to authenticated evidence-grounded Q&A foundation:

- authorize the human before queue creation;
- retrieve through the shared #21 service;
- send a bounded evidence packet through the metered gateway;
- persist answer, model/prompt version, retrieval provenance, and evidence IDs;
- return honest insufficient evidence;
- reject fabricated evidence;
- add leased queue recovery and bounded terminal states; and
- preserve Preview read-only and credential secrecy.

A minimal conversation ID/message ID/owner foundation may be included only if
it remains small and does not pull memory, compaction, streaming, or a general
agent framework into #22.

## Target collapse

The intended progression is:

```text
Architecture PR merged
        |
        v
main + repaired #20 merged
        |
        v
main + rebased/repaired #21 merged
        |
        v
main + rebased/repaired #22 merged
```

This removes the stack one layer at a time. It does not create or reserve future
pull-request numbers in advance.

## Later implementation PRs

After the existing stack is collapsed, create small PRs from already merged
`main`. Suggested dependency families are:

| PR scope | Depends on | Explicitly leaves for later |
| --- | --- | --- |
| Conversation persistence and title lifecycle | Authenticated Q&A foundation | Memory, tools, streaming |
| Incremental memory and compaction | Conversation persistence | Long-term semantic memory |
| Deterministic reasoning and model router | Bounded Context Builder | Capacity-pool expansion |
| Assistant capacity integration | Model router and existing scheduler | Tool loop and UI |
| Typed function-calling loop | Shared retrieval and capacity integration | Streaming UI |
| Streaming and chat UX | Conversation/message contract and tool events | Advanced rich blocks |
| Structured rich content blocks | Streaming/message envelope | Long-term memory |
| Long-term historical memory | Stable compaction provenance | Autonomous behavior |
| Advanced evidence validation | Shared retrieval and persisted provenance | Trading authority |

Independent scopes may proceed in parallel from the same merged base. A short
temporary stack is acceptable only when a dependency cannot be reviewed or
tested independently; it must be collapsed promptly.

Current implementation has reached the server-side typed native function-
calling core: a versioned read-only registry, bounded parallel executor, exact
provider call/response continuity, and capacity-routed model turns. It remains
`PARTIAL` until an authenticated chat runtime invokes it. The next independent
scope is the versioned streaming protocol; it will not need to rewrite
canonical conversation or tool contracts.

## Per-PR completion rule

Every implementation PR:

- starts from merged `main` where practical;
- names one scope and dependency;
- updates the normative document before or with a semantic change;
- reuses shared state, retrieval, gateway, capacity, and content contracts;
- adds contract-level regression coverage for affected sibling paths;
- verifies desktop and phone behavior on the deployed branch Preview for UI
  changes; and
- updates the implementation matrix only after end-to-end behavior exists.
