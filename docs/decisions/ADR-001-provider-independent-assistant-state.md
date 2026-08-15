# ADR-001: Forecaster Owns Provider-Independent Assistant State

- Status: Accepted
- Date: 2026-08-15

## Context

Provider sessions, model versions, API keys, and quota pools can change between
turns. Binding a conversation to any of them would make continuity, failover,
and audit behavior depend on external transport state.

## Decision

XAUUSD Forecaster owns canonical conversations and messages under a stable
Forecaster actor. Provider request IDs, model profiles, and credential-pool IDs
are per-turn provenance only.

Model routing selects a permitted model. Capacity routing separately selects a
healthy `CredentialPool x model`. A conversation can move between 31B, 26B, a
future model, or another provider without storage migration, provided task
policy permits the route.

## Consequences

- Provider or credential failover does not fork history.
- Conversation schema contains no secret or quota ownership.
- Reproduction persists model and capacity provenance per turn.
- The application must build provider requests from canonical state instead of
  relying on opaque provider conversation sessions.

## Rejected alternatives

- One conversation per API key or Google account.
- One provider session as the canonical history.
- A combined `if key then model` routing branch that mixes semantics and quota.

## Related authority

- [`ASSISTANT_STATE.md`](../contracts/ASSISTANT_STATE.md)
- [`ASSISTANT_ORCHESTRATION.md`](../contracts/ASSISTANT_ORCHESTRATION.md)
