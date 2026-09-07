# Collector clock atomicity and degraded recovery

## Change contract

The collector owns one clock-event commit, not each persistence helper. Quote,
News and model preparation runs before acquiring the SQLite writer reservation.
The resulting snapshot, decision, prediction families, derived evidence and
collector completion must commit together. A failed commit cannot advance U5 or
the collector cursor. Exact complete replay reads immutable evidence; it does
not rerun inference. Missing required evidence is not a completed clock.

Existing snapshot-only events retain their exact bytes and hash. When historical
prediction inputs were not frozen, recovery appends a bounded repair batch and
LEGACY_ENGINEERING snapshot assignment. It does not manufacture a decision,
prediction, outcome or LIVE_OOS eligibility. Conflicting content or downstream
evidence rejects this recovery route. No table, trigger or uniqueness constraint
is removed or weakened.

## Owners and impact

Collector entrypoint -> ForwardEngine -> prepared market/News/model evidence ->
ForwardLedger transaction -> existing immutable tables -> completion/cursor/U5.
Local SQLite is controlled-exact authority. U5 files are reconstructible runtime
checkpoints, not completion authority. Code comes from the installed Business
Runtime; state stays beneath the authoritative runtime root. Configuration and
secrets keep their existing owners. Provider availability is not a commit fact.

Control Center owns the incident maintenance transition, installed Watchdog
singleton and normal Switch/Observe. The explicitly permitted degraded baseline
has absent Collector and Watchdog, stale receipt, healthy single-owner remaining
services, Stable traffic 100%, and no active install/release transaction. Absence
requires successful identity enumeration; unknown and duplicate are not absent.
No direct bootstrap before the old collector restart/discovery loop is contained.

## Compatibility and recovery matrix

| Combination | Required behavior |
| --- | --- |
| New code / new complete event | Exact replay; no extra rows |
| New code / old snapshot-only event | Preserve and exclude with explicit repair evidence |
| New code / contradictory old event | Reject without mutation |
| Old code / new complete event | Existing schema remains readable |
| Old code / excluded snapshot-only event | May still fail; rollback is degraded, not healthy |
| Dead Watchdog / dead Collector | Verified incident maintenance, never fake healthy |
| Live or unknown old owner | Adopt only proven owner or block takeover |
| Failed candidate runtime | Existing transaction recovery; retain immutable evidence |

## Failure matrix and rehearsal

Preparation failure writes nothing. Process death after snapshot, decision or
partial v2 insertion must roll back the entire clock. Death after commit and
before cursor/checkpoint must replay the committed result, preserving U5
continuity. Writer contention remains BUSY/LOCKED, not an integrity error.
Recovery death either commits the exclusion batch and assignment together or
commits neither; restart is idempotent.

The preserved 6.95 GB online backup is a read-only baseline. Write rehearsals use
an independent copy, never overwrite production. Reproduce the old UNIQUE error,
recover, restart in a new process, replay the same clock, then process a legitimate
later fixture clock. Real WAL/multiple-connection crash tests supplement this.
Staged Windows recovery must include stale ACTIVE receipt, zero Watchdog and
Collector, living other services, exact bundle identities and real service state.
Production requires a fresh read-only owner/transaction/traffic preflight after
staging and exact-head CI. No production recovery has yet been performed.

## Acceptance

### Incident installation and supervision containment

The zero-owner installation must use the existing install transaction and the
existing Watchdog kernel mutex. A bootstrap installer reserves that mutex while
checking a successfully enumerated absent-owner baseline and suspending both
canonical scheduled tasks. It releases the reservation before waiting for the
replacement Watchdog. The prior stale receipt is evidence, not a lock to delete.

The existing install record carries the incident's clock/hash, broken Business
revision, target revision and runtime/repository binding. This is an explicit
maintenance context, not a new receipt family. While the broken revision is
active, Collector startup is held and automatic Candidate discovery is held;
remaining services retain their actual health and supervision. Only the normal
exact-target Switch may start the corrected Collector. Reboot cannot implicitly
release this hold. Heartbeats expose the maintenance context.

