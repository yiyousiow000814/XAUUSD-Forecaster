# Assistant Security Boundary Contract

## Purpose

This contract separates public research reads, private human Assistant use, and
machine synchronization. It supplements
[`HOSTING_BOUNDARIES.md`](HOSTING_BOUNDARIES.md) and
[`PREVIEW_ISOLATION.md`](PREVIEW_ISOLATION.md).

## Surface classes

### Public research

The dashboard, news reader, Daily Brief display, and public evidence MAY remain
anonymous read-only surfaces. Public access never implies permission to submit
model-consuming work.

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

The bounded News Q&A MVP verifies `Cf-Access-Jwt-Assertion` on the server. It
MUST validate the RS256 signature against the configured team JWKS, issuer,
audience, expiry, application-token type, non-empty subject, and configured
owner membership. Merely receiving an identity-looking header is never
sufficient. The persisted actor identity is `cloudflare-access:<subject>`;
email may match deployment membership policy but is never persisted as object
ownership.

Production activation requires runtime configuration outside source control:

- `CF_ACCESS_TEAM_DOMAIN` identifies the Access team issuer and JWKS endpoint;
- `CF_ACCESS_AUD` declares one or more accepted Access application audiences;
- `ASSISTANT_OWNER_SUBJECTS` and/or `ASSISTANT_OWNER_EMAILS` declares the
  current `OWNER` membership; and
- `INGEST_TOKEN` remains the independent machine identity.

The Cloudflare Access application and policy MUST be provisioned before the
private UI is enabled for use. Missing or malformed configuration fails closed;
it does not fall back to an anonymous queue, a browser credential, or the
machine ingest token.

### Machine synchronization

The local Windows synchronizer and other services use a machine or service
identity. Machine routes MUST NOT rely on a human browser cookie, and human
Assistant routes MUST NOT accept the ingest token as a user identity. Machine
and human authorization are separate policies even when they share hosting.

## Ownership enforcement

Every conversation, message, title change, queue item, stream, and memory read
MUST be scoped to its authenticated owner. Object identifiers alone are not
authorization. List, get, update, archive, regenerate, and stream operations all
perform the same owner check.

Authentication and authorization occur again on the server for every request;
frontend state is not an authority. Error responses MUST not reveal whether a
conversation belonging to another actor exists.

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
