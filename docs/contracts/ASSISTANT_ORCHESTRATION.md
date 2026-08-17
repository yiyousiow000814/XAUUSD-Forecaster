# Assistant Orchestration Contract

## Purpose

This contract defines the bounded path from an authenticated user turn to model
selection, capacity admission, tool execution, evidence validation, and a final
answer. It extends the existing metered model gateway and news evidence
boundaries; it does not grant trading authority.

## Separate policy stages

The following decisions MUST remain separate:

```text
User task
  -> Reasoning Policy
  -> Model Router
  -> candidate ModelProfile(s)
  -> Capacity Router
  -> CredentialPool x model
  -> metered Model Gateway
```

Reasoning Policy classifies the work and chooses a permitted effort profile.
The Model Router chooses models that can satisfy the task contract. The Capacity
Router chooses usable provider capacity for those models. A credential MUST NOT
implicitly decide task semantics.

The first reasoning router SHOULD be deterministic and request-local. It MUST
NOT spend another model request merely to ask how much reasoning to use. Initial
classes MAY include `SIMPLE`, `ANALYTICAL`, and `TOOL_HEAVY`.

One user turn fixes this task/effort policy before its first model request; a
later final-only model call may disable tools but MUST NOT silently downgrade
the effort policy while synthesizing results from the same native sequence.

Every model-consuming Assistant result MUST retain a versioned routing receipt
that identifies the task type, reasoning class, requested thinking level,
model requirement, bounded input/output estimate, declared candidate profiles,
and selected profile/model. The receipt records the decision made before
transport; it MUST NOT contain credentials. A no-model result such as a fixed
insufficient-evidence answer MUST NOT invent a routing receipt.

## Model profiles and task policy

A model is described by operational data equivalent to:

```text
ModelProfile
- model_id
- provider
- context_limit
- supports_thinking
- supports_function_calling
- supports_streaming
- capacity_class
- enabled
```

Conversation storage MUST NOT depend on these fields. A task policy MAY prefer
a larger model and permit a declared smaller fallback. A task marked as
requiring a particular model class MUST fail or defer honestly when that class
is unavailable; it MUST NOT silently downgrade.

For example, title generation is a low-complexity background task and MAY use a
smaller permitted model with minimal reasoning and tiny context. Conflicting
macro evidence, causal analysis, multi-period comparison, and multi-source
synthesis MAY require a larger analytical profile. Model names and context
limits remain configuration, not conversation schema.

Operational model-profile configuration MAY enable multiple sizes. The router
MUST filter disabled profiles, insufficient context limits, missing thinking
support for high-effort work, and missing function-calling support for a tool
plan before transport. A simple task may use a declared smaller-to-larger
fallback. Analytical or tool-heavy work marked `LARGE_REQUIRED` MUST expose an
unavailable result when no compatible large profile exists rather than silently
downgrading.

## Credential pools and capacity

Credentials are represented by server-side references equivalent to:

```text
CredentialPool
- id
- provider
- credential_ref
- enabled
- health
```

Conversation rows MUST NOT contain `CredentialPool` ownership. Capacity is
tracked at `CredentialPool x model`, because one provider account may expose
different limits and health for different models.

Admission MUST consider at least:

- rolling input tokens per minute;
- rolling requests per minute;
- provider-day request use;
- estimated request tokens;
- in-flight work, failures, throttles, cooldown, and health; and
- configurable headroom for estimation error and retries.

Provider TPM, RPM, RPD, reset windows, and soft-cap ratios are operational
configuration. Current provider values MUST NOT be frozen into this contract.
The provider console remains authoritative; repository settings are conservative
local safety limits. Account grouping follows
[`AI_PROVIDER_QUOTAS.md`](../AI_PROVIDER_QUOTAS.md).

The Capacity Router SHOULD try another compatible credential pool, a declared
fallback model, smaller retrieval, safe compaction, deferral, or a bounded queue
before graceful rejection. It MUST protect soft capacity before relying on a
provider `429` as flow control.

The current operational boundary is `assistant-capacity-v1`. It expands the
already-fixed model candidate plan into bounded pool/model pairs, ranks usable
headroom, and durably reserves request, token, and in-flight capacity before the
metered gateway may send. A reservation has a finite lease so a stopped worker
cannot strand in-flight capacity. Provider throttles and repeated transport
failures update pair-specific health and cooldown state.

For a durable chat turn, the worker MUST prove that its publication lease is
still renewable immediately before each provider attempt. That gate runs before
capacity reservation, so a stale or cancelled worker cannot consume a model
reservation and can never extend its lease past the turn expiry.

