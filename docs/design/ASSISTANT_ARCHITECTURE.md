# Assistant Architecture

## Document role

This design is the entry point for the target Assistant architecture. It
explains components and data flow; it does not claim that target components are
implemented.

Normative authority is split by responsibility:

- canonical conversations, memory, and compaction:
  [`ASSISTANT_STATE.md`](../contracts/ASSISTANT_STATE.md);
- reasoning, model/capacity routing, tools, retrieval, and evidence:
  [`ASSISTANT_ORCHESTRATION.md`](../contracts/ASSISTANT_ORCHESTRATION.md);
- human, machine, credential, and Preview boundaries:
  [`ASSISTANT_SECURITY.md`](../contracts/ASSISTANT_SECURITY.md); and
- titles, ordering, queue states, streaming, progress, and content blocks:
  [`ASSISTANT_BEHAVIOR.md`](../specs/ASSISTANT_BEHAVIOR.md).

Current implementation truth is tracked in
[`ASSISTANT_IMPLEMENTATION_STATUS.md`](ASSISTANT_IMPLEMENTATION_STATUS.md).
Future sequencing is a proposal in
[`ASSISTANT_ROADMAP.md`](../plans/ASSISTANT_ROADMAP.md).

Contracts and specifications define required semantics. This design records the
target decomposition. Code is the current implementation of those contracts,
not a hidden alternative source of architecture.

## Normative architecture summary

- Conversation state is provider-, model-, and credential-independent.
- Persistent history is separate from bounded active model context.
- Evidence IDs and provenance survive compaction.
- Model choice and capacity choice are separate decisions.
- All model generation crosses one metered server-side gateway.
- Search, Q&A, and tools share one bounded news retrieval service.
- Every user turn has finite model, tool, context, retrieval, and output budgets.
- Model-consuming user actions require an authorized human identity.
- Human and machine identities are separate.
- Preview is read-only and does not spend production model capacity.
- Streaming events and rich content blocks do not own canonical message state.
- No Assistant component has trading, order, or promotion authority.

## Target architecture

```text
Authenticated User
        |
        v
Chat Frontend
        |
        v
Assistant Orchestrator
        |
        +--> Conversation Manager --> Persistent History
        |             |
        |             v
        |       Memory / Compaction
        |             |
        |             v
        |       Context Builder
        |
        +--> Reasoning Policy --> Model Router --> Capacity Router
                                                   |
                                                   v
                                  Selected Model + Credential Pool
                                                   |
                                                   v
                                        Metered Model Gateway
                                                   |
                                  zero or more typed tool calls
                                                   |
                    +------------------------------+-------------------+
                    |                              |                   |
              News Retrieval                 Market Data          Calendar
                    |                              |                   |
                    +---------- bounded validated results ------------+
                                                   |
                                                   v
                                        Metered Model Gateway
                                                   |
                                                   v
                              Validated Final Message + Provenance
                                                   |
                                                   v
                                Versioned Events + Content Blocks
```

The architecture permits multiple independent tools to execute in parallel
after one bounded planning turn. It does not require a tool call for every
question and does not allow an open-ended agent loop.

## Component responsibilities

| Component | Owns | Must not own |
| --- | --- | --- |
| Chat Frontend | Input, accessible rendering, event consumption | Credentials, provider sessions, authorization decisions |
| Assistant Orchestrator | One bounded authenticated user-turn lifecycle | Canonical history or unlimited retries |
| Conversation Manager | Owner-scoped conversations and messages | Model, provider, or credential affinity |
| Memory / Compaction | Versioned pinned state, summaries, derived memory | Replacement of original history |
| Reasoning Policy | Deterministic task/effort class | Provider capacity |
| Context Builder | Model-specific bounded active context | Unbounded history injection |
| Model Router | Permitted candidate model profiles | API-key selection |
| Capacity Router | Healthy `CredentialPool x model` admission | Conversation identity or task semantics |
| Metered Model Gateway | Accounting, transport, response validation | Feature-specific hidden policy |
| Tool Registry | Typed authorization, execution, bounds, provenance | Arbitrary code or trading authority |
| Streaming Protocol | Ordered progress and answer transport | Canonical conversation storage |
| Content Renderer | Safe responsive block rendering | Arbitrary model HTML |

## One user-turn lifecycle

1. The server authenticates and authorizes the human actor.
2. The Conversation Manager creates or loads owner-scoped canonical state and
   appends the accepted user message.
3. Memory and the Context Builder assemble bounded active context while
   preserving pinned constraints and evidence references.
4. Reasoning Policy selects the task profile. The Model Router selects permitted
   models; the Capacity Router selects usable capacity.
5. The metered gateway performs a bounded model turn. Typed independent tools
   may run in parallel after server-side authorization.
6. Tool results are validated, compacted, and returned for at most the remaining
   model/tool budget.
7. The backend validates the final answer, citations, and content blocks, then
   persists one canonical Assistant message with provenance.
8. The stream completes. Background title or compaction work may follow without
   changing conversation ordering.

Failures append or return an explicit state. No stage invents evidence, exposes
secrets, or silently falls back across a forbidden semantic boundary.

## State and transport separation

These identities are deliberately independent:

| Identity | Purpose |
| --- | --- |
| `conversation_id` | Stable Forecaster-owned user history |
| `message_id` | Canonical accepted user or completed Assistant message |
| `user_turn_id` | One bounded orchestration attempt |
| provider request ID | Transport provenance only |
| model/profile ID | Reproducibility and routing provenance |
| credential-pool ID | Capacity and audit provenance, never a secret |
| streaming event ID/sequence | Ephemeral or replayable delivery order |
| evidence ID | Stable source/evidence audit link |

Changing providers, model sizes, capacity pools, or streaming transports does
not require rewriting conversation storage.

## Existing system reuse

The target extends rather than duplicates these sources of truth:

- the point-in-time and append-only boundaries in
  [`FORWARD_ONLY.md`](../contracts/FORWARD_ONLY.md);
- event identity and model eligibility in
  [`NEWS_EVIDENCE.md`](../contracts/NEWS_EVIDENCE.md);
- the current account-aware AI scheduler in
  [`AI_PRIORITY_SCHEDULER.md`](AI_PRIORITY_SCHEDULER.md);
- provider-account facts in
  [`AI_PROVIDER_QUOTAS.md`](../AI_PROVIDER_QUOTAS.md);
- hosting and secret boundaries in
  [`HOSTING_BOUNDARIES.md`](../contracts/HOSTING_BOUNDARIES.md); and
- branch isolation in
  [`PREVIEW_ISOLATION.md`](../contracts/PREVIEW_ISOLATION.md).

An implementation PR changes those authorities first when its semantics change.
It must not define a competing rule only in a route, component, test, comment,
or pull-request description.

## Explicit non-goals for the architecture PR

This documentation does not implement conversations, memory, compaction,
routing, tools, streaming, or chat UI. It does not modify the open Daily Brief,
Search, or Q&A branches. It establishes shared contracts so later small PRs can
be reviewed against one architecture.
