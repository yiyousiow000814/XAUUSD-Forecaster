# Storyline Research Contract

## Purpose

The storyline layer is a display-only research view over the append-only news and annotation ledgers. It helps a reader follow event development without treating repeated articles as repeated market votes.

## Point-in-time contract

1. A storyline may use only articles and annotations visible by the dashboard cutoff.
2. Original articles, revisions, first-seen times, and annotation versions remain the audit authority.
3. Storylines never update, delete, or replace source evidence.
4. Edges are limited to `STARTS`, `FOLLOWED_BY`, `CONFIRMS`, `ESCALATES`, and `DEESCALATES`.
5. The system does not infer `CAUSES` from chronology.
6. Story summaries and state are `DISPLAY_ONLY`; they do not enter Ridge training or affect a decision.

## Promotion gate

Story state deltas may become a separate Challenger only after manual review shows that story assignment, duplicate handling, confirmations, contradictions, and revisions are reliable. Promotion requires a new frozen feature schema and a forward-only incremental-EV test against the existing market-only model.
