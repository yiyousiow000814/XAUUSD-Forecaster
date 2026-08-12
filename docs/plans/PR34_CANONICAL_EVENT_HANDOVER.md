# PR 34: Canonical Events And Model Handover

Status: implementation complete; Broad-evidence activation with an explicit
Official cold-start state.

## Scope

- Give one real-world event one canonical identity.
- Treat multiple reports as evidence instead of duplicate training events.
- Bound the total weight of one event and one source.
- Remove the remaining semantic keyword admission filters from direct official
  sources while retaining the Forward epoch, 72-hour age limit, immutable
  deduplication, complete-body requirement, and per-source fetch caps.
- Separate source transport health from recent evidence yield and record
  publisher-body failures as degraded.
- Normalize first-party and syndicated reporting identities before applying
  source budgets; collector lanes never count as independent publishers.
- Replace source allowlists with frozen source attributes: officiality,
  reliability, independent-source count, corroboration, and syndication.
- Convert EIA and BEA observations into point-in-time structured release
  packets and distinct v15 Ridge features without pre-judging usefulness.
- Retrain all five models under the complete v15 contract.
- Verify and switch the generation as one complete set.
- Remove superseded v14 runtime and transition code after verification.

## Acceptance Boundary

Immutable historical records remain available for audit. Active runtime code
must not mix v14 and v15 model members.

## Current Live Gate

The v15 implementation does not fabricate retroactive evidence. A complete
generation may activate after 30 point-in-time Broad exposures. When fewer than
30 Official exposures exist, the Official residual is an explicit zero-effect
cold-start artifact and `Full` remains equivalent to Market-only for that
component. Broad residual, Broad Full, and News Only continue to learn from
qualified Broad events. No activated generation may silently omit identities.

The implementation audit also found that 527 of 532 retained items came from
Google News or GDELT discovery lanes, while direct official sources contributed
only five items. Historical direct-source collectors still used keyword
prefilters from the initial implementation. The handover therefore repairs
source intake before the v15 evidence clock is allowed to justify activation.
