# Dashboard Operator and Assistant Security Boundary Contract

## Purpose

This contract defines one reusable human Dashboard Operator boundary and
separates it from public research reads and machine synchronization. Assistant
and System mutations do not own separate login systems. It supplements
[`HOSTING_BOUNDARIES.md`](HOSTING_BOUNDARIES.md) and
[`PREVIEW_ISOLATION.md`](PREVIEW_ISOLATION.md).

## Surface classes

### Public research

The dashboard, news reader, Daily Brief display, and public evidence MAY remain
anonymous read-only surfaces. Public access never implies permission to submit
model-consuming work.

Retry job failure text, scheduler metadata, and operator command history are
private operational evidence. `/api/operator-retry` GET and POST both require
the shared owner-authenticated Dashboard Operator identity even though the
System Health page itself remains publicly readable. Public payloads never
include credentials, lease tokens, provider requests, or operator audit rows.

### Private Dashboard Operator

Cloudflare Access establishes one browser session for every privileged human
Dashboard surface. Assistant conversation routes, News Q&A, and System retry
reads or mutations all call the same server-side verifier and produce the same
stable `cloudflare-access:<subject>` actor. A login initiated from either
Assistant or System therefore authorizes the other section without another
application login. Future privileged human tools must reuse this boundary.

Identity is not permission by itself. Every request validates the Access
application JWT and then applies the configured owner allowlist. Only `OWNER`
may mutate scheduler state. Browser state, visible controls, and an email header
are never authorization.

### Private Assistant

Every endpoint that can create an Assistant message, enqueue Assistant work,
invoke a model, regenerate a title, or mutate conversation state MUST require a
verified human identity before payload parsing, queue creation, storage access,
or model admission.

Hiding a control, CAPTCHA, IP rate limiting, or a global pending cap is not
authentication. Edge identity such as Cloudflare Access or an equivalent
hosting-provided identity is preferred over building a password database,
signup flow, password reset flow, or custom session framework.

ChatGPT Sites identity headers MAY establish a user identity on that hosting
surface, but identity alone does not prove owner membership. The server MUST
apply the configured owner or membership policy before granting model-consuming
access. Initial authorization MAY use one `OWNER` role; a speculative RBAC
system is not required.

Application persistence uses a stable `actor_id` and owner scope. Email and
display name are attributes, not authorization keys or schema ownership.

### Current Cloudflare Access profile

The Dashboard Operator verifier checks `Cf-Access-Jwt-Assertion` on the server. It
MUST validate the RS256 signature against the configured team JWKS, issuer,
audience, expiry, application-token type, non-empty subject, and configured
owner membership. Merely receiving an identity-looking header is never
sufficient. The persisted actor identity is `cloudflare-access:<subject>`;
email may match deployment membership policy but is never persisted as object
ownership.

Production activation requires runtime configuration outside source control:

- `CF_ACCESS_TEAM_DOMAIN` identifies the Access team issuer and JWKS endpoint;
- `CF_ACCESS_AUD` declares one or more accepted Access application audiences;
- `DASHBOARD_OPERATOR_OWNER_SUBJECTS` and/or
  `DASHBOARD_OPERATOR_OWNER_EMAILS` declares the current `OWNER` membership;
  legacy `ASSISTANT_OWNER_*` values are a bounded cutover fallback only when
  neither shared allowlist is configured; and
- `INGEST_TOKEN` remains the independent machine identity.

One Cloudflare Access application and owner-only policy MUST cover every human
privileged path. Its cookie path restriction remains disabled so its protected
paths share one application session. Google or another configured Access IdP
may provide the login experience; application authorization still uses the
owner allowlist. Missing or malformed configuration fails closed;
it does not fall back to an anonymous queue, a browser credential, or the
machine ingest token.

The shared Access application protects `/assistant`, `/api/assistant-chat`,
`/api/assistant-conversations`, and `/api/news-questions`. Those application
paths, plus `/api/operator-retry`, are the human boundary. `/api/assistant-worker/*` is deliberately outside
the Access application because it has no browser identity and is authorized by
the independent machine policy below.

