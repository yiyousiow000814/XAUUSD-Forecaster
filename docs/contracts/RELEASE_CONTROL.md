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
`main` and exist after fetch. It normally equals the exact current
`origin/main`; artifact labels alone are not authority. An already-validated
immutable Candidate may remain eligible after a descendant `main` movement only
when every intervening file is in the explicit Control Plane, formal, test, CI,
or documentation ownership set and no Worker or Windows business-runtime
artifact changed. The active Control Plane must still be the exact hash-verified
new `main`. Any unclassified or product-runtime change restores the exact-main
requirement and fails closed. Preview evidence never authorizes a production
candidate, even when both artifacts originate from the same Git commit.

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

Release Control records materialization for the exact current `origin/main` as
`PENDING`, `MATERIALIZED`, or `PRESERVED`. Only a matching immutable Worker
Version may make it `MATERIALIZED`. `PRESERVED` means an existing immutable
Candidate remains authoritative across a proven Control-Plane-only descendant
main movement; a later upload for that descendant must advance discovery but
must not supersede or discard its independent evidence. If an unvalidated
replacement was selected before that proof ran, recovery may restore the exact
superseded Candidate only from authoritative history, with an exact replacement
key, no active transaction, no accepted replacement work, exact provider
identity, intact persisted validation artifacts, and the same provenance proof.
The unvalidated replacement may itself be a Control-Plane-only ancestor of the
newest main (including the recovery correction's own merge), but it must pass
that same explicit provenance proof independently; arbitrary stale replacement
revisions remain ineligible.
An older main build that completes out of order advances the discovery watermark
but cannot replace, validate as, or supersede the current main Candidate. A
missing exact Version remains visible and retryable without changing Stable.

## Windows ownership and validation

Exactly one Windows production owner may run collector, trainer, annotator,
decision, retry-consumer, and dashboard-sync side effects. Candidate Windows
code is STAGED or TESTING only. Its preflight uses an isolated checkout,
SQLite online backup or copied evidence, isolated outputs, and disabled,
mocked, or replayed provider and network side effects. Candidate MUST NOT claim
production jobs, fetch production news as a second collector, emit production
decisions, write production SQLite or D1, or consume production retry commands.

The Business Runtime checkout owns immutable executable code, while its explicit
runtime state root owns mutable production state. Control Center MUST pass that
state authority to quote, collector, annotator, API, Sync, and broadcast launch
paths. SQLite databases, heartbeats, cursors, schedules, checkpoints, logs, and
operational receipts remain under `RuntimeRoot/.local/forward`; their location
MUST NOT be inferred from a Python module path, current working directory,
repository checkout, or Candidate staging checkout. Static code and intentional
repository configuration or secret authorities remain separate. Moving or
revising the code checkout cannot redirect mutable state, and Switch, hidden
watchdog recovery, Observe, and Reverse use the same service launch contract.
An older installation whose runtime `.local` is a junction to the repository
must be migrated through the explicit state-only Control Center operation before
a new Business Runtime is installed. That operation fences release entry,
verifies the exact installed Control Plane, sole watchdog, Stable revision, and
service-owner baseline, quiesces the watchdog and services, and then replaces
only a junction whose exact target is the authorized repository `.local`. It
restarts and health-checks the same frozen Stable checkout before restoring
supervision; it MUST NOT move Git, change Worker traffic, or change release
state. The migration lock is outside the junction being replaced, and every
release transaction entry fails closed while it exists. Unknown link targets,
concurrent release state, revision changes, missing owners, or failed fresh
service evidence fail closed.

Every Windows service launch contract is owned by the Business Runtime revision
being launched. Current revisions carry a versioned manifest; an explicitly
identified pre-manifest Stable may use a narrow exact-revision adapter. Before
quiescing services or changing the checkout, Control Center captures a
digest-protected recovery plan containing the exact Stable revision and Worker,
running service set, per-service launch contract, process baseline, runtime and
config authorities, and rollback target. Switch, Reverse, migration failure,
hidden-watchdog recovery, and observation rollback restore from that captured
old authority, never from Candidate CLI syntax. Runtime-state migration also
requires bounded, exact process and filesystem-handle quiescence before the
first move. The controller captures revision-owned service roots and their child
identities, waits on those exact process handles, and inventories file,
directory, and process-current-directory handles under the state tree. An
external holder fails before watchdog or service suspension. The only automatic
repair exception is a set made exclusively of Explorer directory handles whose
paths are contained by the exact migration tree: the controller closes only
matching shell windows, rechecks the native inventory, and may restart only the
verified Explorer shell when no file operation is active. Any other holder, an
identity change, or an active Explorer file operation remains fail-closed.
Effective delete/delete-child access and a reversible rename of the real state
directory must pass. A separate preflight executes the
same real-path quiesce, rename, Stable recovery, owner, and health contract
without moving state or changing release evidence; migration is not authorized
until that preflight passes.
For a running pre-manifest quote bridge, its external CLI and secret-root
authorities must be resolved, verified, and frozen into the recovery contract
before quiescence; an implicit checkout-local fallback is not recoverable
authority. Recovery is not complete at process creation: the exact captured
owner set and fresh service heartbeat/API health must pass before the failed
transaction can clear or supervision can resume.

Candidate detection is automatic. Required gates are selected from the changed
boundaries and include repository checks, isolated Windows preflight, startup
viability, ownership uniqueness, compatibility, directed 0% Worker probes, and
actual Cloudflare CPU/error evidence when Worker execution changed. PASSED means
every required gate belongs to the exact release key.

An isolated preflight may observe a live decision after its durable decision row
is appended but before the complete model family is appended. Its explicit
pending-generation mode treats that partial latest boundary like a not-yet-made
boundary. Normal production-shape validation remains fail-closed for the same
partial family, and pending mode still rejects mismatched versions or duplicate
model identities that are already present.

Stable and Candidate dashboard inspection uses exact Worker version overrides
against the same production data authority. Promotion requires semantic parity
for the bounded status, audit, learning, and market projections; a route read
failure or material mismatch is `SEMANTIC_DATA_PARITY_REVIEW_REQUIRED`, never a
pass inferred from HTTP availability alone. A versioned `workers.dev` URL is
not the Cloudflare Access boundary and is recorded as
`AUTH_BOUNDARY_NOT_TESTABLE` unless an unauthenticated exact-version probe on
the protected production hostname proves the boundary. Validation never
simulates or claims a successful human login.

Coordinated migration acceptance is immutable qualification for the exact
Candidate, Stable, database, migration files, and accepted CURRENT/Reverse
boundary. Its root receipt is stored by digest and is never overwritten. The
two-hour expiry applies only to the live migration observation. When that lease
expires, Release Control may write a separate immutable
`MIGRATION_QUALIFICATION_RENEWED` receipt only after the same bounded read-only
migration verification proves the exact Candidate and Stable identities,
authoritative D1 identity, RuntimeRoot, migration artifacts and ledger, CURRENT
generation, legacy Reverse projection, and single Stable production owner are
unchanged. Renewal links the root acceptance and prior renewal digest; it never
claims or executes another migration. A changed identity, generation, receipt
chain, invariant, migration lock, transaction, or production owner fails closed
without invalidating unrelated qualification evidence.

When the protected-host login flow cannot be exercised by Release Control, the
Candidate first enters `ACCESS_BOUNDARY_REVIEW_REQUIRED`. A versioned Access
qualification key owns only the protected origin and destinations, provider
application and policy fingerprint, Access-sensitive repository artifacts, and
the acceptance-contract version. A prior untampered six-check human receipt may
be reused only when authenticated read-only provider inspection covers the
interval since that acceptance, reports no application or policy change, no
Access failure was recorded, and the prior and current qualification keys are
identical. Reuse writes a separate immutable
`ACCESS_QUALIFICATION_REUSED` machine receipt; it never writes or impersonates a
human receipt. Machine evidence keeps a two-hour freshness TTL. Expiry renews
only the machine observation: Release Control reads the exact Access application,
policy, and identity-provider configuration and exhausts the account audit-log
cursor over the continuous interval beginning at the previous successful
inspection boundary. Zero behavior-affecting changes, zero relevant failures,
an unchanged qualification key and provider fingerprint, an intact immutable
machine chain, and the original verified human root permit a new immutable
`ACCESS_QUALIFICATION_RENEWED` receipt. The prior receipt is never overwritten.
Automatic renewal limits one audit interval to 30 days, well inside the
provider's published 18-month audit-log retention boundary, and fails closed
instead of reading more than ten 1,000-entry pages.
Incomplete retention coverage, incomplete pagination, change followed by
reversion, configuration drift, broken chain or changed Access artifact requires
human review. Unreadable or tampered evidence fails closed.

When reuse is unavailable, an operator may use the single
`ApproveAccessBoundary` transition only after personally verifying owner login
and owner-resource access, unauthorized denial, logout, denial after logout,
and successful reauthentication on the displayed protected host. That human
transition retains its time-bounded, SHA-256-protected exact-Candidate receipt.
Both paths preserve accepted migration, directed-validation, parity, and CPU
evidence. Unrelated repository, CPU, Windows, storage, CI, or formal-model
movement cannot invalidate Access qualification. WPF and WinForms invoke the
same PowerShell transition; neither UI performs authentication.
The formal protected origin is the canonical production Worker origin,
`https://aurum-signal-room.yiyousiow1234.workers.dev`. Its `/admin*` routes are
owned by the production Cloudflare Access application. Immutable Version hosts
remain unprotected validation surfaces, and the separately configurable runtime
dashboard URL cannot redefine this Access authority.

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
subject to exact Worker and Git headers. Split Audit resources that the legacy
Windows producer cannot publish are not reported as ordinary parity passes.
Their pre-promotion receipts are `DEFERRED_TO_POST_CUTOVER_OBSERVATION`, bound to
the exact validation key and required Candidate producer revision. All resources
the legacy producer can prove still require normal semantic parity. Any
later Stable uses normal exact-version validation; missing headers or routes do
not infer legacy compatibility.

The local graphical shell is presentation only. WPF/XAML is the normal Windows
surface and the XAML file participates in the exact revision/hash control
bundle; WinForms remains a compatibility fallback. Both invoke the same
PowerShell release engine and neither owns alternate promote or reverse rules.
WinForms fallback is permitted only when WPF fails before its first successful
`ContentRendered` event. After that event, action, child-process, refresh, and
timer failures remain contained in the same WPF owner. GUI action children must
execute the installed, hash-verified control script at the exact bundle revision
captured by their parent GUI; a path or revision mismatch fails closed.

Every GUI operation child writes one `control-center-operation-v1` result and
then exits explicitly: semantic success is exit `0`, while a pre-commit failure
is nonzero with a bounded diagnostic. Ambient native exit state, formatting,
cleanup, and stdout/stderr content never determine the operation outcome.
Presentation consumes the structured result and refreshes release state. If the
result transport fails after an exact approval receipt and matching approval
history were committed, the UI reports the authoritative commit instead of a
false operation failure. A missing result without authoritative commit evidence
is indeterminate, never inferred as success or failure from empty output alone.

Repository validation requires the exact-SHA check runs named `Python regression
suite`, `Web build and tests`, `Windows runtime contracts`, `Repository policy`,
`Release Control TLC`,
and CodeQL `Analyze` jobs for actions, C#, JavaScript/TypeScript, and Python.
For each required name, only runs whose `head_sha` equals the Candidate Git SHA
are eligible, and the latest exact-SHA attempt is authoritative. Latest-attempt
ordering is deterministic by start time and then run ID. An older failed or
cancelled attempt cannot poison a newer successful rerun; an older success
cannot authorize promotion while a newer attempt is in progress or has failed.
Unrelated optional runs cannot substitute.

The external required-check gate has three outcomes. `PENDING` means a required
exact-SHA run is missing or its latest attempt is incomplete, and is represented
by Candidate state `CHECKS_PENDING`. `CHECKS_BLOCKED` means the latest attempt
for a required exact-SHA run completed without success. `PASSED` means every
required name's latest exact-SHA attempt completed successfully.
`CHECKS_PENDING` and `CHECKS_BLOCKED` are fail-closed, non-promotable, and
retryable for the same immutable Worker Version ID plus Git SHA. A later GitHub
rerun for that exact SHA may re-enter validation without creating a new
Candidate identity, and an already-passed isolated Windows preflight is retained.
These external states do not convert deterministic provenance, compatibility,
directed Worker, CPU, parity, transaction, or observation failures into
retryable states; those retain their existing terminal or operator-review
semantics.

Transient external repository or GitHub transport failures leave the same exact
Candidate in retryable `CHECKS_PENDING`, fail closed for promotion, and retain
already-passed isolated Windows preflight evidence for that validation key.
Recovery appends history before validation continues on the same Worker Version
ID and Git SHA. Authentication, authorization, malformed identity, invalid ref,
missing commit, and main-reachability failures remain deterministic and never
become retryable merely because a transport retry path exists.

Required CI is repository trust evidence, not deployed Stable runtime debt.
Progressive delivery never converts a red latest exact-SHA check into Class C.
If a repository test represents a known production defect, its fixture must
model that defect explicitly while continuing to prove the intended contract;
Release Control must not accept a failing test run as unchanged Stable debt.

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

`/`, `/health`, `/audit`, and the favicon are Static Assets. Validation reads
their raw, bounded response bytes from the Candidate's immutable Version preview
hostname, never from the Stable alias. The manifest defines each asset's media
type, encoding, HTML charset declaration, and semantic marker; a mismatch fails
with a bounded receipt containing the exact predicate and response hash, never
the body. Redirects are disabled unless the manifest declares one exact path;
declared redirects must remain on the immutable Candidate host. The validation
window must contain zero candidate Worker invocations.
Worker reads are directed to the exact 0% Version. Affected
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
### Worker CPU qualification and provider uncertainty

Worker request execution and Worker CPU measurement have separate authorities.
Before any directed request is sent, Release Control persists one
`CONTROLLED_EXACT` ledger containing the validation run, Candidate Version,
Worker qualification key, plan and fixture digests, and every request ID with
family, scenario, phase, sample kind, and planned time. Each direct response adds
its exact observed Version and Git identity, HTTP status, bounded semantic
result, `mutated` value, response digest, and completion time. This ledger proves
which requests completed; Cloudflare logs do not.

Cloudflare raw invocation telemetry is `EXTERNAL_AUTHORITATIVE_EVENTUAL`.
Returned CPU, wall, status, outcome, event, request, run, and Version values are
authoritative, but delivery completeness and latency are not internal
invariants. Provider evidence is persisted as a monotonic union keyed by run,
request, event, and Version. Pagination retains exact frozen bounds. Later
shorter or reordered results cannot erase accepted events; duplicate identities
with identical values deduplicate, while conflicting events, multiple events for
one request, unknown requests, or identity contamination fail closed. A
calculation-query count is `EXTERNAL_ADVISORY`: it corroborates the same
run/Version/window, cannot invent per-family samples, cannot override a raw hard
failure, and a contradiction fails closed.

`worker-cpu-policy-v2` applies independently to every required family/scenario:

- two warmups are excluded;
- ten observed acceptance samples remain required;
- two reserve requests are sent once;
- after normal provider recovery reaches a stable plateau, one versioned
  deficit-repair round may freeze and target every deficient family/scenario;
- the frozen round permits at most four deficient groups and four requests per
  group, for an absolute maximum of sixteen targeted requests;
- one headroom-review family may receive one ten-request targeted top-up;
- one successful CPU observation at or above 10 ms enters
  `CPU_OUTLIER_REVIEW_REQUIRED` instead of becoming an immediate deterministic
  failure when its HTTP response succeeded, its provider outcome is `ok`, and
  no resource failure was observed;
- that exact outlier may freeze one ten-request, same-family/scenario/request-
  shape confirmation plan; the plan binds the validation run, Candidate,
  qualification key, original event and request, and every confirmation request
  ID before sending;
- all observed valid acceptance events, including reserve and top-up events,
  remain in the raw metrics and evidence; no favorable reserve or top-up subset
  may be selected;
- no family or scenario may be absent, and missing provider request IDs remain
  explicit.

`QUALIFIED` requires the normal complete observed result. A missing reserve event
may produce `QUALIFIED_WITH_PROVIDER_OMISSION` only when every family still has
at least ten observed samples, every direct response passed exactly, provider
corroboration is non-contradictory, every existing p95/p99 threshold passes, and
each affected family's observed maximum is at most 8 ms. This retains two
milliseconds of headroom to the 10 ms Free-plan hard ceiling; it never treats a
missing required sample as present. A 5xx, 1102, `exceededCpu`,
`exceededMemory`, other applicable provider resource failure, Worker identity
mismatch, or repeated successful CPU observation at or above 10 ms is an
immediate hard failure. One isolated successful CPU outlier can become
`QUALIFIED_WITH_ISOLATED_CPU_OUTLIER` only after its single-use confirmation is
complete, contains no observation at or above 10 ms, and the unchanged quota,
p95 and p99 requirements pass over the confirmed qualification population. The
receipt retains the raw global and family metrics, the original outlier, all
confirmation IDs and metrics, and the exact Candidate identity; the original
observation is never deleted or rewritten. Only after that classification may
the single original outlier be excluded from the separately labeled
qualification distribution; every other original and confirmation sample stays
in that distribution. Confirmation omission remains
non-promotable and cannot start a second round. Global and family qualification
metrics still require p95 at most 6 ms, p99 at most 8 ms, and maximum below 10
ms. A numeric zero CPU value is valid.

Active provider recovery uses six persisted reads with 5, 10, 20, 30, 45, and
60 second backoff. Release Control then performs at most four read-only
background reconciliations, fifteen minutes apart. If quota remains deficient
at a three-read stable plateau, an exact live provider preflight must succeed
before repair. At most three such preflights are permitted, five minutes apart;
provider outage never triggers targeted requests. The controller persists the
exact deficient set, prior counts and digest, qualification key, request IDs,
per-group budget, and total budget before it sends anything. It then sends four
requests only for each frozen deficient group, accumulates all valid old and new
events, and performs no second repair round even if delivery omits top-up events.
The sixteen-request ceiling is derived from the 31-group full CPU manifest: no
more than four groups (12.9%) and 16 requests (4.3% of the original 372
acceptance-request universe) may be repaired. A larger deficient set sends
nothing. Budgets and the exact request ledger survive controller or watchdog
restart. Exhaustion produces stable non-promotable
`PROVIDER_EVIDENCE_INSUFFICIENT`, not Candidate failure and not a human approval
request. Quota satisfaction stops provider queries without waiting for optional
reserve events.

CPU evidence is stored independently from release identity. The versioned
`worker-cpu-qualification-v2` records the exact deployed script ETag as the
per-release Candidate binding. Because the executable embeds Git provenance,
that raw ETag is not itself the reusable CPU behavior key. The reusable key
hashes runtime/compatibility/assets/binding configuration, Worker route and locked
toolchain closure, validation manifest, generated fixture byte digests and
builder closure, CPU policy, D1 schema/capability migrations, and bounded route
data-shape contract. It excludes PowerShell, Control Plane, Windows runtime, and
documentation. The source receipt retains its exact Worker, Git, and ETag, while
the current release records its own exact Worker, Git, and ETag. An immutable
receipt may be reused only on a complete behavior-key match; the current release
still binds that qualification to the exact current Candidate and labels it
`CPU_QUALIFICATION_REUSED`. Fresh measurement is labeled
`CPU_QUALIFICATION_FRESH`; confirmed isolated-outlier qualification is labeled
`CPU_QUALIFICATION_WITH_ISOLATED_CPU_OUTLIER`. Any CPU-affecting mismatch,
including a policy-key mismatch, forces fresh CPU evidence while preserving
independently accepted migration and release stages; an older request plan is
never resumed under the newer policy.

Retry preserves passed migration, directed correctness, parity, and unrelated
family evidence. It resumes unsent preplanned request IDs and provider evidence
from the durable ledger, then targets only a deficient or headroom family. A
full matrix restarts only when the qualification key changes or evidence is
contaminated. The read-only provider token is never serialized into release
state.

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

For an additive D1 migration, **Verify Migration** is the only storage
compatibility acceptance action. It is distinct from the platform-resource
**Approve Compatibility** action. The verifier must bind one short-lived
receipt to the exact Candidate validation key, Git SHA, Worker Version ID,
Stable identity, D1 database UUID, migration filenames and hashes, applied
migration ledger, required schema capabilities, retained legacy read
capabilities, and authoritative projection identity, exact active legacy
identity-set equality, and counts. Count equality is never a substitute for
identity equality. It must prove
the Candidate read path, the still-active Stable read path, and the normal
Reverse target against the same live database. Pending migrations, missing
capabilities, destructive or unknown migration contracts, non-current News,
empty required legacy evidence, stale receipts, receipt tampering, or any live
identity drift fail closed. Candidate validation rechecks the live evidence;
the receipt cannot be copied to another Git SHA or Worker Version.

**Verify Migration** does not stop Dashboard Sync. It binds the receipt to the
CURRENT generation and its activation watermark, then independently revalidates
the live CURRENT, schema, ledger, Stable/Candidate reads, and Reverse projection.
If Stable Sync activates a newer generation during PREPARE or VERIFY, that
advancement is accepted only when its activation watermark is nondecreasing and
the newer CURRENT independently satisfies every receipt, count, identity-set,
and compatibility invariant. Mutation of the recorded generation or regression
to an older generation fails closed. Destructive migrations that genuinely
require downtime need a separate reviewed exceptional contract; they are not
the additive default.

Candidate acceptance blocks regressions rather than requiring unrelated Stable
debt to disappear. Gates have three classes:

- **A — hard safety** always blocks on failure, including exact identity,
  authority, single ownership, security, integrity, migration validity,
  rollback, required critical startup, and real Worker 5xx, 1102, limit, or
  evidence-universe failure.
- **B — change-scoped acceptance** is mandatory when the Candidate changes,
  depends on, or can regress the boundary. The validation manifest's owners and
  producers determine the affected Worker, CPU, semantic, and Access families.
- **C — existing Stable debt** is recorded but does not block when Stable has
  the same unrelated failure, the Candidate does not worsen it, and no A
  invariant is involved. Candidate-only failure, ambiguity, or worsening is a
  regression and blocks.

Two failed Class-C reads are equivalent only when both exact identities pass
and the bounded machine evidence matches: route, HTTP status, stable
machine-readable error code, Worker resource, non-generic failure stage, and a
SHA-256 digest of the bounded JSON response bytes. The response body is never
persisted in release evidence. Missing or untyped reasons, oversized/non-JSON
responses, generic exception/framework/SSR stages, or any fingerprint mismatch
make equivalence unknown and therefore blocking. Candidate authentication,
identity, integrity, corruption, invariant, schema, capability, migration, or
receipt failures remain hard blockers even if Stable reports the same code.

`/api/status` remains a hard critical contract. Other semantic projections use
the same manifest ownership classification as directed Worker validation.
Unchanged debt remains visible in the Candidate receipt; it is never converted
to a pass or used to waive changed-surface acceptance.

## Release lifecycle and switch transaction

The operator lifecycle has four release-attempt phases: `PREPARE`, `VERIFY`,
`SWITCH`, and `OBSERVE`. Discovery, evidence review/retry,
Control Plane handoff, prechecks, and recovery checkpoints are internal
operations, not additional operator phases. `REVIEW_REQUIRED` is a reason
inside `VERIFY`; it is never success. Reverse and automatic return use the same
`SWITCH -> OBSERVE` path with Previous Stable as the target. The authoritative
operator projection is derived by `Get-ReleaseLifecyclePhase`; legacy internal
status and transaction fields remain crash checkpoints and must not be shown as
an alternate lifecycle.

Switch is one durable, serialized transaction. Its internal forward checkpoints
remain `PRECHECK -> CUTOVER -> OBSERVING -> COMMIT_STABLE` for restart recovery,
but they map only to `SWITCH`, `OBSERVE`, and the final Stable transition.

PRECHECK verifies exact identities and evidence, compatibility, the current
Stable placement, and one Windows production owner. CUTOVER uses the recorded
compatibility order to switch the matching Windows and Worker identities.
The dashboard synchronizer continues throughout PREPARE and VERIFY. It is
paused only at the SWITCH boundary, where the matching Windows
revision is activated without sync, Worker traffic is switched, and sync is
resumed only after both identities match. This bounds the mixed-contract window
without creating a second production owner.
OBSERVING reuses the existing full decision-cycle observation and rollback
policy. Deferred split-projection obligations additionally require snapshots
published after the cutover boundary, exact `producer_revision`, exact Worker/Git
response identity, and full production-builder semantic equality. Pending is
bounded and retryable; a hard mismatch or timeout enters automatic rollback.
The parity probe reads the hash-verified persisted audit read model from the
explicit runtime-state root; it does not depend on rebuilding the serving
cache. Projection code is loaded from the explicit producer code root. During
Observe both roots name the activated Windows revision, while their ownership
remains distinct and neither is inferred from the process working directory.
`COMMIT_STABLE` is forbidden until every obligation for the exact validation key
is `PASSED`. It records the prior Stable as Previous Stable only after all
observation succeeds. A newly discovered Candidate during a transaction is
queued and cannot alter the in-flight target.

Failed PRECHECK, CUTOVER, observation, or automatic rollback leaves the
pre-transaction Previous Stable pointer unchanged. Only successful
`COMMIT_STABLE` advances Previous Stable.

An automatic rollback caused solely by a bounded Control Plane deferred-
projection probe timeout records a failed release attempt, not a Candidate
qualification failure. After a Control-Plane-only `main` advance, discovery may
restore the exact saved pre-Switch qualification and re-stage that immutable
Candidate at zero percent only when its validation key, provider artifact,
migration acceptance or renewed live qualification, CPU evidence, Access
receipt, and compatibility remain valid. An expired live migration lease is
renewed from the immutable acceptance through the read-only verification above;
it does not rerun migration or the other Candidate gates. The failed attempt
remains append-only evidence. Any Candidate, data,
identity, receipt, provenance, or resource failure is ineligible for this
recovery and remains fail-closed.

Reverse internally uses `REVERSING -> REVERSE_OBSERVING -> READY` and commits the restored
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
exact source revision and every recorded SHA-256 hash. The current bundle
manifest commits to its schema, exact source revision, exact file count, and
ordinally ordered normalized relative-path/SHA-256 pairs with one repository-owned
UTF-8/LF canonical digest. JSON formatting, host locale, PowerShell version,
filesystem enumeration order, and absolute installation root are not identity
inputs. A versioned schema-2 adapter may verify an existing bundle only by
reconstructing its exact legacy `path=hash` commitment with fixed ordinal order;
new bundles never use that legacy format. Release diagnostics keep
`control_bundle_revision`, `control_bundle_exact_revision`, and
`control_bundle_hash_verified` separately from the Windows business revision.
The Control Plane bundle revision and Business Runtime revision are independent
identities. Promoting or reversing the Business Runtime changes only the
coordinated Worker and Windows business pair; it MUST NOT replace, upgrade, or
imply validation of the installed deployment-control bundle. A Control Plane
bundle change requires its own explicit installation and exact revision/hash
verification.

Control Plane installation is one internal `PREPARE` transaction, not a Business
Runtime Promote. The repository entry point MUST resolve one exact revision
equal to the fetched `origin/main`, stage it from a clean detached Git worktree,
and verify the complete bundle before stopping supervision. The handoff order is
`PRECHECK -> QUIESCE_CONTROL_SUPERVISION -> STOP_OLD_WATCHDOG -> INSTALL_BUNDLE
-> START_NEW_WATCHDOG -> VERIFY_QUIESCED_HANDOFF -> ACTIVATE_NEW_WATCHDOG ->
COMMITTED`. The replacement MUST first acknowledge `QUIESCED`, the exact install
transaction, a different process-start token, the target bundle revision, and
successful exact/hash verification while exactly one watchdog process exists.
It MUST NOT start services, discover Candidates, observe a release, or recover
service ownership before activation is granted. Its `ACTIVE` heartbeat proves
the same exact identity after that grant.
The service-isolation baseline is captured only after control supervision is
quiesced and the old watchdog has stopped. A pre-quiesce snapshot is not an
authoritative baseline because the old watchdog can still recover a service
during immutable bundle staging.

The transaction MUST preserve the Business Runtime revision and every quote,
collector, annotator, API, and sync process identity. Control Plane installation
always requires the normal Stable Sync owner in its authoritative service
baseline; Candidate preparation or migration verification cannot make that
owner optional. An active release transaction or open Control Center GUI blocks
installation. Failure after the
old watchdog stops first compares the recorded process baseline without
re-applying the contextual normal-state rule that caused the failure. It then
restores the previous complete verified bundle, starts a new process from that
bundle, verifies its heartbeat and single ownership, and records `ROLLED_BACK`.
The main scheduled task remains enabled while its current instance is stopped,
so machine restart can launch the exact installed bundle. A matching
non-terminal install resumes the same quiesced handoff. Installer death never
grants activation by itself. The replacement watchdog must independently
re-prove the exact transaction, installed revision and hashes, old-owner fence,
single replacement ownership, transaction-bound quiesced heartbeat,
authoritative service baseline, unchanged release context, and absence of
a concurrent release transaction. Lock ownership includes the exact process
start token so PID reuse cannot impersonate the dead installer. Only that complete proof may
forward-complete activation. Missing or changed evidence restores the previous
verified bundle and recorded safe supervision path. Current bounded evidence is stored in
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
