# ADR-004: Authenticate Private Assistant Use at the Server Edge

- Status: Accepted
- Date: 2026-08-15

## Context

The research dashboard may remain public, but Assistant requests spend model
quota and create owner-specific state. UI hiding, CAPTCHA, IP throttling, or a
global queue cap cannot establish who is authorized. Machine synchronization
also needs credentials that must not become human session authority.

## Decision

Require verified human identity and owner authorization on every
model-consuming or conversation-mutating Assistant endpoint before parsing,
queueing, storage access, or model admission. Prefer hosting-provided edge
identity over a custom password system.

Use a separate machine/service identity for synchronization. Persist a stable
Forecaster `actor_id`; email is an attribute, not ownership. The initial role
model may contain only `OWNER`.

## Consequences

- Anonymous visitors cannot consume Assistant quota or occupy its queue.
- Human and machine credentials cannot substitute for each other.
- Every object lookup requires owner scope, not only an unguessable ID.
- Hosting identity still needs an explicit owner/membership policy.

## Rejected alternatives

- Anonymous Assistant access with rate limiting only.
- A frontend-only access check.
- Using `INGEST_TOKEN` as a human identity.
- Building password, signup, and reset flows without a product need.

## Related authority

- [`ASSISTANT_SECURITY.md`](../contracts/ASSISTANT_SECURITY.md)
- [`HOSTING_BOUNDARIES.md`](../contracts/HOSTING_BOUNDARIES.md)
