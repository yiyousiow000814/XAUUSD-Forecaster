# Shared Live Broadcast Design

## Data path

The existing dashboard mirror heartbeat remains approximately 30 seconds and
keeps audit, learning, news, and history cadences independent. A future explicit
Windows cutover projects one compact `PUBLIC_LIVE_V1` state and publishes it to
the isolated `aurum-live-broadcast` Worker. `LiveHub` stores one latest snapshot
and fans it out through hibernating WebSockets. Browsers merge that state into
the shared `/api/status` cache used by all public views.

This service is separate because adding a first Durable Object lifecycle to
`aurum-signal-room` would couple a one-time platform mutation to the existing
immutable Candidate flow. The website can therefore continue its normal
`wrangler versions upload` and Promote process, while broadcast bootstrap is a
coordinated one-time operation in its own control plane.

## Lifecycle configuration decision

The service declares `LiveHub` with a `new_sqlite_classes` migration. The newer
top-level Durable Object `exports` form was deliberately not selected. Current
Wrangler documentation says an initial namespace can be declared by `exports`
or by migrations, but exports and migrations are mutually exclusive; current
Versions documentation also says `wrangler versions upload` fails when Durable
Object exports are present and lifecycle changes require `wrangler deploy`.

The migration is applied only by the explicit initial bootstrap. Later code-only
versions may use the service's version workflow without creating a new lifecycle
change; Wrangler 4.123.0 accepts this unchanged migration configuration in a
local `versions upload --dry-run`. Any future class rename, deletion, or storage migration is another
coordinated platform operation, not an ordinary Candidate upload.

References:

- [Durable Object lifecycle](https://developers.cloudflare.com/durable-objects/reference/durable-objects-migrations/)
- [Workers versions and deployments](https://developers.cloudflare.com/workers/versions-and-deployments/)
- [Hibernating WebSockets](https://developers.cloudflare.com/durable-objects/best-practices/websockets/)

## Cost and failure shape

Healthy operation is roughly one connection request per client connection
lifetime plus one authenticated publish per changed server state. Fan-out does
not perform a database read per client. It replaces the previous shape of every
visible client polling status every 15 seconds.

The browser still has a bounded HTTP fallback. Broadcast failure produces no
blank page and has no collector or decision-loop impact. Reversing the website
to an older Stable is safe because old code ignores the service. The namespace
and latest state remain intact during rollback.

## Deployment state

This design adds code and an operator runbook only. The publisher is not wired
into Windows startup, no secret is configured, no service exists as a result of
the change, and no Durable Object namespace is provisioned by repository tests.
