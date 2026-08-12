# PR 34: Canonical Events And Model Handover

Status: implementation complete; live activation is evidence-gated.

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
- Retrain all five models under the complete v15 contract.
- Verify and switch the generation as one complete set.
- Remove superseded v14 runtime and transition code after verification.

## Acceptance Boundary

Immutable historical records remain available for audit. Active runtime code
must not mix v14 and v15 model members.

## Current Live Gate

The v15 implementation is ready, but a production generation is not fabricated
from retroactive evidence. As of the implementation audit, point-in-time mature
rows contain zero official and zero Broad v15 exposures because the historical
articles were reviewed only after their original decision times. Activation
therefore remains blocked at the frozen minimum of 30 exposed rows in each lane
until fresh v15 events mature.

The implementation audit also found that 527 of 532 retained items came from
Google News or GDELT discovery lanes, while direct official sources contributed
only five items. Historical direct-source collectors still used keyword
prefilters from the initial implementation. The handover therefore repairs
source intake before the v15 evidence clock is allowed to justify activation.