Failure before or during zero-owner installation restores the verified old
bundle and captured business owners, but does not start the broken supervisor.
Its safe outcome is the explicitly degraded baseline with scheduled bootstrap
disabled, not a healthy rollback. Successful installation only restores
supervision; it does not grant Candidate qualification or Promote authority.
Normal Promote must independently revalidate this exact incident baseline and
retain full qualification and Observe. The rollback receipt must name the
degraded baseline rather than claiming all old business services are healthy.

Implementation is under review. Offline copied-database crash/restart, real
staged QUIESCED withdrawal, installer-granted ACTIVE takeover, singleton refusal,
and same-owner loopback Sync timeout isolation passed. Production verification
remains outstanding. This is not an authorization receipt or evidence that
production bootstrap is presently safe.

SOURCE_READY, SUPERVISION_RECOVERED, COLLECTOR_RECOVERED and STABLE_COMMITTED are
separate results. Offline work continues if supervision is unavailable. Production
does not proceed until the verified maintenance/switch path can preserve other
services and report truthful rollback health. Exact qualification, Access, Free,
Observe and Assistant PAUSED rules remain unchanged. This plan is not evidence
that any of those gates has passed.

## Remaining staged acceptance boundary

The ACTIVE rehearsal must execute the real installer, bundle copy/verification,
launcher, OS mutex, activation wait, and child-owned receipt/heartbeat. Its
external environment is a separately identified fixture: independent roots,
task namespace, simulated business processes, and loopback providers. Fixture
adapters must be present in the child as well as the parent before dispatch;
parent-only PowerShell mocks do not cross a process boundary. Test instrumentation
is not an exact production artifact and must be reported separately from the
unchanged lifecycle functions it exercises. No production endpoint or scheduler
mutation is permitted by a fixture adapter. File/process mutations must reject
targets outside the owned fixture before executing.

| Phase | Dependency on production news-evidence health |
| --- | --- |
| Isolated staged acceptance | None; loopback success/failure is the authority |
| Incident Control Plane install/supervision | Current strict baseline requires Sync OK; no exception proved yet |
| Local snapshot-only exclusion | Current recovered-supervision baseline still requires Sync OK |
| Windows switch | Exact release and recovery baseline required |
| Candidate qualification | Applicable resource/parity/identity evidence required |
| Promote/Observe | Original resource and evidence obligations remain required |

No phase-specific degraded admission is established by this plan. An unavailable
production resource must not block isolated testing, but heartbeat success alone
cannot authorize production installation, exclusion, qualification or promotion.

Failure coverage includes withdrawal before grant, installer disappearance,
replacement disappearance, mutex refusal, degraded rollback and singleton
preservation. A healthy simulated business process is preservation evidence,
not proof of production business health. Copied-database recovery evidence stays
separate; no new multi-gigabyte baseline is needed for process lifecycle tests.

The real ACTIVE rehearsal exposed an incident rollback sibling: controller
termination demanded a live Collector even under the exact persisted restart
hold. Termination now captures Collector absence only under that validated hold,
uses complete process enumeration, and rechecks the same incident identities and
absence after stopping the exact controller. Other business owners remain
mandatory and preserved; normal termination admission is unchanged. Missing or
changed hold, an appearing Collector, or unavailable inventory rejects recovery.

The complete real-process rehearsal is an independently required Windows shard,
not a long unit test with a larger timeout. The existing 30-second unit-test
deadline and five-minute job deadline remain unchanged. The authoritative shard
map selects the rehearsal for its adapters and affected lifecycle/Sync owners;
the existing Windows aggregator requires its successful completion. It includes
real TLS loopback traffic and the unchanged 20-second transport timeout, without
production secrets, network targets or scheduled-task mutations.

## Proven News evidence timeout boundary

