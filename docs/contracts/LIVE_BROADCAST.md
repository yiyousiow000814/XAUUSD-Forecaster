# Live Broadcast Contract

## Authority and scope

`PUBLIC_LIVE_V2` is delivery state for the public XAUUSD dashboard. It is not
research authority, execution authority, or a replacement for append-only D1
evidence. The Windows Stable runtime is its only publisher. Public browsers are
read-only subscribers.

The isolated `aurum-live-broadcast` Worker and its singleton `LiveHub` Durable
Object own transport and latest-state delivery. `aurum-signal-room` continues to
own the website and `/api/status` fallback. Broadcast failure must not affect
news collection, evidence persistence, or market collection.

## Serialized state

Every full state contains:

- `schema_version = PUBLIC_LIVE_V2`;
- a positive, monotonically increasing integer `sequence`;
- `generated_at`, `source_revision`, and `market_session`;
- bounded online/freshness, quote and health summaries; and
- authoritative quote receipt time so clients compute age locally.

The UTF-8 JSON representation must be at most 16,384 bytes. At most four public alerts may be present. Forecasts, recent decisions, feature vectors,
prediction internals, quotas, annotation queues, routing, credentials, admin
state, diagnostics, news archives, learning history, and market history are
forbidden.

## Authentication and mutation

Only `POST /publish` mutates delivery state. The Worker verifies a bearer token
from `LIVE_BROADCAST_PUBLISH_TOKEN` with digest-based constant-time semantics
before reading or parsing the body. The token must never enter a URL, log,
database, Durable Object record, or response. Browser WebSockets have no publish
permission; an application message closes the subscriber connection.
The Windows publisher uses one code-owned `aurum-live-broadcast` origin; runtime
input cannot redirect the credential to another host.

An authenticated `?dry_run=true` publish validates the complete contract and
size but performs zero storage writes and zero broadcasts. Preview and pre-activation
production-shape rehearsals may use only this path or a dedicated
local/test service. They must never write production `LiveHub`.

## Latest-state and sequence semantics

`LiveHub` persists only the latest full state and minimal sequence metadata in
SQLite-backed Durable Object storage. It rejects duplicate or stale sequences.
On connection it sends `FULL_STATE`. Normal publishes send a bounded
`STATE_UPDATE` containing the complete bounded quote and health state. A continuity
gap forces reconnection and a new full state. There is no broadcast event ledger
or per-client queue.

Subscribers are accepted with the Hibernation API. Fan-out uses the sockets
returned by the Durable Object context and performs no D1 or SQLite read per
subscriber. Failed sockets are closed and obsolete states are never queued.

Forecast and decision fields are forbidden in V2. The stable singleton identity
is retained so its sequence authority survives upgrades. Stored states from an
incompatible schema are never sent to subscribers; the first valid V2 publish
replaces the old payload and sends a full state. Restart retains monotonic
sequence rejection and cannot expose the retired model payload.

## Browser fallback

One transport exists per browser tab/app runtime. Every app runtime completes
exactly one full `/api/status` baseline request before opening the WebSocket,
including when `FULL_STATE` would otherwise arrive immediately. `LIVE_PUSH`
then suppresses normal 15-second `/api/status` polling. Initial timeout,
connection failure, or stale push state enables bounded HTTP fallback while
reconnect continues with exponential backoff and jitter. Recovery to a fresh
full state stops recurring fallback polling. Quote age advances from
`source_received_time` on the browser clock without network requests. The three
public modes are `LIVE_PUSH`, `HTTP_FALLBACK`, and `STALE`.
The browser treats 75 seconds without a valid update as stale; post-activation
Release Control allows a 90-second freshness margin for the 30-second publisher.

Heavy and user-specific resources remain independent APIs. Static assets remain
outside normal Worker routing.

## Release boundary

Production activation follows [Release Control](RELEASE_CONTROL.md). Protected
main is the production source. Verify the pinned isolated service authority,
binding, compatible schema, and authenticated zero-mutation dry-run before
publisher activation. Missing configuration or external availability remains
retryable for the same revision. Acceptance requires a real latest
`PUBLIC_LIVE_V2` state from the deployed revision, published within 90 seconds.
Working HTTP fallback proves resilience, not live broadcast readiness.

For V2 activation, deploy the receiver before enabling the matching publisher.
An old publisher is rejected by the V2 receiver; a new publisher rejects V1
health during reconciliation. Browsers retain HTTP fallback until a compatible
full state arrives. No schema compatibility path translates retired forecasts.
Verify a stored V2 state after activation to confirm replacement of the old
Durable Object payload. This isolated service update requires its own authorized
release; a website build does not deploy it.
