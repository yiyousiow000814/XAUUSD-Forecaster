# Release Control Contract

## Release identity and authority

A release binds one exact Git SHA, one immutable Cloudflare Worker Version ID,
one Windows runtime revision, a compatibility decision, and validation evidence
keyed by Worker Version ID plus Git SHA. Git, a Worker Version, Candidate, and
Stable are distinct states.

Every uploaded Worker Version declares one durable artifact kind in its
immutable version annotation. `PREVIEW` is never promotable. Only
`PRODUCTION_CANDIDATE` may enter Candidate validation or promotion. Missing or
unknown provenance fails closed. A production candidate must also declare
`main`, exist after fetch, and be reachable from `origin/main`; artifact labels
alone are not authority. Preview evidence never authorizes a production candidate, even when
both artifacts originate from the same Git commit.

Git push, pull-request merge, and `main` movement MUST NOT change Stable.
Workers Builds may build and upload immutable Versions, but MUST NOT assign
production traffic. A valid new Version stages Candidate only. Stable changes
only when the local operator explicitly confirms **Promote Candidate** in the
existing Control Center. **Reverse Stable** is the normal rollback action.

The operator surface exposes only Stable, Candidate, and Previous Stable. A new
Candidate may replace the Candidate pointer, but MUST NOT inherit validation
from another Worker Version ID or Git SHA. FAILED Candidate state never changes
Stable.

Candidate discovery owns a durable monotonic `(version created_at, version_id)`
watermark. Initialization consumes all versions already present without making
them eligible. Every later version advances the watermark whether it is Preview,
unknown, malformed, accepted, or failed. Restart therefore cannot rediscover
historical or failed candidates. A production candidate arriving during a
transaction is queued until that transaction finishes.

## Windows ownership and validation

Exactly one Windows production owner may run collector, trainer, annotator,
decision, retry-consumer, and dashboard-sync side effects. Candidate Windows
code is STAGED or TESTING only. Its preflight uses an isolated checkout,
SQLite online backup or copied evidence, isolated outputs, and disabled,
mocked, or replayed provider and network side effects. Candidate MUST NOT claim
production jobs, fetch production news as a second collector, emit production
decisions, write production SQLite or D1, or consume production retry commands.

Candidate detection is automatic. Required gates are selected from the changed
boundaries and include repository checks, isolated Windows preflight, startup
viability, ownership uniqueness, compatibility, directed 0% Worker probes, and
actual Cloudflare CPU/error evidence when Worker execution changed. PASSED means
every required gate belongs to the exact release key.

Stable and Candidate dashboard inspection uses exact Worker version overrides
against the same production data authority. Promotion requires semantic parity
for the bounded status, audit, learning, and market projections; a route read
failure or material mismatch is `SEMANTIC_DATA_PARITY_REVIEW_REQUIRED`, never a
pass inferred from HTTP availability alone. A versioned `workers.dev` URL is
not the Cloudflare Access boundary and is recorded as
`AUTH_BOUNDARY_NOT_TESTABLE` unless an unauthenticated exact-version probe on
the protected production hostname proves the boundary. Validation never
simulates or claims a successful human login.

Every parity receipt records the Worker version observed in response headers.
Generated-at serialization differences are ignored, while status/quote and
decision cadence, audit transitions, and unexpectedly empty learning/market
datasets fail with machine-readable route reasons. The Candidate browser page
labels its versioned `workers.dev` surface as unprotected, removes the login
action, and directs operators to validate login only at the formal Access host.

The first migration from an explicitly recorded `LEGACY_BOOTSTRAP_STABLE`
uses a narrow compatibility receipt because that Worker predates exact identity
headers and split Audit routes. Current deployment evidence must prove the
recorded legacy Worker owns 100% traffic, the exact Candidate owns 0%, and the
recorded Windows bootstrap identity is still active. The Candidate remains
subject to exact Worker and Git headers. Its bounded split Audit resources are
validated directly against the legacy `/api/audit` freshness authority. Any
later Stable uses normal exact-version validation; missing headers or routes do
not infer legacy compatibility.

The local graphical shell is presentation only. WPF/XAML is the normal Windows
surface and the XAML file participates in the exact revision/hash control
bundle; WinForms remains a compatibility fallback. Both invoke the same
PowerShell release engine and neither owns alternate promote or reverse rules.

