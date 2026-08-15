# ADR-003: Share Retrieval Through a Bounded Tool Contract

- Status: Accepted
- Date: 2026-08-15

## Context

Search UI, Q&A, and future Assistant tools need the same point-in-time news
records. Route-local queries create inconsistent escaping, filtering, ordering,
Preview fallback, and evidence provenance. Sequential model/tool/model chains
also repeat context and consume avoidable TPM.

## Decision

Use one shared news retrieval service with bounded inputs, deterministic order,
time filters, stable evidence IDs, and explicit source mode. Search, Q&A, and
future tools call that service.

The Assistant uses typed tool schemas and finite per-turn budgets. One planning
turn may request multiple independent tools, which the backend executes in
parallel within the configured bound. Validated compact results return to at
most the remaining bounded model rounds.

## Consequences

- Search and answers agree on query and provenance semantics.
- A recent-news slice cannot masquerade as relevant retrieval.
- Parallel independent tools reduce latency and repeated context.
- Tool failures and unavailable stores require typed honest results.

## Rejected alternatives

- Separate Search and Q&A query implementations.
- `recent_news[:N]` as universal retrieval.
- Unbounded iterative agent loops.
- Model-generated SQL or arbitrary backend execution.

## Related authority

- [`ASSISTANT_ORCHESTRATION.md`](../contracts/ASSISTANT_ORCHESTRATION.md)
- [`NEWS_EVIDENCE.md`](../contracts/NEWS_EVIDENCE.md)
