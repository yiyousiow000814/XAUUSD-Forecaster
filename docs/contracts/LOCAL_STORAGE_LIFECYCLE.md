# Local Storage Lifecycle Contract

## Authority

The active `forward-evidence.sqlite3` database is the append-only forecasting
evidence authority. Daily SQLite snapshots are same-disk recovery artifacts;
they are not an additional evidence authority and do not change row retention.

The Forward Collector is the sole daily-backup, backup-retention, and WAL
checkpoint execution owner. Maintenance starts only after Collector startup
viability and heartbeat publication. Restart, retry, or a second process must
not multiply a backup, retention, or checkpoint operation.

## WAL checkpoint ownership

All production write connections disable connection-owned automatic
checkpointing and apply a 64 MiB post-reset journal size limit. This limit is
16 times the former 1,000-page automatic-checkpoint target at the production
4 KiB page size: it avoids normal truncate churn while bounding retained WAL
capacity after a reset. Because both settings are connection-local, every
Forward writer must use the shared writer-connection boundary.

Only the Collector-supervised background checkpoint owner may perform recurring
checkpoints. It runs independently from decision, annotation, training, and API
commit paths. Each round first uses a non-blocking passive checkpoint. A
truncate is attempted only when that round reports every valid WAL frame
backfilled and the physical file exceeds the size limit. Lock acquisition for
the truncate is capped at 250 milliseconds; a concurrent reader or writer is a
visible retryable state, not authority to block a critical writer or discard a
frame.

The owner publishes a digest-bound fixed state receipt beneath the runtime root
with frame counts, pending frames, physical bytes, size limit, lock timeout,
truncate decision, and error state. Long readers remain bounded by their own
snapshot contracts. Owner restart retries from SQLite's durable WAL state; the
receipt is observability evidence and is never used as database authority.

## Managed daily snapshots

A daily snapshot is retention-managed only when all of these facts hold:

- its name is exactly `forward-evidence-YYYYMMDD.sqlite3`;
- its adjacent completion receipt has the current receipt schema and digest;
- the receipt binds the authoritative database path, file identity, and Forward
  epoch;
- the receipt snapshot identity still matches the complete SQLite file.

Special repair or migration snapshots, legacy snapshots without a completion
receipt, temporary files without a live owner, and malformed pairs are
`UNKNOWN`. Retention reports their count and bytes but never deletes them.

## Retention budget

The managed recovery set retains the newest seven UTC daily snapshots, then one
snapshot from each of four older ISO weeks, then one snapshot from each of three
older calendar months. It is also bounded by:

- 14 managed snapshots;
- 100 days of snapshot age;
- 128 GiB of managed snapshot bytes.

The newest valid snapshot is never deleted merely to satisfy the byte budget.
If that single snapshot exceeds the budget, retention fails closed and reports
the condition for an explicit storage decision.

## Deletion and recovery

Retention persists a digest-bound deletion plan before unlinking any managed
snapshot. The plan binds the stable source identity, policy, exact snapshot and
receipt names, snapshot identity, receipt digest, and byte count. A restarted
owner may resume only that exact plan. Changed identity, policy, source, or
receipt fails closed.

The snapshot is removed before its receipt. If the owner stops between those
steps, the persisted plan is sufficient to remove the already-accounted receipt
on restart. A second retention round is excluded by the owner lease.

Each completed round atomically publishes a fixed retention state receipt with
the policy, managed and unknown bytes, retained and deleted identities, and
managed, unknown, and total GiB-days. Repeating a completed round with unchanged inputs is
idempotent and produces no additional deletion.

## Separate lifecycle families

Historical v1 backup temporaries use the exact form
`.forward-evidence-YYYYMMDD.sqlite3.PID.UUID.tmp`. They are never recovery
authority before the online-backup integrity check and atomic final-name swap.
The retention owner may classify that temp family as `PROVEN_STALE` only when:

- at least 48 hours have elapsed since its last write;
- the encoded owner PID is absent, not merely unrecognized;
- the corresponding final daily target exists;
- no bounded runtime JSON authority references the temp name;
- the exact temp and sidecars still match a persisted reclaim plan; and
- the OS grants DELETE access, proving that no blocking handle exists.

The owner persists the exact reclaim plan before deletion and publishes a
digest-bound fixed state receipt after completion. Any failed bound, changed
identity, active/reused PID, reference, missing final target, or blocking handle
leaves the complete family `UNKNOWN`. No other temp naming family is implied.

Ordinary backup adoption, quote archive retention, Git worktree cleanup, and
release-evidence retention are separate lifecycle families. This contract does
not authorize deleting those objects.
