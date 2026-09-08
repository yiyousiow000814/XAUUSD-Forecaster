# SQL and synchronization efficiency correction

## Change contract

The bounded operator retry mirror currently performs correlated JSON membership scans for each stored job, and drains only one mutation per cycle. Correct membership lookup to a one-time non-correlated set. Advance up to 32 changed jobs and 32 obsolete mirror removals per invocation, retaining zero writes for unchanged records and exact transactional ACK. The previous one-row rate matched observed live churn and starved cleanup. A normal 200-row initial fill completes within seven control cycles.

The local semantic-health query joins scheduler jobs by annotation identity but has no index starting with that identity. Production EXPLAIN selects the prompt/task index repeatedly. Add a scheduler-owned annotation identity index; retain query results, model rules and immutable evidence unchanged.

Owners: DashboardSyncControlLane supplies the authoritative local mirror; the authenticated Worker route updates its disposable D1 mirror and acknowledges only exact completion in its existing batch transaction. The collector owns WAL maintenance; dashboard builders own read-only snapshots. No new owner, state, credentials, API, scheduler or platform is introduced.

Impact: local retry producer -> SYNC_JOBS -> D1 mirror and sync digest -> admin reader. Local scheduler schema -> semantic-health query -> dashboard snapshot lifetime -> WAL checkpoint opportunity. All producer and consumer shapes stay unchanged.

Compatibility: old/new controllers and runtimes share unchanged payloads and tables; the added local index is additive and ignored by old code. No migration of facts or rollback of databases. Failed D1 batches roll back, lost responses replay safely, partial drain remains incomplete. Index creation failure leaves original data intact and follows existing startup failure reporting. Reverting code needs no data rewrite.

Evidence: existing D1 insights are provider analytics with rolling-window attribution uncertainty. SQL plans and deterministic fixture results are controlled exact. Do not infer post-deployment regressions solely from the rolling daily total. Production WAL receipts show backfill advances, not a permanently frozen reader.

Verification: capture old/new production-shaped SQL plans and work; extend existing mirror parity/ACK and semantic-health tests. Run relevant suites before required CI. Verify production mirror completion and WAL/read-model timings after normal main deployment. No production testing mutation or heavy full-database copy. Required independent review remains distinct from author tests. Full SQL audit and quota acceptance remain open until recorded inventory and measurements are complete.

## Additional measured local owner

The same read-only rehearsal found 714 per-version learning queries spending approximately 20 seconds before the 45-second bounded observation ended. Production EXPLAIN reports SCAN p for each model_version lookup. Add the existing evidence schema owner's (model_version, decision_time) index; retain every historical model and exact prediction-time filter. The audit recent-decision path also needs the same decision-primary lookup constraint already used by the critical 18-row path. No display rows are removed.

## Verification status

Initial focused Python health/coverage/WAL tests: 32 passed. Web suite: 380 passed, 6 skipped. Production readonly alternative annotation-identity lookup completed in 0.315 seconds (1,188 rows), while the unmodified query exceeded the 23-second remaining SQL budget. This is a query-path rehearsal, not a claim that production has the new index. A second readonly rehearsal cloned only scheduler jobs into connection-local temporary memory and found the remaining learning scans. No authoritative database was changed or copied in full.

Historical capacity audit correction: the September 3 retry projection of four rows read per invocation and full_scan=false is contradicted by current production insights (about 2.16 million reads/hour across deletion and completion checks). Preserve that historical audit, but do not use its daily total as current acceptance. New production rows_read and rows_written, exact deployment identity, mirror completion throughput, and full recurring SQL inventory remain pending.

Final author inspection: both JSON membership inputs are normalized non-null unique 64-hex identities, so NOT IN preserves empty-list/deletion semantics. No authoritative job or command event is deleted; only the existing disposable mirror deletion is optimized. Both schema indexes install through ForwardLedger's existing evidence and scheduler installers. No new startup process, schema version, or production writer is introduced. Related Python suite: 278 passed; audit/critical sibling lookup regression additionally passed. Author inspection is not an independent approval.

## Retry convergence and budget correction

A bounded production read returned 286 mirror jobs (286 rows read, zero writes), against the local 200-job authoritative list: 87 obsolete mirror identities and one missing identity. Local hashed snapshots changed five jobs over 149 seconds, approximately the old one-job-per-30-second drain rate. The new bounded batch restores catch-up capacity without rewriting unchanged jobs. Maximum job mutations per invocation are 64 (up to 32 upserts plus 32 deletes), plus the completion-state row; index amplification is additional. A 200-row initial fill writes 200 job records once across at most seven cycles, rather than multiplying writes by the batch size. This does not assert a worst-case daily free-plan guarantee. Actual steady-state and burst D1 metrics remain acceptance evidence.

SQLite 200-row replay comparison, preserving identical mutations: deletion VM work 102,900 -> 3,600 steps; completion 108,900 -> 9,400 (rounded down to 100-step samples). These are execution steps, not Cloudflare billed rows. Tests also prove cleanup converges while one retained job changes every cycle.

## Parameterized deep-page lookup follow-up

Boundary: learning-history cursor SQL only. Production placeholders make the OR-based position predicate fail to constrain the time component of the index, unlike a literal-value EXPLAIN. Use row-value time/key comparisons for position and watermark. Existing source rows, response shape, cursor token, ordering, counts, byte bound, authentication and Preview behavior remain unchanged. No writes or schema changes. Old/new callers and saved cursors remain compatible; errors follow the existing GET error response and replay is read-only. The index is owned by the existing D1 migration. Verify the actual built GET route with bound deep cursors and equal-time keys, not a simplified literal query. Record production D1 plan/reads and exact deployment identity separately.