A bounded nonblocking stack capture of the existing production owners' scheduled
retry on 5 September 2026, 15:40:56–15:41:15 Kuala Lumpur, identified the initial
local News evidence GET, before any remote prepare/stage/activate. The API was
executing the visibility aggregation in `_news_evidence_display_rows`; the Sync
owner was waiting for response headers. The resource's normal failure path then
recorded its 58th failure and one-hour backoff. Heartbeat success did not clear it.

The copied-database query plan scans the JSON alias virtual table for each
receipt. Materializing that same alias relation once within the statement allows
an automatic indexed lookup. This changes no canonical identity, aggregation,
schema, data, timeout, receipt or publication contract. Existing handover tests
now compare exact output with the former query for both receipt owners and
measure SQLite VM work; they do not substitute wall-clock timing for correctness.

On the independent existing database copy, the corrected exact resource builder
completed in 5.150 seconds and produced 1,919 records. This is copied-data
execution evidence, not production recovery or full source-first efficiency
acceptance. The query still aggregates historical receipts when a generation
must be built; unrelated Sync revision/ACK optimizations remain outstanding.

Real activation withdrawal belongs to the same required rehearsal shard as
ACTIVE takeover. Both cases execute; neither is removed to fit the unit budget.
Hosted short-path aliases resolve to one physical fixture root before applying
the unchanged deny boundary. Test TLS explicitly requires TLS 1.2 or newer.

The real withdrawal rehearsal uses the existing production handoff deadline
(90 seconds), not its former unit-fixture-only 20-second override. Hosted handoff
did not satisfy that artificial override. This does not change production
deadlines, the 30-second unit contract, or the required five-minute job budget.
Hidden child errors and the pre-cleanup handoff observation are retained as
bounded fixture diagnostics; a timeout or identity mismatch remains failure.

Business stand-ins stay alive while the rehearsal parent holds their stdin pipe;
cleanup closes it, and parent death also closes it. A fixed sleep lifetime is
not a business-preservation contract: the former 60-second stand-in could exit
on its own during a valid hosted handoff. Both real-process scenarios retain
their living-owner assertions and explicit bounded cleanup.

## Copy-timeout retry boundary

The connected rehearsal exposed a local copy timeout followed by a cached
Candidate failure. An unchanged copy-only diagnostic completed in14.467s;
the actual PowerShell copy with API/Sync/annotator running completed in27.834s
under the unchanged30s native limit. Neither qualifies production. A timeout
does not prove that the immutable Candidate can never complete a later copy.

The correction uses the existing REVIEW_REQUIRED state and explicit
RetryCandidateValidation action for a freshly recorded COPY_DATABASE failure
whose detail is exactly NATIVE_PROCESS_TIMEOUT. Automatic discovery does not
retry review states. All other preflight failures keep their existing semantics.
No deadline, accepted gate, SQLite content, mutation privilege or service owner
changes. This is an explicit retry of failed Windows work, not its acceptance.

Actors are the serialized Control Center action, preflight native child, current
runtime readers/writers, candidate discovery, and existing cleanup owner. Only
the current preflight attempt may classify its own failure; stale runtime
diagnostics cannot create retry authority. The Candidate validation key and
Windows revision bind the retained failure. Retry appends its prior failure to
release history and reruns provenance and the complete Windows preflight;
Windows success is never inherited from the failed attempt. No provider or
human acceptance is synthesized. Active transactions exclude retry.

Transitions: testing -> review after a current copy timeout; explicit locked
retry -> testing; repeated timeout -> review; complete qualification -> the
existing later gates. A crash leaves the existing non-promotable state and
owned-child cleanup/reconciliation contract. Review survives process/machine
restart and has no automatic expiry or background retry. The current Stable
runtime is retained throughout; Reverse Stable and production switch authority
remain unchanged. Old controllers remain fail closed on the new review reason;
new controllers do not reinterpret old untyped FAILED records.

