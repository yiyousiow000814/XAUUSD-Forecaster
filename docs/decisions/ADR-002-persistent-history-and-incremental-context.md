# ADR-002: Separate Persistent History from Incremental Active Context

- Status: Accepted
- Date: 2026-08-15

## Context

Complete conversation history is useful for audit and continuity but grows
without bound. Sending it on every request wastes TPM and eventually exceeds
the selected model's context limit. Re-summarizing the entire history on each
turn has the same growth problem and repeatedly degrades evidence references.

## Decision

Persist canonical messages independently from model context. Build each request
from pinned state, a versioned rolling summary, relevant historical memory,
recent verbatim turns, the current message, and bounded tool evidence.

Compaction is incremental: a valid summary plus the next compactable range
produces a new version. Original messages remain canonical, and summaries retain
evidence IDs and source provenance.

## Consequences

- Context can adapt to models with different limits without rewriting history.
- Compaction cost is bounded by newly compactable content.
- Failed summaries do not destroy the last valid context artifact.
- Implementations need versioned summary metadata and provenance validation.

## Rejected alternatives

- Inject all history on every turn.
- Re-summarize all history on every turn.
- Delete original messages after summarization.
- Treat a summary as a second canonical conversation store.

## Related authority

- [`ASSISTANT_STATE.md`](../contracts/ASSISTANT_STATE.md)
