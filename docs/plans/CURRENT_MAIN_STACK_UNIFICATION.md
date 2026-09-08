# Current-main stack unification

Status: IN PROGRESS. This replaces the execution order of the historical
#282–#328 stack, including its final documentation PR #302. It does not reuse
historical test results as current acceptance or authorize production activation.
The initial base is main `01c51df05ef15db4edc9e73dd97501e0e1cb8616`.

## Change contract

Use current implementations, not historical branch patches. Consolidate remaining
owner extraction and classification into one main-based PR. Preserve current
Collector atomicity, source-first sync, strict ACK, news semantics, main-only
publication, authentication, and Assistant PAUSED. No provider calls, database
migration, historical artifact rewrite, service switch, or model activation.

The actors remain the existing Collector, Annotator, API, Sync and main service
supervisor. Package moves do not add actors, durable states, retries, leases or
timers. Process entry points retain argument parsing, startup, thread ownership,
shutdown and restart. Domain functions and their process-local caches move with
their complete dependency owner. SQLite remains the authoritative transaction
owner; D1 remains a projection. Serialized field names and stored identities
remain unchanged. Existing API, scheduler, schema and transaction tests cover
empty, populated, failure and recovery states.

Impact path: existing launcher -> unchanged script entry point -> canonical
Python package -> unchanged SQLite/provider/transport contract -> existing
consumer. Import-time execution and circular dependencies require real Python
execution; static import checks alone are insufficient. JSON resources must
remain available from an installed package, independently of working directory.
No old compatibility implementation will become a second runtime owner.

| Boundary | Compatibility and failure/recovery evidence |
| --- | --- |
| Python imports and module resources | Import all moved owners in fresh processes; exercise CLI help outside repository cwd and installed-package resource loading. Missing modules must fail before service work. |
| Persisted data and models | Keep record/schema/artifact formats unchanged; inspect module-qualified persisted identities before removing old paths. Test schema creation/reopening and existing immutable-record contracts. |
| Recurring work | Preserve production callers, cadence, pools, shutdown and retry transitions. Execute existing Collector, scheduler, Sync and runtime families. |
| Architecture/CI | Update actual path declarations and generated source maps; retain test collection and required gates, without increasing timeouts. |
| Deployment | New PR only. Main-only publication remains the existing owner; this work does not deploy or roll back production. |

Pre-mortem: a renamed import can break monkeypatch targets, dynamic imports,
package resource paths or schema startup after unit tests pass. Inspect all
consumers and execute clean-process composition. If extraction fails, correct
the new branch; do not mutate production or overwrite data to demonstrate recovery.

## Original intent accounting

These are implementation dispositions pending exact-source validation, not PASS.

| PR | Original intent | Current-main disposition |
| --- | --- | --- |
| #282 | Architecture navigation and ownership rules | Reconcile current generated architecture and contracts. |
| #283 | Status snapshot cache | Existing `dashboard/status_cache.py`; verify and reuse. |
| #285 | Health projection | Existing `dashboard/health_projection.py`; verify and reuse current health semantics. |
| #287 | Resource contracts and import policy | Existing package and executable import policy; verify populated owner directions. |
| #288 | News resource ownership | Reuse #478 current resource owner, including subsequent fixes. |
| #289 | Market resources | Existing `dashboard/market_resources.py`; verify consumers. |
| #290 | Status resources | Existing runtime/storage/learning/deployment projections; inspect remaining composition. |
| #291 | Authenticated scheduler operator bridge | Existing `dashboard/operator_bridge.py`; retain scheduler authorization. This is not the retired blue/green Control Panel. |
| #292 | Sync progress, transport and resource owners | Progress/transport exist; inspect remaining resource extraction. |
| #294 | Annotator scheduler and brief runtime | Extract current domain work while retaining process lifecycle. |
| #295 | Collector rules and Control Center owners | Extract current Collector rules; Control Center split is RETIRED by the main-only strategy. |
| #296 | Decision and Evidence packages | Populate canonical owners from current source. |
| #297 | Training package | Populate canonical owners; retain artifacts and activation semantics. |
| #298 | News and AI packages | Populate canonical owners using current simplified review behavior. |
| #299 | Assistant, Runtime and Dashboard packages | Populate retained owners; Assistant remains PAUSED. |
| #301 | Tests organized by owner | Reconcile current contract shards and owner placement without dropping coverage. |
| #304 | Private architecture Explorer | Reuse current-main Explorer; do not restore old graph/runtime topology. |
| #321 | Source architecture compiler | Reuse current compiler including TypeScript and bounded source transport. |
| #324 | Executable architecture evidence | Reuse current evidence binding; UNKNOWN is not execution proof. |
| #325 | Targeted mutation effectiveness | Reuse current mutation runner; preserve historical survivors and retired release intent separately. |
| #328 | Evidence and code drill-down | Reuse current Explorer evidence surface; validate changed source identity. |
| #302 | Stack closure accounting | Replace sequential-stack instructions with this one-PR mapping and final audit. |

## Acceptance sequence

First run relevant import/resource and boundary failures, then owner test families,
then full required CI. Rebuild architecture with its existing compiler. Validate
the final diff and real production callers separately from implementation.
Any UI behavior change additionally requires immutable Preview checks on desktop,
390x844 and 360x800. Reuse unchanged UI evidence only with explicit behavior
identity. Record exact commands, results and remaining gaps before marking any
intent complete; closed PR status alone is not implementation evidence.

## Script and test directory classification

Move development/operational Python entrypoints into runtime, maintenance,
research, architecture and validation directories; move test contracts into
matching domain directories. Keep the four installed Windows launcher/registry
files at their stable public paths: scheduled tasks and an already-running
controller hold those locators. They remain real entrypoints, not compatibility
copies. Python service paths inside the registry move atomically with source.

Authority and state are unchanged: callers are CI, developer CLIs, the Windows
launcher, the Web build and fixture subprocesses. Callees resolve the same
repository and data roots from their new physical locations. No new timers,
leases, retries, migration state or production writes are introduced. Stored
historical receipts remain unchanged; current generated source identities and
all active path consumers are regenerated. Partial working-tree states are not
published. New source with existing data is compatible; old running controller
uses the retained registry path, exits after updating, then restarts the same
launcher with new service paths. A failed check leaves the branch unmerged.

Execution matrix: real Python CLI from unrelated cwd (help, fixture generation,
missing-input rejection), pytest recursive collection with exactly-once shard
membership, PowerShell startup/status and service registry lifecycle tests,
Node Web build/compiler subprocesses, and source compiler currentness. Preserve
all tests and fixture bytes; compare collected node IDs modulo directory moves.
Use current required CI on the final head. Classification does not complete the
separately retained runtime-evidence integration requirement.
