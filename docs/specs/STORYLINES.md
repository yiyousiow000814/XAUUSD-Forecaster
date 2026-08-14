# Storyline Behavior Specification

## Purpose

The storyline layer is a display-only research view over the append-only news and annotation ledgers. It helps a reader follow event development without treating repeated articles as repeated market votes.

## Point-in-time behavior

1. A storyline may use only articles and annotations visible by the dashboard cutoff.
2. Original articles, revisions, first-seen times, and annotation versions remain the audit authority.
3. Storylines never update, delete, or replace source evidence.
4. Edges are limited to `STARTS`, `FOLLOWED_BY`, `CONFIRMS`, `ESCALATES`, and `DEESCALATES`.
5. The system does not infer `CAUSES` from chronology.
6. Story summaries and state are `DISPLAY_ONLY`; they do not enter Ridge training or affect a decision.

## Generic event architecture

1. Events are assigned to a controlled family such as monetary policy, macro release, geopolitics, energy supply, central-bank gold, or financial stress.
2. A story requires the same event family and a specific headline-visible anchor. A country name alone is too broad to create a story.
3. Exact repeated canonical headlines are collapsed before a timeline is shown.
4. The generic state vocabulary is `EMERGING`, `REPORTED`, `CORROBORATED`, `OFFICIALLY_CONFIRMED`, `PHYSICAL_IMPACT_CONFIRMED`, `ESCALATING`, `DEESCALATING`, `CONTRADICTED`, and `RESOLVED`.

## Source-role coverage

1. Coverage is evaluated by roles rather than by hard-coded story-specific source names.
2. Templates can require official primary evidence, a policy or statistics authority, a physical monitor, independent confirmation, or market-reaction confirmation.
3. A domain observed in a story may be proposed as `PROBATION`; a missing role may create a `NEEDS_DISCOVERY` item.
4. A proposal does not grant collection or model permission. Feed availability, body retrieval, latency, revision behavior, blocking, and reliability require separate review.
5. Supported adapter classes are RSS, Atom, JSON API, HTML list, sitemap, article body, and PDF release.

Research and promotion procedure is defined in
[`STORYLINE_PROMOTION.md`](../protocols/STORYLINE_PROMOTION.md). The underlying
append-only and point-in-time guarantees remain authoritative in
[`FORWARD_ONLY.md`](../contracts/FORWARD_ONLY.md) and
[`NEWS_EVIDENCE.md`](../contracts/NEWS_EVIDENCE.md).