Repository validation requires the exact-SHA check runs named `Python regression
suite`, `Web build and tests`, `Windows runtime contracts`, `Repository policy`,
and CodeQL `Analyze` jobs for actions, C#, JavaScript/TypeScript, and Python.
Every named run must exist, be complete, and conclude successfully. Missing
required runs remain PENDING; unrelated optional runs cannot substitute.

Worker validation is planned from `web/worker-validation-manifest.json`, the
authoritative inventory of route method, hosting boundary, criticality,
read/write ownership, Windows transport producers, validation strategy, fixture owner, CPU requirement, and
authentication requirement. CI fails when an App Router handler or a direct
route in any `web/worker/*.ts` sibling lacks policy. Changed-file selection is
derived from manifest ownership; a shared router selects every affected family,
while documentation-only changes select no Worker CPU work.
Package, lockfile, build configuration, shared runtime, Worker entrypoint, and
fixture-builder changes fail safe into baseline CPU validation. Every HEAVY or
CRITICAL Worker route is sampled unless it is a Static Asset; an OPTIONAL
contract-only route has a machine-readable exemption policy. Multi-operation
writes declare fixtures for each materially distinct processing path.

`/`, `/health`, `/audit`, and the favicon are Static Assets: each must return
its canonical identity and the validation window must contain zero candidate
Worker invocations. Worker reads are directed to the exact 0% Version. Affected
authenticated writes use deterministic fixtures built by the production
dashboard transport builders. Their dry-run follows normal authentication,
bounded body read, decode, parse, validation, and CPU-heavy transformation. It
then runs the applicable read-only D1 JSON1/set validation and stops immediately
before the first authoritative schema, upsert, batch, delete, or queue mutation.
Fixture files are generated as UTF-8 and transmitted as the exact byte array.
Their bounds come from the current manifest contract rather than historical
payload sizes. The response must state `mutated: false`; an invalid body still fails the normal
contract. The validation header never bypasses authentication. Every Worker
probe has a unique request ID and one validation-run ID.
Evidence records the exact Version, short window, route family, request IDs, and
exact expected Worker invocation count. Static requests are excluded. If the
platform cannot filter request IDs, exact Version, short-window, and exact-count
isolation is mandatory; noise fails the gate.

Worker-changing candidates first receive excluded warm-up requests. Acceptance
then records at least ten platform samples for every selected hot path and at
least fifty samples overall through the baseline route set. Observability keeps
both global and route-family sample count, maximum, p95, and p99 CPU, maximum
wall time, `exceededCpu`, `exceededMemory`/1102, and 5xx counts. A failed route
family fails the whole Candidate even when the global aggregate appears safe.
The Free-plan CPU gate
is `PASSED` only with the exact invocation count, zero failures, p95 at most
6 ms, p99 at most 8 ms, and maximum below 10 ms. Zero failures with CPU still
within 10 ms but without that headroom is `REVIEW_REQUIRED`. Count contamination,
any failure, p99 above 10 ms, or a sample above 10 ms is `FAILED`; p99 18 ms can
never pass. Missing observability authority leaves Candidate in TESTING. The
read-only API token is protected and never serialized into release state.

PR #268 acceptance is retained only as labeled legacy manual evidence: 104
samples, p50 2 ms, p95 4 ms, p99 4 ms, maximum 5 ms, and zero exceeded CPU,
1102, or 5xx. Its source did not record a bootstrap timestamp; release control
does not invent one or reinterpret older measurements. It is a
`LEGACY_REFERENCE` with `REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED`, never a
promotable Candidate. The pre-control-plane Stable is an exact Worker/Windows
rollback pair labeled `LEGACY_BOOTSTRAP_STABLE`; its unrecorded Worker Git SHA
remains `NOT_RECORDED`.

An automatic compatibility decision covers only a release without storage or
binding changes. A changed D1 or other migration, Wrangler binding, or platform
resource remains `REVIEW_REQUIRED` until a
separate coordinated migration protocol proves Stable-to-Candidate and Reverse
compatibility. A green repository suite alone cannot make that decision.

## Promotion transaction