Each model-consuming routing receipt MUST include a validated capacity receipt
with service priority, an anonymous pool fingerprint, pool class, bounded
candidate/attempt counts, admission estimate, applied soft cap, and whether a
declared model fallback was used. It MUST NOT contain an account ID, API key,
credential reference, or secret. Persistent conversation state remains usable
when a later turn selects a different pool.

## Unified metered gateway

Every model-generating request MUST cross a shared server-side gateway that:

1. identifies purpose, model, and credential pool;
2. obtains or conservatively bounds input tokens;
3. durably reserves applicable capacity before transport;
4. sends the provider request without exposing credentials;
5. validates the response contract; and
6. records model, prompt, usage, timing, and failure provenance.

Feature code MUST NOT call provider generation endpoints directly. Title,
compaction, daily brief, Q&A, tool planning, and final-answer requests all use
the same accounting boundary even when they have different priorities.

Interactive `ASSISTANT_CHAT` runs on the loopback-only local provider boundary.
Its only declared profile is the Assistant-only Qwen 3.5 4B Q4_K_M alias with
an actual 262,144-token Ollama context. The 24 GB host does not advertise a
second chat profile: hardware tests found that a 256K Ministral 8B cache spills
to CPU, while retaining multiple resident chat weights reduces predictable
headroom for the embedding service. The local pool therefore has one in-flight
admission slot and finite model residency. Interactive chat and incremental
compaction share this profile; compaction is admitted only as background work
and uses native schema-constrained, non-thinking output. The profile is
unavailable to news annotation, Daily Brief, title, and legacy Q&A work. Those
workloads retain their separately configured provider routes.

The existing news scheduler design is documented in
[`AI_PRIORITY_SCHEDULER.md`](../design/AI_PRIORITY_SCHEDULER.md). Reusing it does
not mean current news routes already implement the Assistant router.

## Priority and admission

Interactive authenticated turns have a declared service priority. Daily brief,
title generation, compaction refresh, and memory indexing are lower-priority or
preemptible background work. Background work MUST defer before consuming the
headroom reserved for current semantic-pipeline health and interactive turns.
The current Google account registry treats `PREEMPTIBLE` pools as interactive-
eligible reserved capacity and permits `ROUTINE` pools to serve background work
or interactive overflow. A capacity-only deferral releases the remote work
lease with bounded backoff without sending a model request. Deferrals remain
inside the finite orchestration-attempt and task-expiry budgets.

Admission MUST occur before queue creation or model transport where the relevant
identity, payload, or capacity condition is already known. Retry and queue
limits are enforced per actor and globally.

Worker claims for a versioned derived-data queue MUST declare the exact
generation they implement. The server returns no work when that generation is
missing or differs; an older worker may never consume a newer migration's jobs.
Failed cutover work is recovered by rolling to a new immutable generation. The
failed generation and its attempt history remain unchanged; replacement jobs
use distinct identities and only the matching worker generation may claim them.

## Bounded tool loop

Every user turn has configured finite budgets equivalent to:

```text
MAX_MODEL_TURNS_PER_USER_TURN
MAX_TOOL_CALLS_PER_USER_TURN
MAX_PARALLEL_TOOL_CALLS
MAX_TOOL_RESULT_TOKENS
MAX_RETRIEVED_EVIDENCE
MAX_ACTIVE_CONTEXT_TOKENS
MAX_OUTPUT_TOKENS
```

The architecture does not freeze their initial numeric values, but no value may
be unbounded. An infinite or open-ended agent loop is forbidden.

The target loop is:

```text
model plans zero or more typed tool calls
  -> backend authorizes and executes calls
  -> backend validates and compacts results
  -> model receives bounded results
  -> optional bounded second tool round
  -> final answer
```

When calls are independent, one model turn SHOULD plan them together and the
backend SHOULD execute them in parallel within the configured limit. Tool
results remain ordered deterministically in the context. A tool failure is a
typed result; it MUST NOT be hidden as an empty success.

The backend MUST reject an over-budget call plan before executing any subset.

Every native `functionCall` MUST retain its exact provider call ID, and every
`functionResponse` MUST match that ID. Opaque provider thought signatures are
preserved only inside the ephemeral native request sequence; they are neither
canonical Assistant state nor user-visible reasoning. Each model turn crosses
the Model Router, Capacity Router, and metered gateway. The selected model is
fixed for one native sequence while an eligible credential pool may change;
later user turns may select a different compatible model.

The final synthesis request MUST omit provider tool declarations rather than
relying on a provider function-calling mode flag to enforce the loop boundary.
It MUST constrain the response to the claim-level evidence JSON envelope and
the backend MUST still validate that envelope, its citation budget, and every
evidence ID before publishing an answer.

