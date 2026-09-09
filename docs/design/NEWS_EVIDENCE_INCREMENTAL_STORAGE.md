# Incremental news evidence storage

## Change contract

The authenticated evidence endpoint retains its paged-v2 request/ACK contract.
SQLite and the existing serial heavy Sync owner remain authoritative. Worker
storage changes from snapshot-keyed full record copies to stable event-keyed
current records. Bounded batch receipts retain membership keys and only changed
payloads. This reduces actual row mutations, not merely HTTP requests. R2 is
not required for this correction; payload bytes and request count are not
substitutes for billed row measurements.

## Actors, states and impact

Local frozen generation -> existing Sync prepare/stage/activate -> authenticated
Worker -> D1 staging receipts -> atomic current rows and publication pointer ->
generation-bound indexed page reader. Cleanup runs through that same Sync owner.
No new service, credential, timer, lease or operator control is introduced.

Prepare captures the current publication as the immutable comparison baseline.
Stage compares each bounded incoming batch against that baseline, retaining all
keys for completeness and only changed payloads. Activation checks contiguous
receipts, unique membership and the unchanged baseline, then applies changed
records, removes absent keys and advances publication atomically. Readers check
publication identity inside their data query as well as before it. Old cursors
return the existing stale-cursor response. Empty input is a real empty set.

Each receipt and offset advance is one transaction; exact duplicate requests
reuse receipts. Transaction guards reject a concurrent prepare, stage, activation
or cleanup that invalidates an earlier check. If another publication wins,
prepare discards only its own obsolete staging and restarts against the new
baseline. This is retryable, not permanent rejection. No incomplete generation
changes current rows. Restart resumes durable receipts without source recapture.

## Compatibility and recovery

Additive migration copies the existing active evidence and pointer once; old
tables remain untouched audit/recovery data. In-progress old staging is replayed
through the unchanged sender protocol. Migration must precede new Worker
activation. New/old senders use the same request and ACK fields. Old Worker
rollback reads its retained last complete snapshot and ordinary Sync can rebuild
it from local authority; this is stale recovery, never a claim of current data.
No database rollback overwrites newer authoritative local facts. Legacy tables
must not participate in new runtime writes or recurring cleanup.

## Failure and expiry matrix

Missing schema: capability error; apply reviewed migration and retry. Missing
or corrupt receipt: prepare repairs from first invalid prefix. Partial upload:
old current remains readable. Lost ACK: duplicate receipt is accepted only for
identical content. Changed baseline: restart own staging. Activation exception:
D1 transaction rolls back rows and pointer together. Cleanup: bounded obsolete
receipts only, excludes current and fresh staging; abandoned staging expires
after 24 hours. Recent publication receipts retain the existing five-minute
grace. Cleanup cannot delete current rows. All optional failures remain isolated
from heartbeat, collector and decision production.

## Verification and cost acceptance

Controlled exact: real SQLite/D1 SQL boundaries, transaction rollback, count and
membership invariants, filters, keyset pagination, same-request replay, restart,
empty replacement, mutation races and migration from populated old state.
Extend the existing evidence contract tests. Run zero-change, one-change,
addition, removal and changed-filter fixtures with real SQL and count current
table mutations. Include receipt insert/delete overhead. Inspect query plans
for membership deletion; avoid correlated full-list scans.

External advisory: Analytics and query insights can differ and do not form an
exact additive ledger. Real D1 statement metadata must calibrate index writes
and reads using the production-shaped route. Validate branch Preview and actual
deployment before acceptance. Whole-day Free-plan eligibility remains separate
from a bounded-request cost reduction. Do not extrapolate a quiet minute or
pretend local SQLite total_changes counts Cloudflare index writes.

Focused tests have a two-minute budget; complete required CI retains its existing
budgets. Check actual read pages on desktop, 390x844 and 360x800; verify navigate,
paginate, filter and stale-cursor restart. After deployment verify natural Sync
completion and continued updates, preserving prior cost and failure evidence.

## Sender acknowledgement reconciliation

A schema copy and deployment are separate boundaries: the old Worker can
advance its publication after the copy, leaving the sender's valid prior ACK
ahead of the copied publication. Cleanup is not a prerequisite that may prevent
reconciliation forever. On the explicit NEWS_EVIDENCE_CLEANUP_INVALID response,
the single existing sender discards the fast-path authority in memory and runs
normal prepare/stage/activate against its frozen local generation. It preserves
the on-disk ACK until a genuine new response or normal staging progress replaces
it. Other remote errors continue to propagate. No new owner, durable mode,
manual ACK, timer, or database reset is introduced. Restart repeats the same
bounded reconciliation and accepted receipts remain reusable.

The deployment review must pair sender checkpoints with target publications,
including old-version writes between migration and activation; verifying the
copied database alone does not prove handover. Existing cleanup, preparation,
staging, authentication and exact-request ACK contracts retain ownership of
their respective invariants. The focused recovery test covers unchanged local
identity (which must not take the stale fast path), real preparation/activation
ACK validation, and unrelated errors remaining visible. Production acceptance
requires the natural Windows sender to complete a publication on the new store.