Before integration, extend the existing PowerShell preflight/retry contracts:
fresh copy timeout, stale diagnostic rejection, migration/other failures,
same-identity rejection then recovery, active transaction exclusion, preserved
failure history, and unchanged CPU/semantic retries. Exercise PS5.1 and PS7
through the actual operation dispatcher. Rehearse the exact installed owner
with the retained input and actual native copy. Existing independent review
requirements remain; author testing is not independent approval.


## Measured database-copy execution budget

The complete installed rehearsal at source `0dce57e3` recorded two continuously
growing copies of the 6,955,085,824-byte working database. Progress reached
6,018,481,600 bytes after about 25 seconds on the first attempt; the retry
reached full file length only around the generic 30-second deadline. Both native
children were terminated before successful completion. File length alone is not
a consistent-copy receipt. The earlier guarded baseline-only copy completed in
28.317 seconds. These observations expose an unsuitable generic subprocess
budget for a real full-database backup, not test state-space growth or repeated
historical test work. No database size reduction or new copy is required for
unrelated changes.

Change only the existing Copy-CandidatePreflightDatabase invocation to a fixed
120-second operation budget. The generic native timeout remains 30 seconds and
its maximum remains 300 seconds. Two minutes bounds this single required online
backup, including destination close/flush, under running-owner I/O variability;
it is an execution allowance, not a promised storage throughput. Migration,
API preflight, CI, qualification and observation deadlines do not change.

The serialized controller remains the sole owner. Both its direct preflight
call and New-CandidatePreflightDatabase compose the same copy helper, real
Python/SQLite backup and native process-tree cleanup. Source remains read-only;
partial destinations never qualify. The current business runtime stays active.
No durable state, lock, retry loop, schema, locator or mutation authority is
added. Success still requires native exit 0, followed by migration and the full
existing validation path. Timeout remains explicit same-identity review with
preserved failure history; other errors retain existing classification.

New controller/old runtime and new controller/old database use the same backup
interface; old controller/new runtime or rollback retains its former bounded
copy behavior and remains fail closed. A crash uses existing owned-child and
partial-preflight cleanup. No switch has occurred during copy, so Stable
recovery does not depend on finishing it. This is controlled local OS/SQLite
work; provider and human evidence are unaffected.

Verification: extend the existing consistent-copy/migration contract for both
Windows PowerShell and pwsh, recording the actual native binding while executing
real Python/SQLite and preserving source data/schema. Existing native timeout
termination and preflight failure cleanup contracts remain mandatory. Execute
the exact installed controller against the retained production-shaped database
with no other local build/test workload; require successful copy, migration,
API preflight and subsequent qualification. Independent review and real
production eligibility remain separate requirements.


## Stable migration evidence follows the actual status consumer

The installed 9ecef2a8 rehearsal passed copy, Windows preflight and repository
checks, then failed MIGRATION_LEGACY_COMPATIBILITY_FAILED. The migration SQL
reads recent_decisions from obsolete full-audit snapshot 4. The frozen actual
Stable ffe1de29 Worker reads dashboard status from snapshot 1; its audit writer
uses summary 9 and detail 6/7/8. Stable Python already emits bounded recent
decisions on the critical status route. Seeding snapshot 4 solely to pass this
check would not prove the actual Stable read path.

Correct the existing legacy_decisions evidence projection to read the bounded
critical status snapshot 1 and require an actual nonempty recent_decisions array.
Keep its existing receipt field: legacy denotes the still-active Stable, not a
particular retired audit storage slot. Required legacy tables, all News identity
and receipt checks, live Stable/Candidate endpoint checks and exact source/
Worker/database binding remain mandatory. Preserve historical snapshot 4 bytes;
no writer, schema, cleanup, data migration or service switch is introduced.