Promotion is one durable, serialized transaction:

`PRECHECK -> CUTOVER -> OBSERVING -> COMMIT_STABLE`

PRECHECK verifies exact identities and evidence, compatibility, the current
Stable placement, and one Windows production owner. CUTOVER uses the recorded
compatibility order to switch the matching Windows and Worker identities.
The dashboard synchronizer is paused at the boundary, the matching Windows
revision is activated without sync, Worker traffic is switched, and sync is
resumed only after both identities match. This bounds the mixed-contract window
without creating a second production owner.
OBSERVING reuses the existing full decision-cycle observation and rollback
policy. COMMIT_STABLE records the prior Stable as Previous Stable only after
observation succeeds. A newly discovered Candidate during a transaction is
queued and cannot alter the in-flight target.

Failed PRECHECK, CUTOVER, observation, or automatic rollback leaves the
pre-transaction Previous Stable pointer unchanged. Only successful
`COMMIT_STABLE` advances Previous Stable.

Reverse uses `REVERSING -> REVERSE_OBSERVING -> READY` and commits the restored
Stable identity only after the same owner, heartbeat, API, sync, critical-status,
and decision-cadence observation succeeds. Restart during PROMOTING, OBSERVING,
REVERSING, or REVERSE_OBSERVING reconciles observed Worker
traffic and Windows runtime identity with the durable transaction. Unexplained
mismatch is `DEPLOYMENT_DRIFT` or `RECOVERY_REQUIRED`; it MUST NOT silently
start another transaction.

The transaction lock records its process owner. A live owner is never
preempted. An abandoned lock may be removed only after the recorded process no
longer exists (or an incomplete owner record has exceeded its grace period);
the durable transaction and append-only history remain intact for reconciliation.

## Storage, compatibility, and security

D1 and SQLite evidence are not code-version rollback targets. Promote and
Reverse MUST NOT delete evidence, truncate SQLite, rewrite historical
predictions, or destructively roll back schema. Candidate migrations are
backward-compatible and additive across Stable-to-Candidate and Reverse unless
an explicit coordinated migration protocol governs the transition.

The independently installed `.local/runtime-control` bundle owns deployment
transactions. Business checkout, Promote, Reverse, and automatic observation
rollback never copy control files. Their preflight verifies the active bundle's
exact source revision and every recorded SHA-256 hash. Release diagnostics keep
`control_bundle_revision`, `control_bundle_exact_revision`, and
`control_bundle_hash_verified` separately from the Windows business revision.

Control Plane installation is a separate local transaction, not a Business
Runtime Promote. The repository entry point MUST resolve one exact revision
equal to the fetched `origin/main`, stage it from a clean detached Git worktree,
and verify the complete bundle before stopping supervision. The handoff order is
`PRECHECK -> QUIESCE_CONTROL_SUPERVISION -> STOP_OLD_WATCHDOG -> INSTALL_BUNDLE
-> START_NEW_WATCHDOG -> VERIFY_NEW_HEARTBEAT -> COMMITTED`. The new heartbeat
MUST identify a different process-start token, the target bundle revision, and
successful exact/hash verification while exactly one watchdog owns supervision.

The transaction MUST preserve the Business Runtime revision and every quote,
collector, annotator, API, and sync process identity. An active release
transaction or open Control Center GUI blocks installation. Failure after the
old watchdog stops restores the previous complete verified bundle, starts a new
process from that previous bundle, verifies its heartbeat and single ownership,
and records `ROLLED_BACK`. Current bounded evidence is stored in
`.local/forward/control-plane-install-state.json`; it never rewrites release
history or Stable/Candidate identities. The operator procedure is
[`CONTROL_PLANE_INSTALLATION.md`](../runbooks/CONTROL_PLANE_INSTALLATION.md).

Candidate at 0% MUST NOT own background, scheduled, queue, or other duplicate
production side effects. Directed Version Override requests are the only normal
Candidate Worker traffic; this project does not use random percentage canaries.

Release mutation is local operator control, never a public HTTP endpoint.
Cloudflare credentials stay in user-scoped authenticated tooling or protected
secret storage and MUST NOT enter Git, command output, logs, UI payloads,
SQLite/D1 evidence, or pull-request comments.
