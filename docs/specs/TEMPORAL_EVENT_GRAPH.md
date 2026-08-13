# Temporal Event Graph V5 Specification

Status: `DISPLAY_ONLY`

Policy: `temporal-event-graph-v5-material-event`

## Purpose

The graph presents auditable, time-bounded real-world episodes. It separates original articles, claims, evidence documents, material events, and episodes. It has no permission to affect Ridge features or Long, Short, or Wait decisions.

## Five layers

1. `Article`: the append-only source receipt and its revisions.
2. `Claim`: one source's statement about the world.
3. `Evidence Document`: a statement, report, press conference, question-and-answer record, or meeting minutes.
4. `Material Event`: one real-world state change supported by one or more evidence documents.
5. `Episode`: a time-bounded sequence of related material events.

## Time semantics

The chronological story order uses the best available event time and then the source publication time. Collector first-seen time is displayed only as an audit receipt. An item whose event or publication date predates the forward collection window by more than the active-story allowance is `ARCHIVAL_BACKFILL` and cannot appear as a current episode.

## Evidence and independence

Documents published by one organization can support one material event, but they do not create additional material events or independent confirmation. A source domain and the institution that owns it count as one organization. `CROSS_SOURCE_CORROBORATED` requires at least two distinct organizations supporting the same material event.

## Story roles

Only `FACT_EVENT` and `OFFICIAL_CLAIM` may create or update the latest core change. `MARKET_REACTION`, `COMMENTARY_FORECAST`, and `BACKGROUND` are attachments. A group with attachments but no core material event is a `MARKET_NARRATIVE_CANDIDATE`, not an active episode.

## Coverage

Each supported episode family has one named coverage template. The view reports the covered roles, required roles, and missing roles. A story without a template reports a real fallback template; it never displays a contradictory `0/0` count beside covered roles.

## Acceptance rules

1. Story start is the earliest real event, not the first collector receipt.
2. One institution's statement, question-and-answer record, and minutes increase evidence-document count, not event count.
3. Documents from one institution cannot satisfy cross-source corroboration.
4. Historical material is labelled `ARCHIVAL_BACKFILL` and excluded from active stories.
5. Market reactions cannot replace the latest core change.
6. Commentary and headline questions cannot become material events.
7. A Hormuz group without a core fact is shown only as a market-narrative candidate.
8. Coverage counts and role labels must use the same coverage definition.
9. Every card reports material-event count, evidence-document count, and independent-organization count separately.
10. The rendered page reports both Runtime Git SHA and Story Policy Version.

## Model boundary

The graph is append-only display research. New annotations may add structured evidence fields, but existing receipts, decisions, predictions, and outcomes are never rewritten. Story states and summaries are excluded from all model feature builders and promotion logic.
