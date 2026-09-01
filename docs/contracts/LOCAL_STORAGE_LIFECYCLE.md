# Local Storage Lifecycle Contract

## Authority

The active `forward-evidence.sqlite3` database is the append-only forecasting
evidence authority. Daily SQLite snapshots are same-disk recovery artifacts;
they are not an additional evidence authority and do not change row retention.

The Forward Collector is the sole daily-backup and backup-retention execution
owner. Maintenance starts only after Collector startup viability and heartbeat
publication. Restart, retry, or a second process must not multiply a backup or
retention operation.

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

WAL checkpoint ownership, legacy/temporary backup recovery, quote archive
retention, Git worktree cleanup, and release-evidence retention are separate
lifecycle families. This contract does not authorize deleting those objects.