### Local operator bridge

The Windows sync process is the only client of the local retry scheduler bridge.
Both `/api/retry-jobs` and `/api/retry-overrides` require loopback origin plus a
dedicated high-entropy `DASHBOARD_OPERATOR_BRIDGE_TOKEN` carried in the
`X-Aurum-Operator-Bridge-Token` header. The mutation route additionally rejects
browser-origin requests, non-JSON content, and unbounded bodies before opening
a writable scheduler connection. The token is never accepted in a URL, logged,
returned to the browser, reused as `INGEST_TOKEN`, or persisted in evidence.
Missing or invalid configuration fails closed.

### Machine synchronization

The local Windows synchronizer and other services use a machine or service
identity. Machine routes MUST NOT rely on a human browser cookie, and human
Assistant routes MUST NOT accept the ingest token as a user identity. Machine
and human authorization are separate policies even when they share hosting.

Human send, cancel, turn-read, SSE-replay, conversation, and News Q&A requests
exist only on the Access-protected human routes. Machine claim, context,
progress, completion, failure, and capacity-defer requests exist only under
`/api/assistant-worker/*` and require `INGEST_TOKEN`. Every mutation after a
claim must also present the exact unexpired lease token. Lease renewal requires
the same token and cannot extend past the turn expiry. Human routes do not
accept machine modes, and machine routes do not accept Access as a substitute
for `INGEST_TOKEN`.

## Ownership enforcement

Every conversation, message, title change, queue item, stream, and memory read
MUST be scoped to its authenticated owner. Object identifiers alone are not
authorization. List, get, update, archive, regenerate, and stream operations all
perform the same owner check.

Authentication and authorization occur again on the server for every request;
frontend state is not an authority. Error responses MUST not reveal whether a
conversation belonging to another actor exists.

Event replay is finite and owner-scoped on every request. An object ID or
`Last-Event-ID` never grants access, and reconnecting does not weaken the owner
check. Machine completion provenance is strict JSON, bounded, tied to the exact
conversation and user message, checked against routing/tool receipts, and
rejected if it contains credential-like fields or an invalid run hash.

## Credential secrecy

Model credentials and server-side credential references MUST NOT enter:

- browser JavaScript or rendered HTML;
- conversation or message content;
- D1 public payloads or Preview bundles;
- model prompts, tool results, error bodies, logs, or analytics; or
- source control.

The browser never selects an API key or provider account. It submits an
authenticated task; server-side model and capacity routers select a permitted
model and credential pool. Persisted attempts use non-secret pool IDs or key
fingerprints only.

## Preview boundary

Branch Previews remain read-only and non-authoritative. They MUST NOT create
production conversations or queue items, spend production model capacity, or
use production model credentials. Assistant write and model-consuming routes in
a Preview reject before authentication, parsing, storage, or transport.

A Preview MAY render explicit synthetic fixtures or an immutable build snapshot
for UI review. Such data must be labeled as Preview data and cannot be presented
as a live conversation or proof that a model path works.

## Abuse and capacity controls

Authentication precedes queue admission. Implementations enforce bounded
request size, per-owner concurrency, per-owner rate limits, global in-flight
capacity, bounded retries, and terminal queue states. Anonymous traffic cannot
occupy the private Assistant queue.

Security controls fail closed. Missing identity, ambiguous membership, missing
secret configuration, unavailable ownership storage, or invalid request
provenance denies the model-consuming operation.

## Product authority

The Assistant is decision support only. It has no broker credentials, order
execution, automatic Champion promotion, hidden autonomous trading, or ability
to modify the forecasting policy. Its tools and responses cannot override
[`PRODUCT.md`](../specs/PRODUCT.md) or
[`SYSTEM_BOUNDARIES.md`](SYSTEM_BOUNDARIES.md).
