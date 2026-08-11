# PR 33: AI Priority And Key Scheduling

Status: placeholder; implementation has not started.

## Scope

- Separate routine and preemptible API-key pools.
- Let AI semantic review assign urgency; do not infer urgency from headline keywords.
- Allow every available key to help when the urgent queue is backlogged.
- Process impact review before title translation and background work.
- Track quotas per independent account.
- Persist queued work with leases, idempotent writes, backoff, and recovery.

## Acceptance Boundary

This PR changes scheduling only. It must not activate the v15 news contract or
switch a model generation.
