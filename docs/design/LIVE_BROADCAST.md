# Shared Live Broadcast Design

## Data path

The existing dashboard mirror heartbeat remains approximately 30 seconds and
keeps audit, learning, news, and history cadences independent. An explicit,
separately gated Windows publisher projects one compact `PUBLIC_LIVE_V1` state
at approximately 30-second cadence and publishes it to the isolated
`aurum-live-broadcast` Worker. `LiveHub` stores one latest snapshot
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

## Publisher ownership and recovery

The publisher is an optional Control Center service, not a child of collection,
annotation, API, or dashboard sync. Its states are `DISABLED`, `NOT_CONFIGURED`,
`RUNNING`, and `DEGRADED`; its failure never restarts or degrades a core owner.
It remains inactive until both the explicit activation flag and publisher token
are configured during coordinated cutover.

The next sequence is reconciled from an atomically persisted local acknowledgement
and `LiveHub` health. A rejected stale sequence repairs only that delivery stage
from the returned latest sequence; already-valid source projection is preserved.
The local acknowledgement advances only after a non-dry-run publish is accepted.

## Deployment state

This design wires the isolated owner and Control Center lifecycle but does not
activate it, configure a secret, create a service, or provision a Durable Object
namespace. Repository and Preview tests perform no production mutation.