Actors are existing Stable Sync/status writer, both Worker status consumers,
serialized migration verification, receipt renewal and Reverse qualification.
Missing, malformed or empty current status is rejected even if historical 4 is
nonempty. A later valid same-target heartbeat may restore required evidence;
there is no new state, retry loop or acceptance shortcut. Old controllers retain
their old fail-closed check; new controllers read the same status contract used
by the recorded Stable. Existing receipts are immutable and live-evidence
matching still applies; old source qualification is not relabeled.

Extend the existing migration capability SQL family to execute its complete
query against the real migration schema with current status present/absent,
empty, wrong-type and malformed payloads, and independent historical 4 contents.
Retain the one-bounded-scan assertions and actual PowerShell migration rejection/
renewal contracts. Rehearse actual old and target built Workers against the same
D1 fixture after real heartbeat/bootstrap writes. The reusable review failure
is producer-to-consumer wiring: mocked capability counts hid the retired slot.
Independent review and production eligibility remain outstanding.


## Worker identity origin survives isolated transport binding

Installed 205ce275 passed copy/preflight and the corrected database capability
query, then failed the first Candidate status read because browser_url was
empty. The isolated entry point replaces workerUrl with a loopback provider;
Get-ReleaseVersionPreviewUrl incorrectly uses that transport endpoint to derive
a workers.dev version identity. Production's unchanged canonical hostname and
the verified version metadata already provide the required identity.

Retain one named canonical Worker origin before applying the existing isolated
transport override. Derive version URLs from that origin; keep Stable/provider
requests, protected Access binding and all network interception on their existing
transport path. No user-supplied origin, new allowed destination, DNS request,
provider claim, durable state, receipt or mutation privilege is added. Exact
Worker/Git/artifact/has_preview checks still precede URL construction.

The entry point owns origin and transport configuration; discovery consumes the
provider adapter's exact URL; migration/qualification consumers retain their
existing request boundary. Old/new production behavior is identical. Only the
isolated transport can differ, and its network wrapper must still match the
exact declared origin/method/path before forwarding locally. Missing or invalid
metadata remains fail closed; restart reinitializes both values from the same
entry point. Reverse and production Stable remain unaffected.

Extend the existing actual-PowerShell version URL family across normal and
loopback transport, PS5.1/PS7, valid and invalid metadata. Execute the connected
installed entry point and real built Candidate status route afterward. Never
patch Candidate.browser_url in persisted state or relax the request allowlist.


## Shared atomic persistence on long runtime roots

Installed 0d4b364c reached migration receipt publication after the prior gates,
but Write-ControlCenterJsonAtomic failed at Move-Item for the digest-named
receipt. Its separate cmdlet implementation does not share the native-path
support already used by Write-ReleaseEvidenceUtf8Atomic.

Compose JSON serialization with the existing UTF-8 persistence owner. Move the
native-path converter into that owner and publish CreateNew through a completed
temporary file plus atomic no-overwrite move. Mutable replacement continues to
use File.Replace; first publication uses File.Move. Reuse the short temporary
leaf and existing bounded serializer. No new storage root, receipt schema,
lock, state or authority is introduced; existing callers retain their immutable
or mutable mode. Do not change global Windows path policy or relocate evidence.

Actors include migration/qualification receipt writers, release/runtime state,
watchdog state, immutable evidence-node writers and their readers. Before
publication readers see the previous complete document or absence; after it,
they see the complete new document. Immutable collision retains original bytes.
Timeout/crash leaves at most an unreferenced temporary file, never a partial
final receipt. Existing serialized lifecycle ownership and caller containment
remain authoritative. Cleanup targets only the unique temporary/backup files
of the current call; historical evidence remains untouched.

Old/new readers use unchanged UTF-8 JSON and paths. No migration, service
switch, provider permission or recovery ownership changes; reverse uses the
same persisted records. Native converter and writer must load from the same
persistence module so standalone consumers do not gain a hidden import.

