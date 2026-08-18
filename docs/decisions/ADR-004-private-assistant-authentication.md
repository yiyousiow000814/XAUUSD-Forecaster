# ADR-004: Authenticate Private Dashboard Operator Use at the Server Edge

- Status: Accepted
- Date: 2026-08-15

## Context

The research dashboard may remain public, but Assistant requests spend model
quota and retry controls mutate scheduler state. Both expose owner-specific or
operational evidence. UI hiding, CAPTCHA, IP throttling, or a
global queue cap cannot establish who is authorized. Machine synchronization
also needs credentials that must not become human session authority.

## Decision

Require one verified Dashboard Operator identity and owner authorization on
every privileged human endpoint. Assistant and System retry controls reuse the
same Cloudflare Access application session, JWT verifier, stable actor identity,
and owner allowlist. Prefer hosting-provided edge identity over a custom
password or section-specific session system.

Use a separate machine/service identity for synchronization. Persist a stable
Forecaster `actor_id`; email is an attribute, not ownership. The initial role
model may contain only `OWNER`.

## Consequences

- Anonymous visitors cannot consume Assistant quota or occupy its queue.
- Anonymous visitors cannot read retry failure/audit evidence or mutate the
  Windows scheduler.
- Logging in through one privileged Dashboard section covers the others.
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
