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

The UTF-8 JSON representation must be at most 16,384 bytes. At most 18 compact
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

The public forecast projection uses the dashboard's exact `research_forecast`
fields: `model_identity`, `model_version`, `recommended_action`,
`prediction_status`, `ev_long_u5`, `ev_short_u5`, `interval_width`,
`decision_time`, `signal_expiry_seconds`, `forecast_horizon_seconds`,
`directional_bias`, and `frozen_record`. `recommended_action` remains exactly
`LONG`, `SHORT`, or `WAIT`.

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

A main Candidate that introduces the broadcast client first proves the pinned
isolated service authority, binding, compatible revision, and authenticated
zero-mutation dry-run. Missing external configuration or platform availability
is retryable for the same immutable Candidate. After website promotion and
matching Windows publisher activation, Release Control requires a real latest
`PUBLIC_LIVE_V1` state from the exact revision, published within 90 seconds.
Working HTTP fallback proves resilience, not live broadcast readiness.