Extend the existing long-root evidence family for actual PS5.1/PS7 JSON create,
mutable replacement, immutable collision and complete reader round trip. Keep
existing evidence immutability/renewal and installation/state tests. Execute the
installed migration receipt writer and subsequent reader in the full fixture.
This consolidates two implementations of the same atomicity invariant rather
than introducing a new persistence mechanism. Independent review is required.


The extended regression also reproduced reader failure in PS5.1 after native
publication succeeded. Migration root/renewal and Access boundary/reuse/renewal
path constructors now return the existing native I/O representation; receipt
enumerators normalize their file reads the same way. Provider-inspection receipt
publication/retrieval follows this sibling rule. Existing receipt bytes, path
names, root identity and acceptance checks remain unchanged. Ten long-path
create/read/replace/collision cases pass across the five locator families and
both PowerShell runtimes; broader consumer gates remain required.

### Finite quote-input publication recovery

Connected ceaadecf execution retained a Windows PermissionError replacing the synthetic market-session file while readers were active. The quote process exited; its absence then correctly prevented the business-preserving watchdog termination operation. The isolated launcher/watchdog and business children were subsequently identified by PID, start time and exact owned paths and stopped; original failure evidence is retained.

The finite synthetic quote process remains the sole session-file writer. API and control readers can briefly hold a Windows handle. Retry only the same completed temporary session bytes after PermissionError, at 20 ms intervals for at most one second and never beyond the original scenario deadline. Do not append the accepted quote again, recompute its time, extend input lifetime, replace any production data or publish a health/ACK receipt. Permanent denial remains failure, and the writer removes only its own temporary file. Restart continues to use the existing sealed deadline and append-only quote identity. This introduces no durable state, service owner, authority or real broker behavior.

Extend the existing quote-input contract family with transient lock recovery, persistent denial and non-permission failure. Verify old session bytes until atomic publication, unchanged accepted quote count, finite retries and temporary cleanup. Exercise an actual Windows reader handle for the replacement boundary. Separately, the deep isolated Git estate requires local core.longpaths=true; the same real worktree command failed with Filename too long before this fixture-only configuration and succeeded afterward. Production Git configuration is unchanged.
### Failed predecessor does not own new-candidate progress

A source-bound contract reproduces the retained candidate's FAILED/worktree-unavailable shape as a predecessor of a new exact-main candidate. The optional supersession traversal currently rejects that predecessor as an unsafe intermediate, blocking fresh qualification even though it has never been accepted. This contradicts the existing incomplete-but-non-contradictory fallback contract.

Keep current-head identity, provider ownership, ancestry, edge uniqueness, no active transaction, accepted-history and receipt-integrity checks. Only a traversed predecessor at the end of its supersession chain, with matching validation key, FAILED state and no Candidate/Access/Promote/Stable acceptance event ends optional reuse as unavailable. Do not restore it, copy receipts, erase history, turn FAILED into success or retry that same identity. The new head remains non-promotable and must complete every fresh gate. An accepted or key-mismatched failed predecessor remains rejected. Discovery is the existing retry owner; no new durable state, lock, timer, expiry or recovery authority is introduced. Restart recomputes the same bounded plan from preserved history. Extend the existing supersession family and execute both PowerShell runtimes before the next connected run.
### Bounded compatibility gates

On6b067688, the hosted compatibility-release job collected151 cases and was cancelled at the unchanged five-minute job limit after140 passed in253.04s of test execution. The retained per-case timestamps put the discovery/history endpoint near half of the test time. Split its existing contiguous function selection at test_unavailable_supersession_reuse_falls_back_once_without_copying_evidence: candidate discovery/history owns the first range, qualification/recovery/Access owns the second. The existing impact selector, five-minute jobs,30-second per-case limit and all-selected-shards aggregate remain authoritative. No test is deleted, skipped, duplicated or assigned a longer timeout. Existing exact-once manifest coverage verifies the union, and both actual shard entrypoints must run before completion. Failure of either shard still blocks the aggregate; scheduler retries retain the same source identity. No runtime/production owner, secret, deployment or evidence schema changes.