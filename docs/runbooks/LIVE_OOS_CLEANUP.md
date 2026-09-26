# Retired Live OOS data cleanup

This procedure removes model artifacts, predictions, scores and decision history
at the owner's request. It preserves news collection, annotations, current usable
events, source evidence, daily briefs, storylines, quotes and market candles.

## Activation order

1. Merge and deploy the reviewed retirement revision through protected main.
   Verify both the local runtime and Worker identities. A branch is insufficient.
2. Deploy the isolated broadcast V2 receiver before its matching publisher,
   following the [broadcast contract](../contracts/LIVE_BROADCAST.md). Verify
   that a fresh V2 quote-only publish replaced the stored model payload.
   Confirm the collector has no training/prediction/settlement owner and Sync no
   longer schedules learning resources. Keep the existing pause control until
   this is verified. Stop local database writers for offline cleanup using the
   existing runtime owner; do not launch replacement service processes.
3. Inventory the exact authoritative database with
   `python scripts/maintenance/purge_live_oos.py --database <absolute-database>`.
   The default opens read-only. Review counts against the explicit table list.
4. Run the same command with `--apply` during the stopped-writer window.
   Deletion and derived-cache invalidation use one transaction. A failed or
   interrupted transaction rolls back; a committed operation is idempotent.
   A retained foreign key referencing retired data rejects the entire operation.
   Never delete the database itself. This does not shrink the physical file;
   freed pages are reusable. Any later vacuum needs its own space/time budget.
5. Inventory and remove only the retired `models-v2` directory and the following
   files under that same verified state root: `u5-state.json`,
   `u5-warmup-receipt.json`, `forecast-model-activity.json`,
   `dashboard-learning-sync-state.json`,
   `dashboard-learning-sync-state-cloudflare.json`,
   `dashboard-learning-history-sync-state.json`, and
   `dashboard-learning-history-sync-state-cloudflare.json`.
   Reject links/reparse points and verify containment before file removal.
   Preserve quotes, news, operational receipts, unrelated files and backups.
6. Explicitly run `scripts/maintenance/purge_live_oos_d1.sql` against the verified
   production D1 binding. It clears only retired tables and model fields in
   shared snapshots. It is intentionally not an automatic schema migration.
   Statements are restartable; retained news tables are never deleted.
7. Restart retained services through their normal owner. Verify current quote
   and news/event publication, and that retired routes return 404. Record exact
   identities, deletion counts and retained event counts before/after.

## Failure and recovery

Before commit, roll back and retain the diagnostic without partial local data
loss. After commit, do not run an older model producer against the cleaned data.
Repair forward through main. News ingestion and publication must remain
independent of the emptied model tables. Existing backups are recovery evidence;
this procedure does not erase backups or promise forensic erasure of SQLite
pages. Remove historical backup copies only under a separate retention decision.

The production cleanup and activation have not been executed by preparing this
runbook or its scripts.