The operational Windows worker builds the canonical owner-scoped context first,
registers the shared `search_news_v1` adapter, and then runs this loop. The news
adapter fixes `received_to` to the admitted turn cutoff and rejects a fallback
or mismatched evidence receipt. Model credentials remain inside the capacity
router and metered gateway; neither the tool request nor D1 completion receives
raw credential material.

Every tool has a versioned input/output schema, authorization policy, timeout,
result bound, and provenance contract. The initial Assistant registry is
read-only. No order-placement, broker-control, model-promotion, or autonomous
trading tool may be registered.

Registered executors are controlled application adapters, not arbitrary plugin
code. Each adapter MUST honor the supplied deadline and apply its own network or
storage timeout. The orchestrator stops waiting at the deadline and reports a
typed timeout, but it does not claim that a Python thread can forcibly terminate
arbitrary non-cooperative code.

## Shared news retrieval

Search UI, Q&A, the future Assistant, and future news tools MUST use one shared
news retrieval service rather than parallel query logic. At minimum it supports:

- normalized Chinese and multi-token queries;
- headline, source, emerging topic, and impact-reason fields;
- published-time and received-time ranges;
- evidence ID and optional source/category filters;
- bounded result count and page size; and
- deterministic ordering with a stable tie-break key.

Escaping for `%`, `_`, backslash, and provider query syntax MUST be correct.
Failure to reach the authoritative store MUST return an explicit unavailable or
valid labeled Preview-fallback result. A recent-news slice is not retrieval.
Keyword and metadata matching MUST NOT be described as semantic or vector
search.

## Compact evidence packets

Tool results sent to a model MUST omit irrelevant raw fields and oversized
bodies. A default news packet contains only bounded fields such as:

```text
evidence_id
published_at
received_at
source
headline
summary
category
impact
```

The tool response records query, filters, ordering, cutoff, result limit, source
mode, and the canonical IDs returned. Complete immutable publisher content
remains in the evidence store governed by
[`NEWS_EVIDENCE.md`](NEWS_EVIDENCE.md); compaction does not alter it.
The server, not the model, fixes the received-time cutoff for an Assistant turn.

## Evidence-grounded answers

An evidence-grounded answer MUST satisfy all of the following:

- every cited evidence ID came from the retrieved packet for that turn;
- evidence-required claims have at least one validated citation;
- answer and evidence counts are bounded;
- model, prompt, retrieval, and time provenance are persisted; and
- no evidence produces an honest `INSUFFICIENT_EVIDENCE` result rather than a
  model guess.

Unknown or fabricated IDs are rejected, not silently accepted. Filtering an
invented ID out of an otherwise unsupported answer is insufficient validation.
The system MUST NOT claim claim-level factual entailment unless a separate,
documented validator actually proves it.

Current Q&A and native Agent finals use `assistant.evidence.v1`. The model must
return an exact `claims` array; each item contains one bounded single-line claim
and its evidence IDs. The canonical answer is the claims joined by `LF`, so no
unmapped free text can enter the persisted answer. When a current tool or
retrieval packet contains citable evidence, every claim must cite at least one
ID from that exact packet. An unknown ID, an uncited claim, a duplicate ID, an
extra envelope field, or a count violation rejects the complete final.

The deterministic validation receipt records:

- protocol and validator versions;
- validation mode, claim count, and citation count;
- ordered available and actually cited evidence IDs;
- each claim's line index, text SHA-256, and cited IDs;
- canonical answer and canonical receipt SHA-256 values; and
- `coverage_complete` plus `entailment_status: NOT_VERIFIED`.

Python and the Cloudflare boundary share fixtures for the exact receipt. The
Cloudflare boundary reconstructs it from the canonical answer and authoritative
retrieval or tool receipts instead of trusting worker-supplied validation.
`CITATION_COVERAGE` means only that every persisted claim has a structurally
valid citation. It does not mean that the cited source semantically entails the
claim. `NO_CITABLE_EVIDENCE` explicitly marks a general chat answer with no
current citable packet; Q&A instead publishes the fixed
`INSUFFICIENT_EVIDENCE` result. Model, prompt, routing, retrieval cutoff, and
completion timestamps remain in the surrounding immutable provenance.
The v3 handover does not keep a permanent v2 execution path: migration `0016`
preserves completed v2 rows as audit evidence and terminalizes still-active v2
jobs with `PROMPT_VERSION_SUPERSEDED` so callers can submit a current request.

Daily Brief and Q&A are display and decision-support outputs. They MUST remain
excluded from forecasting training and MUST NOT change Champion, Shadow, or
`WAIT` policy.
