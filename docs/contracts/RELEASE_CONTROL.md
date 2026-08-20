# Release Control Contract

## Release identity and authority

A release binds one exact Git SHA, one immutable Cloudflare Worker Version ID,
one Windows runtime revision, a compatibility decision, and validation evidence
keyed by Worker Version ID plus Git SHA. Git, a Worker Version, Candidate, and
Stable are distinct states.

Git push, pull-request merge, and `main` movement MUST NOT change Stable.
Workers Builds may build and upload immutable Versions, but MUST NOT assign
production traffic. A valid new Version stages Candidate only. Stable changes
only when the local operator explicitly confirms **Promote Candidate** in the
existing Control Center. **Reverse Stable** is the normal rollback action.

The operator surface exposes only Stable, Candidate, and Previous Stable. A new
Candidate may replace the Candidate pointer, but MUST NOT inherit validation
from another Worker Version ID or Git SHA. FAILED Candidate state never changes
Stable.

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

Worker acceptance queries Cloudflare Workers Observability for the exact Worker
Version ID and records invocation count, maximum and p99 CPU, maximum wall time,
`exceededCpu`, and 5xx counts. Directed route success without this platform
record is not sufficient. Missing observability authority leaves Candidate in
TESTING; any `exceededCpu` or 5xx fails that Candidate. The read-only API token
is a protected Windows user secret and is never serialized into release state.

An automatic compatibility decision covers only a release without storage
migrations. A changed D1 or other migration remains `REVIEW_REQUIRED` until a
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

Restart during PROMOTING, OBSERVING, or REVERSING reconciles observed Worker
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

Candidate at 0% MUST NOT own background, scheduled, queue, or other duplicate
production side effects. Directed Version Override requests are the only normal
Candidate Worker traffic; this project does not use random percentage canaries.

Release mutation is local operator control, never a public HTTP endpoint.
Cloudflare credentials stay in user-scoped authenticated tooling or protected
secret storage and MUST NOT enter Git, command output, logs, UI payloads,
SQLite/D1 evidence, or pull-request comments.
