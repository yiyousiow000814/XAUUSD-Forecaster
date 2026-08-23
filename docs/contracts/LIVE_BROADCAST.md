# Live Broadcast Contract

## Authority and scope

`PUBLIC_LIVE_V1` is delivery state for the public XAUUSD dashboard. It is not
research authority, execution authority, or a replacement for append-only D1
evidence. The Windows Stable runtime is its only publisher. Public browsers are
read-only subscribers.

The isolated `aurum-live-broadcast` Worker and its singleton `LiveHub` Durable
Object own transport and latest-state delivery. `aurum-signal-room` continues to
own the website and `/api/status` fallback. Broadcast failure must not affect
collection, decisions, evidence persistence, or trading systems.

## Serialized state

Every full state contains:

- `schema_version = PUBLIC_LIVE_V1`;
- a positive, monotonically increasing integer `sequence`;
- `generated_at`, `source_revision`, and `market_session`;
- bounded online/freshness, quote, forecast, health, and optional recent-decision
  summaries; and
- authoritative quote receipt time so clients compute age locally.

The UTF-8 JSON representation must be at most 16,384 bytes. At most six compact
recent decisions and four public alerts may be present. Feature vectors,
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
size but performs zero storage writes and zero broadcasts. Preview and 0%
Candidate production-shape rehearsals may use only this path or a dedicated
local/test service. They must never write production `LiveHub`.

## Latest-state and sequence semantics

`LiveHub` persists only the latest full state and minimal sequence metadata in
SQLite-backed Durable Object storage. It rejects duplicate or stale sequences.
On connection it sends `FULL_STATE`. Normal publishes send a bounded
`STATE_UPDATE`; an unchanged recent-decision window is omitted. A continuity
gap forces reconnection and a new full state. There is no broadcast event ledger
or per-client queue.

Subscribers are accepted with the Hibernation API. Fan-out uses the sockets
returned by the Durable Object context and performs no D1 or SQLite read per
subscriber. Failed sockets are closed and obsolete states are never queued.

## Browser fallback

One transport exists per browser tab/app runtime. `LIVE_PUSH` suppresses normal
15-second `/api/status` polling. Initial timeout, connection failure, or stale
push state enables bounded HTTP fallback while reconnect continues with
exponential backoff and jitter. Recovery to a fresh full state stops fallback
polling. The three public modes are `LIVE_PUSH`, `HTTP_FALLBACK`, and `STALE`.

Heavy and user-specific resources remain independent APIs. Static assets remain
outside normal Worker routing.

## Release boundary

A main Candidate that introduces the broadcast client cannot claim broadcast
readiness until Release Control records a healthy isolated binding, an available
latest state, `PUBLIC_LIVE_V1`, and an exact Candidate or explicitly recorded
compatible broadcast revision. Working HTTP fallback proves resilience, not
broadcast readiness.
