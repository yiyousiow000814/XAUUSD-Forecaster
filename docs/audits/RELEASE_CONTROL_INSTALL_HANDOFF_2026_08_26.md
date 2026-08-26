# Release Control Install Handoff Audit — 2026-08-26

This is point-in-time evidence. It does not authorize another install,
migration, Candidate, or Promote attempt.

Before the failed install, Stable remained Git/Windows
`783d25314b090dd7fbbf124777c3b8de517d2b85` and Worker Version
`76d314fc-e484-4f50-8ace-3689e0896709` at 100% placement. The prepared exact-main
identity was Git/Windows `7c9f40015205786f23f35d690c80ddf9bb18e50a`
and Worker Version `9155d7f4-d115-413f-8d02-784c26e1d076`, with
`REVIEW_REQUIRED / COORDINATED_STORAGE_MIGRATION_REQUIRED`. No Promote,
CUTOVER, OBSERVING, or Reverse transaction existed.

The exact target migration hold intentionally left Sync ownerless. During
Control Plane transaction `21a898077d1a4e73a4bbfc5b8adc7371`, the installer
stopped the old watchdog and captured its isolation baseline. It then launched
the replacement watchdog. The replacement entered normal supervision before
handoff acceptance and started Sync (new Sync process `13720`) at approximately
03:20:36 UTC. The installer then observed a Sync owner where its still-captured
migration context required none and rejected the install with
`CONTROL_PLANE_UNEXPECTED_SERVICE_OWNER_SYNC`.

Automatic rollback restored the old bundle and supervisor, but then re-used
the same contextual normal-state owner assertion. Recovery therefore depended
on the condition already violated and also reported failure. A later independent
install committed only after normal supervision had made the observed owner
state internally consistent; that did not invalidate or erase the first
failure.

Stable remained safe because the install path changed only the local Control
Plane bundle and supervision process. It did not mutate the production runtime
checkout or Cloudflare placement. At the freeze baseline there was one watchdog
owner, business services still ran the prior Stable, the exact migration hold
was bounded, and no second production writer or release transaction was found.
