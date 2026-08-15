# ADR-005: Deliver Versioned Events and Structured Content

- Status: Accepted
- Date: 2026-08-15

## Context

A single Markdown string cannot reliably express tool progress, news evidence,
tables, metrics, or responsive cards. Allowing a model to emit arbitrary HTML
would transfer security, accessibility, and mobile-layout control to untrusted
output. Coupling streaming chunks to stored messages would also make a transport
change require a conversation migration.

## Decision

Keep canonical messages independent from a versioned streaming event protocol.
Persist a completed validated message; use ordered events for progress, deltas,
tool states, completion, errors, and cancellation.

Represent rich output as a validated, bounded set of typed content blocks.
Frontend code owns rendering. Models cannot emit arbitrary HTML, scripts,
styles, or component names.

## Consequences

- Streaming can evolve without replacing message storage.
- Progress reflects real backend events and exposes no private chain-of-thought.
- Desktop and mobile renderers can adapt the same structured data safely.
- Unknown or invalid blocks fail safely instead of entering the DOM.

## Rejected alternatives

- Store raw stream deltas as the only message state.
- One giant Markdown/HTML response for every surface.
- Arbitrary model-generated HTML or frontend component execution.
- An extra model call used only to narrate fake progress.

## Related authority

- [`ASSISTANT_BEHAVIOR.md`](../specs/ASSISTANT_BEHAVIOR.md)
- [`ASSISTANT_STATE.md`](../contracts/ASSISTANT_STATE.md)
